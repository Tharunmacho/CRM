"""Report/export authorization is independent of the shared candidate Data Bank."""
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import Role, User
from app.db.session import get_db
from app.services.reporting import Filters, dashboard_data, read_dashboard_data, read_reporting_data, report_data
from app.services.report_exports import REPORTS, pdf, xlsx
from app.services import staff_weekly, staff_weekly_export

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
def staff_weekly_report(from_date: date | None = None, to_date: date | None = None,
                        user: User = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):
    return staff_weekly.build_report(db, from_date, to_date, get_settings().agency_timezone)


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
def reports(filters: Filters = Depends(report_filters), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return authorized_report(db, user, filters)


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
def dashboard(staff_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in ("admin", "manager") and staff_id not in (None, "", user.id):
        raise HTTPException(403, "Only admins and managers can change which staff member the dashboard shows.")
    zone = get_settings().agency_timezone
    now = datetime.now(ZoneInfo(zone))
    return dashboard_data(read_dashboard_data(db, user, staff_id or None, zone, now), user, staff_id or None, zone, now)
