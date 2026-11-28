"""
Read and write endpoints for the collections the front end holds.

One bootstrap call returns everything the store needs, because the app renders a whole
desk at once — twelve stages, task lists, order progress — and a page that fired ten
requests on mount would simply be slower for no benefit at this size. When the candidate
table outgrows a single payload, this is the seam to paginate behind.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from uuid import uuid4
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user, require_role, require_admin_level
from app.core.config import get_settings
from app.services import agency_settings, client_routing
from app.services.desk_snapshot import read_snapshot
from app.services.encoded_response import Encoded
from app.services.intake_attention import unread_counts
from app.services.parallel_reads import parallel_reads
from app.services.response_cache import cached, scope_of
from app.db.models import (
    Application,
    ApplicationStatus,
    Attachment,
    AuditEntry,
    Candidate,
    CandidateInvoice,
    CandidateStatus,
    Client,
    ClientStaff,
    Company,
    Designation,
    DocumentExtractionJob,
    InvoicePayment,
    Notification,
    Order,
    OrderStatus,
    Priority,
    ReferenceAgency,
    Remark,
    Role,
    Task,
    TaskEvent,
    TaskKind,
    TodoItem,
    User,
)
from app.api.schemas import (
    ApplicationOut,
    CandidateOut,
    ClientOut,
    CompanyOut,
    FileRefOut,
    DesignationOut,
    NotificationOut,
    OrderOut,
    RemarkOut,
    StageRecordOut,
    TaskOut,
    TodoOut,
    UserOut,
    camel,
)
from app.core.roles import is_admin_level

router = APIRouter(prefix="/api", tags=["data"])

DOCUMENT_SLOTS = [
    ("resume", "Resume"),
    ("passport", "Passport"),
    ("aadhaar", "Aadhaar copy"),
    ("source_bundle", "Source document evidence"),
    ("photo", "Photo"),
    ("education", "Education certificates"),
    ("experience", "Experience certificates"),
]
# The candidate profile (GET /api/candidates/{id}) also lists the documents the application form collects (the 18 form slots
# of services/application_form.SLOT_SPECS; "education"/"experience" above are the older keys and stay readable). The desk
# snapshot keeps the seven above: it carries every candidate and the additional form slots are stored only.
FORM_DOCUMENT_SLOTS = [
    ("application_form", "Application form"),
    ("undertaking_letter", "Undertaking letter"),
    ("insurance", "Insurance"),
    ("invoice", "Invoice"),
    ("education_certificate", "Education certificate"),
    ("experience_certificate", "Experience certificate"),
    ("signed_offer", "Signed offer"),
    ("medical", "Medical"),
    ("pcc", "Police clearance certificate (PCC)"),
    ("visa", "Visa"),
    ("google_review", "Google review"),
    ("visa_stamping", "Visa stamping"),
    ("emigration", "Emigration"),
    ("air_ticket", "Air ticket"),
    ("other", "Other documents"),
]


def _accepted_document(versions, manual) -> bool:
    return any(not file.get("identity_mismatch") for file in versions or ()) or bool(not versions and manual and manual["uploaded"])


def _candidate_out(c: dict, files: dict[tuple[str, str], list[dict]], model: type[CandidateOut] = CandidateOut,
                   slots: list[tuple[str, str]] = DOCUMENT_SLOTS, manual_records: dict | None = None,
                   splits: dict[str, dict] | None = None) -> CandidateOut:
    """`splits` (attachment id -> {fileName, pages}) is only given by the single-candidate profile read."""
    manual_records = manual_records or {}
    from app.services.application_form import LEGACY_SLOTS
    documents = [
        {
            "key": key,
            "label": label,
            "uploaded": _accepted_document(files.get((c["id"], key)), manual_records.get(LEGACY_SLOTS.get(key, key))),
            "fileUploaded": bool(files.get((c["id"], key))),
            "manualStatus": manual_records.get(LEGACY_SLOTS.get(key, key)),
            "versions": [
                {
                    "id": f["id"],
                    "name": f["file_name"],
                    "sizeKb": f["size_kb"],
                    "version": f["version"],
                    "url": f"/api/files/{f['id']}",
                    "uploadedBy": f["uploaded_by"], "extractionPending": bool(f.get("extraction_pending")),
                    "uploadedAt": datetime.fromisoformat(f["uploaded_at"]),
                    "identityMismatch": bool(f.get("identity_mismatch")),
                    **({"splitFrom": splits[f["id"]]} if splits and f["id"] in splits else {}),
                }
                for f in files.get((c["id"], key), [])
            ],
        }
        for key, label in slots
    ]
    outcome = None
    if c["call_interested"]:
        outcome = {
            "called": bool(c["call_called"]),
            "interested": c["call_interested"],
            "remarks": c["call_remarks"],
            "recordedAt": c["call_recorded_at"],
        }
    return model.model_validate({
        **c, "added_at": c["created_at"], "documents": documents, "call_outcome": outcome,
    })


_DATETIME = TypeAdapter(datetime)


def _candidate_json(c: dict, files: dict[tuple[str, str], list[dict]], manual_records: dict | None = None) -> dict[str, Any]:
    """The desk's wire form of one candidate, identical to `_candidate_out(c, files).model_dump(by_alias=True, mode="json")`.

    A candidate has seven document slots, most of them empty. Validating and dumping seven nested models per
    candidate for 4,000 candidates cost ~30% of the snapshot's assembly time; the slots are plain dictionaries (the
    schema types `versions` as free-form), so they are written directly and only the candidate's own fields go through
    the model. `test_workspace_startup` and `test_bootstrap_performance` compare the whole wire contract.
    """
    manual_records = manual_records or {}
    from app.services.application_form import LEGACY_SLOTS
    documents = []
    for key, label in DOCUMENT_SLOTS:
        versions = files.get((c["id"], key))
        manual = manual_records.get(LEGACY_SLOTS.get(key, key))
        documents.append({"key": key, "label": label, "uploaded": _accepted_document(versions, manual),
                          "fileUploaded": bool(versions), "manualStatus": manual, "versions": [
            {"id": f["id"], "name": f["file_name"], "sizeKb": f["size_kb"], "version": f["version"], "url": f"/api/files/{f['id']}",
             "uploadedBy": f["uploaded_by"], "extractionPending": bool(f.get("extraction_pending")), "identityMismatch": bool(f.get("identity_mismatch")),
             "uploadedAt": _DATETIME.dump_python(datetime.fromisoformat(f["uploaded_at"]), mode="json")}
            for f in versions or ()
        ]})
    outcome = None
    if c["call_interested"]:
        outcome = {"called": bool(c["call_called"]), "interested": c["call_interested"],
                   "remarks": c["call_remarks"], "recordedAt": c["call_recorded_at"]}
    out = CandidateOut.model_validate({**c, "added_at": c["created_at"], "documents": [], "call_outcome": outcome}).model_dump(by_alias=True, mode="json")
    out["documents"] = documents
    return out


def _candidate_summary_json(c: dict) -> dict[str, Any]:
    """One candidate for the slim desk: the candidate's own fields and, instead of the seven document slots with
    every file in them, `documentCounts` ({"passport": 2}: files held per slot, absent when none). The browser rebuilds
    the slot list from it and reads a candidate's files itself when a screen needs them (GET /api/candidates/{id})."""
    outcome = None
    if c["call_interested"]:
        outcome = {"called": bool(c["call_called"]), "interested": c["call_interested"],
                   "remarks": c["call_remarks"], "recordedAt": c["call_recorded_at"]}
    out = CandidateOut.model_validate({**c, "added_at": c["created_at"], "documents": [], "call_outcome": outcome}).model_dump(by_alias=True, mode="json")
    del out["documents"]
    out["documentCounts"] = c.get("document_counts") or {}
    return out


def _row_defaults(schema: type[BaseModel], **overrides: Any) -> dict[str, Any]:
    """The wire value of every optional field of `schema` when the writer leaves it out, by wire name (JSON-ready)."""
    found = {camel(name): field.get_default(call_default_factory=True)
             for name, field in schema.model_fields.items() if not field.is_required()}
    return jsonable_encoder({**found, **overrides})


def _is_default(value: Any, default: Any) -> bool:
    return value is None if default is None else (type(value) is type(default) and value == default)


