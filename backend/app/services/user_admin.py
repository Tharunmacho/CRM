"""Transactional account administration; credentials never enter audit metadata."""
from __future__ import annotations
from datetime import datetime, timezone
import logging

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import AssignmentPointer, AuditEntry, Candidate, Role, Session as LoginSession, Task, TaskEvent, User


log = logging.getLogger(__name__)


def _assign_waiting_after_change(db: Session, actor_id: str, actor_name: str) -> None:
    """Hand owner-less candidates to staff who just became eligible.

    Best effort and after the account change is committed: a failure here is rolled back
    on its own and must never fail the administrator's request."""
    try:
        from app.services.candidate_review import assign_waiting_candidates
        assign_waiting_candidates(db, actor_id, actor_name)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("Waiting candidates could not be assigned after an account change; use Assign waiting candidates.", exc_info=True)


def _directory(db: Session, actor: User) -> list[User]:
    # The same cursor lock used by manual/email intake prevents assignment to a
    # recruiter while that account is being disabled or removed from the rotation.
    db.execute(insert(AssignmentPointer).values(id=1).on_conflict_do_nothing(index_elements=['id']))
    db.scalar(select(AssignmentPointer).where(AssignmentPointer.id == 1).with_for_update())
    users = list(db.scalars(select(User).order_by(User.id).with_for_update().execution_options(populate_existing=True)))
    current = next((row for row in users if row.id == actor.id), None)
    if current is None or not current.active or current.role != Role.admin:
        raise HTTPException(403, 'Only an active administrator can manage accounts.')
    return users


def _find(users: list[User], user_id: str) -> User:
    row = next((user for user in users if user.id == user_id), None)
    if row is None:
        raise HTTPException(404, 'User not found.')
    return row


def _audit(db: Session, actor: User, user: User, action: str, meta: dict | None = None) -> None:
    db.add(AuditEntry(actor_id=actor.id, action=action, target=user.name,
                      entity_type='user', entity_id=user.id, meta=meta or {}))


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'An account with this email already exists. Refresh and try again.')


def _check_email(users: list[User], email: str, except_id: str | None = None) -> None:
    if any(user.id != except_id and user.email.lower() == email.lower() for user in users):
        raise HTTPException(409, 'An account with this email already exists.')


def _revoke(db: Session, user: User) -> int:
    from app.services.staff_activity import close_user_presence
    # The helper locks User → Login → Presence, also used by heartbeat/logout.
    close_user_presence(db, user.id)
    return db.execute(delete(LoginSession).where(LoginSession.user_id == user.id)).rowcount


