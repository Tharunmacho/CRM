"""Persist explicitly viewed provider messages without forcing candidate OCR."""
from sqlalchemy import select

from app.db.models import MailAccount, MailAttachment, MailMessage
from app.services.email_intake import store_original


def persist_message(db, settings, account_id, incoming):
    return persist_messages(db, settings, account_id, [incoming])[0]


def persist_messages(db, settings, account_id, incoming_messages):
    """Save a viewed page of messages with a fixed number of statements (not several per message)."""
    incoming_messages = list(incoming_messages)
    if not incoming_messages:
        return []
    # Account lock serializes interactive reads with intake's same-message upsert.
    db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
    known = {row.provider_message_id: row for row in db.scalars(select(MailMessage).where(
        MailMessage.account_id == account_id,
        MailMessage.provider_message_id.in_({message.provider_id for message in incoming_messages}),
    ).with_for_update().execution_options(populate_existing=True))}
    rows, created = [], []
    for incoming in incoming_messages:
        row = known.get(incoming.provider_id)
        if row is not None and row.remote_state in {"moving", "unknown"}:
            rows.append(row)  # A delayed view must not overwrite a durable move intent.
            continue
        if row is None:
            row = MailMessage(account_id=account_id, provider_message_id=incoming.provider_id,
                from_name=incoming.from_name[:200], from_address=incoming.from_address[:255],
                subject=incoming.subject, body=incoming.body, received_at=incoming.received_at,
                message_id_header=incoming.message_id_header, references_header=incoming.references_header,
                reply_to=incoming.reply_to)
            db.add(row)
            known[incoming.provider_id] = row
            created.append((row, incoming))
        row.folder, row.remote_uid, row.uid_validity = incoming.folder or "INBOX", incoming.uid, incoming.uid_validity
        row.unread, row.starred = incoming.unread, incoming.starred
        row.to_addresses, row.cc_addresses = list(incoming.to_addresses), list(incoming.cc_addresses)
        row.bcc_addresses = list(incoming.bcc_addresses)
        row.automation_headers = {**incoming.automation_headers,
            **({"historical_import": True} if (row.automation_headers or {}).get("historical_import") else {})}
        row.remote_deleted = False
        if row.remote_state not in {"moving", "unknown"}:
            row.remote_state, row.remote_error, row.remote_operation = "synced", None, None
        rows.append(row)
    if created:
        db.flush()  # one insert for every new message; their ids are needed for the attachments
        for row, incoming in created:
            for index, part in enumerate(incoming.attachments):
                key = store_original(settings, account_id, incoming.provider_id, index, part.data)
                db.add(MailAttachment(message_id=row.id, file_name=part.filename[:255],
                    size_kb=(len(part.data) + 1023) // 1024, object_key=key, content_hash=part.content_hash))
    return rows


def message_out(db, row, *, bundles=None, attachments=None):
    """`bundles` and `attachments` let a page of messages be built from two reads instead of two per message."""
    from app.services.document_bundle_summary import source_summaries
    if bundles is None:
        bundles = source_summaries(db, "email", [row.id])
    if attachments is None:
        attachments = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id == row.id)))
    return {"id": row.id, "accountId": row.account_id, "fromName": row.from_name or row.from_address or "",
        "fromAddress": row.from_address or "", "toAddresses": row.to_addresses or [], "ccAddresses": row.cc_addresses or [],
        "subject": row.subject or "(No subject)", "body": row.body or "", "preview": (row.body or "")[:180],
        "receivedAt": row.received_at, "unread": row.unread, "starred": row.starred, "candidateId": row.candidate_id,
        "documentBundle": bundles.get(row.id),
        "folder": row.folder, "remoteState": row.remote_state, "remoteError": row.remote_error,
        "attachments": [{"id": item.id, "fileName": item.file_name, "sizeKb": item.size_kb or 0,
            "kind": item.kind or "document", "url": f"/api/inbox/attachments/{item.id}" if item.object_key else None,
            "downloadUrl": f"/api/inbox/attachments/{item.id}?download=true" if item.object_key else None} for item in attachments]}
