"""Take candidates back from their recruiter ("Not assigned") - the one implementation behind the Data Bank's Unassign action and
the "leave unassigned" choice when a recruiter's account is deleted. Nothing here commits: the caller owns the transaction."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (Application, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, Order, Remark, Task, TaskEvent,
                           TaskKind, User)


def unassign_refusals(db: Session, candidates: list[Candidate]) -> dict[str, str]:
    """{candidate id: why this candidate must stay with their recruiter (work is under way on an order)}; the others may be freed.

    Two statements for the whole selection, not three per candidate."""
    ids = [c.id for c in candidates if c.status != CandidateStatus.deployed]
    mapped: dict[str, str] = {}
    staged: set[str] = set()
    if ids:
        for candidate_id, ref in db.execute(select(Application.candidate_id, Order.ref).join(Order, Application.order_id == Order.id).where(
                Application.candidate_id.in_(ids), Application.status == ApplicationStatus.active).order_by(Order.ref)):
            mapped.setdefault(candidate_id, ref)
        staged = set(db.scalars(select(Task.candidate_id).where(
            Task.candidate_id.in_(ids), Task.kind != TaskKind.initial_call, Task.completed_at.is_(None), Task.cancelled_at.is_(None)).distinct()))
    out: dict[str, str] = {}
    for c in candidates:
        if c.status == CandidateStatus.deployed:
            out[c.id] = "is deployed and stays with their recruiter"
        elif c.id in mapped:
            out[c.id] = f"is mapped to {mapped[c.id]}: reassign them to another recruiter instead"
        elif c.id in staged:
            out[c.id] = "has open stage work: reassign them to another recruiter instead"
    return out


def unassign_candidates(db: Session, actor: User, candidates: list[Candidate], reason: str, notice_reason: str | None) -> dict:
    """Free every candidate that may be freed; returns {"freed": [Candidate], "refused": [{id, name, reason}], "already": n}.

    The recruiter's follow-up reminder goes, an untouched initial-call task is removed (a fresh one starts when the candidate is
    assigned again); one with remarks, SLA events, an escalation or a recorded call is cancelled, keeping its history. A candidate who
    is deployed, mapped to an order or has open stage work is refused with the reason, and one without a recruiter is skipped.
    Changes nothing at all when nobody can be freed. `candidates` must already be locked (FOR UPDATE) by the caller."""
    todo, refused, already = [], [], 0
    reasons = unassign_refusals(db, [c for c in candidates if c.owner_id is not None])
    for c in candidates:
        if c.owner_id is None:
            already += 1
        elif c.id in reasons:
            refused.append({"id": c.id, "name": c.name, "reason": f"{c.name} {reasons[c.id]}."})
        else:
            todo.append(c)
    if not todo:
        return {"freed": [], "refused": refused, "already": already}
    from app.services.application_form import sync_follow_up_todo
    now = datetime.now(timezone.utc)
    previous_owners = {c.id: c.owner_id for c in todo}
    for c in todo:
        previous = c.owner_id
        c.owner_id = None
        db.add(AuditEntry(actor_id=actor.id, action="Candidate unassigned", target=c.name, entity_type="candidate", entity_id=c.id, at=now,
                          meta={"fromUserId": previous, "toUserId": None, "reason": reason}))
        if c.next_follow_up_date:
            sync_follow_up_todo(db, c)  # the reminder belonged to the old recruiter
    open_tasks = list(db.scalars(select(Task).where(Task.candidate_id.in_([c.id for c in todo]), Task.completed_at.is_(None), Task.cancelled_at.is_(None))
                                 .order_by(Task.id).with_for_update()))
    touched: set[str] = set()  # tasks whose SLA history (remarks, events) must survive: cancelled, never deleted
    if open_tasks:
        task_ids = [t.id for t in open_tasks]
        touched |= set(db.scalars(select(Remark.task_id).where(Remark.task_id.in_(task_ids))))
        touched |= set(db.scalars(select(TaskEvent.task_id).where(TaskEvent.task_id.in_(task_ids))))
    called = {c.id for c in todo if c.call_recorded_at}
    for task in open_tasks:
        if task.id in touched or task.candidate_id in called or task.escalated or task.escalated_at:
            task.cancelled_at = now
        else:
            db.delete(task)  # truly untouched: a fresh one starts when the candidate is assigned again
    db.add(AuditEntry(actor_id=actor.id, action="Unassigned candidates", target=f"{len(todo)} candidates", entity_type="candidate",
                      meta={"candidateIds": [c.id for c in todo], "reason": reason}))
    from app.services.assignment_notifications import notify_candidates_unassigned
    notify_candidates_unassigned(db, [(c.id, c.name, previous_owners[c.id]) for c in todo], actor_id=actor.id, actor_name=actor.name, reason=notice_reason)
    return {"freed": todo, "refused": refused, "already": already}
