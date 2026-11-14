"""Care India TRAVELLER identifiers: the second, separate ID series.

``Candidate.care_id`` (``CI-26-27-001``, see ``care_india_id``) numbers every candidate who enters the Data Bank, by the
date they entered.  The traveller ID numbers only the people who actually travelled, by the date they travelled:

    CI-T-26-27-001

Both start with ``CI`` (the agency), but they are different series with different serials and different counters
(``crm_candidate_fiscal_counters`` for the Data Bank ID, ``crm_traveller_fiscal_counters`` here).

* The financial year is the Indian April--March year of the TRAVEL DATE (Asia/Kolkata), so the serial restarts at 001
  on 1 April of the travel date's year, not of the day the record was typed in.  A placement with no travel date on file
  falls back to the moment the travel stage was completed.
* A candidate "travels" when their placement becomes ``deployed`` (stage 11, which needs the actual ticket and a travel
  date; see ``domain.stages._stage11``).  The ID is assigned there, in the same transaction, by ``assign_traveller_id``.
* It is immutable once assigned and unique.  Reopening stage 11 afterwards keeps the number; completing the stage again
  returns the same one.
* Serial numbers are reserved with one PostgreSQL upsert, so concurrent completions never receive the same number and a
  rolled-back transaction does not consume one.

Everything about the format lives in this module (and the matching regex in migration 0041 / the model's check
constraint): change ``PREFIX`` / ``format_traveller_id`` to change what the public ID looks like.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.care_india_id import fiscal_key


PREFIX = "CI-T"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def format_traveller_id(fiscal: str, serial: int) -> str:
    """Format a positive counter as the compact public traveller ID (``CI-T-26-27-001``)."""
    start, end = fiscal.split("-", 1)
    if serial < 1:
        raise ValueError("Traveller serial must be positive.")
    return f"{PREFIX}-{start[-2:]}-{end[-2:]}-{serial:03d}"


def travel_moment(travel_date: date | datetime | str | None, fallback: datetime | date | None = None) -> datetime | date:
    """The date that decides the financial year: the travel date, else `fallback`, else now.

    `travel_date` may be a date, a datetime or the stage's own ``YYYY-MM-DD`` text. Anything unreadable counts as absent.
    """
    if isinstance(travel_date, str):
        try:
            travel_date = date.fromisoformat(travel_date.strip()) if _DAY.fullmatch(travel_date.strip()) else None
        except ValueError:
            travel_date = None
    if travel_date is not None:
        return travel_date
    return fallback if fallback is not None else datetime.now(timezone.utc)


def allocate_traveller_id(db: Session, at: datetime | date | None = None) -> str:
    """Atomically reserve the next traveller ID of the financial year containing `at` in this database transaction."""
    fiscal = fiscal_key(at)
    serial = db.execute(text("""
        INSERT INTO crm_traveller_fiscal_counters (fiscal_year, last_serial)
        VALUES (:fiscal_year, 1)
        ON CONFLICT (fiscal_year) DO UPDATE
        SET last_serial = crm_traveller_fiscal_counters.last_serial + 1
        RETURNING last_serial
    """), {"fiscal_year": fiscal}).scalar_one()
    return format_traveller_id(fiscal, int(serial))


def assign_traveller_id(db: Session, candidate, travel_date: date | datetime | str | None = None,
                        fallback: datetime | date | None = None) -> str:
    """Give `candidate` their traveller ID once; return it. Never reassigns.

    The caller holds the candidate row lock (every deployment path does), so two requests cannot both number the same
    person; the immutability trigger in migration 0041 is the database's second line of defence.
    """
    if candidate.traveller_id:
        return candidate.traveller_id
    candidate.traveller_id = allocate_traveller_id(db, travel_moment(travel_date, fallback))
    return candidate.traveller_id
