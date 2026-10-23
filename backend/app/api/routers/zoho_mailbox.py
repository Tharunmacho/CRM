"""Live two-way mailbox operations (folders, flags, moves) and Zoho connection."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.mail_accounts import account_summary, save_password_account
from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, MailAccount, MailAttachment, MailMessage, Role, User
from app.db.session import get_db, writes_on_get
from app.integrations.mail.imap import provider_message_id
from app.integrations.mail.mailbox import WritableMailbox, MailAdapterError, MailMutationError
from app.integrations.mail.smtp import address as email_address
from app.services import mail_providers, zoho_mail
from app.services.mail_oauth import encrypt
from app.services.mailbox_sync import persist_messages, message_out
from app.services.parallel_reads import parallel_reads

router = APIRouter(tags=["inbox"])
admin = require_role(Role.admin)


def audit(db, user, action, entity_id, detail):
    db.add(AuditEntry(actor_id=user.id, action=action, entity_type="mail_account", entity_id=entity_id, meta={"detail": detail}))


def account(db, account_id):
    row = db.get(MailAccount, account_id)
    if not row:
        raise HTTPException(404, "Mailbox not found")
    try:
        config = mail_providers.account_config(db, get_settings(), row)
    except MailAdapterError as exc:
        raise HTTPException(409, str(exc)) from None
    return row, config


class ConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(max_length=255)
    password: SecretStr
    region: Literal["com", "in", "eu", "com.au", "jp", "ca", "com.cn", "sa"] = "com"
    account_type: Literal["organization", "personal"] = "organization"
    imap_host: str | None = Field(default=None, max_length=120)
    smtp_host: str | None = Field(default=None, max_length=120)
    smtp_port: Literal[465, 587] = 465
    auto_ingest: StrictBool = True


@router.post("/accounts/zoho")
def connect(body: ConnectIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    settings = get_settings()
    try:
        address = email_address(body.address.strip()).casefold()
        password = body.password.get_secret_value()
        if not password or len(password) > 1000 or any(c in password for c in "\r\n\x00"):
            raise MailAdapterError("zoho_password", "Enter a valid Zoho app password.")
        config = zoho_mail.make_config(address, password, region=body.region, account_type=body.account_type,
            imap_host=body.imap_host, smtp_host=body.smtp_host, smtp_port=body.smtp_port)
        envelope = encrypt(settings, {"password": password, "region": body.region, "account_type": body.account_type,
            "imap_host": config.host, "smtp_host": config.smtp_host, "smtp_port": config.smtp_port})
        with WritableMailbox(config) as mailbox:
            cursor = mailbox.probe()
            folders = mailbox.folders()
    except MailAdapterError as exc:
        raise HTTPException(422 if exc.code.startswith("zoho_") else 502, str(exc)) from None
    row = save_password_account(db, settings, user, provider="zoho", address=address, config=config, envelope=envelope,
        auto_ingest=body.auto_ingest, folders=folders, cursor=cursor)
    return account_summary(row)


FOLDER_CACHE_SECONDS = 45


@router.get("/accounts/{account_id}/folders")
@writes_on_get
def folders(account_id: str, refresh: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row, config = account(db, account_id)
    state = row.sync_state or {}
    saved_at = state.get("folders_at")
    if not refresh and state.get("folders") and saved_at:
        # Counting every folder takes one server round trip each, so a recent answer is reused.
        if (datetime.now(timezone.utc) - datetime.fromisoformat(saved_at)).total_seconds() < FOLDER_CACHE_SECONDS:
            return {"accountId": account_id, "folders": state["folders"]}
    try:
        with WritableMailbox(config) as mailbox:
            result = mailbox.folders()
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from None
    row = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    row.sync_state = {**row.sync_state, "folders": result, "folders_at": datetime.now(timezone.utc).isoformat()}
    db.commit()
    return {"accountId": account_id, "folders": result}


class FolderIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def folder_name(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Folder names cannot contain control characters.")
        return value


@router.post("/accounts/{account_id}/folders")
def create_folder(account_id: str, body: FolderIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _, config = account(db, account_id)
    try:
        with WritableMailbox(config) as mailbox:
            result = mailbox.folders()
            created = body.name not in {item["name"] for item in result}
            if created:
                mailbox.create_folder(body.name)
                result = mailbox.folders()
                if body.name not in {item["name"] for item in result}:
                    raise MailMutationError("The mailbox has not confirmed the new folder. Refresh before retrying.", unknown=True)
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from None
    row = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    row.sync_state = {**row.sync_state, "folders": result}
    if created:
        audit(db, user, "create_mail_folder", account_id, f"Created mailbox folder {body.name}.")
    db.commit()
    return {"accountId": account_id, "folders": result, "created": created}


@router.get("/accounts/{account_id}/mailbox")
@writes_on_get
def mailbox_page(account_id: str, folder: Annotated[str, Query(min_length=1, max_length=255)] = "INBOX",
    search: Annotated[str, Query(max_length=200)] = "", offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25, unread: bool | None = None,
    db: Session = Depends(get_db), user: User = Depends(current_user)):
    row, config = account(db, account_id)
    try:
        with WritableMailbox(config) as mailbox:
            _, total, incoming = mailbox.page(folder, search=search, offset=offset, limit=limit, unread=unread)
        rows = persist_messages(db, get_settings(), account_id, incoming)
        ids = [item.id for item in rows]
        db.commit()
        from app.services.document_bundle_summary import source_summaries
        bundles, files = parallel_reads(
            db,
            lambda session: source_summaries(session, "email", ids),
            lambda session: list(session.scalars(select(MailAttachment).where(MailAttachment.message_id.in_(ids)))) if ids else [],
        )
        by_message: dict[str, list] = {}
        for item in files:
            by_message.setdefault(item.message_id, []).append(item)
        messages = [message_out(db, item, bundles=bundles, attachments=by_message.get(item.id, [])) for item in rows]
    except MailAdapterError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from None
    return {"messages": messages, "total": total, "offset": offset, "limit": limit, "hasMore": offset + len(messages) < total}


class FlagsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unread: StrictBool | None = None
    starred: StrictBool | None = None


def message_account(db, message_id):
    row = db.get(MailMessage, message_id)
    if not row:
        raise HTTPException(404, "Message not found")
    source, config = account(db, row.account_id)
    if not row.remote_uid or not row.uid_validity or row.remote_deleted:
        raise HTTPException(409, "Refresh this message from its mailbox folder before changing it.")
    return row, source, config


@router.patch("/messages/{message_id}/flags")
def flags(message_id: str, body: FlagsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row, source, config = message_account(db, message_id)
    row = db.scalar(select(MailMessage).where(MailMessage.id == message_id).with_for_update().execution_options(populate_existing=True))
    if row.remote_state in {"moving", "unknown", "flags"}:
        raise HTTPException(409, "Resolve the pending mailbox operation before changing this message.")
    row.remote_state, row.remote_operation = "flags", {"action": "flags", "values": body.model_dump(exclude_none=True)}
    db.commit()
    try:
        with WritableMailbox(config) as mailbox:
            incoming = mailbox.set_flags(row.folder, row.remote_uid, row.uid_validity, unread=body.unread, starred=body.starred)
        row.unread, row.starred, row.remote_state, row.remote_error, row.remote_operation = incoming.unread, incoming.starred, "synced", None, None
    except MailAdapterError as exc:
        row.remote_state, row.remote_error = "unknown" if getattr(exc, "unknown", False) else "error", str(exc)
        db.commit()
        raise HTTPException(502, str(exc)) from None
    db.commit()
    return message_out(db, row)


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(min_length=1, max_length=255)


def move_message(db, user, row, config, destination):
    if row.folder == destination:
        return message_out(db, row)
    row = db.scalar(select(MailMessage).where(MailMessage.id == row.id).with_for_update().execution_options(populate_existing=True))
    if row.remote_state in {"moving", "unknown", "flags"}:
        raise HTTPException(409, "Resolve the pending mailbox operation before moving this message.")
    with WritableMailbox(config) as mailbox:
        available = mailbox.folders()
        if destination not in {f["name"] for f in available}:
            raise HTTPException(422, "Choose an existing mailbox folder.")
        original = row.folder
        row.remote_state, row.remote_operation = "moving", {"action": "move", "target": destination, "source": original,
            "uid": row.remote_uid, "validity": row.uid_validity, "started_at": datetime.now(timezone.utc).isoformat()}
        db.commit()  # Durable intent before issuing a non-repeatable remote operation.
        try:
            outcome = mailbox.move(original, row.remote_uid, row.uid_validity, destination)
            if outcome is None and row.message_id_header:
                cursor, uids = mailbox.find_message_id(destination, row.message_id_header)
                outcome = (cursor.uid_validity, uids[0]) if len(uids) == 1 else None
            if outcome is None:
                raise MailMutationError("The move was accepted but its destination identifier is unconfirmed. Check both folders and reconcile.", unknown=True)
            validity, uid = outcome
            existing = db.scalar(select(MailMessage).where(MailMessage.account_id == row.account_id,
                MailMessage.provider_message_id == provider_message_id(destination, validity, uid), MailMessage.id != row.id))
            if existing:
                # Retain both immutable receipt histories; this row keeps candidate provenance.
                existing.provider_message_id = f"retained:{existing.id}:{uuid4()}"
                existing.remote_deleted = True
                db.flush()
            row.previous_folder, row.folder, row.remote_uid, row.uid_validity = original, destination, uid, validity
            row.provider_message_id = provider_message_id(destination, validity, uid)
            row.remote_state, row.remote_error, row.remote_operation = "synced", None, None
            audit(db, user, "move_mail", row.account_id, f"Moved message {row.id} from {original} to {destination}.")
            db.commit()
        except MailAdapterError as exc:
            row.remote_state = "unknown" if getattr(exc, "unknown", False) else "error"
            row.remote_error = str(exc)
            db.commit()
            raise HTTPException(502, str(exc)) from None
    return message_out(db, row)


@router.post("/messages/{message_id}/move")
def move(message_id: str, body: MoveIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row, _, config = message_account(db, message_id)
    try:
        return move_message(db, user, row, config, body.folder)
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from None


class ActionIn(BaseModel):
    action: Literal["archive", "trash", "restore"]


@router.post("/messages/{message_id}/action")
def action(message_id: str, body: ActionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row, _, config = message_account(db, message_id)
    try:
        with WritableMailbox(config) as mailbox:
            available = mailbox.folders()
            if body.action == "archive" and not any(item["role"] == "archive" for item in available):
                mailbox.create_folder("Archive")
                available = mailbox.folders()
        destination = row.previous_folder if body.action == "restore" else None
        if not destination or destination == row.folder:
            wanted = "inbox" if body.action == "restore" else body.action
            destination = next((item["name"] for item in available if item["role"] == wanted), None)
        if not destination:
            raise HTTPException(409, "This mailbox has no matching folder visible over IMAP. Make it visible to IMAP in the mailbox's folder settings.")
        return move_message(db, user, row, config, destination)
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from None


@router.post("/messages/{message_id}/reconcile")
def reconcile(message_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row, _, config = message_account(db, message_id)
    row = db.scalar(select(MailMessage).where(MailMessage.id == message_id).with_for_update().execution_options(populate_existing=True))
    operation = row.remote_operation or {}
    if operation.get("action") == "flags" and row.remote_state in {"flags", "unknown"}:
        try:
            with WritableMailbox(config) as mailbox:
                mailbox.select_folder(row.folder, row.uid_validity)
                incoming = mailbox.fetch(row.remote_uid, uid_validity=row.uid_validity)
            row.unread, row.starred, row.remote_state, row.remote_error, row.remote_operation = incoming.unread, incoming.starred, "synced", None, None
            db.commit()
            return message_out(db, row)
        except MailAdapterError as exc:
            raise HTTPException(502, str(exc)) from None
    if operation.get("action") != "move" or row.remote_state not in {"moving", "unknown"}:
        return message_out(db, row)
    if not row.message_id_header:
        raise HTTPException(409, "This message has no stable Message-ID. Check source and destination folders before further action.")
    try:
        with WritableMailbox(config) as mailbox:
            target, targets = mailbox.find_message_id(operation["target"], row.message_id_header)
            _, sources = mailbox.find_message_id(operation["source"], row.message_id_header)
        if len(targets) == 1 and not sources:
            destination_id = provider_message_id(operation["target"], target.uid_validity, targets[0])
            existing = db.scalar(select(MailMessage).where(MailMessage.account_id == row.account_id,
                MailMessage.provider_message_id == destination_id, MailMessage.id != row.id))
            if existing:
                existing.provider_message_id, existing.remote_deleted = f"retained:{existing.id}:{uuid4()}", True
                db.flush()
            row.previous_folder, row.folder = operation["source"], operation["target"]
            row.remote_uid, row.uid_validity = targets[0], target.uid_validity
            row.provider_message_id = provider_message_id(row.folder, row.uid_validity, row.remote_uid)
            row.remote_state, row.remote_error, row.remote_operation = "synced", None, None
        elif not targets and sources == [operation["uid"]]:
            row.remote_state, row.remote_error, row.remote_operation = "synced", None, None
        else:
            row.remote_state, row.remote_error = "unknown", "The mailbox still shows an ambiguous move outcome. Review both folders; no automatic retry was made."
        db.commit()
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from None
    return message_out(db, row)
