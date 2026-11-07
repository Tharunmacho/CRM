"""Late matching: a candidate who appears (or whose phone / e-mail changes) after a lead was stored flags that lead.

At import time a lead whose phone or e-mail is already a candidate gets `matched_candidate_id` (lead_import). This module does the
same the other way round: whenever a candidate is created or its phone, WhatsApp number or e-mail changes, every stored lead
with the same person key is flagged in the SAME transaction, with ONE set-based statement, so the Lead sheet shows "In Data Bank"
for people who turn up in the Data Bank later (an intake, a walk-in, a manual entry, a merge).

Keys are those of the lead sheet (docs/workflows/lead-sheet-contract.md): the phone key is the last 10 digits (all digits when
fewer), the e-mail key is the lower-cased address. Only leads that are not yet matched and not yet linked to a candidate are
touched; a flag is never overwritten.

How it is wired: `install()` registers a SQLAlchemy `after_flush` hook, so every path that writes a Candidate through the
ORM (create, patch, merge, e-mail / WhatsApp intake, manual extraction) is covered without each caller remembering. Core
`UPDATE crm_candidates ...` statements are not seen; `backfill()` (python -m app.cli backfill-lead-matches) catches those up.
"""
from __future__ import annotations

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from app.services.savepoint_once import guarded

WATCHED = ("phone", "whatsapp", "email")

# Candidate-side keys, spelled exactly as lead_import._find_candidates computes them.
_PHONE_KEY = "right(regexp_replace(coalesce(c.{col}, ''), '\\D', '', 'g'), 10)"

_MATCH_SQL = """
UPDATE crm_leads l SET matched_candidate_id = pick.candidate_id, updated_at = now()
FROM (
  SELECT DISTINCT ON (hits.lead_id) hits.lead_id, hits.candidate_id FROM (
    SELECT l.id AS lead_id, c.id AS candidate_id, c.created_at
    FROM crm_candidates c JOIN crm_leads l ON l.phone_key = {phone}
    WHERE {scope} AND l.matched_candidate_id IS NULL AND l.candidate_id IS NULL AND {phone} <> ''
    UNION ALL
    SELECT l.id, c.id, c.created_at
    FROM crm_candidates c JOIN crm_leads l ON l.phone_key = {whatsapp}
    WHERE {scope} AND l.matched_candidate_id IS NULL AND l.candidate_id IS NULL AND {whatsapp} <> ''
    UNION ALL
    SELECT l.id, c.id, c.created_at
    FROM crm_candidates c JOIN crm_leads l ON l.email_key = lower(c.email)
    WHERE {scope} AND l.matched_candidate_id IS NULL AND l.candidate_id IS NULL AND coalesce(c.email, '') <> ''
  ) hits ORDER BY hits.lead_id, hits.created_at, hits.candidate_id
) pick
WHERE l.id = pick.lead_id AND l.matched_candidate_id IS NULL AND l.candidate_id IS NULL
RETURNING l.id
"""


def _statement(everyone: bool):
    scope = "true" if everyone else "c.id = ANY(CAST(:ids AS text[]))"
    return text(_MATCH_SQL.format(phone=_PHONE_KEY.format(col="phone"), whatsapp=_PHONE_KEY.format(col="whatsapp"), scope=scope))


def match_candidates(connection_or_session, candidate_ids: list[str] | None) -> int:
    """Flag the stored leads that share a phone / WhatsApp number / e-mail with these candidates (all candidates when None).

    One statement. Returns how many leads were flagged. `connection_or_session` is a Session or a Connection.
    """
    if candidate_ids is not None and not candidate_ids:
        return 0
    everyone = candidate_ids is None
    result = connection_or_session.execute(_statement(everyone), {} if everyone else {"ids": candidate_ids})
    return len(result.all())


def backfill(db: Session) -> int:
    """One-off catch-up for existing data: flag every lead whose person is already in the Data Bank. Does not commit."""
    return match_candidates(db, None)


def clear_self_match(lead) -> None:
    """A lead that was converted to / linked with its candidate is not 'also in the Data Bank': drop the flag that names the same candidate."""
    if lead.matched_candidate_id and lead.matched_candidate_id == lead.candidate_id:
        lead.matched_candidate_id = None


# ------------------------------------------------------------------ the ORM hook

_installed = False


def _changed(obj) -> bool:
    state = inspect(obj)
    if state.transient or state.pending:
        return True
    return any(get_history(obj, name).has_changes() for name in WATCHED)


def _after_flush(session: Session, _context) -> None:
    ids = [obj.id for obj in list(session.new) + list(session.dirty) if getattr(obj, "__tablename__", None) == "crm_candidates" and obj.id and _changed(obj)]
    if not ids:
        return
    connection = session.connection()
    if connection.dialect.name != "postgresql":
        return
    try:
        # A missing table (database not migrated to the lead sheet yet) must never break a candidate save, so the first run is
        # inside a savepoint; once it has worked the table is there and the statement runs without the two extra round trips.
        with guarded(connection, "lead_matching.match"):
            match_candidates(connection, ids)
    except Exception:  # noqa: BLE001 - late matching is a convenience, the candidate write comes first
        pass


def install() -> None:
    """Register the hook once per process (idempotent)."""
    global _installed
    if not _installed:
        event.listen(Session, "after_flush", _after_flush)
        _installed = True
