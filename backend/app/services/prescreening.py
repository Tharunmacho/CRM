"""Candidate-level Prescreening: stage 1 for a candidate who has no client or job yet.

The owner's rule: Prescreening is open to everyone WITHOUT a client and a job; there the recruiter can write feedback, decline (the
candidate said no), reject (the recruiter's no) or move the candidate to the next stage. A client AND a job are mandatory from stage 2.

A placement (`crm_applications.order_id`) always has an order, so there is no placement while the candidate has no job. This module
therefore keeps Prescreening on the candidate itself:

* ``save``    writes the feedback (an audit entry, nothing else changes: the candidate stays on the Tasks list);
* ``decline`` / ``reject`` record the call outcome through the one rule every other screen uses (`candidate_call.apply_call`), so the
  candidate lands on the Declined / Rejected tab at once and is never missing from "All";
* ``forward`` is the only action that needs the job: in ONE transaction it puts the candidate on the order (`perform_map`, with every
  check the normal mapping has), records stage 1 as COMPLETED with the feedback, and leaves the placement at stage 2 with its task, exactly
  as completing stage 1 in the stage window does.

Nothing here makes `order_id` nullable and no existing placement or stage >= 2 flow is touched. The caller commits.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, User
from app.domain.stages import stage_by_no, validate_payload
from app.services.candidate_call import NEGATIVE_ANSWERS, apply_call

REQUIRED_FROM_STAGE_2 = "Choose the client and the job: they are required from stage 2."
FEEDBACK_REQUIRED = "Write feedback before moving the candidate to the next stage."
ON_A_JOB = "This candidate is already on a job. Open the job's stages to continue."
SAVE_AUDIT = "Saved prescreening feedback"
FORWARD_AUDIT = "Forwarded from candidate prescreening"

#: The states the screen shows for a candidate without a placement.
NOT_STARTED, IN_PROGRESS, DECLINED, REJECTED, ON_JOB = "not_started", "in_progress", "declined", "rejected", "on_job"


def placements(db, candidate: Candidate) -> list[Application]:
    """The candidate's live placements (active or deployed)."""
    return list(db.scalars(select(Application).where(
        Application.candidate_id == candidate.id,
        Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed]),
    )))


def _last_save(db, candidate: Candidate) -> AuditEntry | None:
    return db.scalars(select(AuditEntry).where(
        AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate.id, AuditEntry.action == SAVE_AUDIT,
    ).order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(1)).first()


def state_of(db, candidate: Candidate) -> dict:
    """What the Prescreening panel shows: the state, the last saved feedback and when it was saved."""
    live = placements(db, candidate)
    saved = _last_save(db, candidate)
    feedback = (saved.meta or {}).get("feedback", "") if saved is not None else ""
    if live:
        state = ON_JOB
    elif candidate.status == CandidateStatus.not_interested and candidate.call_interested in NEGATIVE_ANSWERS:
        state = DECLINED if candidate.call_interested == "no" else REJECTED
    elif saved is not None:
        state = IN_PROGRESS
    else:
        state = NOT_STARTED
    return {"state": state, "feedback": feedback, "savedAt": saved.at if saved is not None else None,
            "decidedAt": candidate.call_recorded_at if state in (DECLINED, REJECTED) else None}


def _refuse_if_past_prescreening(db, candidate: Candidate, *, closing: bool) -> None:
    """Prescreening here is for a candidate with no job. A job at stage 1 may still be closed by a decline / reject (the same rule as the
    call dialog); every other action needs the job's own stage window."""
    live = placements(db, candidate)
    if not live:
        return
    if closing and all(app.status == ApplicationStatus.active and app.current_stage <= 1 for app in live):
        return
    raise HTTPException(409, ON_A_JOB)


def save(db, user: User, candidate: Candidate, feedback: str | None) -> dict:
    text = (feedback or "").strip()
    if not text:
        raise HTTPException(422, "Write the feedback before saving it.")
    _refuse_if_past_prescreening(db, candidate, closing=False)
    now = datetime.now(timezone.utc)
    db.add(AuditEntry(actor_id=user.id, action=SAVE_AUDIT, target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"feedback": text}, at=now))
    return {"ok": True, "notice": "Feedback saved. The candidate stays in Prescreening."}


