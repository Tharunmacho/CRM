"""Statement budgets for the endpoints every page open and every daily screen calls.

The hosted database costs ~300 ms per statement, so what matters is how many statements a request issues (and how many of
them wait on one another), not CPU. This module seeds a realistic synthetic agency (invented names only), calls each
endpoint of the slice as an admin, a manager and a recruiter through the real application, counts the SQL statements, and
fails when an endpoint issues more than its budget. It also prints a table: endpoint, role, statements, depth, ms, KB.

"depth" is wall time with a simulated 20 ms round trip per statement divided by 20 ms: statements that run side by side on
separate connections (parallel_reads) overlap, so depth approximates the number of sequential round trips.

Run on a disposable loopback PostgreSQL only (the shared `schema_database` fixture refuses anything else):
  TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:<port>/crm_schema_test_<x> pytest tests/test_api_statement_budgets_core.py -s
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import itertools
import json
import os
import time

import pytest
from fastapi import Request
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.core import deps
from app.core.security import SESSION_COOKIE, hash_password
from app.db import models as m
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

RTT_SECONDS = 0.02
NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.date()
PASSWORD = "Budget-Test-Pass-1"
SCALE = int(os.environ.get("BUDGET_SCALE", "1"))  # a manual knob for looking at server time on bigger data; the budgets use 1
ROWS: list[tuple] = []
_SEQUENCE = itertools.count(1)
CLIENT_ID = "6f1c1d1e-0000-4000-8000-000000000001"


def seed(engine) -> dict:
    """12 users, 300 leads, 400 candidates (most with an application and stage history), tasks, remarks, attendance."""
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    ids: dict = {"staff": []}
    with sessions() as db:
        people = [("budget-admin", "Asha Admin", m.Role.admin), ("budget-manager", "Maya Manager", m.Role.manager),
                  ("budget-manager-2", "Milan Manager", m.Role.manager)]
        people += [(f"budget-staff-{n}", f"Recruiter {n:02d}", m.Role.staff) for n in range(9)]
        for uid, name, role in people:
            db.add(m.User(id=uid, name=name, email=f"{uid}@example.test", role=role, active=True,
                          password_hash=hash_password(PASSWORD) if uid in ("budget-admin", "budget-staff-0") else None))
            if role == m.Role.staff:
                ids["staff"].append(uid)
        db.flush()
        for uid, _name, _role in people:
            db.add(m.Session(id=f"sess-{uid}", user_id=uid, expires_at=NOW + timedelta(days=5)))
        db.add(m.Client(id="budget-client", name="Client Contact"))
        db.flush()
        db.add(m.Company(id="budget-company", name="Example Employer", client_id="budget-client"))
        db.add(m.Designation(id="budget-designation", title="Welder"))
        db.flush()
        orders = []
        for n in range(12):
            db.add(m.Order(id=f"budget-order-{n}", ref=f"ORD-B{n:03d}", company_id="budget-company",
                           designation_id="budget-designation", openings=20))
            orders.append(f"budget-order-{n}")
        db.flush()
        staff = ids["staff"]
        for n in range(400 * SCALE):
            owner = staff[n % len(staff)]
            db.add(m.Candidate(id=f"budget-cand-{n}", ref=f"CND-B{n:04d}", care_id=f"CI-{n + 1:03d}", name=f"Candidate {n:03d}",
                               phone=f"90000{n:05d}", source=m.Source.whatsapp, owner_id=owner, intake_owner_id=owner,
                               status=m.CandidateStatus.in_process if n % 3 else m.CandidateStatus.new,
                               created_at=NOW - timedelta(hours=n)))
        db.flush()
        for n in range(300 * SCALE):  # candidates 0..299 are placed; 300..399 stay unmatched (initial call work)
            owner = staff[n % len(staff)]
            stage = 1 + n % 7
            db.add(m.Application(id=f"budget-app-{n}", candidate_id=f"budget-cand-{n}", order_id=orders[n % len(orders)],
                                 owner_id=owner, current_stage=stage, status=m.ApplicationStatus.active,
                                 created_at=NOW - timedelta(days=n % 40)))
        db.flush()
        for n in range(300 * SCALE):
            owner = staff[n % len(staff)]
            for stage in range(1, 1 + n % 7):
                db.add(m.ApplicationStage(application_id=f"budget-app-{n}", stage=stage, attempt=1, data={"note": "x"},
                                          outcome="Done", owner_id=owner, completed_by=owner,
                                          completed_at=NOW - timedelta(days=(n % 40), hours=-stage)))
        db.flush()
        for n in range(300 * SCALE):
            owner = staff[n % len(staff)]
            open_task = n % 4 != 0
            db.add(m.Task(id=f"budget-task-{n}", kind=m.TaskKind.stage, title=f"Stage task {n}", candidate_id=f"budget-cand-{n}",
                          application_id=f"budget-app-{n}", stage=1 + n % 7, owner_id=owner,
                          created_at=NOW - timedelta(days=n % 5, hours=3),
                          due_at=NOW + timedelta(hours=(n % 60) - 10),
                          completed_at=None if open_task else NOW - timedelta(hours=n % 20)))
        db.flush()
        for n in range(150 * SCALE):
            db.add(m.Remark(task_id=f"budget-task-{n}", author_id=staff[n % len(staff)], body="Called, will send documents",
                            created_at=NOW - timedelta(hours=n)))
        for n in range(60 * SCALE):
            db.add(m.TaskEvent(task_id=f"budget-task-{n}", candidate_id=f"budget-cand-{n}", owner_id=staff[n % len(staff)],
                               kind="missed_update", event_key=f"budget-missed-{n}", occurred_at=NOW - timedelta(hours=n),
                               meta={"agencyDate": TODAY.isoformat()}))
        for n in range(120 * SCALE):
            db.add(m.Notification(user_id=staff[n % len(staff)] if n % 2 else "budget-admin", kind="candidate_assigned",
                                  title=f"Assigned candidate {n}", detail="Synthetic", link_to="/tasks", read=n % 3 == 0,
                                  created_at=NOW - timedelta(minutes=n)))
        for n in range(40):
            db.add(m.TodoItem(owner_id=staff[n % len(staff)], title=f"Reminder {n}", due_at=NOW + timedelta(hours=n)))
        batch = m.LeadBatch(id="budget-batch", file_name="synthetic.csv", file_sha256="a" * 64, file_format="csv",
                            uploaded_by="budget-admin", rows_total=300, rows_new=300)
        db.add(batch)
        db.flush()
        for n in range(300 * SCALE):
            owner = staff[n % len(staff)] if n % 5 else None
            db.add(m.Lead(id=f"budget-lead-{n}", batch_id="budget-batch", name=f"Lead {n:03d}", phone=f"80000{n:05d}",
                          phone_key=f"80000{n:05d}", status="new" if n % 3 == 0 else "contacted" if n % 3 == 1 else "interested",
                          owner_id=owner, assigned_at=NOW - timedelta(hours=n) if owner else None,
                          last_contacted_at=NOW - timedelta(hours=n // 2) if n % 3 else None,
                          next_follow_up_at=NOW + timedelta(hours=(n % 30) - 10) if n % 4 == 0 else None,
                          call_count=n % 4, created_time=NOW - timedelta(hours=n), created_at=NOW - timedelta(hours=n),
                          position_applied="Welder"))
        db.flush()
        for n in range(300 * SCALE):
            if n % 3:
                db.add(m.LeadActivity(lead_id=f"budget-lead-{n}", user_id=staff[n % len(staff)], kind="call",
                                      summary="Called", meta={"outcome": "interested"}, created_at=NOW - timedelta(hours=n // 2)))
        # Attendance: a month for everyone who clocks in (staff and managers).
        month_start = TODAY.replace(day=1)
        for uid in ids["staff"] + ["budget-manager"]:
            db.add(m.AttendanceWorkTiming(user_id=uid, effective_from=month_start - timedelta(days=60),
                                          shift={"start": "10:00", "end": "19:00", "breakMinutes": 60}, reason="Baseline",
                                          actor_id="budget-admin"))
            for back in range(0, 28):
                day = TODAY - timedelta(days=back)
                if day.weekday() == 6:
                    continue
                start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=4, minutes=30)
                if start > NOW:
                    continue
                db.add(m.StaffAttendanceSession(user_id=uid, checked_in_at=start,
                                                checked_out_at=min(start + timedelta(hours=9), NOW - timedelta(seconds=1)) if back else None,
                                                checkin_request_key=f"{uid}-{back}"))
            for kind, status, back in (("late", "approved", 3), ("work_from_home", "pending", 1)):
                db.add(m.AttendanceHRRequest(user_id=uid, request_key=f"{uid}-{kind}", attendance_date=TODAY - timedelta(days=back),
                                             kind=kind, status=status, minutes=30, reason="Synthetic", approvals={}))
        # Automatic receipt replies: one mailbox and one WhatsApp number, each with the 20 most recent attempts on show.
        db.add(m.MailAccount(id="budget-mailbox", address="inbox@example.test", provider="imap"))
        db.add(m.WhatsAppChannel(phone_number_id="budget-wa", display_number="+910000000000"))
        db.flush()
        db.add(m.WhatsAppConversation(id="budget-conversation", phone_number_id="budget-wa", peer_number="910000000001"))
        db.flush()
        for kind, channel in (("email", "budget-mailbox"), ("whatsapp", "budget-wa")):
            db.add(m.IntakeAcknowledgementSettings(source_kind=kind, channel_id=channel, enabled=True,
                                                   body="Thank you, we have received your documents."))
            for n in range(25):
                outbound = None
                if kind == "whatsapp":
                    outbound = f"budget-send-{n}"
                    db.add(m.WhatsAppSendAttempt(id=outbound, conversation_id="budget-conversation", request_key=f"budget-send-key-{n}",
                                                 payload_hash="b" * 64, message_type="text", body="Thanks", state="delivered",
                                                 sent_at=NOW - timedelta(minutes=n)))
                    db.flush()
                db.add(m.IntakeAcknowledgement(source_kind=kind, source_id=f"budget-source-{kind}-{n}", channel_id=channel,
                                               recipient="person@example.test", body="Thanks", status="sent", request_key=f"budget-ack-{kind}-{n}",
                                               outbound_id=outbound, created_at=NOW - timedelta(minutes=n)))
        for n in range(30):
            db.add(m.AuditEntry(actor_id=staff[n % len(staff)], action="Updated candidate", entity_type="user",
                                entity_id=staff[n % len(staff)], at=NOW - timedelta(hours=n)))
        db.commit()
    return ids


@pytest.fixture
def env(schema_database, monkeypatch):
    from app.db import session as db_session
    # Guard: the module-level engine must be the disposable one, or app code that bypasses get_db would reach another database.
    if db_session.engine.url.database != schema_database.url.database:
        pytest.skip("CRM_ENV_FILE must point DATABASE_URL at the same disposable database as TEST_DATABASE_URL")
    _migrate(schema_database, "head")
    ids = seed(schema_database)
    from app import main
    from app.db.session import get_db
    monkeypatch.setattr(main, "engine", schema_database)
    factory = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    read_factory = sessionmaker(bind=schema_database.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False,
                                expire_on_commit=False)

    def isolated(request: Request):
        endpoint = request.scope.get("endpoint")
        read_only = request.method in ("GET", "HEAD") and not getattr(endpoint, "writes_on_get", False)
        with (read_factory if read_only else factory)() as db:
            yield db

    main.app.dependency_overrides[get_db] = isolated
    from fastapi.testclient import TestClient
    client = TestClient(main.app, base_url="https://testserver")
    yield client, schema_database, ids
    main.app.dependency_overrides.pop(get_db, None)
    client.close()


class Statements(list):
    """The statements of one request; `spent` holds (statement, server milliseconds) pairs for debugging."""

    def __init__(self):
        super().__init__()
        self.spent: list[tuple[str, float]] = []


@contextmanager
def counting(engine, rtt: float = 0.0):
    deps._AUTH_CACHE.clear()  # a fresh request pays its sign-in lookup
    statements = Statements()
    started: dict[int, float] = {}
    spent = statements.spent

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))
        started[id(cursor)] = time.perf_counter()
        if rtt:
            time.sleep(rtt)

    def done(connection, cursor, statement, parameters, context, executemany):
        spent.append((" ".join(statement.split()), round((time.perf_counter() - started.pop(id(cursor), time.perf_counter()) - rtt) * 1000, 1)))

    event.listen(engine, "before_cursor_execute", record)
    event.listen(engine, "after_cursor_execute", done)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)
        event.remove(engine, "after_cursor_execute", done)


def call(env, role: str, method: str, url: str, label: str | None = None, body=None, *, budget: int | None = None):
    client, engine, _ids = env
    headers = {"Cookie": f"{SESSION_COOKIE}=sess-budget-{role}"}
    send = getattr(client, method.lower())

    def kwargs():
        extra = {"headers": headers}
        if body is not None:  # a callable builds a fresh body per call (a heartbeat needs a rising sequence)
            extra["json"] = body(next(_SEQUENCE)) if callable(body) else body
        return extra

    with counting(engine) as statements:
        started = time.perf_counter()
        response = send(url, **kwargs())
        elapsed = (time.perf_counter() - started) * 1000
    with counting(engine, RTT_SECONDS):
        started = time.perf_counter()
        send(url, **kwargs())
        slow = time.perf_counter() - started
    depth = round(slow / RTT_SECONDS, 1)
    if os.environ.get("BUDGET_SHOW") and os.environ["BUDGET_SHOW"] in url:  # debugging aid: print each statement
        print(f"--- {method} {url} as {role}")
        try:
            body_json = response.json()
            if isinstance(body_json, dict):
                big = {key: len(json.dumps(value)) for key, value in body_json.items() if len(json.dumps(value)) > 4000}
                if big:
                    print("  large keys (bytes):", big)
        except ValueError:
            pass
        timing = dict(statements.spent)
        for number, statement in enumerate(statements, 1):
            head, _, tail = statement.partition(" FROM ")
            ms = timing.get(statement, "?")
            print(f"  {number:>2}. [{ms} ms] {head[:40]} ... FROM {tail[:200]}" if tail else f"  {number:>2}. [{ms} ms] {statement[:240]}")
    ROWS.append((label or f"{method} {url}", role, len(statements), depth, round(elapsed), round(len(response.content) / 1024, 1)))
    assert response.status_code < 400, (url, response.status_code, response.text[:300])
    if budget is not None:
        assert len(statements) <= budget, f"{method} {url} as {role}: {len(statements)} statements > {budget}\n" + "\n".join(statements)
    return response, statements


def pytest_table():
    width = max(len(r[0]) for r in ROWS)
    print("\n" + f"{'endpoint':<{width}}  {'role':<8} {'stmts':>5} {'depth':>6} {'ms':>6} {'KB':>8}")
    for name, role, count, depth, ms, kb in ROWS:
        print(f"{name:<{width}}  {role:<8} {count:>5} {depth:>6} {ms:>6} {kb:>8}")


HEADROOM = 1  # a budget is the measured count plus this much, so a small honest change does not need a test edit
A, MG, ST = "admin", "manager", "staff-0"
month = f"year={TODAY.year}&month={TODAY.month}"
span = f"from={(TODAY - timedelta(days=29)).isoformat()}&to={TODAY.isoformat()}"
today = TODAY.isoformat()
pulse = lambda n: {"clientId": CLIENT_ID, "sequence": n, "visible": True, "interactionAgeSeconds": 1}  # noqa: E731

# (method, url, body, {role: statements measured on a cold request, sign-in lookup included}, label or None).
# Reads that ride the response cache are measured with it off (pytest turns it off), i.e. the cost of a miss.
SPEC: list[tuple] = [
    ("GET", "/api/auth/session", None, {A: 1, MG: 1, ST: 1}, None),
    ("GET", "/api/auth/session?workspace=true&compact=true", None, {A: 3, MG: 3, ST: 3}, None),
    ("GET", "/api/auth/me", None, {A: 1, MG: 1, ST: 1}, None),
    ("GET", "/api/bootstrap", None, {A: 3, MG: 3, ST: 3}, None),
    ("GET", "/api/bootstrap?compact=true", None, {A: 3, MG: 3, ST: 3}, None),
    ("GET", "/api/notifications", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", "/api/dashboard", None, {A: 3, MG: 3, ST: 3}, None),
    ("GET", "/api/dashboard?staff_id=budget-staff-1", None, {A: 3, MG: 3}, None),
    ("GET", "/api/dashboard/leads?card=newToday&period=month", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", "/api/dashboard/leads?card=notContacted&period=all", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", f"/api/tasks/missed-updates?day={today}", None, {A: 2, MG: 2}, None),
    ("GET", "/api/workflow/settings", None, {A: 4, MG: 2}, None),  # the very first read creates the one settings row
    ("GET", "/api/daily-work", None, {A: 8, MG: 8, ST: 7}, None),
    ("GET", "/api/follow-up", None, {A: 6, MG: 6, ST: 5}, None),
    ("GET", "/api/daily-goals", None, {A: 3, MG: 3}, None),
    ("GET", "/api/attendance", None, {A: 4, MG: 4, ST: 4}, None),
    ("GET", "/api/attendance?staffId=budget-staff-0", None, {A: 4, MG: 5}, None),
    ("GET", f"/api/attendance/month?{month}", None, {A: 10, MG: 10, ST: 10}, None),
    ("GET", f"/api/attendance/requests?{month}", None, {A: 4, MG: 4, ST: 4}, None),
    ("GET", f"/api/attendance/settings?{month}", None, {A: 6, MG: 7, ST: 7}, None),
    ("GET", f"/api/attendance/adjustments?{month}", None, {A: 3, MG: 3, ST: 3}, None),
    ("GET", f"/api/reports/daily-work?from={today}&to={today}", None, {A: 8, MG: 8, ST: 7}, None),
    ("GET", f"/api/reports/daily-work?{span}", None, {A: 7, MG: 7, ST: 6}, None),
    ("GET", f"/api/reports/daily-work/drilldown?metric=prescreened&{span}", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", f"/api/reports/daily-work/history?{span}", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", "/api/reports/daily-work/mine", None, {A: 4, MG: 4, ST: 4}, None),
    ("GET", f"/api/reports/daily-work/export?{span}", None, {A: 8, MG: 8, ST: 7}, None),
    ("GET", f"/api/reports?from_date={(TODAY - timedelta(days=89)).isoformat()}&to_date={today}", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", "/api/reports/funnel/export?format=xlsx", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", f"/api/reports/staff-weekly?from_date={(TODAY - timedelta(days=6)).isoformat()}&to_date={today}", None, {A: 22}, None),
    ("GET", "/api/reports/staff-weekly/budget-staff-1", None, {A: 25}, None),
    ("GET", "/api/settings", None, {A: 4, MG: 4, ST: 4}, None),
    ("GET", "/api/settings/public", None, {A: 1, MG: 1, ST: 1}, None),
    ("GET", "/api/settings/categories", None, {A: 4, MG: 4, ST: 4}, None),
    ("GET", "/api/users", None, {A: 2}, None),
    ("GET", "/api/users/budget-staff-1/audit", None, {A: 3}, None),
    ("GET", "/api/staff-directory", None, {A: 2, MG: 2}, None),
    ("GET", "/api/staff-directory/availability", None, {A: 2, MG: 2}, None),
    ("GET", "/api/master-data/client/budget-client/audit", None, {A: 3, MG: 3}, None),
    ("GET", "/api/form-options", None, {A: 2, MG: 2, ST: 2}, None),
    ("GET", "/api/form-options/suggestions", None, {A: 8, MG: 8, ST: 8}, None),
    ("GET", "/api/intake-acknowledgements/email/budget-mailbox", None, {A: 4, ST: 4}, None),
    ("GET", "/api/intake-acknowledgements/whatsapp/budget-wa", None, {A: 5, ST: 5}, None),
    ("GET", "/api/health", None, {A: 3}, None),
    ("POST", "/api/notifications/read-all", None, {ST: 2}, None),
    ("POST", "/api/presence/heartbeat", pulse, {ST: 9}, "POST /api/presence/heartbeat (first)"),
    ("POST", "/api/presence/heartbeat", pulse, {ST: 9}, "POST /api/presence/heartbeat (steady)"),
    ("POST", "/api/auth/login", {"email": "budget-staff-0@example.test", "password": PASSWORD}, {A: 2}, None),
]


def test_endpoint_statement_budgets(env):
    failures = []
    for method, url, body, measured, label in SPEC:
        for role, count in measured.items():
            try:
                call(env, role, method, url, label, body=body, budget=count + HEADROOM)
            except AssertionError as error:
                failures.append(str(error)[:400])
    pytest_table()
    assert not failures, "\n\n".join(failures)


def test_intake_acknowledgement_lists_load_their_send_attempts_together(env):
    """The WhatsApp list used to read each attempt's outgoing message on its own (20 extra statements)."""
    response, statements = call(env, A, "GET", "/api/intake-acknowledgements/whatsapp/budget-wa", budget=6)
    body = response.json()
    assert len(body["attempts"]) == 20
    assert {attempt["status"] for attempt in body["attempts"]} == {"sent"}
    assert all(attempt["sentAt"] for attempt in body["attempts"])  # taken from the joined send attempt
    # The reads are autocommit GETs: no SAVEPOINT (which only works inside a transaction) and a template choice that is reported.
    assert not any(statement.startswith("SAVEPOINT") for statement in statements)
    assert body["templateAvailable"] is True and body["template"] is None
    email, _ = call(env, A, "GET", "/api/intake-acknowledgements/email/budget-mailbox", budget=5)
    assert len(email.json()["attempts"]) == 20 and email.json()["templateAvailable"] is False
    client, _engine, _ids = env
    missing = client.get("/api/intake-acknowledgements/whatsapp/not-a-channel", headers={"Cookie": f"{SESSION_COOKIE}=sess-budget-admin"})
    assert missing.status_code == 404


