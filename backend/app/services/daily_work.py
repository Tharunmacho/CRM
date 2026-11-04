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

from sqlalchemy import and_, bindparam, case, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import String

from app.core.config import get_settings
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AttendanceCalendarDay, AttendanceDutyPlan, AttendanceWeeklyOff,
    AttendanceWorkTiming, Candidate, CandidateStatus, Client, Company, DailyGoal, DailyGoalProgress, DailyWorkItem, Designation,
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

# Both kinds in one statement. stage: the linked task decides. call: the owned, unmatched candidate is done when its
# initial call is recorded (after the allocation) or any stage was completed for it after allocation; it is released
# if it moved to someone else or was closed (deployed / not interested) without either.
_RECONCILE = """
WITH calc AS (
  SELECT i.id, i.kind, t.stage AS t_stage, t.cancelled_at AS t_cancelled,
    CASE WHEN i.kind = 'stage' THEN CASE WHEN t.completed_at IS NOT NULL AND t.owner_id = i.owner_id THEN t.completed_at END
         ELSE CASE WHEN c.owner_id = i.owner_id THEN LEAST(
           CASE WHEN c.call_recorded_at IS NOT NULL AND c.call_recorded_at >= i.allocated_at AND c.call_called
                 AND c.call_interested IN ('yes', 'no') THEN c.call_recorded_at END,
           st.done_at) END END AS done_at,
    CASE WHEN i.kind = 'stage' THEN (t.cancelled_at IS NOT NULL OR t.owner_id <> i.owner_id)
         ELSE (c.owner_id IS DISTINCT FROM i.owner_id OR c.status IN ('deployed', 'not_interested')) END AS gone,
    (SELECT MIN(r.created_at) FROM workflow_task_remarks r WHERE r.task_id = i.task_id AND r.author_id = i.owner_id) AS first_remark,
    ((i.work_date + 1)::timestamp AT TIME ZONE :tz) AS next_day
  FROM workflow_daily_work_items i
  JOIN crm_candidates c ON c.id = i.candidate_id
  LEFT JOIN workflow_tasks t ON t.id = i.task_id
  LEFT JOIN LATERAL (
    SELECT MIN(s.completed_at) AS done_at FROM crm_application_stages s JOIN crm_applications a ON a.id = s.application_id
    WHERE a.candidate_id = i.candidate_id AND s.completed_at >= i.allocated_at AND s.reopened_reason IS NULL) st ON i.kind = 'call'
  WHERE i.status = 'open' {scope}
  FOR UPDATE OF i SKIP LOCKED
), calc2 AS (
  SELECT calc.*, CASE WHEN done_at IS NOT NULL THEN 'completed' WHEN gone THEN 'released' ELSE 'open' END AS new_status FROM calc
)
UPDATE workflow_daily_work_items i SET
  status = c.new_status,
  completed_at = CASE WHEN c.new_status = 'completed' THEN c.done_at ELSE i.completed_at END,
  completed_by = CASE WHEN c.new_status = 'completed' THEN COALESCE(CAST(:actor AS varchar),
      (SELECT s.completed_by FROM crm_application_stages s WHERE s.application_id = i.application_id AND s.stage = c.t_stage
       ORDER BY s.completed_at DESC LIMIT 1)) ELSE i.completed_by END,
  released_at = CASE WHEN c.new_status = 'released' THEN :now ELSE i.released_at END,
  release_reason = CASE WHEN c.new_status = 'released'
      THEN CASE WHEN c.t_cancelled IS NOT NULL THEN 'task_cancelled' ELSE 'reassigned' END ELSE i.release_reason END,
  started_at = COALESCE(i.started_at, c.first_remark, CASE WHEN c.new_status = 'completed' THEN c.done_at END),
  overdue_at = COALESCE(i.overdue_at, CASE WHEN (c.new_status = 'open' AND i.due_at < :now)
      OR (c.new_status = 'completed' AND c.done_at > i.due_at) THEN i.due_at END),
  postponed_at = COALESCE(i.postponed_at, CASE WHEN (c.new_status = 'open' AND i.work_date < :today)
      OR (c.new_status = 'completed' AND c.done_at >= c.next_day) THEN c.next_day END)
FROM calc2 c
WHERE c.id = i.id AND (
  c.new_status <> 'open'
  OR (i.overdue_at IS NULL AND i.due_at < :now)
  OR (i.postponed_at IS NULL AND i.work_date < :today)
  OR (i.started_at IS NULL AND c.first_remark IS NOT NULL))
"""


