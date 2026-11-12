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


def full_page(kind, number):
    box = {"x": 10, "y": 20, "w": 300, "h": 18}
    return {
        "page_number": number, "text": f"{kind} complete text {number}\nsecond line", "html": f"<p>{kind} {number}</p>",
        "language": "eng+hin", "width": 1240, "height": 1754, "average_confidence": 0.93, "processing_time_ms": 40 + number,
        "lines": [{"text": f"{kind} line", "confidence": 0.91, "bbox": box,
                   "words": [{"text": kind, "confidence": 0.9, "bbox": box}]}],
        "page_addition": {"kept": number},
    }


def full_payload(kind, count):
    """A provider result touching every documented field plus additions the SDK does not model."""
    pages = [full_page(kind, number) for number in range(1, count + 1)]
    if kind == "aadhaar":
        return {
            "request_id": "req-aadhaar", "page_count": count, "pages": pages, "document_type": "aadhaar",
            "languages_used": "eng+hin", "total_processing_time_ms": 77, "warnings": ["Back side is blurred"],
            "aadhaar": {
                "name": "ALEX EXAMPLE", "aadhaar_number": "000000000000", "masked_aadhaar_number": "XXXX XXXX 0000",
                "aadhaar_number_valid": True, "date_of_birth": "1990-01-01", "year_of_birth": 1990, "gender": "male",
                "mobile_number": "0000000000", "address": "1 Example Road, Example City", "care_of": "C/O Example Parent",
                "pincode": "000000", "vid": "0000000000000000", "enrollment_id": "0000/00000/00000", "document_side": "both",
                "card_addition": "kept",
            },
            "top_addition": {"kept": True},
        }
    if kind == "passport":
        return {
            "request_id": "req-passport", "processing_time_ms": 55, "pages": pages, "confidence": 0.97,
            "mrz_source": "mrz", "raw_mrz": "P<EXAMPLE<<ALEX<<<<<<\nP0000000<0EXA9001010M3001019<<<<<<<<<<<<<<00",
            "warnings": [], "top_addition": {"kept": True},
            "mrz": {
                "document_type": "P", "issuing_country": "IND", "surname": "EXAMPLE", "given_names": "ALEX",
                "passport_number": "P0000000", "nationality": "IND", "date_of_birth": "1990-01-01", "sex": "M",
                "expiry_date": "2030-01-01", "date_of_issue": "2020-01-02", "personal_number": "PN1",
                "all_check_digits_valid": True,
                "individual_check_digits": {"passport_number": True, "date_of_birth": True, "expiry_date": True,
                                            "personal_number": True, "composite": True},
            },
            "fields": [{"label": "Place of issue", "value": "COCHIN", "category": "place", "page": 1,
                        "source": "visual", "confidence": 0.88}],
        }
    return {
        "request_id": "req-resume", "processing_time_ms": 99, "pages": pages, "name": "ALEX EXAMPLE",
        "contact": {"emails": ["alex@example.test", "alt@example.test"], "phones": ["0000000000", "0000000001"],
                    "linkedin": "https://linkedin.example/alex", "address": "2 Example Road", "websites": ["https://alex.example"]},
        "personal_info": {"father_name": "Parent Example", "marital_status": "single", "religion": "n/a",
                          "place_of_birth": "Example City", "languages_known": ["English", "Hindi"]},
        "designation": "welder", "industry": "Construction", "highest_qualification": "Diploma",
        "experience": [{"title": "Welder", "company": "ACME", "location": "Dubai", "country": "UAE", "is_overseas": True,
                        "start": "2019-01", "end": "2022-01", "duration_months": 36, "duration_human": "3 years",
                        "description": "TIG welding", "entry_addition": 1}],
        "total_experience_months": 36, "total_experience_years": 3.0, "total_experience_human": "3 years",
        "overseas_experience_months": 36, "overseas_experience_years": 3.0, "overseas_experience_human": "3 years",
        "skills": ["TIG"], "projects": [{"name": "Plant", "description": "Pipe work"}],
        "education": [{"degree": "Diploma", "institution": "ITI", "start": "2015", "end": "2018"}],
        "achievements": ["Best welder"], "certifications": ["6G"], "warnings": [], "top_addition": {"kept": True},
    }


