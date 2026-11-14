"""Candidates an admin assigns to a recruiter must show on that recruiter's daily work and in the supervisors' table.

Every assignment path sets the candidate's owner; the daily list is every candidate a recruiter owns. A candidate the intake
flagged for review (no phone read yet, document warnings) keeps the status `needs_review` after it is assigned, and the daily list
used to skip those, so the Tasks screen said there was nothing to work for a recruiter who had been given them.
Needs TEST_DATABASE_URL like the other DB tests; all people are synthetic.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routers import candidates, daily_work as daily_router, data
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import daily_work, form_assignment
from test_database_naming import _migrate, schema_database

TUESDAY = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)  # 08:30 IST on a working day
RAVI_OWNED_STATUS = {"new": m.CandidateStatus.new, "review": m.CandidateStatus.needs_review}


@pytest.fixture
def desk(schema_database, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(daily_work, "get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return TUESDAY if tz is None else TUESDAY.astimezone(tz)

    monkeypatch.setattr(daily_router, "datetime", Frozen)
    monkeypatch.setattr(data, "datetime", Frozen)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="asha", name="Asha", email="asha@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="tharun", name="Tharun .v", email="tharun@example.test", role=m.Role.staff, receives_auto_assign=True)])
        db.commit()
        app = FastAPI()
        app.include_router(candidates.router)
        app.include_router(data.router)
        app.include_router(daily_router.router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who)


def candidate(env, key, *, owner=None, status="new", phone="9000000000"):
    """A candidate straight in the Data Bank, as intake or the import would leave it."""
    row = m.Candidate(id=key, ref=f"CND-{key}", name=f"Candidate {key}", phone=phone or "", source=m.Source.walk_in, owner_id=owner,
                      status=RAVI_OWNED_STATUS[status], needs_review=status == "review")
    env.db.add(row)
    env.db.commit()
    return row


def tharuns_work(env):
    """What the admin's Tasks screen shows: the table rows under Tharun, and his summary row (staff board)."""
    env.who["id"] = "admin"
    env.db.expire_all()
    everyone = env.client.get("/api/daily-work?staff=all")
    assert everyone.status_code == 200, everyone.text
    body = everyone.json()
    rows = {row["candidateId"] for row in body["items"] if row["ownerId"] == "tharun"}
    board = next(s for s in body["staff"] if s["userId"] == "tharun")
    assert rows == {row["candidateId"] for row in self_view(env)["items"]}  # his own screen agrees with the supervisors' table
    return rows, board


def self_view(env):
    env.who["id"] = "tharun"
    env.db.expire_all()
    response = env.client.get("/api/daily-work")
    assert response.status_code == 200, response.text
    env.who["id"] = "admin"
    return response.json()


def test_admin_reassigning_candidates_puts_them_on_the_recruiters_daily_work(desk):
    candidate(desk, "ready", owner="asha", status="new")
    candidate(desk, "flagged", owner="asha", status="review", phone="")  # intake could not read a phone: stays flagged for review
    desk.who["id"] = "admin"
    response = desk.client.post("/api/candidates/reassign", json={"candidateIds": ["ready", "flagged"], "toUserId": "tharun", "reason": "Workload"})
    assert response.status_code == 200, response.text

    rows, board = tharuns_work(desk)
    assert rows == {"ready", "flagged"}
    assert board["summary"] == {"total": 2, "toWork": 2, "doneToday": 0}  # no daily cap: both are on his list, nothing "short"


def test_assigning_waiting_candidates_puts_them_on_the_recruiters_daily_work(desk):
    candidate(desk, "waiting", owner=None, status="review", phone="")
    candidate(desk, "waiting2", owner=None, status="new")
    desk.who["id"] = "admin"
    response = desk.client.post("/api/candidates/assign-waiting")
    assert response.status_code == 200, response.text
    assert response.json()["assigned"] == 2

    everyone = desk.client.get("/api/daily-work?staff=all").json()
    allocated = {row["candidateId"] for row in everyone["items"]}
    assert allocated == {"waiting", "waiting2"}  # both recruiters share them round-robin; none is lost on the way
    assert {row["ownerId"] for row in everyone["items"]} <= {"asha", "tharun"}


def test_the_application_form_recruiter_select_puts_the_candidate_on_the_daily_work(desk):
    candidate(desk, "form", owner="asha", status="review", phone="")
    admin = desk.db.get(m.User, "admin")
    result = form_assignment.apply(desk.db, admin, "form", {"recruiterId": "tharun"})
    desk.db.commit()
    assert "recruiter" in result["changed"]

    rows, board = tharuns_work(desk)
    assert rows == {"form"} and board["summary"]["total"] == 1


def test_candidates_created_or_imported_straight_for_a_recruiter_show_on_their_daily_work(desk):
    # The create form and the folder import both save the candidate with the recruiter as owner; one that still needs the
    # recruiter's check (no phone yet) is flagged `needs_review` but is theirs to work.
    candidate(desk, "created", owner="tharun", status="new")
    candidate(desk, "imported", owner="tharun", status="review", phone="")

    rows, board = tharuns_work(desk)
    assert rows == {"created", "imported"} and board["summary"]["total"] == 2


def test_closed_candidates_are_never_on_the_list(desk):
    for key, status in (("gone", m.CandidateStatus.not_interested), ("flown", m.CandidateStatus.deployed)):
        desk.db.add(m.Candidate(id=key, ref=f"CND-{key}", name=key, phone="9000000001", source=m.Source.walk_in, owner_id="tharun", status=status))
    desk.db.commit()

    rows, board = tharuns_work(desk)
    assert rows == set()
    assert board["summary"] == {"total": 0, "toWork": 0, "doneToday": 0}


def test_the_flagged_candidate_leaves_the_old_recruiters_list_when_it_moves_on_and_is_never_listed_twice(desk):
    candidate(desk, "flagged", owner="tharun", status="review", phone="")
    assert tharuns_work(desk)[0] == {"flagged"}
    desk.who["id"] = "admin"
    assert desk.client.post("/api/candidates/reassign", json={"candidateIds": ["flagged"], "toUserId": "asha", "reason": "Back"}).status_code == 200
    desk.db.expire_all()
    mine = desk.client.get("/api/daily-work?staff=all").json()
    owners = [(row["candidateId"], row["ownerId"]) for row in mine["items"]]
    assert owners == [("flagged", "asha")]  # one row per candidate: gone from Tharun's list, on Asha's
