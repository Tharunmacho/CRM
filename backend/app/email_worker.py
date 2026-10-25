"""Run with npm run worker. A DB-backed queue survives process restarts."""
import logging
import signal
import threading
from datetime import timedelta

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models import EmailIngestionJob, MailAccount, MailMessage
from app.db.session import SessionLocal, engine
from app.services.email_intake import now, process_job, receive_account
from app.services.mail_providers import LIVE_PROVIDERS
from app.services.mail_storage import STORAGE_MISMATCH, storage_identity
from app.services.mail_history import receive_history
from app.services.intake_acknowledgements import dispatch_pending_email_acknowledgements

log = logging.getLogger("email_worker")
STOP = threading.Event()
# Database-wide identity; never derive this from a process-local hash.
LOCK = 724681930117


def run_cycle(*, sessions=SessionLocal, settings=None, mode="all"):
    settings = settings or get_settings()
    if not settings.email_ingestion_enabled:
        return
    volume_id = storage_identity(settings.storage_dir, settings)
    with sessions() as db:
        accounts = list(db.scalars(select(MailAccount).where(MailAccount.disconnected_at.is_(None), MailAccount.provider.in_(sorted(LIVE_PROVIDERS))).with_for_update()))
        due, eligible = [], []
        for account in accounts:
            cursor = dict(account.sync_state or {})
            bound_volume = cursor.get("storage_id")
            if bound_volume is None:
                if mode == "extract":
                    continue  # The receiving worker binds the source volume first.
                account.sync_state = {**cursor, "storage_id": volume_id}
            elif bound_volume != volume_id:
                account.last_error = STORAGE_MISMATCH
                continue
            eligible.append(account.id)
            if account.last_error == STORAGE_MISMATCH:
                account.last_error = None
            if mode == "extract":
                account.extraction_seen_at = now()
            else:
                account.worker_seen_at = now()
            if (cursor.get("history", {}).get("status") in {"queued", "running"} or account.sync_requested or account.last_sync_at is None
                    or account.last_sync_at <= now() - timedelta(seconds=settings.email_poll_seconds)):
                due.append(account.id)
        db.commit()
    if not eligible:
        if mode != "extract":
            dispatch_pending_email_acknowledgements(sessions=sessions, settings=settings)
        return
    for account_id in due if mode != "extract" else []:
        if STOP.is_set():
            return
        try:
            receive_account(account_id, sessions=sessions, settings=settings)
            receive_history(account_id, sessions=sessions, settings=settings)
        except Exception as exc:
            from app.integrations.mail.imap import MailAdapterError
            message = str(exc) if isinstance(exc, MailAdapterError) else "We could not check this mailbox for new emails. We will try again soon. If this keeps happening, contact your administrator."
            log.warning("Mailbox %s sync failed (%s)", account_id, type(exc).__name__)
            with sessions() as db:
                account = db.get(MailAccount, account_id)
                if account is not None:
                    account.last_error, account.last_sync_at, account.sync_requested = message[:500], now(), False
                    history = dict((account.sync_state or {}).get("history") or {})
                    if history.get("status") in {"queued", "running"}:
                        history.update(status="error", last_error=message[:500])
                        account.sync_state = {**account.sync_state, "history": history}
                    db.commit()
    if mode == "receive":
        dispatch_pending_email_acknowledgements(sessions=sessions, settings=settings)
        return
    with sessions() as db:
        ready = list(db.scalars(select(EmailIngestionJob.id).join(
            MailMessage, MailMessage.id == EmailIngestionJob.message_id,
        ).where(
            MailMessage.account_id.in_(eligible),
            EmailIngestionJob.status.in_(["pending", "processing", "retry"]),
            EmailIngestionJob.next_attempt_at <= now(),
        ).order_by(EmailIngestionJob.next_attempt_at).limit(10)))
    for job_id in ready:
        if STOP.is_set():
            return
        with sessions() as db:
            # A mailbox may have been disconnected after the initial queue scan.
            source = db.scalar(select(MailAccount).join(MailMessage, MailMessage.account_id == MailAccount.id)
                .join(EmailIngestionJob, EmailIngestionJob.message_id == MailMessage.id).where(EmailIngestionJob.id == job_id))
            if source is None or source.disconnected_at:
                continue
        try:
            process_job(job_id, sessions=sessions, settings=settings)
        except Exception as exc:
            # A failure outside process_job's own recovery path must not block
            # unrelated messages or print exception text/SQL parameters.
            log.error("Ingestion job %s failed (%s); queued work is retained.", job_id, type(exc).__name__)
            with sessions() as db:
                job = db.get(EmailIngestionJob, job_id)
                if job is not None and job.status in {"pending", "processing", "retry"}:
                    job.attempts += 1
                    job.status = "failed" if job.attempts >= 4 else "retry"
                    job.next_attempt_at = now() + timedelta(seconds=90 * 2 ** (job.attempts - 1))
                    job.last_error = "We could not read the documents in this email. Try again, and contact your administrator if it keeps happening."
                    db.commit()
    if mode != "extract":
        dispatch_pending_email_acknowledgements(sessions=sessions, settings=settings)


def main(mode="receive"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.email_ingestion_enabled:
        raise SystemExit("Set EMAIL_INGESTION_ENABLED=true to run the incoming-mail worker.")
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: STOP.set())
    lock_key = LOCK + (1 if mode == "extract" else 0)
    log.info("Email %s worker started; receive cadence %ss. Saved-intake acknowledgements require per-mailbox Admin approval.", mode, settings.email_poll_seconds)
    while not STOP.is_set():
        try:
            # A dedicated autocommit connection holds a session lock, not a long
            # transaction. Another instance can take over immediately after exit.
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
                if lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}):
                    try:
                        run_cycle(settings=settings, mode=mode)
                    finally:
                        lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        except Exception as exc:
            log.error("Worker cycle failed (%s); queued work is retained.", type(exc).__name__)
        STOP.wait(5)
    log.info("Email intake worker stopped.")


if __name__ == "__main__":
    main()
