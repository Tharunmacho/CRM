"""Sign in, sign out, and who-am-I.

Sessions are rows, not JWTs, so an admin's "force logout" can actually revoke one.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.db.session import get_db
from app.core.deps import current_user
from app.db.models import Role, Session as SessionRow, User
from app.core.security import (
    SESSION_COOKIE,
    hash_password,
    needs_rehash,
    session_expiry,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    # Match the submitted address against the database after normalizing it.
    email: str
    password: str


class Me(BaseModel):
    id: str
    name: str
    email: str
    role: Role
    active: bool


@router.post("/login", response_model=Me)
def login(body: LoginIn, response: Response, db: DbSession = Depends(get_db)) -> Me:
    # Credentials and role assignments come exclusively from the stored account.
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))

    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"field": "email", "message": "No account found for this email address."},
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"field": "password", "message": "Password is incorrect."},
        )

    if not user.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")

    if needs_rehash(user.password_hash or ""):
        user.password_hash = hash_password(body.password)

    row = SessionRow(user_id=user.id, expires_at=session_expiry())
    db.add(row)
    db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        row.id,
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=int((row.expires_at - row.created_at).total_seconds())
        if row.created_at
        else 60 * 60 * 24 * 14,
        path="/",
    )
    return Me(id=user.id, name=user.name, email=user.email, role=user.role, active=user.active)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def logout(
    response: Response,
    db: DbSession = Depends(get_db),
    crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> None:
    if crm_session:
        row = db.get(SessionRow, crm_session)
        if row is not None:
            db.delete(row)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=Me)
def me(user: User = Depends(current_user)) -> Me:
    return Me(id=user.id, name=user.name, email=user.email, role=user.role, active=user.active)
