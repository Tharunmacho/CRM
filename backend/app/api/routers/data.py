"""
Read and write endpoints for the collections the front end holds.

One bootstrap call returns everything the store needs, because the app renders a whole
desk at once — eleven stages, task lists, order progress — and a page that fired ten
requests on mount would simply be slower for no benefit at this size. When the candidate
table outgrows a single payload, this is the seam to paginate behind.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user, require_role
from app.services.desk_snapshot import read_snapshot
from app.db.models import (
    Application,
    ApplicationStatus,
    AuditEntry,
    Candidate,
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
    ("source_bundle", "Original email document bundle"),
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


# ------------------------------------------------------------------ clients

class ClientIn(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    country: str | None = None
    city: str | None = None
    notes: str | None = None
    status: str = "active"


class CompanyIn(BaseModel):
    clientId: str
    name: str
    country: str | None = None
    city: str | None = None
    industry: str | None = None
    contactPerson: str | None = None
    contactEmail: str | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    status: str = "active"


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(body: ClientIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    row = Client(
        name=body.name.strip(),
        email=body.email,
        phone=body.phone,
        whatsapp=body.whatsapp,
        country=body.country,
        city=body.city,
        notes=body.notes,
        status=body.status,
    )
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Created client", target=row.name, entity_type="client"))
    db.commit()
    return ClientOut.model_validate(row).model_dump(by_alias=True, mode="json")


@router.post("/companies", status_code=status.HTTP_201_CREATED)
def create_company(body: CompanyIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Client, body.clientId) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")

    row = Company(
        client_id=body.clientId,
        name=body.name.strip(),
        country=body.country,
        city=body.city,
        industry=body.industry,
        contact_person=body.contactPerson,
        contact_email=body.contactEmail,
        phone=body.phone,
        address=body.address,
        notes=body.notes,
        status=body.status,
    )
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Created company", target=row.name, entity_type="company"))
    db.commit()
    return CompanyOut.model_validate(row).model_dump(by_alias=True, mode="json")


# ------------------------------------------------------------- designations

class DesignationIn(BaseModel):
    title: str
    category: str | None = None
    description: str | None = None
    requiredSkills: list[str] = []
    minExperience: int = 0


@router.post("/designations", status_code=status.HTTP_201_CREATED)
def create_designation(body: DesignationIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    row = Designation(
        title=body.title.strip(),
        category=body.category,
        description=body.description,
        required_skills=body.requiredSkills,
        min_experience=body.minExperience,
    )
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Created designation", target=row.title, entity_type="designation"))
    db.commit()
    return DesignationOut.model_validate(row).model_dump(by_alias=True, mode="json")


# ------------------------------------------------------------------- orders

class OrderIn(BaseModel):
    companyId: str
    designationId: str
    openings: int = 1
    country: str | None = None
    city: str | None = None
    salary: float | None = None
    currency: str = "AED"
    benefits: list[str] = []
    contractPeriod: str | None = None
    interviewDate: str | None = None
    deadline: str | None = None


@router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_order(body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    count = db.scalar(select(func.count()).select_from(Order)) or 0
    row = Order(
        ref=f"ORD-{datetime.now(timezone.utc).year}-{count + 1:03d}",
        company_id=body.companyId,
        designation_id=body.designationId,
        openings=body.openings,
        country=body.country,
        city=body.city,
        salary=body.salary,
        currency=body.currency,
        benefits=body.benefits,
        contract_period=body.contractPeriod,
        interview_date=body.interviewDate or None,
        deadline=body.deadline or None,
        status=OrderStatus.open,
    )
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Created order", target=row.ref, entity_type="order"))
    db.commit()
    return OrderOut.model_validate(row).model_dump(by_alias=True, mode="json")


# ----------------------------------------------------------------- deletion
#
# A client owns companies, a company owns orders, and an order holds the candidates
# mapped to it. Deleting goes down that chain — but never through a candidate who is
# still being worked on an order, or who was deployed through it: that is a placement
# the agency has to answer for. Closed history (unmapped, rejected) goes with the order,
# and its stage records, tasks and remarks follow through ON DELETE CASCADE.

def _lock_orders(db: Session, order_ids: list[str]) -> None:
    """
    Held until commit. Mapping a candidate inserts a row referencing the order, which
    waits on this lock and then fails on the deleted order — so nobody can be mapped
    in between the check below and the delete.
    """
    if order_ids:
        db.scalars(select(Order.id).where(Order.id.in_(order_ids)).with_for_update()).all()


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


def _delete_orders(db: Session, order_ids: list[str]) -> None:
    if order_ids:
        db.execute(delete(Application).where(Application.order_id.in_(order_ids)))
        db.execute(delete(Order).where(Order.id.in_(order_ids)))


ADMIN_OR_MANAGER = require_role(Role.admin, Role.manager)


@router.delete("/orders/{order_id}")
def delete_order(order_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_OR_MANAGER)) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    _lock_orders(db, [order.id])
    _refuse_if_placed(db, [order.id])
    ref = order.ref
    _delete_orders(db, [order.id])
    db.add(AuditEntry(actor_id=user.id, action="Deleted order", target=ref, entity_type="order", entity_id=order_id))
    db.commit()
    return {"deleted": {"orders": 1}}


@router.delete("/companies/{company_id}")
def delete_company(company_id: str, db: Session = Depends(get_db), user: User = Depends(ADMIN_OR_MANAGER)) -> dict:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    order_ids = list(db.scalars(select(Order.id).where(Order.company_id == company.id)))
    _lock_orders(db, order_ids)
    _refuse_if_placed(db, order_ids)
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
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")

    company_ids = list(db.scalars(select(Company.id).where(Company.client_id == client.id)))
    order_ids = (
        list(db.scalars(select(Order.id).where(Order.company_id.in_(company_ids)))) if company_ids else []
    )
    _lock_orders(db, order_ids)
    _refuse_if_placed(db, order_ids)
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
    body: str


@router.post("/tasks/{task_id}/remarks", status_code=status.HTTP_201_CREATED)
def add_remark(task_id: str, body: RemarkIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if db.get(Task, task_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    row = Remark(task_id=task_id, body=body.body.strip(), author_id=user.id)
    db.add(row)
    db.commit()
    return RemarkOut.model_validate(row).model_dump(by_alias=True, mode="json")


# --------------------------------------------------------------- call outcome

class CallIn(BaseModel):
    called: bool = True
    interested: str
    remarks: str | None = None


@router.post("/candidates/{candidate_id}/call")
def record_call(candidate_id: str, body: CallIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    c = db.get(Candidate, candidate_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")

    now = datetime.now(timezone.utc)
    c.call_called = body.called
    c.call_interested = body.interested
    c.call_remarks = body.remarks
    c.call_recorded_at = now
    if body.interested == "no":
        c.status = "not_interested"

    # Recording the outcome is what closes the initial-call task.
    for t in db.scalars(
        select(Task).where(
            Task.candidate_id == c.id,
            Task.kind == "initial_call",
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        )
    ):
        t.completed_at = now

    db.commit()
    return {"ok": True}


# -------------------------------------------------------------------- todos

class TodoIn(BaseModel):
    title: str
    notes: str | None = None
    dueAt: datetime
    priority: Priority = Priority.normal
    linkTo: str | None = None
    linkLabel: str | None = None


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
    db.commit()
    return TodoOut.model_validate(row).model_dump(by_alias=True, mode="json")


@router.patch("/todos/{todo_id}")
def toggle_todo(todo_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    row = db.get(TodoItem, todo_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "To-do not found")
    row.done = not row.done
    db.commit()
    return TodoOut.model_validate(row).model_dump(by_alias=True, mode="json")


@router.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_todo(todo_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(TodoItem, todo_id)
    if row is not None and row.owner_id == user.id:
        db.delete(row)
        db.commit()


# ------------------------------------------------------------- reassignment

class ReassignIn(BaseModel):
    candidateIds: list[str]
    toUserId: str


@router.post("/candidates/reassign")
def reassign(body: ReassignIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))) -> dict:
    target = db.get(User, body.toUserId)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    for c in db.scalars(select(Candidate).where(Candidate.id.in_(body.candidateIds))):
        c.owner_id = target.id

    # Open tasks move with the candidate; the 48-hour clock is not reset.
    for t in db.scalars(
        select(Task).where(
            Task.candidate_id.in_(body.candidateIds),
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        )
    ):
        t.owner_id = target.id

    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Reassigned candidates",
            target=f"{len(body.candidateIds)} to {target.name}",
            entity_type="candidate",
        )
    )
    db.commit()
    return {"count": len(body.candidateIds), "toUserId": target.id}
