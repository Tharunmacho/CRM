"""The end-of-day recruiter report the owner (Admin and MD) receives without opening anything.

Runs on the explicitly guarded disposable PostgreSQL DB; one dataset is seeded once for the module and every test starts from an
empty notification / marker state. Agency zone Asia/Kolkata. Days (IST):
  Wed 11-18  asha prescreened c-old                        (older than yesterday in the catch-up test: never reported)
  Thu 11-19  asha prescreened c-thu                        (yesterday)
  Fri 11-20  asha prescreened c-fri and closed her day; ravi called a lead; meena did nothing (the working day)
  Thu 11-12  meena only checked in (no figures)            (attendance alone is activity)
  Tue 11-10  nobody did anything                           (a day with nothing at all)
"""
import os
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, Client, Company, Designation, Lead, LeadActivity,
    Notification, Order, Role, Source, StaffAttendanceSession, User,
)
from app.services import day_end_report, workflow_scheduler
from app.services.day_end_report import KIND, MARKER_ACTION, deliver_day_end_report, link_for
from app.services.daily_work_report import SUBMIT_ACTION
from test_database_naming import _guard_test_url, _migrate

TZ = "Asia/Kolkata"
IST = ZoneInfo(TZ)
OLD, THU, FRI = date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)
CHECKIN_DAY, EMPTY = date(2026, 11, 12), date(2026, 11, 10)


def ist(day, hour=0, minute=0):
    return datetime.combine(day, time(hour, minute), IST).astimezone(timezone.utc)


@pytest.fixture(scope="module")
def sessions():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB")
    engine = create_engine(_guard_test_url(raw), connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})

    def reset():
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))

    reset()
    try:
        _migrate(engine, "head")
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        seed(factory)
        yield factory
    finally:
        reset()
        engine.dispose()


def seed(factory):
    with factory() as db:
        long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for key, role in (("admin", Role.admin), ("md", Role.manager), ("asha", Role.staff), ("ravi", Role.staff),
                          ("meena", Role.staff)):
            db.add(User(id=key, name=key.title(), email=f"{key}@example.test", role=role, created_at=long_ago, updated_at=long_ago))
        db.add(Client(id="client", name="Mr Contact"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Gulf Builders", country="UAE"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add(Order(id="order", ref="ORD-1", company_id="company", designation_id="designation", openings=40, country="Qatar"))
        db.flush()
        for number, (key, day, hour) in enumerate((("old", OLD, 11), ("thu", THU, 11), ("fri", FRI, 11))):
            db.add(Candidate(id=f"c-{key}", ref=f"CND-{key}", name=f"Candidate {key}", phone=f"9000000{number:03}",
                             source=Source.walk_in, owner_id="asha"))
            db.flush()
            db.add(Application(id=f"app-{key}", candidate_id=f"c-{key}", order_id="order", owner_id="asha", current_stage=1,
                               status=ApplicationStatus.active))
            db.flush()
            db.add(ApplicationStage(application_id=f"app-{key}", stage=1, attempt=1, outcome="Passed", completed_by="asha",
                                    owner_id="asha", completed_at=ist(day, hour), data={}))
        db.add(Lead(id="L1", name="Lead 1", phone="+918000000001", owner_id="ravi", status="interested",
                    assigned_at=ist(date(2026, 10, 1), 9), created_time=ist(date(2026, 10, 1), 9),
                    created_at=ist(date(2026, 10, 1), 9), position_applied="Chef"))
        db.flush()
        db.add(LeadActivity(lead_id="L1", user_id="ravi", kind="call", summary="call", meta={"outcome": "interested"},
                            created_at=ist(FRI, 11)))
        db.add(AuditEntry(actor_id="asha", action=SUBMIT_ACTION, target="Asha", entity_type="user", entity_id="asha",
                          meta={"date": FRI.isoformat(), "submittedAt": ist(FRI, 17).isoformat(), "note": None, "figures": {}},
                          at=ist(FRI, 17)))
        db.add(StaffAttendanceSession(id="meena-in", user_id="meena", checked_in_at=ist(CHECKIN_DAY, 10),
                                    checked_out_at=ist(CHECKIN_DAY, 18, 30), checkin_request_key="meena-in"))
        db.commit()


@pytest.fixture
def db(sessions):
    """A clean slate for every test: no notifications and no markers (the seeded day closes stay)."""
    with sessions() as clean:
        clean.execute(text("DELETE FROM system_notifications"))
        clean.execute(text("DELETE FROM system_audit_log WHERE action = :a"), {"a": MARKER_ACTION})
        clean.commit()
    with sessions() as session:
        yield session


def run(db, now):
    result = deliver_day_end_report(db, now=now, agency_timezone=TZ)
    db.commit()
    return result


def reports(db, day=None, user=None):
    query = select(Notification).where(Notification.kind == KIND)
    if day:
        query = query.where(Notification.link_to == link_for(day))
    if user:
        query = query.where(Notification.user_id == user)
    return list(db.scalars(query.order_by(Notification.user_id, Notification.link_to)))


def mark_handled(db, day):
    db.add(AuditEntry(action=MARKER_ACTION, target=day.isoformat(), entity_type="day_report", meta={"date": day.isoformat()}))
    db.commit()


def test_nothing_is_sent_before_the_hour(db):
    mark_handled(db, THU)   # yesterday is already handled, so only today could send
    result = run(db, ist(FRI, 18, 59))
    assert result == {"sent": 0, "days": []}
    assert reports(db) == []


def test_after_the_hour_each_admin_and_md_gets_exactly_one_and_staff_none(db):
    mark_handled(db, THU)
    result = run(db, ist(FRI, 19, 0))
    assert result == {"sent": 2, "days": [FRI.isoformat()]}
    got = reports(db, FRI)
    assert {n.user_id for n in got} == {"admin", "md"}
    assert len(got) == 2
    assert all(n.title == "Recruiter report: Fri 20 Nov" and n.link_to == "/reports?from=2026-11-20&to=2026-11-20" and not n.read
               for n in got)
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id.in_(["asha", "ravi", "meena"]))) == 0
    assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == MARKER_ACTION)) == 2


