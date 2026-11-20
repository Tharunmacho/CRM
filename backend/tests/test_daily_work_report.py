"""Daily CRM Work Report: every figure, the previous period, series, per-staff rows, filters, scope, drill-down, day close,
export and the statement budgets.

Runs on the explicitly guarded disposable PostgreSQL DB (see test_database_naming._guard_test_url). The agency zone is
Asia/Kolkata (UTC+5:30). Time is frozen at Thursday 2026-11-05 14:00 IST; one dataset is seeded once for the module.

The dataset (IST). Stages are written by hand, as the stage endpoint would, on candidates c1..c24:
  Wed 11-04  asha prescreened c15 at 23:30 (Wednesday), scheduled c1 (online interview) and ravi scheduled c8 (in person);
             ravi forwarded c13 but it was then REOPENED, and completed again on Thursday (counts once, Thursday)
  Thu 11-05  asha prescreened c16 at 00:30 (Thursday), c1 and c2 (failed); forwarded c1 and c14 (c14 belongs to ravi: the credit
             goes to asha, who completed it); interview c1 selected. ravi forwarded c3 and c13, 'not forwarded' c4 (not counted),
             offers c5 agreed / c6 declined / c7 not received (not counted), interview c8 rejected. meena: visa c9 arrived,
             c10 visa rejected (not counted), c11 travelled, c12 not travelling (not counted), scheduled c23, 'interview not
             possible' c22 (not counted), a medical c9 (not a counted stage). c3, c5, c9 belong to the order of a second client.
  before     10-30 asha prescreened c19, ravi forwarded c17; 10-31 meena prescreened c20; 11-01 meena travelled c24;
             10-20 ravi forwarded c18 (outside every period used here)
Leads: asha holds L1-L6 and works all six on Thursday (L1 also on Wednesday), ravi holds L7-L9 and works L7 and L8 (a note on
L9 is not a contact), L11 is converted, L10 nobody's.
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, Client, Company, Designation, Lead,
    LeadActivity, Order, Role, Session as SessionRow, Source, Task, TaskKind, User,
)
from app.domain.stages import stage_by_no
from test_database_naming import _guard_test_url, _migrate

_settings_patch = patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
))
_stand_in = _settings_patch.start()
try:
    from app.api.routers import daily_work_report as report_router
    from app.core import deps
    from app.core.security import SESSION_COOKIE
    from app.db.session import get_db
    from app.services import daily_work_report as service
finally:
    _settings_patch.stop()
    import sys
    from app.core.config import get_settings as _real_settings
    for _name, _module in list(sys.modules.items()):
        if _name.startswith("app.") and getattr(_module, "get_settings", None) is _stand_in:
            _module.get_settings = _real_settings

TZ = "Asia/Kolkata"
IST = ZoneInfo(TZ)
WED, THU = date(2026, 11, 4), date(2026, 11, 5)
MON = date(2026, 11, 2)
NOW = datetime(2026, 11, 5, 14, 0, tzinfo=IST).astimezone(timezone.utc)
KEYS = ["leadsContacted", "prescreened", "forwarded", "shortlisted", "interviews", "offers", "visas", "travelled"]
PEOPLE = {"admin": Role.admin, "manager": Role.manager, "asha": Role.staff, "ravi": Role.staff, "meena": Role.staff,
          "zed": Role.staff, "gone": Role.staff}
PHONES, PASSPORT, TRAVELLER = [], "Z9876543", "CI-T-001"


def ist(day, hour=0, minute=0):
    return datetime.combine(day, time(hour, minute), IST).astimezone(timezone.utc)


# ------------------------------------------------------------------ the data

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
    with sessions() as db:
        long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=key != "gone",
                         created_at=long_ago, updated_at=long_ago) for key, role in PEOPLE.items()])
        db.add_all([Client(id="client", name="Mr Contact"), Client(id="client2", name="Ms Contact")])
        db.flush()
        db.add_all([Company(id="company", client_id="client", name="Gulf Builders", country="UAE"),
                    Company(id="company2", client_id="client2", name="Desert Co", country="UAE")])
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add_all([Order(id="order", ref="ORD-1", company_id="company", designation_id="designation", openings=40, country="Qatar"),
                    Order(id="order2", ref="ORD-2", company_id="company2", designation_id="designation", openings=40, country="Qatar")])
        db.flush()

        made = set()

        def did(key, stage, outcome, at, by, *, attempt=1, reopened=None, data=None, owner="ravi", order="order"):
            if key not in made:
                made.add(key)
                phone = f"90000{len(made):05}"
                PHONES.append(phone)
                db.add(Candidate(id=key, ref=f"CND-{key}", name=f"Candidate {key}", phone=phone, source=Source.walk_in, owner_id=owner,
                                 passport_no=PASSPORT if key == "c11" else None, traveller_id=TRAVELLER if key == "c11" else None))
                db.flush()
                db.add(Application(id=f"app-{key}", candidate_id=key, order_id=order, owner_id=owner, current_stage=1,
                                   status=ApplicationStatus.active))
                db.flush()
            db.add(ApplicationStage(application_id=f"app-{key}", stage=stage, attempt=attempt, outcome=outcome, completed_by=by,
                                    owner_id=owner, completed_at=at, reopened_reason=reopened, data=data or {}))

        did("c15", 1, "Passed", ist(WED, 23, 30), "asha", owner="asha")
        did("c13", 2, "Forwarded", ist(WED, 11), "ravi", reopened="redo")
        did("c13", 2, "Forwarded", ist(THU, 11, 30), "ravi", attempt=2)
        did("c1", 3, "Scheduled", ist(WED, 15), "asha", data={"mode": "online"}, owner="asha")
        did("c8", 3, "Scheduled", ist(WED, 16), "ravi", data={"mode": "in_person"})
        did("c16", 1, "Passed", ist(THU, 0, 30), "asha", owner="asha")
        did("c1", 1, "Passed", ist(THU, 10), "asha", owner="asha")
        did("c2", 1, "Failed", ist(THU, 11), "asha", owner="asha")
        did("c1", 2, "Forwarded", ist(THU, 12), "asha", owner="asha")
        did("c14", 2, "Forwarded", ist(THU, 12, 30), "asha")
        did("c1", 4, "Selected", ist(THU, 13, 30), "asha", owner="asha")
        did("c3", 2, "Forwarded", ist(THU, 9, 30), "ravi", order="order2")
        did("c4", 2, "Not forwarded", ist(THU, 9, 45), "ravi")
        did("c5", 5, "Agreed", ist(THU, 10), "ravi", order="order2")
        did("c6", 5, "Declined", ist(THU, 10, 10), "ravi")
        did("c7", 5, "Not received", ist(THU, 10, 20), "ravi")
        did("c8", 4, "Rejected", ist(THU, 13), "ravi")
        did("c9", 6, "Fit", ist(THU, 10), "meena", owner="meena", order="order2")
        did("c9", 10, "Arrived", ist(THU, 11), "meena", owner="meena", order="order2")
        did("c10", 10, "Visa rejected", ist(THU, 11, 10), "meena", owner="meena")
        did("c11", 12, "Booked", ist(THU, 12), "meena", owner="meena", data={"travelDate": "2026-11-20"})
        did("c12", 12, "Not travelling", ist(THU, 12, 10), "meena", owner="meena")
        did("c22", 3, "Interview not possible", ist(THU, 11, 20), "meena", owner="meena")
        did("c23", 3, "Completed manually", ist(THU, 11, 30), "meena", owner="meena")
        did("c19", 1, "Passed", ist(date(2026, 10, 30), 10), "asha", owner="asha")
        did("c17", 2, "Forwarded", ist(date(2026, 10, 30), 11), "ravi")
        did("c20", 1, "Passed", ist(date(2026, 10, 31), 10), "meena", owner="meena")
        did("c24", 12, "Booked", ist(date(2026, 11, 1), 12), "meena", owner="meena")
        did("c18", 2, "Forwarded", ist(date(2026, 10, 20), 10), "ravi")

        assigned = ist(date(2026, 10, 1), 9)
        for key, owner, status, follow in (
                ("L1", "asha", "interested", None), ("L2", "asha", "new", None), ("L3", "asha", "wrong_number", None),
                ("L4", "asha", "not_interested", None), ("L5", "asha", "invalid", None),
                ("L6", "asha", "contacted", ist(date(2026, 11, 6), 10)),
                ("L7", "ravi", "new", None), ("L8", "ravi", "interested", None), ("L9", "ravi", "new", ist(WED, 10)),
                ("L10", None, "new", None), ("L11", "ravi", "converted", None)):
            db.add(Lead(id=key, name=f"Lead {key}", phone=f"+9180000000{key[1:].zfill(2)}", owner_id=owner, status=status,
                        assigned_at=assigned if owner else None, created_time=assigned, created_at=assigned, next_follow_up_at=follow,
                        position_applied="Chef"))
        db.flush()
        # When leads were handed out and uploaded (agency time): the period-aware lead numbers read these.
        for key, when in (("L5", ist(WED, 9)), ("L8", ist(WED, 23, 30)), ("L9", ist(THU, 0, 30)), ("L7", ist(THU, 9)),
                          ("L1", ist(THU, 10)), ("L2", ist(THU, 12, 30))):
            db.execute(text("UPDATE crm_leads SET assigned_at = :t WHERE id = :i"), {"t": when, "i": key})
        for key, when in (("L4", ist(WED, 10)), ("L3", ist(THU, 8)), ("L10", ist(THU, 9))):
            db.execute(text("UPDATE crm_leads SET created_at = :t WHERE id = :i"), {"t": when, "i": key})

        def act(lead, user, kind, at, **meta):
            db.add(LeadActivity(lead_id=lead, user_id=user, kind=kind, summary=kind, meta=meta or None, created_at=at))

        act("L1", "asha", "call", ist(WED, 10), outcome="answered")
        act("L1", "asha", "call", ist(THU, 11), outcome="answered")
        act("L1", "asha", "call", ist(THU, 11, 30), outcome="interested")
        act("L2", "asha", "call", ist(THU, 11, 40), outcome="no_answer")
        act("L3", "asha", "call", ist(THU, 11, 50), outcome="wrong_number")
        act("L4", "asha", "call", ist(THU, 12), outcome="not_interested")
        act("L5", "asha", "status", ist(THU, 12, 10), **{"from": "new", "to": "invalid"})
        act("L6", "asha", "call", ist(THU, 12, 20), outcome="answered")
        act("L7", "ravi", "call", ist(THU, 10), outcome="answered")
        act("L7", "ravi", "call", ist(THU, 10, 30), outcome="no_answer")
        act("L8", "ravi", "call", ist(THU, 11), outcome="interested")
        act("L8", "ravi", "resume", ist(THU, 11, 30))
        act("L9", "ravi", "note", ist(THU, 12))
        act("L9", "ravi", "call", ist(date(2026, 10, 30), 10), outcome="answered")
        act("L2", "asha", "call", ist(date(2026, 10, 31), 10), outcome="answered")

        # The pipeline right now: where each active placement stands, and who is closed / deployed / not on an order yet.
        for key, stage_no in (("c1", 5), ("c15", 2), ("c16", 2), ("c19", 2), ("c3", 3), ("c5", 6), ("c6", 6), ("c8", 5), ("c13", 3),
                              ("c14", 3), ("c17", 3), ("c18", 3), ("c9", 11), ("c22", 3), ("c23", 4), ("c20", 2)):
            db.execute(text("UPDATE crm_applications SET current_stage = :s WHERE id = :a"), {"s": stage_no, "a": f"app-{key}"})
        db.execute(text("UPDATE crm_applications SET status = 'closed' WHERE id IN ('app-c2', 'app-c4', 'app-c7', 'app-c10', 'app-c12')"))
        db.execute(text("UPDATE crm_applications SET status = 'deployed', current_stage = 12 WHERE id IN ('app-c11', 'app-c24')"))
        db.execute(text("UPDATE crm_candidates SET status = 'deployed' WHERE id IN ('c11', 'c24')"))
        for key, owner, status in (("u1", "asha", CandidateStatus.new), ("u2", "asha", CandidateStatus.in_process),
                                   ("u3", "ravi", CandidateStatus.not_interested), ("u4", "meena", CandidateStatus.new)):
            db.add(Candidate(id=key, ref=f"CND-{key}", name=f"Candidate {key}", phone=f"91000{key[1:].zfill(5)}", source=Source.walk_in,
                             owner_id=owner, status=status))
        # Tasks: open ones are pending, an open one past its due time is overdue, a cancelled one is nothing.
        def task(key, owner, candidate, *, due=None, done=None, cancelled=None, kind=TaskKind.stage):
            db.add(Task(id=key, kind=kind, title="Initial call: confirm interest" if kind == TaskKind.initial_call else "Forward CV to client",
                        candidate_id=candidate, owner_id=owner, created_at=ist(WED, 9), due_at=due, completed_at=done, cancelled_at=cancelled))

        task("t1", "asha", "c1", done=ist(THU, 12), due=ist(THU, 20))
        task("t2", "asha", "c2", done=ist(THU, 11), kind=TaskKind.initial_call)
        task("t3", "asha", "c15", done=ist(WED, 10))
        task("t4", "asha", "c16", due=ist(date(2026, 11, 6), 9))
        task("t5", "asha", "c19", due=ist(WED, 9))
        task("t6", "asha", "c1", cancelled=ist(WED, 12), due=ist(WED, 9))
        task("t7", "ravi", "c3", due=ist(date(2026, 11, 3), 9))
        task("t8", "ravi", "c5", due=ist(date(2026, 11, 4), 9))
        task("t9", "ravi", "c8", done=ist(THU, 13))
        db.commit()


# ------------------------------------------------------------------ the HTTP desk

@pytest.fixture
def env(world, monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(report_router, "_now", lambda: clock["now"])
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
        yield SimpleNamespace(client=client, clock=clock, actor=actor, world=world, app=app, database=database)
    with world.sessions() as db:   # day-close entries belong to one test
        db.execute(text("DELETE FROM system_audit_log WHERE action = :a"), {"a": service.SUBMIT_ACTION})
        db.commit()


def as_user(env, key):
    env.actor.id, env.actor.name, env.actor.role = key, key.title(), PEOPLE[key]


def get(env, user, path="", expect=200, **params):
    as_user(env, user)
    response = env.client.get("/api/reports/daily-work" + path, params={k: v for k, v in params.items() if v is not None})
    assert response.status_code == expect, response.text
    return response


def report(env, user="admin", **params):
    return get(env, user, **params).json()


def metric(body, key):
    return next(x for x in body["metrics"] if x["key"] == key)


def values(body, field="value"):
    return {x["key"]: x[field] for x in body["metrics"]}


def person(body, key):
    return next(p for p in body["staff"] if p["id"] == key)


# ------------------------------------------------------------------ definitions

def test_the_counted_outcomes_are_the_ones_the_stage_rules_write():
    """If a stage's wording changes, the report must change with it: every counted outcome is produced by the stage evaluators."""
    samples = {
        1: [{"result": "passed"}, {"result": "failed"}],
        2: [{"forwarded": "yes"}, {}, {"forwarded": "no", "reason": "not_interested"}],
        3: [{"scheduled": "yes"}, {}, {"stop": "not_possible"}],
        4: [{"result": "selected"}, {"result": "rejected"}],
        5: [{"result": "agreed"}, {"result": "declined"}, {"result": "not_received"}],
        10: [{"arrived": "yes"}, {}, {"stop": "visa_rejected"}],
        12: [{"file": {"id": "f"}, "travelDate": "2026-11-05"}, {"stop": "not_travelling"}],
    }
    for key, (number, outcomes) in service.STAGE_RULES.items():
        produced = {stage_by_no(number).evaluate(sample).outcome for sample in samples[number]}
        assert produced, key
        if outcomes:
            assert set(outcomes) <= produced, (key, outcomes, produced)
    assert service.classify("interested", False) == "interested" and service.classify("answered", True) == "callback"
    assert service.classify("invalid", True) == "notReachable" and service.classify(None, False) == "answered"


