"""Recruiters are assigned by hand unless the agency switches automatic assignment on (Agency settings, `autoAssign`).

Covers every place that used to pick a recruiter for a new candidate: email and WhatsApp intake, the manual Add
candidate form and the person-by-person document review, plus the settings round trip. Synthetic data, disposable DB.
"""

from sqlalchemy import func, select, text

from app.db import models as m
from app.services import agency_settings as service
from app.services.candidate_review import WAITING_TITLE
from test_candidate_workflows import db  # noqa: F401  (fixture)
from test_candidate_entry_recovery import client, intake as manual_intake
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_document_person_split import commit, env, prepare  # noqa: F401  (fixtures)
from test_email_intake import extraction, fake_extraction, intake, receipt, run, ORIGINAL  # noqa: F401  (fixtures)
from test_whatsapp_intake import receipt as whatsapp_receipt, setup  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture)
from app.services.whatsapp_intake import process_message


def switch(engine, on):
    with engine.begin() as connection:
        connection.execute(text("UPDATE agency_settings SET auto_assign = :on WHERE id = 1"), {"on": on})


# ------------------------------------------------------------------ default: off

def test_the_setting_is_off_by_default_and_reads_back(db):
    assert db.get(m.AgencySettings, 1).auto_assign is False
    assert service.auto_assign_enabled(db) is False
    assert service.read_payload(db)["autoAssign"] is False


def test_email_intake_leaves_the_candidate_unassigned_by_default(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction())
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(m.Candidate))
        assert candidate.owner_id is None and candidate.intake_owner_id is None
        assert candidate.needs_review and candidate.status == m.CandidateStatus.needs_review
        assert db.scalar(select(func.count()).select_from(m.Task)) == 0
        assert db.get(m.EmailIngestionJob, job_id).status == "completed"
        # Admins and managers hear that the candidate needs a person to assign it.
        assert db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.title == WAITING_TITLE)) >= 1


def test_email_intake_round_robin_works_when_switched_on(intake, monkeypatch):
    switch(intake.engine, True)
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction())
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(m.Candidate))
        assert candidate.owner_id == "staff-a" and db.scalar(select(func.count()).select_from(m.Task)) == 1


def test_whatsapp_intake_leaves_the_candidate_unassigned_by_default(setup):
    sessions, settings = setup
    message_id = whatsapp_receipt(sessions, settings)
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: extraction(fields={"phone": "9000000077", "email": ""}))
    with sessions() as db:
        row = db.get(m.WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        candidate = db.get(m.Candidate, row.candidate_id)
        assert candidate.owner_id is None and candidate.needs_review
        assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.candidate_id == candidate.id)) == 0


def test_whatsapp_intake_round_robin_works_when_switched_on(setup):
    sessions, settings = setup
    with sessions() as db:
        switch(db.get_bind(), True)
    message_id = whatsapp_receipt(sessions, settings)
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: extraction(fields={"phone": "9000000077", "email": ""}))
    with sessions() as db:
        row = db.get(m.WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        assert db.get(m.Candidate, row.candidate_id).owner_id == "owner"


# ------------------------------------------------------------------ manual Add candidate

BODY = {"name": "Manual Person", "phone": "9000000044"}


def test_manual_add_by_an_admin_leaves_it_unassigned_unless_a_recruiter_is_chosen(db):
    db.add(m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager))
    db.commit()
    api = client(db)
    created = api.post("/api/candidates", json=BODY)
    assert created.status_code == 201, created.text
    assert created.json()["ownerId"] is None and created.json()["taskId"] is None
    chosen = api.post("/api/candidates", json={"name": "Chosen Person", "phone": "9000000045", "assign_to": "b"})
    assert chosen.status_code == 201 and chosen.json()["ownerId"] == "b" and chosen.json()["taskId"]
    db.expire_all()
    waiting = db.get(m.Candidate, created.json()["id"])
    assert waiting.needs_review and waiting.status == m.CandidateStatus.needs_review
    # The admin who added it is not told of their own action; the manager hears that it is waiting for a recruiter.
    waiting_for = list(db.scalars(select(m.Notification.user_id).where(m.Notification.title == WAITING_TITLE,
                                                                       m.Notification.link_to.like(f"/candidates/{waiting.id}/%"))))
    assert waiting_for == ["manager"]


def test_a_staff_member_who_adds_a_candidate_keeps_it(db):
    created = client(db, "a").post("/api/candidates", json=BODY)
    assert created.status_code == 201 and created.json()["ownerId"] == "a" and created.json()["taskId"]


