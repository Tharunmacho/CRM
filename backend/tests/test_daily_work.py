"""Daily candidate quota: allocation, hourly slots, carry-over, completion, release, alerts, API and migration.

Runs on the explicitly guarded disposable PostgreSQL DB (see test_database_naming._guard_test_url).
Time is frozen: the agency zone is Asia/Kolkata, so 2026-11-03 03:00 UTC is Tuesday 08:30 local.
"""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, AttendanceCalendarDay, AttendanceWeeklyOff, AttendanceWorkTiming, Candidate, Client, Company,
    DailyGoal, DailyGoalProgress, DailyWorkItem, Designation, Notification, Order, Remark, Role, Source, Task, TaskKind, User, WorkflowSettings,
)
from app.services import daily_work
from app.services.daily_work import ensure_allocations, load_rows, scan_daily_work, slot_plan, summarise
from test_database_naming import _migrate, schema_database

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import applications as applications_router
    from app.api.routers import daily_work as daily_router
    from app.api.routers import data as data_router
    from app.api.routers import workflow as workflow_router

TZ = "Asia/Kolkata"
TUESDAY = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)  # 08:30 IST, before the first slot
WEDNESDAY = TUESDAY + timedelta(days=1)


ZoneInfoIST = ZoneInfo(TZ)


def ist(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), ZoneInfoIST).astimezone(timezone.utc)


def frozen(clock):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"] if tz is None else clock["now"].astimezone(tz)
    return FrozenDatetime


