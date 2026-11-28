"""Daily work (today's candidates) and Follow-up: each recruiter's own candidates in two flat lists. No quota, slots or deadlines.

Three buckets move on their own, computed from the live records on every read (nothing is allocated, nothing is stored):

* **Today's candidates** (`/api/daily-work`): candidates the recruiter OWNS whose status is new, in process or needs review
  (`WORKABLE`) and that still have prescreening to do: no active application yet (the initial call / matching), or an active
  application at stage 1 (Prescreening). When prescreening is finished (stage 1 completed) the application moves to stage 2, the
  task is complete and the candidate leaves the to-work part at once. Deployed, not-interested and ownerless candidates are never
  listed, and neither is a candidate whose last placement ended in ANY negative way, because THEY declined or because they were rejected
  (they are parked in the Data Bank until they are mapped to a new job, or until a call is recorded with the answer "yes" after the closure;
  the Tasks page shows them on its Declined / Rejected tabs). A candidate nobody has contacted yet stays on the list. No daily cap, no morning/evening, no slots, no deadlines, nothing postponed or carried over.
* **Follow-up** (`/api/follow-up`): candidates the recruiter owns whose application is ACTIVE at stage 2 to 13: selected and
  still moving through the later stages. An application that closed (any negative exit: rejected, declined, not received,
  unfit, not interested ...) is no longer active, so it drops out; only candidates still going forward are listed. The stage
  rules decide what closes an application; this module only reads `status = active` and `current_stage`.
* **Travelled**: stage 13 (the air ticket) completes, the application becomes deployed and the candidate shows on the Travelled screen
  (`/api/travelled`, which reads deployed applications). Nothing here.

Today's order, stable and the same on every read:
    1. candidates still to work, those with a stage waiting first (an open application-stage task on an active placement, the one
       waiting longest first), then the rest, newest candidate first (`created_at`, the assignment time is not stored
       separately), the id breaks ties;
    2. then the "Done today" group, most recently finished first.
A candidate is *done today* when its recruiter finished prescreening (stage 1) for it today, credited to the owner and not reopened,
or its initial call got a real outcome today (called, interest answered yes or no). A candidate whose prescreening finished today is
shown in "Done today" (ticked, counted) until the day ends even though it is already in Follow-up; tomorrow only Follow-up has it.
A candidate's own NO is NOT work on this page: when a candidate declines (stage exits Declined / Offer declined / Candidate not interested /
Not travelling at any stage, or "not interested" at the initial call that takes them off a stage-1 placement) the placement closes and the
candidate goes back to the Data Bank (status not interested, or needs review kept). They leave the Tasks page AT ONCE, on the day it
happened too: no row (not even a ticked "Done today" one), and they are in no count (per-recruiter cards, sidebar badge, Today / Overall
figures); only the Data Bank has them (see `_parked`). A later "yes" call or a new mapping takes them back to the ordinary rules. A rejection is treated the same way (the
candidate is on the Rejected tab only, never also on today's list).
One row per candidate. Admin and MD read every recruiter's lists (optionally one recruiter) with per-recruiter counts.

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
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, exists, false, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.services.advance_text import advance_status
from app.services.agency_display import agency_display_expression
from app.services.parallel_reads import _SLOTS
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, Client, ClosureReason, Company, DailyGoal, Designation,
    Order, Remark, Source, Task, TaskKind, User,
)
from app.domain.stages import ADVANCE_STAGE, REVIEW_STAGE, TOTAL_STAGES, VISA_STAGE, stage_by_no

# Owned candidates the daily list draws on: being worked (new / in process) or assigned but still waiting for their
# recruiter's review (missing phone, document warnings). A candidate does not stop being the recruiter's work because
# the intake flagged its data: the assigned recruiter is the one who fixes it, so it must show on their list.
WORKABLE = (CandidateStatus.new, CandidateStatus.in_process, CandidateStatus.needs_review)
FOLLOW_UP_STAGES = (2, TOTAL_STAGES)  # an active application at these stages is Follow-up (13 = ticket booking pending); completing stage 13 deploys it (Travelled)
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
    return and_(Candidate.call_recorded_at.is_not(None), Candidate.call_called.is_(True), Candidate.call_interested.in_(["yes", "no", "rejected"]))


def _waiting_since():
    """When the oldest open stage task of an active placement of this candidate was created; NULL when no stage waits."""
    return (select(func.min(Task.created_at)).select_from(Task).join(Application, Application.id == Task.application_id)
            .where(Task.candidate_id == Candidate.id, Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
                   Task.completed_at.is_(None), Task.cancelled_at.is_(None), Application.status == ApplicationStatus.active)
            .correlate(Candidate).scalar_subquery())


def _done_at(start: datetime, end: datetime):
    """When the owner finished today's work on this candidate in [start, end): prescreening (stage 1) completed, or the initial call answered,
    else NULL. (A candidate's own no or rejection is no work here: `_parked` keeps them off the list.)"""
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


# The closures where the candidate themselves said no (stage exits "Declined", "Offer declined", "Candidate not interested",
# "Not travelling").
DECLINE_CLOSURES = (ClosureReason.stage_declined, ClosureReason.offer_declined, ClosureReason.not_interested, ClosureReason.not_travelling)
# Closures that are no outcome: the candidate was only moved off a job (unmapped, swapped). Every other closure reason is a negative end
# (the candidate's own no, or a rejection / failure): it parks the candidate (`_parked`) and lists them on Declined / Rejected.
NOT_OUTCOMES = (ClosureReason.unmapped, ClosureReason.changed_job)


def _last_application():
    """The id of the candidate's last placement: the greatest (created_at, id), so two created in the same instant never both count as last."""
    newest = aliased(Application)    # the same table again, inside the sub-select
    return (select(newest.id).where(newest.candidate_id == Candidate.id).order_by(newest.created_at.desc(), newest.id.desc())
            .limit(1).correlate(Candidate).scalar_subquery())


def _negative_end():
    """The (subject) placement ended negatively: it is closed with a reason that is an outcome (anything but unmapped / changed job)."""
    return and_(Application.status == ApplicationStatus.closed, Application.closure_reason.is_not(None),
                Application.closure_reason.notin_(NOT_OUTCOMES))


def _no_outcome():
    """The (subject) placement is no outcome of its own, so the candidate's call / status decides: there is none, or it was only unmapped / swapped,
    or it is a legacy row closed with no reason at all (never a deployed one)."""
    return or_(Application.id.is_(None),
               and_(Application.status == ApplicationStatus.closed,
                    or_(Application.closure_reason.is_(None), Application.closure_reason.in_(NOT_OUTCOMES))))


def _is_no_outcome(app) -> bool:
    return app.status == ApplicationStatus.closed and (app.closure_reason is None or app.closure_reason in NOT_OUTCOMES)


def _closed_time():
    """When the (subject) placement was closed: its `closed_at` (the event), else, for a row closed before that column existed, its last update."""
    return func.coalesce(Application.closed_at, Application.updated_at)


def _called_yes_since():
    """A "yes" call was recorded after the (subject) placement closed: the candidate is interested again. Not when the candidate's status was
    edited back to "not interested" afterwards (then the status wins and they are on Declined / Rejected)."""
    return and_(Candidate.call_recorded_at.is_not(None), Candidate.call_interested == "yes",
                Candidate.call_recorded_at > _closed_time(), Candidate.status != CandidateStatus.not_interested)


def _parked():
    """The candidate's LAST placement ended negatively (they declined, or were rejected: any closure reason except unmapped / changed job)
    and no placement is active: they wait in the Data Bank for their next job and show on the Declined / Rejected tab of the Tasks page.
    They are neither today's work nor Follow-up, and the recruiter is not asked to make the initial call again; mapping them to a new job
    brings them back at prescreening.

    "Last" is the greatest (created_at, id), so two placements created in the same instant never both count as the last one.
    The candidate is NOT parked any more when, after that placement was closed, a call was recorded with the answer "yes"
    (`record_call` puts a not-interested candidate back to "new" on a yes): they are interested again and are to be worked
    without waiting for a new mapping. "After it was closed" is `Candidate.call_recorded_at` later than the closed placement's
    closing time (`closed_at`, else the last update of a row closed before that column existed). The status plays no part here:
    a candidate who is "Needs review" and declined stays parked (their status is kept so the flag still shows)."""
    latest = _last_application()
    called_yes_since = _called_yes_since()
    ended = exists().where(Application.id == latest, Application.candidate_id == Candidate.id, _negative_end(),
                           ~func.coalesce(called_yes_since, false())).correlate(Candidate)   # explicit: this is also used inside other sub-selects
    active = exists().where(Application.candidate_id == Candidate.id, Application.status == ApplicationStatus.active).correlate(Candidate)
    return and_(ended, ~active)


def _workable():
    """Today's-list status: new / in process / needs review, or any status with a live placement (a stale "not interested" next to an active
    placement must not take the candidate off every list)."""
    return or_(Candidate.status.in_(WORKABLE),
               exists().where(Application.candidate_id == Candidate.id, Application.status == ApplicationStatus.active).correlate(Candidate))


def _on_list(start: datetime, end: datetime):
    """The candidate is on today's list: workable, not parked after a negative end of their last placement (declined or rejected; not even on
    the day it happened: the Data Bank and the Declined / Rejected tabs have them at once), and either not past prescreening or finished today (shown ticked until the day ends: the candidate is gone tomorrow)."""
    done = _done_at(start, end)
    return and_(_workable(), ~_parked(), or_(~_in_follow_up(), done.is_not(None)))


def owner_counts(db, owner_ids, *, now: datetime | None = None, tz=None) -> dict[str, tuple[int, int]]:
    """{owner id: (candidates to work, done today)} for these recruiters (ids, or a select of ids), exact whatever the row cap."""
    now = now or datetime.now(timezone.utc)
    start, end = day_bounds(agency_today(now, tz), tz)
    done = _done_at(start, end)
    rows = db.execute(select(Candidate.owner_id, func.count().filter(done.is_(None)), func.count().filter(done.is_not(None)))
                      .where(Candidate.owner_id.in_(owner_ids), _on_list(start, end)).group_by(Candidate.owner_id)).all()
    return {owner: (int(todo), int(finished)) for owner, todo, finished in rows}


def summarise(counts: list[tuple[int, int]]) -> dict:
    """Header numbers from (to work, done today) pairs: one recruiter's, or the sum over several."""
    todo = sum(c[0] for c in counts)
    finished = sum(c[1] for c in counts)
    return {"total": todo + finished, "toWork": todo, "doneToday": finished}


def fetch_rows(db, owner_ids, *, now: datetime | None = None, tz=None) -> list:
    """The list's rows (candidate, waiting since, done at, agency, latest stage completion today) for these recruiters, in list order.
    `agency` is the recorded agency, or None when no assignment is known.

    One statement. `load_rows` turns them into the screen's dicts. See the module docstring for the order."""
    now = now or datetime.now(timezone.utc)
    start, end = day_bounds(agency_today(now, tz), tz)
    inner = (select(Candidate.id.label("cid"), _waiting_since().label("waiting_since"), _done_at(start, end).label("done_at"))
             .where(Candidate.owner_id.in_(owner_ids), _on_list(start, end)).subquery("w"))
    # Look across the candidate's placements, including a stage finished on a placement that has since closed.
    moved = (select(func.max(ApplicationStage.completed_at)).join(Application, Application.id == ApplicationStage.application_id)
             .where(Application.candidate_id == Candidate.id, ApplicationStage.completed_at >= start, ApplicationStage.completed_at < end,
                    ApplicationStage.reopened_reason.is_(None), ApplicationStage.outcome.is_distinct_from(UNFIT_REAPPLYING))
             .correlate(Candidate).scalar_subquery())
    return db.execute(
        select(Candidate, inner.c.waiting_since, inner.c.done_at, agency_display_expression(require_assignment=True).label("agency"), moved)
        .join(inner, inner.c.cid == Candidate.id)
        .order_by(inner.c.done_at.is_not(None), inner.c.done_at.desc().nulls_last(), inner.c.waiting_since.is_(None),
                  inner.c.waiting_since.asc().nulls_last(), Candidate.created_at.desc(), Candidate.id)
        .limit(MAX_ROWS)).all()


REMARK, NOTE = "remark", "note"



def _remark_query(*where):
    """The log (newest first) as (body, author name, time, task id, remark id, candidate id, application id, cleared at, author id, kind) rows: the
    Remarks select's remarks AND the notes typed in the NOTES column (`_remark_summary` reads the first, `_candidate_notes` the others, so one
    statement serves both). A deleted entry is never listed. `cleared at` is set once the Remarks cell was cleared ("Select remark"): such a
    remark is no longer the shown one. The author's name rides along in the same statement (no separate read of the whole user table)."""
    return (select(Remark.body, User.name, Remark.created_at, Remark.task_id, Remark.id, Task.candidate_id, Task.application_id, Remark.cleared_at, Remark.author_id, Remark.kind)
            .select_from(Remark).join(Task, Task.id == Remark.task_id)
            .outerjoin(User, User.id == Remark.author_id).where(Remark.deleted_at.is_(None), *where).order_by(Remark.created_at.desc(), Remark.id))


def _remark_summary(remark_rows, *, by_application: bool = False) -> dict[str, dict]:
    """{task id (or application id): newest remark, its author's name and how many there are} from `_remark_query` rows, newest first.
    Only remarks count (a typed note is never the remark). A remark the cell was cleared of ("Select remark") is not the shown remark: a row
    whose remarks are all cleared has none."""
    result: dict[str, dict] = {}
    for body, name, at, task_id, _note_id, _candidate_id, application_id, cleared_at, _author_id, kind in remark_rows:
        key = application_id if by_application else task_id
        if key is None or cleared_at is not None or kind != REMARK:
            continue
        entry = result.setdefault(key, {"body": body, "at": at, "by": name or "Unknown", "count": 0})
        entry["count"] += 1
    return result


def _candidate_notes(remark_rows) -> dict[str, list]:
    """{candidate id: [(application id, note)]}: every NOTE (kind 'note', typed in the NOTES column; all days, finished tasks included), newest first."""
    found: dict[str, list] = defaultdict(list)
    for body, name, at, _task_id, note_id, candidate_id, application_id, _cleared_at, author_id, kind in remark_rows:
        if kind == NOTE:
            found[candidate_id].append((application_id, {"id": note_id, "body": body, "at": at, "by": name or "Unknown", "byId": author_id}))
    return found


def _row_notes(found, candidate_id, app_id):
    """The candidate's initial-call notes and the notes on the placement shown in this row, newest first."""
    return [note for placement, note in found.get(candidate_id, ()) if placement is None or placement == app_id]


def _note_fields(notes: list[dict], *, now: datetime, tz=None) -> dict:
    """`notes` (all of the row's, newest first) and `todayNotes` (those written on the agency day of `now`: the TODAY chip and the Excel's Today cell)."""
    start, end = day_bounds(agency_today(now, tz), tz)
    return {"notes": notes, "todayNotes": [note for note in notes if start <= note["at"] < end]}


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
    # A GET's session is bound to an engine that already carries options (`read_engine`); SQLAlchemy cannot layer an isolation level on
    # such an option engine, which made every caller fall back to running its reads one after another. Start from the plain engine.
    return getattr(engine, "_proxied", engine).execution_options(isolation_level="AUTOCOMMIT")


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


# REVIEW_STAGE (11, "Google review") comes from the stage definitions; the review is "pending" from there on until it is obtained.
# Before migration 0056 it was ticked on the visa stage, so a value saved there still counts (see stage_facts).


def stage_facts_query(*where):
    """Stage 6 (advance), stage 10 (visa arrival, which used to carry the Google review tick), stage 11 (Google review) and stage 13 (air ticket:
    the travel date) records, never a reopened one, for the placements in `where`.

    A reopened stage-13 record is still read (so that it stays the latest attempt and an older attempt's date does not come back), but its data
    is blanked: a placement whose ticket stage was reopened has not travelled yet."""
    reopened = ApplicationStage.reopened_reason.isnot(None)
    data = case((and_(ApplicationStage.stage == TOTAL_STAGES, reopened), None), else_=ApplicationStage.data).label("data")
    return (select(ApplicationStage.application_id, ApplicationStage.stage, ApplicationStage.attempt, ApplicationStage.completed_at,
                   ApplicationStage.amount, ApplicationStage.amount_currency, data)
            .where(ApplicationStage.stage.in_((ADVANCE_STAGE, VISA_STAGE, REVIEW_STAGE, TOTAL_STAGES)),
                   or_(ApplicationStage.reopened_reason.is_(None), ApplicationStage.stage == TOTAL_STAGES), *where))


def _travel_day(data) -> str | None:
    """The stage-13 record's `travelDate` as an ISO date, or None when it is missing or not a real calendar day (the Travelled screen's rule)."""
    raw = (data or {}).get("travelDate")
    if not isinstance(raw, str) or len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def stage_facts(rows) -> dict[str, dict]:
    """{application id: {"advance": (amount, currency) or None, "review": bool, "status", "total", "expected", "currency", "travelDate"}}
    from `stage_facts_query` rows.

    The latest attempt of a stage is the effective record (the Advance collections report's rule). The advance is the amount the
    recruiter recorded on stage 6, in the currency recorded there (INR unless another was chosen); a missing, zero or unreadable
    amount is no advance, and so is the amount of a Pending advance (it is only the one expected). It is the candidate's own money
    paid in; no invoice, CGST or GST figure is read here.

    The stage-9 record also gives `status` ('collected', 'pending' or None; a record from before the status existed reads as
    Collected when it holds an amount), `total` (the total service charge, or None), `expected` (the advance expected while
    pending, or None) and `currency` of those amounts (None without a stage-9 record). `travelDate` is the stage-13 (air ticket) record's
    travel date as an ISO day, or None (no ticket yet, no date, or the stage was reopened). `advance_text.advance_cell` turns an entry
    into the tracker sheet's Advance cell."""
    latest: dict[tuple[str, int], tuple] = {}
    for row in rows:
        key = (row[0], row[1])
        if key not in latest or (row[2], row[3]) > (latest[key][2], latest[key][3]):
            latest[key] = row
    facts: dict[str, dict] = {}
    for (application_id, stage), (_a, _s, _attempt, _at, amount, currency, data) in latest.items():
        entry = facts.setdefault(application_id, {"advance": None, "review": False, "status": None, "total": None, "expected": None, "currency": None, "travelDate": None})
        data = data or {}
        if stage == TOTAL_STAGES:
            entry["travelDate"] = _travel_day(data)
        elif stage == ADVANCE_STAGE:
            raw = amount if amount is not None else data.get("amount")
            value = None
            try:
                value = Decimal(str(raw))
            except Exception:
                pass
            if value is not None and not (value.is_finite() and value > 0):
                value = None
            entry["currency"] = (currency or data.get("amountCurrency") or data.get("currency") or "INR").upper()
            status = advance_status(data, raw)
            entry["status"] = status
            total = data.get("serviceChargeTotal")
            try:
                total = Decimal(str(total))
                entry["total"] = float(total.quantize(Decimal("0.01"))) if total.is_finite() and total > 0 else None
            except Exception:
                entry["total"] = None
            if value is not None:
                figure = float(value.quantize(Decimal("0.01")))
                if status == "pending":
                    entry["expected"] = figure
                else:
                    entry["advance"] = (figure, entry["currency"])
        else:
            entry["review"] = entry["review"] or data.get("googleReview") is True
    return facts


def payment_fields(app, facts: dict[str, dict]) -> dict:
    """`advanceAmount` / `advanceCurrency` (null when none recorded) and `googleReview` ('obtained', 'pending' from stage 11 on, else null).

    Also `advanceStatus` ('collected', 'pending' or null), `advanceExpected` (the amount of a pending advance) and `serviceChargeTotal`.
    `advanceAmount` stays the money actually collected: a pending advance never shows there."""
    fact = facts.get(app.id) if app else None
    advance = fact["advance"] if fact else None
    review = "obtained" if fact and fact["review"] else "pending" if app and app.current_stage >= REVIEW_STAGE else None
    currency = advance[1] if advance else fact["currency"] if fact and fact["expected"] is not None else None
    return {"advanceAmount": advance[0] if advance else None, "advanceCurrency": currency, "googleReview": review,
            "advanceStatus": fact["status"] if fact else None, "advanceExpected": fact["expected"] if fact else None,
            "serviceChargeTotal": fact["total"] if fact else None, "travelDate": fact["travelDate"] if fact else None}


OPEN_WORK_KINDS = [TaskKind.stage, TaskKind.remedical, TaskKind.initial_call]


def placement_reads(candidate_ids, *, now: datetime, tz=None) -> tuple:
    """The four lookups behind the rows of today's list, for these candidates (their ids, or a select of ids): active placements,
    open tasks, advance / review facts and the remarks written on those open tasks or on this agency day (the row's notes).
    None depends on another."""
    # Active placements only: a listed candidate has no declined / rejected placement to show (they are parked, see `_parked`).
    app_query = (select(Application, Order, Company, Client, Designation)
                 .join(Order, Order.id == Application.order_id).join(Company, Company.id == Order.company_id)
                 .join(Client, Client.id == Company.client_id).join(Designation, Designation.id == Order.designation_id)
                 .where(Application.candidate_id.in_(candidate_ids), Application.status == ApplicationStatus.active)
                 .order_by(Application.created_at.desc(), Application.id))
    # Remarks live on tasks: the open stage task of the placement, else the candidate's open initial-call task.
    open_tasks = (Task.candidate_id.in_(candidate_ids), Task.kind.in_(OPEN_WORK_KINDS), Task.completed_at.is_(None), Task.cancelled_at.is_(None))
    task_query = select(Task.candidate_id, Task.application_id, Task.id, Task.kind).where(*open_tasks).order_by(Task.created_at, Task.id)
    # Advance and Google review of the candidates' active placements.
    fact_query = stage_facts_query(Application.id == ApplicationStage.application_id, Application.candidate_id.in_(candidate_ids),
                                   Application.status == ApplicationStatus.active)
    # The remarks of every open task of these candidates (each row then picks its own task's) plus every note of theirs, finished
    # tasks included: no wait for the task ids.
    remark_query = _remark_query(Task.candidate_id.in_(candidate_ids), or_(and_(*open_tasks[1:]), Remark.kind == NOTE))
    return (lambda s: s.execute(app_query).all(), lambda s: s.execute(task_query).all(), lambda s: s.execute(fact_query).all(),
            lambda s: s.execute(remark_query).all())


def assemble_rows(rows, placed, task_rows, fact_rows, remark_rows, *, now: datetime | None = None, tz=None) -> list[dict]:
    """The screen's rows from `fetch_rows` rows and the results of `placement_reads`."""
    now = now or datetime.now(timezone.utc)
    facts = stage_facts(fact_rows)
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
    for cand in (row[0] for row in rows):
        mine = apps.get(cand.id, [])
        # The placement with a stage waiting wins; otherwise the newest active one (the query is newest first).
        placement = next((p for p in mine if p[0].id in stage_tasks), mine[0] if mine else None)
        chosen[cand.id] = placement
        remark_tasks[cand.id] = (stage_tasks.get(placement[0].id) if placement else None) or call_tasks.get(cand.id)
    remarks = _remark_summary(remark_rows)
    found_notes = _candidate_notes(remark_rows)
    out = []
    for cand, waiting, finished, agency, moved in rows:
        placement = chosen[cand.id]
        app, order, company, client, designation = placement if placement else (None, None, None, None, None)
        stage_no = app.current_stage if app else None
        remark_task = remark_tasks[cand.id]
        remark = remarks.get(remark_task) if remark_task else None
        out.append({
            "id": cand.id, "kind": "stage" if app else "call", "taskId": remark_task, "applicationId": app.id if app else None,
            "candidateId": cand.id, "ownerId": cand.owner_id,
            "commencementDate": app.commencement_date.isoformat() if app and app.commencement_date else None,
            "stage": stage_no, "stageTitle": stage_by_no(stage_no).title if stage_no and 1 <= stage_no <= TOTAL_STAGES else None,
            "waiting": waiting is not None,
            "status": "done" if finished is not None else "open", "completedAt": finished,
            "stageMovedAt": moved, "calledAt": cand.call_recorded_at if cand.call_called and cand.call_interested in ("yes", "no", "rejected") else None,
            **placement_fields(order, company, client, designation, cand),
            "candidate": cand.name, "candidateRef": cand.ref, "candidateCareId": cand.care_id, "agency": agency or None,
            "orderRef": order.ref if order else None, **payment_fields(app, facts),
            "referral": {"source": cand.source.value, "label": SOURCE_LABELS.get(cand.source.value, "Other"),
                         "detail": cand.source_detail if cand.source == Source.referral else None},
            "remark": remark, **_note_fields(_row_notes(found_notes, cand.id, app.id if app else None), now=now, tz=tz),
            "outcome": current_outcome(),
        })
    return out


def counts_from_rows(rows) -> dict[str, tuple[int, int]]:
    """`owner_counts` from the fetched rows, exact while the rows are not cut off by MAX_ROWS."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for cand, _waiting, finished, *_rest in rows:
        counts[cand.owner_id][0 if finished is None else 1] += 1
    return {owner: (todo, done) for owner, (todo, done) in counts.items()}


def load_rows(db, owner_ids, *, now: datetime | None = None, tz=None, parallel: bool = False, fetched: list | None = None) -> list[dict]:
    """The screen's rows for these recruiters (ids, or a select of ids), in list order: to work first, then done today.

    `parallel=True` runs the lookups that depend only on the rows (active placements, open tasks, facts, remarks) side by side; use
    it only when the caller has nothing uncommitted (they run on their own connections). `fetched` is the result of
    `fetch_rows` when the caller already ran it."""
    now = now or datetime.now(timezone.utc)
    rows = fetch_rows(db, owner_ids, now=now, tz=tz) if fetched is None else fetched
    if not rows:
        return []
    lookups = placement_reads([row[0].id for row in rows], now=now, tz=tz)
    results = read_together(db, *lookups) if parallel else [look(db) for look in lookups]
    return assemble_rows(rows, *results, now=now, tz=tz)


def wide_reads(owner_ids: list[str], *, now: datetime, tz=None) -> tuple:
    """Everything today's list needs for these recruiters (a short list of ids) as ONE set of independent reads: the rows, then the four
    lookups of `placement_reads` taken for all of the recruiters' workable candidates instead of only the listed ones. Nothing waits for
    the rows, so the whole list is one round trip. Results: (rows, placed, task rows, fact rows, remark rows)."""
    candidates = select(Candidate.id).where(Candidate.owner_id.in_(owner_ids), _workable())
    return (lambda s: fetch_rows(s, owner_ids, now=now, tz=tz), *placement_reads(candidates, now=now, tz=tz))


# ------------------------------------------------------------------------------------------------ outcomes (the other views)

# The Tasks page can also show the candidates whose placement ended: `view=declined|rejected|travelled|all` on `/api/daily-work`.
# A candidate's outcome comes from ONE placement, the "subject": their latest deployed placement when they have one (Travelled, the
# same definition as the Travelled report: the application is deployed), else their last placement when it is closed and nothing
# is active. `unmapped` and `changed_job` closures are no outcome (the candidate was only moved off a job). Declined is the
# candidate's own no (`DECLINE_CLOSURES`); Rejected is every other negative closure. Both are exactly the candidates `_parked` keeps off
# today's list (a "yes" call recorded after the closure, or a new mapping, brings them back to today's list and off these views), so a
# candidate is never on today's list and on Declined / Rejected at the same time.
VIEWS = ("current", "declined", "rejected", "travelled", "all")
MAX_OUTCOME_ROWS = 500   # the newest closures of one non-current list; the counts are exact whatever the cap
OUTCOME_LABELS = {"declined": "Declined", "rejected": "Rejected", "travelled": "Travelled"}


def current_outcome() -> dict:
    """The `outcome` of a row on today's list or on Follow-up (a fresh dict each time: rows are mutated by callers)."""
    return {"kind": "current", "label": None, "reason": None, "stage": None, "at": None, "by": None}


# One plain sentence per closure reason, so nobody has to guess why a candidate shows Declined / Rejected. `{stage}` is the stage it ended at.
REASON_TEXT = {
    "stage_declined": "Declined by the candidate", "offer_declined": "Offer declined by the candidate",
    "not_interested": "Candidate not interested", "not_travelling": "Not travelling",
    "stage_rejected": "Rejected at this stage", "prescreen_failed": "Failed prescreening",
    "resume_not_received": "Resume not received", "interview_rejected": "Rejected at the interview",
    "interview_not_possible": "Interview not possible", "medical_unfit": "Medical unfit", "pcc_not_cleared": "PCC not cleared",
    "passport_not_available": "Passport not available", "advance_not_paid": "Advance not paid", "visa_rejected": "Visa rejected",
    "visa_stamping_refused": "Visa stamping refused", "offer_not_received": "Offer letter not received",
}


def reason_text(reason, stage) -> str | None:
    if reason is None:
        return None
    key = getattr(reason, "value", reason)
    return REASON_TEXT.get(key, key.replace("_", " ").capitalize()).format(stage=stage if stage else "?")


def outcome_of(app, by: str | None = None) -> dict:
    """The `outcome` of a closed or deployed placement: kind, label, a plain sentence for the reason (None for Travelled), the stage it
    ended at, when it closed and `by`, the name of whoever recorded it (the last audit entry of the placement; None when unknown)."""
    if app.status == ApplicationStatus.deployed:
        kind, reason = "travelled", None
    else:
        kind = "declined" if app.closure_reason in DECLINE_CLOSURES else "rejected"
        reason = reason_text(app.closure_reason, app.current_stage) or "Application closed"   # a legacy row closed with no reason
    # The time it closed is `closed_at` (the event; never `updated_at`, which a bulk edit or an import touches); a row closed before that column existed falls back on it.
    return {"kind": kind, "label": OUTCOME_LABELS[kind], "reason": reason, "stage": app.current_stage, "at": app.closed_at or app.updated_at, "by": by}


def outcome_of_call(cand) -> dict:
    """The `outcome` of a candidate who is "not interested" with no placement of their own (never placed, or only moved off a job).
    Rejected at the initial call -> Rejected; their own no at the call -> Declined; "not interested" by an edit or an import with no call -> Declined.
    No stage; closed when the call was recorded (else when the candidate was last changed)."""
    at = cand.call_recorded_at or cand.updated_at
    if cand.call_interested == "rejected":
        return {"kind": "rejected", "label": "Rejected", "reason": "Rejected at the initial call", "stage": None, "at": at, "by": None}
    reason = "Candidate not interested at the initial call" if cand.call_interested == "no" else "Marked as not interested"
    return {"kind": "declined", "label": "Declined", "reason": reason, "stage": None, "at": at, "by": None}


def _subject_placement():
    """Id of the placement an outcome is read from: the candidate's latest deployed one, else their last one."""
    deployed = aliased(Application)
    latest_deployed = (select(deployed.id).where(deployed.candidate_id == Candidate.id, deployed.status == ApplicationStatus.deployed)
                       .order_by(deployed.created_at.desc(), deployed.id.desc()).limit(1).correlate(Candidate).scalar_subquery())
    return func.coalesce(latest_deployed, _last_application())


def _outcome_conditions() -> dict:
    """SQL conditions per outcome kind, over `Candidate` joined to `Application` = the subject placement."""
    active = aliased(Application)
    nothing_active = ~exists().where(active.candidate_id == Candidate.id, active.status == ApplicationStatus.active).correlate(Candidate)
    called_yes_since = _called_yes_since()
    # The candidate is "not interested" (their own no at the call, the recruiter's rejection at the call, or marked so by an edit / an import) and
    # has no placement that ended in an outcome of its own (no placement at all, or only one that was unmapped / swapped): `Application` is then
    # NULL (the subject join is an outer join) or a NOT_OUTCOMES closure. The status is what parks them (not interested is not WORKABLE; a later
    # "yes" call puts them back on today's list), so they are on exactly one of Declined / Rejected, never also on today's list.
    off_order = and_(Candidate.status == CandidateStatus.not_interested, _no_outcome(), nothing_active)
    rejected_at_call = and_(off_order, Candidate.call_interested == "rejected")
    said_no_off_order = and_(off_order, Candidate.call_interested.is_distinct_from("rejected"))
    return {
        "declined": or_(and_(Application.status == ApplicationStatus.closed, Application.closure_reason.in_(DECLINE_CLOSURES),
                             ~func.coalesce(called_yes_since, false()), nothing_active), said_no_off_order),
        "rejected": or_(and_(_negative_end(), Application.closure_reason.notin_(DECLINE_CLOSURES),
                             ~func.coalesce(called_yes_since, false()), nothing_active),
                        rejected_at_call),
        "travelled": Application.status == ApplicationStatus.deployed,
    }


def outcome_kinds(view: str) -> tuple[str, ...]:
    """The outcome kinds a view lists besides today's rows."""
    return ("declined", "rejected", "travelled") if view == "all" else (view,) if view in OUTCOME_LABELS else ()


def view_counts(db, owner_ids) -> dict[str, int]:
    """{declined, rejected, travelled}: candidates of these recruiters (ids, or a select of ids) per outcome. One grouped statement."""
    found = _outcome_conditions()
    declined, rejected, travelled = db.execute(
        select(*(func.count().filter(found[kind]) for kind in ("declined", "rejected", "travelled")))
        .select_from(Candidate).outerjoin(Application, Application.id == _subject_placement()).where(Candidate.owner_id.in_(owner_ids))).one()
    return {"declined": int(declined), "rejected": int(rejected), "travelled": int(travelled)}


def fetch_outcome_rows(db, owner_ids, kinds, *, limit: int | None = None) -> list:
    """(candidate, placement, order, company, client, designation, agency) of the candidates with one of these outcomes, newest closure first.
    One statement. A candidate who said no at the initial call and was never placed (Declined) has `None` for the placement, order, company,
    client and designation: the row then falls back on the candidate's own form, like a row of today's list."""
    found = _outcome_conditions()
    return db.execute(
        select(Candidate, Application, Order, Company, Client, Designation, agency_display_expression().label("agency"))
        .select_from(Candidate).outerjoin(Application, Application.id == _subject_placement())
        .outerjoin(Order, Order.id == Application.order_id).outerjoin(Company, Company.id == Order.company_id)
        .outerjoin(Client, Client.id == Company.client_id).outerjoin(Designation, Designation.id == Order.designation_id)
        .where(Candidate.owner_id.in_(owner_ids), or_(*(found[kind] for kind in kinds)))
        .order_by(case((_no_outcome(), func.coalesce(Candidate.call_recorded_at, Candidate.updated_at)),
                       else_=func.coalesce(Application.closed_at, Application.updated_at, Candidate.call_recorded_at, Candidate.updated_at)).desc().nulls_last(), Application.id, Candidate.id).limit(limit or MAX_OUTCOME_ROWS)).all()


def outcome_reads(app_ids, candidate_ids) -> tuple:   # `app_ids` holds only real placements (a candidate who was never placed has none)
    """The lookups behind outcome rows, for these placements and their candidates: the remarks written on the placements' tasks or on the
    candidate's initial-call tasks (no placement) plus every note of the candidates (`_remark_query` rows, like the current rows), the
    advance / review facts and who made the last change to each placement (the closure), all in the same round."""
    remark_query = _remark_query(Task.candidate_id.in_(candidate_ids),
                                 or_(Remark.kind == NOTE, Task.application_id.in_(app_ids), Task.application_id.is_(None)))
    closed_by = (select(AuditEntry.entity_id, User.name).select_from(AuditEntry).outerjoin(User, User.id == AuditEntry.actor_id)
                 .where(AuditEntry.entity_type == "application", AuditEntry.entity_id.in_(app_ids))
                 .distinct(AuditEntry.entity_id).order_by(AuditEntry.entity_id, AuditEntry.at.desc()))
    return (lambda s: s.execute(remark_query).all(), lambda s: s.execute(stage_facts_query(ApplicationStage.application_id.in_(app_ids))).all(),
            lambda s: dict(s.execute(closed_by).all()))


def _outcome_remarks(remark_rows) -> tuple[dict[str, dict], dict[str, dict]]:
    """({placement id: its newest remark}, {candidate id: newest remark on a task of theirs with no placement, i.e. the initial call}) from
    `_remark_query` rows (newest first). Only remarks the cell still shows count (not cleared, not notes)."""
    by_application: dict[str, dict] = {}
    by_candidate: dict[str, dict] = {}
    for body, name, at, _task_id, _id, candidate_id, application_id, cleared_at, _author_id, kind in remark_rows:
        if cleared_at is not None or kind != REMARK:
            continue
        found, key = (by_candidate, candidate_id) if application_id is None else (by_application, application_id)
        entry = found.setdefault(key, {"body": body, "at": at, "by": name or "Unknown", "count": 0})
        entry["count"] += 1
    return by_application, by_candidate


def assemble_outcome_rows(rows, remark_rows, fact_rows, closed_by: dict | None = None, *, now: datetime | None = None, tz=None) -> list[dict]:
    """The screen's rows for candidates whose placement ended, from `fetch_outcome_rows` rows and `outcome_reads`. Same keys as a row of
    today's list, with the same detail: `remark` is the newest remark of the placement's tasks, else of the candidate's initial call, and
    `notes` / `todayNotes` the candidate's notes of this placement or of no placement. `taskId` is null (nothing to work on), `status` is
    "closed", `stage` is the stage where the placement ended."""
    now = now or datetime.now(timezone.utc)
    by_application, by_candidate = _outcome_remarks(remark_rows)
    found_notes = _candidate_notes(remark_rows)
    facts = stage_facts(fact_rows)
    out = []
    for cand, app, order, company, client, designation, agency in rows:
        # A placement that was only unmapped or swapped is not an outcome: the row is listed because the candidate said no at the call,
        # so it reads as that (Declined, no stage, no order) and not as a "Rejected / Unmapped" placement.
        if app is not None and _is_no_outcome(app):
            app = order = company = client = designation = None
        stage_no = app.current_stage if app is not None else None
        out.append({
            "id": cand.id, "kind": "stage", "taskId": None, "applicationId": app.id if app is not None else None, "candidateId": cand.id, "ownerId": cand.owner_id,
            "commencementDate": app.commencement_date.isoformat() if app is not None and app.commencement_date else None,
            "stage": stage_no, "stageTitle": stage_by_no(stage_no).title if stage_no and 1 <= stage_no <= TOTAL_STAGES else None,
            "waiting": False, "status": "closed", "completedAt": None,
            **placement_fields(order, company, client, designation, cand),
            "candidate": cand.name, "candidateRef": cand.ref, "candidateCareId": cand.care_id, "agency": agency or None,
            "orderRef": order.ref if order is not None else None, **payment_fields(app, facts),
            "referral": {"source": cand.source.value, "label": SOURCE_LABELS.get(cand.source.value, "Other"),
                         "detail": cand.source_detail if cand.source == Source.referral else None},
            "remark": (by_application.get(app.id) if app is not None else None) or by_candidate.get(cand.id),
            **_note_fields(_row_notes(found_notes, cand.id, app.id if app is not None else None), now=now, tz=tz),
            "outcome": outcome_of(app, (closed_by or {}).get(app.id)) if app is not None else outcome_of_call(cand),
        })
    return out


# ------------------------------------------------------------------------------------------------ follow-up

def _follow_up_filter(owner_ids):
    """Active placements at stage 2-13 of candidates these recruiters own (still open: not deployed). A stale "not interested" status next to an
    active placement does not hide them: whoever has a live placement is being worked, so nobody falls off every list."""
    return [Candidate.owner_id.in_(owner_ids), Application.status == ApplicationStatus.active,
            Application.current_stage.between(*FOLLOW_UP_STAGES),
            Candidate.status != CandidateStatus.deployed]


def follow_up_counts(db, owner_ids) -> dict[str, int]:
    """{owner id: candidates in follow-up} for these recruiters (ids, or a select of ids)."""
    rows = db.execute(select(Candidate.owner_id, func.count(func.distinct(Candidate.id)))
                      .select_from(Application).join(Candidate, Candidate.id == Application.candidate_id)
                      .where(*_follow_up_filter(owner_ids)).group_by(Candidate.owner_id)).all()
    return {owner: int(n) for owner, n in rows}


def fetch_follow_up(db, owner_ids) -> list:
    """(application, candidate, order, company, client, designation, last update, last stage finished, agency) for these recruiters, stalest first. One statement."""
    stage_at = (select(func.max(ApplicationStage.completed_at)).where(ApplicationStage.application_id == Application.id)
                .correlate(Application).scalar_subquery())
    remark_at = (select(func.max(Remark.created_at)).select_from(Remark).join(Task, Task.id == Remark.task_id)
                 .where(Task.application_id == Application.id, Remark.deleted_at.is_(None)).correlate(Application).scalar_subquery())
    last = func.greatest(stage_at, remark_at, Application.draft_saved_at, Application.created_at).label("last_update")
    # When a stage was last finished (never a reopened one): the Tasks screen shows "Stage moved" for a placement moved today.
    moved_at = (select(func.max(ApplicationStage.completed_at)).where(ApplicationStage.application_id == Application.id,
                                                                      ApplicationStage.reopened_reason.is_(None),
                                                                      ApplicationStage.outcome.is_distinct_from(UNFIT_REAPPLYING))
                .correlate(Application).scalar_subquery().label("stage_moved_at"))
    return db.execute(
        select(Application, Candidate, Order, Company, Client, Designation, last, moved_at, agency_display_expression(require_assignment=True).label("agency"))
        .join(Candidate, Candidate.id == Application.candidate_id).join(Order, Order.id == Application.order_id)
        .join(Company, Company.id == Order.company_id).join(Client, Client.id == Company.client_id)
        .join(Designation, Designation.id == Order.designation_id)
        .where(*_follow_up_filter(owner_ids)).order_by(last.asc(), Application.id).limit(MAX_ROWS)).all()


def follow_up_reads(app_ids, candidate_ids, *, now: datetime, tz=None) -> tuple:
    """The three lookups behind the rows of Follow-up, for these placements and their candidates (ids, or selects of ids): the remarks
    written on any of the placement's tasks (the newest shows, and every note written on this agency day, whatever the candidate's task),
    the open stage task a new remark goes on, and the advance / review facts."""
    remark_query = _remark_query(or_(Task.application_id.in_(app_ids), and_(Task.candidate_id.in_(candidate_ids), Remark.kind == NOTE)))
    task_query = (select(Task.application_id, Task.id).where(
        Task.application_id.in_(app_ids), Task.kind.in_([TaskKind.stage, TaskKind.remedical]),
        Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.created_at, Task.id))
    return (lambda s: s.execute(remark_query).all(), lambda s: s.execute(task_query).all(),
            lambda s: s.execute(stage_facts_query(ApplicationStage.application_id.in_(app_ids))).all())


def assemble_follow_up(rows, remark_rows, task_rows, fact_rows, *, now: datetime | None = None, tz=None) -> list[dict]:
    """The Follow-up screen's rows from `fetch_follow_up` rows and the results of `follow_up_reads`."""
    now = now or datetime.now(timezone.utc)
    latest = _remark_summary(remark_rows, by_application=True)
    found_notes = _candidate_notes(remark_rows)
    open_task: dict[str, str] = {}
    for application_id, task_id in task_rows:
        open_task.setdefault(application_id, task_id)
    facts = stage_facts(fact_rows)
    out = []
    for app, cand, order, company, client, designation, last_update, moved_at, agency in rows:
        out.append({
            "id": app.id, "applicationId": app.id, "taskId": open_task.get(app.id), "candidateId": cand.id, "ownerId": cand.owner_id,
            "commencementDate": app.commencement_date.isoformat() if app.commencement_date else None,
            "stage": app.current_stage, "lastUpdateAt": last_update, "stageMovedAt": moved_at,
            "calledAt": cand.call_recorded_at if cand.call_called and cand.call_interested in ("yes", "no", "rejected") else None,
            **placement_fields(order, company, client, designation, cand),
            "candidate": cand.name, "candidateRef": cand.ref, "candidateCareId": cand.care_id, "agency": agency or None, "orderRef": order.ref,
            **payment_fields(app, facts), "remark": latest.get(app.id), **_note_fields(_row_notes(found_notes, cand.id, app.id), now=now, tz=tz),
            "outcome": current_outcome(),
        })
    return out


def follow_up_counts_from_rows(rows) -> dict[str, int]:
    """`follow_up_counts` from the fetched rows (candidates counted once however many placements), exact while MAX_ROWS did not cut them off."""
    seen: dict[str, set] = defaultdict(set)
    for _app, cand, *_rest in rows:
        seen[cand.owner_id].add(cand.id)
    return {owner: len(ids) for owner, ids in seen.items()}


def load_follow_up(db, owner_ids, *, now: datetime | None = None, tz=None, fetched: list | None = None, parallel: bool = False) -> list[dict]:
    """The Follow-up screen's rows, oldest last update first. `fetched` is the result of `fetch_follow_up` when already read.
    `parallel=True` runs the three lookups side by side (see `load_rows`)."""
    rows = fetch_follow_up(db, owner_ids) if fetched is None else fetched
    if not rows:
        return []
    now = now or datetime.now(timezone.utc)
    lookups = follow_up_reads([app.id for app, *_ in rows], sorted({cand.id for _, cand, *_ in rows}), now=now, tz=tz)
    results = read_together(db, *lookups) if parallel else [look(db) for look in lookups]
    return assemble_follow_up(rows, *results, now=now, tz=tz)


def follow_up_wide_reads(owner_ids: list[str], *, now: datetime, tz=None) -> tuple:
    """Everything Follow-up needs for these recruiters (a short list of ids) as one set of independent reads: the rows, then the three
    lookups of `follow_up_reads` taken for every placement that qualifies (not only the listed ones). One round trip in all.
    Results: (rows, remark rows, task rows, fact rows)."""
    placed = select(Application.id, Application.candidate_id).join(Candidate, Candidate.id == Application.candidate_id).where(*_follow_up_filter(owner_ids)).subquery("placed")
    return (lambda s: fetch_follow_up(s, owner_ids), *follow_up_reads(select(placed.c.id), select(placed.c.candidate_id), now=now, tz=tz))


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
