"""Connect mailboxes: Gmail and Microsoft 365 (OAuth), Zoho and any IMAP/SMTP server.

There is no limit on the number of connected accounts. Each is an independent
``MailAccount`` row whose credentials are encrypted at rest and never returned.
"""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import require_role
from app.core.security import SESSION_COOKIE
from app.db.models import AuditEntry, MailAccount, Role, User
from app.db.session import get_db, writes_on_get
from app.integrations.mail.imap import (
    UIDVALIDITY_RECOVERY_REQUIRED, MailAdapterError, mailbox_source_id, require_cursor_source,
)
from app.integrations.mail.mailbox import WritableMailbox
from app.integrations.mail.smtp import address as email_address
from app.services import mail_oauth, mail_providers
from app.services.mail_providers import CustomServer, LABELS
from app.services.mail_storage import storage_identity

router = APIRouter(tags=["inbox"])
admin = require_role(Role.admin)

# Zoho and generic IMAP mailboxes share one credential model, so an address can move
# between them (for example a custom domain hosted on Zoho) without losing its cursor.
PASSWORD_PROVIDERS = {"zoho", "imap"}


def audit(db, user, action, account, detail):
    db.add(AuditEntry(actor_id=user.id, action=action, target=account.address, entity_type="mail_account",
        entity_id=account.id, meta={"detail": detail}))


def account_summary(row):
    return {"id": row.id, "address": row.address, "provider": row.provider, "autoIngest": row.auto_ingest,
        "configured": True, "connectionStatus": "connected", "lastSyncAt": row.last_sync_at,
        "disconnectedAt": None, "lastError": None}


def save_password_account(db: Session, settings, user: User, *, provider: str, address: str, config, envelope: str,
        auto_ingest: bool, folders: list, cursor) -> MailAccount:
    """Create or reconnect a Zoho/IMAP account without ever skipping its saved cursor."""
    row = db.scalar(select(MailAccount).where(MailAccount.address == address).with_for_update())
    if row:
        if row.provider not in PASSWORD_PROVIDERS:
            raise HTTPException(409, f"This address is already connected through {LABELS.get(row.provider, 'another provider')}. "
                "Disconnect it first, or use a different address.")
        try:
            require_cursor_source(row.sync_state or {}, config)
        except MailAdapterError as exc:
            raise HTTPException(409, str(exc)) from None
        if str(row.sync_state.get("uid_validity")) != cursor.uid_validity:
            raise HTTPException(409, UIDVALIDITY_RECOVERY_REQUIRED)
    else:
        row = MailAccount(address=address, provider=provider, sync_state={"source_id": mailbox_source_id(config),
            "last_uid": cursor.last_uid, "uid_validity": cursor.uid_validity,
            "storage_id": storage_identity(settings.storage_dir, settings)})
        db.add(row)
        db.flush()
    row.provider, row.secrets = provider, {"encrypted": envelope}
    row.auto_ingest, row.connected_by, row.disconnected_at = auto_ingest, user.id, None
    row.last_error, row.sync_requested = None, True
    row.sync_state = {**row.sync_state, "folders": folders}
    audit(db, user, f"Connected a {LABELS[provider]} mailbox", row, f"Connected {LABELS[provider]}; credentials encrypted and excluded from audit data.")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This mailbox was connected in another request. Refresh the account list.") from None
    return row


@router.get("/providers")
def providers(user: User = Depends(admin)):
    return {"providers": mail_providers.readiness(get_settings())}


class ImapConnectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    address: str = Field(max_length=255)
    password: SecretStr
    imap_host: str = Field(min_length=3, max_length=253)
    imap_port: Literal[993, 143] = 993
    smtp_host: str = Field(min_length=3, max_length=253)
    smtp_port: Literal[465, 587] = 587
    auto_ingest: StrictBool = True


@router.post("/accounts/imap")
def connect_imap(body: ImapConnectIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    settings = get_settings()
    try:
        address = email_address(body.address).casefold()
        password = body.password.get_secret_value()
        server = CustomServer(body.imap_host, body.imap_port, body.smtp_host, body.smtp_port)
        config = mail_providers.make_custom_config(address, password, server)
        envelope = mail_oauth.encrypt(settings, {"password": password, "imap_host": config.host, "imap_port": config.port,
            "smtp_host": config.smtp_host, "smtp_port": config.smtp_port})
        with WritableMailbox(config) as mailbox:
            cursor = mailbox.probe()
            folders = mailbox.folders()
    except MailAdapterError as exc:
        raise HTTPException(422 if exc.code.startswith(("invalid_", "oauth_configuration")) else 502, str(exc)) from None
    row = save_password_account(db, settings, user, provider="imap", address=address, config=config, envelope=envelope,
        auto_ingest=body.auto_ingest, folders=folders, cursor=cursor)
    return account_summary(row)


class OAuthStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    address: str = Field(max_length=255)
    auto_ingest: StrictBool = True


@router.post("/oauth/{provider}/start")
def oauth_start(provider: Literal["gmail", "microsoft"], body: OAuthStartIn, db: Session = Depends(get_db),
        user: User = Depends(admin), session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    try:
        address = email_address(body.address).casefold()
        url = mail_oauth.begin(db, get_settings(), provider, address, user.id, session_id, body.auto_ingest)
    except MailAdapterError as exc:
        raise HTTPException(422 if exc.code == "oauth_configuration" else 400, str(exc)) from None
    return {"authorizationUrl": url}


def back_to_inbox(**params) -> RedirectResponse:
    base = get_settings().app_base_url.rstrip("/")
    query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
    return RedirectResponse(f"{base}/inbox?{query}", status_code=303)


@router.get("/oauth/{provider}/callback", include_in_schema=False)
@writes_on_get
def oauth_callback(provider: Literal["gmail", "microsoft"], state: str = Query(default="", max_length=512),
        code: str = Query(default="", max_length=4096), error: str = Query(default="", max_length=200),
        db: Session = Depends(get_db), user: User = Depends(admin),
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    try:
        row = mail_oauth.finish(db, get_settings(), provider, state, code, user.id, session_id, denied=bool(error))
        audit(db, user, f"Connected a {LABELS[provider]} mailbox", row, f"Connected {LABELS[provider]} through OAuth; tokens encrypted and excluded from audit data.")
        db.commit()
    except MailAdapterError as exc:
        db.rollback()
        return back_to_inbox(mail_error=str(exc))
    return back_to_inbox(mail_connected=row.id)
