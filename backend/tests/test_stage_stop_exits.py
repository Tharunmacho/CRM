"""Every stage can end the application with a stopping exit (Rejected / Declined / its own wording; stage 13: Not travelling).

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

# The exits where the candidate themselves turned the placement down: they go back to the Data Bank as Not interested.
CANDIDATE_SAID_NO = {"stage_declined", "offer_declined", "not_interested", "not_travelling"}


def test_every_stage_offers_a_stopping_exit_with_the_wording_asked_for():
    assert sorted(STOP_EXITS) == list(range(1, 14))
    for n in range(1, 13):
        assert {"rejected", "declined"} <= set(STOP_EXITS[n]), n
    assert set(STOP_EXITS[13]) == {"not_travelling"}
    assert "not_possible" in STOP_EXITS[3] and "not_cleared" in STOP_EXITS[9] and "not_available" in STOP_EXITS[7] and "not_paid" in STOP_EXITS[6]
    assert "visa_rejected" in STOP_EXITS[10] and "refused" in STOP_EXITS[12]
    assert all(stage.closer for stage in STAGES)


def test_each_own_exit_stayed_with_its_stage_when_the_stages_were_reordered():
    # (stage, exit key) -> closure reason: migration 0056 moved Advance (6) and Passport (7) ahead of Medical (8) and PCC (9); each
    # keeps its own exit with it. Medical has none: its own closure is the Unfit answer, not a stopping exit.
    own = {key: (reason, label, outcome) for stage in (3, 6, 7, 8, 9, 10, 11, 12, 13) for key, (reason, label, outcome) in STOP_EXITS[stage].items()
           if key not in ("rejected", "declined")}
    assert STOP_EXITS[9]["not_cleared"] == ("pcc_not_cleared", "PCC not cleared", "Not cleared")
    assert STOP_EXITS[7]["not_available"][0] == "passport_not_available" and STOP_EXITS[6]["not_paid"][0] == "advance_not_paid"
    assert STOP_EXITS[10]["visa_rejected"][0] == "visa_rejected" and STOP_EXITS[12]["refused"][0] == "visa_stamping_refused"
    assert STOP_EXITS[13]["not_travelling"][0] == "not_travelling" and STOP_EXITS[3]["not_possible"][0] == "interview_not_possible"
    assert set(own) == {"not_possible", "not_cleared", "not_available", "not_paid", "visa_rejected", "refused", "not_travelling"}
    assert set(STOP_EXITS[8]) == {"rejected", "declined"} and set(STOP_EXITS[11]) == {"rejected", "declined"}
    for stage, wrong in ((8, "not_cleared"), (9, "not_available"), (6, "not_cleared"), (7, "not_paid"), (11, "visa_rejected"), (10, "refused")):
        assert not stage_by_no(stage).evaluate({"feedback": "x", "stop": wrong}).can_complete, (stage, wrong)


def test_the_rules_close_without_inventing_a_next_stage_and_need_a_listed_choice():
    for n, choice, reason, outcome in EXITS:
        result = stage_by_no(n).evaluate({"feedback": "Reviewed", "stop": choice})
        assert result.can_complete and result.closes == reason and result.outcome == outcome and not result.repeats, (n, choice)
    # A choice that belongs to another stage, or a Rejected/Declined at the final stage, is not accepted.
    assert not stage_by_no(5).evaluate({"stop": "not_paid"}).can_complete
    assert not stage_by_no(13).evaluate({"stop": "declined"}).can_complete
    assert not stage_by_no(13).evaluate({"stop": "rejected"}).can_complete
    # The stop wins over the stage's own answer, including a planned retest.
    assert stage_by_no(4).evaluate({"result": "selected", "stop": "declined"}).closes == "stage_declined"
    assert stage_by_no(8).evaluate({"result": "unfit", "reapplying": "yes", "retestDate": "2026-12-01", "stop": "declined"}).repeats is False
    # Without a stop nothing changes: stage 3 still moves on, stage 13 still needs the ticket and the date.
    assert stage_by_no(3).evaluate({"scheduled": "yes"}).closes is None
    assert stage_by_no(13).evaluate({}).can_complete is False


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
        assert db.get(Candidate, "candidate").status == (CandidateStatus.not_interested if stage == 2 or reason in CANDIDATE_SAID_NO else CandidateStatus.new)  # the candidate's own no parks them as Not interested
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
    # Only an admin-level user (Admin or MD) can reopen it; the recruiter cannot.
    body = {"stage": stage, "reason": "Closed by mistake", "lock_version": version(env, app_id)}
    with env.sessions() as db:
        db.get(Application, next_id).status = ApplicationStatus.closed
        db.commit()
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert env.client.post(f"/api/applications/{app_id}/reopen", json=body).status_code == 403
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    reopened = env.client.post(f"/api/applications/{app_id}/reopen", json=body)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "active" and reopened.json()["currentStage"] == stage
    with env.sessions() as db:
        assert db.get(Application, app_id).closure_reason is None


@pytest.mark.parametrize(("needs_review", "stage", "data", "reason", "status", "on_the_tasks_list"), [
    # The candidate's own no: a candidate whose data still needs review keeps that status (the flag stays too) and is still parked.
    (True, 4, {"stop": "declined"}, "stage_declined", CandidateStatus.needs_review, False),
    (True, 5, {"stop": "declined"}, "offer_declined", CandidateStatus.needs_review, False),
    (True, 13, {"stop": "not_travelling"}, "not_travelling", CandidateStatus.needs_review, False),
    (False, 4, {"stop": "declined"}, "stage_declined", CandidateStatus.not_interested, False),
    # The PCC refusal is not their own no either: they return to the pool as New.
    (False, 9, {"stop": "not_cleared"}, "pcc_not_cleared", CandidateStatus.new, True),
    # A rejection is not their own no: unchanged, they return to the pool (and to the Tasks list) as Needs review / New.
    (True, 4, {"stop": "rejected"}, "interview_rejected", CandidateStatus.needs_review, True),
    (False, 4, {"stop": "rejected"}, "interview_rejected", CandidateStatus.new, True),
    # Stage 2 closed because the resume never arrived: not the candidate's no, but it has always parked them by status; unchanged.
    (True, 2, {"forwarded": "no", "reason": "resume_not_received"}, "resume_not_received", CandidateStatus.not_interested, False),
])
def test_a_decline_keeps_the_needs_review_status_and_a_parked_candidate_stays_off_the_tasks_list(
        workflow, needs_review, stage, data, reason, status, on_the_tasks_list):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    from app.services import daily_work
    env = workflow
    tomorrow = datetime.now(timezone.utc) + timedelta(days=2)      # prescreening was finished today: ask on a later day, so no "done today" row
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.needs_review = needs_review
        candidate.status = CandidateStatus.needs_review if needs_review else CandidateStatus.new
        db.commit()
    app_id = mapped(env)
    reach(env, app_id, stage)
    result = complete(env, app_id, data)
    assert result["status"] == "closed" and result["closureReason"] == reason
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.status == status and candidate.needs_review is needs_review         # the flag itself is never touched
        assert CandidateStatus.needs_review in daily_work.WORKABLE                           # the status alone would NOT hide them
        # The candidate's own no takes them off the list at once (no ticked row either); a rejection stays on it; a stage 2
        # closure that is not their no stays off the list (parked by status), exactly as before.
        today = {row["candidateId"]: row for row in daily_work.load_rows(db, ["staff"], now=datetime.now(timezone.utc) + timedelta(minutes=1), tz="Asia/Kolkata")}
        if reason in CANDIDATE_SAID_NO:
            assert "candidate" not in today
        elif on_the_tasks_list:
            assert "candidate" in today          # stays on the list (ticked: prescreening was finished today)
        else:
            assert "candidate" not in today
        listed = [row["candidateId"] for row in daily_work.load_rows(db, ["staff"], now=tomorrow, tz="Asia/Kolkata")]
        # The fixture's two other candidates (never mapped, status new) are always on the staff member's list.
        assert sorted(listed) == sorted(["second-candidate", "third-candidate"] + (["candidate"] if on_the_tasks_list else []))
        assert daily_work.owner_counts(db, ["staff"], now=tomorrow, tz="Asia/Kolkata")["staff"] == (2 + (1 if on_the_tasks_list else 0), 0)


def test_not_travelling_closes_even_when_every_opening_is_filled_and_never_deploys(workflow):  # noqa: F811
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 13)
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
