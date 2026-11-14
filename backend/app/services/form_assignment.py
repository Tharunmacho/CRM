"""The application form's assignment block: client, job, source, agency and recruiter of a candidate, editable in place.

Contract: docs/workflows/application-form-v2-contract.md section 1. Nothing is stored twice: the job is the candidate's active
application (mapped and unmapped with the same code `POST /api/applications` uses, so the client-staff routing rule applies), the
source is `crm_candidates.source`, the agency is the text in `crm_candidates.source_detail` (the agent/reference text Data Bank
search reads) and the recruiter is `crm_candidates.owner_id`, changed through the assignment history's reassignment helper.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.db.models import (
    Application, ApplicationStatus, AuditEntry, Candidate, Client, Company, Designation, Order, OrderStatus, ReferenceAgency,
    Role, Source, User,
)
from app.services.agency_display import MACHINE_SOURCE_DETAIL_PATTERN

SOURCE_LABELS = {"email": "Email", "whatsapp": "WhatsApp", "walk_in": "Walk-in", "referral": "Referral", "other": "Other"}
SOURCE_ORDER = ("whatsapp", "email", "walk_in", "referral", "other")
# source_detail holds a typed agent / referrer, but automatic intake stores a machine pointer there (see candidate_search).
MACHINE_SOURCE_DETAIL = re.compile(MACHINE_SOURCE_DETAIL_PATTERN)
RECRUITER_ROLES = (Role.staff, Role.manager)
SOURCE_DETAIL_LIMIT = 255


def can_change(user: User, candidate) -> dict:
    """What THIS user may change. Job, source and agency follow the application form's edit rule; the recruiter is the admin only."""
    editable = user.role in (Role.admin, Role.manager) or (user.role == Role.staff and candidate.owner_id == user.id)
    return {"job": editable, "source": editable, "agency": editable, "recruiter": user.role == Role.admin}


# ------------------------------------------------------------------ reads (run in the form's parallel round)

def read_job_options(session: Session, candidate_id: str) -> list:
    """All jobs of active clients, empty clients, and the candidate's current placement.

    Non-open jobs stay visible with their status; the existing mapping guard still
    requires an open job before a new candidate can be assigned.
    """
    current = select(Application.order_id).where(
        Application.candidate_id == candidate_id,
        Application.status.in_((ApplicationStatus.active, ApplicationStatus.deployed)),
    )
    return list(session.execute(
        select(Client.id.label("client_id"), Client.name.label("client_name"), Company.id.label("company_id"), Company.name.label("company_name"),
               Order.id.label("order_id"), Order.ref, Designation.title, Order.openings, Order.status)
        .select_from(Client).outerjoin(Company, Company.client_id == Client.id)
        .outerjoin(Order, and_(Order.company_id == Company.id,
                             or_(Client.status == "active", Order.id.in_(current))))
        .outerjoin(Designation, Designation.id == Order.designation_id)
        .where(or_(Client.status == "active", Order.id.in_(current)))
        .order_by(Client.name, Company.name, Order.ref)).all())


def read_agencies(session: Session) -> list:
    return list(session.execute(select(ReferenceAgency.id, ReferenceAgency.agency_name, ReferenceAgency.name)
                                .order_by(ReferenceAgency.agency_name, ReferenceAgency.id)).all())


def read_recruiters(session: Session) -> list:
    return list(session.execute(
        select(User.id, User.name, User.role).where(User.active.is_(True), User.on_leave.is_(False), User.role.in_(RECRUITER_ROLES))
        .order_by(User.name, User.id)).all())


# ------------------------------------------------------------------ the payload block

def _agency_name(row) -> str:
    return (row.agency_name or row.name or "").strip()


