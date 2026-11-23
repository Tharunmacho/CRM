"""The initial-call outcome of a candidate: one rule, used by every place that records it.

Recording "Not interested" takes a candidate off the Tasks page's Current process and puts them on its Declined tab; recording "Rejected"
(the recruiter's own no: not suitable) puts them on its Rejected tab, with no client or job needed; recording "yes" puts them back. Two screens write this: the "Record the call result" dialog (`POST /api/candidates/{id}/call`) and the
application form's "Interest level" cards (`save_form`), so a recruiter who sets the interest level to Not interested no longer leaves the
candidate on their list with nothing changed. The caller holds the candidate row lock, checks who may act, and commits.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Application, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, ClosureReason, Task, User


#: The answers that end the candidate's chance for now: their own no ("no": Declined) and the recruiter's no ("rejected": Rejected).
NEGATIVE_ANSWERS = ("no", "rejected")


def _placement_tab(db, c: Candidate) -> str | None:
    """"Declined" / "Rejected" when the candidate's LAST placement ended in an outcome of its own (it decides the tab, whatever the call answer
    says), else None (no placement, or one that was only unmapped / swapped: the call answer decides). Same rule as the Tasks page's views."""
    from app.services.daily_work import DECLINE_CLOSURES, NOT_OUTCOMES
    last = db.scalars(select(Application).where(Application.candidate_id == c.id).order_by(Application.created_at.desc(), Application.id.desc()).limit(1)).first()
    if last is None or last.status != ApplicationStatus.closed or last.closure_reason is None or last.closure_reason in NOT_OUTCOMES:
        return None
    return "Declined" if last.closure_reason in DECLINE_CLOSURES else "Rejected"


def apply_call(db, user: User, c: Candidate, *, called: bool, interested: str, remarks: str | None, source: str | None = None, now: datetime | None = None) -> dict:
    """Write the call outcome on the (already locked) candidate; returns {ok, onOrder, statusChanged, notice, leftOrder}. Does not commit.

    The call may be recorded after the candidate was already matched to an order (matching closes the initial-call task, the call result is
    still worth keeping). A "not interested" answer for someone still at prescreening (stage 1) takes them off that order: the application
    closes as "not interested", its tasks are cancelled, and they show on the Declined tab of the Tasks page and can be used for
    another job. `now` is the caller's clock (the router's, so a frozen test clock and the real one agree). Past prescreening, the placement is only ever closed in its own stage, so there the outcome is stored and audited but
    the application is left alone."""
    placements = list(db.scalars(select(Application).where(Application.candidate_id == c.id, Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed])).with_for_update()))
    on_order = bool(placements)
    prescreening = [a for a in placements if a.status == ApplicationStatus.active and a.current_stage <= 1]
    negative = interested in NEGATIVE_ANSWERS
    leaves_order = called and negative and bool(prescreening) and len(prescreening) == len(placements)
    if leaves_order:
        from app.api.routers.applications import _audit, _open_tasks
        closed_at = now or datetime.now(timezone.utc)
        said_no = interested == "no"
        for placement in prescreening:
            placement.status = ApplicationStatus.closed
            placement.closed_at = closed_at
            placement.closure_reason = ClosureReason.not_interested if said_no else ClosureReason.prescreen_failed
            placement.lock_version += 1
            for open_task in _open_tasks(db, placement):
                open_task.cancelled_at = closed_at
            _audit(db, user, "Candidate not interested at the initial call: taken off the order" if said_no
                   else "Candidate rejected at the initial call: taken off the order", placement, c, closed_at)
        on_order = False

    now = now or datetime.now(timezone.utc)
    # A later "unreachable" (or any other answer that is not a yes / no / rejected) never erases a decisive answer already stored: a stored
    # "no" / "rejected" is what keeps the candidate on Declined / Rejected, and a stored "yes" is what keeps a revived candidate on today's
    # list (their closed placement is only cancelled by that "yes"). The time and the remarks of the failed call are still recorded.
    decisive = {"yes", *NEGATIVE_ANSWERS}
    keeps_answer = interested not in decisive and c.call_interested in decisive and not (c.call_interested == "yes" and c.status == CandidateStatus.not_interested)
    if not keeps_answer:
        c.call_called = called
        c.call_interested = interested
    c.call_remarks = remarks
    c.call_recorded_at = now
    if not on_order:
        if negative:
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
        if called and interested in {"yes", *NEGATIVE_ANSWERS}:
            t.completed_at = now

    meta = {"called": called, "interested": interested, "on_order": on_order}
    if source:
        meta["source"] = source
    db.add(AuditEntry(actor_id=user.id, action="Recorded initial call after matching" if on_order else "Recorded initial call",
                      entity_type="candidate", entity_id=c.id, target=c.name, meta=meta, at=now))   # the call's own time: the reports date it by this
    notice = None
    if on_order and called and negative:
        notice = "Saved. This candidate is still on an order, so nothing on the order was changed. Unmap them or record the result in their stage if they should leave it."
    if leaves_order or (negative and not on_order and called):
        answer_tab = "Declined" if interested == "no" else "Rejected"
        # The candidate's last placement decides the tab when it ended in an outcome of its own (a stage "Declined" stays on Declined even if the
        # recruiter now records "rejected", and the other way round): name the tab where they really show, never the one the answer suggests.
        tab = answer_tab if leaves_order else (_placement_tab(db, c) or answer_tab)
        notice = (f"Saved. The candidate is {'not interested' if interested == 'no' else 'rejected'}, so they were taken off the order and are on the {tab} tab of the Tasks page."
                  if leaves_order else f"Saved. The candidate is on the {tab} tab of the Tasks page.")
    return {"ok": True, "onOrder": on_order, "statusChanged": not on_order and interested in {"yes", *NEGATIVE_ANSWERS}, "notice": notice, "leftOrder": leaves_order}
