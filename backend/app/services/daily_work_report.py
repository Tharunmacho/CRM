"""Daily CRM Work Report: what each recruiter did in a day / week / month / custom range, computed from the CRM itself.

Nothing is typed in. Every figure is a count of rows the staff already wrote while working (stage completions on candidates,
calls and status changes on leads); the only stored thing is the day-close submission, an AuditEntry (no new table).

Metric definitions (the `definition` text is shown to users; keep the table and `METRICS` in step)
----------------------------------------------------------------------------------------------------
Stage figures read `crm_application_stages` (one row per completed stage attempt). A row counts on the agency-time day it was
COMPLETED (`completed_at`), for the person who completed it (`completed_by`, else the recorded owner). A row that was reopened
(`reopened_reason` set) is not counted; completing the stage again makes a new row, so it counts once, on the latest completion.
The stage's `outcome` text (written by app.domain.stages) says what happened; only the outcomes below count.

  key             stage and outcome counted                               evidence in app/domain/stages.py
  --------------  ------------------------------------------------------  --------------------------------------------------
  leadsContacted  distinct leads with >= 1 logged call or status change   lead_sheet.log_call writes kind 'call' (meta.outcome),
                  by the person in the range (kind 'call' or 'status')    patch_lead writes kind 'status' (meta.to)
  prescreened     stage 1 completed: 'Passed', 'Failed' (or any stop)     _stage1: a failed prescreening is still a resume that
                                                                          was prescreened; every completion is screening work
  forwarded       stage 2 'Forwarded' or 'Completed manually'             _stage2: 'Not forwarded' closes the placement and is
                                                                          not counted
  shortlisted     stage 3 'Scheduled' or 'Completed manually'             The CRM has no "client response" field. The client
                                                                          shortlists a worker by scheduling an interview for
                                                                          them (_stage3, InterviewSchedule), so a completed
                                                                          interview-scheduling stage is the shortlisting.
                                                                          'Interview not possible' is not counted.
  interviews      stage 4 'Selected' or 'Rejected' (result recorded)      _stage4 / DECISION_STAGES: an interview result can
                                                                          only be recorded after the interview took place.
                                                                          A candidate who 'Declined' before it is not counted.
  interviewsOnline  subset of interviews whose stage 3 mode is 'online'   InterviewSchedule.mode in ('in_person', 'online')
  offers          stage 5 'Agreed' or 'Declined'                          _stage5: both mean an offer letter was received;
                                                                          'Not received' and a client 'Rejected' are not counted
  visas           stage 10 'Arrived' or 'Completed manually'              _stage10 / VisaArrival.arrived; 'Visa rejected' is not
                                                                          counted
  travelled       stage 13 'Booked' (the placement becomes Deployed)      complete_stage deploys the placement and gives the
                                                                          Traveller ID only for a real stage 13 completion;
                                                                          'Not travelling' is a closure, not counted

Travelled counts the day stage 13 was completed in the CRM, not the ticket's `travelDate`: `travelDate` is entered when the
ticket is booked (it can lie in the future), the Travelled screen already treats completion as "deployed", and a folder import
stamps `completed_at` with the stage's own date (bulk_candidate_import.stage_dates), so history stays on its real day.

Lead feedback: for every lead a person contacted in the range, the outcome of their LAST call or status change in the range
(meta.outcome of a call: answered / interested / not_interested / no_answer / wrong_number; meta.to of a status change), grouped:
  interested      interested, resume_requested, resume_received, converted
  notInterested   not_interested
  wrongNumber     wrong_number
  notReachable    invalid (shown as "Not reachable" on the Lead sheet)
  callback        any other outcome while the lead still has a follow-up date set and is open ("call back")
  noAnswer        no_answer without a follow-up date; also a status template with the contact-attempt tone (Ringing, No incoming calls:
                  the status activity carries meta.attempt = true), which counts as contacted but is not reached and not interested
  answered        answered / contacted without a decision and without a follow-up date (a neutral status template counts here)
  notContacted    leads the person holds (assigned and still open, or contacted in the range) that they did NOT contact in the range
Per staff, `other` = answered + notReachable, so interested + notInterested + noAnswer + wrongNumber + callback + other =
contacted. The team's leadsContacted is the sum of each person's distinct leads (a lead two people called counts for both).
The client filter cannot apply to leads (a lead has no client), so lead figures ignore it.

Management table additions
  leads (per staff)  assigned = leads currently owned by the person; contacted / interested / ... as in the feedback groups;
                     notContacted = their owned leads with no call or status change by them in the period.
  leadSheet          every lead on the sheet by its CURRENT status (the real enum, the Lead sheet's labels); assigned = owned,
                     unassigned = nobody's (team view only); contacted = the team's contacted; notContacted = sum of the persons'.
  tasks              workflow_tasks (initial calls, stage tasks, re-medicals): done = completed in the period, credited to the
                     task's owner (the table has no completer column); pending = open now; overdue = open and past due_at.
                     Personal to-dos (lead follow-up reminders) are private and carry no completion time, so they are left out.
  stages             candidates CURRENTLY at each stage: the current stage of every ACTIVE application, grouped by the candidate's
                     recruiter (Daily work's owner, Candidate.owner_id, else the application's recorded recruiter); stage 0 = workable
                     candidates (Daily work's WORKABLE statuses) with no active application and not parked after their own decline.
  partial periods    a range that is still running compares with the SAME elapsed time of the previous period (prev_until).

Round trips matter (a hosted database costs ~300 ms per statement): one grouped statement per family (stage figures, lead figures,
users, clients with activity, the day's submissions), run side by side, never one per person or per day.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from zoneinfo import ZoneInfo

from sqlalchemy import Date, String, and_, case, cast, exists, func, literal, or_, select, text, union_all, update
from sqlalchemy.orm import aliased

from app.core.roles import ROLE_LABELS
from app.db import models as m
from app.db.models import LEAD_STATUSES
from app.domain.stages import MEDICAL_STAGE, STAGES, STOP_OUTCOMES, TICKET_STAGE, TOTAL_STAGES, VISA_STAGE
from app.services import change_feed
from app.services.daily_work import WORKABLE, _parked, agency_today, day_bounds
from app.services.parallel_reads import parallel_reads

MAX_DAYS = 366
WEEKLY_AFTER_DAYS = 92          # a longer range is drawn one point per week
ALL_TIME_FLOOR = date(2000, 1, 1)
ALL_TIME_MONTHLY_AFTER_DAYS = 730   # "Total" spanning more than about two years is drawn one point per month
SUBMIT_ACTION = "Daily report submitted"
NOTE_MAX = 500
ROLE_NAMES = ROLE_LABELS   # what the export shows in its Role column: Admin, MD, Recruiter
BY_CLIENT_MAX = 50              # clients listed in `byClient`; the rest are rolled up as "Other clients"
HISTORY_EXPORT_MAX = 5000
# The figures a day-close snapshot holds (and the history shows).
SNAPSHOT_KEYS = ("forwarded", "prescreened", "shortlisted", "interviews", "interviewsOnline", "offers", "visas", "travelled",
                 "leadsContacted")
CLOSED = "('converted','not_interested','invalid','wrong_number')"   # same set as lead_metrics.CLOSED

# Stage figures: key -> (stage number, outcomes that count; None = every completion).
STAGE_RULES: dict[str, tuple[int, tuple[str, ...] | None]] = {
    "prescreened": (1, None),
    "forwarded": (2, ("Forwarded", "Completed manually")),
    "shortlisted": (3, ("Scheduled", "Completed manually")),
    "interviews": (4, ("Selected", "Rejected")),
    "offers": (5, ("Agreed", "Declined")),
    "visas": (VISA_STAGE, ("Arrived", "Completed manually")),
    "travelled": (TICKET_STAGE, ("Booked",)),
}

# The outcomes that end a placement at each stage: the stopping exits plus the older negative answers (as application_form.FAILED_OUTCOMES).
STOPPED_OUTCOMES: dict[int, set[str]] = {
    no: set(outcomes) | {1: {"Failed"}, 2: {"Not forwarded"}, 4: {"Rejected"}, 5: {"Declined", "Not received"}, MEDICAL_STAGE: {"Unfit"}}.get(no, set())
    for no, outcomes in STOP_OUTCOMES.items()
}

# Business order of the screen (leads first, as the client asked); the funnel runs in STAGE order instead.
METRICS: tuple[tuple[str, str, str], ...] = (
    ("leadsContacted", "Leads contacted",
     "Distinct leads from the lead sheet that the person called or whose status they changed in the period. "
     "A lead two people worked counts once for each."),
    ("prescreened", "Resumes prescreened",
     "Candidates whose Prescreening stage was completed in the period, passed or failed, counted on the day it was "
     "completed and for the person who completed it."),
    ("forwarded", "Resumes forwarded to clients",
     "Candidates whose Forwarded to client stage was completed as forwarded in the period. 'Not forwarded' is not counted."),
    ("shortlisted", "Workers shortlisted by clients",
     "Candidates for whom an interview was scheduled by the client in the period (Interview scheduling stage completed). "
     "'Interview not possible' is not counted."),
    ("interviews", "Interviews conducted",
     "Interviews conducted: Interview result recorded as Selected or Rejected in the period. How many of them were held "
     "online (the mode chosen when the interview was scheduled) is shown beside it."),
    ("offers", "Offer letters received",
     "Offer letter stage recorded as Agreed or Declined in the period (a letter was received). 'Not received' is not counted."),
    ("visas", "Visas received",
     "Visa arrival stage completed as arrived in the period. 'Visa rejected' is not counted."),
    ("travelled", "Workers travelled",
     "Air ticket stage completed in the period, which deploys the candidate and gives the Traveller ID. "
     "'Not travelling' is not counted."),
)
METRIC_KEYS = tuple(key for key, _label, _definition in METRICS)
LABELS = {key: label for key, label, _definition in METRICS}
# The seven columns of the By client table, in the screen's order (frontend DAILY_ORDER). `interviewsOnline` is the online share of
# the interviews, counted beside the metrics (it is not a METRICS key). A client's Total is the sum of exactly these columns.
CLIENT_COLUMNS = ("forwarded", "prescreened", "shortlisted", "interviewsOnline", "offers", "visas", "travelled")
CLIENT_COLUMN_LABELS = {"interviewsOnline": "Online interviews"}


def client_total(metrics: dict, interviews_online: int) -> int:
    """The Total of a By client row: the sum of the figures the table shows (not every metric: interviews conducted is not a column)."""
    return sum(int(interviews_online if key == "interviewsOnline" else metrics.get(key, 0)) for key in CLIENT_COLUMNS)


FUNNEL_ORDER = ("prescreened", "forwarded", "shortlisted", "interviews", "offers", "visas", "travelled")

OUTCOME_LABELS = {
    "interested": "Interested", "callback": "Call back", "answered": "Spoke, no decision yet", "noAnswer": "No answer",
    "notInterested": "Not interested", "wrongNumber": "Wrong number", "notReachable": "Not reachable",
}
NOT_CONTACTED = "Not contacted in this period"
# The real lead status enum (models.LEAD_STATUSES) with the Lead sheet's own labels (lead_sheet.STATUS_LABELS).
LEAD_STATUS_ORDER = LEAD_STATUSES
LEAD_STATUS_LABELS = {
    "new": "Not contacted", "contacted": "Contacted", "interested": "Interested", "resume_requested": "Resume requested",
    "resume_received": "Resume received", "converted": "Converted", "not_interested": "Not interested",
    "invalid": "Not reachable", "wrong_number": "Wrong number",
}
# Pipeline right now: stage 0 is a candidate who is workable but not on an order yet; 1..13 are the stage definitions.
STAGE_NOW_KEYS = {0: ("unmapped", "Not on an order yet"), **{s.no: (s.key, s.title) for s in STAGES}}
_INTERESTED = {"interested", "resume_requested", "resume_received", "converted"}


# ------------------------------------------------------------------ the period

@dataclass(frozen=True)
class Period:
    first: date
    last: date
    prev_first: date
    prev_last: date
    start: datetime       # UTC instant the first day starts (agency time)
    end: datetime         # UTC instant the day after the last day starts
    prev_start: datetime
    # The previous period counts only up to here. A period that is still running ("today so far") is compared with the SAME
    # elapsed time of the previous one (today 00:00-now against yesterday 00:00-the same clock time); a finished period with
    # the whole previous one, so prev_until == start then.
    prev_until: datetime = None
    partial: bool = False
    all_time: bool = False   # "Total": from the earliest record to today, no previous period to compare with

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    @property
    def unit(self) -> str:
        if self.all_time:   # a long history is drawn by month, a short one by week
            return "month" if self.days > ALL_TIME_MONTHLY_AFTER_DAYS else "week"
        return "week" if self.days > WEEKLY_AFTER_DAYS else "day"

    def buckets(self) -> list[date]:
        if self.unit == "day":
            return [self.first + timedelta(days=i) for i in range(self.days)]
        if self.unit == "month":
            out, day = [], self.first.replace(day=1)
            while day <= self.last:
                out.append(day)
                day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
            return out
        out, day = [], self.first - timedelta(days=self.first.weekday())
        while day <= self.last:
            out.append(day)
            day += timedelta(days=7)
        return out


def resolve_period(from_date: date | None, to_date: date | None, now: datetime, zone: str, *, all_time: bool = False,
                   since: date | None = None) -> Period:
    """The chosen range; with `all_time` from/to are ignored: `since` (the earliest record, else the 2000 floor) to today, no
    366-day cap, and no previous period (its window is empty), so the queries simply have no useful lower bound."""
    today = agency_today(now, zone)
    if all_time:
        first = min(max(since or ALL_TIME_FLOOR, ALL_TIME_FLOOR), today)
        start, end = day_bounds(first, zone)[0], day_bounds(today, zone)[1]
        return Period(first, today, None, None, start, end, start, prev_until=start, partial=False, all_time=True)
    first, last = from_date or today, to_date or today
    if first > last:
        raise HTTPException(422, "The start date must be on or before the end date.")
    if first.year < 2000:
        raise HTTPException(422, "Choose a start date from the year 2000 onwards.")
    days = (last - first).days + 1
    if days > MAX_DAYS:
        raise HTTPException(422, f"Choose a period of at most {MAX_DAYS} days.")
    prev_last, prev_first = first - timedelta(days=1), first - timedelta(days=days)
    start, end, prev_start = day_bounds(first, zone)[0], day_bounds(last, zone)[1], day_bounds(prev_first, zone)[0]
    now = now.astimezone(timezone.utc)
    partial = start <= now < end
    return Period(first, last, prev_first, prev_last, start, end, prev_start,
                  prev_until=min(start, prev_start + (now - start)) if partial else start, partial=partial)


def _percent_change(value: int, previous: int):
    return round((value - previous) / previous * 100, 1) if previous else None


# ------------------------------------------------------------------ reads (each one statement)

def _clean(stage):
    return or_(stage.reopened_reason.is_(None), stage.reopened_reason == "")


def _rule(stage, key):
    number, outcomes = STAGE_RULES[key]
    cond = stage.stage == number
    return and_(cond, stage.outcome.in_(outcomes)) if outcomes else cond


def _metric_case(stage):
    return case(*[(_rule(stage, key), key) for key in STAGE_RULES])


def _interview_mode(stage):
    """How the interview was held: the mode recorded on the same placement's (latest, never reopened) scheduling stage."""
    s3 = aliased(m.ApplicationStage)
    return (select(s3.data["mode"].as_string()).where(s3.application_id == stage.application_id, s3.stage == 3, _clean(s3))
            .order_by(s3.attempt.desc()).limit(1).correlate(stage).scalar_subquery())


