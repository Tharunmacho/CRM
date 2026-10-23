"""Monthly overview boundaries and API fields without a database connection."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db.models import Role
from app.services import attendance_hr as hr
from app.services.attendance_policy import AttendancePolicy, calculate_day
from app.services.attendance_policy_types import AttendanceStatus


def report_context(monkeypatch, *, tracking=date(2026, 10, 20), active=True, end=None):
    person = SimpleNamespace(id="employee", name="Recruiter", role=Role.staff,
                             active=active, updated_at=end)
    actor = SimpleNamespace(id="admin", role=Role.admin)
    monkeypatch.setattr(hr, "policy", lambda: AttendancePolicy())
    monkeypatch.setattr(hr, "_people", lambda *args: [person])
    monkeypatch.setattr(hr, "_load_month_inputs", lambda *args: hr.MonthInputs(
        tracking={"employee": tracking}, deactivated={"employee": end}))
    # Boundary calculations use supplied monthly inputs without a DB connection.
    database = SimpleNamespace()
    return database, actor


def day_result(day, *, status=None, now=None):
    result = calculate_day(day, [], non_working_status=status,
                           now=now or datetime(2026, 10, 22, 1, tzinfo=timezone.utc))
    result.update(shift={"start": "10:00", "end": "19:00", "breakMinutes": 60},
                  extra_ot_minutes=0)
    return result


def test_monthly_overview_uses_tracking_boundary_and_agency_today(monkeypatch):
    database, actor = report_context(monkeypatch)
    seen = []

    def recorded_day(db, person, day, at, policy, inputs=None):
        seen.append(day)
        # October 20 is present, October 21 is paid leave, and October 22 is
        # still awaiting attendance at 01:30 IST.
        result = day_result(day, status=AttendanceStatus.PAID_LEAVE if day.day == 21 else None, now=at)
        if day.day == 20:
            result.update(status="P", provisional=False, actual_covered_minutes=480)
        return result

    monkeypatch.setattr(hr, "_day", recorded_day)
    # UTC is still October 21, but the agency's local date is October 22.
    at = datetime(2026, 10, 21, 20, tzinfo=timezone.utc)
    employee = hr.month_report(database, actor, 2026, 10, at=at)["staff"][0]
    assert seen == [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    assert employee["trackingStart"] == date(2026, 10, 20)
    assert employee["days"][0]["date"] == "2026-10-20"
    assert employee["summary"]["scheduledDays"] == 3
    assert employee["summary"]["attendedDays"] == 2
    assert employee["summary"]["attendancePercentage"] == 66.7
    assert employee["summary"]["incompleteDays"] == 1
    # Existing present-days semantics remain separate from attendance credit.
    assert employee["summary"]["presentDays"] == 1


@pytest.mark.parametrize("tracking,month", [(None, 10), (date(2026, 11, 2), 10), (date(2026, 10, 20), 11)])
def test_monthly_overview_has_no_rate_before_tracking_or_for_future_month(monkeypatch, tracking, month):
    database, actor = report_context(monkeypatch, tracking=tracking)

    def unexpected_day(*args):
        pytest.fail("Untracked and future days must not be calculated")

    monkeypatch.setattr(hr, "_day", unexpected_day)
    employee = hr.month_report(database, actor, 2026, month,
                              at=datetime(2026, 10, 21, 3, 30, tzinfo=timezone.utc))["staff"][0]
    assert employee["days"] == []
    assert employee["summary"]["attendancePercentage"] is None
    assert employee["summary"]["scheduledDays"] == 0


def test_monthly_overview_stops_at_deactivation_date(monkeypatch):
    deactivated = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    database, actor = report_context(monkeypatch, active=False, end=deactivated)
    monkeypatch.setattr(hr, "_day", lambda db, person, day, at, policy, inputs=None: day_result(day, now=at))
    employee = hr.month_report(database, actor, 2026, 10, at=deactivated + timedelta(days=7))["staff"][0]
    assert [row["date"] for row in employee["days"]] == ["2026-10-20", "2026-10-21"]
    assert employee["summary"]["scheduledDays"] == employee["summary"]["absenceDays"] == 2


def test_monthly_overview_display_override_does_not_change_pay(monkeypatch):
    database, actor = report_context(monkeypatch, tracking=date(2026, 9, 30))
    at = datetime(2026, 10, 4, 1, tzinfo=timezone.utc)

    def overridden_day(db, person, day, current, policy, inputs=None):
        result = day_result(day, now=current)
        result.update(status="WO", status_override_status="WO")
        return result

    monkeypatch.setattr(hr, "_day", overridden_day)
    employee = hr.month_report(database, actor, 2026, 9, at=at)["staff"][0]
    assert employee["summary"]["attendancePercentage"] is None
    assert employee["summary"]["scheduledDays"] == 0
    assert employee["summary"]["unpaidMinutes"] == 480
