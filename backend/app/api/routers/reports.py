"""Report/export authorization is independent of the shared candidate Data Bank."""
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import Role, User
from app.db.session import get_db
from app.services.encoded_response import Encoded
from app.services.response_cache import cached, scope_of
from app.services.reporting import Filters, dashboard_data, read_dashboard_data, read_reporting_data, report_data
from app.services.report_exports import REPORTS, pdf, xlsx
from app.services import lead_metrics, staff_weekly, staff_weekly_export
from app.services.parallel_reads import parallel_reads

# Report and dashboard numbers are derived from nearly every business table; the change feed adds the "reports" topic to
# any commit that touches one, so a single topic covers them all. The time limit covers what depends on the clock
# (today, overdue) and what the feed cannot see.
REPORT_TOPICS = ("reports",)
# The dashboard and the weekly staff report also carry lead sheet numbers (crm_leads / crm_lead_activities feed the
# `leads` topic), so a lead call or assignment drops their cached copies at once.
LEAD_REPORT_TOPICS = ("reports", "leads")
REPORT_SECONDS = 30
DASHBOARD_SECONDS = 20

router = APIRouter(prefix="/api/reports", tags=["reports"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def report_filters(from_date: date | None = None, to_date: date | None = None,
                   client_id: str | None = None, order_id: str | None = None,
                   designation_id: str | None = None, staff_id: str | None = None):
    today = datetime.now(ZoneInfo(get_settings().agency_timezone)).date()
    return Filters(from_date or today - timedelta(days=89), to_date or today,
                   client_id or None, order_id or None, designation_id or None, staff_id or None)


def authorized_report(db, user, filters):
    authorized = filters.authorize(user)  # Reject scope tampering before any data reads.
    zone = get_settings().agency_timezone
    return report_data(read_reporting_data(db, authorized, zone), user, authorized, zone)


# The weekly staff report is for the admin only. These routes come before `/{report}/export` so that path never
# captures "staff-weekly".
@router.get("/staff-weekly")
def staff_weekly_report(request: Request, from_date: date | None = None, to_date: date | None = None,
                        user: User = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):
    zone = get_settings().agency_timezone
    return cached(("staff-weekly", scope_of(db), from_date, to_date, zone), LEAD_REPORT_TOPICS, REPORT_SECONDS,
                  lambda: Encoded.of(staff_weekly.build_report(db, from_date, to_date, zone))).response(request)


@router.get("/staff-weekly/export")
def staff_weekly_export_file(format: Literal["xlsx", "csv"], from_date: date | None = None, to_date: date | None = None,
                             user: User = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):
    data = staff_weekly.build_report(db, from_date, to_date, get_settings().agency_timezone)
    payload = staff_weekly_export.xlsx(data) if format == "xlsx" else staff_weekly_export.csv_bytes(data)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if format == "xlsx" else "text/csv; charset=utf-8"
    return Response(payload, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="staff-weekly-{data["range"]["from"]}-{data["range"]["to"]}.{format}"',
        "Cache-Control": "private, no-store",
    })


@router.get("/staff-weekly/{staff_id}")
def staff_weekly_detail(staff_id: str, from_date: date | None = None, to_date: date | None = None,
                        user: User = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):
    data = staff_weekly.build_report(db, from_date, to_date, get_settings().agency_timezone, staff_id=staff_id, with_candidates=True)
    if not data["staff"]:
        raise HTTPException(404, "Staff member not found.")
    return data


@router.get("")
def reports(request: Request, filters: Filters = Depends(report_filters), user: User = Depends(current_user), db: Session = Depends(get_db)):
    # What a person may see depends on their role and id, so both are in the key; the scope check runs inside the build.
    key = ("reports", scope_of(db), user.id, user.role.value, repr(filters))
    return cached(key, REPORT_TOPICS, REPORT_SECONDS, lambda: Encoded.of(authorized_report(db, user, filters))).response(request)


@router.get("/{report}/export")
def export_report(report: str, format: Literal["xlsx", "pdf"], filters: Filters = Depends(report_filters),
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    if report not in REPORTS:
        raise HTTPException(404, "Report not found.")
    data = authorized_report(db, user, filters)
    payload = xlsx(report, data) if format == "xlsx" else pdf(report, data)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if format == "xlsx" else "application/pdf"
    return Response(payload, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{report}-{filters.from_date}-{filters.to_date}.{format}"',
        "Cache-Control": "private, no-store",
    })


@dashboard_router.get("")
def dashboard(request: Request, staff_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in ("admin", "manager") and staff_id not in (None, "", user.id):
        raise HTTPException(403, "Only admins and managers can change which staff member the dashboard shows.")
    zone = get_settings().agency_timezone

    def build() -> Encoded:
        now = datetime.now(ZoneInfo(zone))
        # The pipeline read and the lead sheet numbers are independent: they run side by side (one statement each).
        data, leads = parallel_reads(db, lambda session: read_dashboard_data(session, user, staff_id or None, zone, now),
                                     lambda session: lead_metrics.dashboard_leads(session, user, staff_id or None, zone, now))
        result = dashboard_data(data, user, staff_id or None, zone, now)
        if leads is not None:
            result["leads"] = leads  # additive key: older clients ignore it
        return Encoded.of(result)

    # One entry per person and chosen staff member: the dashboard shows "my" tasks and role-scoped numbers.
    return cached(("dashboard", scope_of(db), user.id, user.role.value, staff_id or None, zone), LEAD_REPORT_TOPICS,
                  DASHBOARD_SECONDS, build).response(request)


@dashboard_router.get("/leads")
def dashboard_lead_list(card: Literal["newToday", "notContacted", "followUpsDue", "resumesReceived", "converted", "unassigned"],
                        period: Literal["week", "month", "quarter", "year", "all"] = "month", staff_id: str | None = None,
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The few leads behind one dashboard lead card. Same scope rule as the dashboard itself: staff see their own leads."""
    if user.role not in ("admin", "manager") and staff_id not in (None, "", user.id):
        raise HTTPException(403, "Only admins and managers can change which staff member the dashboard shows.")
    zone = get_settings().agency_timezone
    now = datetime.now(ZoneInfo(zone))
    key = ("dashboard-leads", scope_of(db), user.id, user.role.value, staff_id or None, card, period, zone)
    return cached(key, ("leads",), 10, lambda: lead_metrics.dashboard_lead_items(db, user, staff_id or None, card, period, zone, now))
