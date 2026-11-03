"""Daily candidate quota: automatic allocation, hourly slots, carry-over, goals, summaries and alerts.

Rules (all times in the agency timezone):

* Every active Staff recruiter who works that day is handed their daily goal of candidates (agency default 5, or the
  admin's per-person value): the oldest open application-stage tasks they own, one candidate per task. Nobody
  allocates by hand; the screen, the workflow worker and the supervisor view all call `ensure_allocations`, which
  is idempotent and race-safe.
* The quota is split into morning slots (default 3) and evening slots (the rest), one candidate per hour: with the
  recruiter's shift (default 10:00-19:00, 60 min break) the deadlines are 11:00, 12:00, 13:00 and, after the break,
  15:00 and 16:00.
* An item is finished when its stage task is completed through the stage workflow (`record_completion` is called
  from that transaction; `reconcile` repairs anything completed by another path). It is released, and a replacement
  drawn, when the task is cancelled (unmapped, closed, reopened) or reassigned.
* An item still open after its day is *postponed*: it stays in the list, oldest first, next to the new day's full
  quota. Nothing is capped or dropped, so finishing 4 of 5 gives 1 postponed + 5 new = 6 the next working day.
* Goals (candidates, forwarded to the client, stage moves) are data in `workflow_daily_goals`; progress is computed
  from the real records and a per-day snapshot is kept in `workflow_daily_goal_progress` for reports.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, bindparam, exists, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.types import String

from app.core.config import get_settings
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AttendanceCalendarDay, AttendanceDutyPlan, AttendanceWeeklyOff,
    AttendanceWorkTiming, Candidate, Client, Company, DailyGoal, DailyGoalProgress, DailyWorkItem, Designation,
    Notification, Order, Remark, Role, Source, Task, TaskKind, User,
)

FALLBACK_GOALS = {"candidates": (5, 3), "forwarded_to_client": (2, None), "stage_moves": (5, None)}
METRICS = tuple(FALLBACK_GOALS)
MAX_ROWS = 600
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SOURCE_LABELS = {"email": "Email", "whatsapp": "WhatsApp", "walk_in": "Walk-in", "referral": "Referral", "other": "Other"}


@dataclass(frozen=True)
class Slot:
    no: int
    session: str
    due_at: datetime


def tz_name(tz=None) -> str:
    """The agency timezone name; tolerant of stripped-down settings objects used by isolated callers."""
    return tz or getattr(get_settings(), "agency_timezone", None) or "Asia/Kolkata"


def agency_zone(tz=None) -> ZoneInfo:
    return ZoneInfo(tz_name(tz))


def agency_today(now: datetime, tz=None) -> date:
    return now.astimezone(agency_zone(tz)).date()


def day_bounds(day: date, tz=None) -> tuple[datetime, datetime]:
    zone = agency_zone(tz)
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    return start, datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)


# ------------------------------------------------------------------------------------------------ goals

def load_goals(db, user_ids: list[str]) -> dict[str, dict]:
    """Effective goals per person: their own row, else the agency default, else the built-in fallback.

    Each value is {"candidates": (target, morning), "forwarded_to_client": target, "stage_moves": target,
    "own": set of metrics that come from the person's own row}.
    """
    rows = db.execute(select(DailyGoal.user_id, DailyGoal.metric, DailyGoal.target, DailyGoal.morning_target).where(
        or_(DailyGoal.user_id.is_(None), DailyGoal.user_id.in_(user_ids or [""])))).all()
    agency = {m: v for m, v in FALLBACK_GOALS.items()}
    own: dict[str, dict] = defaultdict(dict)
    for uid, metric, target, morning in rows:
        if uid is None:
            agency[metric] = (target, morning)
        else:
            own[uid][metric] = (target, morning)
    result = {}
    for uid in user_ids:
        merged = {m: own[uid].get(m, agency[m]) for m in METRICS}
        target, morning = merged["candidates"]
        result[uid] = {
            "candidates": (target, min(target, morning if morning is not None else FALLBACK_GOALS["candidates"][1])),
            "forwarded_to_client": merged["forwarded_to_client"][0], "stage_moves": merged["stage_moves"][0],
            "own": set(own[uid]),
        }
    return result


def quota_for(goals: dict) -> tuple[int, int]:
    return goals["candidates"]


# ------------------------------------------------------------------------------------------------ slots

def slot_plan(day: date, start: time, break_minutes: int, quota: int, morning: int, tz=None) -> list[Slot]:
    """Hourly deadlines for one day: `morning` slots from the shift start, the rest after the break."""
    zone = agency_zone(tz)
    begin = datetime.combine(day, start, zone)
    slots = []
    for i in range(1, morning + 1):
        slots.append(Slot(i, "morning", (begin + timedelta(hours=i)).astimezone(timezone.utc)))
    evening_begin = begin + timedelta(hours=morning, minutes=break_minutes if morning else 0)
    for j in range(1, quota - morning + 1):
        slots.append(Slot(morning + j, "evening", (evening_begin + timedelta(hours=j)).astimezone(timezone.utc)))
    return slots


def _shift_of(raw) -> tuple[time, int]:
    from app.services.attendance_hr import _shift_value
    shift = _shift_value(raw)
    return shift.start, shift.break_minutes


def shifts_and_days_off(db, user_ids: list[str], day: date) -> tuple[dict[str, tuple[time, int]], set[str]]:
    """Each person's shift for the day and who is off (explicit calendar entry, weekly off, holiday or leave).

    Mirrors the attendance rules: a one-date duty plan overrides a weekly off or holiday (never approved leave);
    Sunday is the weekly off until someone picks another day for that week.
    """
    if not user_ids:
        return {}, set()
    plans = {r.user_id: r for r in db.scalars(select(AttendanceDutyPlan).where(
        AttendanceDutyPlan.user_id.in_(user_ids), AttendanceDutyPlan.attendance_date == day))}
    timings: dict[str, AttendanceWorkTiming] = {}
    for r in db.scalars(select(AttendanceWorkTiming).where(
            AttendanceWorkTiming.user_id.in_(user_ids), AttendanceWorkTiming.effective_from <= day,
    ).order_by(AttendanceWorkTiming.effective_from.desc())):
        timings.setdefault(r.user_id, r)
    marks = {r.user_id: r.status for r in db.scalars(select(AttendanceCalendarDay).where(
        AttendanceCalendarDay.user_id.in_(user_ids), AttendanceCalendarDay.attendance_date == day))}
    week = day - timedelta(days=day.weekday())
    weekly = {r.user_id: r.day for r in db.scalars(select(AttendanceWeeklyOff).where(
        AttendanceWeeklyOff.user_id.in_(user_ids), AttendanceWeeklyOff.week_start == week))}
    shifts, off = {}, set()
    for uid in user_ids:
        plan = plans.get(uid)
        shifts[uid] = _shift_of(plan.shift if plan else timings[uid].shift if uid in timings else None)
        mark = marks.get(uid)
        if mark in ("PL", "UL"):
            off.add(uid)
        elif plan is None:
            if mark in ("WO", "H") or (mark is None and day.weekday() == (WEEKDAYS.index(weekly[uid]) if uid in weekly else 6)):
                off.add(uid)
    return shifts, off


# ------------------------------------------------------------------------------------------------ lifecycle

_RECONCILE = """
WITH calc AS (
  SELECT i.id, t.completed_at AS t_done, t.stage AS t_stage, t.cancelled_at AS t_cancelled,
    CASE WHEN t.completed_at IS NOT NULL AND t.owner_id = i.owner_id THEN 'completed'
         WHEN t.cancelled_at IS NOT NULL OR t.owner_id <> i.owner_id THEN 'released'
         ELSE 'open' END AS new_status,
    (SELECT MIN(r.created_at) FROM workflow_task_remarks r WHERE r.task_id = i.task_id AND r.author_id = i.owner_id) AS first_remark,
    ((i.work_date + 1)::timestamp AT TIME ZONE :tz) AS next_day
  FROM workflow_daily_work_items i JOIN workflow_tasks t ON t.id = i.task_id
  WHERE i.status = 'open' {scope}
  FOR UPDATE OF i SKIP LOCKED
)
UPDATE workflow_daily_work_items i SET
  status = c.new_status,
  completed_at = CASE WHEN c.new_status = 'completed' THEN c.t_done ELSE i.completed_at END,
  completed_by = CASE WHEN c.new_status = 'completed' THEN COALESCE(CAST(:actor AS varchar),
      (SELECT s.completed_by FROM crm_application_stages s WHERE s.application_id = i.application_id AND s.stage = c.t_stage
       ORDER BY s.completed_at DESC LIMIT 1)) ELSE i.completed_by END,
  released_at = CASE WHEN c.new_status = 'released' THEN :now ELSE i.released_at END,
  release_reason = CASE WHEN c.new_status = 'released'
      THEN CASE WHEN c.t_cancelled IS NOT NULL THEN 'task_cancelled' ELSE 'reassigned' END ELSE i.release_reason END,
  started_at = COALESCE(i.started_at, c.first_remark, CASE WHEN c.new_status = 'completed' THEN c.t_done END),
  overdue_at = COALESCE(i.overdue_at, CASE WHEN (c.new_status = 'open' AND i.due_at < :now)
      OR (c.new_status = 'completed' AND c.t_done > i.due_at) THEN i.due_at END),
  postponed_at = COALESCE(i.postponed_at, CASE WHEN (c.new_status = 'open' AND i.work_date < :today)
      OR (c.new_status = 'completed' AND c.t_done >= c.next_day) THEN c.next_day END)
FROM calc c
WHERE c.id = i.id AND (
  c.new_status <> 'open'
  OR (i.overdue_at IS NULL AND i.due_at < :now)
  OR (i.postponed_at IS NULL AND i.work_date < :today)
  OR (i.started_at IS NULL AND c.first_remark IS NOT NULL))
"""


def reconcile(db, *, now: datetime | None = None, tz=None, task_ids: list[str] | None = None, actor_id: str | None = None) -> int:
    """Bring the stored status and timestamps of open items up to date with their tasks. One statement; caller commits.

    Only rows that really change are written, so a quiet system produces no change notifications.
    """
    now = now or datetime.now(timezone.utc)
    statement = text(_RECONCILE.format(scope="AND i.task_id = ANY(:task_ids)" if task_ids else ""))
    params = {"tz": tz_name(tz), "now": now, "today": agency_today(now, tz), "actor": actor_id}
    if task_ids:
        statement = statement.bindparams(bindparam("task_ids", type_=ARRAY(String)))
        params["task_ids"] = list(task_ids)
    return db.execute(statement, params).rowcount or 0


def record_completion(db, task_ids: list[str], user_id: str, at: datetime, *, tz=None) -> None:
    """Called inside the stage-completion transaction: stamp the finished items (status, completed_at/by, lateness)."""
    if task_ids:
        db.flush()
        reconcile(db, now=at, tz=tz, task_ids=task_ids, actor_id=user_id)


def mark_started(db, application_id: str, at: datetime) -> None:
    """First draft save on an application starts the work on its open daily item."""
    db.execute(text("UPDATE workflow_daily_work_items SET started_at = :at WHERE application_id = :a AND status = 'open' AND started_at IS NULL"),
               {"at": at, "a": application_id})


def _live_item():
    """An item still counts while its task is not cancelled and still belongs to the item's recruiter."""
    return and_(DailyWorkItem.status != "released", Task.cancelled_at.is_(None), Task.owner_id == DailyWorkItem.owner_id)


def ensure_allocations(db, staff_ids: list[str] | None = None, *, now: datetime | None = None, tz=None) -> dict:
    """Top every working recruiter up to today's goal. Returns {user id: new item count}. Caller commits."""
    now = now or datetime.now(timezone.utc)
    today = agency_today(now, tz)
    reconcile(db, now=now, tz=tz)
    query = select(User.id).where(User.active.is_(True), User.role == Role.staff, User.on_leave.is_(False))
    if staff_ids is not None:
        query = query.where(User.id.in_(staff_ids))
    people = list(db.scalars(query.order_by(User.id)))
    if not people:
        return {}
    goals = load_goals(db, people)
    shifts, off = shifts_and_days_off(db, people, today)
    have = dict(db.execute(select(DailyWorkItem.owner_id, func.count()).join(Task, Task.id == DailyWorkItem.task_id).where(
        DailyWorkItem.owner_id.in_(people), DailyWorkItem.work_date == today, _live_item(),
    ).group_by(DailyWorkItem.owner_id)).all())
    created: dict[str, int] = {}
    for uid in people:
        quota, morning = quota_for(goals[uid])
        if uid in off or have.get(uid, 0) >= quota:
            continue
        # One recruiter's top-up at a time, so two requests cannot hand out the same slot or candidate.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"daily-work:{uid}"})
        used = set(db.scalars(select(DailyWorkItem.slot_no).join(Task, Task.id == DailyWorkItem.task_id).where(
            DailyWorkItem.owner_id == uid, DailyWorkItem.work_date == today, _live_item())))
        need = quota - len(used)
        if need <= 0:
            continue
        taken = exists().where(DailyWorkItem.task_id == Task.id, DailyWorkItem.owner_id == uid, DailyWorkItem.status != "released")
        pool = list(db.execute(select(Task.id, Task.application_id, Task.candidate_id, Task.stage)
            .join(Application, Application.id == Task.application_id)
            .where(Task.owner_id == uid, Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
                   Task.completed_at.is_(None), Task.cancelled_at.is_(None),
                   Application.status == ApplicationStatus.active, ~taken)
            .order_by(Task.created_at, Task.id).limit(need)).all())
        if not pool:
            continue
        start, pause = shifts[uid]
        free = [s for s in slot_plan(today, start, pause, max(quota, max(used, default=0)), morning, tz) if s.no not in used]
        # An allocation made after a slot's deadline still gives the recruiter an hour per candidate.
        previous = None
        for slot, (task_id, application_id, candidate_id, stage) in zip(free, pool):
            due = slot.due_at
            if previous is not None or due <= now:
                due = max(due, (previous or now) + timedelta(hours=1))
                previous = due
            inserted = db.scalar(insert(DailyWorkItem).values(
                owner_id=uid, work_date=today, task_id=task_id, application_id=application_id,
                candidate_id=candidate_id, stage=stage, slot_no=slot.no, session=slot.session, due_at=due,
                allocated_at=now,
            ).on_conflict_do_nothing(index_elements=["task_id", "owner_id"], index_where=text("status <> 'released'"))
                .returning(DailyWorkItem.id))
            if inserted:
                created[uid] = created.get(uid, 0) + 1
    return created


# ------------------------------------------------------------------------------------------------ reads

def _remarks(db, task_ids: list[str]) -> dict[str, dict]:
    if not task_ids:
        return {}
    result: dict[str, dict] = {}
    names = dict(db.execute(select(User.id, User.name)).all())
    for body, author, at, task_id in db.execute(select(Remark.body, Remark.author_id, Remark.created_at, Remark.task_id)
            .where(Remark.task_id.in_(task_ids)).order_by(Remark.created_at.desc(), Remark.id)):
        entry = result.setdefault(task_id, {"body": body, "at": at, "by": names.get(author, "Unknown"), "count": 0})
        entry["count"] += 1
    return result


def load_rows(db, owner_ids: list[str], *, now: datetime | None = None, tz=None) -> list[dict]:
    """Today's list for these recruiters: today's quota, everything postponed and backlog cleared today."""
    now = now or datetime.now(timezone.utc)
    today = agency_today(now, tz)
    day_start, _ = day_bounds(today, tz)
    if not owner_ids:
        return []
    rows = db.execute(
        select(DailyWorkItem, Task, Application, Order, Company, Client, Designation, Candidate)
        .join(Task, Task.id == DailyWorkItem.task_id)
        .join(Application, Application.id == DailyWorkItem.application_id)
        .join(Order, Order.id == Application.order_id)
        .join(Company, Company.id == Order.company_id)
        .join(Client, Client.id == Company.client_id)
        .join(Designation, Designation.id == Order.designation_id)
        .join(Candidate, Candidate.id == DailyWorkItem.candidate_id)
        .where(DailyWorkItem.owner_id.in_(owner_ids), DailyWorkItem.work_date <= today, _live_item(),
               or_(DailyWorkItem.work_date == today, Task.completed_at.is_(None), Task.completed_at >= day_start))
        .order_by(DailyWorkItem.work_date, DailyWorkItem.slot_no, DailyWorkItem.id).limit(MAX_ROWS)
    ).all()
    remarks = _remarks(db, [task.id for _item, task, *_rest in rows])
    out = []
    for item, task, app, order, company, client, designation, candidate in rows:
        done = task.completed_at is not None
        postponed = not done and item.work_date < today
        remark = remarks.get(task.id)
        out.append({
            "id": item.id, "taskId": task.id, "applicationId": app.id, "candidateId": candidate.id,
            "ownerId": item.owner_id, "workDate": item.work_date.isoformat(), "slotNo": item.slot_no,
            "session": item.session, "dueAt": item.due_at, "allocatedStage": item.stage, "stage": app.current_stage,
            "status": "done" if done else "open", "completedAt": task.completed_at,
            "allocatedAt": item.allocated_at, "startedAt": item.started_at,
            "overdueAt": item.overdue_at or (item.due_at if (not done and item.due_at < now) or (done and task.completed_at > item.due_at) else None),
            "postponed": postponed, "daysLate": (today - item.work_date).days if postponed else 0,
            "client": company.name, "clientContact": client.name,
            "country": order.country or company.country,
            "candidate": candidate.name, "candidateRef": candidate.ref,
            "position": designation.title, "orderRef": order.ref,
            "referral": {"source": candidate.source.value, "label": SOURCE_LABELS.get(candidate.source.value, "Other"),
                         "detail": candidate.source_detail if candidate.source == Source.referral else None},
            "remark": {"body": remark["body"], "at": remark["at"], "by": remark["by"], "count": remark["count"]} if remark else None,
        })
    return out


def summarise(rows: list[dict], now: datetime, today: str) -> dict:
    """Progress numbers for one recruiter's rows (the list `load_rows` returned for them); `today` is ISO."""
    open_rows = [r for r in rows if r["status"] == "open"]
    upcoming = sorted(r["dueAt"] for r in open_rows if r["dueAt"] > now)
    todays = [r for r in rows if r["workDate"] == today]
    sessions = {}
    for name in ("morning", "evening"):
        mine = [r for r in todays if r["session"] == name]
        sessions[name] = {"done": sum(r["status"] == "done" for r in mine), "total": len(mine)}
    return {
        "total": len(rows), "done": sum(r["status"] == "done" for r in rows), "open": len(open_rows),
        "postponed": sum(r["postponed"] for r in rows), "overdue": sum(r["dueAt"] <= now for r in open_rows),
        "morning": sessions["morning"], "evening": sessions["evening"],
        "nextDueAt": upcoming[0] if upcoming else None,
        "oldestPostponedDays": max((r["daysLate"] for r in rows if r["postponed"]), default=0),
        "complete": bool(rows) and not open_rows,
    }


def checkpoints(rows: list[dict], now: datetime, today: str) -> dict:
    """The hourly checkpoints of today's slots for one recruiter, and whether they are on pace.

    met = finished by its deadline, late = finished after it, missed = still open past it, upcoming = not due yet.
    """
    mine = sorted((r for r in rows if r["workDate"] == today), key=lambda r: r["slotNo"])
    points = []
    for r in mine:
        if r["status"] == "done":
            state = "met" if r["completedAt"] <= r["dueAt"] else "late"
        else:
            state = "missed" if r["dueAt"] <= now else "upcoming"
        points.append({"slotNo": r["slotNo"], "session": r["session"], "dueAt": r["dueAt"], "state": state,
                       "completedAt": r["completedAt"], "candidate": r["candidate"]})
    should = sum(1 for r in mine if r["dueAt"] <= now)
    done = sum(1 for r in mine if r["status"] == "done")
    return {"points": points, "expectedByNow": should, "doneToday": done, "behindBy": max(0, should - done)}


def goal_progress(db, user_ids: list[str], today: date, *, tz=None, persist: bool = False) -> dict[str, dict]:
    """Per person: each goal's target and today's achieved count, computed from the real records.

    With `persist`, the per-day snapshot rows are written when (and only when) they differ.
    """
    if not user_ids:
        return {}
    start, end = day_bounds(today, tz)
    goals = load_goals(db, user_ids)
    done_rows = db.execute(select(DailyWorkItem.owner_id, DailyWorkItem.session, func.count()).where(
        DailyWorkItem.owner_id.in_(user_ids), DailyWorkItem.status == "completed",
        DailyWorkItem.completed_at >= start, DailyWorkItem.completed_at < end,
    ).group_by(DailyWorkItem.owner_id, DailyWorkItem.session)).all()
    cand = defaultdict(lambda: {"morning": 0, "evening": 0})
    for uid, session, n in done_rows:
        cand[uid][session] = n
    stage_filter = [ApplicationStage.owner_id.in_(user_ids), ApplicationStage.completed_at >= start,
                    ApplicationStage.completed_at < end, ApplicationStage.reopened_reason.is_(None)]
    moves = dict(db.execute(select(ApplicationStage.owner_id, func.count()).where(
        *stage_filter, ApplicationStage.outcome.is_distinct_from("Unfit: reapplying")).group_by(ApplicationStage.owner_id)).all())
    forwarded = dict(db.execute(select(ApplicationStage.owner_id, func.count()).where(
        *stage_filter, ApplicationStage.stage == 2, ApplicationStage.outcome == "Forwarded").group_by(ApplicationStage.owner_id)).all())
    result = {}
    for uid in user_ids:
        target, morning = goals[uid]["candidates"]
        m_done, e_done = cand[uid]["morning"], cand[uid]["evening"]
        result[uid] = {
            "candidates": {"target": target, "morning": morning, "evening": target - morning,
                           "done": m_done + e_done, "morningDone": m_done, "eveningDone": e_done},
            "forwardedToClient": {"target": goals[uid]["forwarded_to_client"], "actual": forwarded.get(uid, 0)},
            "stageMoves": {"target": goals[uid]["stage_moves"], "actual": moves.get(uid, 0)},
        }
    if persist:
        _save_progress(db, result, today)
    return result


def _save_progress(db, progress: dict[str, dict], day: date) -> None:
    ids = list(progress)
    existing = {(r.user_id, r.metric): r for r in db.scalars(select(DailyGoalProgress).where(
        DailyGoalProgress.user_id.in_(ids), DailyGoalProgress.goal_date == day))}
    for uid, goals in progress.items():
        for metric, key in (("candidates", "candidates"), ("forwarded_to_client", "forwardedToClient"), ("stage_moves", "stageMoves")):
            g = goals[key]
            want = (g["target"], g.get("morning"), g["done"] if metric == "candidates" else g["actual"],
                    g.get("morningDone") if metric == "candidates" else None)
            row = existing.get((uid, metric))
            if row is None:
                db.execute(insert(DailyGoalProgress).values(
                    user_id=uid, goal_date=day, metric=metric, target=want[0], morning_target=want[1], actual=want[2],
                    morning_actual=want[3]).on_conflict_do_nothing(constraint="uq_daily_goal_progress"))
            elif (row.target, row.morning_target, row.actual, row.morning_actual) != want:
                row.target, row.morning_target, row.actual, row.morning_actual = want


def add_goal_totals(parts: list[dict]) -> dict:
    """Sum several recruiters' goal blocks for a team view."""
    total = {"candidates": {"target": 0, "morning": 0, "evening": 0, "done": 0, "morningDone": 0, "eveningDone": 0},
             "forwardedToClient": {"target": 0, "actual": 0}, "stageMoves": {"target": 0, "actual": 0}}
    for part in parts:
        for key, block in total.items():
            for field in block:
                block[field] += part[key][field]
    return total


# ------------------------------------------------------------------------------------------------ worker

def scan_daily_work(db, supervisors: list[str], *, now: datetime | None = None, tz=None) -> dict:
    """Worker pass: allocate for everyone, then alert on overdue slots and on work carried into a new day."""
    now = now or datetime.now(timezone.utc)
    today = agency_today(now, tz)
    counts = {"dailyAllocated": sum(ensure_allocations(db, now=now, tz=tz).values()), "dailyOverdue": 0, "dailyPostponed": 0}
    names = dict(db.execute(select(User.id, User.name)).all())
    zone = agency_zone(tz)

    overdue = db.execute(select(DailyWorkItem, Candidate.name, Designation.title)
        .join(Task, Task.id == DailyWorkItem.task_id).join(Candidate, Candidate.id == DailyWorkItem.candidate_id)
        .join(Application, Application.id == DailyWorkItem.application_id)
        .join(Order, Order.id == Application.order_id).join(Designation, Designation.id == Order.designation_id)
        .where(DailyWorkItem.work_date == today, DailyWorkItem.due_at < now, DailyWorkItem.overdue_notified_at.is_(None),
               DailyWorkItem.status == "open", Task.completed_at.is_(None), _live_item())
        .order_by(DailyWorkItem.due_at, DailyWorkItem.id).with_for_update(of=DailyWorkItem, skip_locked=True)).all()
    for item, candidate, position in overdue:
        db.add(Notification(user_id=item.owner_id, kind="daily_work", title="Daily task overdue",
                            detail=f"{candidate} · {position} was due at {item.due_at.astimezone(zone):%H:%M}. Finish this stage now.",
                            link_to="/tasks"))
        item.overdue_notified_at = now
        counts["dailyOverdue"] += 1

    late = db.execute(select(DailyWorkItem).join(Task, Task.id == DailyWorkItem.task_id)
        .where(DailyWorkItem.work_date < today, DailyWorkItem.postponed_notified_at.is_(None),
               DailyWorkItem.status == "open", Task.completed_at.is_(None), _live_item())
        .order_by(DailyWorkItem.owner_id, DailyWorkItem.work_date, DailyWorkItem.slot_no)
        .with_for_update(of=DailyWorkItem, skip_locked=True)).scalars().all()
    grouped: dict[str, list[DailyWorkItem]] = defaultdict(list)
    for item in late:
        grouped[item.owner_id].append(item)
    for owner, items in grouped.items():
        n = len(items)
        noun = "task was" if n == 1 else "tasks were"
        db.add(Notification(user_id=owner, kind="daily_work", title="Unfinished tasks carried over",
                            detail=f"{n} {noun} not finished and moved to today. Complete them before today's new candidates.",
                            link_to="/tasks"))
        for supervisor in sorted(set(supervisors)):
            db.add(Notification(user_id=supervisor, kind="daily_work", title=f"{names.get(owner, 'Staff')}: tasks postponed",
                                detail=f"{n} daily {'task' if n == 1 else 'tasks'} carried over from earlier days.",
                                link_to=f"/tasks?staff={owner}"))
        for item in items:
            item.postponed_notified_at = now
        counts["dailyPostponed"] += n

    # Keep the report snapshot current for today and close out yesterday.
    staff = list(db.scalars(select(User.id).where(User.role == Role.staff, User.active.is_(True)).order_by(User.id)))
    for day in (today - timedelta(days=1), today):
        goal_progress(db, staff, day, tz=tz, persist=True)
    return counts
