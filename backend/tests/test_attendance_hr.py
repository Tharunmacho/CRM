"""Reference attendance workflows on an explicitly disposable loopback database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.core.deps import current_user
from app.core.security import SESSION_COOKIE
from app.db.models import (AttendanceAdjustment, AttendanceCalendarDay, AttendanceCoverItem,
    AttendanceHRRequest, AuditEntry, Candidate, Role, Session as LoginSession, Source,
    StaffAttendanceSession, Task, TaskEvent, TaskKind, User)
from app.db.session import get_db
from app.services import attendance_hr as hr, staff_activity
from app.services.attendance_policy import AttendancePolicy, calculate_day, calculate_month
from app.services.attendance_policy_types import Shift
from test_database_naming import schema_database
from test_workflow_scheduler import workspace as base_workspace

NOW = datetime(2026, 10, 21, 3, 30, tzinfo=timezone.utc)  # Wednesday 09:00 IST.


@pytest.fixture
def workspace(base_workspace):
    with base_workspace() as db:
        db.add(User(id="manager2", name="Other manager", email="manager2@example.test", role=Role.manager))
        db.flush()
        db.add_all([LoginSession(id="login-" + key, user_id=key, expires_at=NOW + timedelta(days=365))
                    for key in ("admin", "manager", "manager2", "owner", "other")])
        db.commit()
    return base_workspace


def request(db, user_id="owner", kind="paid_leave", day=date(2026, 10, 21), **values):
    body = {"requestKey": str(uuid4()), "date": day, "kind": kind, "minutes": 0,
            "reason": "Synthetic attendance request", **values}
    if kind in hr.LEAVE_KINDS:
        body.setdefault("coverUserId", "other" if user_id != "other" else "owner")
    result = hr.create_request(db, db.get(User, user_id), body, at=NOW)
    db.commit()
    return result, body


def approve_leave(db, day=date(2026, 10, 21)):
    result, _ = request(db, day=day)
    hr.respond_cover(db, db.get(User, "other"), result["id"], {"accepted": True, "note": "I can cover"}, at=NOW)
    db.commit()
    hr.decide_request(db, db.get(User, "admin"), result["id"], {"approved": True, "reason": "Approved for test"}, at=NOW)
    db.commit()
    return result["id"]


def add_shift(db, day=date(2026, 10, 21), *, hours=9, user_id="owner", start_hour=4, start_minute=30):
    start = datetime(day.year, day.month, day.day, start_hour, start_minute, tzinfo=timezone.utc)
    row = StaffAttendanceSession(user_id=user_id, checked_in_at=start, checked_out_at=start + timedelta(hours=hours),
                                 checkin_request_key=str(uuid4()), checkout_request_key=str(uuid4()))
    db.add(row); db.commit()
    return row


def test_policy_full_workday_flexible_hours_and_no_automatic_ot():
    for start_hour, start_minute in [(4, 0), (4, 30), (5, 0)]:
        start = NOW.replace(hour=start_hour, minute=start_minute)
        row = calculate_day(NOW.date(), [{"action": "check_in", "occurred_at": start},
            {"action": "check_out", "occurred_at": start + timedelta(hours=9)}], now=NOW + timedelta(days=1))
        assert row["required_shift_minutes"] == row["actual_covered_minutes"] == 480
        assert row["uncovered_minutes"] == 0
    assert "extra_ot_minutes" not in row


def test_permission_overlap_cannot_overconsume_grace_or_create_negative_unpaid():
    row = {"date": date(2026, 10, 21), "status": "PP", "approved_actual_minutes": 30,
           "approved_occurrence_minutes": [30, 30], "uncovered_minutes": 0, "unpaid_minutes": 0}
    result = calculate_month([row])[0]
    assert result["paid_permission_minutes"] == 30
    assert result["permission_minutes_remaining"] == 30
    assert result["unpaid_minutes"] == 0


def test_missing_punch_deadline_keeps_grace_and_does_not_double_charge():
    day = date(2026, 10, 21)
    before = calculate_day(day, [], now=datetime(2026, 10, 23, 13, 30, tzinfo=timezone.utc))
    after = calculate_day(day, [], now=datetime(2026, 10, 23, 13, 30, 1, tzinfo=timezone.utc))
    assert before["status"] == "MP" and before["provisional"]
    result = calculate_month([after])[0]
    assert result["status"] == "A" and result["unpaid_minutes"] == 480
    assert result["permission_minutes_remaining"] == 60


def test_leave_cover_then_staged_approval_and_quota(workspace):
    with workspace() as db:
        result, _ = request(db)
        assert result["status"] == "awaiting_cover"
        with pytest.raises(HTTPException):
            hr.decide_request(db, db.get(User, "admin"), result["id"], {"approved": True, "reason": "Too soon"}, at=NOW)
        db.rollback()
        hr.respond_cover(db, db.get(User, "other"), result["id"], {"accepted": True}, at=NOW); db.commit()
        partial = hr.decide_request(db, db.get(User, "manager"), result["id"], {"approved": True, "reason": "Manager approved"}, at=NOW)
        db.commit()
        assert partial["status"] == "pending" and list(partial["approvals"]) == ["manager"]
        final = hr.decide_request(db, db.get(User, "admin"), result["id"], {"approved": True, "reason": "Admin approved"}, at=NOW)
        db.commit()
        assert final["status"] == "approved" and set(final["approvals"]) == {"manager", "admin"}
        second = approve_leave(db, date(2026, 10, 22))
        assert db.get(AttendanceHRRequest, second).converted_from_paid_leave
        statuses = list(db.scalars(select(AttendanceCalendarDay.status).order_by(AttendanceCalendarDay.attendance_date)))
        assert statuses == ["PL", "UL"]


def test_concurrent_paid_leave_calendar_entries_allow_only_one_paid_day(workspace):
    def save(day):
        with workspace() as db:
            result = hr.save_calendar_day(db, db.get(User, "admin"), {"userId": "owner", "date": day, "status": "PL", "reason": "Approved calendar leave"})
            db.commit()
            return result["status"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [date(2026, 10, 22), date(2026, 10, 23)])) == ["PL", "UL"]


def test_request_replay_conflicting_payload_and_own_approvals(workspace):
    with workspace() as db:
        result, body = request(db, user_id="manager", kind="extra_ot", minutes=45)
        replay = hr.create_request(db, db.get(User, "manager"), body, at=NOW)
        assert replay["id"] == result["id"]
        with pytest.raises(HTTPException) as exc:
            hr.create_request(db, db.get(User, "manager"), {**body, "minutes": 60}, at=NOW)
        assert exc.value.status_code == 409
        db.rollback()
        with pytest.raises(HTTPException):
            hr.decide_request(db, db.get(User, "manager"), result["id"], {"approved": True, "reason": "Cannot self approve"}, at=NOW)
        db.rollback()
        with pytest.raises(HTTPException):
            hr.decide_request(db, db.get(User, "manager2"), result["id"], {"approved": True, "reason": "Another manager cannot approve"}, at=NOW)
        db.rollback()
        approved = hr.decide_request(db, db.get(User, "admin"), result["id"], {"approved": True, "reason": "Admin approved"}, at=NOW)
        assert approved["status"] == "approved"


def test_cover_incoming_visibility_and_decline_renomination(workspace):
    with workspace() as db:
        db.add(User(id="third", name="Third staff", email="third@example.test", role=Role.staff)); db.commit()
        result, _ = request(db)
        incoming = hr.request_report(db, db.get(User, "other"), 2026, 10)["requests"]
        assert incoming[0]["id"] == result["id"] and incoming[0]["canRespondCover"]
        hr.respond_cover(db, db.get(User, "other"), result["id"], {"accepted": False, "note": "Unavailable"}); db.commit()
        replacement = hr.nominate_cover(db, db.get(User, "owner"), result["id"], {"coverUserId": "third"})
        db.commit()
        assert replacement["coverUserId"] == "third" and replacement["status"] == "awaiting_cover"


def _cover_task(db, candidate_id, task_id):
    if candidate_id != "candidate":
        db.add(Candidate(id=candidate_id, ref="CND-" + candidate_id, name="Synthetic cover " + candidate_id,
                         phone="900000" + candidate_id, source=Source.walk_in, owner_id="owner")); db.flush()
    task = Task(id=task_id, kind=TaskKind.initial_call, title="Call candidate", candidate_id=candidate_id,
                owner_id="owner", created_at=NOW - timedelta(hours=12), sla_started_at=NOW - timedelta(hours=12),
                due_at=NOW + timedelta(hours=36))
    db.add(task); db.commit()
    return task


def test_cover_sweep_returns_pending_keeps_completed_and_preserves_clocks(workspace):
    with workspace() as db:
        pending = _cover_task(db, "candidate", "pending")
        completed = _cover_task(db, "completed", "completed")
        request_id = approve_leave(db)
        first = hr.run_cover_sweep(db, at=NOW); db.commit()
        assert first["started"] == 2
        assert pending.owner_id == completed.owner_id == "other"
        assert pending.due_at == NOW + timedelta(hours=36)
        assert hr.run_cover_sweep(db, at=NOW + timedelta(minutes=1))["started"] == 0
        db.commit()
        completed.completed_at = NOW + timedelta(hours=1)
        db.get(Candidate, "completed").call_recorded_at = NOW + timedelta(hours=1)
        db.commit()
        settled = hr.run_cover_sweep(db, at=NOW + timedelta(days=1)); db.commit()
        assert settled["returned"] == settled["completed"] == 1
        assert pending.owner_id == "owner" and completed.owner_id == "other"
        assert db.get(Candidate, "completed").owner_id == "other"
        assert pending.due_at == NOW + timedelta(hours=36)
        assert hr.run_cover_sweep(db, at=NOW + timedelta(days=1, minutes=1))["settled"] == 0
        assert db.scalar(select(func.count()).select_from(AttendanceCoverItem).where(AttendanceCoverItem.request_id == request_id)) == 2
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_type == "candidate", AuditEntry.entity_id == "candidate")) == 2


def test_cover_never_steals_human_reassignment_even_back_to_cover(workspace):
    with workspace() as db:
        task = _cover_task(db, "candidate", "task")
        approve_leave(db)
        hr.run_cover_sweep(db, at=NOW); db.commit()
        db.add(AuditEntry(actor_id="admin", action="Reassigned candidate", entity_type="candidate", entity_id="candidate",
            at=NOW + timedelta(hours=1), meta={"fromUserId": "manager", "toUserId": "other", "reason": "Human assignment"}))
        db.commit()
        result = hr.run_cover_sweep(db, at=NOW + timedelta(days=1)); db.commit()
        assert result["released"] == 1 and result["returned"] == 0
        assert task.owner_id == db.get(Candidate, "candidate").owner_id == "other"


def test_weekly_off_cutoff_rotation_and_duty_plan_is_one_day(workspace):
    with workspace() as db:
        owner, admin = db.get(User, "owner"), db.get(User, "admin")
        before = datetime(2026, 10, 22, 18, 29, 59, tzinfo=timezone.utc)
        hr.set_weekly_off(db, owner, {"weekStart": date(2026, 10, 19), "day": "friday"}, at=before); db.commit()
        with pytest.raises(HTTPException):
            hr.set_weekly_off(db, owner, {"weekStart": date(2026, 10, 19), "day": "sunday"}, at=before + timedelta(seconds=1))
        db.rollback()
        add_shift(db)
        body = {"userId": "owner", "date": date(2026, 10, 23), "shift": {"start": "10:00", "end": "19:00", "breakMinutes": 60}, "reason": "Special Friday duty"}
        plan = hr.save_duty_plan(db, admin, body); db.commit()
        day = hr._day(db, owner, date(2026, 10, 23), NOW + timedelta(days=5), hr.policy())
        assert day["status"] == "A"
        hr.delete_duty_plan(db, admin, plan["id"]); db.commit()
        assert hr._day(db, owner, date(2026, 10, 23), NOW + timedelta(days=4), hr.policy())["status"] == "WO"
        assert hr._day(db, owner, date(2026, 10, 30), NOW + timedelta(days=12), hr.policy())["status"] == "A"


def test_roster_and_settings_scope_no_manager_self_changes_or_other_manager_reads(workspace):
    with workspace() as db:
        manager = db.get(User, "manager")
        settings = hr.settings_report(db, manager, 2026, 10, at=NOW)
        assert {row["id"] for row in settings["employees"]} == {"owner", "other", "manager"}
        assert {row["id"] for row in settings["coverColleagues"]} == {"owner", "other"}
        with pytest.raises(HTTPException):
            hr.month_report(db, manager, 2026, 10, "manager2", at=NOW)
        with pytest.raises(HTTPException):
            hr.save_work_timing(db, manager, {"userId": "manager", "effectiveFrom": NOW.date(), "shift": {}, "reason": "Own shift change"})
        db.rollback()
        with pytest.raises(HTTPException):
            hr.save_calendar_day(db, manager, {"userId": "owner", "date": NOW.date(), "status": "PL", "reason": "Finance required"})


def test_monthly_tracking_boundary_inactive_cutoff_and_overnight_shift(workspace):
    with workspace() as db:
        admin, owner = db.get(User, "admin"), db.get(User, "owner")
        assert hr.month_report(db, admin, 2026, 10, "owner", at=NOW)["staff"][0]["days"] == []
        hr.save_work_timing(db, admin, {"userId": "owner", "effectiveFrom": NOW.date(),
            "shift": {"start": "22:00", "end": "07:00", "breakMinutes": 60}, "reason": "Overnight work"}); db.commit()
        add_shift(db, start_hour=16, start_minute=30)
        owner.active = False
        owner.updated_at = NOW + timedelta(days=1)
        db.add(AuditEntry(actor_id="admin", action="User deactivated", entity_type="user", entity_id="owner", at=NOW + timedelta(days=1)))
        db.commit()
        result = hr.month_report(db, admin, 2026, 10, "owner", at=NOW + timedelta(days=7))["staff"][0]
        assert [row["date"] for row in result["days"]] == ["2026-10-21", "2026-10-22"]
        assert result["days"][0]["workedMinutes"] == 480
        assert result["days"][0]["status"] == "P"
        assert hr.month_report(db, admin, 2026, 11, "owner", at=NOW)["staff"][0]["days"] == []


def test_approved_ot_offsets_only_partial_shortfall_not_absence(workspace):
    with workspace() as db:
        add_shift(db, hours=7)  # 120 short; monthly grace absorbs 60.
        result, _ = request(db, kind="extra_ot", minutes=300)
        hr.decide_request(db, db.get(User, "admin"), result["id"], {"approved": True, "reason": "Approved overtime"}, at=NOW); db.commit()
        report = hr.month_report(db, db.get(User, "admin"), 2026, 10, "owner", at=NOW + timedelta(days=6))["staff"][0]
        summary = report["summary"]
        assert summary["extraOtMinutes"] == 300 and summary["otOffsetMinutes"] == 60
        assert summary["unusedOtMinutes"] == 240
        assert summary["unpaidMinutes"] == summary["unpaidBeforeOtMinutes"] - 60
        assert summary["unpaidMinutes"] >= 480


def test_adjustment_is_audited_status_only_and_permissions_enforced(workspace):
    with workspace() as db:
        add_shift(db, hours=7)
        values = {"userId": "owner", "date": NOW.date(), "kind": "status_override", "status": "P", "reason": "Documented status correction"}
        with pytest.raises(HTTPException):
            hr.create_adjustment(db, db.get(User, "manager"), values, at=NOW + timedelta(days=1))
        db.rollback()
        hr.create_adjustment(db, db.get(User, "admin"), values, at=NOW + timedelta(days=1)); db.commit()
        result = hr.month_report(db, db.get(User, "admin"), 2026, 10, "owner", at=NOW + timedelta(days=1))["staff"][0]["days"][0]
        assert result["status"] == "P" and result["workedMinutes"] == 360 and result["unpaidMinutes"] == 60
        assert hr.adjustment_report(db, db.get(User, "owner"), 2026, 10)["adjustments"][0]["reason"] == values["reason"]
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Adjusted attendance")) == 1


def test_api_role_scope_manager_clock_and_schema_validation(workspace, monkeypatch):
    from app.api.routers.attendance import router
    app = FastAPI(); app.include_router(router)
    role = ["manager"]
    def session():
        with workspace() as db:
            yield db
    def user():
        with workspace() as db:
            return db.get(User, role[0])
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_user] = user
    monkeypatch.setattr(staff_activity, "utcnow", lambda: NOW)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "login-manager")
    response = client.post("/api/attendance/check-in", json={"requestKey": str(uuid4())})
    assert response.status_code == 200 and response.json()["attendance"]["status"] == "checked_in"
    assert client.post("/api/presence/heartbeat", json={"clientId": str(uuid4()), "sequence": 1, "visible": True}).status_code == 403
    assert client.get("/api/attendance/month?year=2026&month=10&staffId=manager2").status_code == 404
    role[0] = "admin"
    assert client.post("/api/attendance/check-in", json={"requestKey": str(uuid4())}).status_code == 403
    assert client.post("/api/attendance/duty-plans", json={"userId": "owner", "date": "2026-10-21", "reason": "Invalid break",
        "shift": {"start": "10:00", "end": "10:30", "breakMinutes": 60}}).status_code == 422
    role[0] = "owner"
    assert client.get("/api/attendance/settings?year=2026&month=10&staffId=other").status_code == 404


def test_assignment_availability_uses_final_calendar_and_agency_day(workspace):
    from app.services.attendance_availability import unavailable_staff_ids
    with workspace() as db:
        result, _ = request(db)
        assert unavailable_staff_ids(db, at=NOW) == set()
        hr.respond_cover(db, db.get(User, "other"), result["id"], {"accepted": True}); db.commit()
        hr.decide_request(db, db.get(User, "manager"), result["id"], {"approved": True, "reason": "Manager stage"}, at=NOW); db.commit()
        assert unavailable_staff_ids(db, at=NOW) == set()
        hr.decide_request(db, db.get(User, "admin"), result["id"], {"approved": True, "reason": "Final approval"}, at=NOW); db.commit()
        assert unavailable_staff_ids(db, at=NOW) == {"owner"}
        assert unavailable_staff_ids(db, at=datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc)) == {"owner"}
        assert unavailable_staff_ids(db, at=datetime(2026, 10, 20, 18, 29, tzinfo=timezone.utc)) == set()
        hr.save_calendar_day(db, db.get(User, "admin"), {"userId": "other", "date": NOW.date(), "status": "UL", "reason": "Recorded absence"}); db.commit()
        assert hr._cover_colleagues(db, db.get(User, "owner"), NOW.date()) == []
        hr.save_calendar_day(db, db.get(User, "admin"), {"userId": "owner", "date": NOW.date(), "status": "H", "reason": "Correct approved leave to holiday"}); db.commit()
        assert unavailable_staff_ids(db, at=NOW) == {"other"}
        assert not db.get(User, "owner").on_leave


def test_cover_sweep_rollback_retries_without_half_transferred_work(workspace):
    with workspace() as db:
        _cover_task(db, "candidate", "retry-task")
        request_id = approve_leave(db)
        assert hr.run_cover_sweep(db, at=NOW)["started"] == 1
        db.rollback()
        assert db.get(Candidate, "candidate").owner_id == "owner"
        assert db.get(AttendanceHRRequest, request_id).cover_started_at is None
        assert hr.run_cover_sweep(db, at=NOW + timedelta(minutes=1))["started"] == 1
        db.commit()
        assert db.scalar(select(func.count()).select_from(AttendanceCoverItem)) == 1
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.kind == "assignment")) == 1


def test_status_override_survives_monthly_permission_classification(workspace):
    with workspace() as db:
        add_shift(db, start_hour=5, start_minute=30, hours=8)
        requested, _ = request(db, kind="late", minutes=60)
        hr.decide_request(db, db.get(User, "manager"), requested["id"], {"approved": True, "reason": "Approved late arrival"}, at=NOW)
        db.commit()
        hr.create_adjustment(db, db.get(User, "admin"), {"userId": "owner", "date": NOW.date(),
            "kind": "status_override", "status": "P", "reason": "Explicit classification"}, at=NOW + timedelta(days=1))
        db.commit()
        row = hr.month_report(db, db.get(User, "admin"), 2026, 10, "owner", at=NOW + timedelta(days=1))["staff"][0]["days"][0]
        assert row["status"] == "P" and row["paidPermissionMinutes"] == 60
        assert row["workedMinutes"] == 420 and row["unpaidMinutes"] == 0


def test_late_worker_does_not_start_yesterdays_cover(workspace):
    with workspace() as db:
        _cover_task(db, "candidate", "missed-cover")
        request_id = approve_leave(db)
        result = hr.run_cover_sweep(db, at=NOW + timedelta(days=1)); db.commit()
        assert result["started"] == 0 and result["settled"] == 1
        assert db.get(Candidate, "candidate").owner_id == "owner"
        row = db.get(AttendanceHRRequest, request_id)
        assert row.cover_started_at is None and row.cover_settled_at is not None
        assert hr.run_cover_sweep(db, at=NOW + timedelta(days=1, minutes=1))["settled"] == 0


def test_cover_does_not_start_after_admin_replaces_leave_calendar(workspace):
    with workspace() as db:
        _cover_task(db, "candidate", "corrected-calendar")
        request_id = approve_leave(db)
        hr.save_calendar_day(db, db.get(User, "admin"), {"userId": "owner", "date": NOW.date(),
            "status": "H", "reason": "Leave replaced with office holiday"}); db.commit()
        assert hr.run_cover_sweep(db, at=NOW)["started"] == 0
        db.commit()
        row = db.get(AttendanceHRRequest, request_id)
        assert row.cover_started_at is None and row.cover_settled_at is not None
        assert db.get(Candidate, "candidate").owner_id == "owner"
        assert hr.run_cover_sweep(db, at=NOW + timedelta(minutes=1))["started"] == 0


def test_back_to_back_leave_returns_then_starts_next_cover_atomically(workspace):
    with workspace() as db:
        task = _cover_task(db, "candidate", "consecutive-cover")
        first_id = approve_leave(db)
        second_id = approve_leave(db, NOW.date() + timedelta(days=1))
        assert hr.run_cover_sweep(db, at=NOW)["started"] == 1
        db.commit()
        result = hr.run_cover_sweep(db, at=NOW + timedelta(days=1)); db.commit()
        assert result["returned"] == result["started"] == 1
        assert db.get(AttendanceHRRequest, first_id).cover_settled_at is not None
        assert db.get(AttendanceHRRequest, second_id).cover_started_at is not None
        assert task.owner_id == db.get(Candidate, "candidate").owner_id == "other"
        assert task.due_at == NOW + timedelta(hours=36)


def test_manual_on_leave_prevents_automatic_cover_return(workspace):
    with workspace() as db:
        _cover_task(db, "candidate", "manual-leave")
        approve_leave(db)
        hr.run_cover_sweep(db, at=NOW); db.commit()
        db.get(User, "owner").on_leave = True
        db.commit()
        result = hr.run_cover_sweep(db, at=NOW + timedelta(days=1)); db.commit()
        assert result["returned"] == 0 and result["released"] == 1
        assert db.get(Candidate, "candidate").owner_id == "other"


def test_only_admin_can_approve_a_reasoned_emergency_permission_exception(workspace):
    with workspace() as db:
        requested, _ = request(db, kind="late", minutes=30)
        decision = {"approved": True, "reason": "Documented emergency requires another occasion", "emergencyOverride": True}
        with pytest.raises(HTTPException) as exc:
            hr.decide_request(db, db.get(User, "manager"), requested["id"], decision, at=NOW)
        assert exc.value.status_code == 403
        db.rollback()
        row = db.get(AttendanceHRRequest, requested["id"])
        assert not row.emergency_override and row.status == "pending" and not row.approvals
        result = hr.decide_request(db, db.get(User, "admin"), requested["id"], decision, at=NOW)
        db.commit()
        assert result["emergencyOverride"] and result["status"] == "approved"
        history = hr.request_report(db, db.get(User, "owner"), 2026, 10)["requests"][0]
        assert history["emergencyOverride"]
        audit = db.scalar(select(AuditEntry).where(AuditEntry.entity_id == requested["id"], AuditEntry.action == "Decided attendance request"))
        assert audit.meta["emergencyOverride"] and audit.meta["reason"] == decision["reason"]
