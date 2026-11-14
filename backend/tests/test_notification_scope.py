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
from app.services.candidate_review import WAITING_TITLE
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


def test_the_bell_hides_everything_that_is_not_for_the_person(env):
    for kind in ("attendance", "job", "daily_work", "feedback", "escalation", "reminder", "missed_update", "made_up"):
        for user in ("admin", "manager", "a"):
            add(env, user, kind, f"noise {kind}")
    for user in ("admin", "manager", "a"):
        add(env, user, "lead_assignment", "New leads assigned")
        add(env, user, "todo", "To-do due")
    # An assignment made to Asha: Asha and every admin get a copy; only Asha's is for her to act on.
    for user in ("admin", "admin2", "a"):
        add(env, user, "assignment", "Candidate assigned", "Person One · Assigned to Asha. Reassigned by Admin One: x")
    # An assignment made to the second admin: his own copy shows, the other admin's does not.
    add(env, "admin", "assignment", "Task assigned", "Work · Assigned to Admin Two. Reassigned by Admin One: x")
    add(env, "admin2", "assignment", "Task assigned", "Work · Assigned to Admin Two. Reassigned by Admin One: x")
    # Hand-created candidate (admins get a copy of every creation), email intake, and nobody-can-take-it.
    for user in ("admin", "admin2", "a"):
        add(env, user, "candidate_assignment", "Candidate assigned", "Person Two")
        add(env, user, "candidate_assignment", "Candidate received by email", "Person Three")
    for user in ("admin", "manager"):
        add(env, user, "candidate_assignment", WAITING_TITLE, "Person Four")
        add(env, user, "candidate_assignment", "Candidate needs assignment", "Person Five")
        add(env, user, "candidate_request", "Candidate requested", "Ravi requested Person One")
    env.db.commit()
    env.db.add(m.Notification(user_id="a", kind="assignment", title="Candidate received by WhatsApp", detail="Person Six", link_to="/x"))
    env.db.commit()

    admin = listed(env, "admin")
    # Neither the copy of Asha's assignment, nor the task made to Admin Two, nor a hand-created candidate is for this admin.
    assert admin == {"New leads assigned", "To-do due", "Candidate received by email", WAITING_TITLE,
                     "Candidate needs assignment", "Candidate requested"}
    assert not any(title.startswith("noise") for title in admin)
    assert listed(env, "admin2") == {"Task assigned", "Candidate received by email"}
    assert listed(env, "manager") == {"New leads assigned", "To-do due", WAITING_TITLE, "Candidate needs assignment", "Candidate requested"}
    staff = listed(env, "a")
    assert staff == {"New leads assigned", "To-do due", "Candidate assigned", "Candidate received by email",
                     "Candidate received by WhatsApp"}
    # The workspace snapshot answers with exactly the same notices as the bell list.
    for user in ("admin", "admin2", "manager", "a", "b"):
        assert snapshot_titles(env, user) == listed(env, user)
    # Nothing is deleted: the hidden rows are all still stored.
    assert env.db.scalar(select(func.count()).select_from(m.Notification)) == 48


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
    assert "Asha" in rows[0]["detail"] and rows[0]["linkTo"] == "/candidates/c1" and rows[0]["read"] is False
    assert listed(env, "a") == set() and listed(env, "manager") == set()


def test_the_whatsapp_received_notice_reaches_admins_like_the_email_one(env):
    # An intake with an owner writes kind "assignment"; it carries no "Assigned to <admin>." line, so only its title can show it.
    for user in ("admin", "admin2", "a"):
        add(env, user, "assignment", "Candidate received by WhatsApp", "Person Six")
        add(env, user, "candidate_assignment", "Candidate received by email", "Person Seven")
    # An ordinary assignment copy for somebody else stays hidden from the admins, and the quiet kinds stay quiet.
    add(env, "admin", "assignment", "Candidate assigned", "Person Eight · Assigned to Asha. Reassigned by Admin One: x")
    for kind in ("attendance", "escalation", "missed_update", "daily_work"):
        add(env, "admin", kind, f"noise {kind}")
    env.db.commit()
    assert listed(env, "admin") == {"Candidate received by WhatsApp", "Candidate received by email"}
    assert listed(env, "admin2") == {"Candidate received by WhatsApp", "Candidate received by email"}
    assert listed(env, "a") == {"Candidate received by WhatsApp", "Candidate received by email"}
    assert snapshot_titles(env, "admin") == listed(env, "admin")
