"""Client tracker by client: openings, in process, placed, fill rate, last activity and status per client (disposable PostgreSQL).

Every name, client and agency below is invented.
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text, event
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStatus, Candidate, Client, ClosureReason, Company, Designation, Order, OrderStatus, Role, Source, User,
)
from test_database_naming import _migrate, schema_database

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
                           agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.api.routers import reports as subject
from app.services import client_tracker_clients as clients

NOW = datetime.now(timezone.utc)
RECENT, OLD = NOW - timedelta(days=2), NOW - timedelta(days=30)

# company key, name, country, orders: [(key, status, openings, updated_at)]
COMPANIES = [
    ("alpha", "Alpha Test", "Kuwait", [("a1", "open", 3, RECENT)]),
    ("bravo", "Bravo Test", "UAE", [("b1", "open", 2, OLD)]),
    ("charlie", "Charlie =Test", "Qatar", [("c1", "filled", 1, RECENT)]),
    ("delta", "Delta Test", "Oman", [("d1", "closed", 4, OLD)]),
    ("echo", "Echo Test", "UAE", [("e1", "open", 1, OLD), ("e2", "on_hold", 2, OLD)]),
]
# candidate key, order, owner, status, stage, updated_at
PLACEMENTS = [
    ("ann", "a1", "staff", "deployed", 13, RECENT),
    ("ben", "a1", "staff", "active", 5, RECENT),
    ("cam", "a1", "other", "active", 1, RECENT),
    ("dee", "a1", "other", "closed", 3, RECENT),        # closed: never counted
    ("eli", "b1", "other", "active", 8, OLD),
    ("fay", "c1", "other", "deployed", 13, RECENT),
    ("gus", "e2", "other", "active", 2, OLD),
]


@pytest.fixture
def env(schema_database):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("staff", Role.staff), ("other", Role.staff))])
        db.add(Client(id="client", name="Contact Person"))
        db.add(Designation(id="job", title="Driver"))
        db.flush()
        number = 0
        for key, name, country, orders in COMPANIES:
            db.add(Company(id=key, client_id="client", name=name, country=country))
            db.flush()
            for order_key, status, openings, updated in orders:
                number += 1
                db.add(Order(id=order_key, ref=f"ORD-{number:02d}", company_id=key, designation_id="job", openings=openings, status=OrderStatus(status),
                             country=country, updated_at=updated, created_at=updated))
        db.flush()
        for number, (key, order, owner, status, stage, updated) in enumerate(PLACEMENTS, 1):
            db.add(Candidate(id=f"c-{key}", ref=f"CND-{number:05d}", name=key.title() + " Person", phone=f"90000000{number:02d}", source=Source.other,
                             owner_id=owner, care_id=f"CI-{number:03d}"))
            db.flush()
            db.add(Application(id=f"app-{key}", candidate_id=f"c-{key}", order_id=order, owner_id=owner, current_stage=stage,
                               status=ApplicationStatus(status), closure_reason=ClosureReason.interview_rejected if status == "closed" else None,
                               created_at=updated, updated_at=updated))   # a placement's activity is when it was mapped / moved / closed, never its last edit
        db.commit()
    state = SimpleNamespace(sessions=sessions, engine=schema_database, actor=SimpleNamespace(id="admin", role=Role.admin))
    app = FastAPI()
    app.include_router(subject.router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: state.actor
    with TestClient(app) as client:
        state.client = client
        yield state


def get(env, **params):
    response = env.client.get("/api/reports/client-tracker/clients", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def named(body):
    return {client["name"]: client for client in body["clients"]}


def test_each_client_has_its_openings_in_process_placed_and_rate(env):
    body = get(env)
    rows = named(body)
    assert [c["name"] for c in body["clients"]] == sorted((c["name"] for c in body["clients"]), key=str.lower)
    alpha = rows["Alpha Test"]
    assert (alpha["orderCount"], alpha["openOrders"], alpha["openings"], alpha["inProcess"], alpha["placed"]) == (1, 1, 2, 2, 1)
    assert alpha["fillRate"] == 33.3 and alpha["status"] == "active" and alpha["active"] and not alpha["stalled"]
    order = alpha["orders"][0]
    assert (order["openings"], order["placed"], order["inProcess"], order["open"]) == (3, 1, 2, 2)
    # candidates by stage: those still in process first (by stage), the ones who travelled last; a closed placement is not listed
    assert [(c["name"], c["stageNo"], c["travelled"]) for c in order["candidates"]] == [("Cam Person", 1, False), ("Ben Person", 5, False), ("Ann Person", 13, True)]
    assert order["candidates"][2]["stage"] == "Travelled" and order["candidates"][0]["stage"] == "Prescreening"


def test_stalled_idle_and_the_rate_ignores_orders_that_never_happened(env):
    rows = named(get(env))
    assert rows["Bravo Test"]["status"] == "stalled" and rows["Bravo Test"]["stalled"] and rows["Bravo Test"]["active"]
    echo = rows["Echo Test"]                                  # an open order and an on-hold one, nobody placed, nothing moved for a month
    assert (echo["orderCount"], echo["openOrders"], echo["openings"], echo["inProcess"]) == (2, 2, 3, 1)
    assert echo["status"] == "stalled" and echo["fillRate"] == 0
    charlie = rows["Charlie =Test"]                           # filled: nothing open, so idle, and fully placed
    assert (charlie["openings"], charlie["placed"], charlie["status"], charlie["fillRate"]) == (0, 1, "idle", 100.0)
    delta = rows["Delta Test"]                                # closed with nobody placed: idle, and not held against the client
    assert (delta["openings"], delta["status"], delta["fillRate"]) == (0, "idle", None)


def test_a_bulk_edit_does_not_make_a_stalled_client_look_active(env):
    before = named(get(env))["Bravo Test"]
    assert before["status"] == "stalled"
    with env.sessions() as db:   # an import or a bulk edit touches updated_at of every placement and order's placements today
        db.execute(text("UPDATE crm_applications SET updated_at = now()"))
        db.commit()
    from app.services import response_cache
    response_cache.clear()   # the figure is re-read, not served from the short-lived cache
    after = named(get(env))["Bravo Test"]
    assert after["status"] == "stalled" and after["lastActivity"] == before["lastActivity"]   # nothing HAPPENED to it: still stalled, same last activity


def test_the_cards_add_up_the_clients(env):
    summary = get(env)["summary"]
    assert summary == {"clients": 5, "activeClients": 3, "stalledClients": 2, "idleClients": 2, "openOrders": 4, "openings": 7, "inProcess": 4,
                       "placed": 2, "fillRate": 22.2, "stalledDays": 14}


def test_a_recruiter_sees_only_the_clients_and_placements_they_own(env):
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    body = get(env)
    assert [c["name"] for c in body["clients"]] == ["Alpha Test"]
    alpha = body["clients"][0]
    assert (alpha["inProcess"], alpha["placed"]) == (1, 1)
    assert sorted(c["name"] for c in alpha["orders"][0]["candidates"]) == ["Ann Person", "Ben Person"]
    env.actor = SimpleNamespace(id="manager", role=Role.manager)
    assert len(get(env)["clients"]) == 5


def test_the_excel_file_carries_the_clients_the_screen_shows(env):
    def sheets(**params):
        response = env.client.get("/api/reports/client-tracker/clients", params={"format": "xlsx", **params})
        assert response.status_code == 200 and response.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert response.headers["content-disposition"].startswith('attachment; filename="client-tracker-clients-')
        with ZipFile(BytesIO(response.content)) as archive:
            return archive.read("xl/worksheets/sheet1.xml").decode(), archive.read("xl/worksheets/sheet2.xml").decode()

    first, second = sheets()
    for name in ("Alpha Test", "Bravo Test", "Delta Test", "Echo Test"):
        assert name in first
    assert "Charlie =Test" in first            # only a leading "=" is guarded, not one inside a name
    for header in clients.CLIENT_COLUMNS:
        assert header in first
    for header in clients.ORDER_COLUMNS:
        assert header in second
    assert "Ben Person" in second and "Waiting for signed offer" in second and "Dee Person" not in second
    stalled, orders = sheets(status="stalled")
    assert "Bravo Test" in stalled and "Echo Test" in stalled and "Alpha Test" not in stalled and "Alpha Test" not in orders
    searched, _ = sheets(q="charlie")
    assert "Charlie" in searched and "Alpha Test" not in searched
    assert env.client.get("/api/reports/client-tracker/clients", params={"status": "bogus"}).status_code == 422


def test_a_leading_equals_sign_is_kept_as_text():
    assert clients._cell("=SUM(A1)") == "'=SUM(A1)" and clients._cell("Plain") == "Plain" and clients._cell("a\x07b") == "ab" and clients._cell(3) == 3


def test_the_screen_costs_a_fixed_number_of_statements(env):
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(env.engine, "before_cursor_execute", record)
    try:
        get(env)
    finally:
        event.remove(env.engine, "before_cursor_execute", record)
    data = [s for s in seen if "crm_applications" in s or "crm_job_orders" in s]
    assert len(data) <= 3, data      # orders, placements, newest change per order: never one per client
