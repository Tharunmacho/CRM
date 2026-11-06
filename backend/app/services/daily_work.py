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
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, bindparam, case, cast, column, exists, func, literal, null, or_, select, text, true, update, values
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.orm import Session
from sqlalchemy.types import String

from app.core.config import get_settings
from app.services.parallel_reads import _SLOTS, parallel_reads
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
    return goals_from_rows(rows, user_ids)


def goals_from_rows(rows, user_ids: list[str]) -> dict[str, dict]:
    """`load_goals` from (user id or None, metric, target, morning target) rows already in hand."""
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


def shifts_and_days_off(db, user_ids: list[str], day: date, *, parallel: bool = False) -> tuple[dict[str, tuple[time, int]], set[str]]:
    """Each person's shift for the day and who is off (explicit calendar entry, weekly off, holiday or leave).

    Mirrors the attendance rules: a one-date duty plan overrides a weekly off or holiday (never approved leave);
    Sunday is the weekly off until someone picks another day for that week.

    `parallel=True` issues the four independent reads at once (one round trip, not four); use it only when this
    transaction has not written to the attendance tables, since the reads then run on their own connections.
    """
    if not user_ids:
        return {}, set()
    reads = attendance_reads(user_ids, day)
    rows = parallel_reads(db, *reads) if parallel else [read(db) for read in reads]
    return compute_shifts(user_ids, day, *rows)


def attendance_reads(user_ids: list[str], day: date) -> tuple:
    """The four independent attendance reads behind `shifts_and_days_off`, as callables for `parallel_reads`."""
    week = day - timedelta(days=day.weekday())
    return (
        lambda s: list(s.scalars(select(AttendanceDutyPlan).where(
            AttendanceDutyPlan.user_id.in_(user_ids), AttendanceDutyPlan.attendance_date == day))),
        lambda s: list(s.scalars(select(AttendanceWorkTiming).where(
            AttendanceWorkTiming.user_id.in_(user_ids), AttendanceWorkTiming.effective_from <= day,
        ).order_by(AttendanceWorkTiming.effective_from.desc()))),
        lambda s: list(s.scalars(select(AttendanceCalendarDay).where(
            AttendanceCalendarDay.user_id.in_(user_ids), AttendanceCalendarDay.attendance_date == day))),
        lambda s: list(s.scalars(select(AttendanceWeeklyOff).where(
            AttendanceWeeklyOff.user_id.in_(user_ids), AttendanceWeeklyOff.week_start == week))),
    )


def compute_shifts(user_ids: list[str], day: date, plan_rows, timing_rows, mark_rows, weekly_rows) -> tuple[dict[str, tuple[time, int]], set[str]]:
    plans = {r.user_id: r for r in plan_rows}
    timings: dict[str, AttendanceWorkTiming] = {}
    for r in timing_rows:
        timings.setdefault(r.user_id, r)
    marks = {r.user_id: r.status for r in mark_rows}
    weekly = {r.user_id: r.day for r in weekly_rows}
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


def _busy(today: date):
    """A candidate that is not available: an unfinished item exists, or one was already handed out today."""
    return or_(
        exists().where(DailyWorkItem.candidate_id == Candidate.id, DailyWorkItem.status == "open"),
        exists().where(DailyWorkItem.candidate_id == Candidate.id, DailyWorkItem.work_date == today,
                       DailyWorkItem.status != "released"),
    )


def _stage_ready(owner, busy):
    """Conditions of a stage-task pick for `owner` (an id or a column): open stage task, active placement, free candidate."""
    return [Task.owner_id == owner, Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
            Task.completed_at.is_(None), Task.cancelled_at.is_(None), Application.status == ApplicationStatus.active, ~busy]


def _call_ready(owner, busy):
    """Conditions of an owned-candidate pick for `owner`: being worked (new / in process), not matched to an order, free."""
    matched = exists().where(Application.candidate_id == Candidate.id, Application.status == ApplicationStatus.active)
    return [Candidate.owner_id == owner, Candidate.status.in_([CandidateStatus.new, CandidateStatus.in_process]), ~matched, ~busy]


