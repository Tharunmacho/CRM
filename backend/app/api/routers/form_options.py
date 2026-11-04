"""Admin-managed option lists for the candidate / application form, plus data-derived suggestion lists.

Everyone signed in reads; only an administrator changes. See docs/workflows/application-form-contract.md section 12.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, FormOption, Role, User
from app.db.session import get_db
from app.services import form_options as service

router = APIRouter(prefix="/api/form-options", tags=["form-options"])
admin = require_role(Role.admin)


def _clean(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


class OptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(max_length=40)
    value: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)
    hint: str | None = Field(default=None, max_length=255)
    tone: Literal["neutral", "success", "info", "warning", "danger"] | None = None
    sortOrder: int | None = Field(default=None, ge=0, le=100000)

    @field_validator("value", "label", "hint")
    @classmethod
    def tidy(cls, value):
        return _clean(value)


class OptionPatch(BaseModel):
    """`value` is not here on purpose: it is what candidates store and never changes."""
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, min_length=1, max_length=120)
    hint: str | None = Field(default=None, max_length=255)
    tone: Literal["neutral", "success", "info", "warning", "danger"] | None = None
    sortOrder: int | None = Field(default=None, ge=0, le=100000)
    isActive: bool | None = None

    @field_validator("label", "hint")
    @classmethod
    def tidy(cls, value):
        return _clean(value)


class ReorderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(max_length=40)
    ids: list[str] = Field(min_length=1, max_length=200)


def _audit(db: Session, user: User, action: str, row: FormOption, meta: dict | None = None) -> None:
    db.add(AuditEntry(actor_id=user.id, action=action, target=f"{row.category}: {row.label}", entity_type="form_option",
                      entity_id=row.id, meta={"category": row.category, "value": row.value, **(meta or {})}))


def _get(db: Session, option_id: str) -> FormOption:
    row = db.get(FormOption, option_id)
    if row is None:
        raise HTTPException(404, "Option not found")
    return row


@router.get("")
def read_options(request: Request, response: Response, includeInactive: bool = Query(default=False),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Every active option grouped by category (an administrator may ask for the hidden ones too). ETag-friendly."""
    if includeInactive and user.role != Role.admin:
        raise HTTPException(403, "Only an administrator can list hidden options.")
    payload = service.all_options(db, include_inactive=includeInactive)
    tag = service.etag_of(payload)
    headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return payload


@router.get("/suggestions")
def read_suggestions(response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Lists built from the data already in the system: countries, job preferences, skills, institutes, previous employers."""
    response.headers["Cache-Control"] = "private, max-age=60"
    return service.suggestions(db)


@router.post("", status_code=201)
def create_option(body: OptionIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    meta = service.CATEGORIES.get(body.category)
    if meta is None:
        raise HTTPException(422, "Unknown list.")
    if meta["fixed"]:
        raise HTTPException(422, f"{meta['label']} has fixed values; you can rename, reorder or hide them but not add one.")
    if not service.VALUE_PATTERN.match(body.value):
        raise HTTPException(422, "Use letters, numbers and simple punctuation in the value (up to 60 characters).")
    if db.scalar(select(FormOption.id).where(FormOption.category == body.category, FormOption.value == body.value)):
        raise HTTPException(409, "That value already exists in this list. If it is hidden, show it again instead.")
    top = db.scalar(select(FormOption.sort_order).where(FormOption.category == body.category).order_by(FormOption.sort_order.desc()).limit(1)) or 0
    row = FormOption(category=body.category, value=body.value, label=body.label, hint=body.hint, tone=body.tone,
                     sort_order=body.sortOrder if body.sortOrder is not None else top + 10, is_active=True, is_system=False)
    db.add(row)
    db.flush()
    _audit(db, user, "Added a form option", row)
    out = service.option_out(row)
    db.commit()
    return out


@router.put("/{option_id}")
def update_option(option_id: str, body: OptionPatch, db: Session = Depends(get_db), user: User = Depends(admin)):
    row = _get(db, option_id)
    changes = body.model_dump(exclude_unset=True)
    if (row.category, row.value) in service.PROTECTED and changes.get("isActive") is False:
        raise HTTPException(422, "This choice is the default and cannot be hidden.")
    for key, column in (("label", "label"), ("hint", "hint"), ("tone", "tone"), ("sortOrder", "sort_order"), ("isActive", "is_active")):
        if key in changes and (changes[key] is not None or key in ("hint", "tone")):
            setattr(row, column, changes[key])
    _audit(db, user, "Changed a form option", row, {"changed": sorted(changes)})
    out = service.option_out(row)
    db.commit()
    return out


@router.post("/reorder")
def reorder_options(body: ReorderIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    """Give the listed options of one category the order of `ids` (ids not listed keep their place after them)."""
    if body.category not in service.CATEGORIES:
        raise HTTPException(422, "Unknown list.")
    rows = {row.id: row for row in db.scalars(select(FormOption).where(FormOption.category == body.category))}
    if len(set(body.ids)) != len(body.ids) or set(body.ids) - set(rows):
        raise HTTPException(422, "The order contains an option that is not in this list.")
    for position, option_id in enumerate(body.ids, start=1):
        rows[option_id].sort_order = position * 10
    rest = sorted((row for row in rows.values() if row.id not in set(body.ids)), key=lambda row: (row.sort_order, row.label))
    for position, row in enumerate(rest, start=len(body.ids) + 1):
        row.sort_order = position * 10
    db.add(AuditEntry(actor_id=user.id, action="Reordered a form list", target=service.CATEGORIES[body.category]["label"],
                      entity_type="form_option", entity_id=None, meta={"category": body.category}))
    db.commit()
    return service.all_options(db, include_inactive=True)


@router.delete("/{option_id}")
def remove_option(option_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    """Delete an unused option you added; hide (deactivate) one that is in use or came with the product."""
    row = _get(db, option_id)
    if (row.category, row.value) in service.PROTECTED:
        raise HTTPException(422, "This choice is the default and cannot be removed.")
    used = service.usage_count(db, row)
    if row.is_system or used:
        row.is_active = False
        _audit(db, user, "Hid a form option", row, {"inUse": used})
        out = {"deleted": False, "deactivated": True, "inUse": used, "option": service.option_out(row)}
    else:
        _audit(db, user, "Deleted a form option", row)
        out = {"deleted": True, "deactivated": False, "inUse": 0, "option": service.option_out(row)}
        db.delete(row)
    db.commit()
    return out
