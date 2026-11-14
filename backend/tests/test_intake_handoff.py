"""Cross-channel OCR handoff, document splitting, and durable form application.

All database tests use the guarded disposable test database and fake providers.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from sqlalchemy import func, select

from app.db.models import Attachment, Candidate, DocumentExtractionJob, EmailIngestionJob, MailAttachment, WhatsAppMessage
from app.services import application_form, document_extraction, email_intake, manual_extraction
from app.services.document_extraction import ExtractionDeferred, ExtractionOverdue, ExtractionPending
from app.services.extraction_scheduling import PENDING_RECHECK_SECONDS
from app.services.document_storage import read_bytes
from app.services.whatsapp_intake import process_message
from test_application_form import desk, form, process_pending, upload
from test_database_naming import schema_database
from test_document_extraction import environment, pdf
from test_email_intake import ORIGINAL, extraction, intake, receipt, run
from test_ocr_classification import CV, PASSPORT
from test_whatsapp_intake import receipt as whatsapp_receipt, setup
from test_workflow_scheduler import workspace


def passport_subset():
    output = extraction(kind="passport")
    output["inspection"]["passport_pages"] = [1, 3]
    output["inspection"]["pages"][0]["document_type"] = "passport"
    return output


def test_email_allows_checked_passport_subset_and_rejects_wrong_kind(tmp_path):
    settings = SimpleNamespace(storage_dir=tmp_path, ocr_document_timeout_seconds=600)
    part = SimpleNamespace(id="part", message_id="message")
    output = passport_subset()
    documents = email_intake._save_selected_documents(settings, "account", part, ORIGINAL, output)
    with fitz.open(stream=read_bytes(settings, documents["passport"]["object_key"]), filetype="pdf") as saved:
        assert len(saved) == 1
        assert "passport" in saved[0].get_text()
    output["extractions"]["passport"]["selected_pages"] = [2]
    with pytest.raises(email_intake.IntakeReviewError, match="do not match"):
        email_intake._save_selected_documents(settings, "account", part, ORIGINAL, output)


def test_whatsapp_keeps_successful_passport_when_duplicate_pages_were_not_sent(setup):
    sessions, settings = setup
    identifier = whatsapp_receipt(sessions, settings)
    process_message(identifier, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: passport_subset())
    with sessions() as db:
        message = db.get(WhatsAppMessage, identifier)
        assert message.status == "completed", message.last_error
        document = message.processing_state["documents"]["passport"]
        with fitz.open(stream=read_bytes(settings, document["object_key"]), filetype="pdf") as saved:
            assert len(saved) == 1


def test_whatsapp_keeps_readable_resume_and_flags_partial_file_for_review(setup):
    sessions, settings = setup
    identifier = whatsapp_receipt(sessions, settings)
    output = extraction(status="needs_review", warnings=["Passport could not be read; resume retained."],
                        fields={"phone": "+91 98888 00019", "email": "freshpartial@example.test"})
    process_message(identifier, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=lambda *_, **__: output)
    with sessions() as db:
        message = db.get(WhatsAppMessage, identifier)
        assert message.status == "completed", message.last_error
        candidate = db.get(Candidate, message.candidate_id)
        assert candidate.phone and candidate.needs_review
        assert "resume" in candidate.ocr_data


@pytest.mark.parametrize("overdue", [False, True])
def test_email_dispatches_next_attachment_while_first_is_with_provider(intake, monkeypatch, overdue):
    identifier, first = receipt(intake)
    second_bytes = ORIGINAL + b"\nsecond attachment"
    key = email_intake.store_original(intake.settings, "account-a", "second", 0, second_bytes)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, identifier)
        second = MailAttachment(id="zz-second", message_id=job.message_id, file_name="second.pdf",
                                content_hash=hashlib.sha256(second_bytes).hexdigest(), object_key=key, size_kb=1)
        db.add(second)
        db.commit()
    visited = []
    waiting = True

    def extract(data, *, state, save, settings, **_):
        assert settings.ocr_provider_wait_seconds == 0
        visited.append(data)
        if data == ORIGINAL and waiting:
            state["provider_jobs"] = {"resume": {"job_id": "already-accepted", "status": "queued"}}
            save(state)
            waiting_error = ExtractionOverdue if overdue else ExtractionPending
            raise waiting_error("already-accepted", "resume")
        return extraction()

    monkeypatch.setattr(email_intake, "extract_attachment", extract)
    run(intake, identifier)
    assert visited == [ORIGINAL, second_bytes]
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, identifier)
        assert job.status == "retry" and job.attempts == 0
        assert job.state[first]["provider_jobs"]["resume"]["job_id"] == "already-accepted"
        assert job.state["zz-second"]["output"]["status"] == "complete"
        delay = (job.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
        expected_delay = 30 if overdue else PENDING_RECHECK_SECONDS
        assert expected_delay - 5 < delay <= expected_delay
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0
    waiting = False
    run(intake, identifier)
    assert visited == [ORIGINAL, second_bytes, ORIGINAL]  # second result is retained
    with intake.sessions() as db:
        assert db.get(EmailIngestionJob, identifier).status == "completed"
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1


@pytest.mark.parametrize("waits,expected_delay,visited_count", [
    (("fresh", "overdue"), 2, 3),
    (("overdue", "fresh"), 2, 3),
    (("overdue", "capacity"), 45, 2),
])
def test_email_waits_keep_shortest_poll_unless_provider_capacity_blocks_admission(
    intake, monkeypatch, waits, expected_delay, visited_count,
):
    identifier, first = receipt(intake)
    contents = [ORIGINAL, ORIGINAL + b"\nsecond", ORIGINAL + b"\nthird"]
    ids = [first, "zz-second", "zzz-third"]
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, identifier)
        for ordinal in (1, 2):
            key = email_intake.store_original(intake.settings, "account-a", ids[ordinal], 0, contents[ordinal])
            db.add(MailAttachment(
                id=ids[ordinal], message_id=job.message_id, file_name=f"part-{ordinal}.pdf",
                content_hash=hashlib.sha256(contents[ordinal]).hexdigest(), object_key=key, size_kb=1,
            ))
        db.commit()
    visited = []

    def extract(data, *, state, save, **_):
        ordinal = contents.index(data)
        visited.append(ordinal)
        if ordinal == 2:
            return extraction()
        mode = waits[ordinal]
        if mode == "capacity":
            raise ExtractionDeferred("resume", 45)
        accepted_id = f"accepted-{ordinal}"
        state["provider_jobs"] = {"resume": {"job_id": accepted_id, "status": "queued"}}
        save(state)
        raise (ExtractionOverdue if mode == "overdue" else ExtractionPending)(accepted_id, "resume")

    monkeypatch.setattr(email_intake, "extract_attachment", extract)
    run(intake, identifier)
    assert visited == list(range(visited_count))
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, identifier)
        assert job.status == "retry" and job.attempts == 0
        delay = (job.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
        assert expected_delay - 5 < delay <= expected_delay
        for ordinal, mode in enumerate(waits):
            if mode != "capacity":
                assert job.state[ids[ordinal]]["provider_jobs"]["resume"]["job_id"] == f"accepted-{ordinal}"
        if visited_count == 3:
            assert job.state[ids[2]]["output"]["status"] == "complete"


def test_combined_form_upload_populates_each_document_slot_and_preserves_original(desk):
    response = upload(desk, "resume", pdf(CV, PASSPORT))
    assert response.status_code == 202
    original_id = response.json()["document"]["current"]["attachmentId"]
    process_pending(desk)
    result = form(desk)
    assert result["personal"]["candidateName"] == "Alex Example"  # the saved candidate name is kept
    for kind in ("resume", "passport"):
        item = result["documents"][kind]
        assert item["uploaded"] and item["extraction"]["status"] == "done"
        data = desk.client.get(item["current"]["url"]).content
        with fitz.open(stream=data, filetype="pdf") as document:
            assert len(document) == 1
    with desk.sessions() as db:
        original = db.get(Attachment, original_id)
        with fitz.open(stream=read_bytes(desk.settings, original.object_key), filetype="pdf") as document:
            assert len(document) == 2
        before = db.scalar(select(func.count()).select_from(Attachment))
        job = db.scalar(select(DocumentExtractionJob))
        application_form.apply_job_output(db, job, job.state["output"])
        db.commit()
        assert db.scalar(select(func.count()).select_from(Attachment)) == before


def test_form_save_failure_retries_saved_result_without_reading_again(desk, monkeypatch):
    assert upload(desk, "resume", pdf(CV)).status_code == 202
    original = application_form.apply_job_output
    monkeypatch.setattr(application_form, "apply_job_output", lambda *args: (_ for _ in ()).throw(RuntimeError("temporary save fault")))
    process_pending(desk)
    with desk.sessions() as db:
        job = db.scalar(select(DocumentExtractionJob))
        assert job.status == "retry" and job.attempts == 1
        assert job.state["output"]["extractions"]["resume"]
    monkeypatch.setattr(application_form, "apply_job_output", original)
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **kw: pytest.fail("Paid output must not be extracted again"))
    process_pending(desk)
    with desk.sessions() as db:
        assert db.scalar(select(DocumentExtractionJob.status)) == "completed"


def test_passport_uploaded_in_resume_control_is_filed_as_passport(desk):
    assert upload(desk, "resume", pdf(PASSPORT)).status_code == 202
    process_pending(desk)
    data = form(desk)
    assert data["documents"]["passport"]["uploaded"]
    assert not data["documents"]["resume"]["uploaded"]
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.slot == "source_bundle")) == 1


def test_older_misfiled_passport_does_not_replace_newer_passport_or_count_as_resume(desk):
    assert upload(desk, "resume", pdf(PASSPORT)).status_code == 202
    newer = upload(desk, "passport", pdf(PASSPORT + "\nNew scan"))
    assert newer.status_code == 202
    new_id = newer.json()["document"]["current"]["attachmentId"]
    process_pending(desk)
    data = form(desk)
    assert not data["documents"]["resume"]["uploaded"]
    assert data["documents"]["passport"]["current"]["attachmentId"] == new_id


@pytest.fixture(params=["capacity", "overdue"])
def provider_wait(request):
    return ExtractionDeferred("resume", 45) if request.param == "capacity" else ExtractionOverdue("accepted-job", "resume")


def test_provider_wait_does_not_exhaust_manual_document_attempts(desk, monkeypatch, provider_wait):
    upload(desk, "resume", pdf(CV))
    def busy(*args, **kwargs):
        raise provider_wait
    monkeypatch.setattr(manual_extraction, "extract_attachment", busy)
    for _ in range(5):
        process_pending(desk)
    with desk.sessions() as db:
        job = db.scalar(select(DocumentExtractionJob))
        assert job.status == "retry" and job.attempts == 0
        assert job.last_error == str(provider_wait)
        assert (job.ready_at - datetime.now(timezone.utc)).total_seconds() > provider_wait.retry_seconds - 5


def test_provider_wait_does_not_exhaust_email_attempts(intake, monkeypatch, provider_wait):
    identifier, _ = receipt(intake)
    def busy(*args, **kwargs):
        raise provider_wait
    monkeypatch.setattr(email_intake, "extract_attachment", busy)
    for _ in range(5):
        run(intake, identifier)
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, identifier)
        assert job.status == "retry" and job.attempts == 0 and job.last_error == str(provider_wait)


def test_provider_wait_does_not_exhaust_whatsapp_attempts(setup, provider_wait):
    sessions, settings = setup
    identifier = whatsapp_receipt(sessions, settings)
    def busy(*args, **kwargs):
        raise provider_wait
    for _ in range(5):
        process_message(identifier, sessions=sessions, settings=settings,
                        fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=busy)
    with sessions() as db:
        job = db.get(WhatsAppMessage, identifier)
        assert job.status == "retry" and job.attempts == 0 and job.last_error == str(provider_wait)


def test_old_failed_classification_is_refreshed_before_any_paid_job(environment, monkeypatch):
    from veriis import VerisOCRServerError
    original = environment.jobs.submit
    def busy(*args, **kwargs):
        raise VerisOCRServerError("capacity full", status=503, code="job_queue_full", retry_after=30)
    monkeypatch.setattr(environment.jobs, "submit", busy)
    data = pdf(CV)
    with pytest.raises(ExtractionDeferred):
        environment.extract(data)
    state = deepcopy(environment.saved[-1])
    state["inspection_version"] = "old-classifier"
    state["inspection"]["resume_pages"] = []
    state["inspection"]["aadhaar_pages"] = [1]
    state["inspection"]["pages"][0]["document_type"] = "aadhaar"
    monkeypatch.setattr(environment.jobs, "submit", original)
    result = environment.extract(data, state=state)
    assert result["inspection"]["resume_pages"] == [1]
    assert result["inspection"]["aadhaar_pages"] == []
    assert [kind for kind, *_ in environment.submissions] == ["resume"]


def test_old_classifier_keeps_accepted_job_plan_and_does_not_resubmit(environment, monkeypatch):
    data = pdf(CV)
    environment.statuses["resume"] = "queued"
    with pytest.raises(ExtractionPending):
        environment.extract(data)
    state = deepcopy(environment.saved[-1])
    state["inspection_version"] = "old-classifier"
    environment.statuses["resume"] = "succeeded"
    monkeypatch.setattr(document_extraction, "inspect_document", lambda *a, **k: pytest.fail("Accepted plan must be preserved"))
    assert environment.extract(data, state=state)["status"] == "complete"
    assert len(environment.submissions) == 1