def _call_order():
    return (case((_call_done(), 1), else_=0), Candidate.created_at, Candidate.id)


def _pool(db, uid: str, need: int, today: date) -> list[dict]:
    """Candidates to hand this ONE recruiter, best first, at most `need`. `pool_for` does it for everybody in one statement.

    1. Their open application-stage tasks (matched candidates waiting on a stage), oldest first.
    2. Candidates they OWN that are not matched to any order yet (status new or in process), oldest first, those whose
       initial call is not recorded before the ones already called and interested. A called-and-interested candidate
       stays on the daily list until matched to an order; only not-interested, deployed or ownerless candidates drop out.
    A candidate with an unfinished item is never picked again (it carries over instead), and nobody is picked twice
    on the same day (finishing an item does not hand the same candidate back that day).
    """
    busy = _busy(today)
    found: list[dict] = []
    stage_rows = db.execute(select(Task.id, Task.application_id, Task.candidate_id, Task.stage)
        .join(Application, Application.id == Task.application_id)
        .join(Candidate, Candidate.id == Task.candidate_id)
        .where(*_stage_ready(uid, busy))
        .order_by(Task.created_at, Task.id).limit(need)).all()
    found += [{"kind": "stage", "task_id": r[0], "application_id": r[1], "candidate_id": r[2], "stage": r[3]} for r in stage_rows]
    if len(found) < need:
        call_rows = db.scalars(select(Candidate.id).where(*_call_ready(uid, busy))
                               .order_by(*_call_order()).limit(need - len(found))).all()
        found += [{"kind": "call", "task_id": None, "application_id": None, "candidate_id": cid, "stage": None} for cid in call_rows]
    return found


def pool_for(db, needs: dict[str, int], today: date) -> dict[str, list[dict]]:
    """`_pool` for every recruiter in `needs` ({user id: how many}) in ONE statement.

    Each recruiter's best `need` stage tasks come from a LATERAL subquery (the same filters, order and limit as `_pool`, so
    it stays index-friendly per owner); the owned-candidate picks then fill what the stage tasks left (`need` minus the
    stage picks). One statement for one recruiter or fifty. Returns {user id: picks, best first}.
    """
    needs = {uid: n for uid, n in needs.items() if n > 0}
    if not needs:
        return {}
    busy = _busy(today)
    want = values(column("owner_id", String), column("need", Integer), name="want").data(sorted(needs.items()))
    stage = (select(Task.id.label("task_id"), Task.application_id.label("application_id"), Task.candidate_id.label("candidate_id"),
                    Task.stage.label("stage"), func.row_number().over(order_by=(Task.created_at, Task.id)).label("ord"))
             .join(Application, Application.id == Task.application_id).join(Candidate, Candidate.id == Task.candidate_id)
             .where(*_stage_ready(want.c.owner_id, busy)).order_by(Task.created_at, Task.id).limit(want.c.need).lateral("stage_pick"))
    stage_rows = (select(want.c.owner_id.label("owner_id"), literal(0, Integer).label("pri"), stage.c.ord.label("ord"),
                         stage.c.task_id.label("task_id"), stage.c.application_id.label("application_id"),
                         stage.c.candidate_id.label("candidate_id"), stage.c.stage.label("stage"))
                  .select_from(want.join(stage, true())).cte("stage_rows"))
    taken = select(stage_rows.c.owner_id, func.count().label("n")).group_by(stage_rows.c.owner_id).cte("stage_taken")
    call = (select(Candidate.id.label("candidate_id"), func.row_number().over(order_by=_call_order()).label("ord"))
            .where(*_call_ready(want.c.owner_id, busy)).order_by(*_call_order())
            .limit(func.greatest(want.c.need - func.coalesce(taken.c.n, 0), 0)).lateral("call_pick"))
    call_rows = (select(want.c.owner_id, literal(1, Integer), call.c.ord, cast(null(), String), cast(null(), String),
                        call.c.candidate_id, cast(null(), Integer))
                 .select_from(want.outerjoin(taken, taken.c.owner_id == want.c.owner_id).join(call, true())))
    picks: dict[str, list] = defaultdict(list)
    for owner, pri, ord_, task_id, application_id, candidate_id, stage_no in db.execute(select(stage_rows).union_all(call_rows)).all():
        picks[owner].append((pri, ord_, {"kind": "stage" if pri == 0 else "call", "task_id": task_id,
                                         "application_id": application_id, "candidate_id": candidate_id, "stage": stage_no}))
    return {owner: [pick for _p, _o, pick in sorted(found, key=lambda f: (f[0], f[1]))] for owner, found in picks.items()}


