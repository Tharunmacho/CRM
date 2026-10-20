"""Durable worker OCR orchestration with native fixtures and a fake provider."""

from copy import deepcopy
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from veriis import VerisOCRConnectionError

from app.integrations.ocr import inspection
from app.services import document_extraction as subject
from test_ocr_classification import AADHAAR, AADHAAR_BACK, CERTIFICATE, CV, PASSPORT


def pdf(*texts):
    with fitz.open() as document:
        for text in texts:
            document.new_page().insert_textbox(fitz.Rect(40, 40, 555, 800), text, fontsize=10)
        return document.tobytes(no_new_id=True)


def result(kind, count):
    pages = [{
        "page_number": number, "text": f"Synthetic {kind} page {number}",
        "language": "eng", "width": 595, "height": 842,
        "average_confidence": 0.95, "processing_time_ms": 1,
    } for number in range(1, count + 1)]
    if kind == "aadhaar":
        return {
            "request_id": "synthetic-aadhaar", "page_count": count, "pages": pages,
            "languages_used": "eng", "total_processing_time_ms": 1,
            "aadhaar": {
                "name": "ALEX EXAMPLE", "mobile_number": " 0000000000 ",
                "date_of_birth": "1990-01-01", "gender": "male",
                "masked_aadhaar_number": "XXXX XXXX 0000",
                "address": "An example address without a definite city field",
            },
        }
    value = {"request_id": f"synthetic-{kind}", "processing_time_ms": 1, "pages": pages}
    if kind == "passport":
        value.update(confidence=0.95, mrz={
            "given_names": "ALEX", "surname": "EXAMPLE", "passport_number": "P0000000",
            "all_check_digits_valid": True,
        })
    else:
        value.update(name="ALEX EXAMPLE", designation="welder", total_experience_months=36)
    return value


@pytest.fixture
def environment(monkeypatch):
    settings = SimpleNamespace(
        tesseract_cmd=None, ocr_languages="eng", ocr_max_pages=300,
        ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600,
        veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key",
    )
    state, saved, submissions, polls, opened = {}, [], [], [], []
    statuses, results = {}, {}

    def save(snapshot):
        saved.append(deepcopy(snapshot))

    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            # The idempotency intent must be durable before the paid request.
            assert saved[-1]["provider_jobs"][mode]["idempotency_key"] == idempotency_key
            submissions.append((mode, document, idempotency_key))
            with fitz.open(stream=document.data, filetype="pdf") as sent:
                results.setdefault(mode, result(mode, sent.page_count))
            return SimpleNamespace(job_id=f"job-{mode}", status="queued")

        def get(self, job_id):
            kind = job_id.removeprefix("job-")
            # A crash after acceptance cannot lose the job identifier.
            assert saved[-1]["provider_jobs"][kind]["job_id"] == job_id
            polls.append(job_id)
            return SimpleNamespace(
                status=statuses.get(kind, "succeeded"), result=deepcopy(results[kind]),
            )

    jobs = Jobs()

    class Provider:
        def __init__(self, **options):
            assert options["timeout"] == 30.0
            assert options["max_retries"] == 0
            self.jobs = jobs
            opened.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def __getattr__(self, name):
            raise AssertionError(f"No synchronous provider fallback is allowed: {name}")

    monkeypatch.setattr(subject, "VerisOCR", Provider)
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(inspection.subprocess, "run", lambda *a, **k: pytest.fail("No external processes"))

    def extract(data, **overrides):
        return subject.extract_attachment(
            data, state=overrides.pop("state", state), save=overrides.pop("save", save),
            settings=settings, designations=["Welder"], **overrides,
        )

    return SimpleNamespace(
        extract=extract, state=state, saved=saved, submissions=submissions, polls=polls,
        statuses=statuses, results=results, settings=settings, jobs=jobs, opened=opened,
    )