# ------------------------------------------------------------------ one day

def test_one_day_for_the_team_every_figure_previous_and_series(env):
    body = report(env, **{"from": "2026-11-05", "to": "2026-11-05"})
    assert body["scope"] == "team" and body["period"] == {
        "from": "2026-11-05", "to": "2026-11-05", "days": 1, "previousFrom": "2026-11-04", "previousTo": "2026-11-04", "bucket": "day",
        "partial": True, "previousUntil": "2026-11-04T08:30:00+00:00", "allTime": False}
    assert [x["key"] for x in body["metrics"]] == KEYS
    assert values(body) == {"leadsContacted": 8, "prescreened": 3, "forwarded": 4, "shortlisted": 1, "interviews": 2, "offers": 2,
                            "visas": 1, "travelled": 1}
    # "Today so far" (14:00) is compared with yesterday up to 14:00 only: yesterday's 15:00 / 16:00 scheduling and 23:30 prescreening
    # had not happened yet at that time of the day (see test_a_running_day_is_compared_with_the_same_elapsed_time_of_the_previous_one).
    assert body["period"]["partial"] is True and body["period"]["previousUntil"] == "2026-11-04T08:30:00+00:00"
    assert values(body, "previous") == {"leadsContacted": 1, "prescreened": 0, "forwarded": 0, "shortlisted": 0, "interviews": 0,
                                        "offers": 0, "visas": 0, "travelled": 0}
    assert values(body, "changePct") == {"leadsContacted": 700.0, "prescreened": None, "forwarded": None, "shortlisted": None,
                                         "interviews": None, "offers": None, "visas": None, "travelled": None}
    for item in body["metrics"]:
        assert item["label"] and item["definition"] and item["series"] == [{"date": "2026-11-05", "value": item["value"]}]
    assert body["extra"] == {"interviewsOnline": 1, "leadsAssigned": 9, "leadsContactedPct": 88.9}
    assert body["leadFeedback"] == {
        "contacted": 8, "interested": 2, "resumesReceived": 1, "callbacksDue": 1,
        "byOutcome": [{"key": "interested", "label": "Interested", "count": 2}, {"key": "callback", "label": "Call back", "count": 1},
                      {"key": "noAnswer", "label": "No answer", "count": 2}, {"key": "notInterested", "label": "Not interested", "count": 1},
                      {"key": "wrongNumber", "label": "Wrong number", "count": 1}, {"key": "notReachable", "label": "Not reachable", "count": 1},
                      {"key": "notContacted", "label": "Not contacted in this period", "count": 1}]}
    assert [(x["key"], x["value"]) for x in body["funnel"]] == [
        ("prescreened", 3), ("forwarded", 4), ("shortlisted", 1), ("interviews", 2), ("offers", 2), ("visas", 1), ("travelled", 1)]

    # who: active staff (zed is idle), never admin / manager / an inactive person without activity
    assert [(p["id"], p["total"]) for p in body["staff"]] == [("asha", 12), ("ravi", 7), ("meena", 3), ("zed", 0)]
    assert person(body, "asha")["metrics"] == {"leadsContacted": 6, "prescreened": 3, "forwarded": 2, "shortlisted": 0, "interviews": 1,
                                               "offers": 0, "visas": 0, "travelled": 0}
    assert person(body, "ravi")["metrics"] == {"leadsContacted": 2, "prescreened": 0, "forwarded": 2, "shortlisted": 0, "interviews": 1,
                                               "offers": 2, "visas": 0, "travelled": 0}
    assert person(body, "meena")["metrics"] == {"leadsContacted": 0, "prescreened": 0, "forwarded": 0, "shortlisted": 1, "interviews": 0,
                                                "offers": 0, "visas": 1, "travelled": 1}
    assert person(body, "zed")["idle"] is True and not any(p["idle"] for p in body["staff"] if p["id"] != "zed")
    asha = person(body, "asha")
    assert asha["role"] == "staff" and asha["initials"] == "A" and asha["interviewsOnline"] == 1
    assert asha["leadFeedback"] == {"contacted": 6, "interested": 1, "notInterested": 1, "noAnswer": 1, "wrongNumber": 1, "callback": 1,
                                    "other": 1}
    assert person(body, "ravi")["leadFeedback"] == {"contacted": 2, "interested": 1, "notInterested": 0, "noAnswer": 1, "wrongNumber": 0,
                                                    "callback": 0, "other": 0}
    for p in body["staff"]:   # a person's feedback groups add up to the leads they contacted
        f = p["leadFeedback"]
        assert f["contacted"] == p["metrics"]["leadsContacted"] == sum(v for k, v in f.items() if k != "contacted")
    # the per-staff numbers add up to the team total, metric by metric
    for key in KEYS:
        assert sum(p["metrics"][key] for p in body["staff"]) == metric(body, key)["value"]
    # nobody has closed the day yet
    assert all(p["submitted"] is False and p["submittedAt"] is None and p["note"] is None for p in body["staff"])
    assert body["compliance"]["expected"] == 4 and body["compliance"]["submitted"] == 0
    assert {x["id"] for x in body["compliance"]["missing"]} == {"asha", "ravi", "meena", "zed"}
    assert body["clients"] == [{"id": "company2", "name": "Desert Co"}, {"id": "company", "name": "Gulf Builders"}]
    assert body["generatedAt"] == NOW.isoformat()


def test_the_default_is_today_and_the_clock_decides_the_day(env):
    body = report(env)
    assert body["period"]["from"] == body["period"]["to"] == "2026-11-05"
    env.clock["now"] = ist(THU, 0, 10)   # ten past midnight: still Thursday in agency time (Wednesday in UTC)
    assert report(env)["period"]["from"] == "2026-11-05"
    env.clock["now"] = ist(THU, 23, 50)
    assert report(env)["period"]["from"] == "2026-11-05"


