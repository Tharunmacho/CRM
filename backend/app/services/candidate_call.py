"""The initial-call outcome of a candidate: one rule, used by every place that records it.

Recording "Not interested" is what takes a candidate off the Tasks page's Current process and puts them on its Declined tab; recording "yes"
puts a not-interested candidate back. Two screens write this: the "Record the call result" dialog (`POST /api/candidates/{id}/call`) and the
application form's "Interest level" cards (`save_form`), so a recruiter who sets the interest level to Not interested no longer leaves the
candidate on their list with nothing changed. The caller holds the candidate row lock, checks who may act, and commits.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Application, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, ClosureReason, Task, User


def apply_call(db, user: User, c: Candidate, *, called: bool, interested: str, remarks: str | None, source: str | None = None, now: datetime | None = None) -> dict:
    """Write the call outcome on the (already locked) candidate; returns {ok, onOrder, statusChanged, notice, leftOrder}. Does not commit.

    The call may be recorded after the candidate was already matched to an order (matching closes the initial-call task, the call result is
    still worth keeping). A "not interested" answer for someone still at prescreening (stage 1) takes them off that order: the application
    closes as "not interested", its tasks are cancelled, and they go back to the Data Bank (Declined on the Tasks page) to be used for
    another job. `now` is the caller's clock (the router's, so a frozen test clock and the real one agree). Past prescreening, the placement is only ever closed in its own stage, so there the outcome is stored and audited but
    the application is left alone."""
    placements = list(db.scalars(select(Application).where(Application.candidate_id == c.id, Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed])).with_for_update()))
    on_order = bool(placements)
    prescreening = [a for a in placements if a.status == ApplicationStatus.active and a.current_stage <= 1]
    leaves_order = called and interested == "no" and bool(prescreening) and len(prescreening) == len(placements)
    if leaves_order:
        from app.api.routers.applications import _audit, _open_tasks
        closed_at = now or datetime.now(timezone.utc)
        for placement in prescreening:
            placement.status, placement.closure_reason = ApplicationStatus.closed, ClosureReason.not_interested
            placement.lock_version += 1
            for open_task in _open_tasks(db, placement):
                open_task.cancelled_at = closed_at
            _audit(db, user, "Candidate not interested at the initial call: taken off the order", placement, c, closed_at)
        on_order = False

    now = now or datetime.now(timezone.utc)
    # A later "unreachable" (or any non yes/no) call must not erase a stored "no": the candidate is still Declined, and the Declined tab
    # reads `call_interested == "no"`, so overwriting it would drop them from every tab.
    keeps_no = interested not in {"yes", "no"} and c.status == CandidateStatus.not_interested and c.call_interested == "no"
    if not keeps_no:
        c.call_called = called
        c.call_interested = interested
    c.call_remarks = remarks
    c.call_recorded_at = now
    if not on_order:
        if interested == "no":
            c.status = CandidateStatus.not_interested
        elif interested == "yes" and c.status == CandidateStatus.not_interested:
            c.status = CandidateStatus.new

    # Recording the outcome is what closes the initial-call task.
    for t in db.scalars(
        select(Task).where(
            Task.candidate_id == c.id,
            Task.kind == "initial_call",
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        ).with_for_update()
    ):
        if called and interested in {"yes", "no"}:
            t.completed_at = now

    meta = {"called": called, "interested": interested, "on_order": on_order}
    if source:
        meta["source"] = source
    db.add(AuditEntry(actor_id=user.id, action="Recorded initial call after matching" if on_order else "Recorded initial call",
                      entity_type="candidate", entity_id=c.id, target=c.name, meta=meta))
    notice = None
    if on_order and called and interested == "no":
        notice = "Saved. This candidate is still on an order, so nothing on the order was changed. Unmap them or record the result in their stage if they should leave it."
    if leaves_order:
        notice = "Saved. The candidate is not interested, so they were taken off the order and moved back to the Data Bank. They can be used for another job."
    return {"ok": True, "onOrder": on_order, "statusChanged": not on_order and interested in {"yes", "no"}, "notice": notice, "leftOrder": leaves_order}
