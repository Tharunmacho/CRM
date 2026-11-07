"""The order-wise job application form (see docs/workflows/application-form-contract.md)."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers import applications as applications_router, files as files_router
from app.core.deps import current_user
from app.db.models import Attachment, Candidate, User
from app.db.session import get_db
from app.services import application_form as service, form_assignment, form_feedback

router = APIRouter(prefix="/api/candidates/{candidate_id}/application-form", tags=["application-form"])


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonalIn(_Section):
    candidateName: str | None = Field(default=None, max_length=200)
    fatherName: str | None = Field(default=None, max_length=200)
    dateOfBirth: date | None = None
    maritalStatus: str | None = Field(default=None, max_length=30)
    gender: str | None = Field(default=None, max_length=20)
    nationality: str | None = Field(default=None, max_length=80)
    languagesKnown: list[str] | None = Field(default=None, max_length=30)
    mobileNumber: str | None = Field(default=None, max_length=40)
    currentAddress: str | None = Field(default=None, max_length=500)
    whatsapp: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    currentCity: str | None = Field(default=None, max_length=120)
    whatsappSameAsPhone: bool | None = None  # true: copy the mobile number into whatsapp (not stored itself)


class PassportIn(_Section):
    passportNumber: str | None = Field(default=None, max_length=40)
    placeOfIssue: str | None = Field(default=None, max_length=120)
    issueDate: date | None = None
    expiryDate: date | None = None


class ScreeningIn(_Section):
    nativePlace: str | None = Field(default=None, max_length=120)
    familyMemberNumber: str | None = Field(default=None, max_length=40)
    jobPreference: list[str] | None = Field(default=None, max_length=50)
    countryPreference: str | None = Field(default=None, max_length=200)
    jobDurationPreference: str | None = Field(default=None, max_length=60)
    bestTimeToContact: str | None = Field(default=None, max_length=60)
    serviceChargesStatus: str | None = Field(default=None, max_length=20)
    interestLevel: str | None = Field(default=None, max_length=20)
    nextFollowUpDate: date | None = None
    skills: list[str] | None = Field(default=None, max_length=200)


class EducationIn(_Section):
    level: str | None = Field(default=None, max_length=200)
    passedYear: int | None = None
    institute: str | None = Field(default=None, max_length=300)


class HistoryIn(_Section):
    company: str | None = Field(default=None, max_length=200)
    designation: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    period: str | None = Field(default=None, max_length=120)


class ExperienceIn(_Section):
    totalExperience: int | None = Field(default=None, ge=0, le=80)
    experienceIndia: int | None = Field(default=None, ge=0, le=80)
    experienceAbroad: int | None = Field(default=None, ge=0, le=80)
    history: list[HistoryIn] | None = Field(default=None, max_length=30)


class FormIn(_Section):
    personal: PersonalIn | None = None
    passport: PassportIn | None = None
    education: list[EducationIn] | None = Field(default=None, max_length=20)
    experience: ExperienceIn | None = None
    screening: ScreeningIn | None = None


def _body(model: FormIn) -> dict[str, Any]:
    """Only what the client sent: {section: {key: value}} and the education list."""
    out: dict[str, Any] = {}
    for section in ("personal", "passport", "experience", "screening"):
        part = getattr(model, section)
        if part is not None:
            data = part.model_dump(exclude_unset=True)
            if "history" in data and part.history is not None:
                data["history"] = [row.model_dump() for row in part.history]
            out[section] = data
    if model.education is not None:
        out["education"] = [row.model_dump() for row in model.education]
    return out


class AssignmentIn(_Section):
    """Partial: only the keys sent are changed. `orderId: null` unmaps; `agencyId` and `agencyName` both null clear the agency."""
    orderId: str | None = Field(default=None, max_length=36)
    source: Literal["whatsapp", "email", "walk_in", "referral", "other"] | None = None
    agencyId: str | None = Field(default=None, max_length=36)
    agencyName: str | None = Field(default=None, max_length=255)
    recruiterId: str | None = Field(default=None, max_length=36)


class FeedbackIn(_Section):
    kind: Literal["update", "delay", "issue", "client", "candidate"]
    reason: Literal["waiting_candidate", "waiting_client", "waiting_documents", "waiting_visa", "waiting_payment", "other"] | None = None
    body: str = Field(max_length=5000)
    applicationId: str | None = Field(default=None, max_length=36)


class FeedbackEditIn(_Section):
    kind: Literal["update", "delay", "issue", "client", "candidate"] | None = None
    reason: Literal["waiting_candidate", "waiting_client", "waiting_documents", "waiting_visa", "waiting_payment", "other"] | None = None
    body: str | None = Field(default=None, max_length=5000)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.get("")
def read_form(candidate_id: str, response: Response, orderId: str | None = Query(default=None, max_length=36),
              applicationId: str | None = Query(default=None, max_length=36),
              db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    _no_store(response)
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.put("")
def save_form(candidate_id: str, body: FormIn, response: Response, orderId: str | None = Query(default=None, max_length=36),
              applicationId: str | None = Query(default=None, max_length=36),
              db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    _no_store(response)
    service.save_form(db, user, candidate_id, _body(body))
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.put("/assignment")
def save_assignment(candidate_id: str, body: AssignmentIn, response: Response, orderId: str | None = Query(default=None, max_length=36),
                    applicationId: str | None = Query(default=None, max_length=36),
                    db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Change the client's job, the source, the agency or the recruiter of this candidate from the form (see the v2 contract)."""
    _no_store(response)
    fields = {key: getattr(body, key) for key in body.model_fields_set}
    for key in ("source", "recruiterId"):
        if key in fields and fields[key] is None:
            raise HTTPException(422, "Choose a source." if key == "source" else "Choose a recruiter.")
    with applications_router._mutation(db):
        outcome = form_assignment.apply(db, user, candidate_id, fields)
    if outcome["applicationId"]:  # a new job was mapped: show its application, not the one it replaced
        applicationId, orderId = outcome["applicationId"], None
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
def add_feedback(candidate_id: str, body: FeedbackIn, response: Response, orderId: str | None = Query(default=None, max_length=36),
                 applicationId: str | None = Query(default=None, max_length=36),
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """A recruiter's note on why something takes time. Owner, admin and manager. Answers the whole form."""
    _no_store(response)
    shown = body.applicationId or applicationId
    with applications_router._mutation(db):
        form_feedback.create(db, user, candidate_id, kind=body.kind, reason=body.reason, body=body.body, application_id=shown)
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.put("/feedback/{feedback_id}")
def edit_feedback(candidate_id: str, feedback_id: str, body: FeedbackEditIn, response: Response,
                  orderId: str | None = Query(default=None, max_length=36), applicationId: str | None = Query(default=None, max_length=36),
                  db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Change your own note within 15 minutes of writing it."""
    _no_store(response)
    with applications_router._mutation(db):
        form_feedback.edit(db, user, candidate_id, feedback_id, kind=body.kind, reason=body.reason, body=body.body)
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.delete("/feedback/{feedback_id}")
def delete_feedback(candidate_id: str, feedback_id: str, response: Response, orderId: str | None = Query(default=None, max_length=36),
                    applicationId: str | None = Query(default=None, max_length=36),
                    db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Delete a note: an admin any note, anyone else their own within 15 minutes."""
    _no_store(response)
    with applications_router._mutation(db):
        form_feedback.delete(db, user, candidate_id, feedback_id)
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.post("/refresh")
def refresh_form(candidate_id: str, response: Response, orderId: str | None = Query(default=None, max_length=36),
                 applicationId: str | None = Query(default=None, max_length=36),
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    _no_store(response)
    service.refresh_from_stored(db, user, candidate_id)
    return service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def upload_document(candidate_id: str, response: Response, slot: str = Form(...), file: UploadFile = File(...),
                    orderId: str | None = Query(default=None, max_length=36), applicationId: str | None = Query(default=None, max_length=36),
                    db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Store a document of any slot exactly like POST /api/files; resume and passport are queued for reading, the rest are only stored."""
    _no_store(response)
    slot = slot.strip()
    if slot not in service.SLOTS:
        raise HTTPException(422, "Choose which document this is: " + ", ".join(service.SLOTS) + ".")
    content_type = files_router._content_type(file.content_type, file.filename or "file")
    if slot == "photo" and not content_type.startswith("image/"):
        raise HTTPException(415, "The photo must be an image (JPG, PNG, WebP or HEIC).")
    payload = file.file.read(files_router.MAX_BYTES + 1)
    if not payload:
        raise HTTPException(400, "The file is empty.")
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    latest = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id,
                                                Attachment.slot == slot).order_by(Attachment.version.desc()).limit(1))
    if slot == "other" and latest is not None and latest.content_hash != digest:
        # Several files are kept in "other": the same bytes again, whichever version they were, are not stored twice.
        latest = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id,
                                                    Attachment.slot == slot, Attachment.content_hash == digest).limit(1)) or latest
    if latest is not None and latest.content_hash == digest:
        # The same file again: no new version. If reading it had failed, put it back in the queue.
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            raise HTTPException(404, "Candidate not found")
        service.ensure_can_edit(user, candidate)
        if slot in service.READ_SLOTS and service.requeue_failed(db, candidate_id, latest.id):
            db.commit()
    else:
        file.file.seek(0)
        files_router.upload(entity_type="candidate", entity_id=candidate_id, slot=slot, file=file, db=db, user=user)
    form = service.build_form(db, candidate_id, user, application_id=applicationId, order_id=orderId)
    document = form["documents"][slot]
    extraction = document["extraction"] or {"status": "done", "message": None, "jobId": None, "updatedAt": None,
                                           "kindsFound": [], "filled": [], "kept": []}
    return {"document": document, "extraction": extraction, "form": form}
