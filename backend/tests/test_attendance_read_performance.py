"""Read parity and bounded query counts on a disposable loopback PostgreSQL DB."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from time import perf_counter

import pytest
from sqlalchemy import event, func, select

from app.db import models as m
from app.services import attendance_hr as hr
from test_database_naming import schema_database
from test_workflow_scheduler import workspace

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


@contextmanager
def measured(db):
    result = {"queries": 0}
    def count(*args):
        result["queries"] += 1
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    started = perf_counter()
    try:
        yield result
    finally:
        result["ms"] = round((perf_counter() - started) * 1000, 1)
        event.remove(engine, "before_cursor_execute", count)


def seed_month(db):
    for number in range(6):
        uid = f"perf-{number}"
        db.add(m.User(id=uid, name=uid, email=uid + "@example.test", role=m.Role.staff,
            active=number != 0, updated_at=NOW))
    db.flush()
    for number in range(6):
        uid = f"perf-{number}"
        # Local midnight boundary starts tracking on 1 October, although UTC is September.
        db.add(m.StaffAttendanceSession(user_id=uid, checked_in_at=datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc),
            checked_out_at=datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc), checkin_request_key=uid + "-first"))
        for day in (3, 5, 8, 13, 16, 20):
            start = datetime(2026, 10, day, 5, 30, tzinfo=timezone.utc)
            db.add(m.StaffAttendanceSession(user_id=uid, checked_in_at=start,
                checked_out_at=start + timedelta(hours=9), checkin_request_key=f"{uid}-{day}"))
        # Explicit overnight duty takes precedence over effective-dated timings.
        db.add(m.AttendanceWorkTiming(user_id=uid, effective_from=date(2026, 9, 1),
            shift={"start": "10:00", "end": "19:00", "breakMinutes": 60}, reason="Baseline", actor_id="admin"))
        db.add(m.AttendanceWorkTiming(user_id=uid, effective_from=date(2026, 10, 15),
            shift={"start": "11:00", "end": "20:00", "breakMinutes": 60}, reason="New timing", actor_id="admin"))
        db.add(m.AttendanceDutyPlan(user_id=uid, attendance_date=date(2026, 10, 4),
            shift={"start": "22:00", "end": "07:00", "breakMinutes": 60}, reason="Overnight", actor_id="admin"))
        start = datetime(2026, 10, 4, 16, 30, tzinfo=timezone.utc)
        db.add(m.StaffAttendanceSession(user_id=uid, checked_in_at=start, checked_out_at=start + timedelta(hours=9),
            checkin_request_key=uid + "-overnight"))
        db.add(m.AttendanceWeeklyOff(user_id=uid, week_start=date(2026, 10, 5), day="friday"))
        db.add(m.AttendanceCalendarDay(user_id=uid, attendance_date=date(2026, 10, 12), status="PL",
            reason="Approved leave", actor_id="admin"))
        for day, kind, status, minutes in ((3, "late", "approved", 30), (5, "extra_ot", "approved", 45), (8, "late", "pending", 60)):
            db.add(m.AttendanceHRRequest(user_id=uid, request_key=f"{uid}-{kind}-{day}",
                attendance_date=date(2026, 10, day), kind=kind, status=status, minutes=minutes,
                reason="Test permission", approvals={}, created_at=NOW))
        db.add(m.AttendanceAdjustment(user_id=uid, attendance_date=date(2026, 10, 6), kind="status_override",
            status="WO", reason="Display correction only", actor_id="admin", created_at=NOW))
        db.add(m.AttendanceAdjustment(user_id=uid, attendance_date=date(2026, 10, 7), kind="time_recovery",
            minutes=30, reason="Recovery", actor_id="admin", created_at=NOW))
        for action, hour in (("check_in", 4), ("check_out", 13)):
            db.add(m.AttendanceAdjustment(user_id=uid, attendance_date=date(2026, 10, 14), kind="add_punch",
                action=action, occurred_at=datetime(2026, 10, 14, hour, 30, tzinfo=timezone.utc),
                reason="Missing punch", actor_id="admin", created_at=NOW))
    db.add(m.AuditEntry(actor_id="admin", action="User deactivated", entity_type="user", entity_id="perf-0",
        at=datetime(2026, 10, 18, 12, tzinfo=timezone.utc)))
    db.commit()


def test_month_report_preserves_every_day_and_bounds_queries_for_team(workspace, monkeypatch):
    with workspace() as db:
        seed_month(db)
        actor = db.get(m.User, "admin")
        with measured(db) as batch:
            result = hr.month_report(db, actor, 2026, 10, at=NOW)
        assert batch["queries"] == 10  # Includes users and the inactive-user audit aggregate.
        assert next(row for row in result["staff"] if row["userId"] == "perf-0")["days"][-1]["date"] == "2026-10-18"
        with measured(db) as single:
            hr.month_report(db, actor, 2026, 10, "perf-1", at=NOW)
        assert single["queries"] <= 9
        original_day = hr._day
        def legacy_inputs(db, people, first, last, policy):
            inputs = hr.MonthInputs()
            for person in people:
                inputs.tracking[person.id] = hr._tracking_start(db, person.id, policy)
                if not person.active:
                    inputs.deactivated[person.id] = db.scalar(select(func.max(m.AuditEntry.at)).where(
                        m.AuditEntry.entity_type == "user", m.AuditEntry.entity_id == person.id,
                        m.AuditEntry.action == "User deactivated"))
            return inputs
        monkeypatch.setattr(hr, "_load_month_inputs", legacy_inputs)
        monkeypatch.setattr(hr, "_day", lambda db, person, day, at, policy, inputs=None: original_day(db, person, day, at, policy))
        with measured(db) as legacy:
            previous = hr.month_report(db, actor, 2026, 10, at=NOW)
        assert result == previous
        assert legacy["queries"] > 1200
        print({"attendance_month_batch": batch, "attendance_month_previous": legacy, "single_employee": single})


def test_request_list_batches_names_without_changing_cover_or_decision_scope(workspace):
    with workspace() as db:
        for number in range(20):
            db.add(m.AttendanceHRRequest(user_id="owner", cover_user_id="other", request_key=f"cover-{number}",
                attendance_date=date(2026, 10, number + 1), kind="paid_leave", minutes=0, reason="Leave",
                status="awaiting_cover", cover_status="requested", approvals={}))
        db.commit()
        actor = db.get(m.User, "other")
        with measured(db) as measurement:
            result = hr.request_report(db, actor, 2026, 10)
        assert measurement["queries"] == 3
        assert len(result["requests"]) == 20
        assert all(row["name"] == "Owner" and row["coverName"] == "Other" and row["canRespondCover"] and not row["canDecide"]
                   for row in result["requests"])