@pytest.fixture
def environment(monkeypatch):
    settings = SimpleNamespace(
        tesseract_cmd=None, ocr_languages="eng", ocr_max_pages=300,
        ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600,
        veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key",
    )
    state, saved, submissions, polls, opened = {}, [], [], [], []
    statuses, results, submitted_modes = {}, {}, {}

    def save(snapshot):
        saved.append(deepcopy(snapshot))

    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            # The idempotency intent must be durable before the paid request.
            assert any(part["idempotency_key"] == idempotency_key for record in saved[-1]["provider_jobs"].values()
                       for part in record.get("chunks") or [record])
            submissions.append((mode, document, idempotency_key))
            with fitz.open(stream=document.data, filetype="pdf") as sent:
                results.setdefault(mode, result(mode, sent.page_count))
            index = sum(value == mode for value in submitted_modes.values())
            job_id = f"job-{mode}" + (f"-{index + 1}" if index else "")
            submitted_modes[job_id] = mode
            return SimpleNamespace(job_id=job_id, status="queued")

        def get(self, job_id):
            kind = submitted_modes.get(job_id, job_id.removeprefix("job-"))
            # A crash after acceptance cannot lose the job identifier.
            assert any(part.get("job_id") == job_id for record in saved[-1]["provider_jobs"].values()
                       for part in record.get("chunks") or [record])
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
    # The kinds are submitted side by side, so their order is not defined.
    assert sorted(entry[0] for entry in environment.submissions) == ["aadhaar", "passport", "resume"]
    expected = {"resume": [2], "passport": [3], "aadhaar": [4, 5]}
    for kind, extraction in output["extractions"].items():
        assert extraction["selected_pages"] == expected[kind]
        # Veris numbers stay the provider's; the mapping to the original file is in `source`.
        assert [page["page_number"] for page in extraction["raw"]["pages"]] == list(range(1, len(expected[kind]) + 1))
        assert extraction["source"]["selected_pages"] == expected[kind]
        assert not {"provider_page_number", "local_inspection", "source_sha256"} & set(extraction["raw"])
        assert any("result" in snapshot.get("provider_jobs", {}).get(kind, {}) for snapshot in environment.saved)
    aadhaar = output["extractions"]["aadhaar"]
    assert aadhaar["fields"] == {
        "name": "Alex Example", "dob": "1990-01-01", "gender": "male",
        "current_address": "An example address without a definite city field",  # the address is a form-only value now (13E)
    }
    assert aadhaar["raw"]["aadhaar"]["masked_aadhaar_number"] == "XXXX XXXX 0000"
    assert output["extractions"]["resume"]["fields"]["total_experience"] == 3
    assert "totalExperience" not in output["extractions"]["resume"]["fields"]
    assert output["extractions"]["passport"]["fields"]["passport_no"] == "P0000000"


