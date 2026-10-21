"""Close expired browser intervals without changing explicit attendance."""
import logging
import signal
import threading
from app.db.session import SessionLocal
from app.services.staff_activity import close_stale_presence


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
        except Exception as exc:
            logging.getLogger(__name__).error("Presence update failed (%s); will retry", type(exc).__name__)
        stop.wait(30)


if __name__ == "__main__":
    main()
