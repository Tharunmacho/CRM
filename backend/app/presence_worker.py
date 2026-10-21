"""Expire browser presence and process approved attendance leave cover."""
import logging
import signal
import threading
from app.db.session import SessionLocal
from app.services.staff_activity import close_stale_presence
from app.services.attendance_hr import run_cover_sweep


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                close_stale_presence(db)
                db.commit()
            # Own transaction: cover failures cannot roll back presence expiry.
            # The cursor lock and durable ledger make parallel workers safe.
            with SessionLocal() as db:
                run_cover_sweep(db)
                db.commit()
        except Exception as exc:
            logging.getLogger(__name__).error("Presence or attendance-cover update failed (%s); will retry", type(exc).__name__)
        stop.wait(30)


if __name__ == "__main__":
    main()
