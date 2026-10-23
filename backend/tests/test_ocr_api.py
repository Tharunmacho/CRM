"""OCR request boundaries using synthetic PDFs and no database/provider network."""

import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest
from starlette.datastructures import Headers
from veriis import (
    VerisOCRAuthenticationError, VerisOCRBadRequestError, VerisOCRConnectionError,
    VerisOCRRateLimitError, VerisOCRTimeoutError, VerisOCRValidationError,
)
from veriis.models import AadhaarResponse, PassportResponse, ResumeResponse

from app.integrations.ocr.client import ScanFailed, ScanTimedOut


CERTIFICATE = (
    "CERTIFICATE OF COMPLETION\n"
    "This is to certify that the participant completed the safety training course.\n"
    "The assessment covered safe tools, workshop procedures and protective equipment.\n"
    "Certificate reference SYNTHETIC-CERTIFICATE-MARKER"
)
RESUME = (
    "CURRICULUM VITAE\n"
    "ALICE EXAMPLE\n"
    "alice@example.com\n"
    "Professional Summary\n"
    "Welder with experience in fabrication and equipment maintenance.\n"
    "Work Experience\n"
    "Welder at Example Workshop 2019 - 2024\n"
    "Education\n"
    "Technical diploma in fabrication\n"
    "Skills\n"
    "Welding and blueprint reading\n"
    "SYNTHETIC-RESUME-MARKER"
)
PASSPORT = (
    "PASSPORT\n"
    "REPUBLIC OF EXAMPLE\n"
    "Surname: EXAMPLE\n"
    "Given names: ALICE\n"
    "Nationality: EXAMPLE\n"
    "Date of birth: 01 JAN 1990\n"
    "Date of expiry: 01 JAN 2030\n"
    "Passport number: X0000000\n"
    "SYNTHETIC-PASSPORT-MARKER"
)
AADHAAR = (
    "AADHAAR\nGovernment of India\nUnique Identification Authority of India\n"
    "Name: ALICE EXAMPLE\nDate of birth: 01 JAN 1990\nGender: Female\n"
    "Aadhaar number: 0000 0000 0000\nSYNTHETIC-AADHAAR-MARKER"
)


def native_pdf(*texts):
    with fitz.open() as document:
        for text in texts:
            document.new_page().insert_text((48, 48), text, fontsize=11)
        return document.tobytes(no_new_id=True)


def provider_page(number, text="Verified synthetic page text"):
    return {
        "page_number": number,
        "text": text,
        "language": "eng",
        "width": 595,
        "height": 842,
        "average_confidence": 0.95,
        "processing_time_ms": 1,
        "lines": [],
    }


def provider_response(kind="resume", pages=None):
    """The provider's result dict as returned (extra keys and line boxes included)."""
    payload = _provider_model(kind, pages).model_dump(mode="json")
    payload["unmodelled_key"] = {"kept": [1]}
    return payload


def _provider_model(kind, pages):
    values = {
        "request_id": "synthetic-ocr-result",
        "processing_time_ms": 1,
        "pages": [provider_page(1)] if pages is None else pages,
    }
    if kind == "passport":
        return PassportResponse.model_validate({
            **values,
            "confidence": 0.95,
            "mrz": {
                "given_names": "ALICE",
                "surname": "EXAMPLE",
                "passport_number": "X0000000",
                "all_check_digits_valid": True,
            },
        })
    if kind == "aadhaar":
        return AadhaarResponse.model_validate({
            **values, "page_count": len(values["pages"]),
            "languages_used": "eng", "total_processing_time_ms": 1,
            "aadhaar": {
                "name": "ALICE EXAMPLE", "date_of_birth": "1990-01-01",
                "gender": "female", "masked_aadhaar_number": "XXXX XXXX 0000",
            },
        })
    return ResumeResponse.model_validate({
        **values,
        "name": "ALICE EXAMPLE",
        "contact": {"emails": ["Alice@Example.com"]},
        "designation": "welder",
        "skills": ["Welding"],
    })