def test_manual_add_round_robin_works_when_switched_on(db):
    switch(db.get_bind(), True)
    db.commit()
    created = client(db).post("/api/candidates", json=BODY)
    assert created.status_code == 201 and created.json()["ownerId"] == "a"


# ------------------------------------------------------------------ person-by-person review

def test_confirming_people_leaves_them_unassigned_by_default_but_a_staff_confirmer_keeps_their_own(env):
    bundle = prepare(env)
    assert commit(env, bundle).status_code == 200
    with env.sessions() as db:
        rows = list(db.scalars(select(m.Candidate)))
        assert len(rows) == 2 and all(row.owner_id is None and row.needs_review for row in rows)
        assert db.scalar(select(func.count()).select_from(m.Task)) == 0


def test_confirming_people_round_robin_works_when_switched_on(env):
    with env.sessions() as db:
        switch(db.get_bind(), True)
    bundle = prepare(env)
    assert commit(env, bundle).status_code == 200
    with env.sessions() as db:
        assert {row.owner_id for row in db.scalars(select(m.Candidate))} == {env.staff, env.other}


# ------------------------------------------------------------------ the explicit sweep and the settings API

def test_automatic_sweep_is_skipped_when_off_but_the_admin_button_still_distributes(db):
    from app.services.candidate_review import assign_waiting_candidates
    from app.services.user_admin import _assign_waiting_after_change
    db.add(m.Candidate(ref="CND-WAIT", name="Waiting Person", phone="9000000050", source=m.Source.walk_in))
    db.commit()
    _assign_waiting_after_change(db, "admin", "Admin")
    assert db.scalar(select(m.Candidate.owner_id).where(m.Candidate.ref == "CND-WAIT")) is None
    result = assign_waiting_candidates(db, "admin", "Admin")
    db.commit()
    assert result["assigned"] == 1
    assert db.scalar(select(m.Candidate.owner_id).where(m.Candidate.ref == "CND-WAIT")) == "a"


def test_settings_api_round_trip(schema_database, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from test_database_naming import _migrate
    with patch("app.core.config.get_settings", return_value=SimpleNamespace(
            database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", agency_timezone="Asia/Kolkata", storage_dir="unused")):
        from app.api.routers import settings as subject
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
    _migrate(schema_database, "head")
    service.reset_cache()
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add(m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin, active=True))
        db.commit()
        actor = db.get(m.User, "admin")
    app = FastAPI()
    app.include_router(subject.router)

    def database():
        with sessions() as session:
            yield session
    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: actor
    good = {"name": "Example Agency", "shortName": "Example", "defaultOrderCurrency": "SAR", "defaultAdvanceCurrency": "USD",
            "homeCallingCode": "+44", "defaultNationality": "", "acknowledgementText": "Thank you for writing to Example Agency."}
    with TestClient(app) as api:
        assert api.get("/api/settings").json()["autoAssign"] is False
        assert api.put("/api/settings", json=good).json()["autoAssign"] is False  # not sent: unchanged
        saved = api.put("/api/settings", json={**good, "autoAssign": True})
        assert saved.status_code == 200 and saved.json()["autoAssign"] is True
        service.reset_cache()
        assert api.get("/api/settings").json()["autoAssign"] is True
        assert api.put("/api/settings", json=good).json()["autoAssign"] is True  # an older desk that omits it changes nothing
        assert api.put("/api/settings", json={**good, "autoAssign": False}).json()["autoAssign"] is False
    with sessions() as db:
        assert service.auto_assign_enabled(db) is False
        assert db.scalar(select(func.count()).select_from(m.AuditEntry).where(
            m.AuditEntry.action == "Updated agency settings")) == 3  # the name change, then on, then off


# ------------------------------------------------------------------ client staff allocation is not automatic assignment

def test_client_staff_routing_still_applies_when_automatic_assignment_is_off(intake, monkeypatch):
    from test_client_agency import allocate_welder_client
    allocate_welder_client(intake.sessions, "staff-b")
    job, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction())  # designation Welder: the client's allocated staff
    run(intake, job)
    with intake.sessions() as db:
        candidate = db.scalar(select(m.Candidate))
        assert candidate.owner_id == candidate.intake_owner_id == "staff-b"
        assert db.scalar(select(m.Task.owner_id).where(m.Task.candidate_id == candidate.id)) == "staff-b"


def test_without_a_client_match_the_candidate_stays_unassigned_when_off(intake, monkeypatch):
    from test_client_agency import allocate_welder_client
    allocate_welder_client(intake.sessions, "staff-b")
    job, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields={"designation": "Plumber"}))
    run(intake, job)
    with intake.sessions() as db:
        assert db.scalar(select(m.Candidate.owner_id)) is None
