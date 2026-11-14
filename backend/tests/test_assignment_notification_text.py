"""The exact words of an assignment notice: who assigned what, with both candidate IDs, and never "Reassigned".

POST /api/candidates/reassign as an admin; the recruiter's notice is read back from the database. Synthetic people only.
Isolated PostgreSQL only (needs TEST_DATABASE_URL like the other DB tests)."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers import candidates, data, files
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Asha Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="a", name="Rina Recruiter", email="a@example.test", role=m.Role.staff),
                    m.User(id="b", name="Ravi Recruiter", email="b@example.test", role=m.Role.staff)])
        db.flush()
        db.add(m.Candidate(id="cand-1", ref="CND-00042", care_id="CI-26-27-042", name="PRADEEP KUMAR", phone="9000000042",
                           source=m.Source.other, owner_id="a"))
        db.commit()
        app = FastAPI()
        for router in (files.router, candidates.router, data.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who)


def notices(env, user_id=None):
    env.db.expire_all()
    query = select(m.Notification).order_by(m.Notification.created_at, m.Notification.id)
    if user_id:
        query = query.where(m.Notification.user_id == user_id)
    return list(env.db.scalars(query))


def test_the_recruiter_is_told_who_assigned_which_candidate_with_both_ids(env):
    response = env.client.post("/api/candidates/reassign", json={"candidateIds": ["cand-1"], "toUserId": "b", "reason": "Assigned manually"})
    assert response.status_code == 200, response.text
    arrived = [row for row in notices(env, "b") if row.kind == "candidate_assigned"]
    assert len(arrived) == 1
    assert arrived[0].detail == "Asha Admin assigned you PRADEEP KUMAR (CI-26-27-042 · CND-00042)."


def test_no_notification_text_says_reassigned(env):
    for target in ("b", "a", "b"):   # back and forth, as an admin may
        response = env.client.post("/api/candidates/reassign", json={"candidateIds": ["cand-1"], "toUserId": target, "reason": "Workload"})
        assert response.status_code == 200, response.text
    everything = notices(env)
    assert everything
    assert not any("reassigned" in f"{row.title} {row.detail or ''}".lower() for row in everything)
