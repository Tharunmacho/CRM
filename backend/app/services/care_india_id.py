"""Care India candidate identifiers.

``Candidate.ref`` is a long-lived CRM/technical reference (``CND-*``) and is
used in historical links, messages and imports.  The human-facing Care India
ID is deliberately a second, immutable number: one simple continuous series,
given to every candidate when they are created, always the next number after the highest one in the Data Bank (a deleted
candidate's number is given again, only ever at the end of the series):

    CI-001, CI-002, ... CI-999, CI-1000, ...

There is no financial-year part and no yearly restart.  The number is read under one
transaction lock, so two concurrent intakes cannot receive the same number and a rolled-back transaction does not
consume one.
The database migration also contains a trigger backstop for future direct
inserts that forget to call this service.

A number is NOT reserved for ever: the next number is one more than the highest ``care_id`` that exists in the Data Bank at that
moment.  When the candidate holding the highest number is deleted (a wrong import, a test), the next candidate is given that number
again; only the end of the series is ever given again, a number lower than the highest is never given twice because its holder is
still there.  Consequence: a Care India ID typed or copied elsewhere (an invoice, a WhatsApp or e-mail message, a spreadsheet) is
unique among the candidates that exist now, but is not unique over time: the same text can have meant two different people.  Those
copies must carry the candidate's ``ref`` (``CND-*``, never reused) or an internal id when they have to stay unambiguous.
Callers must take the number inside the same transaction that creates the candidate, so the lock is held until that candidate is
committed (see ``allocate_care_id``).
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


# One lock for the whole series, held until the end of the transaction that took it.
_LOCK_KEY = 7_340_001


def allocate_care_id(db: Session) -> str:
    """Reserve the next Care India ID in this database transaction: the highest number that exists in the Data Bank plus one.

    The number is read from the candidates themselves, so a candidate that was deleted (a wrong import, a test) gives its number
    back and the next candidate takes it: the series has no holes at the end and never runs ahead of the Data Bank. A transaction
    lock makes two intakes at the same moment take turns, each seeing the number the other has just used, and a rolled-back
    transaction does not consume one. The counter row is kept equal to the number given, for the database trigger backstop.

    The lock is a transaction-level advisory lock (key `_LOCK_KEY`): it is released only when the caller's transaction commits or
    rolls back, so the candidate that takes the number must be created (and committed) in that same transaction. A number lower than
    the highest is never given again (its holder still exists), but a deleted tail number is: ID text copied elsewhere is therefore
    not unique over time (see the module docstring).
    """
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    # The lock is a statement of its own on purpose: a statement reads the data as it was when IT started (READ COMMITTED), so the
    # number must be read by a statement that starts after the lock is held. Reading it and writing the counter row is then ONE
    # statement (one round trip fewer on the hosted database).
    serial = db.execute(text("""
        WITH next_number AS (
            SELECT COALESCE(MAX(substring(care_id from '^CI-([0-9]+)$')::bigint), 0) + 1 AS serial
            FROM crm_candidates WHERE care_id ~ '^CI-[0-9]+$'
        )
        INSERT INTO crm_candidate_id_counters (series, last_serial) SELECT :series, serial FROM next_number
        ON CONFLICT (series) DO UPDATE SET last_serial = EXCLUDED.last_serial
        RETURNING last_serial
    """), {"series": COUNTER_KEY}).scalar_one()
    return format_care_id(int(serial))
