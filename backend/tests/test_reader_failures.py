"""Every way the online reader can fail is named precisely (class, HTTP status, attempt, safe detail), retried when a retry can
help and not when it cannot, and shown on the document line and in the per-candidate report: all against a fake reader."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from veriis.errors import (VerisOCRAuthenticationError, VerisOCRError, VerisOCRBadRequestError, VerisOCRConnectionError, VerisOCRNotFoundError,
                           VerisOCRRateLimitError, VerisOCRServerError, VerisOCRTimeoutError, VerisOCRValidationError)
from veriis.models import ResumeResponse

from app.integrations.ocr import client as ocr_client, reader_failures
from app.services import document_extraction as subject
from test_document_extraction import pdf
from test_ocr_classification import CV
from test_passport_pages import data_page
from test_veris_handoff import world  # noqa: F401  (the fixture: a fake reader, no network)

SECRET = "ZZ9876543"   # stands in for a passport number / document text: it must appear in no message, detail or report


def fails(world, data=None, **overrides):
    with pytest.raises(subject.ExtractionFailure) as raised:
        world.extract(data or pdf(CV), **overrides)
    return raised.value


# ------------------------------------------------------------------------------ the classes, one by one


@pytest.mark.parametrize("error, error_class, status, retriable, words", [
    (VerisOCRTimeoutError("t"), "reader_timeout", None, True, "did not answer in time"),
    (VerisOCRConnectionError("https://secret.invalid/?key=1"), "reader_unreachable", None, True, "could not reach the document reader"),
    (VerisOCRConnectionError("bad json", status=200), "reader_bad_data", 200, True, "cut off or was not valid data"),
    (VerisOCRRateLimitError("slow down", status=429, retry_after=12), "reader_busy", 429, True, "busy (too many requests)"),
    (VerisOCRServerError("boom", status=502), "reader_server_error", 502, True, "fault on its side"),
    (VerisOCRBadRequestError("no", status=413), "reader_rejected_file", 413, False, "refused this file"),
    (VerisOCRAuthenticationError("no", status=401), "reader_auth", 401, False, "access key"),
    (VerisOCRNotFoundError("no", status=404), "reader_not_found", 404, False, "no longer has this reading job"),
    (VerisOCRValidationError("bad", status=422, detail=[{"loc": ["body", "file"], "msg": f"echo {SECRET}"}]),
     "reader_rejected_request", 422, False, "rejected the request as invalid"),
    (VerisOCRError("conflict", status=409), "reader_key_conflict", 409, True, "different file"),
    (RuntimeError(f"unexpected {SECRET}"), "internal_error", None, True, "fault on our side"),
])
def test_each_failure_class_has_its_reason_and_message(error, error_class, status, retriable, words):
    reason = reader_failures.classify(error)
    assert reason["errorClass"] == error_class and reason.get("httpStatus") == status and reason["retriable"] is retriable
    text = reader_failures.message(reason, attempt=2, max_attempts=4, retry_in=60)
    assert words in text
    assert SECRET not in text + str(reason) and "secret.invalid" not in text + str(reason)
    if retriable:
        assert "attempt 2 of 4" in text and "tried again" in text
    else:
        assert "attempt" not in text or "will not get better" in text or "Upload" in text or "administrator" in text or "Ask for" in text or "developer" in text


def test_a_schema_mismatch_names_the_fields_and_never_the_value():
    with pytest.raises(ValidationError) as raised:
        ResumeResponse.model_validate({"pages": SECRET})
    reason = reader_failures.classify(raised.value)
    assert reason["errorClass"] == "reader_answer_unusable" and reason["retriable"] is True
    assert "pages" in reason["detail"] and SECRET not in reason["detail"]
    assert "we could not use the answer" in reader_failures.message(reason)


def test_a_failed_job_keeps_the_readers_code_and_its_retry_flag():
    reason = reader_failures.from_job(status="failed", code="pdf_resource_limit", message="Too heavy", retryable=False)
    assert reason["errorClass"] == "reader_job_failed" and reason["retriable"] is False and reason["code"] == "pdf_resource_limit"
    assert "pdf_resource_limit" in reader_failures.message(reason)
    assert reader_failures.from_job(status="cancelled")["errorClass"] == "reader_no_result"


def test_the_message_for_a_missing_field_names_it():
    reason = reader_failures.missing_field("passport number")
    assert reader_failures.message(reason) == "The reader answered, but the passport number field was missing."


# ------------------------------------------------------------------------------ through the extraction, with a fake reader


def test_a_timeout_is_transient_the_same_key_is_retried_and_the_reason_goes_away_on_success(world):
    world.veris.submit_faults = [VerisOCRTimeoutError("slow")]
    data = pdf(CV)
    error = fails(world, data)
    assert error.reason["errorClass"] == "reader_timeout" and error.permanent is False
    assert "did not answer in time" in str(error)
    state = deepcopy(world.saved[-1])
    assert state["provider_jobs"]["resume"]["reader_failure"]["errorClass"] == "reader_timeout"
    key = state["provider_jobs"]["resume"]["idempotency_key"]

    output = world.extract(data, state=state)

    assert output["status"] == "complete"
    assert [s["key"] for s in world.veris.submissions] == [key]        # one job under the saved key: nothing paid twice
    assert "reader_failure" not in world.saved[-1]["provider_jobs"]["resume"]


def test_a_server_fault_is_retried_inline_with_growing_pauses_then_reported(world):
    world.veris.submit_faults = [VerisOCRServerError("down", status=502) for _ in range(ocr_client.RETRY_ATTEMPTS)]
    error = fails(world)
    assert error.reason["errorClass"] == "reader_server_error" and error.reason["httpStatus"] == 502 and not error.permanent
    assert world.sleeps == [0.5, 1.0, 2.0] and world.veris.submissions == []
    assert "HTTP 502" in str(error)


def test_a_busy_reader_defers_without_an_attempt_or_a_pause_and_honours_retry_after(world):
    world.veris.submit_faults = [VerisOCRRateLimitError("busy", status=429, retry_after=45)]
    with pytest.raises(subject.ExtractionDeferred) as raised:
        world.extract(pdf(CV))
    assert 40 <= raised.value.retry_seconds <= 45 and "busy (too many requests): retrying" in str(raised.value)
    assert raised.value.reason["errorClass"] == "reader_busy" and world.sleeps == []
    assert world.saved[-1]["provider_jobs"]["resume"]["reader_failure"]["errorClass"] == "reader_busy"
    assert world.veris.peak <= world.settings.veris_parallel_jobs


@pytest.mark.parametrize("fault, error_class, permanent", [
    (VerisOCRBadRequestError("no", status=400), "reader_rejected_file", True),
    (VerisOCRAuthenticationError("no", status=403), "reader_auth", True),
    (VerisOCRValidationError("bad", status=422), "reader_rejected_request", True),
    (VerisOCRNotFoundError("gone", status=404), "reader_not_found", True),
    (VerisOCRConnectionError("reset"), "reader_unreachable", False),
])
def test_permanent_failures_stop_the_automatic_attempts_and_transient_ones_do_not(world, fault, error_class, permanent):
    world.veris.submit_faults = [fault]
    error = fails(world)
    assert error.reason["errorClass"] == error_class and error.permanent is permanent
    assert len(world.veris.submissions) == 0 and world.sleeps == []     # no inline retry of a refusal; a dropped submit is left to the worker


def test_a_reply_that_is_not_valid_data_on_a_status_check_is_retried_then_named(world):
    polls = []

    def broken(job_id):
        polls.append(job_id)
        raise VerisOCRConnectionError("The Veris OCR service returned an invalid JSON response.", status=200)

    world.veris.get = broken
    error = fails(world)
    assert error.reason["errorClass"] == "reader_bad_data" and error.reason["httpStatus"] == 200
    assert len(polls) == ocr_client.RETRY_ATTEMPTS and world.sleeps == [0.5, 1.0, 2.0]
    assert len(world.veris.submissions) == 1          # the job was accepted once and only polled again


def test_an_answer_that_does_not_fit_the_expected_shape_names_the_fields(world):
    def malformed(job_id):
        ResumeResponse.model_validate({"pages": SECRET})

    world.veris.get = malformed
    error = fails(world)
    assert error.reason["errorClass"] == "reader_answer_unusable" and "pages" in error.reason["detail"]
    assert SECRET not in str(error) + str(error.reason) and not error.permanent


def test_a_job_the_reader_failed_says_what_the_reader_said_and_obeys_its_retry_flag(world):
    world.veris.script[("resume", 0)] = "failed"
    world.veris.errors[("resume", 0)] = SimpleNamespace(code="document_unreadable", message="Pages could not be read", retryable=False)
    error = fails(world)
    assert error.reason["errorClass"] == "reader_job_failed" and error.reason["code"] == "document_unreadable" and error.permanent
    assert "document_unreadable" in str(error) and "Pages could not be read" in str(error)
    assert len(world.veris.submissions) == 1


def test_a_retryable_reader_failure_is_not_permanent(world):
    world.veris.script[("resume", 0)] = "failed"
    world.veris.errors[("resume", 0)] = SimpleNamespace(code="busy_worker", message="Try again", retryable=True)
    error = fails(world)
    assert error.reason["errorClass"] == "reader_job_failed" and error.reason["retriable"] and not error.permanent


def test_a_job_that_succeeds_without_a_result_is_named(world):
    world.veris.script[("resume", 0)] = "cancelled"
    error = fails(world)
    assert error.reason["errorClass"] == "reader_no_result" and "cancelled" in error.reason["detail"]


def test_a_saved_answer_that_fails_validation_names_fields_and_is_read_again_once_automatically(world):
    world.veris.build = lambda mode, number, texts: {"pages": SECRET}
    data = pdf(CV)
    error = fails(world, data)
    assert error.reason["errorClass"] == "reader_answer_unusable" and "pages" in error.reason["detail"]
    assert SECRET not in str(error) + str(error.reason)
    state = deepcopy(world.saved[-1])
    assert state["provider_jobs"]["resume"]["validation_status"] == "failed"

    assert subject.request_extraction_retry(state, automatic=True) == ["resume"]     # one automatic new reading
    error = fails(world, data, state=state)
    assert error.reason["errorClass"] == "reader_answer_unusable"
    assert len(world.veris.submissions) == 2                                         # and only one extra job was paid for
    again = deepcopy(world.saved[-1])
    assert subject.request_extraction_retry(again, automatic=True) == []             # a second bad answer waits for a person
    assert subject.request_extraction_retry(again) == ["resume"]                     # an explicit retry still works


def test_a_passport_the_reader_returned_without_its_number_is_flagged_and_keeps_the_local_value(world):
    world.veris.build = lambda mode, number, texts: {"mrz": {"given_names": "TESTER", "surname": "SAMPLE1"}} if mode == "passport" else {}
    output = world.extract(pdf(data_page(1)))
    notice = next(w for w in output["warnings"] if "passport number field was missing" in w)
    assert "local passport zone" in notice and output["status"] == "needs_review"
    assert any("passport number field was missing" in item for item in output["coverage"]["issues"])


def test_one_kind_failing_for_good_degrades_the_file_with_the_real_reason(world):
    world.settings.veris_early_dispatch = False
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = SimpleNamespace(code="document_unreadable", message="Synthetic", retryable=False)
    from test_ocr_classification import PASSPORT
    output = world.extract(pdf(CV, PASSPORT))
    notice = next(w for w in output["warnings"] if w.startswith("The passport pages could not be read."))
    assert "Why:" in notice and "document_unreadable" in notice


# ------------------------------------------------------------------------------ the worker's record and the screen's view

from test_manual_extraction import intake, process, start, upload  # noqa: E402,F401
from test_database_naming import schema_database  # noqa: E402,F401
from app.db.models import Attachment, DocumentExtractionJob  # noqa: E402
from app.services import manual_extraction  # noqa: E402
from app.services.application_form import extraction_view  # noqa: E402


def _fail_with(monkeypatch, error):
    def raiser(*args, **kwargs):
        raise subject.ExtractionFailure(f"x {SECRET}", reason=reader_failures.classify(error))
    monkeypatch.setattr(manual_extraction, "extract_attachment", raiser)


def test_a_transient_failure_is_recorded_per_attempt_and_the_last_one_says_the_attempts_are_used_up(intake, monkeypatch):
    _fail_with(monkeypatch, VerisOCRTimeoutError("slow"))
    job = upload(intake)
    start(intake, job["id"])
    process(intake, job["id"])
    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        assert row.status == "retry" and row.attempts == 1
        assert "did not answer in time" in row.last_error and "attempt 1 of 4" in row.last_error and "tried again" in row.last_error
        failure = row.draft["readerFailure"]
        assert failure["errorClass"] == "reader_timeout" and failure["attempt"] == 1 and failure["maxAttempts"] == 4
        assert failure["retriable"] is True and failure["retryAt"]
        assert row.state["failure"]["errorClass"] == "reader_timeout"
        view = extraction_view(row)
        assert view["status"] == "processing" and view["failure"]["errorClass"] == "reader_timeout"
        assert "attempt 1 of 4" in view["message"]
        assert 20 < (row.ready_at - manual_extraction.now()).total_seconds() < 45     # 30 s with jitter
    for _ in range(3):
        process(intake, job["id"])
    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        assert row.status == "failed" and row.attempts == 4
        assert "attempt 4 of 4" in row.last_error and "used up" in row.last_error
        view = extraction_view(row)
        assert view["status"] == "failed" and view["failure"]["attempt"] == 4 and view["failure"]["errorClass"] == "reader_timeout"
        assert SECRET not in str(view) + row.last_error + str(row.draft)


def test_a_permanent_failure_is_not_retried_and_says_who_must_act(intake, monkeypatch):
    _fail_with(monkeypatch, VerisOCRAuthenticationError("no", status=401))
    job = upload(intake)
    start(intake, job["id"])
    process(intake, job["id"])
    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        assert row.status == "failed" and row.attempts == 1
        assert "HTTP 401" in row.last_error and "administrator" in row.last_error
        assert row.draft["readerFailure"]["retriable"] is False and row.draft["readerFailure"]["httpStatus"] == 401


def test_a_later_success_clears_the_failure(intake, monkeypatch):
    real = manual_extraction.extract_attachment
    _fail_with(monkeypatch, VerisOCRServerError("down", status=503))
    job = upload(intake)
    start(intake, job["id"])
    process(intake, job["id"])
    monkeypatch.setattr(manual_extraction, "extract_attachment", real)
    process(intake, job["id"])
    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        assert row.status in {"completed", "needs_review"} and "readerFailure" not in (row.draft or {})


def test_a_passport_filled_from_the_local_zone_is_flagged_when_the_reader_gives_up(intake, monkeypatch):
    def raiser(data, *, state, save, **kwargs):
        save({"local_patterns": {"passport": {"number_ok": True, "birth_ok": True, "expiry_ok": False}}, "local_filled": True})
        raise subject.ExtractionFailure("x", reason=reader_failures.classify(VerisOCRBadRequestError("no", status=413)))
    monkeypatch.setattr(manual_extraction, "extract_attachment", raiser)
    job = upload(intake, kind="passport")
    with intake.sessions() as db:
        db.get(DocumentExtractionJob, job["id"]).candidate_id = "candidate"
        db.commit()
    start(intake, job["id"])
    process(intake, job["id"])
    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        failure = row.draft["readerFailure"]
        assert row.status == "failed" and failure["localFallback"] == {"source": "local passport zone", "fields": ["passport number", "date of birth"]}
        assert "local passport zone" in row.last_error and "Check them" in row.last_error
        from app.db.models import AuditEntry
        actions = [a.action for a in db.scalars(select(AuditEntry).where(AuditEntry.entity_id == "candidate"))]
        assert "Kept passport details from the local passport zone" in actions


# ------------------------------------------------------------------------------ the per-candidate report


def test_the_diagnostics_report_lists_every_document_and_holds_no_name_or_number(intake, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routers import document_diagnostics as router_module
    from app.db.models import AuditEntry, Role

    name = "Alexandra Quillfeather"
    with intake.sessions() as db:
        db.add(Attachment(id="att-pass", entity_type="candidate", entity_id="candidate", slot="passport", version=1,
                          file_name="Passport.pdf", size_kb=804, content_type="application/pdf", object_key="k1", uploaded_by="owner"))
        db.add(Attachment(id="att-aad", entity_type="candidate", entity_id="candidate", slot="aadhaar", version=1,
                          file_name="Aadhaar card.jpeg", size_kb=92, content_type="image/jpeg", object_key="k2", uploaded_by="owner"))
        db.add(DocumentExtractionJob(
            id="job-pass", owner_id="owner", group_id="g1", kind="passport", filename="Passport.pdf", content_type="application/pdf",
            object_key="k1", content_hash="h1", size_kb=804, status="needs_review", attempts=2, candidate_id="candidate", attachment_id="att-pass",
            last_error="Check the details.",
            draft={"readerFailure": {"errorClass": "reader_timeout", "httpStatus": None, "retriable": True, "detail": "no answer within 30 seconds",
                                     "attempt": 2, "maxAttempts": 4, "message": f"mentions {SECRET}"},
                   "applicationForm": {"result": {"identityReasons": [
                       {"field": "name", "documentKind": "passport", "found": name, "expected": "Someone Else"},
                       {"field": "candidate", "documentKind": "passport", "matchedOn": ["passport number"]}],
                       "duplicateOf": {"candidateId": "other-id", "careId": "CI-12", "name": name}}}},
            state={"provider_jobs": {"passport": {"status": "failed", "selected_pages": [1, 2], "job_id": "rj-1", "result": {"mrz": {"passport_number": SECRET, "surname": name}},
                                                  "reader_failure": {"errorClass": "reader_timeout", "detail": "no answer", "retriable": True},
                                                  "provider_error": {"code": "ReadTimeout", "http_status": None}}},
                   "local_patterns": {"passport": {"passport_number": SECRET, "surname": name, "number_ok": True}},
                   "inspection": {"pages": [{"page_number": 1, "status": "read", "document_type": "passport", "text": SECRET},
                                            {"page_number": 2, "status": "read", "document_type": "unknown"}]},
                   "failure": {"type": "ExtractionFailure", "where": "services.document_extraction:_fetch:1", "attempt": 2, "at": "now"}}))
        db.add(DocumentExtractionJob(
            id="job-aad", owner_id="owner", group_id="g2", kind="aadhaar", filename="Aadhaar card.jpeg", content_type="image/jpeg",
            object_key="k2", content_hash="h2", size_kb=92, status="retry", attempts=1, candidate_id="candidate", attachment_id="att-aad",
            last_error="The reader is busy", draft={}, state={}))
        db.commit()

    app = FastAPI()
    app.include_router(router_module.router)
    def database():
        with intake.sessions() as db:
            yield db
    app.dependency_overrides[router_module.get_db] = database
    app.dependency_overrides[router_module.current_user] = lambda: intake.actor
    with TestClient(app) as client:
        response = client.get("/api/candidates/candidate/documents/diagnostics")
        assert response.status_code == 200, response.text
        body = response.json()
        raw = response.text
        for secret in (SECRET, name, "Quillfeather", "Someone Else"):
            assert secret not in raw
        slots = {doc["slot"]: doc for doc in body["documents"]}
        passport = slots["passport"]
        assert passport["storedName"] == "Passport.pdf" and passport["sizeKb"] == 804
        reading = passport["reading"]
        assert reading["jobId"] == "job-pass" and reading["status"] == "needs_review" and reading["attempts"] == 2
        assert reading["failure"]["errorClass"] == "reader_timeout" and reading["failure"]["attempt"] == 2
        assert reading["readerJobs"][0]["pagesSent"] == [1, 2] and reading["readerJobs"][0]["errorClass"] == "reader_timeout"
        assert reading["localPages"] == [{"page": 1, "status": "read", "kind": "passport"}, {"page": 2, "status": "read", "kind": "unknown"}]
        assert [r["field"] for r in reading["identityReasons"]] == ["name", "candidate"] and reading["identityReasons"][1]["matchedOn"] == ["passport number"]
        assert reading["duplicateOf"] == {"candidateId": "other-id", "careId": "CI-12"}
        assert slots["aadhaar"]["reading"]["status"] == "retry" and body["documents"][0]["slot"] in {"aadhaar", "passport"}
        assert "reader failure: errorClass=reader_timeout" in body["report"] and "job job-pass" in body["report"]
        # someone who cannot edit this candidate gets nothing
        intake.actor.id, intake.actor.role = "other", Role.staff
        assert client.get("/api/candidates/candidate/documents/diagnostics").status_code in {403, 404}
        intake.actor.id, intake.actor.role = "owner", Role.staff
        assert client.get("/api/candidates/missing/documents/diagnostics").status_code == 404
