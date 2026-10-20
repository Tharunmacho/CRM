"""Initialize shared application state without creating or changing user accounts.

Users, password hashes, roles and account status are managed explicitly in the
database. Neither startup nor login supplies default credentials.
"""

from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import AssignmentPointer

log = logging.getLogger("bootstrap")

_done = False
_last_error: str | None = None


def bootstrap_state() -> dict[str, object]:
    # Keep the health-probe keys used by existing containers. "seeded" now means
    # the assignment cursor is ready; it does not mean accounts were generated.
    return {"seeded": _done, "seedError": _last_error}


def ensure_bootstrap(db: Session) -> None:
    """Ensure the assignment cursor exists, preserving its position and all users."""
    global _done, _last_error
    if _done:
        return
    try:
        db.execute(
            insert(AssignmentPointer)
            .values(id=1, last_user_id=None)
            .on_conflict_do_nothing(index_elements=[AssignmentPointer.id])
        )
        db.commit()
        _done = True
        _last_error = None
    except Exception as exc:
        db.rollback()
        _last_error = f"Application state initialization failed ({type(exc).__name__})."
        log.error("%s", _last_error)
        raise
