"""Shared assignment availability, without changing manual on-leave switches."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AttendanceCalendarDay


def unavailable_staff_ids(db, *, day=None, at=None):
    """Employees with an effective paid/unpaid leave entry on the agency day.

    A finalized approval creates this calendar entry in the same transaction.
    Explicit Admin calendar corrections therefore remain authoritative. Pending
    requests and browser presence never make somebody unavailable. Callers also
    apply their existing active/role/manual-on-leave eligibility rules.
    """
    if day is None:
        now = at or datetime.now(timezone.utc)
        day = now.astimezone(ZoneInfo(get_settings().agency_timezone)).date()
    return set(db.scalars(select(AttendanceCalendarDay.user_id).where(
        AttendanceCalendarDay.attendance_date == day,
        AttendanceCalendarDay.status.in_(["PL", "UL"]),
    )))
