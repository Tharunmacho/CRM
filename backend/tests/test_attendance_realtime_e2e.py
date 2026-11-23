"""Reports > Attendance, end to end through the API with a controlled clock: every step is visible in the very next read, "today" is today's
sessions and presence only, a shift that crosses midnight is split by one rule (the board splits it at the agency's midnight; the month
attributes it to the day it started), a shift never checked out counts no hours, and an online page that stopped sending heartbeats is
no longer online. Disposable PostgreSQL, like test_staff_activity.py."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Session as LoginSession, StaffAttendanceSession as Attendance, User
from app.services import attendance_hr as hr
from app.services import change_feed
from app.services import staff_activity as activity
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace as base_workspace  # noqa: F401  (fixture)

IST = timezone(timedelta(hours=5, minutes=30))
ZONE = "Asia/Kolkata"


def at(day, hour, minute=0, second=0):
    """An agency (IST) wall-clock time as the UTC instant the server stores."""
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST).astimezone(timezone.utc)


D20, D21, D22 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)


@pytest.fixture
def workspace(base_workspace):  # noqa: F811
    with base_workspace() as db:
        db.add_all([LoginSession(id="login-" + key, user_id=key, expires_at=at(D21, 0) + timedelta(days=365))
                    for key in ("owner", "other", "admin", "manager")])
        db.commit()
    return base_workspace


@pytest.fixture
def world(workspace, monkeypatch):
    """The attendance API mounted on a small app, a clock the test moves, and `act(who)` to sign in as someone."""
    from app.api.routers import attendance
    from app.core.deps import current_user
    from app.core.security import SESSION_COOKIE
    from app.db.session import get_db

    clock = {"now": at(D21, 10, 0)}
    monkeypatch.setattr(activity, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(hr, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(attendance, "get_settings", lambda: SimpleNamespace(agency_timezone=ZONE))
    who = {"id": "admin"}

    def database():
        with workspace() as db:
            yield db

    def current():
        with workspace() as db:
            return db.get(User, who["id"])

    app = FastAPI()
    app.include_router(attendance.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = current
    with TestClient(app) as client:
        def act(name):
            who["id"] = name
            client.cookies.set(SESSION_COOKIE, "login-" + name)

        def move(to):
            clock["now"] = to

        def board(day, user="owner"):
            """The admin's reading of one day for one person (every read must be uncached)."""
            previous = who["id"]
            act("admin")
            response = client.get("/api/attendance", params={"date": day.isoformat(), "staffId": user})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            act(previous)
            return response.json()["staff"][0]

        def month(user="owner", year=2026, number=10):
            previous = who["id"]
            act("admin")
            response = client.get("/api/attendance/month", params={"year": year, "month": number, "staffId": user})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            act(previous)
            person = response.json()["staff"][0]
            return {row["date"]: row for row in person["days"]}

        def pulse(name, client_id, sequence, *, age=0, visible=True):
            act(name)
            response = client.post("/api/presence/heartbeat", json={"clientId": client_id, "sequence": sequence, "visible": visible, "interactionAgeSeconds": age})
            assert response.status_code == 200, response.text

        def check_in(name):
            act(name)
            response = client.post("/api/attendance/check-in", json={"requestKey": str(uuid4())})
            assert response.status_code == 200, response.text
            return response.json()["attendance"]["sessionId"]

        def check_out(name, session_id):
            act(name)
            response = client.post("/api/attendance/check-out", json={"sessionId": session_id, "requestKey": str(uuid4())})
            assert response.status_code == 200, response.text

        yield SimpleNamespace(client=client, act=act, move=move, board=board, month=month, pulse=pulse, check_in=check_in,
                              check_out=check_out, workspace=workspace)


def test_every_step_is_visible_in_the_very_next_read_and_online_expires_with_the_heartbeat(world):
    page = str(uuid4())
    world.move(at(D21, 10, 0))
    # signing in and using the CRM is not attendance: nothing exists until the person checks in
    world.pulse("owner", page, 1)
    with world.workspace() as db:
        assert db.scalar(select(func.count()).select_from(Attendance)) == 0
    row = world.board(D21)
    assert row["attendance"]["status"] == "checked_out" and row["attendance"]["sessions"] == []
    assert row["presence"]["status"] == "online" and row["attendance"]["totalSeconds"] == 0

    # check in: the next board read shows it, with the hours running to "now"
    world.move(at(D21, 10, 0, 10))
    session_id = world.check_in("owner")
    world.move(at(D21, 10, 0, 40))
    row = world.board(D21)
    assert row["attendance"]["status"] == "checked_in" and row["attendance"]["sessionId"] == session_id
    assert row["attendance"]["totalSeconds"] == 30 and row["attendance"]["sessions"][0]["unclosed"] is False
    assert row["presence"]["status"] == "online"

    # the page stops sending heartbeats: 90 seconds later it is no longer online, while the person is still checked in
    world.pulse("owner", page, 2)           # last heartbeat at 10:00:40
    world.move(at(D21, 10, 2, 11))          # 91 seconds after it
    row = world.board(D21)
    assert row["presence"]["status"] == "offline" and row["attendance"]["status"] == "checked_in"
    # a heartbeat brings it back at once
    world.pulse("owner", page, 3)
    assert world.board(D21)["presence"]["status"] == "online"

    # check out two hours in: the hours are there in the very next read, and the month agrees about when the day started and ended
    world.move(at(D21, 12, 0, 10))
    world.check_out("owner", session_id)
    row = world.board(D21)
    assert row["attendance"]["status"] == "checked_out" and row["attendance"]["totalSeconds"] == 2 * 3600
    day = world.month()[D21.isoformat()]
    assert day["checkIn"] is not None and day["checkOut"] is not None
    assert day["checkIn"][:16] == row["attendance"]["sessions"][0]["checkedInAt"][:16]


