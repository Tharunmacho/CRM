"""Candidate-level Prescreening (stage 1 without a client or job): read the state, save feedback, decline, reject or forward.

`GET  /api/candidates/{id}/prescreen`  the state shown by the Prescreening panel of the application form.
`POST /api/candidates/{id}/prescreen`  {action: save | decline | reject | forward, feedback, orderId}. Only `forward` needs a job (an
order): the client and the job are mandatory from stage 2. The rules live in `app.services.prescreening`.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.applications import _mutation
from app.core.deps import candidate_work_refusal, current_user, ensure_can_work_candidate
from app.db.models import Candidate, User
from app.db.session import get_db
from app.services import prescreening

router = APIRouter(prefix="/api/candidates", tags=["prescreening"])


class PrescreenIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["save", "decline", "reject", "forward"]
    feedback: str | None = Field(default=None, max_length=10000)
    orderId: str | None = Field(default=None, max_length=36)


@router.get("/{candidate_id}/prescreen")
def prescreen_state(candidate_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    return {**prescreening.state_of(db, candidate), "canWork": candidate_work_refusal(db, candidate, user) is None}


@router.post("/{candidate_id}/prescreen")
def prescreen(candidate_id: str, body: PrescreenIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    with _mutation(db):
        if body.action == "forward":
            # The mapping locks cursor, candidate and order itself, in the order every other mapping uses: nothing is locked before it.
            result = prescreening.forward(db, user, candidate_id, body.orderId, body.feedback)
            return_value = {"ok": True, "action": "forward", **result,
                            "notice": "Moved to stage 2 (Forwarded to client). The client and the job are now set." if not result["repeated"]
                            else "This candidate was already moved to the next stage."}
        else:
            candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
            if candidate is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
            ensure_can_work_candidate(candidate, user, db)
            if body.action == "save":
                return_value = {"action": "save", **prescreening.save(db, user, candidate, body.feedback)}
            else:
                outcome = prescreening.close(db, user, candidate, "no" if body.action == "decline" else "rejected", body.feedback)
                return_value = {"action": body.action, **outcome}
    # After the commit: the state the panel shows next.
    candidate = db.get(Candidate, candidate_id)
    if candidate is not None:
        db.refresh(candidate)
        return_value["state"] = prescreening.state_of(db, candidate)["state"]
    return return_value
