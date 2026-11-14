"""Interview result, Offer letter and Medical are decisions: only the positive answer moves the candidate on.

Selected / Agreed / Fit advance to the next stage. Rejected / Declined / Not received / Unfit stop the candidate there
(the application closes with a reason, its task is closed and the candidate can be matched to another order). Every choice
needs written feedback. Only the assigned recruiter and managers/admins can record it; only an admin can reopen it.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, ClosureReason, Order,
    OrderStatus, Role, Task,
)
from test_application_workflow import complete, mapped, reach, save, version, workflow  # noqa: F401
from test_database_naming import schema_database  # noqa: F401

ADVANCE = [
    (4, {"result": "selected", "remarks": "Client approved"}, "Selected"),
    (5, {"result": "agreed"}, "Agreed"),
    (6, {"applied": "yes", "result": "fit"}, "Fit"),
]
STOP = [
    (4, {"result": "rejected", "remarks": "Client chose someone else"}, "Rejected", "interview_rejected"),
    (5, {"result": "declined"}, "Declined", "offer_declined"),
    (5, {"result": "not_received"}, "Not received", "offer_not_received"),
    (6, {"applied": "yes", "result": "unfit"}, "Unfit", "medical_unfit"),
]


def post_complete(env, app_id, data=None):
    body = {"lock_version": version(env, app_id)}
    if data is not None:
        body["data"] = data
    return env.client.post(f"/api/applications/{app_id}/complete", json=body)


@pytest.mark.parametrize(("stage", "data", "outcome"), ADVANCE)
def test_the_positive_answer_records_the_outcome_and_advances(workflow, stage, data, outcome):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    result = complete(env, app_id, data)
    assert result["status"] == "active" and result["currentStage"] == stage + 1 and result["nextTaskId"]
    with env.sessions() as db:
        row = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == stage))
        assert row.outcome == outcome and row.data["result"] == data["result"]
        assert row.data["feedback"] == "Reviewed with the candidate; move this stage."
        open_tasks = list(db.scalars(select(Task).where(Task.application_id == app_id, Task.completed_at.is_(None), Task.cancelled_at.is_(None))))
        assert [task.stage for task in open_tasks] == [stage + 1]
        assert db.get(Candidate, "candidate").status == CandidateStatus.in_process


@pytest.mark.parametrize(("stage", "data", "outcome", "reason"), STOP)
def test_every_other_answer_stops_the_candidate_at_that_stage(workflow, stage, data, outcome, reason):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    result = complete(env, app_id, data)
    assert result["status"] == "closed" and result["closureReason"] == reason and result["currentStage"] == stage
    assert result["nextTaskId"] is None
    with env.sessions() as db:
        application = db.get(Application, app_id)
        assert (application.status, application.current_stage) == (ApplicationStatus.closed, stage)
        assert application.closure_reason == ClosureReason(reason)
        row = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == stage))
        assert row.outcome == outcome and row.data["feedback"] == "Reviewed with the candidate; move this stage."
        # The stage task is done, nothing is left open, no later stage exists, and the opening is not used up.
        assert not db.scalar(select(Task.id).where(Task.application_id == app_id, Task.completed_at.is_(None), Task.cancelled_at.is_(None)))
        assert db.scalar(select(Task.completed_at).where(Task.application_id == app_id, Task.stage == stage)) is not None
        assert not db.scalar(select(ApplicationStage.id).where(ApplicationStage.application_id == app_id, ApplicationStage.stage > stage))
        assert db.get(Order, "order").status != OrderStatus.filled
        assert db.get(Candidate, "candidate").status == CandidateStatus.new
        audit = db.scalar(select(AuditEntry).where(AuditEntry.entity_id == app_id, AuditEntry.action == f"Recorded stage {stage}: {outcome}"))
        assert audit.meta["closes"] == reason
    # They go back to the Data Bank and can be matched to another order straight away.
    assert mapped(env, order="other-order") != app_id


@pytest.mark.parametrize(("stage", "label"), [(4, "Interview result"), (5, "Offer letter result"), (6, "Medical result")])
def test_the_api_refuses_to_move_a_decision_stage_without_the_choice(workflow, stage, label):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    lock = version(env, app_id)
    for data in ({"feedback": "Recruiter note."}, {"feedback": "Recruiter note.", "applied": "yes"} if stage == 6 else {"feedback": "Recruiter note."}):
        response = post_complete(env, app_id, data)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["missing"] == [label] and detail["message"].startswith("Choose the ")
    with env.sessions() as db:
        assert db.get(Application, app_id).current_stage == stage and db.get(Application, app_id).lock_version == lock
        assert not db.scalar(select(ApplicationStage.id).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == stage))


def test_an_older_offer_answer_cannot_move_the_stage(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 5)
    response = post_complete(env, app_id, {"feedback": "Offer arrived.", "obtained": "yes"})
    assert response.status_code == 422 and response.json()["detail"]["missing"] == ["Offer letter result"]


@pytest.mark.parametrize(("stage", "data"), [(4, {"result": "selected"}), (4, {"result": "rejected"}), (5, {"result": "agreed"}),
                                             (5, {"result": "declined"}), (5, {"result": "not_received"}),
                                             (6, {"result": "fit"}), (6, {"result": "unfit"})])
@pytest.mark.parametrize("feedback", [None, "", "   \n"])
def test_each_choice_needs_written_feedback(workflow, stage, data, feedback):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    body = dict(data)
    if feedback is not None:
        body["feedback"] = feedback
    response = post_complete(env, app_id, body)
    assert response.status_code == 422 and response.json()["detail"]["missing"] == ["Feedback"]
    with env.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.active and db.get(Application, app_id).current_stage == stage


@pytest.mark.parametrize(("stage", "data"), [(4, {"result": "maybe"}), (5, {"result": "accepted"}), (5, {"result": "obtained"}), (6, {"result": "pending"})])
def test_only_the_listed_choices_are_accepted(workflow, stage, data):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    assert save(env, app_id, data).status_code == 422
    assert post_complete(env, app_id, {"feedback": "Recruiter note.", **data}).status_code == 422


def test_unfit_with_a_planned_retest_stays_on_the_stage_and_never_advances(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    result = complete(env, app_id, {"applied": "yes", "appliedDate": "2026-11-02", "result": "unfit", "reapplying": "yes", "retestDate": "2026-11-09"})
    assert result["status"] == "active" and result["currentStage"] == 6
    # A retest without its date is not a decision to wait.
    assert post_complete(env, app_id, {"feedback": "Retest.", "result": "unfit", "reapplying": "yes"}).status_code == 422
    # Unfit again, this time stopping.
    assert complete(env, app_id, {"applied": "yes", "result": "unfit"})["closureReason"] == "medical_unfit"


@pytest.mark.parametrize(("stage", "data"), [(4, {"result": "rejected"}), (5, {"result": "declined"}), (6, {"result": "unfit"})])
def test_only_the_assigned_recruiter_manager_or_admin_can_record_a_decision(workflow, stage, data):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    lock = version(env, app_id)
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    refused = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": lock, "data": {"feedback": "Not my candidate.", **data}})
    assert refused.status_code == 403
    with env.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.active
    env.actor = SimpleNamespace(id="manager", role=Role.manager)
    assert post_complete(env, app_id, {"feedback": "Manager recorded the answer.", **data}).status_code == 200
    with env.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.closed


@pytest.mark.parametrize(("stage", "data", "again", "outcome"), [
    (4, {"result": "rejected"}, {"result": "selected"}, "Selected"),
    (5, {"result": "not_received"}, {"result": "agreed"}, "Agreed"),
    (5, {"result": "declined"}, {"result": "agreed"}, "Agreed"),
    (6, {"result": "unfit"}, {"result": "fit"}, "Fit"),
])
def test_only_an_admin_can_reopen_a_stopped_stage_and_it_needs_a_fresh_answer(workflow, stage, data, again, outcome):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    complete(env, app_id, data)
    body = {"stage": stage, "reason": "The candidate changed their mind", "lock_version": version(env, app_id)}
    for actor, role in (("staff", Role.staff), ("manager", Role.manager)):
        env.actor = SimpleNamespace(id=actor, role=role)
        assert env.client.post(f"/api/applications/{app_id}/reopen", json=body).status_code == 403
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    reopened = env.client.post(f"/api/applications/{app_id}/reopen", json=body)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "active" and reopened.json()["currentStage"] == stage
    with env.sessions() as db:
        assert db.get(Application, app_id).closure_reason is None
        assert db.get(Candidate, "candidate").status == CandidateStatus.in_process
    draft = env.client.get(f"/api/applications/{app_id}/draft").json()["draft"]
    assert "feedback" not in draft and draft["result"] == data["result"]
    # The old answer is kept as history; the stage still cannot move without new feedback and an answer.
    assert post_complete(env, app_id).status_code == 422
    result = complete(env, app_id, again)
    assert result["status"] == "active" and result["currentStage"] == stage + 1
    with env.sessions() as db:
        rows = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == stage).order_by(ApplicationStage.attempt)))
        assert [row.outcome for row in rows][-1] == outcome and rows[0].reopened_reason == body["reason"]
