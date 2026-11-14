"""
Deleting a candidate costs a fixed, small number of statements and one commit, however much history they have.

The hosted database costs a round trip per statement, so the delete is one multi-table statement (see
`_delete_candidate_rows`); stored files are removed after the commit by a background task. These tests count
statements on the guarded disposable PostgreSQL database and fail if a per-row loop or an extra commit comes back.
"""
from datetime import date

from sqlalchemy import event, select

from app.db import models as m
from app.services import change_feed

from test_candidate_delete import NOW, application, candidate, count, env, seed_full_history, stored_file  # noqa: F401  (env is a fixture)
from test_database_naming import schema_database  # noqa: F401  (fixture)

MAX_STATEMENTS = 8


class Counter:
    """Every statement the engine runs (the commit-time NOTIFY uses the raw cursor and is not a statement here)."""

    def __init__(self, engine):
        self.engine, self.statements, self.commits = engine, [], 0

    def _run(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(" ".join(statement.split())[:90])

    def _commit(self, conn):
        self.commits += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._run)
        event.listen(self.engine, "commit", self._commit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._run)
        event.remove(self.engine, "commit", self._commit)


def add_more_history(env, cid, tasks=0, documents=0):
    """Rows in every table the delete touches, in a number the caller chooses."""
    db = env.db
    for index in range(tasks):
        db.add(m.Task(id=f"{cid}-t{index}", kind=m.TaskKind.initial_call, title="Call", candidate_id=cid, owner_id="staff"))
    db.flush()
    for index in range(tasks):
        db.add(m.Remark(task_id=f"{cid}-t{index}", body="Called", author_id="staff"))
        db.add(m.Notification(user_id="staff", kind="task", title="Task", link_to=f"/tasks?task={cid}-t{index}"))
    for index in range(documents):
        stored_file(env, db, cid, f"{cid}-doc{index}.pdf")
    db.add(m.ApplicationFeedback(candidate_id=cid, author_id="staff", kind="update", body="Waiting for the passport"))
    db.add(m.DailyWorkItem(owner_id="staff", work_date=date(2026, 5, 1), kind="call", candidate_id=cid, slot_no=1, session="morning", due_at=NOW))
    db.add(m.Lead(name="Lead", candidate_id=cid))
    db.commit()


def warm(env):
    """The steady state: the assignment cursor row exists and the stand-in login is cached."""
    if env.db.get(m.AssignmentPointer, 1) is None:
        env.db.add(m.AssignmentPointer(id=1))
        env.db.commit()
    env.db.get(m.User, "admin")


def delete_counted(env, cid):
    warm(env)
    with Counter(env.db.get_bind()) as counter:
        response = env.http().delete(f"/api/candidates/{cid}")
    assert response.status_code == 200, response.text
    return response.json(), counter


def test_delete_is_a_few_statements_and_one_commit_with_full_history(env):
    seeded = seed_full_history(env)
    add_more_history(env, seeded.cid, tasks=4, documents=5)
    body, counter = delete_counted(env, seeded.cid)
    assert body["deleted"]["candidates"] == 1 and body["deleted"]["documents"] == 8
    assert len(counter.statements) <= MAX_STATEMENTS, counter.statements
    print("delete statements:", len(counter.statements), counter.statements)
    assert counter.commits == 1, counter.commits
    env.db.expire_all()
    assert env.db.get(m.Candidate, seeded.cid) is None
    for model in (m.ApplicationFeedback, m.DailyWorkItem):
        assert count(env.db, model, model.candidate_id == seeded.cid) == 0
    lead = env.db.scalar(select(m.Lead))
    assert lead.candidate_id is None
    audit = env.db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Deleted candidate"))
    assert audit.meta == {"ref": "C-gone", "documents": 8, "applications": 1, "tasks": 6}
    assert not seeded.resume.exists() and not seeded.receipt.exists()
    assert seeded.shared.exists() and seeded.kept.exists()


def test_statement_count_does_not_grow_with_the_history(env):
    small = candidate(env.db, "small")
    env.db.commit()
    big = seed_full_history(env)
    add_more_history(env, big.cid, tasks=12, documents=15)
    _, few = delete_counted(env, small)
    _, many = delete_counted(env, big.cid)
    # Files are checked in one statement after the commit; a candidate without files does not need that one.
    assert len(many.statements) - len(few.statements) <= 1, (few.statements, many.statements)
    assert few.commits == many.commits == 1


def test_refused_deletes_stay_cheap_and_change_nothing(env):
    cid = candidate(env.db, "busy")
    application(env.db, "a-live", cid, "o2", m.ApplicationStatus.active)
    env.db.commit()
    warm(env)
    with Counter(env.db.get_bind()) as counter:
        response = env.http().delete(f"/api/candidates/{cid}")
    assert response.status_code == 409
    assert len(counter.statements) <= 3, counter.statements  # login lookup, cursor lock, candidate with placements
    assert counter.commits == 0
    assert env.db.get(m.Candidate, cid) is not None


def test_withdraw_and_delete_stays_within_the_statement_budget_with_one_commit(env):
    cid = candidate(env.db, "busy")
    application(env.db, "a-live", cid, "o1", m.ApplicationStatus.active)
    application(env.db, "a-old", cid, "o2", m.ApplicationStatus.closed)
    env.db.get(m.Order, "o1").status = m.OrderStatus.filled
    env.db.commit()
    warm(env)
    with Counter(env.db.get_bind()) as counter:
        response = env.http().delete(f"/api/candidates/{cid}?withdrawFromOrders=true")
    assert response.status_code == 200, response.text
    assert len(counter.statements) <= MAX_STATEMENTS, counter.statements
    assert counter.commits == 1, counter.commits
    env.db.expire_all()
    assert env.db.get(m.Order, "o1").status is m.OrderStatus.open


def test_a_delete_announces_the_tables_it_changed_to_open_desks():
    """The one-statement delete starts with WITH; the live-update feed must still see every table it wrote."""
    statement = "WITH a AS (UPDATE email_messages SET candidate_id=NULL RETURNING id), b AS (DELETE FROM crm_candidates RETURNING id) SELECT 1"
    topics = {table for _, table in change_feed.classify_statements(statement)}
    assert topics == {"email_messages", "crm_candidates"}
    assert change_feed.classify_statements("WITH x AS (SELECT 1) SELECT * FROM crm_candidates FOR UPDATE OF crm_candidates") == []
    assert change_feed.classify_statements("SELECT 1") == []
