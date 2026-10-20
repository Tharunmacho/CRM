"""
OCR for the add-candidate form.

A scan only pre-fills the form; nothing is written here. The recruiter checks the
fields and saves, and the Veris output travels back with that save to be stored on the
candidate (`candidates.ocr_data`). A scan the recruiter abandons leaves nothing behind.

The endpoint is a plain `def` on purpose: the SDK is synchronous and a scan waits on
the Veris job queue for tens of seconds, so FastAPI runs it on the threadpool instead
of stalling every other request behind it.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from veriis import (
    FileDescriptor,
    VerisOCRAuthenticationError,
    VerisOCRBadRequestError,
    VerisOCRError,
    VerisOCRRateLimitError,
    VerisOCRTimeoutError,
    VerisOCRValidationError,
)

from app.core.config import get_settings
from app.db.session import get_db
from app.core.deps import current_user
from app.db.models import Designation, User
from app.integrations.ocr.mapping import aadhaar_fields, passport_fields, resume_fields, trim_raw
from app.integrations.ocr.client import ScanFailed, ScanTimedOut, scan as veris_scan
from app.integrations.ocr.inspection import InspectionError, inspect_document
from app.api.routers.files import MAX_BYTES

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

log = logging.getLogger("uvicorn.error")
settings = get_settings()


def _inspection_error(code: int, message: str, report: dict) -> HTTPException:
    return HTTPException(code, {
        "message": message, "inspection": report, "extraction_status": "needs_review",
    })


def _http_error(exc: VerisOCRError) -> HTTPException:
    if isinstance(exc, (VerisOCRBadRequestError, VerisOCRValidationError)):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The OCR could not read this file. Check the selected pages or enter the details manually.",
        )
    if isinstance(exc, VerisOCRRateLimitError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The OCR is busy. Try the file again in a minute.")
    if isinstance(exc, VerisOCRTimeoutError):
        return HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "The OCR took too long on this file. Try again, or fill the form by hand.",
        )
    # Not passed on as a 401: the page would read that as our own session expiring.
    if isinstance(exc, VerisOCRAuthenticationError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, "The OCR refused our API key. Check VERIS_OCR_API_KEY.")
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        "The OCR is not responding. Fill the form by hand, or try again shortly.",
    )


@router.post("/{kind}")
def scan(
    kind: Literal["resume", "passport", "aadhaar"],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    # Authentication used this session for a read. Release its connection before
    # local OCR or provider polling, both of which can take minutes on long scans.
    db.rollback()
    payload = file.file.read(MAX_BYTES + 1)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Files must be under {MAX_BYTES // (1024 * 1024)} MB.",
        )

    local_started = time.monotonic()
    try:
        inspected = inspect_document(
            payload,
            tesseract_cmd=settings.tesseract_cmd,
            languages=settings.ocr_languages,
            max_pages=settings.ocr_max_pages,
            page_timeout=settings.ocr_page_timeout_seconds,
            document_timeout=settings.ocr_document_timeout_seconds,
        )
    except InspectionError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc

    selected = getattr(inspected, f"{kind}_pages")
    report = inspected.to_dict()
    report["selected_pages"] = selected
    warnings = list(inspected.warnings)
    log.info(
        "Local document inspection: mode=%s pages=%d selected=%s unread=%s uncertain=%s elapsed=%.2fs",
        kind, inspected.page_count, selected, inspected.unread_pages,
        inspected.unknown_pages, time.monotonic() - local_started,
    )
    if not inspected.complete:
        warnings.append(
            "Some pages could not be read or classified confidently. Nothing was sent to Veris. "
            "Review the listed pages and upload a clearer document, or enter the details manually."
        )
        return _response(report, warnings, "needs_review")
    if not selected:
        warnings.append(
            f"No {kind} pages were identified from the document content. Nothing was sent to Veris."
        )
        return _response(report, warnings, "no_resume" if kind == "resume" else "needs_review")
    if not (settings.veris_ocr_base_url and settings.veris_ocr_api_key):
        raise _inspection_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Local inspection is ready. Configure VERIS_OCR_BASE_URL and VERIS_OCR_API_KEY "
            "to extract the selected pages, or enter the details manually.",
            report,
        )

    # Neither the sender's filename nor its declared MIME type chooses a parser.
    # Only content-classified pages, in original order, leave the local reader.
    selected_pdf = inspected.pdf_for_pages(selected)
    doc = FileDescriptor(data=selected_pdf, filename=f"{kind}.pdf", content_type="application/pdf")
    source_hash = hashlib.sha256(payload).hexdigest()
    key_input = f"local-pages-v1:{kind}:{source_hash}:{','.join(map(str, selected))}"
    request_key = "crm-" + hashlib.sha256(key_input.encode()).hexdigest()
    provider_started = time.monotonic()
    try:
        result = veris_scan(
            kind, doc, base_url=settings.veris_ocr_base_url, api_key=settings.veris_ocr_api_key,
            idempotency_key=request_key,
        )
    except ScanFailed:
        log.warning("Veris %s extraction failed (ScanFailed)", kind)
        raise _inspection_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The OCR could not read this file. Check the selected pages or enter the details manually.",
            report,
        ) from None
    except ScanTimedOut:
        log.warning("Veris %s extraction is still running at the deadline", kind)
        raise _inspection_error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "The OCR is still working on this file. Try again in a minute, or fill the form by hand.",
            report,
        ) from None
    except ValidationError:
        log.error("Veris %s result did not match the SDK models (ValidationError)", kind)
        raise _inspection_error(
            status.HTTP_502_BAD_GATEWAY, "The OCR returned a result this app could not read.", report,
        ) from None
    except VerisOCRError as exc:
        log.warning("Veris %s extraction failed (%s)", kind, type(exc).__name__)
        error = _http_error(exc)
        raise _inspection_error(error.status_code, str(error.detail), report) from None

    log.info(
        "Veris extraction: mode=%s selected_pages=%d elapsed=%.2fs",
        kind, len(selected), time.monotonic() - provider_started,
    )
    # A provider success flag alone does not establish page coverage. Require
    # exactly one nonempty result for each submitted page before applying fields.
    returned = list(result.pages)
    if (
        len(returned) != len(selected)
        or {p.page_number for p in returned} != set(range(1, len(selected) + 1))
        or any(not p.text.strip() for p in returned)
    ):
        warnings.append(
            "Veris did not return readable text for every selected page. Extraction needs review; "
            "no fields were applied. Try again to check the same job, or enter the details manually."
        )
        report["provider_page_count"] = len(returned)
        return _response(report, warnings, "needs_review")

    if kind == "resume":
        titles = list(db.scalars(select(Designation.title)))
        fields, provider_warnings = resume_fields(result, designations=titles)
    elif kind == "passport":
        fields, provider_warnings = passport_fields(result)
    else:
        fields, provider_warnings = aadhaar_fields(result)

    raw = trim_raw(result)
    for page in raw["pages"]:
        local_number = page["page_number"]
        page["provider_page_number"] = local_number
        page["page_number"] = selected[local_number - 1]
    raw["pages"].sort(key=lambda p: p["page_number"])
    raw["local_inspection"] = report
    raw["source_sha256"] = source_hash
    return _response(
        report, warnings + provider_warnings, "complete",
        fields=fields.model_dump(by_alias=True, exclude_none=True, mode="json"),
        raw=raw,
    )


def _response(
    inspection: dict,
    warnings: list[str],
    extraction_status: str,
    *,
    fields: dict | None = None,
    raw: dict | None = None,
) -> dict:
    return {
        "fields": fields or {},
        "warnings": warnings,
        "raw": raw or {},
        "inspection": inspection,
        "extraction_status": extraction_status,
    }
