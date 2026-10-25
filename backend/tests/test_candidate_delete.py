"""Admin-only candidate delete on the guarded disposable PostgreSQL database (migrated to head)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers import candidates as route
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local"))
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id=role, name=role.title(), email=f"{role}@example.test", role=m.Role(role))
                    for role in ("admin", "manager", "staff")])
        client = m.Client(id="cl", name="Contact")
        db.add(client)
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


def stored_file(env, db, candidate_id, name, entity="candidate", entity_id=None):
    path = env.storage / name
    path.write_bytes(b"data")
    db.add(m.Attachment(entity_type=entity, entity_id=entity_id or candidate_id, slot=name.split(".")[0], file_name=name,
                        object_key=name, uploaded_by="staff"))
    db.flush()
    return path


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def seed_full_history(env):
    """A candidate with a closed application, stages, tasks, remarks, documents and links."""
    db = env.db
    cid = candidate(db, "gone")
    other = candidate(db, "other")
    application(db, "a-closed", cid, "o1", m.ApplicationStatus.closed, stages=2)
    application(db, "a-other", other, "o1", m.ApplicationStatus.active)
    db.add_all([m.Task(id="t1", kind=m.TaskKind.initial_call, title="Call", candidate_id=cid, owner_id="staff"),
                m.Task(id="t2", kind=m.TaskKind.stage, title="Stage", candidate_id=cid, application_id="a-closed", owner_id="staff"),
                m.Task(id="t-other", kind=m.TaskKind.initial_call, title="Call", candidate_id=other, owner_id="staff")])
    db.flush()
    db.add_all([m.Remark(task_id="t1", body="Called", author_id="staff"),
                m.TaskEvent(task_id="t2", candidate_id=cid, owner_id="staff", kind="reminder", event_key="k1"),
                m.Notification(user_id="staff", kind="candidate_assignment", title="New", link_to=f"/candidates/{cid}"),
                m.Notification(user_id="staff", kind="task", title="Task", link_to="/tasks?task=t2"),
                m.Notification(user_id="staff", kind="candidate_assignment", title="Keep", link_to=f"/candidates/{other}"),
                m.TodoItem(id="todo", owner_id="staff", title="Follow up", due_at=NOW, link_to=f"/candidates/{cid}", link_label="Person")])
    resume = stored_file(env, db, cid, "resume.pdf")
    receipt = stored_file(env, db, cid, "receipt.pdf", "application", "a-closed")
    kept = stored_file(env, db, other, "other.pdf")
    shared = stored_file(env, db, cid, "from-mail.pdf")
    account = m.MailAccount(id="acct", address="hr@example.test", provider="imap")
    db.add(account)
    db.flush()
    message = m.MailMessage(id="msg", account_id="acct", provider_message_id="p1", received_at=NOW, candidate_id=cid)
    db.add(message)
    db.flush()
    # The same stored file is also an attachment of the original email, which stays in the inbox.
    db.add(m.MailAttachment(message_id="msg", file_name="from-mail.pdf", object_key="from-mail.pdf"))
    db.add(m.DocumentExtractionJob(id="job", owner_id="staff", group_id="g", kind="resume", filename="resume.pdf",
                                   content_type="application/pdf", object_key="resume.pdf", content_hash="h", size_kb=1,
                                   candidate_id=cid, attachment_id=db.scalar(select(m.Attachment.id).where(m.Attachment.file_name == "resume.pdf", m.Attachment.entity_type == "candidate"))))
    db.commit()
    return SimpleNamespace(cid=cid, resume=resume, receipt=receipt, kept=kept, shared=shared)


def test_admin_deletes_candidate_with_closed_history_documents_and_tasks(env):
    seeded = seed_full_history(env)
    db = env.db
    response = env.http().delete(f"/api/candidates/{seeded.cid}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": {"candidates": 1, "documents": 3, "applications": 1}}
    db.expire_all()
    assert db.get(m.Candidate, seeded.cid) is None
    for model, column in ((m.Application, m.Application.candidate_id), (m.Task, m.Task.candidate_id),
                          (m.TaskEvent, m.TaskEvent.candidate_id)):
        assert count(db, model, column == seeded.cid) == 0
    assert count(db, m.ApplicationStage, m.ApplicationStage.application_id == "a-closed") == 0
    assert count(db, m.Remark, m.Remark.task_id == "t1") == 0 and count(db, m.TaskEvent, m.TaskEvent.task_id == "t2") == 0
    assert count(db, m.Attachment, m.Attachment.entity_id.in_([seeded.cid, "a-closed"])) == 0
    assert db.get(m.DocumentExtractionJob, "job") is None
    # Other people's rows, the mail itself and the order are untouched.
    assert db.get(m.Candidate, "other") and db.get(m.Application, "a-other") and db.get(m.Task, "t-other")
    assert count(db, m.Attachment, m.Attachment.entity_id == "other") == 1
    assert db.get(m.Order, "o1") is not None
    message = db.get(m.MailMessage, "msg")
    assert message is not None and message.candidate_id is None
    assert db.get(m.TodoItem, "todo").link_to is None and db.get(m.TodoItem, "todo").title == "Follow up"
    titles = set(db.scalars(select(m.Notification.title)))
    assert titles == {"Keep"}
    audit = db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Deleted candidate"))
    assert audit.actor_id == "admin" and audit.entity_id == seeded.cid and "Person gone" in audit.target
    assert audit.meta == {"ref": "C-gone", "documents": 3, "applications": 1, "tasks": 2}
    assert audit.at is not None
    # Stored files go, except the one the original email still uses and other people's files.
    assert not seeded.resume.exists() and not seeded.receipt.exists()
    assert seeded.shared.exists() and seeded.kept.exists()


@pytest.mark.parametrize("who", ["manager", "staff"])
def test_only_admin_can_delete(env, who):
    cid = candidate(env.db, "x")
    env.db.commit()
    response = env.http(who).delete(f"/api/candidates/{cid}")
    assert response.status_code == 403
    assert env.db.get(m.Candidate, cid) is not None
    assert count(env.db, m.AuditEntry, m.AuditEntry.action == "Deleted candidate") == 0


def test_candidate_on_an_active_order_cannot_be_deleted(env):
    cid = candidate(env.db, "busy")
    application(env.db, "a-old", cid, "o1", m.ApplicationStatus.closed)
    application(env.db, "a-live", cid, "o2", m.ApplicationStatus.active)
    env.db.commit()
    response = env.http().delete(f"/api/candidates/{cid}")
    assert response.status_code == 409
    assert response.json()["detail"] == "This candidate is still on an order. Remove them from the order first."
    assert env.db.get(m.Candidate, cid) is not None and count(env.db, m.Application, m.Application.candidate_id == cid) == 2


def test_deployed_candidate_cannot_be_deleted(env):
    cid = candidate(env.db, "placed")
    application(env.db, "a-dep", cid, "o1", m.ApplicationStatus.deployed, stages=11)
    env.db.commit()
    response = env.http().delete(f"/api/candidates/{cid}")
    assert response.status_code == 409
    assert response.json()["detail"] == ("This candidate was deployed through ORD-1, and a deployed placement is kept on record. "
                                         "They cannot be deleted.")
    assert env.db.get(m.Candidate, cid) is not None and env.db.get(m.Application, "a-dep") is not None
    assert count(env.db, m.AuditEntry, m.AuditEntry.action == "Deleted candidate") == 0


def test_missing_candidate_is_404(env):
    response = env.http().delete("/api/candidates/nobody")
    assert response.status_code == 404
    assert count(env.db, m.AuditEntry) == 0


def test_storage_failure_does_not_fail_or_undo_the_delete(env, monkeypatch):
    cid = candidate(env.db, "files")
    first = stored_file(env, env.db, cid, "one.pdf")
    stored_file(env, env.db, cid, "two.pdf")
    env.db.commit()
    attempted = []

    def broken(settings, key):
        attempted.append(key)
        if key == "one.pdf":
            raise OSError("storage is down")
        (env.storage / key).unlink()

    monkeypatch.setattr(route.document_storage, "delete_bytes", broken)
    response = env.http().delete(f"/api/candidates/{cid}")
    assert response.status_code == 200, response.text
    env.db.expire_all()
    assert env.db.get(m.Candidate, cid) is None and count(env.db, m.Attachment, m.Attachment.entity_id == cid) == 0
    assert sorted(attempted) == ["one.pdf", "two.pdf"]
    audit = env.db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Deleted candidate"))
    assert audit.meta["storageLeftover"] == ["one.pdf"] and audit.meta["documents"] == 2
    assert first.exists() and not (env.storage / "two.pdf").exists()


def test_round_robin_pointer_is_left_alone(env):
    env.db.add(m.AssignmentPointer(id=1, last_user_id="staff"))
    cid = candidate(env.db, "rr")
    env.db.commit()
    assert env.http().delete(f"/api/candidates/{cid}").status_code == 200
    env.db.expire_all()
    assert env.db.get(m.AssignmentPointer, 1).last_user_id == "staff"


def test_every_table_pointing_at_a_candidate_is_handled():
    """A new foreign key to candidates must be cascaded or cleared by the delete, never left to fail it."""
    cleared = {"email_messages", "whatsapp_messages", "whatsapp_conversations", "email_ingestion_jobs",
               "document_person_groups", "document_extraction_jobs"}
    for table in m.Base.metadata.tables.values():
        for key in table.foreign_keys:
            if key.column.table.name == "crm_candidates":
                assert key.ondelete in {"CASCADE", "SET NULL"} or table.name in cleared, table.name
