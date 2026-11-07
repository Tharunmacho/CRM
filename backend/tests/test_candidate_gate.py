"""A candidate row is written only after a source was read and gave a real identity.

A name that is not the old "Name needs review" placeholder, plus a phone, an email or (for a
passport-only document) a passport number. Otherwise nothing is written: the intake job goes
to needs-review with a plain reason and the file stays where it was. Synthetic data only, on
the disposable PostgreSQL the shared fixtures verify.
"""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.cli import placeholder_candidates as placeholder_cli
from app.db.models import (
    Attachment, AuditEntry, Candidate, CandidateStatus, EmailIngestionJob, MailAttachment, MailMessage, Source, Task,
    TaskKind, WhatsAppMessage,
)
from app.services.candidate_review import NO_CONTACT_REASON, NO_NAME_REASON, candidate_refusal
from app.services.whatsapp_intake import process_message
from test_database_naming import schema_database  # noqa: F401  (fixture)
from auto_assign import round_robin_on  # noqa: F401  (fixture: these tests exercise the round-robin, which is opt-in)
import pytest as _pytest

pytestmark = _pytest.mark.usefixtures("round_robin_on")
from test_email_intake import extraction, fake_extraction, intake, receipt, row_count, run, ORIGINAL  # noqa: F401  (fixtures)
from test_whatsapp_intake import receipt as whatsapp_receipt, setup  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture)

NONAME = {"name": "", "phone": "", "email": ""}
NAME_CONTACT_GAPS = (
    ({"name": "", "phone": "9000000011", "email": ""}, NO_NAME_REASON),
    ({"name": "Name needs review", "phone": "9000000011", "email": "x@example.test"}, NO_NAME_REASON),
    ({"name": "   ", "phone": "9000000011"}, NO_NAME_REASON),
    ({"name": "Only Name", "phone": "", "email": ""}, NO_CONTACT_REASON),
)


def test_refusal_rule_in_plain_words():
    assert candidate_refusal({"name": "A B", "phone": "9000000011"}) is None
    assert candidate_refusal({"name": "A B", "email": "a@example.test"}) is None
    assert candidate_refusal({"name": "A B", "passport_no": "Z1234567"}) is None
    for values, reason in NAME_CONTACT_GAPS:
        assert candidate_refusal(values) == reason
    assert "could not read a name" in NO_NAME_REASON and "clearer copy" in NO_NAME_REASON


# ------------------------------------------------------------------ email

@pytest.mark.parametrize("fields,reason", NAME_CONTACT_GAPS)
def test_email_without_a_real_identity_creates_nothing_and_keeps_the_file(intake, monkeypatch, fields, reason):
    job_id, part_id = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields=fields))
    run(intake, job_id)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "needs_review" and job.candidate_id is None
        assert job.last_error == reason
        assert row_count(db, Candidate) == row_count(db, Task) == row_count(db, Attachment) == 0
        message = db.get(MailMessage, job.message_id)
        assert message.candidate_id is None
        # The mailbox attachment (Inbox imports list) is untouched.
        assert db.scalar(select(func.count()).select_from(MailAttachment)) == 1


def test_email_with_name_and_phone_creates_one_candidate(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields={"email": ""}))
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(Candidate))
        assert (candidate.name, candidate.phone) == ("Alex Example", "+91 90000 00001")
        assert db.get(EmailIngestionJob, job_id).status == "completed"
        assert row_count(db, Task) == 1


def test_email_with_name_and_email_only_creates_a_candidate_flagged_for_review(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields={"phone": ""}))
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(Candidate))
        assert candidate.name == "Alex Example" and candidate.phone == "" and candidate.email == "alex@example.test"
        assert candidate.needs_review and candidate.status == CandidateStatus.needs_review


def test_passport_only_email_with_a_name_creates_a_candidate(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(kind="passport", fields={
        "name": "Passport Holder", "phone": "", "email": "", "passport_no": "Z1234567"}))
    run(intake, job_id)
    with intake.sessions() as db:
        candidate = db.scalar(select(Candidate))
        assert candidate.name == "Passport Holder" and candidate.passport_no == "Z1234567"
        assert candidate.needs_review
        assert db.get(EmailIngestionJob, job_id).status == "completed"


def test_passport_only_email_without_a_name_creates_nothing(intake, monkeypatch):
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(kind="passport", fields={
        "name": "", "phone": "", "email": "", "passport_no": "Z1234567"}))
    run(intake, job_id)
    with intake.sessions() as db:
        assert db.get(EmailIngestionJob, job_id).status == "needs_review"
        assert row_count(db, Candidate) == 0


def test_retry_after_a_clearer_read_creates_exactly_one_candidate(intake, monkeypatch):
    from app.api.routers.inbox import requeue_job
    job_id, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields=NONAME))
    run(intake, job_id)
    with intake.sessions() as db:
        assert db.get(EmailIngestionJob, job_id).status == "needs_review"
        assert row_count(db, Candidate) == 0
    # A person asks for another reading; the clearer copy gives a real identity.
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        requeue_job(db, job, db.get(MailMessage, job.message_id))
        db.commit()
    fake_extraction(monkeypatch, extraction())
    run(intake, job_id)
    run(intake, job_id)  # a stray second run must not make a second candidate
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed" and job.last_error is None
        assert row_count(db, Candidate) == row_count(db, Task) == 1
        assert db.get(Candidate, job.candidate_id).name == "Alex Example"


