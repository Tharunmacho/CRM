"""Job requirements: a spreadsheet-like list recruiters fill in by hand (DOC date, client, country, the requirement form, an ad flyer,
Active / Closed) plus extra columns the recruiters add themselves.

GET    /api/job-requirements?status=active|closed|all&q=&limit=&offset=  -> {"items": [Item], "fields": [Field], "total"}
POST   /api/job-requirements {docDate?, clientName?, country?, status?, details?, custom?}  (an empty body is a blank row) -> 201 Item
PATCH  /api/job-requirements/{id}  same keys, all optional: docDate / clientName / country / status replace, `details` and `custom`
                                   merge key by key (a key sent as '' is cleared) -> Item
GET    /api/job-requirements  also takes owner, client, country (exact, any letter case), from / to (DOC date) and due=soon|overdue, and
                               returns `summary` {active, closed, addedThisWeek, dueSoon, overdue, byOwner} over every status.
GET    /api/job-requirements/export?format=xlsx|csv  the same filters, a file for every signed-in role.
GET    /api/job-requirements/{id}/history  -> {"items": [{at, actorId, actorName, action, summary}]} newest first.
PATCH also takes ownerId (an active recruiter or MD; '' / null unassigns) and closedReason (filled | client_cancelled | expired | other | '').
DELETE /api/job-requirements/{id}  admin level only -> 204 (the ad file goes too)
POST   /api/job-requirements/{id}/ad   multipart `file` (png, jpg, webp, gif, pdf; 10 MB) replaces any earlier ad -> Item
GET    /api/job-requirements/{id}/ad   the bytes (signed-in users only, Cache-Control private)
DELETE /api/job-requirements/{id}/ad   -> Item
POST   /api/job-requirement-fields {label, type} -> 201 Field;  PATCH .../{id} {label} renames;  DELETE .../{id} admin level only, the
                                   column and every row's value for it disappear.

Item.details always carries every key (an empty string when blank); Item.custom only the columns that have a value. Every role may
list, create, edit and close; only an Admin or MD deletes a requirement or a column. Create / edit / close / reopen / delete are audited.
"""
from __future__ import annotations

import csv
import logging
import math
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field as PField, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user, require_admin_level
from app.db.models import AuditEntry, JobRequirement, JobRequirementField, Role, User
from app.db.session import get_db
from app.services import document_storage

router = APIRouter(tags=["job-requirements"])
logger = logging.getLogger(__name__)
admin = require_admin_level()

MAX_VALUE = 2000
MAX_LONG = 20000
MAX_AD_BYTES = 10 * 1024 * 1024
# The owner's sheet: 16 requirement items (key, label) in order, then `deadline` (ISO date or '') for the due-soon / overdue views.
DETAIL_ITEMS = (("position", "Name of the Position"), ("vacancies", "No. of Requirements"), ("placeOfWork", "Place of Work"),
                ("contractPeriod", "Period of Contract"), ("qualification", "Qualification"), ("agePreference", "Age Preference"),
                ("additionalSkills", "Additional Skills"), ("basicSalary", "Basic Salary"), ("workingHours", "Working Hours"),
                ("overtime", "Overtime"), ("otCalculation", "OT Calculation"), ("food", "Food"), ("accommodation", "Accommodation"),
                ("insurance", "Insurance"), ("transport", "Transport"), ("remarks", "Remarks / Special Conditions"))
DETAIL_KEYS = tuple(key for key, _ in DETAIL_ITEMS) + ("deadline",)
AD_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
AD_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf"}
DUPLICATE = "A column with this name already exists."


# ---------------------------------------------------------------- request bodies

