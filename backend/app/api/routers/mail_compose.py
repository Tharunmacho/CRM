"""Compose, private drafts, attachment ownership and explicit send recovery."""
import hashlib
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import AuditEntry, MailAccount, MailAttachment, MailDraft, MailDraftAttachment, MailMessage, MailSendAttempt, User
from app.db.session import get_db
from app.integrations.mail.mailbox import WritableMailbox, MailAdapterError, role_for
from app.integrations.mail.smtp import address, deliver, MailSendError
from app.services.document_storage import file_response, put_bytes, read_bytes
from app.services import mail_drafts as service, mail_providers
from app.services.parallel_reads import parallel_reads

router = APIRouter(tags=["inbox"])


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=36)
    mode: Literal["compose", "reply", "reply_all", "forward", "edit_draft"] = "compose"
    source_message_id: str | None = Field(default=None, max_length=36)


class SaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_version: int = Field(ge=0)
    to: list[str] = Field(default_factory=list, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    bcc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(default="", max_length=990)
    body: str = Field(default="", max_length=100_000)

    @field_validator("to", "cc", "bcc")
    @classmethod
    def recipients(cls, values):
        try:
            return list(dict.fromkeys(address(value.strip()) for value in values))
        except MailSendError:
            raise ValueError("Enter complete email addresses for all recipients.") from None

    @field_validator("subject")
    @classmethod
    def subject_header(cls, value):
        if any(char in value for char in "\r\n\x00"):
            raise ValueError("Subject cannot contain line breaks.")
        return value


@router.get("/drafts")
def drafts(account_id: str | None = None, status: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 200,
    search: Annotated[str, Query(max_length=200)] = "",
    db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(MailDraft).where(MailDraft.actor_id == user.id, MailDraft.status != "discarded")
    if account_id:
        query = query.where(MailDraft.account_id == account_id)
    if status and status != "all":
        query = query.where(MailDraft.status == status)
    if search.strip():
        pattern = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(*[column.ilike(pattern, escape="\\") for column in
            (MailDraft.subject, MailDraft.body, cast(MailDraft.to_addresses, Text), cast(MailDraft.cc_addresses, Text), cast(MailDraft.bcc_addresses, Text))]))
    total, rows = parallel_reads(
        db,
        lambda session: session.scalar(select(func.count()).select_from(query.subquery())),
        lambda session: list(session.scalars(query.order_by(MailDraft.updated_at.desc(), MailDraft.id.desc()).offset(offset).limit(limit))),
    )
    ids = [row.id for row in rows]
    attempts: dict[str, list] = {row_id: [] for row_id in ids}
    attachments: dict[str, list] = {row_id: [] for row_id in ids}
    if ids:
        # Two reads for the whole page, not one of each per draft.
        sent, files = parallel_reads(
            db,
            lambda session: list(session.scalars(select(MailSendAttempt).where(MailSendAttempt.draft_id.in_(ids)).order_by(MailSendAttempt.created_at.desc()))),
            lambda session: list(session.scalars(select(MailDraftAttachment).where(MailDraftAttachment.draft_id.in_(ids)).order_by(MailDraftAttachment.id))),
        )
        for item in sent:
            attempts[item.draft_id].append(item)
        for item in files:
            attachments[item.draft_id].append(item)
    db.add_all(rows)  # the reads ran in their own sessions; a state fix below is saved through this one
    db.add_all([item for group in attempts.values() for item in group])
    for row in rows:
        service.expire_sending(db, row, attempts[row.id])
    return {"drafts": [service.draft_out(db, row, attachments[row.id]) for row in rows], "total": total, "offset": offset, "limit": limit, "hasMore": offset + len(rows) < total}


@router.post("/drafts", status_code=201)
def create(body: CreateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    account = db.get(MailAccount, body.account_id)
    if not account:
        raise HTTPException(404, "Mailbox not found")
    try:
        config = mail_providers.account_config(db, get_settings(), account)
    except MailAdapterError as exc:
        raise HTTPException(409, str(exc)) from None
    source = None
    if body.mode != "compose":
        source = db.scalar(select(MailMessage).where(MailMessage.id == body.source_message_id,
            MailMessage.account_id == account.id).with_for_update())
        if not source:
            raise HTTPException(422, "Choose a source message from this mailbox.")
    elif body.source_message_id:
        raise HTTPException(422, "A new composition cannot reference a source message.")
    to, cc, bcc, subject, content = [], [], [], "", ""
    if source:
        subject = source.subject or ""
        if body.mode in {"reply", "reply_all"}:
            try:
                to = [address(source.reply_to or source.from_address)]
            except MailSendError as exc:
                raise HTTPException(422, str(exc)) from None
            if body.mode == "reply_all":
                seen = {account.address.casefold(), *(value.casefold() for value in to)}
                cc = []
                for value in [*(source.to_addresses or []), *(source.cc_addresses or [])]:
                    if value.casefold() not in seen:
                        cc.append(value)
                        seen.add(value.casefold())
            # Replies to our own Sent message address its original recipients.
            if to and to[0].casefold() == account.address.casefold():
                to = [value for value in source.to_addresses or [] if value.casefold() != account.address.casefold()]
                cc = [value for value in cc if value.casefold() not in {item.casefold() for item in to}]
            subject = subject if subject.lower().startswith("re:") else "Re: " + subject
            content = "\n\nOn " + str(source.received_at) + ", " + (source.from_address or "sender") + " wrote:\n" + "\n".join("> " + line for line in (source.body or "").splitlines())
        elif body.mode == "forward":
            subject = subject if subject.lower().startswith(("fwd:", "fw:")) else "Fwd: " + subject
            content = "\n\n---------- Forwarded message ----------\nFrom: " + (source.from_address or "") + "\nSubject: " + (source.subject or "") + "\n\n" + (source.body or "")
        else:
            if not source.remote_uid or not source.uid_validity:
                raise HTTPException(409, "Refresh this draft from the mailbox Drafts folder before editing it.")
            try:
                with WritableMailbox(config) as mailbox:
                    folders = mailbox.folders()
                if not any(folder["name"] == source.folder and folder["role"] == "drafts" for folder in folders):
                    raise HTTPException(422, "Only a message in the Drafts folder can be edited as an existing draft.")
            except MailAdapterError as exc:
                raise HTTPException(502, str(exc)) from None
            existing = db.scalar(select(MailDraft).where(MailDraft.account_id == account.id,
                MailDraft.status.not_in(["discarded", "sent"]), or_(and_(MailDraft.source_message_id == source.id, MailDraft.mode == "edit_draft"),
                    and_(MailDraft.remote_uid == source.remote_uid, MailDraft.remote_uid_validity == source.uid_validity,
                        MailDraft.remote_folder == source.folder))))
            if existing:
                if existing.actor_id == user.id:
                    return service.draft_out(db, existing)
                raise HTTPException(409, "Another CRM user is editing this provider draft.")
            to, cc, bcc, content = list(source.to_addresses or []), list(source.cc_addresses or []), list(source.bcc_addresses or []), source.body or ""
    row = MailDraft(account_id=account.id, actor_id=user.id, mode=body.mode, source_message_id=source.id if source else None,
        to_addresses=to, cc_addresses=cc, bcc_addresses=bcc, subject=subject[:990], body=content[:100_000], status="draft", lock_version=0)
    if body.mode == "edit_draft":
        row.remote_folder, row.remote_uid, row.remote_uid_validity, row.remote_state = source.folder, source.remote_uid, source.uid_validity, "synced"
    db.add(row)
    db.flush()
    if source and body.mode in {"forward", "edit_draft"}:
        originals = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id == source.id)))
        total = 0
        for item in originals:
            if not item.object_key:
                raise HTTPException(409, "A source attachment is unavailable. Refresh the original message first.")
            try:
                data = read_bytes(get_settings(), item.object_key)
            except Exception:
                raise HTTPException(503, "A source attachment could not be read. The original message has been retained.") from None
            total += len(data)
            if total > service.MAX_TOTAL_BYTES or len(originals) > service.MAX_ATTACHMENTS:
                raise HTTPException(422, "The source attachments exceed the 18 MB/20-file compose limit.")
            import mimetypes
            db.add(MailDraftAttachment(draft_id=row.id, file_name=item.file_name,
                content_type=mimetypes.guess_type(item.file_name)[0] or "application/octet-stream", size_bytes=len(data),
                object_key=item.object_key, content_hash=item.content_hash or hashlib.sha256(data).hexdigest()))
    db.commit()
    return service.draft_out(db, row)


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id)
    service.expire_sending(db, row)
    return service.draft_out(db, row)


