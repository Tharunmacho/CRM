"""Reports > Attendance: the reading of a day and the month's Excel file (no database; every name is invented)."""
from datetime import date, datetime, timezone
from io import BytesIO
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest

from app.services import attendance_month_export as export

ZONE = ZoneInfo("Asia/Kolkata")
SHIFT = {"start": "10:00", "end": "19:00", "breakMinutes": 60}


def at(day: int, hour: int, minute: int = 0, month: int = 11):
    """A local (Kolkata) time on the given day, as the aware instant the policy report carries."""
    return datetime(2026, month, day, hour, minute, tzinfo=ZONE).astimezone(timezone.utc)


def day(number: int, status: str, check_in=None, check_out=None, worked=0, late=0):
    return {"date": f"2026-11-{number:02d}", "status": status, "checkIn": check_in, "checkOut": check_out, "shift": SHIFT,
            "workedMinutes": worked, "lateMinutes": late, "provisional": False}


TODAY = "2026-11-20"


@pytest.mark.parametrize("record,mark", [
    (day(2, "P", at(2, 9, 55), at(2, 19, 5), 480), "P"),
    (day(3, "P", at(3, 10, 0), at(3, 19, 0), 480), "P"),                       # on the dot is on time
    (day(4, "P", at(4, 10, 20), at(4, 19, 40), 480), "LT"),                    # a full day in the flexible window, but after the start
    (day(5, "LT", at(5, 11, 0), at(5, 19, 0), 420, 60), "LT"),
    (day(6, "A"), "A"),
    (day(7, "WO"), "WO"),
    (day(8, "H"), "H"),
    (day(9, "PL"), "L"),
    (day(10, "UL"), "L"),
    (day(11, "MP", at(11, 10, 0)), "M"),                                       # logged in, never out, a finished day
    (day(12, "MP"), "A"),                                                      # nothing at all on a finished day
    (day(16, "A", at(16, 10, 0)), "M"),                                        # the policy gave up on a half-punched day: still a missed check-out
    (day(20, "MP", at(20, 9, 50)), "P"),                                       # today and still in: here, nothing missed yet
    (day(20, "MP"), "N"),                                                      # today, not clocked in yet: nothing to say
    (day(13, "OD"), "P"),
    (day(14, "WFH"), "P"),
    (day(15, "EE", at(15, 9, 30), at(15, 16, 0), 330), "P"),
])
def test_a_day_is_read_the_same_way_everywhere(record, mark):
    assert export.day_mark(record, TODAY, ZONE) == mark


def test_late_minutes_count_whole_minutes_after_the_shift_start_in_agency_time():
    assert export.late_minutes(day(1, "P", at(1, 10, 0)), ZONE) == 0
    assert export.late_minutes(day(1, "P", at(1, 10, 1)), ZONE) == 1
    assert export.late_minutes(day(1, "P", at(1, 10, 45)), ZONE) == 45
    assert export.late_minutes(day(1, "P"), ZONE) == 0
    assert export.late_minutes(day(1, "OD", at(1, 12, 0)), ZONE) == 0          # away on duty: no arrival to be late for
    early_shift = {**day(1, "P", at(1, 9, 45)), "shift": {"start": "09:30", "end": "18:30"}}
    assert export.late_minutes(early_shift, ZONE) == 15                         # the person's own shift, not a fixed hour


def test_the_summary_adds_up_the_days():
    days = [day(2, "P", at(2, 9, 55), at(2, 19, 5), 480), day(3, "LT", at(3, 10, 30), at(3, 19, 30), 450, 30), day(4, "A"), day(5, "WO"),
            day(6, "PL"), day(9, "MP", at(9, 10, 0)), day(10, "P", at(10, 10, 0), at(10, 18, 0), 420)]
    facts = export.summarize(days, TODAY, ZONE)
    assert facts == {"present": 4, "late": 1, "absent": 1, "leave": 1, "missed": 1, "off": 1, "totalMinutes": 1350, "averageMinutes": 450}


def month_report():
    return {"year": 2026, "month": 11, "timezone": "Asia/Kolkata", "staff": [
        {"userId": "u2", "name": "Zed Tester", "role": "manager", "active": True, "summary": {"attendancePercentage": 100.0},
         "days": [day(2, "P", at(2, 10, 5), at(2, 19, 0), 480)]},
        {"userId": "u1", "name": "=Asha Test", "role": "staff", "active": True, "summary": {"attendancePercentage": None},
         "days": [day(2, "A"), day(3, "P", at(3, 9, 50), at(3, 19, 0), 480)]},
        {"userId": "u0", "name": "Administrator", "role": "admin", "active": True, "summary": {}, "days": [day(2, "P", at(2, 9, 0), at(2, 19, 0), 480)]},
    ]}


def sheets(payload: bytes):
    with ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        return {name: archive.read(name).decode() for name in names}


def test_the_workbook_has_the_overview_and_the_daily_record_and_never_an_administrator():
    files = sheets(export.xlsx_bytes(month_report(), date(2026, 11, 20)))
    workbook = files["xl/workbook.xml"]
    assert 'name="Month overview"' in workbook and 'name="Daily record"' in workbook
    first, second = files["xl/worksheets/sheet1.xml"], files["xl/worksheets/sheet2.xml"]
    for header in export.SUMMARY_COLUMNS:
        assert header in first
    for header in export.DAILY_COLUMNS:
        assert header in second
    assert "Administrator" not in first and "Administrator" not in second
    assert first.index("Asha Test") < first.index("Zed Tester")                      # ordered by name
    assert "&#x27;=Asha Test" in first        # a name that starts like a formula is kept as text
    assert "<f>" not in first and "<f>" not in second
    assert "Recruiter" in first and ">MD<" in first
    assert "10:05" in second and "19:00" in second and "Late" in second         # times in agency time


def test_a_month_with_nobody_is_a_header_only_file():
    files = sheets(export.xlsx_bytes({"year": 2026, "month": 11, "timezone": "Asia/Kolkata", "staff": []}, date(2026, 11, 20)))
    assert files["xl/worksheets/sheet1.xml"].count("<row ") == 1
    assert export.export_name(2026, 3) == "attendance-2026-03.xlsx"
