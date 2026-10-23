"""Supervised manual document queue; originals and provider progress survive exit."""
import logging
import signal
import threading

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models import DocumentExtractionJob, MultiPersonIntake
from app.db.session import SessionLocal, engine
from app.services.manual_extraction import READY, now, process_job
from app.services.document_person_split import process_bundle

STOP = threading.Event()
LOCK = 724681930119
log = logging.getLogger("document_extraction_worker")


def run_cycle(*, sessions=SessionLocal, settings=None):
    settings = settings or get_settings()
    with sessions() as db:
        bundles = list(db.scalars(select(MultiPersonIntake.id).where(MultiPersonIntake.status == "pending",
            MultiPersonIntake.last_error.is_(None)).order_by(MultiPersonIntake.created_at).limit(2)))
    for identifier in bundles:
        if STOP.is_set(): return
        process_bundle(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        ready = list(db.scalars(select(DocumentExtractionJob.id).where(
            DocumentExtractionJob.status.in_(READY), DocumentExtractionJob.next_attempt_at <= now()
        ).order_by(DocumentExtractionJob.next_attempt_at).limit(10)))
    for identifier in ready:
        if STOP.is_set(): return
        process_job(identifier, sessions=sessions, settings=settings)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
                if lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK}):
                    try: run_cycle()
                    finally: lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK})
        except Exception as exc:
            log.error("Manual extraction cycle failed (%s); saved jobs are retained.", type(exc).__name__)
        STOP.wait(3)


if __name__ == "__main__":
    main()
