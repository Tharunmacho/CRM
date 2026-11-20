"""User Management audit: leads travel with a deactivation or role change, phone and password input rules, the sign-in memo is emptied
after the commit, and hidden accounts stay out of the report staff lists and pickers.

Disposable PostgreSQL only (TEST_DATABASE_URL, a loopback crm_schema_test* database); reuses the account fixture of test_user_delete.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core import deps
from app.db.models import AuditEntry, Candidate, Lead, Role, Session as LoginSession, User
from app.services.reporting import Filters, report_data
from test_database_naming import schema_database  # noqa: F401  (fixture module import keeps the guard in one place)
from test_user_delete import act_as, env, give_work  # noqa: F401  (`env` is a fixture)


def lead_only(env, owner="a", count=2):
    with env.factory() as db:
        for number in range(count):
            db.add(Lead(id=f"only-{owner}-{number}", name=f"Lead {number}", phone=f"92000000{number:02d}", owner_id=owner, status="new"))
        db.commit()


def patch_user(env, who, target, **body):
    act_as(env, who)
    return env.client.patch(f"/api/users/{target}", json=body)


def test_a_recruiter_with_only_leads_cannot_be_deactivated_without_choosing_who_takes_them(env):
    lead_only(env)
    refused = patch_user(env, "admin", "a", active=False)
    assert refused.status_code == 422 and "leads" in refused.json()["detail"]
    with env.factory() as db:
        assert db.get(User, "a").active and {lead.owner_id for lead in db.scalars(select(Lead))} == {"a"}
    done = patch_user(env, "admin", "a", active=False, reassignToUserId="b")
    assert done.status_code == 200, done.text
    with env.factory() as db:
        assert not db.get(User, "a").active
        assert {lead.owner_id for lead in db.scalars(select(Lead))} == {"b"}
        entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "User deactivated", AuditEntry.entity_id == "a")).one()
        assert entry.meta["transfer"]["leads"] == 2 and entry.meta["transfer"]["toUserId"] == "b"


def test_a_role_change_away_from_recruiter_moves_the_leads_too_and_an_md_may_do_it(env):
    give_work(env, "a")
    assert patch_user(env, "md", "a", role="md").status_code == 422
    done = patch_user(env, "md", "a", role="md", reassignToUserId="b")
    assert done.status_code == 200, done.text
    with env.factory() as db:
        person = db.get(User, "a")
        assert person.role == Role.manager and not person.receives_auto_assign and not person.on_leave
        assert db.get(Lead, "lead-a").owner_id == "b"
        assert {c.owner_id for c in db.scalars(select(Candidate))} == {"b"}


def test_editing_an_account_that_is_already_off_never_asks_for_a_recruiter_again(env):
    lead_only(env)
    assert patch_user(env, "admin", "a", active=False, reassignToUserId="b").status_code == 200
    with env.factory() as db:
        db.get(Lead, "only-a-0").owner_id = "a"   # an old account that still holds a lead
        db.commit()
    renamed = patch_user(env, "admin", "a", name="Renamed A", active=False)
    assert renamed.status_code == 200, renamed.text
    with env.factory() as db:
        assert db.get(User, "a").name == "Renamed A"
        assert db.scalars(select(AuditEntry).where(AuditEntry.entity_id == "a").order_by(AuditEntry.at.desc())).first().action == "User updated"


def test_phone_is_cleaned_and_validated_and_a_blank_phone_means_no_phone(env):
    act_as(env, "admin")
    base = {"name": "Phone Test", "email": "phone@example.test", "role": "staff", "password": "any-password"}
    assert env.client.post("/api/users", json={**base, "phone": "call me"}).status_code == 422
    assert env.client.post("/api/users", json={**base, "phone": "12"}).status_code == 422
    made = env.client.post("/api/users", json={**base, "phone": "  +971   50 000 0000 "})
    assert made.status_code == 201 and made.json()["phone"] == "+971 50 000 0000"
    other = env.client.post("/api/users", json={**base, "email": "nophone@example.test", "phone": "   "})
    assert other.status_code == 201 and other.json()["phone"] is None
    cleared = env.client.patch(f"/api/users/{made.json()['id']}", json={"phone": ""})
    assert cleared.status_code == 200 and cleared.json()["phone"] is None
    assert env.client.patch(f"/api/users/{made.json()['id']}", json={"phone": "abc"}).status_code == 422


def test_a_blank_or_all_space_password_is_refused_everywhere_and_a_chosen_one_is_kept_exactly(env):
    act_as(env, "admin")
    base = {"name": "Pass Test", "email": "pass@example.test", "role": "staff"}
    assert env.client.post("/api/users", json={**base, "password": "     "}).status_code == 422
    made = env.client.post("/api/users", json={**base, "password": "  spaced out  "})
    assert made.status_code == 201
    assert env.client.post(f"/api/users/{made.json()['id']}/password", json={"password": "   "}).status_code == 422
    assert env.client.post(f"/api/users/{made.json()['id']}/password", json={"password": "short"}).status_code == 200
    from app.core.security import verify_password
    with env.factory() as db:
        assert verify_password("short", db.get(User, made.json()["id"]).password_hash)


def test_the_sign_in_memo_is_emptied_after_the_commit_so_a_racing_request_cannot_keep_a_removed_person_signed_in(env, monkeypatch):
    """A request from the person's own open tab can refill the memo between the statement and the commit on a slow database."""
    real_commit = OrmSession.commit
    def refill_then_commit(self):
        deps._AUTH_CACHE["session-c"] = (time.monotonic(), datetime.now(timezone.utc) + timedelta(days=1), {})
        return real_commit(self)
    monkeypatch.setattr(OrmSession, "commit", refill_then_commit)
    act_as(env, "admin")
    assert env.client.delete("/api/users/c").status_code == 200
    assert "session-c" not in deps._AUTH_CACHE
    deps._AUTH_CACHE["session-d"] = (time.monotonic(), datetime.now(timezone.utc) + timedelta(days=1), {})
    assert env.client.post("/api/users/d/force-logout").status_code == 200
    assert "session-d" not in deps._AUTH_CACHE
    monkeypatch.undo()
    env.client.cookies.clear()
    env.client.cookies.set("crm_session", "session-c")
    with env.factory() as db:
        assert db.get(LoginSession, "session-c") is None


def test_report_staff_rows_and_pickers_skip_the_hidden_former_user_but_history_names_still_resolve():
    day = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
    users = [{"id": "a", "name": "A", "role": "staff", "active": True},
             {"id": "gone", "name": "Former user", "role": "staff", "active": False, "hidden": True},
             {"id": "admin", "name": "Admin", "role": "admin", "active": True}]
    data = {"users": users, "clients": [], "companies": [], "designations": [], "orders": [], "candidates": [], "applications": [],
            "stages": [], "tasks": [{"id": "t", "title": "Call", "candidate_id": None, "application_id": None, "stage": None, "owner_id": "gone",
                                     "created_at": day, "completed_at": day, "cancelled_at": None, "due_at": None, "sla_started_at": None,
                                     "escalated": False, "escalated_at": None}],
            "events": [], "remarks": [], "mailSources": []}
    from types import SimpleNamespace
    result = report_data(data, SimpleNamespace(id="admin", role=Role.admin), Filters(day.date(), day.date()), zone="UTC", now=day)
    assert [row["id"] for row in result["staff"]] == ["a"]
    assert [row["id"] for row in result["options"]["staff"]] == ["a", "admin"]
