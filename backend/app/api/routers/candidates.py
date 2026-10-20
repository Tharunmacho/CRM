"""
Candidate entry.

A duplicate is refused outright rather than offered as a merge: if the date of birth
and the phone number both match an existing record, the save is rejected and the
existing candidate is returned so the user can go and look at it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import Wire
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user
from app.integrations.ocr.mapping import resume_text_from
from app.db.models import (
    AssignmentPointer,
    AuditEntry,
    Candidate,
    CandidateStatus,
    Role,
    Source,
    Task,
    TaskKind,
    User,
)

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


class OcrDataIn(BaseModel):
    """The Veris output the form received from /api/ocr, sent back with the save."""

    model_config = ConfigDict(extra="forbid")

    resume: dict[str, Any] | None = None
    passport: dict[str, Any] | None = None


class CandidateIn(BaseModel):
    name: str
    phone: str
    dob: date | None = None
    whatsapp: str | None = None
    email: str | None = None
    gender: str | None = None
    nationality: str | None = "Indian"
    current_city: str | None = None
    languages: list[str] = Field(default_factory=list)

    passport_no: str | None = None
    passport_issue: date | None = None
    passport_expiry: date | None = None
    passport_place: str | None = None

    designations: list[str] = Field(default_factory=list)
    total_experience: int = 0
    gulf_experience: int = 0
    skills: list[str] = Field(default_factory=list)
    expected_salary: float | None = None

    source: Source = Source.walk_in
    source_detail: str | None = None
    resume_text: str | None = None
    ocr_data: OcrDataIn | None = None


class DuplicateMatch(Wire):
    id: str
    ref: str
    name: str
    phone: str
    dob: date | None
    owner_name: str | None
    matched_on: list[str]


class CandidateOut(Wire):
    id: str
    ref: str
    name: str
    owner_id: str | None
    owner_name: str | None
    task_id: str | None


def find_duplicate(db: Session, phone: str, dob: date | None, passport_no: str | None) -> tuple[Candidate, list[str]] | None:
    """
    Date of birth *and* phone must both match — one alone is not enough, since families
    share handsets and a date of birth is far from unique.

    An exact passport match is also refused on its own: it is the strongest identity a
    candidate has, and two records for one passport will collide later at stage 7.
    """
    # Compare the last ten digits. The same mobile is written "+91 98400 11111",
    # "098400 11111" and "9840011111"; matching the whole string, or even all the
    # digits, would let every one of those through as a new person.
    digits = "".join(ch for ch in phone if ch.isdigit())
    tail = digits[-10:]

    if dob is not None and len(tail) == 10:
        existing = db.scalars(
            select(Candidate).where(
                Candidate.dob == dob,
                func.right(func.regexp_replace(Candidate.phone, r"\D", "", "g"), 10) == tail,
            )
        ).first()
        if existing is not None:
            return existing, ["date of birth", "phone number"]

    if passport_no:
        existing = db.scalars(
            select(Candidate).where(func.upper(Candidate.passport_no) == passport_no.upper())
        ).first()
        if existing is not None:
            return existing, ["passport number"]

    return None


def next_owner(db: Session) -> User | None:
    """
    Strict round-robin across eligible staff. The pointer row is taken FOR UPDATE so two
    candidates arriving in the same moment cannot be handed to the same recruiter.
    """
    pointer = db.scalar(select(AssignmentPointer).where(AssignmentPointer.id == 1).with_for_update())
    if pointer is None:
        pointer = AssignmentPointer(id=1)
        db.add(pointer)
        db.flush()

    eligible = list(
        db.scalars(
            select(User)
            .where(
                User.role == Role.staff,
                User.active.is_(True),
                User.receives_auto_assign.is_(True),
                User.on_leave.is_(False),
            )
            .order_by(User.created_at, User.id)
        )
    )
    if not eligible:
        return None

    ids = [u.id for u in eligible]
    if pointer.last_user_id in ids:
        nxt = eligible[(ids.index(pointer.last_user_id) + 1) % len(ids)]
    else:
        nxt = eligible[0]

    pointer.last_user_id = nxt.id
    return nxt


@router.post("", response_model=CandidateOut, status_code=status.HTTP_201_CREATED)
def create_candidate(
    body: CandidateIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CandidateOut:
    match = find_duplicate(db, body.phone, body.dob, body.passport_no)
    if match is not None:
        existing, fields = match
        owner = db.get(User, existing.owner_id) if existing.owner_id else None
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": f"Duplicate found — {' and '.join(fields)} already on file.",
                "candidate": DuplicateMatch(
                    id=existing.id,
                    ref=existing.ref,
                    name=existing.name,
                    phone=existing.phone,
                    dob=existing.dob,
                    owner_name=owner.name if owner else None,
                    matched_on=fields,
                ).model_dump(by_alias=True, mode="json"),
            },
        )

    count = db.scalar(select(func.count()).select_from(Candidate)) or 0
    owner = next_owner(db)

    ocr_data = body.ocr_data.model_dump(exclude_none=True) if body.ocr_data else {}

    candidate = Candidate(
        ref=f"CND-{1000 + count + 1}",
        name=body.name.strip(),
        phone=body.phone.strip(),
        dob=body.dob,
        whatsapp=body.whatsapp,
        email=body.email.strip().lower() if body.email else None,
        gender=body.gender,
        nationality=body.nationality,
        current_city=body.current_city,
        languages=body.languages,
        passport_no=body.passport_no.strip().upper() if body.passport_no else None,
        passport_issue=body.passport_issue,
        passport_expiry=body.passport_expiry,
        passport_place=body.passport_place,
        designations=body.designations,
        total_experience=body.total_experience,
        gulf_experience=body.gulf_experience,
        skills=body.skills,
        expected_salary=body.expected_salary,
        source=body.source,
        source_detail=body.source_detail,
        status=CandidateStatus.new,
        owner_id=owner.id if owner else None,
        # The scanned resume's own text when there is one; it is what the Data Bank
        # searches, and far more of it than the form's summary line.
        resume_text=resume_text_from(ocr_data) or body.resume_text,
        ocr_data=ocr_data or None,
    )
    db.add(candidate)
    db.flush()

    task_id: str | None = None
    if owner is not None:
        task = Task(
            kind=TaskKind.initial_call,
            title="Initial call: confirm interest",
            candidate_id=candidate.id,
            owner_id=owner.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.flush()
        task_id = task.id

    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Created candidate",
            target=candidate.name,
            entity_type="candidate",
            entity_id=candidate.id,
        )
    )
    db.commit()

    return CandidateOut(
        id=candidate.id,
        ref=candidate.ref,
        name=candidate.name,
        owner_id=owner.id if owner else None,
        owner_name=owner.name if owner else None,
        task_id=task_id,
    )


@router.get("/{candidate_id}/ocr")
def candidate_ocr(
    candidate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """
    The Veris output stored when the candidate was entered, for the profile's resume
    sections. Fetched on its own because the bootstrap carries every candidate and
    never needs it. A candidate entered by hand has none, which is an empty object.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    return candidate.ocr_data or {}


@router.post("/check-duplicate")
def check_duplicate(
    body: CandidateIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Same rule as the save, so the form can warn before the user finishes typing."""
    match = find_duplicate(db, body.phone, body.dob, body.passport_no)
    if match is None:
        return {"duplicate": False}
    existing, fields = match
    owner = db.get(User, existing.owner_id) if existing.owner_id else None
    return {
        "duplicate": True,
        "matched_on": fields,
        "candidate": DuplicateMatch(
            id=existing.id,
            ref=existing.ref,
            name=existing.name,
            phone=existing.phone,
            dob=existing.dob,
            owner_name=owner.name if owner else None,
            matched_on=fields,
        ).model_dump(by_alias=True, mode="json"),
    }
