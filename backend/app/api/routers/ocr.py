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

import logging
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
from app.integrations.ocr.mapping import passport_fields, resume_fields, trim_raw
from app.integrations.ocr.client import ScanFailed, ScanTimedOut, scan as veris_scan
from app.api.routers.files import ALLOWED, MAX_BYTES

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

log = logging.getLogger("ocr")
settings = get_settings()


def _http_error(exc: VerisOCRError) -> HTTPException:
    if isinstance(exc, (VerisOCRBadRequestError, VerisOCRValidationError)):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"The OCR could not read this file: {exc}")
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
    kind: Literal["resume", "passport"],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if not (settings.veris_ocr_base_url and settings.veris_ocr_api_key):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OCR is not configured. Set VERIS_OCR_BASE_URL and VERIS_OCR_API_KEY, or fill the form by hand.",
        )

    payload = file.file.read()
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Files must be under {MAX_BYTES // (1024 * 1024)} MB.",
        )
    if file.content_type and file.content_type not in ALLOWED:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Upload a PDF, Word document, or an image.",
        )

    doc = FileDescriptor(data=payload, filename=file.filename or None, content_type=file.content_type or None)
    try:
        result = veris_scan(
            kind, doc, base_url=settings.veris_ocr_base_url, api_key=settings.veris_ocr_api_key
        )
    except ScanFailed as exc:
        log.warning("veris %s job for %r failed: %s", kind, file.filename, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"The OCR could not read this file: {exc}"
        ) from exc
    except ScanTimedOut as exc:
        log.warning("veris %s job %s for %r still running at the deadline", kind, exc, file.filename)
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "The OCR is still working on this file. Try again in a minute, or fill the form by hand.",
        ) from exc
    except ValidationError as exc:
        log.error("veris %s result did not match the SDK models: %s", kind, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The OCR returned a result this app could not read."
        ) from exc
    except VerisOCRError as exc:
        log.warning("veris %s scan of %r failed: %s", kind, file.filename, exc)
        raise _http_error(exc) from exc

    # Read after the scan, so no database connection is held while Veris works.
    if kind == "resume":
        titles = list(db.scalars(select(Designation.title)))
        fields, warnings = resume_fields(result, designations=titles)
    else:
        fields, warnings = passport_fields(result)

    return {
        "fields": fields.model_dump(by_alias=True, exclude_none=True, mode="json"),
        "warnings": warnings,
        "raw": trim_raw(result),
    }