@pytest.fixture
def desk(schema_database, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(daily_work, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("asha", Role.staff), ("ravi", Role.staff))])
        db.add(Client(id="client", name="Mr Contact", country="UAE"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Gulf Builders", country="UAE"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add(Order(id="order", ref="ORD-1", company_id="company", designation_id="designation", openings=40, country="Qatar"))
        db.commit()
    clock = {"now": TUESDAY}
    monkeypatch.setattr(daily_router, "datetime", frozen(clock))
    monkeypatch.setattr(applications_router, "datetime", frozen(clock))
    monkeypatch.setattr(data_router, "datetime", frozen(clock))
    monkeypatch.setattr(applications_router, "get_settings", lambda: SimpleNamespace(storage_dir="unused"))
    monkeypatch.setattr(workflow_router, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    actor = SimpleNamespace(id="asha", role=Role.staff)
    app = FastAPI()
    app.include_router(applications_router.router)
    app.include_router(daily_router.router)
    app.include_router(workflow_router.router)
    app.include_router(data_router.router)

    def database():
        with sessions() as db:
            yield db

    # require_role(...) builds on current_user, so one override serves every endpoint's role check.
    app.dependency_overrides[daily_router.get_db] = database
    app.dependency_overrides[daily_router.current_user] = lambda: actor
    env = SimpleNamespace(sessions=sessions, clock=clock, actor=actor, app=app)
    with TestClient(app) as client:
        env.client = client
        yield env


def as_user(env, user_id):
    env.actor.id = user_id
    env.actor.role = Role.staff if user_id in {"asha", "ravi"} else Role.admin if user_id == "admin" else Role.manager


def mapped_candidates(env, count, *, owner="asha", start=0):
    """Candidates mapped to the order through the real endpoint; task ages are spread so 'oldest first' is testable."""
    ids = []
    with env.sessions() as db:
        for n in range(start, start + count):
            db.add(Candidate(id=f"c{n}", ref=f"CND-{n:03}", name=f"Candidate {n}", phone=f"90000{n:05}", source=Source.walk_in, owner_id=owner))
        db.commit()
    as_user(env, owner)
    for n in range(start, start + count):
        response = env.client.post("/api/applications", json={"candidate_id": f"c{n}", "order_id": "order"})
        assert response.status_code == 201, response.text
        ids.append((f"c{n}", response.json()["id"]))
    with env.sessions() as db:
        for n, (_candidate, app_id) in zip(range(start, start + count), ids):
            db.execute(text("UPDATE workflow_tasks SET created_at = :at WHERE application_id = :a"),
                       {"at": TUESDAY - timedelta(days=30 - n), "a": app_id})
        db.commit()
    return dict(ids)


def finish_stage_one(env, app_id):
    """The real stage workflow: save the Prescreen result and complete it."""
    version = env.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]
    saved = env.client.put(f"/api/applications/{app_id}/draft", json={"data": {"result": "passed"}, "lock_version": version})
    assert saved.status_code == 200, saved.text
    done = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert done.status_code == 200, done.text


def rows_of(env, user_id, now):
    with env.sessions() as db:
        return load_rows(db, [user_id], now=now, tz=TZ)


# ------------------------------------------------------------------ pure slot plan

def test_slot_plan_gives_three_morning_hours_and_two_after_the_break():
    plan = slot_plan(date(2026, 11, 3), time(10, 0), 60, 5, 3, TZ)
    assert [(s.no, s.session) for s in plan] == [(1, "morning"), (2, "morning"), (3, "morning"), (4, "evening"), (5, "evening")]
    local = [s.due_at.astimezone(ZoneInfoIST).strftime("%H:%M") for s in plan]
    assert local == ["11:00", "12:00", "13:00", "15:00", "16:00"]  # one candidate per hour, lunch 13:00-14:00
    assert all(b.due_at - a.due_at >= timedelta(hours=1) for a, b in zip(plan, plan[1:]))


def test_slot_plan_follows_a_custom_shift_and_split():
    plan = slot_plan(date(2026, 11, 3), time(9, 30), 30, 4, 1, TZ)
    assert [s.due_at.astimezone(ZoneInfoIST).strftime("%H:%M") for s in plan] == ["10:30", "12:00", "13:00", "14:00"]
    assert [s.session for s in plan] == ["morning", "evening", "evening", "evening"]
    assert [s.no for s in slot_plan(date(2026, 11, 3), time(10, 0), 60, 3, 0, TZ)] == [1, 2, 3]


# ------------------------------------------------------------------ allocation

def test_allocates_five_oldest_stage_tasks_with_hourly_slots_and_is_idempotent(desk):
    mapped_candidates(desk, 7)
    with desk.sessions() as db:
        # An initial call has no client or order, so it is never part of the quota.
        db.add(Task(id="call", kind=TaskKind.initial_call, title="Initial call", candidate_id="c0", owner_id="asha", created_at=TUESDAY - timedelta(days=90)))
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {"asha": 5}
        db.commit()
        assert ensure_allocations(db, now=TUESDAY + timedelta(minutes=5), tz=TZ) == {}  # nothing to add: already five
        items = list(db.scalars(select(DailyWorkItem).where(DailyWorkItem.owner_id == "asha").order_by(DailyWorkItem.slot_no)))
        assert [i.candidate_id for i in items] == ["c0", "c1", "c2", "c3", "c4"]  # oldest tasks first
        assert [i.session for i in items] == ["morning"] * 3 + ["evening"] * 2
        assert [i.due_at.astimezone(ZoneInfoIST).strftime("%H:%M") for i in items] == ["11:00", "12:00", "13:00", "15:00", "16:00"]
        assert {i.work_date for i in items} == {date(2026, 11, 3)} and all(i.stage == 1 for i in items)
        assert db.scalar(select(func.count()).select_from(DailyWorkItem).where(DailyWorkItem.owner_id == "ravi")) == 0


def test_fewer_candidates_than_the_quota_and_late_arrivals_top_up_the_remaining_slots(desk):
    mapped_candidates(desk, 2)
    with desk.sessions() as db:
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {"asha": 2}
        db.commit()
    mapped_candidates(desk, 4, start=2)
    with desk.sessions() as db:
        assert ensure_allocations(db, now=TUESDAY + timedelta(hours=1), tz=TZ) == {"asha": 3}  # 5 in total, never more
        db.commit()
        slots = list(db.scalars(select(DailyWorkItem.slot_no).where(DailyWorkItem.owner_id == "asha").order_by(DailyWorkItem.slot_no)))
        assert slots == [1, 2, 3, 4, 5]


def test_an_allocation_after_the_deadlines_still_leaves_an_hour_per_candidate(desk):
    mapped_candidates(desk, 5)
    late = ist(date(2026, 11, 3), 12, 30)  # slots 1 and 2 (11:00, 12:00) are already behind us
    with desk.sessions() as db:
        ensure_allocations(db, now=late, tz=TZ)
        db.commit()
        due = list(db.scalars(select(DailyWorkItem.due_at).where(DailyWorkItem.owner_id == "asha").order_by(DailyWorkItem.slot_no)))
    assert due[0] == late + timedelta(hours=1) and due[1] == late + timedelta(hours=2)
    assert due == sorted(due) and all(b - a >= timedelta(hours=1) for a, b in zip(due, due[1:]))


def test_days_off_and_leave_get_no_new_quota(desk):
    mapped_candidates(desk, 6)
    sunday = ist(date(2026, 11, 8), 9, 0)
    with desk.sessions() as db:
        assert ensure_allocations(db, now=sunday, tz=TZ) == {}  # Sunday is the default weekly off
        db.add(AttendanceWeeklyOff(user_id="asha", week_start=date(2026, 11, 2), day="tuesday"))
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {}  # her chosen weekly off this week
        db.execute(text("DELETE FROM staff_attendance_weekly_off"))
        db.add(AttendanceCalendarDay(user_id="asha", attendance_date=date(2026, 11, 3), status="PL", reason="Approved leave", actor_id="admin"))
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {}
        db.execute(text("DELETE FROM staff_attendance_calendar"))
        db.get(User, "asha").on_leave = True
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {}
        db.get(User, "asha").on_leave = False
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {"asha": 5}


def test_the_recruiters_shift_sets_the_slot_times(desk):
    mapped_candidates(desk, 5)
    with desk.sessions() as db:
        db.add(AttendanceWorkTiming(user_id="asha", effective_from=date(2026, 10, 1), reason="Early shift", actor_id="admin",
                                    shift={"start": "08:00", "end": "16:00", "breakMinutes": 30}))
        db.commit()
        ensure_allocations(db, now=ist(date(2026, 11, 3), 7, 0), tz=TZ)
        db.commit()
        due = [d.astimezone(ZoneInfoIST).strftime("%H:%M") for d in db.scalars(select(DailyWorkItem.due_at).order_by(DailyWorkItem.slot_no))]
    assert due == ["09:00", "10:00", "11:00", "12:30", "13:30"]


# ------------------------------------------------------------------ completion through the stage workflow

def test_finishing_the_stage_through_the_real_workflow_completes_the_item_and_never_adds_extra_work(desk):
    apps = mapped_candidates(desk, 7)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    desk.clock["now"] = TUESDAY + timedelta(hours=2)
    finish_stage_one(desk, apps["c0"])
    rows = rows_of(desk, "asha", desk.clock["now"])
    assert [r["candidate"] for r in rows] == [f"Candidate {n}" for n in range(5)]
    assert rows[0]["status"] == "done" and rows[0]["completedAt"] is not None
    assert all(r["status"] == "open" for r in rows[1:])
    with desk.sessions() as db:
        ensure_allocations(db, now=desk.clock["now"], tz=TZ)  # the finished one still counts towards today's five
        db.commit()
        assert db.scalar(select(func.count()).select_from(DailyWorkItem)) == 5
    summary = summarise(rows, desk.clock["now"], "2026-11-03")
    assert (summary["total"], summary["done"], summary["open"], summary["complete"]) == (5, 1, 4, False)
    assert summary["morning"] == {"done": 1, "total": 3} and summary["evening"] == {"done": 0, "total": 2}


def test_unmapping_or_reassigning_releases_the_slot_and_draws_a_replacement(desk):
    apps = mapped_candidates(desk, 7)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    assert desk.client.post(f"/api/applications/{apps['c1']}/unmap", json={}).status_code == 200
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET owner_id = 'ravi' WHERE application_id = :a"), {"a": apps["c2"]})
        db.commit()
    rows = rows_of(desk, "asha", TUESDAY)
    assert [r["candidate"] for r in rows] == ["Candidate 0", "Candidate 3", "Candidate 4"]  # c1 unmapped, c2 moved away
    with desk.sessions() as db:
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {"asha": 2, "ravi": 1}
        db.commit()
    names = [r["candidate"] for r in rows_of(desk, "asha", TUESDAY)]
    assert names == ["Candidate 0", "Candidate 5", "Candidate 6", "Candidate 3", "Candidate 4"]  # replacements take the freed slots
    assert [r["slotNo"] for r in rows_of(desk, "asha", TUESDAY)] == [1, 2, 3, 4, 5]
    assert [r["candidate"] for r in rows_of(desk, "ravi", TUESDAY)] == ["Candidate 2"]


# ------------------------------------------------------------------ carry-over

def test_finishing_four_of_five_gives_one_postponed_plus_five_new_the_next_day(desk):
    apps = mapped_candidates(desk, 12)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    desk.clock["now"] = TUESDAY + timedelta(hours=6)
    for n in range(4):
        finish_stage_one(desk, apps[f"c{n}"])

    today = summarise(rows_of(desk, "asha", desk.clock["now"]), desk.clock["now"], "2026-11-03")
    assert (today["done"], today["open"], today["postponed"]) == (4, 1, 0)

    desk.clock["now"] = WEDNESDAY  # next morning, 08:30 local
    with desk.sessions() as db:
        assert ensure_allocations(db, now=WEDNESDAY, tz=TZ) == {"asha": 5}
        db.commit()
    rows = rows_of(desk, "asha", WEDNESDAY)
    assert len(rows) == 6  # 1 postponed + 5 new; the 4 finished yesterday are history
    assert rows[0]["candidate"] == "Candidate 4" and rows[0]["postponed"] and rows[0]["daysLate"] == 1
    assert [r["candidate"] for r in rows[1:]] == [f"Candidate {n}" for n in range(5, 10)]
    assert not any(r["postponed"] for r in rows[1:])
    summary = summarise(rows, WEDNESDAY, "2026-11-04")
    assert (summary["total"], summary["postponed"], summary["open"], summary["oldestPostponedDays"]) == (6, 1, 6, 1)
    assert summary["overdue"] == 1  # yesterday's deadline has long passed

    # Clearing the postponed candidate counts towards today, and the list does not grow back.
    finish_stage_one(desk, apps["c4"])
    rows = rows_of(desk, "asha", WEDNESDAY)
    assert rows[0]["status"] == "done" and not rows[0]["postponed"]
    after = summarise(rows, WEDNESDAY, "2026-11-04")
    assert (after["done"], after["postponed"], after["total"]) == (1, 0, 6)
    with desk.sessions() as db:
        assert ensure_allocations(db, now=WEDNESDAY, tz=TZ) == {}


def test_nothing_is_dropped_when_a_recruiter_falls_several_days_behind(desk):
    mapped_candidates(desk, 20)
    for day in range(3):
        with desk.sessions() as db:
            ensure_allocations(db, now=TUESDAY + timedelta(days=day), tz=TZ)  # 3 working days, nothing finished
            db.commit()
    rows = rows_of(desk, "asha", TUESDAY + timedelta(days=2))
    summary = summarise(rows, TUESDAY + timedelta(days=2), "2026-11-05")
    assert (summary["total"], summary["postponed"], summary["oldestPostponedDays"]) == (15, 10, 2)
    assert [r["daysLate"] for r in rows[:10]] == [2] * 5 + [1] * 5  # oldest backlog first


# ------------------------------------------------------------------ alerts

def test_overdue_slots_alert_the_owner_once_and_carry_over_alerts_supervisors_once(desk):
    mapped_candidates(desk, 6)
    supervisors = ["admin", "manager"]
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
        assert scan_daily_work(db, supervisors, now=ist(date(2026, 11, 3), 11, 0) + timedelta(minutes=1), tz=TZ)["dailyOverdue"] == 1
        db.commit()
        again = scan_daily_work(db, supervisors, now=ist(date(2026, 11, 3), 11, 30), tz=TZ)
        assert again["dailyOverdue"] == 0  # the 11:00 slot is only announced once
        db.commit()
        overdue = list(db.scalars(select(Notification).where(Notification.title == "Daily task overdue")))
        assert [n.user_id for n in overdue] == ["asha"] and "Candidate 0" in overdue[0].detail and "11:00" in overdue[0].detail
        assert overdue[0].kind == "daily_work" and overdue[0].link_to == "/tasks"
        assert scan_daily_work(db, supervisors, now=ist(date(2026, 11, 3), 17, 0), tz=TZ)["dailyOverdue"] == 4  # the rest, none repeated
        db.commit()

        # Next morning: five unfinished candidates are carried over, announced once to the owner and to supervisors.
        counts = scan_daily_work(db, supervisors, now=WEDNESDAY, tz=TZ)
        assert counts["dailyPostponed"] == 5 and counts["dailyAllocated"] == 1  # six were mapped and five used: one fresh candidate is left
        db.commit()
        carried = list(db.scalars(select(Notification).where(Notification.title.in_(["Unfinished tasks carried over", "Asha: tasks postponed"]))))
        assert sorted(n.user_id for n in carried) == ["admin", "asha", "manager"]
        assert scan_daily_work(db, supervisors, now=WEDNESDAY + timedelta(minutes=10), tz=TZ)["dailyPostponed"] == 0
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.title.like("%carried over%"))) == 1


