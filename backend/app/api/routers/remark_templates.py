"""Remark templates: the ready-made remarks a recruiter picks on the Tasks page instead of typing.

GET    /api/remark-templates[?all=1]  -> {"items": [{"id", "text", "active", "sortOrder"}]}  everyone signed in reads the active ones in
                                         order; an administrator with `all=1` also sees the switched-off ones.
POST   /api/remark-templates {"text"}  administrator only: trimmed, 1-120 characters, appended last (201). Idempotent: a text that exists
                                         (any letter case) returns the existing item with 200 (switched back on if it was off).
PATCH  /api/remark-templates/{id} {"text"?, "active"?, "sortOrder"?}  administrator only.
DELETE /api/remark-templates/{id}      administrator only: switches the template off (it is never deleted: remarks keep their own text).

The remark endpoint itself still takes any text: a template only pre-fills what the recruiter sends. Every administrator change is audited.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_role, require_admin_level
from app.db.models import AuditEntry, RemarkTemplate, Role, User
from app.db.session import get_db
from app.core.roles import is_admin_level
from app.services.response_cache import cached, scope_of

router = APIRouter(prefix="/api/remark-templates", tags=["remark-templates"])
admin = require_admin_level()

MAX_LENGTH = 120
DUPLICATE = "A remark template with this text already exists."
NOT_INSTALLED = "Remark templates are not available yet. Ask your administrator to apply the latest database update."


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = None
    active: bool | None = None
    sortOrder: int | None = None


def _out(row: RemarkTemplate) -> dict:
    return {"id": row.id, "text": row.body, "active": row.active, "sortOrder": row.sort_order}


def _text(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    if not 1 <= len(text) <= MAX_LENGTH:
        raise HTTPException(422, f"Enter the remark text, 1 to {MAX_LENGTH} characters.")
    return text


def _taken(db: Session, text: str, *, except_id: str | None = None) -> bool:
    query = select(RemarkTemplate.id).where(func.lower(RemarkTemplate.body) == text.lower())
    if except_id:
        query = query.where(RemarkTemplate.id != except_id)
    return db.scalar(query) is not None


def _audit(db: Session, user: User, action: str, row: RemarkTemplate, meta: dict | None = None) -> None:
    db.add(AuditEntry(actor_id=user.id, action=action, target=row.body, entity_type="remark_template", entity_id=row.id, meta=meta))


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:   # two administrators adding the same text at once: the unique index decides
        db.rollback()
        raise HTTPException(409, DUPLICATE)


def _locked(db: Session, template_id: str) -> RemarkTemplate:
    row = db.scalar(select(RemarkTemplate).where(RemarkTemplate.id == template_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Remark template not found.")
    return row


@router.get("")
def list_templates(all: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    everything = bool(all) and is_admin_level(user)   # anyone else always gets the active ones only

    query = select(RemarkTemplate).order_by(RemarkTemplate.sort_order, RemarkTemplate.created_at, RemarkTemplate.id)
    if not everything:
        query = query.where(RemarkTemplate.active.is_(True))

    def read() -> dict:
        try:
            return {"items": [_out(row) for row in db.scalars(query)]}
        except ProgrammingError:   # the migration has not been run on this database yet
            db.rollback()
            raise HTTPException(503, NOT_INSTALLED)

    # The same short list for everybody and rarely changed: kept for half a minute and dropped at once by any change to the templates
    # (the "settings" change-feed topic, which every template write announces), so a recruiter's dropdown never waits on the database.
    return cached(("remark-templates", scope_of(db), everything), ("settings",), 30, read)


@router.post("", status_code=201)
def add_template(body: TemplateIn, response: Response, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    """Add a template (201). Idempotent: a text that already exists (any letter case) answers the existing item with 200 and adds
    nothing; if that one was switched off it is switched back on, so the admin's dropdown shows it again."""
    text = _text(body.text)
    existing = _existing(db, text)
    if existing is not None:
        response.status_code = 200
        return _revive(db, user, existing)
    last = db.scalar(select(func.max(RemarkTemplate.sort_order)))
    row = RemarkTemplate(body=text, active=True, sort_order=(last or 0) + 10)
    db.add(row)
    db.flush()
    _audit(db, user, "Added remark template", row)
    try:
        db.commit()
    except IntegrityError:   # two administrators adding the same text at once: the unique index decides, the loser gets the winner's row
        db.rollback()
        existing = _existing(db, text)
        if existing is None:
            raise
        response.status_code = 200
        return _out(existing)
    return _out(row)


def _existing(db: Session, text: str) -> RemarkTemplate | None:
    return db.scalar(select(RemarkTemplate).where(func.lower(RemarkTemplate.body) == text.lower()).with_for_update())


def _revive(db: Session, user: User, row: RemarkTemplate) -> dict:
    if not row.active:
        row.active = True
        _audit(db, user, "Switched on remark template", row)
        _commit(db)
    return _out(row)


@router.patch("/{template_id}")
def change_template(template_id: str, body: TemplateIn, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    row = _locked(db, template_id)
    changes: dict = {}
    if body.text is not None:
        text = _text(body.text)
        if text != row.body:
            if _taken(db, text, except_id=row.id):
                raise HTTPException(409, DUPLICATE)
            changes["text"] = {"from": row.body, "to": text}
            row.body = text
    if body.active is not None and body.active != row.active:
        changes["active"] = body.active
        row.active = body.active
    if body.sortOrder is not None and body.sortOrder != row.sort_order:
        changes["sortOrder"] = body.sortOrder
        row.sort_order = body.sortOrder
    if changes:
        _audit(db, user, "Updated remark template", row, {"changes": changes})
        _commit(db)
    return _out(row)


@router.delete("/{template_id}")
def deactivate_template(template_id: str, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    """Switch the template off. It stays in the table (an administrator can switch it back on), so nothing that used it breaks."""
    row = _locked(db, template_id)
    if row.active:
        row.active = False
        _audit(db, user, "Switched off remark template", row)
        _commit(db)
    return _out(row)
