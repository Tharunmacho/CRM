"""15-minute task monitoring and 30-second personal reminders, supervised together."""
import logging
import signal
import threading
import time

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.workflow_scheduler import deliver_due_todos, scan_daily_work_only, scan_workflow

log = logging.getLogger("workflow_worker")
TASK_SCAN_SECONDS = 15 * 60
DAILY_SCAN_SECONDS = 60  # allocation, hourly-slot alerts and goal snapshots are at most a minute behind
REMINDER_SCAN_SECONDS = 30


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    next_task_scan = 0.0
    next_daily_scan = 0.0
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                task_scan = time.monotonic() >= next_task_scan
                daily_scan = not task_scan and time.monotonic() >= next_daily_scan
                if task_scan:
                    counts = scan_workflow(db, agency_timezone=get_settings().agency_timezone)
                elif daily_scan:
                    counts = {**scan_daily_work_only(db, agency_timezone=get_settings().agency_timezone), "todos": deliver_due_todos(db)}
                else:
                    counts = {"todos": deliver_due_todos(db)}
                db.commit()
                if task_scan or daily_scan:
                    next_daily_scan = time.monotonic() + DAILY_SCAN_SECONDS
                if daily_scan and any(counts.get(key) for key in ("dailyAllocated", "dailyOverdue", "dailyPostponed")):
                    log.info("Daily work scan complete: %s", counts)
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
