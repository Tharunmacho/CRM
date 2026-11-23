"""Signing in counts as attendance (the owner's decision; it replaces the old "only the Check in button" rule).

The real /api/auth/login and /api/auth/logout endpoints, the attendance board and the presence heartbeat, driven by a clock the test moves:
a recruiter's or manager's sign-in starts today's session (once), the first heartbeat of the day starts it for someone already signed in,
a sign-out ends it, a browser that went silent for 30 minutes is closed at its last heartbeat, administrators have none, and the board,
the month and the summary show every step in the very next read. Disposable PostgreSQL, like test_staff_activity.py."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import AuditEntry, Session as LoginSession, StaffAttendanceSession as Attendance, User
from app.services import attendance_hr as hr
from app.services import staff_activity as activity
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace as base_workspace  # noqa: F401  (fixture)

IST = timezone(timedelta(hours=5, minutes=30))
ZONE = "Asia/Kolkata"
D20, D21, D22 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)
EMAIL = {"owner": "owner@example.test", "other": "other@example.test", "admin": "admin@example.test", "manager": "manager@example.test"}


def at(day, hour, minute=0, second=0):
    """An agency (IST) wall-clock time as the UTC instant the server stores."""
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST).astimezone(timezone.utc)


@pytest.fixture
def workspace(base_workspace):  # noqa: F811
    from app.core.security import hash_password
    with base_workspace() as db:
        for person in db.scalars(select(User)):
            person.password_hash = hash_password("pw")
        db.commit()
    return base_workspace


@pytest.fixture
def world(workspace, monkeypatch):
    """The auth and attendance APIs on a small app (real sign-in, real sign-out, https so the Secure cookie is sent) and a clock the test moves."""
    from app.api.routers import attendance, auth
    from app.db.session import get_db

    clock = {"now": at(D21, 10, 0)}
    monkeypatch.setattr(activity, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(hr, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(attendance, "get_settings", lambda: SimpleNamespace(agency_timezone=ZONE))
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(agency_timezone=ZONE))

    def database():
        with workspace() as db:
            yield db

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(attendance.router)
    app.dependency_overrides[get_db] = database
    with TestClient(app, base_url="https://testserver") as browser:
        clients = {}

        def client_for(name):
            if name not in clients:
                clients[name] = TestClient(app, base_url="https://testserver")
            return clients[name]

        def move(to):
            clock["now"] = to

        def login(name, *, tab=None):
            """A real sign-in. `tab` names a separate browser (its own cookie jar); the default is the person's one browser."""
            response = client_for(tab or name).post("/api/auth/login", json={"email": EMAIL[name], "password": "pw"})
            assert response.status_code == 200, response.text
            return response

        def logout(name, *, tab=None):
            response = client_for(tab or name).post("/api/auth/logout")
            assert response.status_code == 204, response.text

        def pulse(name, client_id, sequence, *, tab=None, age=0, visible=True):
            response = client_for(tab or name).post("/api/presence/heartbeat", json={
                "clientId": client_id, "sequence": sequence, "visible": visible, "interactionAgeSeconds": age})
            assert response.status_code == 200, response.text

        def board(day, user="owner"):
            """The administrator's reading of one day for one person (every read must be uncached)."""
            if "admin" not in clients:
                login("admin")
            response = client_for("admin").get("/api/attendance", params={"date": day.isoformat(), "staffId": user})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            return response.json()["staff"][0]

        def month(user="owner", year=2026, number=10):
            if "admin" not in clients:
                login("admin")
            response = client_for("admin").get("/api/attendance/month", params={"year": year, "month": number, "staffId": user})
            assert response.status_code == 200, response.text
            return {row["date"]: row for row in response.json()["staff"][0]["days"]}

        def rows(name="owner"):
            with workspace() as db:
                return list(db.scalars(select(Attendance).where(Attendance.user_id == name).order_by(Attendance.checked_in_at)))

        def audits(action):
            with workspace() as db:
                return list(db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "attendance", AuditEntry.action == action)))

        yield SimpleNamespace(move=move, login=login, logout=logout, pulse=pulse, board=board, month=month, rows=rows, audits=audits,
                              workspace=workspace, client_for=client_for)


