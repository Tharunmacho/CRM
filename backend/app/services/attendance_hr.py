"""Transactional attendance policy, requests, calendars and leave-cover workflows.

Reference business rules are calculated independently from browser presence. All
HR writes take the assignment cursor before employee locks, matching account
administration and assignment. The caller owns the transaction and commit.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import Date, and_, cast, func, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.models import (
    AssignmentPointer, AttendanceAdjustment, AttendanceCalendarDay,
    AttendanceCoverItem, AttendanceDutyPlan, AttendanceHRRequest,
    AttendanceWeeklyOff, AttendanceWorkTiming, AuditEntry, Candidate,
    Notification, Role, StaffAttendanceSession, Task, TaskEvent, User,
)
from app.services.attendance_policy import AttendancePolicy, attendance_overview, calculate_day, calculate_month, local_day, shift_bounds
from app.services.attendance_policy_types import AttendanceStatus, Shift
from app.services.attendance_availability import unavailable_staff_ids
from app.services.parallel_reads import parallel_reads

EMPLOYEE_ROLES = (Role.staff, Role.manager)
LEAVE_KINDS = {"paid_leave", "unpaid_leave"}
MONEY_KINDS = {*LEAVE_KINDS, "extra_ot"}
HOUR_KINDS = {"late", "early_exit", "early_check_in", "extra_ot"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def utcnow():
    return datetime.now(timezone.utc)


def policy():
    return AttendancePolicy(timezone_name=get_settings().agency_timezone)


def policy_json():
    p = policy()
    return {"workMinutes": 480, "breakMinutes": 60, "monthlyPaidLeaveDays": 1,
            "monthlyGraceMinutes": p.monthly_paid_minutes, "monthlyPermissionOccasions": p.monthly_paid_occasions,
            "warningMinutes": p.warning_minutes, "missingPunchDeadlineDays": p.missing_punch_deadline_days,
            "flexMinutes": p.flex_minutes, "approvalRoles": {"manager": "Manager or Admin", "admin": "Admin"},
            "coverageBasis": "first_check_in_to_last_check_out_less_break"}


def period(year, month):
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise HTTPException(422, "Choose a valid attendance month between 2000 and 2100")
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def _audit(db, actor, action, row, meta=None):
    db.add(AuditEntry(actor_id=actor.id if actor else None, action=action, entity_type="attendance",
                      entity_id=row.id, meta=meta))


def _notify(db, user_id, title, detail):
    db.add(Notification(user_id=user_id, kind="attendance", title=title, detail=detail, link_to="/attendance"))


def _lock(db, actor=None):
    db.execute(insert(AssignmentPointer).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    db.scalar(select(AssignmentPointer).where(AssignmentPointer.id == 1).with_for_update())
    users = list(db.scalars(select(User).order_by(User.id).with_for_update().execution_options(populate_existing=True)))
    if actor is not None and not any(u.id == actor.id and u.active for u in users):
        raise HTTPException(403, "Your account is no longer active")
    return users


def _visible(actor, employee):
    if employee.role not in EMPLOYEE_ROLES:
        return False
    return actor.role == Role.admin or actor.id == employee.id or (actor.role == Role.manager and employee.role == Role.staff)


def _employee(db, actor, user_id, *, manage=False, active=False):
    person = db.get(User, user_id)
    if person is None or not _visible(actor, person) or (active and not person.active):
        raise HTTPException(404, "Attendance employee not found")
    if manage and (actor.role == Role.staff or actor.id == person.id):
        raise HTTPException(403, "A different Manager or Admin must make this attendance change")
    return person


def _people(db, actor, staff_id=None):
    if staff_id:
        return [_employee(db, actor, staff_id)]
    statement = select(User).where(User.role.in_(EMPLOYEE_ROLES))
    if actor.role == Role.staff:
        statement = statement.where(User.id == actor.id)
    elif actor.role == Role.manager:
        statement = statement.where(or_(User.role == Role.staff, User.id == actor.id))
    return list(db.scalars(statement.order_by(User.name, User.id)))


def _shift_value(value):
    value = value or {}
    return Shift(start=value.get("start", "10:00"), end=value.get("end", "19:00"),
                 break_minutes=value.get("breakMinutes", value.get("break_minutes", 60)))


def _shift_json(value):
    return {"start": value.start.strftime("%H:%M"), "end": value.end.strftime("%H:%M"), "breakMinutes": value.break_minutes}


def _shift_for(db, user_id, day):
    plan = db.scalar(select(AttendanceDutyPlan).where(AttendanceDutyPlan.user_id == user_id, AttendanceDutyPlan.attendance_date == day))
    if plan:
        return _shift_value(plan.shift), True
    timing = db.scalar(select(AttendanceWorkTiming).where(AttendanceWorkTiming.user_id == user_id,
        AttendanceWorkTiming.effective_from <= day).order_by(AttendanceWorkTiming.effective_from.desc()).limit(1))
    return _shift_value(timing.shift if timing else None), False


def _tracking_start(db, user_id, p):
    choices = []
    first = db.scalar(select(func.min(StaffAttendanceSession.checked_in_at)).where(StaffAttendanceSession.user_id == user_id))
    if first:
        choices.append(local_day(first, p.timezone_name))
    for model in (AttendanceHRRequest, AttendanceCalendarDay, AttendanceAdjustment):
        first = db.scalar(select(func.min(model.attendance_date)).where(model.user_id == user_id))
        if first:
            choices.append(first)
    return min(choices) if choices else None


@dataclass
class MonthInputs:
    """Bounded employee/month inputs shared by every day calculation.

    No cache survives this request. Historical first-tracking dates are aggregated
    in SQL; documents, presence events and unrelated employees are never loaded.
    """
    sessions: dict = field(default_factory=lambda: defaultdict(list))
    requests: dict = field(default_factory=lambda: defaultdict(list))
    adjustments: dict = field(default_factory=lambda: defaultdict(list))
    calendar: dict = field(default_factory=dict)
    weekly: dict = field(default_factory=dict)
    plans: dict = field(default_factory=dict)
    timings: dict = field(default_factory=lambda: defaultdict(list))
    tracking: dict = field(default_factory=dict)
    deactivated: dict = field(default_factory=dict)

    def shift(self, user_id, day):
        plan = self.plans.get((user_id, day))
        if plan is not None:
            return _shift_value(plan.shift), True
        timing = next((row for row in self.timings[user_id] if row.effective_from <= day), None)
        return _shift_value(timing.shift if timing else None), False


def _load_month_inputs(db, people, first, last, p):
    result = MonthInputs()
    ids = [person.id for person in people]
    if not ids:
        return result
    tracking_queries = [select(StaffAttendanceSession.user_id.label("user_id"),
        cast(func.timezone(p.timezone_name, func.min(StaffAttendanceSession.checked_in_at)), Date).label("first_day"))
        .where(StaffAttendanceSession.user_id.in_(ids)).group_by(StaffAttendanceSession.user_id)]
    for model in (AttendanceHRRequest, AttendanceCalendarDay, AttendanceAdjustment):
        tracking_queries.append(select(model.user_id, func.min(model.attendance_date).label("first_day"))
                                .where(model.user_id.in_(ids)).group_by(model.user_id))
    tracked = union_all(*tracking_queries).subquery("employee_tracking")
    inactive = [person.id for person in people if not person.active]
    start = stop = first_week = last_week = None
    if last >= first:
        start = datetime.combine(first, time.min, ZoneInfo(p.timezone_name)).astimezone(timezone.utc)
        stop = datetime.combine(last + timedelta(days=1), time.min, ZoneInfo(p.timezone_name)).astimezone(timezone.utc)
        first_week = first - timedelta(days=first.weekday())
        last_week = last - timedelta(days=last.weekday())

    def tracking(session):
        return dict(session.execute(select(tracked.c.user_id, func.min(tracked.c.first_day)).group_by(tracked.c.user_id)).all())

    def deactivated(session):
        if not inactive:
            return {}
        return dict(session.execute(select(AuditEntry.entity_id, func.max(AuditEntry.at))
            .where(AuditEntry.entity_type == "user", AuditEntry.entity_id.in_(inactive), AuditEntry.action == "User deactivated")
            .group_by(AuditEntry.entity_id)).all())

    def sessions(session):
        return list(session.scalars(select(StaffAttendanceSession).where(StaffAttendanceSession.user_id.in_(ids),
            StaffAttendanceSession.checked_in_at >= start, StaffAttendanceSession.checked_in_at < stop)))

    def dated(model):
        def read(session):
            query = select(model).where(model.user_id.in_(ids), model.attendance_date.between(first, last))
            if model is AttendanceHRRequest:
                query = query.where(model.status == "approved")
            if model in (AttendanceHRRequest, AttendanceAdjustment):
                query = query.order_by(model.created_at, model.id)
            return list(session.scalars(query))
        return read

    def weekly(session):
        return list(session.scalars(select(AttendanceWeeklyOff).where(AttendanceWeeklyOff.user_id.in_(ids), AttendanceWeeklyOff.week_start.between(first_week, last_week))))

    def timings(session):
        return list(session.scalars(select(AttendanceWorkTiming).where(AttendanceWorkTiming.user_id.in_(ids),
            AttendanceWorkTiming.effective_from <= last).order_by(AttendanceWorkTiming.effective_from.desc())))

    # Every read depends only on the people list: one round trip's wait instead of nine.
    if last < first:
        result.tracking, result.deactivated = parallel_reads(db, tracking, deactivated)
        return result
    (result.tracking, result.deactivated, month_sessions, requests, adjustments, calendar, plans, weekly_rows, timing_rows
     ) = parallel_reads(db, tracking, deactivated, sessions, dated(AttendanceHRRequest), dated(AttendanceAdjustment),
                        dated(AttendanceCalendarDay), dated(AttendanceDutyPlan), weekly, timings)
    for row in month_sessions:
        result.sessions[(row.user_id, local_day(row.checked_in_at, p.timezone_name))].append(row)
    for rows, destination in ((requests, result.requests), (adjustments, result.adjustments)):
        for row in rows:
            destination[(row.user_id, row.attendance_date)].append(row)
    for rows, destination in ((calendar, result.calendar), (plans, result.plans)):
        for row in rows:
            destination[(row.user_id, row.attendance_date)] = row
    for row in weekly_rows:
        result.weekly[(row.user_id, row.week_start)] = row
    for row in timing_rows:
        result.timings[row.user_id].append(row)
    return result


def _day(db, person, day, at, p, inputs=None):
    shift, planned = inputs.shift(person.id, day) if inputs else _shift_for(db, person.id, day)
    midnight = datetime.combine(day, time.min, ZoneInfo(p.timezone_name)).astimezone(timezone.utc)
    # The shift's start date owns an overnight session, including its next-day checkout.
    next_midnight = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(p.timezone_name)).astimezone(timezone.utc)
    sessions = inputs.sessions[(person.id, day)] if inputs else list(db.scalars(select(StaffAttendanceSession).where(StaffAttendanceSession.user_id == person.id,
        StaffAttendanceSession.checked_in_at >= midnight, StaffAttendanceSession.checked_in_at < next_midnight)))
    punches = []
    for session in sessions:
        punches.append({"action": "check_in", "occurred_at": session.checked_in_at})
        if session.checked_out_at is not None:
            punches.append({"action": "check_out", "occurred_at": session.checked_out_at})
    requests = inputs.requests[(person.id, day)] if inputs else list(db.scalars(select(AttendanceHRRequest).where(AttendanceHRRequest.user_id == person.id,
        AttendanceHRRequest.attendance_date == day, AttendanceHRRequest.status == "approved").order_by(AttendanceHRRequest.created_at, AttendanceHRRequest.id)))
    adjustments = inputs.adjustments[(person.id, day)] if inputs else list(db.scalars(select(AttendanceAdjustment).where(AttendanceAdjustment.user_id == person.id,
        AttendanceAdjustment.attendance_date == day).order_by(AttendanceAdjustment.created_at, AttendanceAdjustment.id)))
    punches.extend({"action": row.action, "occurred_at": row.occurred_at} for row in adjustments if row.kind == "add_punch")
    marker = inputs.calendar.get((person.id, day)) if inputs else db.scalar(select(AttendanceCalendarDay).where(AttendanceCalendarDay.user_id == person.id, AttendanceCalendarDay.attendance_date == day))
    week = day - timedelta(days=day.weekday())
    off = inputs.weekly.get((person.id, week)) if inputs else db.scalar(select(AttendanceWeeklyOff).where(AttendanceWeeklyOff.user_id == person.id, AttendanceWeeklyOff.week_start == week))
    off_index = WEEKDAYS.index(off.day) if off else 6
    status = AttendanceStatus(marker.status) if marker else AttendanceStatus.WEEKLY_OFF if day.weekday() == off_index else None
    if planned and status in {AttendanceStatus.WEEKLY_OFF, AttendanceStatus.HOLIDAY}:
        status = None
    result = calculate_day(day, punches, shift=shift, now=at, policy=p,
        approved_permissions=[{"kind": row.kind, "requested_minutes": row.minutes,
                               "emergency_override": row.emergency_override} for row in requests],
        recovered_minutes=sum(row.minutes for row in adjustments if row.kind == "time_recovery"), non_working_status=status)
    override = next((row for row in reversed(adjustments) if row.kind == "status_override"), None)
    if override:
        result["status"] = override.status
        result["status_override_status"] = override.status
        result["status_override_reason"] = override.reason
        # Reference status overrides change the label, not recorded work or pay.
        # Calendar changes, approved permissions and time recovery are explicit
        # independent inputs to the calculation and retain their own audit trail.
    result["shift"] = _shift_json(shift)
    result["extra_ot_minutes"] = sum(row.minutes for row in requests if row.kind == "extra_ot")
    return result


def _day_json(row):
    return {"date": row["date"].isoformat(), "status": row["status"], "checkIn": row["check_in"], "checkOut": row["check_out"],
        "shift": row["shift"], "requiredMinutes": row["required_shift_minutes"], "workedMinutes": row["actual_covered_minutes"],
        "lateMinutes": row["late_minutes"], "earlyMinutes": row["early_minutes"],
        "graceMinutes": row.get("grace_minutes_applied", 0), "paidPermissionMinutes": row.get("paid_permission_minutes", 0),
        "approvedPermissionMinutes": row.get("approved_permission_minutes", 0), "unpaidMinutes": row["unpaid_minutes"],
        "lateUnpaidMinutes": row.get("late_unpaid_minutes", 0),
        "extraOtMinutes": row["extra_ot_minutes"], "provisional": row["provisional"],
        "regularisationDeadline": row.get("regularisation_deadline"), "overrideReason": row.get("status_override_reason"),
        "permissionWarning": row.get("permission_warning", False), "permissionExhausted": row.get("permission_exhausted", False)}


def month_report(db, actor, year, month, staff_id=None, *, at=None):
    first, last = period(year, month)
    at, p = at or utcnow(), policy()
    end = min(last, local_day(at, p.timezone_name))
    people = _people(db, actor, staff_id)
    inputs = _load_month_inputs(db, people, first, end, p)
    output = []
    for person in people:
        person_end = end
        if not person.active:
            deactivated_at = inputs.deactivated.get(person.id)
            person_end = min(end, local_day(deactivated_at or person.updated_at, p.timezone_name))
        tracking_start = inputs.tracking.get(person.id)
        start = max(first, tracking_start) if tracking_start else person_end + timedelta(days=1)
        rows = calculate_month([_day(db, person, start + timedelta(days=i), at, p, inputs=inputs)
                                for i in range(max(0, (person_end - start).days + 1))], p)
        for row in rows:
            if row.get("status_override_status"):
                row["status"] = row["status_override_status"]
        days = [_day_json(row) for row in rows]
        summary = {"presentDays": sum(row["status"] in {"P", "LT", "EE", "PP", "OD", "WFH"} for row in rows),
            "paidLeaveDays": sum(row["status"] == "PL" for row in rows), "unpaidLeaveDays": sum(row["status"] == "UL" for row in rows),
            "weeklyOffDays": sum(row["status"] == "WO" for row in rows), "holidayDays": sum(row["status"] == "H" for row in rows),
            "absentDays": sum(row["status"] == "A" for row in rows), "workedMinutes": sum(row["actual_covered_minutes"] for row in rows),
            "unpaidMinutes": sum(row["unpaid_minutes"] for row in rows), "extraOtMinutes": sum(row["extra_ot_minutes"] for row in rows),
            "graceUsedMinutes": rows[-1]["permission_minutes_used"] if rows else 0,
            "graceRemainingMinutes": rows[-1]["permission_minutes_remaining"] if rows else p.monthly_paid_minutes,
            "permissionOccasionsUsed": rows[-1]["permission_occasions_used"] if rows else 0}
        summary.update(attendance_overview(rows))
        summary["unpaidBeforeOtMinutes"] = summary["unpaidMinutes"]
        summary["otOffsetMinutes"] = min(summary["extraOtMinutes"], sum(row.get("late_unpaid_minutes", 0) for row in rows))
        summary["unpaidMinutes"] -= summary["otOffsetMinutes"]
        summary["unusedOtMinutes"] = summary["extraOtMinutes"] - summary["otOffsetMinutes"]
        output.append({"userId": person.id, "name": person.name, "role": person.role.value, "active": person.active,
                       "trackingStart": tracking_start, "days": days, "summary": summary})
    return {"year": year, "month": month, "timezone": p.timezone_name, "canManage": actor.role != Role.staff,
            "canCorrect": actor.role == Role.admin, "policy": policy_json(), "staff": output}


def required_stages(row):
    return ["manager", "admin"] if row.kind in MONEY_KINDS else ["manager"]


def _decision_stages(actor, employee, row):
    if not actor.active or actor.id == employee.id or row.status != "pending":
        return []
    allowed = []
    if actor.role == Role.admin or (actor.role == Role.manager and employee.role == Role.staff):
        allowed.append("manager")
    if actor.role == Role.admin:
        allowed.append("admin")
    return [stage for stage in required_stages(row) if stage in allowed and not (row.approvals or {}).get(stage, {}).get("approved")]


def _request_json(db, actor, row, people=None):
    employee = people[row.user_id] if people is not None else db.get(User, row.user_id)
    cover = (people.get(row.cover_user_id) if people is not None else db.get(User, row.cover_user_id)) if row.cover_user_id else None
    stages = _decision_stages(actor, employee, row)
    return {"id": row.id, "userId": row.user_id, "name": employee.name, "date": row.attendance_date,
        "kind": row.kind, "minutes": row.minutes, "reason": row.reason, "status": row.status,
        "coverUserId": row.cover_user_id, "coverName": cover.name if cover else None,
        "coverStatus": row.cover_status, "coverNote": row.cover_note,
        "coverStartedAt": row.cover_started_at, "coverSettledAt": row.cover_settled_at,
        "emergencyOverride": row.emergency_override,
        "approvals": row.approvals or {}, "requiredStages": required_stages(row), "canDecide": bool(stages),
        "decisionStages": stages, "canRespondCover": actor.id == row.cover_user_id and row.status == "awaiting_cover" and row.cover_status == "requested",
        "canNominateCover": actor.id == row.user_id and row.kind in LEAVE_KINDS and row.status == "awaiting_cover" and row.cover_status == "declined",
        "convertedFromPaidLeave": row.converted_from_paid_leave, "createdAt": row.created_at}


def request_report(db, actor, year, month, staff_id=None):
    first, last = period(year, month)
    people = {u.id: u for u in _people(db, actor, staff_id)}
    scope = and_(AttendanceHRRequest.attendance_date.between(first, last),
                 or_(AttendanceHRRequest.user_id.in_(people), AttendanceHRRequest.cover_user_id == actor.id))
    # The people named on the requests (requesters, cover colleagues) are found from the same filter,
    # so both reads can run together.
    rows, referenced = parallel_reads(
        db,
        lambda session: list(session.scalars(select(AttendanceHRRequest).where(scope)
            .order_by(AttendanceHRRequest.attendance_date.desc(), AttendanceHRRequest.created_at.desc(), AttendanceHRRequest.id))),
        lambda session: list(session.scalars(select(User).where(or_(
            User.id.in_(select(AttendanceHRRequest.user_id).where(scope)),
            User.id.in_(select(AttendanceHRRequest.cover_user_id).where(scope)))))),
    )
    for person in referenced:
        people.setdefault(person.id, person)
    return {"requests": [_request_json(db, actor, row, people) for row in rows]}


def _cover_colleagues(db, employee, day=None):
    # DCRM candidate/task owners are Staff; a Manager also nominates a Staff
    # colleague, as in the reference. A supervisor role is not an assignee role.
    people = list(db.scalars(select(User).where(User.active.is_(True), User.role == Role.staff, User.id != employee.id,
                                               User.on_leave.is_(False)).order_by(User.name, User.id)))
    if day:
        absent = unavailable_staff_ids(db, day=day)
        people = [person for person in people if person.id not in absent]
    return people


def _check_cover(db, employee, cover_id, day):
    if not any(person.id == cover_id for person in _cover_colleagues(db, employee, day)):
        raise HTTPException(422, "Choose an available Staff colleague as cover")


def _notify_approvers(db, row):
    person = db.get(User, row.user_id)
    for user in db.scalars(select(User).where(User.active.is_(True), User.role.in_([Role.admin, Role.manager]))):
        if _decision_stages(user, person, row):
            _notify(db, user.id, "Attendance approval needed", f"{person.name}: {row.kind.replace('_', ' ')} on {row.attendance_date}")


def create_request(db, actor, body, *, at=None):
    _lock(db, actor)
    person = _employee(db, actor, actor.id, active=True)
    key = str(body["requestKey"])
    existing = db.scalar(select(AttendanceHRRequest).where(AttendanceHRRequest.request_key == key))
    if existing:
        if existing.user_id != actor.id:
            raise HTTPException(409, "Use a new request identifier")
        requested_minutes = body.get("minutes", 0) if body["kind"] in HOUR_KINDS else 0
        requested_cover = body.get("coverUserId") if body["kind"] in LEAVE_KINDS else None
        if (existing.attendance_date != as_date(body["date"]) or existing.kind != body["kind"] or
            existing.minutes != requested_minutes or existing.reason != body["reason"].strip() or
            existing.cover_user_id != requested_cover):
            raise HTTPException(409, "This request was already saved with different details. Close this form and review request history before creating another.")
        return _request_json(db, actor, existing)
    at, p = at or utcnow(), policy()
    day, kind = as_date(body["date"]), body["kind"]
    shift, _ = _shift_for(db, person.id, day)
    start, end = shift_bounds(day, shift, p.timezone_name)
    if kind in {"late", "early_check_in", "work_from_home"} and at >= start:
        raise HTTPException(422, "This permission must be requested before the shift starts")
    if kind == "early_exit":
        cutoff = datetime.combine(day, time.min, ZoneInfo(p.timezone_name)).astimezone(timezone.utc)
        checked_out = db.scalar(select(StaffAttendanceSession.id).where(StaffAttendanceSession.user_id == person.id,
            StaffAttendanceSession.checked_in_at >= cutoff, StaffAttendanceSession.checked_in_at < cutoff + timedelta(days=1),
            StaffAttendanceSession.checked_out_at.is_not(None)).limit(1))
        if at >= end or checked_out:
            raise HTTPException(422, "Early-exit permission must be requested before leaving")
    if kind == "weekly_off":
        week = day - timedelta(days=day.weekday())
        duplicate = db.scalar(select(AttendanceHRRequest.id).where(AttendanceHRRequest.user_id == person.id,
            AttendanceHRRequest.kind == "weekly_off", AttendanceHRRequest.attendance_date.between(week, week + timedelta(days=6)),
            AttendanceHRRequest.status.in_(["pending", "approved"])).limit(1))
        if day.weekday() == 6 or duplicate:
            raise HTTPException(422, "Choose one rotational day other than Sunday for this week")
    if kind in LEAVE_KINDS:
        duplicate = db.scalar(select(AttendanceHRRequest.id).where(AttendanceHRRequest.user_id == person.id,
            AttendanceHRRequest.attendance_date == day, AttendanceHRRequest.kind.in_(LEAVE_KINDS),
            AttendanceHRRequest.status != "rejected").limit(1))
        if duplicate:
            raise HTTPException(409, "A leave request already exists for this date")
    cover_id = body.get("coverUserId") if kind in LEAVE_KINDS else None
    if kind in LEAVE_KINDS:
        if cover_id:
            _check_cover(db, person, cover_id, day)
        elif _cover_colleagues(db, person, day):
            raise HTTPException(422, "Choose a colleague to handle your work on the leave day")
    minutes = body.get("minutes", 0) if kind in HOUR_KINDS else 0
    if kind in HOUR_KINDS and minutes <= 0:
        raise HTTPException(422, "Enter the requested minutes")
    row = AttendanceHRRequest(user_id=person.id, request_key=key, attendance_date=day, kind=kind,
        minutes=minutes, reason=body["reason"].strip(), status="awaiting_cover" if cover_id else "pending",
        cover_user_id=cover_id, cover_status="requested" if cover_id else None, approvals={}, created_at=at, updated_at=at)
    db.add(row); db.flush()
    _audit(db, actor, "Requested attendance permission", row, {"kind": kind, "date": day.isoformat()})
    if cover_id:
        _notify(db, cover_id, "Leave cover requested", f"{person.name} needs cover on {day}")
    else:
        _notify_approvers(db, row)
    return _request_json(db, actor, row)


def _get_request(db, request_id):
    row = db.scalar(select(AttendanceHRRequest).where(AttendanceHRRequest.id == request_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Attendance request not found")
    return row


def _calendar(db, actor, person, day, status, reason, request_id=None):
    first, last = period(day.year, day.month)
    converted = False
    if status == "PL":
        other = db.scalar(select(AttendanceCalendarDay.id).where(AttendanceCalendarDay.user_id == person.id,
            AttendanceCalendarDay.attendance_date.between(first, last), AttendanceCalendarDay.attendance_date != day,
            AttendanceCalendarDay.status == "PL").limit(1))
        if other:
            status, converted = "UL", True
    row = db.scalar(select(AttendanceCalendarDay).where(AttendanceCalendarDay.user_id == person.id, AttendanceCalendarDay.attendance_date == day))
    if row is None:
        row = AttendanceCalendarDay(user_id=person.id, attendance_date=day)
        db.add(row)
    row.status, row.reason, row.actor_id, row.request_id = status, reason, actor.id, request_id
    row.converted_from_paid_leave = converted
    db.flush()
    _audit(db, actor, "Recorded attendance calendar", row, {"date": day.isoformat(), "status": status, "convertedFromPaidLeave": converted})
    return row


def decide_request(db, actor, request_id, body, *, at=None):
    _lock(db, actor)
    row = _get_request(db, request_id)
    person = _employee(db, actor, row.user_id)
    stages = _decision_stages(actor, person, row)
    if not stages:
        raise HTTPException(403 if row.status == "pending" else 409, "This request has no approval stage available to you")
    if body.get("emergencyOverride"):
        if actor.role != Role.admin:
            raise HTTPException(403, "Only Admin can allow an extra permission occasion")
        if row.kind not in {"late", "early_exit"} or not body["approved"]:
            raise HTTPException(422, "An extra permission occasion applies only when approving late arrival or early exit")
    at = at or utcnow()
    if body["approved"] and row.kind == "work_from_home":
        shift, _ = _shift_for(db, person.id, row.attendance_date)
        if at >= shift_bounds(row.attendance_date, shift, policy().timezone_name)[0]:
            raise HTTPException(422, "Work from home must be approved before the shift starts")
    approvals = dict(row.approvals or {})
    record = {"actorId": actor.id, "name": actor.name, "approved": body["approved"], "reason": body["reason"].strip(), "at": at.isoformat()}
    for stage in stages:
        approvals[stage] = record
    row.approvals = approvals
    row.emergency_override = row.emergency_override or (actor.role == Role.admin and body.get("emergencyOverride", False))
    row.updated_at = at
    if not body["approved"]:
        row.status = "rejected"
    elif all(approvals.get(stage, {}).get("approved") for stage in required_stages(row)):
        row.status = "approved"
        if row.kind in LEAVE_KINDS:
            marker = _calendar(db, actor, person, row.attendance_date, "PL" if row.kind == "paid_leave" else "UL", row.reason, row.id)
            row.converted_from_paid_leave = marker.converted_from_paid_leave
        elif row.kind == "weekly_off":
            week = row.attendance_date - timedelta(days=row.attendance_date.weekday())
            off = db.scalar(select(AttendanceWeeklyOff).where(AttendanceWeeklyOff.user_id == person.id, AttendanceWeeklyOff.week_start == week))
            if off is None:
                off = AttendanceWeeklyOff(user_id=person.id, week_start=week)
                db.add(off)
            off.day, off.approved_request_id = WEEKDAYS[row.attendance_date.weekday()], row.id
    db.flush()
    _audit(db, actor, "Decided attendance request", row, {"stages": stages, **record, "status": row.status,
        "emergencyOverride": row.emergency_override})
    _notify(db, person.id, "Attendance request updated", f"{row.kind.replace('_', ' ')} on {row.attendance_date}: {row.status}")
    if row.status == "pending":
        _notify_approvers(db, row)
    return _request_json(db, actor, row)


def respond_cover(db, actor, request_id, body, *, at=None):
    _lock(db, actor)
    row = _get_request(db, request_id)
    if row.cover_user_id != actor.id or row.status != "awaiting_cover" or row.cover_status != "requested":
        raise HTTPException(409, "No pending cover request is addressed to you")
    person = db.get(User, row.user_id)
    if body["accepted"]:
        _check_cover(db, person, actor.id, row.attendance_date)
    row.cover_status = "accepted" if body["accepted"] else "declined"
    row.cover_note = body.get("note", "").strip()
    row.status = "pending" if body["accepted"] else "awaiting_cover"
    db.flush()
    _audit(db, actor, "Responded to leave cover", row, {"accepted": body["accepted"], "note": row.cover_note})
    _notify(db, row.user_id, "Leave cover response", f"{actor.name} {row.cover_status} cover on {row.attendance_date}")
    if body["accepted"]:
        _notify_approvers(db, row)
    return _request_json(db, actor, row)


def nominate_cover(db, actor, request_id, body, *, at=None):
    _lock(db, actor)
    row = _get_request(db, request_id)
    if row.user_id != actor.id or row.status != "awaiting_cover" or row.cover_status != "declined":
        raise HTTPException(409, "A new cover can be nominated after the previous colleague declines")
    _check_cover(db, actor, body["coverUserId"], row.attendance_date)
    row.cover_user_id, row.cover_status, row.cover_note = body["coverUserId"], "requested", None
    db.flush()
    _audit(db, actor, "Nominated replacement leave cover", row, {"coverUserId": row.cover_user_id})
    _notify(db, row.cover_user_id, "Leave cover requested", f"{actor.name} needs cover on {row.attendance_date}")
    return _request_json(db, actor, row)


def _weekly_json(off, week, at):
    deadline = datetime.combine(week + timedelta(days=3), time(23, 59, 59), ZoneInfo(policy().timezone_name))
    return {"weekStart": week, "day": off.day if off else "sunday", "deadline": deadline,
        "canChange": at <= deadline and not (off and off.approved_request_id),
        "approvedRequestId": off.approved_request_id if off else None}


def settings_report(db, actor, year, month, staff_id=None, *, at=None):
    first, last = period(year, month)
    at = at or utcnow()
    people = _people(db, actor, staff_id)
    ids = [u.id for u in people]
    names = {u.id: u.name for u in people}
    today = local_day(at, policy().timezone_name)
    week = today - timedelta(days=today.weekday())
    weeks = [week + timedelta(weeks=i) for i in range(4)]
    employee = actor.role in EMPLOYEE_ROLES
    plans, timings, calendar_rows, weekly_rows, colleagues = parallel_reads(
        db,
        lambda session: list(session.scalars(select(AttendanceDutyPlan).where(AttendanceDutyPlan.user_id.in_(ids), AttendanceDutyPlan.attendance_date.between(first, last)).order_by(AttendanceDutyPlan.attendance_date))),
        lambda session: list(session.scalars(select(AttendanceWorkTiming).where(AttendanceWorkTiming.user_id.in_(ids)).order_by(AttendanceWorkTiming.effective_from.desc()))),
        lambda session: list(session.scalars(select(AttendanceCalendarDay).where(AttendanceCalendarDay.user_id.in_(ids), AttendanceCalendarDay.attendance_date.between(first, last)).order_by(AttendanceCalendarDay.attendance_date))),
        lambda session: list(session.scalars(select(AttendanceWeeklyOff).where(AttendanceWeeklyOff.user_id == actor.id, AttendanceWeeklyOff.week_start.in_(weeks)))),
        lambda session: _cover_colleagues(session, actor) if employee else [],
    )
    weekly = [_weekly_json(next((row for row in weekly_rows if row.week_start == w), None), w, at) for w in weeks] if employee else []
    return {"employees": [{"id": u.id, "name": u.name, "role": u.role.value, "active": u.active} for u in people],
        "coverColleagues": [{"id": u.id, "name": u.name} for u in colleagues],
        "weeklyOff": weekly, "policy": policy_json(),
        "dutyPlans": [{"id": row.id, "userId": row.user_id, "name": names[row.user_id], "date": row.attendance_date, "shift": row.shift, "reason": row.reason} for row in plans],
        "workTimings": [{"id": row.id, "userId": row.user_id, "name": names[row.user_id], "effectiveFrom": row.effective_from, "shift": row.shift, "reason": row.reason} for row in timings],
        "calendarDays": [{"id": row.id, "userId": row.user_id, "name": names[row.user_id], "date": row.attendance_date, "status": row.status, "reason": row.reason} for row in calendar_rows]}


def set_weekly_off(db, actor, body, *, at=None):
    _lock(db, actor)
    _employee(db, actor, actor.id, active=True)
    at = at or utcnow()
    week = as_date(body["weekStart"])
    today = local_day(at, policy().timezone_name)
    current_week = today - timedelta(days=today.weekday())
    if week.weekday() != 0 or week < current_week or week > current_week + timedelta(weeks=3):
        raise HTTPException(422, "Choose this week or one of the next three weeks")
    row = db.scalar(select(AttendanceWeeklyOff).where(AttendanceWeeklyOff.user_id == actor.id, AttendanceWeeklyOff.week_start == week))
    if not _weekly_json(row, week, at)["canChange"]:
        raise HTTPException(409, "The Thursday 11:59 PM deadline passed or a rotational off is already approved")
    if row is None:
        row = AttendanceWeeklyOff(user_id=actor.id, week_start=week)
        db.add(row)
    row.day = body["day"]
    db.flush()
    _audit(db, actor, "Selected weekly off", row, {"weekStart": week.isoformat(), "day": row.day})
    return _weekly_json(row, week, at)


def save_duty_plan(db, actor, body, *, at=None):
    _lock(db, actor)
    person = _employee(db, actor, body["userId"], manage=True, active=True)
    day = as_date(body["date"])
    shift = _shift_json(_shift_value(body["shift"]))
    row = db.scalar(select(AttendanceDutyPlan).where(AttendanceDutyPlan.user_id == person.id, AttendanceDutyPlan.attendance_date == day))
    if row is None:
        row = AttendanceDutyPlan(user_id=person.id, attendance_date=day)
        db.add(row)
    row.shift, row.reason, row.actor_id = shift, body["reason"].strip(), actor.id
    db.flush(); _audit(db, actor, "Saved attendance duty plan", row, {"date": day.isoformat(), "shift": shift})
    _notify(db, person.id, "Duty roster updated", f"{day}: {shift['start']}–{shift['end']}")
    return {"id": row.id}


def delete_duty_plan(db, actor, plan_id):
    _lock(db, actor)
    row = db.get(AttendanceDutyPlan, plan_id)
    if row is None:
        raise HTTPException(404, "Duty plan not found")
    _employee(db, actor, row.user_id, manage=True)
    _audit(db, actor, "Removed attendance duty plan", row, {"date": row.attendance_date.isoformat(), "userId": row.user_id})
    db.delete(row)
    return {"ok": True}


def save_work_timing(db, actor, body, *, at=None):
    _lock(db, actor)
    person = _employee(db, actor, body["userId"], manage=True, active=True)
    day = as_date(body["effectiveFrom"])
    shift = _shift_json(_shift_value(body["shift"]))
    row = db.scalar(select(AttendanceWorkTiming).where(AttendanceWorkTiming.user_id == person.id, AttendanceWorkTiming.effective_from == day))
    if row is None:
        row = AttendanceWorkTiming(user_id=person.id, effective_from=day)
        db.add(row)
    row.shift, row.reason, row.actor_id = shift, body["reason"].strip(), actor.id
    db.flush(); _audit(db, actor, "Saved attendance work timing", row, {"effectiveFrom": day.isoformat(), "shift": shift})
    _notify(db, person.id, "Work timing updated", f"From {day}: {shift['start']}–{shift['end']}")
    return {"id": row.id}


def save_calendar_day(db, actor, body, *, at=None):
    _lock(db, actor)
    person = _employee(db, actor, body["userId"], manage=True)
    # Pay-changing direct entries require the finance-equivalent Admin authority.
    if body["status"] in {"PL", "UL"} and actor.role != Role.admin:
        raise HTTPException(403, "Admin approval is required for paid or unpaid leave")
    existing = db.scalar(select(AttendanceCalendarDay).where(AttendanceCalendarDay.user_id == person.id,
        AttendanceCalendarDay.attendance_date == as_date(body["date"])))
    if existing and existing.status in {"PL", "UL"} and actor.role != Role.admin:
        raise HTTPException(403, "Only Admin can replace an approved leave calendar entry")
    row = _calendar(db, actor, person, as_date(body["date"]), body["status"], body["reason"].strip())
    return {"id": row.id, "status": row.status, "convertedFromPaidLeave": row.converted_from_paid_leave}


def create_adjustment(db, actor, body, *, at=None):
    _lock(db, actor)
    _employee(db, actor, body["userId"], manage=True)
    at = at or utcnow()
    day = as_date(body["date"])
    kind = body["kind"]
    if kind == "status_override" and actor.role != Role.admin:
        raise HTTPException(403, "Only Admin can override an attendance status")
    if kind == "time_recovery" and body.get("minutes", 0) <= 0:
        raise HTTPException(422, "Enter the recovered minutes")
    if kind == "status_override" and not body.get("status"):
        raise HTTPException(422, "Choose a status")
    occurred = body.get("occurredAt")
    if isinstance(occurred, str):
        occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    if kind == "add_punch":
        if not occurred or not body.get("action") or occurred.tzinfo is None or occurred > at:
            raise HTTPException(422, "Provide a valid past punch time including timezone")
        shift, _ = _shift_for(db, body["userId"], day)
        _, end = shift_bounds(day, shift, policy().timezone_name)
        first = datetime.combine(day, time.min, ZoneInfo(policy().timezone_name))
        if occurred < first or occurred > max(first + timedelta(days=1), end + timedelta(hours=1)):
            raise HTTPException(422, "The punch must belong to this attendance date or its overnight shift")
    row = AttendanceAdjustment(user_id=body["userId"], attendance_date=day, kind=kind, reason=body["reason"].strip(),
        actor_id=actor.id, status=body.get("status") if kind == "status_override" else None,
        minutes=body.get("minutes", 0) if kind == "time_recovery" else 0,
        action=body.get("action") if kind == "add_punch" else None, occurred_at=occurred if kind == "add_punch" else None, created_at=at)
    db.add(row); db.flush(); _audit(db, actor, "Adjusted attendance", row, {"date": day.isoformat(), "kind": kind, "reason": row.reason})
    return {"id": row.id}


def adjustment_report(db, actor, year, month, staff_id=None):
    first, last = period(year, month)
    people = _people(db, actor, staff_id)
    names = {u.id: u.name for u in people}
    # The actor's name comes from the same statement, not one lookup per adjustment.
    rows = db.execute(select(AttendanceAdjustment, User.name).outerjoin(User, User.id == AttendanceAdjustment.actor_id)
        .where(AttendanceAdjustment.user_id.in_(names), AttendanceAdjustment.attendance_date.between(first, last))
        .order_by(AttendanceAdjustment.created_at.desc(), AttendanceAdjustment.id)).all()
    return {"adjustments": [{"id": row.id, "userId": row.user_id, "name": names[row.user_id], "date": row.attendance_date,
        "kind": row.kind, "reason": row.reason, "status": row.status, "minutes": row.minutes, "action": row.action,
        "occurredAt": row.occurred_at, "actorName": actor_name, "createdAt": row.created_at} for row, actor_name in rows]}


def _move_task(db, task, target_id, request_id, at, phase):
    previous = task.owner_id
    task.owner_id = target_id
    db.add(TaskEvent(kind="assignment", task_id=task.id, candidate_id=task.candidate_id, owner_id=previous,
        event_key=f"attendance-cover:{request_id}:{phase}:{task.id}", occurred_at=at,
        meta={"fromOwnerId": previous, "toOwnerId": target_id, "attendanceRequestId": request_id, "phase": phase}))


def _cover_audit(db, candidate_id, request, from_id, to_id, at, *, returning=False):
    db.add(AuditEntry(actor_id=None, action="Returned leave-cover candidate" if returning else "Started leave-cover candidate",
        entity_type="candidate", entity_id=candidate_id, at=at,
        meta={"fromUserId": from_id, "toUserId": to_id, "requestId": request.id,
              "reason": f"{'Return after' if returning else 'Accepted'} leave cover for {request.attendance_date}"}))


def run_cover_sweep(db, *, at=None):
    """Scheduled, transactional start/return; never runs as a side effect of GET.

    Only uncontacted candidates with open initial work move. Completion stays with
    the cover. Later human assignments, changed task ownership, inactive original
    owners and unresolved overlaps are released rather than overwritten.
    """
    users = {u.id: u for u in _lock(db)}
    from app.services.assignment_notifications import notify_assignment_changes
    at = at or utcnow()
    today = local_day(at, policy().timezone_name)
    counts = {"started": 0, "returned": 0, "completed": 0, "released": 0, "settled": 0}
    missed = list(db.scalars(select(AttendanceHRRequest).where(AttendanceHRRequest.status == "approved",
        AttendanceHRRequest.kind.in_(LEAVE_KINDS), AttendanceHRRequest.cover_status == "accepted",
        AttendanceHRRequest.cover_started_at.is_(None), AttendanceHRRequest.cover_settled_at.is_(None),
        AttendanceHRRequest.attendance_date < today).order_by(AttendanceHRRequest.id).with_for_update()))
    for request in missed:
        # A worker outage must not lend yesterday's candidates today or leave
        # the UI claiming an expired handover is still scheduled.
        request.cover_settled_at = at
        counts["settled"] += 1
        _audit(db, None, "Leave-cover window elapsed without handover", request, {"date": request.attendance_date.isoformat()})
        _notify(db, request.user_id, "Leave cover did not run", f"The cover window for {request.attendance_date} elapsed before handover; no candidates were moved.")
    finished = list(db.scalars(select(AttendanceHRRequest).where(AttendanceHRRequest.cover_started_at.is_not(None),
        AttendanceHRRequest.cover_settled_at.is_(None), AttendanceHRRequest.attendance_date < today).order_by(AttendanceHRRequest.id).with_for_update()))
    for request in finished:
        ownership_changes = []
        items = list(db.scalars(select(AttendanceCoverItem).where(AttendanceCoverItem.request_id == request.id,
            AttendanceCoverItem.settled_at.is_(None)).order_by(AttendanceCoverItem.candidate_id).with_for_update()))
        for item in items:
            candidate = db.scalar(select(Candidate).where(Candidate.id == item.candidate_id).with_for_update())
            tasks = list(db.scalars(select(Task).where(Task.candidate_id == item.candidate_id).order_by(Task.id).with_for_update()))
            initial = [task for task in tasks if task.id in item.task_ids]
            completed = candidate and (candidate.call_recorded_at is not None or any(task.completed_at is not None for task in initial))
            changed = db.scalar(select(TaskEvent.id).where(TaskEvent.task_id.in_(item.task_ids), TaskEvent.kind == "assignment",
                TaskEvent.occurred_at >= item.started_at, ~TaskEvent.event_key.like(f"attendance-cover:{request.id}:start:%")).limit(1))
            audits = list(db.scalars(select(AuditEntry).where(AuditEntry.at >= item.started_at,
                AuditEntry.action.ilike("%reassign%"), AuditEntry.actor_id.is_not(None))))
            reassigned = any(a.entity_id == item.candidate_id or item.candidate_id in (a.meta or {}).get("candidateIds", []) for a in audits)
            owner = users.get(item.from_user_id)
            open_tasks = [task for task in tasks if task.completed_at is None and task.cancelled_at is None]
            eligible = candidate and candidate.owner_id == item.to_user_id and owner and owner.active and not owner.on_leave and owner.role == Role.staff and not changed and not reassigned
            eligible = eligible and all(task.owner_id == item.to_user_id for task in open_tasks)
            if not eligible:
                outcome = "released"
            elif completed:
                outcome = "completed"
            else:
                candidate.owner_id = item.from_user_id
                ownership_changes.append((candidate.id, candidate.name, item.to_user_id, item.from_user_id))
                for task in open_tasks:
                    _move_task(db, task, item.from_user_id, request.id, at, "return")
                outcome = "returned"
                _cover_audit(db, item.candidate_id, request, item.to_user_id, item.from_user_id, at, returning=True)
            item.outcome, item.settled_at = outcome, at
            counts[outcome] += 1
        request.cover_settled_at = at
        counts["settled"] += 1
        _notify(db, request.user_id, "Leave cover ended", f"Cover for {request.attendance_date} has settled; unfinished unchanged work was returned.")
        notify_assignment_changes(db, ownership_changes, reason=f"Unfinished work returned after leave cover on {request.attendance_date}.", users=users.values())
    # Sessions use autoflush=False. Today's handover must see yesterday's
    # returned ownership and closed ledger entries in the same transaction.
    db.flush()
    due = list(db.scalars(select(AttendanceHRRequest).where(AttendanceHRRequest.status == "approved",
        AttendanceHRRequest.kind.in_(LEAVE_KINDS), AttendanceHRRequest.cover_status == "accepted",
        AttendanceHRRequest.cover_started_at.is_(None), AttendanceHRRequest.cover_settled_at.is_(None),
        AttendanceHRRequest.attendance_date == today)
        .order_by(AttendanceHRRequest.id).with_for_update()))
    for request in due:
        ownership_changes = []
        owner, cover = users.get(request.user_id), users.get(request.cover_user_id)
        leave_still_effective = db.scalar(select(AttendanceCalendarDay.id).where(AttendanceCalendarDay.user_id == request.user_id,
            AttendanceCalendarDay.attendance_date == today, AttendanceCalendarDay.status.in_(["PL", "UL"])).limit(1))
        if not leave_still_effective:
            request.cover_settled_at = at
            _audit(db, None, "Skipped leave cover after calendar correction", request)
            continue
        if not owner or not cover or not owner.active or not cover.active or cover.on_leave or cover.role != Role.staff:
            request.cover_settled_at = at
            _audit(db, None, "Skipped unavailable leave cover", request)
            continue
        cover_absent = db.scalar(select(AttendanceCalendarDay.id).where(AttendanceCalendarDay.user_id == cover.id,
            AttendanceCalendarDay.attendance_date == today, AttendanceCalendarDay.status.in_(["PL", "UL"])).limit(1))
        if cover_absent:
            request.cover_settled_at = at
            _audit(db, None, "Skipped absent leave cover", request)
            continue
        candidates = list(db.scalars(select(Candidate).where(Candidate.owner_id == owner.id,
            Candidate.call_recorded_at.is_(None)).order_by(Candidate.id).with_for_update())) if owner.role == Role.staff else []
        for candidate in candidates:
            tasks = list(db.scalars(select(Task).where(Task.candidate_id == candidate.id, Task.completed_at.is_(None),
                Task.cancelled_at.is_(None)).order_by(Task.id).with_for_update()))
            if not tasks or any(task.owner_id != owner.id for task in tasks):
                continue
            ongoing = db.scalar(select(AttendanceCoverItem.id).where(AttendanceCoverItem.candidate_id == candidate.id,
                AttendanceCoverItem.settled_at.is_(None)).limit(1))
            if ongoing:
                continue
            item = AttendanceCoverItem(request_id=request.id, candidate_id=candidate.id, from_user_id=owner.id,
                to_user_id=cover.id, task_ids=[task.id for task in tasks], started_at=at)
            db.add(item)
            candidate.owner_id = cover.id
            ownership_changes.append((candidate.id, candidate.name, owner.id, cover.id))
            for task in tasks:
                _move_task(db, task, cover.id, request.id, at, "start")
            db.flush()
            _cover_audit(db, candidate.id, request, owner.id, cover.id, at)
            counts["started"] += 1
        request.cover_started_at = at
        _notify(db, cover.id, "Leave cover started", f"You are covering {owner.name} on {today}; unfinished work returns after today.")
        notify_assignment_changes(db, ownership_changes, reason=f"Covering {owner.name} during leave on {today}.", users=users.values())
    db.flush()
    return counts
