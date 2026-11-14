"""Admin-only job (order) delete on the guarded disposable PostgreSQL database (migrated to head)."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers import candidates as candidates_route
from app.api.routers import data as route
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(candidates_route, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local"))
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id=role, name=role.title(), email=f"{role}@example.test", role=m.Role(role))
                    for role in ("admin", "manager", "staff")])
        db.add(m.Client(id="cl", name="Contact"))
        db.flush()
        db.add_all([m.Company(id="co", client_id="cl", name="Employer"), m.Designation(id="de", title="Welder")])
        db.flush()
        db.add_all([m.Order(id="o1", ref="ORD-1", company_id="co", designation_id="de", openings=2),
                    m.Order(id="o2", ref="ORD-2", company_id="co", designation_id="de", openings=2)])
        db.commit()

        def http(who="admin"):
            app = FastAPI()
            app.include_router(route.router)
            app.dependency_overrides[get_db] = lambda: db
            app.dependency_overrides[current_user] = lambda: db.get(m.User, who)
            return TestClient(app)

        yield SimpleNamespace(db=db, http=http, storage=tmp_path)


def candidate(db, key, **values):
    db.add(m.Candidate(id=key, ref=f"C-{key}", name=f"Person {key}", phone=f"90000{abs(hash(key)) % 100000:05d}",
                       source=m.Source.walk_in, owner_id="staff", **values))
    db.flush()
    return key


def application(db, key, candidate_id, order_id, status, stages=1):
    db.add(m.Application(id=key, candidate_id=candidate_id, order_id=order_id, status=status, owner_id="staff",
                         closure_reason=m.ClosureReason.unmapped if status is m.ApplicationStatus.closed else None))
    db.flush()
    for stage in range(1, stages + 1):
        db.add(m.ApplicationStage(application_id=key, stage=stage, data={}))
    db.flush()


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def audit(db, action):
    return db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == action))


def test_admin_deletes_a_job_with_no_candidates(env):
    env.db.add_all([m.TodoItem(id="todo", owner_id="staff", title="Call the employer", due_at=NOW, link_to="/orders/o1", link_label="ORD-1"),
                    m.Notification(user_id="staff", kind="job", title="New job", link_to="/orders/o1"),
                    m.Notification(user_id="staff", kind="job", title="Other job", link_to="/orders/o2")])
    env.db.commit()
    response = env.http().delete("/api/orders/o1")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": {"orders": 1, "applications": 0, "documents": 0}}
    env.db.expire_all()
    assert env.db.get(m.Order, "o1") is None and env.db.get(m.Order, "o2") is not None
    # The to-do stays without the dead link; notifications about the job go.
    todo = env.db.get(m.TodoItem, "todo")
    assert todo.title == "Call the employer" and todo.link_to is None and todo.link_label is None
    assert set(env.db.scalars(select(m.Notification.title))) == {"Other job"}
    entry = audit(env.db, "Deleted order")
    assert entry.actor_id == "admin" and entry.entity_type == "order" and entry.entity_id == "o1" and entry.target == "ORD-1"
    assert entry.meta == {"applications": 0, "candidates": [], "documents": 0}


@pytest.mark.parametrize("who", ["manager", "staff"])
def test_only_an_admin_can_delete_a_job(env, who):
    assert env.http(who).delete("/api/orders/o1").status_code == 403
    assert env.http(who).delete("/api/orders/o1?withdrawApplications=true").status_code == 403
    assert env.db.get(m.Order, "o1") is not None and count(env.db, m.AuditEntry) == 0


def test_missing_job_is_404(env):
    response = env.http().delete("/api/orders/nobody")
    assert response.status_code == 404
    assert count(env.db, m.AuditEntry) == 0


def test_a_job_with_candidates_is_refused_until_the_admin_confirms(env):
    db = env.db
    one, two = candidate(db, "one"), candidate(db, "two")
    application(db, "a-live", one, "o1", m.ApplicationStatus.active)
    application(db, "a-old", two, "o1", m.ApplicationStatus.closed)
    db.commit()
    for suffix in ("", "?withdrawApplications=false"):
        response = env.http().delete(f"/api/orders/o1{suffix}")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["message"].startswith("2 candidates are on ORD-1 (1 active).")
        assert detail["candidates"] == 2 and detail["active"] == 1 and detail["canWithdraw"] is True
        assert {item["candidate"] for item in detail["blockers"]} == {"Person one", "Person two"}
    assert db.get(m.Order, "o1") is not None and count(db, m.Application, m.Application.order_id == "o1") == 2
    assert count(db, m.AuditEntry, m.AuditEntry.action == "Deleted order") == 0


def test_a_job_with_only_closed_candidates_also_needs_confirmation(env):
    cid = candidate(env.db, "old")
    application(env.db, "a-old", cid, "o1", m.ApplicationStatus.closed)
    env.db.commit()
    response = env.http().delete("/api/orders/o1")
    assert response.status_code == 409
    assert response.json()["detail"]["message"].startswith("1 candidate is on ORD-1 (all closed).")
    assert env.http().delete("/api/orders/o1?withdrawApplications=true").status_code == 200
    env.db.expire_all()
    assert env.db.get(m.Order, "o1") is None and env.db.get(m.Candidate, cid) is not None
    assert count(env.db, m.Application, m.Application.order_id == "o1") == 0


def test_withdraw_flag_removes_the_applications_and_returns_candidates_to_the_data_bank(env, monkeypatch):
    db = env.db
    live, old, other = candidate(db, "live"), candidate(db, "old"), candidate(db, "other")
    db.get(m.Candidate, live).status = m.CandidateStatus.in_process
    application(db, "a-live", live, "o1", m.ApplicationStatus.active, stages=3)
    application(db, "a-old", old, "o1", m.ApplicationStatus.closed, stages=2)
    application(db, "a-other", other, "o2", m.ApplicationStatus.active)
    db.add_all([m.Task(id="t1", kind=m.TaskKind.stage, title="Stage", candidate_id=live, application_id="a-live", owner_id="staff"),
                m.Task(id="t-other", kind=m.TaskKind.stage, title="Stage", candidate_id=other, application_id="a-other", owner_id="staff")])
    db.flush()
    db.add_all([m.Notification(user_id="staff", kind="task", title="Stage task", link_to="/tasks?task=t1"),
                m.Notification(user_id="staff", kind="task", title="Other task", link_to="/tasks?task=t-other")])
    (env.storage / "proof.pdf").write_bytes(b"data")
    (env.storage / "keep.pdf").write_bytes(b"data")
    db.add_all([m.Attachment(entity_type="application", entity_id="a-live", slot="stage", file_name="proof.pdf", object_key="proof.pdf", uploaded_by="staff"),
                m.Attachment(entity_type="application", entity_id="a-other", slot="stage", file_name="keep.pdf", object_key="keep.pdf", uploaded_by="staff"),
                m.Attachment(entity_type="candidate", entity_id=live, slot="resume", file_name="resume.pdf", object_key="resume.pdf", uploaded_by="staff")])
    # An invoice still being prepared (no number, no payment) goes with the placement.
    db.add(m.CandidateInvoice(id="inv", candidate_id=live, application_id="a-live", scope_key="a-live", status="pending_fee"))
    db.get(m.Order, "o1").status = m.OrderStatus.on_hold
    db.commit()
    response = env.http().delete("/api/orders/o1?withdrawApplications=true")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": {"orders": 1, "applications": 2, "documents": 1}}
    db.expire_all()
    assert db.get(m.Order, "o1") is None
    assert count(db, m.Application, m.Application.order_id == "o1") == 0
    assert count(db, m.ApplicationStage, m.ApplicationStage.application_id.in_(["a-live", "a-old"])) == 0
    assert db.get(m.Task, "t1") is None and db.get(m.CandidateInvoice, "inv") is None
    # The candidates stay; the one who was active is back in the Data Bank.
    assert db.get(m.Candidate, live).status is m.CandidateStatus.new and db.get(m.Candidate, old) is not None
    # Other jobs, their placements, tasks and files are untouched; the candidate's own documents are kept.
    assert db.get(m.Order, "o2") is not None and db.get(m.Application, "a-other") is not None and db.get(m.Task, "t-other") is not None
    assert set(db.scalars(select(m.Notification.title))) == {"Other task"}
    assert count(db, m.Attachment, m.Attachment.entity_id == "a-live") == 0 and count(db, m.Attachment) == 2
    assert not (env.storage / "proof.pdf").exists() and (env.storage / "keep.pdf").exists()
    entry = audit(db, "Deleted order")
    assert entry.actor_id == "admin" and entry.entity_id == "o1" and entry.target == "ORD-1"
    assert entry.meta == {"applications": 2, "candidates": ["C-live", "C-old"], "documents": 1}
    trace = audit(db, "Removed from a deleted job")
    assert trace.entity_type == "candidate" and trace.entity_id == live and trace.meta == {"order": "ORD-1", "ref": "C-live"}


def test_a_deployed_placement_blocks_even_with_the_flag(env):
    cid = candidate(env.db, "placed")
    application(env.db, "a-dep", cid, "o1", m.ApplicationStatus.deployed, stages=11)
    env.db.commit()
    for suffix in ("", "?withdrawApplications=true"):
        response = env.http().delete(f"/api/orders/o1{suffix}")
        assert response.status_code == 409
        assert response.json()["detail"] == ("Can't delete ORD-1: Person placed was deployed through this job, and a deployed "
                                             "placement is kept on record. Close the job instead.")
    assert env.db.get(m.Order, "o1") is not None and env.db.get(m.Application, "a-dep") is not None
    assert count(env.db, m.AuditEntry) == 0


def test_an_issued_invoice_blocks_even_with_the_flag(env):
    cid = candidate(env.db, "billed")
    application(env.db, "a-live", cid, "o1", m.ApplicationStatus.active)
    env.db.add(m.CandidateInvoice(id="inv", candidate_id=cid, application_id="a-live", scope_key="a-live", service_fee=5000,
                                  number="TEST/009", status="pending_generation"))
    env.db.commit()
    response = env.http().delete("/api/orders/o1?withdrawApplications=true")
    assert response.status_code == 409
    assert response.json()["detail"] == ("Can't delete ORD-1: Person billed has an invoice or a recorded payment on this job. "
                                         "Financial records are kept, so close the job instead.")
    assert env.db.get(m.Order, "o1") is not None and env.db.get(m.Application, "a-live") is not None
    assert env.db.get(m.CandidateInvoice, "inv") is not None


def test_a_recorded_payment_blocks_a_closed_placement_too(env):
    cid = candidate(env.db, "paid")
    application(env.db, "a-old", cid, "o1", m.ApplicationStatus.closed)
    env.db.add(m.CandidateInvoice(id="inv", candidate_id=cid, application_id="a-old", scope_key="a-old", status="pending_fee"))
    env.db.flush()
    env.db.add(m.InvoicePayment(invoice_id="inv", amount=1000, paid_on=date(2026, 5, 1), method="cash", idempotency_key="k1"))
    env.db.commit()
    response = env.http().delete("/api/orders/o1?withdrawApplications=true")
    assert response.status_code == 409 and "recorded payment" in response.json()["detail"]
    assert env.db.get(m.Order, "o1") is not None and env.db.get(m.Application, "a-old") is not None


def test_a_deployment_on_another_job_does_not_block_this_one(env):
    cid = candidate(env.db, "two-jobs")
    application(env.db, "a-dep", cid, "o2", m.ApplicationStatus.deployed, stages=11)
    application(env.db, "a-old", cid, "o1", m.ApplicationStatus.closed)
    env.db.commit()
    # Deployment on ORD-2 does not stop ORD-1 from being deleted once the admin confirms.
    assert env.http().delete("/api/orders/o1?withdrawApplications=true").status_code == 200
    env.db.expire_all()
    assert env.db.get(m.Order, "o1") is None and env.db.get(m.Application, "a-dep") is not None