def test_all_document_types_select_exact_pages_and_keep_aadhaar_identity_separate(environment):
    output = environment.extract(pdf(CERTIFICATE, CV, PASSPORT, AADHAAR, AADHAAR_BACK))

    assert output["status"] == "complete"
    assert output["inspection"]["aadhaar_pages"] == [4, 5]
    assert set(output["extractions"]) == {"resume", "passport", "aadhaar"}
    assert [entry[0] for entry in environment.submissions] == ["resume", "passport", "aadhaar"]
    expected = {"resume": [2], "passport": [3], "aadhaar": [4, 5]}
    for kind, extraction in output["extractions"].items():
        assert extraction["selected_pages"] == expected[kind]
        assert [page["page_number"] for page in extraction["raw"]["pages"]] == expected[kind]
        assert [page["provider_page_number"] for page in extraction["raw"]["pages"]] == list(
            range(1, len(expected[kind]) + 1)
        )
        assert any("result" in snapshot.get("provider_jobs", {}).get(kind, {}) for snapshot in environment.saved)
    aadhaar = output["extractions"]["aadhaar"]
    assert aadhaar["fields"] == {
        "name": "Alex Example", "phone": "0000000000", "dob": "1990-01-01", "gender": "male",
    }
    assert aadhaar["raw"]["aadhaar"]["masked_aadhaar_number"] == "XXXX XXXX 0000"
    assert output["extractions"]["resume"]["fields"]["total_experience"] == 3
    assert "totalExperience" not in output["extractions"]["resume"]["fields"]
    assert output["extractions"]["passport"]["fields"]["passport_no"] == "P0000000"


def test_pending_job_is_saved_before_poll_and_resumed_after_worker_restart(environment):
    environment.statuses["resume"] = "running"
    data = pdf(CV)
    with pytest.raises(subject.ExtractionPending) as error:
        environment.extract(data)
    assert error.value.job_id == "job-resume"
    assert error.value.kind == "resume"
    restarted = deepcopy(environment.saved[-1])
    assert restarted["provider_jobs"]["resume"]["status"] == "running"

    environment.statuses["resume"] = "succeeded"
    output = environment.extract(data, state=restarted)

    assert output["status"] == "complete"
    assert len(environment.submissions) == 1
    assert environment.polls == ["job-resume", "job-resume"]
    assert "attempts" not in restarted["provider_jobs"]["resume"]


def test_pending_poll_reuses_complete_inspection_without_repeating_local_ocr(environment, monkeypatch):
    data = pdf(CV)
    environment.statuses["resume"] = "running"
    with pytest.raises(subject.ExtractionPending):
        environment.extract(data)
    monkeypatch.setattr(subject, "inspect_document", lambda *a, **k: pytest.fail("Local OCR repeated"))

    with pytest.raises(subject.ExtractionPending):
        environment.extract(data, state=deepcopy(environment.saved[-1]))
    assert len(environment.submissions) == 1
    assert environment.polls == ["job-resume", "job-resume"]


def test_inspection_settings_change_invalidates_cached_inspection(environment, monkeypatch):
    data = pdf(CV)
    environment.extract(data)
    original = subject.inspect_document
    inspected = []
    def inspect(*args, **kwargs):
        inspected.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(subject, "inspect_document", inspect)
    environment.settings.ocr_languages = "eng+hin"

    assert environment.extract(data)["status"] == "complete"
    assert len(inspected) == 1
    assert inspected[0]["languages"] == "eng+hin"
    assert len(environment.submissions) == 1


def test_corrupt_cached_page_positions_force_complete_reinspection(environment, monkeypatch):
    data = pdf(CV)
    environment.extract(data)
    environment.state["inspection"]["pages"][0]["page_number"] = 4
    original = subject.inspect_document
    inspected = []
    def inspect(*args, **kwargs):
        inspected.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(subject, "inspect_document", inspect)

    assert environment.extract(data)["status"] == "complete"
    assert inspected == [True]
    assert len(environment.submissions) == 1


def test_paid_result_survives_mapping_failure_without_another_provider_request(environment, monkeypatch):
    mapper = subject.resume_fields
    data = pdf(CV)

    def fail_mapping(*args, **kwargs):
        assert "result" in environment.saved[-1]["provider_jobs"]["resume"]
        raise RuntimeError("Synthetic downstream mapping failure")

    monkeypatch.setattr(subject, "resume_fields", fail_mapping)
    with pytest.raises(RuntimeError, match="downstream"):
        environment.extract(data)
    restarted = deepcopy(environment.saved[-1])
    monkeypatch.setattr(subject, "resume_fields", mapper)

    assert environment.extract(data, state=restarted)["status"] == "complete"
    assert len(environment.submissions) == len(environment.polls) == len(environment.opened) == 1


