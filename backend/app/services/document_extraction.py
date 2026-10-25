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
from app.integrations.ocr.mapping import aadhaar_fields, passport_fields, resume_fields
from app.integrations.ocr.person_split import page_identity, incompatible


PIPELINE_VERSION = "local-pages-v1"
_MODELS = {"resume": ResumeResponse, "passport": PassportResponse, "aadhaar": AadhaarResponse}


class ExtractionPending(Exception):
    def __init__(self, job_id: str, kind: str):
        self.job_id = job_id
        self.kind = kind
        super().__init__(f"The {kind} extraction is still processing.")


class ExtractionFailure(Exception):
    """An actionable failure without provider URLs, credentials or document text."""


class UnreadableDocument(ExtractionFailure):
    """This file itself cannot be read (damaged, protected, unsupported): retrying the
    same bytes cannot help, unlike a provider outage or a missing server tool."""


def _unreadable(exc: InspectionError) -> ExtractionFailure:
    return ExtractionFailure(str(exc)) if exc.retryable else UnreadableDocument(str(exc))


def checked_person_output(data, output, state, settings):
    """Recheck legacy/cached results locally without discarding paid checkpoints."""
    if state.get("person_detection_version") != 1:
        try:
            inspected = inspect_document(data, tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng"),
                max_pages=getattr(settings, "ocr_max_pages", 300), page_timeout=getattr(settings, "ocr_page_timeout_seconds", 20),
                document_timeout=getattr(settings, "ocr_document_timeout_seconds", 600))
        except InspectionError as exc:
            raise _unreadable(exc) from None
        evidence = [page_identity(page.text) for page in inspected.pages if page.status == "read"]
        state["person_detection_version"] = 1
        state["person_identities"] = evidence
        state["splitSuggested"] = any(item["mixed"] for item in evidence) or any(
            incompatible(left, right) for index, left in enumerate(evidence) for right in evidence[index + 1:])
    if state.get("splitSuggested"):
        return {**output, "status": "needs_review", "splitSuggested": True, "extractions": {},
            "warnings": list(dict.fromkeys([*output.get("warnings", []), "Different candidate identities were found. Open Review people and pages before saving."]))}
    return output


def _persist(state: dict, save: Callable[[dict], None]) -> None:
    try:
        save(deepcopy(state))
    except Exception:
        raise ExtractionFailure("We could not save the progress of reading this document. Try again in a few minutes.") from None


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
            raise ExtractionFailure("The pages chosen for this document have changed. Check the saved results before trying again.")
        if record.get("status") == "failed":
            raise ExtractionFailure(
                f"We could not read the {kind} pages. Try again later, or enter the details by hand."
            )
        if "result" in record:
            return deepcopy(record["result"])
        if record.get("status") == "succeeded":
            raise ExtractionFailure("The saved result for this document has gone missing. Check the document, then try again.")
    if not (settings.veris_ocr_base_url and settings.veris_ocr_api_key):
        raise ExtractionFailure("Document reading is not set up on this server yet. Ask your system administrator to finish the setup.")
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
            "The document reader sent back an answer we could not use. Try again."
        ) from None

    record["status"] = job.status
    if job.status in {"queued", "running"}:
        _persist(state, save)
        raise ExtractionPending(record["job_id"], kind)
    if job.status == "failed":
        record["error"] = "The document reader could not read this file."
        _persist(state, save)
        raise ExtractionFailure(
            f"We could not read the {kind} pages. Try again later, or enter the details by hand."
        )
    if job.status != "succeeded" or not isinstance(job.result, dict):
        if job.status == "succeeded":
            record["validation_status"] = "failed"
        _persist(state, save)
        raise ExtractionFailure("The document reader did not find anything we could use. Check the document, then try again.")
    record["result"] = deepcopy(job.result)
    # Save the paid response before validation, mapping, or any candidate write.
    _persist(state, save)
    return deepcopy(record["result"])


def _finish(state: dict, save: Callable[[dict], None], report: dict, extractions: dict,
            status: str, warnings: list[str], reason: str | None = None) -> dict:
    result = {
        "inspection": report, "extractions": extractions,
        "status": status, "warnings": list(dict.fromkeys(warnings)),
        "splitSuggested": bool(state.get("splitSuggested")),
    }
    if reason:
        result["reason"] = reason
    state.update(
        extractions=deepcopy(extractions), extraction_status=status,
        warnings=list(result["warnings"]),
    )
    _persist(state, save)
    return result


