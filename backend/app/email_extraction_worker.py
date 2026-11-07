"""Read mail attachments side by side without delaying mailbox receive cadence.

Same design as the upload worker (see services/extraction_queue.py): every email intake job is leased with
FOR UPDATE SKIP LOCKED, several worker processes run at once, each keeps ``threads_per_worker`` messages being read and
refills a slot the moment one frees. Mailboxes are interleaved, so one busy mailbox does not hold back another.
"""
from datetime import timedelta
import logging
import os
import signal
import threading
import time


from app.core.config import get_settings
from app.db.models import EmailIngestionJob
from app.db.session import SessionLocal, engine
from app.services.extraction_queue import (Dispatcher, LeasedQueue, WakeListener, new_worker_id, threads_per_worker, utcnow)

STOP = threading.Event()
log = logging.getLogger("email_extraction_worker")
BOOKKEEPING_SECONDS = 60  # how often mailbox eligibility and the "extraction worker seen" stamp are refreshed (the screen allows 15 minutes)


def email_queue(sessions, settings, accounts: list) -> LeasedQueue:
    """``accounts`` is the live list of eligible mailbox ids; the housekeeping step replaces its contents."""
    from app.services.email_intake import process_job

    def run(job_id, worker):
        try:
            process_job(job_id, sessions=sessions, settings=settings)
        except Exception as exc:
            # A failure outside process_job's own recovery path must not block unrelated messages.
            log.error("Ingestion job %s failed (%s); queued work is retained.", job_id, type(exc).__name__)
            with sessions() as db:
                job = db.get(EmailIngestionJob, job_id)
                if job is not None and job.status in {"pending", "processing", "retry"}:
                    job.attempts += 1
                    job.status = "failed" if job.attempts >= 4 else "retry"
                    job.next_attempt_at = utcnow() + timedelta(seconds=90 * 2 ** (job.attempts - 1))
                    job.last_error = "We could not read the documents in this email. Try again, and contact your administrator if it keeps happening."
                    job.claimed_by = job.lease_until = None
                    db.commit()

    queue = LeasedQueue(
        name="email", table="email_ingestion_jobs", sessions=sessions, runner=run,
        owner_expr="m.account_id",
        join="JOIN email_messages m ON m.id = j.message_id JOIN email_accounts a ON a.id = m.account_id",
        where="AND a.disconnected_at IS NULL AND m.account_id = ANY(:accounts)",
        ready="(({a}.status IN ('pending','retry') AND {a}.next_attempt_at <= :now) "
              "OR ({a}.status = 'processing' AND ({a}.lease_until IS NULL OR {a}.lease_until < :now)))",
        set_clause="status = 'processing', updated_at = :now")
    queue.extra_params = {"accounts": accounts}
    return queue


def build_dispatcher(*, sessions=SessionLocal, settings=None, stop=None, wake=None, concurrency=None, presence="email", **options):
    from app import email_worker
    settings = settings or get_settings()
    accounts: list = []
    queue = email_queue(sessions, settings, accounts)
    due = [0.0]

    def bookkeeping():
        # Mailbox eligibility (storage binding, disconnects) and the "extraction worker seen" heartbeat.
        if time.monotonic() < due[0]:
            return
        eligible = email_worker.run_cycle(sessions=sessions, settings=settings, mode="extract", process_jobs=False) or []
        accounts[:] = eligible
        due[0] = time.monotonic() + BOOKKEEPING_SECONDS

    dispatcher = Dispatcher(
        [queue], concurrency=concurrency or threads_per_worker(settings), worker=new_worker_id("mail"),
        stop=stop if stop is not None else STOP, wake=wake, before_claim=bookkeeping,
        lease_seconds=getattr(settings, "extraction_lease_seconds", 180),
        grace=getattr(settings, "extraction_shutdown_seconds", 30), presence=presence, **options)
    dispatcher.queue = queue
    return dispatcher


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.email_ingestion_enabled:
        raise SystemExit("Set EMAIL_INGESTION_ENABLED=true to run the incoming-mail worker.")
    wake = threading.Event()
    dispatcher = build_dispatcher(settings=settings, wake=wake)
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: dispatcher.request_stop())
    listener = WakeListener(engine.url.set(drivername="postgresql").render_as_string(hide_password=False), wake)
    dispatcher.wake_alive = listener.listening.is_set  # polling is only a safety net while the NOTIFY wake-up is connected
    listener.start()
    log.info("Email reading worker %s started: %s messages at once.", dispatcher.worker, dispatcher.concurrency)
    abandoned = dispatcher.run()
    listener.stop()
    log.info("Email reading worker stopped; %s unfinished message(s) were handed back to the queue.", abandoned)
    if abandoned:
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()