def test_cached_paid_result_does_not_require_available_provider_configuration(environment):
    data = pdf(CV)
    environment.extract(data)
    restarted = deepcopy(environment.saved[-1])
    environment.settings.veris_ocr_api_key = ""

    assert environment.extract(data, state=restarted)["status"] == "complete"
    assert len(environment.submissions) == len(environment.polls) == len(environment.opened) == 1


def test_accepted_failed_job_is_not_resubmitted_without_retry_budget_authorization(environment):
    data = pdf(CV)
    environment.statuses["resume"] = "failed"
    with pytest.raises(subject.ExtractionFailure, match="accepted resume job failed"):
        environment.extract(data)
    restarted = deepcopy(environment.saved[-1])
    with pytest.raises(subject.ExtractionFailure, match="accepted resume job failed"):
        environment.extract(data, state=restarted)
    assert len(environment.submissions) == len(environment.polls) == len(environment.opened) == 1


def test_budgeted_retry_allows_initial_attempt_and_three_distinct_confirmed_failure_retries(environment):
    data = pdf(CV)
    environment.statuses["resume"] = "failed"
    for attempt in range(1, 5):
        with pytest.raises(subject.ExtractionFailure, match="accepted resume job failed"):
            environment.extract(data)
        assert len(environment.submissions) == attempt
        if attempt < 4:
            assert subject.request_extraction_retry(environment.state, automatic=True) == ["resume"]

    assert len({entry[2] for entry in environment.submissions}) == 4
    assert len(environment.state["provider_job_history"]["resume"]) == 3
    assert environment.state["provider_jobs"]["resume"]["retry_generation"] == 3
    with pytest.raises(subject.ExtractionFailure):
        environment.extract(data)
    assert len(environment.submissions) == 4


@pytest.mark.parametrize("status", ["queued", "running", "submitting", "unknown"])
def test_budgeted_retry_retains_pending_and_unknown_provider_identity(environment, status):
    data = pdf(CV)
    environment.statuses["resume"] = "running"
    with pytest.raises(subject.ExtractionPending):
        environment.extract(data)
    environment.state["provider_jobs"]["resume"]["status"] = status
    original = deepcopy(environment.state["provider_jobs"]["resume"])

    assert subject.request_extraction_retry(environment.state, automatic=True) == []
    assert environment.state["provider_jobs"]["resume"] == original
    with pytest.raises(subject.ExtractionPending):
        environment.extract(data)
    assert len(environment.submissions) == 1
    assert environment.polls == ["job-resume", "job-resume"]


def test_budgeted_retry_keeps_successful_but_incomplete_result_for_operator(environment):
    data = pdf(CV)
    environment.results["resume"] = result("resume", 0)
    assert environment.extract(data)["status"] == "needs_review"

    assert subject.request_extraction_retry(environment.state, automatic=True) == []
    assert environment.extract(data)["status"] == "needs_review"
    assert len(environment.submissions) == 1


def test_explicit_retry_of_confirmed_failure_retains_history_and_uses_new_stable_key(environment):
    data = pdf(CV)
    environment.statuses["resume"] = "failed"
    with pytest.raises(subject.ExtractionFailure):
        environment.extract(data)
    first = deepcopy(environment.state["provider_jobs"]["resume"])

    assert subject.request_extraction_retry(environment.state) == ["resume"]
    assert environment.state["provider_job_history"]["resume"] == [first]
    environment.statuses["resume"] = "running"
    with pytest.raises(subject.ExtractionPending):
        environment.extract(data)
    new = environment.state["provider_jobs"]["resume"]
    assert new["retry_generation"] == 1
    assert new["idempotency_key"] != first["idempotency_key"]

    assert subject.request_extraction_retry(environment.state) == []
    with pytest.raises(subject.ExtractionPending):
        environment.extract(data)
    assert len(environment.submissions) == 2
    assert environment.state["provider_jobs"]["resume"]["idempotency_key"] == new["idempotency_key"]


def test_explicit_retry_restarts_incomplete_completed_result_but_retains_valid_work(environment):
    data = pdf(CV)
    environment.results["resume"] = result("resume", 0)
    assert environment.extract(data)["status"] == "needs_review"
    assert subject.request_extraction_retry(environment.state) == ["resume"]
    environment.results["resume"] = result("resume", 1)

    assert environment.extract(data)["status"] == "complete"
    assert subject.request_extraction_retry(environment.state) == []
    assert len(environment.submissions) == 2
    assert len(environment.state["provider_job_history"]["resume"]) == 1