def test_master_data_audit_still_reports_a_missing_record(env):
    client, engine, _ids = env
    headers = {"Cookie": f"{SESSION_COOKIE}=sess-budget-admin"}
    assert client.get("/api/master-data/client/nobody/audit", headers=headers).status_code == 404
    with sessionmaker(bind=engine)() as db:
        db.add(m.AuditEntry(actor_id="budget-admin", action="Updated client", entity_type="client", entity_id="budget-client", target="Client Contact"))
        db.commit()
    rows = client.get("/api/master-data/client/budget-client/audit", headers=headers).json()
    assert [row["action"] for row in rows] == ["Updated client"]


def test_daily_goals_list_every_recruiter_with_their_overrides(env):
    client, _engine, ids = env
    headers = {"Cookie": f"{SESSION_COOKIE}=sess-budget-admin"}
    before = client.get("/api/daily-goals", headers=headers).json()
    assert sorted(row["userId"] for row in before["staff"]) == sorted(ids["staff"])
    assert [row["name"] for row in before["staff"]] == sorted(row["name"] for row in before["staff"])
    assert all(row["overrides"] == [] for row in before["staff"])
    saved = client.put(f"/api/daily-goals/staff/{ids['staff'][0]}", headers=headers, json={"candidates": 9, "morning": 4})
    assert saved.status_code == 200 and saved.json()["overrides"] == ["candidates"]
    after = {row["userId"]: row for row in client.get("/api/daily-goals", headers=headers).json()["staff"]}
    assert after[ids["staff"][0]]["goals"]["candidates"] == 9 and after[ids["staff"][0]]["goals"]["morning"] == 4
    assert after[ids["staff"][1]]["overrides"] == []


