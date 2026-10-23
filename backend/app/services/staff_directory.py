"""Read-only staff workload and assignment availability from one database snapshot."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, func, select

from app.db.models import AttendanceCalendarDay, Candidate, Remark, Role, Task, TaskKind, User


def directory_period(today: date, from_date: date | None, to_date: date | None):
    start, end = from_date or today.replace(day=1), to_date or today
    if start > end:
        raise HTTPException(422, "The start date must be on or before the end date.")
    if end == date.max:
        raise HTTPException(422, "Choose an end date before 9999-12-31.")
    return start, end


def availability(row):
    """Eligibility is for auto-assignment; paused staff can still be assigned manually."""
    if not row["active"]:
        state, label = "inactive", "Inactive account"
    elif row["role"] != Role.staff:
        state, label = "not_assigned", "Does not receive candidates"
    elif row["approved_leave"]:
        state, label = "approved_leave", "Approved paid leave" if row["approved_leave"] == "PL" else "Approved unpaid leave"
    elif row["on_leave"]:
        state, label = "manual_leave", "On leave"
    elif not row["receives_auto_assign"]:
        state, label = "paused", "Auto-assignment paused"
    else:
        state, label = "available", "Available for assignments"
    result = {"status": state, "label": label, "eligible": state == "available"}
    if state == "approved_leave":
        result["leaveType"] = row["approved_leave"]
    return result


def _identity_query(today):
    leave = select(AttendanceCalendarDay.user_id, AttendanceCalendarDay.status).where(
        AttendanceCalendarDay.attendance_date == today,
        AttendanceCalendarDay.status.in_(["PL", "UL"]),
    ).subquery("effective_leave")
    # Deliberately select public identity fields, never credentials or session data.
    return select(User.id, User.name, User.email, User.phone, User.role, User.active,
                  User.receives_auto_assign, User.on_leave, leave.c.status.label("approved_leave")).outerjoin(
        leave, leave.c.user_id == User.id).order_by(func.lower(User.name), User.id)


def _identity(row):
    return {**{key: row[key] for key in ("id", "name", "email", "phone", "role", "active")},
            "availability": availability(row)}


def staff_availability(db, zone, *, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(zone)).date()
    rows = db.execute(_identity_query(today)).mappings()
    return {"today": today.isoformat(), "timezone": zone, "staff": [_identity(row) for row in rows]}


def staff_directory(db, zone, from_date=None, to_date=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(zone)
    today = now.astimezone(tz).date()
    start, end = directory_period(today, from_date, to_date)
    day_start = datetime.combine(today, time.min, tzinfo=tz)
    day_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    period_start = datetime.combine(start, time.min, tzinfo=tz)
    period_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)

    candidates = select(Candidate.owner_id, func.count().label("assignedCandidates")).group_by(
        Candidate.owner_id).subquery("candidate_workload")
    # One row per task/author prevents multiple daily remarks multiplying counts.
    remarks = select(Remark.task_id, Remark.author_id).where(
        Remark.created_at >= day_start, Remark.created_at < day_end,
    ).distinct().subquery("owner_updates_today")
    opened = and_(Task.completed_at.is_(None), Task.cancelled_at.is_(None))
    completed = and_(Task.cancelled_at.is_(None), Task.completed_at >= period_start, Task.completed_at < period_end)
    due = func.coalesce(Task.due_at, func.coalesce(Task.sla_started_at, Task.created_at) + timedelta(hours=48))
    hours = func.greatest(func.extract("epoch", Task.completed_at - Task.created_at) / 3600.0, 0)
    tasks = select(
        Task.owner_id,
        func.count().filter(opened, Task.kind == TaskKind.initial_call).label("initialCallsPending"),
        func.count().filter(opened).label("openTasks"),
        func.count().filter(opened, due < now).label("overdueTasks"),
        func.count().filter(opened, Task.escalated.is_(True)).label("escalatedTasks"),
        func.count().filter(opened, remarks.c.task_id.is_(None)).label("missingUpdates"),
        func.count().filter(completed).label("completedTasks"),
        func.avg(hours).filter(completed).label("averageCompletionHours"),
    ).outerjoin(remarks, and_(remarks.c.task_id == Task.id, remarks.c.author_id == Task.owner_id)).group_by(
        Task.owner_id).subquery("task_workload")
    # A single aggregate statement keeps availability and all counters consistent,
    # with no per-user queries and no loading of candidate or remark bodies.
    query = _identity_query(today).add_columns(
        candidates.c.assignedCandidates, *(column for column in tasks.c if column.key != "owner_id"),
    ).outerjoin(candidates, candidates.c.owner_id == User.id).outerjoin(tasks, tasks.c.owner_id == User.id)
    counts = ("assignedCandidates", "initialCallsPending", "openTasks", "overdueTasks", "escalatedTasks",
              "missingUpdates", "completedTasks")
    staff = []
    for row in db.execute(query).mappings():
        staff.append({**_identity(row), **{key: int(row[key] or 0) for key in counts},
                      "averageCompletionHours": round(float(row["averageCompletionHours"]), 2)
                      if row["averageCompletionHours"] is not None else None})
    return {"generatedAt": now.isoformat(), "today": today.isoformat(), "timezone": zone,
            "period": {"from": start.isoformat(), "to": end.isoformat()}, "staff": staff}
