"""Reopen a CLOSED application (`POST /api/applications/{id}/reopen-application`): a placement that ended before any stage was completed
(declined at the call, removed) or at a later stage (declined / rejected in the stage window) goes back to its stage with an open task,
the candidate leaves Declined / Rejected and is on today's Current list in the very next read.

Runs on the explicitly guarded disposable PostgreSQL DB with the same desk fixture as test_daily_work.
"""
import pytest
from sqlalchemy import text

from test_daily_work import (  # noqa: F401  (desk is a fixture)
    advance, as_user, desk, ids, mapped_candidates, record_call, second_order, set_application, to_stage,
)
from test_daily_work_views import view
from test_database_naming import schema_database  # noqa: F401  (fixture used by desk)
from app.api.routers import application_reopen


@pytest.fixture
def reopen(desk):
    desk.app.include_router(application_reopen.router)
    return desk


def lock(env, app_id):
    with env.sessions() as db:
        return db.execute(text("SELECT lock_version FROM crm_applications WHERE id = :a"), {"a": app_id}).scalar_one()


def row(env, app_id):
    with env.sessions() as db:
        return db.execute(text("SELECT status::text, closure_reason::text, current_stage, closed_at, draft FROM crm_applications WHERE id = :a"),
                          {"a": app_id}).one()


def do_reopen(env, app_id, *, reason="Candidate is interested again", version=None):
    return env.client.post(f"/api/applications/{app_id}/reopen-application",
                           json={"reason": reason, "lock_version": lock(env, app_id) if version is None else version})


def tabs(env, user="asha"):
    return {name: ids(view(env, user, name)) for name in ("current", "declined", "rejected", "all")}


