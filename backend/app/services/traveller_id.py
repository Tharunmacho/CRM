"""Care India TRAVELLER numbers: the second, separate ID series.

``Candidate.care_id`` (``CI-001``, see ``care_india_id``) numbers every candidate who enters the Data Bank.  The traveller
number numbers only the people who actually travelled:

    CI 01, CI 02, ... CI 99, CI 100, CI 101, ...

The people who had travelled before this format existed were numbered once, by migration 0057, in the order of their DATE OF
TRAVEL (oldest = ``CI 01``); everybody after them continues the same series in the order they become travellers.

Both start with ``CI`` (the agency), but they are different series with different counters
(``crm_candidate_id_counters`` for the Data Bank ID, ``crm_traveller_id_counters`` here).

* The series is continuous: the next traveller always gets one more than the highest number ever issued, it never restarts
  (not each April, not ever) and a number is never reused, even if its candidate is deleted.
* A candidate "travels" when their placement becomes ``deployed`` (stage 13, the air ticket, which needs the actual ticket and a travel
  date; see ``domain.stages._stage12``).  The ID is assigned there, dynamically and never before, in the same transaction,
  by ``assign_traveller_id``.
* It is immutable once assigned and unique.  Reopening stage 13 afterwards keeps the number; completing the stage again
  returns the same one.
* Serial numbers are reserved with one PostgreSQL upsert on a single counter row, so concurrent completions never receive
  the same number and a rolled-back transaction does not consume one.

Everything about the format lives in this module (and the matching regex in migration 0057 / the model's check
constraint): change ``PREFIX`` / ``format_traveller_id`` to change what the public ID looks like.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


PREFIX = "CI"
COUNTER_KEY = "GLOBAL"


def format_traveller_id(serial: int) -> str:
    """Format a positive counter as the public traveller number (``CI 07``; it simply grows wider after ``CI 99``: ``CI 100``)."""
    if serial < 1:
        raise ValueError("Traveller serial must be positive.")
    return f"{PREFIX} {serial:02d}"


def allocate_traveller_id(db: Session) -> str:
    """Atomically reserve the next traveller ID in this database transaction."""
    serial = db.execute(text("""
        INSERT INTO crm_traveller_id_counters (series, last_serial)
        VALUES (:series, 1)
        ON CONFLICT (series) DO UPDATE
        SET last_serial = crm_traveller_id_counters.last_serial + 1
        RETURNING last_serial
    """), {"series": COUNTER_KEY}).scalar_one()
    return format_traveller_id(int(serial))


def assign_traveller_id(db: Session, candidate) -> str:
    """Give `candidate` their traveller ID once; return it. Never reassigns.

    The caller holds the candidate row lock (every deployment path does), so two requests cannot both number the same
    person; the immutability trigger in migration 0041 is the database's second line of defence.
    """
    if candidate.traveller_id:
        return candidate.traveller_id
    candidate.traveller_id = allocate_traveller_id(db)
    return candidate.traveller_id