def _trim(rows: list[dict[str, Any]], defaults: dict[str, Any], keep: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Rows without the keys that hold their default (null, empty list, 0, false...). `defaults` goes out once beside them
    and the browser puts the keys back, so what the screens read is identical; nulls were a third of a task row."""
    return [{key: value for key, value in row.items() if key in keep or key not in defaults or not _is_default(value, defaults[key])} for row in rows]


# Everything the desk snapshot reads. Mail is here only for the unread badge (`inboxUnread`); a finished intake changes the
# snapshot through its `ingestionRevision`, which is a write to the job tables (topic "documents").
DESK_TOPICS = ("users", "clients", "companies", "designations", "orders", "candidates", "documents", "applications",
               "tasks", "notifications", "todos", "settings", "mail")  # "mail": the unread badge (`inboxUnread`)
DESK_TTL_SECONDS = 60


def desk_response(request: Request, db: Session, user: User, compact: bool, headers: dict[str, str] | None = None, slim: bool = False) -> Response:
    """The desk snapshot as a finished HTTP response.

    The snapshot is the slowest thing the app loads (every candidate, ~2 KB each). It is built once per signed-in
    user and compactness, kept already encoded and compressed, and discarded as soon as any collection it contains
    changes (see response_cache). A person never receives another person's entry: the key holds the user id, and the
    sign-in check has already run for this request.
    """
    def build() -> Encoded:
        return Encoded.of(_bootstrap(db, user, compact, slim), json_ready=True, revision=slim)

    encoded = cached(("desk", scope_of(db), user.id, compact, slim), DESK_TOPICS, DESK_TTL_SECONDS, build)
    return encoded.response(request, headers)


def warm_desk(people: int = 3) -> int:
    """Build the (compact) desk snapshot ahead of the first request, for the people who signed in most recently.

    The first load after a restart is the slowest one the app ever does (a cold connection pool plus the snapshot's
    one big statement, JSON encoding and compression). Run once in the background at start-up, it leaves the finished
    response in the memo, so that person's first page after the restart is served from memory. Returns how many were built.
    """
    import os
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 0
    from app.db.models import Session as LoginSession
    from app.db.session import ReadSessionLocal
    built = 0
    with ReadSessionLocal() as db:
        recent = db.execute(select(LoginSession.user_id).where(LoginSession.expires_at > datetime.now(timezone.utc))
                            .group_by(LoginSession.user_id).order_by(func.max(LoginSession.created_at).desc()).limit(people)).scalars().all()
        for user_id in recent:
            user = db.get(User, user_id)
            if user is None or not user.active:
                continue
            cached(("desk", scope_of(db), user.id, True, True), DESK_TOPICS, DESK_TTL_SECONDS,
                   lambda user=user: Encoded.of(_bootstrap(db, user, True, True), json_ready=True, revision=True))
            built += 1
    return built


@router.get("/bootstrap")
def bootstrap(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user), compact: bool = False, slim: bool = False) -> Response:
    """A fresh desk snapshot with one data query after the session check.

    `compact=true` leaves out resume text. `slim=true` (implies compact) is the lean form the current front end asks for:
    rows without their default-valued keys, per-slot file counts instead of every candidate's files, and a `revision`
    that doubles as the ETag (If-None-Match gets an empty 304 while nothing changed). Callers that send only
    `compact=true`, such as a tab still running an earlier version of the app, keep the exact earlier shape."""
    return desk_response(request, db, user, compact or slim, None, slim)


def _unread_badge(session: Session) -> dict:
    try:
        return unread_counts(session)
    except SQLAlchemyError:  # a database without the mail tables yet: the badge is simply empty, the desk still loads
        return {"total": 0, "accounts": {}}


def _bootstrap(db: Session, user: User, compact: bool = False, slim: bool = False) -> dict[str, Any]:
    # The sidebar's unread mail badge rides in the same round trip (a second statement beside the snapshot), so the page
    # does not need a separate /api/inbox/pulse request right after it loads.
    snapshot, unread = parallel_reads(db, lambda session: read_snapshot(session, user.id, compact=compact, slim=slim), _unread_badge)
    from app.services.document_status import statuses_by_candidate
    manual_records = statuses_by_candidate(snapshot.get("manual_documents"))

    files: dict[tuple[str, str], list[dict]] = {}
    for f in snapshot.get("files", ()):
        files.setdefault((f["entity_id"], f["slot"]), []).append(f)

    agreements = {f["entity_id"]: f for f in snapshot["agreements"]}

    def company_out(co: dict) -> dict[str, Any]:
        out = CompanyOut.model_validate(co)
        if (a := agreements.get(co["id"])) is not None:
            out.agreement = FileRefOut(id=a["id"], name=a["file_name"], url=f"/api/files/{a['id']}")
        return out.model_dump(by_alias=True, mode="json")

    stages: dict[str, list[dict]] = {}
    for srow in snapshot["stages"]:
        stages.setdefault(srow["application_id"], []).append(srow)

    apps_out = []
    for a in snapshot["applications"]:
        item = ApplicationOut.model_validate(a)
        item.stages = [StageRecordOut.model_validate(s) for s in stages.get(a["id"], [])]
        apps_out.append(item)

    body = {
        "me": UserOut.model_validate(user).model_dump(by_alias=True, mode="json"),
        "ingestionRevision": snapshot["ingestionRevision"],
        "inboxUnread": unread,
        "agencyTimezone": get_settings().agency_timezone,
        "settings": agency_settings.build_payload(snapshot.get("agency"), snapshot.get("currencies"), snapshot.get("categories")),
        "users": [UserOut.model_validate(u).model_dump(by_alias=True, mode="json") for u in snapshot["users"]],
        "clients": [ClientOut.model_validate(c).model_dump(by_alias=True, mode="json") for c in snapshot["clients"]],
        "companies": [company_out(c) for c in snapshot["companies"]],
        "designations": [DesignationOut.model_validate(d).model_dump(by_alias=True, mode="json") for d in snapshot["designations"]],
        "orders": [OrderOut.model_validate(o).model_dump(by_alias=True, mode="json") for o in snapshot["orders"]],
        "candidates": ([_candidate_summary_json(c) for c in snapshot["candidates"]] if slim
                       else [_candidate_json(c, files, manual_records.get(c["id"])) for c in snapshot["candidates"]]),
        "applications": [a.model_dump(by_alias=True, mode="json") for a in apps_out],
        "tasks": [TaskOut.model_validate(t).model_dump(by_alias=True, mode="json") for t in snapshot["tasks"]],
        "remarks": [RemarkOut.model_validate(r).model_dump(by_alias=True, mode="json") for r in snapshot["remarks"]],
        "todos": [TodoOut.model_validate(t).model_dump(by_alias=True, mode="json") for t in snapshot["todos"]],
        "notifications": [NotificationOut.model_validate(n).model_dump(by_alias=True, mode="json") for n in snapshot["notifications"]],
    }
    if slim:
        _shrink(body)
    return body


# What the slim desk leaves out of each row because it holds its default; `rowDefaults` in the same payload says what.
# `resumeText` is always "" in the compact desk, `documentCounts` is the compact form of `documents`.
_COMPACT_ROWS: dict[str, tuple[type[BaseModel], dict[str, Any]]] = {
    "candidates": (CandidateOut, {"resumeText": "", "documentCounts": {}}),
    "applications": (ApplicationOut, {}),
    "tasks": (TaskOut, {}),
    "orders": (OrderOut, {}),
    "companies": (CompanyOut, {}),
    "clients": (ClientOut, {}),
}


def _shrink(body: dict[str, Any]) -> None:
    """Slim desk only: trim default-valued keys from the big row collections and say, once, how to put them back
    (`rowDefaults`, and `documentLabels` for the slots in `documentCounts`)."""
    defaults: dict[str, dict[str, Any]] = {"stages": _row_defaults(StageRecordOut)}  # the stage records inside each application
    for application in body["applications"]:
        application["stages"] = _trim(application["stages"], defaults["stages"])
    for name, (schema, overrides) in _COMPACT_ROWS.items():
        found = _row_defaults(schema, **overrides)
        found.pop("documents", None)
        defaults[name] = found
        body[name] = _trim(body[name], found)
    body["rowDefaults"] = defaults
    body["documentLabels"] = dict(DOCUMENT_SLOTS)


# ---------------------------------------------------------------- master data

_WEBSITE_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9\u00a1-\uffff]([a-z0-9\u00a1-\uffff-]{0,61}[a-z0-9\u00a1-\uffff])?\.)+[a-z\u00a1-\uffff][a-z0-9\u00a1-\uffff-]{0,61}[a-z0-9\u00a1-\uffff]$")


def normalize_website(raw: str) -> str | None:
    """A company website as one https:// (or http://) address. Blank gives None, a bare domain gets https://, and
    spaces, user:password@ parts, script schemes (javascript:, data:, ...) and anything without a real host are refused."""
    text_ = raw.strip()
    if not text_:
        return None
    if re.search(r"\s", text_):
        raise ValueError("website: enter a web address without spaces, such as www.example.com")
    scheme = re.match(r"^([a-z][a-z0-9+.-]*):", text_, re.I)
    if scheme:
        if text_[scheme.end():scheme.end() + 2] == "//":
            if scheme.group(1).lower() not in ("http", "https"):
                raise ValueError("website: only http:// or https:// addresses are accepted")
        elif not re.match(r"^[^/:?#]+:\d+(/|$|\?|#)", text_):  # `example.com:8080` is a host with a port; the rest is javascript:, mailto:, ...
            raise ValueError("website: only http:// or https:// addresses are accepted")
        else:
            text_ = f"https://{text_}"
    else:
        text_ = f"https://{text_}"
    parts = urlsplit(text_)
    try:
        host, _port = (parts.hostname or ""), parts.port
    except ValueError:
        raise ValueError("website: enter a valid web address, such as www.example.com") from None
    if "@" in parts.netloc or not _WEBSITE_HOST.match(host):
        raise ValueError("website: enter a valid web address, such as www.example.com")
    path = "" if parts.path == "/" and not parts.query and not parts.fragment else parts.path
    result = f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{'?' + parts.query if parts.query else ''}{'#' + parts.fragment if parts.fragment else ''}"
    if len(result) > 255:
        raise ValueError("website: the address is too long (255 characters at most)")
    return result


class MasterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_optional_fields(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if isinstance(value.get("website"), str):
                value["website"] = normalize_website(value["website"])
            for key in ("email", "contactEmail", "designation", "interviewDate", "deadline", "state", "agencyName"):
                if isinstance(value.get(key), str):
                    value[key] = value[key].strip() or None
            for key in ("email", "contactEmail"):
                if isinstance(value.get(key), str) and value[key]:
                    value[key] = value[key].lower()
                    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value[key]):
                        raise ValueError(f"{key}: enter a valid email address")
        return value

    @field_validator("requiredSkills", "benefits", check_fields=False)
    @classmethod
    def distinct_values(cls, values):
        result = []
        seen = set()
        for value in values:
            value = value.strip()
            if len(value) > 120:
                raise ValueError("Each skill or benefit must be 120 characters or fewer")
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                result.append(value)
        return result


MAX_STAFF_CONTACTS = 50


class StaffContactIn(BaseModel):
    """One person on a client's or reference agency's own team. Blank optional boxes are stored as null."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    designation: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)

    @field_validator("designation", "phone", "email", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
        return value or None

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, value):
        return value.lower() if value else value


class ClientCompanyIn(MasterInput):
    """The employer saved together with a new client contact."""

    name: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=2000)
    state: str | None = Field(default=None, max_length=100)
    # The company's own point of contact. Left blank, the client contact's details are used.
    contactPerson: str | None = Field(default=None, max_length=160)
    contactEmail: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)