def test_running_again_adds_nothing_and_a_restart_the_next_minute_too(db):
    mark_handled(db, THU)
    run(db, ist(FRI, 19, 0))
    for minute in (1, 2):
        assert run(db, ist(FRI, 19, minute)) == {"sent": 0, "days": []}
    assert run(db, ist(FRI, 23, 30)) == {"sent": 0, "days": []}
    assert len(reports(db)) == 2


def test_a_missing_recipient_alone_is_filled_in_without_duplicating_the_other(db):
    mark_handled(db, THU)
    run(db, ist(FRI, 19, 0))
    db.execute(text("DELETE FROM system_notifications WHERE user_id = 'md'"))
    db.execute(text("DELETE FROM system_audit_log WHERE action = :a AND target = :t"), {"a": MARKER_ACTION, "t": FRI.isoformat()})
    db.commit()
    assert run(db, ist(FRI, 20, 0)) == {"sent": 1, "days": [FRI.isoformat()]}
    assert sorted(n.user_id for n in reports(db, FRI)) == ["admin", "md"]


def test_the_text_names_who_did_not_close_the_day_and_reads_as_a_short_summary(db):
    mark_handled(db, THU)
    run(db, ist(FRI, 19, 0))
    detail = reports(db, FRI, "admin")[0].detail
    assert len(detail) <= 900
    assert "<" not in detail and ">" not in detail
    assert "Prescreened 1" in detail and "1 leads contacted" in detail
    assert "1 of 3 recruiters closed their day. Not closed: Ravi, Meena." in detail
    assert "Top contributors: Asha 1, Ravi 1." in detail
    assert "Nothing recorded: Meena." in detail
    assert "Attendance: 0 present, 3 absent (Asha, Meena, Ravi)." in detail


def test_a_day_with_no_activity_sends_nothing_but_is_not_looked_at_again(db):
    result = run(db, ist(EMPTY, 20, 0))
    assert reports(db) == []
    assert result["sent"] == 0 and result["days"] == [(EMPTY.replace(day=9)).isoformat(), EMPTY.isoformat()]
    assert run(db, ist(EMPTY, 20, 1)) == {"sent": 0, "days": []}   # both days are marked: no second look


def test_attendance_alone_counts_as_a_working_day(db):
    mark_handled(db, date(2026, 11, 11))
    result = run(db, ist(CHECKIN_DAY, 19, 30))
    assert result["sent"] == 2
    detail = reports(db, CHECKIN_DAY, "md")[0].detail
    assert "Attendance: 1 present" in detail and "2 absent (Asha, Ravi)" in detail
    assert "0 of 3 recruiters closed their day. Not closed: Asha, Meena, Ravi." in detail


def test_a_missed_day_is_caught_up_once_for_yesterday_but_not_older(db):
    # The worker was down all of Thursday evening and Friday until 20:00: Thursday and Friday are both reported, Wednesday never.
    result = run(db, ist(FRI, 20, 0))
    assert result["days"] == [THU.isoformat(), FRI.isoformat()]
    assert result["sent"] == 4
    assert {n.link_to for n in reports(db)} == {link_for(THU), link_for(FRI)}
    assert reports(db, OLD) == []
    assert run(db, ist(FRI, 21, 0)) == {"sent": 0, "days": []}


def test_yesterday_is_caught_up_in_the_morning_after_a_night_of_downtime(db):
    result = run(db, ist(FRI, 8, 15))   # before the hour: only yesterday, today is not due yet
    assert result == {"sent": 2, "days": [THU.isoformat()]}
    assert {n.link_to for n in reports(db)} == {link_for(THU)}


def test_the_worker_pass_never_raises_and_pauses_after_a_failure(db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("report builder down")
    monkeypatch.setattr(day_end_report, "deliver_day_end_report", boom)
    monkeypatch.setattr(workflow_scheduler, "_day_report_retry_at", 0.0)
    first = workflow_scheduler.scan_day_end_report(db, now=ist(FRI, 20, 0), agency_timezone=TZ)
    assert first == {"sent": 0, "days": [], "failed": True}
    monkeypatch.setattr(day_end_report, "deliver_day_end_report", lambda *a, **k: pytest.fail("retried during the pause"))
    assert workflow_scheduler.scan_day_end_report(db, now=ist(FRI, 20, 1), agency_timezone=TZ) == {"sent": 0, "days": []}
    monkeypatch.setattr(workflow_scheduler, "_day_report_retry_at", 0.0)


def test_the_worker_pass_delivers_through_the_scheduler_entry_point(db):
    mark_handled(db, THU)
    result = workflow_scheduler.scan_day_end_report(db, now=ist(FRI, 19, 5), agency_timezone=TZ)
    db.commit()
    assert result["sent"] == 2 and len(reports(db, FRI)) == 2
