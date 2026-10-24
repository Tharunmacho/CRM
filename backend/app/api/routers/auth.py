"""Sign in, sign out, and who-am-I.

Sessions are rows, not JWTs, so an admin's "force logout" can actually revoke one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import UserOut
from app.db.session import get_db
from app.core.deps import current_user, remember_signin
from app.db.models import Role, Session as SessionRow, User
from app.core.security import (
    SESSION_COOKIE,
    SESSION_TTL,
    hash_password,
    needs_rehash,
    session_expiry,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A readable (not HttpOnly) hint of WHO is signed in: the user id and nothing else. The session cookie is
# HttpOnly, so the page cannot tell whose account the browser holds until the session request returns.
# The front end paints a remembered workspace instantly only when this id matches the one it stored, which
# keeps another person's data off the screen after a sign-out or account switch in a different tab. It is
# not a credential: it grants nothing and the server never trusts it.
USER_HINT_COOKIE = "crm_uid"


def _set_user_hint(response: Response, user_id: str) -> None:
    response.set_cookie(USER_HINT_COOKIE, user_id, httponly=False, samesite="lax", secure=True,
                        max_age=int(SESSION_TTL.total_seconds()), path="/")


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


@router.get("/session")
def session_state(
    response: Response,
    db: DbSession = Depends(get_db),
    crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    user_hint: str | None = Cookie(default=None, alias=USER_HINT_COOKIE),
    workspace: bool = False,
    compact: bool = False,
) -> dict[str, Any]:
    """Return the current account, or an anonymous state without an auth error."""
    response.headers["Cache-Control"] = "no-store"
    if not crm_session:
        if user_hint:
            response.delete_cookie(USER_HINT_COOKIE, path="/")
        return {"me": None}
    try:
        user = current_user(db=db, crm_session=crm_session)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            if user_hint:
                response.delete_cookie(USER_HINT_COOKIE, path="/")
            return {"me": None}
        raise
    if user_hint != user.id:
        _set_user_hint(response, user.id)  # accounts that signed in before the hint existed
    if workspace:
        # Authenticate once and return the desk in the same response. Anonymous
        # visits still return immediately without loading any CRM collections.
        from app.api.routers.data import bootstrap
        return bootstrap(db=db, user=user, compact=compact)
    return {"me": UserOut.model_validate(user)}


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
    _set_user_hint(response, user.id)
    # The commit above emptied the sign-in memo; seed it so this session's first request skips the lookup.
    remember_signin(row.id, row.expires_at, user)
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
        from app.services.staff_activity import logout_session
        logout_session(db, crm_session)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(USER_HINT_COOKIE, path="/")


@router.get("/me", response_model=Me)
def me(user: User = Depends(current_user)) -> Me:
    return Me(id=user.id, name=user.name, email=user.email, role=user.role, active=user.active)
