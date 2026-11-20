"""Deleting a Recruiter's account PERMANENTLY: Admin and MD only, never an Admin, an MD or yourself, never damaging history.

The auth_users row is physically removed (the email is free again); every row that pointed at it moves to the ONE system account
named "Former user" (created on first use, disabled, hidden from every list and picker).

Disposable PostgreSQL only (TEST_DATABASE_URL, a loopback crm_schema_test* database); the whole schema is built from the models.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.security import SESSION_COOKIE, hash_password
from app.db.models import (FORMER_USER_EMAIL, FORMER_USER_NAME, Application, ApplicationStatus, AssignmentPointer, AuditEntry, Base, Candidate,
                           CandidateStatus, ClientStaff, DailyGoal, Lead, Notification, Remark, Role, Session as LoginSession, Source, Task,
                           TaskEvent, TaskKind, TodoItem, User)
from app.db.session import get_db
from app.services import user_admin
from test_database_naming import _guard_test_url

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def env():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB")
    engine = create_engine(_guard_test_url(raw), connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for identifier, role in (("admin", Role.admin), ("admin2", Role.admin), ("md", Role.manager), ("a", Role.staff), ("b", Role.staff),
                                 ("c", Role.staff), ("d", Role.staff)):
            db.add(User(id=identifier, name=identifier.upper() + " Person", email=f"{identifier}@example.test", role=role, active=True,
                        phone="+919000000000", receives_auto_assign=role is Role.staff, on_leave=False, password_hash=hash_password("initial-test-password")))
        db.flush()
        for identifier in ("admin", "admin2", "md", "a", "b", "c", "d"):
            db.add(LoginSession(id=f"session-{identifier}", user_id=identifier, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()
    from app.api.routers import users
    application = FastAPI()
    application.include_router(users.router)

    def database():
        with factory() as db:
            yield db
    application.dependency_overrides[get_db] = database
    try:
        with TestClient(application, base_url="https://testserver", raise_server_exceptions=False) as client:
            yield SimpleNamespace(client=client, factory=factory, engine=engine)
    finally:
        engine.dispose()


def act_as(env, who):
    env.client.cookies.clear()
    env.client.cookies.set(SESSION_COOKIE, f"session-{who}")


def delete(env, who, target, **query):
    act_as(env, who)
    return env.client.delete(f"/api/users/{target}", params=query)


def give_work(env, owner="a", candidates=3, with_remark=True):
    """Candidates with an open initial-call task each, one finished task, a remark, a to-do, a lead, goals and a client allocation."""
    with env.factory() as db:
        for number in range(candidates):
            cid = f"cand-{owner}-{number}"
            db.add(Candidate(id=cid, ref=f"C-{owner}-{number}", name=f"Candidate {number}", phone=f"90000000{number:02d}", source=Source.walk_in,
                             owner_id=owner, languages=[], skills=[], designations=[]))
            db.flush()
            db.add(Task(id=f"task-{owner}-{number}", title="Initial call", kind=TaskKind.initial_call, candidate_id=cid, owner_id=owner, created_at=NOW))
        db.add(Task(id=f"done-{owner}", title="Finished", kind=TaskKind.initial_call, candidate_id=f"cand-{owner}-0", owner_id=owner,
                    created_at=NOW, completed_at=NOW + timedelta(hours=1)))
        db.flush()
        if with_remark:
            db.add(Remark(task_id=f"done-{owner}", body="Called, will come tomorrow", author_id=owner))
        db.add(AuditEntry(actor_id=owner, action="Recorded a call", entity_type="candidate", entity_id=f"cand-{owner}-0", target="Candidate 0"))
        db.add(Lead(id=f"lead-{owner}", name="Lead One", phone="9111111111", owner_id=owner, status="new"))
        db.add(Notification(user_id=owner, kind="x", title="Something for them"))
        db.add(TodoItem(owner_id=owner, title="Private", due_at=NOW))
        db.add(DailyGoal(user_id=owner, metric="candidates", target=5))
        db.execute(text("INSERT INTO crm_client_contacts (id, name) VALUES ('client-1', 'Client One') ON CONFLICT (id) DO NOTHING"))
        db.add(ClientStaff(client_id="client-1", user_id=owner))
        db.merge(AssignmentPointer(id=1, last_user_id=owner))
        db.commit()


def former_id(db):
    """The id of the one "Former user" system account (None until a delete has needed it)."""
    return db.scalar(select(User.id).where(User.email == FORMER_USER_EMAIL))


def gone(db, user_id):
    return db.get(User, user_id) is None


# ----------------------------------------------------------------------------------------- who may delete whom

@pytest.mark.parametrize("who", ["admin", "md"])
def test_admin_and_md_may_delete_a_recruiter_with_nothing_to_move(env, who):
    response = delete(env, who, "d")
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "nothing to move"


def test_a_recruiter_may_not_delete_anyone(env):
    for target in ("d", "c", "a"):
        assert delete(env, "b", target).status_code == 403
    with env.factory() as db:
        assert db.get(User, "d").active and not db.get(User, "d").deleted


@pytest.mark.parametrize("who, target, words", [("admin", "admin2", "Only Recruiter accounts"), ("md", "admin", "Only Recruiter accounts"),
                                                ("admin", "md", "Only Recruiter accounts"), ("md", "md", "your own account"),
                                                ("admin", "admin", "your own account")])
def test_admin_md_and_self_cannot_be_deleted(env, who, target, words):
    response = delete(env, who, target)
    assert response.status_code == 409 and words in response.json()["detail"], response.text
    with env.factory() as db:
        assert db.get(User, target).active and not db.get(User, target).deleted


def test_an_unknown_or_already_deleted_account_is_not_found(env):
    assert delete(env, "admin", "nobody").status_code == 404
    assert delete(env, "admin", "d").status_code == 200
    assert delete(env, "admin", "d").status_code == 404


def test_a_recruiter_cannot_even_ask_what_deleting_would_move(env):
    act_as(env, "b")
    assert env.client.get("/api/users/a/delete-summary").status_code == 403
    act_as(env, "md")
    assert env.client.get("/api/users/a/delete-summary").status_code == 200


# ----------------------------------------------------------------------------------------- the account itself

def test_the_row_is_physically_deleted_and_the_person_vanishes_from_the_list(env):
    assert delete(env, "md", "d").status_code == 200
    with env.factory() as db:
        assert gone(db, "d"), "no tombstone row remains"
        assert db.scalar(select(func.count()).select_from(User).where(User.email.like("d@%"))) == 0
        assert db.scalar(select(func.count()).select_from(User).where(User.name.like("%former user%"))) == 0
        assert db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == "d")) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 7, "seven people, minus one, plus the Former user account"
    act_as(env, "admin")
    listed = {row["id"] for row in env.client.get("/api/users").json()}
    assert "d" not in listed and {"a", "b", "c", "admin", "md"} <= listed
    # The cookie of the deleted person is dead, and they cannot be edited, reset or deleted again.
    env.client.cookies.clear()
    env.client.cookies.set(SESSION_COOKIE, "session-d")
    assert env.client.get("/api/users").status_code == 401
    act_as(env, "admin")
    assert env.client.patch("/api/users/d", json={"name": "Back"}).status_code == 404
    assert env.client.post("/api/users/d/password", json={"password": "another-long-password"}).status_code == 404


def test_the_former_user_account_is_one_disabled_account_nobody_can_see_use_or_sign_into(env):
    assert delete(env, "admin", "d").status_code == 200
    assert delete(env, "admin", "c").status_code == 200
    with env.factory() as db:
        formers = list(db.scalars(select(User).where(User.email == FORMER_USER_EMAIL)))
        assert len(formers) == 1, "ONE permanent account, however many people are deleted"
        former = formers[0]
        assert former.name == FORMER_USER_NAME and former.deleted
        assert not former.active and former.password_hash is None and former.phone is None
        assert not former.receives_auto_assign and not former.on_leave and former.role == Role.staff
        assert db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == former.id)) == 0
        fid = former.id
    act_as(env, "admin")
    assert fid not in {row["id"] for row in env.client.get("/api/users").json()}
    assert env.client.patch(f"/api/users/{fid}", json={"active": True}).status_code == 404
    assert env.client.post(f"/api/users/{fid}/password", json={"password": "another-long-password"}).status_code == 404
    assert env.client.delete(f"/api/users/{fid}").status_code == 404
    give_work(env, "a")
    assert delete(env, "admin", "a", reassignTo=fid).status_code == 404, "work can never be handed to the Former user"
    refused = env.client.post("/api/users", json={"name": "X", "email": FORMER_USER_EMAIL, "role": "staff", "password": "a-long-password"})
    assert refused.status_code == 422
    with env.factory() as db:
        from app.services import staff_directory
        assert fid not in {row["id"] for row in staff_directory.staff_availability(db, "Asia/Kolkata")["staff"]}


def test_the_email_address_is_free_again_and_the_reserved_domains_are_refused(env):
    delete(env, "admin", "d")
    act_as(env, "admin")
    again = env.client.post("/api/users", json={"name": "New D", "email": "d@example.test", "role": "staff", "password": "a-long-password"})
    assert again.status_code == 201, again.text
    refused = env.client.post("/api/users", json={"name": "Sneaky", "email": "x@deleted.invalid", "role": "staff", "password": "a-long-password"})
    assert refused.status_code == 422
    refused = env.client.post("/api/users", json={"name": "Sneaky", "email": "x@system.invalid", "role": "staff", "password": "a-long-password"})
    assert refused.status_code == 422


def test_the_audit_entry_names_who_what_and_to_whom(env):
    give_work(env, "a")
    assert delete(env, "md", "a", reassignTo="b").status_code == 200
    with env.factory() as db:
        entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "Deleted user")).one()
        assert (entry.actor_id, entry.entity_type, entry.entity_id, entry.target) == ("md", "user", "a", "A Person")
        assert entry.meta["candidates"] == 3 and entry.meta["tasks"] == 3 and entry.meta["leads"] == 1
        assert entry.meta["toUserId"] == "b" and entry.meta["mode"] == "reassign" and entry.meta["originalEmail"] == "a@example.test"
        assert entry.meta["revokedSessions"] == 1


# ----------------------------------------------------------------------------------------- their work

def test_a_choice_is_required_when_there_is_work_and_only_one_may_be_given(env):
    give_work(env, "a")
    assert delete(env, "admin", "a").status_code == 422
    both = delete(env, "admin", "a", reassignTo="b", leaveUnassigned="true")
    assert both.status_code == 422
    with env.factory() as db:
        assert not db.get(User, "a").deleted and db.get(Candidate, "cand-a-0").owner_id == "a"


def test_reassigning_moves_candidates_open_tasks_and_leads_and_keeps_history(env):
    give_work(env, "a")
    response = delete(env, "admin", "a", reassignTo="b")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["candidates"], body["tasks"], body["leads"], body["toUserId"]) == (3, 3, 1, "b")
    with env.factory() as db:
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {"b"}
        assert {t.owner_id for t in db.scalars(select(Task).where(Task.completed_at.is_(None)))} == {"b"}
        assert db.get(Task, "done-a").owner_id == former_id(db), "a finished task stays, under Former user"
        assert db.get(Lead, "lead-a").owner_id == "b"
        # History is kept and now belongs to "Former user": the remark, the audit entry the person made, the finished task's owner.
        fid = former_id(db)
        assert fid and gone(db, "a")
        remark = db.scalars(select(Remark)).one()
        assert remark.author_id == fid and db.get(User, remark.author_id).name == FORMER_USER_NAME
        made = db.scalars(select(AuditEntry).where(AuditEntry.action == "Recorded a call")).one()
        assert made.actor_id == fid
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.kind == "assignment")) == 3
        reassigned = list(db.scalars(select(AuditEntry).where(AuditEntry.action == "Candidate reassigned during account change")))
        assert len(reassigned) == 3 and all(e.meta["toUserId"] == "b" and e.meta["fromUserId"] == "a" for e in reassigned)
        assert all(e.actor_id == "admin" for e in reassigned)
        # The new recruiter is told; the deleted person's own notices, to-dos, goals and client allocation are gone.
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == "b")) >= 1
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == "a")) == 0
        assert db.scalar(select(func.count()).select_from(TodoItem).where(TodoItem.owner_id == "a")) == 0
        assert db.scalar(select(func.count()).select_from(DailyGoal).where(DailyGoal.user_id == "a")) == 0
        assert db.scalar(select(func.count()).select_from(ClientStaff).where(ClientStaff.user_id == "a")) == 0
        assert db.get(AssignmentPointer, 1).last_user_id is None


def test_leaving_unassigned_frees_candidates_and_leads_and_cancels_what_remains(env):
    give_work(env, "a")
    response = delete(env, "md", "a", leaveUnassigned="true")
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "unassign" and response.json()["candidates"] == 3
    with env.factory() as db:
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {None}
        assert db.get(Lead, "lead-a").owner_id is None
        open_tasks = list(db.scalars(select(Task).where(Task.owner_id.in_(["a", former_id(db)]), Task.completed_at.is_(None), Task.cancelled_at.is_(None))))
        assert open_tasks == [], "no open task is left with a person who is gone"
        assert db.get(Task, "done-a").completed_at is not None and db.get(Task, "done-a").owner_id == former_id(db)
        assert db.scalars(select(Remark)).one().author_id == former_id(db)
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Candidate unassigned")) == 3


def test_a_candidate_who_cannot_be_left_without_a_recruiter_blocks_the_unassign_choice_and_changes_nothing(env):
    give_work(env, "a")
    with env.factory() as db:
        db.get(Candidate, "cand-a-1").status = CandidateStatus.deployed
        db.commit()
    response = delete(env, "admin", "a", leaveUnassigned="true")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "cannot be left without a recruiter" in detail and "deployed" in detail and "deactivate the account instead" in detail
    with env.factory() as db:
        assert not db.get(User, "a").deleted and db.get(User, "a").active
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {"a"}
        assert db.get(Lead, "lead-a").owner_id == "a"
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action.in_(["Deleted user", "Candidate unassigned"]))) == 0
        assert db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == "a")) == 1
    # The same person can still be deleted by handing everything to a recruiter.
    assert delete(env, "admin", "a", reassignTo="b").status_code == 200


@pytest.mark.parametrize("recipient, words", [("a", "different active recruiter"), ("admin", "different active recruiter"), ("md", "different active recruiter")])
def test_the_recipient_must_be_another_active_recruiter(env, recipient, words):
    give_work(env, "a")
    response = delete(env, "admin", "a", reassignTo=recipient)
    assert response.status_code == 422 and words in response.json()["detail"], response.text
    with env.factory() as db:
        assert db.get(Candidate, "cand-a-0").owner_id == "a" and not db.get(User, "a").deleted


def test_a_recipient_who_is_inactive_or_on_leave_or_deleted_is_refused(env):
    give_work(env, "a")
    with env.factory() as db:
        db.get(User, "c").active = False
        db.get(User, "d").on_leave = True
        db.commit()
    assert delete(env, "admin", "a", reassignTo="c").status_code == 422
    assert delete(env, "admin", "a", reassignTo="d").status_code == 422
    assert delete(env, "admin", "b").status_code == 200
    assert delete(env, "admin", "a", reassignTo="b").status_code == 404


def test_a_deactivated_recruiter_with_nothing_is_deleted_without_any_choice(env):
    act_as(env, "admin")
    assert env.client.patch("/api/users/d", json={"active": False}).status_code == 200
    assert env.client.get("/api/users/d/delete-summary").json() == {"candidates": 0, "openTasks": 0, "leads": 0}
    assert delete(env, "admin", "d").status_code == 200


def test_the_summary_counts_what_the_dialog_will_show(env):
    give_work(env, "a")
    act_as(env, "admin")
    assert env.client.get("/api/users/a/delete-summary").json() == {"candidates": 3, "openTasks": 3, "leads": 1}


def test_a_failed_commit_is_one_calm_sentence_offering_deactivate_and_rolls_everything_back(env):
    give_work(env, "a")
    with env.factory() as db:
        actor = db.get(User, "admin")

        def refuse():
            raise IntegrityError("DELETE", {}, Exception("restrict"))
        db.commit = refuse
        with pytest.raises(HTTPException) as caught:
            user_admin.delete_user(db, actor, "a", "b")
        assert caught.value.status_code == 409 and "Deactivate it instead" in caught.value.detail
    with env.factory() as db:
        assert not db.get(User, "a").deleted and db.get(Candidate, "cand-a-0").owner_id == "a"


# ----------------------------------------------------------------------------------------- every reference is re-pointed, nothing breaks

FOREIGN_KEYS = """
SELECT k.relname AS tbl, a.attname AS col, cardinality(c.conkey) AS width
  FROM pg_constraint c JOIN pg_class k ON k.oid = c.conrelid JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
 WHERE c.contype = 'f' AND c.confrelid = 'auth_users'::regclass ORDER BY 1, 2"""


def test_every_foreign_key_to_auth_users_is_single_column_and_the_policy_lists_name_real_columns(env):
    with env.factory() as db:
        keys = list(db.execute(text(FOREIGN_KEYS)).all())
    assert len(keys) > 50 and all(row.width == 1 for row in keys), "the catalog block handles single-column keys"
    found = {f"{row.tbl}.{row.col}" for row in keys}
    for named in (*user_admin._VANISH, *user_admin._BLANK):
        assert named in found, f"{named} is named in the delete policy but is not a foreign key to auth_users"


def test_no_row_anywhere_still_points_at_the_deleted_person_even_in_a_table_the_code_has_never_heard_of(env):
    give_work(env, "a")
    with env.factory() as db:
        db.execute(text("CREATE TABLE zz_extra (id serial PRIMARY KEY, who varchar(36) NOT NULL REFERENCES auth_users(id), note text)"))
        db.execute(text("INSERT INTO zz_extra (who, note) VALUES ('a', 'history')"))
        db.add(TaskEvent(kind="assignment", task_id="done-a", candidate_id="cand-a-0", owner_id="a", event_key="k-a", occurred_at=NOW, meta={}))
        db.commit()
    assert delete(env, "admin", "a", reassignTo="b").status_code == 200
    with env.factory() as db:
        fid = former_id(db)
        assert db.scalar(text("SELECT who FROM zz_extra")) == fid
        leftovers = []
        for row in db.execute(text(FOREIGN_KEYS)).all():
            if row.tbl == "zz_extra":
                continue
            count = db.scalar(text(f'SELECT count(*) FROM "{row.tbl}" WHERE "{row.col}" = \'a\''))
            if count:
                leftovers.append((row.tbl, row.col, count))
        assert leftovers == []
        assert db.scalar(select(TaskEvent.owner_id).where(TaskEvent.event_key == "k-a")) == fid


def test_two_deleted_people_with_a_clashing_unique_row_and_open_attendance_do_not_block_each_other(env):
    from app.db.models import AttendanceCalendarDay, StaffAttendanceSession, StaffPresenceInterval
    with env.factory() as db:
        for who in ("c", "d"):
            db.add(AttendanceCalendarDay(user_id=who, attendance_date=NOW.date(), status="PL", reason="leave", actor_id="admin"))
            db.add(StaffAttendanceSession(user_id=who, checked_in_at=NOW, checkin_request_key=f"in-{who}"))
            db.add(StaffPresenceInterval(user_id=who, client_id="tab", state="online", started_at=NOW, last_seen_at=NOW))
        db.commit()
    assert delete(env, "admin", "c").status_code == 200
    assert delete(env, "admin", "d").status_code == 200
    with env.factory() as db:
        fid = former_id(db)
        sessions = list(db.scalars(select(StaffAttendanceSession).order_by(StaffAttendanceSession.checkin_request_key)))
        assert [s.user_id for s in sessions] == [fid, fid] and all(s.checked_out_at is not None for s in sessions), "ended, then kept as history"
        assert all(i.user_id == fid and i.ended_at is not None for i in db.scalars(select(StaffPresenceInterval)))
        assert db.scalar(select(func.count()).select_from(AttendanceCalendarDay)) == 0, "personal schedule rows go with the person"


def test_the_former_user_never_appears_in_the_attendance_report_or_the_daily_work_roster(env):
    from app.db.models import StaffAttendanceSession
    from app.services import staff_activity
    with env.factory() as db:
        db.add(StaffAttendanceSession(user_id="d", checked_in_at=NOW, checked_out_at=NOW + timedelta(hours=1), checkin_request_key="in-d"))
        db.commit()
    assert delete(env, "admin", "d").status_code == 200
    with env.factory() as db:
        fid = former_id(db)
        assert db.scalar(select(func.count()).select_from(StaffAttendanceSession).where(StaffAttendanceSession.user_id == fid)) == 1
        report = staff_activity.attendance_report(db, db.get(User, "admin"), NOW.date(), "Asia/Kolkata")
        text_of_report = repr(report)
        assert fid not in text_of_report and FORMER_USER_NAME not in text_of_report


# ----------------------------------------------------------------------------------------- the rest of the app tolerates it

def test_screens_that_list_people_skip_the_deleted_account(env):
    from app.services import staff_directory
    delete(env, "admin", "d")
    with env.factory() as db:
        ids = {row["id"] for row in staff_directory.staff_availability(db, "Asia/Kolkata")["staff"]}
        assert "d" not in ids and {"a", "b", "c", "admin"} <= ids


# ----------------------------------------------------------------------------------------- speed: round trips are the cost on the hosted database

def statements(env, action):
    counted: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        counted.append(statement)
    event.listen(env.engine, "before_cursor_execute", count)
    try:
        action()
    finally:
        event.remove(env.engine, "before_cursor_execute", count)
    return counted


def test_the_summary_is_one_statement(env):
    give_work(env, "a")
    with env.factory() as db:
        counted = statements(env, lambda: user_admin.delete_summary(db, "a"))
    assert len(counted) == 1, counted


def test_statement_budget_for_a_recruiter_with_nothing_to_move(env):
    with env.factory() as db:
        actor = db.get(User, "admin")
        counted = statements(env, lambda: user_admin.delete_user(db, actor, "d"))
    # lock + directory (2), what they hold (1), the audit entry, ONE block that re-points every foreign key, the DELETE; begin/commit are not statements.
    assert len(counted) <= 7, [c[:60] for c in counted]
    assert sum(1 for c in counted if c.lstrip().startswith("DO ")) == 1, "one server-side block, no per-table loop in Python"


def test_statement_budget_for_a_recruiter_with_many_candidates(env):
    give_work(env, "a", candidates=30)
    with env.factory() as db:
        actor = db.get(User, "admin")
        counted = statements(env, lambda: user_admin.delete_user(db, actor, "a", "b"))
    # The transfer is batched (writes are executemany), so thirty candidates cost the same round trips as three.
    assert len(counted) <= 25, [c[:60] for c in counted]  # measured: 25 (it was 41 with auth, before)
    give_work(env, "c", candidates=3)
    with env.factory() as db:
        actor = db.get(User, "admin")
        few = statements(env, lambda: user_admin.delete_user(db, actor, "c", "b"))
    assert abs(len(few) - len(counted)) <= 2, (len(few), len(counted))


def test_the_whole_request_stays_within_a_small_statement_budget(env):
    give_work(env, "a", candidates=30)
    counted = statements(env, lambda: delete(env, "admin", "a", reassignTo="b"))
    assert len(counted) <= 28, len(counted)  # measured 26; it was 41 before this change (tombstone, one query per table)
    quiet = statements(env, lambda: delete(env, "admin", "d"))
    assert len(quiet) <= 10, len(quiet)
