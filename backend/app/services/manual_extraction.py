"""Durable manual-document extraction, independent of candidate creation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import socket
import traceback
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import undefer

from app.db.models import Attachment, AuditEntry, Candidate, Designation, DocumentExtractionJob, Role
from app.integrations.ocr.inspection import restore_inspection
from app.integrations.ocr.mapping import OcrFields
from app.services.document_extraction import extract_attachment, ExtractionFailure, ExtractionPending, provider_codes, request_extraction_retry
from app.services.document_storage import put_bytes, read_bytes
from app.services.reading_notes import plain_notes
from app.services.worker_heartbeat import waiting_reason

log = logging.getLogger(__name__)


def _plain(value):
    """JSON for values the standard encoder refuses (numpy numbers from an image library, sets, dates)."""
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _where(exc: BaseException) -> str:
    """module:function:line of the innermost frame in this application (never any text from the document)."""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "app" + os.sep in f.filename or "app/" in f.filename]
    frame = (ours or frames or [None])[-1]
    if frame is None:
        return "unknown"
    name = frame.filename.replace(os.sep, "/").rsplit("/app/", 1)[-1].removesuffix(".py").replace("/", ".")
    return f"{name}:{frame.name}:{frame.lineno}"


def failure_record(exc: BaseException, *, attempt: int, worker: str | None) -> dict:
    """A short, safe account of why an attempt failed, kept in the job's state: no message, no document content."""
    from app.services.worker_heartbeat import code_version
    return {"type": type(exc).__name__, "where": _where(exc), "at": now().isoformat(), "attempt": attempt,
            "worker": worker, "version": code_version()}
READY = {"pending", "processing", "retry"}
# Lease renewed by each checkpoint; the worker pool also renews it by heartbeat (settings.extraction_lease_seconds).
LEASE_SECONDS = 180
TERMINAL = {"completed", "needs_review", "failed", "attached", "dismissed"}
# A document is read this many times before it is marked failed.
MAX_ATTEMPTS = 4


def error_hint(message):
    """The first sentence of a stored error, short and safe for a screen (those messages never carry document content)."""
    first = " ".join((message or "").split()).split(". ")[0].rstrip(".")
    return (first[:120] + ".") if first else None


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


def job_out(job, *, detail=False, queue=None, reader=None):
    """``queue`` is ``extraction_queue.queue_positions(...)`` for the listed jobs: waiting jobs get their place in line.

    ``reader`` is ``worker_heartbeat.reader_status(db)`` (None when unknown): it is added as ``reader`` and decides
    ``waitingReason`` (``reader_not_running`` / ``queue`` / null) so the screen never waits silently on a dead reader.
    """
    place = (queue or {}).get(job.id) or {}
    result = {"id": job.id, "groupId": job.group_id, "kind": job.kind, "filename": job.filename,
              "sizeKb": job.size_kb, "status": job.status, "attempts": job.attempts,
              "lastError": job.last_error, "candidateId": job.candidate_id, "attachmentId": job.attachment_id,
              "createdAt": job.created_at, "updatedAt": job.updated_at,
              "queuedAt": job.queued_at or job.created_at, "startedAt": job.started_at, "finishedAt": job.finished_at,
              "queuePosition": place.get("queuePosition"), "aheadOfYou": place.get("aheadOfYou"),
              "originalUrl": "/api/ocr/jobs/" + job.id + "/original",
              "waitingReason": waiting_reason(job, reader),
              # For the upload screen: the attempt budget, a one-line reason, and when a waiting job is really due
              # (next_attempt_at holds a far-future value for stamped jobs, see migration 0032).
              "maxAttempts": MAX_ATTEMPTS, "lastErrorHint": error_hint(job.last_error),
              "readyAt": job.ready_at or (job.next_attempt_at if job.next_attempt_at and job.next_attempt_at.year < 2100 else None)}
    if reader is not None:
        result["reader"] = dict(reader)
    if detail:
        result.update(result=job.result or {}, draft=job.draft or {})
    return result


