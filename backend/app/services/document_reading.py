"""Explicit reading of stored candidate documents, separate from uploading bytes."""
from fastapi import HTTPException
from sqlalchemy import and_, exists, literal, or_, select
from sqlalchemy.orm import aliased
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob

from app.services.bulk_candidate_import import SENT_TO_READER as READABLE_SLOTS  # resume, passport, Aadhaar and education certificates


def pending_expression():
    """Uncorrelated sets keep profile/bootstrap pending flags in their existing SQL round trip."""
    file = aliased(Attachment)      # its own alias, so this set never correlates with the Attachment of the statement it sits in
    imported = AuditEntry.meta["bulk_import"].as_boolean().is_(True)
    # A file waits to be read when its upload was stored with the reading deferred, or when the bulk import stored it in a readable slot:
    # imports made before the import wrote the deferred flag carry only bulk_import. Either way, until a reading job exists for it. An
    # imported file is read once per stored file (candidate + object_key, as the Import screen's read-again matches), however many slots hold it.
    queued = exists(select(literal(1)).where(or_(
        DocumentExtractionJob.attachment_id == file.id,
        and_(imported, DocumentExtractionJob.candidate_id == file.entity_id, DocumentExtractionJob.object_key == file.object_key),
    )).correlate(file, AuditEntry))
    deferred = select(file.id).join(AuditEntry, AuditEntry.meta["attachment_id"].astext == file.id).where(
        AuditEntry.action == "Uploaded document", AuditEntry.entity_type == "candidate",
        or_(AuditEntry.meta["extraction_deferred"].as_boolean().is_(True), and_(imported, file.slot.in_(READABLE_SLOTS))),
        ~queued,
    )
    # A failed reading remains visible and can be explicitly retried; uploading
    # the same file with deferral must never silently restart it.
    latest = select(DocumentExtractionJob.attachment_id, DocumentExtractionJob.status).where(
        DocumentExtractionJob.attachment_id.is_not(None)).distinct(DocumentExtractionJob.attachment_id).order_by(
        DocumentExtractionJob.attachment_id, DocumentExtractionJob.created_at.desc()).subquery()
    failed = select(latest.c.attachment_id).where(latest.c.status == "failed")
    return or_(Attachment.id.in_(deferred), Attachment.id.in_(failed)).label("extraction_pending")


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
        raise HTTPException(422, "Choose stored resume, passport, Aadhaar or education certificate files belonging to this candidate.")
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
