"""Admin-only account lifecycle and real audit history."""
from __future__ import annotations

from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.schemas import UserOut, Wire
from app.core.deps import require_role
from app.db.models import AuditEntry, Role, User
from app.db.session import get_db
from app.services.parallel_reads import parallel_reads
from app.services import user_admin

router = APIRouter(prefix='/api/users', tags=['users'])
admin = require_role(Role.admin)


class UserFields(Wire):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    role: Role = Role.staff
    active: bool = True
    receives_auto_assign: bool = True
    on_leave: bool = False

    @field_validator('name', mode='before')
    @classmethod
    def name_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('email', mode='before')
    @classmethod
    def email_text(cls, value):
        if not isinstance(value, str):
            raise ValueError('Enter a valid email address.')
        value = value.strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address.')
        return value


class CreateUserIn(UserFields):
    password: str = Field(min_length=12, max_length=128)


class UpdateUserIn(Wire):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    role: Role | None = None
    active: bool | None = None
    receives_auto_assign: bool | None = None
    on_leave: bool | None = None
    reassign_to_user_id: str | None = Field(default=None, max_length=36)

    @field_validator('name', mode='before')
    @classmethod
    def name_text(cls, value):
        return UserFields.name_text(value)

    @field_validator('email', mode='before')
    @classmethod
    def email_text(cls, value):
        return UserFields.email_text(value)


class PasswordIn(Wire):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=12, max_length=128)


@router.get('', response_model=list[UserOut])
def list_users(response: Response, db: Session = Depends(get_db), user: User = Depends(admin)):
    response.headers['Cache-Control'] = 'no-store'
    return list(db.scalars(select(User).order_by(User.name, User.id)))


@router.post('', response_model=UserOut, status_code=201)
def create(body: CreateUserIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    return user_admin.create_user(db, user, body.model_dump())


@router.patch('/{user_id}', response_model=UserOut)
def update(user_id: str, body: UpdateUserIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    values = body.model_dump(exclude_unset=True)
    if any(value is None and key not in ('phone', 'reassign_to_user_id') for key, value in values.items()):
        raise HTTPException(422, 'Name, email, role and availability cannot be left empty.')
    return user_admin.update_user(db, user, user_id, values)


@router.post('/{user_id}/password')
def password(user_id: str, body: PasswordIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    return user_admin.reset_password(db, user, user_id, body.password)


@router.post('/{user_id}/force-logout')
def logout(user_id: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    return user_admin.force_logout(db, user, user_id)


@router.get('/{user_id}/audit')
def audit(user_id: str, response: Response, limit: int = Query(50, ge=1, le=100),
          before: str | None = None, db: Session = Depends(get_db), user: User = Depends(admin)):
    statement = select(AuditEntry).where(or_(AuditEntry.actor_id == user_id,
        (AuditEntry.entity_type == 'user') & (AuditEntry.entity_id == user_id)))
    if before is not None:
        try:
            timestamp, identifier = before.rsplit('|', 1)
            timestamp = datetime.fromisoformat(timestamp)
            if not identifier or len(identifier) > 36:
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, 'We could not load more history. Reload the page and try again.')
        statement = statement.where(or_(AuditEntry.at < timestamp,
            (AuditEntry.at == timestamp) & (AuditEntry.id < identifier)))
    known, rows = parallel_reads(
        db,
        lambda session: session.scalar(select(User.id).where(User.id == user_id)) is not None,
        lambda session: list(session.scalars(statement.order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit + 1))),
    )
    if not known:
        raise HTTPException(404, 'User not found.')
    response.headers['Cache-Control'] = 'no-store'
    return {'items': [{'id': row.id, 'actorId': row.actor_id, 'action': row.action,
        'target': row.target, 'entityType': row.entity_type, 'entityId': row.entity_id,
        'meta': row.meta or {}, 'at': row.at.isoformat()} for row in rows[:limit]],
        'nextBefore': f'{rows[limit - 1].at.isoformat()}|{rows[limit - 1].id}' if len(rows) > limit else None}