def _with_form_fields(kind, extraction):
    """Fields saved before the application form existed lack its values; read them from the stored raw result."""
    fields = dict(extraction.get("fields", {}))
    try:
        from app.services.form_extraction import values_for
        from app.integrations.ocr.mapping import _FORM_ONLY
        for name, value in values_for(kind, extraction.get("raw")).items():
            if name in _FORM_ONLY and name not in fields:
                fields[name] = value
    except Exception:  # the preview must never fail because of an optional extra
        pass
    return fields


def _scan_results(output):
    """What the person sees of a reading. The raw warnings stay in the job's state (diag_extraction reads them);
    here they are plain, and a warning about one kind appears on that kind's card only."""
    report = output.get("inspection", {})
    raw_warnings = output.get("warnings", [])
    shown_report = {**report, "warnings": plain_notes(report.get("warnings") or [])} if "warnings" in report else report
    scans = {}
    for kind in ("resume", "passport", "aadhaar"):
        extraction = output.get("extractions", {}).get(kind)
        selected = report.get(kind + "_pages", [])
        if not selected and kind != "resume":
            continue
        scans[kind] = {
            "fields": OcrFields.model_validate(_with_form_fields(kind, extraction)).model_dump(by_alias=True, exclude_none=True, mode="json") if extraction else {},
            "raw": extraction.get("raw", {}) if extraction else {},
            "source": extraction.get("source", {}) if extraction else {},
            "inspection": {**shown_report, "selected_pages": selected},
            "warnings": plain_notes(raw_warnings, kind=kind),
            "extraction_status": "complete" if extraction else "no_resume" if kind == "resume" and not selected and output["status"] != "needs_review" else "needs_review",
        }
    return {"scans": scans, "warnings": plain_notes(raw_warnings), "inspection": shown_report, "coverage": output.get("coverage")}


def _autocommit(db):
    """Run this session's statements outside a transaction: one round trip each, nothing to commit or roll back.
    Only for reads and for noise-only writes (checkpoints, leases), which announce nothing to the live feed."""
    db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})


def _lost(job, claimed_by):
    """True when a job no longer belongs to this claim (finished, released, or leased to another worker)."""
    return job is None or job.status not in READY or (claimed_by is not None and job.claimed_by != claimed_by)


def _hand_back(identifier, claimed_by, sessions, reason, delay=300):
    """Give a job back without counting an attempt (its file is not in this server's storage): another server takes it, or
    this one looks again later. It is never marked failed for this."""
    stamp = now()
    with sessions() as db:
        db.execute(text(
            "UPDATE document_extraction_jobs SET status = 'retry', next_attempt_at = :due, last_error = :reason, updated_at = :stamp, "
            "claimed_by = NULL, lease_until = NULL WHERE id = :id AND status = 'processing' "
            "AND (CAST(:fence AS TEXT) IS NULL OR claimed_by = :fence)"),
            {"due": stamp + timedelta(seconds=delay), "reason": reason, "stamp": stamp, "id": identifier, "fence": claimed_by})
        db.commit()
    log.warning("Document %s is not in this server's storage; handed back without an attempt.", identifier)


