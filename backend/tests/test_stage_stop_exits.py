"""Every stage can end the application with a stopping exit (Rejected / Declined / its own wording; stage 11: Not travelling).

The exit needs written feedback, closes the application like the older negative exits (no next stage, no further task, the
candidate goes back to the Data Bank), never deploys, and the admin can reopen it. Disposable PostgreSQL, synthetic data.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, Order, OrderStatus, Role, Task
from app.domain.stages import STAGES, STOP_EXITS, stage_by_no
from test_application_workflow import complete, mapped, reach, save, version, workflow  # noqa: F401
from test_database_naming import schema_database  # noqa: F401

# (stage, choice, closure reason, outcome written on the stage record)
EXITS = [(n, choice, reason, outcome) for n, exits in STOP_EXITS.items() for choice, (reason, _label, outcome) in exits.items()]


def test_every_stage_offers_a_stopping_exit_with_the_wording_asked_for():
    assert sorted(STOP_EXITS) == list(range(1, 12))
    for n in range(1, 11):
        assert {"rejected", "declined"} <= set(STOP_EXITS[n]), n
    assert set(STOP_EXITS[11]) == {"not_travelling"}
    assert "not_possible" in STOP_EXITS[3] and "not_available" in STOP_EXITS[7] and "not_paid" in STOP_EXITS[8]
    assert "visa_rejected" in STOP_EXITS[9] and "refused" in STOP_EXITS[10]
    assert all(stage.closer for stage in STAGES)


def test_the_rules_close_without_inventing_a_next_stage_and_need_a_listed_choice():
    for n, choice, reason, outcome in EXITS:
        result = stage_by_no(n).evaluate({"feedback": "Reviewed", "stop": choice})
        assert result.can_complete and result.closes == reason and result.outcome == outcome and not result.repeats, (n, choice)
    # A choice that belongs to another stage, or a Rejected/Declined at the final stage, is not accepted.
    assert not stage_by_no(5).evaluate({"stop": "not_paid"}).can_complete
    assert not stage_by_no(11).evaluate({"stop": "declined"}).can_complete
    assert not stage_by_no(11).evaluate({"stop": "rejected"}).can_complete
    # The stop wins over the stage's own answer, including a planned retest.
    assert stage_by_no(4).evaluate({"result": "selected", "stop": "declined"}).closes == "stage_declined"
    assert stage_by_no(6).evaluate({"result": "unfit", "reapplying": "yes", "retestDate": "2026-12-01", "stop": "declined"}).repeats is False
    # Without a stop nothing changes: stage 3 still moves on, stage 11 still needs the ticket and the date.
    assert stage_by_no(3).evaluate({"scheduled": "yes"}).closes is None
    assert stage_by_no(11).evaluate({}).can_complete is False


@pytest.mark.parametrize(("stage", "choice", "reason", "outcome"), EXITS)
def test_a_stopping_exit_at_every_stage_closes_the_application(workflow, stage, choice, reason, outcome):  # noqa: F811
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, stage)
    # Feedback is required: the exit is refused until the recruiter has written it.
    draft = save(env, app_id, {"stop": choice})
    assert draft.status_code == 200, draft.text
    refused = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": draft.json()["lockVersion"]})
    assert refused.status_code == 422 and refused.json()["detail"]["missing"] == ["Feedback"], refused.text
    result = complete(env, app_id, {"stop": choice})
    assert result["status"] == "closed" and result["closureReason"] == reason
    assert result["currentStage"] == stage and result["nextTaskId"] is None
    with env.sessions() as db:
        app = db.get(Application, app_id)
        assert app.status == ApplicationStatus.closed and app.current_stage == stage
        record = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == stage))
        assert record.outcome == outcome and record.data["stop"] == choice and record.data["feedback"]
        # No task is left open for the closed application, so it drops out of the recruiter's Follow-up.
        assert not db.scalar(select(Task.id).where(Task.application_id == app_id, Task.completed_at.is_(None), Task.cancelled_at.is_(None)))
        assert db.get(Candidate, "candidate").status == (CandidateStatus.not_interested if stage == 2 else CandidateStatus.new)  # as the older exits
        # The outcome shows in the history.
        action = db.scalar(select(AuditEntry.action).where(AuditEntry.entity_id == app_id, AuditEntry.action.like(f"Recorded stage {stage}:%")))
        assert action == f"Recorded stage {stage}: {outcome}"
        # It is not a deployment: the order's filled openings are untouched and it can take someone else.
        assert not db.scalar(select(Application.id).where(Application.order_id == "order", Application.status == ApplicationStatus.deployed))
        assert db.get(Order, "order").status == OrderStatus.open
    # A fresh mapping is allowed straight away, exactly like the older negative exits.
    next_id = mapped(env, order="other-order")
    with env.sessions() as db:
        assert db.get(Application, next_id).current_stage == 1
    # Only the admin can reopen it; the recruiter cannot.
    body = {"stage": stage, "reason": "Closed by mistake", "lock_version": version(env, app_id)}
    with env.sessions() as db:
        db.get(Application, next_id).status = ApplicationStatus.closed
        db.commit()
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert env.client.post(f"/api/applications/{app_id}/reopen", json=body).status_code == 403
    env.actor = SimpleNamespace(id="manager", role=Role.manager)
    assert env.client.post(f"/api/applications/{app_id}/reopen", json=body).status_code == 403
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    reopened = env.client.post(f"/api/applications/{app_id}/reopen", json=body)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "active" and reopened.json()["currentStage"] == stage
    with env.sessions() as db:
        assert db.get(Application, app_id).closure_reason is None


def test_not_travelling_closes_even_when_every_opening_is_filled_and_never_deploys(workflow):  # noqa: F811
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 11)
    with env.sessions() as db:
        other = mapped(env, candidate="second-candidate")
        order = db.get(Order, "order")
        order.openings = 1
        db.get(Application, other).status = ApplicationStatus.deployed
        db.commit()
    result = complete(env, app_id, {"stop": "not_travelling"})
    assert result["status"] == "closed" and result["closureReason"] == "not_travelling"
    with env.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.closed
        assert db.get(Candidate, "candidate").status != CandidateStatus.deployed
        assert db.get(Candidate, "candidate").traveller_id is None


def test_a_stop_not_offered_at_the_stage_is_refused_by_the_server(workflow):  # noqa: F811
    env = workflow
    app_id = mapped(env)
    assert save(env, app_id, {"stop": "not_paid"}).status_code == 200
    refused = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": version(env, app_id), "data": {"feedback": "x", "stop": "not_paid"}})
    assert refused.status_code == 422
    with env.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.active and db.get(Application, app_id).current_stage == 1
