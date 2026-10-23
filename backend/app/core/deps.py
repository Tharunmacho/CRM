"""Request dependencies: who is signed in, and what they may touch."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, make_transient_to_detached

from app.core.security import SESSION_COOKIE
from app.db.models import Application, Candidate, Role, Session as SessionRow, User
from app.db.session import get_db


# Who is signed in is looked up on every request, which costs a full database round trip (150-350 ms
# against a hosted database). A short-lived in-process memo removes it from the hot path. Any write to
# the account or session tables, from any code path in this process, empties it first, so sign-out,
# deactivation, role changes and force-logout still take effect at once; another process (the CLI,
# a second server) is picked up within AUTH_CACHE_SECONDS.
AUTH_CACHE_SECONDS = 10
_AUTH_CACHE_LIMIT = 500
_AUTH_TABLES = ("auth_sessions", "auth_users")
_AUTH_CACHE: dict[str, tuple[float, datetime, dict]] = {}


@event.listens_for(Engine, "before_cursor_execute")
def _forget_signins_on_account_writes(conn, cursor, statement, parameters, context, executemany):
    head = statement.lstrip()[:6].upper()
    if head in {"INSERT", "UPDATE", "DELETE"} and any(table in statement for table in _AUTH_TABLES):
        _AUTH_CACHE.clear()
    elif head in {"DROP S", "TRUNCA"}:
        _AUTH_CACHE.clear()


def _snapshot(user: User) -> dict:
    return {attr.key: getattr(user, attr.key) for attr in inspect(User).column_attrs}


def _restore(db: Session, values: dict) -> User:
    user = User(**values)
    make_transient_to_detached(user)
    return db.merge(user, load=False)


def current_user(
    db: Session = Depends(get_db),
    crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    if not crm_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    remembered = _AUTH_CACHE.get(crm_session)
    if remembered and time.monotonic() - remembered[0] < AUTH_CACHE_SECONDS and remembered[1] > datetime.now(timezone.utc):
        return _restore(db, remembered[2])

    result = db.execute(
        select(SessionRow, User)
        .outerjoin(User, User.id == SessionRow.user_id)
        .where(SessionRow.id == crm_session)
    ).one_or_none()
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session not found")

    row, user = result
    if row.expires_at <= datetime.now(timezone.utc):
        from app.services.staff_activity import logout_session
        logout_session(db, row.id)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    if user is None or not user.active:
        # Deactivating an account takes effect immediately, including for anyone
        # already holding a session.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is not active")

    if len(_AUTH_CACHE) >= _AUTH_CACHE_LIMIT:
        _AUTH_CACHE.clear()
    _AUTH_CACHE[crm_session] = (time.monotonic(), row.expires_at, _snapshot(user))
    return user


def require_role(*roles: Role):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Your role does not allow this action"
            )
        return user

    return dependency


def ensure_can_work(db: Session, app: Application, user: User) -> None:
    """
    Admin and manager may work any candidate's stages; staff only their own. Enforced
    here as well as in the UI, because the UI is only a suggestion.
    """
    if user.role in (Role.admin, Role.manager):
        return

    candidate = db.get(Candidate, app.candidate_id)
    if candidate is None or candidate.owner_id != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This candidate belongs to another recruiter.",
        )