def pool_waiting(db, uids: list[str], today: date) -> set[str]:
    """Which of these recruiters have at least one candidate to hand out right now (one statement, no ordering).

    Lets a caller skip the lock-and-allocate path for a short-handed recruiter whose pool is empty, without caching
    anything: the answer is read in the same round trip as everything else.
    """
    if not uids:
        return set()
    busy = _busy(today)
    stage = exists(select(Task.id).join(Application, Application.id == Task.application_id)
                   .join(Candidate, Candidate.id == Task.candidate_id).where(*_stage_ready(User.id, busy)))
    call = exists(select(Candidate.id).where(*_call_ready(User.id, busy)))
    return set(db.scalars(select(User.id).where(User.id.in_(uids), or_(stage, call))))


# ------------------------------------------------------------------------------------------------ one-statement reads

# Everything a request needs about the people it covers, in ONE statement: scalar sub-selects returning JSON. Each part
# is optional. `:ids` are the people, `:day` the agency day, `:week` its Monday, `:start`/`:end` the day's UTC bounds.
_STATE_PARTS = {
    "goals": """(SELECT COALESCE(json_agg(json_build_object('u', g.user_id, 'm', g.metric, 't', g.target, 'mt', g.morning_target)), '[]'::json)
        FROM workflow_daily_goals g WHERE g.user_id IS NULL OR g.user_id = ANY(:ids)) AS goals""",
    "attendance": """(SELECT COALESCE(json_agg(json_build_object('user_id', p.user_id, 'shift', p.shift)), '[]'::json)
        FROM staff_attendance_duty_plans p WHERE p.user_id = ANY(:ids) AND p.attendance_date = :day) AS plans,
      (SELECT COALESCE(json_agg(json_build_object('user_id', t.user_id, 'shift', t.shift) ORDER BY t.effective_from DESC), '[]'::json)
        FROM staff_attendance_work_timings t WHERE t.user_id = ANY(:ids) AND t.effective_from <= :day) AS timings,
      (SELECT COALESCE(json_agg(json_build_object('user_id', m.user_id, 'status', m.status)), '[]'::json)
        FROM staff_attendance_calendar m WHERE m.user_id = ANY(:ids) AND m.attendance_date = :day) AS marks,
      (SELECT COALESCE(json_agg(json_build_object('user_id', w.user_id, 'day', w.day)), '[]'::json)
        FROM staff_attendance_weekly_off w WHERE w.user_id = ANY(:ids) AND w.week_start = :week) AS weekly""",
    "owned": """(SELECT COALESCE(json_object_agg(o.owner_id, o.n), '{}'::json) FROM (
        SELECT owner_id, count(*) AS n FROM crm_candidates WHERE owner_id = ANY(:ids) AND status IN ('new', 'in_process')
        GROUP BY owner_id) o) AS owned""",
    "have": """(SELECT COALESCE(json_object_agg(h.owner_id, h.n), '{}'::json) FROM (
        SELECT owner_id, count(*) AS n FROM workflow_daily_work_items
        WHERE owner_id = ANY(:ids) AND work_date = :day AND status <> 'released' GROUP BY owner_id) h) AS have""",
    "progress": """(SELECT COALESCE(json_agg(json_build_object('o', d.owner_id, 's', d.session, 'n', d.n)), '[]'::json) FROM (
        SELECT owner_id, session, count(*) AS n FROM workflow_daily_work_items
        WHERE owner_id = ANY(:ids) AND status = 'completed' AND completed_at >= :start AND completed_at < :end
        GROUP BY owner_id, session) d) AS done,
      (SELECT COALESCE(json_agg(json_build_object('o', s.owner_id, 'moves', s.moves, 'fwd', s.fwd)), '[]'::json) FROM (
        SELECT owner_id, count(*) FILTER (WHERE outcome IS DISTINCT FROM 'Unfit: reapplying') AS moves,
               count(*) FILTER (WHERE stage = 2 AND outcome = 'Forwarded') AS fwd
        FROM crm_application_stages WHERE owner_id = ANY(:ids) AND completed_at >= :start AND completed_at < :end
          AND reopened_reason IS NULL GROUP BY owner_id) s) AS stages,
      (SELECT COALESCE(json_agg(json_build_object('id', r.id, 'o', r.user_id, 'm', r.metric, 't', r.target, 'mt', r.morning_target,
                                                  'a', r.actual, 'ma', r.morning_actual)), '[]'::json)
        FROM workflow_daily_goal_progress r WHERE r.user_id = ANY(:ids) AND r.goal_date = :day) AS snapshot""",
}
STATE_PARTS = tuple(_STATE_PARTS)