def _as_text(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return value.strip() if isinstance(value, str) else value


class DetailsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: str | None = PField(None, max_length=MAX_VALUE)
    vacancies: str | None = PField(None, max_length=MAX_VALUE)
    placeOfWork: str | None = PField(None, max_length=MAX_VALUE)
    contractPeriod: str | None = PField(None, max_length=MAX_VALUE)
    qualification: str | None = PField(None, max_length=MAX_VALUE)
    agePreference: str | None = PField(None, max_length=MAX_VALUE)
    additionalSkills: str | None = PField(None, max_length=MAX_VALUE)
    basicSalary: str | None = PField(None, max_length=MAX_VALUE)
    workingHours: str | None = PField(None, max_length=MAX_VALUE)
    overtime: str | None = PField(None, max_length=MAX_VALUE)
    otCalculation: str | None = PField(None, max_length=MAX_VALUE)
    food: str | None = PField(None, max_length=MAX_VALUE)
    accommodation: str | None = PField(None, max_length=MAX_VALUE)
    insurance: str | None = PField(None, max_length=MAX_VALUE)
    transport: str | None = PField(None, max_length=MAX_VALUE)
    remarks: str | None = PField(None, max_length=MAX_LONG)
    deadline: str | None = PField(None, max_length=MAX_VALUE)

    @field_validator("*", mode="before")
    @classmethod
    def _coerce(cls, value):
        return _as_text(value)

    @field_validator("deadline")
    @classmethod
    def _iso(cls, value):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Use a date like 2026-11-21 or leave it empty.")
        return value


class RequirementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    docDate: date | None = None
    clientName: str | None = PField(None, max_length=200)
    country: str | None = PField(None, max_length=100)
    status: Literal["active", "closed"] | None = None
    ownerId: str | None = None
    closedReason: Literal["filled", "client_cancelled", "expired", "other", ""] | None = None
    details: DetailsIn | None = None
    custom: dict[str, Any] | None = None

    @field_validator("clientName", "country", mode="before")
    @classmethod
    def _strip(cls, value):
        return _as_text(value)


def _one_line(value):
    return " ".join(value.split()) if isinstance(value, str) else value


class FieldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = PField(min_length=1, max_length=80)
    type: Literal["text", "number", "date", "yes_no"] = "text"
    _label = field_validator("label", mode="before")(_one_line)


class FieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = PField(None, min_length=1, max_length=80)
    _label = field_validator("label", mode="before")(_one_line)


# ---------------------------------------------------------------- output

def _person(users: dict[str, User], user_id: str | None) -> dict | None:
    user = users.get(user_id) if user_id else None
    return {"id": user.id, "name": user.name} if user else None


def _item(row: JobRequirement, users: dict[str, User]) -> dict:
    details = {key: (row.details or {}).get(key, "") for key in DETAIL_KEYS}
    return {
        "id": row.id, "docDate": row.doc_date.isoformat() if row.doc_date else None, "clientName": row.client_name, "country": row.country,
        "details": details, "adUrl": f"/api/job-requirements/{row.id}/ad" if row.ad_object_key else None,
        "adName": row.ad_file_name if row.ad_object_key else None, "status": row.status, "custom": dict(row.custom or {}),
        "ownerId": row.owner_id, "ownerName": users[row.owner_id].name if row.owner_id in users else None,
        "closedReason": row.closed_reason or "", "closedAt": row.closed_at.isoformat() if row.closed_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None, "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "createdBy": _person(users, row.created_by), "updatedBy": _person(users, row.updated_by),
    }


def _field(row: JobRequirementField) -> dict:
    return {"id": row.id, "key": row.key, "label": row.label, "type": row.type, "sort": row.sort}


def _items(db: Session, rows: list[JobRequirement]) -> list[dict]:
    ids = {value for row in rows for value in (row.created_by, row.updated_by, row.owner_id) if value}
    users = {user.id: user for user in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return [_item(row, users) for row in rows]


def _fields(db: Session) -> list[JobRequirementField]:
    return list(db.scalars(select(JobRequirementField).order_by(JobRequirementField.sort, JobRequirementField.created_at, JobRequirementField.id)))


def _audit(db: Session, user: User, action: str, row: JobRequirement, meta: dict | None = None) -> None:
    db.add(AuditEntry(actor_id=user.id, action=action, target=row.client_name or "Job requirement", entity_type="job_requirement",
                      entity_id=row.id, meta=meta))


def _row(db: Session, requirement_id: str, *, lock: bool = False) -> JobRequirement:
    statement = select(JobRequirement).where(JobRequirement.id == requirement_id)
    row = db.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise HTTPException(404, "This job requirement was not found.")
    return row


# ---------------------------------------------------------------- value rules

def _clean_custom(db: Session, values: dict[str, Any]) -> dict[str, str]:
    """Check each value against the type of its column; returns {field id: text}. Unknown column ids and bad values are refused (422)."""
    if not values:
        return {}
    known = {row.id: row for row in db.scalars(select(JobRequirementField).where(JobRequirementField.id.in_(list(values))))}
    clean: dict[str, str] = {}
    for field_id, raw in values.items():
        field = known.get(field_id)
        if field is None:
            raise HTTPException(422, f"There is no column with the id {field_id}.")
        if raw is None:
            value = ""
        elif isinstance(raw, bool):
            value = ("yes" if raw else "no") if field.type == "yes_no" else None
        elif isinstance(raw, (int, float, str)):
            value = str(raw).strip()
        else:
            value = None
        if value is None or len(value) > MAX_VALUE:
            raise HTTPException(422, f"The value for \"{field.label}\" is not valid.")
        if value:
            if field.type == "number":
                try:
                    if not math.isfinite(float(value)):
                        raise ValueError
                except ValueError:
                    raise HTTPException(422, f"\"{field.label}\" takes a number.")
            elif field.type == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(422, f"\"{field.label}\" takes a date like 2026-11-21.")
            elif field.type == "yes_no" and value not in ("yes", "no"):
                raise HTTPException(422, f"\"{field.label}\" takes yes or no.")
        clean[field_id] = value
    return clean


def _merge(current: dict | None, changes: dict[str, str]) -> dict:
    merged = dict(current or {})
    for key, value in changes.items():
        if value == "":
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _details_changes(body: DetailsIn | None) -> dict[str, str]:
    return {key: (value or "") for key, value in body.model_dump(exclude_unset=True).items()} if body is not None else {}


# ---------------------------------------------------------------- requirements

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
DUE_SOON_DAYS = 7
CLOSED_REASONS = {"filled": "filled", "client_cancelled": "client cancelled", "expired": "expired", "other": "other"}
_DEADLINE = JobRequirement.details["deadline"].astext


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().agency_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _check_owner(db: Session, owner_id: str) -> None:
    """An owner is an active recruiter or the MD; admins, disabled accounts and unknown ids are refused (422)."""
    owner = db.get(User, owner_id)
    if owner is None or not owner.active or owner.role not in (Role.staff, Role.manager):
        raise HTTPException(422, "Choose an active recruiter or the MD to assign this requirement to.")


def _deadline_condition(kind: str, today: date):
    valid = _DEADLINE.op("~")(DATE_PATTERN)
    start, end = today.isoformat(), (today + timedelta(days=DUE_SOON_DAYS)).isoformat()
    if kind == "overdue":
        return and_(JobRequirement.status == "active", valid, _DEADLINE < start)
    return and_(JobRequirement.status == "active", valid, _DEADLINE >= start, _DEADLINE <= end)


def _filters(q: str | None, owner: str | None, client: str | None, country: str | None, start: date | None, end: date | None) -> list:
    """The filters the summary also respects (everything except the status tab and the due filter)."""
    conditions = []
    term = " ".join((q or "").split())
    if term:
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(JobRequirement.client_name.ilike(like, escape="\\"), JobRequirement.country.ilike(like, escape="\\"),
                              *(JobRequirement.details[key].astext.ilike(like, escape="\\")
                                for key in ("position", "placeOfWork", "qualification", "remarks"))))
    if owner:
        conditions.append(JobRequirement.owner_id == owner)
    if client and client.strip():
        conditions.append(func.lower(JobRequirement.client_name) == " ".join(client.split()).lower())
    if country and country.strip():
        conditions.append(func.lower(JobRequirement.country) == " ".join(country.split()).lower())
    if start:
        conditions.append(JobRequirement.doc_date >= start)
    if end:
        conditions.append(JobRequirement.doc_date <= end)
    return conditions


def _summary(db: Session, conditions: list, today: date) -> dict:
    active, closed = JobRequirement.status == "active", JobRequirement.status == "closed"
    week_start = datetime.combine(today - timedelta(days=6), time.min, tzinfo=_zone())
    counts = db.execute(select(
        func.count().filter(active), func.count().filter(closed), func.count().filter(JobRequirement.created_at >= week_start),
        func.count().filter(_deadline_condition("soon", today)), func.count().filter(_deadline_condition("overdue", today)),
    ).where(*conditions)).one()
    owners = db.execute(select(JobRequirement.owner_id, func.count().filter(active)).where(*conditions, JobRequirement.owner_id.is_not(None))
                        .group_by(JobRequirement.owner_id)).all()
    names = {user.id: user.name for user in db.scalars(select(User).where(User.id.in_([row[0] for row in owners])))} if owners else {}
    by_owner = sorted(({"id": owner_id, "name": names.get(owner_id, ""), "active": count} for owner_id, count in owners),
                      key=lambda item: (item["name"].lower(), item["id"]))
    return {"active": counts[0], "closed": counts[1], "addedThisWeek": counts[2], "dueSoon": counts[3], "overdue": counts[4], "byOwner": by_owner}


def _query(status: str, due: str | None, today: date, conditions: list) -> list:
    extra = list(conditions)
    if status != "all":
        extra.append(JobRequirement.status == status)
    if due:
        extra.append(_deadline_condition(due, today))
    return extra


_ORDER = (JobRequirement.doc_date.desc().nulls_last(), JobRequirement.created_at.desc(), JobRequirement.id)


@router.get("/api/job-requirements")
def list_requirements(status: Literal["active", "closed", "all"] = "all", q: str | None = None, owner: str | None = None,
                      client: str | None = None, country: str | None = None, from_: date | None = Query(None, alias="from"),
                      to: date | None = None, due: Literal["soon", "overdue"] | None = None,
                      limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0),
                      user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    today = datetime.now(_zone()).date()
    shared = _filters(q, owner, client, country, from_, to)
    listed = _query(status, due, today, shared)
    total = db.scalar(select(func.count()).select_from(JobRequirement).where(*listed)) or 0
    rows = list(db.scalars(select(JobRequirement).where(*listed).order_by(*_ORDER).limit(limit).offset(offset)))
    return {"items": _items(db, rows), "fields": [_field(field) for field in _fields(db)], "total": total, "summary": _summary(db, shared, today)}


def _cell(value):
    """Keep a spreadsheet from running text that starts like a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _export_table(db: Session, rows: list[JobRequirement], fields: list[JobRequirementField]) -> list[list]:
    zone = _zone()
    ids = {value for row in rows for value in (row.created_by, row.updated_by, row.owner_id) if value}
    users = {user.id: user.name for user in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}

    def stamp(value: datetime | None) -> str:
        return value.astimezone(zone).strftime("%Y-%m-%d %H:%M") if value else ""
    header = (["S.No", "DOC", "Client name", "Country"] + [label for _, label in DETAIL_ITEMS] +
              ["Deadline", "Assigned to", "Status", "Closed reason", "Closed on", "Added by", "Last updated"] + [field.label for field in fields])
    table = [header]
    for number, row in enumerate(rows, 1):
        details = row.details or {}
        updated = stamp(row.updated_at) + (f" by {users[row.updated_by]}" if row.updated_by in users else "")
        values = ([number, row.doc_date.isoformat() if row.doc_date else "", row.client_name, row.country] +
                  [details.get(key, "") for key, _ in DETAIL_ITEMS] +
                  [details.get("deadline", ""), users.get(row.owner_id, ""), "Active" if row.status == "active" else "Closed",
                   CLOSED_REASONS.get(row.closed_reason or "", "").capitalize(), stamp(row.closed_at)[:10], users.get(row.created_by, ""),
                   updated.strip()] + [(row.custom or {}).get(field.id, "") for field in fields])
        table.append([_cell(value) for value in values])
    return table


@router.get("/api/job-requirements/export")
def export_requirements(format: Literal["xlsx", "csv"], status: Literal["active", "closed", "all"] = "all", q: str | None = None,
                        owner: str | None = None, client: str | None = None, country: str | None = None,
                        from_: date | None = Query(None, alias="from"), to: date | None = None, due: Literal["soon", "overdue"] | None = None,
                        user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    today = datetime.now(_zone()).date()
    listed = _query(status, due, today, _filters(q, owner, client, country, from_, to))
    rows = list(db.scalars(select(JobRequirement).where(*listed).order_by(*_ORDER)))
    table = _export_table(db, rows, _fields(db))
    if format == "xlsx":
        from app.services import lead_xlsx
        payload = lead_xlsx.build_workbook([{"name": "Job requirements", "rows": table, "widths": lead_xlsx.fit_widths(table),
                                              "header": True, "freeze": True, "filter": True}])
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        out = StringIO()
        csv.writer(out, lineterminator="\r\n").writerows(table)
        payload = ("﻿" + out.getvalue()).encode("utf-8")   # a byte-order mark so Excel reads names correctly
        mime = "text/csv; charset=utf-8"
    return Response(payload, media_type=mime, headers={"Content-Disposition": f'attachment; filename="job-requirements.{format}"',
                                                       "Cache-Control": "private, no-store"})


@router.post("/api/job-requirements", status_code=201)
def create_requirement(body: RequirementIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    body = body or RequirementIn()
    if body.ownerId:
        _check_owner(db, body.ownerId)
        owner_id = body.ownerId
    else:   # the creator handles it, unless the creator is an Admin (admins are not offered as owners) or an empty owner was sent
        owner_id = user.id if "ownerId" not in body.model_fields_set and user.role in (Role.staff, Role.manager) else None
    status = body.status or "active"
    if body.closedReason and status != "closed":
        raise HTTPException(422, "A closing reason only goes with a closed requirement.")
    row = JobRequirement(id=str(uuid.uuid4()), doc_date=body.docDate or datetime.now(_zone()).date(), client_name=body.clientName or "",
                         country=body.country or "", status=status, details=_merge({}, _details_changes(body.details)),
                         custom=_merge({}, _clean_custom(db, body.custom or {})), owner_id=owner_id, created_by=user.id, updated_by=user.id,
                         closed_at=datetime.now(timezone.utc) if status == "closed" else None,
                         closed_reason=(body.closedReason or None) if status == "closed" else None)
    db.add(row)
    db.flush()
    _audit(db, user, "Added job requirement", row)
    db.commit()
    db.refresh(row)
    return _items(db, [row])[0]


@router.patch("/api/job-requirements/{requirement_id}")
def edit_requirement(requirement_id: str, body: RequirementIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = _row(db, requirement_id, lock=True)
    sent = body.model_fields_set
    changed: list[str] = []
    previous_status = row.status
    if "docDate" in sent and body.docDate != row.doc_date:
        row.doc_date = body.docDate
        changed.append("docDate")
    if "clientName" in sent and (body.clientName or "") != row.client_name:
        row.client_name = body.clientName or ""
        changed.append("clientName")
    if "country" in sent and (body.country or "") != row.country:
        row.country = body.country or ""
        changed.append("country")
    known = {key: value for key, value in (row.details or {}).items() if key in DETAIL_KEYS}
    if known != (row.details or {}):
        row.details = known   # drop the keys of an older form layout
    changes = _details_changes(body.details)
    if changes:
        merged = _merge(row.details, changes)
        if merged != (row.details or {}):
            row.details = merged
            changed += [f"details.{key}" for key in changes]
    custom_changes = _clean_custom(db, body.custom or {})
    if custom_changes:
        merged = _merge(row.custom, custom_changes)
        if merged != (row.custom or {}):
            row.custom = merged
            changed += [f"custom.{key}" for key in custom_changes]
    owner_change = None
    if "ownerId" in sent and (body.ownerId or None) != row.owner_id:
        if body.ownerId:
            _check_owner(db, body.ownerId)
        previous = db.get(User, row.owner_id).name if row.owner_id else None
        row.owner_id = body.ownerId or None
        owner_change = {"from": previous, "to": db.get(User, row.owner_id).name if row.owner_id else None}
    new_status = body.status if "status" in sent and body.status is not None else row.status
    reason = (body.closedReason or None) if "closedReason" in sent else None
    if reason and new_status != "closed":
        raise HTTPException(422, "A closing reason only goes with a closed requirement.")
    reason_changed = False
    if new_status != row.status:
        row.status = new_status
        row.closed_at = datetime.now(timezone.utc) if new_status == "closed" else None
        row.closed_reason = reason if new_status == "closed" else None
    elif "closedReason" in sent and row.status == "closed" and reason != row.closed_reason:
        row.closed_reason = reason
        reason_changed = True
    status_changed = row.status != previous_status
    if changed or status_changed or owner_change or reason_changed:
        row.updated_by = user.id
        row.updated_at = func.now()
    if changed:
        _audit(db, user, "Edited job requirement", row, {"changed": changed})
    if owner_change:
        _audit(db, user, "Reassigned job requirement", row, owner_change)
    if status_changed:
        _audit(db, user, "Closed job requirement" if row.status == "closed" else "Reopened job requirement", row,
               {"reason": row.closed_reason} if row.status == "closed" else None)
    elif reason_changed:
        _audit(db, user, "Changed job requirement closing reason", row, {"reason": row.closed_reason})
    db.commit()
    db.refresh(row)
    return _items(db, [row])[0]


@router.delete("/api/job-requirements/{requirement_id}", status_code=204)
def delete_requirement(requirement_id: str, user: User = Depends(admin), db: Session = Depends(get_db)) -> Response:
    row = _row(db, requirement_id, lock=True)
    key = row.ad_object_key
    _audit(db, user, "Deleted job requirement", row, {"clientName": row.client_name, "country": row.country, "hadAd": bool(key)})
    db.delete(row)
    db.commit()
    _discard(key)
    return Response(status_code=204)


# ---------------------------------------------------------------- history

_LABELS = {"docDate": "DOC date", "clientName": "Client name", "country": "Country", "deadline": "Deadline", **dict(DETAIL_ITEMS)}


def _history_text(entry: AuditEntry, columns: dict[str, str]) -> str:
    meta = entry.meta or {}
    action = entry.action
    if action == "Added job requirement":
        return "Added the requirement"
    if action == "Edited job requirement":
        names: list[str] = []
        for key in meta.get("changed", []):
            label = columns.get(key[7:], "a removed column") if key.startswith("custom.") else _LABELS.get(key.split(".", 1)[-1], key)
            if label not in names:
                names.append(label)
        return "Changed " + ", ".join(names) if names else "Edited"
    if action == "Closed job requirement":
        reason = CLOSED_REASONS.get(meta.get("reason") or "")
        return f"Closed ({reason})" if reason else "Closed"
    if action == "Reopened job requirement":
        return "Reopened"
    if action == "Changed job requirement closing reason":
        reason = CLOSED_REASONS.get(meta.get("reason") or "")
        return f"Closing reason: {reason}" if reason else "Closing reason cleared"
    if action == "Reassigned job requirement":
        before = f" (was {meta['from']})" if meta.get("from") else ""
        return f"Assigned to {meta['to']}{before}" if meta.get("to") else f"Unassigned{before}"
    return {"Added job requirement ad": "Ad uploaded", "Replaced job requirement ad": "Ad replaced", "Removed job requirement ad": "Ad removed"}.get(action, "")


@router.get("/api/job-requirements/{requirement_id}/history")
def history(requirement_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    _row(db, requirement_id)
    entries = list(db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "job_requirement", AuditEntry.entity_id == requirement_id)
                              .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())))
    actors = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({e.actor_id for e in entries if e.actor_id})))} if entries else {}
    columns = {f.id: f.label for f in _fields(db)}
    return {"items": [{"at": e.at.isoformat(), "actorId": e.actor_id, "actorName": actors.get(e.actor_id), "action": e.action,
                       "summary": _history_text(e, columns)} for e in entries]}


# ---------------------------------------------------------------- the ad file

def _discard(key: str | None) -> None:
    """Remove a stored ad after the database no longer points at it; a storage hiccup only leaves an orphan, never an error."""
    if key:
        try:
            document_storage.delete_bytes(get_settings(), key)
        except Exception:
            logger.warning("Could not remove a job requirement ad from storage.")


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:1024].lstrip().startswith(b"%PDF-"):
        return "application/pdf"
    return None


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _file_name(name: str | None, content_type: str) -> str:
    cleaned = _SAFE_NAME.sub("-", (name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip("-. ")[:150]
    return cleaned or "ad." + {"image/jpeg": "jpg", "application/pdf": "pdf"}.get(content_type, content_type.split("/")[-1])


@router.post("/api/job-requirements/{requirement_id}/ad")
def upload_ad(requirement_id: str, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    _row(db, requirement_id)
    declared = (file.content_type or "").split(";", 1)[0].strip().lower()
    if not declared or declared == "application/octet-stream":
        name = file.filename or ""
        declared = AD_EXTENSIONS.get("." + name.rsplit(".", 1)[-1].lower() if "." in name else "", declared)
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared not in AD_TYPES:
        raise HTTPException(415, "Upload a PNG, JPG, WebP, GIF or PDF file.")
    data = file.file.read(MAX_AD_BYTES + 1)
    if not data:
        raise HTTPException(422, "This file is empty.")
    if len(data) > MAX_AD_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB.")
    kind = _sniff(data)
    if kind is None or kind != declared:
        raise HTTPException(415, "This file is not really a " + declared.split("/")[-1].upper() + ". Upload a PNG, JPG, WebP, GIF or PDF file.")
    try:
        key = document_storage.put_bytes(get_settings(), f"job-requirements/{requirement_id}/{uuid.uuid4()}", data, kind)
    except Exception:
        logger.exception("Could not store a job requirement ad")
        raise HTTPException(503, "The file could not be saved. Try again in a moment.")
    row = _row(db, requirement_id, lock=True)
    old = row.ad_object_key
    row.ad_object_key, row.ad_file_name, row.ad_content_type = key, _file_name(file.filename, kind), kind
    row.ad_size_kb = max(1, math.ceil(len(data) / 1024))
    row.updated_by = user.id
    row.updated_at = func.now()
    _audit(db, user, "Replaced job requirement ad" if old else "Added job requirement ad", row, {"file": row.ad_file_name})
    db.commit()
    db.refresh(row)
    _discard(old)
    return _items(db, [row])[0]


@router.get("/api/job-requirements/{requirement_id}/ad")
def download_ad(requirement_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _row(db, requirement_id)
    if not row.ad_object_key:
        raise HTTPException(404, "This job requirement has no ad.")
    try:
        response = document_storage.file_response(get_settings(), row.ad_object_key, row.ad_file_name or "ad", row.ad_content_type or "application/octet-stream")
    except Exception:
        raise HTTPException(404, "The ad file is unavailable.")
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@router.delete("/api/job-requirements/{requirement_id}/ad")
def remove_ad(requirement_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = _row(db, requirement_id, lock=True)
    old = row.ad_object_key
    if old:
        row.ad_object_key = row.ad_file_name = row.ad_content_type = row.ad_size_kb = None
        row.updated_by = user.id
        row.updated_at = func.now()
        _audit(db, user, "Removed job requirement ad", row)
    db.commit()
    db.refresh(row)
    _discard(old)
    return _items(db, [row])[0]


# ---------------------------------------------------------------- recruiter-added columns

def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:70] or "field"


def _label_taken(db: Session, label: str, except_id: str | None = None) -> bool:
    statement = select(JobRequirementField.id).where(func.lower(JobRequirementField.label) == label.lower())
    if except_id:
        statement = statement.where(JobRequirementField.id != except_id)
    return db.scalar(statement.limit(1)) is not None


@router.post("/api/job-requirement-fields", status_code=201)
def add_field(body: FieldIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if not body.label:
        raise HTTPException(422, "Enter a column name.")
    if _label_taken(db, body.label):
        raise HTTPException(409, DUPLICATE)
    base = _slug(body.label)
    key, number = base, 1
    while db.scalar(select(JobRequirementField.id).where(JobRequirementField.key == key)) is not None:
        number += 1
        key = f"{base}_{number}"
    last = db.scalar(select(func.max(JobRequirementField.sort)))
    row = JobRequirementField(id=str(uuid.uuid4()), key=key, label=body.label, type=body.type, sort=(last + 1) if last is not None else 0, created_by=user.id)
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Added job requirement column", target=row.label, entity_type="job_requirement_field",
                      entity_id=row.id, meta={"type": row.type}))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, DUPLICATE)
    return _field(row)


@router.patch("/api/job-requirement-fields/{field_id}")
def rename_field(field_id: str, body: FieldPatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(JobRequirementField).where(JobRequirementField.id == field_id).with_for_update())
    if row is None:
        raise HTTPException(404, "This column was not found.")
    if body.label and body.label != row.label:
        if _label_taken(db, body.label, field_id):
            raise HTTPException(409, DUPLICATE)
        db.add(AuditEntry(actor_id=user.id, action="Renamed job requirement column", target=body.label, entity_type="job_requirement_field",
                          entity_id=row.id, meta={"from": row.label}))
        row.label = body.label
    db.commit()
    return _field(row)


@router.delete("/api/job-requirement-fields/{field_id}", status_code=204)
def delete_field(field_id: str, user: User = Depends(admin), db: Session = Depends(get_db)) -> Response:
    row = db.scalar(select(JobRequirementField).where(JobRequirementField.id == field_id).with_for_update())
    if row is None:
        raise HTTPException(404, "This column was not found.")
    holders = list(db.scalars(select(JobRequirement).where(JobRequirement.custom.has_key(field_id))))
    for holder in holders:
        holder.custom = {key: value for key, value in (holder.custom or {}).items() if key != field_id}
    db.add(AuditEntry(actor_id=user.id, action="Deleted job requirement column", target=row.label, entity_type="job_requirement_field",
                      entity_id=row.id, meta={"valuesRemoved": len(holders)}))
    db.delete(row)
    db.commit()
    return Response(status_code=204)