def _stage_filters(stage, p: Period, start, staff_id, client_id, only: str | None = None):
    """The rows that count (for one metric when `only` is given): completed in [start, end), never reopened, with a person to credit."""
    who = func.coalesce(stage.completed_by, stage.owner_id)
    conditions = [stage.completed_at >= start, stage.completed_at < p.end, _clean(stage), who.is_not(None),
                  stage.stage.in_([number for number, _ in STAGE_RULES.values()]),
                  _rule(stage, only) if only else _metric_case(stage).is_not(None)]
    if start < p.start and p.prev_until < p.start:   # a running period: the previous one only up to the same elapsed time
        conditions.append(or_(stage.completed_at < p.prev_until, stage.completed_at >= p.start))
    if staff_id:
        conditions.append(who == staff_id)
    if client_id:
        conditions.append(stage.application_id.in_(
            select(m.Application.id).join(m.Order, m.Order.id == m.Application.order_id).where(m.Order.company_id == client_id)))
    return who, conditions


def read_stages(session, p: Period, zone: str, staff_id: str | None, client_id: str | None) -> list:
    """(person, metric, in the period?, bucket day or None, count, online interviews) over the period and the one before it."""
    AS = m.ApplicationStage
    who, conditions = _stage_filters(AS, p, p.prev_start, staff_id, client_id)
    in_cur = AS.completed_at >= p.start
    online = case((AS.stage == 4, func.coalesce(_interview_mode(AS) == "online", False)), else_=False)
    bucket = case((in_cur, cast(func.date_trunc(p.unit, func.timezone(zone, AS.completed_at)), Date)))
    A, O = m.Application, m.Order
    inner = (select(who.label("uid"), _metric_case(AS).label("metric"), in_cur.label("cur"), bucket.label("bucket"),
                    online.label("online"), O.company_id.label("company"))
             .select_from(AS).join(A, A.id == AS.application_id).join(O, O.id == A.order_id).where(*conditions).cte("s"))
    # One statement, two groupings of the same rows: per person / metric / day, and per client / metric (the period only).
    per_person = (select(literal("main").label("kind"), inner.c.uid, inner.c.metric, inner.c.cur, inner.c.bucket,
                         cast(None, String).label("company"), cast(None, String).label("cname"),
                         func.count().label("n"), func.count().filter(inner.c.online).label("n_online"))
                  .group_by(inner.c.uid, inner.c.metric, inner.c.cur, inner.c.bucket))
    per_client = (select(literal("client"), cast(None, String), inner.c.metric, literal(True), cast(None, Date), inner.c.company,
                         m.Company.name, func.count(), func.count().filter(inner.c.online))
                  .select_from(inner).join(m.Company, m.Company.id == inner.c.company).where(inner.c.cur.is_(True))
                  .group_by(inner.c.company, m.Company.name, inner.c.metric))
    return session.execute(union_all(per_person, per_client)).all()


