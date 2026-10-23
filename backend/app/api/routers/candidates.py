"""Candidate entry, explicit conservative merge and recruiter corrections."""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas import CandidateOut as CandidateDetailOut, Wire
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user, require_role
from app.integrations.ocr.mapping import resume_text_from
from app.services.candidate_identity import identity_matches, enrich_empty, normalize_candidate_values, normalize_phone
from app.services.candidate_intake_binding import bind_intake, lock_intake_group, saved_candidate
from app.services.candidate_search import Pool, Search, search_candidates, export_candidates, candidate_skill_facets, pool_counts
from app.services.candidate_matching import match_candidates
from app.services.parallel_reads import parallel_reads
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


def candidate_filters(q: str = Query(default="", max_length=500), pool: Pool = "all",
                      skills: list[str] = Query(default=[]), skill_mode: Literal["all", "any"] = "all", missing_skills: bool = False,
                      column_filters: str = Query(default="{}", max_length=5000), sort: str = "added", direction: Literal["asc", "desc"] = "desc",
                      staff: str | None = Query(default=None, max_length=36)):
    if len(skills) > 50 or any(len(skill) > 200 for skill in skills):
        raise HTTPException(422, "Select at most 50 skills of at most 200 characters each.")
    return Search(q, pool, skills, skill_mode, missing_skills, column_filters, sort, direction, staff or None)


