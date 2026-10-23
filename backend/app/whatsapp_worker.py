"""Process verified WhatsApp media receipts independently of mailbox polling."""
import logging
import signal
import threading

from sqlalchemy import select, text, update

from app.core.config import get_settings
from app.db.models import WhatsAppChannel, WhatsAppMessage
from app.db.session import SessionLocal, engine
from app.integrations.whatsapp import missing_settings
from app.services.whatsapp_intake import process_message, utcnow
from app.services.whatsapp_chat import dispatch_queued


def outbound_loop(stop, settings):
    """Chat must not wait behind long document extraction jobs."""
    while not stop.is_set():
        try:
            dispatch_queued(sessions=SessionLocal, settings=settings)
        except Exception as exc:
            logging.error("WhatsApp outbox cycle failed (%s); send attempts retained.", type(exc).__name__)
        stop.wait(2)


def main():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    settings = get_settings()
    if not settings.whatsapp_enabled or missing_settings(settings):
        raise SystemExit("Configure the WhatsApp channel before starting its worker")
    # Every provider call first persists a send claim. Multiple worker processes
    # are safe, and an interrupted HTTP send is recovered as unknown, never sent
    # a second time automatically.
    threading.Thread(target=outbound_loop, args=(stop, settings), name="whatsapp-outbox", daemon=True).start()
    while not stop.is_set():
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
                if lock.scalar(text("SELECT pg_try_advisory_lock(724681930120)")):
                    try:
                        with SessionLocal() as db:
                            db.execute(update(WhatsAppChannel).where(WhatsAppChannel.phone_number_id == settings.whatsapp_phone_number_id, WhatsAppChannel.enabled.is_(True)).values(worker_seen_at=utcnow()))
                            ids = list(db.scalars(select(WhatsAppMessage.id).join(WhatsAppChannel).where(WhatsAppChannel.enabled.is_(True),
                                WhatsAppChannel.phone_number_id == settings.whatsapp_phone_number_id,
                                WhatsAppMessage.status.in_(["pending", "processing", "retry"]), WhatsAppMessage.next_attempt_at <= utcnow()).order_by(WhatsAppMessage.next_attempt_at).limit(5)))
                            db.commit()
                        for message_id in ids:
                            if stop.is_set(): break
                            process_message(message_id, sessions=SessionLocal, settings=settings)
                    finally: lock.execute(text("SELECT pg_advisory_unlock(724681930120)"))
        except Exception as exc:
            logging.error("WhatsApp worker cycle failed (%s); receipts retained.", type(exc).__name__)
        stop.wait(5)


if __name__ == "__main__":
    main()
