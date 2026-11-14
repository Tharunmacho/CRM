"""Daily work (today's candidates) and Follow-up: each recruiter's own candidates in two flat lists. No quota, slots or deadlines.

Three buckets move on their own, computed from the live records on every read (nothing is allocated, nothing is stored):

* **Today's candidates** (`/api/daily-work`): candidates the recruiter OWNS whose status is new, in process or needs review
  (`WORKABLE`) and that still have prescreening to do: no active application yet (the initial call / matching), or an active
  application at stage 1 (Prescreening). When prescreening is finished (stage 1 completed) the application moves to stage 2, the
  task is complete and the candidate leaves the to-work part at once. Deployed, not-interested and ownerless candidates are never
  listed. No daily cap, no morning/evening, no slots, no deadlines, nothing postponed or carried over.
* **Follow-up** (`/api/follow-up`): candidates the recruiter owns whose application is ACTIVE at stage 2 to 11: selected and
  still moving through the later stages. An application that closed (any negative exit: rejected, declined, not received,
  unfit, not interested ...) is no longer active, so it drops out; only candidates still going forward are listed. The stage
  rules decide what closes an application; this module only reads `status = active` and `current_stage`.
* **Travelled**: stage 11 completes, the application becomes deployed and the candidate shows on the Travelled screen
  (`/api/travelled`, which reads deployed applications). Nothing here.

Today's order, stable and the same on every read:
    1. candidates still to work, those with a stage waiting first (an open application-stage task on an active placement, the one
       waiting longest first), then the rest, newest candidate first (`created_at`, the assignment time is not stored
       separately), the id breaks ties;
    2. then the "Done today" group, most recently finished first.
A candidate is *done today* when its recruiter finished prescreening (stage 1) for it today, credited to the owner and not reopened,
or its initial call got a real outcome today (called, interest answered yes or no). A candidate whose prescreening finished today is
shown in "Done today" (ticked, counted) until the day ends even though it is already in Follow-up; tomorrow only Follow-up has it.
One row per candidate. Admin and manager read every recruiter's lists (optionally one recruiter) with per-recruiter counts.

Follow-up order: oldest last update first so stale candidates surface. The last update is the latest of the last stage completed,
the last remark written on the placement's tasks, the last draft saved and the placement's creation. One row per active placement.

The goal settings (`workflow_daily_goals`), the old per-day snapshot and the old item table (`workflow_daily_work_items`) stay
in the database: the weekly performance report still reads the stage-move target and the history of finished items. Nothing
writes new items; `scan_daily_work` only retires the items the old quota left open.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, exists, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.parallel_reads import _SLOTS
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Candidate, CandidateStatus, Client, Company, DailyGoal, Designation,
    Order, Remark, Source, Task, TaskKind, User,
)

# Owned candidates the daily list draws on: being worked (new / in process) or assigned but still waiting for their
# recruiter's review (missing phone, document warnings). A candidate does not stop being the recruiter's work because
# the intake flagged its data: the assigned recruiter is the one who fixes it, so it must show on their list.
WORKABLE = (CandidateStatus.new, CandidateStatus.in_process, CandidateStatus.needs_review)
FOLLOW_UP_STAGES = (2, 11)  # an active application at these stages is Follow-up (11 = ticket booking pending); completing stage 11 deploys it (Travelled)
FALLBACK_GOALS = {"candidates": (5, 3), "forwarded_to_client": (2, None), "stage_moves": (5, None)}
METRICS = tuple(FALLBACK_GOALS)
MAX_ROWS = 1500  # a safety cap on the rows of one response; the counts are exact whatever the cap
SOURCE_LABELS = {"email": "Email", "whatsapp": "WhatsApp", "walk_in": "Walk-in", "referral": "Referral", "other": "Other"}
UNFIT_REAPPLYING = "Unfit: reapplying"  # a stage outcome that is not a real move (same exclusion as the performance report)


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

# The admin's goal settings are not used by the Daily work screen any more. They are kept because the weekly performance
# report scores stage moves against the stage-move target and `/api/daily-goals` still edits them.

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


# ------------------------------------------------------------------------------------------------ the list

def _call_done():
    """The initial call has a real outcome: made, with interest answered yes or no (an unanswered call stays to be worked)."""
    return and_(Candidate.call_recorded_at.is_not(None), Candidate.call_called.is_(True), Candidate.call_interested.in_(["yes", "no"]))


def _waiting_since():
    """When the oldest open stage task of an active placement of this candidate was created; NULL when no stage waits."""
    return (select(func.min(Task.created_at)).select_from(Task).join(Application, Application.id == Task.application_id)
            .where(Task.candidate_id == Candidate.id, Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
                   Task.completed_at.is_(None), Task.cancelled_at.is_(None), Application.status == ApplicationStatus.active)
            .correlate(Candidate).scalar_subquery())


def _done_at(start: datetime, end: datetime):
    """When the owner finished today's work on this candidate in [start, end): prescreening (stage 1) completed or the initial call answered, else NULL."""
    stage = (select(func.max(ApplicationStage.completed_at)).select_from(ApplicationStage)
             .join(Application, Application.id == ApplicationStage.application_id)
             .where(Application.candidate_id == Candidate.id, ApplicationStage.stage == 1, ApplicationStage.completed_at >= start, ApplicationStage.completed_at < end,
                    ApplicationStage.reopened_reason.is_(None), ApplicationStage.outcome.is_distinct_from(UNFIT_REAPPLYING),
                    func.coalesce(ApplicationStage.owner_id, ApplicationStage.completed_by) == Candidate.owner_id)
             .correlate(Candidate).scalar_subquery())
    call = case((and_(_call_done(), Candidate.call_recorded_at >= start, Candidate.call_recorded_at < end), Candidate.call_recorded_at))
    return func.greatest(stage, call)