def read_state(db, ids: list[str], day: date, *, tz=None, parts: tuple[str, ...] = STATE_PARTS) -> SimpleNamespace:
    """The `parts` asked for, about these people on this day, in a single statement.

    goals (effective goals per person plus "__agency__"), attendance (the four row lists `compute_shifts` takes), owned
    (new / in-process candidates owned), have (today's non-released items) and progress (candidates done per session,
    stage counts and the stored snapshot rows). Parts that were not asked for are None.
    """
    start, end = day_bounds(day, tz)
    statement = text("SELECT " + ", ".join(_STATE_PARTS[part] for part in parts)).bindparams(bindparam("ids", type_=ARRAY(String)))
    row = db.execute(statement, {"ids": list(ids), "day": day, "week": day - timedelta(days=day.weekday()), "start": start, "end": end}).one()._mapping
    state = SimpleNamespace(goals=None, attendance=None, owned=None, have=None, done=None, stages=None, snapshot=None)
    if "goals" in parts:
        state.goals = goals_from_rows([(r["u"], r["m"], r["t"], r["mt"]) for r in row["goals"]], list(ids) + ["__agency__"])
    if "attendance" in parts:
        state.attendance = tuple([SimpleNamespace(**r) for r in row[name]] for name in ("plans", "timings", "marks", "weekly"))
    if "owned" in parts:
        state.owned = {uid: int(n) for uid, n in row["owned"].items()}
    if "have" in parts:
        state.have = {uid: int(n) for uid, n in row["have"].items()}
    if "progress" in parts:
        state.done, state.stages, state.snapshot = row["done"], row["stages"], row["snapshot"]
    return state


@lru_cache(maxsize=8)
def _autocommit(engine):
    return engine.execution_options(isolation_level="AUTOCOMMIT")


def read_together(db, *reads):
    """Run independent read-only callables side by side, each on its own autocommit connection: one round trip for all.

    Autocommit matters at a distance: psycopg sends BEGIN as a message of its own before a transaction's first statement
    and the pool rolls back when the connection comes back, so a one-statement read in a transaction costs three round
    trips instead of one. The reads see only committed data, so use them for data the caller's own uncommitted writes
    cannot change. Results come back in order; the first failure is raised once every read has finished.
    """
    _, results = run_beside(db, None, *reads)
    return results


def run_beside(db, main, *reads):
    """Run `main(db)` on the caller's connection while the read-only `reads` run on autocommit connections of their own.

    See `read_together`. `main` may be None. Returns (main's result, [each read's result]). If `main` wrote, the reads
    did not see it: re-read on `db` what it could have changed.
    """
    try:
        engine = _autocommit(db.get_bind())
    except Exception:  # a stand-in session, or one with no bind: run in line
        engine = None
    if engine is None:
        return (main(db) if main else None), [read(db) for read in reads]

    def run(read):
        with _SLOTS, Session(engine, autoflush=False, expire_on_commit=False) as session:
            return read(session)

    if not reads:
        return (main(db) if main else None), []
    if len(reads) == 1 and main is None:  # nothing to overlap with: no thread needed
        return None, [run(reads[0])]
    with ThreadPoolExecutor(max_workers=len(reads)) as pool:
        futures = [pool.submit(copy_context().run, run, read) for read in reads]
        try:
            result = main(db) if main else None
        finally:
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except BaseException as exc:  # surfaced below, after every thread has finished
                    outcomes.append(exc)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return result, outcomes