# ------------------------------------------------------------------ a range

def test_a_week_range_previous_series_credit_reopening_and_day_boundaries(env):
    body = report(env, **{"from": "2026-11-02", "to": "2026-11-05"})
    assert body["period"]["previousFrom"] == "2026-10-29" and body["period"]["previousTo"] == "2026-11-01"
    assert values(body) == {"leadsContacted": 8, "prescreened": 4, "forwarded": 4, "shortlisted": 3, "interviews": 2, "offers": 2,
                            "visas": 1, "travelled": 1}
    assert values(body, "previous") == {"leadsContacted": 2, "prescreened": 2, "forwarded": 1, "shortlisted": 0, "interviews": 0,
                                        "offers": 0, "visas": 0, "travelled": 1}
    assert values(body, "changePct") == {"leadsContacted": 300.0, "prescreened": 100.0, "forwarded": 300.0, "shortlisted": None,
                                         "interviews": None, "offers": None, "visas": None, "travelled": 0.0}
    series = {item["key"]: [(x["date"], x["value"]) for x in item["series"]] for item in body["metrics"]}
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    assert [d for d, _ in series["prescreened"]] == days            # one point per day, zeros included
    assert [v for _, v in series["prescreened"]] == [0, 0, 1, 3]    # 23:30 IST Wednesday vs 00:30 IST Thursday
    assert [v for _, v in series["forwarded"]] == [0, 0, 0, 4]      # the reopened Wednesday completion is not counted anywhere
    assert [v for _, v in series["shortlisted"]] == [0, 0, 2, 1]
    assert [v for _, v in series["interviews"]] == [0, 0, 0, 2]
    assert [v for _, v in series["leadsContacted"]] == [0, 0, 1, 8]  # distinct leads per day: asha's L1 is worked on two days
    for key in KEYS[1:]:
        assert sum(v for _, v in series[key]) == metric(body, key)["value"]
    assert [(p["id"], p["total"]) for p in body["staff"]] == [("asha", 14), ("ravi", 8), ("meena", 3), ("zed", 0)]
    assert person(body, "asha")["metrics"]["prescreened"] == 4 and person(body, "meena")["metrics"]["shortlisted"] == 1
    for key in KEYS:
        assert sum(p["metrics"][key] for p in body["staff"]) == metric(body, key)["value"]
    assert body["compliance"] is None and all(p["submitted"] is None for p in body["staff"])   # ranges have no day close


def test_a_long_range_is_drawn_by_week_and_the_limits_are_enforced(env):
    body = report(env, **{"from": "2026-07-08", "to": "2026-11-05"})   # 121 days
    assert body["period"]["days"] == 121 and body["period"]["bucket"] == "week"
    prescreened = metric(body, "prescreened")["series"]
    assert all(x["bucket"] == "week" for x in prescreened)
    starts = [date.fromisoformat(x["date"]) for x in prescreened]
    assert all(d.weekday() == 0 for d in starts) and starts[0] == date(2026, 7, 6) and starts[-1] == date(2026, 11, 2)
    assert all(b - a == timedelta(days=7) for a, b in zip(starts, starts[1:]))
    assert sum(x["value"] for x in prescreened) == metric(body, "prescreened")["value"] == 6   # + c19 and c20 of 10-30 / 10-31
    assert metric(body, "forwarded")["value"] == 6   # + c17 and c18
    assert report(env, **{"from": "2025-11-05", "to": "2026-11-05"})["period"]["days"] == 366   # exactly the maximum
    get(env, "admin", expect=422, **{"from": "2025-11-04", "to": "2026-11-05"})                # 367 days
    get(env, "admin", expect=422, **{"from": "2026-11-06", "to": "2026-11-05"})
    get(env, "admin", expect=422, **{"from": "1999-01-01", "to": "1999-01-02"})
    empty = report(env, **{"from": "2026-12-01", "to": "2026-12-03"})   # nothing happened: zeros everywhere, no division by zero
    assert set(values(empty).values()) == {0} and all(x["changePct"] is None for x in empty["metrics"])
    assert empty["extra"]["leadsContactedPct"] == 0.0 and [p["total"] for p in empty["staff"]] == [0, 0, 0, 0]


# ------------------------------------------------------------------ filters and scope

def test_staff_and_client_filters_and_the_recruiter_sees_only_themselves(env):
    rng = {"from": "2026-11-02", "to": "2026-11-05"}
    one = report(env, staff="ravi", **rng)
    assert [p["id"] for p in one["staff"]] == ["ravi"] and values(one) == person(one, "ravi")["metrics"]
    assert one["scope"] == "team" and one["leadFeedback"]["contacted"] == 2
    assert report(env, staff="all", **rng) == report(env, **rng) | {"generatedAt": NOW.isoformat()}
    get(env, "admin", expect=404, staff="nobody", **rng)
    assert [p["id"] for p in report(env, "manager", staff="asha", **rng)["staff"]] == ["asha"]   # a manager reads the team too

    desert = report(env, client="company2", **rng)   # the second client: c3 forwarded, c5 offer, c9 visa (leads have no client)
    assert values(desert) == {"leadsContacted": 8, "prescreened": 0, "forwarded": 1, "shortlisted": 0, "interviews": 0, "offers": 1,
                              "visas": 1, "travelled": 0}
    assert len(desert["clients"]) == 2   # the filter keeps offering every client
    gulf = report(env, client="company", **rng)
    assert values(gulf)["forwarded"] == 3 and values(gulf)["visas"] == 0 and values(gulf)["offers"] == 1
    assert values(report(env, client="company", staff="asha", **rng))["prescreened"] == 4
    assert values(report(env, client="no-such-client", **rng))["forwarded"] == 0

    mine = report(env, "ravi", **rng)
    assert mine["scope"] == "self" and mine["compliance"] is None and [p["id"] for p in mine["staff"]] == ["ravi"]
    assert values(mine)["forwarded"] == 2 and values(mine)["prescreened"] == 0 and mine["extra"]["leadsAssigned"] == 3
    assert mine["leadFeedback"]["contacted"] == 2 and mine["funnel"][1]["value"] == 2
    assert values(mine, "previous")["forwarded"] == 1   # ravi's c17; asha's and meena's earlier work is not his
    assert report(env, "ravi", staff="ravi", **rng) == mine | {} and report(env, "ravi", staff="all", **rng) == mine
    get(env, "ravi", expect=403, staff="asha", **rng)
    today = report(env, "meena")
    assert today["scope"] == "self" and today["compliance"] is None and values(today)["travelled"] == 1
    assert [p["id"] for p in today["staff"]] == ["meena"] and today["staff"][0]["submitted"] is False
    assert report(env, "zed")["staff"][0]["idle"] is True   # a recruiter with nothing yet still gets their own (empty) row


# ------------------------------------------------------------------ drill-down

def test_drilldown_rows_match_every_number_and_never_carry_id_numbers(env):
    rng = {"from": "2026-11-02", "to": "2026-11-05"}
    overview = report(env, **rng)
    everything = []
    for key in KEYS:
        body = get(env, "admin", "/drilldown", metric=key, page_size=100, **rng).json()
        assert body["metric"] == key and body["label"] == metric(overview, key)["label"]
        assert body["total"] == len(body["rows"]) == metric(overview, key)["value"], key
        assert body["page"] == 0 and body["pageSize"] == 100
        times = [r["at"] for r in body["rows"]]
        assert times == sorted(times, reverse=True)   # newest first
        everything.append(body)
    forwarded = everything[2]["rows"]
    assert {r["candidateId"] for r in forwarded} == {"c1", "c14", "c3", "c13"}   # c13 once (its Thursday completion)
    assert {r["staffName"] for r in forwarded} == {"Asha", "Ravi"} and {r["client"] for r in forwarded} == {"Gulf Builders", "Desert Co"}
    row = next(r for r in forwarded if r["candidateId"] == "c14")
    assert re.fullmatch(r"CI-\d{3}", row["careId"])   # the public Care ID is the one identifier the row carries (contract)
    assert row == {"at": ist(THU, 12, 30).isoformat(), "staffName": "Asha", "kind": "candidate", "candidateId": "c14", "leadId": None,
                   "careId": row["careId"], "name": "Candidate c14", "client": "Gulf Builders", "job": "Welder",
                   "detail": "Forwarded to Gulf Builders"}   # c14 belongs to ravi: the credit is asha's
    details = {r["candidateId"]: r["detail"] for r in everything[4]["rows"]}
    assert details == {"c1": "Interview result: Selected (online)", "c8": "Interview result: Rejected (in person)"}
    assert {r["detail"] for r in everything[5]["rows"]} == {"Offer letter received: Agreed", "Offer letter received: Declined"}
    assert everything[1]["rows"][0]["detail"].startswith("Prescreening: ") and everything[7]["rows"][0]["detail"] == "Travelled (travel date 2026-11-20)"
    leads = everything[0]["rows"]
    assert {(r["leadId"], r["staffName"]) for r in leads} == {(f"L{n}", "Asha") for n in range(1, 7)} | {("L7", "Ravi"), ("L8", "Ravi")}
    assert all(r["kind"] == "lead" and r["candidateId"] is None and r["client"] is None and r["job"] == "Chef" for r in leads)
    by_lead = {r["leadId"]: r["detail"] for r in leads}
    assert by_lead["L3"] == "Outcome: Wrong number" and by_lead["L1"] == "Outcome: Interested" and by_lead["L6"] == "Outcome: Call back"
    assert by_lead["L7"] == "Outcome: No answer" and by_lead["L5"] == "Outcome: Not reachable"
    # nothing sensitive: no phone, passport or Traveller ID anywhere in any row
    blob = json.dumps(everything)
    assert PASSPORT not in blob and TRAVELLER not in blob and not [p for p in PHONES if p in blob] and "+9180000000" not in blob

    # one person, one client, paging
    asha = get(env, "admin", "/drilldown", metric="forwarded", staff="asha", **rng).json()
    assert asha["total"] == 2 == person(report(env, staff="asha", **rng), "asha")["metrics"]["forwarded"]
    desert = get(env, "admin", "/drilldown", metric="forwarded", client="company2", **rng).json()
    assert desert["total"] == 1 and desert["rows"][0]["candidateId"] == "c3"
    pages = [get(env, "admin", "/drilldown", metric="forwarded", page=n, page_size=3, **rng).json() for n in (0, 1, 2)]
    assert [(p["total"], len(p["rows"])) for p in pages] == [(4, 3), (4, 1), (4, 0)]   # a page past the end still has the total
    assert [r["candidateId"] for p in pages for r in p["rows"]] == [r["candidateId"] for r in forwarded]
    assert get(env, "admin", "/drilldown", metric="leadsContacted", page=1, page_size=5, **rng).json()["total"] == 8

    # permissions and validation
    ravi = get(env, "ravi", "/drilldown", metric="forwarded", **rng).json()
    assert ravi["total"] == 2 and {r["staffName"] for r in ravi["rows"]} == {"Ravi"}
    assert get(env, "ravi", "/drilldown", metric="leadsContacted", **rng).json()["total"] == 2
    get(env, "ravi", "/drilldown", expect=403, metric="forwarded", staff="asha", **rng)
    get(env, "admin", "/drilldown", expect=422, metric="nonsense", **rng)
    get(env, "admin", "/drilldown", expect=422, metric="forwarded", page_size=101, **rng)
    get(env, "admin", "/drilldown", expect=422, metric="forwarded", page=-1, **rng)