def test_a_finished_candidate_never_raises_an_overdue_alert(desk):
    apps = mapped_candidates(desk, 5)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    desk.clock["now"] = TUESDAY + timedelta(hours=1)
    finish_stage_one(desk, apps["c0"])
    with desk.sessions() as db:
        counts = scan_daily_work(db, ["admin"], now=ist(date(2026, 11, 3), 11, 5), tz=TZ)
        assert counts["dailyOverdue"] == 0


# ------------------------------------------------------------------ API

def test_staff_see_only_their_own_list_and_supervisors_see_every_recruiter(desk):
    mapped_candidates(desk, 7)
    mapped_candidates(desk, 3, owner="ravi", start=20)
    as_user(desk, "asha")
    body = desk.client.get("/api/daily-work").json()
    assert body["agencyDate"] == "2026-11-03" and (body["dailyQuota"], body["morningQuota"], body["eveningQuota"]) == (5, 3, 2)
    assert len(body["items"]) == 5 and body["summary"]["total"] == 5 and "staff" not in body
    first = body["items"][0]
    assert (first["client"], first["country"], first["candidate"], first["position"], first["stage"]) == ("Gulf Builders", "Qatar", "Candidate 0", "Welder", 1)
    assert first["referral"]["label"] == "Walk-in" and first["status"] == "open" and first["remark"] is None
    assert desk.client.get("/api/daily-work?staff=ravi").status_code == 403
    assert desk.client.get("/api/daily-work?staff=asha").status_code == 200

    as_user(desk, "manager")
    team = desk.client.get("/api/daily-work").json()
    assert {s["userId"]: s["summary"]["total"] for s in team["staff"]} == {"asha": 5, "ravi": 3}
    assert len(team["items"]) == 8 and team["summary"]["total"] == 8
    only = desk.client.get("/api/daily-work?staff=ravi").json()
    assert {i["ownerId"] for i in only["items"]} == {"ravi"} and only["summary"]["total"] == 3
    assert {s["userId"] for s in only["staff"]} == {"asha", "ravi"}  # the board always shows everyone
    assert desk.client.get("/api/daily-work?staff=nobody").status_code == 404


def test_a_remark_added_through_the_task_shows_in_the_daily_row(desk):
    mapped_candidates(desk, 1)
    as_user(desk, "asha")
    task_id = desk.client.get("/api/daily-work").json()["items"][0]["taskId"]
    with desk.sessions() as db:
        db.add(Remark(task_id=task_id, body="Called; passport tomorrow", author_id="asha"))
        db.commit()
    row = desk.client.get("/api/daily-work").json()["items"][0]
    assert row["remark"]["body"] == "Called; passport tomorrow" and row["remark"]["count"] == 1 and row["remark"]["by"] == "Asha"


def finish(env, app_id, stage):
    """Complete any of the first three stages through the real endpoints."""
    values = {1: {"result": "passed"}, 2: {"forwarded": "yes"},
              3: {"scheduled": "yes", "date": "2026-11-10", "time": "10:00", "mode": "online"}}[stage]
    version = env.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]
    saved = env.client.put(f"/api/applications/{app_id}/draft", json={"data": values, "lock_version": version})
    assert saved.status_code == 200, saved.text
    done = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert done.status_code == 200, done.text


def item_rows(env):
    with env.sessions() as db:
        return {r.candidate_id: r for r in db.scalars(select(DailyWorkItem).order_by(DailyWorkItem.slot_no))}


# ------------------------------------------------------------------ stored lifecycle timestamps

