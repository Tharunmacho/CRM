"""PRD intake step 2: skip a file whose hash was already processed.

A file counts as processed once its exact bytes (sha256) were committed from an email, from a
WhatsApp message, or are stored against any candidate. Only completed work counts, so a file
whose earlier attempt failed or still waits for review is processed normally. A genuinely new
version of a resume has different bytes, so it is never matched here.
"""
from sqlalchemy import select

from app.db.models import Attachment, EmailIngestionJob, MailAttachment, MailMessage, WhatsAppMessage

DUPLICATE_REASON = "Duplicate file: these exact bytes were already processed."


def processed_hashes(db, hashes, *, exclude_mail_message_id=None, exclude_whatsapp_id=None) -> dict[str, dict]:
    """Map each already-processed hash to where it was processed (source, row id, candidate)."""
    wanted = {h for h in hashes if h}
    found: dict[str, dict] = {}
    if not wanted:
        return found
    for digest, row_id, candidate_id in db.execute(
        select(Attachment.content_hash, Attachment.id, Attachment.entity_id)
        .where(Attachment.entity_type == "candidate", Attachment.content_hash.in_(wanted))
        .order_by(Attachment.uploaded_at, Attachment.id)
    ):
        found.setdefault(digest, {"source": "candidate_file", "id": row_id, "candidateId": candidate_id})
    for digest, row_id, candidate_id in db.execute(
        select(MailAttachment.content_hash, MailAttachment.message_id, MailMessage.candidate_id)
        .join(MailMessage, MailMessage.id == MailAttachment.message_id)
        .join(EmailIngestionJob, EmailIngestionJob.message_id == MailAttachment.message_id)
        .where(MailAttachment.content_hash.in_(wanted), EmailIngestionJob.status == "completed",
               MailAttachment.message_id != (exclude_mail_message_id or ""))
        .order_by(MailMessage.received_at, MailAttachment.id)
    ):
        found.setdefault(digest, {"source": "email", "id": row_id, "candidateId": candidate_id})
    for digest, row_id, candidate_id in db.execute(
        select(WhatsAppMessage.content_hash, WhatsAppMessage.id, WhatsAppMessage.candidate_id)
        .where(WhatsAppMessage.content_hash.in_(wanted), WhatsAppMessage.status == "completed",
               WhatsAppMessage.id != (exclude_whatsapp_id or ""))
        .order_by(WhatsAppMessage.received_at, WhatsAppMessage.id)
    ):
        found.setdefault(digest, {"source": "whatsapp", "id": row_id, "candidateId": candidate_id})
    return found
