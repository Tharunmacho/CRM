"""Follow-up reminders of the Lead sheet, kept as personal to-dos of the lead's owner.

A lead with a follow-up date (`crm_leads.next_follow_up_at`) has exactly one open to-do for its owner:
title "Follow up lead: <name>", due 09:00 agency time on the follow-up day, linking back to the lead
(`link_to = /leads?lead=<id>`, `link_label = LABEL`). It is the same idea as the candidate follow-up to-do
(`application_form.sync_follow_up_todo`) and uses the same to-do list and reminder worker, so no table is added.

The reminder follows the lead:

* date set or moved: the to-do is created / moved (and its reminder re-armed);
* date cleared, lead closed (converted, not interested, invalid, wrong number), lead deleted or lead without an owner: the open
  to-do is removed;
* lead reassigned: the to-do moves to the new owner;
* a to-do the person has already ticked off is left alone and is not created again for the same due time.

`sync` is one set-based statement for any number of leads, idempotent, and runs in the caller's transaction. Every code
path that changes `next_follow_up_at`, `status` or `owner_id` (or deletes leads) calls it with the affected ids. It never commits.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

LABEL = "Lead follow-up"
LINK_PREFIX = "/leads?lead="
TITLE_PREFIX = "Follow up lead: "
DUE_HOUR = "09:00"
CLOSED = ("converted", "not_interested", "invalid", "wrong_number")
CHUNK = 5000

# The reminders wanted for the given leads. Each of the three statements below starts with its own INSERT / UPDATE / DELETE (not a WITH),
# because the live change feed classifies writes by their first words and has to announce the "todos" topic.
_WANT = """(
  SELECT l.id AS lead_id, l.owner_id,
         CAST(:prefix AS text) || left(coalesce(nullif(btrim(l.name), ''), l.phone, 'lead'), 270) AS title,
         (((l.next_follow_up_at AT TIME ZONE CAST(:tz AS text))::date + time '09:00') AT TIME ZONE CAST(:tz AS text)) AS due
  FROM crm_leads l
  WHERE l.id = ANY(CAST(:ids AS text[])) AND l.next_follow_up_at IS NOT NULL AND l.owner_id IS NOT NULL
    AND l.status NOT IN ('converted', 'not_interested', 'invalid', 'wrong_number')
)"""

_REMOVE = text(f"""
DELETE FROM personal_todos t
WHERE t.link_label = CAST(:label AS text) AND t.done = false AND t.link_to = ANY(CAST(:links AS text[]))
  AND NOT EXISTS (SELECT 1 FROM {_WANT} w WHERE CAST(:link AS text) || w.lead_id = t.link_to)""")

_MOVE = text(f"""
UPDATE personal_todos t
SET owner_id = w.owner_id, title = w.title, due_at = w.due,
    reminded_at = CASE WHEN t.due_at IS DISTINCT FROM w.due THEN NULL ELSE t.reminded_at END
FROM {_WANT} w
WHERE t.link_label = CAST(:label AS text) AND t.done = false AND t.link_to = CAST(:link AS text) || w.lead_id
  AND (t.owner_id <> w.owner_id OR t.title <> w.title OR t.due_at IS DISTINCT FROM w.due)""")

_ADD = text(f"""
INSERT INTO personal_todos (id, owner_id, title, notes, due_at, priority, done, link_to, link_label)
SELECT md5(random()::text || clock_timestamp()::text || w.lead_id)::uuid::text, w.owner_id, w.title,
       'Follow-up date set on the lead sheet.', w.due, CAST('normal' AS todo_priority), false,
       CAST(:link AS text) || w.lead_id, CAST(:label AS text)
FROM {_WANT} w
WHERE NOT EXISTS (SELECT 1 FROM personal_todos t WHERE t.link_label = CAST(:label AS text) AND t.link_to = CAST(:link AS text) || w.lead_id
                  AND (t.done = false OR t.due_at = w.due))""")


def sync(db: Session, lead_ids: Iterable[str]) -> dict:
    """Make the open reminders of these leads match their follow-up date, owner and status. Returns {created, moved, removed}."""
    from app.services.daily_work import tz_name
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    out = {"created": 0, "moved": 0, "removed": 0}
    if not ids:
        return out
    db.flush()  # the caller's ORM changes must be visible to the statement
    zone = tz_name()
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        base = {"label": LABEL, "link": LINK_PREFIX}
        want = {**base, "ids": part, "tz": zone, "prefix": TITLE_PREFIX}
        out["removed"] += max(0, db.execute(_REMOVE, {**want, "links": [LINK_PREFIX + i for i in part]}).rowcount)
        out["moved"] += max(0, db.execute(_MOVE, want).rowcount)
        out["created"] += max(0, db.execute(_ADD, want).rowcount)
    return out


def sync_all(db: Session) -> dict:
    """One-off backfill: every lead with a follow-up date, plus every open reminder whose lead no longer needs it."""
    ids = list(db.scalars(text(
        "SELECT id FROM crm_leads WHERE next_follow_up_at IS NOT NULL "
        "UNION SELECT substr(link_to, :n) FROM personal_todos WHERE link_label = :label AND done = false"
    ).bindparams(n=len(LINK_PREFIX) + 1, label=LABEL)))
    return sync(db, ids)
