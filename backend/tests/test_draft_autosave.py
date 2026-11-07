"""Autosave of the Add candidate draft: PUT /api/ocr/jobs/groups/{group_id}/draft costs two statements, not eight.

Every statement against the hosted database is a round trip of 300-1000 ms, and the form saves while somebody types.
Synthetic data only, on the disposable loopback database.
"""
from uuid import uuid4

from sqlalchemy import event, func, select, text

from app.api.routers import document_jobs
from app.db import models as m
from test_candidate_workflows import db, make_client  # noqa: F401  (fixture)
from test_database_naming import schema_database  # noqa: F401  (fixture)

FORM = {"name": "Half Filled", "phone": "9000000001", "fatherName": "Suresh", "nativePlace": "Thrissur"}


def intake(db, count=2, owner="admin"):
    group = str(uuid4())
    jobs = [m.DocumentExtractionJob(owner_id=owner, group_id=group, kind=kind, filename=f"{kind}.pdf", content_type="application/pdf",
                                    object_key=f"synthetic/{kind}-{uuid4().hex}", content_hash=uuid4().hex * 2, size_kb=1, status="completed", draft={})
            for kind in ("resume", "passport", "aadhaar")[:count]]
    db.add_all(jobs)
    db.commit()
    api = make_client(db)
    api.app.include_router(document_jobs.router)
    return api, group, jobs


def draft_of(db, job):
    db.expire_all()
    return db.execute(text("SELECT draft FROM document_extraction_jobs WHERE id = :i"), {"i": job.id}).scalar()


class Statements:
    def __init__(self, db):
        self.engine, self.seen = db.get_bind(), []

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._note)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._note)

    def _note(self, conn, cursor, statement, parameters, context, executemany):
        self.seen.append(" ".join(statement.split()))

    @property
    def writes(self):
        return [s for s in self.seen if s.upper().startswith(("UPDATE", "INSERT", "WITH WRITTEN"))]


def test_a_draft_save_is_two_statements_for_any_number_of_documents(db):
    api, group, jobs = intake(db, count=3)
    body = {"form": FORM, "editedFields": ["fatherName"], "jobIds": [job.id for job in jobs]}
    with Statements(db) as statements:
        response = api.put(f"/api/ocr/jobs/groups/{group}/draft", json=body)
    assert response.status_code == 200 and response.json() == {"saved": True}
    sql = statements.seen
    # sign-in check aside (the test client's own), the route itself runs the guard and the write
    route = [s for s in sql if "document_extraction_jobs" in s]
    assert len(route) == 2, route
    for job in jobs:
        saved = draft_of(db, job)
        assert saved["form"] == FORM and saved["editedFields"] == ["fatherName"] and saved["jobIds"] == body["jobIds"]
    audits = db.scalars(select(m.AuditEntry).where(m.AuditEntry.entity_id == group)).all()
    assert len(audits) == 1 and audits[0].target == min(jobs, key=lambda job: job.id).filename and audits[0].actor_id == "admin"


def test_an_unchanged_draft_writes_nothing_and_adds_no_audit_line(db):
    api, group, jobs = intake(db)
    body = {"form": FORM, "editedFields": [], "jobIds": [job.id for job in jobs]}
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json=body).status_code == 200
    before = db.execute(text("SELECT updated_at FROM document_extraction_jobs WHERE id = :i"), {"i": jobs[0].id}).scalar()
    with Statements(db) as statements:
        again = api.put(f"/api/ocr/jobs/groups/{group}/draft", json={**body, "form": dict(reversed(list(FORM.items())))})
    assert again.status_code == 200 and again.json() == {"saved": True, "unchanged": True}
    assert not [s for s in statements.seen if s.upper().startswith(("UPDATE", "INSERT")) or "written AS" in s]
    db.expire_all()
    assert db.execute(text("SELECT updated_at FROM document_extraction_jobs WHERE id = :i"), {"i": jobs[0].id}).scalar() == before
    # one more keystroke is saved again, with the audit line still once per hour
    changed = api.put(f"/api/ocr/jobs/groups/{group}/draft", json={**body, "form": {**FORM, "name": "Half Filled Two"}})
    assert changed.json() == {"saved": True}
    assert db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.entity_id == group)) == 1


def test_autosave_does_not_wake_every_open_desk():
    from app.services.change_feed import classify_statement
    assert classify_statement("UPDATE document_extraction_jobs SET draft = %(d)s, updated_at = %(u)s WHERE group_id = %(g)s") is None
    assert classify_statement("UPDATE document_extraction_jobs SET status = 'completed', updated_at = %(u)s WHERE id = %(i)s") == ("UPDATE", "document_extraction_jobs")


def test_every_existing_check_still_answers_the_same(db):
    api, group, jobs = intake(db)
    ids = [job.id for job in jobs]
    other_group = str(uuid4())
    assert api.put(f"/api/ocr/jobs/groups/{other_group}/draft", json={"form": FORM, "jobIds": []}).status_code == 404
    assert api.put(f"/api/ocr/jobs/groups/not-a-uuid/draft", json={"form": FORM, "jobIds": []}).status_code == 422
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": FORM, "jobIds": [*ids, "someone-elses-job"]}).status_code == 422
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": {"bogusKey": "x"}, "jobIds": ids}).status_code == 422
    # a person-by-person review owns the group: autosave from an old tab must not touch it
    db.add(m.MultiPersonIntake(source_type="manual", source_id=group, owner_id="admin", created_by="admin", status="review"))
    db.commit()
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": FORM, "jobIds": ids}).status_code == 409


def test_a_draft_never_erases_the_candidate_it_was_saved_as(db):
    api, group, jobs = intake(db)
    candidate = m.Candidate(ref="DRAFT-1", source="walk_in", name="Saved Person", phone="9000000042", owner_id="admin", created_by="admin")
    db.add(candidate)
    db.commit()
    ids = [job.id for job in jobs]
    first = api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": FORM, "jobIds": ids, "savedCandidateId": candidate.id})
    assert first.status_code == 200, first.text
    assert draft_of(db, jobs[0])["savedCandidateId"] == candidate.id
    # an older tab, still holding the pre-save draft, autosaves afterwards: the binding stays
    stale = api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": {**FORM, "name": "Typed later"}, "jobIds": ids})
    assert stale.status_code == 200
    assert all(draft_of(db, job)["savedCandidateId"] == candidate.id and draft_of(db, job)["form"]["name"] == "Typed later" for job in jobs)
    other = m.Candidate(ref="DRAFT-2", source="walk_in", name="Someone Else", phone="9000000043", owner_id="admin", created_by="admin")
    db.add(other)
    db.commit()
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": FORM, "jobIds": ids, "savedCandidateId": other.id}).status_code == 409
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": FORM, "jobIds": ids, "savedCandidateId": "no-such-candidate"}).status_code in (404, 409)


def test_someone_elses_intake_is_not_found(db):
    api, group, jobs = intake(db, owner="admin")
    db.add(m.User(id="mallory", name="Mallory", email="mallory@example.test", role=m.Role.staff, active=True))
    db.commit()
    mine = intake(db, owner="mallory")[1]
    assert api.put(f"/api/ocr/jobs/groups/{mine}/draft", json={"form": FORM, "jobIds": []}).status_code == 404
