"""The admin reallocates a lead or a candidate to another staff member and can make them "not allocated" again.

Leads: POST /api/leads/assign and the bulk "assign" action (ownerId "none" = unassigned). Candidates: POST /api/candidates/reassign
(already existed) and POST /api/candidates/unassign. All people are synthetic. Needs TEST_DATABASE_URL like the other DB tests.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.routers import candidates, data, files, leads, lead_bulk as lead_bulk_router
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from lead_sheet_fixtures import meta_export, meta_rows
from test_database_naming import _migrate, schema_database

FOLLOW_UP = "2031-01-01T10:00:00+00:00"


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha", email="a@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="b", name="Ravi", email="b@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="away", name="Away", email="away@example.test", role=m.Role.staff, receives_auto_assign=True, on_leave=True)])
        db.flush()
        db.add(m.Client(id="client", name="Contact"))
        db.flush()
        db.add(m.Company(id="company", client_id="client", name="Employer"))
        db.add(m.Designation(id="designation", title="Welder"))
        db.flush()
        db.add(m.Order(id="order", ref="ORD-1", company_id="company", designation_id="designation", openings=3))
        db.commit()
        app = FastAPI()
        for router in (leads.router, lead_bulk_router.router, files.router, candidates.router, data.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who)


def act_as(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()


def make_leads(env, count=3, assign_to="a"):
    act_as(env, "admin")
    response = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export(meta_rows(0, count)), "text/csv")},
                               data={"dry_run": "false", **({"assign_to": assign_to} if assign_to else {})})
    assert response.status_code == 200, response.text
    return [row[0] for row in env.db.execute(text("SELECT id FROM crm_leads ORDER BY import_row"))]


def visible_to(env, user_id, **params):
    act_as(env, user_id)
    listing = env.client.get("/api/leads", params={"page_size": 100, **params}).json()
    return {row["id"] for row in listing["items"]}


def follow_up_todos(env, lead_id):
    env.db.expire_all()
    return list(env.db.scalars(select(m.TodoItem).where(m.TodoItem.link_to == f"/leads?lead={lead_id}", m.TodoItem.done.is_(False))))


def activities(env, lead_id):
    env.db.expire_all()
    return [row.summary for row in env.db.scalars(select(m.LeadActivity).where(m.LeadActivity.lead_id == lead_id, m.LeadActivity.kind == "assign")
                                                   .order_by(m.LeadActivity.created_at, m.LeadActivity.id))]


def audits(env, action):
    env.db.expire_all()
    return list(env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == action)))


# ------------------------------------------------------------------ leads: reallocate

def test_admin_reallocates_an_already_assigned_lead(env):
    ids = make_leads(env, 2)
    act_as(env, "admin")
    assert env.client.post(f"/api/leads/{ids[0]}/call", json={"outcome": "interested", "nextFollowUpAt": FOLLOW_UP}).status_code == 200
    assert [t.owner_id for t in follow_up_todos(env, ids[0])] == ["a"]
    assert visible_to(env, "a") == set(ids) and visible_to(env, "b") == set()

    act_as(env, "admin")
    moved = env.client.post("/api/leads/assign", json={"ids": [ids[0]], "ownerId": "b"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["assigned"] == 1 and moved.json()["owners"] == [{"id": "b", "name": "Ravi", "count": 1}]

    # The previous owner loses it (and cannot even open it); the new one has it; the other lead stays put.
    assert visible_to(env, "a") == {ids[1]} and visible_to(env, "b") == {ids[0]}
    act_as(env, "a")
    assert env.client.get(f"/api/leads/{ids[0]}").status_code == 404
    env.db.expire_all()
    lead = env.db.get(m.Lead, ids[0])
    assert lead.owner_id == "b" and lead.assigned_at is not None and lead.status == "interested"
    # History and audit say who moved it from whom to whom; the follow-up to-do moved to the new owner.
    assert activities(env, ids[0]) == ["Assigned to Ravi"]
    entry = audits(env, "Lead reassigned")[0]
    assert (entry.actor_id, entry.entity_id, entry.meta["fromUserId"], entry.meta["toUserId"]) == ("admin", ids[0], "a", "b")
    assert [t.owner_id for t in follow_up_todos(env, ids[0])] == ["b"]
    # Only the new owner is told.
    told = {n.user_id for n in env.db.scalars(select(m.Notification).where(m.Notification.title == "New leads assigned"))}
    assert told == {"a", "b"}  # "a" from the first allocation at import time
    assert env.db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.user_id == "a")) == 1


def test_reallocation_target_must_be_an_active_staff_member(env):
    ids = make_leads(env, 1)
    act_as(env, "admin")
    for bad in ("away", "manager", "missing"):
        assert env.client.post("/api/leads/assign", json={"ids": ids, "ownerId": bad}).status_code == 422, bad
    env.db.expire_all()
    assert env.db.get(m.Lead, ids[0]).owner_id == "a"


# ------------------------------------------------------------------ leads: make unassigned

def test_admin_unassigns_a_lead_and_only_the_admin_sees_it_afterwards(env):
    ids = make_leads(env, 3)
    act_as(env, "admin")
    assert env.client.post(f"/api/leads/{ids[0]}/call", json={"outcome": "interested", "nextFollowUpAt": FOLLOW_UP}).status_code == 200
    assert len(follow_up_todos(env, ids[0])) == 1
    before = env.db.scalar(select(func.count()).select_from(m.Notification))

    act_as(env, "admin")
    result = env.client.post("/api/leads/assign", json={"ids": [ids[0]], "ownerId": "none"})
    assert result.status_code == 200, result.text
    assert result.json() == {"assigned": 0, "unassigned": 1, "skipped": 0, "owners": []}

    env.db.expire_all()
    lead = env.db.get(m.Lead, ids[0])
    assert lead.owner_id is None and lead.assigned_at is None and lead.status == "interested"
    assert activities(env, ids[0])[-1] == "Unassigned from Asha"
    entry = audits(env, "Lead unassigned")[0]
    assert (entry.actor_id, entry.entity_id, entry.meta["fromUserId"], entry.meta["toUserId"]) == ("admin", ids[0], "a", None)
    # The old owner's follow-up to-do is gone, and nobody is notified (a reassignment does not tell the old owner either).
    assert follow_up_todos(env, ids[0]) == []
    assert env.db.scalar(select(func.count()).select_from(m.Notification)) == before
    # Visibility: the admin finds it under "Unassigned"; the old owner and everyone else no longer see it.
    assert visible_to(env, "admin", owner="none") == {ids[0]}
    assert visible_to(env, "a") == set(ids[1:]) and visible_to(env, "b") == set() and visible_to(env, "manager") == set()
    for who in ("a", "b", "manager"):
        act_as(env, who)
        assert env.client.get(f"/api/leads/{ids[0]}").status_code == 404, who
    # Doing it again is a skip, not an error; and the admin can hand it out again.
    act_as(env, "admin")
    again = env.client.post("/api/leads/assign", json={"ids": [ids[0]], "ownerId": "none"}).json()
    assert (again["unassigned"], again["skipped"]) == (0, 1)
    assert env.client.post("/api/leads/assign", json={"ids": [ids[0]], "ownerId": "b"}).json()["assigned"] == 1
    assert visible_to(env, "b") == {ids[0]}


def test_unassign_by_filter_skips_converted_leads(env):
    ids = make_leads(env, 3)
    env.db.execute(text("UPDATE crm_leads SET status = 'converted' WHERE id = :i"), {"i": ids[2]})
    env.db.commit()
    act_as(env, "admin")
    result = env.client.post("/api/leads/assign", json={"filter": {"owner": "a"}, "ownerId": "none"}).json()
    assert (result["unassigned"], result["assigned"]) == (2, 0)
    env.db.expire_all()
    assert env.db.get(m.Lead, ids[2]).owner_id == "a"
    assert {env.db.get(m.Lead, i).owner_id for i in ids[:2]} == {None}


def test_only_the_admin_can_reallocate_or_unassign_leads(env):
    ids = make_leads(env, 1)
    for who in ("manager", "a", "b"):
        act_as(env, who)
        for owner in ("none", "b"):
            assert env.client.post("/api/leads/assign", json={"ids": ids, "ownerId": owner}).status_code == 403, (who, owner)
            assert env.client.post("/api/leads/bulk", json={"action": "assign", "ids": ids, "ownerId": owner}).status_code == 403, (who, owner)
    env.db.expire_all()
    assert env.db.get(m.Lead, ids[0]).owner_id == "a"


def test_bulk_reallocate_and_unassign_report_what_was_skipped(env):
    ids = make_leads(env, 4)
    env.db.execute(text("UPDATE crm_leads SET owner_id = NULL WHERE id = :i"), {"i": ids[3]})
    env.db.execute(text("UPDATE crm_leads SET status = 'converted' WHERE id = :i"), {"i": ids[2]})
    env.db.commit()
    act_as(env, "admin")
    moved = env.client.post("/api/leads/bulk", json={"action": "assign", "ids": ids, "ownerId": "b"}).json()
    assert (moved["updated"], moved["skipped"]) == (3, 1) and {r["code"] for r in moved["skippedReasons"]} == {"converted"}
    assert visible_to(env, "b") == {ids[0], ids[1], ids[3]} and visible_to(env, "a") == {ids[2]}
    act_as(env, "admin")
    freed = env.client.post("/api/leads/bulk", json={"action": "assign", "ids": ids, "ownerId": "none"}).json()
    assert (freed["updated"], freed["unassigned"], freed["assigned"], freed["skipped"]) == (3, 3, 0, 1)
    assert [r["code"] for r in freed["skippedReasons"]] == ["converted"]
    again = env.client.post("/api/leads/bulk", json={"action": "assign", "ids": ids[:2], "ownerId": "none"})
    assert again.status_code == 200 and again.json()["updated"] == 0
    assert again.json()["skippedReasons"][0]["code"] == "already_unassigned"
    assert visible_to(env, "admin", owner="none") == {ids[0], ids[1], ids[3]}
    assert activities(env, ids[0]) == ["Assigned to Ravi", "Unassigned from Ravi"]
    assert len(audits(env, "Lead unassigned")) == 3 and len(audits(env, "Lead reassigned")) == 2
    summary = audits(env, "Bulk lead unassign")
    assert len(summary) == 1 and summary[0].meta == {"count": 3}


# ------------------------------------------------------------------ candidates

def add_candidate(env, n, owner="a", **extra):
    candidate = m.Candidate(id=f"cand-{n}", ref=f"CND-{n:05d}", name=f"Person {n}", phone=f"90000000{n:02d}", source=m.Source.other, owner_id=owner, **extra)
    env.db.add(candidate)
    env.db.flush()
    return candidate


def add_task(env, candidate, kind=m.TaskKind.initial_call, **extra):
    now = datetime.now(timezone.utc)
    task = m.Task(kind=kind, title="Initial call: confirm interest" if kind == m.TaskKind.initial_call else "Stage work", candidate_id=candidate.id,
                  owner_id=candidate.owner_id, created_at=now, sla_started_at=now, due_at=now + timedelta(hours=48), **extra)
    env.db.add(task)
    env.db.flush()
    return task


def reassign(env, ids, to, reason="Workload"):
    return env.client.post("/api/candidates/reassign", json={"candidateIds": ids, "toUserId": to, "reason": reason})


def test_admin_reallocates_an_assigned_candidate_with_the_open_work(env):
    candidate = add_candidate(env, 1, next_follow_up_date=datetime(2031, 1, 1).date())
    task = add_task(env, candidate)
    env.db.commit()
    act_as(env, "admin")
    response = reassign(env, [candidate.id], "b")
    assert response.status_code == 200, response.text
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "b" and env.db.get(m.Task, task.id).owner_id == "b"
    entry = audits(env, "Candidate reassigned")[0]
    assert (entry.meta["fromUserId"], entry.meta["toUserId"]) == ("a", "b")
    told = {n.user_id for n in env.db.scalars(select(m.Notification).where(m.Notification.title == "Candidate assigned"))}
    assert told == {"b", "admin"}
    # And back to the first person, as often as needed.
    assert reassign(env, [candidate.id], "a").status_code == 200
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "a"


def test_admin_unassigns_a_candidate_who_then_waits_for_a_recruiter(env):
    candidate = add_candidate(env, 1, next_follow_up_date=datetime(2031, 1, 1).date())
    task = add_task(env, candidate)
    env.db.add(m.TodoItem(owner_id="a", title="Follow up: Person 1", due_at=datetime(2031, 1, 1, 9, tzinfo=timezone.utc), priority=m.Priority.normal,
                          link_to=f"/candidates/{candidate.id}", link_label="Follow-up"))
    env.db.commit()
    task_id, candidate_id = task.id, candidate.id
    act_as(env, "admin")
    assert env.client.get("/api/candidates/pool-counts").json()["unassigned"] == 0
    response = env.client.post("/api/candidates/unassign", json={"candidateIds": [candidate_id]})
    assert response.status_code == 200, response.text
    assert response.json() == {"count": 1, "unassigned": 1, "alreadyUnassigned": 0, "refused": []}
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate_id).owner_id is None
    assert env.db.get(m.Candidate, candidate_id).intake_owner_id in (None, "a")  # intake history is not rewritten
    # The old recruiter's untouched initial call and the follow-up reminder are gone.
    assert env.db.get(m.Task, task_id) is None
    assert env.db.scalar(select(func.count()).select_from(m.TodoItem).where(m.TodoItem.link_to == f"/candidates/{candidate_id}")) == 0
    entry = audits(env, "Candidate unassigned")[0]
    assert (entry.actor_id, entry.entity_id, entry.meta["fromUserId"], entry.meta["toUserId"]) == ("admin", candidate_id, "a", None)
    assert env.db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.user_id == "a")) == 0
    history = env.client.get(f"/api/candidates/{candidate_id}/assignment-history").json()["items"]
    assert (history[0]["action"], history[0]["fromName"], history[0]["toName"]) == ("Candidate unassigned", "Asha", "Unassigned")
    # It is in the "Not assigned" pool again.
    assert env.client.get("/api/candidates/pool-counts").json()["unassigned"] == 1
    listing = env.client.get("/api/candidates", params={"pool": "unassigned"}).json()
    assert [row["id"] for row in listing["items"]] == [candidate_id] and listing["items"][0]["ownerId"] is None
    # Doing it twice is harmless.
    again = env.client.post("/api/candidates/unassign", json={"candidateIds": [candidate_id]})
    assert again.status_code == 200 and again.json()["unassigned"] == 0 and again.json()["alreadyUnassigned"] == 1
    # The admin's "Assign waiting candidates" hands it out again, with a fresh initial call for the new recruiter.
    swept = env.client.post("/api/candidates/assign-waiting").json()
    assert swept["assigned"] == 1 and swept["remaining"] == 0
    env.db.expire_all()
    owner = env.db.get(m.Candidate, candidate_id).owner_id
    assert owner in ("a", "b")
    fresh = env.db.scalars(select(m.Task).where(m.Task.candidate_id == candidate_id, m.Task.completed_at.is_(None))).all()
    assert len(fresh) == 1 and fresh[0].owner_id == owner and fresh[0].kind == m.TaskKind.initial_call


def test_unassign_keeps_the_history_of_an_initial_call_that_already_has_remarks(env):
    candidate = add_candidate(env, 1)
    task = add_task(env, candidate)
    env.db.add(m.Remark(task_id=task.id, body="Called, will think", author_id="a"))
    env.db.commit()
    task_id = task.id
    act_as(env, "admin")
    assert env.client.post("/api/candidates/unassign", json={"candidateIds": [candidate.id]}).status_code == 200
    env.db.expire_all()
    kept = env.db.get(m.Task, task_id)
    assert kept is not None and kept.cancelled_at is not None
    assert env.db.scalar(select(func.count()).select_from(m.Remark).where(m.Remark.task_id == task_id)) == 1


def test_a_candidate_with_work_on_an_order_cannot_be_unassigned(env):
    mapped = add_candidate(env, 1, status=m.CandidateStatus.in_process)
    application = m.Application(candidate_id=mapped.id, order_id="order", owner_id="a", current_stage=2, status=m.ApplicationStatus.active, lock_version=0, draft={})
    env.db.add(application)
    env.db.flush()
    add_task(env, mapped, m.TaskKind.stage, application_id=application.id, stage=2)
    stage_only = add_candidate(env, 2)
    add_task(env, stage_only, m.TaskKind.stage)
    deployed = add_candidate(env, 3, status=m.CandidateStatus.deployed)
    free = add_candidate(env, 4)
    env.db.commit()
    act_as(env, "admin")
    one = env.client.post("/api/candidates/unassign", json={"candidateIds": [mapped.id]})
    assert one.status_code == 409 and "ORD-1" in one.json()["detail"] and "reassign them to another recruiter" in one.json()["detail"]
    assert env.client.post("/api/candidates/unassign", json={"candidateIds": [stage_only.id]}).status_code == 409
    assert env.client.post("/api/candidates/unassign", json={"candidateIds": [deployed.id]}).status_code == 409
    # In a selection, the ones that may be freed are freed and the refused ones come back with their reasons.
    mixed = env.client.post("/api/candidates/unassign", json={"candidateIds": [mapped.id, deployed.id, free.id]})
    assert mixed.status_code == 200, mixed.text
    body = mixed.json()
    assert body["unassigned"] == 1 and {r["id"] for r in body["refused"]} == {mapped.id, deployed.id}
    env.db.expire_all()
    assert env.db.get(m.Candidate, free.id).owner_id is None
    assert {env.db.get(m.Candidate, i).owner_id for i in (mapped.id, deployed.id, stage_only.id)} == {"a"}
    assert env.client.post("/api/candidates/unassign", json={"candidateIds": []}).status_code == 422
    assert env.client.post("/api/candidates/unassign", json={"candidateIds": ["nobody"]}).status_code == 404
    # The mapped candidate can still be handed to another recruiter instead.
    assert reassign(env, [mapped.id], "b").status_code == 200


def test_only_the_admin_can_unassign_candidates(env):
    candidate = add_candidate(env, 1)
    env.db.commit()
    for who in ("manager", "a", "b"):
        act_as(env, who)
        assert env.client.post("/api/candidates/unassign", json={"candidateIds": [candidate.id]}).status_code == 403, who
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "a"


def test_unassign_keeps_a_task_with_sla_events_or_a_recorded_call_and_deletes_only_an_untouched_one(env):
    untouched, with_event, called = add_candidate(env, 1), add_candidate(env, 2), add_candidate(env, 3, call_recorded_at=datetime.now(timezone.utc))
    tasks = {c.id: add_task(env, c) for c in (untouched, with_event, called)}
    env.db.add(m.TaskEvent(task_id=tasks[with_event.id].id, candidate_id=with_event.id, owner_id="a", kind="reminder",
                           event_key="review-fix-reminder", occurred_at=datetime.now(timezone.utc)))
    env.db.commit()
    task_ids = {cid: t.id for cid, t in tasks.items()}
    act_as(env, "admin")
    response = env.client.post("/api/candidates/unassign", json={"candidateIds": list(task_ids)})
    assert response.status_code == 200 and response.json()["unassigned"] == 3, response.text
    env.db.expire_all()
    assert env.db.get(m.Task, task_ids[untouched.id]) is None
    for candidate in (with_event, called):
        kept = env.db.get(m.Task, task_ids[candidate.id])
        assert kept is not None and kept.cancelled_at is not None, candidate.id
    assert env.db.scalar(select(func.count()).select_from(m.TaskEvent).where(m.TaskEvent.task_id == task_ids[with_event.id])) == 1


def test_unassign_checks_a_whole_selection_in_a_fixed_number_of_statements(env):
    from sqlalchemy import event
    ids = [add_candidate(env, n).id for n in range(1, 9)]
    for n in range(1, 9):
        add_task(env, env.db.get(m.Candidate, f"cand-{n}"))
    env.db.commit()
    act_as(env, "admin")
    seen = []
    def watch(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    event.listen(env.db.get_bind(), "before_cursor_execute", watch)
    try:
        response = env.client.post("/api/candidates/unassign", json={"candidateIds": ids})
    finally:
        event.remove(env.db.get_bind(), "before_cursor_execute", watch)
    assert response.status_code == 200 and response.json()["unassigned"] == 8
    assert len(seen) < 25, f"{len(seen)} statements for 8 candidates"


def test_a_manager_cannot_reassign_or_sweep_candidates(env):
    candidate = add_candidate(env, 1)
    env.db.commit()
    act_as(env, "manager")
    assert reassign(env, [candidate.id], "b").status_code == 403
    assert env.client.post("/api/candidates/assign-waiting").status_code == 403
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "a"