def build_block(user: User, candidate, owner_name: str | None, chosen, job_rows: list, agencies: list, recruiters: list) -> dict:
    """`assignment` of the form payload. `chosen` is the (Application, Order, Company, Designation, Client) row of the shown application."""
    can = can_change(user, candidate)
    application, order, company, designation, client = chosen if chosen is not None else (None,) * 5
    on_job = application is not None and application.status in (ApplicationStatus.active, ApplicationStatus.deployed)
    detail = (candidate.source_detail or "").strip()
    agency = None
    matches = [row for row in agencies if _agency_name(row).casefold() == detail.casefold()] if detail else []
    # Bulk imports without an agency retain this provenance label in source_detail.
    # An explicit referral or registered agency with that name still takes precedence.
    import_marker = candidate.source != Source.referral and not matches and " ".join(detail.split()).casefold() == "bulk import"
    if detail and not MACHINE_SOURCE_DETAIL.match(detail) and not import_marker:
        agency = {"id": matches[0].id if len(matches) == 1 else None, "name": detail}
    options: dict[str, Any] = {"clients": [], "agencies": [], "recruiters": []}
    if can["job"]:
        grouped: dict[str, dict] = {}
        for row in job_rows:
            entry = grouped.setdefault(row.client_id, {
                "id": row.client_id, "name": row.client_name, "companyId": row.company_id, "companyName": row.company_name, "jobs": []})
            # The Client selector identifies a contact, which can own several companies.
            # Keep every company's jobs reachable under that one option.
            if entry["companyId"] != row.company_id:
                entry["companyId"] = entry["companyName"] = None
            if row.order_id is not None:
                entry["jobs"].append({"orderId": row.order_id, "ref": row.ref, "title": row.title, "openings": row.openings, "status": row.status.value})
        options["clients"] = list(grouped.values())
    if can["agency"]:
        options["agencies"] = [{"id": row.id, "name": _agency_name(row)} for row in agencies]
    if can["recruiter"]:
        options["recruiters"] = [{"id": row.id, "name": row.name, "role": row.role.value} for row in recruiters]
    return {
        "client": ({"id": client.id, "name": client.name, "companyId": company.id, "companyName": company.name} if on_job else None),
        "job": ({"orderId": order.id, "ref": order.ref, "title": designation.title, "applicationId": application.id,
                 "stage": application.current_stage, "country": order.country or company.country} if on_job else None),
        "source": {"key": candidate.source.value, "label": SOURCE_LABELS.get(candidate.source.value, candidate.source.value)},
        "agency": agency,
        "recruiter": {"id": candidate.owner_id, "name": owner_name or "Former team member"} if candidate.owner_id else None,
        "can": can,
        "options": options,
    }


# ------------------------------------------------------------------ saving

def _audit(db: Session, user: User, action: str, candidate, meta: dict) -> None:
    db.add(AuditEntry(actor_id=user.id, action=f"Application form: {action}", target=candidate.name, entity_type="candidate",
                      entity_id=candidate.id, meta=meta))


def _recruiter(db: Session, user_id: str) -> User:
    from app.services.attendance_availability import unavailable_staff_ids
    target = db.get(User, user_id) if user_id else None
    if target is None or not target.active or target.role not in RECRUITER_ROLES or target.on_leave:
        raise HTTPException(422, "Choose an active recruiter or manager who is not on leave.")
    if target.role == Role.staff and target.id in unavailable_staff_ids(db):
        raise HTTPException(422, "Choose an active recruiter or manager who is not on leave.")
    return target


def _resolve_agency(db: Session, fields: dict) -> tuple[bool, str | None, str | None]:
    """(changes?, text for source_detail, agency id). `agencyId` wins over `agencyName`; both null / blank clears."""
    if "agencyId" not in fields and "agencyName" not in fields:
        return False, None, None
    agency_id = fields.get("agencyId")
    if agency_id:
        row = db.get(ReferenceAgency, agency_id)
        if row is None:
            raise HTTPException(404, "That reference agency no longer exists.")
        return True, _agency_name(row)[:SOURCE_DETAIL_LIMIT] or None, row.id
    text = " ".join(str(fields.get("agencyName") or "").split())[:SOURCE_DETAIL_LIMIT]
    return True, text or None, None


