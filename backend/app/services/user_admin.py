"""Transactional account administration; credentials never enter audit metadata."""
from __future__ import annotations
from datetime import datetime, timezone
import logging

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import is_admin_level, is_real_admin
from app.core.security import hash_password
from app.db.models import (AssignmentPointer, AuditEntry, Base, Candidate, Lead, Role, Session as LoginSession, Task, TaskEvent, User,
                           deleted_user_email)


log = logging.getLogger(__name__)


def _assign_waiting_after_change(db: Session, actor_id: str, actor_name: str) -> None:
    """Hand owner-less candidates to staff who just became eligible.

    Best effort and after the account change is committed: a failure here is rolled back
    on its own and must never fail the administrator's request."""
    try:
        from app.services.agency_settings import auto_assign_enabled
        if not auto_assign_enabled(db):  # recruiters are assigned by hand unless the agency switched automatic assignment on
            return
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
    if current is None or not current.active or not is_admin_level(current):
        raise HTTPException(403, 'Only an active admin or MD can manage accounts.')
    return users


def _find(users: list[User], user_id: str) -> User:
    row = next((user for user in users if user.id == user_id), None)
    if row is None or row.deleted:  # a deleted account is gone for every screen and action
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
        raise HTTPException(409, 'An account with this email already exists. Refresh the page and try again.')


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
        raise HTTPException(422, 'Choose a recruiter to take over all of this person\'s candidates and open tasks.')
    target = _find(users, target_id)
    from app.services.attendance_availability import unavailable_staff_ids
    if target.id == user.id or not target.active or target.role != Role.staff or target.on_leave or target.id in unavailable_staff_ids(db):
        raise HTTPException(422, 'Choose a different active recruiter who is not on leave.')
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
    # The system must always keep one real Admin: only role `admin` counts here, never an MD.
    loses_admin = is_real_admin(user) and (not next_active or not is_real_admin(next_role))
    if loses_admin and sum(row.active and is_real_admin(row) for row in users) <= 1:
        raise HTTPException(409, 'There must always be at least one active admin. This account has to stay active as an admin.')
    if user.id == actor.id and (not next_active or next_role != user.role):
        raise HTTPException(409, 'You cannot turn off your own account or change your own role.')
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
        how = f"because {user.name}'s account was changed"
        notify_assignment_changes(db, ownership_changes, actor_id=actor.id, actor_name=actor.name, how=how, users=users)
        notify_task_assignment_changes(db, task_only_changes, actor_id=actor.id, actor_name=actor.name, how=how, users=users)
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


# ------------------------------------------------------------------ delete

FORMER_USER = ' (former user)'


def _count(db: Session, statement) -> int:
    return db.scalar(statement) or 0


def delete_summary(db: Session, user_id: str) -> dict:
    """What deleting this person would move: their candidates, open tasks and leads (for the dialog and the checks)."""
    open_tasks = select(func.count()).select_from(Task).where(Task.owner_id == user_id, Task.completed_at.is_(None), Task.cancelled_at.is_(None))
    return {'candidates': _count(db, select(func.count()).select_from(Candidate).where(Candidate.owner_id == user_id)),
            'openTasks': _count(db, open_tasks),
            'leads': _count(db, select(func.count()).select_from(Lead).where(Lead.owner_id == user_id))}


