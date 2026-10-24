"""A lightweight completion revision refreshes every signed-in desk safely.

Only the guarded disposable PostgreSQL fixture is used. Extraction and mailbox
adapters remain the intake fixture's synthetic implementations.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from app.core.security import SESSION_COOKIE
from app.db.models import (
    Candidate, CandidateStatus, EmailIngestionJob, Role, Session as SessionRow, Source, User,
    WhatsAppChannel, WhatsAppMessage,
)
from app.services.ingestion_revision import read_revision, revision_expression
from test_bootstrap_performance import (
    EMPTY_REVISION, assert_no_private_data, assert_select_budget, recorded_statements,
)
from test_database_naming import schema_database
from test_email_intake import (
    ORIGINAL, extraction, fake_extraction, intake, receipt, row_count, run,
)


@pytest.fixture
def revision_client(intake, monkeypatch):
    # Importing a router must not initialize a connection from backend/.env.
    with patch("app.core.config.get_settings", return_value=SimpleNamespace(
            agency_timezone="Asia/Kolkata",
        database_url=intake.engine.url.render_as_string(hide_password=False),
    )):
        from app.api.routers import data, inbox
        from app.db.session import get_db

    monkeypatch.setattr(data, "get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
    with intake.sessions() as db:
        db.add(User(id="revision-manager", name="Manager", email="manager@example.test", role=Role.manager))
        db.flush()
        for user_id in ("admin", "staff-a", "revision-manager"):
            db.add(SessionRow(
                id=f"revision-{user_id}-session", user_id=user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            ))
        db.commit()

    application = FastAPI()
    application.include_router(data.router)
    application.include_router(inbox.router)

    def isolated_db():
        with intake.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db
    with TestClient(application, base_url="https://testserver") as client:
        client.cookies.set(SESSION_COOKIE, "revision-admin-session")
        yield client


def request_revision(client, engine):
    with recorded_statements(engine) as statements:
        response = client.get("/api/inbox/revision")
    assert response.status_code == 200
    assert_select_budget(statements, 2)  # Existing session check plus one scalar.
    assert set(response.json()) == {"revision"}
    revision = response.json()["revision"]
    assert isinstance(revision, str) and revision
    assert_no_private_data(response.json())
    for forbidden in ("state", "inspection", "last_error", "candidate_id", "message_id"):
        assert f"email_ingestion_jobs.{forbidden}" not in statements[-1]
    assert "email_messages" not in statements[-1]
    assert "email_accounts" not in statements[-1]
    assert "whatsapp_messages.processing_state" not in statements[-1]
    assert "crm_candidates.resume_text" not in statements[-1]
    return revision


def request_bootstrap(client, engine, expected_revision):
    with recorded_statements(engine) as statements:
        response = client.get("/api/bootstrap")
    assert response.status_code == 200
    assert_select_budget(statements, 2)  # Revision is part of the same snapshot SQL.
    result = response.json()
    assert result["ingestionRevision"] == expected_revision
    assert_no_private_data(result)
    return result


def test_revision_sql_compiles_to_private_data_free_scalar():
    statement = select(revision_expression())
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert len(statement.selected_columns) == 1
    assert "email_ingestion_jobs.updated_at" in sql
    assert "email_ingestion_jobs.status" in sql
    assert "whatsapp_messages.updated_at" in sql and "whatsapp_messages.status" in sql
    assert "crm_candidates.updated_at" in sql
    assert "completed" in compiled.params.values()
    for excluded in (
        "email_ingestion_jobs.state", "email_ingestion_jobs.inspection",
        "email_ingestion_jobs.message_id", "email_ingestion_jobs.last_error",
        "email_messages", "email_accounts", "document_attachments",
        "whatsapp_messages.processing_state", "whatsapp_messages.body", "whatsapp_messages.sender_number",
        "whatsapp_messages.object_key", "whatsapp_messages.last_error",
        "crm_candidates.resume_text", "crm_candidates.phone", "crm_candidates.name", "crm_candidates.email",
    ):
        assert excluded not in sql


def test_pulse_returns_revision_and_unread_badge_in_one_request(intake, revision_client):
    revision = request_revision(revision_client, intake.engine)
    with recorded_statements(intake.engine) as statements:
        response = revision_client.get("/api/inbox/pulse")
    assert response.status_code == 200
    assert_select_budget(statements, 3)  # Session check plus the revision and the unread count, read side by side.
    body = response.json()
    assert set(body) == {"revision", "unread"}
    assert body["revision"] == revision
    assert set(body["unread"]) == {"total", "accounts"}
    assert_no_private_data(body)


def test_empty_revision_agrees_with_bootstrap_without_extra_queries(intake, revision_client):
    revision = request_revision(revision_client, intake.engine)
    assert revision == EMPTY_REVISION
    body = request_bootstrap(revision_client, intake.engine, revision)
    assert body["candidates"] == []


@pytest.mark.parametrize("user_id", ["admin", "staff-a", "revision-manager"])
def test_revision_is_available_to_every_signed_in_role_without_mailbox_details(
    intake, revision_client, user_id,
):
    revision_client.cookies.set(SESSION_COOKIE, f"revision-{user_id}-session")
    assert request_revision(revision_client, intake.engine) == EMPTY_REVISION


def test_revision_requires_current_active_session_before_reading_completion_metadata(intake, revision_client):
    revision_client.cookies.clear()
    with recorded_statements(intake.engine) as statements:
        response = revision_client.get("/api/inbox/revision")
    assert response.status_code == 401 and statements == []

    revision_client.cookies.set(SESSION_COOKIE, "nonexistent-session")
    with recorded_statements(intake.engine) as statements:
        response = revision_client.get("/api/inbox/revision")
    assert response.status_code == 401
    assert_select_budget(statements, 1)
    assert "email_ingestion_jobs" not in statements[0]

    revision_client.cookies.set(SESSION_COOKIE, "revision-staff-a-session")
    assert request_revision(revision_client, intake.engine) == EMPTY_REVISION
    with intake.sessions() as db:
        db.get(User, "staff-a").active = False
        db.commit()
    with recorded_statements(intake.engine) as statements:
        response = revision_client.get("/api/inbox/revision")
    assert response.status_code == 401
    assert not any("email_ingestion_jobs" in statement for statement in statements)

    revision_client.cookies.set(SESSION_COOKIE, "revision-admin-session")
    with intake.sessions() as db:
        db.delete(db.get(SessionRow, "revision-admin-session"))
        db.commit()
    with recorded_statements(intake.engine) as statements:
        response = revision_client.get("/api/inbox/revision")
    assert response.status_code == 401
    assert not any("email_ingestion_jobs" in statement for statement in statements)


def test_completed_new_candidate_and_existing_candidate_enrichment_both_advance_revision(
    intake, revision_client, monkeypatch,
):
    before = request_revision(revision_client, intake.engine)
    first_job, _ = receipt(intake)
    first_calls = fake_extraction(monkeypatch)
    assert request_revision(revision_client, intake.engine) == before
    run(intake, first_job)
    created = request_revision(revision_client, intake.engine)
    assert created != before
    body = request_bootstrap(revision_client, intake.engine, created)
    assert len(body["candidates"]) == 1
    candidate_id = body["candidates"][0]["id"]
    assert body["candidates"][0]["passportNo"] is None
    assert len(body["tasks"]) == 1
    assert len(first_calls) == 1

    second_job, _ = receipt(intake, uid=12, data=ORIGINAL + b"\nnew source evidence")
    second_calls = fake_extraction(monkeypatch, extraction(fields={"passport_no": "P1234567"}))
    assert request_revision(revision_client, intake.engine) == created
    run(intake, second_job)
    enriched = request_revision(revision_client, intake.engine)
    assert enriched != created
    body = request_bootstrap(revision_client, intake.engine, enriched)
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["id"] == candidate_id
    assert body["candidates"][0]["passportNo"] == "P1234567"
    assert len(body["tasks"]) == 1
    with intake.sessions() as db:
        assert row_count(db, Candidate) == 1
        assert db.get(EmailIngestionJob, second_job).candidate_id == candidate_id
    assert len(second_calls) == 1
    # Reprocessing a completed receipt must not trigger an endless refresh loop.
    run(intake, second_job)
    assert request_revision(revision_client, intake.engine) == enriched


def test_unfinished_failed_and_ignored_jobs_do_not_change_completed_revision(
    intake, revision_client, monkeypatch,
):
    completed, _ = receipt(intake)
    fake_extraction(monkeypatch)
    run(intake, completed)
    before = request_revision(revision_client, intake.engine)
    unfinished, _ = receipt(intake, uid=12, data=ORIGINAL + b"\nunfinished")
    for number, status in enumerate(("pending", "processing", "retry", "needs_review", "failed", "ignored"), 1):
        with intake.sessions() as db:
            job = db.get(EmailIngestionJob, unfinished)
            job.status = status
            job.updated_at = datetime(2040, 1, number, tzinfo=timezone.utc)
            job.state = {"private": "raw-provider-state-must-not-leave-database"}
            job.last_error = "private-extraction-details"
            db.commit()
        assert request_revision(revision_client, intake.engine) == before


def test_revision_is_transactional_and_identical_across_connection_timezones(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch)
    run(intake, job_id)
    with intake.sessions() as observer:
        initial = read_revision(observer)
    # Candidate enrichment and the completion timestamp become visible together,
    # even when a timestamp changes on an existing completed receipt.
    with intake.sessions() as writer:
        job = writer.get(EmailIngestionJob, job_id)
        writer.get(Candidate, job.candidate_id).current_city = "Updated city"
        job.updated_at = datetime(2040, 1, 1, 12, 34, 56, 123456, tzinfo=timezone.utc)
        writer.flush()
        with intake.sessions() as observer:
            assert read_revision(observer) == initial
            assert observer.get(Candidate, job.candidate_id).current_city is None
        writer.commit()
    with intake.sessions() as observer:
        observer.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        utc_revision = read_revision(observer)
        observer.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
        india_revision = read_revision(observer)
        assert utc_revision == india_revision != initial
        assert observer.scalar(select(Candidate.current_city)) == "Updated city"


def add_whatsapp(intake, status="pending"):
    with intake.sessions() as db:
        if db.get(WhatsAppChannel, "channel-1") is None:
            db.add(WhatsAppChannel(phone_number_id="channel-1"))
            db.flush()
        message = WhatsAppMessage(
            provider_message_id=f"wamid.{status}", phone_number_id="channel-1", sender_number="+910000000099",
            message_type="document", received_at=datetime(2026, 10, 20, tzinfo=timezone.utc), status=status,
        )
        db.add(message)
        db.commit()
        return message.id


def test_whatsapp_completion_advances_revision_but_unfinished_receipts_do_not(intake, revision_client):
    before = request_revision(revision_client, intake.engine)
    message_id = add_whatsapp(intake, "pending")
    assert request_revision(revision_client, intake.engine) == before
    for status in ("processing", "retry", "needs_review", "failed", "ignored"):
        with intake.sessions() as db:
            db.get(WhatsAppMessage, message_id).status = status
            db.commit()
        assert request_revision(revision_client, intake.engine) == before
    with intake.sessions() as db:
        db.get(WhatsAppMessage, message_id).status = "completed"
        db.commit()
    completed = request_revision(revision_client, intake.engine)
    assert completed != before
    assert completed.split(":")[2:4] != before.split(":")[2:4]  # Only the WhatsApp segment moved.
    assert request_revision(revision_client, intake.engine) == completed


def test_candidate_creation_edit_and_deletion_advance_revision(intake, revision_client):
    before = request_revision(revision_client, intake.engine)
    with intake.sessions() as db:
        candidate = Candidate(ref="REV-1", name="Revision Person", phone="+910000000001",
                              source=Source.whatsapp, status=CandidateStatus.new)
        db.add(candidate)
        db.commit()
        candidate_id = candidate.id
    created = request_revision(revision_client, intake.engine)
    assert created != before and created.split(":")[:4] == before.split(":")[:4]
    assert request_revision(revision_client, intake.engine) == created
    # Bundle commits and merges edit existing candidates without creating one.
    with intake.sessions() as db:
        db.get(Candidate, candidate_id).current_city = "Merged city"
        db.commit()
    edited = request_revision(revision_client, intake.engine)
    assert edited != created
    with intake.sessions() as db:
        db.delete(db.get(Candidate, candidate_id))
        db.commit()
    deleted = request_revision(revision_client, intake.engine)
    assert deleted not in {created, edited}
    assert deleted.split(":")[4] == "0"