def test_every_moment_of_an_item_is_stored_for_reports(desk):
    apps = mapped_candidates(desk, 8)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    first = item_rows(desk)["c0"]
    assert first.status == "open" and first.allocated_at == TUESDAY
    assert first.started_at is None and first.completed_at is None and first.overdue_at is None and first.postponed_at is None

    # Saving a draft starts the work; finishing the stage completes the item in the same transaction.
    desk.clock["now"] = ist(date(2026, 11, 3), 10, 15)
    version = desk.client.get(f"/api/applications/{apps['c0']}/draft").json()["lockVersion"]
    assert desk.client.put(f"/api/applications/{apps['c0']}/draft", json={"data": {"result": "passed"}, "lock_version": version}).status_code == 200
    assert item_rows(desk)["c0"].started_at == ist(date(2026, 11, 3), 10, 15)
    desk.clock["now"] = ist(date(2026, 11, 3), 10, 40)
    version = desk.client.get(f"/api/applications/{apps['c0']}/draft").json()["lockVersion"]
    assert desk.client.post(f"/api/applications/{apps['c0']}/complete", json={"lock_version": version}).status_code == 200
    done = item_rows(desk)["c0"]
    assert (done.status, done.completed_at, done.completed_by, done.overdue_at) == ("completed", ist(date(2026, 11, 3), 10, 40), "asha", None)

    # c1 is finished after its 12:00 slot: stored as late. c2 stays open past 13:00: overdue once reconciled.
    desk.clock["now"] = ist(date(2026, 11, 3), 12, 20)
    finish(desk, apps["c1"], 1)
    with desk.sessions() as db:
        reconcile_at = ist(date(2026, 11, 3), 13, 5)
        daily_work.reconcile(db, now=reconcile_at, tz=TZ)
        db.commit()
    rows = item_rows(desk)
    assert rows["c1"].completed_at == ist(date(2026, 11, 3), 12, 20) and rows["c1"].overdue_at == rows["c1"].due_at == ist(date(2026, 11, 3), 12, 0)
    assert rows["c1"].started_at == rows["c1"].completed_at or rows["c1"].started_at < rows["c1"].completed_at
    assert rows["c2"].status == "open" and rows["c2"].overdue_at == ist(date(2026, 11, 3), 13, 0) and rows["c2"].postponed_at is None

    # The next morning the unfinished ones are stored as postponed from midnight, and a remark by the owner is the start.
    with desk.sessions() as db:
        db.add(Remark(task_id=db.scalar(select(Task.id).where(Task.application_id == apps["c3"])), body="Called", author_id="asha",
                      created_at=ist(date(2026, 11, 3), 14, 0)))
        db.commit()
        daily_work.reconcile(db, now=WEDNESDAY, tz=TZ)
        db.commit()
    rows = item_rows(desk)
    midnight = ist(date(2026, 11, 4), 0, 0)
    assert rows["c3"].postponed_at == midnight and rows["c3"].started_at == ist(date(2026, 11, 3), 14, 0)
    assert rows["c0"].postponed_at is None  # finished within its day


def test_cancelled_or_reassigned_items_are_stored_as_released(desk):
    apps = mapped_candidates(desk, 6)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    assert desk.client.post(f"/api/applications/{apps['c1']}/unmap", json={}).status_code == 200
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET owner_id = 'ravi' WHERE application_id = :a"), {"a": apps["c2"]})
        db.commit()
        daily_work.reconcile(db, now=TUESDAY + timedelta(minutes=5), tz=TZ)
        db.commit()
    rows = item_rows(desk)
    assert (rows["c1"].status, rows["c1"].release_reason) == ("released", "task_cancelled")
    assert (rows["c2"].status, rows["c2"].release_reason) == ("released", "reassigned")
    assert rows["c1"].released_at == TUESDAY + timedelta(minutes=5) and rows["c0"].status == "open"
    # Handing the task back makes it eligible for a fresh item instead of being blocked by the released one.
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET owner_id = 'asha' WHERE application_id = :a"), {"a": apps["c2"]})
        db.commit()
        assert ensure_allocations(db, now=TUESDAY + timedelta(minutes=10), tz=TZ)["asha"] == 2  # replaces c1 and c2


# ------------------------------------------------------------------ goals

def test_goals_are_data_the_admin_can_set_for_the_agency_and_per_person(desk):
    mapped_candidates(desk, 8)
    mapped_candidates(desk, 6, owner="ravi", start=30)
    as_user(desk, "manager")
    table = desk.client.get("/api/daily-goals").json()
    assert table["default"] == {"candidates": 5, "morning": 3, "evening": 2, "forwardedToClient": 2, "stageMoves": 5}  # seeded rows
    assert {s["userId"] for s in table["staff"]} == {"asha", "ravi"} and all(s["overrides"] == [] for s in table["staff"])
    assert desk.client.put("/api/daily-goals/default", json={"candidates": 4, "morning": 1}).status_code == 403  # admin only

    as_user(desk, "admin")
    assert desk.client.put("/api/daily-goals/default", json={"candidates": 2, "morning": 3}).status_code == 422
    assert desk.client.put("/api/daily-goals/default", json={"candidates": 4}).status_code == 422  # morning goes with it
    assert desk.client.put("/api/daily-goals/default", json={}).status_code == 422
    assert desk.client.put("/api/daily-goals/default", json={"candidates": 0, "morning": 0}).status_code == 422
    saved = desk.client.put("/api/daily-goals/default", json={"candidates": 4, "morning": 1, "forwardedToClient": 3})
    assert saved.status_code == 200 and saved.json() == {"candidates": 4, "morning": 1, "evening": 3, "forwardedToClient": 3, "stageMoves": 5}
    own = desk.client.put("/api/daily-goals/staff/ravi", json={"candidates": 2, "morning": 2, "stageMoves": 9}).json()
    assert own["goals"] == {"candidates": 2, "morning": 2, "evening": 0, "forwardedToClient": 3, "stageMoves": 9}
    assert own["overrides"] == ["candidates", "stage_moves"]
    assert desk.client.put("/api/daily-goals/staff/admin", json={"stageMoves": 1}).status_code == 404

    as_user(desk, "asha")
    a = desk.client.get("/api/daily-work").json()
    assert (a["dailyQuota"], a["morningQuota"], len(a["items"])) == (4, 1, 4)
    assert [i["session"] for i in a["items"]] == ["morning", "evening", "evening", "evening"]
    as_user(desk, "ravi")
    r = desk.client.get("/api/daily-work").json()
    assert (r["dailyQuota"], r["morningQuota"], len(r["items"])) == (2, 2, 2) and r["goals"]["stageMoves"]["target"] == 9

    as_user(desk, "admin")
    reset = desk.client.delete("/api/daily-goals/staff/ravi").json()
    assert reset["overrides"] == [] and reset["goals"]["candidates"] == 4
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DailyGoal).where(DailyGoal.user_id == "ravi")) == 0


def test_goal_progress_counts_the_real_stage_work_and_is_snapshotted_per_day(desk):
    apps = mapped_candidates(desk, 6)
    as_user(desk, "asha")
    desk.client.get("/api/daily-work")  # allocate
    finish(desk, apps["c0"], 1)
    finish(desk, apps["c1"], 1)
    finish(desk, apps["c1"], 2)  # forwarded to the client
    body = desk.client.get("/api/daily-work").json()
    goals = body["goals"]
    assert goals["candidates"] == {"target": 5, "morning": 3, "evening": 2, "done": 2, "morningDone": 2, "eveningDone": 0}
    assert goals["forwardedToClient"] == {"target": 2, "actual": 1}
    assert goals["stageMoves"] == {"target": 5, "actual": 3}
    with desk.sessions() as db:
        snap = {r.metric: (r.target, r.morning_target, r.actual, r.morning_actual) for r in db.scalars(
            select(DailyGoalProgress).where(DailyGoalProgress.user_id == "asha", DailyGoalProgress.goal_date == date(2026, 11, 3)))}
    assert snap == {"candidates": (5, 3, 2, 2), "forwarded_to_client": (2, None, 1, None), "stage_moves": (5, None, 3, None)}

    # A later change of the goals does not rewrite a finished day, and the snapshot follows the new day.
    as_user(desk, "admin")
    desk.client.put("/api/daily-goals/default", json={"stageMoves": 8})
    desk.clock["now"] = WEDNESDAY
    as_user(desk, "asha")
    desk.client.get("/api/daily-work")
    with desk.sessions() as db:
        rows = {(r.goal_date, r.metric): (r.target, r.actual) for r in db.scalars(select(DailyGoalProgress).where(DailyGoalProgress.user_id == "asha"))}
    assert rows[(date(2026, 11, 3), "stage_moves")] == (5, 3)
    assert rows[(date(2026, 11, 4), "stage_moves")] == (8, 0)


