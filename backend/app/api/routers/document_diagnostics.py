"""How a candidate's documents were read, for a person to send to the developer (no personal values in it)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services import document_diagnostics as service

router = APIRouter(prefix="/api/candidates/{candidate_id}/documents", tags=["documents"])


@router.get("/diagnostics")
def document_diagnostics(candidate_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Every stored document with its reading job, attempts, reader error class and detail, identity reasons (field names only),
    local page kinds and timestamps, plus ``report``: the same as text for the Copy report button. Same permission as editing
    the candidate; never any name, number or page text."""
    response.headers["Cache-Control"] = "no-store"
    return service.build(db, user, candidate_id)