def _run_kinds(selected_by_kind, inspected, report, source_hash, *, state, save, settings, designations):
    """Send each kind's pages to Veris (resuming checkpointed jobs), validate coverage and map
    the fields. Returns (extractions, warnings, every_kind_covered)."""
    extractions: dict = {}
    warnings: list[str] = []
    covered = True
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
                f"The saved {kind} result could not be used. Check it before trying to read the document again."
            ) from None
        pages = response.pages
        if (
            len(pages) != len(selected)
            or {page.page_number for page in pages} != set(range(1, len(selected) + 1))
            or any(not page.text.strip() for page in pages)
        ):
            state["provider_jobs"][kind]["coverage_status"] = "needs_review"
            _persist(state, save)
            covered = False
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
        # The stored document is exactly what Veris returned: the checkpointed job
        # result, never the validated model. Provenance travels beside it, and
        # selected_pages[page_number - 1] maps a provider page to the original file.
        extractions[kind] = {
            "fields": fields.model_dump(by_alias=False, exclude_none=True, mode="json"),
            "raw": deepcopy(payload),
            "source": {
                "local_inspection": {**report, "selected_pages": list(selected)},
                "source_sha256": source_hash, "selected_pages": list(selected),
            },
            "selected_pages": list(selected),
        }
        state["extractions"] = deepcopy(extractions)
        _persist(state, save)
    return extractions, warnings, covered


def extract_selected_pages(
    page_pdfs: dict[str, bytes], *, state: dict, save: Callable[[dict], None], settings,
    designations: list[str] | None = None,
) -> dict:
    """Extract pages a person already chose (kind -> PDF of exactly those pages), with the
    same provider jobs, idempotency keys, checkpoints and field mapping as a whole file.
    """

    class _Chosen:
        def __init__(self, content: bytes):
            self.content = content

        def pdf_for_pages(self, selected):
            return self.content

    extractions: dict = {}
    warnings: list[str] = []
    status = "complete"
    for kind, content in page_pdfs.items():
        source_hash = hashlib.sha256(content).hexdigest()
        import pymupdf as fitz
        with fitz.open(stream=content, filetype="pdf") as document:
            count = document.page_count
        report = {"source_format": "pdf", "page_count": count, "chosen_by_reviewer": True}
        done, notices, covered = _run_kinds(
            {kind: list(range(1, count + 1))}, _Chosen(content), report, source_hash, state=state, save=save,
            settings=settings, designations=designations,
        )
        extractions.update(done)
        warnings.extend(notices)
        if not covered:
            status = "needs_review"
    return {"extractions": extractions, "status": status, "warnings": list(dict.fromkeys(warnings))}


def extract_attachment(
    data: bytes, *, state: dict, save: Callable[[dict], None], settings,
    designations: list[str] | None = None, partial: bool = False, ignore_decorative: bool = False,
) -> dict:
    """Inspect every page and resume recorded résumé/passport/Aadhaar jobs.

    ``partial`` extracts the confidently classified pages of a file that also has
    unread or unclassified ones (reported as needs_review, never dropped). Without it
    any such page holds the whole file back. ``ignore_decorative`` sets aside a single
    picture holding no document text (a logo or photo) instead of asking for review.
    """
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
    if same_source and state.get("person_detection_version") == 1 and state.get("inspection_signature") == signature and isinstance(state.get("inspection"), dict):
        try:
            # Unread pages may be a time limit or a hiccup, so only a report whose gaps
            # are classification gaps is trusted in place of reading the file again.
            inspected = restore_inspection(
                data, state["inspection"], document_timeout=settings.ocr_document_timeout_seconds,
                allow_partial=partial and not state["inspection"].get("unread_pages"),
            )
        except InspectionError:
            # An incomplete or incompatible checkpoint is never authority to
            # skip pages. Inspect the original again before selecting anything.
            pass
    try:
        if inspected is None:
            inspected = inspect_document(data, **options)
            evidence = [page_identity(page.text) for page in inspected.pages if page.status == "read"]
            state["person_detection_version"] = 1
            state["person_identities"] = evidence
            state["splitSuggested"] = any(item["mixed"] for item in evidence) or any(
                incompatible(left, right) for index, left in enumerate(evidence) for right in evidence[index + 1:])
    except InspectionError as exc:
        raise _unreadable(exc) from None
    report = inspected.to_dict()
    state["inspection"] = report
    state["inspection_signature"] = signature
    _persist(state, save)
    warnings = list(inspected.warnings)
    if ignore_decorative and inspected.decorative_image and not state.get("splitSuggested"):
        note = "The image holds no readable document text (a logo, signature or photo) and was set aside."
        return _finish(state, save, report, {}, "ignored", [], reason=note)
    inspected.allow_partial = partial
    if state.get("splitSuggested"):
        warnings.append("This document contains different candidate identities. Open Review people and pages before saving any candidate.")
        return _finish(state, save, report, {}, "needs_review", warnings)
    if not inspected.complete and not (partial and inspected.has_classified_pages):
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
    if not inspected.complete:
        # Only the pages identified with confidence are sent; the rest stay in the stored original.
        gaps = sorted({*inspected.unread_pages, *inspected.unknown_pages})
        warnings.append(
            f"Pages {', '.join(map(str, gaps[:20]))} could not be read or classified and were not extracted. "
            "Review them in the stored original."
        )
        status = "needs_review"
    done, notices, covered = _run_kinds(
        selected_by_kind, inspected, report, source_hash, state=state, save=save,
        settings=settings, designations=designations,
    )
    extractions.update(done)
    warnings.extend(notices)
    if not covered:
        status = "needs_review"
    return _finish(state, save, report, extractions, status, warnings)