def test_hourly_checkpoints_show_met_late_missed_and_upcoming(desk):
    apps = mapped_candidates(desk, 5)
    as_user(desk, "asha")
    desk.client.get("/api/daily-work")
    desk.clock["now"] = ist(date(2026, 11, 3), 10, 30)
    finish(desk, apps["c0"], 1)  # before 11:00: met
    desk.clock["now"] = ist(date(2026, 11, 3), 12, 30)
    finish(desk, apps["c1"], 1)  # after 12:00: late; c2 (13:00) still upcoming
    body = desk.client.get("/api/daily-work").json()
    assert [p["state"] for p in body["checkpoints"]["points"]] == ["met", "late", "upcoming", "upcoming", "upcoming"]
    assert (body["checkpoints"]["expectedByNow"], body["checkpoints"]["doneToday"], body["checkpoints"]["behindBy"]) == (2, 2, 0)
    desk.clock["now"] = ist(date(2026, 11, 3), 15, 30)
    cp = desk.client.get("/api/daily-work").json()["checkpoints"]
    assert [p["state"] for p in cp["points"]] == ["met", "late", "missed", "missed", "upcoming"]
    assert (cp["expectedByNow"], cp["doneToday"], cp["behindBy"]) == (4, 2, 2)
    as_user(desk, "manager")
    assert desk.client.get("/api/daily-work?staff=all").json()["checkpoints"] is None  # only meaningful for one person
    assert desk.client.get("/api/daily-work?staff=asha").json()["checkpoints"]["behindBy"] == 2


# ------------------------------------------------------------------ schema

def test_migration_adds_three_tables_seeds_the_agency_goals_and_downgrades_cleanly(schema_database):
    engine = schema_database
    _migrate(engine, "0019_job_categories")
    before = set(inspect(engine).get_table_names())
    assert "workflow_daily_work_items" not in before
    _migrate(engine, "head")
    added = {"workflow_daily_work_items", "workflow_daily_goals", "workflow_daily_goal_progress"}
    assert added <= set(inspect(engine).get_table_names()) - before  # later migrations may add their own tables too
    with engine.connect() as connection:
        seeded = connection.execute(text("SELECT metric, target, morning_target FROM workflow_daily_goals WHERE user_id IS NULL ORDER BY metric")).all()
    assert seeded == [("candidates", 5, 3), ("forwarded_to_client", 2, None), ("stage_moves", 5, None)]
    with engine.begin() as connection:
        connection.execute(text("UPDATE workflow_daily_goals SET target = 7, morning_target = 4 WHERE metric = 'candidates'"))
        # Pretend the revision was lost after the tables were made: running 0020 again must keep the edit.
        connection.execute(text("UPDATE alembic_version SET version_num = '0019_job_categories'"))
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*), max(target) FROM workflow_daily_goals WHERE metric = 'candidates'")).one() == (1, 7)
    with engine.begin() as connection:  # the database itself refuses nonsense
        for bad in ("INSERT INTO workflow_daily_goals (id, metric, target) VALUES ('x', 'bogus', 1)",
                    "INSERT INTO workflow_daily_goals (id, metric, target, morning_target) VALUES ('y', 'candidates', 3, 4)",
                    "INSERT INTO workflow_daily_goals (id, metric, target) VALUES ('z', 'candidates', 3)"):
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(text(bad))
    _migrate(engine, "0019_job_categories", downgrade=True)
    assert not added & set(inspect(engine).get_table_names())  # the three tables are gone, nothing else of ours is left
    _migrate(engine, "head")
    assert added <= set(inspect(engine).get_table_names())


def test_the_item_table_is_announced_to_live_screens():
    from app.services.change_feed import TOPIC_TABLES
    from app.services.change_feed import IGNORED_TABLES
    assert TOPIC_TABLES["workflow_daily_work_items"] == ("tasks",) and TOPIC_TABLES["workflow_daily_goals"] == ("tasks",)
    assert "workflow_daily_goal_progress" in IGNORED_TABLES  # rewritten by reads, so it must not wake every desk


def test_team_view_shows_the_per_recruiter_quota_and_labels_the_sum_as_a_team_total(desk):
    mapped_candidates(desk, 7)
    mapped_candidates(desk, 6, owner="ravi", start=40)
    as_user(desk, "admin")
    team = desk.client.get("/api/daily-work").json()
    # The quota is what each recruiter gets (5 = 3 + 2), never the sum across recruiters.
    assert (team["dailyQuota"], team["morningQuota"], team["eveningQuota"]) == (5, 3, 2)
    assert (team["goalsMode"], team["staffCount"], team["targetsVary"]) == ("average", 2, False)
    assert team["goals"]["candidates"] == {"target": 5, "morning": 3, "evening": 2, "done": 0, "morningDone": 0, "eveningDone": 0}
    assert team["goals"]["forwardedToClient"]["target"] == 2 and team["goals"]["stageMoves"]["target"] == 5
    assert team["teamGoals"]["candidates"]["target"] == 10 and team["teamGoals"]["forwardedToClient"]["target"] == 4  # the labelled total

    one = desk.client.get("/api/daily-work?staff=ravi").json()
    assert (one["goalsMode"], one["staffCount"], one["dailyQuota"]) == ("person", 1, 5)
    assert one["goals"]["candidates"]["target"] == 5 and one["teamGoals"]["candidates"]["target"] == 5

    # An override makes the average only approximate, and says so.
    assert desk.client.put("/api/daily-goals/staff/ravi", json={"candidates": 3, "morning": 1}).status_code == 200
    mixed = desk.client.get("/api/daily-work").json()
    assert mixed["targetsVary"] is True and (mixed["dailyQuota"], mixed["morningQuota"]) == (4, 2)
    assert desk.client.get("/api/daily-work?staff=ravi").json()["dailyQuota"] == 3
    assert desk.client.get("/api/daily-work?staff=asha").json()["dailyQuota"] == 5


def test_a_recruiter_with_nothing_to_allocate_is_marked_so_and_day_off_is_explained(desk):
    mapped_candidates(desk, 2)
    as_user(desk, "manager")
    staff = {s["userId"]: s for s in desk.client.get("/api/daily-work").json()["staff"]}
    assert staff["ravi"]["summary"]["total"] == 0 and staff["ravi"]["offToday"] is False  # nothing waiting, not a day off
    assert staff["ravi"]["shortfall"] == {"target": 5, "allocated": 0, "reason": "No candidates assigned to this staff member. Assign candidates from the Data Bank."}
    assert staff["asha"]["summary"]["total"] == 2
    assert staff["asha"]["shortfall"]["allocated"] == 2 and "Only 2 candidates are waiting" in staff["asha"]["shortfall"]["reason"]
    desk.clock["now"] = ist(date(2026, 11, 8), 9, 0)  # Sunday, the default weekly off
    sunday = {s["userId"]: s for s in desk.client.get("/api/daily-work").json()["staff"]}
    assert sunday["ravi"]["offToday"] is True and sunday["asha"]["offToday"] is True
    assert sunday["ravi"]["shortfall"] is None  # a day off is not a shortage


