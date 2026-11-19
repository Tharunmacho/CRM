"""Daily CRM Work Report endpoints (the figures and their definitions live in app/services/daily_work_report.py).

CONTRACT (camelCase JSON). Dates are agency-time days `YYYY-MM-DD` (setting AGENCY_TIMEZONE, Asia/Kolkata). Include this router
BEFORE the generic reports router: `/api/reports/{report}/export` would otherwise read "daily-work" as a report name.

PERIOD "TOTAL": every endpoint below except /mine and /submit also takes `period=all` (from/to ignored): all time, from the earliest
record to today, no 366-day cap, no previous period (metric.previous / changePct / leadSheet.previous / period.previousFrom / previousTo /
previousUntil are null, period.partial false, period.allTime true), series by week (span up to ~2 years) or by month (point "bucket":
"month"), compliance null.

(1) GET /api/reports/daily-work?from=&to=&staff=<userId|all>&client=<companyId|all>
    defaults from = to = today; more than 366 days -> 422; from after to -> 422. Admin/MD: everyone, `staff` narrows.
    A recruiter (role staff): scope "self", staff = [themselves], compliance null; naming someone else -> 403.
    {"period": {"from","to","days","previousFrom","previousTo","bucket": "day"|"week"},
     "generatedAt": ISO, "scope": "team"|"self",
     "metrics": [{"key": "leadsContacted"|"prescreened"|"forwarded"|"shortlisted"|"interviews"|"offers"|"visas"|"travelled",
                  "label", "definition", "value": int, "previous": int, "changePct": number|null,
                  "series": [{"date": "YYYY-MM-DD", "value": int}]}],   # one point per day, zeros included; more than 92 days:
                                   # one point per week (date = Monday) and every point also carries "bucket": "week"
     "extra": {"interviewsOnline": int, "leadsAssigned": int, "leadsContactedPct": number|null},
     "leadFeedback": {"contacted": int, "byOutcome": [{"key","label","count"}], "resumesReceived": int, "interested": int,
                      "callbacksDue": int},
     "funnel": [{"key","label","value"}],   # prescreened, forwarded, shortlisted, interviews, offers, visas, travelled
     "staff": [{"id","name","role","initials","metrics": {<8 keys>: int}, "total": int, "interviewsOnline": int,
                "leadFeedback": {"contacted","interested","notInterested","noAnswer","wrongNumber","callback","other"},
                "submitted": bool|null, "submittedAt": ISO|null, "note": str|null, "idle": bool}],   # total desc, then name
     "compliance": {"expected": int, "submitted": int, "missing": [{"id","name"}]} | null,   # one-day range, supervisors only
     "clients": [{"id","name"}]}   # employer companies with activity in the range, for the client filter

    ADDITIONS (management table): period.partial (the range includes the running moment; `previous` then covers only the same elapsed
    time of the previous period, up to period.previousUntil ISO), staff[].leads {assigned, contacted, interested, notInterested,
    noAnswer, wrongNumber, callback, notContacted}, staff[].tasks {done, pending, overdue} (not in /mine),
    "leadSheet": {totalLeads, assigned, unassigned, contacted, notContacted, byStatus: [{key,label,count}]},
    "tasks": {total, done, pending, overdue}, "stages": {totals: [{no 0..11, key, label, count}], perStaff: [{id, counts: {"0".."11"}, total}]}.

    PERIOD-AWARE LEADS (additive; the all-time numbers stay): staff[].leads gains assignedInPeriod (leads handed to the recruiter in the
    period: COALESCE(assigned_at, created_at) in the range, agency time), notContactedInPeriod (of those, no call/status by the
    recruiter since the assignment) and backlog (owned, status new, never called); leadSheet gains newInPeriod (uploaded in the period),
    assignedInPeriod, notContactedInPeriod, backlog and previous {assignedInPeriod, contacted, newInPeriod} (same elapsed-time rule as
    the figures). Drill-down metrics leadsAssignedInPeriod, leadsNewInPeriod, leadsNotContactedInPeriod, leadsBacklog.
    MORE ADDITIONS: "byClient": [{id, name, metrics: {the 7 stage figures}, interviewsOnline, total}] sorted by total desc, at most 50
    rows then {id: "other", name: "Other clients"}; staff[].submittedFigures {forwarded, prescreened, shortlisted, interviews,
    interviewsOnline, offers, visas, travelled, leadsContacted} | null and staff[].changedSinceSubmit: bool (one-day range: what the
    person submitted, and whether the live numbers differ now; ranges: null / false).

    GET /api/reports/daily-work/history?from=&to=&staff=&page=0&page_size=25 (default: the last 30 days) ->
    {"total","page","pageSize","rows": [{"date","staffId","staffName","submittedAt","note","figures": {the 9 keys above}}]},
    newest first, one row per (person, date) with the latest snapshot; a recruiter reads only their own.

(2) GET /api/reports/daily-work/drilldown?metric=<key>&from=&to=&staff=&client=&page=0&page_size=25   (page_size 1-100)
    metric also accepts leadsAssigned, tasksDone, tasksPending, tasksOverdue and stage:<0-11> (candidates now at that stage).
    {"metric","label","total","page","pageSize","rows": [{"at": ISO, "staffName", "kind": "candidate"|"lead", "candidateId"|null,
      "leadId"|null, "careId"|null, "name", "client"|null, "job"|null, "detail"}]}   newest first; never a phone/passport/ID number.

(3) POST /api/reports/daily-work/submit {"date": "YYYY-MM-DD" (today or yesterday), "note": str|null (<= 500)}
    -> {"date","submittedAt": ISO,"figures": {<8 keys>: int, "interviewsOnline": int},"note"}; only for oneself; idempotent per
    (person, date): a repeat updates the note and the figures and keeps the first submittedAt. The record is an AuditEntry
    "Daily report submitted" (entity_type "user", entity_id = the person).
    GET /api/reports/daily-work/mine?date=YYYY-MM-DD (default today) -> {"date", ...the caller's staff row of the report...}

(4) GET /api/reports/daily-work/export?from=&to=&staff=&client= -> daily-work-report_<from>_<to>.xlsx with the sheets
    Summary, By staff, By day, Lead feedback. Same scoping as (1).
"""
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services import daily_work_report as report_service