# ------------------------------------------------------------------------------------------------ allocation

def _lock_owners(db, uids: list[str]) -> None:
    """One recruiter's top-up at a time, so two requests cannot hand out the same slot or candidate.

    All the locks are taken in ONE statement, in sorted order, so two requests that overlap on several recruiters cannot
    deadlock. They are transaction-level: released by the commit/rollback that ends the allocation.
    """
    keys = sorted(f"daily-work:{uid}" for uid in uids)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(s.k, 0)) FROM "
                    "(SELECT k FROM unnest(CAST(:keys AS text[])) AS k ORDER BY k OFFSET 0) s"), {"keys": keys})


def _used_slots(db, uids: list[str], today: date) -> dict[str, set[int]]:
    return {owner: set(slots) for owner, slots in db.execute(select(DailyWorkItem.owner_id, func.array_agg(DailyWorkItem.slot_no)).where(
        DailyWorkItem.owner_id.in_(uids), DailyWorkItem.work_date == today, DailyWorkItem.status != "released",
    ).group_by(DailyWorkItem.owner_id)).all()}


def ensure_allocations(db, staff_ids: list[str] | None = None, *, now: datetime | None = None, tz=None,
                       people: list[str] | None = None, goals: dict | None = None, attendance: tuple | None = None,
                       have: dict[str, int] | None = None, reconciled: bool = False, pool_hint: set[str] | None = None) -> dict:
    """Top every working recruiter up to today's goal. Returns {user id: new item count}. Caller commits.

    Set-based: the recruiters who are short are locked in one statement, their free slots and candidate picks are
    read in one statement each, and all the new items go in with one INSERT, however many recruiters there are.

    A caller that has already read the working recruiters (`people`, sorted by id), their effective `goals` and
    their `attendance` (the result of `shifts_and_days_off`, which may cover more people) passes them in, so the
    same reads are not repeated inside this transaction. `have` (today's item count per person), `reconciled`
    (the caller ran `reconcile`) and `pool_hint` (the people `pool_waiting` found something for) are valid only when
    the caller has not written to the items since it read them.
    """
    now = now or datetime.now(timezone.utc)
    today = agency_today(now, tz)
    if not reconciled:
        reconcile(db, now=now, tz=tz)
    if people is None:
        query = select(User.id).where(User.active.is_(True), User.role == Role.staff, User.on_leave.is_(False))
        if staff_ids is not None:
            query = query.where(User.id.in_(staff_ids))
        people = list(db.scalars(query.order_by(User.id)))
    if not people:
        return {}
    missing = tuple(part for part, absent in (("goals", goals is None), ("attendance", attendance is None), ("have", have is None)) if absent)
    if missing:
        state = read_state(db, people, today, tz=tz, parts=missing)
        goals = state.goals if goals is None else goals
        attendance = compute_shifts(people, today, *state.attendance) if attendance is None else attendance
        have = state.have if have is None else have
    shifts, off = attendance
    todo = [uid for uid in people if uid not in off and have.get(uid, 0) < quota_for(goals[uid])[0]]
    if pool_hint is not None:
        todo = [uid for uid in todo if uid in pool_hint]
    created: dict[str, int] = {}
    for attempt in range(3):  # a later pass only when another request took a candidate between our read and our insert
        if not todo:
            break
        if attempt == 0:
            _lock_owners(db, todo)
        used = _used_slots(db, todo, today)
        pools = pool_for(db, {uid: quota_for(goals[uid])[0] - len(used.get(uid, ())) for uid in todo}, today)
        batch = []
        for uid in todo:
            quota, morning = quota_for(goals[uid])
            taken = used.get(uid, set())
            start, pause = shifts[uid]
            free = [s for s in slot_plan(today, start, pause, max(quota, max(taken, default=0)), morning, tz) if s.no not in taken]
            # An allocation made after a slot's deadline still gives the recruiter an hour per candidate.
            previous = None
            for slot, pick in zip(free, pools.get(uid, [])):
                due = slot.due_at
                if previous is not None or due <= now:
                    due = max(due, (previous or now) + timedelta(hours=1))
                    previous = due
                batch.append(dict(owner_id=uid, work_date=today, kind=pick["kind"], task_id=pick["task_id"],
                                  application_id=pick["application_id"], candidate_id=pick["candidate_id"], stage=pick["stage"],
                                  slot_no=slot.no, session=slot.session, due_at=due, allocated_at=now))
        if not batch:
            break
        landed = db.execute(insert(DailyWorkItem).values(batch).on_conflict_do_nothing().returning(DailyWorkItem.owner_id)).scalars().all()
        wanted: dict[str, int] = defaultdict(int)
        for item in batch:
            wanted[item["owner_id"]] += 1
        for owner in landed:
            created[owner] = created.get(owner, 0) + 1
            wanted[owner] -= 1
        todo = [uid for uid in todo if wanted.get(uid, 0) > 0]  # a conflict: another request took that candidate; pick the next
    return created