def read_stage_flow(session, p: Period, staff_id: str | None, client_id: str | None) -> list:
    """(stage, completed, stopped) for EVERY stage: the stage records completed in the period (never reopened, with a person to credit,
    filtered like the figures), and how many of them ended the placement (the stage's own stopping outcomes). The complete stage-by-stage
    progress of the Daily report: how many were prescreened, forwarded ... up to the air ticket."""
    AS = m.ApplicationStage
    who = func.coalesce(AS.completed_by, AS.owner_id)
    conditions = [AS.completed_at >= p.start, AS.completed_at < p.end, _clean(AS), who.is_not(None), AS.outcome != "Unfit: reapplying"]
    if staff_id:
        conditions.append(who == staff_id)
    if client_id:
        conditions.append(AS.application_id.in_(
            select(m.Application.id).join(m.Order, m.Order.id == m.Application.order_id).where(m.Order.company_id == client_id)))
    stopped = case(*[(and_(AS.stage == no, AS.outcome.in_(sorted(outcomes))), 1) for no, outcomes in STOPPED_OUTCOMES.items() if outcomes], else_=0)
    return session.execute(select(AS.stage, func.count(), func.coalesce(func.sum(stopped), 0)).where(*conditions).group_by(AS.stage)).all()


_ACTIVITY = f"""
  SELECT a.id, a.user_id AS uid, a.lead_id, a.created_at, a.kind, a.meta
  FROM crm_lead_activities a
  WHERE a.kind IN ('call', 'status') AND a.user_id IS NOT NULL
    AND a.created_at >= :prev_start AND a.created_at < :end
    AND (a.created_at < :prev_until OR a.created_at >= :cur_start)
    AND (CAST(:staff AS varchar) IS NULL OR a.user_id = CAST(:staff AS varchar))"""

# When a lead was handed to its recruiter: `assigned_at` is written by every assignment (a manual one, the spread, an import with an
# owner) and cleared when a lead is taken back; an owned lead without it falls back to the day it was stored. Only the LATEST
# assignment is kept (a reassignment overwrites it), so a lead counts for the period of its current owner's assignment.
AT = "COALESCE(l.assigned_at, l.created_at)"

_LEADS_SQL = text(f"""
WITH act AS ({_ACTIVITY})
SELECT 'series' AS part, uid, CAST(date_trunc(CAST(:unit AS text), timezone(CAST(:tz AS text), created_at)) AS date) AS day,
       CAST(NULL AS text) AS key, CAST(NULL AS boolean) AS flag, count(DISTINCT lead_id) AS n
  FROM act WHERE created_at >= :cur_start GROUP BY 2, 3
UNION ALL
SELECT 'total', uid, CAST(NULL AS date), CAST(NULL AS text), created_at >= :cur_start, count(DISTINCT lead_id)
  FROM act GROUP BY 2, 5
UNION ALL
SELECT 'final', f.uid, CAST(NULL AS date), f.raw, f.follow, count(*) FROM (
  SELECT DISTINCT ON (a.uid, a.lead_id) a.uid,
         CASE WHEN a.kind = 'call' THEN a.meta ->> 'outcome' WHEN a.meta ->> 'attempt' = 'true' THEN 'no_answer' ELSE a.meta ->> 'to' END AS raw,
         (l.next_follow_up_at IS NOT NULL AND l.status NOT IN {CLOSED}) AS follow
    FROM act a JOIN crm_leads l ON l.id = a.lead_id
   WHERE a.created_at >= :cur_start
   ORDER BY a.uid, a.lead_id, a.created_at DESC, a.id DESC) f
  GROUP BY 2, 4, 5
UNION ALL
SELECT 'pool', l.owner_id, CAST(NULL AS date), CAST(NULL AS text),
       NOT EXISTS (SELECT 1 FROM act x WHERE x.lead_id = l.id AND x.uid = l.owner_id AND x.created_at >= :cur_start), count(*)
  FROM crm_leads l
 WHERE l.owner_id IS NOT NULL AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
   AND (l.assigned_at IS NULL OR l.assigned_at < :end)
   AND (l.status NOT IN {CLOSED}
        OR EXISTS (SELECT 1 FROM act x WHERE x.lead_id = l.id AND x.uid = l.owner_id AND x.created_at >= :cur_start))
 GROUP BY 2, 5
UNION ALL
SELECT 'callbacks', l.owner_id, CAST(NULL AS date), CAST(NULL AS text), CAST(NULL AS boolean), count(*)
  FROM crm_leads l
 WHERE l.owner_id IS NOT NULL AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
   AND l.next_follow_up_at <= :now AND l.status NOT IN {CLOSED}
 GROUP BY 2
UNION ALL
SELECT 'resumes', a.user_id, CAST(NULL AS date), CAST(NULL AS text), CAST(NULL AS boolean), count(DISTINCT a.lead_id)
  FROM crm_lead_activities a
 WHERE a.kind = 'resume' AND a.user_id IS NOT NULL AND a.created_at >= :cur_start AND a.created_at < :end
   AND (CAST(:staff AS varchar) IS NULL OR a.user_id = CAST(:staff AS varchar))
 GROUP BY 2
UNION ALL
SELECT 'status', CAST(NULL AS varchar), CAST(NULL AS date), l.status, CAST(NULL AS boolean), count(*)
  FROM crm_leads l
 WHERE CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar)
 GROUP BY 4
UNION ALL
SELECT 'owned', l.owner_id, CAST(NULL AS date), CAST(NULL AS text),
       NOT EXISTS (SELECT 1 FROM act x WHERE x.lead_id = l.id AND x.uid = l.owner_id AND x.created_at >= :cur_start), count(*)
  FROM crm_leads l
 WHERE l.owner_id IS NOT NULL AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
 GROUP BY 2, 5
UNION ALL
SELECT 'assigned_p', l.owner_id, CAST(NULL AS date), CAST(NULL AS text), {AT} >= :cur_start, count(*)
  FROM crm_leads l
 WHERE l.owner_id IS NOT NULL AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
   AND (({AT} >= :cur_start AND {AT} < :end) OR ({AT} >= :prev_start AND {AT} < :prev_until))
 GROUP BY 2, 5
UNION ALL
SELECT 'notcontacted_p', l.owner_id, CAST(NULL AS date), CAST(NULL AS text), CAST(NULL AS boolean), count(*)
  FROM crm_leads l
 WHERE l.owner_id IS NOT NULL AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
   AND {AT} >= :cur_start AND {AT} < :end
   AND NOT EXISTS (SELECT 1 FROM crm_lead_activities x WHERE x.lead_id = l.id AND x.user_id = l.owner_id
                      AND x.kind IN ('call', 'status') AND x.created_at >= {AT})
 GROUP BY 2
UNION ALL
SELECT 'backlog', l.owner_id, CAST(NULL AS date), CAST(NULL AS text), CAST(NULL AS boolean), count(*)
  FROM crm_leads l
 WHERE l.owner_id IS NOT NULL AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
   AND l.status = 'new' AND l.call_count = 0
 GROUP BY 2
UNION ALL
SELECT 'unowned', CAST(NULL AS varchar), CAST(NULL AS date), 'total', CAST(NULL AS boolean), count(*)
  FROM crm_leads l WHERE l.owner_id IS NULL AND CAST(:staff AS varchar) IS NULL
UNION ALL
SELECT 'unowned', CAST(NULL AS varchar), CAST(NULL AS date), 'never', CAST(NULL AS boolean), count(*)
  FROM crm_leads l WHERE l.owner_id IS NULL AND l.status = 'new' AND l.call_count = 0 AND CAST(:staff AS varchar) IS NULL
UNION ALL
SELECT 'unowned', CAST(NULL AS varchar), CAST(NULL AS date), 'new', CAST(NULL AS boolean), count(*)
  FROM crm_leads l WHERE l.owner_id IS NULL AND l.created_at >= :cur_start AND l.created_at < :end AND CAST(:staff AS varchar) IS NULL
UNION ALL
SELECT 'new_p', CAST(NULL AS varchar), CAST(NULL AS date), CAST(NULL AS text), l.created_at >= :cur_start, count(*)
  FROM crm_leads l
 WHERE (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar))
   AND ((l.created_at >= :cur_start AND l.created_at < :end) OR (l.created_at >= :prev_start AND l.created_at < :prev_until))
 GROUP BY 5
""")


def read_leads(session, p: Period, zone: str, staff_id: str | None, now: datetime) -> list:
    return session.execute(_LEADS_SQL, {"staff": staff_id, "unit": p.unit, "tz": zone, "prev_start": p.prev_start, "cur_start": p.start,
                                        "prev_until": p.prev_until, "end": p.end, "now": now}).all()


def read_tasks(session, p: Period, staff_id: str | None, now: datetime) -> list:
    """(owner, done in the period, open now, open and past due) from workflow_tasks: initial calls, stage tasks and re-medicals.
    Done is credited to the task's owner (a task has no separate completer column); personal to-dos have no completion time."""
    T = m.Task
    is_open = and_(T.completed_at.is_(None), T.cancelled_at.is_(None))
    query = (select(T.owner_id, func.count().filter(and_(T.completed_at >= p.start, T.completed_at < p.end)),
                    func.count().filter(is_open), func.count().filter(and_(is_open, T.due_at < now)))
             .where(or_(and_(T.completed_at >= p.start, T.completed_at < p.end), is_open)).group_by(T.owner_id))
    if staff_id:
        query = query.where(T.owner_id == staff_id)
    return session.execute(query).all()


