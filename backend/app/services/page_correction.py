"""A person's correction of where one page of a scan belongs ("this page is a medical report, not Other").

Small and safe on purpose:
  * only the person who uploaded the file, an MD or an admin (and, for a file already on a candidate, anyone who may work that
    candidate); the page must exist in the job's own stored file; the same correction twice changes nothing (idempotent);
  * nothing is read in the request: a saved candidate's page is cut out and filed in the chosen field (a resume / passport / Aadhaar page
    is queued for the reader); a file not yet attached has the page's kind changed in its saved reading, so it is filed that way when the
    upload is saved (and a resume / passport / Aadhaar page is read again by the reading worker);
  * every correction is written to the audit trail with the SHA-256 of the page's normalised text and the chosen field, so the
    corrections can be used later as training data. No page text is stored in the trail.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob, Role
from app.services import bulk_candidate_import as bulk
from app.services import page_sort
from app.services.document_storage import read_bytes
from app.core.roles import is_admin_level

IDENTITY = ("resume", "passport", "aadhaar")


def allowed_kinds() -> tuple[str, ...]:
    from app.services.application_form import SLOTS
    from app.services.document_splitting import NEVER_FILED
    return tuple(slot for slot in SLOTS if slot not in NEVER_FILED)


def _job(db, job_id: str, user) -> DocumentExtractionJob:
    job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.result),
                                                          undefer(DocumentExtractionJob.draft))
                    .where(DocumentExtractionJob.id == job_id).with_for_update())
    if job is None or job.kind == "resort" or (job.owner_id != user.id and not is_admin_level(user)):
        raise HTTPException(404, "Document intake not found")
    return job


def _summary_page(state: dict, page: int) -> dict:
    summary = page_sort.summary_of(state) or {}
    return next((entry for entry in summary.get("pages", []) if entry.get("page") == page), {})


def _update_summary(state: dict, result: dict, page: int, kind: str) -> None:
    for holder in (page_sort.summary_of(state), result.get("sorting")):
        if not isinstance(holder, dict):
            continue
        for entry in holder.get("pages", []):
            if entry.get("page") == page:
                entry.update(kind=kind, corrected=True)
                entry.pop("reason", None), entry.pop("code", None)
        holder["unplaced"] = [item for item in holder.get("unplaced", []) if item.get("page") != page]
        holder["pagesSorted"] = sum(1 for entry in holder.get("pages", []) if entry.get("kind"))


def _identity_page(state: dict, page: int, kind: str) -> None:
    """The saved inspection says the page is this identity document, so the reading that is queued sends it as such (no OCR again)."""
    report = (state.get("output") or {}).get("inspection") or state.get("inspection")
    if not isinstance(report, dict) or not isinstance(report.get("pages"), list):
        return
    for entry in report["pages"]:
        if entry.get("page_number") == page:
            entry.update(status="read", document_type=kind, also=[], inherited=False, reason="Chosen by a person.")
    for name in ("resume", "passport", "aadhaar"):
        report[name + "_pages"] = [e["page_number"] for e in report["pages"] if e.get("status") == "read" and (e.get("document_type") == name or name in e.get("also", []))]
    report["unread_pages"] = [e["page_number"] for e in report["pages"] if e.get("status") == "unread"]
    report["unknown_pages"] = [e["page_number"] for e in report["pages"] if e.get("status") == "read" and e.get("document_type") == "unknown"]
    report["complete"] = not report["unread_pages"] and not report["unknown_pages"]
    if isinstance(state.get("inspection"), dict) and state["inspection"] is not report:
        state["inspection"] = deepcopy(report)


def correct_page(db, user, job_id: str, page: int, kind: str, settings) -> dict:
    from app.core.deps import ensure_can_work_candidate
    from app.services import application_form, document_splitting
    from app.services.manual_extraction import job_out, now
    if kind not in allowed_kinds():
        raise HTTPException(422, "Choose one of the document fields.")
    job = _job(db, job_id, user)
    candidate = db.scalar(select(Candidate).where(Candidate.id == job.candidate_id).with_for_update()) if job.candidate_id else None
    if candidate is not None:
        ensure_can_work_candidate(candidate, user, db)
    if job.content_type != "application/pdf":
        raise HTTPException(422, "Only the pages of a PDF can be placed one by one.")
    data = read_bytes(settings, job.object_key)
    if hashlib.sha256(data).hexdigest() != job.content_hash:
        raise HTTPException(409, "The saved file looks damaged. Upload it again.")
    count = document_splitting._page_count(data)
    if not 1 <= page <= count:
        raise HTTPException(404, "This file has no such page.")
    state = deepcopy(job.state or {})
    result = deepcopy(job.result or {})
    corrections = state.setdefault("page_corrections", {})
    if (corrections.get(str(page)) or {}).get("kind") == kind:          # idempotent: nothing is filed or queued twice
        return {**job_out(job, detail=True), "correction": {"page": page, "kind": kind, "alreadyDone": True}}
    text_hash = _summary_page(state, page).get("textHash") or ""
    filed = queued = False
    if candidate is not None:
        piece = bulk.cut_pdf_pages(data, [page])
        digest = hashlib.sha256(piece).hexdigest()
        versions = db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                                                       Attachment.slot == kind).order_by(Attachment.version.desc())).all()
        if not any(row.content_hash == digest for row in versions):
            written: list[str] = []
            name = document_splitting.derived_file_name(candidate, kind, set(), job.filename)
            try:
                key, _ = bulk._store_bytes(settings, piece, name, "application/pdf", written)
                row = Attachment(id=str(uuid4()), entity_type="candidate", entity_id=candidate.id, slot=kind,
                                 version=(versions[0].version if versions else 0) + 1, file_name=name, size_kb=max(1, round(len(piece) / 1024)),
                                 content_type="application/pdf", object_key=key, content_hash=digest, uploaded_by=user.id, uploaded_at=now())
                db.add(row)
                db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=name, entity_type="candidate", entity_id=candidate.id,
                                  meta={"attachment_id": row.id, "slot": kind, "version": row.version, "original_name": job.filename,
                                        "split_pages": [page], "document_job_id": job.id, "corrected_page": page}))
                db.flush()
                filed = True
                if kind in application_form.READ_SLOTS:
                    application_form.queue_reading(db, user, candidate, row, "application/pdf")
                    queued = True
            except Exception:
                bulk.discard_written(settings, written)
                raise
    else:
        saved = state.get("page_kinds")
        if isinstance(saved, dict) and isinstance(saved.get("pages"), dict):
            if kind in IDENTITY:
                saved["pages"].pop(str(page), None)
            else:
                saved["pages"][str(page)] = {"kind": kind, "confidence": 1.0, "reason": "Chosen by a person.", "inherited": False,
                                             "overrides_identity": True, "alongside_identity": False}
        if kind in IDENTITY:
            _identity_page(state, page, kind)
            output = state.get("output")
            if isinstance(output, dict):
                state.pop("output", None)
                job.status, job.attempts, job.next_attempt_at = "pending", 0, now()
                job.queued_at, job.started_at, job.finished_at, job.claimed_by, job.lease_until = job.next_attempt_at, None, None, None, None
                job.last_error = None
                queued = True
                from app.services.extraction_queue import notify_work
                notify_work(db, "manual")
        elif isinstance(saved, dict) and isinstance(state.get("output"), dict):
            kinds = document_splitting.kinds_found(data, report=state["output"].get("inspection"), saved_kinds=saved)
            if kinds is not None:
                result["kinds"] = kinds
    corrections[str(page)] = {"kind": kind, "textHash": text_hash, "by": user.id, "at": now().isoformat()}
    _update_summary(state, result, page, kind)
    job.state, job.result, job.updated_at = state, result, now()
    db.add(AuditEntry(actor_id=user.id, action="Corrected the field of a page", target=job.filename,
                      entity_type="candidate" if candidate is not None else "document_extraction_job",
                      entity_id=candidate.id if candidate is not None else job.id,
                      meta={"document_job_id": job.id, "page": page, "kind": kind, "page_text_sha256": text_hash,
                            "file_sha256": job.content_hash, "filed": filed, "reading_queued": queued}))
    db.commit()
    return {**job_out(job, detail=True), "correction": {"page": page, "kind": kind, "filed": filed, "readingQueued": queued}}