def reconcile(db, *, now: datetime | None = None, tz=None, task_ids: list[str] | None = None, actor_id: str | None = None) -> int:
    """Bring the stored status and timestamps of open items up to date with their tasks/candidates. One statement.

    Only rows that really change are written, so a quiet system produces no change notifications. Caller commits.
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


def _call_done():
    """The initial call has a real outcome: made, with interest answered yes or no (an unanswered call stays open)."""
    return and_(Candidate.call_recorded_at.is_not(None), Candidate.call_called.is_(True), Candidate.call_interested.in_(["yes", "no"]))


def _pool(db, uid: str, need: int, today: date) -> list[dict]:
    """Candidates to hand this recruiter, best first, at most `need`.

    1. Their open application-stage tasks (matched candidates waiting on a stage), oldest first.
    2. Candidates they OWN that are not matched to any order yet (status new or in process), oldest first, those whose
       initial call is not recorded before the ones already called and interested. A called-and-interested candidate
       stays on the daily list until matched to an order; only not-interested, deployed or ownerless candidates drop out.
    A candidate with an unfinished item is never picked again (it carries over instead), and nobody is picked twice
    on the same day (finishing an item does not hand the same candidate back that day).
    """
    busy = or_(
        exists().where(DailyWorkItem.candidate_id == Candidate.id, DailyWorkItem.status == "open"),
        exists().where(DailyWorkItem.candidate_id == Candidate.id, DailyWorkItem.work_date == today,
                       DailyWorkItem.status != "released"),
    )
    found: list[dict] = []
    stage_rows = db.execute(select(Task.id, Task.application_id, Task.candidate_id, Task.stage)
        .join(Application, Application.id == Task.application_id)
        .join(Candidate, Candidate.id == Task.candidate_id)
        .where(Task.owner_id == uid, Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
               Task.completed_at.is_(None), Task.cancelled_at.is_(None),
               Application.status == ApplicationStatus.active, ~busy)
        .order_by(Task.created_at, Task.id).limit(need)).all()
    found += [{"kind": "stage", "task_id": r[0], "application_id": r[1], "candidate_id": r[2], "stage": r[3]} for r in stage_rows]
    if len(found) < need:
        matched = exists().where(Application.candidate_id == Candidate.id, Application.status == ApplicationStatus.active)
        call_rows = db.scalars(select(Candidate.id).where(
            Candidate.owner_id == uid, Candidate.status.in_([CandidateStatus.new, CandidateStatus.in_process]),
            ~matched, ~busy,
        ).order_by(case((_call_done(), 1), else_=0), Candidate.created_at, Candidate.id).limit(need - len(found))).all()
        found += [{"kind": "call", "task_id": None, "application_id": None, "candidate_id": cid, "stage": None} for cid in call_rows]
    return found


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
    have = dict(db.execute(select(DailyWorkItem.owner_id, func.count()).where(
        DailyWorkItem.owner_id.in_(people), DailyWorkItem.work_date == today, DailyWorkItem.status != "released",
    ).group_by(DailyWorkItem.owner_id)).all())
    created: dict[str, int] = {}
    for uid in people:
        quota, morning = quota_for(goals[uid])
        if uid in off or have.get(uid, 0) >= quota:
            continue
        # One recruiter's top-up at a time, so two requests cannot hand out the same slot or candidate.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"daily-work:{uid}"})
        used = set(db.scalars(select(DailyWorkItem.slot_no).where(
            DailyWorkItem.owner_id == uid, DailyWorkItem.work_date == today, DailyWorkItem.status != "released")))
        need = quota - len(used)
        if need <= 0:
            continue
        pool = _pool(db, uid, need, today)
        if not pool:
            continue
        start, pause = shifts[uid]
        free = [s for s in slot_plan(today, start, pause, max(quota, max(used, default=0)), morning, tz) if s.no not in used]
        # An allocation made after a slot's deadline still gives the recruiter an hour per candidate.
        previous = None
        for slot, pick in zip(free, pool):
            due = slot.due_at
            if previous is not None or due <= now:
                due = max(due, (previous or now) + timedelta(hours=1))
                previous = due
            try:
                with db.begin_nested():
                    db.execute(insert(DailyWorkItem).values(
                        owner_id=uid, work_date=today, kind=pick["kind"], task_id=pick["task_id"],
                        application_id=pick["application_id"], candidate_id=pick["candidate_id"], stage=pick["stage"],
                        slot_no=slot.no, session=slot.session, due_at=due, allocated_at=now))
                created[uid] = created.get(uid, 0) + 1
            except IntegrityError:
                pass  # another request just took this candidate; the next slot will pick the next one
    return created


def work_shortfalls(db, goals_by_user: dict, rows_by_owner: dict[str, list[dict]], today: str, off: set[str], people: list[str]) -> dict[str, dict | None]:
    """Who has fewer candidates today than their goal, and why (so the admin can assign candidates)."""
    owned = dict(db.execute(select(Candidate.owner_id, func.count()).where(
        Candidate.owner_id.in_(people or [""]), Candidate.status.in_([CandidateStatus.new, CandidateStatus.in_process]),
    ).group_by(Candidate.owner_id)).all())
    result: dict[str, dict | None] = {}
    for uid in people:
        target = goals_by_user[uid]["candidates"]["target"]
        got = sum(1 for r in rows_by_owner.get(uid, []) if r["workDate"] == today)
        if uid in off or got >= target:
            result[uid] = None
            continue
        carried = sum(1 for r in rows_by_owner.get(uid, []) if r["postponed"])
        if owned.get(uid, 0) == 0 and got == 0 and carried == 0:
            reason = "No candidates assigned to this staff member. Assign candidates from the Data Bank."
        elif carried:
            reason = (f"Only {got} new {'candidate' if got == 1 else 'candidates'} today besides {carried} postponed. "
                      "Assign more candidates from the Data Bank.")
        else:
            reason = f"Only {got} {'candidate is' if got == 1 else 'candidates are'} waiting for this staff member. Assign more candidates from the Data Bank."
        result[uid] = {"target": target, "allocated": got, "reason": reason}
    return result


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
    live = or_(
        and_(DailyWorkItem.kind == "stage", Task.cancelled_at.is_(None), Task.owner_id == DailyWorkItem.owner_id),
        and_(DailyWorkItem.kind == "call", Candidate.owner_id == DailyWorkItem.owner_id,
             or_(_call_done(), DailyWorkItem.status == "completed",
                 Candidate.status.notin_([CandidateStatus.deployed, CandidateStatus.not_interested]))))
    done_at = case((DailyWorkItem.kind == "stage", Task.completed_at),
                   else_=func.coalesce(case((and_(_call_done(), Candidate.call_recorded_at >= DailyWorkItem.allocated_at), Candidate.call_recorded_at), else_=None), DailyWorkItem.completed_at))
    rows = db.execute(
        select(DailyWorkItem, Task, Candidate, done_at)
        .join(Candidate, Candidate.id == DailyWorkItem.candidate_id)
        .outerjoin(Task, Task.id == DailyWorkItem.task_id)
        .where(DailyWorkItem.owner_id.in_(owner_ids), DailyWorkItem.work_date <= today, DailyWorkItem.status != "released", live,
               or_(DailyWorkItem.work_date == today, done_at.is_(None), done_at >= day_start))
        .order_by(DailyWorkItem.work_date, DailyWorkItem.slot_no, DailyWorkItem.id).limit(MAX_ROWS)
    ).all()
    # The placement behind each row: its own application, or (for an owned candidate matched since) the active one.
    app_ids = [item.application_id for item, *_ in rows if item.application_id]
    loose = [cand.id for item, _t, cand, _d in rows if not item.application_id]
    app_query = (select(Application, Order, Company, Client, Designation)
                 .join(Order, Order.id == Application.order_id).join(Company, Company.id == Order.company_id)
                 .join(Client, Client.id == Company.client_id).join(Designation, Designation.id == Order.designation_id))
    placements = {a.id: (a, o, co, cl, d) for a, o, co, cl, d in db.execute(app_query.where(Application.id.in_(app_ids or [""]))).all()}
    active = {a.candidate_id: (a, o, co, cl, d) for a, o, co, cl, d in db.execute(
        app_query.where(Application.candidate_id.in_(loose or [""]), Application.status == ApplicationStatus.active)).all()}
    # Remarks live on tasks: the stage task, or for a call item the candidate's open initial-call task, if any.
    call_tasks = dict(db.execute(select(Task.candidate_id, Task.id).where(
        Task.candidate_id.in_(loose or [""]), Task.kind == TaskKind.initial_call,
        Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.created_at.desc())).all())
    remark_tasks = {item.id: (item.task_id or call_tasks.get(cand.id)) for item, _t, cand, _d in rows}
    remarks = _remarks(db, [t for t in remark_tasks.values() if t])
    out = []
    for item, task, cand, finished in rows:
        placement = placements.get(item.application_id) if item.application_id else active.get(cand.id)
        app, order, company, client, designation = placement if placement else (None, None, None, None, None)
        done = finished is not None
        postponed = not done and item.work_date < today
        remark_task = remark_tasks.get(item.id)
        remark = remarks.get(remark_task) if remark_task else None
        preferred = (cand.designations or [None])[0]
        out.append({
            "id": item.id, "kind": item.kind, "taskId": remark_task, "applicationId": app.id if app else None,
            "candidateId": cand.id, "ownerId": item.owner_id, "workDate": item.work_date.isoformat(), "slotNo": item.slot_no,
            "session": item.session, "dueAt": item.due_at, "allocatedStage": item.stage,
            "stage": app.current_stage if app else None,
            "status": "done" if done else "open", "completedAt": finished,
            "allocatedAt": item.allocated_at, "startedAt": item.started_at,
            "overdueAt": item.overdue_at or (item.due_at if (not done and item.due_at < now) or (done and finished > item.due_at) else None),
            "postponed": postponed, "daysLate": (today - item.work_date).days if postponed else 0,
            "client": company.name if company else None, "clientContact": client.name if client else None,
            "country": (order.country or company.country) if order else None,
            "candidate": cand.name, "candidateRef": cand.ref,
            "position": designation.title if designation else preferred,
            "orderRef": order.ref if order else None,
            "referral": {"source": cand.source.value, "label": SOURCE_LABELS.get(cand.source.value, "Other"),
                         "detail": cand.source_detail if cand.source == Source.referral else None},
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


def average_goals(parts: list[dict], fallback: dict) -> tuple[dict, bool]:
    """Per-recruiter average of several goal blocks (targets rounded, achieved counts to one decimal).

    Returns (block, targets_vary). With nobody to average, the `fallback` block (agency default targets) is used.
    """
    if not parts:
        return fallback, False
    n = len(parts)

    def mean(key, field, digits=None):
        value = sum(p[key][field] for p in parts) / n
        return round(value) if digits is None else round(value, digits)

    morning, target = mean("candidates", "morning"), mean("candidates", "target")
    morning = min(morning, target)
    block = {
        "candidates": {"target": target, "morning": morning, "evening": target - morning,
                       "done": mean("candidates", "done", 1), "morningDone": mean("candidates", "morningDone", 1),
                       "eveningDone": mean("candidates", "eveningDone", 1)},
        "forwardedToClient": {"target": mean("forwardedToClient", "target"), "actual": mean("forwardedToClient", "actual", 1)},
        "stageMoves": {"target": mean("stageMoves", "target"), "actual": mean("stageMoves", "actual", 1)},
    }
    shape = lambda p: (p["candidates"]["target"], p["candidates"]["morning"], p["forwardedToClient"]["target"], p["stageMoves"]["target"])  # noqa: E731
    return block, len({shape(p) for p in parts}) > 1


# ------------------------------------------------------------------------------------------------ worker

def scan_daily_work(db, supervisors: list[str], *, now: datetime | None = None, tz=None) -> dict:
    """Worker pass: allocate for everyone, then alert on overdue slots and on work carried into a new day."""
    now = now or datetime.now(timezone.utc)
    today = agency_today(now, tz)
    counts = {"dailyAllocated": sum(ensure_allocations(db, now=now, tz=tz).values()), "dailyOverdue": 0, "dailyPostponed": 0}
    names = dict(db.execute(select(User.id, User.name)).all())
    zone = agency_zone(tz)

    overdue = db.execute(select(DailyWorkItem, Candidate.name, Designation.title, Candidate.designations)
        .join(Candidate, Candidate.id == DailyWorkItem.candidate_id)
        .outerjoin(Application, Application.id == DailyWorkItem.application_id)
        .outerjoin(Order, Order.id == Application.order_id).outerjoin(Designation, Designation.id == Order.designation_id)
        .where(DailyWorkItem.work_date == today, DailyWorkItem.due_at < now, DailyWorkItem.overdue_notified_at.is_(None),
               DailyWorkItem.status == "open")
        .order_by(DailyWorkItem.due_at, DailyWorkItem.id).with_for_update(of=DailyWorkItem, skip_locked=True)).all()
    for item, candidate, position, preferred in overdue:
        label = position or (preferred or [None])[0] or "initial call"
        db.add(Notification(user_id=item.owner_id, kind="daily_work", title="Daily task overdue",
                            detail=f"{candidate} · {label} was due at {item.due_at.astimezone(zone):%H:%M}. Finish this now.",
                            link_to="/tasks"))
        item.overdue_notified_at = now
        counts["dailyOverdue"] += 1

    late = db.execute(select(DailyWorkItem)
        .where(DailyWorkItem.work_date < today, DailyWorkItem.postponed_notified_at.is_(None), DailyWorkItem.status == "open")
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
