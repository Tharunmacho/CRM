"""Care India candidate identifiers.

``Candidate.ref`` is a long-lived CRM/technical reference (``CND-*``) and is
used in historical links, messages and imports.  The human-facing Care India
ID is deliberately a second, immutable number: one simple continuous series,
given to every candidate when they are created and never reused:

    CI-001, CI-002, ... CI-999, CI-1000, ...

There is no financial-year part and no yearly restart.  The counter update is
one PostgreSQL upsert on a single row, so two concurrent intakes cannot
receive the same number and a rolled-back transaction does not consume one.
The database migration also contains a trigger backstop for future direct
inserts that forget to call this service.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


PREFIX = "CI"
COUNTER_KEY = "GLOBAL"


def format_care_id(serial: int) -> str:
    """Format a positive counter as the public candidate ID (``CI-007``; four digits once past 999)."""
    if serial < 1:
        raise ValueError("Care India serial must be positive.")
    return f"{PREFIX}-{serial:03d}"


def allocate_care_id(db: Session) -> str:
    """Atomically reserve the next Care India ID in this database transaction."""
    serial = db.execute(text("""
        INSERT INTO crm_candidate_id_counters (series, last_serial)
        VALUES (:series, 1)
        ON CONFLICT (series) DO UPDATE
        SET last_serial = crm_candidate_id_counters.last_serial + 1
        RETURNING last_serial
    """), {"series": COUNTER_KEY}).scalar_one()
    return format_care_id(int(serial))