# ------------------------------------------------------------------ owned candidates who are not matched yet

def owned_candidates(env, count, *, owner="asha", start=100, **fields):
    """Candidates the recruiter owns that no order has been matched to; ages are spread so 'oldest first' is testable."""
    ids = []
    with env.sessions() as db:
        for n in range(start, start + count):
            db.add(Candidate(id=f"u{n}", ref=f"CND-U{n}", name=f"Owned {n}", phone=f"91000{n:05}", source=Source.walk_in, owner_id=owner,
                             designations=["Mason"], created_at=TUESDAY - timedelta(days=60 - n), **fields))
            ids.append(f"u{n}")
        db.commit()
    return ids


def record_call(env, candidate_id, interested="yes", called=True):
    return env.client.post(f"/api/candidates/{candidate_id}/call", json={"called": called, "interested": interested})


def test_every_recruiter_gets_five_owned_candidates_in_morning_and_evening_slots(desk):
    owned_candidates(desk, 6)
    with desk.sessions() as db:  # none of these may ever be handed out
        db.add_all([
            Candidate(id="dep", ref="CND-DEP", name="Deployed", phone="9200000001", source=Source.walk_in, owner_id="asha", status="deployed", created_at=TUESDAY - timedelta(days=90)),
            Candidate(id="ni", ref="CND-NI", name="Not interested", phone="9200000002", source=Source.walk_in, owner_id="asha", status="not_interested", created_at=TUESDAY - timedelta(days=90)),
            Candidate(id="none", ref="CND-NO", name="No owner", phone="9200000003", source=Source.walk_in, owner_id=None, created_at=TUESDAY - timedelta(days=90)),
            Candidate(id="called", ref="CND-CA", name="Already called", phone="9200000004", source=Source.walk_in, owner_id="asha", created_at=TUESDAY - timedelta(days=90),
                      call_called=True, call_interested="yes", call_recorded_at=TUESDAY - timedelta(days=5)),
        ])
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {"asha": 5}
        db.commit()
    rows = rows_of(desk, "asha", TUESDAY)
    assert [r["candidateId"] for r in rows] == ["u100", "u101", "u102", "u103", "u104"]  # oldest first; the 6th waits
    assert [r["kind"] for r in rows] == ["call"] * 5 and all(r["applicationId"] is None and r["stage"] is None for r in rows)
    assert [r["session"] for r in rows] == ["morning"] * 3 + ["evening"] * 2
    assert [r["dueAt"].astimezone(ZoneInfoIST).strftime("%H:%M") for r in rows] == ["11:00", "12:00", "13:00", "15:00", "16:00"]
    assert (rows[0]["client"], rows[0]["country"], rows[0]["position"], rows[0]["orderRef"]) == (None, None, "Mason", None)  # the form's preferred job
    assert rows_of(desk, "ravi", TUESDAY) == []


def test_an_unmatched_candidates_row_falls_back_on_the_form_preferences_and_says_so(desk):
    with desk.sessions() as db:
        base = dict(source=Source.walk_in, owner_id="asha")
        db.add_all([
            Candidate(id="p1", ref="CND-P1", name="Prefers", phone="9300000001", designations=["Welder", "Fitter"], skills=["Rigging"],
                      country_preference=" Qatar ", current_city="Kochi", created_at=TUESDAY - timedelta(days=50), **base),
            Candidate(id="p2", ref="CND-P2", name="Skills only", phone="9300000002", designations=[], skills=["Rigging", "Welding"],
                      current_city="Dubai, UAE", created_at=TUESDAY - timedelta(days=49), **base),
            Candidate(id="p3", ref="CND-P3", name="Blank form", phone="9300000003", designations=[], skills=[], country_preference="  ",
                      created_at=TUESDAY - timedelta(days=48), **base),
        ])
        db.commit()
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    by_id = {r["candidateId"]: r for r in rows_of(desk, "asha", TUESDAY)}
    a, b, c = by_id["p1"], by_id["p2"], by_id["p3"]
    assert (a["country"], a["countrySource"], a["countryIsPreferred"], a["locationHint"]) == ("Qatar", "preference", True, None)
    assert (a["position"], a["positionSource"], a["positionIsPreferred"]) == ("Welder", "preference", True)
    assert (a["client"], a["clientContact"], a["orderRef"]) == (None, None, None)  # a client exists only once matched
    assert (b["country"], b["countrySource"], b["countryIsPreferred"]) == (None, None, False)
    assert b["locationHint"] == "Dubai, UAE"  # the screen may still recognise a country in the current location
    assert (b["position"], b["positionSource"], b["positionIsPreferred"]) == ("Rigging", "skill", True)
    assert (c["country"], c["countrySource"], c["countryIsPreferred"], c["locationHint"]) == (None, None, False, None)
    assert (c["position"], c["positionSource"], c["positionIsPreferred"]) == (None, None, False)


def test_a_matched_candidate_keeps_the_orders_values_whatever_the_form_prefers(desk):
    mapped_candidates(desk, 1)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET country_preference = 'Oman', designations = '[\"Mason\"]'::jsonb WHERE id = 'c0'"))
        db.commit()
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    row = rows_of(desk, "asha", TUESDAY)[0]
    assert (row["kind"], row["client"], row["country"], row["position"]) == ("stage", "Gulf Builders", "Qatar", "Welder")
    assert (row["countrySource"], row["countryIsPreferred"], row["positionSource"], row["positionIsPreferred"], row["locationHint"]) == ("order", False, "order", False, None)


def test_matched_candidates_come_first_then_owned_unmatched_ones(desk):
    mapped_candidates(desk, 2)
    owned_candidates(desk, 6)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    rows = rows_of(desk, "asha", TUESDAY)
    assert [(r["kind"], r["candidateId"]) for r in rows] == [("stage", "c0"), ("stage", "c1"), ("call", "u100"), ("call", "u101"), ("call", "u102")]
    assert rows[0]["client"] == "Gulf Builders" and rows[0]["stage"] == 1


def test_the_sixth_candidate_is_allocated_the_next_day_once_the_first_five_are_finished(desk):
    owned_candidates(desk, 6)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    as_user(desk, "asha")
    desk.clock["now"] = TUESDAY + timedelta(hours=3)
    for n in range(100, 105):  # the real initial-call flow
        assert record_call(desk, f"u{n}").status_code == 200
    rows = rows_of(desk, "asha", desk.clock["now"])
    assert all(r["status"] == "done" for r in rows) and len(rows) == 5
    with desk.sessions() as db:
        daily_work.reconcile(db, now=desk.clock["now"], tz=TZ)
        db.commit()
    done = item_rows(desk)["u100"]
    assert done.status == "completed" and done.completed_at is not None and done.kind == "call"
    with desk.sessions() as db:
        assert ensure_allocations(db, now=desk.clock["now"], tz=TZ) == {}  # five done = the day's quota; nothing more today
        assert ensure_allocations(db, now=WEDNESDAY, tz=TZ) == {"asha": 5}  # the 6th owned candidate, then those awaiting a match
        db.commit()
    rows = rows_of(desk, "asha", WEDNESDAY)
    # Never-worked candidates come first (the 6th), then the called-and-interested ones that are still unmatched.
    assert [(r["candidateId"], r["slotNo"], r["session"], r["postponed"]) for r in rows] == [
        ("u105", 1, "morning", False), ("u100", 2, "morning", False), ("u101", 3, "morning", False),
        ("u102", 4, "evening", False), ("u103", 5, "evening", False)]