class ClientIn(MasterInput):
    # Only honoured on create: the contact's details become the employer's contact
    # details, so one form captures the whole PRD client record in one transaction.
    company: ClientCompanyIn | None = None
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    # The contact person's job title.
    designation: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=20000)
    status: Literal["active", "inactive"] = "active"
    # The client's own team. On PUT, a list that is sent replaces the saved one; one that is left out keeps it.
    staffContacts: list[StaffContactIn] = Field(default_factory=list, max_length=MAX_STAFF_CONTACTS)
    # CRM users (active, role staff or MD) who look after this client. Same PUT rule as staffContacts.
    staffIds: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("staffIds")
    @classmethod
    def distinct_staff_ids(cls, values):
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 36 for value in cleaned):
            raise ValueError("staffIds: each entry must be a user id")
        return list(dict.fromkeys(cleaned))


class CompanyIn(MasterInput):
    clientId: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=120)
    contactPerson: str | None = Field(default=None, max_length=160)
    contactEmail: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=2000)
    state: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=20000)
    status: Literal["active", "inactive"] = "active"


class DesignationIn(MasterInput):
    title: str = Field(min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=20000)
    requiredSkills: list[str] = Field(default_factory=list, max_length=100)
    minExperience: int = Field(default=0, ge=0, le=60)


class OrderIn(MasterInput):
    companyId: str = Field(min_length=1, max_length=36)
    designationId: str = Field(min_length=1, max_length=36)
    openings: int = Field(default=1, ge=1, le=100000)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    salary: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    # Left out, the agency's default order currency applies (see _order_requirements).
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    benefits: list[str] = Field(default_factory=list, max_length=100)
    contractPeriod: str | None = Field(default=None, max_length=60)
    ageRequirement: str | None = Field(default=None, max_length=120)
    genderRequirement: str | None = Field(default=None, max_length=120)
    educationRequirement: str | None = Field(default=None, max_length=2000)
    interviewDate: date | None = None
    deadline: date | None = None
    status: OrderStatus = OrderStatus.open

    @field_validator("ageRequirement", "genderRequirement", "educationRequirement")
    @classmethod
    def optional_requirements(cls, value):
        return value or None

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.interviewDate and self.deadline and self.interviewDate > self.deadline:
            raise ValueError("The interview date must be on or before the order deadline")
        return self


def _master_row(db: Session, model, identifier: str):
    row = db.scalar(select(model).where(model.id == identifier).with_for_update())
    if row is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return row


def _master_values(body: MasterInput):
    aliases = {"clientId": "client_id", "contactPerson": "contact_person", "contactEmail": "contact_email",
               "requiredSkills": "required_skills", "minExperience": "min_experience",
               "companyId": "company_id", "designationId": "designation_id",
               "contractPeriod": "contract_period", "interviewDate": "interview_date",
               "ageRequirement": "age_requirement", "genderRequirement": "gender_requirement",
               "educationRequirement": "education_requirement",
               "staffContacts": "staff_contacts", "agencyName": "agency_name",
               "preferredCountries": "preferred_countries", "activeAgency": "active_agency"}
    return {aliases.get(key, key): value for key, value in body.model_dump(exclude={"company", "staffIds"}).items()}


def _master_json(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return float(value)
    return value


def _master_save(db, user, row, body, kind, schema, *, new=False, commit=True, extra_changes=None):
    changes = dict(extra_changes or {})
    values = _master_values(body)
    if not new and "staffContacts" not in body.model_fields_set:
        values.pop("staff_contacts", None)  # a PUT that does not mention the team keeps it
    for wire, column in (("specialisations", "specialisations"), ("preferredCountries", "preferred_countries"),
                         ("ageRequirement", "age_requirement"), ("genderRequirement", "gender_requirement"),
                         ("educationRequirement", "education_requirement")):
        if not new and wire not in body.model_fields_set:
            values.pop(column, None)  # nor does it clear a selection it never mentioned
    for key, value in values.items():
        previous = getattr(row, key)
        if previous != value:
            changes[key] = {"from": _master_json(previous), "to": _master_json(value)}
            setattr(row, key, value)
    try:
        db.add(row)
        db.flush()
        if new or changes:
            db.add(AuditEntry(actor_id=user.id, action=f"{'Created' if new else 'Updated'} {kind}",
                              target=getattr(row, "name", getattr(row, "title", getattr(row, "ref", kind))),
                              entity_type=kind, entity_id=row.id, meta={"changes": changes}))
        if commit:
            db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A related record changed while saving. Refresh and try again.")
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "The record could not be saved. Please try again.")
    return schema.model_validate(row).model_dump(by_alias=True, mode="json")


ALLOCATABLE_ROLES = (Role.staff, Role.manager)


