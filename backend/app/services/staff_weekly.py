"""Weekly staff performance report for the admin: attendance, daily tasks, stage moves and the end-to-end funnel.

Everything here is computed on request from stored rows (attendance sessions, browser presence, the daily quota,
stage completions and applications); nothing is cached or typed in. A week is Monday to Sunday in the agency
timezone, but any range up to MAX_DAYS days can be asked for.

Round trips matter (a hosted database costs 150-350 ms per statement), so the data is read in two parallel
batches: (1) staff, attendance, presence, stage moves, the application snapshot, stuck candidates, daily-quota
rows and task events; (2) the attendance calendar inputs (weekly offs, holidays, leave, duty plans, approved
requests), which are shared with the Attendance module so "absent" means exactly what it means there.

The performance score is deliberately simple and is returned with its inputs (`components`) so an admin can read
how it was reached and question the staff member about the weak part.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import ProgrammingError

from app.db import models as m
from app.domain.stages import STAGES, TOTAL_STAGES
from app.services import attendance_hr as hr
from app.services import lead_metrics
from app.services.attendance_policy import AttendancePolicy, shift_bounds
from app.services.parallel_reads import parallel_reads
from app.services.staff_activity import intersections, merged, presence_end, seconds

MAX_DAYS = 62

# Highlights ("questions to ask") fire from these thresholds; the response repeats them so the screen can state them.
STUCK_DAYS = 7            # an active candidate with no stage completed for this many days is "stuck"
LOW_COMPLETION_PERCENT = 60
LOW_COMPLETION_MIN_TASKS = 3
LOW_HOURS_PERCENT = 75
LOW_HOURS_MIN_EXPECTED = 4.0
BACKLOG_MIN = 3

# Score weights (percent). A component without a basis (no tasks allocated, no expected hours...) is left out and
# the remaining weights are scaled up, so being on leave never counts against anyone.
WEIGHTS = {"tasks": 40, "hours": 30, "stages": 30}
# Lead follow-up is an OPTIONAL fourth part: it joins the score only for a person who had leads assigned in the period,
# and then the weights are these (tasks 40->35, stage moves 30->25, lead follow-up 10). Everyone else keeps WEIGHTS, so a
# week without leads scores exactly as it did before the Lead sheet existed.
WEIGHTS_WITH_LEADS = {"tasks": 35, "hours": 30, "stages": 25, "leads": 10}
# Questions to ask about the lead sheet.
LEADS_UNTOUCHED_MIN = 3   # this many leads assigned in the period and none called
LEADS_OVERDUE_MIN = 5     # this many follow-ups already overdue

PRESENT_STATUSES = {"P", "LT", "EE", "PP", "OD", "WFH"}
CREDITED_STATUSES = {"OD", "WFH"}  # approved duty away from the office: credited the full day, no punches exist
LEAVE_STATUSES = {"PL", "UL"}
OFF_STATUSES = {"WO", "H"}

STAGE_LABELS = [stage.title for stage in STAGES]
# Outcomes written by app.domain.stages for the milestones the admin asks about.
OUTCOME_COUNTS = {
    "forwarded": (2, "Forwarded"),
    "interviewsScheduled": (3, "Scheduled"),
    "selected": (4, "Selected"),
    "visaReceived": (9, "Arrived"),
    "visaStamped": (10, "Stamped"),
    "deployed": (11, "Booked"),
}


# ------------------------------------------------------------------ range

def week_of(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def resolve_range(from_date: date | None, to_date: date | None, today: date) -> tuple[date, date]:
    """The asked range, or the current Monday-Sunday week. Validated so one request stays cheap."""
    if from_date is None and to_date is None:
        return week_of(today)
    if from_date is None or to_date is None:
        return week_of(from_date or to_date)
    if from_date > to_date:
        raise HTTPException(422, "The start date must be on or before the end date.")
    if (to_date - from_date).days + 1 > MAX_DAYS:
        raise HTTPException(422, f"Choose a period of at most {MAX_DAYS} days.")
    if from_date.year < 2000:
        raise HTTPException(422, "Choose a start date from the year 2000 onwards.")
    return from_date, to_date


def _utc(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tz).astimezone(timezone.utc)


def _percent(part, whole):
    return round(part / whole * 100, 1) if whole else None


def _hours(secs) -> float:
    return round(secs / 3600, 2)


# ------------------------------------------------------------------ reads (batch 1)

def _scope(ids_column, staff_id):
    sub = select(m.User.id).where(m.User.role == m.Role.staff)
    if staff_id:
        sub = sub.where(m.User.id == staff_id)
    return ids_column.in_(sub)


def _clean_stage():
    return or_(m.ApplicationStage.reopened_reason.is_(None), m.ApplicationStage.reopened_reason == "")


def _candidate_owner():
    return func.coalesce(m.Candidate.owner_id, m.Application.owner_id)


def _read_all(db, start, end, first, last, now, staff_id):
    S, P, AS, A, C = m.StaffAttendanceSession, m.StaffPresenceInterval, m.ApplicationStage, m.Application, m.Candidate
    owner = _candidate_owner()
    stuck_before = now - timedelta(days=STUCK_DAYS)
    last_move = (select(AS.application_id, func.max(AS.completed_at).label("last_at"))
                 .where(_clean_stage()).group_by(AS.application_id).subquery("last_move"))
    since = func.coalesce(last_move.c.last_at, A.created_at)
    stuck_base = (select(A.id.label("application_id"), A.candidate_id, C.name, C.ref, owner.label("owner"),
                         A.current_stage, since.label("since"))
                  .join(C, C.id == A.candidate_id).outerjoin(last_move, last_move.c.application_id == A.id)
                  .where(A.status == m.ApplicationStatus.active, since < stuck_before))
    if staff_id:
        stuck_base = stuck_base.where(owner == staff_id)
    stuck_sub = stuck_base.subquery("stuck")

    def users(session):
        query = select(m.User.id, m.User.name, m.User.email, m.User.active, m.User.created_at, m.User.updated_at
                       ).where(m.User.role == m.Role.staff)
        if staff_id:
            query = query.where(m.User.id == staff_id)
        return session.execute(query.order_by(func.lower(m.User.name), m.User.id)).all()

    def sessions(session):
        return session.execute(select(S.user_id, S.checked_in_at, S.checked_out_at).where(
            _scope(S.user_id, staff_id), S.checked_in_at < end,
            or_(S.checked_out_at.is_(None), S.checked_out_at > start))).all()

    def presence(session):
        return session.execute(select(P.user_id, P.state, P.started_at, P.ended_at, P.last_seen_at).where(
            _scope(P.user_id, staff_id), P.started_at < end, or_(P.ended_at.is_(None), P.ended_at > start))).all()

    def moves(session):
        query = (select(AS.application_id, AS.stage, AS.attempt, AS.outcome, AS.owner_id, AS.completed_by,
                        AS.completed_at, A.candidate_id, C.name, C.ref, C.owner_id.label("candidate_owner"))
                 .join(A, A.id == AS.application_id).join(C, C.id == A.candidate_id)
                 .where(AS.completed_at >= start, AS.completed_at < end, _clean_stage()))
        if staff_id:
            query = query.where(func.coalesce(AS.owner_id, AS.completed_by, C.owner_id) == staff_id)
        return session.execute(query.order_by(AS.completed_at.desc())).all()

    def snapshot(session):
        query = (select(owner.label("owner"), A.status, A.current_stage, func.count()).select_from(A)
                 .join(C, C.id == A.candidate_id).group_by(owner, A.status, A.current_stage))
        if staff_id:
            query = query.where(owner == staff_id)
        return session.execute(query).all()

    def stuck_totals(session):
        return session.execute(select(stuck_sub.c.owner, func.count(), func.min(stuck_sub.c.since))
                               .group_by(stuck_sub.c.owner)).all()

    def stuck_list(session):
        return session.execute(select(stuck_sub).order_by(stuck_sub.c.since).limit(400)).all()

    def daily(session):
        """Daily-quota rows and goals (see docs/workflows/tasks-data-contract.md), or None when those tables are missing."""
        try:
            DW, G = m.DailyWorkItem, m.DailyGoal
            rows = session.execute(select(DW.owner_id, DW.work_date, DW.due_at, DW.status, DW.completed_at, DW.postponed_at,
                                          DW.candidate_id, DW.session).where(
                _scope(DW.owner_id, staff_id), DW.status != "released",
                or_(DW.work_date.between(first, last),
                    and_(DW.work_date < first, or_(DW.status == "open", DW.completed_at >= start))))).all()
            goals = session.execute(select(G.user_id, G.metric, G.target)).all()
            return {"rows": rows, "goals": goals}
        except (ProgrammingError, AttributeError):
            session.rollback()
            return None

    def events(session):
        TE = m.TaskEvent
        return session.execute(select(TE.owner_id, TE.kind, func.count()).where(
            TE.occurred_at >= start, TE.occurred_at < end, TE.kind.in_(["escalation", "missed_update"]),
            _scope(TE.owner_id, staff_id)).group_by(TE.owner_id, TE.kind)).all()

    def first_punch(session):
        """When attendance was first recorded by anyone: days before that were never tracked, so nobody was 'absent'."""
        return session.scalar(select(func.min(S.checked_in_at)))

    def leads(session):
        """Lead sheet numbers per person (one aggregate statement), or None when the lead tables are missing."""
        return lead_metrics.weekly_lead_numbers(session, start, end, now, staff_id)

    reads = [users, sessions, presence, moves, snapshot, stuck_totals, stuck_list, daily, events, first_punch, leads]
    names = ["users", "sessions", "presence", "moves", "snapshot", "stuck_totals", "stuck_list", "daily", "events", "first_punch", "leads"]
    return dict(zip(names, parallel_reads(db, *reads)))


def _candidate_rows(db, staff_id, start, end, now):
    """The end-to-end list for one staff member: each owned application with its stage history."""
    A, C, O, AS = m.Application, m.Candidate, m.Order, m.ApplicationStage
    owner = _candidate_owner()
    total = db.scalar(select(func.count()).select_from(A).join(C, C.id == A.candidate_id).where(owner == staff_id)) or 0
    apps = db.execute(
        select(A.id, A.candidate_id, C.name, C.ref, A.status, A.closure_reason, A.current_stage, A.created_at, A.updated_at,
               O.ref.label("order_ref"), m.Designation.title.label("designation"), m.Client.name.label("client"))
        .join(C, C.id == A.candidate_id).join(O, O.id == A.order_id)
        .join(m.Designation, m.Designation.id == O.designation_id)
        .join(m.Company, m.Company.id == O.company_id).join(m.Client, m.Client.id == m.Company.client_id)
        .where(owner == staff_id)
        .order_by(case_active(A), A.current_stage.desc(), A.updated_at.desc()).limit(300)).all()
    ids = [row.id for row in apps]
    stages = db.execute(select(AS.application_id, AS.stage, AS.outcome, AS.completed_at, AS.completed_by)
                        .where(AS.application_id.in_(ids), _clean_stage()).order_by(AS.completed_at)).all() if ids else []
    by_app = defaultdict(list)
    for row in stages:
        by_app[row.application_id].append(row)
    out = []
    for row in apps:
        history = by_app[row.id]
        last_at = history[-1].completed_at if history else None
        since = last_at or row.created_at
        status = getattr(row.status, "value", row.status)
        out.append({
            "applicationId": row.id, "candidateId": row.candidate_id, "candidate": row.name, "ref": row.ref,
            "order": row.order_ref, "designation": row.designation, "client": row.client,
            "status": status, "closureReason": getattr(row.closure_reason, "value", row.closure_reason),
            "stage": row.current_stage, "stageLabel": STAGE_LABELS[row.current_stage - 1],
            "reached": TOTAL_STAGES if status == "deployed" else max([s.stage for s in history] + [row.current_stage]),
            "since": since, "daysInStage": max(0, (now - since).days) if status == "active" else None,
            "movedInRange": sum(start <= s.completed_at < end for s in history),
            "history": [{"stage": s.stage, "outcome": s.outcome, "at": s.completed_at} for s in history],
        })
    return {"total": total, "shown": len(out), "items": out}


def case_active(app_model):
    """Active placements first, then deployed, then closed."""
    return case((app_model.status == m.ApplicationStatus.active, 0), (app_model.status == m.ApplicationStatus.deployed, 1), else_=2)


# ------------------------------------------------------------------ the report

def build_report(db, from_date: date | None, to_date: date | None, zone: str, *, now: datetime | None = None,
                 staff_id: str | None = None, with_candidates: bool = False) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tz = ZoneInfo(zone)
    today = now.astimezone(tz).date()
    first, last = resolve_range(from_date, to_date, today)
    start, end = _utc(first, tz), _utc(last + timedelta(days=1), tz)
    cut = min(now, end)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    day_bounds = {d: (_utc(d, tz), _utc(d + timedelta(days=1), tz)) for d in days}
    local_day = lambda moment: moment.astimezone(tz).date()

    data = _read_all(db, start, end, first, last, now, staff_id)
    candidate_block = _candidate_rows(db, staff_id, start, end, now) if (with_candidates and staff_id) else None

    # Who is reported: every active staff member, plus a former one who still did something in this period.
    active_between = set()
    for row in data["sessions"]:
        active_between.add(row.user_id)
    for row in data["moves"]:
        active_between.add(row.owner_id or row.completed_by or row.candidate_owner)
    if data["daily"]:
        active_between.update(row.owner_id for row in data["daily"]["rows"])
    people = [u for u in data["users"] if u.active or u.id in active_between]
    people_ids = {u.id for u in people}

    # ---- attendance calendar (batch 2): the same rules as the Attendance module
    policy = AttendancePolicy(timezone_name=zone)
    evaluate_until = min(last, today)
    inputs = hr._load_month_inputs(db, [SimpleNamespace(id=u.id, active=u.active) for u in people], first, evaluate_until, policy) \
        if people and first <= today else hr.MonthInputs()

    sessions_by_user, presence_by_user = defaultdict(list), defaultdict(list)
    for row in data["sessions"]:
        sessions_by_user[row.user_id].append(row)
    for row in data["presence"]:
        presence_by_user[row.user_id].append(row)

    moves_by_user = defaultdict(list)
    for row in data["moves"]:
        who = row.owner_id or row.completed_by or row.candidate_owner
        if who in people_ids:
            moves_by_user[who].append(row)

    snapshot = defaultdict(lambda: defaultdict(int))  # owner -> (status, stage) -> count
    team_snapshot = defaultdict(int)
    for owner_id, status, stage, count in data["snapshot"]:
        status = getattr(status, "value", status)
        snapshot[owner_id][(status, stage)] += count
        team_snapshot[(status, stage)] += count

    stuck_total = {owner_id: (count, oldest) for owner_id, count, oldest in data["stuck_totals"]}
    stuck_names = defaultdict(list)
    for row in data["stuck_list"]:
        if len(stuck_names[row.owner]) < 5:
            stuck_names[row.owner].append({"candidateId": row.candidate_id, "candidate": row.name, "ref": row.ref,
                                           "stage": row.current_stage, "stageLabel": STAGE_LABELS[row.current_stage - 1],
                                           "days": (now - row.since).days})
    event_counts = defaultdict(lambda: defaultdict(int))
    for owner_id, kind, count in data["events"]:
        event_counts[owner_id][kind] += count

    daily = data["daily"]
    daily_by_user = defaultdict(list)
    if daily:
        for row in daily["rows"]:
            daily_by_user[row.owner_id].append(row)
    goal_rows = daily["goals"] if daily else []

    team_start = local_day(data["first_punch"]) if data["first_punch"] else None
    staff_rows = []
    for person in people:
        staff_rows.append(_staff_row(
            person, days, day_bounds, tz, now, today, cut, policy, inputs, _goals_for(person.id, goal_rows),
            sessions_by_user[person.id], presence_by_user[person.id], moves_by_user[person.id],
            snapshot[person.id], stuck_total.get(person.id), stuck_names.get(person.id, []),
            event_counts[person.id], daily_by_user[person.id] if daily else None, local_day, team_start,
            leads=lead_metrics.person_numbers(data["leads"].get(person.id)) if data["leads"] is not None else lead_metrics.unavailable_numbers()))

    funnel = _team_funnel(team_snapshot, data["moves"], people_ids)
    highlights = _highlights(staff_rows)
    summary = _summary(staff_rows, funnel, daily is not None)
    summary["highlights"] = len(highlights)

    notes = [
        "Hours worked are the time between check-in and check-out in the agency timezone, split by day. A session still open "
        "from an earlier day counts no hours and is flagged: the person never checked out.",
        "Absent means a scheduled working day with no check-in. Weekly offs, holidays and approved leave are not absences "
        "(same rules as the Attendance page). Days before a person's first attendance record, and days still to come, are not judged.",
        "Daily tasks are the quota the system hands each recruiter. Completed means the stage task was finished; postponed means "
        "it was not finished by the end of its day; overdue means still open after its hourly deadline.",
        "A stage move is a stage completed in the period, credited to the recruiter responsible for the candidate. Stages that were "
        "later reopened are left out.",
        f"Stuck means an active candidate with no stage completed for {STUCK_DAYS} days or more.",
    ]
    if daily is None:
        notes.append("The daily-task tables are not in this database yet, so task numbers are unavailable.")
    if data["leads"] is None:
        notes.append("The Lead sheet tables are not in this database yet, so lead numbers are unavailable.")
    else:
        notes.append("Lead sheet: assigned = leads given to the person in the period; contacted = those called at least once in the period; "
                     "calls and leads worked are credited to whoever logged the call; resumes and conversions to the lead's owner; "
                     "conversion % = converted / leads worked; overdue follow-ups are as of now.")

    return {
        "generatedAt": now.isoformat(), "timezone": zone,
        "range": {"from": first.isoformat(), "to": last.isoformat(), "days": [d.isoformat() for d in days],
                  "isWeek": first.weekday() == 0 and (last - first).days == 6, "today": today.isoformat(),
                  "isCurrent": first <= today <= last},
        "formula": _formula(_goals_for(None, goal_rows)),
        "thresholds": {"stuckDays": STUCK_DAYS, "lowCompletionPercent": LOW_COMPLETION_PERCENT,
                       "lowHoursPercent": LOW_HOURS_PERCENT, "backlogMin": BACKLOG_MIN,
                       "leadsUntouchedMin": LEADS_UNTOUCHED_MIN, "leadsOverdueMin": LEADS_OVERDUE_MIN},
        "sources": {"dailyTasks": {"available": daily is not None,
                                    "hasData": bool(daily and daily["rows"]),
                                    "goals": _goals_for(None, goal_rows)},
                    "leadSheet": {"available": data["leads"] is not None, "hasData": bool(data["leads"])}},
        "summary": summary, "staff": staff_rows, "funnel": funnel, "highlights": highlights, "notes": notes,
        **({"candidates": candidate_block,
            "moves": [{"at": r.completed_at, "stage": r.stage, "stageLabel": STAGE_LABELS[r.stage - 1], "outcome": r.outcome,
                       "candidateId": r.candidate_id, "candidate": r.name, "ref": r.ref}
                      for r in data["moves"][:300]]} if candidate_block is not None else {}),
    }


DEFAULT_GOALS = {"candidates": 5, "forwarded_to_client": 2, "stage_moves": 5}


def _goals_for(user_id, rows):
    """Daily targets in force: the person's own row, else the agency row, else the built-in default."""
    agency, own = {}, {}
    for owner_id, metric, target in rows:
        (own if owner_id and owner_id == user_id else agency if owner_id is None else {})[metric] = target
    return {metric: own.get(metric, agency.get(metric, default)) for metric, default in DEFAULT_GOALS.items()}