def test_the_stored_document_is_exactly_the_provider_result_for_resume_passport_and_aadhaar(environment):
    payloads = {"resume": full_payload("resume", 1), "passport": full_payload("passport", 1), "aadhaar": full_payload("aadhaar", 2)}
    environment.results.update(deepcopy(payloads))

    output = environment.extract(pdf(CV, PASSPORT, AADHAAR, AADHAAR_BACK))

    assert output["status"] == "complete"
    expected_pages = {"resume": [1], "passport": [2], "aadhaar": [3, 4]}
    for kind, payload in payloads.items():
        extraction = output["extractions"][kind]
        # Byte-for-byte: unknown keys, html, line boxes and the provider's page numbers included.
        assert extraction["raw"] == payload
        assert extraction["raw"] == environment.saved[-1]["provider_jobs"][kind]["result"]
        assert extraction["source"]["selected_pages"] == expected_pages[kind]
        assert extraction["source"]["local_inspection"]["selected_pages"] == expected_pages[kind]
        assert extraction["source"]["local_inspection"]["page_count"] == 4
        assert extraction["source"]["source_sha256"] == environment.state["source_sha256"]
    # Aadhaar identifiers and the passport's printed fields are stored although not mapped to columns.
    assert set(output["extractions"]["aadhaar"]["fields"]) == {"name", "dob", "gender", "current_address"}
    assert output["extractions"]["aadhaar"]["raw"]["aadhaar"]["aadhaar_number"] == "000000000000"
    assert output["extractions"]["passport"]["raw"]["fields"][0]["value"] == "COCHIN"
    assert output["extractions"]["passport"]["fields"]["passport_place"] == "COCHIN"
    # The paid response also stays verbatim in the durable checkpoint.
    for kind, payload in payloads.items():
        assert environment.saved[-1]["provider_jobs"][kind]["result"] == payload


def test_a_page_beyond_the_limit_is_reported_and_the_pages_that_were_read_are_still_extracted(environment):
    environment.settings.ocr_max_pages = 1

    output = environment.extract(pdf(CV, PASSPORT))

    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume"}
    assert output["inspection"]["unread_pages"] == [2]
    assert "too many to read" in output["inspection"]["pages"][1]["reason"]
    assert [entry[0] for entry in environment.submissions] == ["resume"]
    assert output["coverage"]["pages"]["not_sent"][0]["page"] == 2 and output["coverage"]["pages"]["not_sent"][0]["blocking"]
    # An explicit opt-out still holds the whole file back.
    held = environment.extract(pdf(CV, PASSPORT), partial=False, state={})
    assert held["status"] == "needs_review" and held["extractions"] == {}


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


@pytest.mark.parametrize("early_dispatch", [False, True])
def test_classifier_upgrade_retries_missing_continuation_once_and_reuses_paid_first_page(environment, monkeypatch, early_dispatch):
    continuation = """Livspace Home Interior
Job title: Interior Designer
Duration: November 2024 to February 2026
Location: Chennai, India
Software Skills
AutoCAD and SketchUp
"""
    data = pdf(CV, continuation)
    inspect = subject.inspect_document
    def old_inspection(*args, **kwargs):
        report = inspect(*args, **kwargs)
        report.pages[1].document_type = "unknown"
        report.pages[1].kinds = []
        report.pages[1].inherited = False
        return report
    environment.settings.veris_early_dispatch = False
    current_version = subject.INSPECTION_VERSION
    monkeypatch.setattr(subject, "INSPECTION_VERSION", "old-classifier")
    monkeypatch.setattr(subject, "inspect_document", old_inspection)
    environment.results["resume"] = {**result("resume", 1), "total_experience_months": 16,
        "experience": [{"company": "Designhaus", "title": "Interior Designer", "start": "2023-08", "end": "2024-11",
                        "country": "India", "is_overseas": False, "duration_months": 16}]}
    first = environment.extract(data, partial=True)
    assert first["inspection"]["resume_pages"] == [1] and first["status"] == "needs_review"
    assert len(environment.submissions) == 1
    paid = deepcopy(environment.state["provider_jobs"]["resume"])
    assert paid["selected_pages"] == [1] and paid["status"] == "succeeded"
    environment.state["documents"] = {"resume": {"object_key": "old-single-page-derived.pdf"}}
    environment.state["output"] = deepcopy(first)
    # Mirror the explicit retry endpoint, retaining accepted work and clearing its final output.
    subject.request_extraction_retry(environment.state)
    environment.state.pop("output", None)
    monkeypatch.setattr(subject, "INSPECTION_VERSION", current_version)
    monkeypatch.setattr(subject, "inspect_document", inspect)
    environment.settings.veris_early_dispatch = early_dispatch
    environment.results["resume"] = {**result("resume", 1), "total_experience_months": 15,
        "experience": [{"company": "Livspace", "title": "Interior Designer", "start": "2024-11", "end": "2026-02",
                        "country": "India", "is_overseas": False, "duration_months": 15}]}
    complete = environment.extract(data, partial=True)
    assert complete["inspection"]["resume_pages"] == [1, 2]
    assert complete["status"] == "complete" and complete["coverage"]["complete"]
    assert complete["extractions"]["resume"]["selected_pages"] == [1, 2]
    assert {entry["company"] for entry in complete["extractions"]["resume"]["raw"]["experience"]} == {"Designhaus", "Livspace"}
    assert complete["extractions"]["resume"]["raw"]["total_experience_months"] == 31
    assert environment.state["provider_jobs"]["resume"] == paid
    assert environment.state["provider_jobs"]["resume+"]["selected_pages"] == [2]
    assert len(environment.submissions) == 2 and len(environment.polls) == 2
    with fitz.open(stream=environment.submissions[1][1].data, filetype="pdf") as sent:
        assert sent.page_count == 1 and "Livspace" in sent[0].get_text() and "CURRICULUM" not in sent[0].get_text()
    assert environment.state["documents"] == {}
    subject.request_extraction_retry(environment.state)
    again = environment.extract(data, partial=True)
    assert again["status"] == "complete" and again["extractions"]["resume"]["selected_pages"] == [1, 2]
    assert len(environment.submissions) == 2 and len(environment.polls) == 2


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
    with pytest.raises(subject.ExtractionFailure, match="could not read the resume pages"):
        environment.extract(data)
    restarted = deepcopy(environment.saved[-1])
    with pytest.raises(subject.ExtractionFailure, match="could not read the resume pages"):
        environment.extract(data, state=restarted)
    assert len(environment.submissions) == len(environment.polls) == len(environment.opened) == 1


