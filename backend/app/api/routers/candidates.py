"""Candidate entry, explicit conservative merge and recruiter corrections."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas import Wire
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user, require_role
from app.integrations.ocr.mapping import resume_text_from
from app.services.candidate_identity import identity_matches, enrich_empty, normalize_candidate_values, normalize_phone
from app.services.candidate_search import Search, search_candidates, export_candidates
from app.db.models import (
    AssignmentPointer,
    AuditEntry,
    Candidate,
    CandidateStatus,
    Notification,
    Role,
    Source,
    Task,
    TaskKind,
    User,
)

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


def candidate_filters(q: str = Query(default="", max_length=500), pool: Literal["all", "unmapped", "not_interested"] = "all",
                      skills: list[str] = Query(default=[]), skill_mode: Literal["all", "any"] = "all", missing_skills: bool = False,
                      column_filters: str = Query(default="{}", max_length=5000), sort: str = "added", direction: Literal["asc", "desc"] = "desc"):
    if len(skills) > 50 or any(len(skill) > 200 for skill in skills):
        raise HTTPException(422, "Select at most 50 skills of at most 200 characters each.")
    return Search(q, pool, skills, skill_mode, missing_skills, column_filters, sort, direction)


@router.get("")
def candidate_list(filters: Search = Depends(candidate_filters), page: int = Query(default=0, ge=0), page_size: int = Query(default=20, ge=1, le=100),
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    return search_candidates(db, filters, page, page_size)


@router.get("/export")
def candidate_export(filters: Search = Depends(candidate_filters), db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    return Response(export_candidates(db, filters), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="candidate-data-bank.xlsx"', "Cache-Control": "private, no-store"})


@router.get("/{candidate_id}/assignment-history")
def assignment_history(candidate_id: str, response: Response, limit: int = Query(20, ge=1, le=100),
                       before: str | None = Query(None, max_length=150),
                       db: Session = Depends(get_db), user: User = Depends(current_user)):
    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(404, "Candidate not found")
    statement = select(AuditEntry).where(AuditEntry.entity_type == 'candidate', AuditEntry.entity_id == candidate_id,
        or_(AuditEntry.meta.has_key('fromUserId'), AuditEntry.meta.has_key('fromOwnerId')))
    if before:
        try:
            timestamp, identifier = before.rsplit('|', 1)
            moment = datetime.fromisoformat(timestamp)
            if moment.tzinfo is None or not identifier or len(identifier) > 36:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid history cursor. Reload ownership history.")
        statement = statement.where(or_(AuditEntry.at < moment, (AuditEntry.at == moment) & (AuditEntry.id < identifier)))
    rows = list(db.scalars(statement.order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit + 1)))
    user_ids = {uid for row in rows[:limit] for uid in (row.actor_id, row.meta.get('fromUserId', row.meta.get('fromOwnerId')), row.meta.get('toUserId', row.meta.get('toOwnerId'))) if uid}
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()) if user_ids else {}
    response.headers['Cache-Control'] = 'private, no-store'
    items = []
    for row in rows[:limit]:
        previous = row.meta.get('fromUserId', row.meta.get('fromOwnerId'))
        target = row.meta.get('toUserId', row.meta.get('toOwnerId'))
        items.append({'id': row.id, 'at': row.at, 'action': row.action, 'actorId': row.actor_id,
            'actorName': names.get(row.actor_id, 'Former team member') if row.actor_id else 'System', 'fromUserId': previous,
            'fromName': names.get(previous, 'Former team member') if previous else 'Unassigned',
            'toUserId': target, 'toName': names.get(target, 'Former team member') if target else 'Unassigned',
            'reason': row.meta.get('reason')})
    return {'items': items, 'nextBefore': f'{rows[limit-1].at.isoformat()}|{rows[limit-1].id}' if len(rows) > limit else None}


class OcrDataIn(BaseModel):
    """The Veris output the form received from /api/ocr, sent back with the save."""

    model_config = ConfigDict(extra="forbid")

    resume: dict[str, Any] | None = None
    passport: dict[str, Any] | None = None
    aadhaar: dict[str, Any] | None = None


PROFILE_CHECK_FIELDS = ("phone", "dob", "passport_issue", "passport_expiry", "total_experience", "gulf_experience")


def _validate_profile_relationships(values, supplied=None):
    """Validate the combined record; partial input defers missing pairs to the locked row."""
    if values.get("dob") and values["dob"] > date.today():
        raise ValueError("Date of birth cannot be in the future.")
    if supplied is None or {"passport_issue", "passport_expiry"} <= supplied:
        if values.get("passport_issue") and values.get("passport_expiry") and values["passport_expiry"] < values["passport_issue"]:
            raise ValueError("Passport expiry cannot precede its issue date.")
    if supplied is None or {"total_experience", "gulf_experience"} <= supplied:
        if values["gulf_experience"] > values["total_experience"]:
            raise ValueError("Gulf experience cannot exceed total experience.")
    if not normalize_phone(values["phone"]):
        raise ValueError("Enter a valid phone number with at least seven digits.")


class CandidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=40)
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
    total_experience: int = Field(default=0, ge=0, le=80)
    gulf_experience: int = Field(default=0, ge=0, le=80)
    skills: list[str] = Field(default_factory=list)
    expected_salary: float | None = Field(default=None, ge=0, le=9999999999.99, allow_inf_nan=False)

    source: Source = Source.walk_in
    source_detail: str | None = None
    resume_text: str | None = None
    ocr_data: OcrDataIn | None = None

    @field_validator("name", "phone")
    @classmethod
    def nonblank(cls, raw):
        if not raw.strip():
            raise ValueError("This field cannot be blank.")
        return raw.strip()

    @field_validator("whatsapp", "email", "gender", "nationality", "current_city", "passport_no", "passport_place", "source_detail")
    @classmethod
    def bounded_text(cls, raw, info):
        if raw is None:
            return None
        limit = {"whatsapp": 40, "email": 255, "gender": 20, "nationality": 80, "current_city": 120,
                 "passport_no": 40, "passport_place": 120, "source_detail": 255}[info.field_name]
        if len(raw.strip()) > limit:
            raise ValueError(f"Use at most {limit} characters.")
        return raw.strip() or None

    @field_validator("skills", "designations", "languages")
    @classmethod
    def bounded_lists(cls, values):
        if len(values) > 200 or any(len(item) > 200 for item in values):
            raise ValueError("Use at most 200 entries, each no longer than 200 characters.")
        return values

    @model_validator(mode="after")
    def dates(self):
        _validate_profile_relationships({key: getattr(self, key) for key in PROFILE_CHECK_FIELDS})
        return self


class DuplicateMatch(Wire):
    id: str
    ref: str
    name: str
    phone: str
    dob: date | None
    owner_name: str | None
    owner_id: str | None = None
    matched_on: list[str]


class CandidateOut(Wire):
    id: str
    ref: str
    name: str
    owner_id: str | None
    owner_name: str | None
    task_id: str | None


def find_duplicate(db: Session, phone: str, dob: date | None, passport_no: str | None, email: str | None = None):
    matches = identity_matches(db, {"phone": phone, "email": email, "passport_no": passport_no})
    if len(matches) > 1:
        raise HTTPException(409, "Phone, email or passport match different candidates. Resolve the identity conflict before merging.")
    return matches[0] if matches else None


def lock_assignment_cursor(db: Session) -> AssignmentPointer:
    """Serialize candidate identity, reference and assignment decisions together."""
    db.execute(insert(AssignmentPointer).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return db.scalar(select(AssignmentPointer).where(AssignmentPointer.id == 1).with_for_update())


def next_owner(db: Session, *, pointer: AssignmentPointer | None = None) -> User | None:
    """
    Strict round-robin across eligible staff. The pointer row is taken FOR UPDATE so two
    candidates arriving in the same moment cannot be handed to the same recruiter.
    """
    if pointer is None:
        pointer = lock_assignment_cursor(db)

    from app.services.attendance_availability import unavailable_staff_ids
    absent = unavailable_staff_ids(db)

    eligible = list(
        db.scalars(
            select(User)
            .where(
                User.role == Role.staff,
                User.active.is_(True),
                User.receives_auto_assign.is_(True),
                User.on_leave.is_(False),
                User.id.not_in(absent),
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
    pointer = lock_assignment_cursor(db)
    match = find_duplicate(db, body.phone, body.dob, body.passport_no, body.email)
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
                    owner_id=existing.owner_id,
                    matched_on=fields,
                ).model_dump(by_alias=True, mode="json"),
            },
        )

    owner = next_owner(db, pointer=pointer)

    ocr_data = body.ocr_data.model_dump(exclude_none=True) if body.ocr_data else {}

    candidate = Candidate(
        ref=f"CND-{db.scalar(select(func.nextval('crm_candidate_reference_seq'))):05d}",
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
        status=CandidateStatus.new if owner else CandidateStatus.needs_review,
        needs_review=owner is None,
        owner_id=owner.id if owner else None,
        intake_owner_id=owner.id if owner else None,
        created_by=user.id,
        # The scanned resume's own text when there is one; it is what the Data Bank
        # searches, and far more of it than the form's summary line.
        resume_text=resume_text_from(ocr_data) or body.resume_text,
        ocr_data=ocr_data or None,
    )
    for key, val in normalize_candidate_values({"skills": body.skills, "designations": body.designations, "languages": body.languages,
                                               "phone": body.phone, "whatsapp": body.whatsapp,
                                               "email": body.email, "passport_no": body.passport_no}).items():
        setattr(candidate, key, val)
    db.add(candidate)
    db.flush()

    task_id: str | None = None
    if owner is not None:
        started = datetime.now(timezone.utc)
        task = Task(
            kind=TaskKind.initial_call,
            title="Initial call: confirm interest",
            candidate_id=candidate.id,
            owner_id=owner.id,
            created_at=started, sla_started_at=started, due_at=started + timedelta(hours=48),
        )
        db.add(task)
        db.flush()
        task_id = task.id

    recipients = set(db.scalars(select(User.id).where(User.active.is_(True), User.role.in_([Role.admin, Role.manager]))))
    if owner:
        recipients.add(owner.id)
    for recipient in recipients:
        db.add(Notification(user_id=recipient, kind="candidate_assignment", title="Candidate assigned" if owner else "Candidate needs assignment",
                            detail=candidate.name, link_to=f"/candidates/{candidate.id}"))

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
    match = find_duplicate(db, body.phone, body.dob, body.passport_no, body.email)
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
            owner_id=existing.owner_id,
            matched_on=fields,
        ).model_dump(by_alias=True, mode="json"),
    }


def ensure_candidate_edit(candidate, user):
    if user.role == Role.staff and candidate.owner_id != user.id:
        raise HTTPException(403, "Only the assigned recruiter or a supervisor can change this candidate.")


@router.post("/{candidate_id}/merge", response_model=CandidateOut)
def merge_candidate(candidate_id: str, body: CandidateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    ensure_candidate_edit(candidate, user)
    matches = identity_matches(db, body.model_dump())
    if len(matches) != 1 or matches[0][0].id != candidate.id:
        raise HTTPException(409, "The candidate identity changed. Check duplicates again before merging.")
    if candidate.dob and body.dob and candidate.dob != body.dob:
        raise HTTPException(409, "The date of birth conflicts with the existing record. Review the documents before merging.")
    values = body.model_dump(exclude={"ocr_data", "source", "source_detail"})
    combined = {key: getattr(candidate, key) for key in PROFILE_CHECK_FIELDS}
    for key in PROFILE_CHECK_FIELDS:
        if combined[key] in (None, "") and values.get(key) not in (None, ""):
            combined[key] = values[key]
    try:
        _validate_profile_relationships(combined)
    except ValueError as exc:
        raise HTTPException(409, f"These details conflict with the retained candidate profile: {exc}") from exc
    changed = enrich_empty(candidate, values, body.ocr_data.model_dump(exclude_none=True) if body.ocr_data else None)
    db.add(AuditEntry(actor_id=user.id, action="Merged candidate entry", target=candidate.name, entity_type="candidate", entity_id=candidate.id, meta={"filled_fields": changed}))
    db.commit()
    owner_user = db.get(User, candidate.owner_id) if candidate.owner_id else None
    return CandidateOut(id=candidate.id, ref=candidate.ref, name=candidate.name, owner_id=candidate.owner_id,
                        owner_name=owner_user.name if owner_user else None, task_id=None)


class CandidateCorrection(CandidateIn):
    mark_reviewed: bool = False

    @model_validator(mode="after")
    def dates(self):
        _validate_profile_relationships({key: getattr(self, key) for key in PROFILE_CHECK_FIELDS}, self.model_fields_set)
        return self


@router.patch("/{candidate_id}")
def correct_candidate(candidate_id: str, body: CandidateCorrection, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    ensure_candidate_edit(candidate, user)
    if identity_matches(db, body.model_dump(), exclude_id=candidate.id):
        raise HTTPException(409, "The corrected phone, email or passport belongs to another candidate.")
    if body.mark_reviewed and not candidate.owner_id:
        raise HTTPException(422, "Assign this candidate to an eligible recruiter before clearing review.")
    # Patch only explicitly sent editable fields. Ownership, source provenance and
    # OCR evidence are never replaced by a profile correction.
    changes = normalize_candidate_values(body.model_dump(exclude_unset=True, exclude={"source", "source_detail", "ocr_data", "mark_reviewed"}))
    try:
        _validate_profile_relationships({key: changes.get(key, getattr(candidate, key)) for key in PROFILE_CHECK_FIELDS})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    before = {key: getattr(candidate, key) for key in changes}
    for key, val in changes.items():
        setattr(candidate, key, val)
    if body.mark_reviewed:
        candidate.needs_review = False
        if candidate.status == CandidateStatus.needs_review:
            candidate.status = CandidateStatus.new
    db.add(AuditEntry(actor_id=user.id, action="Corrected candidate", target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"changed_fields": [key for key in changes if before[key] != changes[key]], "review_completed": body.mark_reviewed}))
    db.commit()
    return {"id": candidate.id, "needsReview": candidate.needs_review}
