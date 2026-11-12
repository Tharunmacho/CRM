"""Manual movement records feedback atomically, independent of the document checklist."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import Application, ApplicationStage, ApplicationStatus, Attachment, Candidate, CandidateStatus, Role, Task
from test_application_workflow import complete, mapped, save, version, workflow  # noqa: F401
from test_database_naming import schema_database  # noqa: F401


@pytest.mark.parametrize("feedback", [None, "", "  \n\t  "])
def test_feedback_is_required_even_when_stage_details_are_present(workflow, feedback):
    env = workflow
    app_id = mapped(env)
    details = {"result": "passed", "remarks": "Legacy detail is not transition feedback"}
    if feedback is not None:
        details["feedback"] = feedback
    saved = save(env, app_id, details)
    assert saved.status_code == 200, saved.text
    lock = saved.json()["lockVersion"]
    # Both completing a saved draft and submitting a final payload enforce the same rule.
    for body in ({"lock_version": lock}, {"lock_version": lock, "data": details}):
        response = env.client.post(f"/api/applications/{app_id}/complete", json=body)
        assert response.status_code == 422 and response.json()["detail"]["missing"] == ["Feedback"]
    with env.sessions() as db:
        app = db.get(Application, app_id)
        assert app.current_stage == 1 and app.lock_version == lock
        assert not db.scalar(select(ApplicationStage.id).where(ApplicationStage.application_id == app_id))
        assert db.scalar(select(func.count()).select_from(Task).where(Task.application_id == app_id, Task.completed_at.is_(None))) == 1


def test_all_eleven_stages_accept_feedback_only_and_keep_history_without_documents(workflow):
    env = workflow
    app_id = mapped(env)
    for stage in range(1, 12):
        lock = version(env, app_id)
        body = {"lock_version": lock, "data": {"feedback": f"  Stage {stage}: recruiter approved this move.  "}}
        response = env.client.post(f"/api/applications/{app_id}/complete", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["currentStage"] == min(stage + 1, 11)
        # A duplicate submit cannot complete the next stage using the same feedback.
        repeated = env.client.post(f"/api/applications/{app_id}/complete", json=body)
        assert repeated.status_code == 409
        if stage < 11:
            next_without_feedback = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": version(env, app_id)})
            assert next_without_feedback.status_code == 422
    with env.sessions() as db:
        app = db.get(Application, app_id)
        assert app.status == ApplicationStatus.deployed
        assert db.get(Candidate, "candidate").status == CandidateStatus.deployed
        assert not db.scalar(select(Attachment.id))
        records = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app_id).order_by(ApplicationStage.stage)))
        assert len(records) == 11
        for stage, row in enumerate(records, 1):
            assert row.data["feedback"] == f"Stage {stage}: recruiter approved this move."
            assert row.outcome == "Completed manually" and row.completed_by == "staff"
            assert row.completed_at is not None
        assert "travelDate" not in records[-1].data


def test_saving_feedback_never_moves_stage_and_reopening_preserves_history_without_reusing_feedback(workflow):
    env = workflow
    app_id = mapped(env)
    saved = save(env, app_id, {"feedback": "Original stage feedback"})
    assert saved.status_code == 200
    with env.sessions() as db:
        assert db.get(Application, app_id).current_stage == 1
        assert not db.scalar(select(ApplicationStage.id))
    done = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert done.status_code == 200
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    reopened = env.client.post(f"/api/applications/{app_id}/reopen", json={
        "stage": 1, "reason": "Need to confirm the candidate's new details", "lock_version": version(env, app_id),
    })
    assert reopened.status_code == 200, reopened.text
    assert "feedback" not in env.client.get(f"/api/applications/{app_id}/draft").json()["draft"]
    refused = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": version(env, app_id)})
    assert refused.status_code == 422
    complete(env, app_id, {"feedback": "Candidate reconfirmed after correction"})
    with env.sessions() as db:
        records = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app_id).order_by(ApplicationStage.attempt)))
        assert [row.data["feedback"] for row in records] == ["Original stage feedback", "Candidate reconfirmed after correction"]
        assert records[0].reopened_reason == "Need to confirm the candidate's new details"
        assert records[1].reopened_reason is None


def test_another_staff_member_cannot_move_a_stage_even_with_feedback(workflow):
    env = workflow
    app_id = mapped(env)
    lock = version(env, app_id)
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    response = env.client.post(f"/api/applications/{app_id}/complete", json={
        "lock_version": lock, "data": {"feedback": "Attempted move by someone else"},
    })
    assert response.status_code == 403
    with env.sessions() as db:
        assert db.get(Application, app_id).current_stage == 1
        assert not db.scalar(select(ApplicationStage.id))