@pytest.mark.parametrize("fault", ["partial", "duplicate", "empty"])
def test_provider_success_with_incomplete_page_coverage_requires_review(environment, fault):
    data = pdf(CV, CV)
    payload = result("resume", 2)
    if fault == "partial":
        payload["pages"].pop()
    elif fault == "duplicate":
        payload["pages"][1]["page_number"] = 1
    else:
        payload["pages"][1]["text"] = " \n "
    environment.results["resume"] = payload

    output = environment.extract(data)

    assert output["status"] == "needs_review"
    assert output["extractions"] == {}
    assert output["warnings"]
    assert environment.saved[-1]["provider_jobs"]["resume"]["result"] == payload
    assert environment.extract(data)["status"] == "needs_review"
    assert len(environment.submissions) == len(environment.polls) == 1


@pytest.mark.parametrize("unresolved", ["unknown", "page-limit"])
def test_local_incomplete_inspection_prevents_all_paid_calls(environment, unresolved):
    if unresolved == "page-limit":
        environment.settings.ocr_max_pages = 1
        data = pdf(CV, CERTIFICATE)
    else:
        data = pdf(CV, "Readable correspondence about unrelated meeting arrangements. " * 4)

    output = environment.extract(data)

    assert output["status"] == "needs_review"
    assert output["extractions"] == {}
    assert environment.submissions == environment.opened == []
    assert environment.saved[-1]["inspection"]["page_count"] == 2


def test_nonmatching_content_is_ignored_without_provider_configuration(environment):
    environment.settings.veris_ocr_api_key = ""
    assert environment.extract(pdf(CERTIFICATE))["status"] == "ignored"
    assert environment.submissions == environment.opened == []


def test_missing_provider_configuration_retains_inspection(environment):
    environment.settings.veris_ocr_api_key = ""
    with pytest.raises(subject.ExtractionFailure, match="not configured"):
        environment.extract(pdf(CV))
    assert environment.saved[-1]["inspection"]["resume_pages"] == [1]
    assert environment.submissions == environment.opened == []


def test_checkpoint_failure_stops_before_paid_work(environment):
    def unavailable(snapshot):
        raise RuntimeError("Synthetic storage outage")

    with pytest.raises(subject.ExtractionFailure, match="progress could not be saved"):
        environment.extract(pdf(CV), save=unavailable)
    assert environment.submissions == environment.opened == []


def test_provider_error_is_sanitized_and_retry_keeps_the_same_key(environment, monkeypatch):
    submit = environment.jobs.submit
    keys = []

    def lost_response(document, *, mode, idempotency_key):
        keys.append(idempotency_key)
        if len(keys) == 1:
            raise VerisOCRConnectionError("https://secret.invalid/?api_key=private-value")
        return submit(document, mode=mode, idempotency_key=idempotency_key)

    monkeypatch.setattr(environment.jobs, "submit", lost_response)
    data = pdf(CV)
    with pytest.raises(subject.ExtractionFailure) as error:
        environment.extract(data)
    assert "secret.invalid" not in str(error.value)
    assert "private-value" not in str(error.value)
    restarted = deepcopy(environment.saved[-1])

    assert environment.extract(data, state=restarted)["status"] == "complete"
    assert len(keys) == 2 and keys[0] == keys[1]


def test_state_cannot_be_reused_for_changed_source_bytes(environment):
    data = pdf(CV)
    environment.extract(data)
    with pytest.raises(subject.ExtractionFailure, match="attachment changed"):
        environment.extract(pdf(CV + "\nDifferent source bytes"))
    assert len(environment.submissions) == 1


def test_aadhaar_birth_year_and_number_never_become_invented_candidate_fields(environment):
    payload = result("aadhaar", 1)
    payload["aadhaar"].pop("date_of_birth")
    payload["aadhaar"].update(year_of_birth=1990, aadhaar_number_valid=False)
    environment.results["aadhaar"] = payload

    output = environment.extract(pdf(AADHAAR))

    fields = output["extractions"]["aadhaar"]["fields"]
    assert "dob" not in fields
    assert "passport_no" not in fields
    assert "nationality" not in fields
    assert "current_city" not in fields
    assert any("birth year" in warning for warning in output["warnings"])
    assert any("number did not pass" in warning for warning in output["warnings"])
