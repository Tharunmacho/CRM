"""The bell shows only notices a person has to act on, and a staff allocation request reaches the admin at once.

Isolated PostgreSQL only (loopback crm_schema_test*); every name and number here is synthetic."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.routers import candidates, workflow
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import change_feed
from app.services.candidate_review import WAITING_TITLE  # noqa: F401
from app.services.change_feed import visible_to
from app.services.desk_snapshot import read_snapshot
from test_change_feed import Listener
from test_database_naming import schema_database  # noqa: F401  (fixture)


@pytest.fixture
def env(schema_database):
    change_feed.install()
    m.Base.metadata.create_all(schema_database)
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([m.User(id="admin", name="Admin One", email="admin@example.test", role=m.Role.admin),
                    m.User(id="admin2", name="Admin Two", email="admin2@example.test", role=m.Role.admin),
                    m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha", email="a@example.test", role=m.Role.staff),
                    m.User(id="b", name="Ravi", email="b@example.test", role=m.Role.staff)])
        db.flush()
        db.add(m.Candidate(id="c1", ref="REF-1", name="Person One", phone="9000000001", source=m.Source.other,
                           owner_id="b", intake_owner_id="b", status=m.CandidateStatus.new))
        db.commit()
    app = FastAPI()
    app.include_router(workflow.router)
    app.include_router(candidates.router)
    who = {"id": "a"}
    db = sessions()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
    listener = Listener(schema_database)
    listener.messages(0.3)
    with TestClient(app) as client:
        yield SimpleNamespace(db=db, client=client, who=who, listener=listener, sessions=sessions)
    listener.close()
    db.close()


def add(env, user_id, kind, title, detail=None, link="/x"):
    row = m.Notification(user_id=user_id, kind=kind, title=title, detail=detail, link_to=link)
    env.db.add(row)
    return row


def listed(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()
    response = env.client.get("/api/notifications")
    assert response.status_code == 200, response.text
    return {row["title"] for row in response.json()}


def snapshot_titles(env, user_id):
    env.db.expire_all()
    return {row["title"] for row in read_snapshot(env.db, user_id)["notifications"]}


def test_the_bell_shows_only_the_kinds_written_for_the_person(env):
    shown = ("candidate_assigned", "candidate_removed", "candidate_waiting", "candidate_request", "task_assigned",
             "task_removed", "lead_assignment", "lead_removed", "todo")
    retired = ("assignment", "candidate_assignment", "daily_work")
    quiet = ("attendance", "job", "feedback", "escalation", "reminder", "missed_update", "made_up")
    for user in ("admin", "manager", "a"):
        for kind in shown:
            add(env, user, kind, f"shown {kind}")
        for kind in retired + quiet:
            add(env, user, kind, f"noise {kind}")
    # The retired notices keep their stored text but never reach the bell, whoever they were written for.
    add(env, "a", "daily_work", "Daily task overdue", "PRADEEP · Engineer was due at 13:00. Finish this now.")
    add(env, "a", "assignment", "Candidate assigned", "Person One · Assigned to Asha. Reassigned by Admin One: Assigned manually")
    add(env, "admin", "candidate_assignment", WAITING_TITLE, "Person Four")
    env.db.commit()

    expected = {f"shown {kind}" for kind in shown}
    for user in ("admin", "manager", "a"):
        assert listed(env, user) == expected, user
    assert listed(env, "b") == set()
    for user in ("admin", "admin2", "manager", "a", "b"):  # the workspace snapshot answers with exactly the bell's list
        assert snapshot_titles(env, user) == listed(env, user)
    env.who["id"] = "a"
    env.db.expire_all()
    assert "Daily task overdue" not in {row["title"] for row in env.client.get("/api/notifications").json()}
    # Nothing is deleted: the hidden rows are all still stored.
    assert env.db.scalar(select(func.count()).select_from(m.Notification)) == 3 * (len(shown) + len(retired) + len(quiet)) + 3


def test_unread_counts_follow_the_visible_notices_only(env):
    add(env, "a", "candidate_assigned", "New candidate assigned to you")
    add(env, "a", "daily_work", "Daily task overdue")
    add(env, "a", "escalation", "Task overdue")
    env.db.commit()
    env.who["id"] = "a"
    env.db.expire_all()
    rows = env.client.get("/api/notifications").json()
    assert [row["title"] for row in rows] == ["New candidate assigned to you"] and not rows[0]["read"]
    assert sum(1 for row in read_snapshot(env.db, "a")["notifications"] if not row["read"]) == 1


def test_staff_request_reaches_only_the_admins_and_the_bell_shows_who_asked(env):
    env.listener.messages(0.3)
    env.who["id"] = "a"
    env.db.expire_all()
    response = env.client.post("/api/candidates/c1/request-allocation")
    assert response.status_code == 200, response.text
    assert response.json()["notified"] == 2
    messages = env.listener.messages()
    assert len(messages) == 1
    body = messages[0]
    assert "notifications" in body["t"]
    assert body["u"]["notifications"] == ["admin", "admin2"]  # owner-scoped: named recipients only
    assert "Asha" not in json.dumps(body) and "Person One" not in json.dumps(body)  # opaque ids and topic names only
    for admin in ("admin", "admin2"):
        assert "notifications" in visible_to(body, "admin", admin)["topics"]
    assert "notifications" not in (visible_to(body, "staff", "a") or {}).get("topics", [])
    assert "notifications" not in (visible_to(body, "staff", "b") or {}).get("topics", [])
    assert "notifications" not in (visible_to(body, "manager", "manager") or {}).get("topics", [])
    # The admin's bell lists it, with the requester's name and a link to the candidate.
    env.who["id"] = "admin"
    env.db.expire_all()
    rows = env.client.get("/api/notifications").json()
    assert len(rows) == 1 and rows[0]["kind"] == "candidate_request"
    assert rows[0]["title"] == "A recruiter is asking for a candidate"
    assert rows[0]["detail"] == ("Asha asked you to assign Person One (REF-1) to them. The candidate is with Ravi now. "
                                 "Open the candidate to assign a recruiter.")
    assert rows[0]["linkTo"] == "/candidates/c1/application" and rows[0]["read"] is False
    assert listed(env, "a") == set() and listed(env, "manager") == set()
