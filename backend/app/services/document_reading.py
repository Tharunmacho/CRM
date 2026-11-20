"""Explicit reading of stored candidate documents, separate from uploading bytes."""
from fastapi import HTTPException
from sqlalchemy import and_, exists, literal, or_, select
from sqlalchemy.orm import aliased
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob

from app.services.bulk_candidate_import import SENT_TO_READER as READABLE_SLOTS

PACKET_LINK = "Indexed document packet"
READ_PROOF = "Read document in intake packet"


def record_read(db, candidate, attachment, origin):
    db.add(AuditEntry(action=READ_PROOF, target=attachment.file_name, entity_type="candidate", entity_id=candidate.id,
                      meta={"attachment_id": attachment.id, **origin}))


def register_packet(db, candidate, source, pieces):
    """Record which cut files share this candidate's retained original, for immediate and deferred reading."""
    if source.slot != "source_bundle" or source.entity_type != "candidate" or source.entity_id != candidate.id:
        raise ValueError("The packet must be an original belonging to this candidate.")
    identifiers = {piece.id for slot, piece in pieces if slot in READABLE_SLOTS and piece.entity_type == "candidate"
                   and piece.entity_id == candidate.id and piece.deleted_at is None}
    if not identifiers:
        return
    previous = set(db.scalars(select(AuditEntry.meta["attachment_id"].astext).where(
        AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate.id, AuditEntry.action == PACKET_LINK,
        AuditEntry.meta["source_attachment_id"].astext == source.id)))
    for identifier in sorted(identifiers - previous):
        db.add(AuditEntry(entity_type="candidate", entity_id=candidate.id, action=PACKET_LINK, target=source.file_name,
                          meta={"attachment_id": identifier, "source_attachment_id": source.id}))
    db.flush()


def packet_sources(db, candidate_ids):
    """Map each cut file to its own original. A deleted original still records provenance, so its cuts cannot silently be resubmitted."""
    rows = db.execute(select(AuditEntry.entity_id, AuditEntry.meta["attachment_id"].astext, Attachment).select_from(AuditEntry)
        .join(Attachment, Attachment.id == AuditEntry.meta["source_attachment_id"].astext)
        .where(AuditEntry.action == PACKET_LINK, AuditEntry.entity_type == "candidate", AuditEntry.entity_id.in_(candidate_ids),
               Attachment.slot == "source_bundle", Attachment.entity_type == "candidate", Attachment.entity_id == AuditEntry.entity_id)
        .order_by(Attachment.deleted_at.is_(None), AuditEntry.at, AuditEntry.id))
    return {(candidate, piece): original for candidate, piece, original in rows}


def pending_expression():
    """Uncorrelated sets keep profile/bootstrap pending flags in their existing SQL round trip."""
    file = aliased(Attachment)      # its own alias, so this set never correlates with the Attachment of the statement it sits in
    imported = AuditEntry.meta["bulk_import"].as_boolean().is_(True)
    # A file waits to be read when its upload was stored with the reading deferred, or when the bulk import stored it in a readable slot:
    # imports made before the import wrote the deferred flag carry only bulk_import. Either way, until a reading job exists for it. An
    # imported file is read once per stored file (candidate + object_key, as the Import screen's read-again matches), however many slots hold it.
    link = aliased(AuditEntry)
    packet = select(link.meta["source_attachment_id"].astext).where(
        link.action == PACKET_LINK, link.entity_type == "candidate", link.entity_id == file.entity_id,
        link.meta["attachment_id"].astext == file.id).correlate(file)
    queued = exists(select(literal(1)).where(or_(
        DocumentExtractionJob.attachment_id == file.id,
        and_(DocumentExtractionJob.candidate_id == file.entity_id, DocumentExtractionJob.attachment_id.in_(packet)),
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
    failed_pieces = select(link.meta["attachment_id"].astext).where(link.action == PACKET_LINK, link.meta["source_attachment_id"].astext.in_(failed))
    return or_(Attachment.id.in_(deferred), Attachment.id.in_(failed), Attachment.id.in_(failed_pieces)).label("extraction_pending")


def start(db, candidate_id, user, attachment_ids):
    from app.services.application_form import ensure_can_edit, queue_reading, requeue_failed
    from app.services.document_identity import rejected_expression
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    ensure_can_edit(user, candidate)
    identifiers = list(dict.fromkeys(attachment_ids))
    files = list(db.scalars(select(Attachment).where(Attachment.id.in_(identifiers),
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id, Attachment.deleted_at.is_(None),
        Attachment.slot.in_(READABLE_SLOTS)).order_by(Attachment.uploaded_at, Attachment.version, Attachment.id)))
    if len(files) != len(identifiers):
        raise HTTPException(422, "Choose stored resume, passport, Aadhaar or education certificate files belonging to this candidate.")
    if db.scalar(select(Attachment.id).where(Attachment.id.in_(identifiers), rejected_expression()).limit(1)):
        raise HTTPException(409, "A selected document belongs to another person. Upload the correct document before extracting details.")
    sources = packet_sources(db, [candidate_id])
    covered = set(db.scalars(select(AuditEntry.meta["attachment_id"].astext).where(
        AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate_id, AuditEntry.meta["attachment_id"].astext.in_(identifiers),
        AuditEntry.action.in_(("Separated a document from a combined upload", READ_PROOF)))))
    targets = {}
    queued, existing, job_ids = 0, 0, []
    for piece in files:
        original = sources.get((candidate_id, piece.id))
        if original is None and piece.id in covered:
            existing += 1
            continue
        file = original or piece
        targets[file.id] = file
    if sources and db.scalar(select(Attachment.id).where(Attachment.id.in_(targets), or_(Attachment.deleted_at.is_not(None), rejected_expression())).limit(1)):
        raise HTTPException(409, "The original packet needs an identity check before its documents can be read.")
    for file in targets.values():
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