@router.put("/drafts/{draft_id}")
def save(draft_id: str, body: SaveIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id, lock=True)
    service.check_edit(row, body.lock_version)
    if len(body.to) + len(body.cc) + len(body.bcc) > 100:
        raise HTTPException(422, "Use at most 100 recipients per message.")
    row.to_addresses, row.cc_addresses, row.bcc_addresses = body.to, body.cc, body.bcc
    row.subject, row.body, row.status, row.lock_version = body.subject, body.body, "draft", row.lock_version + 1
    row.remote_state, row.last_error = "pending", None
    db.commit()
    row = service.sync_draft(db, get_settings(), row)
    return service.draft_out(db, row)


@router.post("/drafts/{draft_id}/sync")
def sync(draft_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id)
    row = service.sync_draft(db, get_settings(), row)
    return service.draft_out(db, row)


@router.post("/drafts/{draft_id}/attachments")
async def upload(draft_id: str, file: UploadFile = File(...), lock_version: int = Form(...),
    db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id, lock=True)
    service.check_edit(row, lock_version)
    files = service.attachment_rows(db, row.id)
    data = await file.read(service.MAX_TOTAL_BYTES + 1)
    if not data or len(files) >= service.MAX_ATTACHMENTS or len(data) + sum(item.size_bytes for item in files) > service.MAX_TOTAL_BYTES:
        raise HTTPException(422, "Use nonempty attachments, at most 20 files totaling 18 MB.")
    name = PurePosixPath(PureWindowsPath(file.filename or "attachment.bin").name).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:255] or "attachment.bin"
    digest, attachment_id = hashlib.sha256(data).hexdigest(), str(uuid4())
    mime = file.content_type or "application/octet-stream"
    if not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", mime) or len(mime) > 120:
        mime = "application/octet-stream"
    try:
        key = put_bytes(get_settings(), f"mail-drafts/{row.id}/{attachment_id}-{digest}", data, mime)
    except Exception:
        raise HTTPException(503, "Attachment storage is unavailable. Try the upload again.") from None
    db.add(MailDraftAttachment(id=attachment_id, draft_id=row.id, file_name=name, content_type=mime,
        size_bytes=len(data), object_key=key, content_hash=digest))
    row.lock_version, row.remote_state = row.lock_version + 1, "pending"
    db.commit()
    return service.draft_out(db, service.sync_draft(db, get_settings(), row))