def _formula(goals):
    return {
        "weights": WEIGHTS, "weightsWithLeads": WEIGHTS_WITH_LEADS,
        "lines": [
            f"Tasks {WEIGHTS['tasks']}%: daily tasks completed divided by daily tasks assigned in the period.",
            f"Hours {WEIGHTS['hours']}%: hours worked divided by hours expected from the shift on scheduled days (capped at 100%).",
            f"Stage moves {WEIGHTS['stages']}%: stages completed divided by the daily stage-move goal (agency default {goals['stage_moves']}) "
            "times the days the person worked (capped at 100%).",
            f"Lead follow-up (optional): only for a person who had leads assigned in the period. Then it counts "
            f"{WEIGHTS_WITH_LEADS['leads']}% (leads assigned in the period that were called at least once, divided by leads assigned) and the weights become "
            f"tasks {WEIGHTS_WITH_LEADS['tasks']}%, hours {WEIGHTS_WITH_LEADS['hours']}%, stage moves {WEIGHTS_WITH_LEADS['stages']}%. "
            "Without assigned leads the score uses the three parts above.",
            "A part with nothing to measure (no tasks assigned, a day off all week) is left out and the others count for more. "
            "No measurable part means no score.",
        ],
    }


# ------------------------------------------------------------------ one person