def create_user(db: Session, actor: User, values: dict) -> User:
    users = _directory(db, actor)
    values = dict(values)
    _check_email(users, values['email'])
    password = values.pop('password')
    if values['role'] != Role.staff:
        values['receives_auto_assign'] = False
        values['on_leave'] = False
    if not values.get('active', True):
        values['receives_auto_assign'] = False
    user = User(**values, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    _audit(db, actor, user, 'User created', {'role': user.role.value, 'active': user.active})
    _commit(db)
    from app.services.candidate_review import eligible_for_assignment
    if eligible_for_assignment(user):
        _assign_waiting_after_change(db, actor.id, actor.name)
    return user


def _transfer(db: Session, actor: User, user: User, users: list[User], target_id: str | None) -> tuple[dict, list, list]:
    candidates = list(db.scalars(select(Candidate).where(Candidate.owner_id == user.id).order_by(Candidate.id).with_for_update()))
    open_count = db.scalar(select(func.count()).select_from(Task).where(
        Task.owner_id == user.id, Task.completed_at.is_(None), Task.cancelled_at.is_(None))) or 0
    if not candidates and not open_count:
        return {'candidates': 0, 'tasks': 0}, [], []
    if not target_id:
        raise HTTPException(422, 'Choose a staff member to receive all owned candidates and open tasks.')
    target = _find(users, target_id)
    from app.services.attendance_availability import unavailable_staff_ids
    if target.id == user.id or not target.active or target.role != Role.staff or target.on_leave or target.id in unavailable_staff_ids(db):
        raise HTTPException(422, 'Choose another active staff member who is not on leave.')
    ids = [candidate.id for candidate in candidates]
    for candidate in candidates:
        candidate.owner_id = target.id
        db.add(AuditEntry(actor_id=actor.id, action='Candidate reassigned during account change',
            target=candidate.name, entity_type='candidate', entity_id=candidate.id,
            meta={'fromUserId': user.id, 'toUserId': target.id}))
    tasks = list(db.scalars(select(Task).where(
        or_(Task.owner_id == user.id, Task.candidate_id.in_(ids)),
        Task.completed_at.is_(None), Task.cancelled_at.is_(None),
    ).order_by(Task.id).with_for_update()))
    at = datetime.now(timezone.utc)
    moved = 0
    task_only_changes = []
    for task in tasks:
        if task.owner_id == target.id:
            continue
        if task.candidate_id not in ids:
            task_only_changes.append((task.id, task.title, task.owner_id, target.id))
        db.add(TaskEvent(kind='assignment', task_id=task.id, candidate_id=task.candidate_id,
            owner_id=target.id, event_key=f'assignment:{task.id}:{at.isoformat()}:{target.id}', occurred_at=at,
            meta={'fromOwnerId': task.owner_id, 'toOwnerId': target.id}))
        task.owner_id = target.id
        moved += 1
    return {'candidates': len(candidates), 'tasks': moved, 'toUserId': target.id}, [
        (candidate.id, candidate.name, user.id, target.id) for candidate in candidates], task_only_changes


def update_user(db: Session, actor: User, user_id: str, values: dict) -> User:
    users = _directory(db, actor)
    user = _find(users, user_id)
    values = dict(values)
    target_id = values.pop('reassign_to_user_id', None)
    next_active = values.get('active', user.active)
    next_role = values.get('role', user.role)
    loses_admin = user.role == Role.admin and (not next_active or next_role != Role.admin)
    if loses_admin and sum(row.active and row.role == Role.admin for row in users) <= 1:
        raise HTTPException(409, 'The last active administrator must remain active with the Admin role.')
    if user.id == actor.id and (not next_active or next_role != Role.admin):
        raise HTTPException(409, 'You cannot deactivate your own account or remove your own Admin role.')
    if 'email' in values:
        _check_email(users, values['email'], user.id)
    if next_role != Role.staff:
        values.update(receives_auto_assign=False, on_leave=False)
    elif not next_active:
        values['receives_auto_assign'] = False
    changes = {key: {'before': getattr(user, key), 'after': value} for key, value in values.items()
               if value != getattr(user, key)}
    transfer = None
    ownership_changes = []
    task_only_changes = []
    if not next_active or (user.role == Role.staff and next_role != Role.staff):
        transfer, ownership_changes, task_only_changes = _transfer(db, actor, user, users, target_id)
    revoke = not next_active or next_role != user.role or values.get('email', user.email) != user.email
    from app.services.candidate_review import eligible_for_assignment
    was_eligible = eligible_for_assignment(user)
    for key, value in values.items():
        setattr(user, key, value)
    became_eligible = not was_eligible and eligible_for_assignment(user)
    if ownership_changes or task_only_changes:
        from app.services.assignment_notifications import notify_assignment_changes, notify_task_assignment_changes
        notify_assignment_changes(db, ownership_changes,
            reason=f"Transferred during an account change by {actor.name}.", users=users)
        notify_task_assignment_changes(db, task_only_changes,
            reason=f"Transferred during an account change by {actor.name}.", users=users)
    revoked = _revoke(db, user) if revoke else 0
    if changes or transfer:
        _audit(db, actor, user, 'User updated' if next_active else 'User deactivated',
               {'changes': changes, 'transfer': transfer, 'revokedSessions': revoked})
    _commit(db)
    if became_eligible:
        _assign_waiting_after_change(db, actor.id, actor.name)
    return user


def reset_password(db: Session, actor: User, user_id: str, password: str) -> dict:
    user = _find(_directory(db, actor), user_id)
    user.password_hash = hash_password(password)
    count = _revoke(db, user)
    _audit(db, actor, user, 'Password reset', {'revokedSessions': count})
    _commit(db)
    return {'revokedSessions': count}


def force_logout(db: Session, actor: User, user_id: str) -> dict:
    user = _find(_directory(db, actor), user_id)
    count = _revoke(db, user)
    _audit(db, actor, user, 'Sessions revoked', {'revokedSessions': count})
    _commit(db)
    return {'revokedSessions': count}