class FakeSession:
    def __init__(self):
        self.events = []

    def rollback(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Blocking OCR work must run outside the event loop")
        self.events.append("rollback")

    def scalars(self, statement):
        self.events.append("designations")
        return ["Welder"]


@pytest.fixture
def ocr_environment(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://unused@127.0.0.1:1/unused",
        storage_dir=str(tmp_path),
        veris_ocr_base_url="https://ocr.invalid",
        veris_ocr_api_key="synthetic-key",
        tesseract_cmd=None,
        ocr_languages="eng",
        ocr_max_pages=300,
        ocr_page_timeout_seconds=20,
        ocr_document_timeout_seconds=600,
    )
    # Prevent imports from loading backend/.env. The engine, if constructed,
    # points at an unused local port and every request replaces its dependency.
    with patch("app.core.config.get_settings", return_value=settings):
        from app.api.routers import ocr

    monkeypatch.setattr(ocr, "settings", settings)
    database = FakeSession()
    calls = []
    real_inspect = ocr.inspect_document

    def inspect(payload, **kwargs):
        assert database.events[-1] == "rollback"
        database.events.append("inspection")
        return real_inspect(payload, **kwargs)

    def provider(kind, document, **kwargs):
        assert database.events[-1] == "inspection"
        database.events.append("provider")
        calls.append((kind, document, kwargs))
        return provider_response(kind)

    monkeypatch.setattr(ocr, "inspect_document", inspect)
    monkeypatch.setattr(ocr, "veris_scan", provider)
    application = FastAPI()
    application.include_router(ocr.router)
    application.dependency_overrides[ocr.get_db] = lambda: database
    application.dependency_overrides[ocr.current_user] = lambda: SimpleNamespace(id="synthetic-user")
    with TestClient(application) as client:
        yield SimpleNamespace(
            client=client, application=application, ocr=ocr, database=database,
            calls=calls, settings=settings, inspect=real_inspect,
        )


def post_scan(environment, payload, *, kind="resume", filename="document.pdf", mime="application/pdf"):
    return environment.client.post(
        f"/api/ocr/{kind}", files={"file": (filename, payload, mime)}
    )


@pytest.mark.parametrize(
    ("kind", "selected", "marker"),
    [
        ("resume", 2, "SYNTHETIC-RESUME-MARKER"),
        ("passport", 3, "SYNTHETIC-PASSPORT-MARKER"),
        ("aadhaar", 4, "SYNTHETIC-AADHAAR-MARKER"),
    ],
)
def test_content_selects_only_matching_pages_despite_filename_and_mime(
    ocr_environment, kind, selected, marker
):
    environment = ocr_environment
    payload = native_pdf(CERTIFICATE, RESUME, PASSPORT, AADHAAR)

    response = post_scan(
        environment, payload, kind=kind, filename="invoice.exe", mime="application/octet-stream"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_status"] == "complete"
    report = body["inspection"]
    assert report["source_format"] == "pdf"
    assert report["page_count"] == 4
    assert report["resume_pages"] == [2]
    assert report["passport_pages"] == [3]
    assert report["aadhaar_pages"] == [4]
    assert report["selected_pages"] == [selected]
    assert report["unread_pages"] == report["unknown_pages"] == []
    assert all("text" not in page for page in report["pages"])
    assert body["fields"]["name"] == "Alice Example"
    if kind == "resume":
        assert body["fields"]["email"] == "alice@example.com"
        assert body["fields"]["designation"] == "Welder"
    if kind == "aadhaar":
        assert body["fields"] == {"name": "Alice Example", "dob": "1990-01-01", "gender": "female"}
        assert body["raw"]["aadhaar"]["masked_aadhaar_number"] == "XXXX XXXX 0000"
    # The raw document is the provider's result, untouched; provenance is separate.
    assert body["raw"] == provider_response(kind)
    assert body["raw"]["pages"][0]["page_number"] == 1
    assert body["source"]["selected_pages"] == [selected]
    assert body["source"]["local_inspection"]["selected_pages"] == [selected]
    assert body["source"]["source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert not {"provider_page_number", "local_inspection", "source_sha256"} & set(body["raw"])
    called_kind, upload, options = environment.calls[0]
    assert called_kind == kind
    assert upload.filename == f"{kind}.pdf"
    assert upload.content_type == "application/pdf"
    assert options["idempotency_key"]
    with fitz.open(stream=upload.data, filetype="pdf") as sent:
        assert sent.page_count == 1
        sent_text = sent[0].get_text()
        assert marker in sent_text
        assert "SYNTHETIC-CERTIFICATE-MARKER" not in sent_text
        for other in ("SYNTHETIC-RESUME-MARKER", "SYNTHETIC-PASSPORT-MARKER", "SYNTHETIC-AADHAAR-MARKER"):
            if other != marker:
                assert other not in sent_text
    assert environment.database.events[:3] == ["rollback", "inspection", "provider"]
    assert ("designations" in environment.database.events) == (kind == "resume")


def test_retry_reuses_key_and_identical_payload_while_mode_and_source_are_distinct(ocr_environment):
    environment = ocr_environment
    payload = native_pdf(CERTIFICATE, RESUME, PASSPORT)
    changed = native_pdf(CERTIFICATE, RESUME.replace("2019", "2018"), PASSPORT)

    assert post_scan(environment, payload).status_code == 200
    assert post_scan(environment, payload, filename="renamed.jpg", mime="image/jpeg").status_code == 200
    assert post_scan(environment, payload, kind="passport").status_code == 200
    assert post_scan(environment, changed).status_code == 200

    first, retry, passport, changed_source = environment.calls
    assert first[2]["idempotency_key"] == retry[2]["idempotency_key"]
    assert first[1].data == retry[1].data
    assert len({
        first[2]["idempotency_key"], passport[2]["idempotency_key"],
        changed_source[2]["idempotency_key"],
    }) == 3


@pytest.mark.parametrize(
    ("kind", "content", "expected"),
    [
        ("resume", CERTIFICATE, "no_resume"), ("passport", RESUME, "needs_review"),
        ("aadhaar", RESUME, "needs_review"),
    ],
)
def test_no_matching_content_never_calls_provider(ocr_environment, kind, content, expected):
    environment = ocr_environment
    # Content-only decisions must still work before provider configuration exists.
    environment.settings.veris_ocr_api_key = ""

    response = post_scan(environment, native_pdf(content), kind=kind, filename="cv.pdf")

    assert response.status_code == 200
    assert response.json()["extraction_status"] == expected
    assert response.json()["inspection"]["selected_pages"] == []
    assert response.json()["fields"] == response.json()["raw"] == {}
    assert environment.calls == []
    assert environment.database.events == ["rollback", "inspection"]


@pytest.mark.parametrize("unresolved", ["unread", "unknown"])
def test_one_unresolved_page_prevents_partial_paid_extraction(ocr_environment, monkeypatch, unresolved):
    from app.integrations.ocr.inspection import InspectedPage, Inspection

    environment = ocr_environment
    payload = native_pdf(RESUME, CERTIFICATE)
    inspection = Inspection(
        [
            InspectedPage(1, "read", "resume", "native_text", RESUME, 1.0),
            InspectedPage(
                2, "unread" if unresolved == "unread" else "read", "unknown",
                "failed" if unresolved == "unread" else "native_text",
                "" if unresolved == "unread" else "Unclassified synthetic document",
                reason="Synthetic unresolved page",
            ),
        ],
        2, "pdf", ["Page 2 needs review"], payload,
    )

    def inspect(data, **kwargs):
        assert environment.database.events == ["rollback"]
        environment.database.events.append("inspection")
        return inspection

    monkeypatch.setattr(environment.ocr, "inspect_document", inspect)
    response = post_scan(environment, payload)

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_status"] == "needs_review"
    assert body["inspection"][f"{unresolved}_pages"] == [2]
    assert body["fields"] == body["raw"] == {}
    assert environment.calls == []
    assert "designations" not in environment.database.events


@pytest.mark.parametrize(
    "pages",
    [
        [],
        [provider_page(1)],
        [provider_page(1), provider_page(1)],
        [provider_page(1), provider_page(3)],
        [provider_page(1), provider_page(2, " \n ")],
        [provider_page(1), provider_page(2), provider_page(3)],
    ],
    ids=["missing", "partial", "duplicate", "wrong-number", "empty-text", "extra"],
)
def test_incomplete_provider_coverage_cannot_be_complete(ocr_environment, monkeypatch, pages):
    environment = ocr_environment

    def provider(kind, document, **kwargs):
        environment.calls.append((kind, document, kwargs))
        return provider_response(pages=pages)

    monkeypatch.setattr(environment.ocr, "veris_scan", provider)
    response = post_scan(environment, native_pdf(CERTIFICATE, RESUME, RESUME))

    assert response.status_code == 200
    body = response.json()
    assert body["inspection"]["selected_pages"] == [2, 3]
    assert body["extraction_status"] == "needs_review"
    assert body["fields"] == body["raw"] == {}
    assert body["warnings"]
    assert len(environment.calls) == 1
    assert "designations" not in environment.database.events


def test_complete_provider_pages_are_stored_in_provider_order_with_a_page_map(ocr_environment, monkeypatch):
    environment = ocr_environment

    def provider(kind, document, **kwargs):
        return provider_response(pages=[
            provider_page(2, "Second selected page"),
            provider_page(1, "First selected page"),
        ])

    monkeypatch.setattr(environment.ocr, "veris_scan", provider)
    response = post_scan(environment, native_pdf(CERTIFICATE, RESUME, RESUME))

    assert response.status_code == 200
    body = response.json()
    assert body["extraction_status"] == "complete"
    assert body["inspection"]["selected_pages"] == [2, 3]
    assert body["source"]["selected_pages"] == [2, 3]
    # Veris order and numbering are kept; `source.selected_pages[page_number - 1]` is the original page.
    assert [(page["page_number"], page["text"]) for page in body["raw"]["pages"]] == [
        (2, "Second selected page"), (1, "First selected page")]
    assert [body["source"]["selected_pages"][page["page_number"] - 1] for page in body["raw"]["pages"]] == [3, 2]


def test_pdf_filename_and_mime_do_not_admit_non_document_bytes(ocr_environment):
    environment = ocr_environment

    response = post_scan(environment, b"These are not PDF bytes", filename="resume.pdf")

    assert response.status_code == 422
    assert environment.calls == []
    assert environment.database.events == ["rollback", "inspection"]


def test_eligible_document_requires_provider_configuration_after_inspection(ocr_environment):
    environment = ocr_environment
    environment.settings.veris_ocr_api_key = ""

    response = post_scan(environment, native_pdf(RESUME))

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["extraction_status"] == "needs_review"
    assert detail["inspection"]["page_count"] == 1
    assert detail["inspection"]["selected_pages"] == [1]
    assert all("text" not in page for page in detail["inspection"]["pages"])
    assert "Configure" in detail["message"]
    assert environment.calls == []
    assert environment.database.events == ["rollback", "inspection"]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ScanFailed("PRIVATE provider document content"), 422),
        (ScanTimedOut("PRIVATE pending job identifier"), 504),
        (VerisOCRAuthenticationError("PRIVATE provider authentication failure"), 502),
        (VerisOCRRateLimitError("PRIVATE provider rate limit"), 429),
        (VerisOCRBadRequestError("PRIVATE source document"), 422),
        (VerisOCRValidationError("PRIVATE validation input"), 422),
        (VerisOCRConnectionError("https://PRIVATE.invalid/?key=PRIVATE"), 502),
        (VerisOCRTimeoutError("PRIVATE timeout input"), 504),
    ],
)
def test_provider_failure_preserves_local_inspection_without_candidate_fields(
    ocr_environment, monkeypatch, caplog, error, status
):
    environment = ocr_environment

    def provider(kind, document, **kwargs):
        assert environment.database.events[-1] == "inspection"
        environment.calls.append((kind, document, kwargs))
        raise error

    monkeypatch.setattr(environment.ocr, "veris_scan", provider)
    response = post_scan(
        environment, native_pdf(CERTIFICATE, RESUME, PASSPORT), filename="PRIVATE-document.pdf",
    )

    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["extraction_status"] == "needs_review"
    assert detail["message"]
    assert detail["inspection"]["page_count"] == 3
    assert detail["inspection"]["selected_pages"] == [2]
    assert detail["inspection"]["passport_pages"] == [3]
    assert all("text" not in page for page in detail["inspection"]["pages"])
    assert "fields" not in detail
    assert "raw" not in detail
    assert len(environment.calls) == 1
    assert "designations" not in environment.database.events
    assert "PRIVATE" not in response.text
    assert "PRIVATE" not in caplog.text


