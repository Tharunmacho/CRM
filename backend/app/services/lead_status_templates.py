"""Lead sheet status templates: the statuses a recruiter picks besides the nine built-in ones, shared by every user.

Storage (migration 0050). `crm_leads.status` keeps holding one of the nine built-in codes (models.LEAD_STATUSES): its CHECK
constraint, its indexes and every query, report, filter and dashboard figure that reads it are unchanged. A lead that holds a template
has TWO values: `status`, the built-in code the template COUNTS AS, and `status_label`, the template's name (NULL = the built-in
status as it is, which is every lead that existed before). The Lead sheet shows `status_label` when there is one.

How a template counts (decided by its tone, fixed when the template is created; the same rule for the three built-in templates):

  tone      counts as (`status`)   in the reports
  neutral   contacted              contacted; a lead contact that did connect: "spoke, no decision yet"
  positive  interested             contacted and interested (like Interested)
  warning   contacted              a CONTACT ATTEMPT that did not connect: counts as contacted / called, but is NOT reached (it is the
                                   "No answer" group of the lead feedback report) and NOT interested
  negative  not_interested         a negative end state like Not interested: the lead is closed, no follow-up is due

The built-in templates: Ringing (warning), No incoming calls (warning), Not eligible (negative). Anything a user adds counts as
"contacted / neutral" unless they choose another tone. The status activity written for a change keeps `meta.to` = the counting code, adds
`meta.toLabel` (the template name) and, for a warning tone, `meta.attempt = true`; the lead feedback report reads that flag.

Rules: a name is trimmed, inner spaces collapsed, 1 to 40 characters, no comma (a comma separates statuses in a filter), unique ignoring
case and never equal to a built-in status name. Anybody signed in adds a template; an admin or MD renames (the leads that hold it follow),
hides or deletes it. Built-in templates cannot be renamed or deleted (they can be hidden). A lead whose template is hidden or deleted
keeps its status text.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LEAD_STATUS_TONES, LEAD_STATUSES, Lead, LeadStatusTemplate

MAX_LENGTH = 40
DEFAULT_TONE = "neutral"
COUNTS_AS = {"neutral": "contacted", "positive": "interested", "warning": "contacted", "negative": "not_interested"}
ATTEMPT_TONE = "warning"   # a contact attempt that did not connect (see the module docstring)
# What a built-in status is called on the screens (the Lead sheet's wording and the server's own), lower-cased: none can be a template name.
BUILTIN_NAMES = frozenset(name.lower() for name in (
    *LEAD_STATUSES, "Not contacted", "Contacted", "Interested", "Resume requested", "Resume received", "Interested (resume in)", "Converted",
    "Not interested", "Not reachable", "Wrong number"))
# Built-in statuses a person may choose by hand, in the Lead sheet's order (converted comes only from Convert to candidate).
SETTABLE_CODES = tuple(code for code in LEAD_STATUSES if code != "converted")


def fail(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **extra})


def counts_as(tone: str) -> str:
    return COUNTS_AS.get(tone, COUNTS_AS[DEFAULT_TONE])


def is_attempt(tone: str | None) -> bool:
    return tone == ATTEMPT_TONE


def clean_tone(value: str | None) -> str:
    tone = (value or DEFAULT_TONE).strip().lower()
    if tone not in LEAD_STATUS_TONES:
        raise fail(422, "invalid_request", f"tone must be one of: {', '.join(LEAD_STATUS_TONES)}.")
    return tone


def clean_name(value: str | None) -> str:
    """The name as it is stored: trimmed, inner spaces collapsed. 422 when empty, over 40 characters, with a comma or a built-in's name."""
    name = " ".join(str(value or "").split())
    if not 1 <= len(name) <= MAX_LENGTH:
        raise fail(422, "invalid_request", f"Enter the status name, 1 to {MAX_LENGTH} characters.")
    if "," in name:
        raise fail(422, "invalid_request", "A status name cannot contain a comma.")
    if name.lower() in BUILTIN_NAMES:
        raise fail(409, "status_exists", f"\"{name}\" is already one of the standard statuses.")
    return name


def template_out(row: LeadStatusTemplate, count: int | None = None) -> dict:
    out = {"id": row.id, "name": row.name, "tone": row.tone, "active": row.active, "builtin": row.builtin, "sortOrder": row.sort_order,
           "countsAs": counts_as(row.tone)}
    if count is not None:
        out["count"] = count
    return out


def by_name(db: Session, name: str, *, lock: bool = False) -> LeadStatusTemplate | None:
    query = select(LeadStatusTemplate).where(func.lower(LeadStatusTemplate.name) == name.lower())
    return db.scalar(query.with_for_update() if lock else query)


def ordered(rows: list[LeadStatusTemplate]) -> list[LeadStatusTemplate]:
    """Built-in templates first (their own order), then the rest alphabetically."""
    return sorted(rows, key=lambda row: (0, row.sort_order, row.name.lower()) if row.builtin else (1, 0, row.name.lower()))


def list_templates(db: Session, *, include_hidden: bool = False) -> list[LeadStatusTemplate]:
    query = select(LeadStatusTemplate)
    if not include_hidden:
        query = query.where(LeadStatusTemplate.active.is_(True))
    return ordered(list(db.scalars(query)))


@dataclass(frozen=True)
class Target:
    """What a status choice means for the lead row: the counting code, the template name (None for a built-in) and its tone."""
    code: str
    label: str | None = None
    tone: str | None = None

    @property
    def attempt(self) -> bool:
        return is_attempt(self.tone)


def resolve_target(db: Session, value: str | None, *, current_label: str | None = None) -> Target:
    """A status choice as the API receives it: a built-in code ("contacted") or a template's name ("Ringing", any letter case).

    A hidden template is refused unless the lead already holds it (`current_label`). Converted is never chosen by hand."""
    text = " ".join(str(value or "").split())
    if text in LEAD_STATUSES:
        if text == "converted":
            raise fail(422, "invalid_request", "Use Convert to turn a lead into a candidate.")
        return Target(text)
    row = by_name(db, text) if text else None
    if row is None:
        raise fail(422, "invalid_request", "status must be one of the standard statuses or an existing status template.")
    if not row.active and (current_label or "").lower() != row.name.lower():
        raise fail(422, "invalid_request", f"The status \"{row.name}\" is switched off.")
    return Target(counts_as(row.tone), row.name, row.tone)


def label_is_attempt(db: Session, label: str | None) -> bool:
    """True when `label` names a template with the contact-attempt tone (a call that connects ends such a status)."""
    if not label:
        return False
    return db.scalar(select(LeadStatusTemplate.tone).where(func.lower(LeadStatusTemplate.name) == label.lower())) == ATTEMPT_TONE


def check_filter_names(db: Session, names: list[str]) -> None:
    """A status filter value that is not a built-in code must be a template or a label some lead holds (else 422, like any unknown status)."""
    wanted = {name.lower() for name in names}
    if not wanted:
        return
    known = {n.lower() for n in db.scalars(select(LeadStatusTemplate.name).where(func.lower(LeadStatusTemplate.name).in_(wanted)))}
    missing = wanted - known
    if missing:
        known |= {n.lower() for n in db.scalars(select(func.lower(Lead.status_label)).where(func.lower(Lead.status_label).in_(missing)).distinct())}
    unknown = [name for name in names if name.lower() not in known]
    if unknown:
        raise fail(422, "invalid_request", f"Unknown status: {', '.join(unknown)}.")