def _checked_staff_ids(db: Session, ids: list[str]) -> list[str]:
    """The CRM users a client may be allocated to: active accounts whose role is staff or MD. One statement."""
    if not ids:
        return []
    found = {person.id: person for person in db.execute(
        select(User.id, User.name, User.active, User.role).where(User.id.in_(ids))).all()}
    problems = []
    for identifier in ids:
        person = found.get(identifier)
        if person is None:
            problems.append(f"{identifier} is not a CRM user.")
        elif not person.active:
            problems.append(f"{person.name} is deactivated, so they cannot be allocated.")
        elif person.role not in ALLOCATABLE_ROLES:
            problems.append(f"{person.name} is an {person.role.value}; only recruiters or MDs can be allocated.")
    if problems:
        raise HTTPException(422, {"message": "Choose active recruiters or MDs to allocate.",
                                  "errors": [{"field": "staffIds", "message": message} for message in problems]})
    return list(ids)


def allocated_staff_ids(db: Session, client_id: str) -> list[str]:
    """The client's allocated users who can still work its candidates, ordered by name (the desk snapshot's order)."""
    return list(db.scalars(
        select(ClientStaff.user_id).join(User, User.id == ClientStaff.user_id)
        .where(ClientStaff.client_id == client_id, User.active.is_(True), User.role.in_(ALLOCATABLE_ROLES))
        .order_by(User.name, User.id)))


def _replace_client_staff(db: Session, client_id: str, wanted: list[str]) -> dict | None:
    """Make the saved allocation exactly `wanted`; returns the audit change, or None when nothing moved."""
    saved = list(db.scalars(select(ClientStaff.user_id).where(ClientStaff.client_id == client_id).order_by(ClientStaff.user_id)))
    if set(saved) == set(wanted):
        return None
    gone = [identifier for identifier in saved if identifier not in wanted]
    if gone:
        db.execute(delete(ClientStaff).where(ClientStaff.client_id == client_id, ClientStaff.user_id.in_(gone)))
    db.add_all([ClientStaff(client_id=client_id, user_id=identifier) for identifier in wanted if identifier not in saved])
    return {"from": saved, "to": sorted(wanted)}


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A related record changed while saving. Refresh and try again.")
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "The record could not be saved. Please try again.")


def _squash(value) -> str:
    """Lower-cased text with extra spaces removed (the same reading as the SQL below)."""
    return " ".join((value or "").split()).casefold()


def _duplicate_client(db, body):
    """The existing client this one would repeat, or None: the same name AND the same country (both read ignoring case and extra spaces; no
    country equals no country), or the same phone number, or the same e-mail address. Two real clients that share a name tell themselves apart
    by country, phone or e-mail."""
    name, country = _squash(body.name), _squash(body.country)
    digits = re.sub(r"\D", "", body.phone or "")
    email = (body.email or "").strip().casefold()
    squash = lambda column: func.lower(func.regexp_replace(func.btrim(func.coalesce(column, "")), r"\s+", " ", "g"))  # noqa: E731
    tests = []
    if name:
        tests.append(and_(squash(Client.name) == name, squash(Client.country) == country))
    if len(digits) >= 6:
        tests.append(func.regexp_replace(func.coalesce(Client.phone, ""), r"\D", "", "g") == digits)
    if email:
        tests.append(func.lower(func.btrim(func.coalesce(Client.email, ""))) == email)
    if not tests:
        return None
    # Two creates at the same moment (a double click, two people) take turns on the same keys, so the second one sees the first one's row.
    keys = sorted({f"client:{name}|{country}"} | ({f"client-phone:{digits}"} if len(digits) >= 6 else set()) | ({f"client-mail:{email}"} if email else set()))
    for key in keys:
        db.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))
    return db.scalars(select(Client).where(or_(*tests)).order_by(Client.created_at, Client.id).limit(1)).first()


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(body: ClientIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    # One client per name and country (or phone, or e-mail): a double click, or two people adding the same client at the same moment, must not
    # leave two rows that split its jobs and candidates between them.
    existing = _duplicate_client(db, body)
    if existing is not None:
        where = f" in {existing.country}" if existing.country else ""
        raise HTTPException(409, f'A client "{existing.name}"{where} already exists. Open it from the Clients list, or give this one a different country, '
                                 "phone number or e-mail address.")
    staff_ids = _checked_staff_ids(db, body.staffIds)
    # Client contact, first employer and staff allocation commit together, so a failure never leaves a
    # contact without the company the form described.
    client = _master_save(db, user, Client(), body, "client", ClientOut, new=True, commit=False,
                          extra_changes={"staffIds": {"from": [], "to": sorted(staff_ids)}} if staff_ids else None)
    if staff_ids:
        db.add_all([ClientStaff(client_id=client["id"], user_id=identifier) for identifier in staff_ids])
    if body.company is None:
        _commit_or_conflict(db)
        return {**client, "staffIds": allocated_staff_ids(db, client["id"])}
    details = body.company.model_dump()
    for key, fallback in (("contactPerson", body.name), ("contactEmail", body.email), ("phone", body.phone), ("whatsapp", body.whatsapp)):
        details[key] = details[key] or fallback
    employer = CompanyIn(clientId=client["id"], status=body.status, notes=body.notes, **details)
    try:
        company = _master_save(db, user, Company(), employer, "company", CompanyOut, new=True, commit=False)
        db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "The record could not be saved. Please try again.")
    return {**client, "staffIds": allocated_staff_ids(db, client["id"]), "company": company}


