"""Plain-English notices for changes of ownership (candidates, tasks and leads).

Every notice goes to the one person who has to act on it and says what happened, to what, and who did it:

  * the NEW owner:      "Administrator assigned you SUBRAMANIAN (CI-26-27-045 · CND-0012). Reason: ..."
  * the PREVIOUS owner: "SUBRAMANIAN (...) was moved from you to Gayathri by Administrator."
  * nobody else. The person who made the change is never notified of their own action, and administrators do not
    get a copy of routine assignments (they only hear about things an admin must do: a candidate nobody could take,
    a recruiter asking for a candidate).

Several items for one person in one action become ONE notice ("Administrator assigned you 12 candidates: A, B, C
and 9 more"), never one row per item. The reason is quoted only when somebody typed one.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Candidate, Notification, Role, User

# What the reassign dialog writes when the admin typed nothing; it is not a reason worth showing.
DEFAULT_REASONS = {"assigned manually"}
NAMES_SHOWN = 3


def candidate_label(name, care_id=None, ref=None) -> str:
    """'NAME (CI-26-27-045 · CND-0012)', with whichever of the two identifiers is known."""
    parts = [part for part in (care_id, ref) if part]
    return f"{name} ({' · '.join(parts)})" if parts else str(name)


def typed_reason(reason) -> str | None:
    text = (reason or "").strip()
    return None if not text or text.lower().rstrip(".") in DEFAULT_REASONS else text


class _Kind:
    def __init__(self, noun, plural, assigned_kind, removed_kind, assigned_title, removed_title, link, list_link):
        self.noun, self.plural = noun, plural
        self.assigned_kind, self.removed_kind = assigned_kind, removed_kind
        self.assigned_title, self.removed_title = assigned_title, removed_title
        self.link, self.list_link = link, list_link


CANDIDATES = _Kind("candidate", "candidates", "candidate_assigned", "candidate_removed",
                   "New candidate assigned to you", "Candidate taken off your list",
                   lambda identifier: f"/candidates/{identifier}/application", "/candidates")
TASKS = _Kind("task", "tasks", "task_assigned", "task_removed",
              "New task assigned to you", "Task taken off your list",
              lambda identifier: f"/tasks?task={identifier}", "/tasks")
LEADS = _Kind("lead", "leads", "lead_assignment", "lead_removed",
              "New lead assigned to you", "Lead taken off your list",
              lambda identifier: "/leads", "/leads")


def _names(labels) -> str:
    shown = ", ".join(labels[:NAMES_SHOWN])
    rest = len(labels) - NAMES_SHOWN
    return f"{shown} and {rest} more" if rest > 0 else shown


def _reason(reason) -> str:
    text = typed_reason(reason)
    return f' Reason: "{text}"' if text else ""


def _people(db, users):
    rows = users if users is not None else db.execute(select(User.id, User.name, User.role, User.active)).all()
    return {person.id: person for person in rows}


def _emit(db, kind: _Kind, items, *, actor_id, actor_name, reason, how, users) -> int:
    """items: (identifier, label, previous owner, new owner). Adds the grouped notices; returns how many rows."""
    items = [item for item in items if item[3] and item[2] != item[3]]
    if not items:
        return 0
    people = _people(db, users)
    active = {identifier for identifier, person in people.items() if person.active}
    how = f" {how.strip()}" if how else ""
    why = _reason(reason)
    count = 0

    arriving: dict[str, list] = {}
    leaving: dict[str, list] = {}
    for item in items:
        arriving.setdefault(item[3], []).append(item)
        if item[2]:
            leaving.setdefault(item[2], []).append(item)

    for owner, rows in sorted(arriving.items()):
        if owner == actor_id or owner not in active:
            continue
        labels = [row[1] for row in rows]
        if len(rows) == 1:
            title = kind.assigned_title
            body = (f"{actor_name} assigned you {labels[0]}{how}." if actor_name
                    else f"{labels[0]} was assigned to you{how}.")
            link = kind.link(rows[0][0])
        else:
            title = f"{len(rows)} {kind.plural} assigned to you"
            body = (f"{actor_name} assigned you {len(rows)} {kind.plural}{how}: {_names(labels)}." if actor_name
                    else f"{len(rows)} {kind.plural} were assigned to you{how}: {_names(labels)}.")
            link = kind.list_link
        db.add(Notification(user_id=owner, kind=kind.assigned_kind, title=title, detail=body + why, link_to=link))
        count += 1

    for previous, rows in sorted(leaving.items()):
        if previous == actor_id or previous not in active:
            continue
        labels = [row[1] for row in rows]
        targets = {row[3] for row in rows}
        to = f" to {people[next(iter(targets))].name}" if len(targets) == 1 and next(iter(targets)) in people else ""
        by = f" by {actor_name}" if actor_name else ""
        if len(rows) == 1:
            title = kind.removed_title
            body = f"{labels[0]} was moved from you{to}{by}{how}."
        else:
            title = f"{len(rows)} {kind.plural} taken off your list"
            body = f"{len(rows)} {kind.plural} were moved from you{to}{by}{how}: {_names(labels)}."
        db.add(Notification(user_id=previous, kind=kind.removed_kind, title=title, detail=body + why, link_to=kind.list_link))
        count += 1
    return count


def _candidate_items(db, changes):
    identifiers = {change[0] for change in changes}
    known = {row.id: row for row in db.execute(
        select(Candidate.id, Candidate.care_id, Candidate.ref).where(Candidate.id.in_(identifiers))).all()}
    return [(identifier, candidate_label(name, getattr(known.get(identifier), "care_id", None), getattr(known.get(identifier), "ref", None)),
             previous, target) for identifier, name, previous, target in changes]


def notify_assignment_changes(db, changes, *, actor_id=None, actor_name=None, reason=None, how=None, users=None) -> int:
    """Candidate ownership changes: each change is (candidate ID, candidate name, previous owner, new owner).

    Notices go in the caller's transaction. `reason` is only what a person typed; `how` is a short phrase for a
    system cause ("from the waiting list"). Account administration can supply its already-locked, updated user rows."""
    changes = [change for change in changes if change[3] and change[2] != change[3]]
    if not changes:
        return 0
    return _emit(db, CANDIDATES, _candidate_items(db, changes), actor_id=actor_id, actor_name=actor_name,
                 reason=reason, how=how, users=users)


def notify_task_assignment_changes(db, changes, *, actor_id=None, actor_name=None, reason=None, how=None, users=None) -> int:
    """Task-only ownership repairs not already covered by a candidate notice: (task ID, task title, previous, new)."""
    items = [(identifier, f'"{title}"', previous, target) for identifier, title, previous, target in changes]
    return _emit(db, TASKS, items, actor_id=actor_id, actor_name=actor_name, reason=reason, how=how, users=users)


def _taken_back(db, kind: _Kind, items, *, actor_id, actor_name, reason, users) -> int:
    """items: (identifier, label, previous owner). Nobody owns them now; each previous owner gets one grouped notice."""
    people = _people(db, users)
    grouped: dict[str, list[str]] = {}
    for _identifier, label, previous in items:
        if previous:
            grouped.setdefault(previous, []).append(label)
    by = f" by {actor_name}" if actor_name else ""
    why = _reason(reason)
    count = 0
    for previous, labels in sorted(grouped.items()):
        if previous == actor_id or previous not in people or not people[previous].active:
            continue
        if len(labels) == 1:
            title, body = kind.removed_title, f"{labels[0]} was taken off your list{by}. It is not assigned to anyone now."
        else:
            title = f"{len(labels)} {kind.plural} taken off your list"
            body = f"{len(labels)} {kind.plural} were taken off your list{by}. They are not assigned to anyone now: {_names(labels)}."
        db.add(Notification(user_id=previous, kind=kind.removed_kind, title=title, detail=body + why, link_to=kind.list_link))
        count += 1
    return count


def notify_candidates_unassigned(db, changes, *, actor_id=None, actor_name=None, reason=None, users=None) -> int:
    """Candidates taken back from their recruiter: (candidate ID, name, previous owner)."""
    changes = [change for change in changes if change[2]]
    if not changes:
        return 0
    items = _candidate_items(db, [(identifier, name, previous, None) for identifier, name, previous in changes])
    return _taken_back(db, CANDIDATES, [(i, label, previous) for i, label, previous, _ in items],
                       actor_id=actor_id, actor_name=actor_name, reason=reason, users=users)


def notify_lead_changes(db, changes, *, actor_id=None, actor_name=None, reason=None, how=None, users=None) -> int:
    """Lead ownership changes: (lead ID, lead name or phone, previous owner, new owner); a new owner of None is a take-back."""
    items = [(identifier, name or "a lead", previous, target) for identifier, name, previous, target in changes]
    count = _emit(db, LEADS, items, actor_id=actor_id, actor_name=actor_name, reason=reason, how=how, users=users)
    back = [(identifier, label, previous) for identifier, label, previous, target in items if target is None]
    return count + (_taken_back(db, LEADS, back, actor_id=actor_id, actor_name=actor_name, reason=reason, users=users) if back else 0)


def notify_lead_import(db, per_owner: dict, *, actor_id, actor_name, file_name) -> int:
    """One notice per recruiter who received leads from an import (not the person who imported)."""
    count = 0
    for owner, number in sorted(per_owner.items()):
        if owner == actor_id or not number:
            continue
        noun = "lead" if number == 1 else "leads"
        db.add(Notification(user_id=owner, kind=LEADS.assigned_kind,
                            title=f"{number} new {noun} assigned to you" if number > 1 else LEADS.assigned_title,
                            detail=f"{actor_name} imported {file_name or 'a lead sheet'} and assigned you {number} {noun}.",
                            link_to=LEADS.list_link))
        count += 1
    return count


def notify_new_candidate(db, candidate, owner, *, actor_id=None, actor_name=None, channel=None) -> int:
    """A candidate created by a person or by intake (`channel` = 'email' / 'WhatsApp').

    With an owner: only the owner is told (nobody tells the person who created it about their own action; admins get no
    copy). With no owner: the admins and managers are told it is waiting for them to assign (again not the creator)."""
    label = candidate_label(candidate.name, getattr(candidate, "care_id", None), getattr(candidate, "ref", None))
    link = CANDIDATES.link(candidate.id)
    if owner is not None:
        if owner.id == actor_id:
            return 0
        if actor_name:
            body = f"{actor_name} added {label} and assigned it to you."
        elif channel:
            body = f"{label} arrived by {channel} and was assigned to you automatically."
        else:
            body = f"{label} was assigned to you automatically."
        db.add(Notification(user_id=owner.id, kind=CANDIDATES.assigned_kind, title=CANDIDATES.assigned_title, detail=body, link_to=link))
        return 1
    came = f"arrived by {channel}" if channel else (f"was added by {actor_name}" if actor_name else "was added")
    recipients = db.scalars(select(User.id).where(User.active.is_(True), User.role.in_([Role.admin, Role.manager]))).all()
    count = 0
    for recipient in sorted(set(recipients) - {actor_id}):
        db.add(Notification(user_id=recipient, kind="candidate_waiting", title="Candidate waiting for a recruiter",
                            detail=f"{label} {came}, but no recruiter is available to take it. Open the candidate and assign a recruiter.",
                            link_to=link))
        count += 1
    return count
