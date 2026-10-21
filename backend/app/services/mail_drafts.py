"""Private server drafts and immutable, idempotent outbound attempts."""
from datetime import datetime, timedelta, timezone
from email import policy
import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import MailAccount, MailDraft, MailDraftAttachment, MailMessage, MailSendAttempt
from app.integrations.mail.mailbox import WritableMailbox, MailAdapterError
from app.integrations.mail.smtp import compose
from app.services.document_storage import read_bytes
from app.services import zoho_mail

MAX_ATTACHMENTS = 20
MAX_TOTAL_BYTES = 18 * 1024 * 1024  # MIME base64 expansion stays below 25 MB.


def now():
    return datetime.now(timezone.utc)


def owned(db, draft_id, actor_id, *, lock=False):
    query = select(MailDraft).where(MailDraft.id == draft_id, MailDraft.actor_id == actor_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row:
        raise HTTPException(404, "Draft not found")
    return row


def check_edit(row, version):
    if row.lock_version != version:
        raise HTTPException(409, "This draft changed in another request. Reload the saved draft before editing.")
    if row.status not in {"draft", "failed"}:
        raise HTTPException(409, "This draft has a send outcome that must be checked before further changes.")


def attachment_rows(db, draft_id):
    return list(db.scalars(select(MailDraftAttachment).where(MailDraftAttachment.draft_id == draft_id).order_by(MailDraftAttachment.id)))


def draft_out(db, row):
    return {"id": row.id, "accountId": row.account_id, "actorId": row.actor_id, "mode": row.mode,
        "sourceMessageId": row.source_message_id, "to": row.to_addresses or [], "cc": row.cc_addresses or [],
        "bcc": row.bcc_addresses or [], "subject": row.subject or "", "body": row.body or "",
        "lockVersion": row.lock_version, "status": row.status, "remoteState": row.remote_state,
        "lastError": row.last_error, "createdAt": row.created_at, "updatedAt": row.updated_at,
        "attachments": [{"id": item.id, "name": item.file_name, "mime": item.content_type,
            "sizeBytes": item.size_bytes, "url": f"/api/inbox/drafts/{row.id}/attachments/{item.id}",
            "downloadUrl": f"/api/inbox/drafts/{row.id}/attachments/{item.id}?download=true"} for item in attachment_rows(db, row.id)]}


def attempt_out(row):
    return {"id": row.id, "draftId": row.draft_id, "requestKey": row.request_key, "status": row.status,
        "providerMessageId": row.provider_message_id, "lastError": row.last_error,
        "refusedRecipients": row.refused_recipients or [], "createdAt": row.created_at, "sentAt": row.sent_at}


def config_for(db, settings, row):
    source = db.get(MailAccount, row.account_id)
    try:
        return zoho_mail.account_config(db, settings, source)
    except MailAdapterError as exc:
        raise HTTPException(409, str(exc)) from None


def envelope(db, settings, row, config, message_id):
    attachments = attachment_rows(db, row.id)
    if len(attachments) > MAX_ATTACHMENTS or sum(a.size_bytes for a in attachments) > MAX_TOTAL_BYTES:
        raise HTTPException(422, "Use at most 20 attachments totaling 18 MB.")
    files = []
    try:
        for attachment in attachments:
            data = read_bytes(settings, attachment.object_key)
            if len(data) != attachment.size_bytes or hashlib.sha256(data).hexdigest() != attachment.content_hash:
                raise HTTPException(409, "An attachment is incomplete. Remove it and upload it again.")
            files.append({"name": attachment.file_name, "mime": attachment.content_type, "data": data})
    except FileNotFoundError:
        raise HTTPException(409, "An attachment is missing. Remove it and upload it again.") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Attachment storage is unavailable. The draft has been retained.") from None
    source = db.get(MailMessage, row.source_message_id) if row.source_message_id else None
    return compose(config, message_id=message_id, to=row.to_addresses or [], cc=row.cc_addresses or [],
        bcc=row.bcc_addresses or [], subject=row.subject or "", body=row.body or "", attachments=files,
        in_reply_to=source.message_id_header if source and row.mode in {"reply", "reply_all"} else None,
        references=source.references_header if source else None)


def sync_draft(db, settings, row):
    """CRM commit precedes remote save; errors never discard the editable draft."""
    row = owned(db, row.id, row.actor_id, lock=True)
    if row.status not in {"draft", "failed"}:
        return row
    try:
        config = config_for(db, settings, row)
        message_id = f"<draft-{row.id}-{row.lock_version}@crm-mail.local>"
        outgoing, _ = envelope(db, settings, row, config, message_id)
        # Bcc belongs in a saved draft, but SMTP deliver removes it from transmission.
        if row.bcc_addresses:
            outgoing["Bcc"] = ", ".join(row.bcc_addresses)
        with WritableMailbox(config) as mailbox:
            folders = mailbox.folders()
            destination = next((item["name"] for item in folders if item["role"] == "drafts"), None)
            if not destination:
                raise MailAdapterError("draft_folder", "Enable the Drafts folder in Zoho's IMAP folder settings. The CRM draft is saved.")
            cursor, uids = mailbox.find_message_id(destination, message_id)
            if len(uids) > 1:
                raise MailAdapterError("draft_duplicates", "Zoho has multiple copies of this draft version. Review them in Zoho before syncing again.")
            if uids:
                validity, uid = cursor.uid_validity, uids[0]
            else:
                result = mailbox.append_draft(destination, outgoing.as_bytes(policy=policy.SMTP))
                if result is None:
                    cursor, uids = mailbox.find_message_id(destination, message_id)
                    if len(uids) != 1:
                        raise MailAdapterError("draft_unknown", "Zoho accepted the draft but its location is unconfirmed. Check draft sync before sending.")
                    result = cursor.uid_validity, uids[0]
                validity, uid = result
            old = row.remote_folder, row.remote_uid, row.remote_uid_validity
            row.remote_folder, row.remote_uid, row.remote_uid_validity = destination, uid, validity
            row.remote_state, row.last_error = "synced", None
            db.commit()  # Retain the new identifier even if removing an old version fails.
            if old[1] and old != (destination, uid, validity):
                try:
                    mailbox.remove_draft(*old)
                except MailAdapterError:
                    row.last_error = "The latest draft is saved. Zoho may still show an older draft copy; remove it there after checking."
    except (MailAdapterError, HTTPException) as exc:
        row.remote_state = "unknown" if getattr(exc, "unknown", False) or getattr(exc, "code", "") == "draft_unknown" else "error"
        row.last_error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    db.commit()
    return row


def expire_sending(db, row):
    attempts = list(db.scalars(select(MailSendAttempt).where(MailSendAttempt.draft_id == row.id).order_by(MailSendAttempt.created_at.desc())))
    changed = False
    for attempt in attempts:
        if attempt.status in {"pending", "sending"} and attempt.created_at < now() - timedelta(minutes=3):
            attempt.status, attempt.last_error = "unknown", "The send request was interrupted. Check Zoho Sent before sending another copy."
            if row.status == "sending":
                row.status, row.last_error = "unknown", attempt.last_error
            changed = True
    if changed:
        db.commit()
    return attempts


def remove_remote_draft(db, row, config):
    if not row.remote_uid:
        return
    try:
        with WritableMailbox(config) as mailbox:
            mailbox.remove_draft(row.remote_folder, row.remote_uid, row.remote_uid_validity)
        row.remote_state, row.remote_uid = "synced", None
    except MailAdapterError:
        row.remote_state, row.last_error = "error", "Zoho may still show the saved draft. Remove that copy after checking Sent; do not send it again."
    db.commit()
