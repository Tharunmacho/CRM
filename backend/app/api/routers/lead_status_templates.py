"""Lead status templates: the statuses recruiters add to the Lead sheet's status list ("+ Add a status"), shared by every user.

GET    /api/lead-status-templates[?all=1]  -> {"items": [{"id", "name", "tone", "active", "builtin", "sortOrder", "countsAs"}]}  anybody signed in
                                              reads the active ones (built-in first, then alphabetical); an admin or MD with `all=1` also gets hidden ones.
POST   /api/lead-status-templates {"name", "tone"?}  anybody signed in: trimmed, 1-40 characters, no comma, tone neutral (default) / positive /
                                              warning / negative (201). A name that exists in any letter case, or that is one of the standard
                                              statuses, is refused with 409 (the body carries the existing item when there is one).
PATCH  /api/lead-status-templates/{id} {"name"?, "active"?, "sortOrder"?}  admin or MD: rename (the leads holding it follow), hide / show.
                                              A built-in template cannot be renamed.
DELETE /api/lead-status-templates/{id}  admin or MD: removes a custom template. A built-in one is never deleted (409). Leads that hold it
                                              keep showing its text.

How a template counts in the reports: services/lead_status_templates.py. Every change is audited.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_admin_level
from app.core.roles import is_admin_level
from app.db.models import AuditEntry, LeadStatusTemplate, User
from app.db.session import get_db
from app.services import lead_status_templates as templates
from app.services.lead_status_templates import fail, template_out

router = APIRouter(prefix="/api/lead-status-templates", tags=["lead-status-templates"])
admin = require_admin_level()

NOT_INSTALLED = "Status templates are not available yet. Ask your administrator to apply the latest database update."


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    tone: str | None = None
    active: bool | None = None
    sortOrder: int | None = None


def _audit(db: Session, user: User, action: str, row: LeadStatusTemplate, meta: dict | None = None) -> None:
    db.add(AuditEntry(actor_id=user.id, action=action, target=row.name, entity_type="lead_status_template", entity_id=row.id, meta=meta))


def _duplicate(row: LeadStatusTemplate):
    return fail(409, "status_exists", f"A status named \"{row.name}\" already exists" + ("" if row.active else " but is switched off. Ask an admin to switch it on."),
                existing=template_out(row))


def _locked(db: Session, template_id: str) -> LeadStatusTemplate:
    row = db.scalar(select(LeadStatusTemplate).where(LeadStatusTemplate.id == template_id).with_for_update())
    if row is None:
        raise fail(404, "not_found", "Status template not found.")
    return row


@router.get("")
def list_templates(all: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    try:
        rows = templates.list_templates(db, include_hidden=bool(all) and is_admin_level(user))  # anyone else always gets the active ones
        return {"items": [template_out(row) for row in rows]}
    except ProgrammingError:   # the migration has not been run on this database yet
        db.rollback()
        raise fail(503, "not_installed", NOT_INSTALLED)


@router.post("", status_code=201)
def add_template(body: TemplateIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    name = templates.clean_name(body.name)
    tone = templates.clean_tone(body.tone)
    existing = templates.by_name(db, name)
    if existing is not None:
        raise _duplicate(existing)
    last = db.scalar(select(func.max(LeadStatusTemplate.sort_order)).where(LeadStatusTemplate.builtin.is_(False)))
    row = LeadStatusTemplate(name=name, tone=tone, active=True, builtin=False, sort_order=max(last or 0, 100) + 10, created_by=user.id)
    db.add(row)
    db.flush()
    _audit(db, user, "Added lead status", row, {"tone": tone})
    try:
        db.commit()
    except IntegrityError:   # two people adding the same name at once: the unique index decides, the loser gets the winner's row
        db.rollback()
        existing = templates.by_name(db, name)
        if existing is None:
            raise
        raise _duplicate(existing)
    return template_out(row)


@router.patch("/{template_id}")
def change_template(template_id: str, body: TemplateIn, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    if body.tone is not None:
        raise fail(422, "invalid_request", "The tone of a status cannot be changed: it decides how leads that hold it were counted. Add a new status instead.")
    row = _locked(db, template_id)
    changes: dict = {}
    if body.name is not None:
        name = templates.clean_name(body.name)
        if name != row.name:
            if row.builtin:
                raise fail(409, "builtin_status", "A built-in status cannot be renamed.")
            clash = templates.by_name(db, name)
            if clash is not None and clash.id != row.id:
                raise _duplicate(clash)
            changes["name"] = {"from": row.name, "to": name}
            db.execute(text("UPDATE crm_leads SET status_label = :new, updated_at = now() WHERE lower(status_label) = lower(:old)"),
                       {"new": name, "old": row.name})   # the leads that hold it follow the rename
            row.name = name
    if body.active is not None and body.active != row.active:
        changes["active"] = body.active
        row.active = body.active
    if body.sortOrder is not None and body.sortOrder != row.sort_order:
        changes["sortOrder"] = body.sortOrder
        row.sort_order = body.sortOrder
    if changes:
        _audit(db, user, "Updated lead status", row, {"changes": changes})
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise fail(409, "status_exists", "A status with this name already exists.")
    return template_out(row)


@router.delete("/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    row = _locked(db, template_id)
    if row.builtin:
        raise fail(409, "builtin_status", "A built-in status can never be deleted. Switch it off instead.")
    in_use = db.scalar(text("SELECT count(*) FROM crm_leads WHERE lower(status_label) = lower(:n)"), {"n": row.name}) or 0
    out = template_out(row)
    _audit(db, user, "Deleted lead status", row, {"leadsKeepingIt": int(in_use)})
    db.delete(row)
    db.commit()
    return {**out, "deleted": True, "leadsKeepingIt": int(in_use)}