def test_a_shift_across_agency_midnight_is_split_on_the_board_and_belongs_to_its_start_day_in_the_month(world):
    world.move(at(D21, 23, 50))
    session_id = world.check_in("other")
    world.move(at(D22, 0, 20))
    world.check_out("other", session_id)
    world.move(at(D22, 6, 0))
    first, second = world.board(D21, "other"), world.board(D22, "other")
    # the board counts the minutes inside each calendar day: 10 + 20 = the 30 minutes of the shift
    assert first["attendance"]["totalSeconds"] == 600 and second["attendance"]["totalSeconds"] == 1200
    assert first["attendance"]["totalSeconds"] + second["attendance"]["totalSeconds"] == 1800
    assert first["attendance"]["sessions"][0]["unclosed"] is False and second["attendance"]["sessions"][0]["unclosed"] is False
    # the month (policy) attributes the whole shift to the day it started
    days = world.month("other")
    assert days[D21.isoformat()]["checkIn"] is not None and days[D21.isoformat()]["checkOut"] is not None
    assert days[D22.isoformat()]["checkIn"] is None


def test_today_is_todays_sessions_and_presence_only(world):
    # yesterday: a closed shift of two hours and some browser activity
    world.move(at(D20, 10, 0))
    yesterday = world.check_in("owner")
    world.pulse("owner", str(uuid4()), 1)
    world.move(at(D20, 12, 0))
    world.check_out("owner", yesterday)
    # today: a shift that is still open
    world.move(at(D21, 10, 0))
    today = world.check_in("owner")
    world.move(at(D21, 10, 30))
    one, two = world.board(D20), world.board(D21)
    assert [s["id"] for s in one["attendance"]["sessions"]] == [yesterday] and one["attendance"]["totalSeconds"] == 2 * 3600
    assert [s["id"] for s in two["attendance"]["sessions"]] == [today] and two["attendance"]["totalSeconds"] == 1800
    # yesterday's browser activity is not in today's figures, and today's open shift is not in yesterday's list
    assert two["presence"]["activeSeconds"] == 0 and two["presence"]["idleSeconds"] == 0
    assert today not in [s["id"] for s in one["attendance"]["sessions"]]


def test_a_shift_never_checked_out_counts_no_hours_once_its_day_is_over(world):
    world.move(at(D20, 10, 0))
    forgotten = world.check_in("owner")
    world.move(at(D21, 10, 30))
    past = world.board(D20)
    # the day is over: the shift is a missed check-out with no hours, like the month (a half-punched day has no worked minutes)
    session = past["attendance"]["sessions"][0]
    assert session["id"] == forgotten and session["unclosed"] is True and session["durationSeconds"] == 0
    assert past["attendance"]["totalSeconds"] == 0
    assert world.month()[D20.isoformat()]["workedMinutes"] == 0
    # the day it is still open on (today) it runs to now, because the person is still logged in
    now = world.board(D21)
    assert now["attendance"]["status"] == "checked_in"
    assert now["attendance"]["sessions"][0]["unclosed"] is False
    assert now["attendance"]["totalSeconds"] == int((at(D21, 10, 30) - at(D21, 0, 0)).total_seconds())


def test_the_board_and_the_month_agree_on_who_logged_in_each_day_and_every_change_topic_is_live(world):
    for user, day_hour in (("owner", 10), ("other", 11)):
        world.move(at(D21, day_hour, 0))
        session_id = world.check_in(user)
        world.move(at(D21, day_hour + 2, 0))
        world.check_out(user, session_id)
    world.move(at(D21, 18, 0))
    for user in ("owner", "other"):
        board = world.board(D21, user)["attendance"]["sessions"]
        month = world.month(user)
        assert len(board) == 1 and month[D21.isoformat()]["checkIn"] is not None
        assert month[D21.isoformat()]["checkIn"][:16] == board[0]["checkedInAt"][:16]
        # before the person's first check-in the month has no row for the day at all, and the board has no session
        assert month.get(D20.isoformat(), {}).get("checkIn") is None and world.board(D20, user)["attendance"]["sessions"] == []
    # every table behind these figures pushes the "attendance" topic, so a change reaches an open board at once
    for table in ("staff_attendance_sessions", "staff_presence_intervals", "staff_attendance_hr_requests", "staff_attendance_calendar",
                  "staff_attendance_adjustments", "staff_attendance_weekly_off", "staff_attendance_work_timings", "staff_attendance_duty_plans"):
        assert "attendance" in change_feed.TOPIC_TABLES[table], table
