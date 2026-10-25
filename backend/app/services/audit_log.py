"""Small helpers for writing audit entries from routers."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEntry


def audit_once(db: Session, actor_id: str, action: str, *, target: str | None, entity_type: str, entity_id: str,
               window_minutes: int = 30, meta: dict | None = None) -> bool:
    """Log an action unless the same user already logged it for this record recently.

    Autosave fires many times while someone types; one line per working session
    keeps the history readable. Returns True when an entry was added.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    recent = db.scalar(select(AuditEntry.id).where(AuditEntry.actor_id == actor_id, AuditEntry.action == action,
        AuditEntry.entity_type == entity_type, AuditEntry.entity_id == entity_id, AuditEntry.at >= since).limit(1))
    if recent:
        return False
    db.add(AuditEntry(actor_id=actor_id, action=action, target=target, entity_type=entity_type, entity_id=entity_id, meta=meta))
    return True
