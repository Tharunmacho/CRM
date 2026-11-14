"""Staff default view (my new candidates, newest assignment first) and the staff "Request" for a candidate that is not theirs.

Isolated PostgreSQL only; every name and number here is synthetic."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers import candidates
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)


@pytest.fixture
def env(schema_database):
    _migrate(schema_database, "head")
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="admin2", name="Second Admin", email="admin2@example.test", role=m.Role.admin),
                    m.User(id="gone", name="Former Admin", email="gone@example.test", role=m.Role.admin, active=False),
                    m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha", email="a@example.test", role=m.Role.staff),
                    m.User(id="b", name="Ravi", email="b@example.test", role=m.Role.staff)])
        db.flush()
        now = datetime.now(timezone.utc)

        def person(identifier, owner, number, status="new", days=0):
            db.add(m.Candidate(id=identifier, ref=f"REF-{identifier}", name=f"Person {identifier}", phone=f"90000000{number:02d}",
                               source=m.Source.other, owner_id=owner, intake_owner_id=owner, status=m.CandidateStatus(status),
                               created_at=now - timedelta(days=days)))
        person("old", "a", 1, days=10)
        person("mid", "a", 2, days=5)
        person("fresh", "a", 3, days=1)
        person("worked", "a", 4, status="in_process", days=0)
        person("theirs", "b", 5, days=2)
        person("waiting", None, 6, days=3)
        db.commit()
        app = FastAPI()
        app.include_router(candidates.router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "a"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who)


def act_as(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()


def listing(env, **params):
    response = env.client.get("/api/candidates", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload):
    return [row["id"] for row in payload["items"]]


EVERYONE = {"old", "mid", "fresh", "worked", "theirs", "waiting"}


# ------------------------------------------------------------------------------ staff default view

def test_my_new_view_lists_only_my_new_candidates_newest_assignment_first(env):
    payload = listing(env, mine_new="true", sort="assigned", direction="desc")
    assert ids(payload) == ["fresh", "mid", "old"]
    assert payload["total"] == 3


def test_a_reassignment_moves_a_candidate_to_the_top_of_my_new_view(env):
    # "old" arrived ten days ago but was given to Asha just now.
    env.db.add(m.AuditEntry(actor_id="admin", action="Candidate reassigned", target="Person old", entity_type="candidate", entity_id="old",
                            meta={"fromUserId": "b", "toUserId": "a", "reason": "test"}))
    # A later hand-over to someone else must not count as her assignment time.
    env.db.add(m.AuditEntry(actor_id="admin", action="Candidate reassigned", target="Person mid", entity_type="candidate", entity_id="mid",
                            at=datetime.now(timezone.utc) + timedelta(days=1), meta={"fromUserId": "a", "toUserId": "b"}))
    env.db.commit()
    assert ids(listing(env, mine_new="true", sort="assigned", direction="desc")) == ["old", "fresh", "mid"]


def test_the_full_data_bank_is_one_parameter_away_and_unchanged(env):
    assert set(ids(listing(env))) == EVERYONE
    assert listing(env)["total"] == 6
    assert set(ids(listing(env, sort="added", direction="desc"))) == EVERYONE


def test_my_new_view_always_means_the_signed_in_person_whatever_staff_is_sent(env):
    act_as(env, "b")
    assert ids(listing(env, mine_new="true", staff="a")) == ["theirs"]


def test_admin_without_the_flag_is_exactly_as_before(env):
    act_as(env, "admin")
    assert set(ids(listing(env))) == EVERYONE
    assert ids(listing(env, mine_new="true")) == []


def test_the_assigned_sort_is_accepted_and_unknown_sorts_are_still_refused(env):
    assert len(ids(listing(env, sort="assigned", direction="asc"))) == 6
    assert env.client.get("/api/candidates", params={"sort": "nonsense"}).status_code == 422


# ------------------------------------------------------------------------------ request an allocation

def notices(env, kind="candidate_request"):
    return list(env.db.scalars(select(m.Notification).where(m.Notification.kind == kind).order_by(m.Notification.user_id)))


def requests_logged(env, candidate_id):
    return list(env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == "Requested candidate allocation",
                                                          m.AuditEntry.entity_id == candidate_id)))


def test_a_staff_request_notifies_every_active_admin_and_is_audited(env):
    response = env.client.post("/api/candidates/theirs/request-allocation")
    assert response.status_code == 200, response.text
    assert response.json() == {"requested": True, "alreadyRequested": False, "notified": 2}
    rows = notices(env)
    assert [row.user_id for row in rows] == ["admin", "admin2"]  # not the manager, the inactive admin or the requester
    assert all(row.title == "Candidate requested" and row.link_to == "/candidates/theirs" and not row.read for row in rows)
    assert "Asha requested Person theirs (REF-theirs)" in rows[0].detail and "now with Ravi" in rows[0].detail
    [entry] = requests_logged(env, "theirs")
    assert entry.actor_id == "a" and entry.entity_type == "candidate" and entry.target == "Person theirs"
    assert entry.meta["currentOwnerId"] == "b"


def test_an_unassigned_candidate_can_be_requested_too(env):
    response = env.client.post("/api/candidates/waiting/request-allocation")
    assert response.status_code == 200 and response.json()["alreadyRequested"] is False
    assert "not assigned yet" in notices(env)[0].detail


def test_a_repeat_request_within_a_day_is_answered_without_new_notices(env):
    assert env.client.post("/api/candidates/theirs/request-allocation").json()["alreadyRequested"] is False
    again = env.client.post("/api/candidates/theirs/request-allocation")
    assert again.status_code == 200
    assert again.json() == {"requested": True, "alreadyRequested": True, "notified": 0}
    assert len(notices(env)) == 2 and len(requests_logged(env, "theirs")) == 1


def test_a_request_is_allowed_again_after_a_day_and_people_and_candidates_are_independent(env):
    env.client.post("/api/candidates/theirs/request-allocation")
    requests_logged(env, "theirs")[0].at = datetime.now(timezone.utc) - timedelta(hours=25)
    env.db.commit()
    assert env.client.post("/api/candidates/theirs/request-allocation").json()["alreadyRequested"] is False
    assert env.client.post("/api/candidates/waiting/request-allocation").json()["alreadyRequested"] is False
    act_as(env, "b")
    assert env.client.post("/api/candidates/waiting/request-allocation").json()["alreadyRequested"] is False
    assert len(requests_logged(env, "waiting")) == 2


def test_a_staff_member_cannot_request_their_own_candidate(env):
    response = env.client.post("/api/candidates/fresh/request-allocation")
    assert response.status_code == 409
    assert "already assigned to you" in response.json()["detail"]
    assert notices(env) == [] and requests_logged(env, "fresh") == []


@pytest.mark.parametrize("role", ["admin", "manager"])
def test_admins_and_managers_cannot_send_requests(env, role):
    act_as(env, role)
    assert env.client.post("/api/candidates/theirs/request-allocation").status_code == 403
    assert notices(env) == [] and requests_logged(env, "theirs") == []


def test_an_unknown_candidate_is_a_404(env):
    response = env.client.post("/api/candidates/missing/request-allocation")
    assert response.status_code == 404
    assert notices(env) == []


def test_with_no_active_admin_nothing_is_recorded_and_a_later_request_goes_through(env):
    env.db.execute(m.User.__table__.update().where(m.User.role == m.Role.admin).values(active=False))
    env.db.commit()
    act_as(env, "a")
    response = env.client.post("/api/candidates/theirs/request-allocation")
    assert response.status_code == 503 and "No admin is available" in response.json()["detail"]
    assert notices(env) == [] and requests_logged(env, "theirs") == []
    env.db.execute(m.User.__table__.update().where(m.User.id == "admin").values(active=True))
    env.db.commit()
    act_as(env, "a")
    later = env.client.post("/api/candidates/theirs/request-allocation")
    assert later.status_code == 200 and later.json() == {"requested": True, "alreadyRequested": False, "notified": 1}


def test_a_staff_member_cannot_flood_the_admins_with_requests(env):
    now = datetime.now(timezone.utc)
    for n in range(candidates.ALLOCATION_REQUESTS_PER_HOUR):
        env.db.add(m.AuditEntry(actor_id="a", action="Requested candidate allocation", target=f"P{n}", entity_type="candidate",
                                entity_id=f"other-{n}", at=now - timedelta(minutes=5), meta={}))
    env.db.commit()
    act_as(env, "a")
    refused = env.client.post("/api/candidates/theirs/request-allocation")
    assert refused.status_code == 429
    assert notices(env) == [] and requests_logged(env, "theirs") == []
    act_as(env, "b")  # the limit is per person
    assert env.client.post("/api/candidates/waiting/request-allocation").status_code == 200
