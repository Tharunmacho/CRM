"""Cheap, read-only summaries for the inbox badge and the dashboard attention list.

Nothing here loads message bodies, OCR state or extraction payloads: only the few
columns a person needs to decide whether to retry or open a failed intake.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentExtractionJob, EmailIngestionJob, MailAccount, MailAttachment, MailMessage,
    Role, User, WhatsAppChannel, WhatsAppMessage,
)
from app.services import mail_providers
from app.services.parallel_reads import parallel_reads

ATTENTION_STATUSES = ("failed", "needs_review")
ATTENTION_LIMIT = 20
MANUAL_RETRY_KINDS = frozenset({"resume", "passport", "aadhaar"})
REASON_LIMIT = 240
WHATSAPP_RETRY_STATUSES = frozenset({"received", "needs_review", "failed"})


def unread_counts(db: Session) -> dict:
    """Unread INBOX messages for connected, live accounts in one grouped query."""
    rows = db.execute(
        select(MailMessage.account_id, func.count())
        .join(MailAccount, MailAccount.id == MailMessage.account_id)
        .where(
            MailMessage.unread.is_(True), MailMessage.folder == "INBOX", MailMessage.remote_deleted.is_(False),
            MailAccount.disconnected_at.is_(None), MailAccount.provider.in_(sorted(mail_providers.LIVE_PROVIDERS)),
        )
        .group_by(MailMessage.account_id)
    ).all()
    return {"total": sum(count for _, count in rows), "accounts": {account_id: count for account_id, count in rows}}


def _reason(error: str | None, status: str) -> str:
    text = " ".join((error or "").split())
    if not text:
        return "Needs review" if status == "needs_review" else "Processing failed"
    return text if len(text) <= REASON_LIMIT else text[: REASON_LIMIT - 1].rstrip() + "…"


def _email_items(db: Session, can_retry: bool) -> list[dict]:
    rows = db.execute(
        select(
            EmailIngestionJob.id, EmailIngestionJob.status, EmailIngestionJob.attempts, EmailIngestionJob.last_error,
            EmailIngestionJob.candidate_id, EmailIngestionJob.updated_at, EmailIngestionJob.message_id,
            EmailIngestionJob.state.has_key("receive_error").label("receive_error"),
            MailMessage.subject, MailMessage.from_name, MailMessage.from_address,
            MailAccount.address, MailAccount.provider, MailAccount.disconnected_at,
        )
        .join(MailMessage, MailMessage.id == EmailIngestionJob.message_id)
        .join(MailAccount, MailAccount.id == MailMessage.account_id)
        .where(EmailIngestionJob.status.in_(ATTENTION_STATUSES))
        .order_by(EmailIngestionJob.updated_at.desc(), EmailIngestionJob.id).limit(ATTENTION_LIMIT)
    ).all()
    files: dict[str, list[str]] = {}
    if rows:
        for message_id, name in db.execute(
            select(MailAttachment.message_id, MailAttachment.file_name)
            .where(MailAttachment.message_id.in_([row.message_id for row in rows])).order_by(MailAttachment.file_name)
        ):
            files.setdefault(message_id, []).append(name)
    return [{
        "id": f"email:{row.id}", "source": "email", "jobId": row.id, "messageId": row.message_id,
        "status": row.status, "title": row.subject or "(No subject)",
        "sender": row.from_name or row.from_address or "", "channel": row.address,
        "files": files.get(row.message_id, []), "reason": _reason(row.last_error, row.status),
        "attempts": row.attempts, "updatedAt": row.updated_at, "candidateId": row.candidate_id,
        # The retry route refuses an unreceivable message or a disconnected/archived mailbox.
        "retryable": can_retry and not row.receive_error and row.disconnected_at is None
                     and mail_providers.is_live(row.provider),
    } for row in rows]


def _whatsapp_items(db: Session, can_retry: bool) -> list[dict]:
    rows = db.execute(
        select(
            WhatsAppMessage.id, WhatsAppMessage.status, WhatsAppMessage.attempts, WhatsAppMessage.last_error,
            WhatsAppMessage.candidate_id, WhatsAppMessage.updated_at, WhatsAppMessage.message_type,
            WhatsAppMessage.file_name, WhatsAppMessage.sender_name, WhatsAppMessage.sender_number,
            WhatsAppMessage.media_id, func.left(WhatsAppMessage.body, 80).label("snippet"),
            WhatsAppChannel.display_number, WhatsAppChannel.phone_number_id, WhatsAppChannel.enabled,
        )
        .join(WhatsAppChannel, WhatsAppChannel.phone_number_id == WhatsAppMessage.phone_number_id)
        .where(WhatsAppMessage.status.in_(ATTENTION_STATUSES))
        .order_by(WhatsAppMessage.updated_at.desc(), WhatsAppMessage.id).limit(ATTENTION_LIMIT)
    ).all()
    return [{
        "id": f"whatsapp:{row.id}", "source": "whatsapp", "jobId": row.id, "messageId": row.id,
        "status": row.status, "title": row.file_name or row.snippet or f"WhatsApp {row.message_type}",
        "sender": row.sender_name or row.sender_number, "channel": row.display_number or row.phone_number_id,
        "files": [row.file_name] if row.file_name else [], "reason": _reason(row.last_error, row.status),
        "attempts": row.attempts, "updatedAt": row.updated_at, "candidateId": row.candidate_id,
        "retryable": can_retry and bool(row.media_id) and row.enabled and row.status in WHATSAPP_RETRY_STATUSES,
    } for row in rows]


def _manual_items(db: Session, user: User) -> list[dict]:
    rows = db.execute(
        select(
            DocumentExtractionJob.id, DocumentExtractionJob.status, DocumentExtractionJob.attempts,
            DocumentExtractionJob.last_error, DocumentExtractionJob.candidate_id, DocumentExtractionJob.updated_at,
            DocumentExtractionJob.filename, DocumentExtractionJob.kind, DocumentExtractionJob.owner_id,
            DocumentExtractionJob.group_id, User.name,
        )
        .join(User, User.id == DocumentExtractionJob.owner_id)
        .where(DocumentExtractionJob.status.in_(ATTENTION_STATUSES))
        .order_by(DocumentExtractionJob.updated_at.desc(), DocumentExtractionJob.id).limit(ATTENTION_LIMIT)
    ).all()
    return [{
        "id": f"manual:{row.id}", "source": "manual", "jobId": row.id, "messageId": None, "groupId": row.group_id,
        "status": row.status, "title": row.filename, "sender": row.name, "channel": "Manual upload",
        "files": [row.filename], "reason": _reason(row.last_error, row.status),
        "attempts": row.attempts, "updatedAt": row.updated_at, "candidateId": row.candidate_id,
        # Manual uploads are owner-scoped: the OCR retry route only accepts the uploader.
        "retryable": user.role == Role.admin and row.owner_id == user.id and row.kind in MANUAL_RETRY_KINDS,
    } for row in rows]


def attention_list(db: Session, user: User) -> dict:
    """Most recent failed or needs-review intake from email, WhatsApp and manual uploads."""
    can_retry = user.role == Role.admin

    def count(model):
        return lambda session: session.scalar(select(func.count()).select_from(model).where(model.status.in_(ATTENTION_STATUSES))) or 0

    # Six independent reads: waiting for them one after another costs six round trips.
    email, whatsapp, manual, email_total, whatsapp_total, manual_total = parallel_reads(
        db,
        lambda session: _email_items(session, can_retry),
        lambda session: _whatsapp_items(session, can_retry),
        lambda session: _manual_items(session, user),
        count(EmailIngestionJob), count(WhatsAppMessage), count(DocumentExtractionJob),
    )
    items = [*email, *whatsapp, *manual]
    items.sort(key=lambda item: (item["updatedAt"], item["id"]), reverse=True)
    totals = {"email": email_total, "whatsapp": whatsapp_total, "manual": manual_total}
    return {"items": items[:ATTENTION_LIMIT], "totals": {**totals, "all": sum(totals.values())}, "limit": ATTENTION_LIMIT, "canRetry": can_retry}