def owned_candidate_counts(db, people: list[str]) -> dict[str, int]:
    """Candidates each person owns that are still being worked (new or in process)."""
    return dict(db.execute(select(Candidate.owner_id, func.count()).where(
        Candidate.owner_id.in_(people or [""]), Candidate.status.in_([CandidateStatus.new, CandidateStatus.in_process]),
    ).group_by(Candidate.owner_id)).all())


def work_shortfalls(db, goals_by_user: dict, rows_by_owner: dict[str, list[dict]], today: str, off: set[str], people: list[str],
                    owned: dict[str, int] | None = None) -> dict[str, dict | None]:
    """Who has fewer candidates today than their goal, and why (so the admin can assign candidates)."""
    if owned is None:
        owned = owned_candidate_counts(db, people)
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
    # The author's name rides along in the same statement (no separate read of the whole user table).
    for body, name, at, task_id in db.execute(select(Remark.body, User.name, Remark.created_at, Remark.task_id)
            .outerjoin(User, User.id == Remark.author_id)
            .where(Remark.task_id.in_(task_ids)).order_by(Remark.created_at.desc(), Remark.id)):
        entry = result.setdefault(task_id, {"body": body, "at": at, "by": name or "Unknown", "count": 0})
        entry["count"] += 1
    return result


def _first_text(values) -> str | None:
    """First non-blank entry of a stored list (job preference, skills) as trimmed text."""
    for value in values or []:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _location_hint(cand) -> str | None:
    """Where the candidate lives now, as typed on the form (city first, else the end of the address)."""
    city = (cand.current_city or "").strip()
    if city:
        return city
    address = (cand.current_address or "").strip()
    return address[-120:] or None


def placement_fields(order, company, client, designation, cand) -> dict:
    """Client, country and position for one row.

    A candidate matched to an order shows the order's values. One that is not matched yet has no order, so the row falls
    back on what the candidate's own form says: the preferred country, then the first preferred job (else first skill).
    `...Source` names where each value came from, `...IsPreferred` is true for the candidate's own wishes, and
    `locationHint` is the current city/address so the screen can still recognise a country in it. Client has no fallback.
    """
    country, country_source = None, None
    if order is not None and (order.country or company.country):
        country, country_source = order.country or company.country, "order"
    elif (cand.country_preference or "").strip():
        country, country_source = cand.country_preference.strip(), "preference"
    position, position_source = None, None
    if designation is not None:
        position, position_source = designation.title, "order"
    elif _first_text(cand.designations):
        position, position_source = _first_text(cand.designations), "preference"
    elif _first_text(cand.skills):
        position, position_source = _first_text(cand.skills), "skill"
    return {
        "client": company.name if company else None, "clientContact": client.name if client else None,
        "country": country, "countrySource": country_source, "countryIsPreferred": country_source == "preference",
        "locationHint": None if country else _location_hint(cand),
        "position": position, "positionSource": position_source, "positionIsPreferred": position_source in ("preference", "skill"),
    }