# ------------------------------------------------------------------------------------------------------------------------ sign-in

def test_a_sign_in_starts_exactly_one_open_session_with_one_audit_entry(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    [row] = world.rows()
    assert row.checked_in_at == at(D21, 10, 0) and row.checked_out_at is None
    assert row.checkin_request_key.startswith("login:") and activity.key_source(row.checkin_request_key) == "login"
    entries = world.audits("Recruiter checked in")
    assert len(entries) == 1 and entries[0].meta["source"] == "login" and entries[0].actor_id == "owner"
    # the very next read of the board shows the person checked in, with the source named
    person = world.board(D21)
    assert person["attendance"]["status"] == "checked_in" and person["attendance"]["checkedInAt"] is not None
    assert person["attendance"]["sessions"][0]["checkInSource"] == "login" and person["attendance"]["sessions"][0]["checkOutSource"] is None


def test_a_second_sign_in_or_a_second_browser_never_starts_a_second_open_session(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    world.move(at(D21, 10, 5))
    world.login("owner")                       # the same browser again
    world.move(at(D21, 10, 10))
    world.login("owner", tab="owner-laptop")   # another browser
    assert len(world.rows()) == 1 and world.rows()[0].checked_out_at is None
    assert len(world.audits("Recruiter checked in")) == 1
    with world.workspace() as db:
        assert db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == "owner")) == 3


