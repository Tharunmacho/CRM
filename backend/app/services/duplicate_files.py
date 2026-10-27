"""PRD intake step 2: skip a file whose hash was already processed.

A file counts as processed only while the candidate it created still exists. Its exact bytes (sha256)
must be stored against a candidate, or have been committed into one from an email or a WhatsApp
message. Once that candidate is deleted, the same file is new again and is read normally. Only
completed work counts, so a file whose earlier attempt failed or still waits for review is processed
normally too. A genuinely new version of a resume has different bytes, so it is never matched here.
"""
from datetime import datetime

from sqlalchemy import select

from app.db.models import Attachment, Candidate, EmailIngestionJob, MailAttachment, MailMessage, WhatsAppMessage

DUPLICATE_REASON = "Duplicate file: this is an exact copy of a file we already processed."

_WHERE = {"candidate_file": "saved in the documents of", "email": "already read from an email for", "whatsapp": "already read from WhatsApp for"}


def processed_hashes(db, hashes, *, exclude_mail_message_id=None, exclude_whatsapp_id=None) -> dict[str, dict]:
    """Map each already-processed hash to where it was processed (source, row id, candidate).

    Only candidates that still exist count: every query joins the candidate row, so a deleted
    candidate never blocks the same file from being read again.
    """
    wanted = {h for h in hashes if h}
    found: dict[str, dict] = {}
    if not wanted:
        return found
    for digest, row_id, candidate_id, name, ref, at in db.execute(
        select(Attachment.content_hash, Attachment.id, Candidate.id, Candidate.name, Candidate.ref, Attachment.uploaded_at)
        .join(Candidate, Candidate.id == Attachment.entity_id)
        .where(Attachment.entity_type == "candidate", Attachment.content_hash.in_(wanted))
        .order_by(Attachment.uploaded_at, Attachment.id)
    ):
        found.setdefault(digest, {"source": "candidate_file", "id": row_id, "candidateId": candidate_id,
                                  "candidateName": name, "candidateRef": ref, "at": at.isoformat() if at else None})
    for digest, row_id, candidate_id, name, ref, at in db.execute(
        select(MailAttachment.content_hash, MailAttachment.message_id, Candidate.id, Candidate.name, Candidate.ref, MailMessage.received_at)
        .join(MailMessage, MailMessage.id == MailAttachment.message_id)
        .join(Candidate, Candidate.id == MailMessage.candidate_id)
        .join(EmailIngestionJob, EmailIngestionJob.message_id == MailAttachment.message_id)
        .where(MailAttachment.content_hash.in_(wanted), EmailIngestionJob.status == "completed",
               MailAttachment.message_id != (exclude_mail_message_id or ""))
        .order_by(MailMessage.received_at, MailAttachment.id)
    ):
        found.setdefault(digest, {"source": "email", "id": row_id, "candidateId": candidate_id,
                                  "candidateName": name, "candidateRef": ref, "at": at.isoformat() if at else None})
    for digest, row_id, candidate_id, name, ref, at in db.execute(
        select(WhatsAppMessage.content_hash, WhatsAppMessage.id, Candidate.id, Candidate.name, Candidate.ref, WhatsAppMessage.received_at)
        .join(Candidate, Candidate.id == WhatsAppMessage.candidate_id)
        .where(WhatsAppMessage.content_hash.in_(wanted), WhatsAppMessage.status == "completed",
               WhatsAppMessage.id != (exclude_whatsapp_id or ""))
        .order_by(WhatsAppMessage.received_at, WhatsAppMessage.id)
    ):
        found.setdefault(digest, {"source": "whatsapp", "id": row_id, "candidateId": candidate_id,
                                  "candidateName": name, "candidateRef": ref, "at": at.isoformat() if at else None})
    return found


def duplicate_reason(matches) -> str:
    """The exact reason a file was skipped: which candidate already has it, and where it came from.

    ``matches`` is one value from ``processed_hashes`` or several. The text always starts with
    "Duplicate file" and says what to do if the earlier record was removed by mistake.
    """
    items = [matches] if isinstance(matches, dict) else list(matches)
    seen: set[str] = set()
    parts: list[str] = []
    for info in items:
        key = info.get("candidateId") or info.get("id") or ""
        if key in seen:
            continue
        seen.add(key)
        who = info.get("candidateName") or "a candidate"
        if info.get("candidateRef"):
            who = f"{who} ({info['candidateRef']})"
        try:
            stamp = f" on {datetime.fromisoformat(info['at']):%d %b %Y}" if info.get("at") else ""
        except (TypeError, ValueError):
            stamp = ""
        parts.append(f"{_WHERE.get(info.get('source'), 'already saved for')} {who}{stamp}")
    if not parts:
        return DUPLICATE_REASON
    shown = parts[:3]
    more = f" and {len(parts) - 3} more" if len(parts) > 3 else ""
    return ("Duplicate file: this exact file is " + "; ".join(shown) + more
            + ". Delete that candidate if you want to read the file again.")
