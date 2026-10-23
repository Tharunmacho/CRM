"""Recipient-consistent notices for committed changes of candidate ownership."""
from sqlalchemy import select

from app.db.models import Notification, Role, User


def notify_assignment_changes(db, changes, *, reason, users=None):
    """Add notices to the caller's transaction; repeated/no-op rows are ignored.

    Each change is (candidate ID, candidate name, previous owner, new owner).
    Account administration can supply its already-locked, updated user rows.
    """
    return _notify_changes(db, changes, reason=reason, users=users, task=False)


def notify_task_assignment_changes(db, changes, *, reason, users=None):
    """Notify task-only ownership repairs not already covered by a candidate notice."""
    return _notify_changes(db, changes, reason=reason, users=users, task=True)


def _notify_changes(db, changes, *, reason, users, task):
    changed = {identifier: (name, previous, target) for identifier, name, previous, target in changes
               if target and previous != target}
    if not changed:
        return 0
    people = users if users is not None else db.execute(select(User.id, User.name, User.role, User.active)).all()
    active = {person.id: person for person in people if person.active}
    supervisors = {person.id for person in active.values() if person.role in (Role.admin, Role.manager)}
    count = 0
    for identifier, (name, _previous, target) in changed.items():
        recipients = supervisors | ({target} if target in active else set())
        target_name = active[target].name if target in active else 'the assigned recruiter'
        for recipient in sorted(recipients):
            db.add(Notification(user_id=recipient, kind='assignment', title='Task assigned' if task else 'Candidate assigned',
                detail=f'{name} · Assigned to {target_name}. {reason}',
                link_to=f'/tasks?task={identifier}' if task else f'/candidates/{identifier}'))
            count += 1
    return count