def delete_user(db: Session, actor: User, user_id: str, reassign_to: str | None = None, leave_unassigned: bool = False) -> dict:
    """Delete a Recruiter's account, for good as far as sign-in goes, without damaging a single history row.

    The login is removed (password, phone, sessions, email) and the person disappears from every list, but the auth_users row stays
    as a tombstone named "<name> (former user)": audit entries, remarks, stage records, attachments, attendance and invoices keep
    pointing at it and keep showing the name. Their candidates, open tasks and leads are never lost or silently moved: the caller
    chooses a recruiter to take them (`reassign_to`, the same transfer as deactivating a recruiter) or `leave_unassigned` (the
    Data Bank's unassign rule: a candidate that is deployed, mapped to an order or in stage work cannot be left without a recruiter).
    One transaction; nothing is changed when anything is refused."""
    users = _directory(db, actor)
    person = _find(users, user_id)
    if person.id == actor.id:
        raise HTTPException(409, 'You cannot delete your own account.')
    if person.role != Role.staff:
        raise HTTPException(409, 'Only Recruiter accounts can be deleted. An Admin or MD account can be deactivated instead; '
                                 'change the role to Recruiter first if it really has to go.')
    if reassign_to and leave_unassigned:
        raise HTTPException(422, 'Choose one: give their work to a recruiter, or leave it unassigned.')
    owned = delete_summary(db, person.id)
    has_work = bool(owned['candidates'] or owned['openTasks'] or owned['leads'])
    if has_work and not reassign_to and not leave_unassigned:
        raise HTTPException(422, "Choose who takes over this person's candidates, open tasks and leads, or leave them unassigned.")

    original_name, original_email = person.name, person.email
    recipient = None
    ownership_changes: list = []
    task_only_changes: list = []
    transfer: dict = {'candidates': 0, 'tasks': 0}
    cancelled_tasks = 0
    if reassign_to:
        recipient = _find(users, reassign_to)
        from app.services.attendance_availability import unavailable_staff_ids
        if recipient.id == person.id or not recipient.active or recipient.role != Role.staff or recipient.on_leave or recipient.id in unavailable_staff_ids(db):
            raise HTTPException(422, 'Choose a different active recruiter who is not on leave.')
        transfer, ownership_changes, task_only_changes = _transfer(db, actor, person, users, reassign_to)
    elif owned['candidates'] or owned['openTasks']:
        from app.api.routers.candidates import lock_assignment_cursor
        from app.services.candidate_unassign import unassign_candidates
        lock_assignment_cursor(db)
        mine = list(db.scalars(select(Candidate).where(Candidate.owner_id == person.id).order_by(Candidate.id).with_for_update()))
        result = unassign_candidates(db, actor, mine, f'Account of {original_name} deleted by {actor.name}', None)
        if result['refused']:
            db.rollback()
            first = result['refused'][0]['reason']
            more = f' (and {len(result["refused"]) - 1} more)' if len(result['refused']) > 1 else ''
            raise HTTPException(409, f'{len(result["refused"])} candidate(s) cannot be left without a recruiter: {first}{more} '
                                     'Choose a recruiter to take them over, or deactivate the account instead.')
        transfer = {'candidates': len(result['freed']), 'tasks': 0}
        # Open tasks that were not tied to a candidate they owned are cancelled, never deleted: their history stays.
        now = datetime.now(timezone.utc)
        for task in db.scalars(select(Task).where(Task.owner_id == person.id, Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.id).with_for_update()):
            task.cancelled_at = now
            cancelled_tasks += 1

    leads = owned['leads']
    if leads:
        from app.services import lead_sheet
        lead_ids = list(db.scalars(select(Lead.id).where(Lead.owner_id == person.id).order_by(Lead.id)))
        lead_sheet.apply_owner_changes(db, actor, {recipient.id if recipient else None: lead_ids},
                                       {recipient.id: recipient.name} if recipient else {}, bulk=True)

    if ownership_changes or task_only_changes:
        from app.services.assignment_notifications import notify_assignment_changes, notify_task_assignment_changes
        how = f"because {original_name}'s account was deleted"
        notify_assignment_changes(db, ownership_changes, actor_id=actor.id, actor_name=actor.name, how=how, users=users)
        notify_task_assignment_changes(db, task_only_changes, actor_id=actor.id, actor_name=actor.name, how=how, users=users)

    # The login, and everything that only made sense while the person could sign in.
    revoked = _revoke(db, person)
    tables = Base.metadata.tables
    for name, column in (('system_notifications', 'user_id'), ('personal_todos', 'owner_id'), ('email_oauth_states', 'user_id'),
                         ('crm_client_staff', 'user_id'), ('workflow_daily_goals', 'user_id'), ('staff_presence_clients', 'user_id')):
        if name in tables:
            db.execute(delete(tables[name]).where(tables[name].c[column] == person.id))
    cursor = tables.get('workflow_assignment_cursor')
    if cursor is not None:
        db.execute(cursor.update().where(cursor.c.last_user_id == person.id).values(last_user_id=None))
    person.name = original_name[:160 - len(FORMER_USER)] + FORMER_USER
    person.email = deleted_user_email(person.id)
    person.phone = None
    person.password_hash = None
    person.active = False
    person.receives_auto_assign = False
    person.on_leave = False

    mode = 'reassign' if recipient else ('unassign' if has_work else 'nothing to move')
    summary = {'candidates': transfer.get('candidates', 0), 'tasks': transfer.get('tasks', 0), 'cancelledTasks': cancelled_tasks,
               'leads': leads, 'revokedSessions': revoked, 'toUserId': recipient.id if recipient else None,
               'toName': recipient.name if recipient else None, 'mode': mode,
               'originalName': original_name, 'originalEmail': original_email}
    db.add(AuditEntry(actor_id=actor.id, action='Deleted user', target=original_name, entity_type='user', entity_id=person.id, meta=summary))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'This account cannot be deleted because other records still depend on it. Deactivate it instead.')
    return summary