def _in_follow_up():
    """The candidate has an active application past prescreening: it belongs to Follow-up, not to today's candidates."""
    return exists().where(Application.candidate_id == Candidate.id, Application.status == ApplicationStatus.active,
                          Application.current_stage >= FOLLOW_UP_STAGES[0])


def _listed(done):
    """On today's list: not past prescreening, or prescreening was finished today (shown ticked until the day ends)."""
    return or_(~_in_follow_up(), done.is_not(None))


def owner_counts(db, owner_ids, *, now: datetime | None = None, tz=None) -> dict[str, tuple[int, int]]:
    """{owner id: (candidates to work, done today)} for these recruiters (ids, or a select of ids), exact whatever the row cap."""
    now = now or datetime.now(timezone.utc)
    start, end = day_bounds(agency_today(now, tz), tz)
    done = _done_at(start, end)
    rows = db.execute(select(Candidate.owner_id, func.count().filter(done.is_(None)), func.count().filter(done.is_not(None)))
                      .where(Candidate.owner_id.in_(owner_ids), Candidate.status.in_(WORKABLE), _listed(done)).group_by(Candidate.owner_id)).all()
    return {owner: (int(todo), int(finished)) for owner, todo, finished in rows}


def summarise(counts: list[tuple[int, int]]) -> dict:
    """Header numbers from (to work, done today) pairs: one recruiter's, or the sum over several."""
    todo = sum(c[0] for c in counts)
    finished = sum(c[1] for c in counts)
    return {"total": todo + finished, "toWork": todo, "doneToday": finished}


def fetch_rows(db, owner_ids, *, now: datetime | None = None, tz=None) -> list:
    """The list's rows (candidate, waiting since, done at) for these recruiters (ids, or a select of ids), in list order.

    One statement. `load_rows` turns them into the screen's dicts. See the module docstring for the order."""
    now = now or datetime.now(timezone.utc)
    start, end = day_bounds(agency_today(now, tz), tz)
    inner = (select(Candidate.id.label("cid"), _waiting_since().label("waiting_since"), _done_at(start, end).label("done_at"))
             .where(Candidate.owner_id.in_(owner_ids), Candidate.status.in_(WORKABLE), _listed(_done_at(start, end))).subquery("w"))
    return db.execute(
        select(Candidate, inner.c.waiting_since, inner.c.done_at).join(inner, inner.c.cid == Candidate.id)
        .order_by(inner.c.done_at.is_not(None), inner.c.done_at.desc().nulls_last(), inner.c.waiting_since.is_(None),
                  inner.c.waiting_since.asc().nulls_last(), Candidate.created_at.desc(), Candidate.id)
        .limit(MAX_ROWS)).all()


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