@router.get("")
def candidate_list(filters: Search = Depends(candidate_filters), page: int = Query(default=0, ge=0), page_size: int = Query(default=20, ge=1, le=100),
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    return search_candidates(db, filters, page, page_size)


@router.get("/export")
def candidate_export(filters: Search = Depends(candidate_filters), db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    return Response(export_candidates(db, filters), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="candidate-data-bank.xlsx"', "Cache-Control": "private, no-store"})


@router.get("/skill-facets")
def skill_facets(response: Response, pool: Pool = "all",
                 staff: str | None = Query(default=None, max_length=36), matchable: bool = False,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return candidate_skill_facets(db, Search(pool=pool, staff=staff or None, matchable=matchable))


@router.get("/matching")
def candidate_matching(response: Response, order_id: str = Query(min_length=1, max_length=36),
                       filters: Search = Depends(candidate_filters), designation: str = Query(default="", max_length=200),
                       min_experience: float | None = Query(default=None, ge=0, le=100),
                       passport: Literal["any", "present", "missing"] = "any", location: str = Query(default="", max_length=120),
                       page: int = Query(default=0, ge=0), page_size: int = Query(default=20, ge=1, le=100),
                       db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    filters.designation, filters.min_experience = designation, min_experience
    filters.passport, filters.location = passport, location
    return match_candidates(db, order_id, filters, page, page_size)


@router.get("/pool-counts")
def candidate_pool_counts(response: Response, staff: str | None = Query(default=None, max_length=36),
                          db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Size of every Data Bank pool, so a tab or summary card can show a count and a
    supervisor can see how many candidates are still waiting for a recruiter."""
    response.headers["Cache-Control"] = "private, no-store"
    return pool_counts(db, staff or None)


@router.post("/assign-waiting")
def assign_waiting(db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))):
    """Give every owner-less candidate to the next eligible recruiter (round-robin).

    Intake leaves a candidate unassigned when nobody is eligible; this is the sweep that
    runs once staff are, and the button a supervisor can press. One transaction under the
    shared assignment-cursor lock."""
    from app.services.candidate_review import assign_waiting_candidates
    result = assign_waiting_candidates(db, user.id, user.name)
    db.commit()
    return result


@router.get("/{candidate_id}/assignment-history")
def assignment_history(candidate_id: str, response: Response, limit: int = Query(20, ge=1, le=100),
                       before: str | None = Query(None, max_length=150),
                       db: Session = Depends(get_db), user: User = Depends(current_user)):
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
    exists_, rows = parallel_reads(
        db,
        lambda session: session.scalar(select(Candidate.id).where(Candidate.id == candidate_id)) is not None,
        lambda session: list(session.scalars(statement.order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit + 1))),
    )
    if not exists_:
        raise HTTPException(404, "Candidate not found")
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


@router.get("/{candidate_id}", response_model=CandidateDetailOut)
def candidate_detail(candidate_id: str, response: Response,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_role(Role.admin, Role.manager, Role.staff))):
    """Return the existing shared profile to the three authorized CRM roles.

    BANK-01 explicitly permits Staff to read every candidate (requirements-matrix
    and workflows/07-data-bank-skills). This is the same schema/field allowlist
    already returned by bootstrap, with no application drafts, stages or tasks.
    test_desk_snapshot_scope protects that separate ownership boundary.
    """
    from app.api.routers.data import _candidate_out
    from app.db.models import Attachment
    from app.services.desk_snapshot import _fields, _rows

    statement = select(
        _rows("candidate", _fields(Candidate, CandidateDetailOut, "created_at", "call_called",
            "call_interested", "call_remarks", "call_recorded_at").where(Candidate.id == candidate_id)),
        _rows("files", select(Attachment.id, Attachment.entity_id, Attachment.slot, Attachment.version,
            Attachment.file_name, Attachment.size_kb, Attachment.uploaded_by, Attachment.uploaded_at)
            .where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id), ("version", False)),
    )
    result = db.execute(statement).mappings().one()
    if not result["candidate"]:
        raise HTTPException(404, "Candidate not found")
    files = {}
    for attachment in result["files"] or []:
        files.setdefault((candidate_id, attachment["slot"]), []).append(attachment)
    response.headers["Cache-Control"] = "private, no-store"
    return _candidate_out(result["candidate"][0], files)


class OcrDataIn(BaseModel):
    """The Veris output the form received from /api/ocr, sent back with the save.

    `resume` / `passport` / `aadhaar` are each the verbatim Veris result (the scan's
    `raw`). The form's own notes about them (the scan's `source`) go in `_sources`,
    keyed by kind, and are stored beside the documents, never inside them.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    resume: dict[str, Any] | None = None
    passport: dict[str, Any] | None = None
    aadhaar: dict[str, Any] | None = None
    sources: dict[Literal["resume", "passport", "aadhaar"], dict[str, Any]] | None = Field(default=None, alias="_sources")
    # Further documents of a kind already above, as [{"passport": {...raw...}, "_source": {...}}].
    additional_documents: list[dict[Literal["resume", "passport", "aadhaar", "_source"], dict[str, Any]]] | None = Field(default=None, max_length=50)

    def stored(self) -> dict[str, Any]:
        """The shape kept in candidates.ocr_data."""
        return self.model_dump(exclude_none=True, by_alias=True)


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
    intake_group_id: UUID | None = None

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
    intake_jobs = lock_intake_group(db, body.intake_group_id, user.id)
    recovered = saved_candidate(db, intake_jobs, user)
    if recovered is not None:
        owner = db.get(User, recovered.owner_id) if recovered.owner_id else None
        task_id = db.scalar(select(Task.id).where(Task.candidate_id == recovered.id, Task.kind == TaskKind.initial_call))
        return CandidateOut(id=recovered.id, ref=recovered.ref, name=recovered.name,
                            owner_id=recovered.owner_id, owner_name=owner.name if owner else None, task_id=task_id)
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

    ocr_data = body.ocr_data.stored() if body.ocr_data else {}

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
    bind_intake(intake_jobs, candidate.id)
    from app.services.whatsapp_chat import link_conversations_for_candidate
    link_conversations_for_candidate(db, candidate)

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
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """
    The Veris output stored when the candidate was entered, for the profile's resume
    sections. Fetched on its own because the bootstrap carries every candidate and
    never needs it. A candidate entered by hand has none, which is an empty object.

    Admin, manager and the candidate's owner get the stored payload exactly. For anyone
    else the Aadhaar number, VID and enrolment id (also where they occur in the Aadhaar
    page text) keep only their last four digits. Documents
    of a kind beyond the first are under `additional_documents` as `{kind: raw, "_source": {...}}`.
    Each kind holds the verbatim Veris result; our provenance for it is in `_sources[kind]`.
    Rows saved earlier keep their old shape (provenance keys inside each document).
    """
    # One narrow read: loading the whole candidate and then its deferred ocr_data was two round trips.
    candidate = db.execute(select(Candidate.owner_id, Candidate.ocr_data).where(Candidate.id == candidate_id)).one_or_none()
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    response.headers["Cache-Control"] = "private, no-store"
    stored = candidate.ocr_data or {}
    if user.role in {Role.admin, Role.manager} or (candidate.owner_id and candidate.owner_id == user.id):
        return stored
    return mask_aadhaar_identifiers(stored)


_AADHAAR_ID_KEYS = ("aadhaar_number", "vid", "enrollment_id")
_LONG_DIGITS = re.compile(r"(?<!\d)(\d[\d ]{9,18}\d)(?!\d)")


def _mask_digits(value: str) -> str:
    """Every digit but the last four hidden; spacing is kept so the shape still reads."""
    total = sum(c.isdigit() for c in value)
    seen = 0
    out = []
    for char in value:
        if char.isdigit():
            seen += 1
            out.append(char if seen > total - 4 else "X")
        else:
            out.append(char)
    return "".join(out)


def _mask_text(value):
    if not isinstance(value, str):
        return value
    return _LONG_DIGITS.sub(lambda m: _mask_digits(m.group(1)) if sum(c.isdigit() for c in m.group(1)) in (12, 16) else m.group(0), value)


def _mask_aadhaar_document(document):
    if not isinstance(document, dict):
        return document
    masked = copy.deepcopy(document)
    identity = masked.get("aadhaar")
    if isinstance(identity, dict):
        for key in _AADHAAR_ID_KEYS:
            if isinstance(identity.get(key), str):
                identity[key] = _mask_digits(identity[key])
    for page in masked.get("pages") or []:
        if isinstance(page, dict):
            for key in ("text", "html"):
                page[key] = _mask_text(page.get(key))
            for line in page.get("lines") or []:
                if isinstance(line, dict):
                    original = line.get("text")
                    line["text"] = _mask_text(original)
                    if line["text"] != original:
                        # The digits also sit word by word; hide every group but the last.
                        numeric = [w for w in line.get("words") or [] if isinstance(w, dict) and str(w.get("text", "")).isdigit()]
                        for word in numeric[:-1]:
                            word["text"] = "X" * len(word["text"])
    return masked


def mask_aadhaar_identifiers(stored: dict) -> dict:
    result = dict(stored)
    if "aadhaar" in result:
        result["aadhaar"] = _mask_aadhaar_document(result["aadhaar"])
    if isinstance(result.get("additional_documents"), list):
        result["additional_documents"] = [
            {**entry, "aadhaar": _mask_aadhaar_document(entry["aadhaar"])} if isinstance(entry, dict) and "aadhaar" in entry else entry
            for entry in result["additional_documents"]
        ]
    return result


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
    # Attachments take candidate -> document job locks. Keep the same ordering
    # when an existing candidate is merged with a saved intake.
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    ensure_candidate_edit(candidate, user)
    intake_jobs = lock_intake_group(db, body.intake_group_id, user.id)
    recovered = saved_candidate(db, intake_jobs, user)
    if recovered is not None and recovered.id != candidate_id:
        raise HTTPException(409, "These documents already belong to another saved candidate.")
    if recovered is not None:
        owner = db.get(User, candidate.owner_id) if candidate.owner_id else None
        return CandidateOut(id=candidate.id, ref=candidate.ref, name=candidate.name, owner_id=candidate.owner_id,
                            owner_name=owner.name if owner else None, task_id=None)
    matches = identity_matches(db, body.model_dump())
    if len(matches) != 1 or matches[0][0].id != candidate.id:
        raise HTTPException(409, "The candidate identity changed. Check duplicates again before merging.")
    if candidate.dob and body.dob and candidate.dob != body.dob:
        raise HTTPException(409, "The date of birth conflicts with the existing record. Review the documents before merging.")
    values = body.model_dump(exclude={"ocr_data", "source", "source_detail", "intake_group_id"})
    combined = {key: getattr(candidate, key) for key in PROFILE_CHECK_FIELDS}
    for key in PROFILE_CHECK_FIELDS:
        if combined[key] in (None, "") and values.get(key) not in (None, ""):
            combined[key] = values[key]
    try:
        _validate_profile_relationships(combined)
    except ValueError as exc:
        raise HTTPException(409, f"These details conflict with the retained candidate profile: {exc}") from exc
    changed = enrich_empty(candidate, values, body.ocr_data.stored() if body.ocr_data else None)
    bind_intake(intake_jobs, candidate.id)
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
    if body.intake_group_id is not None:
        raise HTTPException(422, "Resume saved document intake from Add Candidate before attaching its documents.")
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
    changes = normalize_candidate_values(body.model_dump(exclude_unset=True, exclude={"source", "source_detail", "ocr_data", "mark_reviewed", "intake_group_id"}))
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
