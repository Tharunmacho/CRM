"""Mailbox configuration references and durable intake controls. No outgoing mail."""
from datetime import datetime, timezone
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, load_only

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import EmailIngestionJob, MailAccount, MailAttachment, MailMessage, Role, User
from app.db.session import get_db
from app.integrations.mail.imap import (
    IMAPMailbox, MailAdapterError, mailbox_source_id, require_cursor_source,
)
from app.services.mail_config import configured_mailboxes
from app.services.document_extraction import request_extraction_retry

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
admin = require_role(Role.admin)


class ConnectIn(BaseModel):
    address: str | None = None
    auto_ingest: bool = True


class AccountIn(BaseModel):
    auto_ingest: bool


def configs():
    try:
        return configured_mailboxes(get_settings())
    except (ValueError, MailAdapterError) as exc:
        raise HTTPException(503, str(exc)) from exc


def account_out(account, configured):
    return {
        "id": account.id, "address": account.address, "provider": account.provider,
        "autoIngest": account.auto_ingest, "lastSyncAt": account.last_sync_at,
        "configured": account.address in configured,
        "connectionStatus": "error" if account.last_error else "connected",
        "lastError": account.last_error,
    }


@router.get("")
def inbox(db: Session = Depends(get_db), user: User = Depends(current_user)):
    accounts = list(db.scalars(select(MailAccount).options(
        defer(MailAccount.secrets), defer(MailAccount.sync_state),
    ).order_by(MailAccount.address)))
    messages = list(db.scalars(select(MailMessage).order_by(MailMessage.received_at.desc()).limit(200)))
    ids = [m.id for m in messages]
    attachments = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id.in_(ids)))) if ids else []
    jobs = list(db.scalars(select(EmailIngestionJob).order_by(EmailIngestionJob.updated_at.desc()).limit(100)))
    # Administrators configure accounts; other roles never receive credential names/values.
    configured = configs() if user.role == Role.admin else {}
    heartbeat = max((a.worker_seen_at for a in accounts if a.worker_seen_at), default=None)
    return {
        "accounts": [account_out(a, configured) for a in accounts],
        "availableAccounts": list(configured) if user.role == Role.admin else [],
        "messages": [{
            "id": m.id, "accountId": m.account_id, "fromName": m.from_name or m.from_address or "",
            "fromAddress": m.from_address or "", "subject": m.subject or "(No subject)",
            "body": m.body or "", "preview": (m.body or "")[:180],
            "receivedAt": m.received_at, "unread": m.unread, "candidateId": m.candidate_id,
            "attachments": [{"id": a.id, "fileName": a.file_name, "sizeKb": a.size_kb or 0,
                             "kind": a.kind or "document"} for a in attachments if a.message_id == m.id],
        } for m in messages],
        "jobs": [{
            "id": j.id, "messageId": j.message_id, "status": j.status, "attempts": j.attempts,
            "lastError": j.last_error, "candidateId": j.candidate_id, "updatedAt": j.updated_at,
            "inspection": j.inspection,
        } for j in jobs],
        "worker": {"enabled": get_settings().email_ingestion_enabled, "lastHeartbeat": heartbeat},
    }


@router.get("/health")
def health(db: Session = Depends(get_db), user: User = Depends(admin)):
    accounts = list(db.scalars(select(MailAccount).options(load_only(
        MailAccount.id, MailAccount.address, MailAccount.last_sync_at,
        MailAccount.last_error, MailAccount.auto_ingest, MailAccount.worker_seen_at,
    )).order_by(MailAccount.address)))
    counts = {name: 0 for name in (
        "pending", "processing", "retry", "needs_review", "failed", "completed", "ignored",
    )}
    for name, count in db.execute(
        select(EmailIngestionJob.status, func.count()).group_by(EmailIngestionJob.status)
    ):
        if name in counts:
            counts[name] = count
    return {
        "accounts": [{
            "id": account.id, "address": account.address, "lastSyncAt": account.last_sync_at,
            "lastError": account.last_error, "autoIngest": account.auto_ingest,
        } for account in accounts],
        "counts": counts,
        "worker": {
            "enabled": get_settings().email_ingestion_enabled,
            "lastHeartbeat": max((a.worker_seen_at for a in accounts if a.worker_seen_at), default=None),
        },
    }


@router.post("/connect")
def connect(body: ConnectIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    available = configs()
    if not available:
        raise HTTPException(503, "Set IMAP_SERVER, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD, IMAP_USE_SSL and IMAP_FOLDER in backend environment settings.")
    address = body.address.strip().lower() if body.address else next(iter(available))
    if address not in available:
        raise HTTPException(422, "This address has no server-side mailbox configuration.")
    existing = db.scalar(select(MailAccount).where(MailAccount.address == address))
    if existing:
        try:
            require_cursor_source(existing.sync_state or {}, available[address])
        except MailAdapterError as exc:
            raise HTTPException(409, str(exc)) from None
        # Reconnecting must never jump over an existing durable cursor.
        return account_out(existing, available)
    db.rollback()
    try:
        with IMAPMailbox(available[address]) as mailbox:
            cursor = mailbox.probe()
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from exc
    account = MailAccount(address=address, provider="imap", auto_ingest=body.auto_ingest,
                          sync_state={"uid_validity": cursor.uid_validity, "last_uid": cursor.last_uid,
                                      "source_id": mailbox_source_id(available[address])},
                          sync_requested=True)
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(MailAccount).where(MailAccount.address == address))
        if existing is None:
            raise
        return account_out(existing, available)
    return account_out(account, available)


@router.patch("/accounts/{account_id}")
def update_account(account_id: str, body: AccountIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = db.get(MailAccount, account_id)
    if not account:
        raise HTTPException(404, "Mailbox not found")
    account.auto_ingest = body.auto_ingest
    db.commit()
    return account_out(account, configs())


@router.post("/messages/{message_id}/ingest", status_code=202)
def ingest(message_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    message = db.get(MailMessage, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == message_id))
    if job:
        return {"status": job.status}
    if not db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == message_id).limit(1)):
        raise HTTPException(422, "This message has no imported attachments.")
    db.add(EmailIngestionJob(message_id=message_id, state={}))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == message_id))
        if existing is None:
            raise
        return {"status": existing.status}
    return {"status": "queued"}


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    job = db.get(EmailIngestionJob, job_id)
    if not job:
        raise HTTPException(404, "Ingestion job not found")
    if job.status not in {"failed", "needs_review", "ignored"}:
        raise HTTPException(409, "This job is already queued, running or complete.")
    state = deepcopy(job.state or {})
    if state.get("receive_error"):
        raise HTTPException(422, "The source message could not be imported. Correct or resend the attachment.")
    job.status, job.last_error, job.attempts = "pending", None, 0
    job.next_attempt_at = datetime.now(timezone.utc)
    for document in state.values():
        if isinstance(document, dict):
            request_extraction_retry(document)
            for key in ("output", "inspection", "inspection_signature", "extractions", "extraction_status", "warnings"):
                document.pop(key, None)
    job.state = state
    # The explicit retry authorizes a new generation only for confirmed failed
    # or invalid completed jobs. Pending/unknown accepted IDs remain resumable.
    db.commit()
    return {"status": "queued"}