def test_budgeted_retry_allows_initial_attempt_and_three_distinct_confirmed_failure_retries(environment):
    data = pdf(CV)
    environment.statuses["resume"] = "failed"
    for attempt in range(1, 5):
        with pytest.raises(subject.ExtractionFailure, match="could not read the resume pages"):
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
def test_local_incomplete_inspection_prevents_all_paid_calls_when_partial_extraction_is_off(environment, unresolved):
    if unresolved == "page-limit":
        environment.settings.ocr_max_pages = 1
        data = pdf(CV, CERTIFICATE)
    else:
        data = pdf(CV, "Readable correspondence about unrelated meeting arrangements. " * 4)

    output = environment.extract(data, partial=False)

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
    with pytest.raises(subject.ExtractionFailure, match="not set up"):
        environment.extract(pdf(CV))
    assert environment.saved[-1]["inspection"]["resume_pages"] == [1]
    assert environment.submissions == environment.opened == []


def test_checkpoint_failure_stops_before_paid_work(environment):
    def unavailable(snapshot):
        raise RuntimeError("Synthetic storage outage")

    with pytest.raises(subject.ExtractionFailure, match="could not save the progress"):
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
    assert any("number does not look right" in warning for warning in output["warnings"])


# ---- partial extraction, dual-kind pages, unreadable and decorative files -----------------

UNCLASSIFIED = "This is correspondence about travel arrangements and several unrelated meeting dates. " * 3


def gray_png(width=600, height=800):
    import io
    from PIL import Image
    with Image.new("RGB", (width, height), "gray") as image:
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


def test_unclassified_page_no_longer_holds_the_rest_of_the_file_back(environment):
    data = pdf(CV, UNCLASSIFIED)
    output = environment.extract(data)
    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume"}
    held = environment.extract(data, partial=False, state={})
    assert held["status"] == "needs_review" and not held["extractions"]


