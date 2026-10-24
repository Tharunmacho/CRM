"""Connected inbox, explicit replies and durable intake controls."""
from datetime import datetime, timezone
from copy import deepcopy
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, load_only

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, EmailIngestionJob, MailAccount, MailAttachment, MailMessage, Role, User
from app.db.session import get_db
from app.integrations.mail.imap import (
    IMAPMailbox, MailAdapterError, UIDVALIDITY_RECOVERY_REQUIRED, mailbox_source_id, require_cursor_source,
)
from app.services.mail_config import configured_mailboxes
from app.services.document_extraction import request_extraction_retry
from app.services.document_person_split import ensure_single_person_source
from app.services.document_bundle_summary import source_summaries
from app.services.ingestion_revision import read_revision
from app.services.parallel_reads import parallel_reads
from app.services.intake_attention import attention_list, unread_counts
from app.services import mail_providers
from app.api.routers.mail_actions import router as actions_router
from app.api.routers.zoho_mailbox import router as zoho_router
from app.api.routers.mail_compose import router as compose_router
from app.api.routers.mail_accounts import router as accounts_router
from app.services.mail_storage import storage_identity

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
router.include_router(actions_router)
router.include_router(zoho_router)
router.include_router(compose_router)
router.include_router(accounts_router)
admin = require_role(Role.admin)
supervisor = require_role(Role.admin, Role.manager)


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


def account_out(account, configured, counts=None, has_secrets=None):
    total, unread = (counts or {}).get(account.id, (0, 0))
    if has_secrets is None:
        has_secrets = bool((account.secrets or {}).get("encrypted"))
    return {
        "id": account.id, "address": account.address, "provider": account.provider,
        "providerLabel": mail_providers.LABELS.get(account.provider, account.provider),
        "autoIngest": account.auto_ingest, "lastSyncAt": account.last_sync_at,
        "messageCount": total, "unreadCount": unread,
        "configured": not account.disconnected_at and mail_providers.is_live(account.provider)
            and (account.provider != "imap" or has_secrets or account.address in configured),
        "connectionStatus": "disconnected" if account.disconnected_at else "error" if account.last_error else "connected",
        "disconnectedAt": account.disconnected_at,
        "lastError": account.last_error,
    }


def audit(db: Session, user: User, action: str, *, target: str | None, entity_type: str, entity_id: str, detail: dict | None = None) -> None:
    """Who did it and when; the entry is saved in the same commit as the change."""
    db.add(AuditEntry(actor_id=user.id, action=action, target=target, entity_type=entity_type, entity_id=entity_id, meta=detail or {}))


RETRYABLE_JOB_STATES = {"failed", "needs_review", "ignored"}


def requeue_job(db: Session, job: EmailIngestionJob, message: MailMessage) -> None:
    """Put a failed, skipped or needs-review job back in the queue (the caller commits)."""
    ensure_single_person_source(db, "email", message.id)
    require_intake_account(db, message.account_id)
    state = deepcopy(job.state or {})
    if state.get("receive_error"):
        raise HTTPException(422, "We could not read this email. Fix the attachment or ask the sender to send it again.")
    job.status, job.last_error, job.attempts = "pending", None, 0
    job.next_attempt_at = datetime.now(timezone.utc)
    for document in state.values():
        if isinstance(document, dict):
            request_extraction_retry(document)
            for key in ("output", "inspection", "inspection_signature", "extractions", "extraction_status", "warnings"):
                document.pop(key, None)
    job.state = state


def require_intake_account(db: Session, account_id: str) -> None:
    account = db.get(MailAccount, account_id)
    if not account:
        raise HTTPException(404, "Mailbox not found")
    # Match the receiving/extraction worker's supported source set. Retained
    # historical receipts can still be read and reviewed without queueing work.
    if not mail_providers.is_live(account.provider):
        raise HTTPException(409, "This mail provider is no longer supported. Old emails can still be read and their attachments downloaded. Connect the mailbox again to import new candidates.")
    if account.disconnected_at:
        raise HTTPException(409, "Reconnect this mailbox before you create a candidate from an email. Saved emails, results and downloads are still available.")


PREVIEW_CHARS = 180
LIST_BODY_CHARS = 600  # enough for the list; the reader loads one message in full