def apply(db: Session, user: User, candidate_id: str, fields: dict) -> dict:
    """Apply a partial assignment save in the caller's transaction (the caller commits). `fields` holds only the keys the client sent.

    Returns {"applicationId": the application the form should show afterwards or None, "changed": [..]}.
    """
    from app.api.routers import applications as applications_router
    from app.api.routers.candidates import lock_assignment_cursor
    from app.core.deps import ensure_can_work_candidate
    from app.services import client_routing

    if db.get(Candidate, candidate_id) is None:
        raise HTTPException(404, "Candidate not found")
    if "recruiterId" in fields and user.role != Role.admin:
        raise HTTPException(403, "Only the admin can change the recruiter.")
    changed: list[str] = []
    shown: str | None = None
    wants_job = "orderId" in fields
    if wants_job or "recruiterId" in fields:
        # Wait for the shared assignment cursor for a few seconds only. If another transaction (a bulk import, a stuck connection)
        # holds it, the person gets the calm "another change is being saved" answer from the caller instead of a screen that hangs
        # until the browser gives up after 30 s with "The server took too long to respond".
        db.execute(sql_text("SET LOCAL lock_timeout = '10s'"))
        lock_assignment_cursor(db)  # before the candidate / order rows, like every other owner change

    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update().execution_options(populate_existing=True))
    if any(key in fields for key in ("orderId", "source", "agencyId", "agencyName")):
        ensure_can_work_candidate(candidate, user, db)

    # ---- job
    if wants_job:
        active = db.scalar(select(Application).where(Application.candidate_id == candidate.id, Application.status == ApplicationStatus.active))
        order_id = fields["orderId"]
        if order_id is None:
            if active is not None:
                applications_router.perform_unmap(db, user, active.id)
                _audit(db, user, "job removed", candidate, {"order_id": active.order_id, "application_id": active.id})
                changed.append("job")
        elif active is None or active.order_id != order_id:
            if active is not None and active.current_stage != 1:
                raise HTTPException(409, applications_router.PAST_PRESCREENING)
            mapped = applications_router.perform_map(db, user, candidate.id, order_id,
                                                     replace_application_id=active.id if active is not None else None)
            shown = mapped["application"].id
            _audit(db, user, "job changed" if active is not None else "job set", candidate,
                   {"from_order_id": active.order_id if active is not None else None, "to_order_id": order_id,
                    "application_id": shown, "owner_id": mapped["application"].owner_id})
            changed.append("job")

    # ---- source
    if "source" in fields:
        value = Source(fields["source"])
        if value != candidate.source:
            from app.services.form_options import ensure_allowed
            ensure_allowed(db, "candidate_source", value.value, current=candidate.source.value)
            _audit(db, user, "source changed", candidate, {"from": candidate.source.value, "to": value.value})
            candidate.source = value
            changed.append("source")

    # ---- agency (the reference text in source_detail)
    touched, text, agency_id = _resolve_agency(db, fields)
    if touched and (candidate.source_detail or None) != text:
        previous = candidate.source_detail
        meta = {"from": previous, "to": text, "agency_id": agency_id}
        if previous and MACHINE_SOURCE_DETAIL.match(previous):
            meta["replaced_intake_pointer"] = previous  # the pointer that tied the candidate to an inbox message is overwritten
        _audit(db, user, "agency changed", candidate, meta)
        candidate.source_detail = text
        changed.append("agency")

    # ---- recruiter (last, so an explicit choice wins over the client-staff routing a new job may have applied)
    if "recruiterId" in fields:
        target = _recruiter(db, fields["recruiterId"])
        if target.id != candidate.owner_id:
            client_routing.reassign_candidate(
                db, candidate, target, actor_id=user.id, reason=f"Changed on the application form by {user.name}")
            changed.append("recruiter")
    return {"applicationId": shown, "changed": changed}
