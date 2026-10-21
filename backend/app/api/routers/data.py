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
    ("source_bundle", "Original document bundles"),
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
def bootstrap(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """A fresh desk snapshot with one data query after the session check."""
    snapshot = read_snapshot(db, user.id)

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


class ClientIn(MasterInput):
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
    currency: str = Field(default="AED", pattern=r"^[A-Z]{3}$")
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
    return {aliases.get(key, key): value for key, value in body.model_dump().items()}


def _master_json(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return float(value)
    return value


def _master_save(db, user, row, body, kind, schema, *, new=False):
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
    return _master_save(db, user, Client(), body, "client", ClientOut, new=True)


@router.put("/clients/{client_id}")
def update_client(client_id: str, body: ClientIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
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
        raise HTTPException(409, "This designation has linked orders and cannot be deleted")
    db.add(AuditEntry(actor_id=user.id, action="Deleted designation", target=row.title, entity_type="designation", entity_id=row.id))
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This designation is now used by an order. Refresh and try again.")


def _order_requirements(db, body, row=None):
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
        raise HTTPException(409, "An order with candidate history must keep its company and designation")
    deployed = counts.get(ApplicationStatus.deployed, 0)
    if body.openings < deployed:
        raise HTTPException(409, f"This order already has {deployed} deployments; openings cannot be lower")
    if body.status == OrderStatus.filled and deployed < body.openings:
        raise HTTPException(409, "An order is filled only when all openings have a deployed candidate")
    if body.status == OrderStatus.open and deployed >= body.openings:
        raise HTTPException(409, "All openings are deployed. Increase openings before reopening this order")
    if body.status == OrderStatus.closed and counts.get(ApplicationStatus.active, 0):
        raise HTTPException(409, "Unmap or complete active candidates before closing; use On hold to pause new matching")


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
            raise HTTPException(409, "Can't delete: retained candidate application history belongs to this record. Close the order or mark the company/client inactive instead.")
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
        raise HTTPException(409, "This task is closed; its remark history is read-only")
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
            raise ValueError("Record a call before confirming interest")
        return self


@router.post("/candidates/{candidate_id}/call")
def record_call(candidate_id: str, body: CallIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    c = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    if user.role == Role.staff and c.owner_id != user.id:
        raise HTTPException(403, "This candidate belongs to another recruiter")
    if db.scalar(select(Application.id).where(Application.candidate_id == c.id, Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed])).limit(1)):
        raise HTTPException(409, "Record the outcome in the candidate's application workflow")

    now = datetime.now(timezone.utc)
    c.call_called = body.called
    c.call_interested = body.interested
    c.call_remarks = body.remarks
    c.call_recorded_at = now
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

    db.add(AuditEntry(actor_id=user.id, action="Recorded initial call", entity_type="candidate", entity_id=c.id,
                      target=c.name, meta={"called": body.called, "interested": body.interested}))
    db.commit()
    return {"ok": True}


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
            raise ValueError("Include the timezone with the due date")
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


@router.patch("/todos/{todo_id}")
def toggle_todo(todo_id: str, body: TodoIn | None = None, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))) -> dict:
    row = db.scalar(select(TodoItem).where(TodoItem.id == todo_id).with_for_update())
    if row is None or row.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "To-do not found")
    if body is None:
        row.done = not row.done
    else:
        if row.due_at != body.dueAt:
            row.reminded_at = None
        row.title, row.notes, row.due_at, row.priority = body.title, body.notes, body.dueAt, body.priority
        row.link_to, row.link_label = body.linkTo, body.linkLabel
    db.add(AuditEntry(actor_id=user.id, action="Updated personal to-do" if body else "Completed personal to-do" if row.done else "Reopened personal to-do", entity_type="todo", entity_id=row.id, target=row.title))
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


@router.post("/candidates/reassign")
def reassign(body: ReassignIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))) -> dict:
    from app.api.routers.candidates import lock_assignment_cursor
    lock_assignment_cursor(db)
    target = db.get(User, body.toUserId)
    if target is None or target.role != Role.staff or not target.active or target.on_leave:
        raise HTTPException(422, "Choose an active Staff account that is not on leave")
    ids = sorted(set(body.candidateIds))
    if not ids:
        raise HTTPException(422, "Select at least one candidate")
    candidates = list(db.scalars(select(Candidate).where(Candidate.id.in_(ids)).order_by(Candidate.id).with_for_update()))
    if len(candidates) != len(ids):
        raise HTTPException(404, "One or more candidates no longer exist")

    now = datetime.now(timezone.utc)
    for c in candidates:
        c.owner_id = target.id
        if c.intake_owner_id is None:
            c.intake_owner_id = target.id
        if not db.scalar(select(Task.id).where(Task.candidate_id == c.id).limit(1)) and not c.call_recorded_at:
            db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest", candidate_id=c.id,
                        owner_id=target.id, created_at=now, sla_started_at=now, due_at=now + timedelta(hours=48)))
        db.add(Notification(user_id=target.id, kind="assignment", title="Candidate assigned", detail=c.name, link_to=f"/candidates/{c.id}"))

    from app.services.workflow_scheduler import add_event

    # Open tasks move with the candidate; preserve the previous owner for daily history.
    for t in db.scalars(
        select(Task).where(
            Task.candidate_id.in_(ids),
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        ).order_by(Task.id).with_for_update()
    ):
        if t.owner_id != target.id:
            add_event(db, t, "assignment", f"assignment:{t.id}:{now.isoformat()}", now,
                      {"fromOwnerId": t.owner_id, "toOwnerId": target.id, "actorId": user.id})
            t.owner_id = target.id

    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Reassigned candidates",
            target=f"{len(ids)} to {target.name}",
            entity_type="candidate",
            meta={"candidateIds": ids, "toUserId": target.id},
        )
    )
    db.commit()
    return {"count": len(ids), "toUserId": target.id}