def _clip(intervals, a, b):
    return [(max(s, a), min(e, b)) for s, e in intervals if max(s, a) < min(e, b)]


def _staff_row(person, days, day_bounds, tz, now, today, cut, policy, inputs, goals, sessions, presence, moves,
               snapshot, stuck, stuck_list, events, daily_rows, local_day, team_start, leads=None):
    today_start = day_bounds[today][0] if today in day_bounds else _utc(today, tz)
    clocked, missing_checkout = [], set()
    for row in sessions:
        stop = row.checked_out_at
        if stop is None:
            if row.checked_in_at >= today_start:
                stop = now
            else:  # opened on an earlier day and never closed: no trustworthy duration
                missing_checkout.add(local_day(row.checked_in_at))
                continue
        clocked.append((row.checked_in_at, min(stop, now)))
    clocked = merged(clocked)
    online = merged((r.started_at, min(presence_end(r, now), now)) for r in presence if r.state == "online")

    created_day = local_day(person.created_at)
    # Judged from the later of: the account's creation and the day attendance was first recorded by anyone.
    tracking_start = max(created_day, team_start or created_day)
    last_day = today
    if not person.active:
        last_day = min(today, local_day(inputs.deactivated.get(person.id) or person.updated_at))

    moves_by_day = defaultdict(int)
    stage_moves = defaultdict(int)
    outcome_counts = defaultdict(int)
    for row in moves:
        moves_by_day[local_day(row.completed_at)] += 1
        stage_moves[row.stage] += 1
        for key, (stage_no, outcome) in OUTCOME_COUNTS.items():
            if row.stage == stage_no and row.outcome == outcome:
                outcome_counts[key] += 1
    interview_rejected = sum(1 for r in moves if r.stage == 4 and r.outcome == "Rejected")

    task_info = _task_numbers(daily_rows, days, tz, now, cut, today, day_bounds) if daily_rows is not None else None

    day_rows = []
    totals = {"clocked": 0, "credited": 0, "active": 0, "expected": 0.0}
    counts = {"present": 0, "absent": 0, "leave": 0, "off": 0, "pending": 0, "untracked": 0, "future": 0}
    absent_dates, first_in_range = [], None
    for d in days:
        a, b = day_bounds[d]
        clocked_day = _clip(clocked, a, b)
        secs = seconds(clocked_day)
        active_secs = seconds(intersections(clocked_day, _clip(online, a, b))) if secs else 0
        ins = [s for s, _e in clocked_day]
        outs = [e for _s, e in clocked_day]
        state, status, expected, credited = "future", None, 0.0, 0
        if d > today:
            state = "future"
        elif d < tracking_start or d > last_day:
            state = "untracked"
        else:
            row = hr._day(None, person, d, now, policy, inputs)
            status = row["status"]
            required = row["required_shift_minutes"]
            if status in LEAVE_STATUSES:
                state = "leave"
            elif status in OFF_STATUSES:
                state = "off"
            elif secs > 0 or status in PRESENT_STATUSES or d in missing_checkout:
                state = "present"
                if status in CREDITED_STATUSES and secs == 0:
                    credited = required * 60
                shift, _planned = inputs.shift(person.id, d)
                s0, s1 = shift_bounds(d, shift, policy.timezone_name)
                share = 1.0 if d < today else max(0.0, min(1.0, (now - s0) / (s1 - s0)))
                expected = required * share
            elif d == today:
                state = "pending"
            else:
                state = "absent"
                expected = float(required)
                absent_dates.append(d.isoformat())
        counts[state] += 1
        totals["clocked"] += secs
        totals["credited"] += credited
        totals["active"] += active_secs
        totals["expected"] += expected
        tasks_day = (task_info["days"].get(d) if task_info else None) or {"assigned": 0, "done": 0, "missed": 0}
        day_rows.append({
            "date": d.isoformat(), "state": state, "status": status, "workedSeconds": secs + credited,
            "activeSeconds": active_secs, "firstIn": min(ins) if ins else None, "lastOut": max(outs) if outs else None,
            "missingCheckout": d in missing_checkout, "expectedMinutes": round(expected),
            "tasks": tasks_day, "stageMoves": moves_by_day.get(d, 0),
        })

    worked_seconds = totals["clocked"] + totals["credited"]
    expected_hours = round(totals["expected"] / 60, 2)
    worked_hours = _hours(worked_seconds)
    if worked_seconds > 0 or counts["present"] > 0:
        status = "worked"
    elif counts["absent"] > 0:
        status = "no_attendance"
    elif counts["pending"] > 0:
        status = "pending"
    else:
        status = "not_scheduled"

    by_stage = {n: 0 for n in range(1, TOTAL_STAGES + 1)}
    reached = {n: 0 for n in range(1, TOTAL_STAGES + 1)}
    active_total = closed_total = deployed_total = 0
    for (state_name, stage), count in snapshot.items():
        if state_name == "active":
            by_stage[stage] += count
            active_total += count
        elif state_name == "closed":
            closed_total += count
        elif state_name == "deployed":
            deployed_total += count
        for n in range(1, TOTAL_STAGES + 1):
            if state_name == "deployed" or stage >= n:
                reached[n] += count

    stage_move_total = sum(stage_moves.values())
    tasks_block = _tasks_block(task_info, events)
    leads = leads or lead_metrics.unavailable_numbers()
    score = _score(tasks_block, worked_hours, expected_hours, stage_move_total, counts["present"], goals["stage_moves"], leads)
    return {
        "id": person.id, "name": person.name, "email": person.email, "active": person.active, "status": status,
        "attendance": {"workedDays": counts["present"], "absentDays": counts["absent"], "leaveDays": counts["leave"],
                       "offDays": counts["off"], "pendingDays": counts["pending"], "absentDates": absent_dates,
                       "hoursWorked": worked_hours, "activeHours": _hours(totals["active"]),
                       "expectedHours": expected_hours,
                       "hoursPercent": _percent(worked_hours, expected_hours) if expected_hours else None,
                       "missingCheckouts": len(missing_checkout)},
        "tasks": tasks_block,
        "stages": {"inStageNow": by_stage, "movesByStage": {n: stage_moves.get(n, 0) for n in range(1, TOTAL_STAGES + 1)},
                   "moves": stage_move_total, "reached": reached, "activeCandidates": active_total,
                   "closed": closed_total, "deployedTotal": deployed_total},
        "outcomes": {**{key: outcome_counts.get(key, 0) for key in OUTCOME_COUNTS}, "interviewRejected": interview_rejected},
        "stuck": {"count": stuck[0] if stuck else 0, "oldestDays": (now - stuck[1]).days if stuck else 0, "examples": stuck_list},
        "goals": {"candidatesPerDay": goals["candidates"], "forwardedPerDay": goals["forwarded_to_client"],
                  "stageMovesPerDay": goals["stage_moves"], "scheduledDays": counts["present"] + counts["absent"]},
        "leads": leads,
        "score": score["score"], "scoreParts": score["parts"],
        "days": day_rows,
    }