# ------------------------------------------------------------------ the day close

def submit(env, user, day, note=None, **extra):
    as_user(env, user)
    return env.client.post("/api/reports/daily-work/submit", json={"date": day, "note": note, **extra})


def test_submitting_the_day_is_idempotent_limited_to_today_or_yesterday_and_only_for_oneself(env):
    first = submit(env, "asha", "2026-11-05", "All forwarded, two to call back.")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["date"] == "2026-11-05" and body["submittedAt"] == NOW.isoformat() and body["note"] == "All forwarded, two to call back."
    assert body["figures"] == {"leadsContacted": 6, "prescreened": 3, "forwarded": 2, "shortlisted": 0, "interviews": 1, "offers": 0,
                               "visas": 0, "travelled": 0, "interviewsOnline": 1}
    with env.world.sessions() as db:
        entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "Daily report submitted")).one()
        assert (entry.actor_id, entry.entity_type, entry.entity_id) == ("asha", "user", "asha")
        assert entry.meta["date"] == "2026-11-05" and entry.meta["note"] == body["note"] and entry.meta["figures"] == body["figures"]

    env.clock["now"] = NOW + timedelta(hours=2)   # a second submit later: the note and figures update, the first time stays
    again = submit(env, "asha", "2026-11-05", "Updated note").json()
    assert again["submittedAt"] == NOW.isoformat() and again["note"] == "Updated note"
    with env.world.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Daily report submitted")) == 1
        assert db.scalars(select(AuditEntry).where(AuditEntry.action == "Daily report submitted")).one().meta["note"] == "Updated note"
    assert submit(env, "asha", "2026-11-05", None).json()["note"] is None   # a note can be cleared

    # management sees who has closed the day, and who has not
    env.clock["now"] = NOW
    team = report(env, **{"from": "2026-11-05", "to": "2026-11-05"})
    asha = person(team, "asha")
    assert asha["submitted"] is True and asha["submittedAt"] == NOW.isoformat() and asha["note"] is None
    assert all(p["submitted"] is False for p in team["staff"] if p["id"] != "asha")
    assert team["compliance"]["expected"] == 4 and team["compliance"]["submitted"] == 1
    assert {x["id"] for x in team["compliance"]["missing"]} == {"ravi", "meena", "zed"} and "gone" not in json.dumps(team["compliance"])
    assert report(env, "asha")["staff"][0]["submitted"] is True and report(env, "ravi")["staff"][0]["submitted"] is False
    mine = get(env, "asha", "/mine").json()
    assert mine["date"] == "2026-11-05" and mine["submitted"] is True and mine["metrics"]["forwarded"] == 2 and mine["total"] == 12
    assert get(env, "ravi", "/mine", date="2026-11-05").json()["submitted"] is False
    wed = get(env, "asha", "/mine", date="2026-11-04").json()
    assert wed["submitted"] is False and wed["metrics"]["prescreened"] == 1 and wed["metrics"]["shortlisted"] == 1
    assert wed["metrics"]["leadsContacted"] == 1

    # yesterday is allowed, the day before and tomorrow are not
    yesterday = submit(env, "ravi", "2026-11-04", "Late close")
    assert yesterday.status_code == 200 and yesterday.json()["figures"]["shortlisted"] == 1 and yesterday.json()["figures"]["forwarded"] == 0
    assert report(env, **{"from": "2026-11-04", "to": "2026-11-04"})["compliance"]["submitted"] == 1
    assert submit(env, "ravi", "2026-11-03").status_code == 422
    assert submit(env, "ravi", "2026-11-06").status_code == 422
    env.clock["now"] = ist(THU, 0, 20)   # just after midnight Thursday IST: today is still the 5th (the UTC date is the 4th)
    assert submit(env, "meena", "2026-11-05").status_code == 200
    assert submit(env, "meena", "2026-11-03").status_code == 422 and submit(env, "meena", "2026-11-04").status_code == 200

    # note length and who it is for: the body names no person, so nobody can submit for somebody else
    assert submit(env, "ravi", "2026-11-05", "x" * 501).status_code == 422
    assert submit(env, "ravi", "2026-11-05", "x" * 500).status_code == 200
    assert submit(env, "ravi", "2026-11-05", "hi", staff="asha").status_code == 422
    assert submit(env, "ravi", "2026-11-05", "hi", userId="asha").status_code == 422
    assert submit(env, "ravi", "not-a-date").status_code == 422
    with env.world.sessions() as db:
        assert {e.entity_id for e in db.scalars(select(AuditEntry).where(AuditEntry.action == "Daily report submitted"))} == {"asha", "ravi", "meena"}
    # an admin or manager closes their own day too (they are not expected to: compliance lists recruiters only)
    assert submit(env, "manager", "2026-11-05").status_code == 200
    assert report(env, **{"from": "2026-11-05", "to": "2026-11-05"})["compliance"]["expected"] == 4


# ------------------------------------------------------------------ export

def sheets_of(payload):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(BytesIO(payload)) as archive:
        names = [s.get("name") for s in ET.fromstring(archive.read("xl/workbook.xml")).iter(ns + "sheet")]
        out = {}
        for number, name in enumerate(names, 1):
            rows = []
            for row in ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml")).iter(ns + "row"):
                cells = []
                for c in row.findall(ns + "c"):
                    if c.get("t") == "n":
                        number_ = float(c.find(ns + "v").text)
                        cells.append(int(number_) if number_.is_integer() else number_)
                    else:
                        cells.append("".join(t.text or "" for t in c.iter(ns + "t")))
                rows.append(cells)
            out[name] = rows
        raw = b"".join(archive.read(n) for n in archive.namelist())
    return out, raw


def test_the_export_has_four_sheets_matching_the_screen_and_no_id_numbers(env):
    rng = {"from": "2026-11-02", "to": "2026-11-05"}
    response = get(env, "admin", "/export", **rng)
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert 'filename="daily-work-report_2026-11-02_2026-11-05.xlsx"' in response.headers["content-disposition"]
    book, raw = sheets_of(response.content)
    assert list(book) == ["Summary", "By recruiter", "By day", "Lead feedback", "Leads & tasks", "Stages now", "By client", "Submitted reports"]
    overview = report(env, **rng)
    summary = book["Summary"]
    assert summary[0] == ["Metric", "Definition", "This period", "Previous period", "Change (%)"]
    assert [(r[0], r[2], r[3]) for r in summary[1:9]] == [(x["label"], x["value"], x["previous"]) for x in overview["metrics"]]
    assert [r[4] for r in summary[1:9]] == [x["changePct"] if x["changePct"] is not None else "" for x in overview["metrics"]]
    by_staff = book["By recruiter"]
    assert by_staff[0][:2] == ["Recruiter", "Role"] and by_staff[0][2:10] == [x["label"] for x in overview["metrics"]]
    assert [r[0] for r in by_staff[1:]] == ["Asha", "Ravi", "Meena", "Zed"]
    for column, key in enumerate(KEYS, 2):   # the staff rows add up to the Summary totals
        assert sum(r[column] for r in by_staff[1:]) == metric(overview, key)["value"]
    assert [r[10] for r in by_staff[1:]] == [14, 8, 3, 0] and "Day closed" not in by_staff[0]
    by_day = book["By day"]
    assert [r[0] for r in by_day[1:]] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"] and by_day[0][0] == "Date"
    for column, key in enumerate(KEYS, 1):
        assert [r[column] for r in by_day[1:]] == [x["value"] for x in metric(overview, key)["series"]]
    feedback = {r[0]: r[1] for r in book["Lead feedback"] if len(r) == 2}
    assert feedback["Interested"] == 2 and feedback["Not contacted in this period"] == 1 and feedback["Leads contacted"] == 8
    assert feedback["Resumes received"] == 1 and feedback["Call backs due"] == 1 and feedback["Leads assigned and open"] == 9
    text_ = raw.decode("utf-8", "ignore")
    assert PASSPORT not in text_ and TRAVELLER not in text_ and not [p for p in PHONES if p in text_] and "+9180000000" not in text_

    one_day = sheets_of(get(env, "admin", "/export", **{"from": "2026-11-05", "to": "2026-11-05"}).content)[0]
    assert one_day["By recruiter"][0][-3:] == ["Day closed", "Submitted at", "Note"] and one_day["By recruiter"][1][-3] == "No"
    only = sheets_of(get(env, "ravi", "/export", **rng).content)[0]
    assert [r[0] for r in only["By recruiter"][1:]] == ["Ravi"] and only["Summary"][1][2] == 2
    get(env, "ravi", "/export", expect=403, staff="asha", **rng)
    assert get(env, "admin", "/export", client="company2", **rng).status_code == 200
    long = sheets_of(get(env, "admin", "/export", **{"from": "2026-07-08", "to": "2026-11-05"}).content)[0]
    assert long["By day"][0][0] == "Week starting" and long["By day"][1][0] == "2026-07-06"


# ------------------------------------------------------------------ statement budgets