@router.put("/clients/{client_id}")
def update_client(client_id: str, body: ClientIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.company is not None:
        raise HTTPException(422, "Employer details are saved on the company, not here")
    row = _master_row(db, Client, client_id)
    wanted = _checked_staff_ids(db, body.staffIds) if "staffIds" in body.model_fields_set else None
    extra = None
    if wanted is not None:
        moved = _replace_client_staff(db, row.id, wanted)
        extra = {"staffIds": moved} if moved else None
    out = _master_save(db, user, row, body, "client", ClientOut, commit=False, extra_changes=extra)
    _commit_or_conflict(db)
    return {**out, "staffIds": allocated_staff_ids(db, client_id)}


def _company_parent(db, body, row=None):
    client = db.get(Client, body.clientId)
    if client is None: raise HTTPException(404, "Client not found")
    if body.status == "active" and client.status != "active" and (
        row is None or row.client_id != body.clientId or row.status != "active"
    ):
        raise HTTPException(409, "Activate the client before adding or activating their company")


@router.post("/companies", status_code=status.HTTP_201_CREATED)
def create_company(body: CompanyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _company_parent(db, body)
    return _master_save(db, user, Company(), body, "company", CompanyOut, new=True)


@router.put("/companies/{company_id}")
def update_company(company_id: str, body: CompanyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = _master_row(db, Company, company_id)
    _company_parent(db, body, row)
    return _master_save(db, user, row, body, "company", CompanyOut)


@router.post("/designations", status_code=status.HTTP_201_CREATED)
def create_designation(body: DesignationIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    body.category = agency_settings.canonical_category(db, body.category)
    return _master_save(db, user, Designation(), body, "designation", DesignationOut, new=True)


@router.put("/designations/{designation_id}")
def update_designation(designation_id: str, body: DesignationIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    body.category = agency_settings.canonical_category(db, body.category)
    return _master_save(db, user, _master_row(db, Designation, designation_id), body, "designation", DesignationOut)


@router.delete("/designations/{designation_id}", status_code=204)
def delete_designation(designation_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin_level())):
    row = _master_row(db, Designation, designation_id)
    if db.scalar(select(Order.id).where(Order.designation_id == row.id).limit(1)):
        raise HTTPException(409, "This job title is used by orders, so it cannot be deleted")
    db.add(AuditEntry(actor_id=user.id, action="Deleted designation", target=row.title, entity_type="designation", entity_id=row.id))
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This job title is now used by an order. Refresh the page and try again.")


def _order_currency(db, body, row=None):
    """A new order's currency must be an active one; an order may keep the currency it already has."""
    code = body.currency or (row.currency if row is not None else agency_settings.default_order_currency(db))
    if code != (row.currency if row is not None else None) and code not in agency_settings.active_currency_codes(db):
        raise HTTPException(422, {"message": "Choose an available currency.",
                                  "errors": [{"field": "currency", "message": f"{code} is not an available currency. Choose one from the list."}]})
    body.currency = code


def _order_requirements(db, body, row=None):
    _order_currency(db, body, row)
    company = db.get(Company, body.companyId)
    if company is None: raise HTTPException(404, "Company not found")
    if db.get(Designation, body.designationId) is None: raise HTTPException(404, "Designation not found")
    client = db.get(Client, company.client_id)
    if body.status == OrderStatus.open and (row is None or row.status != OrderStatus.open or row.company_id != body.companyId):
        if company.status != "active" or client is None or client.status != "active":
            raise HTTPException(409, "Activate the company and its client before opening an order")
    counts = dict(db.execute(select(Application.status, func.count()).where(
        Application.order_id == row.id).group_by(Application.status)).all()) if row is not None else {}
    if counts and (row.company_id != body.companyId or row.designation_id != body.designationId):
        raise HTTPException(409, "An order that already has candidates must keep its company and job title")
    deployed = counts.get(ApplicationStatus.deployed, 0)
    if body.openings < deployed:
        raise HTTPException(409, f"This order already has {deployed} deployments; openings cannot be lower")
    if body.status == OrderStatus.filled and deployed < body.openings:
        raise HTTPException(409, "An order can be marked Filled only when every opening has a deployed candidate")
    if body.status == OrderStatus.open and deployed >= body.openings:
        raise HTTPException(409, "All openings are filled. Add more openings before you reopen this order")
    if body.status == OrderStatus.closed and counts.get(ApplicationStatus.active, 0):
        raise HTTPException(409, "Unmap or finish the active candidates before closing this order. Use On hold to stop new matching for now")


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _order_requirements(db, body)
    number = db.scalar(select(func.nextval("crm_order_reference_seq")))
    row = Order(ref=f"ORD-{datetime.now(timezone.utc).year}-{number:05d}")
    saved = _master_save(db, user, row, body, "order", OrderOut, new=True, commit=False)
    # The staff allocated to the client are told in the same transaction, so a notice never exists for a job that failed to save.
    client_routing.notify_new_job(db, row, actor_id=user.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A related record changed while saving. Refresh and try again.")
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "The record could not be saved. Please try again.")
    return saved


@router.put("/orders/{order_id}")
def update_order(order_id: str, body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    # Application mutations also hold this order lock, so counts remain valid until commit.
    row = _master_row(db, Order, order_id)
    _order_requirements(db, body, row)
    return _master_save(db, user, row, body, "order", OrderOut)


@router.get("/master-data/{kind}/{record_id}/audit")
def master_audit(kind: Literal["client", "company", "designation", "order", "reference_agency"], record_id: str,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    model = {"client": Client, "company": Company, "designation": Designation, "order": Order, "reference_agency": ReferenceAgency}[kind]
    # The record check and the history are independent: one round trip for both.
    found, rows = parallel_reads(
        db, lambda session: session.scalar(select(model.id).where(model.id == record_id)) is not None,
        lambda session: session.scalars(select(AuditEntry).where(AuditEntry.entity_type == kind, AuditEntry.entity_id == record_id)
                                        .order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(100)).all())
    if not found: raise HTTPException(404, "Record not found")
    return [{"id": row.id, "action": row.action, "actorId": row.actor_id, "at": row.at,
             "target": row.target, "meta": row.meta} for row in rows]


# ----------------------------------------------------------------- deletion
#
# A client owns companies, a company owns orders, and an order holds the candidates
# mapped to it. Deleting goes down that chain — but never through a candidate who is
# has application history or retained documents. Inactivate a client/company or close
# an order instead: evidence must remain attached to its original record.

def _company_orders_for_update(db: Session, company_ids: list[str]) -> list[str]:
    # Read membership and acquire locks together. If an order moves to another
    # company while this query waits, PostgreSQL rechecks the company predicate;
    # an earlier unlocked list of IDs would incorrectly delete that moved order.
    if not company_ids:
        return []
    return list(db.scalars(select(Order.id).where(Order.company_id.in_(company_ids))
                           .order_by(Order.id).with_for_update()))


def _refuse_if_placed(db: Session, order_ids: list[str]) -> None:
    if not order_ids:
        return
    placed = db.execute(
        select(Candidate.name, Order.ref, Application.status)
        .join(Application, Application.candidate_id == Candidate.id)
        .join(Order, Order.id == Application.order_id)
        .where(
            Application.order_id.in_(order_ids),
            Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed]),
        )
        .order_by(Application.status, Order.ref, Candidate.name)
    ).all()
    if not placed:
        history = db.scalar(select(Application.id).where(Application.order_id.in_(order_ids)).limit(1))
        if history:
            raise HTTPException(409, "This cannot be deleted because candidate history is kept for it. Close the order, or mark the company or client Inactive instead.")
        return

    name, ref, state = placed[0]
    if state is ApplicationStatus.active:
        reason = f"{name} is active on {ref}. Unmap them first."
    else:
        reason = f"{name} was deployed through {ref}, and a deployed placement is kept on record."
    more = f" ({len(placed) - 1} more candidate{'s' if len(placed) > 2 else ''} too.)" if len(placed) > 1 else ""
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "message": f"Can't delete: {reason}{more}",
            "blockers": [
                {"candidate": n, "order": r, "status": s.value} for n, r, s in placed
            ],
        },
    )


def _refuse_if_documents(db: Session, entity_type: str, ids: list[str]) -> None:
    if ids and db.scalar(select(Attachment.id).where(
        Attachment.entity_type == entity_type, Attachment.entity_id.in_(ids),
    ).limit(1)):
        raise HTTPException(409, f"Can't delete: retained {entity_type} documents belong to this record. Mark the {entity_type} inactive instead.")


def _delete_orders(db: Session, order_ids: list[str]) -> None:
    if order_ids:
        db.execute(delete(Order).where(Order.id.in_(order_ids)))


ADMIN_LEVEL = require_admin_level()


@router.delete("/orders/{order_id}")
def delete_order(order_id: str, background: BackgroundTasks,
                 withdraw_applications: bool = Query(False, alias="withdrawApplications"),
                 db: Session = Depends(get_db), user: User = Depends(require_admin_level())) -> dict:
    """
    Admin only. A job (order) with no candidates on it is simply deleted. A job with candidates is deleted only when the
    admin confirms with `?withdrawApplications=true`: its candidate applications (stages, tasks and stage documents) are
    removed in the same transaction and anyone still active on it goes back to the Data Bank. A deployed placement, or any
    invoice or payment history, is kept on record, so such a job is refused and can only be closed.
    Locks: the candidates on the job first, then the job (the order used when mapping or deleting a candidate).
    """
    placed = db.execute(
        select(Application.id, Application.status, Candidate.id, Candidate.name, Candidate.ref, Candidate.needs_review)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .where(Application.order_id == order_id)
        .order_by(Candidate.id, Application.id)
        .with_for_update(of=Candidate)
    ).all()
    order = _master_row(db, Order, order_id)
    ref = order.ref
    app_ids = [row[0] for row in placed]
    who = {row[0]: row[3] for row in placed}
    if app_ids:
        deployed = next((row[3] for row in placed if row[1] is ApplicationStatus.deployed), None)
        if deployed:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Can't delete {ref}: {deployed} was deployed through this job, and a deployed placement is kept on record. Close the job instead.")
        # Read after the candidate locks above: invoice and receipt writes take the same candidate lock first.
        money_app = db.scalar(select(CandidateInvoice.application_id).where(
            CandidateInvoice.application_id.in_(app_ids),
            or_(CandidateInvoice.number.is_not(None), exists().where(InvoicePayment.invoice_id == CandidateInvoice.id)),
        ).limit(1)) or db.scalar(select(InvoicePayment.source_application_id).where(
            InvoicePayment.source_application_id.in_(app_ids)).limit(1))
        if money_app:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Can't delete {ref}: {who.get(money_app, 'A candidate')} has an invoice or a recorded payment on this job. Financial records are kept, so close the job instead.")
        candidates = len({row[2] for row in placed})
        if not withdraw_applications:
            active = sum(1 for row in placed if row[1] is ApplicationStatus.active)
            detail = f"{candidates} candidate{'s are' if candidates != 1 else ' is'} on {ref}"
            detail += f" ({active} active)" if active else " (all closed)"
            raise HTTPException(status.HTTP_409_CONFLICT, {
                "message": f"{detail}. Deleting the job also removes their applications from it, stage history and stage documents included. Confirm to continue.",
                "candidates": candidates, "active": active, "canWithdraw": True,
                "blockers": [{"candidate": row[3], "order": ref, "status": row[1].value} for row in placed],
            })

    # Applications, stages, tasks, remarks, SLA events and daily-work items follow by ON DELETE CASCADE.
    # Rows with a plain reference are handled here: stage documents, unissued invoices, links in notifications and to-dos.
    keys: set[str] = set()
    documents = 0
    if app_ids:
        docs = db.execute(select(Attachment.id, Attachment.object_key).where(
            Attachment.entity_type == "application", Attachment.entity_id.in_(app_ids))).all()
        documents = len(docs)
        keys = {key for _, key in docs if key}
        if docs:
            doc_ids = [doc_id for doc_id, _ in docs]
            db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.attachment_id.in_(doc_ids))
                       .values(attachment_id=None))
            db.execute(delete(Attachment).where(Attachment.id.in_(doc_ids)))
        db.execute(delete(Notification).where(Notification.link_to.in_(
            select(func.concat("/tasks?task=", Task.id)).where(Task.application_id.in_(app_ids)))))
        db.execute(delete(CandidateInvoice).where(
            CandidateInvoice.application_id.in_(app_ids), CandidateInvoice.number.is_(None),
            ~exists().where(InvoicePayment.invoice_id == CandidateInvoice.id)))
        # A candidate who was still active on the job is back in the Data Bank, as after "remove from job".
        leaving = {row[2]: row for row in placed if row[1] is ApplicationStatus.active}
        if leaving:
            for candidate in db.scalars(select(Candidate).where(Candidate.id.in_(list(leaving)))):
                if candidate.status == CandidateStatus.in_process:
                    candidate.status = CandidateStatus.needs_review if candidate.needs_review else CandidateStatus.new
            for candidate_id, row in leaving.items():
                db.add(AuditEntry(actor_id=user.id, action="Removed from a deleted job", target=f"{row[3]} ({ref})",
                                  entity_type="candidate", entity_id=candidate_id, meta={"order": ref, "ref": row[4]}))
        db.execute(delete(Application).where(Application.order_id == order.id))
    link = f"/orders/{order.id}"
    db.execute(update(TodoItem).where(TodoItem.link_to == link).values(link_to=None, link_label=None))
    db.execute(delete(Notification).where(Notification.link_to == link))
    _delete_orders(db, [order.id])
    audit = AuditEntry(actor_id=user.id, action="Deleted order", target=ref, entity_type="order", entity_id=order_id,
                       meta={"applications": len(app_ids), "candidates": sorted({row[4] for row in placed}),
                             "documents": documents})
    db.add(audit)
    db.flush()
    audit_id = audit.id
    db.commit()
    if keys:
        # After the commit and the response: object storage can be slow and its failure must not matter here.
        from app.api.routers.candidates import _remove_stored_files
        background.add_task(_remove_stored_files, db.get_bind(), audit_id, keys)
    return {"deleted": {"orders": 1, "applications": len(app_ids), "documents": documents}}