def _task_numbers(rows, days, tz, now, cut, today, day_bounds):
    """Daily-quota numbers for one person as of `cut` (now, or the end of a past period)."""
    first, last = days[0], days[-1]
    per_day = defaultdict(lambda: {"assigned": 0, "done": 0, "missed": 0})
    assigned = completed = on_time = postponed = overdue = 0
    backlog = []
    cleared = 0
    cut_day = cut.astimezone(tz).date()
    for row in rows:
        done = row.status == "completed" and row.completed_at is not None and row.completed_at <= cut
        if not (first <= row.work_date <= last):
            if done and row.completed_at >= _utc(first, tz):
                cleared += 1  # older work finished during this period
            elif not done:
                backlog.append(row.work_date)
            continue
        assigned += 1
        slot = per_day[row.work_date]
        slot["assigned"] += 1
        if done:
            completed += 1
            slot["done"] += 1
            on_time += row.completed_at <= row.due_at
        elif row.due_at < cut:
            overdue += 1
            slot["missed"] += 1
        day_end = _utc(row.work_date + timedelta(days=1), tz)
        if day_end <= cut and (not done or row.completed_at >= day_end):
            postponed += 1
        if not done and row.work_date < cut_day:
            backlog.append(row.work_date)
    oldest = (cut_day - min(backlog)).days if backlog else 0
    return {"assigned": assigned, "completed": completed, "onTime": on_time, "postponed": postponed, "overdue": overdue,
            "backlog": len(backlog), "oldestBacklogDays": oldest, "clearedBacklog": cleared, "days": dict(per_day)}