def test_invalid_provider_model_does_not_expose_its_input(ocr_environment, monkeypatch, caplog):
    def provider(*args, **kwargs):
        return {"request_id": {"PRIVATE": "document identity"}, "processing_time_ms": 1}

    monkeypatch.setattr(ocr_environment.ocr, "veris_scan", provider)
    response = post_scan(ocr_environment, native_pdf(RESUME), filename="PRIVATE-document.pdf")

    assert response.status_code == 502
    assert response.json()["detail"]["inspection"]["selected_pages"] == [1]
    assert "PRIVATE" not in response.text
    assert "PRIVATE" not in caplog.text


def test_candidate_ocr_input_accepts_aadhaar_as_separate_evidence(ocr_environment):
    with patch("app.core.config.get_settings", return_value=ocr_environment.settings):
        from app.api.routers.candidates import OcrDataIn

    raw = provider_response("aadhaar")
    source = {"source_sha256": "a", "selected_pages": [4], "local_inspection": {"page_count": 4}}
    stored = OcrDataIn.model_validate({"aadhaar": raw, "_sources": {"aadhaar": source}}).stored()

    assert stored == {"aadhaar": raw, "_sources": {"aadhaar": source}}


def test_unauthenticated_request_cannot_inspect_or_submit(ocr_environment):
    environment = ocr_environment
    del environment.application.dependency_overrides[environment.ocr.current_user]

    response = post_scan(environment, native_pdf(RESUME))

    assert response.status_code == 401
    assert environment.calls == []
    assert environment.database.events == []


@pytest.mark.parametrize(("payload", "status"), [(b"", 400), (b"x" * 1025, 413)])
def test_invalid_size_is_rejected_before_inspection(ocr_environment, monkeypatch, payload, status):
    environment = ocr_environment
    monkeypatch.setattr(environment.ocr, "MAX_BYTES", 1024)

    response = post_scan(environment, payload)

    assert response.status_code == status
    assert environment.calls == []
    assert "inspection" not in environment.database.events


def test_read_is_bounded_to_limit_plus_one_byte(ocr_environment, monkeypatch):
    environment = ocr_environment
    monkeypatch.setattr(environment.ocr, "MAX_BYTES", 1024)
    stream = BytesIO(b"x" * 20_000)
    upload = UploadFile(
        file=stream, filename="large.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )
    try:
        with pytest.raises(HTTPException) as error:
            environment.ocr.scan(
                kind="resume", file=upload, db=environment.database,
                user=SimpleNamespace(id="synthetic-user"),
            )
        assert error.value.status_code == 413
        assert stream.tell() == 1025
        assert environment.calls == []
    finally:
        stream.close()
