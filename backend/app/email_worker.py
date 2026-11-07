"""Run with npm run worker. A DB-backed queue survives process restarts."""
import logging
import signal
import threading
import time
from datetime import timedelta

from sqlalchemy import select, text, update

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


def extract_bookkeeping(*, sessions=SessionLocal, settings=None) -> list[str]:
    """What the parallel reading pool needs from the mailboxes, in two autocommit statements and no row locks:
    the ids of the mailboxes it may read for (storage bound to this volume), and the 'extraction worker seen' stamp.

    The receiving worker owns binding and mismatch flags; the reader only looks. (It used to lock every mailbox row
    with FOR UPDATE and rewrite it in a transaction on every cycle, from every reading process.)
    """
    settings = settings or get_settings()
    if not settings.email_ingestion_enabled:
        return []
    volume_id = storage_identity(settings.storage_dir, settings)
    with sessions() as db:
        db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        rows = db.execute(select(MailAccount.id, MailAccount.sync_state["storage_id"].astext)
                          .where(MailAccount.disconnected_at.is_(None), MailAccount.provider.in_(sorted(LIVE_PROVIDERS)))).all()
        eligible = [row[0] for row in rows if row[1] == volume_id]
        if eligible:
            db.execute(update(MailAccount).where(MailAccount.id.in_(eligible)).values(extraction_seen_at=now()))
    return eligible


def work_waiting(*, sessions=SessionLocal, settings=None) -> bool:
    """ONE read-only statement: is there anything for the receiving worker to do (a mailbox due, a requested sync, a
    first sync or an unfinished history import, an acknowledgement to send)? Lets an idle receiver skip the whole
    locked cycle."""
    settings = settings or get_settings()
    statement = text("""
        SELECT EXISTS (SELECT 1 FROM email_accounts a
                        WHERE a.disconnected_at IS NULL AND a.provider = ANY(:providers)
                          AND (a.sync_requested OR a.last_sync_at IS NULL OR a.last_sync_at <= :due
                               OR a.sync_state ->> 'storage_id' IS NULL
                               OR a.sync_state #>> '{history,status}' IN ('queued', 'running')))
            OR EXISTS (SELECT 1 FROM intake_acknowledgements k
                        WHERE k.source_kind = 'email' AND k.status IN ('pending', 'sending'))""")
    with sessions() as db:
        db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        return bool(db.execute(statement, {"providers": sorted(LIVE_PROVIDERS),
                                           "due": now() - timedelta(seconds=settings.email_poll_seconds)}).scalar())


def run_cycle(*, sessions=SessionLocal, settings=None, mode="all", process_jobs=True):
    """``process_jobs=False`` in extract mode only does the mailbox bookkeeping and returns the eligible account ids:
    the parallel reading pool (email_extraction_worker.py) claims and reads the jobs itself."""
    settings = settings or get_settings()
    if not settings.email_ingestion_enabled:
        return
    if mode == "extract" and not process_jobs:
        return extract_bookkeeping(sessions=sessions, settings=settings)
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
        return []
    if mode == "extract" and not process_jobs:
        return eligible
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


ACTIVE_POLL_SECONDS = 5     # while a cycle found work (a sync, a history import, an acknowledgement), look again soon
IDLE_POLL_SECONDS = 15      # nothing due: one read-only statement per look
SEEN_SECONDS = 300          # the 'receiver seen' stamp and the full locked cycle run at least this often (the screen allows 15 minutes)
BACKOFF_MAX_SECONDS = 120


def main(mode="receive", stop=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.email_ingestion_enabled:
        raise SystemExit("Set EMAIL_INGESTION_ENABLED=true to run the incoming-mail worker.")
    stop = stop or STOP
    if stop is STOP:
        for name in ("SIGINT", "SIGTERM"):
            signal.signal(getattr(signal, name), lambda *_: STOP.set())
    lock_key = LOCK + (1 if mode == "extract" else 0)
    log.info("Email %s worker started; receive cadence %ss. Saved-intake acknowledgements require per-mailbox Admin approval.", mode, settings.email_poll_seconds)
    last_cycle = failures = 0
    wait = ACTIVE_POLL_SECONDS
    while not stop.is_set():
        try:
            # An idle receiver asks ONE read-only question; the locked cycle (advisory lock, row locks, stamps,
            # acknowledgement scan: ~7 statements) only runs when something is due or the stamp is getting old.
            if time.monotonic() - last_cycle >= SEEN_SECONDS or work_waiting(settings=settings):
                # A dedicated autocommit connection holds a session lock, not a long
                # transaction. Another instance can take over immediately after exit.
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
                    if lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}):
                        try:
                            run_cycle(settings=settings, mode=mode)
                        finally:
                            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                last_cycle = time.monotonic()
                wait = ACTIVE_POLL_SECONDS
            else:
                wait = IDLE_POLL_SECONDS
            failures = 0
        except Exception as exc:
            failures += 1
            wait = min(BACKOFF_MAX_SECONDS, ACTIVE_POLL_SECONDS * 2 ** min(failures, 5))
            log.error("Worker cycle failed (%s); queued work is retained. Trying again in %ss.", type(exc).__name__, wait)
        stop.wait(wait)
    log.info("Email intake worker stopped.")


if __name__ == "__main__":
    main()
