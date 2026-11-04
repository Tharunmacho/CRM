"""The dashboard's pipeline overview cards: offer letter, visa, departure and idle placements.

Synthetic data only. The pure tests build the same dict the dashboard reads produce; the PostgreSQL test proves the
compact one-statement dashboard read gives the identical answer as the full read.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.api.routers import reports
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import reporting
from app.services.reporting import OVERVIEW_ITEM_LIMIT, dashboard_data, read_dashboard_data, read_reporting_data
from app.services.staff_weekly import STUCK_DAYS
from test_database_naming import _migrate, schema_database  # noqa: F401  (pytest fixture)

NOW = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
ADMIN = SimpleNamespace(id="admin", role=m.Role.admin)
MANAGER = SimpleNamespace(id="manager", role=m.Role.manager)
ANN = SimpleNamespace(id="ann", role=m.Role.staff)
BOB = SimpleNamespace(id="bob", role=m.Role.staff)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


def build(placements):
    """placements: (key, owner, status, current_stage, created_ago_days, {stage: days ago completed}, extra stage rows)."""
    users = [{"id": uid, "name": uid.title(), "role": role} for uid, role in
             [("ann", "staff"), ("bob", "staff"), ("admin", "admin"), ("manager", "manager")]]
    data = {"users": users, "clients": [{"id": "cl", "name": "Gulf Client"}],
            "companies": [{"id": "co", "name": "Gulf Employer", "client_id": "cl"}],
            "designations": [{"id": "d", "title": "Welder"}],
            "orders": [{"id": "o", "ref": "ORD-1", "company_id": "co", "designation_id": "d", "openings": 99, "status": "open", "created_at": ago(days=90)}],
            "candidates": [], "applications": [], "stages": [], "tasks": [], "remarks": []}
    for key, owner, status, stage, created, done in placements:
        data["candidates"].append({"id": f"c-{key}", "name": f"Cand {key}", "owner_id": owner, "created_at": ago(days=created), "source": "email"})
        data["applications"].append({"id": f"a-{key}", "candidate_id": f"c-{key}", "order_id": "o", "current_stage": stage,
                                     "status": status, "created_at": ago(days=created)})
        for number, days in done.items():
            data["stages"].append({"application_id": f"a-{key}", "stage": number, "attempt": 1, "data": {}, "completed_at": ago(days=days),
                                   "reopened_reason": None})
    return data


PLACEMENTS = [
    # offer letter: two waiting (one for ann, one for bob), one issued and now in medical
    ("offer-ann", "ann", "active", 5, 40, {1: 39, 2: 30, 3: 20, 4: 3}),
    ("offer-bob", "bob", "active", 5, 40, {1: 39, 4: 2}),
    ("issued", "ann", "active", 6, 60, {4: 30, 5: 4}),
    # visa: waiting at 9, waiting at 10, and one stamped who moved on to the ticket
    ("visa9", "ann", "active", 9, 80, {4: 40, 5: 30, 8: 10}),
    ("visa10", "bob", "active", 10, 80, {4: 40, 5: 30, 9: 5}),
    ("stamped", "bob", "active", 11, 90, {4: 50, 5: 40, 9: 20, 10: 6}),
    # departure: ticket stage (the stamped one above) plus two deployed
    ("flown-a", "ann", "deployed", 11, 120, {5: 90, 9: 60, 10: 50, 11: 12}),
    ("flown-b", "bob", "deployed", 11, 120, {5: 90, 9: 60, 10: 50, 11: 1}),
    # a closed placement after an offer must still count as an issued offer, never as waiting
    ("closed", "ann", "closed", 6, 70, {4: 40, 5: 35}),
]


@pytest.fixture
def dataset():
    return build(PLACEMENTS)


def ids(card):
    return [row["applicationId"] for row in card["items"]]


def test_offer_visa_departure_counts_and_lists_for_the_whole_team(dataset):
    overview = dashboard_data(dataset, ADMIN, now=NOW)["overview"]
    assert overview["idleDays"] == STUCK_DAYS == 7
    offer, visa, departure = overview["offer"], overview["visa"], overview["departure"]
    # Offer: waiting = active at stage 5; issued = stage 5 completed (issued, stamped, flown, closed all had one).
    assert offer["current"] == 2
    assert offer["issued"] == 7
    assert ids(offer)[:2] == ["a-offer-ann", "a-offer-bob"]  # waiting first
    assert [row["state"] for row in offer["items"][:2]] == ["current", "current"]
    assert {row["state"] for row in offer["items"][2:]} == {"done"} and offer["itemsTotal"] == 9
    # Visa: waiting at 9 or 10; received = stage 9 done; stamped = stage 10 done.
    assert visa["current"] == 2 and set(ids(visa)[:2]) == {"a-visa9", "a-visa10"}
    assert visa["received"] == 4 and visa["stamped"] == 3
    assert "a-offer-ann" not in ids(visa)
    # Departure: waiting at the ticket stage, and deployed placements (closed ones never count).
    assert departure["current"] == 1 and ids(departure)[0] == "a-stamped"
    assert departure["departed"] == 2 and {"a-flown-a", "a-flown-b"} == set(ids(departure)[1:])
    assert "a-closed" not in ids(departure) and "a-closed" not in ids(visa)
    first = offer["items"][0]
    assert first["candidate"] == "Cand offer-ann" and first["order"] == "ORD-1" and first["designation"] == "Welder"
    assert first["client"] == "Gulf Client" and first["company"] == "Gulf Employer" and first["owner"] == "Ann"
    assert first["stage"] == 5 and first["stageLabel"] == "Offer letter" and first["days"] == 3


def test_a_closed_offer_is_history_not_waiting(dataset):
    offer = dashboard_data(dataset, ADMIN, now=NOW)["overview"]["offer"]
    closed = next(row for row in offer["items"] if row["applicationId"] == "a-closed")
    assert closed["state"] == "done" and closed["status"] == "closed" and closed["days"] == 35


def test_idle_uses_the_weekly_report_threshold_and_reports_the_longest_wait():
    data = build([
        ("fresh", "ann", "active", 3, 30, {2: 6}),            # moved 6 days ago
        ("edge", "ann", "active", 3, 30, {2: 7}),             # exactly 7 days: not yet stuck (the report uses "before 7 days ago")
        ("stale", "ann", "active", 3, 30, {2: 8}),
        ("oldest", "ann", "active", 4, 100, {3: 45}),
        ("never", "ann", "active", 1, 9, {}),                 # no stage ever finished: measured from when it was mapped
        ("new-app", "ann", "active", 1, 2, {}),
        ("deployed-old", "ann", "deployed", 11, 200, {11: 100}),   # not active, never idle
        ("closed-old", "ann", "closed", 2, 200, {2: 100}),
    ])
    idle = dashboard_data(data, ADMIN, now=NOW)["overview"]["idle"]
    assert [row["applicationId"] for row in idle["items"]] == ["a-oldest", "a-never", "a-stale"]
    assert idle["count"] == 3 and idle["itemsTotal"] == 3 and idle["longestDays"] == 45
    assert idle["items"][0]["days"] == 45 and idle["items"][1]["days"] == 9


def test_a_reopened_stage_record_does_not_count_as_progress_or_as_an_issued_offer():
    data = build([("re", "ann", "active", 4, 60, {4: 30, 5: 20})])
    for row in data["stages"]:
        if row["stage"] == 5:
            row["reopened_reason"] = "Offer withdrawn"
    overview = dashboard_data(data, ADMIN, now=NOW)["overview"]
    assert overview["offer"]["issued"] == 0 and overview["offer"]["items"] == []
    # The last real move is the stage 4 record 30 days ago, not the voided offer.
    assert overview["idle"]["items"][0]["days"] == 30


def test_staff_see_only_their_own_candidates_and_admin_can_narrow_to_one_person(dataset):
    ann = dashboard_data(dataset, ANN, now=NOW)["overview"]
    assert ann["offer"]["current"] == 1 and ids(ann["offer"])[0] == "a-offer-ann"
    assert all(row["ownerId"] == "ann" for card in ("offer", "visa", "departure", "idle") for row in ann[card]["items"])
    assert ann["visa"]["current"] == 1 and ann["departure"]["current"] == 0 and ann["departure"]["departed"] == 1
    bob = dashboard_data(dataset, BOB, now=NOW)["overview"]
    assert ids(bob["visa"])[:1] == ["a-visa10"] and bob["visa"]["current"] == 1
    team = dashboard_data(dataset, ADMIN, now=NOW)["overview"]
    assert team["offer"]["current"] == ann["offer"]["current"] + bob["offer"]["current"]
    assert team["idle"]["count"] == ann["idle"]["count"] + bob["idle"]["count"]
    # Managers are treated like the admin; the person filter narrows both.
    assert dashboard_data(dataset, MANAGER, now=NOW)["overview"] == team
    assert dashboard_data(dataset, ADMIN, "ann", now=NOW)["overview"] == ann
    assert dashboard_data(dataset, MANAGER, "bob", now=NOW)["overview"] == bob


def test_a_staff_member_cannot_widen_the_overview_by_naming_someone_else(dataset):
    with pytest.raises(HTTPException) as refused:
        dashboard_data(dataset, ANN, "bob", now=NOW)
    assert refused.value.status_code == 403
    # Naming themselves is allowed and changes nothing.
    assert dashboard_data(dataset, ANN, "ann", now=NOW)["overview"] == dashboard_data(dataset, ANN, now=NOW)["overview"]


def test_the_http_route_keeps_staff_to_their_own_scope(dataset):
    def client(user):
        app = FastAPI()
        app.include_router(reports.dashboard_router)
        app.dependency_overrides[get_db] = lambda: object()
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    with patch.object(reports, "read_dashboard_data", return_value=dataset) as read:
        assert client(ANN).get("/api/dashboard?staff_id=bob").status_code == 403
        read.assert_not_called()
        own = client(ANN).get("/api/dashboard?staff_id=ann")
        assert own.status_code == 200
        assert [row["applicationId"] for row in own.json()["overview"]["offer"]["items"]][:1] == ["a-offer-ann"]
        assert own.json()["overview"]["offer"]["current"] == 1
        everyone = client(ADMIN).get("/api/dashboard").json()["overview"]
        assert everyone["offer"]["current"] == 2
        just_bob = client(MANAGER).get("/api/dashboard?staff_id=bob").json()["overview"]
        assert just_bob["offer"]["current"] == 1 and ids(just_bob["offer"]) [0] == "a-offer-bob"


def test_lists_are_capped_but_the_counts_stay_exact():
    count = OVERVIEW_ITEM_LIMIT + 25
    data = build([(f"w{n}", "ann", "active", 5, 90 - (n % 50), {4: 60 - (n % 50)}) for n in range(count)])
    offer = dashboard_data(data, ADMIN, now=NOW)["overview"]["offer"]
    assert offer["current"] == count and offer["itemsTotal"] == count and len(offer["items"]) == OVERVIEW_ITEM_LIMIT
    days = [row["days"] for row in offer["items"]]
    assert days == sorted(days, reverse=True)  # the longest waits survive the cap


def test_compact_dashboard_read_matches_the_full_read_in_one_statement(schema_database, dataset):
    """Real PostgreSQL (guarded loopback crm_schema_test*): same overview from both reads, one round trip."""
    from test_attendance_read_performance import measured
    _migrate(schema_database, "head")
    with Session(schema_database) as db:
        for row in dataset["users"]:
            db.add(m.User(**row, email=f"{row['id']}@example.test"))
        db.flush()
        for key, model in [("clients", m.Client), ("companies", m.Company), ("designations", m.Designation),
                           ("orders", m.Order), ("candidates", m.Candidate), ("applications", m.Application)]:
            db.add_all(model(**row, **({"ref": row["id"], "phone": row["id"]} if key == "candidates" else {})) for row in dataset[key]); db.flush()
        db.add_all(m.ApplicationStage(**row, outcome="Done") for row in dataset["stages"])
        # A superseded (reopened) stage 5 attempt must be ignored by both reads.
        db.add(m.ApplicationStage(application_id="a-visa9", stage=5, attempt=2, data={}, outcome="Done", completed_at=ago(days=1),
                                  reopened_reason="Admin correction"))
        db.commit()
        loaded = read_reporting_data(db)
        for user, scope in [(ANN, None), (BOB, None), (MANAGER, None), (ADMIN, None), (ADMIN, "ann"), (ADMIN, "bob")]:
            with measured(db) as measurement:
                compact = read_dashboard_data(db, user, scope, now=NOW)
            assert measurement["queries"] == 1
            assert {row["stage"] for row in compact["stages"]} <= set(reporting.OVERVIEW_STAGES)
            assert dashboard_data(compact, user, scope, now=NOW) == dashboard_data(loaded, user, scope, now=NOW)
        team = dashboard_data(read_dashboard_data(db, ADMIN, None, now=NOW), ADMIN, None, now=NOW)["overview"]
        assert team["offer"]["current"] == 2 and team["offer"]["issued"] == 7
        assert team["visa"]["received"] == 4 and team["departure"]["departed"] == 2
        mine = dashboard_data(read_dashboard_data(db, ANN, None, now=NOW), ANN, None, now=NOW)["overview"]
        assert all(row["ownerId"] == "ann" for card in ("offer", "visa", "departure", "idle") for row in mine[card]["items"])