router = APIRouter(prefix="/api/reports/daily-work", tags=["daily-work-report"])


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    note: str | None = Field(default=None, max_length=report_service.NOTE_MAX)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("")
def daily_work_report(from_: date | None = Query(default=None, alias="from"), to: date | None = None, staff: str | None = None,
                      client: str | None = None, period: Literal["all"] | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return report_service.build_report(db, user, from_, to, staff, client, get_settings().agency_timezone, _now(),
                                      all_time=period == "all")


@router.get("/drilldown")
def daily_work_drilldown(metric: str, from_: date | None = Query(default=None, alias="from"), to: date | None = None,
                         staff: str | None = None, client: str | None = None, page: int = Query(default=0, ge=0, le=100000),
                         page_size: int = Query(default=25, ge=1, le=100), period: Literal["all"] | None = None, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    return report_service.drilldown(db, user, metric, from_, to, staff, client, page, page_size, get_settings().agency_timezone, _now(),
                                   all_time=period == "all")


@router.get("/history")
def daily_work_history(from_: date | None = Query(default=None, alias="from"), to: date | None = None, staff: str | None = None,
                       page: int = Query(default=0, ge=0, le=100000), page_size: int = Query(default=25, ge=1, le=100),
                       period: Literal["all"] | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return report_service.history(db, user, from_, to, staff, page, page_size, get_settings().agency_timezone, _now(),
                                  all_time=period == "all")


@router.get("/mine")
def daily_work_mine(date_: date | None = Query(default=None, alias="date"), user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    zone, now = get_settings().agency_timezone, _now()
    return report_service.my_day(db, user, date_ or report_service.agency_today(now, zone), zone, now)


@router.get("/export")
def daily_work_export(from_: date | None = Query(default=None, alias="from"), to: date | None = None, staff: str | None = None,
                      client: str | None = None, period: Literal["all"] | None = None, user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
    zone, now = get_settings().agency_timezone, _now()
    all_time = period == "all"
    data = report_service.build_report(db, user, from_, to, staff, client, zone, now, all_time=all_time)
    period = data["period"]
    closes = report_service.history(db, user, date.fromisoformat(period["from"]), date.fromisoformat(period["to"]), staff, 0,
                                    report_service.HISTORY_EXPORT_MAX, zone, now, all_time=all_time)["rows"]   # the day closes of the same range
    return Response(report_service.export_workbook(data, closes),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="daily-work-report_{period["from"]}_{period["to"]}.xlsx"',
                             "Cache-Control": "private, no-store"})


@router.post("/submit")
def daily_work_submit(body: SubmitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return report_service.submit_day(db, user, body.date, body.note, get_settings().agency_timezone, _now())