def stage_now_union(staff_id: str | None):
    """(owner, stage, candidates) for every candidate CURRENTLY at a stage, as one UNION query. Stage 1-13: the current stage of each
    ACTIVE application. Stage 0 ('Not on an order yet'): workable candidates (new / in process / needs review, the Daily work list's
    WORKABLE) with no active application that are not parked after their own decline (daily_work._parked). The owner is the
    candidate's recruiter (Daily work's definition: Candidate.owner_id), else the application's own recorded recruiter."""
    A, C = m.Application, m.Candidate
    owner = func.coalesce(C.owner_id, A.owner_id)
    placed = (select(owner.label("owner"), A.current_stage.label("stage"), func.count().label("n")).select_from(A)
              .join(C, C.id == A.candidate_id).where(A.status == m.ApplicationStatus.active).group_by(owner, A.current_stage))
    unmapped = (select(C.owner_id.label("owner"), literal(0).label("stage"), func.count().label("n"))
                .where(C.owner_id.is_not(None), C.status.in_(WORKABLE),
                       ~exists().where(A.candidate_id == C.id, A.status == m.ApplicationStatus.active).correlate(C), ~_parked())
                .group_by(C.owner_id))
    if staff_id:
        placed, unmapped = placed.where(owner == staff_id), unmapped.where(C.owner_id == staff_id)
    return union_all(placed, unmapped)


def read_stage_now(session, staff_id: str | None) -> list:
    return session.execute(stage_now_union(staff_id)).all()


def read_users(session) -> list:
    return session.execute(select(m.User.id, m.User.name, m.User.role, m.User.active)
                           .order_by(func.lower(m.User.name), m.User.id)).all()


def read_clients(session, p: Period, staff_id: str | None) -> list:
    """Clients (employer companies) with any counted stage activity in the period: the choices of the client filter."""
    AS = m.ApplicationStage
    _who, conditions = _stage_filters(AS, p, p.start, staff_id, None)
    return session.execute(
        select(m.Company.id, m.Company.name).select_from(AS).join(m.Application, m.Application.id == AS.application_id)
        .join(m.Order, m.Order.id == m.Application.order_id).join(m.Company, m.Company.id == m.Order.company_id)
        .where(*conditions).group_by(m.Company.id, m.Company.name).order_by(func.lower(m.Company.name), m.Company.id)).all()


def read_submissions(session, day: date, zone: str, staff_id: str | None) -> list:
    """The day-close entries for one agency day. They are written that day or the next, so the time window keeps the scan small."""
    AE = m.AuditEntry
    start, end = day_bounds(day, zone)[0], day_bounds(day + timedelta(days=1), zone)[1]
    query = select(AE.id, AE.entity_id, AE.at, AE.meta).where(
        AE.action == SUBMIT_ACTION, AE.entity_type == "user", AE.at >= start, AE.at < end, AE.meta["date"].as_string() == day.isoformat())
    if staff_id:
        query = query.where(AE.entity_id == staff_id)
    return session.execute(query.order_by(AE.at, AE.id)).all()


# ------------------------------------------------------------------ the report

def classify(raw: str | None, follow_up: bool) -> str:
    """Which feedback group a lead's last call outcome / new status belongs to (see the module docstring)."""
    if raw in _INTERESTED:
        return "interested"
    if raw == "not_interested":
        return "notInterested"
    if raw == "wrong_number":
        return "wrongNumber"
    if raw == "invalid":
        return "notReachable"
    if follow_up:
        return "callback"
    return "noAnswer" if raw == "no_answer" else "answered"


def _initials(name: str) -> str:
    return "".join(word[0] for word in (name or "").split()[:2]).upper() or "?"


def _is_staff(user) -> bool:
    return getattr(user.role, "value", user.role) == m.Role.staff.value


def build_report(db, viewer, from_date: date | None, to_date: date | None, staff: str | None, client: str | None, zone: str,
                 now: datetime, *, force_self: bool = False, all_time: bool = False) -> dict:
    """The whole answer of GET /api/reports/daily-work. A recruiter (or `force_self`) gets only their own numbers.
    `all_time` is the "Total" view: from the earliest record to today, nothing to compare with."""
    return _build(db, viewer, from_date, to_date, staff, client, zone, now, force_self, all_time)[0]


_EARLIEST_SQL = text("""
SELECT least(
  (SELECT min(s.completed_at) FROM crm_application_stages s
    WHERE CAST(:staff AS varchar) IS NULL OR coalesce(s.completed_by, s.owner_id) = CAST(:staff AS varchar)),
  (SELECT min(a.created_at) FROM crm_lead_activities a WHERE a.kind IN ('call', 'status')
    AND (CAST(:staff AS varchar) IS NULL OR a.user_id = CAST(:staff AS varchar))),
  (SELECT min(l.created_at) FROM crm_leads l WHERE CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar)))
""")


