"""
Read and write endpoints for the collections the front end holds.

One bootstrap call returns everything the store needs, because the app renders a whole
desk at once — eleven stages, task lists, order progress — and a page that fired ten
requests on mount would simply be slower for no benefit at this size. When the candidate
table outgrows a single payload, this is the seam to paginate behind.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user, require_role
from app.core.config import get_settings
from app.services import agency_settings
from app.services.desk_snapshot import read_snapshot
from app.db.models import (
    Application,
    ApplicationStatus,
    Attachment,
    AuditEntry,
    Candidate,
    CandidateStatus,
    Client,
    Company,
    Designation,
    Notification,
    Order,
    OrderStatus,
    Priority,
    Remark,
    Role,
    Task,
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
)

router = APIRouter(prefix="/api", tags=["data"])

DOCUMENT_SLOTS = [
    ("resume", "Resume"),
    ("passport", "Passport copy"),
    ("aadhaar", "Aadhaar copy"),
    ("source_bundle", "Source document evidence"),
    ("photo", "Photograph"),
    ("education", "Education certificates"),
    ("experience", "Experience certificates"),
]


def _candidate_out(c: dict, files: dict[tuple[str, str], list[dict]]) -> CandidateOut:
    documents = [
        {
            "key": key,
            "label": label,
            "versions": [
                {
                    "id": f["id"],
                    "name": f["file_name"],
                    "sizeKb": f["size_kb"],
                    "version": f["version"],
                    "url": f"/api/files/{f['id']}",
                    "uploadedBy": f["uploaded_by"],
                    "uploadedAt": datetime.fromisoformat(f["uploaded_at"]),
                }
                for f in files.get((c["id"], key), [])
            ],
        }
        for key, label in DOCUMENT_SLOTS
    ]
    outcome = None
    if c["call_interested"]:
        outcome = {
            "called": bool(c["call_called"]),
            "interested": c["call_interested"],
            "remarks": c["call_remarks"],
            "recordedAt": c["call_recorded_at"],
        }
    return CandidateOut.model_validate({
        **c, "added_at": c["created_at"], "documents": documents, "call_outcome": outcome,
    })


@router.get("/bootstrap")
def bootstrap(db: Session = Depends(get_db), user: User = Depends(current_user), compact: bool = False) -> dict[str, Any]:
    """A fresh desk snapshot with one data query after the session check."""
    snapshot = read_snapshot(db, user.id, compact=compact)

    files: dict[tuple[str, str], list[dict]] = {}
    for f in snapshot["files"]:
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

    return {
        "me": UserOut.model_validate(user).model_dump(by_alias=True, mode="json"),
        "ingestionRevision": snapshot["ingestionRevision"],
        "agencyTimezone": get_settings().agency_timezone,
        "settings": agency_settings.build_payload(snapshot.get("agency"), snapshot.get("currencies")),
        "users": [UserOut.model_validate(u).model_dump(by_alias=True, mode="json") for u in snapshot["users"]],
        "clients": [ClientOut.model_validate(c).model_dump(by_alias=True, mode="json") for c in snapshot["clients"]],
        "companies": [company_out(c) for c in snapshot["companies"]],
        "designations": [DesignationOut.model_validate(d).model_dump(by_alias=True, mode="json") for d in snapshot["designations"]],
        "orders": [OrderOut.model_validate(o).model_dump(by_alias=True, mode="json") for o in snapshot["orders"]],
        "candidates": [_candidate_out(c, files).model_dump(by_alias=True, mode="json") for c in snapshot["candidates"]],
        "applications": [a.model_dump(by_alias=True, mode="json") for a in apps_out],
        "tasks": [TaskOut.model_validate(t).model_dump(by_alias=True, mode="json") for t in snapshot["tasks"]],
        "remarks": [RemarkOut.model_validate(r).model_dump(by_alias=True, mode="json") for r in snapshot["remarks"]],
        "todos": [TodoOut.model_validate(t).model_dump(by_alias=True, mode="json") for t in snapshot["todos"]],
        "notifications": [NotificationOut.model_validate(n).model_dump(by_alias=True, mode="json") for n in snapshot["notifications"]],
    }


# ---------------------------------------------------------------- master data

class MasterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_optional_fields(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key in ("email", "contactEmail", "interviewDate", "deadline"):
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


class ClientCompanyIn(MasterInput):
    """The employer saved together with a new client contact."""

    name: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=2000)


class ClientIn(MasterInput):
    # Only honoured on create: the contact's details become the employer's contact
    # details, so one form captures the whole PRD client record in one transaction.
    company: ClientCompanyIn | None = None
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=20000)
    status: Literal["active", "inactive"] = "active"


class CompanyIn(MasterInput):
    clientId: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=120)
    contactPerson: str | None = Field(default=None, max_length=160)
    contactEmail: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    whatsapp: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=2000)
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
    interviewDate: date | None = None
    deadline: date | None = None
    status: OrderStatus = OrderStatus.open

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
               "contractPeriod": "contract_period", "interviewDate": "interview_date"}
    return {aliases.get(key, key): value for key, value in body.model_dump(exclude={"company"}).items()}


def _master_json(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return float(value)
    return value


def _master_save(db, user, row, body, kind, schema, *, new=False, commit=True):
    changes = {}
    for key, value in _master_values(body).items():
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


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(body: ClientIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.company is None:
        return _master_save(db, user, Client(), body, "client", ClientOut, new=True)
    # Client contact and first employer commit together, so a failure never leaves a
    # contact without the company the form described.
    client = _master_save(db, user, Client(), body, "client", ClientOut, new=True, commit=False)
    employer = CompanyIn(clientId=client["id"], status=body.status, contactPerson=body.name, contactEmail=body.email,
                         phone=body.phone, whatsapp=body.whatsapp, notes=body.notes,
                         **body.company.model_dump())
    try:
        company = _master_save(db, user, Company(), employer, "company", CompanyOut, new=True, commit=False)
        db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "The record could not be saved. Please try again.")
    return {**client, "company": company}


@router.put("/clients/{client_id}")
def update_client(client_id: str, body: ClientIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.company is not None:
        raise HTTPException(422, "Employer details are saved on the company, not here")
    return _master_save(db, user, _master_row(db, Client, client_id), body, "client", ClientOut)


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
    return _master_save(db, user, Designation(), body, "designation", DesignationOut, new=True)


@router.put("/designations/{designation_id}")
def update_designation(designation_id: str, body: DesignationIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _master_save(db, user, _master_row(db, Designation, designation_id), body, "designation", DesignationOut)


@router.delete("/designations/{designation_id}", status_code=204)
def delete_designation(designation_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))):
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
    return _master_save(db, user, row, body, "order", OrderOut, new=True)


@router.put("/orders/{order_id}")
def update_order(order_id: str, body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    # Application mutations also hold this order lock, so counts remain valid until commit.
    row = _master_row(db, Order, order_id)
    _order_requirements(db, body, row)
    return _master_save(db, user, row, body, "order", OrderOut)


@router.get("/master-data/{kind}/{record_id}/audit")
def master_audit(kind: Literal["client", "company", "designation", "order"], record_id: str,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    model = {"client": Client, "company": Company, "designation": Designation, "order": Order}[kind]
    if db.get(model, record_id) is None: raise HTTPException(404, "Record not found")
    rows = db.scalars(select(AuditEntry).where(AuditEntry.entity_type == kind, AuditEntry.entity_id == record_id)
                      .order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(100)).all()
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


ADMIN_OR_MANAGER = require_role(Role.admin, Role.manager)


@router.delete("/orders/{order_id}")
def delete_order(order_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_OR_MANAGER)) -> dict:
    order = _master_row(db, Order, order_id)
    _refuse_if_placed(db, [order.id])
    ref = order.ref
    _delete_orders(db, [order.id])
    db.add(AuditEntry(actor_id=user.id, action="Deleted order", target=ref, entity_type="order", entity_id=order_id))
    db.commit()
    return {"deleted": {"orders": 1}}


@router.delete("/companies/{company_id}")
def delete_company(company_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_OR_MANAGER)) -> dict:
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
def delete_client(client_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_OR_MANAGER)) -> dict:
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


@router.post("/tasks/{task_id}/remarks", status_code=status.HTTP_201_CREATED)
def add_remark(task_id: str, body: RemarkIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if user.role == Role.staff and task.owner_id != user.id:
        raise HTTPException(403, "Only the assigned recruiter or a supervisor can update this task")
    if task.completed_at or task.cancelled_at:
        raise HTTPException(409, "This task is closed, so its notes can no longer be changed")
    row = Remark(task_id=task_id, body=body.body.strip(), author_id=user.id)
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Added task remark", entity_type="task", entity_id=task.id, target=task.title))
    db.commit()
    return RemarkOut.model_validate(row).model_dump(by_alias=True, mode="json")


# --------------------------------------------------------------- call outcome

class CallIn(BaseModel):
    called: bool = True
    interested: Literal["yes", "no", "unreachable"]
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
    if user.role == Role.staff and c.owner_id != user.id:
        raise HTTPException(403, "This candidate belongs to another recruiter")
    # The call may be recorded after the candidate was already matched to an order
    # (matching closes the initial-call task, the call result is still worth keeping).
    # In that case the outcome is stored and audited, but the candidate's status and
    # the order application are left alone: a "not interested" answer must not close
    # an active application silently, the recruiter does that in the order's stages.
    on_order = db.scalar(select(Application.id).where(Application.candidate_id == c.id, Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed])).limit(1)) is not None

    now = datetime.now(timezone.utc)
    c.call_called = body.called
    c.call_interested = body.interested
    c.call_remarks = body.remarks
    c.call_recorded_at = now
    if not on_order:
        if body.interested == "no":
            c.status = CandidateStatus.not_interested
        elif body.interested == "yes" and c.status == CandidateStatus.not_interested:
            c.status = CandidateStatus.new

    # Recording the outcome is what closes the initial-call task.
    for t in db.scalars(
        select(Task).where(
            Task.candidate_id == c.id,
            Task.kind == "initial_call",
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        ).with_for_update()
    ):
        if body.called and body.interested in {"yes", "no"}:
            t.completed_at = now

    db.add(AuditEntry(actor_id=user.id, action="Recorded initial call after matching" if on_order else "Recorded initial call",
                      entity_type="candidate", entity_id=c.id, target=c.name,
                      meta={"called": body.called, "interested": body.interested, "on_order": on_order}))
    db.commit()
    notice = None
    if on_order and body.called and body.interested == "no":
        notice = "Saved. This candidate is still on an order, so nothing on the order was changed. Unmap them or record the result in their stage if they should leave it."
    return {"ok": True, "onOrder": on_order, "statusChanged": not on_order and body.interested in {"yes", "no"}, "notice": notice}


# -------------------------------------------------------------------- todos

class TodoIn(BaseModel):
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
def create_todo(body: TodoIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))) -> dict:
    row = TodoItem(
        owner_id=user.id,
        title=body.title.strip(),
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
def toggle_todo(todo_id: str, body: TodoIn | TodoCompletionIn | None = None, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))) -> dict:
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
        row.title, row.notes, row.due_at, row.priority = body.title, body.notes, body.dueAt, body.priority
        row.link_to, row.link_label = body.linkTo, body.linkLabel
    db.add(AuditEntry(actor_id=user.id, action="Updated personal to-do" if isinstance(body, TodoIn) else "Completed personal to-do" if row.done else "Reopened personal to-do", entity_type="todo", entity_id=row.id, target=row.title))
    db.commit()
    return TodoOut.model_validate(row).model_dump(by_alias=True, mode="json")


@router.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_todo(todo_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
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
def reassign(body: ReassignIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))) -> dict:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "Give a reason for moving these candidates to another person")
    restart_reason = (body.restartReason or "").strip()
    if body.restartClock:
        if user.role != Role.admin:
            raise HTTPException(403, "Only an admin can restart the 48-hour timer when moving candidates")
        if len(restart_reason) < 3:
            raise HTTPException(422, "Give a reason for restarting the 48-hour timer")
    from app.api.routers.candidates import lock_assignment_cursor
    lock_assignment_cursor(db)
    target = db.get(User, body.toUserId)
    from app.services.attendance_availability import unavailable_staff_ids
    if target is None or target.role != Role.staff or not target.active or target.on_leave or target.id in unavailable_staff_ids(db):
        raise HTTPException(422, "Choose an active staff member who is not on leave")
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
    notification_reason = f"Reassigned by {user.name}: {reason}"
    notify_assignment_changes(db, ownership_changes, reason=notification_reason)
    notify_task_assignment_changes(db, task_only_changes, reason=notification_reason)

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