def test_statement_budgets_with_the_real_sign_in_check(env):
    """GET report <= 10 statements including the sign-in lookup, drill-down <= 4, submit <= 5. The counts do not grow with the
    number of people, days or rows (one grouped statement per family)."""
    with env.world.sessions() as db:
        db.add(SessionRow(id="budget-admin", user_id="admin", expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc)))
        db.add(SessionRow(id="budget-asha", user_id="asha", expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    env.app.dependency_overrides.pop(deps.current_user)   # the real current_user: one lookup per first request of a session
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    def counted(method, path, session_id, **kwargs):
        env.client.cookies.clear()
        env.client.cookies.set(SESSION_COOKIE, session_id)
        seen.clear()
        event.listen(env.world.engine, "before_cursor_execute", record)
        try:
            response = getattr(env.client, method)("/api/reports/daily-work" + path, **kwargs)
        finally:
            event.remove(env.world.engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return response, len(seen)

    try:
        deps._AUTH_CACHE.clear()
        _, one_day = counted("get", "", "budget-admin", params={"from": "2026-11-05", "to": "2026-11-05"})
        assert one_day <= 10, seen
        deps._AUTH_CACHE.clear()
        _, week = counted("get", "", "budget-admin", params={"from": "2026-11-02", "to": "2026-11-05"})
        deps._AUTH_CACHE.clear()
        _, long_range = counted("get", "", "budget-admin", params={"from": "2025-11-05", "to": "2026-11-05"})
        assert long_range <= 10 and long_range == week == one_day - 1, seen   # more days and rows cost no more (a range skips the day-close read)
        deps._AUTH_CACHE.clear()
        _, recruiter = counted("get", "", "budget-asha", params={"from": "2026-11-05", "to": "2026-11-05"})
        assert recruiter <= 10, seen
        deps._AUTH_CACHE.clear()
        _, stage_drill = counted("get", "/drilldown", "budget-admin", params={"metric": "forwarded", "from": "2026-11-02", "to": "2026-11-05"})
        assert stage_drill <= 4, seen
        deps._AUTH_CACHE.clear()
        _, lead_drill = counted("get", "/drilldown", "budget-admin", params={"metric": "leadsContacted", "from": "2026-11-02", "to": "2026-11-05"})
        assert lead_drill <= 4, seen
        deps._AUTH_CACHE.clear()
        _, mine = counted("get", "/mine", "budget-asha")
        assert mine <= 8, seen
        deps._AUTH_CACHE.clear()
        _, first_submit = counted("post", "/submit", "budget-asha", json={"date": "2026-11-05", "note": "budget"})
        assert first_submit <= 5, seen
        deps._AUTH_CACHE.clear()
        _, repeat_submit = counted("post", "/submit", "budget-asha", json={"date": "2026-11-05", "note": "again"})
        assert repeat_submit <= 5, seen
        deps._AUTH_CACHE.clear()
        _, export = counted("get", "/export", "budget-admin", params={"from": "2026-11-05", "to": "2026-11-05"})
        assert export <= 10, seen
        deps._AUTH_CACHE.clear()
        _, tasks_drill = counted("get", "/drilldown", "budget-admin", params={"metric": "tasksOverdue", "from": "2026-11-05", "to": "2026-11-05"})
        deps._AUTH_CACHE.clear()
        _, stage_drill_now = counted("get", "/drilldown", "budget-admin", params={"metric": "stage:3"})
        deps._AUTH_CACHE.clear()
        _, assigned_drill = counted("get", "/drilldown", "budget-admin", params={"metric": "leadsAssigned"})
        assert max(tasks_drill, stage_drill_now, assigned_drill) <= 4, seen
        deps._AUTH_CACHE.clear()
        _, all_time = counted("get", "", "budget-admin", params={"period": "all"})
        deps._AUTH_CACHE.clear()
        _, all_recruiter = counted("get", "", "budget-asha", params={"period": "all"})
        assert max(all_time, all_recruiter) <= 10, seen
        deps._AUTH_CACHE.clear()
        _, history_count = counted("get", "/history", "budget-admin")
        assert history_count <= 4, seen
        deps._AUTH_CACHE.clear()
        _, history_recruiter = counted("get", "/history", "budget-asha")
        assert history_recruiter <= 4, seen
        print("statements:", dict(report_one_day=one_day, report_year=long_range, recruiter=recruiter, drill_stage=stage_drill,
                                  drill_leads=lead_drill, mine=mine, submit=first_submit, submit_again=repeat_submit, export=export))
    finally:
        env.client.cookies.clear()
        deps._AUTH_CACHE.clear()
        with env.world.sessions() as db:
            db.execute(text("DELETE FROM auth_sessions WHERE id IN ('budget-admin', 'budget-asha')"))
            db.commit()


# ------------------------------------------------------------------ the management table: leads, tasks, pipeline now, partial periods

def test_the_management_table_leads_tasks_and_the_pipeline_right_now(env):
    day = {"from": "2026-11-05", "to": "2026-11-05"}
    body = report(env, **day)
    sheet = body["leadSheet"]
    assert sheet["totalLeads"] == 11 and sheet["assigned"] == 10 and sheet["unassigned"] == 1
    assert sheet["contacted"] == 8 and sheet["notContacted"] == 2   # ravi's L9 (only a note) and L11 (converted) were not contacted
    assert [(x["key"], x["count"]) for x in sheet["byStatus"]] == [
        ("new", 4), ("contacted", 1), ("interested", 2), ("resume_requested", 0), ("resume_received", 0), ("converted", 1),
        ("not_interested", 1), ("invalid", 1), ("wrong_number", 1)]
    assert [x["label"] for x in sheet["byStatus"]][:3] == ["Not contacted", "Contacted", "Interested"]
    assert person(body, "asha")["leads"] == {"assigned": 6, "contacted": 6, "interested": 1, "notInterested": 1, "noAnswer": 1,
                                             "wrongNumber": 1, "callback": 1, "notContacted": 0, "assignedInPeriod": 2,
                                             "notContactedInPeriod": 1, "backlog": 1, "neverContacted": 1}
    assert person(body, "ravi")["leads"] == {"assigned": 4, "contacted": 2, "interested": 1, "notInterested": 0, "noAnswer": 1,
                                             "wrongNumber": 0, "callback": 0, "notContacted": 2, "assignedInPeriod": 2,
                                             "notContactedInPeriod": 1, "backlog": 2, "neverContacted": 2}
    assert person(body, "meena")["leads"]["assigned"] == 0 and person(body, "zed")["leads"]["notContacted"] == 0
    assert sheet["assigned"] == sum(p["leads"]["assigned"] for p in body["staff"])

    assert body["tasks"] == {"total": 7, "done": 3, "pending": 4, "overdue": 3}
    assert person(body, "asha")["tasks"] == {"done": 2, "pending": 2, "overdue": 1}   # a cancelled task is nothing; one done on Wednesday is not today's
    assert person(body, "ravi")["tasks"] == {"done": 1, "pending": 2, "overdue": 2}
    assert person(body, "meena")["tasks"] == {"done": 0, "pending": 0, "overdue": 0}
    week = report(env, **{"from": "2026-11-02", "to": "2026-11-05"})
    assert week["tasks"] == {"total": 8, "done": 4, "pending": 4, "overdue": 3} and person(week, "asha")["tasks"]["done"] == 3

    stages = body["stages"]
    assert [(x["no"], x["count"]) for x in stages["totals"]] == [
        (0, 8), (1, 0), (2, 4), (3, 6), (4, 1), (5, 2), (6, 2), (7, 0), (8, 0), (9, 0), (10, 0), (11, 1), (12, 0)]
    assert stages["totals"][0] == {"no": 0, "key": "unmapped", "label": "Not on an order yet", "count": 8}
    assert stages["totals"][2]["key"] == "forwarded" and stages["totals"][3]["label"] == "Interview scheduling"
    by_id = {x["id"]: x for x in stages["perStaff"]}
    assert [x["id"] for x in stages["perStaff"]] == [p["id"] for p in body["staff"]]   # same order as the staff rows
    assert by_id["asha"]["counts"] == {str(n): {0: 3, 2: 3, 5: 1}.get(n, 0) for n in range(13)} and by_id["asha"]["total"] == 7
    assert by_id["ravi"]["counts"]["3"] == 5 and by_id["ravi"]["counts"]["6"] == 2 and by_id["ravi"]["counts"]["0"] == 2
    assert by_id["meena"]["counts"]["0"] == 3 and by_id["meena"]["counts"]["11"] == 1 and by_id["zed"]["total"] == 0   # deployed / closed are nowhere
    for no in range(13):
        assert sum(x["counts"][str(no)] for x in stages["perStaff"]) == stages["totals"][no]["count"]
    # the pipeline is "now": it does not change with the chosen period
    other = report(env, **{"from": "2026-10-01", "to": "2026-10-02"})["stages"]
    assert other["totals"] == stages["totals"] and {x["id"]: x for x in other["perStaff"]} == by_id

    mine = report(env, "ravi", **day)   # a recruiter: only their own numbers, in the same shapes
    assert mine["tasks"] == {"total": 3, "done": 1, "pending": 2, "overdue": 2}
    assert mine["leadSheet"]["totalLeads"] == 4 and mine["leadSheet"]["unassigned"] == 0 and mine["leadSheet"]["assigned"] == 4
    assert [x["id"] for x in mine["stages"]["perStaff"]] == ["ravi"] and mine["stages"]["totals"][3]["count"] == 5
    one = report(env, staff="meena", **day)
    assert one["tasks"]["total"] == 0 and one["stages"]["totals"][11]["count"] == 1 and one["leadSheet"]["totalLeads"] == 0
    # /mine carries the staff row only (no extra statements for the pipeline)
    assert "tasks" not in get(env, "ravi", "/mine").json()


def test_a_running_day_is_compared_with_the_same_elapsed_time_of_the_previous_one(env):
    day = {"from": "2026-11-05", "to": "2026-11-05"}
    early = report(env, **day)                       # 14:00 Thursday
    assert early["period"]["partial"] is True and early["period"]["previousUntil"] == ist(WED, 14).isoformat()
    assert values(early, "previous")["prescreened"] == 0 and values(early, "previous")["shortlisted"] == 0
    env.clock["now"] = ist(THU, 23, 50)              # the day is almost over: Wednesday's 23:30 prescreening now counts as "so far"
    late = report(env, **day)
    assert late["period"]["previousUntil"] == ist(WED, 23, 50).isoformat()
    assert values(late, "previous")["prescreened"] == 1 and values(late, "previous")["shortlisted"] == 2   # 15:00 and 16:00
    assert values(late, "changePct")["prescreened"] == 200.0 and values(late, "changePct")["shortlisted"] == -50.0
    assert metric(late, "prescreened")["series"] == [{"date": "2026-11-05", "value": 3}]   # the series is not touched
    env.clock["now"] = ist(THU, 0, 5)                # just after midnight: yesterday up to 00:05 only
    assert report(env, **day)["period"]["previousUntil"] == ist(WED, 0, 5).isoformat()
    env.clock["now"] = NOW
    past = report(env, **{"from": "2026-11-04", "to": "2026-11-04"})   # a finished day: the whole day before, not partial
    assert past["period"]["partial"] is False and past["period"]["previousUntil"] == ist(WED).isoformat()
    assert values(past, "previous") == {key: 0 for key in KEYS}
    week = report(env, **{"from": "2026-11-02", "to": "2026-11-05"})   # week to date: previous period up to the same elapsed time
    assert week["period"]["partial"] is True and week["period"]["previousUntil"] == ist(date(2026, 11, 1), 14).isoformat()
    assert values(week, "previous")["travelled"] == 1   # meena's 12:00 on 11-01 is inside "so far", and the change is computed on it
    env.clock["now"] = ist(date(2026, 12, 1), 10)       # a range wholly in the past is never partial
    assert report(env, **{"from": "2026-11-02", "to": "2026-11-05"})["period"]["partial"] is False
    env.clock["now"] = ist(date(2026, 10, 1), 10)       # nor one that has not started yet
    assert report(env, **{"from": "2026-11-02", "to": "2026-11-05"})["period"]["partial"] is False


def test_drilldown_of_the_new_numbers_matches_each_total(env):
    day = {"from": "2026-11-05", "to": "2026-11-05"}
    overview = report(env, **day)

    def drill(user="admin", **params):
        return get(env, user, "/drilldown", page_size=100, **params).json()

    leads = drill(metric="leadsAssigned")
    assert leads["total"] == len(leads["rows"]) == overview["leadSheet"]["assigned"] == 10 and leads["label"] == "Leads assigned"
    assert all(r["kind"] == "lead" and r["detail"].startswith("Status: ") for r in leads["rows"])
    assert {r["staffName"] for r in leads["rows"]} == {"Asha", "Ravi"}
    assert drill(metric="leadsAssigned", staff="ravi")["total"] == 4
    for key, field in (("tasksDone", "done"), ("tasksPending", "pending"), ("tasksOverdue", "overdue")):
        body = drill(metric=key, **day)
        assert body["total"] == len(body["rows"]) == overview["tasks"][field], key
        assert all(r["kind"] == "candidate" and r["candidateId"] and r["detail"].split(": ")[0].startswith("Task") for r in body["rows"])
        assert drill(metric=key, staff="asha", **day)["total"] == person(overview, "asha")["tasks"][field]
    overdue = drill(metric="tasksOverdue", **day)
    assert {r["detail"] for r in overdue["rows"]} == {"Task overdue: Forward CV to client"} and overdue["label"] == "Tasks overdue"
    assert drill(metric="tasksDone", **{"from": "2026-11-02", "to": "2026-11-05"})["total"] == 4
    for no in range(13):
        body = drill(metric=f"stage:{no}")
        assert body["total"] == len(body["rows"]) == overview["stages"]["totals"][no]["count"], no
    third = drill(metric="stage:3")
    assert third["label"] == "Interview scheduling" and {r["candidateId"] for r in third["rows"]} == {"c3", "c13", "c14", "c17", "c18", "c22"}
    assert all(r["detail"] == "At stage 3: Interview scheduling" and r["kind"] == "candidate" for r in third["rows"])
    assert {r["staffName"] for r in third["rows"]} == {"Ravi", "Meena"}
    assert drill(metric="stage:3", staff="ravi")["total"] == 5
    unmapped = drill(metric="stage:0")
    assert unmapped["label"] == "Not on an order yet" and unmapped["total"] == 8
    assert {r["candidateId"] for r in unmapped["rows"]} == {"c2", "c4", "c7", "c10", "c12", "u1", "u2", "u4"}   # u3 declined: not workable
    assert all(r["detail"] == "Not on an order yet" and r["client"] is None for r in unmapped["rows"])
    assert {r["candidateId"] for r in drill(metric="stage:0", staff="asha")["rows"]} == {"c2", "u1", "u2"}
    assert drill(user="ravi", metric="stage:3")["total"] == 5 and drill(user="ravi", metric="tasksPending")["total"] == 2
    assert drill(user="ravi", metric="leadsAssigned")["total"] == 4
    get(env, "ravi", "/drilldown", expect=403, metric="stage:3", staff="asha")
    pages = [get(env, "admin", "/drilldown", metric="stage:3", page=n, page_size=4).json() for n in (0, 1, 2)]
    assert [(p["total"], len(p["rows"])) for p in pages] == [(6, 4), (6, 2), (6, 0)]
    for bad in ("stage:13", "stage:x", "stage:", "tasks", "stage:-1"):
        get(env, "admin", "/drilldown", expect=422, metric=bad)
    blob = json.dumps([leads, overdue, third, unmapped])
    assert PASSPORT not in blob and TRAVELLER not in blob and not [p for p in PHONES if p in blob] and "+9180000000" not in blob


def test_the_export_has_the_leads_and_tasks_and_stages_now_sheets(env):
    body = report(env, **{"from": "2026-11-05", "to": "2026-11-05"})
    book, raw = sheets_of(get(env, "admin", "/export", **{"from": "2026-11-05", "to": "2026-11-05"}).content)
    table = book["Leads & tasks"]
    assert table[0] == ["Recruiter", "Leads assigned", "Leads contacted", "Interested", "Not interested", "No answer", "Wrong number",
                        "Call back", "Not contacted", "Assigned in period", "Not contacted in period", "Backlog (never contacted)",
                        "Tasks done", "Tasks pending", "Tasks overdue"]
    names = [r[0] for r in table[1:6]]
    assert names == ["Asha", "Ravi", "Meena", "Zed", "Total"]
    assert table[1][1:] == [6, 6, 1, 1, 1, 1, 1, 0, 2, 1, 1, 2, 2, 1] and table[5][1:] == [10, 8, 2, 1, 2, 1, 1, 2, 4, 2, 3, 3, 4, 3]
    assert ["New leads in period", 2] in table and ["Assigned in period", 4] in table and ["Not contacted in period", 2] in table
    assert ["Backlog (assigned, never contacted)", 3] in table
    assert ["Unassigned", 1] in table and ["Status: Interested", 2] in table and ["All leads on the sheet", 11] in table
    stages = book["Stages now"]
    assert stages[0][0] == "Recruiter" and stages[0][1] == "Not on an order yet" and stages[0][-1] == "Total" and len(stages[0]) == 15
    assert [r[0] for r in stages[1:]] == ["Asha", "Ravi", "Meena", "Zed", "Total"]
    assert stages[-1][1:] == [x["count"] for x in body["stages"]["totals"]] + [sum(x["count"] for x in body["stages"]["totals"])]
    assert stages[1][1:] == [3, 0, 3, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 7]
    assert PASSPORT not in raw.decode("utf-8", "ignore")


# ------------------------------------------------------------------ by client, submitted figures, history

CLIENT_KEYS = ["prescreened", "forwarded", "shortlisted", "interviews", "offers", "visas", "travelled"]
SNAPSHOT = ["forwarded", "prescreened", "shortlisted", "interviews", "interviewsOnline", "offers", "visas", "travelled", "leadsContacted"]


def test_by_client_groups_the_same_stage_rows_per_employer(env, monkeypatch):
    day = {"from": "2026-11-05", "to": "2026-11-05"}
    body = report(env, **day)
    gulf = {"id": "company", "name": "Gulf Builders", "interviewsOnline": 1, "total": 10,
            "metrics": {"prescreened": 3, "forwarded": 3, "shortlisted": 1, "interviews": 2, "offers": 1, "visas": 0, "travelled": 1}}
    desert = {"id": "company2", "name": "Desert Co", "interviewsOnline": 0, "total": 3,
              "metrics": {"prescreened": 0, "forwarded": 1, "shortlisted": 0, "interviews": 0, "offers": 1, "visas": 1, "travelled": 0}}
    assert body["byClient"] == [gulf, desert]   # busiest first; the lead figure has no client, so it is not in the rows
    for key in CLIENT_KEYS:   # the rows add up to the team figures (leads aside)
        assert sum(c["metrics"][key] for c in body["byClient"]) == metric(body, key)["value"]
    week = report(env, **{"from": "2026-11-02", "to": "2026-11-05"})["byClient"]
    assert [(c["name"], c["total"]) for c in week] == [("Gulf Builders", 13), ("Desert Co", 3)]
    # the staff filter and a recruiter see only their own work per client
    ravi = [{"id": "company2", "name": "Desert Co", "interviewsOnline": 0, "total": 2,   # 2 and 2: the tie goes to the name
             "metrics": {"prescreened": 0, "forwarded": 1, "shortlisted": 0, "interviews": 0, "offers": 1, "visas": 0, "travelled": 0}},
            {"id": "company", "name": "Gulf Builders", "interviewsOnline": 0, "total": 2,
             "metrics": {"prescreened": 0, "forwarded": 1, "shortlisted": 0, "interviews": 1, "offers": 1, "visas": 0, "travelled": 0}}]
    assert report(env, staff="ravi", **day)["byClient"] == ravi and report(env, "ravi", **day)["byClient"] == ravi
    assert [c["id"] for c in report(env, "meena", **day)["byClient"]] == ["company", "company2"]   # c23 / c11 on Gulf, c9 on Desert
    assert [c["id"] for c in report(env, client="company2", **day)["byClient"]] == ["company2"]
    assert report(env, **{"from": "2026-12-01", "to": "2026-12-03"})["byClient"] == []
    # more clients than the limit: the rest become one "Other clients" row
    monkeypatch.setattr(service, "BY_CLIENT_MAX", 1)
    rolled = report(env, **day)["byClient"]
    assert rolled[0] == gulf and rolled[1] == {"id": "other", "name": "Other clients", "interviewsOnline": 0, "total": 3,
                                              "metrics": desert["metrics"]}


def test_the_submitted_snapshot_is_shown_and_flags_later_changes(env):
    day = {"from": "2026-11-05", "to": "2026-11-05"}
    assert all(p["submittedFigures"] is None and p["changedSinceSubmit"] is False for p in report(env, **day)["staff"])
    assert submit(env, "asha", "2026-11-05", "Done").status_code == 200
    body = report(env, **day)
    asha = person(body, "asha")
    assert asha["submittedFigures"] == {"forwarded": 2, "prescreened": 3, "shortlisted": 0, "interviews": 1, "interviewsOnline": 1,
                                        "offers": 0, "visas": 0, "travelled": 0, "leadsContacted": 6}
    assert list(asha["submittedFigures"]) == SNAPSHOT and asha["changedSinceSubmit"] is False
    assert all(p["submittedFigures"] is None and p["changedSinceSubmit"] is False for p in body["staff"] if p["id"] != "asha")
    assert report(env, "asha", **day)["staff"][0]["submittedFigures"] == asha["submittedFigures"]
    week = report(env, **{"from": "2026-11-02", "to": "2026-11-05"})   # ranges carry no snapshot
    assert all(p["submittedFigures"] is None and p["changedSinceSubmit"] is False for p in week["staff"])
    assert get(env, "asha", "/mine").json()["submittedFigures"] == asha["submittedFigures"]
    with env.world.sessions() as db:   # asha works one more lead after closing the day
        extra = LeadActivity(lead_id="L10", user_id="asha", kind="call", summary="call", meta={"outcome": "answered"},
                             created_at=ist(THU, 13, 30))
        db.add(extra)
        db.commit()
        extra_id = extra.id
    try:
        changed = person(report(env, **day), "asha")
        assert changed["metrics"]["leadsContacted"] == 7 and changed["submittedFigures"]["leadsContacted"] == 6
        assert changed["changedSinceSubmit"] is True
        assert submit(env, "asha", "2026-11-05", "Done, one more").status_code == 200   # handing it in again takes a new snapshot
        again = person(report(env, **day), "asha")
        assert again["submittedFigures"]["leadsContacted"] == 7 and again["changedSinceSubmit"] is False
    finally:
        with env.world.sessions() as db:
            db.execute(text("DELETE FROM crm_lead_activities WHERE id = :i"), {"i": extra_id})
            db.commit()


def test_the_history_of_submitted_reports_and_its_export_sheets(env):
    def at(hours):
        env.clock["now"] = NOW + timedelta(hours=hours)

    assert get(env, "admin", "/history").json() == {"total": 0, "page": 0, "pageSize": 25, "rows": []}
    assert submit(env, "asha", "2026-11-05", "First note").status_code == 200
    assert submit(env, "ravi", "2026-11-04", "Late close").status_code == 200
    at(1)
    assert submit(env, "meena", "2026-11-05", None).status_code == 200
    at(2)
    assert submit(env, "asha", "2026-11-05", "Final note").status_code == 200   # the same (person, day): one row, the latest snapshot
    env.clock["now"] = NOW

    body = get(env, "admin", "/history").json()
    assert body["total"] == 3 and body["page"] == 0 and body["pageSize"] == 25
    assert [(r["date"], r["staffId"]) for r in body["rows"]] == [("2026-11-05", "meena"), ("2026-11-05", "asha"), ("2026-11-04", "ravi")]
    asha = body["rows"][1]
    assert asha == {"date": "2026-11-05", "staffId": "asha", "staffName": "Asha", "submittedAt": NOW.isoformat(), "note": "Final note",
                    "figures": {"forwarded": 2, "prescreened": 3, "shortlisted": 0, "interviews": 1, "interviewsOnline": 1, "offers": 0,
                                "visas": 0, "travelled": 0, "leadsContacted": 6}}
    assert list(asha["figures"]) == SNAPSHOT
    assert body["rows"][0]["submittedAt"] == (NOW + timedelta(hours=1)).isoformat() and body["rows"][0]["note"] is None
    assert body["rows"][2]["figures"]["shortlisted"] == 1 and body["rows"][2]["note"] == "Late close"
    assert get(env, "manager", "/history").json() == body   # supervisors read everybody
    assert [r["staffId"] for r in get(env, "admin", "/history", staff="ravi").json()["rows"]] == ["ravi"]
    assert [r["date"] for r in get(env, "admin", "/history", **{"from": "2026-11-04", "to": "2026-11-04"}).json()["rows"]] == ["2026-11-04"]
    assert get(env, "admin", "/history", **{"from": "2026-11-06", "to": "2026-11-07"}).json()["total"] == 0
    pages = [get(env, "admin", "/history", page=n, page_size=2).json() for n in (0, 1, 2)]
    assert [(p["total"], len(p["rows"])) for p in pages] == [(3, 2), (3, 1), (3, 0)]
    # a recruiter reads only their own closes
    mine = get(env, "asha", "/history").json()
    assert mine["total"] == 1 and mine["rows"][0]["staffId"] == "asha"
    assert get(env, "asha", "/history", staff="asha").json() == mine
    get(env, "asha", "/history", expect=403, staff="ravi")
    get(env, "admin", "/history", expect=422, **{"from": "2026-11-06", "to": "2026-11-05"})
    get(env, "admin", "/history", expect=422, **{"from": "2025-01-01", "to": "2026-11-05"})
    get(env, "admin", "/history", expect=422, page_size=101)

    book, raw = sheets_of(get(env, "admin", "/export", **{"from": "2026-11-04", "to": "2026-11-05"}).content)
    submitted = book["Submitted reports"]
    assert submitted[0] == ["Date", "Recruiter", "Submitted at"] + SNAPSHOT + ["Note"]
    assert [(r[0], r[1], r[-1]) for r in submitted[1:]] == [("2026-11-05", "Meena", ""), ("2026-11-05", "Asha", "Final note"),
                                                            ("2026-11-04", "Ravi", "Late close")]
    assert submitted[2][3 + SNAPSHOT.index("forwarded")] == 2 and submitted[2][3 + SNAPSHOT.index("leadsContacted")] == 6
    clients = book["By client"]
    assert clients[0] == ["Client", "Resumes forwarded to clients", "Resumes prescreened", "Workers shortlisted by clients", "Online interviews",
                          "Offer letters received", "Visas received", "Workers travelled", "Total", "Interviews conducted (all modes)"]
    assert [r[0] for r in clients[1:]] == ["Gulf Builders", "Desert Co"]
    for row in clients[1:]:   # the Total is the sum of the figure columns beside it, exactly like the table on screen
        assert row[8] == sum(row[1:8])
    only = sheets_of(get(env, "asha", "/export", **{"from": "2026-11-04", "to": "2026-11-05"}).content)[0]["Submitted reports"]
    assert [r[1] for r in only[1:]] == ["Asha"]


# ------------------------------------------------------------------ period-aware lead numbers

def test_lead_numbers_follow_the_chosen_period_and_agency_day_boundaries(env):
    day = {"from": "2026-11-05", "to": "2026-11-05"}
    body = report(env, **day)
    sheet = body["leadSheet"]
    # handed out on Thursday: L9 at 00:30 IST (Wednesday in UTC), L7 09:00, L1 10:00, L2 12:30. L8 at 23:30 IST Wednesday is Wednesday's.
    assert (sheet["newInPeriod"], sheet["assignedInPeriod"], sheet["notContactedInPeriod"], sheet["backlog"]) == (2, 4, 2, 3)
    # the all-time numbers are still there (what is assigned now, the backlog of the period)
    assert sheet["assigned"] == 10 and sheet["totalLeads"] == 11 and sheet["notContacted"] == 2 and sheet["contacted"] == 8
    # compared with yesterday up to 14:00 only: L5 handed out 09:00, L4 uploaded 10:00, asha's call on L1 at 10:00; L8's 23:30 is later
    assert sheet["previous"] == {"assignedInPeriod": 1, "contacted": 1, "newInPeriod": 1}
    assert sheet["previous"]["contacted"] == metric(body, "leadsContacted")["previous"]
    asha, ravi = person(body, "asha")["leads"], person(body, "ravi")["leads"]
    assert (asha["assignedInPeriod"], asha["notContactedInPeriod"], asha["backlog"]) == (2, 1, 1)   # L2 was called BEFORE it was assigned
    assert (ravi["assignedInPeriod"], ravi["notContactedInPeriod"], ravi["backlog"]) == (2, 1, 2)   # L9 has only a note
    for key in ("assignedInPeriod", "notContactedInPeriod", "backlog"):
        assert sum(p["leads"][key] for p in body["staff"]) == sheet[key]
    assert report(env, staff="asha", **day)["leadSheet"]["newInPeriod"] == 1   # only the leads uploaded that are asha's: L3
    assert report(env, "ravi", **day)["leadSheet"]["assignedInPeriod"] == 2 and report(env, "ravi", **day)["leadSheet"]["newInPeriod"] == 0

    wed = report(env, **{"from": "2026-11-04", "to": "2026-11-04"})   # a finished day: 23:30 IST counts for Wednesday, 00:30 for Thursday
    assert wed["period"]["partial"] is False
    assert (wed["leadSheet"]["assignedInPeriod"], wed["leadSheet"]["newInPeriod"], wed["leadSheet"]["notContactedInPeriod"]) == (2, 1, 0)   # L8 and L5 were worked later, on Thursday
    assert person(wed, "ravi")["leads"]["assignedInPeriod"] == 1 and person(wed, "asha")["leads"]["assignedInPeriod"] == 1   # L8 / L5
    assert wed["leadSheet"]["previous"] == {"assignedInPeriod": 0, "contacted": 0, "newInPeriod": 0}
    week = report(env, **{"from": "2026-11-02", "to": "2026-11-05"})["leadSheet"]
    assert (week["assignedInPeriod"], week["newInPeriod"]) == (6, 3) and week["previous"]["assignedInPeriod"] == 0
    quiet = report(env, **{"from": "2026-12-01", "to": "2026-12-03"})["leadSheet"]   # nothing handed out: zeros, the backlog stays
    assert (quiet["assignedInPeriod"], quiet["newInPeriod"], quiet["notContactedInPeriod"], quiet["backlog"]) == (0, 0, 0, 3)

    def drill(metric_key, user="admin", **params):
        return get(env, user, "/drilldown", metric=metric_key, page_size=100, **{**day, **params}).json()

    expected = {"leadsAssignedInPeriod": ({"L1", "L2", "L7", "L9"}, 4, "Assigned"), "leadsNewInPeriod": ({"L3", "L10"}, 2, "Uploaded"),
                "leadsNotContactedInPeriod": ({"L2", "L9"}, 2, "Not contacted since assigned"),
                "leadsBacklog": ({"L2", "L7", "L9"}, 3, "Never contacted")}
    for key, (ids, total, verb) in expected.items():
        rows = drill(key)
        assert rows["total"] == len(rows["rows"]) == total and {r["leadId"] for r in rows["rows"]} == ids, key
        assert all(r["kind"] == "lead" and r["detail"].startswith(verb + ": ") for r in rows["rows"])
        times = [r["at"] for r in rows["rows"]]
        assert times == sorted(times, reverse=True)
    assert drill("leadsNewInPeriod")["label"] == "New leads in the period"
    assert {r["staffName"] for r in drill("leadsNewInPeriod")["rows"]} == {"Asha", "Unassigned"}   # L3 is asha's, L10 nobody's
    assert drill("leadsAssignedInPeriod", staff="ravi")["total"] == 2 and drill("leadsBacklog", staff="ravi")["total"] == 2
    assert drill("leadsAssignedInPeriod", user="asha")["total"] == 2 and drill("leadsNotContactedInPeriod", user="ravi")["total"] == 1
    assert drill("leadsAssignedInPeriod", **{"from": "2026-11-04", "to": "2026-11-04"})["total"] == 2
    get(env, "ravi", "/drilldown", expect=403, metric="leadsBacklog", staff="asha")
    blob = json.dumps(drill("leadsBacklog"))
    assert "+9180000000" not in blob and not [p for p in PHONES if p in blob]


# ------------------------------------------------------------------ reconciling with the Lead sheet screen

def test_the_lead_sheet_numbers_reconcile_with_the_lead_sheet_screen(env):
    from app.services import lead_sheet

    day = {"from": "2026-11-05", "to": "2026-11-05"}
    body = report(env, **day)
    sheet = body["leadSheet"]
    with env.world.sessions() as db:   # the Lead sheet's own summary on the same data
        screen = lead_sheet.summary(db, db.get(User, "admin"))
        ravi_screen = lead_sheet.summary(db, db.get(User, "ravi"))
    assert screen["total"] == sheet["totalLeads"] == 11 and screen["uncontacted"] == sheet["neverContacted"] == 4
    assert screen["byStatus"]["new"] == 4   # the "Not contacted" status; the card also needs zero calls, true for all four here
    assert sheet["unassignedLeads"] == {"total": 1, "neverContacted": 1, "newInPeriod": 1}   # L10, uploaded 09:00 Thursday, never called
    assert sheet["unassigned"] == 1   # still a plain count
    # the invariants the owner asked for: the rows plus the unassigned leads are the sheet
    assert sheet["totalLeads"] == sum(p["leads"]["assigned"] for p in body["staff"]) + sheet["unassignedLeads"]["total"]
    assert sheet["neverContacted"] == sum(p["leads"]["neverContacted"] for p in body["staff"]) + sheet["unassignedLeads"]["neverContacted"]
    assert sheet["newInPeriod"] >= sheet["unassignedLeads"]["newInPeriod"]
    for p in body["staff"]:   # per recruiter: never contacted is the backlog, one rule and one number
        assert p["leads"]["neverContacted"] == p["leads"]["backlog"]
    assert person(body, "asha")["leads"]["neverContacted"] == 1 and person(body, "ravi")["leads"]["neverContacted"] == 2
    # a recruiter's own view matches what their Lead sheet shows (managers and staff see only their leads there)
    mine = report(env, "ravi", **day)["leadSheet"]
    assert mine["totalLeads"] == ravi_screen["total"] == 4 and mine["neverContacted"] == ravi_screen["uncontacted"] == 2
    assert mine["unassignedLeads"] == {"total": 0, "neverContacted": 0, "newInPeriod": 0}
    one = report(env, staff="asha", **day)["leadSheet"]   # a chosen recruiter: their leads only, no unassigned pool
    assert one["totalLeads"] == 6 and one["neverContacted"] == 1 and one["unassignedLeads"]["total"] == 0
    # the period does not change them: they are the sheet as it stands, whatever range is chosen
    for rng in ({"from": "2026-11-02", "to": "2026-11-05"}, {"from": "2026-12-01", "to": "2026-12-03"}):
        other = report(env, **rng)["leadSheet"]
        assert (other["totalLeads"], other["neverContacted"], other["unassignedLeads"]["total"]) == (11, 4, 1)
    # leadSheet.assigned is the owned leads: the Lead sheet's own "assigned" (total minus the leads nobody owns)
    assert sheet["assigned"] == screen["total"] - 1 == 10


# ------------------------------------------------------------------ the "Total" (all time) period

def test_period_all_is_the_whole_history_with_nothing_to_compare_with(env):
    wide = report(env, **{"from": "2026-09-01", "to": "2026-11-05"})
    total = report(env, period="all")
    assert total["period"] == {"from": "2026-10-01", "to": "2026-11-05", "days": 36, "previousFrom": None, "previousTo": None,
                               "bucket": "week", "partial": False, "previousUntil": None, "allTime": True}
    assert report(env, period="all", **{"from": "2026-11-05", "to": "2026-11-05"}) == total   # from / to are ignored
    for key in KEYS:   # the same numbers as a wide explicit range, and nothing is compared
        item, ref = metric(total, key), metric(wide, key)
        assert item["value"] == ref["value"] and item["previous"] is None and item["changePct"] is None, key
        assert all(x["bucket"] == "week" for x in item["series"])
    assert values(total) == {"leadsContacted": 9, "prescreened": 6, "forwarded": 6, "shortlisted": 3, "interviews": 2, "offers": 2,
                             "visas": 1, "travelled": 2}
    starts = [date.fromisoformat(x["date"]) for x in metric(total, "prescreened")["series"]]
    assert starts[0] == date(2026, 9, 28) and starts[-1] == date(2026, 11, 2) and all(d.weekday() == 0 for d in starts)
    for key in KEYS[1:]:
        assert sum(x["value"] for x in metric(total, key)["series"]) == metric(total, key)["value"]
    assert [(p["id"], p["metrics"], p["total"]) for p in total["staff"]] == [(p["id"], p["metrics"], p["total"]) for p in wide["staff"]]
    assert total["leadSheet"]["previous"] is None and total["leadSheet"]["totalLeads"] == 11 and total["compliance"] is None
    assert all(p["submitted"] is None and p["submittedFigures"] is None for p in total["staff"])
    assert total["byClient"] == wide["byClient"] and total["leadFeedback"]["contacted"] == 9 and total["tasks"]["done"] == 4
    assert report(env, period="all", staff="ravi")["staff"][0]["id"] == "ravi"
    assert values(report(env, period="all", client="company2"))["forwarded"] == 1

    for key in KEYS:   # the drill-down of the whole history: every total equals the number shown
        body = get(env, "admin", "/drilldown", metric=key, page_size=100, period="all").json()
        assert body["total"] == len(body["rows"]) == metric(total, key)["value"], key
    assert get(env, "admin", "/drilldown", metric="tasksDone", period="all").json()["total"] == 4
    assert get(env, "admin", "/drilldown", metric="leadsAssignedInPeriod", period="all").json()["total"] == 10

    # a recruiter's scope is kept
    mine = report(env, "ravi", period="all")
    assert mine["scope"] == "self" and [p["id"] for p in mine["staff"]] == ["ravi"] and mine["period"]["allTime"] is True
    assert values(mine) == values(report(env, "ravi", **{"from": "2026-09-01", "to": "2026-11-05"})) and values(mine)["forwarded"] == 4
    assert get(env, "ravi", "/drilldown", metric="forwarded", period="all").json()["total"] == 4
    get(env, "ravi", "/drilldown", expect=403, metric="forwarded", period="all", staff="asha")
    get(env, "ravi", expect=403, period="all", staff="asha")

    book, raw = sheets_of(get(env, "admin", "/export", period="all").content)
    assert list(book)[:2] == ["Summary", "By recruiter"] and len(book) == 8
    assert book["Summary"][1][3] == "" and book["Summary"][1][4] == ""   # no previous value, no change
    assert book["By day"][0][0] == "Week starting" and book["By day"][1][0] == "2026-09-28"
    assert ["Previous period", "none (all time)"] in book["Summary"]
    response = get(env, "admin", "/export", period="all")
    assert 'filename="daily-work-report_2026-10-01_2026-11-05.xlsx"' in response.headers["content-disposition"]
    only = sheets_of(get(env, "ravi", "/export", period="all").content)[0]
    assert [r[0] for r in only["By recruiter"][1:]] == ["Ravi"]

    assert submit(env, "asha", "2026-11-05", "Closed").status_code == 200   # the day closes handed in, all time
    closes = get(env, "admin", "/history", period="all").json()
    assert closes["total"] == 1 and closes["rows"][0]["staffId"] == "asha"
    assert get(env, "ravi", "/history", period="all").json()["total"] == 0
    get(env, "admin", expect=422, period="week")


def test_a_long_history_is_drawn_by_month(env):
    with env.world.sessions() as db:   # one old call, three years back
        old = LeadActivity(lead_id="L10", user_id="asha", kind="call", summary="call", meta={"outcome": "answered"},
                           created_at=ist(date(2023, 3, 10), 11))
        db.add(old)
        db.commit()
        old_id = old.id
    try:
        total = report(env, period="all")
        assert total["period"]["from"] == "2023-03-10" and total["period"]["bucket"] == "month" and total["period"]["days"] > 730
        series = metric(total, "leadsContacted")["series"]
        assert all(x["bucket"] == "month" for x in series) and series[0] == {"date": "2023-03-01", "value": 1, "bucket": "month"}
        assert series[-1]["date"] == "2026-11-01" and len(series) == 45 and all(x["date"][8:] == "01" for x in series)
        assert metric(total, "leadsContacted")["value"] == 10 and metric(total, "leadsContacted")["previous"] is None
        assert sum(x["value"] for x in metric(total, "prescreened")["series"]) == metric(total, "prescreened")["value"] == 6
        assert [x["value"] for x in metric(total, "prescreened")["series"]][-2:] == [2, 4]   # October, then November
        assert get(env, "admin", "/drilldown", metric="leadsContacted", period="all").json()["total"] == 10
        book = sheets_of(get(env, "admin", "/export", period="all").content)[0]
        assert book["By day"][0][0] == "Month starting" and book["By day"][1][0] == "2023-03-01"
    finally:
        with env.world.sessions() as db:
            db.execute(text("DELETE FROM crm_lead_activities WHERE id = :i"), {"i": old_id})
            db.commit()


def test_drilldown_of_the_lead_sheet_cards_matches_total_leads_and_never_contacted(env):
    def drill(user="admin", **params):
        return get(env, user, "/drilldown", page_size=100, **params).json()

    for extra in ({"from": "2026-11-05", "to": "2026-11-05"}, {"period": "all"}):
        sheet = report(env, **extra)["leadSheet"]
        total = drill(metric="leadsTotal", **extra)
        assert total["label"] == "Leads on the sheet" and total["total"] == len(total["rows"]) == sheet["totalLeads"] == 11
        assert all(r["kind"] == "lead" and r["detail"].startswith("Lead: ") for r in total["rows"])
        assert {r["staffName"] for r in total["rows"]} == {"Asha", "Ravi", "Unassigned"}
        assert [r["staffName"] for r in total["rows"] if r["leadId"] == "L10"] == ["Unassigned"]
        never = drill(metric="leadsNotContacted", **extra)
        assert never["total"] == len(never["rows"]) == sheet["neverContacted"] == 4
        assert {r["leadId"] for r in never["rows"]} == {"L2", "L7", "L9", "L10"}   # status new and no call, assigned or not
        assert all(r["detail"].startswith("Never contacted: ") for r in never["rows"])
        assert [r["staffName"] for r in never["rows"] if r["leadId"] == "L10"] == ["Unassigned"]
        assert drill(metric="leadsBacklog", **extra)["total"] == 3   # the assigned part only, unchanged
        for staff, leads, never_count in (("ravi", 4, 2), ("asha", 6, 1)):   # a chosen recruiter: their leads only
            one = report(env, staff=staff, **extra)["leadSheet"]
            assert drill(metric="leadsTotal", staff=staff, **extra)["total"] == one["totalLeads"] == leads
            assert drill(metric="leadsNotContacted", staff=staff, **extra)["total"] == one["neverContacted"] == never_count
        mine = report(env, "ravi", **extra)["leadSheet"]   # a recruiter sees only their own, and cannot name another
        assert drill("ravi", metric="leadsTotal", **extra)["total"] == mine["totalLeads"] == 4
        assert drill("ravi", metric="leadsNotContacted", **extra)["total"] == mine["neverContacted"] == 2
        assert {r["staffName"] for r in drill("ravi", metric="leadsTotal", **extra)["rows"]} == {"Ravi"}
    get(env, "ravi", "/drilldown", expect=403, metric="leadsTotal", staff="asha")
    pages = [get(env, "admin", "/drilldown", metric="leadsTotal", page=n, page_size=5).json() for n in (0, 1, 2, 3)]
    assert [(p["total"], len(p["rows"])) for p in pages] == [(11, 5), (11, 5), (11, 1), (11, 0)]
    blob = json.dumps(drill(metric="leadsTotal"))
    assert "+9180000000" not in blob and not [p for p in PHONES if p in blob]
