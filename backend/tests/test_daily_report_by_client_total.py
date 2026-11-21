"""By client: a row's Total is the sum of the seven columns the table shows, hand-computed on a small dataset of two clients.

Time is frozen at Thursday 2026-11-05 14:00 IST. Stage completions (IST), counted per client:

  Gulf Builders   11-05  forwarded c1 c2 (2); prescreened c3 (passed) c4 (failed) c5 (passed) (3); scheduled c6 (1);
                         interview results c7 (scheduled online on 11-04) and c8 (in person): 2 conducted, 1 online;
                         offer c9 agreed (1); air ticket c10 (1); no visa
                  -> forwarded 2, prescreened 3, shortlisted 1, online interviews 1, offers 1, visas 0, travelled 1: Total 9
                     (the old sum also added the 2 interviews conducted, a column the table does not show: it said 10)
  Desert Co       11-05  forwarded c11, prescreened c13, visa c12 arrived; 'Not forwarded' c16 and a reopened forward c15 are not
                         counted; forwarded c14 on 11-03 is outside the day
                  -> forwarded 1, prescreened 1, visas 1: Total 3 (the week 11-02..11-05 has c14 too: forwarded 2, Total 4)
"""
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Candidate, Client, Company, Designation, Order, Role, Source, User,
)
from test_database_naming import _guard_test_url, _migrate
from test_daily_work_report import NOW, TZ, report_router, service, deps, get_db, ist, sheets_of

DAY = {"from": "2026-11-05", "to": "2026-11-05"}
COLUMNS = ["forwarded", "prescreened", "shortlisted", "interviewsOnline", "offers", "visas", "travelled"]


def cells(row):
    return {key: row["interviewsOnline"] if key == "interviewsOnline" else row["metrics"][key] for key in COLUMNS}


@pytest.fixture(scope="module")
def world():
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
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        seed(sessions)
        yield SimpleNamespace(engine=engine, sessions=sessions)
    finally:
        reset()
        engine.dispose()


def seed(sessions):
    day = date(2026, 11, 5)
    with sessions() as db:
        long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True, created_at=long_ago,
                         updated_at=long_ago) for key, role in (("admin", Role.admin), ("asha", Role.staff), ("ravi", Role.staff))])
        db.add_all([Client(id="client", name="Mr Contact"), Client(id="client2", name="Ms Contact")])
        db.flush()
        db.add_all([Company(id="gulf", client_id="client", name="Gulf Builders", country="UAE"),
                    Company(id="desert", client_id="client2", name="Desert Co", country="UAE")])
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add_all([Order(id="o-gulf", ref="ORD-1", company_id="gulf", designation_id="designation", openings=40, country="Qatar"),
                    Order(id="o-desert", ref="ORD-2", company_id="desert", designation_id="designation", openings=40, country="Qatar")])
        db.flush()
        made = set()

        def did(key, stage, outcome, at, by, *, order, attempt=1, reopened=None, data=None):
            if key not in made:
                made.add(key)
                db.add(Candidate(id=key, ref=f"CND-{key}", name=f"Candidate {key}", phone=f"9100000{len(made):04}",
                                 source=Source.walk_in, owner_id=by))
                db.flush()
                db.add(Application(id=f"app-{key}", candidate_id=key, order_id=order, owner_id=by, current_stage=1,
                                   status=ApplicationStatus.active))
                db.flush()
            db.add(ApplicationStage(application_id=f"app-{key}", stage=stage, attempt=attempt, outcome=outcome, completed_by=by,
                                    owner_id=by, completed_at=at, reopened_reason=reopened, data=data or {}))

        g, d = "o-gulf", "o-desert"
        for key, who in (("c1", "asha"), ("c2", "ravi")):
            did(key, 2, "Forwarded", ist(day, 10), who, order=g)
        for key, outcome, who in (("c3", "Passed", "asha"), ("c4", "Failed", "asha"), ("c5", "Passed", "ravi")):
            did(key, 1, outcome, ist(day, 9), who, order=g)
        did("c6", 3, "Scheduled", ist(day, 11), "asha", order=g, data={"mode": "online"})
        did("c7", 3, "Scheduled", ist(date(2026, 11, 4), 11), "asha", order=g, data={"mode": "online"})
        did("c7", 4, "Selected", ist(day, 12), "asha", order=g)
        did("c8", 3, "Scheduled", ist(date(2026, 11, 4), 12), "ravi", order=g, data={"mode": "in_person"})
        did("c8", 4, "Rejected", ist(day, 12, 30), "ravi", order=g)
        did("c9", 5, "Agreed", ist(day, 10, 30), "ravi", order=g)
        did("c10", 13, "Booked", ist(day, 13), "asha", order=g, data={"travelDate": "2026-11-20"})

        did("c11", 2, "Forwarded", ist(day, 10), "ravi", order=d)
        did("c13", 1, "Passed", ist(day, 9, 30), "asha", order=d)
        did("c12", 10, "Arrived", ist(day, 11), "asha", order=d)
        did("c16", 2, "Not forwarded", ist(day, 10, 30), "ravi", order=d)
        did("c15", 2, "Forwarded", ist(day, 10, 45), "ravi", order=d, reopened="redo")
        did("c14", 2, "Forwarded", ist(date(2026, 11, 3), 10), "ravi", order=d)
        db.commit()


