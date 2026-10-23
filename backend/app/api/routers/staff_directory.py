"""Supervisor staff operations; account/security management remains Admin-only."""
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import require_role
from app.db.models import Role, User
from app.db.session import get_db
from app.services import staff_directory as directory

router = APIRouter(prefix="/api/staff-directory", tags=["staff-directory"])
supervisor = require_role(Role.admin, Role.manager)


@router.get("/availability")
def availability(response: Response, user: User = Depends(supervisor), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    return directory.staff_availability(db, get_settings().agency_timezone)


@router.get("")
def staff_directory(response: Response, from_date: date | None = Query(None, alias="from"),
                    to_date: date | None = Query(None, alias="to"),
                    user: User = Depends(supervisor), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    return directory.staff_directory(db, get_settings().agency_timezone, from_date, to_date)