def _build(db, viewer, from_date, to_date, staff, client, zone, now, force_self, all_time=False):
    """(the report, the day's submission rows). The rows carry the entry ids the submit endpoint needs to update one."""
    now = now.astimezone(timezone.utc)
    staff = None if staff in (None, "", "all") else staff
    client = None if client in (None, "", "all") else client
    self_only = force_self or _is_staff(viewer)
    if self_only and staff not in (None, viewer.id):
        raise HTTPException(403, "You can only see your own report.")
    staff_id = viewer.id if self_only else staff
    if all_time:   # one small statement first: where the history starts (it decides the series' buckets), then the usual round
        earliest = db.execute(_EARLIEST_SQL, {"staff": staff_id}).scalar()
        p = resolve_period(None, None, now, zone, all_time=True,
                           since=earliest.astimezone(ZoneInfo(zone)).date() if earliest else None)
    else:
        p = resolve_period(from_date, to_date, now, zone)
    single = p.days == 1 and not all_time

    reads = {"stages": lambda s: read_stages(s, p, zone, staff_id, client), "leads": lambda s: read_leads(s, p, zone, staff_id, now)}
    if not self_only:
        reads["users"] = read_users
    if not force_self:
        reads["clients"] = lambda s: read_clients(s, p, staff_id)
        reads["tasks"] = lambda s: read_tasks(s, p, staff_id, now)
        reads["stage_now"] = lambda s: read_stage_now(s, staff_id)
    reads["stage_flow"] = lambda s: read_stage_flow(s, p, staff_id, client)
    if single:
        reads["submissions"] = lambda s: read_submissions(s, p.first, zone, staff_id)
    raw = dict(zip(reads, parallel_reads(db, *reads.values())))

    # ---- stage figures: person -> metric -> count, the previous period and the series
    cur: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    online: dict[str, int] = defaultdict(int)
    previous: dict[str, int] = defaultdict(int)
    series: dict[str, dict[date, int]] = {key: defaultdict(int) for key in METRIC_KEYS}
    by_company: dict[str, dict] = {}
    for kind, uid, metric, in_period, bucket, company, cname, n, n_online in raw["stages"]:
        if kind == "client":
            entry = by_company.setdefault(company, {"name": cname, "metrics": defaultdict(int), "online": 0})
            entry["metrics"][metric] += n
            entry["online"] += n_online
            continue
        if in_period:
            cur[uid][metric] += n
            online[uid] += n_online
            series[metric][bucket] += n
        else:
            previous[metric] += n

    # ---- lead figures
    feedback: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    pool, untouched, callbacks, resumes = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    owned, owned_untouched, by_status = defaultdict(int), defaultdict(int), defaultdict(int)
    assigned_in, notc_in, backlog = defaultdict(int), defaultdict(int), defaultdict(int)
    prev_assigned = prev_new = new_in = 0
    unowned: dict[str, int] = {"total": 0, "never": 0, "new": 0}
    for part, uid, day, key, flag, n in raw["leads"]:
        if part == "series":
            series["leadsContacted"][day] += n
        elif part == "total":
            if flag:
                cur[uid]["leadsContacted"] += n
            else:
                previous["leadsContacted"] += n
        elif part == "final":
            feedback[uid][classify(key, bool(flag))] += n
        elif part == "pool":
            pool[uid] += n
            untouched[uid] += n if flag else 0
        elif part == "callbacks":
            callbacks[uid] += n
        elif part == "resumes":
            resumes[uid] += n
        elif part == "owned":
            owned[uid] += n
            owned_untouched[uid] += n if flag else 0
        elif part == "status":
            by_status[key] += n
        elif part == "assigned_p":
            if flag:
                assigned_in[uid] += n
            else:
                prev_assigned += n
        elif part == "notcontacted_p":
            notc_in[uid] += n
        elif part == "backlog":
            backlog[uid] += n
        elif part == "unowned":
            unowned[key] = int(n)
        elif part == "new_p":
            if flag:
                new_in += n
            else:
                prev_new += n

    # ---- tasks and the pipeline right now
    task_rows = {uid: (int(done), int(pending), int(overdue)) for uid, done, pending, overdue in raw.get("tasks", [])}
    stage_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for uid, stage_no, n in raw.get("stage_now", []):
        if uid is not None:
            stage_counts[uid][int(stage_no)] += int(n)

    # ---- who is listed
    if self_only:
        people = [(viewer.id, viewer.name, getattr(viewer.role, "value", viewer.role), True)]
    else:
        users = {u.id: u for u in raw["users"]}
        if staff and staff not in users:
            raise HTTPException(404, "Recruiter not found.")
        people = []
        # Anybody who did something, or still holds leads, open tasks or candidates, is listed (so the totals add up).
        holders = set(cur) | set(feedback) | set(owned) | {u for u, (done, pending, _o) in task_rows.items() if done or pending} | set(stage_counts)
        for u in raw["users"]:
            busy = u.id in holders
            if staff and u.id != staff:
                continue
            if staff or busy or (u.active and u.role == m.Role.staff):
                people.append((u.id, u.name, u.role.value, u.active))
        known = {pid for pid, *_ in people}
        for uid in holders:   # a figure always belongs to somebody listed, so the rows add up to the team
            if uid not in known:
                people.append((uid, "Unknown user", "staff", False))

    submissions: dict[str, tuple] = {}
    for _id, entity, at, meta in raw.get("submissions", []):
        submissions.setdefault(entity, (at, (meta or {}).get("note"), (meta or {}).get("figures")))

    rows = []
    for uid, name, role, active in people:
        figures = {key: int(cur.get(uid, {}).get(key, 0)) for key in METRIC_KEYS}
        mine = feedback.get(uid, {})
        contacted = figures["leadsContacted"]
        row = {
            "id": uid, "name": name, "role": role, "initials": _initials(name), "metrics": figures, "total": sum(figures.values()),
            "interviewsOnline": int(online.get(uid, 0)),
            "leadFeedback": {
                "contacted": contacted, "interested": mine.get("interested", 0), "notInterested": mine.get("notInterested", 0),
                "noAnswer": mine.get("noAnswer", 0), "wrongNumber": mine.get("wrongNumber", 0), "callback": mine.get("callback", 0),
                "other": mine.get("answered", 0) + mine.get("notReachable", 0),
            },
            "leads": {"assigned": owned.get(uid, 0), "contacted": contacted, "interested": mine.get("interested", 0),
                      "notInterested": mine.get("notInterested", 0), "noAnswer": mine.get("noAnswer", 0),
                      "wrongNumber": mine.get("wrongNumber", 0), "callback": mine.get("callback", 0),
                      "notContacted": owned_untouched.get(uid, 0), "assignedInPeriod": assigned_in.get(uid, 0),
                      "notContactedInPeriod": notc_in.get(uid, 0), "backlog": backlog.get(uid, 0),
                      "neverContacted": backlog.get(uid, 0)},   # the same rule and number as `backlog`
            "submitted": None, "submittedAt": None, "note": None, "idle": sum(figures.values()) == 0,
            "_active": active, "_resumes": resumes.get(uid, 0), "_pool": pool.get(uid, 0), "_untouched": untouched.get(uid, 0),
            "_callbacks": callbacks.get(uid, 0),
        }
        if "tasks" in raw:
            done, pending, overdue = task_rows.get(uid, (0, 0, 0))
            row["tasks"] = {"done": done, "pending": pending, "overdue": overdue}
        if single:
            found = submissions.get(uid)
            row["submitted"] = found is not None
            row["submittedAt"] = found[0].isoformat() if found else None
            row["note"] = found[1] if found else None
            if found and isinstance(found[2], dict):
                # What the person handed in, and whether the live numbers have moved since (any figure they submitted differs now).
                live = {**figures, "interviewsOnline": row["interviewsOnline"]}
                row["submittedFigures"] = {key: int(found[2].get(key, 0)) for key in SNAPSHOT_KEYS}
                row["changedSinceSubmit"] = any(live[key] != row["submittedFigures"][key] for key in SNAPSHOT_KEYS)
        row.setdefault("submittedFigures", None)
        row.setdefault("changedSinceSubmit", False)
        rows.append(row)
    rows.sort(key=lambda r: (-r["total"], r["name"].casefold(), r["id"]))

    # ---- team figures
    totals = {key: sum(r["metrics"][key] for r in rows) for key in METRIC_KEYS}
    prev_totals = {key: int(previous.get(key, 0)) for key in METRIC_KEYS}
    buckets = p.buckets()
    metrics = []
    for key, label, definition in METRICS:
        points = [{"date": day.isoformat(), "value": int(series[key].get(day, 0)),
                   **({"bucket": p.unit} if p.unit != "day" else {})} for day in buckets]
        metrics.append({"key": key, "label": label, "definition": definition, "value": totals[key],
                        "previous": None if p.all_time else prev_totals[key],
                        "changePct": None if p.all_time else _percent_change(totals[key], prev_totals[key]), "series": points})

    by_group: dict[str, int] = defaultdict(int)
    for r in rows:
        for group, n in feedback.get(r["id"], {}).items():
            by_group[group] += n
    not_contacted = sum(r["_untouched"] for r in rows)
    assigned = sum(r["_pool"] for r in rows)
    by_outcome = [{"key": key, "label": label, "count": by_group[key]} for key, label in OUTCOME_LABELS.items() if by_group.get(key)]
    by_outcome.append({"key": "notContacted", "label": NOT_CONTACTED, "count": not_contacted})
    contacted_total = totals["leadsContacted"]

    compliance = None
    if single and not self_only:
        expected = [r for r in rows if r["role"] == m.Role.staff.value and r["_active"]]
        compliance = {"expected": len(expected), "submitted": sum(1 for r in expected if r["submitted"]),
                      "missing": [{"id": r["id"], "name": r["name"]} for r in expected if not r["submitted"]]}

    clients = [{"id": cid, "name": name} for cid, name in raw.get("clients", [])]
    by_client = []
    for cid, entry in by_company.items():
        figures_ = {key: int(entry["metrics"].get(key, 0)) for key in METRIC_KEYS if key != "leadsContacted"}
        by_client.append({"id": cid, "name": entry["name"], "metrics": figures_, "interviewsOnline": int(entry["online"]),
                          "total": client_total(figures_, int(entry["online"]))})
    by_client.sort(key=lambda r: (-r["total"], r["name"].casefold(), r["id"]))
    if len(by_client) > BY_CLIENT_MAX:
        rest, by_client = by_client[BY_CLIENT_MAX:], by_client[:BY_CLIENT_MAX]
        rolled = {key: sum(r["metrics"][key] for r in rest) for key in by_client[0]["metrics"]} if by_client else \
            {key: sum(r["metrics"][key] for r in rest) for key in rest[0]["metrics"]}
        rolled_online = sum(r["interviewsOnline"] for r in rest)
        by_client.append({"id": "other", "name": "Other clients", "metrics": rolled,
                          "interviewsOnline": rolled_online, "total": client_total(rolled, rolled_online)})
    listed = {r["id"] for r in rows}
    total_status = sum(by_status.values())
    owned_total = sum(owned.get(uid, 0) for uid in listed)
    lead_sheet = {
        "totalLeads": total_status, "assigned": owned_total, "unassigned": max(0, total_status - owned_total) if not staff_id else 0,
        "contacted": contacted_total, "notContacted": sum(owned_untouched.get(uid, 0) for uid in listed),
        # Period-aware numbers (the figures above are the sheet as it stands now): what was uploaded / handed out in the period,
        # how much of it nobody has worked yet, and the whole backlog of assigned leads never contacted.
        # Reconciles with the Lead sheet screen: neverContacted is its "Not contacted" card (status new and no call, EVERY lead,
        # assigned or not = lead_sheet.summary()["uncontacted"]); the leads nobody owns are `unassignedLeads` (`unassigned` stays the
        # plain count of every unowned lead). Always:
        #   totalLeads == sum(staff[].leads.assigned) + unassignedLeads.total
        #   neverContacted == sum(staff[].leads.neverContacted) + unassignedLeads.neverContacted
        "neverContacted": sum(backlog.get(uid, 0) for uid in listed) + unowned["never"],
        "unassignedLeads": {"total": unowned["total"], "neverContacted": unowned["never"], "newInPeriod": unowned["new"]},
        "newInPeriod": new_in, "assignedInPeriod": sum(assigned_in.get(uid, 0) for uid in listed),
        "notContactedInPeriod": sum(notc_in.get(uid, 0) for uid in listed), "backlog": sum(backlog.get(uid, 0) for uid in listed),
        "previous": None if p.all_time else {"assignedInPeriod": prev_assigned, "contacted": prev_totals["leadsContacted"],
                                             "newInPeriod": prev_new},
        "byStatus": [{"key": key, "label": LEAD_STATUS_LABELS[key], "count": by_status.get(key, 0)} for key in LEAD_STATUS_ORDER],
    }
    team_tasks = None
    if "tasks" in raw:
        done = sum(r["tasks"]["done"] for r in rows)
        pending = sum(r["tasks"]["pending"] for r in rows)
        team_tasks = {"total": done + pending, "done": done, "pending": pending, "overdue": sum(r["tasks"]["overdue"] for r in rows)}
    stages = None
    if "stage_now" in raw:
        per_staff = []
        for r in rows:
            counts = {str(no): int(stage_counts.get(r["id"], {}).get(no, 0)) for no in range(0, TOTAL_STAGES + 1)}
            per_staff.append({"id": r["id"], "counts": counts, "total": sum(counts.values())})
        stages = {"totals": [{"no": no, "key": STAGE_NOW_KEYS[no][0], "label": STAGE_NOW_KEYS[no][1],
                              "count": sum(x["counts"][str(no)] for x in per_staff)} for no in range(0, TOTAL_STAGES + 1)],
                  "perStaff": per_staff}
    flow = {no: (int(done), int(ended)) for no, done, ended in raw["stage_flow"]}
    stage_flow = [{"no": definition.no, "key": definition.key, "label": definition.title,
                   "completed": flow.get(definition.no, (0, 0))[0], "stopped": flow.get(definition.no, (0, 0))[1]} for definition in STAGES]
    for r in rows:   # the working counters are not part of the answer
        for hidden in [k for k in r if k.startswith("_")]:
            del r[hidden]
    report = {
        "period": {"from": p.first.isoformat(), "to": p.last.isoformat(), "days": p.days, "previousFrom": p.prev_first.isoformat() if p.prev_first else None,
                   "previousTo": p.prev_last.isoformat() if p.prev_last else None, "bucket": p.unit, "partial": p.partial,
                   "previousUntil": None if p.all_time else p.prev_until.isoformat(), "allTime": p.all_time},
        "generatedAt": now.isoformat(), "scope": "self" if self_only else "team", "metrics": metrics,
        "extra": {"interviewsOnline": sum(r["interviewsOnline"] for r in rows), "leadsAssigned": assigned,
                  "leadsContactedPct": round(contacted_total / max(assigned, contacted_total) * 100, 1) if max(assigned, contacted_total) else None},
        "leadFeedback": {"contacted": contacted_total, "byOutcome": by_outcome, "resumesReceived": sum(resumes.get(r["id"], 0) for r in rows),
                         "interested": by_group.get("interested", 0), "callbacksDue": sum(callbacks.get(r["id"], 0) for r in rows)},
        "funnel": [{"key": key, "label": LABELS[key], "value": totals[key]} for key in FUNNEL_ORDER],
        "staff": rows, "compliance": compliance, "clients": clients, "leadSheet": lead_sheet, "byClient": by_client,
        "stageFlow": stage_flow,
    }
    if team_tasks is not None:
        report["tasks"] = team_tasks
    if stages is not None:
        report["stages"] = stages
    return report, raw.get("submissions", [])