@router.delete("/drafts/{draft_id}/attachments/{attachment_id}")
def remove_attachment(draft_id: str, attachment_id: str, lock_version: int,
    db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id, lock=True)
    service.check_edit(row, lock_version)
    item = db.scalar(select(MailDraftAttachment).where(MailDraftAttachment.id == attachment_id, MailDraftAttachment.draft_id == row.id))
    if not item:
        raise HTTPException(404, "Attachment not found")
    db.delete(item)  # Original immutable bytes can also belong to retained inbound evidence.
    row.lock_version, row.remote_state = row.lock_version + 1, "pending"
    db.commit()
    return service.draft_out(db, service.sync_draft(db, get_settings(), row))


@router.get("/drafts/{draft_id}/attachments/{attachment_id}")
def attachment(draft_id: str, attachment_id: str, download: bool = False,
    db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id)
    item = db.scalar(select(MailDraftAttachment).where(MailDraftAttachment.id == attachment_id, MailDraftAttachment.draft_id == row.id))
    if not item:
        raise HTTPException(404, "Attachment not found")
    safe_inline = item.content_type in {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
    try:
        return file_response(get_settings(), item.object_key, item.file_name, item.content_type, download or not safe_inline)
    except FileNotFoundError:
        raise HTTPException(404, "Attachment bytes not found") from None
    except Exception:
        raise HTTPException(503, "Attachment storage is unavailable") from None


@router.delete("/drafts/{draft_id}")
def discard(draft_id: str, lock_version: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id, lock=True)
    service.check_edit(row, lock_version)
    config = service.config_for(db, get_settings(), row)
    if row.remote_uid:
        try:
            with WritableMailbox(config) as mailbox:
                mailbox.remove_draft(row.remote_folder, row.remote_uid, row.remote_uid_validity)
        except MailAdapterError as exc:
            row.remote_state, row.last_error = "error", str(exc)
            db.commit()
            raise HTTPException(502, "The provider draft could not be removed. Your CRM draft is retained for retry.") from None
    row.status, row.lock_version = "discarded", row.lock_version + 1
    db.commit()
    return service.draft_out(db, row)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_version: int = Field(ge=0)
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/drafts/{draft_id}/send")
def send(draft_id: str, body: SendIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id, lock=True)
    previous = db.scalar(select(MailSendAttempt).where(MailSendAttempt.request_key == body.request_key))
    if previous:
        if previous.draft_id != row.id or previous.actor_id != user.id or previous.snapshot.get("lock_version") != body.lock_version:
            raise HTTPException(409, "This send request identifier belongs to a different draft version.")
        service.expire_sending(db, row)
        return {"attempt": service.attempt_out(previous), "draft": service.draft_out(db, row)}
    service.check_edit(row, body.lock_version)
    if not row.to_addresses and not row.cc_addresses and not row.bcc_addresses:
        raise HTTPException(422, "Add at least one recipient before sending.")
    config = service.config_for(db, get_settings(), row)
    attempt_id = str(uuid4())
    provider_id = f"<send-{attempt_id}@crm-mail.local>"
    try:
        outgoing, recipients = service.envelope(db, get_settings(), row, config, provider_id)
    except MailAdapterError as exc:
        raise HTTPException(422, str(exc)) from None
    attempt = MailSendAttempt(id=attempt_id, draft_id=row.id, actor_id=user.id, request_key=body.request_key,
        snapshot={"lock_version": row.lock_version, "to": row.to_addresses, "cc": row.cc_addresses,
            "bcc": row.bcc_addresses, "subject": row.subject, "body": row.body,
            "attachments": [{"id": item.id, "object_key": item.object_key, "content_hash": item.content_hash,
                "file_name": item.file_name, "content_type": item.content_type, "size_bytes": item.size_bytes}
                for item in service.attachment_rows(db, row.id)]},
        status="sending", provider_message_id=provider_id)
    db.add(attempt)
    row.status, row.last_error = "sending", None
    try:
        db.commit()  # Send intent and recipients become durable before contacting SMTP.
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This send request was already recorded. Check draft status.") from None
    try:
        refused = deliver(config, outgoing, recipients)
        attempt.status, attempt.sent_at = ("partial" if refused else "sent"), service.now()
        attempt.refused_recipients = refused
        attempt.last_error = "Some recipients were refused. Accepted recipients must not be sent another copy automatically." if refused else None
        row.status = "unknown" if refused else "sent"
    except MailSendError as exc:
        attempt.status = "unknown" if exc.unknown else "failed"
        attempt.last_error, row.status = str(exc), attempt.status
    row.last_error = attempt.last_error
    db.add(AuditEntry(actor_id=user.id, action="Mail send " + attempt.status, entity_type="mail_send", entity_id=attempt.id,
        meta={"draft_id": row.id, "account_id": row.account_id, "status": attempt.status, "recipient_count": len(recipients)}))
    db.commit()
    if attempt.status == "sent":
        service.remove_remote_draft(db, row, config)
    return {"attempt": service.attempt_out(attempt), "draft": service.draft_out(db, row)}


@router.get("/drafts/{draft_id}/attempts")
def attempts(draft_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id)
    return {"attempts": [service.attempt_out(item) for item in service.expire_sending(db, row)]}


@router.post("/drafts/{draft_id}/reconcile")
def reconcile(draft_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = service.owned(db, draft_id, user.id, lock=True)
    attempts = service.expire_sending(db, row)
    attempt = attempts[0] if attempts else None
    if not attempt or attempt.status not in {"unknown", "sending"}:
        return {"attempt": service.attempt_out(attempt) if attempt else None, "draft": service.draft_out(db, row)}
    config = service.config_for(db, get_settings(), row)
    try:
        with WritableMailbox(config) as mailbox:
            sent_folder = next((folder["name"] for folder in mailbox.folders() if folder["role"] == "sent"), None)
            if not sent_folder:
                raise MailAdapterError("sent_folder", "Make the Sent folder visible to IMAP in the mailbox settings to check this send.")
            _, uids = mailbox.find_message_id(sent_folder, attempt.provider_message_id)
            if uids:
                attempt.status, row.status, attempt.sent_at = "sent", "sent", service.now()
                attempt.last_error = "The message is present in the Sent folder. Individual recipient delivery cannot be reconstructed from Sent alone."
                row.last_error = attempt.last_error
            else:
                row.last_error = "No matching Sent message was found yet. The send remains unconfirmed; no message was resent."
        db.commit()
        if row.status == "sent":
            service.remove_remote_draft(db, row, config)
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"attempt": service.attempt_out(attempt), "draft": service.draft_out(db, row)}