@router.delete("/companies/{company_id}")
def delete_company(company_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_LEVEL)) -> dict:
    # Uploads lock this same parent; document checks cannot race a committed upload.
    company = _master_row(db, Company, company_id)

    order_ids = _company_orders_for_update(db, [company.id])
    _refuse_if_placed(db, order_ids)
    _refuse_if_documents(db, "company", [company.id])
    name = company.name
    _delete_orders(db, order_ids)
    db.execute(delete(Company).where(Company.id == company_id))
    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Deleted company",
            target=name,
            entity_type="company",
            entity_id=company_id,
            meta={"orders": len(order_ids)},
        )
    )
    db.commit()
    return {"deleted": {"companies": 1, "orders": len(order_ids)}}


@router.delete("/clients/{client_id}")
def delete_client(client_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_LEVEL)) -> dict:
    client = _master_row(db, Client, client_id)

    company_ids = list(db.scalars(select(Company.id).where(Company.client_id == client.id).order_by(Company.id).with_for_update()))
    order_ids = _company_orders_for_update(db, company_ids)
    _refuse_if_placed(db, order_ids)
    _refuse_if_documents(db, "client", [client.id])
    _refuse_if_documents(db, "company", company_ids)
    name = client.name
    _delete_orders(db, order_ids)
    db.execute(delete(Company).where(Company.client_id == client_id))
    db.execute(delete(Client).where(Client.id == client_id))
    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Deleted client",
            target=name,
            entity_type="client",
            entity_id=client_id,
            meta={"companies": len(company_ids), "orders": len(order_ids)},
        )
    )
    db.commit()
    return {"deleted": {"clients": 1, "companies": len(company_ids), "orders": len(order_ids)}}


# ------------------------------------------------------------------ remarks

class RemarkIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=10000)
    # 'remark' (default, what older clients send) is the Remarks select's value; 'note' is typed in the NOTES column and is never the current remark.
    kind: Literal["remark", "note"] = "remark"


