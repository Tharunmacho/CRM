"""Explicit mailbox connections and conversation actions."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from typing import Literal
import mimetypes

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import Attachment, AuditEntry, Candidate, EmailIngestionJob, MailAccount, MailAttachment, MailMessage, MailReply, Role, User
from app.db.session import get_db, writes_on_get
from app.integrations.mail.base import MailAdapterError
from app.integrations.mail.smtp import MailSendError, address as valid_address, send_reply
from app.services import mail_providers
from app.services.parallel_reads import parallel_reads
from app.services.document_storage import file_response

router = APIRouter(tags=["inbox"])
admin = require_role(Role.admin)


@router.post("/accounts/{account_id}/disconnect")
def disconnect(account_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    if account is None:
        raise HTTPException(404, "Mailbox not found")
    account.disconnected_at = datetime.now(timezone.utc)
    account.secrets, account.sync_requested = None, False
    db.add(AuditEntry(actor_id=user.id, action="Disconnected mailbox", target=account.address,
        entity_type="mail_account", entity_id=account.id))
    db.commit()
    return {"status": "disconnected"}


@router.post("/accounts/{account_id}/sync", status_code=202)
def sync_account(account_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Mailbox not found")
    if not mail_providers.is_live(account.provider):
        raise HTTPException(409, "This mail provider is no longer supported. Old emails can still be read.")
    if account.disconnected_at:
        raise HTTPException(409, "Reconnect this mailbox before you ask it to check for new emails.")
    if not get_settings().email_ingestion_enabled:
        raise HTTPException(503, "Email checking is switched off on the server. Ask your administrator to turn it on.")
    account.sync_requested = True
    db.commit()
    return {"status": "queued"}


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unread: StrictBool


@router.patch("/messages/{message_id}/read")
def mark_read(message_id: str, body: ReadIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    message = db.get(MailMessage, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    source = db.get(MailAccount, message.account_id)
    if mail_providers.is_live(source.provider):
        from app.api.routers.zoho_mailbox import FlagsIn, flags
        return flags(message_id, FlagsIn(unread=body.unread), db, user)
    message.unread = body.unread
    db.commit()
    return {"id": message.id, "unread": message.unread}


@router.get("/attachments/{attachment_id}")
def attachment_file(attachment_id: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    attachment = db.get(MailAttachment, attachment_id)
    if not attachment or not attachment.object_key:
        raise HTTPException(404, "Attachment is not available")
    mime = mimetypes.guess_type(attachment.file_name)[0] or "application/octet-stream"
    inline = mime in {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}
    try:
        return file_response(get_settings(), attachment.object_key, attachment.file_name, mime, download or not inline)
    except FileNotFoundError:
        raise HTTPException(404, "The stored attachment is unavailable.") from None
    except (OSError, ValueError):
        raise HTTPException(503, "The attachment storage is unavailable. Please try again.") from None
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise HTTPException(404, "The stored attachment is unavailable.") from None
        raise HTTPException(503, "The attachment storage is unavailable. Please try again.") from None


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=50000)
    request_key: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def reply_out(reply):
    return {"id": reply.id, "messageId": reply.message_id, "accountId": reply.account_id,
        "actorId": reply.actor_id, "body": reply.body, "status": reply.status,
        "lastError": reply.last_error, "createdAt": reply.created_at, "sentAt": reply.sent_at,
        "requestKey": reply.request_key}


@router.get("/messages/{message_id}/replies")
@writes_on_get
def replies(message_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.get(MailMessage, message_id) is None:
        raise HTTPException(404, "Message not found")
    rows = list(db.scalars(select(MailReply).where(MailReply.message_id == message_id).order_by(MailReply.created_at)))
    for row in rows:
        if row.status in {"pending", "sending"} and row.created_at < datetime.now(timezone.utc) - timedelta(minutes=3):
            row.status, row.last_error = "unknown", "The sending process ended without a confirmed result. Check Sent mail before composing another reply."
    db.commit()
    return [reply_out(row) for row in rows]


@router.post("/messages/{message_id}/reply")
def reply(message_id: str, body: ReplyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    existing = db.scalar(select(MailReply).where(MailReply.request_key == body.request_key))
    if existing:
        if existing.message_id != message_id or existing.actor_id != user.id or existing.body != body.body:
            raise HTTPException(409, "This reply was already sent. Refresh the conversation to see it.")
        return reply_out(existing)
    message = db.get(MailMessage, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    account = db.scalar(select(MailAccount).where(MailAccount.id == message.account_id).with_for_update())
    if account.disconnected_at:
        raise HTTPException(409, "Reconnect the mailbox this email came from before you reply.")
    try:
        config = mail_providers.account_config(db, get_settings(), account)
        valid_address(message.reply_to or message.from_address)
    except MailAdapterError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    row = MailReply(message_id=message_id, account_id=account.id, actor_id=user.id,
        request_key=body.request_key, body=body.body, status="sending")
    db.add(row)
    try:
        db.commit()  # Persist intent before any provider request; retries reuse this key.
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(select(MailReply).where(MailReply.request_key == body.request_key))
        if duplicate and duplicate.message_id == message_id and duplicate.actor_id == user.id and duplicate.body == body.body:
            return reply_out(duplicate)
        raise HTTPException(409, "This reply was already sent.") from None
    try:
        row.provider_message_id = send_reply(config, message, body.body, row.id)
        row.status, row.sent_at = "sent", datetime.now(timezone.utc)
    except MailSendError as exc:
        row.status, row.last_error = "unknown" if exc.unknown else "failed", str(exc)
    db.add(AuditEntry(actor_id=user.id, action="Email reply " + row.status, target=account.address,
        entity_type="mail_reply", entity_id=row.id, meta={"message_id": message.id, "status": row.status}))
    db.commit()
    return reply_out(row)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: Literal["link", "ignore"]
    reason: str = Field(min_length=1, max_length=2000)
    candidate_id: str | None = Field(default=None, max_length=36)


@router.post("/jobs/{job_id}/review")
def review_job(job_id: str, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    from app.api.routers.candidates import lock_assignment_cursor
    from app.services.document_person_split import ensure_single_person_source
    from app.services.intake_acknowledgements import queue_intake_acknowledgement
    lock_assignment_cursor(db)
    candidate = None
    if body.decision == "link":
        if not body.candidate_id:
            raise HTTPException(422, "Choose the existing candidate whose details you have checked.")
        candidate = db.scalar(select(Candidate).where(Candidate.id == body.candidate_id).with_for_update())
        if not candidate:
            raise HTTPException(404, "Candidate not found")
    job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "Ingestion job not found")
    if job.status not in {"needs_review", "failed", "ignored"}:
        raise HTTPException(409, "This email is already being processed or is done. Refresh the page to see where it stands.")
    message = db.get(MailMessage, job.message_id)
    ensure_single_person_source(db, "email", message.id)
    evidence = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id == message.id)))
    linked = 0
    if candidate:
        for original in evidence:
            documents = (job.state or {}).get(original.id, {}).get("documents", {})
            links = [{"slot": "source_bundle", "file_name": original.file_name, "size_kb": original.size_kb,
                "object_key": original.object_key, "content_hash": original.content_hash,
                "content_type": "application/octet-stream"}]
            links.extend({"slot": kind, **document} for kind, document in documents.items() if kind in {"resume", "passport", "aadhaar"})
            for item in links:
                if not item.get("object_key"):
                    continue
                duplicates = select(Attachment.id).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                    Attachment.slot == item["slot"], Attachment.content_hash == item["content_hash"] if item.get("content_hash") else Attachment.object_key == item["object_key"])
                if db.scalar(duplicates):
                    continue
                version = (db.scalar(select(func.max(Attachment.version)).where(Attachment.entity_type == "candidate",
                    Attachment.entity_id == candidate.id, Attachment.slot == item["slot"])) or 0) + 1
                db.add(Attachment(entity_type="candidate", entity_id=candidate.id, uploaded_by=user.id, version=version,
                    **{name: item.get(name) for name in ("slot", "file_name", "size_kb", "object_key", "content_hash", "content_type")}))
                db.flush(); linked += 1
        message.candidate_id, job.candidate_id, job.status = candidate.id, candidate.id, "completed"
        queue_intake_acknowledgement(db, "email", message.id)
        db.add(AuditEntry(actor_id=user.id, action="Linked reviewed email evidence", target=candidate.name,
            entity_type="candidate", entity_id=candidate.id, meta={"job_id": job.id, "message_id": message.id, "reason": body.reason, "documents": linked}))
    else:
        job.status = "ignored"
    job.last_error = None
    job.state = {**(job.state or {}), "manual_review": {"decision": body.decision, "reason": body.reason,
        "actor_id": user.id, "at": datetime.now(timezone.utc).isoformat(), "candidate_id": body.candidate_id if candidate else None}}
    db.add(AuditEntry(actor_id=user.id, action="Reviewed email receipt", target=message.subject or "Email receipt",
        entity_type="email_job", entity_id=job.id, meta=job.state["manual_review"]))
    db.commit()
    return {"status": job.status, "candidateId": job.candidate_id, "documentsLinked": linked}


@router.get("/candidates/{candidate_id}/evidence")
def candidate_email_history(candidate_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from app.db.models import DocumentPersonGroup, MultiPersonIntake
    from sqlalchemy import or_
    statement = (select(MailMessage.id, MailMessage.subject, MailMessage.from_address, MailMessage.received_at, MailAccount.address,
        EmailIngestionJob.state["manual_review"].label("review"))
        .join(MailAccount, MailAccount.id == MailMessage.account_id)
        .outerjoin(EmailIngestionJob, EmailIngestionJob.message_id == MailMessage.id)
        .where(or_(MailMessage.candidate_id == candidate_id, select(DocumentPersonGroup.id)
            .join(MultiPersonIntake, MultiPersonIntake.id == DocumentPersonGroup.bundle_id)
            .where(DocumentPersonGroup.candidate_id == candidate_id,
                   MultiPersonIntake.source_type == "email",
                   MultiPersonIntake.source_id == MailMessage.id,
                   MultiPersonIntake.status == "completed").exists()))
        .order_by(MailMessage.received_at.desc(), MailMessage.id.desc()).limit(100))
    known, rows = parallel_reads(
        db,
        lambda session: session.scalar(select(Candidate.id).where(Candidate.id == candidate_id)) is not None,
        lambda session: session.execute(statement).all(),
    )
    if not known:
        raise HTTPException(404, "Candidate not found")
    return [{"messageId": message_id, "subject": subject or "(No subject)", "mailbox": mailbox,
        "fromAddress": from_address, "receivedAt": received_at,
        "review": {name: review.get(name) for name in ("decision", "reason", "actor_id", "at")} if isinstance(review, dict) else None}
        for message_id, subject, from_address, received_at, mailbox, review in rows]


class HistoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    since: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: int = Field(default=100, ge=1, le=500)
    auto_ingest: StrictBool = False

    @field_validator("since")
    @classmethod
    def valid_date(cls, value):
        parsed = date.fromisoformat(value)
        if parsed > date.today() or parsed.year < 1970:
            raise ValueError("Choose a past date from 1970 onward.")
        return value


class HistoryMoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)


def history_out(account):
    history = (account.sync_state or {}).get("history")
    return {key: history.get(key) for key in ("id", "since", "status", "processed", "imported", "max_count", "auto_ingest", "last_error")} if history else None


@router.get("/accounts/{account_id}/history")
def history_status(account_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = db.get(MailAccount, account_id)
    if not account:
        raise HTTPException(404, "Mailbox not found")
    return history_out(account)


def history_account(db, account_id):
    account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Mailbox not found")
    if not mail_providers.is_live(account.provider):
        raise HTTPException(409, "This mail provider is no longer supported. Old emails can still be read.")
    if account.disconnected_at:
        raise HTTPException(409, "Reconnect this mailbox before you import its old emails.")
    if not get_settings().email_ingestion_enabled:
        raise HTTPException(503, "Receiving email is switched off on the server. Ask your administrator to turn it on.")
    return account


@router.post("/accounts/{account_id}/history", status_code=202)
def start_history(account_id: str, body: HistoryIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = history_account(db, account_id)
    cursor = dict(account.sync_state or {})
    if cursor.get("history", {}).get("status") in {"queued", "running"}:
        raise HTTPException(409, "An import of old emails is already running. Pause it before you start another one.")
    if not cursor.get("uid_validity") or "last_uid" not in cursor:
        raise HTTPException(409, "Reconnect this mailbox first so it can start receiving new emails.")
    history = {"id": str(uuid4()), "since": body.since, "max_count": body.limit, "auto_ingest": body.auto_ingest,
        "after_uid": 0, "end_uid": cursor["last_uid"], "processed": 0, "imported": 0, "status": "queued", "last_error": None}
    account.sync_state, account.sync_requested = {**cursor, "history": history}, True
    db.add(AuditEntry(actor_id=user.id, action="Requested historical mail import", target=account.address,
        entity_type="mail_account", entity_id=account.id, meta={"since": body.since, "limit": body.limit, "auto_ingest": body.auto_ingest}))
    db.commit()
    return history_out(account)


@router.post("/accounts/{account_id}/history/resume", status_code=202)
def resume_history(account_id: str, body: HistoryMoreIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = history_account(db, account_id)
    progress = dict((account.sync_state or {}).get("history") or {})
    if progress.get("status") not in {"paused", "error", "limited"}:
        raise HTTPException(409, "There is nothing left to continue in this import.")
    progress.update(status="queued", max_count=progress.get("processed", 0) + body.limit, last_error=None)
    account.sync_state, account.sync_requested = {**account.sync_state, "history": progress}, True
    db.add(AuditEntry(actor_id=user.id, action="Resumed historical mail import", target=account.address,
        entity_type="mail_account", entity_id=account.id, meta={"limit": body.limit, "history_id": progress["id"]}))
    db.commit()
    return history_out(account)


@router.post("/accounts/{account_id}/history/pause")
def pause_history(account_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    account = history_account(db, account_id)
    progress = dict((account.sync_state or {}).get("history") or {})
    if progress.get("status") not in {"queued", "running"}:
        raise HTTPException(409, "No import of old emails is running right now.")
    progress["status"] = "paused"
    account.sync_state = {**account.sync_state, "history": progress}
    db.commit()
    return history_out(account)
