"""Resumable document extraction with caller-owned durable checkpoints.

Only one status poll is performed per invocation. A worker can release its slot
on ExtractionPending and revisit the same provider job later.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json

from pydantic import ValidationError
from veriis import FileDescriptor, VerisOCR, VerisOCRError
from veriis.models import AadhaarResponse, PassportResponse, ResumeResponse

from app.integrations.ocr.inspection import InspectionError, inspect_document, restore_inspection
from app.integrations.ocr.mapping import aadhaar_fields, passport_fields, resume_fields, trim_raw


PIPELINE_VERSION = "local-pages-v1"
_MODELS = {"resume": ResumeResponse, "passport": PassportResponse, "aadhaar": AadhaarResponse}


class ExtractionPending(Exception):
    def __init__(self, job_id: str, kind: str):
        self.job_id = job_id
        self.kind = kind
        super().__init__(f"The {kind} extraction is still processing.")


class ExtractionFailure(Exception):
    """An actionable failure without provider URLs, credentials or document text."""


def _persist(state: dict, save: Callable[[dict], None]) -> None:
    try:
        save(deepcopy(state))
    except Exception:
        raise ExtractionFailure("OCR progress could not be saved. Retry after storage is available.") from None


def _request_key(source_hash: str, kind: str, selected: list[int], generation: int = 0) -> str:
    value = f"{PIPELINE_VERSION}:{kind}:{source_hash}:{','.join(map(str, selected))}"
    if generation:
        value += f":retry:{generation}"
    return "crm-" + hashlib.sha256(value.encode()).hexdigest()


def request_extraction_retry(state: dict, *, automatic: bool = False) -> list[str]:
    """Retain terminal failures before another budgeted or explicit attempt.

    The worker may call with ``automatic=True`` only when its durable retry
    budget has attempts remaining. A paid but incomplete/invalid result needs
    an explicit operator retry; pending or unknown jobs always retain their ID.
    """
    restarted = []
    for kind, record in list(state.get("provider_jobs", {}).items()):
        failed = record.get("status") == "failed"
        invalid = record.get("status") == "succeeded" and (
            record.get("coverage_status") == "needs_review" or record.get("validation_status") == "failed"
        )
        if not (failed or (invalid and not automatic)):
            continue
        state.setdefault("provider_job_history", {}).setdefault(kind, []).append(deepcopy(record))
        generations = state.setdefault("provider_generations", {})
        generations[kind] = generations.get(kind, 0) + 1
        del state["provider_jobs"][kind]
        restarted.append(kind)
    return restarted


def _provider_result(
    kind: str, selected: list[int], inspected, *, state: dict,
    save: Callable[[dict], None], settings, source_hash: str,
) -> dict:
    generation = state.get("provider_generations", {}).get(kind, 0)
    key = _request_key(source_hash, kind, selected, generation)
    jobs = state.setdefault("provider_jobs", {})
    record = jobs.get(kind)
    if record is not None:
        if record.get("idempotency_key") != key or record.get("selected_pages") != selected:
            raise ExtractionFailure("Document page selection changed. Review the saved extraction before retrying.")
        if record.get("status") == "failed":
            raise ExtractionFailure(
                f"The accepted {kind} job failed. Retry when another extraction attempt is available."
            )
        if "result" in record:
            return deepcopy(record["result"])
        if record.get("status") == "succeeded":
            raise ExtractionFailure("A completed OCR result is missing from storage. Review it before retrying.")
    if not (settings.veris_ocr_base_url and settings.veris_ocr_api_key):
        raise ExtractionFailure("OCR extraction is not configured. Configure the provider before retrying.")
    if record is None:
        record = {
            "idempotency_key": key, "selected_pages": list(selected), "status": "submitting",
            "retry_generation": generation,
        }
        jobs[kind] = record
        # Persist the key before any paid work. Lost acceptance responses can
        # safely retry the same payload/key rather than create another job.
        _persist(state, save)

    try:
        with VerisOCR(
            base_url=settings.veris_ocr_base_url, api_key=settings.veris_ocr_api_key,
            timeout=30.0, max_retries=0,
        ) as provider:
            if not record.get("job_id"):
                document = FileDescriptor(
                    inspected.pdf_for_pages(selected), filename=f"{kind}.pdf", content_type="application/pdf",
                )
                accepted = provider.jobs.submit(document, mode=kind, idempotency_key=key)
                record.update(job_id=accepted.job_id, status=accepted.status)
                # The remote job is now recoverable even if the worker exits
                # before its first status request.
                _persist(state, save)
            job = provider.jobs.get(record["job_id"])
    except (VerisOCRError, ValidationError):
        raise ExtractionFailure(
            "The OCR provider could not return a valid response. Retry to check the same saved job."
        ) from None

    record["status"] = job.status
    if job.status in {"queued", "running"}:
        _persist(state, save)
        raise ExtractionPending(record["job_id"], kind)
    if job.status == "failed":
        record["error"] = "The OCR provider reported that this job failed."
        _persist(state, save)
        raise ExtractionFailure(
            f"The accepted {kind} job failed. Retry when another extraction attempt is available."
        )
    if job.status != "succeeded" or not isinstance(job.result, dict):
        if job.status == "succeeded":
            record["validation_status"] = "failed"
        _persist(state, save)
        raise ExtractionFailure("The OCR job returned no usable result. Review the saved job before retrying.")
    record["result"] = deepcopy(job.result)
    # Save the paid response before validation, mapping, or any candidate write.
    _persist(state, save)
    return deepcopy(record["result"])


def _finish(state: dict, save: Callable[[dict], None], report: dict, extractions: dict,
            status: str, warnings: list[str]) -> dict:
    result = {
        "inspection": report, "extractions": extractions,
        "status": status, "warnings": list(dict.fromkeys(warnings)),
    }
    state.update(
        extractions=deepcopy(extractions), extraction_status=status,
        warnings=list(result["warnings"]),
    )
    _persist(state, save)
    return result


def extract_attachment(
    data: bytes, *, state: dict, save: Callable[[dict], None], settings,
    designations: list[str] | None = None,
) -> dict:
    """Inspect every page and resume recorded résumé/passport/Aadhaar jobs."""
    source_hash = hashlib.sha256(data).hexdigest()
    if state.get("source_sha256") not in {None, source_hash}:
        raise ExtractionFailure("The attachment changed. Start a separate intake for the replacement document.")
    same_source = state.get("source_sha256") == source_hash
    state["source_sha256"] = source_hash
    options = {
        "tesseract_cmd": settings.tesseract_cmd, "languages": settings.ocr_languages,
        "max_pages": settings.ocr_max_pages, "page_timeout": settings.ocr_page_timeout_seconds,
        "document_timeout": settings.ocr_document_timeout_seconds,
    }
    signature = hashlib.sha256(json.dumps(
        {"version": PIPELINE_VERSION, **options}, sort_keys=True,
    ).encode()).hexdigest()
    inspected = None
    if same_source and state.get("inspection_signature") == signature and isinstance(state.get("inspection"), dict):
        try:
            inspected = restore_inspection(
                data, state["inspection"], document_timeout=settings.ocr_document_timeout_seconds,
            )
        except InspectionError:
            # An incomplete or incompatible checkpoint is never authority to
            # skip pages. Inspect the original again before selecting anything.
            pass
    try:
        inspected = inspected or inspect_document(data, **options)
    except InspectionError as exc:
        raise ExtractionFailure(str(exc)) from None
    report = inspected.to_dict()
    state["inspection"] = report
    state["inspection_signature"] = signature
    _persist(state, save)
    warnings = list(inspected.warnings)
    if not inspected.complete:
        warnings.append("Some pages are unread or unclassified. Review the document before extraction.")
        return _finish(state, save, report, {}, "needs_review", warnings)

    selected_by_kind = {
        "resume": inspected.resume_pages, "passport": inspected.passport_pages,
        "aadhaar": inspected.aadhaar_pages,
    }
    if not any(selected_by_kind.values()):
        warnings.append("No résumé, passport or Aadhaar pages were identified.")
        return _finish(state, save, report, {}, "ignored", warnings)
    extractions: dict = {}
    status = "complete"
    for kind, selected in selected_by_kind.items():
        if not selected:
            continue
        payload = _provider_result(
            kind, selected, inspected, state=state, save=save, settings=settings, source_hash=source_hash,
        )
        try:
            response = _MODELS[kind].model_validate(payload)
        except ValidationError:
            state["provider_jobs"][kind]["validation_status"] = "failed"
            _persist(state, save)
            raise ExtractionFailure(
                f"The saved {kind} result has an invalid structure. Review it before requesting another extraction."
            ) from None
        pages = response.pages
        if (
            len(pages) != len(selected)
            or {page.page_number for page in pages} != set(range(1, len(selected) + 1))
            or any(not page.text.strip() for page in pages)
        ):
            state["provider_jobs"][kind]["coverage_status"] = "needs_review"
            _persist(state, save)
            status = "needs_review"
            warnings.append(f"The {kind} result did not cover every selected page. No fields from it were applied.")
            continue
        state["provider_jobs"][kind]["coverage_status"] = "complete"
        if kind == "resume":
            fields, notices = resume_fields(response, designations=designations or [])
        elif kind == "passport":
            fields, notices = passport_fields(response)
        else:
            fields, notices = aadhaar_fields(response)
        warnings.extend(notices)
        raw = trim_raw(response)
        for page in raw["pages"]:
            number = page["page_number"]
            page["provider_page_number"] = number
            page["page_number"] = selected[number - 1]
        raw["pages"].sort(key=lambda page: page["page_number"])
        raw["local_inspection"] = {**report, "selected_pages": list(selected)}
        raw["source_sha256"] = source_hash
        extractions[kind] = {
            "fields": fields.model_dump(by_alias=False, exclude_none=True, mode="json"),
            "raw": raw, "selected_pages": list(selected),
        }
        state["extractions"] = deepcopy(extractions)
        _persist(state, save)
    return _finish(state, save, report, extractions, status, warnings)
