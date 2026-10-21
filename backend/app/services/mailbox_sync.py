"""Persist explicitly viewed provider messages without forcing candidate OCR."""
from sqlalchemy import select

from app.db.models import MailAccount, MailAttachment, MailMessage
from app.services.email_intake import store_original


def persist_message(db, settings, account_id, incoming):
    # Account lock serializes interactive reads with intake's same-message upsert.
    db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    row = db.scalar(select(MailMessage).where(MailMessage.account_id == account_id,
        MailMessage.provider_message_id == incoming.provider_id).with_for_update().execution_options(populate_existing=True))
    if row is not None and row.remote_state in {"moving", "unknown"}:
        return row  # A delayed view must not overwrite a durable move intent.
    if row is None:
        row = MailMessage(account_id=account_id, provider_message_id=incoming.provider_id,
            from_name=incoming.from_name[:200], from_address=incoming.from_address[:255],
            subject=incoming.subject, body=incoming.body, received_at=incoming.received_at,
            message_id_header=incoming.message_id_header, references_header=incoming.references_header,
            reply_to=incoming.reply_to)
        db.add(row)
        db.flush()
        for index, part in enumerate(incoming.attachments):
            key = store_original(settings, account_id, incoming.provider_id, index, part.data)
            db.add(MailAttachment(message_id=row.id, file_name=part.filename[:255],
                size_kb=(len(part.data) + 1023) // 1024, object_key=key, content_hash=part.content_hash))
    row.folder, row.remote_uid, row.uid_validity = incoming.folder or "INBOX", incoming.uid, incoming.uid_validity
    row.unread, row.starred = incoming.unread, incoming.starred
    row.to_addresses, row.cc_addresses = list(incoming.to_addresses), list(incoming.cc_addresses)
    row.bcc_addresses = list(incoming.bcc_addresses)
    row.remote_deleted = False
    if row.remote_state not in {"moving", "unknown"}:
        row.remote_state, row.remote_error, row.remote_operation = "synced", None, None
    return row


def message_out(db, row):
    attachments = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id == row.id)))
    return {"id": row.id, "accountId": row.account_id, "fromName": row.from_name or row.from_address or "",
        "fromAddress": row.from_address or "", "toAddresses": row.to_addresses or [], "ccAddresses": row.cc_addresses or [],
        "subject": row.subject or "(No subject)", "body": row.body or "", "preview": (row.body or "")[:180],
        "receivedAt": row.received_at, "unread": row.unread, "starred": row.starred, "candidateId": row.candidate_id,
        "folder": row.folder, "remoteState": row.remote_state, "remoteError": row.remote_error,
        "attachments": [{"id": item.id, "fileName": item.file_name, "sizeKb": item.size_kb or 0,
            "kind": item.kind or "document", "url": f"/api/inbox/attachments/{item.id}" if item.object_key else None,
            "downloadUrl": f"/api/inbox/attachments/{item.id}?download=true" if item.object_key else None} for item in attachments]}
