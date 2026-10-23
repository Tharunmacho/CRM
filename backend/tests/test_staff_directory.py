"""Staff operations against guarded, disposable loopback PostgreSQL only."""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from test_database_naming import schema_database
from app.core.security import SESSION_COOKIE
from app.db.models import AttendanceCalendarDay, Base, Candidate, Remark, Role, Session as LoginSession, Source, Task, TaskKind, User
from app.services import staff_directory as service

with patch("app.core.config.get_settings", return_value=SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused")):
    from app.api.routers import candidates, staff_directory
    from app.db.session import get_db

NOW = datetime(2026, 10, 22, 0, 30, tzinfo=timezone.utc)
START = datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc)  # Oct21 midnight Kolkata
END = datetime(2026, 10, 21, 18, 30, tzinfo=timezone.utc)


@pytest.fixture
def workspace(schema_database, monkeypatch):
    Base.metadata.create_all(schema_database)
    factory = sessionmaker(bind=schema_database, expire_on_commit=False, autoflush=False)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(staff_directory, "get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
    application = FastAPI()
    application.include_router(staff_directory.router)
    application.include_router(candidates.router)
    def database():
        with factory() as db:
            # The endpoints, including authentication, must work with a DB that
            # rejects writes; fixtures are inserted using separate connections.
            db.execute(text("SET TRANSACTION READ ONLY"))
            yield db
    application.dependency_overrides[get_db] = database
    with factory() as db:
        for identifier, role in [("admin", Role.admin), ("manager", Role.manager), ("staff", Role.staff),
                                 ("paused", Role.staff), ("manual", Role.staff), ("approved", Role.staff),
                                 ("inactive", Role.staff)]:
            db.add(User(id=identifier, name=identifier.title(), email=identifier + "@example.test", role=role,
                        password_hash="not-a-login-password", active=identifier != "inactive",
                        receives_auto_assign=role == Role.staff and identifier != "paused", on_leave=identifier == "manual"))
        db.flush()
        for identifier in ("admin", "manager", "staff", "inactive"):
            db.add(LoginSession(id="session-" + identifier, user_id=identifier, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.add(AttendanceCalendarDay(user_id="approved", attendance_date=date(2026, 10, 22), status="PL", reason="Approved leave", actor_id="admin"))
        for identifier, owner in (("one", "staff"), ("two", "staff"), ("three", "manager")):
            db.add(Candidate(id=identifier, ref="C-" + identifier, name="Candidate " + identifier, phone="12345",
                             owner_id=owner, source=Source.walk_in, languages=[], skills=[], designations=[]))
        db.commit()
    with TestClient(application, base_url="https://testserver") as client:
        client.cookies.set(SESSION_COOKIE, "session-admin")
        yield SimpleNamespace(client=client, factory=factory, engine=schema_database)


def rows(response):
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()["staff"]}


@pytest.mark.parametrize("path", ["/api/staff-directory", "/api/staff-directory/availability"])
def test_supervisor_only_and_uncached_identity(workspace, path):
    workspace.client.cookies.clear()
    assert workspace.client.get(path).status_code == 401
    for identity, code in (("staff", 403), ("inactive", 401), ("manager", 200), ("admin", 200)):
        workspace.client.cookies.set(SESSION_COOKIE, "session-" + identity)
        response = workspace.client.get(path)
        assert response.status_code == code, response.text
        if code == 200:
            assert response.headers["cache-control"] == "private, no-store"
            assert len(rows(response)) == 7
            assert not any(word in response.text.lower() for word in ("password", "session-", "receives_auto_assign"))


def test_availability_is_live_and_uses_agency_calendar(workspace):
    def available():
        return rows(workspace.client.get("/api/staff-directory/availability"))
    data = available()
    for identifier, status in (("admin", "not_assigned"), ("manager", "not_assigned"), ("staff", "available"),
                               ("paused", "paused"), ("manual", "manual_leave"), ("approved", "approved_leave"), ("inactive", "inactive")):
        assert data[identifier]["availability"]["status"] == status
        assert data[identifier]["availability"]["eligible"] is (identifier == "staff")
    assert data["approved"]["availability"]["leaveType"] == "PL"
    with workspace.factory() as db:
        calendar = db.scalar(select(AttendanceCalendarDay).where(AttendanceCalendarDay.user_id == "approved"))
        calendar.status = "UL"
        db.get(User, "staff").on_leave = True
        db.commit()
    data = available()
    assert data["approved"]["availability"]["leaveType"] == "UL"
    assert data["staff"]["availability"]["status"] == "manual_leave"
    with workspace.factory() as db:
        calendar = db.scalar(select(AttendanceCalendarDay).where(AttendanceCalendarDay.user_id == "approved"))
        calendar.attendance_date = date(2026, 10, 21)  # Yesterday must not affect today.
        db.commit()
    assert available()["approved"]["availability"]["eligible"] is True


def test_current_workload_and_completed_period_are_separate(workspace):
    with workspace.factory() as db:
        def task(identifier, **kwargs):
            values = {"id": identifier, "title": identifier, "candidate_id": "one", "owner_id": "staff",
                      "kind": TaskKind.initial_call, "created_at": NOW - timedelta(hours=72)}
            db.add(Task(**(values | kwargs)))
        task("overdue", due_at=NOW - timedelta(hours=1), escalated=True)
        task("reset", kind=TaskKind.stage, due_at=NOW + timedelta(hours=1))
        task("sla_fallback", sla_started_at=NOW - timedelta(hours=24))
        task("created_fallback", kind=TaskKind.stage)
        task("exact_due", due_at=NOW)
        task("completed_start", created_at=START - timedelta(hours=24), completed_at=START)
        task("completed_end", created_at=END - timedelta(microseconds=1, hours=48), completed_at=END - timedelta(microseconds=1))
        task("before_period", completed_at=START - timedelta(microseconds=1))
        task("after_period", completed_at=END)
        task("cancelled", cancelled_at=NOW)
        task("completed_cancelled", completed_at=START, cancelled_at=NOW)
        db.flush()
        for identifier in ("first", "second"):
            db.add(Remark(task_id="reset", author_id="staff", created_at=NOW, body=identifier))
        db.add(Remark(task_id="sla_fallback", author_id="manager", created_at=NOW, body="Supervisor does not satisfy owner update"))
        db.add(Remark(task_id="created_fallback", author_id="staff", created_at=END - timedelta(microseconds=1), body="Yesterday"))
        db.add(Remark(task_id="exact_due", author_id="staff", created_at=END + timedelta(days=1), body="Tomorrow"))
        db.commit()
    response = workspace.client.get("/api/staff-directory?from=2026-10-21&to=2026-10-21")
    data = rows(response)
    assert response.json()["period"] == {"from": "2026-10-21", "to": "2026-10-21"}
    assert response.json()["today"] == "2026-10-22"
    assert {key: data["staff"][key] for key in ("assignedCandidates", "initialCallsPending", "openTasks", "overdueTasks", "escalatedTasks", "missingUpdates", "completedTasks", "averageCompletionHours")} == {
        "assignedCandidates": 2, "initialCallsPending": 3, "openTasks": 5, "overdueTasks": 2,
        "escalatedTasks": 1, "missingUpdates": 4, "completedTasks": 2, "averageCompletionHours": 36.0}
    assert data["manager"]["assignedCandidates"] == 1
    assert data["manager"]["averageCompletionHours"] is None
    other = rows(workspace.client.get("/api/staff-directory?from=2020-01-01&to=2020-01-01"))["staff"]
    assert other["completedTasks"] == 0 and other["averageCompletionHours"] is None
    assert other["openTasks"] == 5 and other["missingUpdates"] == 4 and other["assignedCandidates"] == 2


def test_period_validation_defaults_and_one_snapshot_query(workspace):
    for suffix in ("?from=2026-10-22&to=2026-10-21", "?from=invalid", "?to=9999-12-31"):
        assert workspace.client.get("/api/staff-directory" + suffix).status_code == 422
    statements = []
    def observed(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(workspace.engine, "before_cursor_execute", observed)
    try:
        response = workspace.client.get("/api/staff-directory")
    finally:
        event.remove(workspace.engine, "before_cursor_execute", observed)
    assert response.status_code == 200, response.text
    assert response.json()["period"] == {"from": "2026-10-01", "to": "2026-10-22"}
    assert sum(statement.lstrip().upper().startswith("SELECT") for statement in statements) == 2  # Authentication + directory.
    assert all(statement.lstrip().upper().startswith(("SELECT", "SET TRANSACTION READ ONLY")) for statement in statements)


def test_candidate_owner_drilldown_and_export_use_same_exact_filter(workspace):
    response = workspace.client.get("/api/candidates?staff=staff")
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["items"]} == {"one", "two"}
    assert response.json()["total"] == 2
    assert workspace.client.get("/api/candidates?staff=sta").json()["total"] == 0
    response = workspace.client.get("/api/candidates/export?staff=staff")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        worksheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    exported = worksheet.findall("{*}sheetData/{*}row")[1:]
    assert {row.find("{*}c/{*}is/{*}t").text for row in exported} == {"C-one", "C-two"}
    workspace.client.cookies.set(SESSION_COOKIE, "session-manager")
    assert workspace.client.get("/api/candidates?staff=staff").status_code == 200
    assert workspace.client.get("/api/candidates/export?staff=staff").status_code == 403