def process_job(identifier, *, sessions, settings, claimed_by=None):
    """Read one document. ``claimed_by`` is the worker that leased it (see services/extraction_queue.py); a lease that
    has moved to another worker makes this run stop writing. Without it (direct calls) the job is taken here."""
    titles = None
    with sessions() as db:
        if claimed_by is None:
            job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state))
                            .where(DocumentExtractionJob.id == identifier).with_for_update())
            if job is None or job.status not in READY:
                return
            job.status = "processing"
            job.updated_at = job.started_at = now()
            state = deepcopy(job.state or {})
            key, digest = job.object_key, job.content_hash
            owner, group_id, form_candidate = job.owner_id, job.group_id, job.candidate_id
            attempts = job.attempts
            if not state.get("output"):
                titles = list(db.scalars(select(Designation.title)))
            db.commit()
        else:
            # The claim already moved the job to processing: one read gets everything this run needs.
            _autocommit(db)
            row = db.execute(select(DocumentExtractionJob.status, DocumentExtractionJob.claimed_by, DocumentExtractionJob.state,
                DocumentExtractionJob.object_key, DocumentExtractionJob.content_hash, DocumentExtractionJob.owner_id,
                DocumentExtractionJob.group_id, DocumentExtractionJob.candidate_id, DocumentExtractionJob.attempts,
                select(func.array_agg(Designation.title)).scalar_subquery().label("titles"))
                .where(DocumentExtractionJob.id == identifier)).one_or_none()
            if row is None or row.status not in READY or row.claimed_by != claimed_by:
                return
            state = deepcopy(row.state or {})
            key, digest, owner, group_id, form_candidate = row.object_key, row.content_hash, row.owner_id, row.group_id, row.candidate_id
            attempts = row.attempts
            titles = list(row.titles or [])

    # Which reader ran this attempt (a job that has no "reader" in its state was handled by older code).
    from app.services.worker_heartbeat import code_version
    from app.services.instance_guard import instance_name
    state["reader"] = {"version": code_version(), "worker": claimed_by, "host": socket.gethostname()[:40], "pid": os.getpid(),
                       "instance": instance_name(settings)}

    # What the row already holds, key by key: a checkpoint sends only the keys that changed (and nothing when none did),
    # so a 300-page inspection report is not uploaded again with every small step.
    saved_keys = {name: json.dumps(item, sort_keys=True, default=_plain) for name, item in state.items()}
    saved_keys.pop("reader", None)  # always written by the first checkpoint of this attempt

    def checkpoint(value):
        # One statement: merge the changed keys into the saved state and renew the lease (a checkpoint proves the worker is alive).
        changed = {name: json.dumps(item, sort_keys=True, default=_plain) for name, item in value.items()}
        changed = {name: text_ for name, text_ in changed.items() if saved_keys.get(name) != text_}
        if not changed:
            return
        stamp = now()
        with sessions() as db:
            _autocommit(db)
            row = db.execute(text(
                "UPDATE document_extraction_jobs SET state = state || CAST(:value AS JSONB), updated_at = :stamp, "
                "lease_until = CASE WHEN claimed_by IS NOT NULL THEN :lease ELSE lease_until END "
                "WHERE id = :id AND (CAST(:fence AS TEXT) IS NULL OR claimed_by = :fence) RETURNING status"),
                {"value": "{" + ",".join(json.dumps(name) + ":" + body for name, body in changed.items()) + "}", "stamp": stamp, "lease": stamp + timedelta(seconds=getattr(settings, "extraction_lease_seconds", LEASE_SECONDS)), "id": identifier, "fence": claimed_by}).first()
        # A reviewer can reserve the source while a provider acceptance is in flight. The returned ID/result
        # is kept, but the job is never reopened. A job leased to someone else is not ours to write.
        if row is None or row.status not in READY:
            raise JobFinished()
        saved_keys.update(changed)

    from app.services.instance_guard import NOT_HERE, stored_here
    if not stored_here(settings, key):
        _hand_back(identifier, claimed_by, sessions, NOT_HERE)
        return
    try:
        try:
            data = read_bytes(settings, key)
        except FileNotFoundError:
            _hand_back(identifier, claimed_by, sessions, NOT_HERE)
            return
        if hashlib.sha256(data).hexdigest() != digest:
            raise ExtractionFailure("The saved copy of this document looks damaged. Upload the original again, then try again.")
        output = state.get("output")
        if output is None:
            output = extract_attachment(data, state=state, save=checkpoint, settings=settings, designations=titles or [],
                                        final_attempt=attempts + 1 >= MAX_ATTEMPTS)
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
            inspected = restore_inspection(data, output["inspection"], document_timeout=settings.ocr_document_timeout_seconds, allow_partial=True)
            for kind, extraction in output["extractions"].items():
                if kind in documents:
                    continue
                selected = extraction["selected_pages"]
                # The pages sent are the checked pages or fewer: a passport plan sends only the pages with passport details of their own.
                if not selected or not set(selected) <= set(getattr(inspected, kind + "_pages")):
                    raise ExtractionFailure("The selected pages no longer match what was checked earlier. Check the pages and try again.")
                content = inspected.pdf_for_pages(selected)
                document_key = put_bytes(settings, "manual/" + owner + "/" + identifier + "/" + kind + ".pdf", content, "application/pdf")
                documents[kind] = {"object_key": document_key, "file_name": kind + ".pdf", "content_type": "application/pdf",
                                   "size_kb": max(1, (len(content) + 1023) // 1024), "content_hash": hashlib.sha256(content).hexdigest()}
                checkpoint(state)
        with sessions() as db:
            # A file read for a candidate's application form fills that candidate's form in this same transaction.
            # Candidate before job, the order every other writer uses.
            if form_candidate:
                db.scalar(select(Candidate.id).where(Candidate.id == form_candidate).with_for_update())
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if _lost(job, claimed_by):
                return
            job.state = json.loads(json.dumps(state, default=_plain))
            job.result = json.loads(json.dumps(_scan_results(output), default=_plain))
            job.status = "completed" if output["status"] == "complete" else "needs_review"
            # A kind the reader could not read is named for the person (the upload row shows this line).
            unread = (output.get("coverage") or {}).get("failed") or []
            job.last_error = None if job.status == "completed" else (
                " ".join(unread)[:500] if unread else "Review the saved reading report or enter details manually.")
            job.updated_at = job.finished_at = now()
            job.claimed_by = job.lease_until = None
            if form_candidate:
                try:
                    from app.services.application_form import apply_job_output
                    with db.begin_nested():
                        apply_job_output(db, job, output)
                except Exception:
                    log.warning("Filling the application form from job %s failed; the reading itself is saved.", identifier, exc_info=True)
            db.commit()
    except JobFinished:
        return
    except ExtractionPending:
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if not _lost(job, claimed_by):
                job.status, job.next_attempt_at = "retry", now() + timedelta(seconds=2)
                job.last_error = "The document is still being read. Its progress is saved."
                job.updated_at = now()
                job.claimed_by = job.lease_until = None
                db.commit()
    except Exception as exc:
        # The traceback stays in the server log (never in the job row or the response) so a file that keeps failing can be diagnosed.
        log.warning("Manual extraction %s failed (%s at %s; reader codes: %s); original and checkpoints retained.",
                    identifier, type(exc).__name__, _where(exc), provider_codes(state), exc_info=True)
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state))
                            .where(DocumentExtractionJob.id == identifier).with_for_update())
            if not _lost(job, claimed_by):
                job.attempts += 1
                saved = deepcopy(job.state or {})
                if job.attempts < 4:
                    request_extraction_retry(saved, automatic=True)
                # Why, for whoever has to look: the exception type and where it was raised, never the document.
                saved["failure"] = failure_record(exc, attempt=job.attempts, worker=claimed_by)
                saved["reader"] = state.get("reader")
                job.state = saved
                job.status = "failed" if job.attempts >= 4 else "retry"
                job.last_error = str(exc)[:500] if isinstance(exc, ExtractionFailure) else (
                    f"Reading failed ({type(exc).__name__}). The technical details are in the server log. "
                    "The original file is safe. Try again, and contact your administrator if it keeps happening.")
                job.next_attempt_at = now() + timedelta(seconds=30 * 2 ** (job.attempts - 1))
                job.updated_at = now()
                job.claimed_by = job.lease_until = None
                if job.status == "failed":
                    job.finished_at = job.updated_at
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
    job = owned_job(db, identifier, user.id, lock=True)
    # Adding files is work on the candidate: the assigned recruiter, a manager or an admin. Having entered the
    # candidate, or an earlier merge, no longer counts once an admin has given it to someone else.
    from app.core.deps import ensure_can_work_candidate
    ensure_can_work_candidate(candidate, user, db)
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
        db.add(AuditEntry(actor_id=user.id, action="Added a saved document to a candidate", target=row.file_name,
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
