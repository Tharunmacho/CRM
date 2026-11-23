"""Reopen a CLOSED application: a placement that ended (declined, rejected, not interested at the call, removed from the job) goes back to
the stage where it ended, with an open task, so the candidate is on the Tasks "Current" list again and no longer on Declined / Rejected.

`POST /applications/{id}/reopen-application` is not `/reopen` (which sets aside an earlier COMPLETED stage attempt and so needs a
completed stage to act on): a placement closed before any stage was completed had no way back. The rules, in the order they are checked:
  * the people who may work the candidate (the same rule as every stage action);
  * only a closed placement (an open one answers "already open", so a second click is harmless; a deployed one cannot be reopened here);
  * the version the screen holds (somebody may have changed it meanwhile);
  * the candidate has an active recruiter, and no other active or deployed placement;
  * the order is open (the mapping rule: a closed, filled or on-hold order is not matched with candidates).
Everything happens in one transaction through the same helpers as the stage actions, so the change feed publishes it like any other write.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers import applications as base
from app.core.deps import current_user, ensure_can_work
from app.db.models import Application, ApplicationStage, ApplicationStatus, CandidateStatus, OrderStatus, User
from app.db.session import get_db
from app.domain.orders import reopen_blocker

router = APIRouter(prefix="/api/applications", tags=["applications"])

#: The marker the medical retest writes at a stage; it is not the record that ended the placement.
REAPPLYING = "Unfit: reapplying"


class ReopenApplicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    lock_version: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{application_id}/reopen-application", response_model=base.CompleteOut)
def reopen_application(application_id: str, body: ReopenApplicationIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    with base._mutation(db):
        app, candidate, order = base._locked_application(db, application_id)
        ensure_can_work(db, app, user)
        if app.status == ApplicationStatus.active:
            raise HTTPException(409, "This application is already open. Refresh the page to see the latest.")
        if app.status != ApplicationStatus.closed:
            raise HTTPException(409, "A deployed placement cannot be reopened here.")
        base._check_version(app, body.lock_version)
        base._owner(db, candidate)
        if db.scalar(select(Application.id).where(
            Application.candidate_id == candidate.id, Application.id != app.id,
            Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed]),
        )):
            raise HTTPException(409, "This candidate is on another job now. Unmap them from that job first, then reopen this application.")
        blocker = reopen_blocker(order.status)
        if blocker:
            raise HTTPException(409, blocker)
        if order.status != OrderStatus.open:
            raise HTTPException(409, "Open this order before reopening the candidate's application.")

        at = base.datetime.now(base.timezone.utc)
        closure = app.closure_reason.value if app.closure_reason else None
        stage = max(1, app.current_stage or 1)
        # The record that ended the placement at this stage (a stage exit such as Declined or Rejected) is set aside, exactly as reopening a
        # completed stage does, so the stage is worked again and the closure no longer counts. A placement closed without one (a decline at
        # the call, an unmap, a job swap) has none, and its draft is kept as it was.
        ended = list(db.scalars(select(ApplicationStage).where(
            ApplicationStage.application_id == app.id, ApplicationStage.stage == stage, ApplicationStage.reopened_reason.is_(None),
        ).order_by(ApplicationStage.attempt).with_for_update()))
        closing = [row for row in ended if row.outcome != REAPPLYING]
        if closing:
            app.draft = dict(closing[-1].data or {})
            app.draft.pop("feedback", None)   # the next move needs fresh feedback
        for row in ended:
            row.reopened_reason = body.reason
        for task in base._open_tasks(db, app):
            task.cancelled_at = at
        app.status, app.closure_reason, app.current_stage = ApplicationStatus.active, None, stage
        app.closed_at = None
        app.draft_saved_at, app.draft_saved_by = (at, user.id) if app.draft else (None, None)
        app.lock_version += 1
        candidate.status = CandidateStatus.in_process
        next_task = base._task(db, app, candidate, at)
        base._sync_order(db, order)
        base._audit(db, user, "Reopened application", app, candidate, at, {
            "reason": body.reason, "previous_status": "closed", "previous_closure": closure, "stage": stage,
            "superseded_attempts": [{"stage": row.stage, "attempt": row.attempt} for row in ended],
        })
    return base._out(app, next_task)
