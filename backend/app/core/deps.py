"""Request dependencies: who is signed in, and what they may touch."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import SESSION_COOKIE
from app.db.models import Application, Candidate, Role, Session as SessionRow, User
from app.db.session import get_db


def current_user(
    db: Session = Depends(get_db),
    crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    if not crm_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    row = db.scalar(select(SessionRow).where(SessionRow.id == crm_session))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session not found")

    if row.expires_at <= datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    user = db.get(User, row.user_id)
    if user is None or not user.active:
        # Deactivating an account takes effect immediately, including for anyone
        # already holding a session.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is not active")

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