@router.post("/tasks/{task_id}/remarks", status_code=status.HTTP_201_CREATED)
def add_remark(task_id: str, body: RemarkIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    # ONE statement: lock the task, check who may write on it and that it is still open, add the remark and its audit entry, and hand the
    # remark back. (It used to be a locking read and two inserts, one round trip each, before the commit.)
    row = db.execute(text(
        "WITH t AS (SELECT id, owner_id, title, completed_at, cancelled_at FROM workflow_tasks WHERE id = :task FOR UPDATE), "
        "ok AS (SELECT id, title FROM t WHERE completed_at IS NULL AND cancelled_at IS NULL AND (CAST(:anyone AS boolean) OR owner_id = :me)), "
        "r AS (INSERT INTO workflow_task_remarks (id, task_id, body, author_id, kind) SELECT :remark_id, id, :body, :me, :kind FROM ok "
        "RETURNING id, task_id, body, author_id, created_at, kind), "
        "a AS (INSERT INTO system_audit_log (id, actor_id, action, target, entity_type, entity_id) "
        "SELECT :audit_id, :me, :action, title, 'task', id FROM ok) "
        "SELECT t.owner_id, t.completed_at, t.cancelled_at, r.id, r.task_id, r.body, r.author_id, r.created_at, r.kind FROM t LEFT JOIN r ON true"),
        {"task": task_id, "anyone": user.role != Role.staff, "me": user.id, "remark_id": str(uuid4()), "audit_id": str(uuid4()),
         "body": body.body.strip(), "kind": body.kind, "action": "Added task note" if body.kind == "note" else "Added task remark"}).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if row.id is None:
        if user.role == Role.staff and row.owner_id != user.id:
            raise HTTPException(403, "Only the assigned recruiter or a supervisor can update this task")
        raise HTTPException(409, "This task is closed, so its notes can no longer be changed")
    db.commit()
    return RemarkOut(id=row.id, task_id=row.task_id, body=row.body, author_id=row.author_id,
                     created_at=row.created_at, kind=row.kind).model_dump(by_alias=True, mode="json")


@router.delete("/remarks/{remark_id}")
def delete_remark(remark_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Delete one note (the NOTES column of the Tasks page). A SOFT delete: the row stays with `deleted_at` / `deleted_by`, every reader ignores
    it, and the audit entry "Deleted a note" keeps its text, who deleted it and when. Allowed for the note's author, the candidate's recruiter
    (or the task's owner) and any Admin / MD. ONE statement; the change feed announces the update like an added remark."""
    row = db.execute(text(
        "WITH r AS (SELECT m.id, m.task_id, m.body, m.author_id, m.deleted_at, t.title, t.owner_id AS task_owner, c.owner_id AS candidate_owner "
        "FROM workflow_task_remarks m JOIN workflow_tasks t ON t.id = m.task_id LEFT JOIN crm_candidates c ON c.id = t.candidate_id "
        "WHERE m.id = :remark), "
        "ok AS (SELECT id FROM r WHERE deleted_at IS NULL AND (CAST(:anyone AS boolean) OR author_id = :me OR candidate_owner = :me OR task_owner = :me)), "
        "u AS (UPDATE workflow_task_remarks SET deleted_at = now(), deleted_by = :me WHERE id IN (SELECT id FROM ok) AND deleted_at IS NULL "
        "RETURNING id, task_id, body, author_id, deleted_at), "
        "a AS (INSERT INTO system_audit_log (id, actor_id, action, target, entity_type, entity_id, meta) "
        "SELECT :audit_id, :me, 'Deleted a note', r.title, 'task', u.task_id, "
        "jsonb_build_object('remarkId', u.id, 'text', u.body, 'writtenBy', u.author_id, 'deletedAt', u.deleted_at) FROM u JOIN r ON r.id = u.id) "
        "SELECT r.deleted_at AS was_deleted, (SELECT count(*) FROM u) AS removed, "
        "(CAST(:anyone AS boolean) OR r.author_id = :me OR r.candidate_owner = :me OR r.task_owner = :me) AS allowed FROM r"),
        {"remark": remark_id, "anyone": is_admin_level(user), "me": user.id, "audit_id": str(uuid4())}).one_or_none()
    if row is None or row.was_deleted is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    if not row.removed:
        if not row.allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who wrote this note, the candidate's recruiter, an MD or an admin can delete it")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    db.commit()
    return {"deleted": remark_id}


@router.post("/tasks/{task_id}/remarks/clear")
def clear_task_remark(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """"Select remark" in the Remarks dropdown: the candidate's Remarks cell reads empty again (and so does the Excel's Remarks column).

    Only remarks (kind 'remark') are cleared; typed notes (kind 'note') stay. Nothing is deleted: every not-yet-cleared remark on the candidate's tasks gets `cleared_at`, so they stay in the history, the day's
    notes and the profile, and a remark written afterwards shows as before. An audit entry "Cleared task remark" records who and how many.
    Same permission as adding a remark: the task's recruiter, an MD or an admin. ONE statement."""
    row = db.execute(text(
        "WITH t AS (SELECT id, owner_id, title, candidate_id FROM workflow_tasks WHERE id = :task), "
        "ok AS (SELECT id, title, candidate_id FROM t WHERE CAST(:anyone AS boolean) OR owner_id = :me), "
        "u AS (UPDATE workflow_task_remarks SET cleared_at = now() WHERE deleted_at IS NULL AND cleared_at IS NULL AND kind = 'remark' AND task_id IN "
        "(SELECT x.id FROM workflow_tasks x JOIN ok ON x.id = ok.id OR (ok.candidate_id IS NOT NULL AND x.candidate_id = ok.candidate_id)) RETURNING id), "
        "a AS (INSERT INTO system_audit_log (id, actor_id, action, target, entity_type, entity_id, meta) "
        "SELECT :audit_id, :me, 'Cleared task remark', ok.title, 'task', ok.id, jsonb_build_object('remarks', (SELECT count(*) FROM u)) "
        "FROM ok WHERE EXISTS (SELECT 1 FROM u)) "
        "SELECT t.owner_id, ok.id AS ok_id, (SELECT count(*) FROM u) AS cleared FROM t LEFT JOIN ok ON true"),
        {"task": task_id, "anyone": is_admin_level(user), "me": user.id, "audit_id": str(uuid4())}).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if row.ok_id is None:
        raise HTTPException(403, "Only the assigned recruiter or a supervisor can update this task")
    db.commit()
    return {"cleared": int(row.cleared or 0)}


# Remarks and notes on a placement that ENDED (the Tasks page tabs Declined / Rejected / Travelled): those rows carry no open task, so they are
# written by placement. Nothing here touches the placement (no status, no `updated_at`: that is the closure time `daily_work._parked` reads),
# the candidate or any open task, so a rejected or declined candidate stays off today's list.

def _ended_placement_task(db: Session, user: User, application_id: str) -> tuple[Application, Candidate, str]:
    """(placement, candidate, id of the task that holds its remarks): the placement's newest task, else a cancelled "holder" task made for the
    purpose (cancelled, so it is nobody's open work and in no count). Only a recruiter who may work the candidate, an MD or an admin."""
    from app.core.deps import ensure_can_work_candidate
    application = db.scalar(select(Application).where(Application.id == application_id))
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Placement not found")
    candidate = db.get(Candidate, application.candidate_id)
    ensure_can_work_candidate(candidate, user, db)
    if application.status == ApplicationStatus.active:
        raise HTTPException(409, "This placement is still active: write on its task")
    task_id = db.scalar(select(Task.id).where(Task.application_id == application.id).order_by(Task.created_at.desc(), Task.id.desc()).limit(1))
    if task_id is None:
        holder = Task(kind=TaskKind.stage, title="Notes on an ended placement", candidate_id=candidate.id, application_id=application.id,
                      stage=application.current_stage, owner_id=candidate.owner_id or user.id, cancelled_at=datetime.now(timezone.utc))
        db.add(holder)
        db.flush()
        task_id = holder.id
    return application, candidate, task_id


@router.post("/applications/{application_id}/ended-remarks", status_code=status.HTTP_201_CREATED)
def add_ended_remark(application_id: str, body: RemarkIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """A remark (the Remarks cell) or a note (the NOTES cell) on a declined / rejected / travelled placement; read back on the next list as the
    row's `remark`, `notes` and `todayNotes` like on a current row."""
    application, candidate, task_id = _ended_placement_task(db, user, application_id)
    remark = Remark(task_id=task_id, body=body.body.strip(), author_id=user.id, kind=body.kind)
    db.add(remark)
    db.add(AuditEntry(actor_id=user.id, action="Added task note" if body.kind == "note" else "Added task remark", target=candidate.name,
                      entity_type="task", entity_id=task_id))
    db.flush()
    out = RemarkOut(id=remark.id, task_id=task_id, body=remark.body, author_id=user.id, created_at=remark.created_at,
                    kind=remark.kind).model_dump(by_alias=True, mode="json")
    db.commit()
    return out


@router.post("/applications/{application_id}/ended-remarks/clear")
def clear_ended_remark(application_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """"Select remark" on an ended placement: its Remarks cell reads empty again (notes stay), as `clear_task_remark` does for a current row."""
    application, candidate, task_id = _ended_placement_task(db, user, application_id)
    cleared = db.execute(update(Remark).where(Remark.deleted_at.is_(None), Remark.cleared_at.is_(None), Remark.kind == "remark", Remark.task_id.in_(
        select(Task.id).where(or_(Task.application_id == application.id, and_(Task.candidate_id == candidate.id, Task.application_id.is_(None))))))
        .values(cleared_at=func.now())).rowcount
    if cleared:
        db.add(AuditEntry(actor_id=user.id, action="Cleared task remark", target=candidate.name, entity_type="task", entity_id=task_id,
                          meta={"remarks": cleared}))
    db.commit()
    return {"cleared": int(cleared or 0)}


# --------------------------------------------------------------- call outcome

class CallIn(BaseModel):
    called: bool = True
    interested: Literal["yes", "no", "unreachable", "rejected"]
    remarks: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def validate_outcome(self):
        if not self.called and self.interested != "unreachable":
            raise ValueError("Record a call before you confirm interest")
        return self


@router.post("/candidates/{candidate_id}/call")
def record_call(candidate_id: str, body: CallIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    c = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    from app.core.deps import ensure_can_work_candidate
    ensure_can_work_candidate(c, user, db)
    from app.services.candidate_call import apply_call
    result = apply_call(db, user, c, called=body.called, interested=body.interested, remarks=body.remarks, now=datetime.now(timezone.utc))
    db.commit()
    return result


# -------------------------------------------------------------------- todos

class TodoIn(BaseModel):
    section: Literal["meetings", "interviews", "calls", "recruitment", "payables", "receivables", "deployment", "reminders", "review"] | None = None
    details: dict[str, str] | None = Field(default=None, max_length=12)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    notes: str | None = None
    dueAt: datetime
    priority: Priority = Priority.normal
    linkTo: str | None = None
    linkLabel: str | None = None

    @field_validator("dueAt")
    @classmethod
    def aware_date(cls, value):
        if value.tzinfo is None:
            raise ValueError("Add a time zone to the due date.")
        return value

    @field_validator("linkTo")
    @classmethod
    def local_link(cls, value):
        if value and (not value.startswith("/") or value.startswith("//")):
            raise ValueError("Choose a link inside the CRM")
        return value


@router.post("/todos", status_code=status.HTTP_201_CREATED)
def create_todo(body: TodoIn, db: Session = Depends(get_db), user: User = Depends(require_admin_level())) -> dict:
    row = TodoItem(
        owner_id=user.id,
        title=body.title.strip(),
        section=body.section, details=body.details,
        notes=body.notes,
        due_at=body.dueAt,
        priority=body.priority,
        link_to=body.linkTo,
        link_label=body.linkLabel,
    )
    db.add(row)
    db.flush()
    db.add(AuditEntry(actor_id=user.id, action="Created personal to-do", entity_type="todo", entity_id=row.id, target=row.title))
    db.commit()
    return TodoOut.model_validate(row).model_dump(by_alias=True, mode="json")


class TodoCompletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool


@router.patch("/todos/{todo_id}")
def toggle_todo(todo_id: str, body: TodoIn | TodoCompletionIn | None = None, db: Session = Depends(get_db), user: User = Depends(require_admin_level())) -> dict:
    row = db.scalar(select(TodoItem).where(TodoItem.id == todo_id).with_for_update())
    if row is None or row.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "To-do not found")
    if body is None:
        row.done = not row.done
    elif isinstance(body, TodoCompletionIn):
        if row.done == body.done:
            return TodoOut.model_validate(row).model_dump(by_alias=True, mode="json")
        row.done = body.done
    else:
        if row.due_at != body.dueAt:
            row.reminded_at = None
        if "section" in body.model_fields_set:
            row.section = body.section
        if "details" in body.model_fields_set:
            row.details = body.details
        row.title, row.notes, row.due_at, row.priority = body.title, body.notes, body.dueAt, body.priority
        row.link_to, row.link_label = body.linkTo, body.linkLabel
    db.add(AuditEntry(actor_id=user.id, action="Updated personal to-do" if isinstance(body, TodoIn) else "Completed personal to-do" if row.done else "Reopened personal to-do", entity_type="todo", entity_id=row.id, target=row.title))
    db.commit()
    return TodoOut.model_validate(row).model_dump(by_alias=True, mode="json")


@router.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_todo(todo_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin_level())):
    row = db.get(TodoItem, todo_id)
    if row is not None and row.owner_id == user.id:
        db.add(AuditEntry(actor_id=user.id, action="Deleted personal to-do", entity_type="todo", entity_id=row.id, target=row.title))
        db.delete(row)
        db.commit()


# ------------------------------------------------------------- reassignment

class ReassignIn(BaseModel):
    candidateIds: list[str]
    toUserId: str
    reason: str = Field(min_length=1, max_length=1000)
    # PRD p11: the 48-hour clock does not reset on reassignment unless the admin chooses to.
    restartClock: bool = False
    restartReason: str | None = Field(default=None, max_length=2000)


@router.post("/candidates/reassign")
def reassign(body: ReassignIn, db: Session = Depends(get_db), user: User = Depends(require_admin_level())) -> dict:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "Give a reason for moving these candidates to another person")
    restart_reason = (body.restartReason or "").strip()
    if body.restartClock:
        if not is_admin_level(user):
            raise HTTPException(403, "Only an admin or MD can restart the 48-hour timer when moving candidates")
        if len(restart_reason) < 3:
            raise HTTPException(422, "Give a reason for restarting the 48-hour timer")
    from app.api.routers.candidates import ensure_assignable, lock_assignment_cursor
    lock_assignment_cursor(db)
    target = ensure_assignable(db, body.toUserId)
    ids = sorted(set(body.candidateIds))
    if not ids:
        raise HTTPException(422, "Select at least one candidate")
    candidates = list(db.scalars(select(Candidate).where(Candidate.id.in_(ids)).order_by(Candidate.id).with_for_update()))
    if len(candidates) != len(ids):
        raise HTTPException(404, "One or more candidates no longer exist")

    now = datetime.now(timezone.utc)
    ownership_changes = []
    # A candidate that waited only for an owner leaves review once it has one; any other
    # reason (missing name or phone, document warnings) still needs a person's check.
    from app.services.candidate_review import release_ownership_review, review_reasons
    waiting_review = [c for c in candidates if c.owner_id is None and c.needs_review]
    blockers = review_reasons(db, waiting_review)
    for c in candidates:
        previous_owner = c.owner_id
        c.owner_id = target.id
        if previous_owner is None:
            release_ownership_review(c, blockers.get(c.id, set()))
        if previous_owner != target.id:
            ownership_changes.append((c.id, c.name, previous_owner, target.id))
            db.add(AuditEntry(actor_id=user.id, action="Candidate reassigned", target=c.name,
                entity_type="candidate", entity_id=c.id, at=now,
                meta={"fromUserId": previous_owner, "toUserId": target.id, "reason": reason}))
        if c.intake_owner_id is None:
            c.intake_owner_id = target.id
        if not db.scalar(select(Task.id).where(Task.candidate_id == c.id).limit(1)) and not c.call_recorded_at:
            db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest", candidate_id=c.id,
                        owner_id=target.id, created_at=now, sla_started_at=now, due_at=now + timedelta(hours=48)))
    from app.services.workflow_scheduler import add_event, restart_sla
    changed_candidate_ids = {change[0] for change in ownership_changes}
    task_only_changes = []

    # The follow-up reminder on the to-do list moves with the candidate. (An application keeps the owner it was
    # mapped under as history; the live recruiter is always the candidate's, which is what every check reads.)
    if changed_candidate_ids:
        from app.services.application_form import sync_follow_up_todo
        for c in candidates:
            if c.id in changed_candidate_ids and c.next_follow_up_date:
                sync_follow_up_todo(db, c)

    # Open tasks move with the candidate; preserve the previous owner for daily history.
    for t in db.scalars(
        select(Task).where(
            Task.candidate_id.in_(ids),
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        ).order_by(Task.id).with_for_update()
    ):
        if t.owner_id != target.id:
            if t.candidate_id not in changed_candidate_ids:
                task_only_changes.append((t.id, t.title, t.owner_id, target.id))
            add_event(db, t, "assignment", f"assignment:{t.id}:{now.isoformat()}", now,
                      {"fromOwnerId": t.owner_id, "toOwnerId": target.id, "actorId": user.id, "reason": reason})
            t.owner_id = target.id
        if body.restartClock:
            restart_sla(db, t, now, user.id, restart_reason)
            db.add(AuditEntry(actor_id=user.id, action="Restarted the 48-hour clock", entity_type="task", entity_id=t.id, target=t.title,
                              meta={"reason": restart_reason, "viaReassignment": True}))

    from app.services.assignment_notifications import notify_assignment_changes, notify_task_assignment_changes
    notify_assignment_changes(db, ownership_changes, actor_id=user.id, actor_name=user.name, reason=reason)
    notify_task_assignment_changes(db, task_only_changes, actor_id=user.id, actor_name=user.name, reason=reason)

    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Reassigned candidates",
            target=f"{len(ids)} to {target.name}",
            entity_type="candidate",
            meta={"candidateIds": ids, "toUserId": target.id, "reason": reason, "restartClock": body.restartClock},
        )
    )
    db.commit()
    return {"count": len(ids), "toUserId": target.id}


class UnassignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidateIds: list[str] = Field(max_length=2000)
    reason: str | None = Field(default=None, max_length=1000)


from app.services.candidate_unassign import unassign_candidates, unassign_refusals  # noqa: E402,F401  (unassign_refusals is re-exported)


@router.post("/candidates/unassign")
def unassign(body: UnassignIn, db: Session = Depends(get_db), user: User = Depends(require_admin_level())) -> dict:
    """Admin only: take candidates back from their recruiter (owner none). They are waiting for a recruiter again ("Not assigned",
    and the admin's "Assign waiting candidates").

    The recruiter's follow-up reminder goes, an untouched initial-call task is removed (a fresh one starts when the candidate is assigned
    again); one with remarks, SLA events, an escalation or a recorded call is cancelled, keeping its history. A candidate who is deployed, mapped to an order or has
    open stage work is refused with the reason, and a candidate without a recruiter is skipped."""
    ids = sorted(set(body.candidateIds))
    if not ids:
        raise HTTPException(422, "Select at least one candidate")
    from app.api.routers.candidates import lock_assignment_cursor
    lock_assignment_cursor(db)
    candidates = list(db.scalars(select(Candidate).where(Candidate.id.in_(ids)).order_by(Candidate.id).with_for_update()))
    if len(candidates) != len(ids):
        raise HTTPException(404, "One or more candidates no longer exist")
    reason = (body.reason or "").strip() or f"Allocation removed by {user.name}"
    result = unassign_candidates(db, user, candidates, reason, body.reason)
    todo, refused, already = result["freed"], result["refused"], result["already"]
    if refused and not todo:
        raise HTTPException(409, refused[0]["reason"] if len(refused) == 1 else f"{len(refused)} candidates cannot be unassigned. {refused[0]['reason']}")
    db.commit()
    return {"count": len(todo), "unassigned": len(todo), "alreadyUnassigned": already, "refused": refused}