def _tasks_block(info, events):
    base = {"escalations": events.get("escalation", 0), "missedUpdates": events.get("missed_update", 0)}
    if info is None:
        return {"available": False, "assigned": 0, "completed": 0, "postponed": 0, "overdue": 0, "backlog": 0,
                "oldestBacklogDays": 0, "clearedBacklog": 0, "onTime": 0, "completionPercent": None, **base}
    return {"available": True, **{k: info[k] for k in ("assigned", "completed", "onTime", "postponed", "overdue", "backlog",
                                                       "oldestBacklogDays", "clearedBacklog")},
            "completionPercent": _percent(info["completed"], info["assigned"]), **base}


def _score(tasks, hours, expected_hours, moves, worked_days, daily_goal, leads=None):
    """0-100 from up to four explainable parts; each part is None when there is nothing to measure.

    The lead follow-up part exists only for a person who had leads assigned in the period (and then the weights are
    WEIGHTS_WITH_LEADS); for everybody else the score is the original three parts.
    """
    with_leads = bool(leads and leads.get("available") and leads.get("assigned", 0) > 0)
    weights = WEIGHTS_WITH_LEADS if with_leads else WEIGHTS
    parts = []
    task_ratio = tasks["completed"] / tasks["assigned"] if tasks["available"] and tasks["assigned"] else None
    parts.append({"key": "tasks", "label": "Daily tasks", "weight": weights["tasks"], "ratio": task_ratio,
                  "detail": f"{tasks['completed']} of {tasks['assigned']} completed" if task_ratio is not None else "No daily tasks assigned"})
    hours_ratio = min(1.0, hours / expected_hours) if expected_hours > 0 else None
    parts.append({"key": "hours", "label": "Hours", "weight": weights["hours"], "ratio": hours_ratio,
                  "detail": f"{hours:g} h of {expected_hours:g} h expected" if hours_ratio is not None else "No hours expected"})
    target = daily_goal * worked_days
    stage_ratio = min(1.0, moves / target) if target > 0 else None
    parts.append({"key": "stages", "label": "Stage moves", "weight": weights["stages"], "ratio": stage_ratio,
                  "detail": f"{moves} moves against {target} ({daily_goal} a day x {worked_days} days)" if stage_ratio is not None else "No worked days to measure"})
    if with_leads:
        lead_ratio = min(1.0, leads["contacted"] / leads["assigned"])
        parts.append({"key": "leads", "label": "Lead follow-up (optional)", "weight": weights["leads"], "ratio": lead_ratio,
                      "detail": f"{leads['contacted']} of {leads['assigned']} assigned leads called"})
    measured = [p for p in parts if p["ratio"] is not None]
    total_weight = sum(p["weight"] for p in measured)
    score = round(sum(p["ratio"] * p["weight"] for p in measured) / total_weight * 100) if total_weight else None
    for p in parts:
        p["ratio"] = None if p["ratio"] is None else round(p["ratio"], 4)
    return {"score": score, "parts": parts}