def test_a_manual_check_in_after_a_sign_in_returns_the_open_session_instead_of_a_second(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    response = world.client_for("owner").post("/api/attendance/check-in", json={"requestKey": str(uuid4())})
    assert response.status_code == 200 and response.json()["attendance"]["status"] == "checked_in"
    assert len(world.rows()) == 1 and len(world.audits("Recruiter checked in")) == 1


def test_an_administrator_signing_in_creates_no_attendance(world):
    world.move(at(D21, 10, 0))
    world.login("admin")
    with world.workspace() as db:
        assert db.scalar(select(func.count()).select_from(Attendance)) == 0


def test_a_manager_signing_in_counts_and_a_forgotten_session_of_an_earlier_day_is_a_missed_check_out(world):
    world.move(at(D20, 9, 0))
    world.login("manager")
    [first] = world.rows("manager")
    assert first.checked_out_at is None
    world.move(at(D21, 9, 30))
    world.login("manager")                      # the next day: the old one cannot be given invented hours, the new one starts clean
    old, new = world.rows("manager")
    assert old.id == first.id and old.checked_out_at == old.checked_in_at and old.checkout_request_key.startswith("missed:")
    assert new.checked_out_at is None and new.checked_in_at == at(D21, 9, 30) and new.checkin_request_key.startswith("login:")
    yesterday = world.board(D20, "manager")["attendance"]
    assert yesterday["totalSeconds"] == 0 and yesterday["sessions"][0]["unclosed"] is True
    assert world.board(D21, "manager")["attendance"]["status"] == "checked_in"


# ------------------------------------------------------------------------------------------------------------------------ heartbeat

def test_the_first_heartbeat_of_the_day_starts_a_session_for_someone_already_signed_in(world, workspace):
    with workspace() as db:      # signed in before this version existed: a login row, no attendance
        # the sign-in must still be valid by the REAL clock (the web layer checks it), whatever day the test pretends it is
        db.add(LoginSession(id="login-owner", user_id="owner", expires_at=datetime.now(timezone.utc) + timedelta(days=30)))
        db.commit()
    from app.core.security import SESSION_COOKIE
    world.client_for("owner").cookies.set(SESSION_COOKIE, "login-owner")
    world.move(at(D21, 9, 40))
    page = str(uuid4())
    world.pulse("owner", page, 1)
    [row] = world.rows()
    assert row.checked_in_at == at(D21, 9, 40) and row.checkin_request_key.startswith("auto:")
    world.move(at(D21, 9, 41))
    world.pulse("owner", page, 2)                              # the same page again
    world.move(at(D21, 9, 45))
    world.pulse("owner", str(uuid4()), 1)                      # a second tab
    assert len(world.rows()) == 1 and len(world.audits("Recruiter checked in")) == 1
    assert world.audits("Recruiter checked in")[0].meta["source"] == "heartbeat"


def test_a_heartbeat_never_restarts_the_day_after_a_check_out(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    [row] = world.rows()
    world.move(at(D21, 12, 0))
    response = world.client_for("owner").post("/api/attendance/check-out", json={"sessionId": row.id, "requestKey": str(uuid4())})
    assert response.status_code == 200
    world.move(at(D21, 12, 5))                  # a page that appears afterwards must not reopen or restart anything
    world.pulse("owner", str(uuid4()), 1)
    [only] = world.rows()
    assert only.id == row.id and only.checked_out_at == at(D21, 12, 0)


# ------------------------------------------------------------------------------------------------------------------------ sign-out

def test_a_sign_out_closes_the_open_session_at_the_sign_out_time_and_a_later_sign_in_starts_a_new_one(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    world.move(at(D21, 12, 30))
    world.logout("owner")
    [row] = world.rows()
    assert row.checked_out_at == at(D21, 12, 30) and row.checkout_request_key.startswith("logout:")
    closed = world.audits("Recruiter checked out")
    assert len(closed) == 1 and closed[0].meta["source"] == "logout"
    person = world.board(D21)["attendance"]                    # the very next read: last logout and hours are filled in
    assert person["status"] == "checked_out" and person["totalSeconds"] == int(timedelta(hours=2, minutes=30).total_seconds())
    assert person["lastCheckedOutAt"] is not None and person["sessions"][0]["checkOutSource"] == "logout"
    world.move(at(D21, 14, 0))
    world.login("owner")                                       # the same day, again: a new session
    first, second = world.rows()
    assert first.checked_out_at is not None and second.checked_out_at is None and second.checked_in_at == at(D21, 14, 0)


def test_signing_out_of_one_browser_keeps_the_day_going_while_another_is_still_active(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    world.login("owner", tab="owner-laptop")
    world.move(at(D21, 10, 20))
    world.pulse("owner", str(uuid4()), 1, tab="owner-laptop")  # the laptop is in use
    world.move(at(D21, 10, 20, 30))
    world.logout("owner")                                      # the other browser signs out
    [row] = world.rows()
    assert row.checked_out_at is None
    world.move(at(D21, 11, 0))
    world.logout("owner", tab="owner-laptop")                  # the last one: now the day ends
    [row] = world.rows()
    assert row.checked_out_at == at(D21, 11, 0)


def test_an_administrator_signing_out_changes_nothing(world):
    world.login("admin")
    world.logout("admin")
    with world.workspace() as db:
        assert db.scalar(select(func.count()).select_from(Attendance)) == 0


def test_cleaning_up_an_expired_login_never_ends_the_day_of_someone_working_elsewhere(world, workspace):
    """An old cookie in another browser is found expired and removed: that is not a sign-out and must not check anyone out."""
    world.move(at(D21, 10, 0))
    world.login("owner")
    world.move(at(D21, 11, 0))
    with workspace() as db:
        db.add(LoginSession(id="old-cookie", user_id="owner", expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
        db.commit()
    from app.core.security import SESSION_COOKIE
    stale_browser = world.client_for("owner-old-browser")
    stale_browser.cookies.set(SESSION_COOKIE, "old-cookie")
    assert stale_browser.get("/api/attendance").status_code == 401        # expired: the login row is cleaned up
    with workspace() as db:
        assert db.get(LoginSession, "old-cookie") is None
    [row] = world.rows()
    assert row.checked_out_at is None                                     # the day goes on


# ------------------------------------------------------------------------------------------------------------------------ abandoned sessions

def test_a_session_whose_browser_went_silent_for_thirty_minutes_is_closed_at_its_last_heartbeat(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    page = str(uuid4())
    world.pulse("owner", page, 1)
    world.move(at(D21, 10, 10))
    world.pulse("owner", page, 2)                              # the last sign of life
    last_seen = at(D21, 10, 10)
    with world.workspace() as db:                              # 29 min 59 s of silence: still open
        assert activity.close_abandoned_sessions(db, at=last_seen + timedelta(minutes=30) - timedelta(seconds=1)) == 0
        db.commit()
    assert world.rows()[0].checked_out_at is None
    with world.workspace() as db:                              # exactly 30 minutes: closed at the last heartbeat, not at "now"
        assert activity.close_abandoned_sessions(db, at=last_seen + timedelta(minutes=30)) == 1
        db.commit()
        assert activity.close_abandoned_sessions(db, at=last_seen + timedelta(hours=5)) == 0   # nothing left to close
    [row] = world.rows()
    assert row.checked_out_at == last_seen and row.checkout_request_key.startswith("sweep:")
    assert activity.key_source(row.checkout_request_key) == "inactivity"
    move_to = at(D21, 18, 0)
    world.move(move_to)
    person = world.board(D21)["attendance"]                    # honest hours: 10:00 to 10:10, not to 18:00
    assert person["totalSeconds"] == 600 and person["sessions"][0]["unclosed"] is False and person["sessions"][0]["checkOutSource"] == "inactivity"


def test_a_session_with_no_heartbeat_at_all_is_closed_at_its_own_start_and_a_manager_is_never_swept(world):
    world.move(at(D21, 9, 0))
    world.login("owner")
    world.login("manager")
    with world.workspace() as db:
        assert activity.close_abandoned_sessions(db, at=at(D21, 12, 0)) == 1
        db.commit()
    [owner] = world.rows("owner")
    [manager] = world.rows("manager")
    assert owner.checked_out_at == owner.checked_in_at          # no signal ever: no invented hours
    assert manager.checked_out_at is None                       # managers send no heartbeat, so silence proves nothing


def test_a_sign_in_after_an_abandoned_session_closes_it_at_the_last_heartbeat_and_starts_clean(world):
    world.move(at(D21, 9, 0))
    world.login("owner")
    world.pulse("owner", str(uuid4()), 1)
    world.move(at(D21, 13, 0))                                  # four hours of silence, nobody swept it yet
    world.login("owner", tab="owner-later")
    old, new = world.rows()
    assert old.checked_out_at == at(D21, 9, 0) and old.checkout_request_key.startswith("sweep:")
    assert new.checked_in_at == at(D21, 13, 0) and new.checked_out_at is None


def test_a_tab_hidden_for_over_thirty_minutes_restarts_the_day_when_it_comes_back(world):
    """A recruiter working in another program with the CRM tab hidden: the session closes at the last heartbeat, and the page coming back starts
    a new one, so the rest of the day is counted (a deliberate check-out or sign-out is never undone this way)."""
    world.move(at(D21, 10, 0))
    world.login("owner")
    page = str(uuid4())
    world.pulse("owner", page, 1)
    world.move(at(D21, 10, 10))
    world.pulse("owner", page, 2)                               # the last visible heartbeat
    world.move(at(D21, 11, 0))                                  # fifty minutes later the tab is visible again
    world.pulse("owner", str(uuid4()), 1)
    first, second = world.rows()
    assert first.checked_out_at == at(D21, 10, 10) and first.checkout_request_key.startswith("sweep:")
    assert second.checked_in_at == at(D21, 11, 0) and second.checked_out_at is None and second.checkin_request_key.startswith("auto:")
    world.move(at(D21, 11, 30))
    person = world.board(D21)["attendance"]
    assert person["status"] == "checked_in" and person["totalSeconds"] == 600 + 1800     # 10:00-10:10 and 11:00-11:30, never the gap
    assert len(person["sessions"]) == 2


def test_the_strict_rule_switch_never_restarts_after_inactivity(world, monkeypatch):
    monkeypatch.setattr(activity, "RESTART_AFTER_INACTIVITY", False)
    world.move(at(D21, 10, 0))
    world.login("owner")
    world.pulse("owner", str(uuid4()), 1)
    world.move(at(D21, 11, 0))
    world.pulse("owner", str(uuid4()), 1)
    [only] = world.rows()
    assert only.checked_out_at == at(D21, 10, 0) and only.checkout_request_key.startswith("sweep:")


# ------------------------------------------------------------------------------------------------------------------------ board, month, edges

def test_the_board_the_month_and_the_summary_count_a_sign_in_like_a_manual_check_in(world):
    world.move(at(D21, 10, 0))
    world.login("owner")
    world.login("other")
    world.client_for("other").post("/api/attendance/check-out", json={
        "sessionId": world.rows("other")[0].id, "requestKey": str(uuid4())})
    world.move(at(D21, 11, 0))
    for name in ("owner", "other"):
        # the same shape whichever way the session started
        shape = world.board(D21, name)["attendance"]["sessions"][0]
        assert shape["checkedInAt"] is not None and "durationSeconds" in shape and shape["unclosed"] is False
        assert world.month(name)[D21.isoformat()]["checkIn"] is not None
    # a person with no session at all is still "Not clocked in": the summary says nobody else has anything
    assert world.board(D21, "manager")["attendance"]["sessions"] == []
    assert world.month("manager").get(D21.isoformat(), {}).get("checkIn") is None


def test_a_sign_in_at_23_50_and_a_sign_out_at_00_20_are_split_at_the_agency_midnight(world):
    world.move(at(D21, 23, 50))
    world.login("owner")
    world.move(at(D22, 0, 20))
    world.logout("owner")
    world.move(at(D22, 6, 0))
    first, second = world.board(D21)["attendance"], world.board(D22)["attendance"]
    assert first["totalSeconds"] == 600 and second["totalSeconds"] == 1200
    assert world.month()[D21.isoformat()]["checkIn"] is not None and world.month()[D22.isoformat()]["checkIn"] is None


def test_a_sign_in_at_00_20_belongs_to_the_new_day_only(world):
    world.move(at(D22, 0, 20))
    world.login("owner")
    assert world.board(D21)["attendance"]["sessions"] == []
    assert len(world.board(D22)["attendance"]["sessions"]) == 1
    assert world.month()[D22.isoformat()]["checkIn"] is not None


def test_late_is_measured_from_the_first_sign_in_of_the_day(world):
    """Two recruiters sign in at different times on the same day and sign out in the evening: the month's lateness follows the sign-in time."""
    world.move(at(D21, 9, 30))
    world.login("owner")
    world.move(at(D21, 12, 45))
    world.login("other")
    world.move(at(D21, 18, 30))
    world.logout("owner")
    world.logout("other")
    world.move(at(D22, 6, 0))
    early, late = world.month("owner")[D21.isoformat()], world.month("other")[D21.isoformat()]
    # the first sign-in IS the day's first punch: the shift starts at 10:00, so 09:30 is on time and 12:45 is exactly 165 minutes late
    assert early["checkIn"].startswith("2026-10-21T04:00") and late["checkIn"].startswith("2026-10-21T07:15")
    assert early["status"] == "P" and early["lateMinutes"] == 0
    assert late["status"] == "LT" and late["lateMinutes"] == 165
    # the sign-out is the last punch, so the hours are filled in
    assert early["checkOut"].startswith("2026-10-21T13:00") and early["workedMinutes"] > 0