def load_rows(db, owner_ids, *, now: datetime | None = None, tz=None, parallel: bool = False, fetched: list | None = None) -> list[dict]:
    """The screen's rows for these recruiters (ids, or a select of ids), in list order: to work first, then done today.

    `parallel=True` runs the two lookups that depend only on the rows (active placements, open tasks) side by side; use
    it only when the caller has nothing uncommitted (they run on their own connections). `fetched` is the result of
    `fetch_rows` when the caller already ran it."""
    now = now or datetime.now(timezone.utc)
    rows = fetch_rows(db, owner_ids, now=now, tz=tz) if fetched is None else fetched
    if not rows:
        return []
    ids = [cand.id for cand, _waiting, _done in rows]
    app_query = (select(Application, Order, Company, Client, Designation)
                 .join(Order, Order.id == Application.order_id).join(Company, Company.id == Order.company_id)
                 .join(Client, Client.id == Company.client_id).join(Designation, Designation.id == Order.designation_id)
                 .where(Application.candidate_id.in_(ids), Application.status == ApplicationStatus.active)
                 .order_by(Application.created_at.desc(), Application.id))
    # Remarks live on tasks: the open stage task of the placement, else the candidate's open initial-call task.
    task_query = (select(Task.candidate_id, Task.application_id, Task.id, Task.kind).where(
        Task.candidate_id.in_(ids), Task.kind.in_([TaskKind.stage, TaskKind.remedical, TaskKind.initial_call]),
        Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.created_at, Task.id))
    lookups = (lambda s: s.execute(app_query).all(), lambda s: s.execute(task_query).all())
    placed, task_rows = read_together(db, *lookups) if parallel else [look(db) for look in lookups]
    apps: dict[str, list] = defaultdict(list)
    for placement in placed:
        apps[placement[0].candidate_id].append(placement)
    stage_tasks: dict[str, str] = {}  # application id -> its oldest open stage task
    call_tasks: dict[str, str] = {}   # candidate id -> its newest open initial-call task
    for candidate_id, application_id, task_id, kind in task_rows:
        if kind == TaskKind.initial_call:
            call_tasks[candidate_id] = task_id
        elif application_id:
            stage_tasks.setdefault(application_id, task_id)
    chosen: dict[str, tuple | None] = {}
    remark_tasks: dict[str, str | None] = {}
    for cand in (c for c, _w, _d in rows):
        mine = apps.get(cand.id, [])
        # The placement with a stage waiting wins; otherwise the newest active one (the query is newest first).
        placement = next((p for p in mine if p[0].id in stage_tasks), mine[0] if mine else None)
        chosen[cand.id] = placement
        remark_tasks[cand.id] = (stage_tasks.get(placement[0].id) if placement else None) or call_tasks.get(cand.id)
    remarks = _remarks(db, sorted({t for t in remark_tasks.values() if t}))
    out = []
    for cand, waiting, finished in rows:
        placement = chosen[cand.id]
        app, order, company, client, designation = placement if placement else (None, None, None, None, None)
        remark_task = remark_tasks[cand.id]
        remark = remarks.get(remark_task) if remark_task else None
        out.append({
            "id": cand.id, "kind": "stage" if app else "call", "taskId": remark_task, "applicationId": app.id if app else None,
            "candidateId": cand.id, "ownerId": cand.owner_id,
            "stage": app.current_stage if app else None, "waiting": waiting is not None,
            "status": "done" if finished is not None else "open", "completedAt": finished,
            **placement_fields(order, company, client, designation, cand),
            "candidate": cand.name, "candidateRef": cand.ref, "candidateCareId": cand.care_id,
            "orderRef": order.ref if order else None,
            "referral": {"source": cand.source.value, "label": SOURCE_LABELS.get(cand.source.value, "Other"),
                         "detail": cand.source_detail if cand.source == Source.referral else None},
            "remark": {"body": remark["body"], "at": remark["at"], "by": remark["by"], "count": remark["count"]} if remark else None,
        })
    return out


# ------------------------------------------------------------------------------------------------ follow-up

def _follow_up_filter(owner_ids):
    """Active placements at stage 2-11 of candidates these recruiters own (and that are still open: not deployed / not interested)."""
    return [Candidate.owner_id.in_(owner_ids), Application.status == ApplicationStatus.active,
            Application.current_stage.between(*FOLLOW_UP_STAGES),
            Candidate.status.notin_([CandidateStatus.deployed, CandidateStatus.not_interested])]


