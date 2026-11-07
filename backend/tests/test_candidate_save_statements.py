"""Saving a candidate costs a fixed, small number of statements.

The hosted database costs a round trip per statement (about 300 ms), so the save is kept to few statements: the assignment cursor is one
locking read once its row exists, the guarded optional steps (lead matching, WhatsApp linking, the auto-assign setting) run without a
savepoint once they have worked, and the intake rows are read once. The duplicate rule is enforced by the save itself (409 with the match,
nothing written), so the form needs no separate check in front of it.
"""
from uuid import uuid4

from sqlalchemy import event, func, select

from app.db import models as m
from app.services import savepoint_once
from test_candidate_workflows import db, make_client  # noqa: F401  (db is a fixture)
from test_database_naming import schema_database  # noqa: F401  (fixture)

MAX_CREATE_STATEMENTS = 15  # was 22 (3 of them savepoints); the login lookup is cached in production


class Counter:
    def __init__(self, engine):
        self.engine, self.statements = engine, []

    def _run(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(" ".join(statement.split())[:90])

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._run)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._run)


def intake(db):
    job = m.DocumentExtractionJob(owner_id="admin", group_id=str(uuid4()), kind="resume", filename="r.pdf", content_type="application/pdf",
                                  object_key="k", content_hash=uuid4().hex * 2, size_kb=1, status="uploaded", draft={})
    db.add(job)
    db.commit()
    return job


def warm(api):
    assert api.post("/api/candidates", json={"name": "Warm Up", "phone": "9000000088"}).status_code == 201


def test_create_with_an_upload_is_a_few_statements_without_savepoints(db):
    api = make_client(db)
    warm(api)
    job = intake(db)
    savepoint_once.forget()
    warm_again = api.post("/api/candidates", json={"name": "Second Warm", "phone": "9000000089"})  # proves the guarded blocks once
    assert warm_again.status_code == 201
    with Counter(db.get_bind()) as counter:
        response = api.post("/api/candidates", json={"name": "Probe Person", "phone": "9000000077", "email": "p@example.com", "intake_group_id": job.group_id})
    assert response.status_code == 201, response.text
    assert len(counter.statements) <= MAX_CREATE_STATEMENTS, counter.statements
    assert not [s for s in counter.statements if "SAVEPOINT" in s], counter.statements
    assert sum("workflow_assignment_cursor" in s for s in counter.statements) == 1, counter.statements
    db.expire_all()
    assert db.get(m.DocumentExtractionJob, job.id).draft["savedCandidateId"] == response.json()["id"]


def test_the_first_run_of_each_guarded_step_is_still_inside_a_savepoint(db):
    api = make_client(db)
    savepoint_once.forget()
    with Counter(db.get_bind()) as counter:
        assert api.post("/api/candidates", json={"name": "First Ever", "phone": "9000000090"}).status_code == 201
    assert any("SAVEPOINT" in s for s in counter.statements), counter.statements


def test_a_duplicate_is_refused_before_anything_is_written_and_names_the_match(db):
    api = make_client(db)
    first = api.post("/api/candidates", json={"name": "Existing Person", "phone": "9000000091"})
    assert first.status_code == 201
    before = db.scalar(select(func.count()).select_from(m.Candidate))
    again = api.post("/api/candidates", json={"name": "Same Person", "phone": "+91 90000 00091"})
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert detail["candidate"]["id"] == first.json()["id"] and detail["candidate"]["matchedOn"] == ["phone number"]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(m.Candidate)) == before


def test_the_duplicate_check_is_a_read_that_takes_no_row_locks(db):
    api = make_client(db)
    api.post("/api/candidates", json={"name": "Existing Person", "phone": "9000000092"})
    with Counter(db.get_bind()) as counter:
        found = api.post("/api/candidates/check-duplicate", json={"phone": "9000000092"})
    assert found.status_code == 200 and found.json()["duplicate"] is True
    assert not [s for s in counter.statements if "FOR UPDATE" in s.upper()], counter.statements
