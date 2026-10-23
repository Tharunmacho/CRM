"""Inbox unread badge and the dashboard's failed-intake list.

Only the guarded disposable PostgreSQL fixture is used; mailbox, OCR and WhatsApp
providers are never contacted.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.security import SESSION_COOKIE
from app.db.models import (
    DocumentExtractionJob, EmailIngestionJob, MailAccount, MailAttachment, MailMessage, Role,
    Session as SessionRow, User, WhatsAppChannel, WhatsAppMessage,
)
from test_bootstrap_performance import assert_no_private_data, assert_select_budget, recorded_statements
from test_email_intake import intake, receipt  # noqa: F401
from test_database_naming import schema_database  # noqa: F401

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def desk(intake):
    with patch("app.core.config.get_settings", return_value=SimpleNamespace(
        agency_timezone="Asia/Kolkata",
        database_url=intake.engine.url.render_as_string(hide_password=False),
    )):
        from app.api.routers import inbox
        from app.db.session import get_db

    with intake.sessions() as db:
        db.add_all([
            User(id="attention-manager", name="Manager", email="manager@example.test", role=Role.manager),
            User(id="attention-staff", name="Staff", email="staff@example.test", role=Role.staff),
        ])
        db.flush()
        for user_id in ("admin", "attention-manager", "attention-staff"):
            db.add(SessionRow(id=f"{user_id}-session", user_id=user_id, expires_at=NOW + timedelta(days=3650)))
        db.commit()

    application = FastAPI()
    application.include_router(inbox.router)

    def isolated_db():
        with intake.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db
    with TestClient(application, base_url="https://testserver") as client:
        client.as_user = lambda user_id: client.cookies.set(SESSION_COOKIE, f"{user_id}-session")
        client.as_user("admin")
        yield client


def add_message(intake, account, number, *, unread=True, folder="INBOX", deleted=False, subject=None):
    with intake.sessions() as db:
        message = MailMessage(account_id=account, provider_message_id=f"unread:{account}:{number}",
                              received_at=NOW, unread=unread, folder=folder, remote_deleted=deleted, subject=subject)
        db.add(message)
        db.commit()
        return message.id


def test_unread_count_is_grouped_per_connected_live_inbox_and_open_to_every_role(intake, desk):
    for number in range(3):
        add_message(intake, "account-a", number)
    add_message(intake, "account-b", 1)
    add_message(intake, "account-a", 10, unread=False)
    add_message(intake, "account-a", 11, folder="Sent")
    add_message(intake, "account-a", 12, deleted=True)
    with intake.sessions() as db:
        db.add(MailAccount(id="account-gone", address="gone@example.test", provider="imap", sync_state={},
                           disconnected_at=NOW))
        db.add(MailAccount(id="account-old", address="old@example.test", provider="legacy-provider", sync_state={}))
        db.commit()
    add_message(intake, "account-gone", 1)
    add_message(intake, "account-old", 1)

    for user_id in ("admin", "attention-manager", "attention-staff"):
        desk.as_user(user_id)
        with recorded_statements(intake.engine) as statements:
            response = desk.get("/api/inbox/unread-count")
        assert response.status_code == 200
        assert_select_budget(statements, 2)  # Session check plus one grouped count.
        assert response.json() == {"total": 4, "accounts": {"account-a": 3, "account-b": 1}}
        assert_no_private_data(response.json())
        assert "email_attachments" not in statements[-1] and "email_ingestion_jobs" not in statements[-1]
        assert "GROUP BY" in statements[-1].upper()


def test_unread_count_is_zero_without_unread_mail_and_requires_a_session(intake, desk):
    assert desk.get("/api/inbox/unread-count").json() == {"total": 0, "accounts": {}}
    desk.cookies.clear()
    assert desk.get("/api/inbox/unread-count").status_code == 401
    assert desk.get("/api/inbox/attention").status_code == 401


def failed_email(intake, uid, status="failed", *, subject="Welder CV", error="Document processing failed.", updated=None,
                 state=None, account="account-a"):
    job_id, part_id = receipt(intake, account=account, uid=uid, data=f"attention document {uid}".encode())
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        message = db.get(MailMessage, job.message_id)
        message.subject, message.from_name, message.from_address = subject, "Asha Applicant", "asha@example.test"
        db.get(MailAttachment, part_id).file_name = f"cv-{uid}.pdf"
        job.status, job.last_error, job.attempts = status, error, 4
        job.state = state if state is not None else {"private": "raw-provider-state-must-not-leave-database"}
        job.updated_at = updated or NOW
        db.commit()
    return job_id, part_id


def add_whatsapp(intake, name, status="failed", *, media="media-1", error="Media download failed.", updated=NOW, enabled=True):
    with intake.sessions() as db:
        if db.get(WhatsAppChannel, "channel-1") is None:
            db.add(WhatsAppChannel(phone_number_id="channel-1", display_number="+91 80000 00000", enabled=enabled))
            db.flush()
        row = WhatsAppMessage(
            provider_message_id=f"wamid.{name}", phone_number_id="channel-1", sender_number="+910000000099",
            sender_name="Ravi", message_type="document", file_name=f"{name}.pdf", media_id=media, received_at=NOW,
            status=status, attempts=2, last_error=error, body="PRIVATE BODY TEXT",
            processing_state={"private": "raw-ocr-must-not-leave-database"}, updated_at=updated,
        )
        db.add(row)
        db.commit()
        return row.id


def add_manual(intake, name, owner="admin", status="failed", *, kind="passport", updated=NOW, error="Could not read the passport."):
    with intake.sessions() as db:
        row = DocumentExtractionJob(
            owner_id=owner, group_id=str(uuid5(NAMESPACE_URL, name)), kind=kind,
            filename=f"{name}.pdf", content_type="application/pdf", object_key=f"private-verification/{name}",
            content_hash=hashlib.sha256(name.encode()).hexdigest(), size_kb=1, status=status, attempts=3, last_error=error,
            updated_at=updated,
        )
        db.add(row)
        db.commit()
        return row.id


def test_attention_lists_failed_and_needs_review_intake_from_every_source_newest_first(intake, desk):
    email_failed, _ = failed_email(intake, 11, "failed", updated=NOW - timedelta(minutes=1))
    email_review, _ = failed_email(intake, 12, "needs_review", subject="Cook CV", error="Pages need review.", updated=NOW)
    for uid, status in enumerate(("completed", "pending", "processing", "retry", "ignored"), 20):
        failed_email(intake, uid, status)
    wa_failed = add_whatsapp(intake, "scan", "failed", updated=NOW - timedelta(minutes=2))
    add_whatsapp(intake, "fine", "completed")
    add_whatsapp(intake, "queued", "pending")
    manual_review = add_manual(intake, "passport-a", "attention-staff", "needs_review", updated=NOW - timedelta(minutes=3))
    add_manual(intake, "done", "admin", "attached")

    with recorded_statements(intake.engine) as statements:
        response = desk.get("/api/inbox/attention")
    assert response.status_code == 200
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    body = response.json()
    assert_no_private_data(body)
    serialized = str(body)
    for private in ("PRIVATE BODY TEXT", "raw-provider-state", "raw-ocr-must", "private-verification"):
        assert private not in serialized

    assert [item["id"] for item in body["items"]] == [
        f"email:{email_review}", f"email:{email_failed}", f"whatsapp:{wa_failed}", f"manual:{manual_review}",
    ]
    review, failed, whatsapp, manual = body["items"]
    assert (review["source"], review["status"], review["title"], review["reason"]) == (
        "email", "needs_review", "Cook CV", "Pages need review.")
    assert failed["sender"] == "Asha Applicant" and failed["channel"] == "inbox-a@example.test"
    assert failed["files"] == ["cv-11.pdf"] and failed["attempts"] == 4 and failed["candidateId"] is None
    assert failed["messageId"] and failed["jobId"] == email_failed and failed["updatedAt"]
    assert (whatsapp["source"], whatsapp["title"], whatsapp["sender"], whatsapp["files"]) == (
        "whatsapp", "scan.pdf", "Ravi", ["scan.pdf"])
    assert whatsapp["channel"] == "+91 80000 00000" and whatsapp["reason"] == "Media download failed."
    assert (manual["source"], manual["title"], manual["sender"], manual["status"]) == (
        "manual", "passport-a.pdf", "Staff", "needs_review")
    assert body["totals"] == {"email": 2, "whatsapp": 1, "manual": 1, "all": 4}
    assert body["limit"] == 20 and body["canRetry"] is True


def test_only_admin_may_retry_and_only_retryable_rows_are_flagged(intake, desk):
    ok, _ = failed_email(intake, 11)
    unreceivable, _ = failed_email(intake, 12, state={"receive_error": True}, error="Message could not be imported.")
    with intake.sessions() as db:
        db.add(MailAccount(id="account-off", address="off@example.test", provider="imap", sync_state={}, disconnected_at=NOW))
        db.commit()
    off_job, _ = failed_email(intake, 13, account="account-off")
    wa_ok = add_whatsapp(intake, "retryable")
    wa_nomedia = add_whatsapp(intake, "nomedia", media=None)
    mine = add_manual(intake, "mine", "admin")
    theirs = add_manual(intake, "theirs", "attention-staff")
    attachment = add_manual(intake, "photo", "admin", kind="photo")

    def flags(user_id):
        desk.as_user(user_id)
        response = desk.get("/api/inbox/attention")
        assert response.status_code == 200
        return response.json()["canRetry"], {item["jobId"]: item["retryable"] for item in response.json()["items"]}

    can_retry, retryable = flags("admin")
    assert can_retry is True
    assert retryable == {ok: True, unreceivable: False, off_job: False, wa_ok: True, wa_nomedia: False,
                         mine: True, theirs: False, attachment: False}
    can_retry, retryable = flags("attention-manager")
    assert can_retry is False and set(retryable) == {ok, unreceivable, off_job, wa_ok, wa_nomedia, mine, theirs, attachment}
    assert not any(retryable.values())

    desk.as_user("attention-staff")
    assert desk.get("/api/inbox/attention").status_code == 403


def test_attention_is_capped_at_twenty_newest_rows_with_true_totals(intake, desk):
    ids = []
    for index in range(12):
        job_id, _ = failed_email(intake, 100 + index, updated=NOW - timedelta(minutes=index))
        ids.append(f"email:{job_id}")
    for index in range(12):
        add_whatsapp(intake, f"wa{index}", updated=NOW - timedelta(minutes=index, seconds=30))
    for index in range(12):
        add_manual(intake, f"m{index}", updated=NOW - timedelta(minutes=index, seconds=45))
    body = desk.get("/api/inbox/attention").json()
    assert len(body["items"]) == 20
    stamps = [item["updatedAt"] for item in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert body["items"][0]["id"] == ids[0]
    assert body["totals"] == {"email": 12, "whatsapp": 12, "manual": 12, "all": 36}


def test_attention_is_empty_and_cheap_without_failures(intake, desk):
    with recorded_statements(intake.engine) as statements:
        body = desk.get("/api/inbox/attention").json()
    assert body["items"] == [] and body["totals"] == {"email": 0, "whatsapp": 0, "manual": 0, "all": 0}
    assert len(statements) <= 7  # Session, three list queries and three counts.