def follow_up_counts(db, owner_ids) -> dict[str, int]:
    """{owner id: candidates in follow-up} for these recruiters (ids, or a select of ids)."""
    rows = db.execute(select(Candidate.owner_id, func.count(func.distinct(Candidate.id)))
                      .select_from(Application).join(Candidate, Candidate.id == Application.candidate_id)
                      .where(*_follow_up_filter(owner_ids)).group_by(Candidate.owner_id)).all()
    return {owner: int(n) for owner, n in rows}


def fetch_follow_up(db, owner_ids) -> list:
    """(application, candidate, order, company, client, designation, last update) for these recruiters, stalest first. One statement."""
    stage_at = (select(func.max(ApplicationStage.completed_at)).where(ApplicationStage.application_id == Application.id)
                .correlate(Application).scalar_subquery())
    remark_at = (select(func.max(Remark.created_at)).select_from(Remark).join(Task, Task.id == Remark.task_id)
                 .where(Task.application_id == Application.id).correlate(Application).scalar_subquery())
    last = func.greatest(stage_at, remark_at, Application.draft_saved_at, Application.created_at).label("last_update")
    return db.execute(
        select(Application, Candidate, Order, Company, Client, Designation, last)
        .join(Candidate, Candidate.id == Application.candidate_id).join(Order, Order.id == Application.order_id)
        .join(Company, Company.id == Order.company_id).join(Client, Client.id == Company.client_id)
        .join(Designation, Designation.id == Order.designation_id)
        .where(*_follow_up_filter(owner_ids)).order_by(last.asc(), Application.id).limit(MAX_ROWS)).all()


def load_follow_up(db, owner_ids, *, fetched: list | None = None) -> list[dict]:
    """The Follow-up screen's rows, oldest last update first. `fetched` is the result of `fetch_follow_up` when already read."""
    rows = fetch_follow_up(db, owner_ids) if fetched is None else fetched
    if not rows:
        return []
    app_ids = [app.id for app, *_ in rows]
    # The newest remark written on any of the placement's tasks, and the open stage task a new remark goes on.
    latest: dict[str, dict] = {}
    for application_id, body, name, at in db.execute(
            select(Task.application_id, Remark.body, User.name, Remark.created_at).select_from(Remark)
            .join(Task, Task.id == Remark.task_id).outerjoin(User, User.id == Remark.author_id)
            .where(Task.application_id.in_(app_ids)).order_by(Remark.created_at.desc(), Remark.id)):
        entry = latest.setdefault(application_id, {"body": body, "at": at, "by": name or "Unknown", "count": 0})
        entry["count"] += 1
    open_task: dict[str, str] = {}
    for application_id, task_id in db.execute(select(Task.application_id, Task.id).where(
            Task.application_id.in_(app_ids), Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
            Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.created_at, Task.id)):
        open_task.setdefault(application_id, task_id)
    out = []
    for app, cand, order, company, client, designation, last_update in rows:
        out.append({
            "id": app.id, "applicationId": app.id, "taskId": open_task.get(app.id), "candidateId": cand.id, "ownerId": cand.owner_id,
            "stage": app.current_stage, "lastUpdateAt": last_update,
            **placement_fields(order, company, client, designation, cand),
            "candidate": cand.name, "candidateRef": cand.ref, "candidateCareId": cand.care_id, "orderRef": order.ref,
            "remark": latest.get(app.id),
        })
    return out


# ------------------------------------------------------------------------------------------------ worker

def retire_open_items(db, *, now: datetime | None = None) -> int:
    """Release every item the old daily quota left open, so the report no longer counts it as backlog. Caller commits.

    Finished items are history and stay as they are. Nothing creates items any more, so this is a one-off clean-up that
    then finds nothing (the partial index on open items makes the empty case cheap)."""
    now = now or datetime.now(timezone.utc)
    return db.execute(text("UPDATE workflow_daily_work_items SET status = 'released', released_at = :now, release_reason = 'quota_removed' "
                           "WHERE status = 'open'"), {"now": now}).rowcount or 0


def scan_daily_work(db, supervisors: list[str], *, now: datetime | None = None, tz=None) -> dict:
    """Worker pass. The daily list is computed live, so there is nothing to allocate and no overdue or carry-over alert to send;
    the only job left is retiring the items the old quota left open."""
    return {"dailyRetired": retire_open_items(db, now=now)}
