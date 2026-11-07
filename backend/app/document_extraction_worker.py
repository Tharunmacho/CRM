"""Reading worker for uploaded documents: claims jobs one by one and reads many side by side.

Run any number of these (the supervisor in app/runtime.py starts EXTRACTION_WORKERS of them). Each process keeps
``threads_per_worker`` documents being read, refills a slot the moment one frees, and shares nothing with the others
except the database, where every job is leased with FOR UPDATE SKIP LOCKED (services/extraction_queue.py).
"""
import logging
import os
import signal
import threading

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.services.extraction_queue import (Dispatcher, WakeListener, bundle_queue, manual_queue, new_worker_id,
                                           threads_per_worker)

STOP = threading.Event()
log = logging.getLogger("document_extraction_worker")


def build_dispatcher(*, sessions=SessionLocal, settings=None, stop=None, wake=None, concurrency=None, presence="document", **options) -> Dispatcher:
    settings = settings or get_settings()
    return Dispatcher(
        [bundle_queue(sessions, settings), manual_queue(sessions, settings)],
        concurrency=concurrency or threads_per_worker(settings), worker=new_worker_id("doc"),
        stop=stop if stop is not None else STOP, wake=wake,
        lease_seconds=getattr(settings, "extraction_lease_seconds", 180),
        grace=getattr(settings, "extraction_shutdown_seconds", 30), presence=presence, **options)


def run_cycle(*, sessions=SessionLocal, settings=None, concurrency=None):
    """Read everything that is ready right now, side by side, then return (used by tests and one-off runs)."""
    settings = settings or get_settings()
    stop = threading.Event()
    dispatcher = build_dispatcher(sessions=sessions, settings=settings, stop=stop, concurrency=concurrency, busy_poll=0.05, idle_poll=0.05, presence=None)
    pool_done = threading.Event()

    def watch():
        # Stop once a claim round finds nothing and nothing is still running.
        while not pool_done.wait(0.05):
            if not dispatcher.inflight and dispatcher.idle_rounds >= 2:
                dispatcher.request_stop()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        dispatcher.run()
    finally:
        pool_done.set()


def configure_logging() -> None:
    """Console and a rotating file (backend/data/logs/reader-worker.log), so a failed attempt can be read afterwards
    even when the terminal that started the worker is hidden or closed."""
    from logging.handlers import RotatingFileHandler
    from pathlib import Path
    from app.core.config import BACKEND_ROOT

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        folder = Path(BACKEND_ROOT) / "data" / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(folder / "reader-worker.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True)


def main():
    configure_logging()
    settings = get_settings()
    wake = threading.Event()
    dispatcher = build_dispatcher(settings=settings, wake=wake)

    def terminate(*_):
        dispatcher.request_stop()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), terminate)
    listener = WakeListener(engine.url.set(drivername="postgresql").render_as_string(hide_password=False), wake)
    dispatcher.wake_alive = listener.listening.is_set  # polling is only a safety net while the NOTIFY wake-up is connected
    listener.start()
    log.info("Reading worker %s started: %s documents at once (workers x threads is capped at %s).",
             dispatcher.worker, dispatcher.concurrency, settings.extraction_max_parallel)
    abandoned = dispatcher.run()
    listener.stop()
    log.info("Reading worker stopped; %s unfinished document(s) were handed back to the queue.", abandoned)
    if abandoned:
        logging.shutdown()
        os._exit(0)  # reading threads cannot be interrupted; their claims are already released


if __name__ == "__main__":
    main()
