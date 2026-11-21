"""The recruiter report the owner receives automatically at the end of every working day.

Admin and MD users get one in-app notification (kind `day_report`) per agency day, built from the same numbers as the Daily CRM
Work Report screen (`daily_work_report.build_report`), without opening anything.

Rules
- It fires once per agency day, at or after DAY_END_REPORT_HOUR local time (19:00 in the agency time zone), and only for a day on
  which somebody actually worked: a recruiter has a figure, a check-in, a finished task, a lead activity or a day close. A day
  with nothing at all is recorded as checked and skipped.
- If the worker was down at the hour it delivers on the next run of the same day, and it also catches up YESTERDAY once when that
  day was never handled (never older than yesterday).
- Repeat-safe: a day is marked done with one `Day-end report` audit entry (entity_type `day_report`, target the day), and a
  recipient is skipped when it already holds the notification for that day (same kind and link). A concurrent worker is kept out by an
  advisory lock. Running it every minute, or after a restart, therefore adds nothing.
- The hour is a module constant. There is no existing settings column for it (the workflow settings table is the escalation
  checkpoint), so changing it means editing DAY_END_REPORT_HOUR.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select, text

from app.core.config import get_settings
from app.core.roles import ADMIN_LEVEL_ROLES
from app.db.models import AuditEntry, LeadActivity, Notification, Role, User
from app.services.daily_work import day_bounds

log = logging.getLogger("day_end_report")

DAY_END_REPORT_HOUR = 19             # agency-local hour from which the day's report is delivered
KIND = "day_report"
MARKER_ACTION = "Day-end report"     # one audit entry per handled day (delivered or skipped for no activity)
MARKER_ENTITY = "day_report"
LOCK_KEY = 724681930120              # next to the task-monitoring lock, never the same
DETAIL_MAX = 900
LATE_STATUSES = {"LT"}
ABSENT_STATUSES = {"A", "UL"}
PRESENT_STATUSES = {"P", "EE", "PP", "OD", "WFH"}   # worked the day (a late arrival is present too, and also counted as late)
UNDECIDED_STATUSES = {None, "MP"}                  # no verdict from the HR calculation (or missing punch): the check-in record decides
TOP_CONTRIBUTORS = 3
NAMES_SHOWN = 6


def link_for(day: date) -> str:
    return f"/reports?from={day.isoformat()}&to={day.isoformat()}"


def day_title(day: date) -> str:
    return f"Recruiter report: {day:%a} {day.day} {day:%b}"


def _names(names: list[str], limit: int = NAMES_SHOWN) -> str:
    shown = ", ".join(names[:limit])
    return f"{shown} and {len(names) - limit} more" if len(names) > limit else shown


def _attendance(db, viewer, day: date, zone: str, now: datetime, recruiters: dict[str, str]) -> dict:
    """Present / late / absent among the recruiters, the ones still checked in and who checked in at all.

    The HR month calculation (late, absent, weekly off, leave) decides when it knows the person that day; otherwise the plain
    check-in record decides: a check-in is present, none is absent."""
    from app.services import attendance_hr, staff_activity
    sessions = {row["userId"]: row for row in staff_activity.attendance_report(db, viewer, day, zone, at=now)["staff"]}
    status: dict[str, str] = {}
    try:
        with db.begin_nested():
            month = attendance_hr.month_report(db, viewer, day.year, day.month, at=now)
        for person in month["staff"]:
            found = next((d for d in person["days"] if d["date"] == day.isoformat()), None)
            if found:
                status[person["userId"]] = found["status"]
    except Exception as exc:   # the check-in record alone is enough for a one-line summary
        log.warning("Day-end report: HR attendance month unavailable (%s); using check-ins only.", type(exc).__name__)
    present, late, absent, still_in, checked_in = [], [], [], [], []
    for uid, name in recruiters.items():
        mine = (sessions.get(uid) or {}).get("attendance") or {}
        came = bool(mine.get("sessions"))
        if came:
            checked_in.append(name)
        if mine.get("status") == "checked_in":
            still_in.append(name)
        code = status.get(uid)
        if code in LATE_STATUSES:
            late.append(name)
        if code in PRESENT_STATUSES or code in LATE_STATUSES or (code in UNDECIDED_STATUSES and came):
            present.append(name)
        elif code in ABSENT_STATUSES or (code in UNDECIDED_STATUSES and not came):
            absent.append(name)   # weekly off, holiday and approved leave are neither present nor absent
    return {"present": present, "late": late, "absent": absent, "stillIn": still_in, "checkedIn": checked_in}


def compose(db, viewer, day: date, zone: str, now: datetime) -> dict | None:
    """{title, detail, link, day, ...figures} for one agency day, or None when nobody did anything that day."""
    from app.services import daily_work_report
    report = daily_work_report.build_report(db, viewer, day, day, None, None, zone, now)
    recruiters = {uid: name for uid, name in db.execute(
        select(User.id, User.name).where(User.active.is_(True), User.role == Role.staff).order_by(User.name, User.id))}
    rows = [r for r in report["staff"] if r["id"] in recruiters]
    totals = {metric["key"]: metric["value"] for metric in report["metrics"]}
    done_tasks = {r["id"]: r.get("tasks", {}).get("done", 0) for r in rows}
    start, end = day_bounds(day, zone)
    lead_activity = db.scalar(select(exists().where(
        LeadActivity.user_id.in_(list(recruiters)), LeadActivity.created_at >= start, LeadActivity.created_at < end))) if recruiters else False
    attendance = _attendance(db, viewer, day, zone, now, recruiters)
    closed = [r for r in rows if r["submitted"]]
    worked = (any(r["total"] for r in rows) or any(done_tasks.values()) or bool(attendance["checkedIn"]) or bool(lead_activity)
              or bool(closed))
    if not worked:
        return None

    lines = [
        f"Prescreened {totals.get('prescreened', 0)}, forwarded {totals.get('forwarded', 0)}, shortlisted {totals.get('shortlisted', 0)}, "
        f"interviews {totals.get('interviews', 0)}, offers {totals.get('offers', 0)}, visas {totals.get('visas', 0)}, "
        f"travelled {totals.get('travelled', 0)}; {totals.get('leadsContacted', 0)} leads contacted."]
    compliance = report.get("compliance") or {}
    expected = compliance.get("expected", 0)
    missing = [entry["name"] for entry in compliance.get("missing", [])]
    if expected:
        if missing:
            lines.append(f"{compliance['submitted']} of {expected} recruiters closed their day. Not closed: {_names(missing)}.")
        else:
            lines.append(f"All {expected} recruiters closed their day.")
    if recruiters:
        part = f"Attendance: {len(attendance['present'])} present"
        if attendance["late"]:
            part += f", {len(attendance['late'])} late ({_names(attendance['late'], 3)})"
        part += f", {len(attendance['absent'])} absent"
        if attendance["absent"]:
            part += f" ({_names(attendance['absent'], 3)})"
        part += "."
        if attendance["stillIn"]:
            part += f" Still logged in: {_names(attendance['stillIn'], 3)}."
        lines.append(part)
    top = [f"{r['name']} {r['total']}" for r in rows if r["total"] > 0][:TOP_CONTRIBUTORS]
    nothing = [r["name"] for r in rows if r["total"] == 0 and not done_tasks.get(r["id"])]
    if top:
        lines.append(f"Top contributors: {', '.join(top)}.")
    if nothing:
        lines.append(f"Nothing recorded: {_names(nothing)}.")
    detail = "\n".join(lines)
    if len(detail) > DETAIL_MAX:
        detail = detail[:DETAIL_MAX - 1].rstrip() + "…"
    return {"title": day_title(day), "detail": detail, "link": link_for(day), "date": day.isoformat(),
            "totals": totals, "closed": len(closed), "expected": expected, "notClosed": missing,
            "attendance": {key: list(value) for key, value in attendance.items()}}


def _deliver_day(db, viewer, admins: list[str], day: date, zone: str, now: datetime) -> int:
    from app.services.workflow_scheduler import notify
    link = link_for(day)
    have = set(db.scalars(select(Notification.user_id).where(Notification.kind == KIND, Notification.link_to == link)))
    message = compose(db, viewer, day, zone, now)
    sent = 0
    if message:
        recipients = [uid for uid in admins if uid not in have]
        notify(db, recipients, KIND, message["title"], message["detail"], link)
        sent = len(recipients)
    db.add(AuditEntry(actor_id=None, action=MARKER_ACTION, target=day.isoformat(), entity_type=MARKER_ENTITY,
                      meta={"date": day.isoformat(), "sent": sent, "skipped": None if message else "no activity"}, at=now))
    db.flush()
    return sent


def deliver_day_end_report(db, *, now: datetime | None = None, agency_timezone: str | None = None) -> dict:
    """One small pass; the caller commits. Returns {"sent": notifications added, "days": [days handled]}."""
    now = now or datetime.now(timezone.utc)
    zone_name = agency_timezone or get_settings().agency_timezone
    local = now.astimezone(ZoneInfo(zone_name))
    today = local.date()
    candidates = [today - timedelta(days=1)] + ([today] if local.time() >= time(DAY_END_REPORT_HOUR) else [])
    handled = set(db.scalars(select(AuditEntry.target).where(
        AuditEntry.action == MARKER_ACTION, AuditEntry.entity_type == MARKER_ENTITY,
        AuditEntry.target.in_([d.isoformat() for d in candidates]))))
    pending = [d for d in candidates if d.isoformat() not in handled]
    result = {"sent": 0, "days": []}
    if not pending or not db.scalar(text(f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})")):
        return result
    # Re-read under the lock: another worker may have finished while this one waited.
    handled = set(db.scalars(select(AuditEntry.target).where(
        AuditEntry.action == MARKER_ACTION, AuditEntry.entity_type == MARKER_ENTITY,
        AuditEntry.target.in_([d.isoformat() for d in pending]))))
    pending = [d for d in pending if d.isoformat() not in handled]
    admins = list(db.scalars(select(User).where(User.active.is_(True), User.role.in_(ADMIN_LEVEL_ROLES)).order_by(User.id)))
    if not pending or not admins:
        return result
    for day in pending:
        result["sent"] += _deliver_day(db, admins[0], [a.id for a in admins], day, zone_name, now)
        result["days"].append(day.isoformat())
    return result
