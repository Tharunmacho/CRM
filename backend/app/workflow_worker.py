"""15-minute task monitoring and 30-second personal reminders, supervised together."""
import logging
import signal
import threading
import time

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.workflow_scheduler import deliver_due_todos, scan_workflow

log = logging.getLogger("workflow_worker")
TASK_SCAN_SECONDS = 15 * 60
REMINDER_SCAN_SECONDS = 30


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    next_task_scan = 0.0
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                task_scan = time.monotonic() >= next_task_scan
                counts = scan_workflow(db, agency_timezone=get_settings().agency_timezone) if task_scan else {"todos": deliver_due_todos(db)}
                db.commit()
                if task_scan:
                    next_task_scan = time.monotonic() + TASK_SCAN_SECONDS
                    log.info("Task monitoring complete: %s", counts)
                elif counts["todos"]:
                    log.info("Personal reminders delivered: %s", counts["todos"])
        except Exception as exc:
            log.error("Task monitoring failed (%s); checkpoint retained for retry.", type(exc).__name__)
        stop.wait(REMINDER_SCAN_SECONDS)


if __name__ == "__main__":
    main()
