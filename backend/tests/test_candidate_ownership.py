"""Who may work a candidate: the assigned recruiter, a manager or an admin; every other staff member can only look.

Covers the rule at each door (details, documents, initial call, stages, application form, order matching), the
uploader becoming the owner, the admin's recruiter choice, duplicate handling and reassignment.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers import application_form, applications, candidates, data, files, workflow
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import daily_work
from test_database_naming import _migrate, schema_database
from auto_assign import round_robin_on  # noqa: F401  (fixture: these tests exercise the round-robin, which is opt-in)
import pytest as _pytest

pytestmark = _pytest.mark.usefixtures("round_robin_on")

PDF = b"%PDF-1.7 synthetic document"


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    monkeypatch.setattr(applications, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path)))
    monkeypatch.setattr(workflow, "get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
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
        for router in (candidates.router, files.router, data.router, applications.router, application_form.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who)


def act_as(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()


def add_candidate(env, who="a", phone="9000000001", **extra):
    act_as(env, who)
    response = env.client.post("/api/candidates", json={"name": "Synthetic candidate", "phone": phone, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def refused(response, owner="Asha"):
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert f"assigned to {owner}" in detail and "Only they, a manager or an admin can work on it" in detail


# --------------------------------------------------------------------------- uploader becomes owner

def test_a_staff_member_who_adds_a_candidate_is_the_owner_and_the_round_robin_is_untouched(env):
    created = add_candidate(env, "b")
    candidate = env.db.get(m.Candidate, created["id"])
    assert created["ownerId"] == "b" and created["ownerName"] == "Ravi"
    assert candidate.owner_id == candidate.intake_owner_id == candidate.created_by == "b"
    assert env.db.get(m.Task, created["taskId"]).owner_id == "b"
    pointer = env.db.get(m.AssignmentPointer, 1)
    assert pointer is None or pointer.last_user_id is None
    # Admin and the owner are told, nobody else.
    assert set(env.db.scalars(select(m.Notification.user_id))) == {"b", "admin"}


def test_staff_cannot_name_another_recruiter_but_may_name_themselves(env):
    act_as(env, "a")
    other = env.client.post("/api/candidates", json={"name": "X", "phone": "9000000002", "assign_to": "b"})
    assert other.status_code == 403 and "assigned to you" in other.json()["detail"]
    assert env.db.scalar(select(func.count()).select_from(m.Candidate)) == 0
    own = env.client.post("/api/candidates", json={"name": "X", "phone": "9000000002", "assign_to": "a"})
    assert own.status_code == 201 and own.json()["ownerId"] == "a"


def test_only_the_admin_keeps_the_recruiter_choice(env):
    automatic = add_candidate(env, "admin", "9000000003")
    assert automatic["ownerId"] == "a"  # strict round-robin: the first eligible recruiter
    assert add_candidate(env, "admin", "9000000004")["ownerId"] == "b"
    chosen = add_candidate(env, "admin", "9000000005", assign_to="a")
    assert chosen["ownerId"] == "a"
    # A manager adds a candidate like a recruiter does: it is theirs, they cannot hand it to someone else.
    act_as(env, "manager")
    other = env.client.post("/api/candidates", json={"name": "X", "phone": "9000000006", "assign_to": "b"})
    assert other.status_code == 403 and "Only the admin" in other.json()["detail"]
    own = add_candidate(env, "manager", "9000000008")
    assert own["ownerId"] == "manager" and env.db.get(m.Candidate, own["id"]).created_by == "manager"
    # The round-robin did not move for the explicit choice or the manager's own add: the next automatic one follows the last automatic one.
    assert add_candidate(env, "admin", "9000000007")["ownerId"] == "a"
    act_as(env, "admin")
    for bad in ("away", "manager", "missing"):
        response = env.client.post("/api/candidates", json={"name": "Y", "phone": "9000000099", "assign_to": bad})
        assert response.status_code == 422, (bad, response.text)


# --------------------------------------------------------------------------- the rule at every door

def test_only_owner_manager_and_admin_can_change_details_record_the_call_and_upload(env):
    created = add_candidate(env, "a")
    cid = created["id"]
    act_as(env, "b")
    refused(env.client.patch(f"/api/candidates/{cid}", json={"name": "Changed", "phone": "9000000001"}))
    refused(env.client.post(f"/api/candidates/{cid}/call", json={"called": True, "interested": "yes"}))
    refused(env.client.post("/api/files", data={"entity_type": "candidate", "entity_id": cid, "slot": "education"},
                            files={"file": ("cert.pdf", PDF, "application/pdf")}))
    assert env.db.scalar(select(func.count()).select_from(m.Attachment)) == 0
    assert env.db.get(m.Candidate, cid).name == "Synthetic candidate" and env.db.get(m.Candidate, cid).call_recorded_at is None
    for who in ("a", "manager", "admin"):
        act_as(env, who)
        assert env.client.patch(f"/api/candidates/{cid}", json={"name": "Changed", "phone": "9000000001"}).status_code == 200, who
        upload = env.client.post("/api/files", data={"entity_type": "candidate", "entity_id": cid, "slot": "education"},
                                 files={"file": ("cert.pdf", PDF, "application/pdf")})
        assert upload.status_code == 201, (who, upload.text)
    act_as(env, "a")
    assert env.client.post(f"/api/candidates/{cid}/call", json={"called": True, "interested": "yes"}).status_code == 200
    assert env.db.scalar(select(func.count()).select_from(m.Attachment)) == 3


def test_the_person_who_entered_a_candidate_cannot_keep_working_it_after_it_is_given_away(env):
    created = add_candidate(env, "a")
    act_as(env, "admin")
    assert env.client.post("/api/candidates/reassign", json={"candidateIds": [created["id"]], "toUserId": "b", "reason": "Workload"}).status_code == 200
    act_as(env, "a")
    refused(env.client.post("/api/files", data={"entity_type": "candidate", "entity_id": created["id"], "slot": "education"},
                            files={"file": ("cert.pdf", PDF, "application/pdf")}), owner="Ravi")
    refused(env.client.patch(f"/api/candidates/{created['id']}", json={"name": "Mine", "phone": "9000000001"}), owner="Ravi")
    act_as(env, "b")
    assert env.client.patch(f"/api/candidates/{created['id']}", json={"name": "Theirs now", "phone": "9000000001"}).status_code == 200


def test_everyone_can_still_read_the_candidate(env):
    created = add_candidate(env, "a")
    for who in ("b", "manager", "admin"):
        act_as(env, who)
        assert env.client.get(f"/api/candidates/{created['id']}").status_code == 200
    act_as(env, "b")
    form = env.client.get(f"/api/candidates/{created['id']}/application-form")
    assert form.status_code == 200 and form.json()["candidate"]["canEdit"] is False
    act_as(env, "a")
    assert env.client.get(f"/api/candidates/{created['id']}/application-form").json()["candidate"]["canEdit"] is True


def test_application_form_save_and_documents_follow_the_same_rule(env):
    cid = add_candidate(env, "a")["id"]
    act_as(env, "b")
    refused(env.client.put(f"/api/candidates/{cid}/application-form", json={"personal": {"fatherName": "Someone"}}))
    refused(env.client.post(f"/api/candidates/{cid}/application-form/documents", data={"slot": "photo"},
                            files={"file": ("photo.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")}))
    refused(env.client.post(f"/api/candidates/{cid}/application-form/refresh"))
    assert env.db.get(m.Candidate, cid).father_name is None
    assert env.db.scalar(select(func.count()).select_from(m.Attachment)) == 0
    for who in ("a", "admin"):
        act_as(env, who)
        assert env.client.put(f"/api/candidates/{cid}/application-form", json={"personal": {"fatherName": "Someone"}}).status_code == 200, who
    act_as(env, "admin")
    photo = env.client.post(f"/api/candidates/{cid}/application-form/documents", data={"slot": "photo"},
                            files={"file": ("photo.png", b"\x89PNG\r\n\x1a\nsynthetic", "image/png")})
    assert photo.status_code == 202, photo.text


def test_stage_work_is_the_owners_and_unmapping_and_matching_follow_it(env):
    cid = add_candidate(env, "a")["id"]
    # Matching starts the stage work: another recruiter cannot put someone else's candidate on an order.
    act_as(env, "b")
    refused(env.client.post("/api/applications", json={"candidate_id": cid, "order_id": "order"}))
    act_as(env, "a")
    mapped = env.client.post("/api/applications", json={"candidate_id": cid, "order_id": "order"})
    assert mapped.status_code == 201, mapped.text
    app_id = mapped.json()["id"]
    lock = env.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]
    act_as(env, "b")
    refused(env.client.get(f"/api/applications/{app_id}/draft"))
    refused(env.client.put(f"/api/applications/{app_id}/draft", json={"data": {"remarks": "x"}, "lock_version": lock}))
    refused(env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": lock}))
    refused(env.client.post(f"/api/applications/{app_id}/unmap", json={"lock_version": lock}))
    # Reopening a finished stage is an admin action for everyone else.
    assert env.client.post(f"/api/applications/{app_id}/reopen", json={"stage": 1, "reason": "x", "lock_version": lock}).status_code == 403
    for who in ("manager", "a"):
        act_as(env, who)
        assert env.client.put(f"/api/applications/{app_id}/draft", json={"data": {"remarks": "ok"}, "lock_version": env.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]}).status_code == 200, who
    act_as(env, "a")
    refused_reopen = env.client.post(f"/api/applications/{app_id}/reopen", json={"stage": 1, "reason": "x", "lock_version": 0})
    assert refused_reopen.status_code == 403  # even the owner cannot reopen: admin only
    act_as(env, "admin")
    assert env.client.post(f"/api/applications/{app_id}/unmap", json={}).status_code == 200


# --------------------------------------------------------------------------- duplicates

def test_a_duplicate_of_someone_elses_candidate_names_the_owner_and_cannot_be_combined(env):
    original = add_candidate(env, "a")
    act_as(env, "b")
    again = env.client.post("/api/candidates", json={"name": "Typed by B", "phone": "9000000001", "email": "b@example.test"})
    assert again.status_code == 409, again.text
    card = again.json()["detail"]["candidate"]
    assert card["id"] == original["id"] and card["ownerName"] == "Asha" and card["canMerge"] is False
    check = env.client.post("/api/candidates/check-duplicate", json={"phone": "9000000001"}).json()
    assert check["duplicate"] is True and check["candidate"]["canMerge"] is False and check["candidate"]["ownerName"] == "Asha"
    refused(env.client.post(f"/api/candidates/{original['id']}/merge", json={"name": "Typed by B", "phone": "9000000001", "email": "b@example.test"}))
    assert env.db.get(m.Candidate, original["id"]).email is None
    assert env.db.scalar(select(func.count()).select_from(m.Candidate)) == 1
    # The owner, a manager and an admin are offered the combine.
    for who in ("a", "manager", "admin"):
        act_as(env, who)
        check = env.client.post("/api/candidates/check-duplicate", json={"phone": "9000000001"}).json()
        assert check["candidate"]["canMerge"] is True, who
    assert env.client.post(f"/api/candidates/{original['id']}/merge", json={"name": "x", "phone": "9000000001", "email": "admin@example.test"}).status_code == 200


# --------------------------------------------------------------------------- reassignment

def test_admin_reassignment_moves_the_work_the_notices_and_the_reminder(env):
    created = add_candidate(env, "a", next_follow_up_date="2031-01-01")
    cid = created["id"]
    candidate = env.db.get(m.Candidate, cid)
    today = datetime.now(timezone.utc).date()
    env.db.add(m.DailyWorkItem(owner_id="a", work_date=today, kind="call", candidate_id=cid, slot_no=1, session="morning",
                               due_at=datetime.now(timezone.utc) + timedelta(hours=2), status="open"))
    env.db.commit()
    link = f"/candidates/{cid}"
    assert env.db.scalar(select(m.TodoItem.owner_id).where(m.TodoItem.link_to == link)) == "a"

    # Only the admin hands a candidate over: staff and managers cannot.
    for who in ("b", "manager"):
        act_as(env, who)
        assert env.client.post("/api/candidates/reassign", json={"candidateIds": [cid], "toUserId": "b", "reason": "Mine"}).status_code == 403, who
    act_as(env, "admin")
    response = env.client.post("/api/candidates/reassign", json={"candidateIds": [cid], "toUserId": "b", "reason": "Workload"})
    assert response.status_code == 200, response.text
    env.db.expire_all()
    assert env.db.get(m.Candidate, cid).owner_id == "b"
    task = env.db.get(m.Task, created["taskId"])
    assert task.owner_id == "b" and task.completed_at is None
    assert env.db.scalar(select(m.TodoItem.owner_id).where(m.TodoItem.link_to == link)) == "b"
    # The audit trail names who moved it, from whom, to whom and why.
    audit = env.db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Candidate reassigned", m.AuditEntry.entity_id == cid))
    assert audit.actor_id == "admin" and audit.meta["fromUserId"] == "a" and audit.meta["toUserId"] == "b" and audit.meta["reason"] == "Workload"
    # The new recruiter is told; the daily-work item of the old one is released with the reason "reassigned".
    assert env.db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.user_id == "b", m.Notification.link_to.like(f"%{cid}%"))) >= 1
    daily_work.reconcile(env.db)
    env.db.commit()
    item = env.db.scalar(select(m.DailyWorkItem).where(m.DailyWorkItem.candidate_id == cid))
    assert (item.status, item.release_reason) == ("released", "reassigned")
    # The rule follows the new owner at once.
    act_as(env, "a")
    refused(env.client.post(f"/api/candidates/{cid}/call", json={"called": True, "interested": "yes"}), owner="Ravi")
    act_as(env, "b")
    assert env.client.post(f"/api/candidates/{cid}/call", json={"called": True, "interested": "yes"}).status_code == 200


def test_bulk_reassignment_is_the_admins_alone_and_moves_every_candidate(env):
    ids = [add_candidate(env, "a", f"90000001{n:02d}")["id"] for n in range(3)]
    act_as(env, "manager")
    assert env.client.post("/api/candidates/reassign", json={"candidateIds": ids, "toUserId": "b", "reason": "Cover"}).status_code == 403
    env.db.expire_all()
    assert {env.db.get(m.Candidate, i).owner_id for i in ids} == {"a"}
    act_as(env, "admin")
    assert env.client.post("/api/candidates/reassign", json={"candidateIds": ids, "toUserId": "b", "reason": "Cover"}).status_code == 200
    env.db.expire_all()
    assert {env.db.get(m.Candidate, i).owner_id for i in ids} == {"b"}
    assert env.db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == "Candidate reassigned")) == 3
    open_tasks = list(env.db.scalars(select(m.Task).where(m.Task.candidate_id.in_(ids), m.Task.completed_at.is_(None))))
    assert open_tasks and {t.owner_id for t in open_tasks} == {"b"}
    act_as(env, "admin")
    unavailable = env.client.post("/api/candidates/reassign", json={"candidateIds": ids, "toUserId": "away", "reason": "x"})
    assert unavailable.status_code == 422