def test_unfinished_owned_candidates_carry_over_without_being_duplicated(desk):
    owned_candidates(desk, 7)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    as_user(desk, "asha")
    desk.clock["now"] = TUESDAY + timedelta(hours=3)
    for n in range(100, 103):
        assert record_call(desk, f"u{n}").status_code == 200  # 3 of 5 finished
    with desk.sessions() as db:
        ensure_allocations(db, now=WEDNESDAY, tz=TZ)
        db.commit()
    rows = rows_of(desk, "asha", WEDNESDAY)
    assert [(r["candidateId"], r["postponed"], r["daysLate"]) for r in rows] == [
        ("u103", True, 1), ("u104", True, 1),  # unfinished yesterday, carried over
        ("u105", False, 0), ("u106", False, 0),  # never worked: first
        ("u100", False, 0), ("u101", False, 0), ("u102", False, 0)]  # called and interested, still unmatched: they stay in the pool
    assert len({r["candidateId"] for r in rows}) == len(rows)


def test_an_unanswered_call_does_not_finish_the_work_but_matching_and_a_stage_does(desk):
    owned_candidates(desk, 2)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    as_user(desk, "asha")
    assert record_call(desk, "u100", interested="unreachable", called=False).status_code == 200
    rows = rows_of(desk, "asha", TUESDAY)
    assert rows[0]["status"] == "open"  # an unreachable candidate stays on the list
    # Matched to an order and the first stage finished: that finishes the owned-candidate item too.
    app_id = desk.client.post("/api/applications", json={"candidate_id": "u101", "order_id": "order"}).json()["id"]
    shown = rows_of(desk, "asha", TUESDAY)
    assert [r["status"] for r in shown] == ["open", "open"]
    assert shown[1]["applicationId"] == app_id and shown[1]["stage"] == 1 and shown[1]["client"] == "Gulf Builders"  # the row now opens the stage
    with desk.sessions() as db:
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {}  # the matched candidate's stage task is not handed out twice
        db.commit()
    finish(desk, app_id, 1)
    with desk.sessions() as db:
        daily_work.reconcile(db, now=TUESDAY + timedelta(hours=1), tz=TZ)
        db.commit()
    assert item_rows(desk)["u101"].status == "completed"
    assert [r["status"] for r in rows_of(desk, "asha", TUESDAY + timedelta(hours=1))] == ["open", "done"]


def test_owned_candidate_items_are_released_when_the_candidate_moves_or_closes(desk):
    owned_candidates(desk, 7)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
        db.execute(text("UPDATE crm_candidates SET owner_id = 'ravi' WHERE id = 'u100'"))
        db.execute(text("UPDATE crm_candidates SET status = 'deployed' WHERE id = 'u101'"))
        db.commit()
        assert ensure_allocations(db, now=TUESDAY + timedelta(minutes=5), tz=TZ) == {"asha": 2, "ravi": 1}  # replacements; ravi gets his new candidate
        db.commit()
        reasons = {(r.candidate_id, r.owner_id): (r.status, r.release_reason) for r in db.scalars(select(DailyWorkItem))}
    assert reasons[("u100", "asha")] == ("released", "reassigned") and reasons[("u101", "asha")][0] == "released"
    assert [r["candidateId"] for r in rows_of(desk, "asha", TUESDAY)] == ["u105", "u106", "u102", "u103", "u104"]  # replacements take the freed slots
    assert [r["candidateId"] for r in rows_of(desk, "ravi", TUESDAY)] == ["u100"]


def test_a_recruiter_with_few_candidates_is_told_why_and_the_admin_sees_the_warning(desk):
    owned_candidates(desk, 2, owner="ravi")
    as_user(desk, "asha")
    body = desk.client.get("/api/daily-work").json()
    assert body["items"] == [] and body["summary"]["total"] == 0
    assert body["shortfall"] == {"target": 5, "allocated": 0, "reason": "No candidates assigned to this staff member. Assign candidates from the Data Bank."}
    as_user(desk, "ravi")
    mine = desk.client.get("/api/daily-work").json()
    assert len(mine["items"]) == 2 and mine["shortfall"]["allocated"] == 2 and "Only 2 candidates are waiting" in mine["shortfall"]["reason"]
    as_user(desk, "admin")
    board = {s["userId"]: s["shortfall"] for s in desk.client.get("/api/daily-work").json()["staff"]}
    assert board["asha"]["allocated"] == 0 and board["ravi"]["allocated"] == 2


def test_opening_the_list_keeps_a_real_transaction_because_it_allocates():
    # GETs run on autocommit connections unless marked; the allocation uses savepoints and advisory locks.
    assert getattr(daily_router.daily_work_today, "writes_on_get", False) is True


def test_postponed_work_is_counted_in_the_shortage_message(desk):
    owned_candidates(desk, 2)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    as_user(desk, "manager")
    desk.clock["now"] = WEDNESDAY
    # Both were left unfinished; nobody else is waiting, so tomorrow has 2 postponed and no new candidate.
    ravi_less = {s["userId"]: s for s in desk.client.get("/api/daily-work").json()["staff"]}["asha"]
    assert ravi_less["summary"]["postponed"] == 2
    assert ravi_less["shortfall"] == {"target": 5, "allocated": 0,
                                      "reason": "Only 0 new candidates today besides 2 postponed. Assign more candidates from the Data Bank."}


def test_a_candidate_is_never_allocated_twice_or_held_by_two_open_items(desk):
    # c0 is matched (stage task, owner asha); u100 is owned and unmatched. Two recruiters, repeated top-ups, a
    # stage task whose owner differs from the candidate's owner: still one open item per candidate.
    mapped_candidates(desk, 3)
    owned_candidates(desk, 3)
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET owner_id = 'ravi' WHERE application_id = (SELECT id FROM crm_applications WHERE candidate_id = 'c2')"))
        db.commit()
        for minutes in (0, 1, 2):
            ensure_allocations(db, now=TUESDAY + timedelta(minutes=minutes), tz=TZ)
            db.commit()
        for day in (1, 2, 3):
            ensure_allocations(db, now=TUESDAY + timedelta(days=day), tz=TZ)
            db.commit()
        open_per_candidate = dict(db.execute(text(
            "SELECT candidate_id, count(*) FROM workflow_daily_work_items WHERE status = 'open' GROUP BY candidate_id")).all())
        per_day = db.execute(text(
            "SELECT work_date, candidate_id, count(*) FROM workflow_daily_work_items WHERE status <> 'released' GROUP BY 1, 2 HAVING count(*) > 1")).all()
    assert open_per_candidate and set(open_per_candidate.values()) == {1}
    assert per_day == []