# ------------------------------------------------------------------ team views

def _team_funnel(team_snapshot, moves, people_ids):
    reached = {n: 0 for n in range(1, TOTAL_STAGES + 1)}
    here = {n: 0 for n in reached}
    closed = {n: 0 for n in reached}
    for (status, stage), count in team_snapshot.items():
        if status == "active":
            here[stage] += count
        elif status == "closed":
            closed[stage] += count
        for n in reached:
            if status == "deployed" or stage >= n:
                reached[n] += count
    done_in_period = {n: 0 for n in reached}
    for row in moves:
        who = row.owner_id or row.completed_by or row.candidate_owner
        if people_ids is None or who in people_ids:
            done_in_period[row.stage] += 1
    rows = []
    for n in reached:
        previous = reached[n - 1] if n > 1 else None
        rows.append({"stage": n, "label": STAGE_LABELS[n - 1], "reached": reached[n], "inStageNow": here[n],
                     "closedHere": closed[n], "movesInPeriod": done_in_period[n],
                     "conversionPercent": _percent(reached[n], previous) if previous else None,
                     "lostFromPrevious": max(0, previous - reached[n]) if previous else None})
    return rows


def _highlights(staff):
    out = []

    def add(kind, severity, person, title, detail, value=None):
        out.append({"id": f"{kind}:{person['id']}", "kind": kind, "severity": severity, "staffId": person["id"],
                    "staffName": person["name"], "title": title, "detail": detail, "value": value})

    for p in staff:
        a, t, st = p["attendance"], p["tasks"], p["stuck"]
        if p["status"] == "no_attendance":
            add("no_attendance", "high", p, "No attendance this period",
                f"Did not check in on {a['absentDays']} scheduled day{'s' if a['absentDays'] != 1 else ''}.", a["absentDays"])
        elif a["absentDays"]:
            add("absent_days", "medium", p, "Absent on scheduled days",
                "No check-in on " + ", ".join(a["absentDates"]) + ".", a["absentDays"])
        if a["missingCheckouts"]:
            add("missing_checkout", "low", p, "Did not check out",
                f"{a['missingCheckouts']} session{'s were' if a['missingCheckouts'] != 1 else ' was'} left open, so those hours are not counted.", a["missingCheckouts"])
        if (p["status"] == "worked" and a["expectedHours"] >= LOW_HOURS_MIN_EXPECTED and a["hoursPercent"] is not None
                and a["hoursPercent"] < LOW_HOURS_PERCENT):
            add("low_hours", "medium", p, "Fewer hours than expected",
                f"{a['hoursWorked']:g} h worked of {a['expectedHours']:g} h expected ({a['hoursPercent']:g}%).", a["hoursPercent"])
        if t["available"] and t["assigned"] >= LOW_COMPLETION_MIN_TASKS and t["completionPercent"] is not None \
                and t["completionPercent"] < LOW_COMPLETION_PERCENT:
            add("low_completion", "high" if t["completionPercent"] < LOW_COMPLETION_PERCENT / 2 else "medium", p,
                "Low daily task completion", f"{t['completed']} of {t['assigned']} daily tasks completed ({t['completionPercent']:g}%).",
                t["completionPercent"])
        if t["available"] and (t["backlog"] >= BACKLOG_MIN or t["overdue"] >= BACKLOG_MIN):
            add("backlog", "medium", p, "Postponed and overdue backlog",
                f"{t['backlog']} unfinished from earlier days (oldest {t['oldestBacklogDays']} days), {t['overdue']} past their deadline.",
                t["backlog"])
        lead = p.get("leads") or {}
        if lead.get("available"):
            if lead["assigned"] >= LEADS_UNTOUCHED_MIN and lead["contacted"] == 0:
                add("leads_untouched", "medium", p, "Leads assigned but none called",
                    f"{lead['assigned']} leads were assigned in this period and none of them was called.", lead["assigned"])
            if lead["followUpsOverdue"] >= LEADS_OVERDUE_MIN:
                add("leads_overdue", "low", p, "Lead follow-ups overdue",
                    f"{lead['followUpsOverdue']} lead follow-ups are already past their date.", lead["followUpsOverdue"])
        if st["count"]:
            first = st["examples"][0] if st["examples"] else None
            idle = p["stages"]["moves"] == 0 and p["status"] == "worked"
            add("stuck_candidates", "high" if idle else "medium", p,
                "Idle with candidates stuck" if idle else "Candidates stuck in a stage",
                f"{st['count']} active candidate{'s have' if st['count'] != 1 else ' has'} not moved for {STUCK_DAYS}+ days "
                f"(longest {st['oldestDays']} days" + (f", e.g. {first['candidate']} at {first['stageLabel']}" if first else "") + ").",
                st["count"])
        elif p["status"] == "worked" and p["stages"]["moves"] == 0 and t["completed"] == 0 and p["stages"]["activeCandidates"]:
            add("idle", "medium", p, "Worked but moved no candidate",
                f"{a['hoursWorked']:g} h worked with {p['stages']['activeCandidates']} active candidates, but no stage was completed.", 0)
    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda h: (rank[h["severity"]], h["staffName"].lower(), h["kind"]))
    return out