def test_a_placement_declined_at_the_call_before_any_stage_goes_back_to_prescreening_and_the_current_list(reopen):
    apps = mapped_candidates(reopen, 1)
    app_id = apps["c0"]
    as_user(reopen, "asha")
    assert record_call(reopen, "c0", interested="no").status_code == 200
    before = tabs(reopen)
    assert before["declined"] == ["c0"] and "c0" not in before["current"]   # Aneesh's situation: closed, no stage record, no way back
    assert row(reopen, app_id).status == "closed"
    with reopen.sessions() as db:
        assert db.execute(text("SELECT count(*) FROM crm_application_stages WHERE application_id = :a"), {"a": app_id}).scalar_one() == 0

    response = do_reopen(reopen, app_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active" and body["currentStage"] == 1 and body["closureReason"] is None and body["nextTaskId"]

    after = tabs(reopen)   # the very next read: no cache lag
    assert "c0" in after["current"] and after["declined"] == [] and after["rejected"] == [] and after["all"] == ["c0"]
    assert view(reopen, "asha", "current")["viewCounts"]["declined"] == 0
    stored = row(reopen, app_id)
    assert (stored.status, stored.closure_reason, stored.current_stage, stored.closed_at) == ("active", None, 1, None)
    with reopen.sessions() as db:
        assert db.execute(text("SELECT status::text FROM crm_candidates WHERE id = 'c0'")).scalar_one() == "in_process"
        task = db.execute(text("SELECT stage, owner_id FROM workflow_tasks WHERE application_id = :a AND completed_at IS NULL AND cancelled_at IS NULL"),
                          {"a": app_id}).all()
        assert [(t.stage, t.owner_id) for t in task] == [(1, "asha")]
        audit = db.execute(text("SELECT actor_id, meta FROM system_audit_log WHERE entity_id = :a AND action = 'Reopened application'"), {"a": app_id}).one()
        assert audit.actor_id == "asha" and audit.meta["reason"] == "Candidate is interested again" and audit.meta["previous_closure"] == "not_interested"


@pytest.mark.parametrize("stage, answer, tab", [(4, {"result": "rejected"}, "rejected"), (5, {"result": "declined"}, "declined")])
def test_a_placement_ended_in_a_stage_window_is_reopened_at_that_stage_and_the_closing_record_is_set_aside(reopen, stage, answer, tab):
    apps = mapped_candidates(reopen, 1)
    app_id = apps["c0"]
    as_user(reopen, "asha")
    to_stage(reopen, app_id, stage)
    advance(reopen, app_id, answer)
    assert tabs(reopen)[tab] == ["c0"] and tabs(reopen)["current"] == []

    response = do_reopen(reopen, app_id, reason="Client asked to reconsider")
    assert response.status_code == 200, response.text
    assert response.json()["currentStage"] == stage and response.json()["status"] == "active"
    after = tabs(reopen)
    assert after["current"] == ["c0"] and after["declined"] == [] and after["rejected"] == []
    with reopen.sessions() as db:
        records = db.execute(text("SELECT stage, reopened_reason FROM crm_application_stages WHERE application_id = :a ORDER BY stage, attempt"), {"a": app_id}).all()
        assert [r.stage for r in records] == list(range(1, stage + 1))
        assert records[-1].reopened_reason == "Client asked to reconsider"   # the closing record no longer counts
        assert all(r.reopened_reason is None for r in records[:-1])         # the earlier completed stages stay as they were
    assert "feedback" not in (row(reopen, app_id).draft or {})            # the next move needs fresh feedback


def test_a_placement_that_was_only_unmapped_can_be_reopened_too(reopen):
    apps = mapped_candidates(reopen, 1)
    app_id = apps["c0"]
    as_user(reopen, "asha")
    assert reopen.client.post(f"/api/applications/{app_id}/unmap", json={}).status_code in (200, 204)
    assert row(reopen, app_id).closure_reason == "unmapped"
    response = do_reopen(reopen, app_id)
    assert response.status_code == 200, response.text
    assert tabs(reopen)["current"] == ["c0"]


def test_it_is_refused_while_the_candidate_is_on_another_job(reopen):
    apps = mapped_candidates(reopen, 1)
    as_user(reopen, "asha")
    assert record_call(reopen, "c0", interested="no").status_code == 200
    order2 = second_order(reopen)
    assert reopen.client.post("/api/applications", json={"candidate_id": "c0", "order_id": order2}).status_code == 201
    refused = do_reopen(reopen, apps["c0"])
    assert refused.status_code == 409 and "another job" in refused.text
    assert row(reopen, apps["c0"]).status == "closed"


@pytest.mark.parametrize("order_status, words", [("closed", "closed"), ("filled", "Open this order"), ("on_hold", "Open this order")])
def test_it_is_refused_on_an_order_that_is_not_open(reopen, order_status, words):
    apps = mapped_candidates(reopen, 1)
    as_user(reopen, "asha")
    assert record_call(reopen, "c0", interested="no").status_code == 200
    with reopen.sessions() as db:
        db.execute(text("UPDATE crm_job_orders SET status = :s WHERE id = 'order'"), {"s": order_status})
        db.commit()
    refused = do_reopen(reopen, apps["c0"])
    assert refused.status_code == 409 and words in refused.text
    assert row(reopen, apps["c0"]).status == "closed"


def test_a_stale_screen_gets_a_version_conflict_and_nothing_changes(reopen):
    apps = mapped_candidates(reopen, 1)
    app_id = apps["c0"]
    as_user(reopen, "asha")
    assert record_call(reopen, "c0", interested="no").status_code == 200
    stale = lock(reopen, app_id) - 1
    refused = do_reopen(reopen, app_id, version=stale)
    assert refused.status_code == 409 and "changed while you were editing" in refused.text
    assert row(reopen, app_id).status == "closed"


def test_only_the_people_who_may_work_the_candidate_can_reopen_and_the_reason_is_required(reopen):
    apps = mapped_candidates(reopen, 1)
    app_id = apps["c0"]
    as_user(reopen, "asha")
    assert record_call(reopen, "c0", interested="no").status_code == 200
    as_user(reopen, "ravi")   # another recruiter's candidate
    assert do_reopen(reopen, app_id).status_code == 403
    as_user(reopen, "asha")
    assert do_reopen(reopen, app_id, reason="  ").status_code == 422
    assert do_reopen(reopen, app_id, reason="ok").status_code == 422     # 3 characters at least
    assert row(reopen, app_id).status == "closed"
    as_user(reopen, "manager")   # the MD may work any candidate
    assert do_reopen(reopen, app_id).status_code == 200


def test_a_second_click_and_a_deployed_placement_get_plain_refusals(reopen):
    apps = mapped_candidates(reopen, 2)
    as_user(reopen, "asha")
    assert record_call(reopen, "c0", interested="no").status_code == 200
    first = do_reopen(reopen, apps["c0"])
    assert first.status_code == 200
    again = reopen.client.post(f"/api/applications/{apps['c0']}/reopen-application", json={"reason": "Clicked twice", "lock_version": first.json()["lockVersion"] - 1})
    assert again.status_code == 409 and "already open" in again.text
    set_application(reopen, apps["c1"], stage=13, status="deployed", candidate_status="deployed")
    deployed = do_reopen(reopen, apps["c1"])
    assert deployed.status_code == 409 and "deployed" in deployed.text