# ------------------------------------------------------------------ my day and the day-close submission

def my_day(db, viewer, day: date, zone: str, now: datetime) -> dict:
    """The caller's own figures for one agency day (the staff row of the report) and whether the day was submitted."""
    report = build_report(db, viewer, day, day, None, None, zone, now, force_self=True)
    return {"date": day.isoformat(), **report["staff"][0]}


def _clean_note(note: str | None) -> str | None:
    text_ = "".join(c for c in (note or "") if c >= " " or c in "\n\t").strip()
    return text_ or None


def submit_day(db, viewer, day: date, note: str | None, zone: str, now: datetime) -> dict:
    """Close the caller's day: one AuditEntry per (person, date). A second submit updates the note and the figures and keeps the
    first submittedAt. Only today or yesterday (agency time) can be submitted, and only for oneself."""
    now = now.astimezone(timezone.utc)
    today = agency_today(now, zone)
    if day not in (today, today - timedelta(days=1)):
        raise HTTPException(422, "You can submit the report for today or yesterday only.")
    report, entries = _build(db, viewer, day, day, None, None, zone, now, True)
    row = report["staff"][0]
    figures = {**row["metrics"], "interviewsOnline": row["interviewsOnline"]}
    note = _clean_note(note)
    existing = next((e for e in entries if e.entity_id == viewer.id), None)
    if existing is not None:
        meta = {**(existing.meta or {}), "note": note, "figures": figures, "leadFeedback": row["leadFeedback"],
                "updatedAt": now.isoformat()}
        meta.setdefault("submittedAt", existing.at.isoformat())
        db.execute(update(m.AuditEntry).where(m.AuditEntry.id == existing.id).values(meta=meta))
        submitted_at = existing.at
    else:
        meta = {"date": day.isoformat(), "submittedAt": now.isoformat(), "note": note, "figures": figures,
                "leadFeedback": row["leadFeedback"]}
        db.add(m.AuditEntry(actor_id=viewer.id, action=SUBMIT_ACTION, target=viewer.name, entity_type="user", entity_id=viewer.id,
                            meta=meta, at=now))
        submitted_at = now
    change_feed.announce_daily_report(db, viewer.id)   # the audit table announces nothing by itself; add no statement, only a topic
    db.commit()
    return {"date": day.isoformat(), "submittedAt": submitted_at.isoformat(), "figures": figures, "note": note}


# ------------------------------------------------------------------ the day closes handed in

_HISTORY_SQL = text("""
WITH latest AS (
  SELECT DISTINCT ON (a.entity_id, a.meta ->> 'date') a.entity_id, a.meta ->> 'date' AS day, a.at, a.meta
    FROM system_audit_log a
   WHERE a.action = :action AND a.entity_type = 'user' AND a.at >= :lo AND a.at < :hi
     AND a.meta ->> 'date' BETWEEN :first AND :last
     AND (CAST(:staff AS varchar) IS NULL OR a.entity_id = CAST(:staff AS varchar))
   ORDER BY a.entity_id, a.meta ->> 'date', a.at DESC, a.id DESC)
SELECT l.day, l.entity_id, u.name, l.at, l.meta, count(*) OVER () AS total
  FROM latest l LEFT JOIN auth_users u ON u.id = l.entity_id
 ORDER BY l.day DESC, l.at DESC, l.entity_id
 LIMIT :limit OFFSET :offset
""")


def history(db, viewer, from_date: date | None, to_date: date | None, staff: str | None, page: int, page_size: int, zone: str,
            now: datetime, *, all_time: bool = False) -> dict:
    """The submitted day closes of the range (default: the last 30 days), newest first, one row per (person, date) holding the latest
    snapshot. Supervisors read everybody (or `staff`), a recruiter only themselves. One statement (one more for a page past the end)."""
    now = now.astimezone(timezone.utc)
    today = agency_today(now, zone)
    last = to_date or today
    first = from_date or last - timedelta(days=29)
    p = resolve_period(None, None, now, zone, all_time=True) if all_time else resolve_period(first, last, now, zone)
    staff = None if staff in (None, "", "all") else staff
    if _is_staff(viewer):
        if staff not in (None, viewer.id):
            raise HTTPException(403, "You can only see your own report.")
        staff = viewer.id
    # a day close is written on its own day or the next one, which keeps the scan of the audit table small
    base = {"action": SUBMIT_ACTION, "lo": p.start, "hi": day_bounds(p.last + timedelta(days=1), zone)[1], "first": p.first.isoformat(),
            "last": p.last.isoformat(), "staff": staff}

    def fetch(offset, limit):
        return db.execute(_HISTORY_SQL, {**base, "offset": offset, "limit": limit}).all()

    rows = fetch(page * page_size, page_size)
    if not rows and page > 0:
        total = (fetch(0, 1) or [None])[0]
        total = int(total.total) if total else 0
    else:
        total = int(rows[0].total) if rows else 0
    out = []
    for r in rows:
        meta = r.meta or {}
        figures = meta.get("figures") or {}
        out.append({"date": r.day, "staffId": r.entity_id, "staffName": r.name or "Unknown user", "submittedAt": r.at.isoformat(),
                    "note": meta.get("note"), "figures": {key: int(figures.get(key, 0)) for key in SNAPSHOT_KEYS}})
    return {"total": total, "page": page, "pageSize": page_size, "rows": out}


# ------------------------------------------------------------------ the candidates and leads behind a number

def _stage_detail(key: str, outcome: str | None, company: str | None, mode: str | None, travel_date: str | None) -> str:
    manual = " (completed without details)" if outcome == "Completed manually" else ""
    if key == "prescreened":
        return f"Prescreening: {outcome or 'completed'}"
    if key == "forwarded":
        return f"Forwarded to {company or 'the client'}{manual}"
    if key == "shortlisted":
        return f"Shortlisted by {company or 'the client'}: interview scheduled{manual}"
    if key == "interviews":
        held = {"online": " (online)", "in_person": " (in person)"}.get(mode or "", "")
        return f"Interview result: {outcome}{held}"
    if key == "offers":
        return f"Offer letter received: {outcome}"
    if key == "visas":
        return f"Visa received{manual}"
    return "Travelled" + (f" (travel date {travel_date})" if travel_date else "")


def _stage_rows(session, key, p, staff_id, client_id, offset, limit):
    AS, A, C, O = m.ApplicationStage, m.Application, m.Candidate, m.Order
    who, conditions = _stage_filters(AS, p, p.start, staff_id, client_id, only=key)
    mode = _interview_mode(AS) if key == "interviews" else cast(None, String)
    rows = session.execute(
        select(AS.completed_at, m.User.name, C.id, C.name, C.care_id, m.Company.name, m.Designation.title, AS.outcome, mode,
               AS.data["travelDate"].as_string(), func.count().over())
        .select_from(AS).join(A, A.id == AS.application_id).join(C, C.id == A.candidate_id).join(O, O.id == A.order_id)
        .join(m.Company, m.Company.id == O.company_id).join(m.Designation, m.Designation.id == O.designation_id)
        .outerjoin(m.User, m.User.id == who).where(*conditions)
        .order_by(AS.completed_at.desc(), AS.id).offset(offset).limit(limit)).all()
    return [{"at": r[0].isoformat(), "staffName": r[1] or "Unknown user", "kind": "candidate", "candidateId": r[2], "leadId": None,
             "careId": r[4], "name": r[3], "client": r[5], "job": r[6], "detail": _stage_detail(key, r[7], r[5], r[8], r[9])}
            for r in rows], (int(rows[0][10]) if rows else 0)