def _summary(staff, funnel, tasks_available):
    counted = [p for p in staff if p["status"] != "not_scheduled"]
    assigned = sum(p["tasks"]["assigned"] for p in staff)
    completed = sum(p["tasks"]["completed"] for p in staff)
    scores = [p["score"] for p in staff if p["score"] is not None]
    hours = round(sum(p["attendance"]["hoursWorked"] for p in staff), 2)
    expected = round(sum(p["attendance"]["expectedHours"] for p in staff), 2)

    def outcome(key):
        return sum(p["outcomes"][key] for p in staff)

    def lead(key):
        return sum((p.get("leads") or {}).get(key, 0) for p in staff)

    leads_worked = lead("worked")

    return {
        "staffTotal": len(staff), "staffWorked": sum(p["status"] == "worked" for p in staff),
        "staffAbsent": sum(p["status"] == "no_attendance" for p in staff),
        "staffPending": sum(p["status"] == "pending" for p in staff),
        "staffNotScheduled": sum(p["status"] == "not_scheduled" for p in staff), "staffConsidered": len(counted),
        "hoursWorked": hours, "hoursExpected": expected,
        "activeHours": round(sum(p["attendance"]["activeHours"] for p in staff), 2),
        "tasksAssigned": assigned, "tasksCompleted": completed, "tasksPostponed": sum(p["tasks"]["postponed"] for p in staff),
        "tasksOverdue": sum(p["tasks"]["overdue"] for p in staff), "tasksAvailable": tasks_available,
        "taskCompletionPercent": _percent(completed, assigned),
        "stageMoves": sum(p["stages"]["moves"] for p in staff),
        "forwarded": outcome("forwarded"), "interviewsScheduled": outcome("interviewsScheduled"), "selected": outcome("selected"),
        "visaReceived": outcome("visaReceived"), "visaStamped": outcome("visaStamped"), "deployed": outcome("deployed"),
        "stuckCandidates": sum(p["stuck"]["count"] for p in staff),
        "averageScore": round(sum(scores) / len(scores)) if scores else None,
        "leadsAvailable": bool(staff) and all((p.get("leads") or {}).get("available") for p in staff),
        "leadsAssigned": lead("assigned"), "leadsContacted": lead("contacted"), "leadsCalls": lead("calls"),
        "leadsWorked": leads_worked, "leadsInterested": lead("interested"), "leadsResumesReceived": lead("resumesReceived"),
        "leadsConverted": lead("converted"), "leadsConversionPercent": _percent(lead("converted"), leads_worked),
        "leadsFollowUpsOverdue": lead("followUpsOverdue"),
    }
