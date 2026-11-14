"""Care India candidate identifiers.

``Candidate.ref`` is a long-lived CRM/technical reference (``CND-*``) and is
used in historical links, messages and imports.  The human-facing Care India
ID is deliberately a second, immutable number.  It follows the agency's
April--March financial year in India time:

    CI-26-27-001

The counter update is one PostgreSQL upsert, so two concurrent intakes cannot
receive the same number and a rolled-back transaction does not consume one.
The database migration also contains a trigger backstop for future direct
inserts that forget to call this service.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session


CARE_INDIA_ZONE = ZoneInfo("Asia/Kolkata")


def fiscal_year(at: datetime | date | None = None) -> tuple[int, int]:
    """Return the Care India financial-year bounds for an instant/date.

    Dates are agency-local by definition.  A naive datetime is treated as
    Asia/Kolkata time; aware datetimes are converted there first.
    """
    if at is None:
        day = datetime.now(timezone.utc).astimezone(CARE_INDIA_ZONE).date()
    elif isinstance(at, datetime):
        day = (at.replace(tzinfo=CARE_INDIA_ZONE) if at.tzinfo is None else at.astimezone(CARE_INDIA_ZONE)).date()
    else:
        day = at
    start = day.year if day.month >= 4 else day.year - 1
    return start, start + 1


def fiscal_key(at: datetime | date | None = None) -> str:
    start, end = fiscal_year(at)
    return f"{start:04d}-{end:04d}"


def format_care_id(fiscal: str, serial: int) -> str:
    """Format a positive counter as the compact public candidate ID."""
    start, end = fiscal.split("-", 1)
    if serial < 1:
        raise ValueError("Care India serial must be positive.")
    return f"CI-{start[-2:]}-{end[-2:]}-{serial:03d}"


def allocate_care_id(db: Session, at: datetime | date | None = None) -> str:
    """Atomically reserve the next Care India ID in this database transaction."""
    fiscal = fiscal_key(at)
    serial = db.execute(text("""
        INSERT INTO crm_candidate_fiscal_counters (fiscal_year, last_serial)
        VALUES (:fiscal_year, 1)
        ON CONFLICT (fiscal_year) DO UPDATE
        SET last_serial = crm_candidate_fiscal_counters.last_serial + 1
        RETURNING last_serial
    """), {"fiscal_year": fiscal}).scalar_one()
    return format_care_id(fiscal, int(serial))
