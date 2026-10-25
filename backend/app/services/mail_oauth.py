"""Server-held OAuth grants for IMAP/SMTP; public responses never contain tokens."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.db.models import MailAccount, MailOAuthState
from app.integrations.mail.imap import IMAPMailbox, MailAdapterError, MailConfig, mailbox_source_id, require_cursor_source
from app.services.mail_config import configured_mailboxes
from app.services.mail_storage import storage_identity


def utcnow():
    return datetime.now(timezone.utc)


def cipher(settings):
    try:
        return Fernet((getattr(settings, "mail_token_encryption_key", None) or "").encode())
    except (ValueError, TypeError):
        raise MailAdapterError("oauth_configuration", "Mailbox connections are not set up on this server yet. Ask your system administrator to finish the setup, then try again.") from None


def encrypt(settings, value: dict) -> str:
    return cipher(settings).encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()


def decrypt(settings, value: str) -> dict:
    try:
        return json.loads(cipher(settings).decrypt(value.encode()))
    except (InvalidToken, ValueError, TypeError, AttributeError):
        raise MailAdapterError("oauth_tokens", "We could not read the saved sign-in details for this mailbox. Connect the mailbox again to fix this.") from None


def provider_config(settings, provider: str):
    if provider not in {"gmail", "microsoft"}:
        raise MailAdapterError("oauth_provider", "Choose Gmail or Microsoft 365.")
    client = getattr(settings, f"{provider}_client_id", None)
    secret = getattr(settings, f"{provider}_client_secret", None)
    redirect = getattr(settings, f"{provider}_redirect_uri", None)
    if not client or not secret or not redirect:
        raise MailAdapterError("oauth_configuration", f"{'Gmail' if provider == 'gmail' else 'Microsoft 365'} sign-in is not set up on this server yet. Ask your system administrator to finish the setup, then try again.")
    parsed = urlparse(redirect)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})) or parsed.query or parsed.fragment or parsed.username:
        raise MailAdapterError("oauth_configuration", "The sign-in return address on this server is not set up correctly. Ask your system administrator to fix it, then try again.")
    if parsed.path != f"/api/inbox/oauth/{provider}/callback":
        raise MailAdapterError("oauth_configuration", "The sign-in return address on this server is not set up correctly. Ask your system administrator to fix it, then try again.")
    cipher(settings)
    tenant = getattr(settings, "microsoft_tenant", "common")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", tenant):
        raise MailAdapterError("oauth_configuration", "Microsoft 365 sign-in is not set up correctly on this server. Ask your system administrator to fix it, then try again.")
    return {
        "client_id": client, "client_secret": secret, "redirect_uri": redirect,
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth" if provider == "gmail" else f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://oauth2.googleapis.com/token" if provider == "gmail" else f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "scope": "https://mail.google.com/" if provider == "gmail" else "offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send",
    }


def readiness(settings):
    result = []
    for provider in ("gmail", "microsoft"):
        try:
            provider_config(settings, provider)
            result.append({"provider": provider, "ready": True, "message": "Ready to connect"})
        except MailAdapterError as exc:
            result.append({"provider": provider, "ready": False, "message": str(exc)})
    return result


def begin(db, settings, provider, address, user_id, session_id, auto_ingest=True):
    config = provider_config(settings, provider)
    if not session_id:
        raise MailAdapterError("oauth_session", "Sign in again before connecting a mailbox.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.add(MailOAuthState(state_hash=hashlib.sha256(state.encode()).hexdigest(), user_id=user_id,
        provider=provider, address=address, verifier_encrypted=encrypt(settings, {
            "verifier": verifier, "session": hashlib.sha256(session_id.encode()).hexdigest(), "auto_ingest": auto_ingest,
        }), expires_at=utcnow() + timedelta(minutes=10)))
    db.commit()
    params = {"response_type": "code", "client_id": config["client_id"], "redirect_uri": config["redirect_uri"],
        "scope": config["scope"], "state": state, "login_hint": address,
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
        "code_challenge_method": "S256"}
    if provider == "gmail":
        params.update(access_type="offline", prompt="consent")
    else:
        params.update(response_mode="query", prompt="select_account")
    return config["authorize"] + "?" + urlencode(params)


def exchange(settings, provider, payload, old_refresh=None):
    config = provider_config(settings, provider)
    try:
        response = httpx.post(config["token"], data={"client_id": config["client_id"], "client_secret": config["client_secret"], **payload}, timeout=20)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise ValueError()
        token = {"access_token": result["access_token"], "refresh_token": result.get("refresh_token") or old_refresh,
            "expires_at": (utcnow() + timedelta(seconds=max(60, int(result.get("expires_in", 3600))))).isoformat()}
        if not token["refresh_token"]:
            raise MailAdapterError("oauth_offline", "Your mail provider did not allow us to keep checking this mailbox. Connect again and approve all the access it asks for.")
        return token
    except MailAdapterError:
        raise
    except (httpx.HTTPError, ValueError, TypeError):
        raise MailAdapterError("oauth_exchange", "We could not get permission to use this mailbox. Connect it again, or ask the mailbox owner to check that access is allowed.") from None


def oauth_mail_config(provider, address, access_token):
    return MailConfig(host="imap.gmail.com" if provider == "gmail" else "outlook.office365.com", port=993,
        username=address, password="", access_token=access_token,
        smtp_host="smtp.gmail.com" if provider == "gmail" else "smtp.office365.com", smtp_port=587)


def finish(db, settings, provider, state, code, user_id, session_id, denied=False):
    row = db.scalar(select(MailOAuthState).where(MailOAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()).with_for_update())
    if not row or row.provider != provider or row.user_id != user_id or row.consumed_at or row.expires_at <= utcnow():
        db.rollback()
        raise MailAdapterError("oauth_state", "This mailbox connection has expired or was already used. Start connecting the mailbox again.")
    saved = decrypt(settings, row.verifier_encrypted)
    if not session_id or not secrets.compare_digest(saved["session"], hashlib.sha256(session_id.encode()).hexdigest()):
        db.rollback()
        raise MailAdapterError("oauth_session", "Finish connecting the mailbox in the same browser where you started, and stay signed in.")
    address = row.address
    row.consumed_at = utcnow()
    db.commit()  # Consume before exchanging; a callback cannot replay an authorization code.
    if denied or not code:
        raise MailAdapterError("oauth_denied", "Connecting the mailbox was cancelled. Nothing was connected.")
    config = provider_config(settings, provider)
    token = exchange(settings, provider, {"grant_type": "authorization_code", "code": code,
        "redirect_uri": config["redirect_uri"], "code_verifier": saved["verifier"]})
    mailbox_config = oauth_mail_config(provider, address, token["access_token"])
    with IMAPMailbox(mailbox_config) as mailbox:
        cursor = mailbox.probe()  # Confirms the grant actually permits this requested mailbox.
    account = db.scalar(select(MailAccount).where(MailAccount.address == address).with_for_update())
    if account:
        if account.provider != provider:
            db.rollback()
            raise MailAdapterError("oauth_source", "This address is already connected through another mail provider. Use that connection, or connect a different address.")
        require_cursor_source(account.sync_state or {}, mailbox_config)
        if str(account.sync_state.get("uid_validity")) != cursor.uid_validity:
            db.rollback()
            raise MailAdapterError("oauth_cursor", "This mailbox was reset by the mail provider, so we cannot carry on where we stopped. Your saved emails are safe. Contact your administrator for help.")
    else:
        account = MailAccount(address=address, provider=provider, sync_state={"uid_validity": cursor.uid_validity,
            "last_uid": cursor.last_uid, "source_id": mailbox_source_id(mailbox_config),
            "storage_id": storage_identity(settings.storage_dir, settings)})
        db.add(account)
    account.secrets = {"encrypted": encrypt(settings, token)}
    account.disconnected_at, account.last_error = None, None
    account.connected_by, account.auto_ingest, account.sync_requested = user_id, saved["auto_ingest"], True
    db.commit()
    return account


def account_config(db, settings, account):
    if account.disconnected_at:
        raise MailAdapterError("disconnected", "This mailbox is disconnected. An administrator can reconnect it.")
    if account.provider == "imap":
        config = configured_mailboxes(settings).get(account.address)
        if config is None:
            raise MailAdapterError("mail_configuration", "The sign-in details for this mailbox are missing. An administrator can reconnect it.")
        return config
    # Token refresh is serialized for this mailbox only, so rotated refresh tokens
    # cannot overwrite each other. The provider request has a bounded timeout.
    account = db.scalar(select(MailAccount).where(MailAccount.id == account.id).with_for_update().execution_options(populate_existing=True))
    if account.disconnected_at:
        raise MailAdapterError("disconnected", "This mailbox is disconnected. An administrator can reconnect it.")
    token = decrypt(settings, (account.secrets or {}).get("encrypted", ""))
    try:
        expires = datetime.fromisoformat(token["expires_at"])
    except (ValueError, KeyError, TypeError):
        raise MailAdapterError("oauth_tokens", "The saved sign-in details for this mailbox are incomplete. Connect the mailbox again.") from None
    if expires <= utcnow() + timedelta(minutes=2):
        token = exchange(settings, account.provider, {"grant_type": "refresh_token", "refresh_token": token.get("refresh_token"),
            "scope": provider_config(settings, account.provider)["scope"]}, old_refresh=token.get("refresh_token"))
        account.secrets = {"encrypted": encrypt(settings, token)}
    config = oauth_mail_config(account.provider, account.address, token["access_token"])
    db.commit()
    return config
