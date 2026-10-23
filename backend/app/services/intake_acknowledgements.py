"""Receipt-level, opt-in acknowledgements with durable uncertain-send recovery.

Queue in the candidate transaction; contact providers only after that transaction
commits. RFC 3834 safety checks are deliberately conservative for old receipts.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from email.utils import getaddresses
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import (DocumentPersonGroup, EmailIngestionJob, IntakeAcknowledgement,
    IntakeAcknowledgementSettings, MailAccount, MailMessage, MultiPersonIntake,
    WhatsAppChannel, WhatsAppMessage, WhatsAppSendAttempt)
from app.integrations.mail.smtp import MailSendError, address, compose, deliver
from app.integrations.mail.base import MailAdapterError
from app.services.mail_providers import account_config


DEFAULT_BODY = ("Thank you for contacting Care India Overseas. We have received and saved "
                "your candidate documents. Our recruitment team will review them and contact "
                "you when there is a suitable next step.")


def utcnow():
    return datetime.now(timezone.utc)


def channel(db, source_kind, channel_id, *, lock=False):
    model, key = (MailAccount, MailAccount.id) if source_kind == "email" else (WhatsAppChannel, WhatsAppChannel.phone_number_id)
    query = select(model).where(key == channel_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "Mailbox or WhatsApp channel not found.")
    return row


def connected(source_kind, row):
    return row.disconnected_at is None if source_kind == "email" else row.enabled


def configuration(db, source_kind, channel_id):
    return db.scalar(select(IntakeAcknowledgementSettings).where(
        IntakeAcknowledgementSettings.source_kind == source_kind,
        IntakeAcknowledgementSettings.channel_id == channel_id))


def completed_source(db, source_kind, source_id):
    source = db.get(MailMessage if source_kind == "email" else WhatsAppMessage, source_id)
    if source is None:
        return None
    if source_kind == "email":
        job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == source_id))
        if job is None or job.status != "completed":
            return None
    elif source.status != "completed":
        return None
    bundle = db.scalar(select(MultiPersonIntake).where(
        MultiPersonIntake.source_type == source_kind, MultiPersonIntake.source_id == source_id))
    if bundle is not None:
        groups = list(db.scalars(select(DocumentPersonGroup).where(DocumentPersonGroup.bundle_id == bundle.id)))
        if bundle.status != "completed" or not groups or any(row.status != "committed" or not row.candidate_id for row in groups):
            return None
    elif not source.candidate_id:
        return None
    return source


def email_suppression(db, message, account):
    """Only human, single-sender receipts with a known non-null return path."""
    headers = message.automation_headers or {}
    if headers.get("historical_import"):
        return "Historical imports are not automatically acknowledged."
    if headers.get("parsed") is not True:
        return "Original mail safety headers are unavailable; automatic acknowledgement skipped."
    def values(name):
        value = headers.get(name, [])
        return [str(item).strip().lower() for item in value] if isinstance(value, list) else ["invalid"]
    paths = values("return-path")
    if len(paths) != 1 or paths[0] in {"", "<>"}:
        return "Missing, ambiguous or empty return path; automatic acknowledgement skipped."
    try:
        address(paths[0])
        recipient = address(message.from_address).lower()
    except MailSendError:
        return "The source has no safe sender address for an automatic acknowledgement."
    from_headers = values("from")
    if len(from_headers) != 1 or len(getaddresses(from_headers)) != 1:
        return "Ambiguous source sender; automatic acknowledgement skipped."
    if any(value.split(";", 1)[0].strip() != "no" for value in values("auto-submitted")):
        return "Automatic message; acknowledgement suppressed to prevent mail loops."
    if values("list-id") or values("list-unsubscribe") or any(value in {"bulk", "list", "junk"} for value in values("precedence")):
        return "Mailing-list or bulk message; automatic acknowledgement skipped."
    if any(value not in {"", "none"} for value in values("x-auto-response-suppress")):
        return "The sender requested suppression of automatic responses."
    local = re.sub(r"[-_.]", "", recipient.split("@", 1)[0])
    if local in {"noreply", "donotreply", "mailerdaemon", "postmaster", "bounce", "bounces"}:
        return "Automated sender address; automatic acknowledgement skipped."
    owned = {value.lower() for value in db.scalars(select(MailAccount.address))}
    if recipient == account.address.lower() or recipient in owned:
        return "Internal mailbox sender; automatic acknowledgement skipped to prevent loops."
    if (message.folder or "INBOX").upper() != "INBOX" or message.remote_deleted:
        return "The source is not an active incoming message."
    return None


def queue_intake_acknowledgement(db, source_kind, source_id):
    """No commit/provider I/O. One durable intent for an eligible saved receipt."""
    if source_kind not in {"email", "whatsapp"}:
        return None
    db.flush()
    source = completed_source(db, source_kind, source_id)
    if source is None:
        return None
    channel_id = source.account_id if source_kind == "email" else source.phone_number_id
    config = configuration(db, source_kind, channel_id)
    if config is None or not config.enabled or config.enabled_at is None or source.received_at < config.enabled_at:
        return None  # Enabling never acknowledges historic imports/backlog.
    existing = db.scalar(select(IntakeAcknowledgement).where(
        IntakeAcknowledgement.source_kind == source_kind, IntakeAcknowledgement.source_id == source_id))
    if existing:
        return existing
    account = channel(db, source_kind, channel_id)
    recipient = (source.from_address or "") if source_kind == "email" else source.sender_number
    reason = email_suppression(db, source, account) if source_kind == "email" else None
    status = "suppressed" if reason else "pending" if connected(source_kind, account) else "paused"
    key = "ack-" + sha256(f"{source_kind}:{source_id}".encode()).hexdigest()[:60]
    subject = re.sub(r"[\r\n\x00]", " ", source.subject or "Candidate documents")[:900] if source_kind == "email" else None
    if subject and not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    row_id = db.scalar(insert(IntakeAcknowledgement).values(source_kind=source_kind, source_id=source_id,
        channel_id=channel_id, recipient=recipient[:255], body=config.body, subject=subject, status=status,
        request_key=key, last_error=reason or ("Channel disconnected; acknowledgement paused." if status == "paused" else None))
        .on_conflict_do_nothing(index_elements=["source_kind", "source_id"]).returning(IntakeAcknowledgement.id))
    if row_id is None:
        return db.scalar(select(IntakeAcknowledgement).where(IntakeAcknowledgement.request_key == key))
    row = db.get(IntakeAcknowledgement, row_id)
    if source_kind == "whatsapp" and status == "pending":
        enqueue_whatsapp(db, row)
    return row


def enqueue_whatsapp(db, row):
    from app.services.whatsapp_chat import enqueue
    try:
        with db.begin_nested():
            attempt = enqueue(db, channel_id=row.channel_id, recipient=row.recipient, body=row.body,
                request_key=row.request_key, source_message_id=row.source_id, actor_id=None)
            db.flush()
            row.outbound_id = attempt.id
    except HTTPException as exc:
        row.status, row.last_error = "failed", str(exc.detail)[:500]


def attempt_out(db, row):
    state = row.status
    error, provider_id, sent_at = row.last_error, row.provider_message_id, row.sent_at
    if row.source_kind == "whatsapp" and row.outbound_id:
        outgoing = db.get(WhatsAppSendAttempt, row.outbound_id)
        if outgoing:
            state = {"queued": "pending", "preparing": "sending", "accepted": "sent",
                "delivered": "sent", "read": "sent", "cancelled": "suppressed"}.get(outgoing.state, outgoing.state)
            error, provider_id, sent_at = outgoing.last_error, outgoing.provider_message_id, outgoing.sent_at or outgoing.accepted_at
    elif state == "sending" and row.started_at and row.started_at < utcnow() - timedelta(minutes=3):
        state, error = "unknown", "Sending was interrupted. Check the provider's Sent folder; this acknowledgement will not resend automatically."
    return {"id": row.id, "sourceId": row.source_id, "recipient": row.recipient, "body": row.body,
        "subject": row.subject, "status": state, "providerMessageId": provider_id, "createdAt": row.created_at,
        "sentAt": sent_at, "lastError": error, "canRetry": row.source_kind == "email" and state in {"failed", "paused"}}


def dispatch_email_acknowledgement(attempt_id, *, sessions, settings, sender=None):
    """Commit sending intent before SMTP; crashes and ambiguous acceptance never retry."""
    sender = sender or deliver
    with sessions() as db:
        # Consistent account-before-attempt order with disconnect/settings actions.
        identity = db.get(IntakeAcknowledgement, attempt_id)
        if identity is None or identity.source_kind != "email":
            return
        account = channel(db, "email", identity.channel_id, lock=True)
        row = db.scalar(select(IntakeAcknowledgement).where(IntakeAcknowledgement.id == attempt_id)
            .with_for_update().execution_options(populate_existing=True))
        if row.status != "pending":
            return
        config = configuration(db, "email", row.channel_id)
        if not connected("email", account) or config is None or not config.enabled or not settings.email_ingestion_enabled:
            row.status, row.last_error = "paused", "Automatic acknowledgements or this mailbox are disconnected."
            db.commit()
            return
        source = completed_source(db, "email", row.source_id)
        reason = email_suppression(db, source, account) if source else "The source intake is not fully saved."
        if reason:
            row.status, row.last_error = "suppressed", reason
            db.commit()
            return
        try:
            mail_config = account_config(db, settings, account)
            message_id = f"<crm-ack-{row.id}@{account.address.split('@')[-1]}>"
            outgoing, recipients = compose(mail_config, message_id=message_id, to=[row.recipient], cc=[], bcc=[],
                subject=row.subject or "Candidate documents received", body=row.body,
                in_reply_to=source.message_id_header, references=source.references_header)
            outgoing["Auto-Submitted"] = "auto-replied"
            outgoing["X-Auto-Response-Suppress"] = "All"
        except (MailAdapterError, HTTPException):
            row.status, row.last_error = "failed", "Outgoing mail is not ready. Check this mailbox's connection before retrying."
            db.commit()
            return
        row.status, row.started_at, row.last_error = "sending", utcnow(), None
        row.attempts += 1
        row.provider_message_id = message_id
        db.commit()
    try:
        refused = sender(mail_config, outgoing, recipients, envelope_from="")
        state, error = ("failed", "The provider refused this recipient.") if refused else ("sent", None)
    except MailSendError as exc:
        state, error = ("unknown" if exc.unknown else "failed"), str(exc)
    except Exception:
        state, error = "unknown", "Delivery could not be confirmed. Check the provider's Sent folder before resolving this acknowledgement."
    with sessions() as db:
        row = db.scalar(select(IntakeAcknowledgement).where(IntakeAcknowledgement.id == attempt_id).with_for_update())
        # Never erase a provider-confirmed/admin-resolved result with delayed work.
        if row.status == "sending":
            row.status, row.last_error = state, error
            if state == "sent":
                row.sent_at = utcnow()
            db.commit()


def dispatch_pending_email_acknowledgements(*, sessions, settings, limit=5):
    with sessions() as db:
        stale = list(db.scalars(select(IntakeAcknowledgement).where(IntakeAcknowledgement.source_kind == "email",
            IntakeAcknowledgement.status == "sending", IntakeAcknowledgement.started_at < utcnow() - timedelta(minutes=3))
            .with_for_update(skip_locked=True)))
        for row in stale:
            row.status, row.last_error = "unknown", "Sending was interrupted; check the provider's Sent folder before resolving."
        ids = list(db.scalars(select(IntakeAcknowledgement.id).where(IntakeAcknowledgement.source_kind == "email",
            IntakeAcknowledgement.status == "pending").order_by(IntakeAcknowledgement.created_at, IntakeAcknowledgement.id).limit(limit)))
        db.commit()
    for attempt_id in ids:
        dispatch_email_acknowledgement(attempt_id, sessions=sessions, settings=settings)
