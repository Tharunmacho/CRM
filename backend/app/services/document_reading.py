"""Explicit reading of stored candidate documents, separate from uploading bytes."""
from fastapi import HTTPException
from sqlalchemy import or_, select
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob

READABLE_SLOTS = ("resume", "passport", "aadhaar")


def pending_expression():
    """Uncorrelated sets keep profile/bootstrap pending flags in their existing SQL round trip."""
    deferred = select(AuditEntry.meta["attachment_id"].astext).where(
        AuditEntry.action == "Uploaded document", AuditEntry.entity_type == "candidate",
        AuditEntry.meta["extraction_deferred"].as_boolean().is_(True),
    )
    started = select(DocumentExtractionJob.attachment_id).where(DocumentExtractionJob.attachment_id.is_not(None))
    # A failed reading remains visible and can be explicitly retried; uploading
    # the same file with deferral must never silently restart it.
    latest = select(DocumentExtractionJob.attachment_id, DocumentExtractionJob.status).where(
        DocumentExtractionJob.attachment_id.is_not(None)).distinct(DocumentExtractionJob.attachment_id).order_by(
        DocumentExtractionJob.attachment_id, DocumentExtractionJob.created_at.desc()).subquery()
    failed = select(latest.c.attachment_id).where(latest.c.status == "failed")
    return or_(Attachment.id.in_(deferred.except_(started)), Attachment.id.in_(failed)).label("extraction_pending")


def start(db, candidate_id, user, attachment_ids):
    from app.services.application_form import ensure_can_edit, queue_reading, requeue_failed
    from app.services.document_identity import rejected_expression
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    ensure_can_edit(user, candidate)
    identifiers = list(dict.fromkeys(attachment_ids))
    files = list(db.scalars(select(Attachment).where(Attachment.id.in_(identifiers),
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id,
        Attachment.slot.in_(READABLE_SLOTS)).order_by(Attachment.uploaded_at, Attachment.version, Attachment.id)))
    if len(files) != len(identifiers):
        raise HTTPException(422, "Choose stored resume, passport or Aadhaar files belonging to this candidate.")
    if db.scalar(select(Attachment.id).where(Attachment.id.in_(identifiers), rejected_expression()).limit(1)):
        raise HTTPException(409, "A selected document belongs to another person. Upload the correct document before extracting details.")
    queued, existing, job_ids = 0, 0, []
    for file in files:
        job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id,
            DocumentExtractionJob.attachment_id == file.id).order_by(DocumentExtractionJob.created_at.desc()).limit(1))
        if job is None:
            job = queue_reading(db, user, candidate, file, file.content_type or "application/pdf")
            queued += 1
        elif job.status == "failed" and requeue_failed(db, candidate_id, file.id):
            queued += 1
        else:
            existing += 1
        job_ids.append(job.id)
    return {"queued": queued, "existing": existing, "jobIds": job_ids}
