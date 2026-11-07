"""Route candidates to the CRM staff allocated to a client.

A client can have CRM users allocated to it (`crm_client_staff`, set on the Clients page). Two moments consult that list:

1. Mapping (an application is created, `POST /api/applications`): when the order's client has staff and the candidate's
   owner is not one of them, the candidate moves to the client's least-loaded staff member (`reroute_for_order`). The
   move is written exactly as an admin's reassignment is (audit entry with fromUserId/toUserId that the assignment
   history reads, open tasks follow the candidate, the new owner and the admins are notified).
2. Intake (email, WhatsApp, document confirmation, manual entry, the waiting-list sweep): a new candidate whose
   designation matches the job title of an open order of a client with staff goes to the least-loaded of that client's
   staff (`route_new_candidate`). When nothing matches the caller falls back to the ordinary round-robin, untouched.

Both run inside the caller's transaction and the callers already hold the assignment-cursor lock
(`lock_assignment_cursor`), so two candidates arriving together cannot both be counted against the same load.
"Load" is the number of candidates a person owns; ties go to the lowest name, then the lowest id. An admin's own
reassignment is never revisited: neither rule runs again after the application exists or the candidate has been created.
Allocated people who are deactivated, not staff/manager, on leave or absent today are skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import distinct, func, select

from app.db.models import (
    AuditEntry, Candidate, Client, ClientStaff, Company, Designation, Notification, Order, OrderStatus, Role, Task, User,
)

ALLOCATABLE_ROLES = (Role.staff, Role.manager)


def _title_key(value: str | None) -> str:
    return " ".join((value or "").split()).lower()


def allocated_ids_for_order(db, order_id: str) -> tuple[list[str], str | None]:
    """(allocated user ids, client name) for the order's client. Empty list when the client has no staff. One statement."""
    rows = db.execute(
        select(ClientStaff.user_id, Client.name)
        .select_from(Order)
        .join(Company, Company.id == Order.company_id)
        .join(Client, Client.id == Company.client_id)
        .join(ClientStaff, ClientStaff.client_id == Client.id)
        .where(Order.id == order_id)
    ).all()
    return [row.user_id for row in rows], (rows[0].name if rows else None)


def _available(db, user_ids) -> list[User]:
    """Allocated people who can take a candidate today: active staff/manager, not on leave, not absent."""
    if not user_ids:
        return []
    from app.services.attendance_availability import unavailable_staff_ids
    people = list(db.scalars(select(User).where(User.id.in_(list(user_ids)), User.active.is_(True), User.role.in_(ALLOCATABLE_ROLES))))
    absent = unavailable_staff_ids(db)
    return [person for person in people if not person.on_leave and person.id not in absent]


def least_loaded(db, people: list[User]) -> User | None:
    """The person who owns the fewest candidates; ties by name, then id."""
    if not people:
        return None
    db.flush()  # candidates assigned earlier in this transaction count
    loads = dict(db.execute(select(Candidate.owner_id, func.count()).where(
        Candidate.owner_id.in_([person.id for person in people])).group_by(Candidate.owner_id)).all())
    return min(people, key=lambda person: (loads.get(person.id, 0), person.name.casefold(), person.id))


def any_allocations(db) -> bool:
    """True when at least one client has CRM staff allocated (lets a batch skip per-candidate lookups)."""
    return db.scalar(select(ClientStaff.user_id).limit(1)) is not None


def matching_staff_ids(db, designations) -> set[str]:
    """Allocated users of every active client that has an open order for one of these job titles (case-insensitive)."""
    titles = {_title_key(value) for value in (designations or []) if isinstance(value, str) and _title_key(value)}
    if not titles:
        return set()
    title = func.lower(func.regexp_replace(func.btrim(Designation.title), r"\s+", " ", "g"))
    return set(db.scalars(
        select(distinct(ClientStaff.user_id))
        .select_from(Order)
        .join(Designation, Designation.id == Order.designation_id)
        .join(Company, Company.id == Order.company_id)
        .join(Client, Client.id == Company.client_id)
        .join(ClientStaff, ClientStaff.client_id == Client.id)
        .where(Order.status == OrderStatus.open, Client.status == "active", title.in_(titles))
    ))


