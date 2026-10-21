"""Application contracts against the explicitly guarded disposable PostgreSQL DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate,
    CandidateStatus, Client, Company, Designation, Order, OrderStatus, Role, Source,
    Task, TaskKind, User,
)
from test_database_naming import _migrate, schema_database

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
)):
    from app.api.routers import applications as subject


@pytest.fixture
def workflow(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key, email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("staff", Role.staff), ("other", Role.staff), ("manager", Role.manager))])
        db.add(Client(id="client", name="Synthetic contact"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Synthetic employer"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add_all([Order(id=key, ref=key, company_id="company", designation_id="designation", openings=2)
                    for key in ("order", "other-order")])
        db.add_all([Candidate(id=key, ref=key, name=key, phone=f"90000000{n:02}",
                              source=Source.walk_in, owner_id="staff")
                    for n, key in enumerate(("candidate", "second-candidate", "third-candidate"))])
        db.commit()
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path)))
    env = SimpleNamespace(sessions=sessions, storage=tmp_path, actor=SimpleNamespace(id="staff", role=Role.staff))
    app = FastAPI()
    app.include_router(subject.router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: env.actor
    with TestClient(app) as client:
        env.client = client
        yield env


def mapped(env, candidate="candidate", order="order"):
    response = env.client.post("/api/applications", json={"candidate_id": candidate, "order_id": order})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def attachment(env, app_id, stage, field="file", *, key=None, entity_type="application", entity_id=None, slot=None):
    with env.sessions() as db:
        row = Attachment(entity_type=entity_type, entity_id=entity_id or app_id,
                         slot=slot or f"stage_{stage}_{field}", version=1,
                         file_name="synthetic.pdf", content_type="application/pdf", object_key=key or f"{app_id}-{stage}-{field}.pdf")
        db.add(row)
        db.commit()
        (env.storage / row.object_key).write_bytes(b"%PDF-1.7 synthetic evidence")
        return {"id": row.id, "name": "forged filename.pdf", "url": "/wrong-url"}


def version(env, app_id):
    return env.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]


def save(env, app_id, data, *, lock=None):
    return env.client.put(f"/api/applications/{app_id}/draft", json={
        "data": data, "lock_version": version(env, app_id) if lock is None else lock,
    })


def complete(env, app_id, data):
    saved = save(env, app_id, data)
    assert saved.status_code == 200, saved.text
    response = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert response.status_code == 200, response.text
    return response.json()


def payload(env, app_id, stage):
    values = {
        1: {"result": "passed"}, 2: {"forwarded": "yes"},
        3: {"scheduled": "yes", "date": "2026-11-02", "time": "09:30", "mode": "online"},
        4: {"result": "selected", "remarks": "Client approved"},
        5: {"obtained": "yes"}, 6: {"applied": "yes", "result": "fit"},
        7: {"collected": "yes", "heldBy": {"id": "staff", "name": "spoofed"}},
        8: {"collected": "yes", "amount": "1500.25", "amountCurrency": "INR", "mode": "upi"},
        9: {"arrived": "yes", "googleReview": False}, 10: {"done": "yes"}, 11: {},
    }[stage]
    if stage in (5, 10, 11):
        values["file"] = attachment(env, app_id, stage)
    elif stage == 8:
        values["receipt"] = attachment(env, app_id, stage, "receipt")
    return values


def reach(env, app_id, stage):
    for number in range(1, stage):
        complete(env, app_id, payload(env, app_id, number))


def test_full_eleven_stage_path_has_one_task_and_keeps_evidence(workflow):
    env = workflow
    app_id = mapped(env)
    with env.sessions() as db:
        db.get(Order, "order").openings = 1
        db.commit()
    for stage in range(1, 12):
        result = complete(env, app_id, payload(env, app_id, stage))
        with env.sessions() as db:
            open_tasks = list(db.scalars(select(Task).where(Task.application_id == app_id,
                                Task.completed_at.is_(None), Task.cancelled_at.is_(None))))
            assert len(open_tasks) == (0 if stage == 11 else 1)
            if open_tasks:
                assert open_tasks[0].stage == stage + 1
                assert open_tasks[0].due_at - open_tasks[0].created_at == timedelta(hours=48)
    assert result["status"] == "deployed"
    with env.sessions() as db:
        assert db.get(Candidate, "candidate").status == CandidateStatus.deployed
        assert db.get(Order, "order").status == OrderStatus.filled
        history = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app_id).order_by(ApplicationStage.stage)))
        assert len(history) == 11
        assert all(row.owner_id == "staff" and row.completed_by == "staff" for row in history)
        assert history[4].data["file"]["name"] == "synthetic.pdf"
        assert history[6].data["heldBy"] == {"id": "staff", "name": "staff"}
        assert history[7].amount_currency == "INR"
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_id == app_id)) == 23


@pytest.mark.parametrize(("stage", "data", "reason"), [
    (1, {"result": "failed"}, "prescreen_failed"),
    (2, {"forwarded": "no", "reason": "not_interested"}, "not_interested"),
    (2, {"forwarded": "no", "reason": "resume_not_received"}, "resume_not_received"),
    (4, {"result": "rejected", "remarks": "Rejected by client"}, "interview_rejected"),
    (6, {"result": "unfit", "reapplying": "no"}, "medical_unfit"),
])
def test_negative_outcomes_close_tasks_and_allow_fresh_mapping(workflow, stage, data, reason):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    result = complete(env, app_id, data)
    assert result["status"] == "closed" and result["closureReason"] == reason
    with env.sessions() as db:
        assert not db.scalar(select(Task.id).where(Task.application_id == app_id, Task.completed_at.is_(None), Task.cancelled_at.is_(None)))
        assert db.get(Candidate, "candidate").status == (CandidateStatus.not_interested if stage == 2 else CandidateStatus.new)
    next_id = mapped(env, order="other-order")
    assert next_id != app_id
    with env.sessions() as db:
        assert db.get(Application, next_id).current_stage == 1
        assert db.get(Candidate, "candidate").status == CandidateStatus.in_process


def test_repeated_medical_results_preserve_attempts_and_follow_up_tasks(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    for day in (5, 9):
        result = complete(env, app_id, {"applied": "yes", "result": "unfit", "reapplying": "yes", "retestDate": f"2026-11-{day:02}"})
        assert result["currentStage"] == 6 and result["status"] == "active"
        with env.sessions() as db:
            task = db.get(Task, result["nextTaskId"])
            assert task.kind == TaskKind.remedical and task.stage == 6
            assert f"2026-11-{day:02}" in task.title
        assert env.client.get(f"/api/applications/{app_id}/draft").json()["draft"] == {"applied": "yes", "appliedDate": f"2026-11-{day:02}"}
    complete(env, app_id, {"applied": "yes", "result": "fit"})
    with env.sessions() as db:
        attempts = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == 6).order_by(ApplicationStage.attempt)))
        assert [row.outcome for row in attempts] == ["Unfit: reapplying", "Unfit: reapplying", "Fit"]
        assert db.get(Application, app_id).current_stage == 7


def test_strict_values_and_real_document_ownership_before_advancing(workflow):
    env = workflow
    app_id = mapped(env)
    assert save(env, app_id, {"result": "unsupported"}).status_code == 422
    reach(env, app_id, 5)
    other = mapped(env, candidate="second-candidate")
    wrong_owner = attachment(env, other, 5)
    wrong_stage = attachment(env, app_id, 9)
    for file in ("invented", {"id": "missing"}, wrong_owner, wrong_stage):
        assert save(env, app_id, {"obtained": "yes", "file": file}).status_code == 422
    own = attachment(env, app_id, 5)
    response = save(env, app_id, {"obtained": "yes", "file": own})
    assert response.status_code == 200
    with env.sessions() as db:
        stored = db.get(Attachment, own["id"])
        (env.storage / stored.object_key).unlink()
    response = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": version(env, app_id)})
    assert response.status_code == 422
    with env.sessions() as db:
        assert db.get(Application, app_id).current_stage == 5


def test_owner_guard_and_admin_reopen_restores_stage_and_retains_superseded_history(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 4)
    original_version = version(env, app_id)
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert save(env, app_id, {"result": "selected", "remarks": "not owned"}, lock=original_version).status_code == 403
    env.actor = SimpleNamespace(id="manager", role=Role.manager)
    body = {"stage": 2, "reason": "Correct the forwarding date", "lock_version": version(env, app_id)}
    assert env.client.post(f"/api/applications/{app_id}/reopen", json=body).status_code == 403
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    assert env.client.post(f"/api/applications/{app_id}/reopen", json={**body, "reason": "  "}).status_code == 422
    reopened = env.client.post(f"/api/applications/{app_id}/reopen", json=body)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["currentStage"] == 2
    with env.sessions() as db:
        rows = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app_id).order_by(ApplicationStage.stage)))
        assert rows[0].reopened_reason is None
        assert [row.reopened_reason for row in rows[1:]] == [body["reason"], body["reason"]]
        assert sum(task.cancelled_at is None and task.completed_at is None for task in db.scalars(select(Task).where(Task.application_id == app_id))) == 1
    complete(env, app_id, {"forwarded": "yes"})
    with env.sessions() as db:
        row = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == 2, ApplicationStage.reopened_reason.is_(None)))
        assert row.attempt == 2


def test_stage_draft_read_is_private_to_current_owner_and_supervisors(workflow):
    env = workflow
    app_id = mapped(env)
    assert save(env, app_id, {"remarks": "Private unfinished notes"}).status_code == 200
    for actor, role, expected in (("staff", Role.staff, 200), ("other", Role.staff, 403),
                                  ("manager", Role.manager, 200), ("admin", Role.admin, 200)):
        env.actor = SimpleNamespace(id=actor, role=role)
        response = env.client.get(f"/api/applications/{app_id}/draft")
        assert response.status_code == expected, response.text
        if expected == 200:
            assert response.json()["draft"]["remarks"] == "Private unfinished notes"
        else:
            assert "Private unfinished notes" not in response.text
    with env.sessions() as db:
        db.get(Candidate, "candidate").owner_id = "other"
        db.commit()
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert env.client.get(f"/api/applications/{app_id}/draft").status_code == 403
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert env.client.get(f"/api/applications/{app_id}/draft").status_code == 200


def concurrent(*operations):
    barrier = Barrier(len(operations))
    def invoke(operation):
        barrier.wait(timeout=10)
        try:
            operation()
            return 200
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(invoke, operations))


def test_concurrent_draft_save_has_one_winner_and_preserves_conflict(workflow):
    env = workflow
    app_id = mapped(env)
    def write(value):
        with env.sessions() as db:
            subject.save_draft(app_id, subject.DraftIn(lock_version=0, data={"remarks": value}), db, env.actor)
    assert sorted(concurrent(lambda: write("first"), lambda: write("second"))) == [200, 409]
    with env.sessions() as db:
        app = db.get(Application, app_id)
        assert app.lock_version == 1 and app.draft["remarks"] in {"first", "second"}


def test_concurrent_map_and_completion_are_single_transitions(workflow):
    env = workflow
    def map_to(order):
        with env.sessions() as db:
            subject.map_candidate(subject.MapIn(candidate_id="candidate", order_id=order), db, env.actor)
    assert sorted(concurrent(lambda: map_to("order"), lambda: map_to("other-order"))) == [200, 409]
    with env.sessions() as db:
        app_id = db.scalar(select(Application.id))
    saved = save(env, app_id, {"result": "passed"}).json()
    def finish():
        with env.sessions() as db:
            subject.complete_stage(app_id, subject.CompleteIn(lock_version=saved["lockVersion"]), db, env.actor)
    assert sorted(concurrent(finish, finish)) == [200, 409]
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ApplicationStage)) == 1
        assert db.scalar(select(func.count()).select_from(Task).where(Task.completed_at.is_(None), Task.cancelled_at.is_(None))) == 1


def test_concurrent_final_deployments_fill_order_and_reopen_reverses_count(workflow):
    env = workflow
    ids = [mapped(env, candidate=key) for key in ("candidate", "second-candidate")]
    for app_id in ids:
        reach(env, app_id, 11)
        save(env, app_id, payload(env, app_id, 11))
    locks = [version(env, app_id) for app_id in ids]
    def finish(index):
        with env.sessions() as db:
            subject.complete_stage(ids[index], subject.CompleteIn(lock_version=locks[index]), db, env.actor)
    assert concurrent(lambda: finish(0), lambda: finish(1)) == [200, 200]
    with env.sessions() as db:
        assert db.get(Order, "order").status == OrderStatus.filled
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    response = env.client.post(f"/api/applications/{ids[0]}/reopen", json={
        "stage": 11, "reason": "Corrected ticket needed", "lock_version": version(env, ids[0]),
    })
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        assert db.get(Order, "order").status == OrderStatus.open
        assert db.get(Candidate, "candidate").status == CandidateStatus.in_process
        assert db.scalar(select(func.count()).select_from(Application).where(Application.status == ApplicationStatus.deployed)) == 1


def test_concurrent_deployments_cannot_exceed_remaining_opening(workflow):
    env = workflow
    ids = [mapped(env, candidate=key) for key in ("candidate", "second-candidate")]
    for app_id in ids:
        reach(env, app_id, 11)
        save(env, app_id, payload(env, app_id, 11))
    with env.sessions() as db:
        db.get(Order, "order").openings = 1
        db.commit()
    locks = [version(env, app_id) for app_id in ids]

    def finish(index):
        with env.sessions() as db:
            subject.complete_stage(ids[index], subject.CompleteIn(lock_version=locks[index]), db, env.actor)

    assert sorted(concurrent(lambda: finish(0), lambda: finish(1))) == [200, 409]
    with env.sessions() as db:
        deployed = db.scalars(select(Application).where(Application.status == ApplicationStatus.deployed)).all()
        assert len(deployed) == 1
        waiting = db.scalar(select(Application).where(Application.status == ApplicationStatus.active))
        assert waiting.current_stage == 11 and waiting.draft.get("file")
        assert waiting.lock_version == locks[ids.index(waiting.id)]
        assert db.get(Candidate, waiting.candidate_id).status == CandidateStatus.in_process
        assert db.scalar(select(Task.id).where(Task.application_id == waiting.id,
            Task.completed_at.is_(None), Task.cancelled_at.is_(None)))
        assert not db.scalar(select(ApplicationStage.id).where(ApplicationStage.application_id == waiting.id,
            ApplicationStage.stage == 11))
        assert db.get(Order, "order").status == OrderStatus.filled

    # Explicitly increasing openings makes the retained stage retryable.
    with env.sessions() as db:
        db.get(Order, "order").openings = 2
        db.get(Order, "order").status = OrderStatus.open
        waiting_id = waiting.id
        db.commit()
    result = env.client.post(f"/api/applications/{waiting_id}/complete", json={"lock_version": version(env, waiting_id)})
    assert result.status_code == 200, result.text


def test_unmap_cancels_tasks_and_old_application_cannot_be_reopened_over_new_mapping(workflow):
    env = workflow
    app_id = mapped(env)
    complete(env, app_id, {"result": "passed"})
    response = env.client.post(f"/api/applications/{app_id}/unmap", json={"lock_version": version(env, app_id)})
    assert response.status_code == 200
    mapped(env, order="other-order")
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    response = env.client.post(f"/api/applications/{app_id}/reopen", json={
        "stage": 1, "reason": "Cannot conflict with new application", "lock_version": version(env, app_id),
    })
    assert response.status_code == 409


def test_atomic_complete_records_submitted_values_and_owner_at_completion(workflow):
    env = workflow
    app_id = mapped(env)
    save(env, app_id, {"result": "failed", "remarks": "Old draft"})
    with env.sessions() as db:
        db.get(Candidate, "candidate").owner_id = "other"
        db.commit()
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    response = env.client.post(f"/api/applications/{app_id}/complete", json={
        "lock_version": version(env, app_id), "data": {"result": "passed", "remarks": "New submitted values"},
    })
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        app = db.get(Application, app_id)
        record = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == app_id))
        assert app.owner_id == "staff" and record.owner_id == "other"
        assert record.data["remarks"] == "New submitted values" and record.outcome == "Passed"
        assert app.current_stage == 2


def test_concurrent_unmap_and_atomic_complete_have_one_winner(workflow):
    env = workflow
    app_id = mapped(env)
    def finish():
        with env.sessions() as db:
            subject.complete_stage(app_id, subject.CompleteIn(lock_version=0, data={"result": "passed"}), db, env.actor)
    def unmap():
        with env.sessions() as db:
            subject.unmap(app_id, subject.UnmapIn(lock_version=0), db, env.actor)
    assert sorted(concurrent(finish, unmap)) == [200, 409]
    with env.sessions() as db:
        app = db.get(Application, app_id)
        assert app.lock_version == 1
        open_tasks = db.scalar(select(func.count()).select_from(Task).where(Task.application_id == app_id, Task.completed_at.is_(None), Task.cancelled_at.is_(None)))
        assert open_tasks == (1 if app.status == ApplicationStatus.active else 0)
