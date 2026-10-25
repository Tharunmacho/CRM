"""Small, resumable historical-mail batches with a cursor separate from receiving."""
from datetime import datetime, timezone
from sqlalchemy import select

from app.db.models import EmailIngestionJob, MailAccount, MailAttachment, MailMessage
from app.integrations.mail.imap import IMAPMailbox, MailAdapterError, MailMessageError, require_cursor_source
from app.services.email_intake import store_original
from app.services.mail_providers import account_config


ACTIVE = {"queued", "running"}


def receive_history(account_id, *, sessions, settings):
    with sessions() as db:
        account = db.get(MailAccount, account_id)
        if not account or account.disconnected_at:
            return
        cursor = dict(account.sync_state)
        history = dict(cursor.get("history") or {})
        if history.get("status") not in ACTIVE:
            return
        config = account_config(db, settings, account)
    require_cursor_source(cursor, config)
    try:
        with IMAPMailbox(config) as mailbox:
            remaining = history["max_count"] - history.get("processed", 0)
            uids = mailbox.history_uids(after_uid=history.get("after_uid", 0), before_uid=history["end_uid"],
                since=history["since"], uid_validity=cursor["uid_validity"], limit=min(25, remaining)) if remaining > 0 else []
            for uid in uids:
                with sessions() as db:
                    current = db.get(MailAccount, account_id)
                    progress = (current.sync_state or {}).get("history") or {}
                    if current.disconnected_at or progress.get("id") != history["id"] or progress.get("status") not in ACTIVE:
                        return
                error, incoming, stored = None, None, []
                try:
                    incoming = mailbox.fetch(uid, uid_validity=cursor["uid_validity"])
                    stored = [(part, store_original(settings, account_id, incoming.provider_id, index, part.data))
                        for index, part in enumerate(incoming.attachments)]
                except MailMessageError as exc:
                    if exc.retryable:
                        raise
                    error = exc
                with sessions() as db:
                    current = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
                    progress = dict(current.sync_state.get("history") or {})
                    if current.disconnected_at or progress.get("id") != history["id"] or progress.get("status") not in ACTIVE:
                        return
                    provider_id = incoming.provider_id if incoming else error.provider_id
                    message = db.scalar(select(MailMessage).where(MailMessage.account_id == account_id, MailMessage.provider_message_id == provider_id))
                    if message is None:
                        message = MailMessage(account_id=account_id, provider_message_id=provider_id,
                            subject=incoming.subject if incoming else "Historical message could not be imported",
                            received_at=incoming.received_at if incoming else datetime.now(timezone.utc),
                            from_name=incoming.from_name[:200] if incoming else None, from_address=incoming.from_address[:255] if incoming else None,
                            body=incoming.body if incoming else None, unread=incoming.unread if incoming else True,
                            message_id_header=incoming.message_id_header if incoming else None,
                            automation_headers={**incoming.automation_headers, "historical_import": True} if incoming else {},
                            references_header=incoming.references_header if incoming else None, reply_to=incoming.reply_to if incoming else None,
                            folder=(incoming.folder or "INBOX") if incoming else "INBOX", remote_uid=incoming.uid if incoming else None,
                            uid_validity=incoming.uid_validity if incoming else None,
                            to_addresses=list(incoming.to_addresses) if incoming else [],
                            cc_addresses=list(incoming.cc_addresses) if incoming else [],
                            bcc_addresses=list(incoming.bcc_addresses) if incoming else [], starred=incoming.starred if incoming else False)
                        db.add(message); db.flush()
                        for part, key in stored:
                            db.add(MailAttachment(message_id=message.id, file_name=part.filename[:255],
                                size_kb=(len(part.data) + 1023) // 1024, object_key=key, content_hash=part.content_hash))
                        progress["imported"] = progress.get("imported", 0) + 1
                        if error:
                            db.add(EmailIngestionJob(message_id=message.id, status="failed", last_error=str(error), state={"receive_error": True}))
                    if progress.get("auto_ingest") and stored and not db.scalar(select(EmailIngestionJob.id).where(EmailIngestionJob.message_id == message.id)):
                        db.add(EmailIngestionJob(message_id=message.id, state={}))
                    progress.update(after_uid=uid, processed=progress.get("processed", 0) + 1, status="running", last_error=None)
                    current.sync_state = {**current.sync_state, "history": progress}
                    db.commit()
                    history = progress
            with sessions() as db:
                current = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
                progress = dict(current.sync_state.get("history") or {})
                if progress.get("id") == history["id"] and progress.get("status") in ACTIVE:
                    progress["status"] = "limited" if progress.get("processed", 0) >= progress["max_count"] else "complete" if len(uids) < min(25, remaining) else "running"
                    current.sync_state = {**current.sync_state, "history": progress}
                    db.commit()
    except Exception as exc:
        with sessions() as db:
            current = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
            progress = dict(current.sync_state.get("history") or {})
            if progress.get("id") == history["id"] and progress.get("status") in ACTIVE:
                progress.update(status="error", last_error=str(exc) if isinstance(exc, MailAdapterError) else "We could not import your older emails. Check the mailbox connection, then continue the import.")
                current.sync_state = {**current.sync_state, "history": progress}
                db.commit()