@router.get("")
def inbox(db: Session = Depends(get_db), user: User = Depends(current_user),
          limit: Annotated[int, Query(ge=1, le=100)] = 50,
          offset: Annotated[int, Query(ge=0)] = 0,
          q: Annotated[str, Query(max_length=200)] = "",
          account_ids: str | None = None, unread: bool | None = None, message_id: str | None = None,
          has_attachments: bool | None = None, scope: Literal["inbox", "stored"] = "inbox"):
    # A message list only needs the start of each body; full text is read one message at a time.
    # Everything independent runs together, so the page waits for the slowest query, not the sum.
    filters = [MailMessage.remote_deleted.is_(False)]
    if message_id:
        filters.append(MailMessage.id == message_id)
    elif scope != "stored":
        filters.append(MailMessage.folder == "INBOX")
    if account_ids is not None:
        filters.append(MailMessage.account_id.in_([value for value in account_ids.split(",") if value][:100]))
    if unread is not None:
        filters.append(MailMessage.unread == unread)
    if has_attachments is not None:
        attached = select(MailAttachment.id).where(MailAttachment.message_id == MailMessage.id).exists()
        filters.append(attached if has_attachments else ~attached)
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filters.append(or_(*[column.ilike(pattern, escape="\\") for column in (MailMessage.subject, MailMessage.from_name, MailMessage.from_address, MailMessage.body)]))
    order = (MailMessage.received_at.desc(), MailMessage.id.desc())

    def read_accounts(session):
        return list(session.execute(select(MailAccount, MailAccount.secrets.isnot(None).label("has_secrets")).options(
            defer(MailAccount.secrets), defer(MailAccount.sync_state)).order_by(MailAccount.address)))

    def read_counts(session):
        # Per-account totals feed the account switcher; single-message reads (job polling) skip them.
        if message_id:
            return {}
        return {account_id: (total, unread_count or 0) for account_id, total, unread_count in session.execute(
            select(MailMessage.account_id, func.count(), func.sum(case((MailMessage.unread.is_(True), 1), else_=0)))
            .where(MailMessage.remote_deleted.is_(False), MailMessage.folder == "INBOX").group_by(MailMessage.account_id))}

    def read_page(session):
        if message_id:
            rows = [(m, m.body, 1) for m in session.scalars(select(MailMessage).where(*filters))]
        else:
            rows = [(m, head, count) for m, head, count in session.execute(
                select(MailMessage, func.left(MailMessage.body, LIST_BODY_CHARS), func.count().over())
                .options(defer(MailMessage.body)).where(*filters).order_by(*order).offset(offset).limit(limit))]
        total = rows[0][2] if rows else (0 if offset == 0 else session.scalar(select(func.count()).select_from(select(MailMessage.id).where(*filters).subquery())))
        return rows, total

    # The page's ids are a subquery, so the per-message lookups do not wait for the page itself.
    page_ids = select(MailMessage.id).where(*filters).order_by(*order).offset(0 if message_id else offset).limit(1 if message_id else limit).subquery()
    on_page = select(page_ids.c.id)

    def read_attachments(session):
        return list(session.scalars(select(MailAttachment).where(MailAttachment.message_id.in_(on_page))))

    def read_jobs(session):
        return list(session.scalars(select(EmailIngestionJob).where(EmailIngestionJob.message_id.in_(on_page)).order_by(EmailIngestionJob.updated_at.desc())))

    def read_bundles(session):
        return source_summaries(session, "email", on_page)

    account_rows, counts, (page, total), attachments, jobs, bundles = parallel_reads(
        db, read_accounts, read_counts, read_page, read_attachments, read_jobs, read_bundles)
    messages = [row[0] for row in page]
    bodies = {row[0].id: row[1] or "" for row in page}
    # Administrators configure accounts; other roles never receive credential names/values.
    configured = configs() if user.role == Role.admin else {}
    accounts = [row[0] for row in account_rows]
    heartbeat = max((a.worker_seen_at for a in accounts if not a.disconnected_at and a.worker_seen_at), default=None)
    extraction_heartbeat = max((a.extraction_seen_at for a in accounts if not a.disconnected_at and a.extraction_seen_at), default=None)
    attached_to = {}
    for item in attachments:
        attached_to.setdefault(item.message_id, []).append(item)
    return {
        "accounts": [account_out(account, configured, counts, has_secrets) for account, has_secrets in account_rows],
        "availableAccounts": list(configured) if user.role == Role.admin else [],
        "providers": mail_providers.readiness(get_settings()) if user.role == Role.admin else [],
        "total": total, "offset": offset, "limit": limit, "hasMore": offset + len(messages) < total,
        "scope": scope,
        "messages": [{
            "id": m.id, "accountId": m.account_id, "fromName": m.from_name or m.from_address or "",
            "fromAddress": m.from_address or "", "subject": m.subject or "(No subject)",
            "body": bodies[m.id], "bodyComplete": bool(message_id) or len(bodies[m.id]) < LIST_BODY_CHARS,
            "preview": bodies[m.id][:PREVIEW_CHARS],
            "receivedAt": m.received_at, "unread": m.unread, "candidateId": m.candidate_id,
            "documentBundle": bundles.get(m.id),
            "folder": m.folder, "starred": m.starred, "toAddresses": m.to_addresses or [], "ccAddresses": m.cc_addresses or [],
            "remoteState": m.remote_state, "remoteError": m.remote_error,
            "attachments": [{"id": a.id, "fileName": a.file_name, "sizeKb": a.size_kb or 0,
                             "kind": a.kind or "document", "url": f"/api/inbox/attachments/{a.id}" if a.object_key else None} for a in attached_to.get(m.id, [])],
        } for m in messages],
        "jobs": [{
            "id": j.id, "messageId": j.message_id, "status": j.status, "attempts": j.attempts,
            "lastError": j.last_error, "candidateId": j.candidate_id, "updatedAt": j.updated_at,
            "inspection": j.inspection,
        } for j in jobs],
        "worker": {"enabled": get_settings().email_ingestion_enabled, "lastHeartbeat": heartbeat, "extractionHeartbeat": extraction_heartbeat},
    }


