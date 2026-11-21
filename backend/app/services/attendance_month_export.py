"""Reports > Attendance, the month as an Excel file: one line per recruiter (and the MD), and every day behind it.

Built from the month report of the attendance policy (`attendance_hr.month_report`), the same record the screen's grid reads, with the same
reading of a day (`day_mark`):

* P present, LT late (first login after the shift start; still a day worked), M missed check-out (logged in, never logged out on a finished
  day, even once the policy has turned it into absent; still a day worked), A absent (no login on a finished day), L leave (paid or unpaid), WO weekly off, H holiday.
* Hours are the policy's worked minutes (first login to last logout inside the shift window, less the break).
* Average hours: total hours over the days with hours on them.
Administrator accounts are never listed (they do not clock in), whoever asks.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services import lead_xlsx

HIDDEN_ROLES = ("admin",)
SUMMARY_SHEET = "Month overview"
DAILY_SHEET = "Daily record"
SUMMARY_COLUMNS = ("Recruiter", "Role", "Present days", "Late days", "Absent days", "Leave days", "Missed check-outs", "Weekly offs and holidays",
                   "Total hours", "Average hours per day", "Attendance %")
DAILY_COLUMNS = ("Recruiter", "Date", "Day", "Mark", "Status", "Shift", "Login", "Logout", "Hours worked", "Late by (minutes)")
STATUS_WORDS = {"P": "Present", "LT": "Late", "EE": "Early exit", "PP": "Paid permission", "OD": "Official duty", "WFH": "Work from home",
                "PL": "Paid leave", "UL": "Unpaid leave", "A": "Absent", "MP": "Missed check-in or out", "WO": "Weekly off", "H": "Holiday"}
MARK_WORDS = {"P": "Present", "LT": "Late", "M": "Missed check-out", "A": "Absent", "L": "Leave", "WO": "Weekly off", "H": "Holiday", "N": "Not yet"}
_PRESENT_CODES = {"P", "LT", "EE", "PP", "OD", "WFH"}
_NO_LATE = {"OD", "WFH"}   # working away from the office has no arrival time to be late for


def _stamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def late_minutes(day: dict, zone: ZoneInfo) -> int:
    """Minutes the first login came after the shift start (whole minutes; 0 when on time, when there is no login, or on duty away)."""
    at = _stamp(day.get("checkIn"))
    if at is None or day.get("status") in _NO_LATE:
        return 0
    start = str((day.get("shift") or {}).get("start") or "10:00")[:5]
    try:
        hours, minutes = (int(part) for part in start.split(":"))
    except ValueError:
        hours, minutes = 10, 0
    local = at.astimezone(zone)
    return max(0, int(day.get("lateMinutes") or 0), local.hour * 60 + local.minute - (hours * 60 + minutes))


def day_mark(day: dict, today: str, zone: ZoneInfo) -> str:
    """P, LT, M, A, L, WO, H or N (a day with nothing to say yet: today before the first login)."""
    status = day.get("status")
    when = str(day.get("date"))[:10]
    if status in ("WO", "H"):
        return status
    if status in ("PL", "UL"):
        return "L"
    has_in = _stamp(day.get("checkIn")) is not None
    if status == "A":
        # The policy turns a half-punched day into "absent" once its fix-up window has closed; the person was there, so it stays a missed check-out
        return "M" if has_in and _stamp(day.get("checkOut")) is None else "A"
    if status == "MP":
        if not has_in:
            return "A" if when < today else "N"
        return "P" if when >= today and _stamp(day.get("checkOut")) is None else "M"
    if status == "LT" or (status in _PRESENT_CODES and late_minutes(day, zone) > 0):
        return "LT"
    return "P" if status in _PRESENT_CODES else "N"


def summarize(days: list[dict], today: str, zone: ZoneInfo) -> dict:
    marks = [day_mark(day, today, zone) for day in days]
    minutes = [max(0, int(day.get("workedMinutes") or 0)) for day in days]
    worked_days = sum(1 for value in minutes if value > 0)
    total = sum(minutes)
    return {"present": sum(m in ("P", "LT", "M") for m in marks), "late": marks.count("LT"), "absent": marks.count("A"), "leave": marks.count("L"),
            "missed": marks.count("M"), "off": marks.count("WO") + marks.count("H"), "totalMinutes": total,
            "averageMinutes": round(total / worked_days) if worked_days else 0}


def status_word(mark: str, day: dict) -> str:
    """Present days keep the policy's finer words (Official duty, Paid permission...), leave says paid or unpaid, the rest is the mark's."""
    if mark in ("P", "L"):
        return STATUS_WORDS.get(day.get("status"), MARK_WORDS[mark])
    return MARK_WORDS.get(mark, "")


def _cell(value):
    """Keep a spreadsheet from running text that starts like a formula (and drop control characters that corrupt the sheet's XML)."""
    if isinstance(value, str):
        value = "".join(ch for ch in value if ord(ch) >= 32)
        if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
    return value


def _hours(minutes: int) -> float:
    return round(minutes / 60, 2)


def _clock(value, zone: ZoneInfo) -> str:
    at = _stamp(value)
    return at.astimezone(zone).strftime("%H:%M") if at else ""


def export_name(year: int, month: int) -> str:
    return f"attendance-{year}-{month:02d}.xlsx"


def xlsx_bytes(report: dict, today: date) -> bytes:
    """The workbook for `attendance_hr.month_report`'s answer; `today` is the agency's current day."""
    zone = ZoneInfo(report.get("timezone") or "UTC")
    day_text = today.isoformat()
    people = [person for person in report["staff"] if str(person.get("role")) not in HIDDEN_ROLES]
    summary, daily = [list(SUMMARY_COLUMNS)], [list(DAILY_COLUMNS)]
    for person in sorted(people, key=lambda p: (str(p["name"]).lower(), p["userId"])):
        days = sorted(person["days"], key=lambda d: str(d["date"]))
        facts = summarize(days, day_text, zone)
        percent = (person.get("summary") or {}).get("attendancePercentage")
        summary.append([_cell(v) for v in (person["name"], "MD" if str(person["role"]) == "manager" else "Recruiter", facts["present"], facts["late"],
                                          facts["absent"], facts["leave"], facts["missed"], facts["off"], _hours(facts["totalMinutes"]),
                                          _hours(facts["averageMinutes"]), percent if percent is not None else "")])
        for day in days:
            mark = day_mark(day, day_text, zone)
            late = late_minutes(day, zone)
            shift = day.get("shift") or {}
            daily.append([_cell(v) for v in (person["name"], date.fromisoformat(str(day["date"])[:10]), date.fromisoformat(str(day["date"])[:10]).strftime("%a"),
                                            mark, status_word(mark, day),
                                            f"{str(shift.get('start', ''))[:5]} - {str(shift.get('end', ''))[:5]}" if shift and mark not in ("WO", "H") else "",
                                            _clock(day.get("checkIn"), zone), _clock(day.get("checkOut"), zone),
                                            _hours(int(day.get("workedMinutes") or 0)) if day.get("checkIn") or day.get("workedMinutes") else "",
                                            late if late else "")])
    return lead_xlsx.build_workbook([
        {"name": SUMMARY_SHEET, "rows": summary, "widths": [28, 11, 12, 11, 12, 11, 17, 24, 12, 20, 14], "header": True, "freeze": True, "filter": True},
        {"name": DAILY_SHEET, "rows": daily, "widths": [28, 14, 7, 7, 24, 15, 9, 9, 13, 18], "header": True, "freeze": True, "filter": True}])