def test_daily_work_lists_are_shared_between_tabs_until_something_changes(env, monkeypatch):
    """GET /api/daily-work and /api/follow-up are rebuilt only when a change feed topic they read moves (or after 10 s)."""
    from app.services import response_cache
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    response_cache.clear()
    client, engine, _ids = env
    try:
        for url in ("/api/daily-work", "/api/follow-up"):
            headers = {"Cookie": f"{SESSION_COOKIE}=sess-budget-staff-0"}
            with counting(engine) as cold:
                first = client.get(url, headers=headers)
            with counting(engine) as warm:
                second = client.get(url, headers=headers)
            assert first.status_code == second.status_code == 200
            assert len(cold) > 3 and len(warm) == 1, (url, len(cold), len(warm))  # the warm one only checks the sign-in
            assert first.json() == second.json()
            assert first.json()["items"], url
            # Another recruiter never receives this person's entry.
            other = client.get(url, headers={"Cookie": f"{SESSION_COOKIE}=sess-budget-staff-1"})
            mine = {row["candidateId"] for row in first.json()["items"]}
            assert other.status_code == 200 and not mine & {row["candidateId"] for row in other.json()["items"]}
            # A change to a candidate this person owns empties the memo, so the next read shows it.
            changed = first.json()["items"][0]["candidateId"]
            with sessionmaker(bind=engine)() as db:
                db.execute(text("UPDATE crm_candidates SET name = :name WHERE id = :id"), {"name": "Renamed " + url[5:], "id": changed})
                db.commit()
            with counting(engine) as after:
                third = client.get(url, headers=headers)
            assert len(after) > 1
            assert "Renamed " + url[5:] in {item["candidate"] for item in third.json()["items"]}
    finally:
        response_cache.clear()