def close(db, user: User, candidate: Candidate, interested: str, feedback: str | None) -> dict:
    """Decline ("no": the candidate's own no) or reject ("rejected": not suitable): the candidate is on Declined / Rejected from the next read."""
    if interested not in NEGATIVE_ANSWERS:
        raise HTTPException(422, "Choose Declined or Rejected.")
    _refuse_if_past_prescreening(db, candidate, closing=True)
    result = apply_call(db, user, candidate, called=True, interested=interested, remarks=(feedback or "").strip() or None,
                        source="prescreening", now=datetime.now(timezone.utc))
    return result


def forward(db, user: User, candidate_id: str, order_id: str | None, feedback: str | None) -> dict:
    """Move the candidate to stage 2: needs the client's job (an order). One transaction; the caller commits."""
    from app.api.routers.applications import _audit, _task, perform_map
    if not (order_id or "").strip():
        raise HTTPException(422, REQUIRED_FROM_STAGE_2)
    text = (feedback or "").strip()
    if not text:
        raise HTTPException(422, FEEDBACK_REQUIRED)
    # A repeated click (or a retried request) of an action that already succeeded answers with what it did, and writes nothing.
    again = db.scalars(select(Application).where(
        Application.candidate_id == candidate_id, Application.order_id == order_id,
        Application.status == ApplicationStatus.active, Application.current_stage >= 2,
    ).order_by(Application.created_at.desc(), Application.id.desc()).limit(1)).first()
    if again is not None and db.scalar(select(AuditEntry.id).where(
            AuditEntry.entity_type == "application", AuditEntry.entity_id == again.id, AuditEntry.action == FORWARD_AUDIT).limit(1)):
        return {"applicationId": again.id, "orderId": order_id, "currentStage": again.current_stage, "taskId": None, "repeated": True}

    # Every check of the normal mapping runs here: the order is open, the candidate has no other live placement, the recruiter may work
    # the candidate and is active, client staff routing. It locks cursor, candidate and order in its own order, so nothing is locked before it.
    mapped = perform_map(db, user, candidate_id, order_id)
    app, candidate, task = mapped["application"], mapped["candidate"], mapped["task"]
    at = datetime.now(timezone.utc)

    # Stage 1 is COMPLETED with the feedback, in the shape the stage window writes, so the history shows "Prescreening: Passed".
    definition = stage_by_no(1)
    data = validate_payload(1, {"feedback": text, "result": "passed"})
    evaluation = definition.evaluate(data, None)
    db.add(ApplicationStage(
        application_id=app.id, stage=1, attempt=1, data=data, outcome=evaluation.outcome, completed_by=user.id, completed_at=at,
        owner_id=candidate.owner_id or app.owner_id, amount=None, amount_currency=None,
    ))
    task.completed_at = at
    app.current_stage = 2
    app.draft = {}
    app.draft_saved_at = app.draft_saved_by = None
    app.lock_version += 1
    next_task = _task(db, app, candidate, at, flush=False)

    # The call outcome: the candidate passed prescreening, so their answer is "yes".
    candidate.call_called, candidate.call_interested, candidate.call_remarks, candidate.call_recorded_at = True, "yes", text, at
    db.add(AuditEntry(actor_id=user.id, action="Recorded initial call", entity_type="candidate", entity_id=candidate.id, target=candidate.name,
                      meta={"called": True, "interested": "yes", "on_order": True, "source": "prescreening"}, at=at))
    _audit(db, user, f"Recorded stage 1: {evaluation.outcome}", app, candidate, at,
           {"stage": 1, "attempt": 1, "outcome": evaluation.outcome, "closes": None, "repeats": False})
    _audit(db, user, FORWARD_AUDIT, app, candidate, at, {"order_id": order_id})
    return {"applicationId": app.id, "orderId": order_id, "currentStage": 2, "taskId": next_task.id, "repeated": False}