# --------------------------------------------------------------- whatsapp

def _wa_candidates(db):
    return db.scalar(select(func.count()).select_from(Candidate).where(Candidate.source == Source.whatsapp))


def _process(setup, message_id, output):
    sessions, settings = setup
    process_message(message_id, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=lambda *_, **__: deepcopy(output))


def test_whatsapp_without_a_real_identity_creates_nothing_then_a_retry_creates_one(setup):
    sessions, settings = setup
    message_id = whatsapp_receipt(sessions, settings)
    _process(setup, message_id, extraction(fields=NONAME))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "needs_review" and row.last_error == NO_NAME_REASON
        assert row.candidate_id is None and row.object_key  # the original stays
        assert _wa_candidates(db) == 0
    # The sender's own number is not an identity: a name alone is still refused.
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        state = dict(row.processing_state)
        state.pop("output", None), state.pop("documents", None)
        row.processing_state, row.status, row.attempts, row.last_error = state, "pending", 0, None
        db.commit()
    _process(setup, message_id, extraction(fields={"phone": "", "email": ""}))
    with sessions() as db:
        assert db.get(WhatsAppMessage, message_id).status == "needs_review"
        assert db.get(WhatsAppMessage, message_id).last_error == NO_CONTACT_REASON
        assert _wa_candidates(db) == 0
    # A clearer copy is read: now the candidate is created, once.
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        state = dict(row.processing_state)
        state.pop("output", None), state.pop("documents", None)
        row.processing_state, row.status, row.attempts, row.last_error = state, "pending", 0, None
        db.commit()
    _process(setup, message_id, extraction(fields={"phone": "9000000077", "email": ""}))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        assert _wa_candidates(db) == 1
        assert db.get(Candidate, row.candidate_id).name == "Alex Example"


# ----------------------------------------------------------- the clean-up CLI

def _placeholder(db, ref, *, name="Name needs review", phone="", email=None, source=Source.email):
    candidate = Candidate(ref=ref, name=name, phone=phone, email=email, source=source)
    db.add(candidate)
    db.flush()
    return candidate


def test_placeholder_cli_dry_run_lists_only_refs_and_delete_removes_only_the_qualifying_rows(intake, monkeypatch, capsys):
    import app.cli as cli
    monkeypatch.setattr(cli, "SessionLocal", intake.sessions)
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    with intake.sessions() as db:
        job_id, _ = receipt(intake)
        placeholder = _placeholder(db, "CND-01004")
        blank = _placeholder(db, "CND-01005", name="")
        with_phone = _placeholder(db, "CND-01006", phone="9000000031")
        worked = _placeholder(db, "CND-01007")
        with_resume = _placeholder(db, "CND-01008")
        named = _placeholder(db, "CND-01009", name="Real Person")
        for owner in (placeholder, blank):  # the untouched first-call task the intake created is not activity
            db.add(Task(kind=TaskKind.initial_call, title="Initial call", candidate_id=owner.id, owner_id="staff-a"))
        db.add(Attachment(entity_type="candidate", entity_id=placeholder.id, slot="source_bundle", version=1, file_name="source.pdf",
                          content_hash="h1", object_key="k1", content_type="application/pdf"))
        db.add(Task(kind=TaskKind.initial_call, title="Initial call", candidate_id=worked.id, owner_id="staff-a", completed_at=now))
        db.add(Attachment(entity_type="candidate", entity_id=with_resume.id, slot="resume", version=1, file_name="resume.pdf",
                          content_hash="h2", object_key="k2", content_type="application/pdf"))
        job = db.get(EmailIngestionJob, job_id)
        job.candidate_id, job.status = placeholder.id, "completed"
        db.get(MailMessage, job.message_id).candidate_id = placeholder.id
        db.commit()
        kept = {with_phone.id, worked.id, with_resume.id, named.id}

    assert placeholder_cli(False) == 0
    dry = capsys.readouterr().out
    assert "CND-01004" in dry and "CND-01005" in dry
    assert not any(ref in dry for ref in ("CND-01006", "CND-01007", "CND-01008", "CND-01009"))
    assert "Real Person" not in dry and "Name needs review" not in dry
    with intake.sessions() as db:
        assert row_count(db, Candidate) == 6

    assert placeholder_cli(True) == 0
    capsys.readouterr()
    with intake.sessions() as db:
        assert set(db.scalars(select(Candidate.id))) == kept
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action.like("Removed placeholder candidate%"))) == 2
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.slot == "source_bundle")) == 0
        # The source file goes back to needs-review so a person can enter the details or upload a clearer copy.
        job = db.get(EmailIngestionJob, job_id)
        assert job.candidate_id is None and job.status == "needs_review" and job.last_error == NO_NAME_REASON
        assert db.get(MailMessage, job.message_id).candidate_id is None
    # Nothing is left to clean: a repeat run is a no-op.
    assert placeholder_cli(True) == 0
    assert "0 placeholder" in capsys.readouterr().out