def fetch_rows(db, owner_ids: list[str], *, now: datetime | None = None, tz=None) -> list:
    """The list's rows (item, stage task, candidate, finished-at) for these recruiters: today's quota, everything postponed
    and backlog cleared today. One statement; `load_rows` turns them into the screen's dicts."""
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
    return db.execute(
        select(DailyWorkItem, Task, Candidate, done_at)
        .join(Candidate, Candidate.id == DailyWorkItem.candidate_id)
        .outerjoin(Task, Task.id == DailyWorkItem.task_id)
        .where(DailyWorkItem.owner_id.in_(owner_ids), DailyWorkItem.work_date <= today, DailyWorkItem.status != "released", live,
               or_(DailyWorkItem.work_date == today, done_at.is_(None), done_at >= day_start))
        .order_by(DailyWorkItem.work_date, DailyWorkItem.slot_no, DailyWorkItem.id).limit(MAX_ROWS)
    ).all()


def load_rows(db, owner_ids: list[str], *, now: datetime | None = None, tz=None, parallel: bool = False, fetched: list | None = None) -> list[dict]:
    """Today's list for these recruiters: today's quota, everything postponed and backlog cleared today.

    `parallel=True` runs the three lookups that depend only on the rows (placements, active placements, open call
    tasks) side by side; only for a caller whose changes are already committed (they run on their own connections).
    `fetched` is the result of `fetch_rows` when the caller already ran it (and nothing has changed since)."""
    now = now or datetime.now(timezone.utc)
    today = agency_today(now, tz)
    if not owner_ids:
        return []
    rows = fetch_rows(db, owner_ids, now=now, tz=tz) if fetched is None else fetched
    # The placement behind each row: its own application, or (for an owned candidate matched since) the active one.
    app_ids = [item.application_id for item, *_ in rows if item.application_id]
    loose = [cand.id for item, _t, cand, _d in rows if not item.application_id]
    app_query = (select(Application, Order, Company, Client, Designation)
                 .join(Order, Order.id == Application.order_id).join(Company, Company.id == Order.company_id)
                 .join(Client, Client.id == Company.client_id).join(Designation, Designation.id == Order.designation_id))
    lookups = (
        lambda s: s.execute(app_query.where(Application.id.in_(app_ids or [""]))).all(),
        lambda s: s.execute(app_query.where(Application.candidate_id.in_(loose or [""]), Application.status == ApplicationStatus.active)).all(),
        # Remarks live on tasks: the stage task, or for a call item the candidate's open initial-call task, if any.
        lambda s: s.execute(select(Task.candidate_id, Task.id).where(
            Task.candidate_id.in_(loose or [""]), Task.kind == TaskKind.initial_call,
            Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.created_at.desc())).all(),
        # The stage items' remarks need only the rows too, so they ride in the same round; the (rare) call items' open
        # initial-call tasks are known only after it and are fetched afterwards.
        lambda s: _remarks(s, stage_tasks),
    )
    stage_tasks = sorted({item.task_id for item, *_ in rows if item.task_id})
    placed, activated, call_task_rows, remarks = read_together(db, *lookups) if parallel else [look(db) for look in lookups]
    placements = {a.id: (a, o, co, cl, d) for a, o, co, cl, d in placed}
    active = {a.candidate_id: (a, o, co, cl, d) for a, o, co, cl, d in activated}
    call_tasks = dict(call_task_rows)
    remark_tasks = {item.id: (item.task_id or call_tasks.get(cand.id)) for item, _t, cand, _d in rows}
    late = sorted({t for t in remark_tasks.values() if t and t not in set(stage_tasks)})
    if late:
        remarks = {**remarks, **_remarks(db, late)}
    out = []
    for item, task, cand, finished in rows:
        placement = placements.get(item.application_id) if item.application_id else active.get(cand.id)
        app, order, company, client, designation = placement if placement else (None, None, None, None, None)
        done = finished is not None
        postponed = not done and item.work_date < today
        remark_task = remark_tasks.get(item.id)
        remark = remarks.get(remark_task) if remark_task else None
        out.append({
            "id": item.id, "kind": item.kind, "taskId": remark_task, "applicationId": app.id if app else None,
            "candidateId": cand.id, "ownerId": item.owner_id, "workDate": item.work_date.isoformat(), "slotNo": item.slot_no,
            "session": item.session, "dueAt": item.due_at, "allocatedStage": item.stage,
            "stage": app.current_stage if app else None,
            "status": "done" if done else "open", "completedAt": finished,
            "allocatedAt": item.allocated_at, "startedAt": item.started_at,
            "overdueAt": item.overdue_at or (item.due_at if (not done and item.due_at < now) or (done and finished > item.due_at) else None),
            "postponed": postponed, "daysLate": (today - item.work_date).days if postponed else 0,
            **placement_fields(order, company, client, designation, cand),
            "candidate": cand.name, "candidateRef": cand.ref,
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


def goal_progress(db, user_ids: list[str], today: date, *, tz=None, persist: bool = False, goals: dict | None = None,
                  state: SimpleNamespace | None = None, stats: dict | None = None) -> dict[str, dict]:
    """Per person: each goal's target and today's achieved count, computed from the real records.

    With `persist`, the per-day snapshot rows are written when (and only when) they differ, in at most two statements
    (one INSERT for the missing rows, one UPDATE for the changed ones). `state` is a `read_state` result with the
    "progress" part (and "goals", unless `goals` is given) that the caller read in an earlier round trip and that is still
    valid; otherwise it is read here, in one statement. `stats`, if given, receives {"writes": rows written}.
    """
    if not user_ids:
        return {}
    if state is None or state.done is None or (goals is None and state.goals is None):
        state = read_state(db, user_ids, today, tz=tz, parts=("progress",) if goals is not None else ("goals", "progress"))
    goals = goals if goals is not None else state.goals
    cand = defaultdict(lambda: {"morning": 0, "evening": 0})
    for row in state.done:
        cand[row["o"]][row["s"]] = row["n"]
    moves = {row["o"]: row["moves"] for row in state.stages}
    forwarded = {row["o"]: row["fwd"] for row in state.stages}
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
        writes = save_progress(db, result, today, state.snapshot)
        if stats is not None:
            stats["writes"] = writes
    return result


def save_progress(db, progress: dict[str, dict], day: date, existing_rows: list[dict] | None = None) -> int:
    """Write the day's snapshot rows that are missing or differ; returns how many rows were written.

    `existing_rows` are the stored rows (as `read_state` returns them); without them they are read here."""
    if existing_rows is None:
        existing_rows = read_state(db, list(progress), day, parts=("progress",)).snapshot
    existing = {(r["o"], r["m"]): r for r in existing_rows}
    inserts, updates = [], []
    for uid, goals in progress.items():
        for metric, key in (("candidates", "candidates"), ("forwarded_to_client", "forwardedToClient"), ("stage_moves", "stageMoves")):
            g = goals[key]
            want = (g["target"], g.get("morning"), g["done"] if metric == "candidates" else g["actual"],
                    g.get("morningDone") if metric == "candidates" else None)
            row = existing.get((uid, metric))
            if row is None:
                inserts.append(dict(user_id=uid, goal_date=day, metric=metric, target=want[0], morning_target=want[1],
                                    actual=want[2], morning_actual=want[3]))
            elif (row["t"], row["mt"], row["a"], row["ma"]) != want:
                updates.append(dict(row_id=row["id"], want_target=want[0], want_morning=want[1], want_actual=want[2], want_morning_actual=want[3]))
    if inserts:
        db.execute(insert(DailyGoalProgress).values(inserts).on_conflict_do_nothing(constraint="uq_daily_goal_progress"))
    if updates:
        table = DailyGoalProgress.__table__
        db.execute(update(table).where(table.c.id == bindparam("row_id")).values(
            target=bindparam("want_target"), morning_target=bindparam("want_morning"), actual=bindparam("want_actual"),
            morning_actual=bindparam("want_morning_actual")), updates)
    return len(inserts) + len(updates)


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
