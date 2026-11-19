"""Deleting a Recruiter's account: Admin and MD only, never an Admin, an MD or yourself, never damaging history.

Disposable PostgreSQL only (TEST_DATABASE_URL, a loopback crm_schema_test* database); the whole schema is built from the models.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.security import SESSION_COOKIE, hash_password
from app.db.models import (Application, ApplicationStatus, AssignmentPointer, AuditEntry, Base, Candidate, CandidateStatus, ClientStaff, DailyGoal,
                           Lead, Notification, Remark, Role, Session as LoginSession, Source, Task, TaskEvent, TaskKind, TodoItem, User)
from app.db.session import get_db
from app.services import user_admin
from test_database_naming import _guard_test_url

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def env():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB")
    engine = create_engine(_guard_test_url(raw), connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for identifier, role in (("admin", Role.admin), ("admin2", Role.admin), ("md", Role.manager), ("a", Role.staff), ("b", Role.staff),
                                 ("c", Role.staff), ("d", Role.staff)):
            db.add(User(id=identifier, name=identifier.upper() + " Person", email=f"{identifier}@example.test", role=role, active=True,
                        phone="+919000000000", receives_auto_assign=role is Role.staff, on_leave=False, password_hash=hash_password("initial-test-password")))
        db.flush()
        for identifier in ("admin", "admin2", "md", "a", "b", "c", "d"):
            db.add(LoginSession(id=f"session-{identifier}", user_id=identifier, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()
    from app.api.routers import users
    application = FastAPI()
    application.include_router(users.router)

    def database():
        with factory() as db:
            yield db
    application.dependency_overrides[get_db] = database
    try:
        with TestClient(application, base_url="https://testserver", raise_server_exceptions=False) as client:
            yield SimpleNamespace(client=client, factory=factory, engine=engine)
    finally:
        engine.dispose()


def act_as(env, who):
    env.client.cookies.clear()
    env.client.cookies.set(SESSION_COOKIE, f"session-{who}")


def delete(env, who, target, **query):
    act_as(env, who)
    return env.client.delete(f"/api/users/{target}", params=query)


def give_work(env, owner="a", candidates=3, with_remark=True):
    """Candidates with an open initial-call task each, one finished task, a remark, a to-do, a lead, goals and a client allocation."""
    with env.factory() as db:
        for number in range(candidates):
            cid = f"cand-{owner}-{number}"
            db.add(Candidate(id=cid, ref=f"C-{owner}-{number}", name=f"Candidate {number}", phone=f"90000000{number:02d}", source=Source.walk_in,
                             owner_id=owner, languages=[], skills=[], designations=[]))
            db.flush()
            db.add(Task(id=f"task-{owner}-{number}", title="Initial call", kind=TaskKind.initial_call, candidate_id=cid, owner_id=owner, created_at=NOW))
        db.add(Task(id=f"done-{owner}", title="Finished", kind=TaskKind.initial_call, candidate_id=f"cand-{owner}-0", owner_id=owner,
                    created_at=NOW, completed_at=NOW + timedelta(hours=1)))
        db.flush()
        if with_remark:
            db.add(Remark(task_id=f"done-{owner}", body="Called, will come tomorrow", author_id=owner))
        db.add(AuditEntry(actor_id=owner, action="Recorded a call", entity_type="candidate", entity_id=f"cand-{owner}-0", target="Candidate 0"))
        db.add(Lead(id=f"lead-{owner}", name="Lead One", phone="9111111111", owner_id=owner, status="new"))
        db.add(Notification(user_id=owner, kind="x", title="Something for them"))
        db.add(TodoItem(owner_id=owner, title="Private", due_at=NOW))
        db.add(DailyGoal(user_id=owner, metric="candidates", target=5))
        db.execute(text("INSERT INTO crm_client_contacts (id, name) VALUES ('client-1', 'Client One')"))
        db.add(ClientStaff(client_id="client-1", user_id=owner))
        db.add(AssignmentPointer(id=1, last_user_id=owner))
        db.commit()


# ----------------------------------------------------------------------------------------- who may delete whom

@pytest.mark.parametrize("who", ["admin", "md"])
def test_admin_and_md_may_delete_a_recruiter_with_nothing_to_move(env, who):
    response = delete(env, who, "d")
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "nothing to move"


def test_a_recruiter_may_not_delete_anyone(env):
    for target in ("d", "c", "a"):
        assert delete(env, "b", target).status_code == 403
    with env.factory() as db:
        assert db.get(User, "d").active and not db.get(User, "d").deleted


@pytest.mark.parametrize("who, target, words", [("admin", "admin2", "Only Recruiter accounts"), ("md", "admin", "Only Recruiter accounts"),
                                                ("admin", "md", "Only Recruiter accounts"), ("md", "md", "your own account"),
                                                ("admin", "admin", "your own account")])
def test_admin_md_and_self_cannot_be_deleted(env, who, target, words):
    response = delete(env, who, target)
    assert response.status_code == 409 and words in response.json()["detail"], response.text
    with env.factory() as db:
        assert db.get(User, target).active and not db.get(User, target).deleted


def test_an_unknown_or_already_deleted_account_is_not_found(env):
    assert delete(env, "admin", "nobody").status_code == 404
    assert delete(env, "admin", "d").status_code == 200
    assert delete(env, "admin", "d").status_code == 404


def test_a_recruiter_cannot_even_ask_what_deleting_would_move(env):
    act_as(env, "b")
    assert env.client.get("/api/users/a/delete-summary").status_code == 403
    act_as(env, "md")
    assert env.client.get("/api/users/a/delete-summary").status_code == 200


# ----------------------------------------------------------------------------------------- the account itself

def test_the_login_is_removed_and_the_person_vanishes_from_the_list(env):
    assert delete(env, "md", "d").status_code == 200
    with env.factory() as db:
        person = db.get(User, "d")
        assert person.deleted and not person.active and person.password_hash is None and person.phone is None
        assert person.email == "deleted-d@deleted.invalid" and person.name == "D Person (former user)"
        assert not person.receives_auto_assign and not person.on_leave
        assert db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == "d")) == 0
    act_as(env, "admin")
    listed = {row["id"] for row in env.client.get("/api/users").json()}
    assert "d" not in listed and {"a", "b", "c", "admin", "md"} <= listed
    # They cannot sign in (no password, no session), the cookie of the deleted person is dead, and they cannot be edited.
    env.client.cookies.clear()
    env.client.cookies.set(SESSION_COOKIE, "session-d")
    assert env.client.get("/api/users").status_code == 401
    act_as(env, "admin")
    assert env.client.patch("/api/users/d", json={"name": "Back"}).status_code == 404
    assert env.client.post("/api/users/d/password", json={"password": "another-long-password"}).status_code == 404


def test_the_email_address_is_free_again_and_the_reserved_domain_is_refused(env):
    delete(env, "admin", "d")
    act_as(env, "admin")
    again = env.client.post("/api/users", json={"name": "New D", "email": "d@example.test", "role": "staff", "password": "a-long-password"})
    assert again.status_code == 201, again.text
    refused = env.client.post("/api/users", json={"name": "Sneaky", "email": "x@deleted.invalid", "role": "staff", "password": "a-long-password"})
    assert refused.status_code == 422


def test_the_audit_entry_names_who_what_and_to_whom(env):
    give_work(env, "a")
    assert delete(env, "md", "a", reassignTo="b").status_code == 200
    with env.factory() as db:
        entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "Deleted user")).one()
        assert (entry.actor_id, entry.entity_type, entry.entity_id, entry.target) == ("md", "user", "a", "A Person")
        assert entry.meta["candidates"] == 3 and entry.meta["tasks"] == 3 and entry.meta["leads"] == 1
        assert entry.meta["toUserId"] == "b" and entry.meta["mode"] == "reassign" and entry.meta["originalEmail"] == "a@example.test"
        assert entry.meta["revokedSessions"] == 1


# ----------------------------------------------------------------------------------------- their work

def test_a_choice_is_required_when_there_is_work_and_only_one_may_be_given(env):
    give_work(env, "a")
    assert delete(env, "admin", "a").status_code == 422
    both = delete(env, "admin", "a", reassignTo="b", leaveUnassigned="true")
    assert both.status_code == 422
    with env.factory() as db:
        assert not db.get(User, "a").deleted and db.get(Candidate, "cand-a-0").owner_id == "a"


def test_reassigning_moves_candidates_open_tasks_and_leads_and_keeps_history(env):
    give_work(env, "a")
    response = delete(env, "admin", "a", reassignTo="b")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["candidates"], body["tasks"], body["leads"], body["toUserId"]) == (3, 3, 1, "b")
    with env.factory() as db:
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {"b"}
        assert {t.owner_id for t in db.scalars(select(Task).where(Task.completed_at.is_(None)))} == {"b"}
        assert db.get(Task, "done-a").owner_id == "a", "a finished task stays as it was"
        assert db.get(Lead, "lead-a").owner_id == "b"
        # History is kept and keeps the name: the remark, the audit entry the person made, the finished task's owner.
        remark = db.scalars(select(Remark)).one()
        assert remark.author_id == "a" and db.get(User, remark.author_id).name == "A Person (former user)"
        made = db.scalars(select(AuditEntry).where(AuditEntry.action == "Recorded a call")).one()
        assert made.actor_id == "a"
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.kind == "assignment")) == 3
        reassigned = list(db.scalars(select(AuditEntry).where(AuditEntry.action == "Candidate reassigned during account change")))
        assert len(reassigned) == 3 and all(e.meta["toUserId"] == "b" and e.meta["fromUserId"] == "a" for e in reassigned)
        # The new recruiter is told; the deleted person's own notices, to-dos, goals and client allocation are gone.
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == "b")) >= 1
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == "a")) == 0
        assert db.scalar(select(func.count()).select_from(TodoItem).where(TodoItem.owner_id == "a")) == 0
        assert db.scalar(select(func.count()).select_from(DailyGoal).where(DailyGoal.user_id == "a")) == 0
        assert db.scalar(select(func.count()).select_from(ClientStaff).where(ClientStaff.user_id == "a")) == 0
        assert db.get(AssignmentPointer, 1).last_user_id is None


def test_leaving_unassigned_frees_candidates_and_leads_and_cancels_what_remains(env):
    give_work(env, "a")
    response = delete(env, "md", "a", leaveUnassigned="true")
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "unassign" and response.json()["candidates"] == 3
    with env.factory() as db:
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {None}
        assert db.get(Lead, "lead-a").owner_id is None
        open_tasks = list(db.scalars(select(Task).where(Task.owner_id == "a", Task.completed_at.is_(None), Task.cancelled_at.is_(None))))
        assert open_tasks == [], "no open task is left with a person who is gone"
        assert db.get(Task, "done-a").completed_at is not None
        assert db.scalars(select(Remark)).one().author_id == "a"
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Candidate unassigned")) == 3


def test_a_candidate_who_cannot_be_left_without_a_recruiter_blocks_the_unassign_choice_and_changes_nothing(env):
    give_work(env, "a")
    with env.factory() as db:
        db.get(Candidate, "cand-a-1").status = CandidateStatus.deployed
        db.commit()
    response = delete(env, "admin", "a", leaveUnassigned="true")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "cannot be left without a recruiter" in detail and "deployed" in detail and "deactivate the account instead" in detail
    with env.factory() as db:
        assert not db.get(User, "a").deleted and db.get(User, "a").active
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {"a"}
        assert db.get(Lead, "lead-a").owner_id == "a"
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action.in_(["Deleted user", "Candidate unassigned"]))) == 0
        assert db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == "a")) == 1
    # The same person can still be deleted by handing everything to a recruiter.
    assert delete(env, "admin", "a", reassignTo="b").status_code == 200


@pytest.mark.parametrize("recipient, words", [("a", "different active recruiter"), ("admin", "different active recruiter"), ("md", "different active recruiter")])
def test_the_recipient_must_be_another_active_recruiter(env, recipient, words):
    give_work(env, "a")
    response = delete(env, "admin", "a", reassignTo=recipient)
    assert response.status_code == 422 and words in response.json()["detail"], response.text
    with env.factory() as db:
        assert db.get(Candidate, "cand-a-0").owner_id == "a" and not db.get(User, "a").deleted


def test_a_recipient_who_is_inactive_or_on_leave_or_deleted_is_refused(env):
    give_work(env, "a")
    with env.factory() as db:
        db.get(User, "c").active = False
        db.get(User, "d").on_leave = True
        db.commit()
    assert delete(env, "admin", "a", reassignTo="c").status_code == 422
    assert delete(env, "admin", "a", reassignTo="d").status_code == 422
    assert delete(env, "admin", "b").status_code == 200
    assert delete(env, "admin", "a", reassignTo="b").status_code == 404


def test_a_deactivated_recruiter_with_nothing_is_deleted_without_any_choice(env):
    act_as(env, "admin")
    assert env.client.patch("/api/users/d", json={"active": False}).status_code == 200
    assert env.client.get("/api/users/d/delete-summary").json() == {"candidates": 0, "openTasks": 0, "leads": 0}
    assert delete(env, "admin", "d").status_code == 200


def test_the_summary_counts_what_the_dialog_will_show(env):
    give_work(env, "a")
    act_as(env, "admin")
    assert env.client.get("/api/users/a/delete-summary").json() == {"candidates": 3, "openTasks": 3, "leads": 1}


def test_a_failed_commit_is_one_calm_sentence_offering_deactivate_and_rolls_everything_back(env):
    give_work(env, "a")
    with env.factory() as db:
        actor = db.get(User, "admin")

        def refuse():
            raise IntegrityError("DELETE", {}, Exception("restrict"))
        db.commit = refuse
        with pytest.raises(HTTPException) as caught:
            user_admin.delete_user(db, actor, "a", "b")
        assert caught.value.status_code == 409 and "Deactivate it instead" in caught.value.detail
    with env.factory() as db:
        assert not db.get(User, "a").deleted and db.get(Candidate, "cand-a-0").owner_id == "a"


# ----------------------------------------------------------------------------------------- the rest of the app tolerates the tombstone

def test_screens_that_list_people_skip_the_deleted_account(env):
    from app.services import staff_directory
    delete(env, "admin", "d")
    with env.factory() as db:
        ids = {row["id"] for row in staff_directory.staff_availability(db, "Asia/Kolkata")["staff"]}
        assert "d" not in ids and {"a", "b", "c", "admin"} <= ids


def test_statement_budget_for_a_recruiter_with_many_candidates(env):
    give_work(env, "a", candidates=30)
    counted: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        counted.append(statement)
    event.listen(env.engine, "before_cursor_execute", count)
    try:
        assert delete(env, "admin", "a", reassignTo="b").status_code == 200
    finally:
        event.remove(env.engine, "before_cursor_execute", count)
    # One transaction; the transfer is batched (inserts are executemany), so thirty candidates must not cost thirty round trips each.
    assert len(counted) < 80, len(counted)