@router.get("/health")
def health(db: Session = Depends(get_db), user: User = Depends(admin)):
    def read_accounts(session):
        return list(session.scalars(select(MailAccount).options(load_only(
            MailAccount.id, MailAccount.address, MailAccount.last_sync_at,
            MailAccount.last_error, MailAccount.auto_ingest, MailAccount.worker_seen_at, MailAccount.extraction_seen_at,
        )).where(MailAccount.disconnected_at.is_(None)).order_by(MailAccount.address)))

    def read_counts(session):
        return session.execute(select(EmailIngestionJob.status, func.count()).group_by(EmailIngestionJob.status)).all()

    accounts, grouped = parallel_reads(db, read_accounts, read_counts)
    counts = {name: 0 for name in (
        "pending", "processing", "retry", "needs_review", "failed", "completed", "ignored",
    )}
    for name, count in grouped:
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
            "extractionHeartbeat": max((a.extraction_seen_at for a in accounts if a.extraction_seen_at), default=None),
        },
    }


@router.get("/revision")
def revision(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Let every signed-in desk detect completed intake without loading mail."""
    return {"revision": read_revision(db)}


@router.get("/pulse")
def pulse(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The two cheap change signals the desk polls, in one request: the intake revision and the unread badge.

    Replaces separate /revision and /unread-count polls: one request, one sign-in check, and the two reads
    run side by side, so a poll costs one database round trip instead of two requests of one each.
    """
    revision_token, unread = parallel_reads(db, read_revision, unread_counts)
    return {"revision": revision_token, "unread": unread}


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Sidebar badge: unread INBOX mail across connected live mailboxes, without loading messages."""
    return unread_counts(db)


@router.get("/attention")
def attention(db: Session = Depends(get_db), user: User = Depends(supervisor)):
    """Failed or needs-review intake across email, WhatsApp and manual uploads (admin retries, manager reads)."""
    return attention_list(db, user)


@router.post("/connect")
def connect(body: ConnectIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    available = configs()
    if not available:
        raise HTTPException(503, "Set IMAP_SERVER, IMAP_PORT, IMAP_USERNAME, IMAP_PASSWORD, IMAP_USE_SSL and IMAP_FOLDER in backend environment settings.")
    address = body.address.strip().lower() if body.address else next(iter(available))
    if address not in available:
        raise HTTPException(422, "This email address is not set up on the server.")
    try:
        mail_providers.validate_config(available[address])
    except MailAdapterError as exc:
        raise HTTPException(422, str(exc)) from None
    existing = db.scalar(select(MailAccount).where(MailAccount.address == address))
    if existing:
        if existing.provider != "imap":
            detail = f"Reconnect this account from Inbox → Accounts using its {mail_providers.LABELS.get(existing.provider, 'original')} connection."
            raise HTTPException(409, detail)
        try:
            require_cursor_source(existing.sync_state or {}, available[address])
        except MailAdapterError as exc:
            raise HTTPException(409, str(exc)) from None
        # Reconnecting must never jump over an existing durable cursor.
        if existing.disconnected_at:
            saved_cursor = dict(existing.sync_state)
            saved_id = existing.id
            db.rollback()
            try:
                with IMAPMailbox(available[address]) as mailbox:
                    current_cursor = mailbox.probe()
                if current_cursor.uid_validity != str(saved_cursor.get("uid_validity")):
                    raise MailAdapterError("uidvalidity_changed", UIDVALIDITY_RECOVERY_REQUIRED)
            except MailAdapterError as exc:
                raise HTTPException(502, str(exc)) from None
            existing = db.scalar(select(MailAccount).where(MailAccount.id == saved_id).with_for_update())
            if not existing or dict(existing.sync_state) != saved_cursor:
                raise HTTPException(409, "The mailbox changed while it was reconnecting. Refresh the page and try again.")
            existing.disconnected_at, existing.last_error, existing.sync_requested = None, None, True
            existing.auto_ingest, existing.connected_by = body.auto_ingest, user.id
            audit(db, user, "Reconnected mailbox", target=existing.address, entity_type="mail_account", entity_id=existing.id, detail={"autoIngest": body.auto_ingest})
            db.commit()
        return account_out(existing, available)
    db.rollback()
    try:
        with IMAPMailbox(available[address]) as mailbox:
            cursor = mailbox.probe()
    except MailAdapterError as exc:
        raise HTTPException(502, str(exc)) from exc
    account = MailAccount(address=address, provider="imap", auto_ingest=body.auto_ingest, connected_by=user.id,
                          sync_state={"uid_validity": cursor.uid_validity, "last_uid": cursor.last_uid,
                                      "source_id": mailbox_source_id(available[address]),
                                      "storage_id": storage_identity(get_settings().storage_dir, get_settings())},
                          sync_requested=True)
    db.add(account)
    try:
        db.flush()
        audit(db, user, "Connected mailbox", target=account.address, entity_type="mail_account", entity_id=account.id, detail={"provider": "imap", "autoIngest": body.auto_ingest})
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
    if not mail_providers.is_live(account.provider):
        raise HTTPException(409, "This mail provider is no longer supported. Reconnect the mailbox before you change automatic import.")
    if account.disconnected_at:
        raise HTTPException(409, "Reconnect this mailbox before you change automatic import.")
    previous = account.auto_ingest
    account.auto_ingest = body.auto_ingest
    audit(db, user, "Turned automatic candidate import " + ("on" if body.auto_ingest else "off"), target=account.address,
          entity_type="mail_account", entity_id=account.id, detail={"autoIngest": {"before": previous, "after": body.auto_ingest}})
    db.commit()
    return account_out(account, configs())


@router.post("/messages/{message_id}/ingest", status_code=202)
def ingest(message_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Manual "Create candidate from attachment" for any role: starts a job, or restarts a failed, skipped or needs-review one."""
    message = db.get(MailMessage, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    ensure_single_person_source(db, "email", message_id)
    job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == message_id).with_for_update())
    if job:
        if job.status not in RETRYABLE_JOB_STATES:
            return {"status": job.status}
        if not db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == message_id).limit(1)):
            raise HTTPException(422, "This email has no attachments to work with.")
        requeue_job(db, job, message)
        audit(db, user, "Restarted candidate creation from email", target=(message.subject or "Email")[:300], entity_type="mail_message", entity_id=message.id, detail={"job": job.id})
        db.commit()
        return {"status": "queued"}
    require_intake_account(db, message.account_id)
    if not db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == message_id).limit(1)):
        raise HTTPException(422, "This email has no attachments to work with.")
    db.add(EmailIngestionJob(message_id=message_id, state={}))
    audit(db, user, "Started candidate creation from email", target=(message.subject or "Email")[:300], entity_type="mail_message", entity_id=message.id)
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
    job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "Ingestion job not found")
    if job.status not in RETRYABLE_JOB_STATES:
        raise HTTPException(409, "This is already waiting, running or finished.")
    message = db.get(MailMessage, job.message_id)
    if not message:
        raise HTTPException(404, "Source message not found")
    requeue_job(db, job, message)
    # The explicit retry authorizes a new generation only for confirmed failed
    # or invalid completed jobs. Pending/unknown accepted IDs remain resumable.
    db.commit()
    return {"status": "queued"}