def test_partial_extraction_sends_the_classified_pages_and_names_the_rest(environment):
    output = environment.extract(pdf(CV, UNCLASSIFIED), partial=True)

    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume"}
    assert output["extractions"]["resume"]["selected_pages"] == [1]
    assert output["inspection"]["unknown_pages"] == [2]
    assert any("Pages 2" in warning and "not extracted" in warning for warning in output["warnings"])
    assert [entry[0] for entry in environment.submissions] == ["resume"]
    with fitz.open(stream=environment.submissions[0][1].data, filetype="pdf") as sent:
        assert sent.page_count == 1


def test_partial_extraction_with_nothing_classified_still_needs_review(environment):
    output = environment.extract(pdf(UNCLASSIFIED), partial=True)
    assert output["status"] == "needs_review" and not output["extractions"] and not environment.submissions


def test_partial_checkpoint_resumes_without_a_second_submission(environment):
    data = pdf(CV, UNCLASSIFIED)
    environment.extract(data, partial=True)
    again = environment.extract(data, partial=True)
    assert again["extractions"]["resume"]["selected_pages"] == [1]
    assert len(environment.submissions) == 1


def test_page_with_passport_and_aadhaar_is_sent_to_both_extractions(environment):
    output = environment.extract(pdf(CV, PASSPORT + AADHAAR))

    assert output["status"] == "complete"
    assert output["extractions"]["passport"]["selected_pages"] == [2]
    assert output["extractions"]["aadhaar"]["selected_pages"] == [2]


def test_decorative_image_is_ignored_only_when_the_caller_allows_it(environment):
    data = gray_png()
    ignored = environment.extract(data, ignore_decorative=True)
    assert ignored["status"] == "ignored" and ignored["reason"] and not ignored["warnings"]
    held = environment.extract(data, state={})
    assert held["status"] == "needs_review"


def test_damaged_file_is_unreadable_but_a_missing_converter_is_a_retryable_failure(environment, monkeypatch):
    with pytest.raises(subject.UnreadableDocument):
        environment.extract(b"%PDF-1.4 definitely not a pdf", state={})
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w/>")
    monkeypatch.setattr(inspection, "_find_libreoffice", lambda: None)
    with pytest.raises(subject.ExtractionFailure) as raised:
        environment.extract(buffer.getvalue(), state={})
    assert not isinstance(raised.value, subject.UnreadableDocument)


@pytest.mark.parametrize("missing_last", [False, True])
def test_every_page_is_accounted_for_across_resume_passport_and_aadhaar(environment, missing_last):
    from test_ocr_classification import PASSPORT_ADDRESS
    continuation = """Example Interiors Pvt Ltd
Job title: Interior Designer
Duration: November 2024 - February 2026
Location: Example City, India
Software Skills
Design and drawing software
"""
    pages = [CV, continuation, PASSPORT, PASSPORT_ADDRESS, AADHAAR, AADHAAR_BACK, CERTIFICATE]
    if missing_last:
        pages.append("Unlabelled content with several words and numbers. Its purpose is unknown.")
    output = environment.extract(pdf(*pages))
    coverage = output["coverage"]
    assert coverage["pages"]["total"] == len(pages)
    assert output["extractions"]["resume"]["selected_pages"] == [1, 2]
    assert output["extractions"]["passport"]["selected_pages"] == [3, 4]
    assert output["extractions"]["aadhaar"]["selected_pages"] == [5, 6]
    sent = set(coverage["pages"]["sent"])
    excluded = {page["page"] for page in coverage["pages"]["not_sent"]}
    assert not sent & excluded
    assert sent | excluded == set(range(1, len(pages) + 1))
    assert all(page["reason"] for page in coverage["pages"]["not_sent"])
    assert 7 in coverage["pages"]["ignored"]
    if missing_last:
        assert coverage["pages"]["unresolved"] == [8]
        assert coverage["complete"] is False and output["status"] == "needs_review"
    else:
        assert coverage["pages"]["unresolved"] == []
        assert coverage["complete"] is True and output["status"] == "complete"
