"""Report/export authorization is independent of the shared candidate Data Bank."""
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services.reporting import Filters, dashboard_data, read_reporting_data, report_data
from app.services.report_exports import REPORTS, pdf, xlsx

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
    return report_data(read_reporting_data(db), user, authorized, get_settings().agency_timezone)


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
    if user.role != "admin" and staff_id not in (None, "", user.id):
        raise HTTPException(403, "Only administrators can change dashboard staff scope.")
    return dashboard_data(read_reporting_data(db), user, staff_id or None, get_settings().agency_timezone)
