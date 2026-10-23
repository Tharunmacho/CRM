"""Authenticated, durable human confirmation of document/person boundaries."""
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services import document_person_split as service

router = APIRouter(prefix="/api/document-bundles", tags=["document-person-review"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartIn(StrictModel):
    sourceType: Literal["manual", "email", "whatsapp"]
    sourceId: UUID


class PageIn(StrictModel):
    sourceId: UUID
    pageNumber: int = Field(ge=1, le=120, strict=True)
    kind: Literal["resume", "passport", "aadhaar", "photo", "education", "experience", "source_bundle"]


class IgnoredIn(StrictModel):
    sourceId: UUID
    pageNumber: int = Field(ge=1, le=120, strict=True)
    reason: str = Field(min_length=1, max_length=500)


class GroupIn(StrictModel):
    id: UUID | None = None
    label: str = Field(min_length=1, max_length=200)
    fields: dict[str, Any] = Field(default_factory=dict)
    pages: list[PageIn] = Field(default_factory=list, max_length=120)

    @field_validator("fields")
    @classmethod
    def bounded_fields(cls, value):
        import json
        if set(value) - service.FIELD_KEYS or len(json.dumps(value)) > 30000:
            raise ValueError("Unsupported or oversized candidate details")
        return value


class ReviewIn(StrictModel):
    revision: int = Field(ge=0)
    groups: list[GroupIn] = Field(max_length=40)
    ignoredPages: list[IgnoredIn] = Field(default_factory=list, max_length=120)


class DecisionIn(StrictModel):
    groupId: UUID
    candidateId: UUID


class CommitIn(StrictModel):
    revision: int = Field(ge=0)
    confirmed: Literal[True]
    duplicateDecisions: list[DecisionIn] = Field(default_factory=list, max_length=40)


def mutate(db, callback):
    try:
        return callback()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        # A lost commit response is recovered by GET or replaying the same bundle;
        # never instruct the reviewer to make a new intake after uncertainty.
        raise HTTPException(503, "The saved review could not be confirmed. Reload this same review before retrying; its saved people and originals are retained.") from None


@router.post("", status_code=202)
def start(body: StartIn, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return mutate(db, lambda: service.prepare_bundle(db, body.sourceType, str(body.sourceId), user))


@router.get("/{identifier}")
def detail(identifier: UUID, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return service.bundle_out(db, service.get_bundle(db, str(identifier), user))


@router.get("/candidates/{candidate_id}/sources")
def candidate_sources(candidate_id: UUID, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return service.candidate_manual_sources(db, str(candidate_id), user)


@router.put("/{identifier}/review")
def review(identifier: UUID, body: ReviewIn, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return mutate(db, lambda: service.save_review(db, str(identifier), user, body.revision,
        [group.model_dump(mode="json") for group in body.groups], [page.model_dump(mode="json") for page in body.ignoredPages]))


@router.post("/{identifier}/commit")
def commit(identifier: UUID, body: CommitIn, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    choices = {str(item.groupId): str(item.candidateId) for item in body.duplicateDecisions}
    if len(choices) != len(body.duplicateDecisions):
        raise HTTPException(422, "Choose only one duplicate decision per person")
    settings = get_settings()

    def confirm():
        # Reading the reviewed pages happens first, outside the confirming transaction.
        service.extract_groups(db, str(identifier), user, body.revision, settings)
        return service.commit_review(db, str(identifier), user, body.revision, choices, settings)

    return mutate(db, confirm)


@router.get("/{identifier}/sources/{source_id}/pages/{page_number}")
def preview(identifier: UUID, source_id: UUID, page_number: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        data = service.page_pdf(db, str(identifier), str(source_id), page_number, user, get_settings())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "This saved page preview is unavailable. Restore document storage before confirming ownership.") from None
    return Response(data, media_type="application/pdf", headers={"Cache-Control": "private, no-store",
        "Content-Disposition": f'inline; filename="page-{page_number}.pdf"', "X-Content-Type-Options": "nosniff"})