_LEAD_ROWS_SQL = text(f"""
WITH f AS (
  SELECT DISTINCT ON (a.user_id, a.lead_id) a.user_id AS uid, a.lead_id, a.created_at AS at,
         CASE WHEN a.kind = 'call' THEN a.meta ->> 'outcome' WHEN a.meta ->> 'attempt' = 'true' THEN 'no_answer' ELSE a.meta ->> 'to' END AS raw
    FROM crm_lead_activities a
   WHERE a.kind IN ('call', 'status') AND a.user_id IS NOT NULL AND a.created_at >= :cur_start AND a.created_at < :end
     AND (CAST(:staff AS varchar) IS NULL OR a.user_id = CAST(:staff AS varchar))
   ORDER BY a.user_id, a.lead_id, a.created_at DESC, a.id DESC)
SELECT f.at, u.name AS staff_name, l.id AS lead_id, l.name, l.position_applied, f.raw,
       (l.next_follow_up_at IS NOT NULL AND l.status NOT IN {CLOSED}) AS follow, count(*) OVER () AS total
  FROM f JOIN crm_leads l ON l.id = f.lead_id LEFT JOIN auth_users u ON u.id = f.uid
 ORDER BY f.at DESC, l.id, f.uid
 LIMIT :limit OFFSET :offset
""")


def _lead_rows(session, p, staff_id, offset, limit):
    rows = session.execute(_LEAD_ROWS_SQL, {"staff": staff_id, "cur_start": p.start, "end": p.end, "offset": offset, "limit": limit}).all()
    return [{"at": r.at.isoformat(), "staffName": r.staff_name or "Unknown user", "kind": "lead", "candidateId": None, "leadId": r.lead_id,
             "careId": None, "name": r.name or "No name", "client": None, "job": r.position_applied,
             "detail": f"Outcome: {OUTCOME_LABELS[classify(r.raw, bool(r.follow))]}"} for r in rows], (int(rows[0].total) if rows else 0)


EXTRA_LABELS = {"leadsAssigned": "Leads assigned", "tasksDone": "Tasks done", "tasksPending": "Tasks pending",
                "tasksOverdue": "Tasks overdue"}


def metric_label(metric: str) -> str | None:
    """Label of a drill-down metric: the eight figures, the lead / task totals and `stage:<0-13>` (candidates now at that stage)."""
    if metric in LABELS:
        return LABELS[metric]
    if metric in EXTRA_LABELS:
        return EXTRA_LABELS[metric]
    if metric in LEAD_LIST_METRICS:
        return LEAD_LIST_METRICS[metric][0]
    found = re.fullmatch(r"stage:(\d{1,2})", metric)
    if found and int(found.group(1)) in STAGE_NOW_KEYS:
        return STAGE_NOW_KEYS[int(found.group(1))][1]
    return None


LEAD_LIST_METRICS = {
    "leadsAssignedInPeriod": ("Leads assigned in the period", "Assigned"),
    "leadsNewInPeriod": ("New leads in the period", "Uploaded"),
    "leadsNotContactedInPeriod": ("Assigned in the period, not contacted yet", "Not contacted since assigned"),
    "leadsBacklog": ("Backlog: assigned, never contacted", "Never contacted"),
    # The Lead sheet screen's own cards, whoever holds the lead (unowned ones show as "Unassigned"): all leads, and status new with no call.
    "leadsTotal": ("Leads on the sheet", "Lead"),
    "leadsNotContacted": ("Never contacted (Lead sheet)", "Never contacted"),
}
_LEAD_LIST_WHERE = {
    "leadsAssignedInPeriod": (AT, f"l.owner_id IS NOT NULL AND {AT} >= :cur_start AND {AT} < :end"),
    "leadsNotContactedInPeriod": (AT, f"l.owner_id IS NOT NULL AND {AT} >= :cur_start AND {AT} < :end AND NOT EXISTS "
                                      f"(SELECT 1 FROM crm_lead_activities x WHERE x.lead_id = l.id AND x.user_id = l.owner_id "
                                      f"AND x.kind IN ('call', 'status') AND x.created_at >= {AT})"),
    "leadsNewInPeriod": ("l.created_at", "l.created_at >= :cur_start AND l.created_at < :end"),
    "leadsBacklog": (AT, "l.owner_id IS NOT NULL AND l.status = 'new' AND l.call_count = 0"),
    "leadsTotal": ("l.created_at", "TRUE"),
    "leadsNotContacted": ("l.created_at", "l.status = 'new' AND l.call_count = 0"),
}


def _lead_list_rows(session, metric, p, staff_id, offset, limit):
    """The leads behind the period-aware lead numbers (same conditions as the report's SQL), newest first."""
    at, where = _LEAD_LIST_WHERE[metric]
    sql = text(f"SELECT {at} AS at, u.name AS owner_name, l.id, l.name, l.position_applied, l.status, l.status_label, count(*) OVER () AS total "
               f"FROM crm_leads l LEFT JOIN auth_users u ON u.id = l.owner_id WHERE {where} "
               f"AND (CAST(:staff AS varchar) IS NULL OR l.owner_id = CAST(:staff AS varchar)) ORDER BY 1 DESC, l.id LIMIT :limit OFFSET :offset")
    rows = session.execute(sql, {"staff": staff_id, "cur_start": p.start, "end": p.end, "offset": offset, "limit": limit}).all()
    verb = LEAD_LIST_METRICS[metric][1]
    return [{"at": r.at.isoformat(), "staffName": r.owner_name or "Unassigned", "kind": "lead", "candidateId": None, "leadId": r.id,
             "careId": None, "name": r.name or "No name", "client": None, "job": r.position_applied,
             "detail": f"{verb}: {r.status_label or LEAD_STATUS_LABELS.get(r.status, r.status)}"} for r in rows], (int(rows[0].total) if rows else 0)


def _owned_lead_rows(session, staff_id, offset, limit):
    L = m.Lead
    at = func.coalesce(L.assigned_at, L.created_at)
    query = select(at, m.User.name, L.id, L.name, L.position_applied, L.status, L.status_label, func.count().over()).select_from(L) \
        .join(m.User, m.User.id == L.owner_id).where(L.owner_id.is_not(None))
    if staff_id:
        query = query.where(L.owner_id == staff_id)
    rows = session.execute(query.order_by(at.desc(), L.id).offset(offset).limit(limit)).all()
    return [{"at": r[0].isoformat(), "staffName": r[1], "kind": "lead", "candidateId": None, "leadId": r[2], "careId": None,
             "name": r[3] or "No name", "client": None, "job": r[4],
             "detail": f"Status: {r[6] or LEAD_STATUS_LABELS.get(r[5], r[5])}"} for r in rows], (int(rows[0][7]) if rows else 0)


def _task_rows(session, metric, p, staff_id, now, offset, limit):
    T, C = m.Task, m.Candidate
    is_open = and_(T.completed_at.is_(None), T.cancelled_at.is_(None))
    if metric == "tasksDone":
        condition, at, verb = and_(T.completed_at >= p.start, T.completed_at < p.end), T.completed_at, "Task done"
    elif metric == "tasksPending":
        condition, at, verb = is_open, T.created_at, "Task pending"
    else:
        condition, at, verb = and_(is_open, T.due_at < now), T.due_at, "Task overdue"
    query = (select(at, m.User.name, C.id, C.name, C.care_id, m.Company.name, m.Designation.title, T.title, func.count().over())
             .select_from(T).join(C, C.id == T.candidate_id).outerjoin(m.User, m.User.id == T.owner_id)
             .outerjoin(m.Application, m.Application.id == T.application_id).outerjoin(m.Order, m.Order.id == m.Application.order_id)
             .outerjoin(m.Company, m.Company.id == m.Order.company_id).outerjoin(m.Designation, m.Designation.id == m.Order.designation_id)
             .where(condition))
    if staff_id:
        query = query.where(T.owner_id == staff_id)
    rows = session.execute(query.order_by(at.desc(), T.id).offset(offset).limit(limit)).all()
    return [{"at": r[0].isoformat(), "staffName": r[1] or "Unknown user", "kind": "candidate", "candidateId": r[2], "leadId": None,
             "careId": r[4], "name": r[3], "client": r[5], "job": r[6], "detail": f"{verb}: {r[7]}"} for r in rows], \
        (int(rows[0][8]) if rows else 0)


