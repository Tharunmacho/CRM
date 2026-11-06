"""Candidate entry, explicit conservative merge and recruiter corrections."""

from __future__ import annotations

import copy
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas import CandidateWithFormOut as CandidateDetailOut, Wire
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services import document_storage
from app.core.deps import candidate_work_refusal, current_user, ensure_can_work_candidate, require_role
from app.integrations.ocr.mapping import resume_text_from
from app.services import client_routing
from app.services import form_extraction as form_fx
from app.services.candidate_identity import identity_matches, enrich_empty, normalize_candidate_values, normalize_phone
from app.services.candidate_intake_binding import bind_intake, lock_intake_group, saved_candidate, saved_candidate_id
from app.services.candidate_review import UNNAMED
from app.services.candidate_search import Pool, Search, search_candidates, export_candidates, candidate_skill_facets, pool_counts, position_groups
from app.services.candidate_matching import match_candidates
from app.services.parallel_reads import parallel_reads
from app.services.response_cache import cached, scope_of
from app.db.models import (
    Application,
    ApplicationStatus,
    AssignmentPointer,
    Attachment,
    AuditEntry,
    Candidate,
    CandidateStatus,
    DocumentExtractionJob,
    DocumentPersonGroup,
    EmailIngestionJob,
    MailAttachment,
    MailDraftAttachment,
    MailMessage,
    Notification,
    Order,
    Role,
    Source,
    Task,
    TaskKind,
    TodoItem,
    User,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppSendAttempt,
)

router = APIRouter(prefix="/api/candidates", tags=["candidates"])
logger = logging.getLogger(__name__)


def candidate_filters(q: str = Query(default="", max_length=500), pool: Pool = "all",
                      skills: list[str] = Query(default=[]), skill_mode: Literal["all", "any"] = "all", missing_skills: bool = False,
                      column_filters: str = Query(default="{}", max_length=5000), sort: str = "added", direction: Literal["asc", "desc"] = "desc",
                      staff: str | None = Query(default=None, max_length=36), position_id: str = Query(default="", max_length=36)):
    if len(skills) > 50 or any(len(skill) > 200 for skill in skills):
        raise HTTPException(422, "Select at most 50 skills of at most 200 characters each.")
    return Search(q, pool, skills, skill_mode, missing_skills, column_filters, sort, direction, staff or None, order_id=position_id)


# The Data Bank summaries (pool sizes, skill facets, position groups) are the same for every signed-in user - the bank is
# shared - and they only change when a candidate, placement, order or recruiter does. They are kept for up to a minute
# and dropped the moment one of those commits (see services/response_cache.py).
BANK_TOPICS = ("candidates", "applications", "orders", "designations", "companies", "clients", "users")
BANK_SECONDS = 60


