"""Lead sheet numbers on the dashboard: role scope, the card lists, the HTTP route, cache isolation.

Synthetic data only. Time is frozen at Thursday 2026-11-05 14:00 IST for the pure service calls; the HTTP tests use
the real clock with leads created relative to it. Runs on the guarded disposable PostgreSQL DB.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.routers import reports
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import lead_metrics
from app.services import response_cache as cache
from test_database_naming import _migrate, schema_database  # noqa: F401  (pytest fixture)

TZ = "Asia/Kolkata"
IST = ZoneInfo(TZ)
NOW = datetime(2026, 11, 5, 14, 0, tzinfo=IST)
ADMIN = SimpleNamespace(id="admin", role=m.Role.admin)
MANAGER = SimpleNamespace(id="manager", role=m.Role.manager)
ANN = SimpleNamespace(id="ann", role=m.Role.staff)
BOB = SimpleNamespace(id="bob", role=m.Role.staff)


def seed(sessions, now=NOW):
    """Ten synthetic leads; see the expectations below. `now` anchors every date."""
    today = now - timedelta(hours=3)
    old = now - timedelta(days=20)
    with sessions() as db:
        for key, role in (("admin", m.Role.admin), ("manager", m.Role.manager), ("ann", m.Role.staff), ("bob", m.Role.staff)):
            db.add(m.User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True))
        db.flush()

        def lead(key, owner, status, *, created=old, calls=0, follow=None, resume=None, assigned=None):
            db.add(m.Lead(id=key, name=f"Lead {key}", phone=f"+9190000000{len(key)}", owner_id=owner, status=status, created_time=created,
                          call_count=calls, next_follow_up_at=follow, resume_received_at=resume,
                          assigned_at=assigned or (old if owner else None), position_applied="Chef", country_interest="Dubai"))

        lead("L1", "ann", "new", created=today)
        lead("L2", "ann", "contacted", calls=2, follow=now - timedelta(days=1))
        lead("L3", "ann", "resume_received", calls=3, resume=now - timedelta(days=1))
        lead("L4", "ann", "converted", calls=4)
        lead("L5", "bob", "new", created=today)
        lead("L6", "bob", "interested", calls=1, follow=now + timedelta(days=2))
        lead("L7", None, "new", created=today)
        lead("L8", None, "invalid")
        lead("L9", "bob", "converted", calls=2)
        lead("L10", "ann", "not_interested", calls=1, follow=now - timedelta(days=1))
        db.flush()
        for lead_id, owner, days_ago in (("L4", "ann", 2), ("L9", "bob", 40)):
            db.add(m.LeadActivity(lead_id=lead_id, user_id=owner, kind="convert", summary="Converted to candidate",
                                  created_at=now - timedelta(days=days_ago)))
        db.commit()


@pytest.fixture
def desk(schema_database):  # noqa: F811
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    seed(sessions)
    return SimpleNamespace(sessions=sessions, engine=schema_database)


def leads_for(desk, user, staff_id=None):
    with desk.sessions() as db:
        return lead_metrics.dashboard_leads(db, user, staff_id, TZ, NOW)


# ------------------------------------------------------------------ the numbers

def test_team_numbers_are_admin_only(desk):
    team = leads_for(desk, ADMIN)
    assert team["scope"] == "team" and team["staffId"] is None
    assert (team["newToday"], team["notContacted"], team["followUpsDue"], team["resumesReceived"]) == (3, 2, 1, 1)
    assert team["converted"] == {"week": 1, "month": 1, "quarter": 2, "year": 2, "all": 2}
    assert (team["total"], team["assigned"], team["convertedTotal"], team["conversionPercent"]) == (10, 8, 2, 25.0)
    assert team["unassigned"] == 1  # L7 (L8 is invalid, so closed)
    # a manager counts only the leads assigned to them (none in this seed), like staff: no team numbers, no pool
    manager = leads_for(desk, MANAGER)
    assert manager["scope"] == "mine" and manager["staffId"] == "manager" and manager["unassigned"] is None
    assert (manager["total"], manager["assigned"], manager["newToday"], manager["notContacted"]) == (0, 0, 0, 0)
    assert manager["converted"]["all"] == 0


def test_managers_count_only_the_leads_assigned_to_them(desk):
    with desk.sessions() as db:
        for key in ("L5", "L9"):  # one new today, one converted 40 days ago
            db.get(m.Lead, key).owner_id = "manager"
        db.commit()
    manager = leads_for(desk, MANAGER)
    assert manager["scope"] == "mine" and (manager["total"], manager["newToday"], manager["notContacted"]) == (2, 1, 1)
    assert manager["converted"] == {"week": 0, "month": 0, "quarter": 1, "year": 1, "all": 1}  # L9, now the manager's
    # naming a staff member does not widen a manager's numbers
    assert leads_for(desk, MANAGER, "ann") == manager
    assert leads_for(desk, ADMIN, "manager")["total"] == 2


def test_staff_see_only_their_own_leads_and_no_unassigned_card(desk):
    ann, bob = leads_for(desk, ANN), leads_for(desk, BOB)
    assert ann["scope"] == "mine" and ann["staffId"] == "ann" and ann["unassigned"] is None
    assert (ann["newToday"], ann["notContacted"], ann["followUpsDue"], ann["resumesReceived"]) == (1, 1, 1, 1)
    assert ann["converted"]["week"] == 1 and ann["converted"]["all"] == 1
    assert (ann["total"], ann["assigned"], ann["conversionPercent"]) == (5, 5, 20.0)
    assert (bob["newToday"], bob["notContacted"], bob["followUpsDue"], bob["resumesReceived"]) == (1, 1, 0, 0)
    assert bob["converted"] == {"week": 0, "month": 0, "quarter": 1, "year": 1, "all": 1}
    assert bob["conversionPercent"] == pytest.approx(33.3)
    # A staff member naming someone else is still answered with their own numbers at this layer (the route refuses it first).
    assert leads_for(desk, ANN, "bob") == ann


def test_admin_can_narrow_to_one_person_and_unassigned_stays_global(desk):
    bob = leads_for(desk, ADMIN, "bob")
    assert bob["scope"] == "person" and bob["staffId"] == "bob"
    assert (bob["newToday"], bob["notContacted"], bob["total"]) == (1, 1, 3)
    assert bob["unassigned"] == 1
    assert leads_for(desk, ADMIN, "ann")["resumesReceived"] == 1
    assert leads_for(desk, MANAGER, "ann")["resumesReceived"] == 0, "a manager cannot narrow to someone else's leads"


def test_lists_behind_the_cards_match_the_counts_and_scope(desk):
    def items(user, card, staff=None, period="month"):
        with desk.sessions() as db:
            return lead_metrics.dashboard_lead_items(db, user, staff, card, period, TZ, NOW)

    team_new = items(ADMIN, "newToday")
    assert team_new["total"] == 3 and {row["id"] for row in team_new["items"]} == {"L1", "L5", "L7"}
    assert [row["id"] for row in items(ANN, "followUpsDue")["items"]] == ["L2"]
    assert items(BOB, "followUpsDue")["total"] == 0
    assert items(ADMIN, "unassigned")["total"] == 1 and items(ADMIN, "unassigned", "bob")["total"] == 1
    assert items(ANN, "unassigned") == {"card": "unassigned", "total": 0, "items": []}  # staff never see the pool
    assert [row["id"] for row in items(ADMIN, "converted", period="week")["items"]] == ["L4"]
    assert {row["id"] for row in items(ADMIN, "converted", period="quarter")["items"]} == {"L4", "L9"}
    assert [row["id"] for row in items(ADMIN, "converted", "bob", period="all")["items"]] == ["L9"]
    assert items(ANN, "converted", period="all")["total"] == 1
    assert [row["id"] for row in items(ANN, "resumesReceived")["items"]] == ["L3"]
    nc = items(ADMIN, "notContacted")
    assert nc["total"] == 2 and {row["id"] for row in nc["items"]} == {"L1", "L5"}
    row = nc["items"][0]
    assert set(row) == {"id", "name", "position", "country", "status", "ownerName", "callCount", "nextFollowUpAt", "at"}


def test_missing_lead_tables_degrade_to_no_leads_key(desk):
    from sqlalchemy import text
    with desk.engine.begin() as connection:
        connection.execute(text("DROP TABLE crm_lead_activities, crm_leads, crm_lead_batches CASCADE"))
    assert leads_for(desk, ADMIN) is None


# ------------------------------------------------------------------ the HTTP routes and the cache

@pytest.fixture
def api(desk, monkeypatch):
    monkeypatch.setattr(reports, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    actor = SimpleNamespace(id="admin", role=m.Role.admin)
    app = FastAPI()
    app.include_router(reports.dashboard_router)

    def database():
        with desk.sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, actor=actor, desk=desk)


def as_user(api, user):
    api.actor.id, api.actor.role = user.id, user.role


def test_dashboard_response_carries_the_leads_key_per_role(api):
    as_user(api, ADMIN)
    team = api.client.get("/api/dashboard").json()
    assert team["leads"]["scope"] == "team" and team["leads"]["total"] == 10 and "overview" in team
    as_user(api, ANN)
    mine = api.client.get("/api/dashboard").json()["leads"]
    assert mine["scope"] == "mine" and mine["total"] == 5 and mine["unassigned"] is None


def test_staff_cannot_widen_the_leads_by_naming_someone_else(api):
    as_user(api, ANN)
    assert api.client.get("/api/dashboard?staff_id=bob").status_code == 403
    assert api.client.get("/api/dashboard/leads?card=followUpsDue&staff_id=bob").status_code == 403
    own = api.client.get("/api/dashboard/leads?card=followUpsDue&staff_id=ann")
    assert own.status_code == 200 and [row["id"] for row in own.json()["items"]] == ["L2"]
    assert api.client.get("/api/dashboard/leads?card=bogus").status_code == 422
    as_user(api, ADMIN)
    narrowed = api.client.get("/api/dashboard?staff_id=bob").json()["leads"]
    assert narrowed["staffId"] == "bob" and narrowed["total"] == 3
    as_user(api, MANAGER)  # a manager's lead numbers stay their own, whoever they name
    managed = api.client.get("/api/dashboard?staff_id=bob").json()["leads"]
    assert managed["staffId"] == "manager" and managed["scope"] == "mine" and managed["total"] == 0
    assert api.client.get("/api/dashboard/leads?card=notContacted&staff_id=bob").json()["total"] == 0


def test_cache_keeps_people_apart_and_is_dropped_by_the_leads_topic(api, monkeypatch):
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    cache.clear()
    try:
        as_user(api, ADMIN)
        assert api.client.get("/api/dashboard").json()["leads"]["notContacted"] == 2
        as_user(api, ANN)
        ann = api.client.get("/api/dashboard").json()["leads"]
        assert ann["scope"] == "mine" and ann["notContacted"] == 1  # never the admin's cached copy
        as_user(api, ADMIN)
        assert api.client.get("/api/dashboard").json()["leads"]["notContacted"] == 2  # served from the memo
        before = cache.stats()["hits"]
        api.client.get("/api/dashboard")
        assert cache.stats()["hits"] == before + 1
        # Somebody calls lead L5: the commit touches crm_leads, which publishes the `leads` topic and empties the entry.
        with api.desk.sessions() as db:
            db.get(m.Lead, "L5").call_count = 1
            db.commit()
        assert api.client.get("/api/dashboard").json()["leads"]["notContacted"] == 1
        # The card list is cached per card and person, and dropped the same way.
        as_user(api, BOB)
        assert api.client.get("/api/dashboard/leads?card=notContacted").json()["total"] == 0
        as_user(api, ADMIN)
        assert api.client.get("/api/dashboard/leads?card=notContacted").json()["total"] == 1
    finally:
        cache.clear()
