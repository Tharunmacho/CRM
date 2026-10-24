"""Durable manual-document extraction, independent of candidate creation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Attachment, AuditEntry, Candidate, Designation, DocumentExtractionJob, Role
from app.integrations.ocr.inspection import restore_inspection
from app.integrations.ocr.mapping import OcrFields
from app.services.document_extraction import extract_attachment, ExtractionFailure, ExtractionPending, request_extraction_retry
from app.services.document_storage import put_bytes, read_bytes

log = logging.getLogger(__name__)
READY = {"pending", "processing", "retry"}
TERMINAL = {"completed", "needs_review", "failed", "attached", "dismissed"}


def now():
    return datetime.now(timezone.utc)


class JobFinished(Exception):
    """An operator attached/dismissed the original while processing was in flight."""


def owned_job(db, identifier, owner_id, *, lock=False):
    statement = select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier, DocumentExtractionJob.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    job = db.scalar(statement)
    if job is None:
        raise HTTPException(404, "Document intake not found")
    return job


def job_out(job, *, detail=False):
    result = {"id": job.id, "groupId": job.group_id, "kind": job.kind, "filename": job.filename,
              "sizeKb": job.size_kb, "status": job.status, "attempts": job.attempts,
              "lastError": job.last_error, "candidateId": job.candidate_id, "attachmentId": job.attachment_id,
              "createdAt": job.created_at, "updatedAt": job.updated_at,
              "originalUrl": "/api/ocr/jobs/" + job.id + "/original"}
    if detail:
        result.update(result=job.result or {}, draft=job.draft or {})
    return result


def _scan_results(output):
    report = output.get("inspection", {})
    scans = {}
    for kind in ("resume", "passport", "aadhaar"):
        extraction = output.get("extractions", {}).get(kind)
        selected = report.get(kind + "_pages", [])
        if not selected and kind != "resume":
            continue
        scans[kind] = {
            "fields": OcrFields.model_validate(extraction.get("fields", {})).model_dump(by_alias=True, exclude_none=True, mode="json") if extraction else {},
            "raw": extraction.get("raw", {}) if extraction else {},
            "source": extraction.get("source", {}) if extraction else {},
            "inspection": {**report, "selected_pages": selected},
            "warnings": output.get("warnings", []),
            "extraction_status": "complete" if extraction else "no_resume" if kind == "resume" and not selected and output["status"] != "needs_review" else "needs_review",
        }
    return {"scans": scans, "warnings": output.get("warnings", []), "inspection": report}


def process_job(identifier, *, sessions, settings):
    with sessions() as db:
        job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
        if job is None or job.status not in READY:
            return
        job.status = "processing"
        job.updated_at = now()
        state = deepcopy(job.state or {})
        key, digest = job.object_key, job.content_hash
        owner, group_id = job.owner_id, job.group_id
        titles = list(db.scalars(select(Designation.title)))
        db.commit()

    def checkpoint(value):
        with sessions() as db:
            current = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if current is None:
                raise JobFinished()
            paused = current.status not in READY
            # A reviewer can reserve the source while a provider acceptance is
            # in flight. Keep its returned ID/result, but never reopen the job.
            current.state = {**deepcopy(current.state or {}), **deepcopy(value)}
            current.updated_at = now()
            db.commit()
            if paused:
                raise JobFinished()

    try:
        data = read_bytes(settings, key)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ExtractionFailure("The saved original failed its integrity check. Restore the document before retrying.")
        output = state.get("output")
        if output is None:
            output = extract_attachment(data, state=state, save=checkpoint, settings=settings, designations=titles)
            state["output"] = deepcopy(output)
            checkpoint(state)
        from app.services.document_extraction import checked_person_output
        output = checked_person_output(data, output, state, settings)
        state["output"] = deepcopy(output)
        checkpoint(state)
        # Retain selected PDFs beside the original bundle; later attachment is a
        # short transaction and never needs to resubmit paid extraction work.
        documents = state.setdefault("documents", {})
        if output.get("extractions"):
            inspected = restore_inspection(data, output["inspection"], document_timeout=settings.ocr_document_timeout_seconds)
            for kind, extraction in output["extractions"].items():
                if kind in documents:
                    continue
                selected = extraction["selected_pages"]
                if selected != getattr(inspected, kind + "_pages"):
                    raise ExtractionFailure("The selected pages no longer match the saved inspection.")
                content = inspected.pdf_for_pages(selected)
                document_key = put_bytes(settings, "manual/" + owner + "/" + identifier + "/" + kind + ".pdf", content, "application/pdf")
                documents[kind] = {"object_key": document_key, "file_name": kind + ".pdf", "content_type": "application/pdf",
                                   "size_kb": max(1, (len(content) + 1023) // 1024), "content_hash": hashlib.sha256(content).hexdigest()}
                checkpoint(state)
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if job is None or job.status not in READY:
                return
            job.state = deepcopy(state)
            job.result = _scan_results(output)
            job.status = "completed" if output["status"] == "complete" else "needs_review"
            job.last_error = None if job.status == "completed" else "Review the saved reading report or enter details manually."
            job.updated_at = now()
            db.commit()
    except JobFinished:
        return
    except ExtractionPending:
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if job is not None and job.status in READY:
                job.status, job.next_attempt_at = "retry", now() + timedelta(seconds=5)
                job.last_error = "The document is still being read. Its progress is saved."
                job.updated_at = now()
                db.commit()
    except Exception as exc:
        log.warning("Manual extraction %s failed (%s); original and checkpoints retained.", identifier, type(exc).__name__)
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if job is not None and job.status in READY:
                job.attempts += 1
                saved = deepcopy(job.state or {})
                if job.attempts < 4:
                    request_extraction_retry(saved, automatic=True)
                job.state = saved
                job.status = "failed" if job.attempts >= 4 else "retry"
                job.last_error = str(exc)[:500] if isinstance(exc, ExtractionFailure) else "Document reading could not finish. The original is saved; retry after checking the worker."
                job.next_attempt_at = now() + timedelta(seconds=30 * 2 ** (job.attempts - 1))
                job.updated_at = now()
                db.commit()


def attach_original(db, user, identifier, candidate_id, settings):
    from app.api.routers.candidates import lock_assignment_cursor
    from app.services.document_person_split import ensure_single_person_source
    lock_assignment_cursor(db)
    source_job = owned_job(db, identifier, user.id)
    ensure_single_person_source(db, "manual", source_job.group_id)
    # Candidate locks are shared with normal file uploads, guaranteeing one next
    # version even when another recruiter uploads to this candidate concurrently.
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    if user.role == Role.staff and candidate.owner_id != user.id and candidate.created_by != user.id:
        raise HTTPException(403, "Only the assigned recruiter, candidate creator or a supervisor can attach these documents")
    job = owned_job(db, identifier, user.id, lock=True)
    if job.candidate_id:
        if job.candidate_id != candidate.id:
            raise HTTPException(409, "These documents are already attached to another candidate")
        return job_out(job, detail=True)
    if job.status == "dismissed":
        raise HTTPException(409, "This upload was dismissed. Restore it before you attach it.")
    original = read_bytes(settings, job.object_key)
    if hashlib.sha256(original).hexdigest() != job.content_hash:
        raise HTTPException(409, "The saved file looks damaged. Upload it again.")
    documents = {"source_bundle": {"object_key": job.object_key, "file_name": job.filename,
                  "content_type": job.content_type, "size_kb": job.size_kb, "content_hash": job.content_hash},
                 **deepcopy((job.state or {}).get("documents", {}))}
    if job.kind in {"photo", "education", "experience"}:
        documents[job.kind] = deepcopy(documents["source_bundle"])
    attached = {}
    for slot, document in documents.items():
        data = original if slot == "source_bundle" else read_bytes(settings, document["object_key"])
        if hashlib.sha256(data).hexdigest() != document["content_hash"]:
            raise HTTPException(409, "A selected file looks damaged. Upload it again.")
        previous = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate",
            Attachment.entity_id == candidate.id, Attachment.slot == slot).order_by(Attachment.version.desc()).limit(1))
        row = Attachment(id=str(uuid4()), entity_type="candidate", entity_id=candidate.id, slot=slot,
            version=(previous.version if previous else 0) + 1, file_name=document["file_name"],
            content_type=document["content_type"], object_key=document["object_key"], content_hash=document["content_hash"], size_kb=document["size_kb"],
            uploaded_by=user.id, uploaded_at=now())
        db.add(row)
        attached[slot] = row.id
        db.add(AuditEntry(actor_id=user.id, action="Attached saved intake document", target=row.file_name,
            entity_type="candidate", entity_id=candidate.id, meta={"attachment_id": row.id, "slot": slot,
            "version": row.version, "document_job_id": job.id}))
    db.flush()
    job.attachment_id = attached["source_bundle"]
    job.candidate_id = candidate.id
    job.status = "attached"
    job.updated_at = now()
    job.state = {**deepcopy(job.state or {}), "attached_documents": attached}
    db.commit()
    return job_out(job, detail=True)