@router.get("")
def candidate_list(filters: Search = Depends(candidate_filters), page: int = Query(default=0, ge=0), page_size: int = Query(default=20, ge=1, le=100),
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    # The bank is shared by every signed-in user and the answer depends only on the search, so it is kept for a few seconds
    # (and dropped by any candidate, placement, order or recruiter commit): paging back, a second tab and a refresh are free.
    return cached(("candidate-list", scope_of(db), repr(filters), page, page_size), BANK_TOPICS, 15,
                  lambda: search_candidates(db, filters, page, page_size))


@router.get("/position-groups")
def candidate_position_groups(response: Response, filters: Search = Depends(candidate_filters),
                              db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The positions (orders) that hold the candidates this search finds, with a resume count each."""
    response.headers["Cache-Control"] = "private, no-store"
    return cached(("position-groups", scope_of(db), repr(filters)), BANK_TOPICS, BANK_SECONDS, lambda: position_groups(db, filters))


@router.get("/export")
def candidate_export(filters: Search = Depends(candidate_filters), db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    return Response(export_candidates(db, filters), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="candidate-data-bank.xlsx"', "Cache-Control": "private, no-store"})


@router.get("/skill-facets")
def skill_facets(response: Response, pool: Pool = "all",
                 staff: str | None = Query(default=None, max_length=36), matchable: bool = False,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    search = Search(pool=pool, staff=staff or None, matchable=matchable)
    return cached(("skill-facets", scope_of(db), repr(search)), BANK_TOPICS, BANK_SECONDS, lambda: candidate_skill_facets(db, search))


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
    return cached(("pool-counts", scope_of(db), staff or None), BANK_TOPICS, BANK_SECONDS, lambda: pool_counts(db, staff or None))


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
            raise HTTPException(422, "We could not load more history. Reload the page and try again.")
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
    return _candidate_out(result["candidate"][0], files, CandidateDetailOut)


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
    # A phone may be blank (a candidate saved from an uploaded document before the number is known);
    # a number that is given must still be a real one.
    if values["phone"] and not normalize_phone(values["phone"]):
        raise ValueError("Enter a valid phone number with at least seven digits.")


class EducationRowIn(BaseModel):
    """One educational row of the application form. Same shape as the form contract: {level, passedYear, institute}."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    level: str | None = Field(default=None, max_length=200)
    passedYear: int | None = Field(default=None, ge=1950, le=date.today().year + 6, validation_alias=AliasChoices("passedYear", "passed_year"))
    institute: str | None = Field(default=None, max_length=300)


class WorkRowIn(BaseModel):
    """One previous job: {company, designation, country, period}."""
    model_config = ConfigDict(extra="forbid")
    company: str | None = Field(default=None, max_length=200)
    designation: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=80)
    period: str | None = Field(default=None, max_length=120)


# The application-form contract names a few candidate fields differently from this older snake_case body.
_FORM_ALIASES = {
    "candidateName": "name", "mobileNumber": "phone", "dateOfBirth": "dob", "languagesKnown": "languages",
    "passportNumber": "passport_no", "placeOfIssue": "passport_place", "issueDate": "passport_issue",
    "expiryDate": "passport_expiry", "experienceAbroad": "gulf_experience", "jobPreference": "designations",
}


class CandidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Both are required unless the candidate comes from an uploaded document (see `dates`).
    name: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    dob: date | None = None
    whatsapp: str | None = None
    email: str | None = None
    gender: str | None = None
    nationality: str | None = None
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

    # Application-form details (migration 0021). camelCase names (fatherName, workHistory, experienceIndia,
    # experienceAbroad, ...) are accepted as well; see docs/workflows/application-form-contract.md.
    father_name: str | None = None
    marital_status: str | None = None
    current_address: str | None = None
    education: list[EducationRowIn] = Field(default_factory=list, max_length=form_fx.MAX_EDUCATION)
    work_history: list[WorkRowIn] = Field(default_factory=list, max_length=form_fx.MAX_HISTORY)
    experience_india: int = Field(default=0, ge=0, le=80)

    # Screening details (telemarketer intake form). Job preference is `designations`, the agent `source_detail`.
    native_place: str | None = None
    family_member_number: str | None = None
    job_duration_preference: str | None = None
    country_preference: str | None = None
    best_time_to_contact: str | None = None
    # Checked against the admin-managed lists (services/form_options.py) when saved, not frozen here.
    service_charges_status: str = Field(default="not_discussed", min_length=1, max_length=20)
    interest_level: str | None = Field(default=None, max_length=20)
    next_follow_up_date: date | None = None

    source: Source = Source.walk_in
    source_detail: str | None = None
    resume_text: str | None = None
    ocr_data: OcrDataIn | None = None
    intake_group_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def camel_case_names(cls, data):
        """Accept the application form's camelCase names beside the snake_case ones (snake_case wins if both are sent)."""
        if not isinstance(data, dict):
            return data
        renamed = {}
        # "WhatsApp is the same as the phone": a flag, not a stored column. Copies the phone unless a number was sent.
        same = data.get("whatsappSameAsPhone", data.get("whatsapp_same_as_phone"))
        data = {k: v for k, v in data.items() if k not in ("whatsappSameAsPhone", "whatsapp_same_as_phone")}
        phone = data.get("phone", data.get("mobileNumber"))
        if same is True and phone and not data.get("whatsapp"):
            data["whatsapp"] = phone
        for key, value in data.items():
            snake = _FORM_ALIASES.get(key) or (re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower() if isinstance(key, str) and key != key.lower() else key)
            if snake != key and snake in data:
                continue
            renamed[snake] = value
        return renamed

    @field_validator("name", "phone")
    @classmethod
    def trimmed(cls, raw):
        return raw.strip()

    @field_validator("whatsapp", "email", "gender", "nationality", "current_city", "passport_no", "passport_place", "source_detail",
                     "father_name", "marital_status", "current_address", "native_place", "family_member_number",
                     "job_duration_preference", "country_preference", "best_time_to_contact")
    @classmethod
    def bounded_text(cls, raw, info):
        if raw is None:
            return None
        limit = {"whatsapp": 40, "email": 255, "gender": 20, "nationality": 80, "current_city": 120,
                 "passport_no": 40, "passport_place": 120, "source_detail": 255,
                 "father_name": 200, "marital_status": 30, "current_address": 500, "native_place": 120,
                 "family_member_number": 40, "job_duration_preference": 60, "country_preference": 200,
                 "best_time_to_contact": 60}[info.field_name]
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
        # A document is the evidence for a candidate saved without them; they are then flagged for review.
        if self.intake_group_id is None and not (self.name and self.phone):
            raise ValueError("Enter the candidate's name and phone number.")
        _validate_profile_relationships({key: getattr(self, key) for key in PROFILE_CHECK_FIELDS})
        return self


class DuplicateLookup(CandidateIn):
    """The live duplicate check only matches on phone, email and passport, so nothing else is required."""

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
    # False when the signed-in person may look at this candidate but not work on it (another recruiter's candidate).
    can_merge: bool = True


class CandidateOut(Wire):
    id: str
    ref: str
    name: str
    owner_id: str | None
    owner_name: str | None
    task_id: str | None


class CandidateCreate(CandidateIn):
    # Admin and manager may name the recruiter right away; empty means "the next free recruiter".
    # A staff member's own entry always goes to that staff member, so this is refused for them.
    assign_to: str | None = Field(default=None, max_length=36)


def duplicate_match(db: Session, existing: Candidate, fields: list[str], user: User) -> dict:
    """The existing candidate as the duplicate dialog shows it, including who owns it and whether `user` may combine into it."""
    owner = db.get(User, existing.owner_id) if existing.owner_id else None
    return DuplicateMatch(
        id=existing.id, ref=existing.ref, name=existing.name, phone=existing.phone, dob=existing.dob,
        owner_name=owner.name if owner else None, owner_id=existing.owner_id, matched_on=fields,
        can_merge=candidate_work_refusal(db, existing, user) is None,
    ).model_dump(by_alias=True, mode="json")


def ensure_assignable(db: Session, user_id: str | None) -> User:
    """The person a supervisor hands a candidate to: an active staff member who is not on leave."""
    from app.services.attendance_availability import unavailable_staff_ids
    target = db.get(User, user_id) if user_id else None
    if target is None or target.role != Role.staff or not target.active or target.on_leave or target.id in unavailable_staff_ids(db):
        raise HTTPException(422, "Choose an active staff member who is not on leave")
    return target


def find_duplicate(db: Session, phone: str, dob: date | None, passport_no: str | None, email: str | None = None):
    matches = identity_matches(db, {"phone": phone, "email": email, "passport_no": passport_no})
    if len(matches) > 1:
        raise HTTPException(409, "The phone, email or passport belong to different candidates. Sort out the duplicate before you merge.")
    return matches[0] if matches else None


def _check_form_choices(db: Session, body: "CandidateIn", current=None) -> None:
    """New values of the admin-managed lists must be active options; a value the candidate already has stays valid."""
    from app.services.form_options import ensure_allowed
    sent = body.model_fields_set
    for field, category in (("service_charges_status", "service_charges_status"), ("interest_level", "interest_level")):
        if field in sent:
            ensure_allowed(db, category, getattr(body, field), current=getattr(current, field, None) if current is not None else None,
                           default="not_discussed" if field == "service_charges_status" else None)
    if "source" in sent and (current is None):
        ensure_allowed(db, "candidate_source", body.source, default=Source.walk_in)


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

    # The pointer is located in the FULL staff ring (not only the eligible subset), so a
    # pointer user who has since gone on leave / switched auto-assign off / been deactivated
    # still marks a position: we continue with the next eligible person after that position
    # instead of restarting at the top (which would hand the first person two in a row and
    # skip the one after the pointer). Deactivated staff stay in the ring for the same reason.
    ring = list(db.scalars(select(User).where(User.role == Role.staff).order_by(User.created_at, User.id)))
    absent = set(absent)

    def is_eligible(u: User) -> bool:
        return bool(u.active and u.receives_auto_assign and not u.on_leave and u.id not in absent)

    if not any(is_eligible(u) for u in ring):
        return None

    ids = [u.id for u in ring]
    # If the pointer user no longer exists at all (hard-deleted/FK nulled) or was never set,
    # there is no position to continue from, so start at the first eligible person.
    start = ids.index(pointer.last_user_id) + 1 if pointer.last_user_id in ids else 0
    nxt = None
    for step in range(len(ring)):
        candidate = ring[(start + step) % len(ring)]
        if is_eligible(candidate):
            nxt = candidate
            break

    pointer.last_user_id = nxt.id
    return nxt


@router.post("", response_model=CandidateOut, status_code=status.HTTP_201_CREATED)
def create_candidate(
    body: CandidateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CandidateOut:
    pointer = lock_assignment_cursor(db)
    _check_form_choices(db, body)
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
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": f"Duplicate found — {' and '.join(fields)} already on file.",
             "candidate": duplicate_match(db, existing, fields, user)},
        )

    # Who gets the candidate. A staff member who adds a candidate works it: it is assigned to them and the
    # round-robin is left alone. Admin and manager may name a recruiter, or leave it to the next free one.
    requested = getattr(body, "assign_to", None)
    # Client staff routing (see services/client_routing.py): a designation that an open order of a client with allocated staff
    # asks for goes to the least-loaded of that staff. An admin's or manager's explicit choice always wins.
    if user.role == Role.staff:
        if requested and requested != user.id:
            raise HTTPException(403, "A candidate you add is assigned to you. Only a manager or an admin can choose another recruiter.")
        owner = client_routing.route_new_candidate(db, body.designations, keep_user_id=user.id) or user
    elif requested:
        owner = ensure_assignable(db, requested)
    else:
        owner = client_routing.route_new_candidate(db, body.designations) or next_owner(db, pointer=pointer)

    ocr_data = body.ocr_data.stored() if body.ocr_data else {}
    # Saved from a document that did not give a name or number: kept, and flagged for a person to complete.
    incomplete = not (body.name and body.phone)

    candidate = Candidate(
        ref=f"CND-{db.scalar(select(func.nextval('crm_candidate_reference_seq'))):05d}",
        name=body.name or UNNAMED,
        phone=body.phone,
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
        **form_fx.form_columns_from(body),
        source=body.source,
        source_detail=body.source_detail,
        status=CandidateStatus.new if owner and not incomplete else CandidateStatus.needs_review,
        needs_review=owner is None or incomplete,
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
    if not ocr_data:
        # Typed by hand: protect the form values from later document reads. (With OCR output, form_sync stamps the
        # ones that equal what the document says as "extracted"; anything the person changed stays unmarked, i.e. kept.)
        form_fx.mark_manual(candidate, [column for column in form_fx.SPECS if column in body.model_fields_set and column not in ("education", "work_history")
                                        or column in ("education", "work_history") and getattr(body, column)])
    db.add(candidate)
    db.flush()
    bind_intake(intake_jobs, candidate.id)
    if candidate.next_follow_up_date:
        from app.services.application_form import sync_follow_up_todo
        sync_follow_up_todo(db, candidate)
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

    # PRD: the owner and the admin are told about an assignment. Admins and managers both hear about a candidate nobody could take.
    told = [Role.admin] if owner else [Role.admin, Role.manager]
    recipients = set(db.scalars(select(User.id).where(User.active.is_(True), User.role.in_(told))))
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
    body: DuplicateLookup,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Same rule as the save, so the form can warn before the user finishes typing."""
    match = find_duplicate(db, body.phone, body.dob, body.passport_no, body.email)
    if match is None:
        return {"duplicate": False}
    existing, fields = match
    return {"duplicate": True, "matched_on": fields, "candidate": duplicate_match(db, existing, fields, user)}


def ensure_candidate_edit(candidate, user):
    ensure_can_work_candidate(candidate, user)


@router.post("/{candidate_id}/merge", response_model=CandidateOut)
def merge_candidate(candidate_id: str, body: CandidateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    # Attachments take candidate -> document job locks. Keep the same ordering
    # when an existing candidate is merged with a saved intake.
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    # Combining adds documents and fills empty fields of someone's candidate, so it is work on that
    # candidate: the assigned recruiter, a manager or an admin. Anyone else is told who owns it.
    ensure_can_work_candidate(candidate, user, db)
    intake_jobs = lock_intake_group(db, body.intake_group_id, user.id)
    recovered_id = saved_candidate_id(intake_jobs)
    if recovered_id is not None and recovered_id != candidate_id:
        raise HTTPException(409, "These documents already belong to another saved candidate.")
    if recovered_id is not None:
        owner = db.get(User, candidate.owner_id) if candidate.owner_id else None
        return CandidateOut(id=candidate.id, ref=candidate.ref, name=candidate.name, owner_id=candidate.owner_id,
                            owner_name=owner.name if owner else None, task_id=None)
    matches = identity_matches(db, body.model_dump())
    if len(matches) != 1 or matches[0][0].id != candidate.id:
        raise HTTPException(409, "This candidate has changed. Check for duplicates again before you merge.")
    if candidate.dob and body.dob and candidate.dob != body.dob:
        raise HTTPException(409, "The date of birth does not match the existing record. Check the documents before you merge.")
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
    db.add(AuditEntry(actor_id=user.id, action="Merged candidate entry", target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"filled_fields": changed, "merged_by": user.name, "owner_id": candidate.owner_id}))
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
        raise HTTPException(422, "Open the saved upload from Add Candidate before you attach its documents.")
    lock_assignment_cursor(db)
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    ensure_candidate_edit(candidate, user)
    _check_form_choices(db, body, candidate)
    if identity_matches(db, body.model_dump(), exclude_id=candidate.id):
        raise HTTPException(409, "The corrected phone, email or passport already belongs to another candidate.")
    if body.mark_reviewed and not candidate.owner_id:
        raise HTTPException(422, "Give this candidate to a recruiter who can take them before you clear the review.")
    # Patch only explicitly sent editable fields. Ownership, source provenance and
    # OCR evidence are never replaced by a profile correction.
    changes = normalize_candidate_values(body.model_dump(exclude_unset=True, exclude={"source", "source_detail", "ocr_data", "mark_reviewed", "intake_group_id"}))
    try:
        _validate_profile_relationships({key: changes.get(key, getattr(candidate, key)) for key in PROFILE_CHECK_FIELDS})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    before = {key: getattr(candidate, key) for key in changes}
    for key in ("name", "phone"):
        if key in changes and not changes[key] and before[key] not in ("", UNNAMED):
            raise HTTPException(422, f"The candidate's {'name' if key == 'name' else 'phone number'} cannot be removed. Replace it instead.")
    for key, val in changes.items():
        setattr(candidate, key, val)
    # A person corrected these, so a later document read must leave them alone.
    form_fx.mark_manual(candidate, [key for key in changes if key in form_fx.SPECS and before[key] != changes[key]])
    if "next_follow_up_date" in changes or "owner_id" in changes:
        from app.services.application_form import sync_follow_up_todo
        sync_follow_up_todo(db, candidate)
    if body.mark_reviewed:
        candidate.needs_review = False
        if candidate.status == CandidateStatus.needs_review:
            candidate.status = CandidateStatus.new
    db.add(AuditEntry(actor_id=user.id, action="Corrected candidate", target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"changed_fields": [key for key in changes if before[key] != changes[key]], "review_completed": body.mark_reviewed}))
    db.commit()
    return {"id": candidate.id, "needsReview": candidate.needs_review}


# ----------------------------------------------------------------- deletion
#
# Admin only. A candidate on an order (active) or with a deployed placement is never
# deleted: the order's filled count and the placement record must stay. Everything else
# goes in one transaction. Tables that declare ON DELETE CASCADE from the candidate or
# its applications/tasks (applications, application stages, tasks, task remarks, task
# SLA events, leave-cover items) are removed by the database. The tables below have a
# plain foreign key, so they are handled here or the delete would fail:
#   - kept, link cleared: mail messages, WhatsApp messages and conversations, email
#     intake jobs, multi-person document groups (the mail and chats stay in the inbox)
#   - removed: stored documents (all versions), manual upload jobs, notifications and
#     to-do links that pointed at the candidate or its tasks
# The audit trail is kept, and records that the candidate was deleted. The round-robin
# pointer holds a user, not a candidate, so it is untouched.

def _files_still_used(db: Session, keys: set[str]) -> set[str]:
    """Stored objects that another record (mail, WhatsApp, drafts, other documents) still points at."""
    used: set[str] = set()
    for column in (Attachment.object_key, MailAttachment.object_key, WhatsAppMessage.object_key,
                   WhatsAppSendAttempt.object_key, DocumentExtractionJob.object_key, MailDraftAttachment.object_key):
        used.update(db.scalars(select(column).where(column.in_(keys))))
    return used


def _remove_stored_files(db: Session, audit_id: str, keys: set[str]) -> None:
    """Best effort, after the commit. A storage problem must never undo or fail the delete."""
    leftover: list[str] = []
    try:
        shared = _files_still_used(db, keys)
    except Exception:  # noqa: BLE001
        logger.exception("Could not check which stored files of a deleted candidate are still in use")
        db.rollback()
        shared, leftover, keys = set(), sorted(keys), set()
    settings = get_settings()
    for key in sorted(keys - shared):
        try:
            document_storage.delete_bytes(settings, key)
        except Exception:  # noqa: BLE001
            logger.exception("Could not remove stored file %s of a deleted candidate", key)
            leftover.append(key)
    if leftover:
        try:
            entry = db.get(AuditEntry, audit_id)
            entry.meta = {**(entry.meta or {}), "storageLeftover": leftover}
            db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Could not record the leftover stored files of a deleted candidate")
            db.rollback()


@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))) -> dict:
    # Same lock order as a profile correction: assignment cursor, then the candidate.
    lock_assignment_cursor(db)
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")

    placements = db.execute(
        select(Application.id, Application.status, Order.ref)
        .join(Order, Order.id == Application.order_id)
        .where(Application.candidate_id == candidate_id)
    ).all()
    if any(state is ApplicationStatus.active for _, state, _ in placements):
        raise HTTPException(status.HTTP_409_CONFLICT, "This candidate is still on an order. Remove them from the order first.")
    deployed = next((order_ref for _, state, order_ref in placements if state is ApplicationStatus.deployed), None)
    if deployed:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This candidate was deployed through {deployed}, and a deployed placement is kept on record. They cannot be deleted.")

    name, ref = candidate.name, candidate.ref
    application_ids = [row[0] for row in placements]
    attached = db.execute(select(Attachment.id, Attachment.object_key).where(
        or_(
            (Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id),
            (Attachment.entity_type == "application") & Attachment.entity_id.in_(application_ids or [""]),
        ))).all()
    attachment_ids = [row[0] for row in attached]
    jobs = db.execute(select(DocumentExtractionJob.object_key).where(DocumentExtractionJob.candidate_id == candidate_id)).all()
    task_ids = list(db.scalars(select(Task.id).where(Task.candidate_id == candidate_id)))
    stored_keys = {key for _, key in attached if key} | {key for (key,) in jobs if key}

    # Records that keep existing but must stop pointing at this candidate.
    for model in (MailMessage, WhatsAppMessage, WhatsAppConversation, EmailIngestionJob):
        db.execute(update(model).where(model.candidate_id == candidate_id).values(candidate_id=None))
    db.execute(update(DocumentPersonGroup).where(DocumentPersonGroup.candidate_id == candidate_id)
               .values(candidate_id=None, fields={}))
    link = f"/candidates/{candidate_id}"
    db.execute(update(TodoItem).where(TodoItem.link_to == link).values(link_to=None, link_label=None))
    db.execute(delete(Notification).where(Notification.link_to.in_([link, *[f"/tasks?task={task_id}" for task_id in task_ids]])))

    # Documents: first anything that points at them, then the documents themselves.
    if attachment_ids:
        db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.attachment_id.in_(attachment_ids))
                   .values(attachment_id=None))
    db.execute(delete(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id))
    if attachment_ids:
        db.execute(delete(Attachment).where(Attachment.id.in_(attachment_ids)))

    # Applications, stages, tasks, remarks and SLA events follow by ON DELETE CASCADE.
    db.execute(delete(Candidate).where(Candidate.id == candidate_id))
    audit = AuditEntry(actor_id=user.id, action="Deleted candidate", target=f"{name} ({ref})", entity_type="candidate",
                       entity_id=candidate_id, meta={"ref": ref, "documents": len(attachment_ids),
                                                     "applications": len(application_ids), "tasks": len(task_ids)})
    db.add(audit)
    db.flush()
    audit_id = audit.id
    db.commit()

    if stored_keys:
        _remove_stored_files(db, audit_id, stored_keys)
    return {"deleted": {"candidates": 1, "documents": len(attachment_ids), "applications": len(application_ids)}}