def route_new_candidate(db, designations, *, keep_user_id: str | None = None) -> User | None:
    """Rule 2. The least-loaded available staff member of the clients that want this designation, or None to use the
    ordinary assignment. `keep_user_id` is a staff member who is adding the candidate themselves: when they are one of
    the matching staff they keep it."""
    staff = matching_staff_ids(db, designations)
    if not staff or (keep_user_id and keep_user_id in staff):
        return None
    return least_loaded(db, _available(db, staff))


def reroute_for_order(db, candidate: Candidate, order: Order, allocated_ids: list[str], client_name: str | None, *,
                      actor_id: str | None) -> User | None:
    """Rule 1. Hand `candidate` to the order's client staff when their owner is not one of them. Returns the new owner,
    or None when nothing changed (no staff, the owner is already one of them, or none of them is available)."""
    if not allocated_ids or candidate.owner_id in allocated_ids:
        return None
    target = least_loaded(db, _available(db, allocated_ids))
    if target is None:
        return None
    reason = f"Matched to {order.ref}: {client_name or 'the client'} is looked after by {target.name}"
    reassign_candidate(db, candidate, target, actor_id=actor_id, reason=reason, notification_reason=f"Client staff routing. {reason}.",
                       start_call=False)
    return target


def reassign_candidate(db, candidate: Candidate, target: User, *, actor_id: str | None, reason: str,
                       notification_reason: str, start_call: bool = True) -> bool:
    """Move one candidate to `target` the way the admin reassignment does (`POST /api/candidates/reassign`):
    the audit entry the assignment history reads, the review flag released when the owner was all it waited for, the
    follow-up reminder and every open task following the candidate, and the notices to the new owner and the admins."""
    from app.services.application_form import sync_follow_up_todo
    from app.services.assignment_notifications import notify_assignment_changes
    from app.services.candidate_review import release_ownership_review, review_reasons, start_initial_call
    from app.services.workflow_scheduler import add_event

    previous = candidate.owner_id
    if previous == target.id:
        return False
    now = datetime.now(timezone.utc)
    candidate.owner_id = target.id
    if previous is None:
        release_ownership_review(candidate, review_reasons(db, [candidate]).get(candidate.id, set()))
    db.add(AuditEntry(actor_id=actor_id, action="Candidate reassigned", target=candidate.name, entity_type="candidate",
                      entity_id=candidate.id, at=now, meta={"fromUserId": previous, "toUserId": target.id, "reason": reason}))
    if candidate.intake_owner_id is None:
        candidate.intake_owner_id = target.id
    if start_call:
        start_initial_call(db, candidate, target.id, now)
    if candidate.next_follow_up_date:
        sync_follow_up_todo(db, candidate)
    for task in db.scalars(select(Task).where(
        Task.candidate_id == candidate.id, Task.completed_at.is_(None), Task.cancelled_at.is_(None),
    ).order_by(Task.id).with_for_update()):
        if task.owner_id != target.id:
            add_event(db, task, "assignment", f"assignment:{task.id}:{now.isoformat()}", now,
                      {"fromOwnerId": task.owner_id, "toOwnerId": target.id, "actorId": actor_id, "reason": reason})
            task.owner_id = target.id
    notify_assignment_changes(db, [(candidate.id, candidate.name, previous, target.id)], reason=notification_reason)
    return True


def notify_new_job(db, order: Order, *, actor_id: str | None) -> int:
    """Tell the staff allocated to the order's client that a new job exists for them (added to the caller's transaction).

    The person who created the job already knows, so they are not notified. Nothing happens when the client has no
    allocated staff. Returns how many notices were added.
    """
    allocated, client_name = allocated_ids_for_order(db, order.id)
    recipients = [user_id for user_id in dict.fromkeys(allocated) if user_id != actor_id]
    if not recipients:
        return 0
    title = db.scalar(select(Designation.title).where(Designation.id == order.designation_id)) or "Job"
    company_name = db.scalar(select(Company.name).where(Company.id == order.company_id)) or client_name
    active = set(db.scalars(select(User.id).where(User.id.in_(recipients), User.active.is_(True))))
    for user_id in recipients:
        if user_id in active:
            db.add(Notification(user_id=user_id, kind="job", title=f"New job for {company_name or 'your client'}",
                                detail=f"{title} - {order.openings} {'opening' if order.openings == 1 else 'openings'}", link_to=f"/orders/{order.id}"))
    return len(active)
