"""Staff attendance and visible online/idle/offline history."""
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.core.security import SESSION_COOKIE
from app.db.models import Role, User
from app.db.session import get_db
from app.services import staff_activity as activity

router = APIRouter(prefix="/api", tags=["attendance"])
staff = require_role(Role.staff)


class CheckIn(BaseModel):
    requestKey: UUID


class CheckOut(CheckIn):
    sessionId: str = Field(min_length=1, max_length=36)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientId: UUID
    sequence: int = Field(ge=1, le=9007199254740991)
    visible: bool
    interactionAgeSeconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)


class Leave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientId: UUID
    sequence: int = Field(ge=1, le=9007199254740991)


class Correction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    checkedInAt: datetime
    checkedOutAt: datetime | None = None
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("checkedInAt", "checkedOutAt")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None: raise ValueError("Include the attendance timezone")
        return value


def own_summary(db, user):
    zone = get_settings().agency_timezone
    result = activity.attendance_report(db, user, activity.utcnow().astimezone(ZoneInfo(zone)).date(), zone, user.id)
    return result["staff"][0]


@router.get("/attendance")
def report(response: Response, date: date | None = None, staffId: str | None = None,
           db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    zone = get_settings().agency_timezone
    day = date or activity.utcnow().astimezone(ZoneInfo(zone)).date()
    return activity.attendance_report(db, user, day, zone, staffId)


@router.post("/attendance/check-in")
def check_in(body: CheckIn, db: Session = Depends(get_db), user: User = Depends(staff),
             crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    activity.check_in(db, user, str(body.requestKey), auth_session_id=crm_session); db.commit()
    return own_summary(db, user)


@router.post("/attendance/check-out")
def check_out(body: CheckOut, db: Session = Depends(get_db), user: User = Depends(staff),
              crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    activity.check_out(db, user, body.sessionId, str(body.requestKey), auth_session_id=crm_session); db.commit()
    return own_summary(db, user)


@router.patch("/attendance/sessions/{session_id}")
def correct(session_id: str, body: Correction, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    row = activity.correct_session(db, user, session_id, body.checkedInAt, body.checkedOutAt, body.reason)
    db.commit()
    return {"id": row.id, "checkedInAt": row.checked_in_at, "checkedOutAt": row.checked_out_at, "correctionReason": row.correction_reason}


@router.post("/presence/heartbeat")
def heartbeat(body: Heartbeat, db: Session = Depends(get_db), user: User = Depends(staff),
              crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    activity.heartbeat(db, user, str(body.clientId), crm_session, body.visible, body.interactionAgeSeconds, sequence=body.sequence)
    db.commit()
    return own_summary(db, user)["presence"]


@router.post("/presence/leave")
def leave(body: Leave, db: Session = Depends(get_db), user: User = Depends(staff),
          crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    activity.heartbeat(db, user, str(body.clientId), crm_session, False, None, sequence=body.sequence)
    db.commit()
    return {"ok": True}