@pytest.fixture
def env(world, monkeypatch):
    monkeypatch.setattr(report_router, "_now", lambda: NOW)
    monkeypatch.setattr(report_router, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    actor = SimpleNamespace(id="admin", name="Admin", role=Role.admin)
    app = FastAPI()
    app.include_router(report_router.router)

    def database():
        with world.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[deps.current_user] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client)


def fetch(env, path="", **params):
    response = env.client.get("/api/reports/daily-work" + path, params=params)
    assert response.status_code == 200, response.text
    return response


def test_each_client_column_and_the_total_match_the_hand_computed_figures(env):
    rows = fetch(env, **DAY).json()["byClient"]
    assert [r["name"] for r in rows] == ["Gulf Builders", "Desert Co"]
    gulf, desert = rows
    assert cells(gulf) == {"forwarded": 2, "prescreened": 3, "shortlisted": 1, "interviewsOnline": 1, "offers": 1, "visas": 0, "travelled": 1}
    assert gulf["metrics"]["interviews"] == 2   # conducted in every mode: not a column of the table, so not in the Total
    assert gulf["total"] == 9
    assert cells(desert) == {"forwarded": 1, "prescreened": 1, "shortlisted": 0, "interviewsOnline": 0, "offers": 0, "visas": 1, "travelled": 0}
    assert desert["total"] == 3
    for row in rows:
        assert row["total"] == sum(cells(row).values())


def test_a_longer_period_adds_the_earlier_day_and_keeps_total_equal_to_the_columns(env):
    rows = fetch(env, **{"from": "2026-11-02", "to": "2026-11-05"}).json()["byClient"]
    desert = next(r for r in rows if r["id"] == "desert")
    assert desert["metrics"]["forwarded"] == 2 and desert["total"] == 4
    for row in rows:
        assert row["total"] == sum(cells(row).values())


def test_the_other_clients_roll_up_and_the_sort_use_the_same_total(env, monkeypatch):
    monkeypatch.setattr(service, "BY_CLIENT_MAX", 1)
    rows = fetch(env, **DAY).json()["byClient"]
    assert [(r["id"], r["total"]) for r in rows] == [("gulf", 9), ("other", 3)]
    assert rows[1]["total"] == sum(cells(rows[1]).values())


def test_the_helper_adds_exactly_the_seven_columns():
    metrics = {key: 1 for key in service.METRIC_KEYS}
    metrics["interviews"] = 50
    assert service.client_total(metrics, 4) == 6 + 4   # six metric columns plus online interviews; interviews / leadsContacted left out
    assert service.CLIENT_COLUMNS == ("forwarded", "prescreened", "shortlisted", "interviewsOnline", "offers", "visas", "travelled")


def test_the_export_by_client_sheet_matches_the_table(env):
    book, _ = sheets_of(fetch(env, "/export", **DAY).content)
    sheet = book["By client"]
    assert sheet[0][1:8] == ["Resumes forwarded to clients", "Resumes prescreened", "Workers shortlisted by clients", "Online interviews",
                             "Offer letters received", "Visas received", "Workers travelled"]
    assert sheet[0][8] == "Total"
    assert sheet[1][:9] == ["Gulf Builders", 2, 3, 1, 1, 1, 0, 1, 9] and sheet[2][:9] == ["Desert Co", 1, 1, 0, 0, 0, 1, 0, 3]
    assert sheet[1][9] == 2   # interviews conducted, shown after the Total and not part of it
