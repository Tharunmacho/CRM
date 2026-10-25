"""Monthly attendance, approvals and scheduling; all writes are transactional."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services import attendance_hr as hr
from app.services.attendance_policy_types import AttendanceStatus, Shift

router = APIRouter(prefix="/attendance", tags=["attendance-policy"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequestInput(Input):
    requestKey: UUID
    date: date
    kind: Literal["late", "early_exit", "early_check_in", "official_duty", "work_from_home", "paid_leave", "unpaid_leave", "weekly_off", "extra_ot"]
    minutes: int = Field(default=0, ge=0, le=1440)
    reason: str = Field(min_length=3, max_length=1000)
    coverUserId: str | None = Field(default=None, max_length=36)


class DecisionInput(Input):
    approved: bool
    reason: str = Field(min_length=3, max_length=1000)
    emergencyOverride: bool = False


class CoverResponseInput(Input):
    accepted: bool
    note: str = Field(default="", max_length=500)


class CoverInput(Input):
    coverUserId: str = Field(min_length=1, max_length=36)


class WeeklyOffInput(Input):
    weekStart: date
    day: Literal["sunday", "friday"]


class ShiftInput(Input):
    start: str = Field(default="10:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(default="19:00", pattern=r"^\d{2}:\d{2}$")
    breakMinutes: int = Field(default=60, ge=0, le=480)

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, value):
        from datetime import time
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def valid_span(self):
        Shift(start=self.start, end=self.end, break_minutes=self.breakMinutes)
        return self


class EmployeeChange(Input):
    userId: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=1000)


class DutyPlanInput(EmployeeChange):
    date: date
    shift: ShiftInput


class TimingInput(EmployeeChange):
    effectiveFrom: date
    shift: ShiftInput


class CalendarInput(EmployeeChange):
    date: date
    status: Literal["WO", "H", "PL", "UL"]


class AdjustmentInput(EmployeeChange):
    date: date
    kind: Literal["add_punch", "time_recovery", "status_override"]
    status: AttendanceStatus | None = None
    minutes: int = Field(default=0, ge=0, le=1440)
    action: Literal["check_in", "check_out"] | None = None
    occurredAt: datetime | None = None

    @field_validator("occurredAt")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Add a time zone to the attendance time.")
        return value


def _write(db, call, *args, **kwargs):
    result = call(db, *args, **kwargs)
    db.commit()
    return result


@router.get("/month")
def month(response: Response, year: int, month: int, staffId: str | None = None,
          db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return hr.month_report(db, user, year, month, staffId)


@router.get("/requests")
def requests(response: Response, year: int, month: int, staffId: str | None = None,
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return hr.request_report(db, user, year, month, staffId)


@router.post("/requests", status_code=201)
def request(body: RequestInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.create_request, user, body.model_dump())


@router.post("/requests/{request_id}/decision")
def decide(request_id: str, body: DecisionInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.decide_request, user, request_id, body.model_dump())


@router.post("/requests/{request_id}/cover-response")
def cover_response(request_id: str, body: CoverResponseInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.respond_cover, user, request_id, body.model_dump())


@router.post("/requests/{request_id}/cover")
def cover(request_id: str, body: CoverInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.nominate_cover, user, request_id, body.model_dump())


@router.get("/settings")
def settings(response: Response, year: int, month: int, staffId: str | None = None,
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return hr.settings_report(db, user, year, month, staffId)


@router.put("/weekly-off")
def weekly_off(body: WeeklyOffInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.set_weekly_off, user, body.model_dump())


@router.post("/duty-plans", status_code=201)
def duty_plan(body: DutyPlanInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.save_duty_plan, user, body.model_dump())


@router.delete("/duty-plans/{plan_id}")
def remove_duty_plan(plan_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.delete_duty_plan, user, plan_id)


@router.post("/work-timings", status_code=201)
def timing(body: TimingInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.save_work_timing, user, body.model_dump())


@router.post("/calendar", status_code=201)
def calendar_day(body: CalendarInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.save_calendar_day, user, body.model_dump())


@router.post("/adjustments", status_code=201)
def adjustment(body: AdjustmentInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _write(db, hr.create_adjustment, user, body.model_dump())


@router.get("/adjustments")
def adjustments(response: Response, year: int, month: int, staffId: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return hr.adjustment_report(db, user, year, month, staffId)