def _stage_now_rows(session, number, staff_id, offset, limit):
    A, C, O = m.Application, m.Candidate, m.Order
    if number == 0:
        query = (select(C.updated_at, m.User.name, C.id, C.name, C.care_id, cast(None, String).label("client"),
                        cast(None, String).label("job"), func.count().over())
                 .select_from(C).join(m.User, m.User.id == C.owner_id)
                 .where(C.status.in_(WORKABLE), ~exists().where(A.candidate_id == C.id, A.status == m.ApplicationStatus.active).correlate(C),
                        ~_parked()))
        if staff_id:
            query = query.where(C.owner_id == staff_id)
        rows = session.execute(query.order_by(C.updated_at.desc(), C.id).offset(offset).limit(limit)).all()
        return [{"at": r[0].isoformat(), "staffName": r[1], "kind": "candidate", "candidateId": r[2], "leadId": None, "careId": r[4],
                 "name": r[3], "client": None, "job": None, "detail": "Not on an order yet"} for r in rows], (int(rows[0][7]) if rows else 0)
    owner = func.coalesce(C.owner_id, A.owner_id)
    query = (select(A.updated_at, m.User.name, C.id, C.name, C.care_id, m.Company.name, m.Designation.title, func.count().over())
             .select_from(A).join(C, C.id == A.candidate_id).join(O, O.id == A.order_id).join(m.Company, m.Company.id == O.company_id)
             .join(m.Designation, m.Designation.id == O.designation_id).outerjoin(m.User, m.User.id == owner)
             .where(A.status == m.ApplicationStatus.active, A.current_stage == number))
    if staff_id:
        query = query.where(owner == staff_id)
    rows = session.execute(query.order_by(A.updated_at.desc(), A.id).offset(offset).limit(limit)).all()
    title = STAGE_NOW_KEYS[number][1]
    return [{"at": r[0].isoformat(), "staffName": r[1] or "Unknown user", "kind": "candidate", "candidateId": r[2], "leadId": None,
             "careId": r[4], "name": r[3], "client": r[5], "job": r[6], "detail": f"At stage {number}: {title}"} for r in rows], \
        (int(rows[0][7]) if rows else 0)


def drilldown(db, viewer, metric: str, from_date, to_date, staff, client, page: int, page_size: int, zone: str, now: datetime,
              *, all_time: bool = False) -> dict:
    """The rows behind one number, newest first, in the same scope as the report. One statement (plus one only for a page past the end)."""
    label = metric_label(metric)
    if label is None:
        raise HTTPException(422, "Unknown metric.")
    now = now.astimezone(timezone.utc)
    p = resolve_period(None, None, now, zone, all_time=True) if all_time else resolve_period(from_date, to_date, now, zone)
    staff = None if staff in (None, "", "all") else staff
    client = None if client in (None, "", "all") else client
    if _is_staff(viewer):
        if staff not in (None, viewer.id):
            raise HTTPException(403, "You can only see your own report.")
        staff = viewer.id

    def fetch(offset, limit):
        if metric == "leadsContacted":
            return _lead_rows(db, p, staff, offset, limit)
        if metric == "leadsAssigned":
            return _owned_lead_rows(db, staff, offset, limit)
        if metric in LEAD_LIST_METRICS:
            return _lead_list_rows(db, metric, p, staff, offset, limit)
        if metric in ("tasksDone", "tasksPending", "tasksOverdue"):
            return _task_rows(db, metric, p, staff, now, offset, limit)
        if metric.startswith("stage:"):
            return _stage_now_rows(db, int(metric[6:]), staff, offset, limit)
        return _stage_rows(db, metric, p, staff, client, offset, limit)

    rows, total = fetch(page * page_size, page_size)
    if not rows and page > 0:   # a page past the end still reports the real total
        total = fetch(0, 1)[1]
    return {"metric": metric, "label": label, "total": total, "page": page, "pageSize": page_size, "rows": rows}


# ------------------------------------------------------------------ Excel export

def export_workbook(report: dict, history_rows: list[dict] | None = None) -> bytes:
    """Summary, By staff, By day and Lead feedback as one .xlsx (the report_exports writer: text cells are inline strings). No ID numbers."""
    from app.services.report_exports import xlsx_rows

    period = report["period"]
    summary = [["Metric", "Definition", "This period", "Previous period", "Change (%)"]]
    summary += [[x["label"], x["definition"], x["value"], x["previous"], x["changePct"]] for x in report["metrics"]]
    summary += [[], ["Online interviews (included in interviews)", "", report["extra"]["interviewsOnline"]],
                ["Period", f'{period["from"]} to {period["to"]}'], ["Previous period", f'{period["previousFrom"]} to {period["previousTo"]}' if period["previousFrom"] else "none (all time)"],
                ["Generated at", report["generatedAt"]], ["Scope", report["scope"]]]

    single = period["days"] == 1
    head = ["Recruiter", "Role"] + [LABELS[k] for k in METRIC_KEYS] + [
        "Total", "Interviews online", "Interested", "Not interested", "No answer", "Wrong number", "Call back", "Other"]
    if single:
        head += ["Day closed", "Submitted at", "Note"]
    by_staff = [head]
    for s in report["staff"]:
        f = s["leadFeedback"]
        line = [s["name"], ROLE_NAMES.get(s["role"], s["role"])] + [s["metrics"][k] for k in METRIC_KEYS] + [
            s["total"], s["interviewsOnline"], f["interested"], f["notInterested"], f["noAnswer"], f["wrongNumber"], f["callback"], f["other"]]
        if single:
            line += ["Yes" if s["submitted"] else "No", s["submittedAt"] or "", s["note"] or ""]
        by_staff.append(line)

    by_day = [[{"week": "Week starting", "month": "Month starting"}.get(period["bucket"], "Date")] + [LABELS[k] for k in METRIC_KEYS]]
    for i, point in enumerate(report["metrics"][0]["series"]):
        by_day.append([point["date"]] + [x["series"][i]["value"] for x in report["metrics"]])

    lead = report["leadFeedback"]
    feedback = [["Outcome", "Leads"]] + [[x["label"], x["count"]] for x in lead["byOutcome"]]
    feedback += [[], ["Leads contacted", lead["contacted"]], ["Of them interested", lead["interested"]],
                 ["Resumes received", lead["resumesReceived"]], ["Call backs due", lead["callbacksDue"]],
                 ["Leads assigned and open", report["extra"]["leadsAssigned"]]]
    sheets = [("Summary", summary), ("By recruiter", by_staff), ("By day", by_day), ("Lead feedback", feedback)]

    # Leads & tasks: one row per person and a Total row, then the lead sheet by current status.
    tasks_on = "tasks" in report
    head = ["Recruiter", "Leads assigned", "Leads contacted", "Interested", "Not interested", "No answer", "Wrong number", "Call back",
            "Not contacted", "Assigned in period", "Not contacted in period", "Backlog (never contacted)"] \
        + (["Tasks done", "Tasks pending", "Tasks overdue"] if tasks_on else [])
    table, sums = [head], [0] * (len(head) - 1)
    for s in report["staff"]:
        f = s["leads"]
        line = [f["assigned"], f["contacted"], f["interested"], f["notInterested"], f["noAnswer"], f["wrongNumber"], f["callback"],
                f["notContacted"], f["assignedInPeriod"], f["notContactedInPeriod"], f["backlog"]] + ([s["tasks"]["done"], s["tasks"]["pending"], s["tasks"]["overdue"]] if tasks_on else [])
        sums = [a + b for a, b in zip(sums, line)]
        table.append([s["name"]] + line)
    table.append(["Total"] + sums)
    sheet = report["leadSheet"]
    table += [[], ["Lead sheet", "Leads"], ["All leads on the sheet", sheet["totalLeads"]], ["Assigned", sheet["assigned"]],
              ["Unassigned", sheet["unassigned"]], ["New leads in period", sheet["newInPeriod"]],
              ["Assigned in period", sheet["assignedInPeriod"]], ["Not contacted in period", sheet["notContactedInPeriod"]],
              ["Backlog (assigned, never contacted)", sheet["backlog"]], ["Never contacted (Lead sheet, all leads)", sheet["neverContacted"]],
              ["Unassigned: never contacted", sheet["unassignedLeads"]["neverContacted"]]] + [[f"Status: {x['label']}", x["count"]] for x in sheet["byStatus"]]
    sheets.append(("Leads & tasks", table))

    if "stages" in report:   # Stages now: candidates currently at each stage, per person
        stages = report["stages"]
        head = ["Recruiter"] + [x["label"] for x in stages["totals"]] + ["Total"]
        names = {s["id"]: s["name"] for s in report["staff"]}
        table = [head] + [[names.get(x["id"], x["id"])] + [x["counts"][str(no)] for no in range(0, TOTAL_STAGES + 1)] + [x["total"]]
                          for x in stages["perStaff"]]
        table.append(["Total"] + [x["count"] for x in stages["totals"]] + [sum(x["total"] for x in stages["perStaff"])])
        sheets.append(("Stages now", table))

    # Same columns and order as the By client table on screen; Total is the sum of exactly those. Interviews conducted (every mode) is
    # not a column of the table, so it stands after Total as a note and is not part of it.
    def column(c, key):
        return c["interviewsOnline"] if key == "interviewsOnline" else c["metrics"][key]
    by_client = [["Client"] + [CLIENT_COLUMN_LABELS.get(k) or LABELS[k] for k in CLIENT_COLUMNS] + ["Total", "Interviews conducted (all modes)"]]
    by_client += [[c["name"]] + [column(c, k) for k in CLIENT_COLUMNS] + [c["total"], c["metrics"]["interviews"]] for c in report["byClient"]]
    sheets.append(("By client", by_client))

    submitted = [["Date", "Recruiter", "Submitted at"] + [k for k in SNAPSHOT_KEYS] + ["Note"]]
    submitted += [[r["date"], r["staffName"], r["submittedAt"]] + [r["figures"][k] for k in SNAPSHOT_KEYS] + [r["note"] or ""]
                  for r in (history_rows or [])]
    sheets.append(("Submitted reports", submitted))
    return xlsx_rows(sheets)