def test_called_and_interested_candidates_stay_on_the_list_until_matched_to_an_order(desk):
    owned_candidates(desk, 1)
    with desk.sessions() as db:
        ensure_allocations(db, now=TUESDAY, tz=TZ)
        db.commit()
    as_user(desk, "asha")
    desk.clock["now"] = TUESDAY + timedelta(hours=2)
    assert record_call(desk, "u100", interested="yes").status_code == 200
    assert rows_of(desk, "asha", desk.clock["now"])[0]["status"] == "done"  # today's call work is done

    # Next day the same candidate is on the list again: interested, but nobody has matched them to an order yet.
    with desk.sessions() as db:
        assert ensure_allocations(db, now=WEDNESDAY, tz=TZ) == {"asha": 1}
        db.commit()
    again = rows_of(desk, "asha", WEDNESDAY)
    assert [(r["candidateId"], r["status"], r["workDate"]) for r in again] == [("u100", "open", "2026-11-04")]  # the old call does not count
    with desk.sessions() as db:
        assert ensure_allocations(db, now=WEDNESDAY + timedelta(hours=1), tz=TZ) == {}  # once a day, never twice

    # Matching them and finishing a stage is what finishes this item, and then they are a stage candidate.
    desk.clock["now"] = WEDNESDAY + timedelta(hours=1)
    app_id = desk.client.post("/api/applications", json={"candidate_id": "u100", "order_id": "order"}).json()["id"]
    assert rows_of(desk, "asha", desk.clock["now"])[0]["applicationId"] == app_id
    finish(desk, app_id, 1)
    with desk.sessions() as db:
        daily_work.reconcile(db, now=desk.clock["now"], tz=TZ)
        db.commit()
    assert item_rows(desk)["u100"].status == "completed"


def test_only_not_interested_or_deployed_candidates_leave_the_list(desk):
    owned_candidates(desk, 3)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_called = true, call_interested = 'yes', call_recorded_at = :at WHERE id = 'u100'"), {"at": TUESDAY - timedelta(days=2)})
        db.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'u101'"))
        db.execute(text("UPDATE crm_candidates SET status = 'deployed' WHERE id = 'u102'"))
        db.commit()
        assert ensure_allocations(db, now=TUESDAY, tz=TZ) == {"asha": 1}
        db.commit()
    assert [r["candidateId"] for r in rows_of(desk, "asha", TUESDAY)] == ["u100"]  # called and interested: still worked


def test_upgrading_a_database_that_already_has_the_first_daily_work_schema_keeps_its_rows(schema_database):
    """The committed 0020 schema (no kind column, task_id required) upgrades in place to the current head."""
    engine = schema_database
    _migrate(engine, "0020_daily_work_quota")
    cols = {c["name"]: c for c in inspect(engine).get_columns("workflow_daily_work_items")}
    assert "kind" not in cols and cols["task_id"]["nullable"] is False
    with engine.begin() as c:
        c.execute(text("INSERT INTO auth_users (id,name,email,role,active,receives_auto_assign,on_leave) VALUES ('u','U','u@example.test','staff',true,false,false)"))
        c.execute(text("INSERT INTO crm_candidates (id,ref,name,phone,source,status,needs_review) VALUES ('c','CND-1','Old','9000000001','walk_in','new',false)"))
        c.execute(text("INSERT INTO workflow_tasks (id,kind,title,candidate_id,owner_id,escalated) VALUES ('t','stage','Stage','c','u',false)"))
        c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,task_id,candidate_id,slot_no,session,due_at,status) "
                       "VALUES ('i','u','2026-11-02','t','c',1,'morning','2026-11-02 05:30+00','open')"))
    _migrate(engine, "head")
    cols = {c["name"]: c for c in inspect(engine).get_columns("workflow_daily_work_items")}
    assert cols["kind"]["nullable"] is False and cols["task_id"]["nullable"] is True
    with engine.connect() as c:
        assert c.execute(text("SELECT kind, task_id, status FROM workflow_daily_work_items WHERE id = 'i'")).one() == ("stage", "t", "open")
        assert c.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_daily_work_one_open_per_candidate'")) == 0
        assert c.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_daily_work_candidate_open'")) == 1
    with engine.begin() as c:  # the database now allows an owned-candidate item, and still refuses a stage item with no task
        c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,kind,candidate_id,slot_no,session,due_at) "
                       "VALUES ('j','u','2026-11-02','call','c',2,'morning','2026-11-02 06:30+00')"))
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,kind,candidate_id,slot_no,session,due_at) "
                           "VALUES ('k','u','2026-11-02','stage','c',3,'morning','2026-11-02 06:30+00')"))
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,kind,candidate_id,slot_no,session,due_at) "
                           "VALUES ('m','u','2026-11-02','bogus','c',4,'morning','2026-11-02 06:30+00')"))

    # A repeat is harmless; the downgrade refuses while owned-candidate items exist, then restores the old shape.
    with engine.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = '0022_form_options'"))
    _migrate(engine, "head")
    with pytest.raises(Exception, match="Cannot downgrade 0023"):
        _migrate(engine, "0022_form_options", downgrade=True)
    with engine.begin() as c:
        c.execute(text("DELETE FROM workflow_daily_work_items WHERE kind = 'call'"))
    _migrate(engine, "0022_form_options", downgrade=True)
    cols = {c["name"]: c for c in inspect(engine).get_columns("workflow_daily_work_items")}
    assert "kind" not in cols and cols["task_id"]["nullable"] is False
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM workflow_daily_work_items WHERE id = 'i'")) == 1  # the original row survived both ways
    _migrate(engine, "head")  # up again
    with engine.connect() as c:
        assert c.execute(text("SELECT kind FROM workflow_daily_work_items WHERE id = 'i'")).scalar() == "stage"


def test_daily_work_screen_stays_within_a_round_trip_budget(desk):
    """The screen used to run 26-28 statements one after another (about 4 s at a 100 ms round trip), then 16 in about nine
    rounds. The goals, the four attendance tables, owned counts, today's item counts and the progress inputs are now ONE
    statement, read beside the reconcile, the list's rows and the "who still has candidates" check (one round trip); only the
    placements and remarks behind the rows follow. Eight to ten statements in about four rounds is the repeat-call budget."""
    from sqlalchemy import event
    mapped_candidates(desk, 7)
    mapped_candidates(desk, 3, owner="ravi", start=20)
    engine = desk.sessions.kw["bind"]

    def statements(user_id, path="/api/daily-work"):
        as_user(desk, user_id)
        seen = []
        record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = desk.client.get(path)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200
        return seen, response.json()

    statements("asha")  # the first call allocates; the budget is for the usual repeat
    seen, body = statements("asha")
    assert len(seen) <= 10, [sql[:60] for sql in seen]
    assert sum("workflow_daily_goals" in sql for sql in seen) == 1  # goals are read once, not three times
    assert sum("staff_attendance_duty_plans" in sql for sql in seen) == 1  # and so are the attendance tables
    assert sum("crm_application_stages" in sql and "count" in sql.lower() for sql in seen) == 1  # both stage counts together
    assert len(body["items"]) == 5

    statements("manager")
    seen, team = statements("manager")
    # ravi has fewer candidates than his quota but nothing left to hand out: no lock, no top-up, no write on a repeat call
    assert len(seen) <= 12, " | ".join(sql[:48] for sql in seen)
    assert not any("pg_advisory_xact_lock" in sql for sql in seen)
    assert {s["userId"] for s in team["staff"]} == {"asha", "ravi"} and len(team["items"]) == 8
