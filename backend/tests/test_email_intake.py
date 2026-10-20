"""Durable intake contracts against an explicitly opted-in, disposable PostgreSQL DB.

Mailbox and extraction are synthetic. The shared fixture verifies a loopback server
and a crm_schema_test* database before resetting its schema. No settings file, mail
account, OCR provider, or production database is consulted.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pymupdf as fitz
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    AssignmentPointer, Attachment, AuditEntry, Candidate, CandidateStatus,
    EmailIngestionJob, MailAccount, MailAttachment, MailMessage, Notification,
    Role, Source, Task, TaskKind, User,
)
from app.integrations.mail.base import InboundAttachment, InboundMessage, MailMessageError
from app.integrations.mail.imap import MailConfig, MailAdapterError, mailbox_source_id, provider_message_id
from test_database_naming import _migrate, schema_database


with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/intake_tests"
)):
    from app.services import email_intake as subject


STAMP = datetime(2026, 10, 20, tzinfo=timezone.utc)
MAIL_CONFIGS = {
    f"inbox-{suffix}@example.test": MailConfig(
        host="imap.example.test", port=993, username=f"inbox-{suffix}@example.test",
        password="synthetic-only-password",
    ) for suffix in ("a", "b")
}


def pdf(*texts):
    with fitz.open() as document:
        for content in texts:
            document.new_page().insert_text((40, 50), content)
        return document.tobytes(no_new_id=True)


ORIGINAL = pdf("Page one: certificate", "Page two: resume", "Page three: passport")


def extraction(*, kind="resume", fields=None, status="complete", warnings=None):
    fields = {
        "name": "Alex Example", "phone": "+91 90000 00001",
        "email": "alex@example.test", "designation": "Welder", **(fields or {}),
    }
    inspection = {
        "page_count": 3, "resume_pages": [2], "passport_pages": [3],
        "aadhaar_pages": [], "unread_pages": [], "unknown_pages": [],
        "pages": [
            {"page_number": i, "text": "SECRET RAW DOCUMENT TEXT", "status": "read",
             "document_type": document_type, "method": "native"}
            for i, document_type in enumerate(("other", "resume", "passport"), 1)
        ],
        "source_format": "pdf", "complete": True,
    }
    return {
        "status": status, "inspection": inspection, "warnings": warnings or [],
        "extractions": {
            kind: {
                "fields": fields,
                "raw": {"pages": [{"page_number": 2, "text": "Synthetic resume evidence"}]},
                "selected_pages": [3] if kind == "passport" else [2],
            },
        },
    }


@pytest.fixture
def intake(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    settings = SimpleNamespace(storage_dir=tmp_path, ocr_document_timeout_seconds=600)
    with sessions() as db:
        db.add_all([
            User(id="admin", name="Admin", email="admin@example.test", role=Role.admin),
            User(id="staff-a", name="Staff A", email="a@example.test", role=Role.staff,
                 active=True, receives_auto_assign=True, on_leave=False, created_at=STAMP),
            User(id="staff-b", name="Staff B", email="b@example.test", role=Role.staff,
                 active=True, receives_auto_assign=True, on_leave=False, created_at=STAMP),
            User(id="staff-leave", name="Staff Leave", email="leave@example.test", role=Role.staff,
                 active=True, receives_auto_assign=True, on_leave=True, created_at=STAMP),
            MailAccount(id="account-a", address="inbox-a@example.test", provider="imap",
                        auto_ingest=True, sync_state={"uid_validity": "100", "last_uid": 10,
                                                     "source_id": mailbox_source_id(MAIL_CONFIGS["inbox-a@example.test"])}),
            MailAccount(id="account-b", address="inbox-b@example.test", provider="imap",
                        auto_ingest=True, sync_state={"uid_validity": "100", "last_uid": 10,
                                                     "source_id": mailbox_source_id(MAIL_CONFIGS["inbox-b@example.test"])}),
        ])
        db.commit()
    # Any accidental integration call is a test failure before network is possible.
    monkeypatch.setattr(subject, "IMAPMailbox", lambda *a, **kw: pytest.fail("Unexpected real mailbox"))
    monkeypatch.setattr(subject, "extract_attachment", lambda *a, **kw: pytest.fail("Unexpected OCR call"))
    monkeypatch.setattr(subject, "configured_mailboxes", lambda _: MAIL_CONFIGS)
    return SimpleNamespace(sessions=sessions, settings=settings, engine=schema_database)


def receipt(intake, *, account="account-a", uid=11, data=ORIGINAL):
    key = subject.store_original(intake.settings, account, str(uid), 0, data)
    with intake.sessions() as db:
        message = MailMessage(account_id=account, provider_message_id=f"synthetic:{uid}", received_at=STAMP)
        db.add(message)
        db.flush()
        part = MailAttachment(message_id=message.id, file_name="misleading-name.jpg",
                              content_hash=hashlib.sha256(data).hexdigest(), object_key=key, size_kb=1)
        job = EmailIngestionJob(message_id=message.id, state={})
        db.add_all([part, job])
        db.commit()
        return job.id, part.id


def run(intake, job_id):
    subject.process_job(job_id, sessions=intake.sessions, settings=intake.settings)


def fake_extraction(monkeypatch, output=None):
    calls = []
    output = output or extraction()

    def extract(data, *, state, save, **kwargs):
        calls.append(data)
        state.update(source_sha256=hashlib.sha256(data).hexdigest(),
                     inspection=deepcopy(output["inspection"]))
        save(state)
        return deepcopy(output)
    monkeypatch.setattr(subject, "extract_attachment", extract)
    return calls


def row_count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def mailbox(monkeypatch, *, error=None):
    data = ORIGINAL
    part = InboundAttachment("part:2", "certificate.pdf", "application/pdf", data, hashlib.sha256(data).hexdigest())
    message = InboundMessage(provider_id=provider_message_id("INBOX", "100", 11),
                             subject="Synthetic application", from_name="Example Applicant",
                             from_address="sender@example.test", body="Synthetic message body",
                             received_at=STAMP, unread=True, attachments=(part,),
                             uid=11, uid_validity="100", folder="INBOX")
    calls = []

    class Mailbox:
        def __init__(self, account):
            self.account = account
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def list_uids(self, *, after_uid, uid_validity, limit):
            calls.append((self.account, after_uid, uid_validity, limit))
            # A repeated provider receipt must remain idempotent even if a
            # transport replays it after the cursor was committed.
            return [11]
        def fetch(self, uid, *, uid_validity):
            if error:
                raise error
            return message
    monkeypatch.setattr(subject, "IMAPMailbox", Mailbox)
    return calls


def test_combining_top_level_low_confidence_warnings_requires_review():
    fields, raw, review = subject.combine_extractions([extraction(warnings=["Low confidence"])])
    assert review is True
    assert fields["name"] == "Alex Example"
    assert raw["resume"]["pages"][0]["page_number"] == 2


@pytest.mark.parametrize("difference", [
    {"name": "Another Applicant"}, {"phone": "9000000099"},
    {"email": "a.lex@example.test"},
])
def test_combining_conflicting_identities_does_not_silently_merge(difference):
    with pytest.raises(subject.IntakeReviewError):
        subject.combine_extractions([
            extraction(), extraction(kind="passport", fields=difference),
        ])


@pytest.mark.parametrize("field,first,second", [
    ("dob", "1990-01-01", "1991-01-01"),
    ("gender", "male", "female"),
])
def test_combining_conflicting_birth_date_or_gender_requires_review(field, first, second):
    with pytest.raises(subject.IntakeReviewError):
        subject.combine_extractions([
            extraction(fields={field: first}),
            extraction(kind="passport", fields={field: second}),
        ])


def test_receipt_cursor_and_job_are_idempotent_and_account_scoped(intake, monkeypatch):
    calls = mailbox(monkeypatch)
    for account in ("account-a", "account-a", "account-b"):
        subject.receive_account(account, sessions=intake.sessions, settings=intake.settings)
    with intake.sessions() as db:
        assert row_count(db, MailMessage) == row_count(db, MailAttachment) == row_count(db, EmailIngestionJob) == 2
        messages = list(db.scalars(select(MailMessage)))
        assert messages[0].provider_message_id == messages[1].provider_message_id
        assert {row.account_id for row in messages} == {"account-a", "account-b"}
        for account in db.scalars(select(MailAccount)):
            assert account.sync_state == {"uid_validity": "100", "last_uid": 11,
                                          "source_id": mailbox_source_id(MAIL_CONFIGS[account.address])}
            assert account.last_sync_at is not None
        parts = list(db.scalars(select(MailAttachment)))
        assert len({part.object_key for part in parts}) == 2
        assert all((Path(intake.settings.storage_dir) / part.object_key).read_bytes() == ORIGINAL for part in parts)
    assert calls[1][1] == 11


@pytest.mark.parametrize("binding", [None, "another-mailbox-source"])
def test_unbound_or_changed_source_cannot_connect_or_advance_existing_cursor(intake, binding):
    with intake.sessions() as db:
        account = db.get(MailAccount, "account-a")
        account.sync_state = {"uid_validity": "100", "last_uid": 1000, "source_id": binding}
        db.commit()
    # The fixture's IMAPMailbox already fails if it is even constructed.
    with pytest.raises(MailAdapterError) as raised:
        subject.receive_account("account-a", sessions=intake.sessions, settings=intake.settings)
    assert raised.value.code == ("source_unbound" if binding is None else "source_changed")
    with intake.sessions() as db:
        assert db.get(MailAccount, "account-a").sync_state == {
            "uid_validity": "100", "last_uid": 1000, "source_id": binding,
        }
        assert row_count(db, MailMessage) == row_count(db, EmailIngestionJob) == 0


def test_receipt_failure_rolls_back_cursor_message_attachments_and_job(intake, monkeypatch):
    mailbox(monkeypatch)

    def fail_receipt(connection, cursor, statement, *args):
        if statement.startswith("INSERT INTO email_attachments"):
            raise RuntimeError("synthetic write failure")
    event.listen(intake.engine, "before_cursor_execute", fail_receipt)
    try:
        with pytest.raises(RuntimeError, match="synthetic write failure"):
            subject.receive_account("account-a", sessions=intake.sessions, settings=intake.settings)
    finally:
        event.remove(intake.engine, "before_cursor_execute", fail_receipt)
    with intake.sessions() as db:
        assert db.get(MailAccount, "account-a").sync_state["last_uid"] == 10
        assert row_count(db, MailMessage) == row_count(db, MailAttachment) == row_count(db, EmailIngestionJob) == 0
    subject.receive_account("account-a", sessions=intake.sessions, settings=intake.settings)
    with intake.sessions() as db:
        assert db.get(MailAccount, "account-a").sync_state["last_uid"] == 11
        assert row_count(db, EmailIngestionJob) == 1


def test_oversized_message_leaves_visible_failure_before_advancing_cursor(intake, monkeypatch):
    mailbox(monkeypatch, error=MailMessageError(
        "message_too_large", "Message exceeds the configured size limit.",
        uid=11, provider_id=provider_message_id("INBOX", "100", 11),
    ))
    subject.receive_account("account-a", sessions=intake.sessions, settings=intake.settings)
    with intake.sessions() as db:
        job = db.scalar(select(EmailIngestionJob))
        assert job.status == "failed"
        assert job.state == {"receive_error": True}
        assert "size limit" in job.last_error
        assert db.get(MailAccount, "account-a").sync_state["last_uid"] == 11
        assert row_count(db, Candidate) == row_count(db, MailAttachment) == 0


def test_transient_mail_download_keeps_cursor_for_the_next_poll(intake, monkeypatch):
    mailbox(monkeypatch, error=MailMessageError(
        "incomplete_message", "The email download was incomplete.",
        uid=11, provider_id=provider_message_id("INBOX", "100", 11), retryable=True,
    ))
    with pytest.raises(MailMessageError):
        subject.receive_account("account-a", sessions=intake.sessions, settings=intake.settings)
    with intake.sessions() as db:
        assert db.get(MailAccount, "account-a").sync_state["last_uid"] == 10
        assert row_count(db, MailMessage) == row_count(db, EmailIngestionJob) == 0
    mailbox(monkeypatch)
    subject.receive_account("account-a", sessions=intake.sessions, settings=intake.settings)
    with intake.sessions() as db:
        assert row_count(db, EmailIngestionJob) == 1
        assert db.get(MailAccount, "account-a").sync_state["last_uid"] == 11


def test_candidate_evidence_assignment_task_and_notifications_commit_once(intake, monkeypatch):
    job_id, part_id = receipt(intake)
    calls = fake_extraction(monkeypatch)
    run(intake, job_id)
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(Candidate))
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed"
        assert candidate.id == job.candidate_id == db.get(MailMessage, job.message_id).candidate_id
        assert candidate.ref == f"CND-E-{job_id.replace('-', '')}"
        assert len(candidate.ref) == 38
        assert candidate.owner_id == "staff-a"
        assert candidate.source == Source.email and candidate.status == CandidateStatus.new
        task = db.scalar(select(Task))
        assert task.kind == TaskKind.initial_call and task.candidate_id == candidate.id and task.owner_id == "staff-a"
        assert {notice.user_id for notice in db.scalars(select(Notification))} == {"admin", "staff-a"}
        assert row_count(db, Candidate) == row_count(db, Task) == row_count(db, AuditEntry) == 1
        assert row_count(db, Attachment) == 2
        original = db.scalar(select(Attachment).where(Attachment.slot == "source_bundle"))
        assert original.object_key == db.get(MailAttachment, part_id).object_key
        assert job.inspection == [{
            "attachmentId": part_id, "fileName": "misleading-name.jpg", "pageCount": 3,
            "resumePages": [2], "passportPages": [3], "aadhaarPages": [],
            "unreadPages": [], "unknownPages": [], "status": "complete",
        }]
        assert "SECRET RAW DOCUMENT TEXT" not in json.dumps(job.inspection)
        assert db.get(AssignmentPointer, 1).last_user_id == "staff-a"
    assert calls == [ORIGINAL]


def test_pending_provider_job_survives_retry_and_creates_one_candidate(intake, monkeypatch):
    job_id, part_id = receipt(intake)
    invocations = []

    def extract(data, *, state, save, **kwargs):
        invocations.append(deepcopy(state))
        state["source_sha256"] = hashlib.sha256(data).hexdigest()
        state["inspection"] = extraction()["inspection"]
        if not state.get("provider_jobs"):
            state["provider_jobs"] = {"resume": {"job_id": "synthetic-accepted", "status": "running"}}
            save(state)
            raise subject.ExtractionPending("synthetic-accepted", "resume")
        assert state["provider_jobs"]["resume"]["job_id"] == "synthetic-accepted"
        return extraction()
    monkeypatch.setattr(subject, "extract_attachment", extract)
    run(intake, job_id)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "retry" and job.attempts == 0
        assert job.state[part_id]["provider_jobs"]["resume"]["job_id"] == "synthetic-accepted"
        assert job.inspection[0]["status"] == "processing"
        assert row_count(db, Candidate) == 0
    run(intake, job_id)
    with intake.sessions() as db:
        assert db.get(EmailIngestionJob, job_id).status == "completed"
        assert row_count(db, Candidate) == row_count(db, Task) == 1
    assert len(invocations) == 2


def test_candidate_transaction_failure_preserves_ocr_and_rolls_back_all_domain_writes(intake, monkeypatch, caplog):
    job_id, part_id = receipt(intake)
    calls = fake_extraction(monkeypatch)
    secret = "synthetic-secret-that-must-not-be-logged"

    def fail_notification(connection, cursor, statement, *args):
        if statement.startswith("INSERT INTO system_notifications"):
            raise RuntimeError(secret)
    event.listen(intake.engine, "before_cursor_execute", fail_notification)
    try:
        run(intake, job_id)
    finally:
        event.remove(intake.engine, "before_cursor_execute", fail_notification)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "retry" and job.attempts == 1
        assert job.state[part_id]["output"]["status"] == "complete"
        assert all(row_count(db, model) == 0 for model in (Candidate, Attachment, Task, Notification, AuditEntry))
        assert db.get(MailMessage, job.message_id).candidate_id is None
        assert secret not in job.last_error
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    run(intake, job_id)
    with intake.sessions() as db:
        assert db.get(EmailIngestionJob, job_id).status == "completed"
        assert row_count(db, Candidate) == row_count(db, Task) == 1
        assert row_count(db, Notification) == 2
    assert len(calls) == 1, "Retry must use the committed output, not request paid OCR again"


def test_processing_retries_three_times_with_backoff_then_stops_safely(intake, monkeypatch, caplog):
    job_id, _ = receipt(intake)
    attempts = []
    monkeypatch.setattr(subject, "now", lambda: STAMP)

    def fail(data, **kwargs):
        attempts.append(1)
        raise RuntimeError("synthetic-private-provider-detail")
    monkeypatch.setattr(subject, "extract_attachment", fail)
    for number in range(1, 5):
        run(intake, job_id)
        with intake.sessions() as db:
            job = db.get(EmailIngestionJob, job_id)
            assert job.attempts == number
            assert job.status == ("retry" if number < 4 else "failed")
            assert (job.next_attempt_at - STAMP).total_seconds() == 90 * 2 ** (number - 1)
            assert "synthetic-private" not in job.last_error
            assert row_count(db, Candidate) == 0
    run(intake, job_id)
    assert len(attempts) == 4
    assert "synthetic-private" not in caplog.text


@pytest.mark.parametrize("provider_status", ["failed", "running", "succeeded"])
def test_only_confirmed_provider_failure_receives_an_automatic_retry_generation(intake, monkeypatch, provider_status):
    job_id, part_id = receipt(intake)

    def fail(data, *, state, save, **kwargs):
        state["source_sha256"] = hashlib.sha256(data).hexdigest()
        state["provider_jobs"] = {"resume": {
            "job_id": "accepted-job", "status": provider_status,
            "coverage_status": "needs_review" if provider_status == "succeeded" else None,
        }}
        save(state)
        raise RuntimeError("synthetic processing failure")
    monkeypatch.setattr(subject, "extract_attachment", fail)
    run(intake, job_id)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        document = job.state[part_id]
        assert job.status == "retry" and job.attempts == 1
        if provider_status == "failed":
            assert document["provider_jobs"] == {}
            assert document["provider_generations"] == {"resume": 1}
            assert document["provider_job_history"]["resume"][0]["job_id"] == "accepted-job"
        else:
            assert document["provider_jobs"]["resume"]["job_id"] == "accepted-job"
            assert not document.get("provider_generations")


def test_same_content_in_another_message_and_account_reuses_completed_extraction(intake, monkeypatch):
    first, _ = receipt(intake)
    calls = fake_extraction(monkeypatch)
    run(intake, first)
    second, second_part = receipt(intake, uid=12, account="account-b")
    run(intake, second)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, second)
        assert job.status == "completed"
        assert job.candidate_id == db.get(EmailIngestionJob, first).candidate_id
        assert job.state[second_part]["reused_from_attachment_id"]
        assert row_count(db, Candidate) == row_count(db, Task) == 1
        assert row_count(db, Attachment) == 2
        assert row_count(db, Notification) == 2
        assert row_count(db, MailMessage) == row_count(db, MailAttachment) == 2
    assert len(calls) == 1


def test_same_content_twice_in_one_message_does_not_repeat_extraction(intake, monkeypatch):
    job_id, part_id = receipt(intake)
    with intake.sessions() as db:
        original = db.get(MailAttachment, part_id)
        db.add(MailAttachment(
            message_id=original.message_id, file_name="second-copy.pdf",
            content_hash=original.content_hash, object_key=original.object_key, size_kb=1,
        ))
        db.commit()
    calls = fake_extraction(monkeypatch)
    run(intake, job_id)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed"
        assert row_count(db, Candidate) == 1 and row_count(db, Attachment) == 2
        assert len(job.inspection) == 2
    assert len(calls) == 1


def test_duplicate_fills_empty_fields_and_keeps_human_edits_owner_status(intake, monkeypatch):
    with intake.sessions() as db:
        candidate = Candidate(ref="CND-EXISTING", name="Human corrected name", phone="9000000001",
                              email=None, current_city="Human corrected city", skills=[],
                              owner_id="staff-b", source=Source.walk_in,
                              status=CandidateStatus.needs_review, needs_review=True)
        db.add(candidate)
        db.commit()
        candidate_id = candidate.id
    first, _ = receipt(intake)
    calls = fake_extraction(monkeypatch, extraction(fields={"skills": ["Welding"], "current_city": "OCR city"}))
    run(intake, first)
    second, _ = receipt(intake, uid=12, data=ORIGINAL + b" changed")
    run(intake, second)
    with intake.sessions() as db:
        candidate = db.get(Candidate, candidate_id)
        assert candidate.name == "Human corrected name" and candidate.current_city == "Human corrected city"
        assert candidate.email == "alex@example.test" and candidate.skills == ["Welding"]
        assert candidate.owner_id == "staff-b" and candidate.status == CandidateStatus.needs_review
        assert row_count(db, Candidate) == 1 and row_count(db, Task) == row_count(db, Notification) == 0
        originals = select(Attachment.version).where(Attachment.slot == "source_bundle").order_by(Attachment.version)
        assert list(db.scalars(originals)) == [1, 2]
        # Different originals with byte-identical selected pages do not duplicate
        # the derived resume document.
        assert list(db.scalars(select(Attachment.version).where(Attachment.slot == "resume"))) == [1]
        assert db.get(EmailIngestionJob, second).candidate_id == candidate_id
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["partial", "conflicting_documents", "ambiguous_database"])
def test_incomplete_or_conflicting_results_require_review_without_candidate_writes(intake, monkeypatch, failure):
    job_id, _ = receipt(intake)
    output = extraction()
    if failure == "partial":
        output["status"] = "needs_review"
        output["warnings"] = ["Provider did not return every selected page"]
    elif failure == "conflicting_documents":
        output["extractions"]["passport"] = extraction(kind="passport", fields={"name": "Another Applicant"})["extractions"]["passport"]
    else:
        with intake.sessions() as db:
            db.add_all([
                Candidate(ref="existing-a", name="Existing A", phone="9000000001", source=Source.walk_in),
                Candidate(ref="existing-b", name="Existing B", phone="9000000009",
                          email="alex@example.test", source=Source.walk_in),
            ])
            db.commit()
    fake_extraction(monkeypatch, output)
    run(intake, job_id)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "needs_review" and job.candidate_id is None
        assert row_count(db, Candidate) == (2 if failure == "ambiguous_database" else 0)
        assert row_count(db, Task) == row_count(db, Notification) == row_count(db, Attachment) == 0
        assert db.get(MailMessage, job.message_id).candidate_id is None


def test_warning_or_missing_phone_creates_review_candidate_not_silently_ready(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields={"phone": ""}, warnings=["Low confidence"]))
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(Candidate))
        assert candidate.phone == ""
        assert candidate.needs_review and candidate.status == CandidateStatus.needs_review
        assert db.get(EmailIngestionJob, job_id).status == "completed"


def test_original_checksum_is_verified_before_using_a_cached_result(intake, monkeypatch):
    first, _ = receipt(intake)
    calls = fake_extraction(monkeypatch)
    run(intake, first)
    second, part_id = receipt(intake, uid=12)
    with intake.sessions() as db:
        key = db.get(MailAttachment, part_id).object_key
    (Path(intake.settings.storage_dir) / key).write_bytes(b"damaged original")
    run(intake, second)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, second)
        assert job.status == "needs_review" and "checksum mismatch" in job.last_error
        assert job.candidate_id is None
        assert row_count(db, Candidate) == 1
    assert len(calls) == 1


def test_bundle_retains_original_and_stores_correct_separate_document_pages(intake, monkeypatch):
    data = pdf("CERTIFICATE excluded", "RESUME selected", "PASSPORT selected", "AADHAAR selected")
    job_id, part_id = receipt(intake, data=data)
    output = extraction()
    output["inspection"]["page_count"] = 4
    output["inspection"]["aadhaar_pages"] = [4]
    output["inspection"]["pages"].append({
        "page_number": 4, "status": "read", "document_type": "aadhaar", "method": "native",
    })
    for kind, page in (("passport", 3), ("aadhaar", 4)):
        output["extractions"][kind] = {
            "fields": {"name": "Alex Example"},
            "raw": {"pages": [{"page_number": page, "text": f"Synthetic {kind} evidence"}]},
            "selected_pages": [page],
        }
    fake_extraction(monkeypatch, output)
    run(intake, job_id)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed"
        links = {item.slot: item for item in db.scalars(select(Attachment))}
        assert set(links) == {"source_bundle", "resume", "passport", "aadhaar"}
        original = db.get(MailAttachment, part_id)
        assert original.file_name == "misleading-name.jpg" and original.kind is None
        assert (Path(intake.settings.storage_dir) / original.object_key).read_bytes() == data
        for kind in ("resume", "passport", "aadhaar"):
            item = links[kind]
            assert item.content_type == "application/pdf" and item.file_name == f"{kind}.pdf"
            content = (Path(intake.settings.storage_dir) / item.object_key).read_bytes()
            assert hashlib.sha256(content).hexdigest() == item.content_hash
            with fitz.open(stream=content, filetype="pdf") as selected:
                assert selected.page_count == 1
                assert selected[0].get_text().strip() == f"{kind.upper()} selected"
        assert job.inspection[0]["aadhaarPages"] == [4]
        candidate = db.get(Candidate, job.candidate_id)
        assert candidate.resume_text == "Synthetic resume evidence"
        assert candidate.ocr_data["passport"]["pages"][0]["text"] == "Synthetic passport evidence"
        assert candidate.ocr_data["aadhaar"]["pages"][0]["text"] == "Synthetic aadhaar evidence"


def test_email_migration_round_trip_preserves_existing_accounts_messages_and_candidates(schema_database):
    _migrate(schema_database, "0006_domain_table_names")
    with schema_database.begin() as connection:
        connection.execute(text("""
            INSERT INTO email_accounts (id, address, provider, auto_ingest)
            VALUES ('existing-account', 'existing@example.test', 'imap', true)
        """))
        connection.execute(text("""
            INSERT INTO crm_candidates (id, ref, name, phone, source)
            VALUES ('existing-candidate', 'CND-EXISTING', 'Existing candidate', '9000000001', 'email')
        """))
        connection.execute(text("""
            INSERT INTO email_messages (id, account_id, provider_message_id, received_at, candidate_id, body)
            VALUES ('existing-message', 'existing-account', 'existing-provider-id', now(),
                    'existing-candidate', 'Existing synthetic body')
        """))
    tables = ("email_accounts", "email_messages", "crm_candidates")

    def snapshots():
        with schema_database.connect() as connection:
            return {
                name: connection.scalar(text(
                    f"SELECT md5(jsonb_agg(to_jsonb(t) ORDER BY t.id)::text) FROM {name} t"
                )) for name in tables
            }
    before = snapshots()
    _migrate(schema_database, "0007_email_intake")
    with schema_database.connect() as connection:
        account = connection.execute(text("""
            SELECT sync_state, sync_requested, last_error, worker_seen_at
            FROM email_accounts WHERE id='existing-account'
        """)).one()
        assert tuple(account) == ({}, False, None, None)
        assert connection.scalar(text("SELECT count(*) FROM email_ingestion_jobs")) == 0
        assert connection.scalar(text("SELECT candidate_id FROM email_messages")) == "existing-candidate"
        for name in ("email_messages", "crm_candidates"):
            assert connection.scalar(text(
                f"SELECT md5(jsonb_agg(to_jsonb(t) ORDER BY t.id)::text) FROM {name} t"
            )) == before[name]
    _migrate(schema_database, "0006_domain_table_names", downgrade=True)
    assert "email_ingestion_jobs" not in inspect(schema_database).get_table_names()
    assert snapshots() == before


def test_manual_creation_waits_for_intake_lock_before_duplicate_and_reference_reads(intake):
    from fastapi import HTTPException
    from app.api.routers import candidates

    reached_lock = threading.Event()
    manual_queries = []

    def observe(connection, cursor, statement, *args):
        if threading.current_thread().name.startswith("manual-create"):
            manual_queries.append(statement)
            if "workflow_assignment_cursor" in statement:
                reached_lock.set()

    def manual_create():
        with intake.sessions() as db:
            try:
                candidates.create_candidate(
                    candidates.CandidateIn(name="Concurrent manual", phone="9000000001", passport_no="P1234567"),
                    db=db, user=SimpleNamespace(id="admin"),
                )
            except HTTPException as exc:
                return exc.status_code
            return 201

    # The worker holds the same lock while creating a candidate. The manual
    # request must wait before deciding that the identity is absent.
    with intake.sessions() as worker:
        candidates.lock_assignment_cursor(worker)
        event.listen(intake.engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="manual-create") as pool:
                pending = pool.submit(manual_create)
                try:
                    assert reached_lock.wait(5), "Manual request never reached the shared assignment lock"
                    assert not any("FROM crm_candidates" in query for query in manual_queries)
                    worker.add(Candidate(
                        ref="CND-E-concurrent", name="Worker candidate", phone="9000000001",
                        passport_no="P1234567", source=Source.email,
                    ))
                    worker.commit()
                finally:
                    # Always release the held transaction before joining the
                    # worker thread, including when an assertion fails.
                    worker.rollback()
                assert pending.result(timeout=10) == 409
        finally:
            event.remove(intake.engine, "before_cursor_execute", observe)
    with intake.sessions() as db:
        assert row_count(db, Candidate) == 1
        assert row_count(db, Task) == row_count(db, AuditEntry) == 0
