"""Reference attendance arithmetic with synthetic timestamps and no database."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.attendance_policy import attendance_overview, calculate_day, calculate_month, required_shift_minutes
from app.services.attendance_policy_types import AttendanceStatus, Shift

DAY = date(2026, 10, 5)
ZONE = ZoneInfo('Asia/Kolkata')


def moment(hour, minute=0, day=DAY):
    return datetime.combine(day, time(hour, minute), ZONE)


def punches(start, end):
    return [{'occurred_at': start, 'action': 'check_in'}, {'occurred_at': end, 'action': 'check_out'}]


@pytest.mark.parametrize('days', [[], [{'status': 'WO'}, {'status': 'H'}]])
def test_overview_has_no_rate_when_there_are_no_scheduled_days(days):
    summary = attendance_overview(days)
    assert summary['attendancePercentage'] is None
    assert summary['attendedDays'] == summary['scheduledDays'] == 0


def test_overview_matches_reference_paid_leave_and_exception_rules():
    # Paid leave is attendance credit. Unpaid leave is an absence, while
    # weekly offs and holidays are excluded from the rate altogether.
    rows = [{'status': status} for status in ['P', 'LT', 'EE', 'PP', 'OD', 'WFH', 'PL', 'UL', 'A', 'MP', 'WO', 'H']]
    summary = attendance_overview(rows)
    assert summary['attendedDays'] == 7
    assert summary['scheduledDays'] == 10
    assert summary['attendancePercentage'] == 70.0
    assert summary['absenceDays'] == 2
    assert summary['lateDays'] == 1
    assert summary['incompleteDays'] == 1


def test_overview_provisional_checkin_counts_until_missing_punch_deadline():
    start = [{'occurred_at': moment(10), 'action': 'check_in'}]
    underway = calculate_day(DAY, start, now=moment(12))
    awaiting = calculate_day(DAY + timedelta(days=1), [], now=moment(12, day=DAY + timedelta(days=1)))
    summary = attendance_overview([underway, awaiting])
    assert summary['attendancePercentage'] == 50.0
    assert summary['attendedDays'] == 1
    assert summary['incompleteDays'] == 2
    assert summary['absenceDays'] == 0

    expired = calculate_day(DAY, start, now=moment(20, day=DAY + timedelta(days=2)))
    summary = attendance_overview([expired])
    assert summary['attendancePercentage'] == 0.0
    assert summary['absenceDays'] == 1
    assert summary['incompleteDays'] == 0


def test_overview_missing_checkin_does_not_count_as_attended():
    missing = calculate_day(DAY, [{'occurred_at': moment(19), 'action': 'check_out'}], now=moment(20))
    assert missing['provisional']
    assert attendance_overview([missing])['attendedDays'] == 0


def test_overview_status_override_cannot_count_nonworking_day_in_numerator():
    row = calculate_day(DAY, [{'occurred_at': moment(10), 'action': 'check_in'}], now=moment(12))
    row['status'] = 'H'  # A reasoned display correction can retain provisional punches.
    summary = attendance_overview([row, {'status': 'P'}])
    assert summary['attendedDays'] == summary['scheduledDays'] == 1
    assert summary['attendancePercentage'] == 100.0


def test_overview_counts_late_result_not_flexible_full_workday_as_exception():
    row = calculate_day(DAY, punches(moment(10, 30), moment(19, 30)), now=moment(21))
    assert row['late_minutes'] == 30 and row['status'] == 'P'
    assert attendance_overview([row])['lateDays'] == 0


@pytest.mark.parametrize('start,end', [((10, 0), (19, 0)), ((9, 30), (18, 30)), ((10, 30), (19, 30))])
def test_nine_hours_inside_flex_window_is_eight_payable_hours(start, end):
    row = calculate_day(DAY, punches(moment(*start), moment(*end)), now=moment(21))
    assert row['status'] == 'P'
    assert row['required_shift_minutes'] == row['actual_covered_minutes'] == 480
    assert calculate_month([row])[0]['unpaid_minutes'] == 0


def test_monthly_grace_is_chronological_and_does_not_erase_shortfall_after_sixty_minutes():
    rows = []
    for offset, late in enumerate([20, 30, 40]):
        day = DAY + timedelta(days=offset)
        rows.append(calculate_day(day, punches(moment(10, late, day), moment(19, day=day)), now=moment(21, day=day)))
    month = calculate_month(reversed(rows))
    assert [row['grace_minutes_applied'] for row in month] == [20, 30, 10]
    assert [row['unpaid_minutes'] for row in month] == [0, 0, 30]
    assert [row['permission_minutes_remaining'] for row in month] == [40, 10, 0]


def test_approved_permission_consumes_only_actual_shortfall_and_never_creates_negative_unpaid():
    first = calculate_day(DAY, punches(moment(10, 30), moment(19, 15)),
        approved_permissions=[{'kind': 'late', 'requested_minutes': 30}], now=moment(21))
    next_day = DAY + timedelta(days=1)
    second = calculate_day(next_day, punches(moment(10, 50, next_day), moment(19, day=next_day)), now=moment(21, day=next_day))
    result = calculate_month([first, second])
    assert result[0]['approved_permission_minutes'] == 15
    assert result[0]['unpaid_minutes'] == 0
    assert result[0]['permission_minutes_remaining'] == 45
    assert result[1]['unpaid_minutes'] == 5


def test_incomplete_punch_has_two_day_correction_window_before_full_absence():
    incomplete = [{'occurred_at': moment(10), 'action': 'check_in'}]
    boundary = moment(19, day=DAY + timedelta(days=2))
    provisional = calculate_month([calculate_day(DAY, incomplete, now=boundary)])[0]
    expired = calculate_month([calculate_day(DAY, incomplete, now=boundary + timedelta(seconds=1))])[0]
    assert provisional['status'] == 'MP' and provisional['provisional'] is True
    assert provisional['unpaid_minutes'] == 0
    assert expired['status'] == 'A' and expired['unpaid_minutes'] == 480
    assert expired['permission_minutes_remaining'] == 60
    assert expired['late_unpaid_minutes'] == 0


@pytest.mark.parametrize('status,unpaid', [(AttendanceStatus.WEEKLY_OFF, 0), (AttendanceStatus.HOLIDAY, 0), (AttendanceStatus.PAID_LEAVE, 0), (AttendanceStatus.UNPAID_LEAVE, 480)])
def test_non_working_days_and_full_unpaid_leave_do_not_consume_monthly_grace(status, unpaid):
    row = calculate_month([calculate_day(DAY, [], non_working_status=status, now=moment(21))])[0]
    assert row['unpaid_minutes'] == unpaid
    assert row['permission_minutes_remaining'] == 60
    assert row['late_unpaid_minutes'] == 0


def test_overnight_shift_uses_next_day_and_the_same_break_rule():
    shift = Shift(start=time(22), end=time(7))
    row = calculate_day(DAY, punches(moment(22), moment(7, day=DAY + timedelta(days=1))),
        shift=shift, now=moment(9, day=DAY + timedelta(days=1)))
    assert required_shift_minutes(shift, day=DAY) == 480
    assert row['actual_covered_minutes'] == 480
    assert row['status'] == 'P'


def test_ambiguous_timestamps_are_rejected():
    with pytest.raises(ValueError, match='timezone'):
        calculate_day(DAY, [], now=datetime(2026, 10, 5, 21))
