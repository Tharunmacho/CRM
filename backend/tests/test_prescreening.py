"""Candidate-level Prescreening (`/api/candidates/{id}/prescreen`): stage 1 without a client or job.

Owner's rule: Prescreening is open without a client and a job (save feedback, decline, reject, move on); a client AND a job are
mandatory from stage 2, so only `forward` needs an order. Runs on the explicitly guarded disposable PostgreSQL DB with the same desk
fixture as test_daily_work; the Tasks views are read through the real endpoint, so the very next read proves the live behaviour.
"""
import pytest
from sqlalchemy import text

from app.api.routers import prescreening as prescreening_router
from app.db.models import Order, OrderStatus
from test_daily_work import (  # noqa: F401  (desk is a fixture)
    as_user, desk, follow, ids, listing, mapped_candidates, owned_candidates, record_call, second_order, to_stage,
)
from test_daily_work_views import view
from test_database_naming import schema_database  # noqa: F401  (fixture used by desk)

URL = "/api/candidates/{}/prescreen"


@pytest.fixture
def pre(desk):
    desk.app.include_router(prescreening_router.router)
    return desk


def act(env, candidate_id, action, **body):
    return env.client.post(URL.format(candidate_id), json={"action": action, **body})


def tabs(env, user="asha"):
    return {name: ids(view(env, user, name)) for name in ("current", "declined", "rejected", "all")}


def followed(env, user="asha"):
    """Candidates on the Follow-up list (stage 2 onward): the Tasks screen shows Today's list and this one together."""
    return {item["candidateId"]: item["stage"] for item in follow(env, user)["items"]}


def audit_actions(env, entity_id):
    with env.sessions() as db:
        return [row[0] for row in db.execute(text("SELECT action FROM system_audit_log WHERE entity_id = :e ORDER BY at, id"), {"e": entity_id})]


def placements(env, candidate_id):
    with env.sessions() as db:
        return db.execute(text("SELECT id, order_id, status::text, current_stage FROM crm_applications WHERE candidate_id = :c ORDER BY created_at"),
                          {"c": candidate_id}).all()


def test_the_state_of_a_candidate_with_no_job_starts_not_started_and_is_readable_by_anyone_who_may_see_the_candidate(pre):
    owned_candidates(pre, 1)
    as_user(pre, "asha")
    body = pre.client.get(URL.format("u100")).json()
    assert body["state"] == "not_started" and body["canWork"] is True and body["feedback"] == ""
    as_user(pre, "ravi")
    assert pre.client.get(URL.format("u100")).json()["canWork"] is False   # another recruiter's candidate: may look, may not work
    assert pre.client.get(URL.format("nobody")).status_code == 404


def test_saving_feedback_needs_no_client_or_job_and_keeps_the_candidate_on_the_list(pre):
    owned_candidates(pre, 1)
    as_user(pre, "asha")
    assert act(pre, "u100", "save", feedback="   ").status_code == 422        # blank feedback is not a save
    response = act(pre, "u100", "save", feedback="Spoke to him: good English, wants the Gulf.")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "in_progress"
    state = pre.client.get(URL.format("u100")).json()
    assert state["state"] == "in_progress" and state["feedback"].startswith("Spoke to him") and state["savedAt"]
    assert "u100" in tabs(pre)["current"] and placements(pre, "u100") == []   # still working it, no job needed
    assert "Saved prescreening feedback" in audit_actions(pre, "u100")
    # a second save replaces what the panel shows and keeps the earlier text in the history
    act(pre, "u100", "save", feedback="Second call: confirmed his passport is valid.")
    assert pre.client.get(URL.format("u100")).json()["feedback"].startswith("Second call")
    assert audit_actions(pre, "u100").count("Saved prescreening feedback") == 2


def test_declining_without_a_job_puts_the_candidate_on_declined_at_once(pre):
    owned_candidates(pre, 2)
    as_user(pre, "asha")
    assert "u100" in tabs(pre)["current"]
    before = view(pre, "asha", "current")["viewCounts"]
    response = act(pre, "u100", "decline", feedback="Does not want to work abroad now.")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "declined" and "Declined" in response.json()["notice"]
    after = tabs(pre)
    assert after["declined"] == ["u100"] and "u100" not in after["current"] and "u100" in after["all"] and "u101" in after["current"]
    counts = view(pre, "asha", "current")["viewCounts"]
    assert counts["declined"] == before["declined"] + 1   # the tab badge moves in the same read
    assert pre.client.get(URL.format("u100")).json()["state"] == "declined"
    assert placements(pre, "u100") == []                  # nothing was created: no client, no job


def test_rejecting_without_a_job_puts_the_candidate_on_rejected_at_once(pre):
    owned_candidates(pre, 1)
    as_user(pre, "asha")
    response = act(pre, "u100", "reject", feedback="Not suitable: no experience.")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "rejected" and "Rejected" in response.json()["notice"]
    after = tabs(pre)
    assert after["rejected"] == ["u100"] and after["declined"] == [] and "u100" not in after["current"] and after["all"] == ["u100"]
    # a later Yes call brings the candidate back to today's list (the same revive rule as the call dialog)
    assert record_call(pre, "u100", interested="yes").status_code == 200
    assert "u100" in tabs(pre)["current"] and tabs(pre)["rejected"] == []


def test_forwarding_needs_the_client_job_but_feedback_is_optional(pre):
    owned_candidates(pre, 1)
    as_user(pre, "asha")
    no_job = act(pre, "u100", "forward", feedback="Passed prescreening.")
    assert no_job.status_code == 422 and "required from stage 2" in no_job.json()["detail"]
    assert act(pre, "u100", "forward", orderId="", feedback="Passed prescreening.").status_code == 422
    assert placements(pre, "u100") == [] and "u100" in tabs(pre)["current"]
    no_feedback = act(pre, "u100", "forward", orderId="order")
    assert no_feedback.status_code == 200, no_feedback.text
    assert no_feedback.json()["currentStage"] == 2


def test_forwarding_completes_stage_one_and_leaves_the_placement_on_stage_two_with_its_task(pre):
    owned_candidates(pre, 1)
    as_user(pre, "asha")
    act(pre, "u100", "save", feedback="Early notes.")
    response = act(pre, "u100", "forward", orderId="order", feedback="Passed prescreening: ready for the client.")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currentStage"] == 2 and body["taskId"] and body["repeated"] is False and body["state"] == "on_job"
    (app_id, order_id, status, stage), = placements(pre, "u100")
    assert (order_id, status, stage) == ("order", "active", 2)
    with pre.sessions() as db:
        records = db.execute(text("SELECT stage, attempt, outcome, data FROM crm_application_stages WHERE application_id = :a"), {"a": app_id}).all()
        assert [(r.stage, r.attempt, r.outcome) for r in records] == [(1, 1, "Passed")]
        assert records[0].data["feedback"].startswith("Passed prescreening") and records[0].data["result"] == "passed"
        open_tasks = db.execute(text("SELECT stage, kind::text FROM workflow_tasks WHERE candidate_id = 'u100' AND completed_at IS NULL AND cancelled_at IS NULL")).all()
        assert [(t.stage, t.kind) for t in open_tasks] == [(2, "stage")]        # the initial call and the stage-1 tasks are closed
        done = db.execute(text("SELECT count(*) FROM workflow_tasks WHERE candidate_id = 'u100' AND stage = 1 AND completed_at IS NOT NULL")).scalar_one()
        assert done == 1
        candidate = db.execute(text("SELECT status::text, call_interested, call_called FROM crm_candidates WHERE id = 'u100'")).one()
        assert tuple(candidate) == ("in_process", "yes", True)
    actions = audit_actions(pre, app_id)
    assert "Mapped candidate to order" in actions and "Recorded stage 1: Passed" in actions and "Forwarded from candidate prescreening" in actions
    # the very next read: the placement is on the Follow-up list at stage 2 (the Tasks screen shows it there), on no outcome tab, and in no
    # other tab twice
    assert followed(pre) == {"u100": 2}
    after = tabs(pre)
    assert "u100" not in after["current"] and after["declined"] == [] and after["rejected"] == []
    # the stage window continues normally from stage 2
    assert pre.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"] >= 1


def test_a_repeated_forward_answers_with_what_it_did_and_writes_nothing_more(pre):
    owned_candidates(pre, 1)
    as_user(pre, "asha")
    first = act(pre, "u100", "forward", orderId="order", feedback="Passed prescreening.")
    assert first.status_code == 200
    again = act(pre, "u100", "forward", orderId="order", feedback="Passed prescreening.")
    assert again.status_code == 200 and again.json()["repeated"] is True and again.json()["currentStage"] == 2
    assert len(placements(pre, "u100")) == 1
    assert audit_actions(pre, first.json()["applicationId"]).count("Forwarded from candidate prescreening") == 1


def test_forwarding_is_refused_for_a_filled_order_and_for_a_candidate_who_is_already_on_a_job(pre):
    owned_candidates(pre, 2)
    as_user(pre, "asha")
    with pre.sessions() as db:
        db.get(Order, "order").status = OrderStatus.filled
        db.commit()
    filled = act(pre, "u100", "forward", orderId="order", feedback="Passed prescreening.")
    assert filled.status_code == 409 and "Open this order" in filled.json()["detail"]
    assert placements(pre, "u100") == []
    with pre.sessions() as db:
        db.get(Order, "order").status = OrderStatus.open
        db.commit()
    other = second_order(pre)
    assert act(pre, "u101", "forward", orderId="order", feedback="Passed prescreening.").status_code == 200
    busy = act(pre, "u101", "forward", orderId=other, feedback="Passed prescreening.")
    assert busy.status_code == 409 and "already on an order" in busy.json()["detail"]
    assert len(placements(pre, "u101")) == 1


def test_a_candidate_past_prescreening_is_handled_in_the_stage_window_not_here(pre):
    apps = mapped_candidates(pre, 1)
    as_user(pre, "asha")
    to_stage(pre, apps["c0"], 3)
    for action, extra in (("save", {"feedback": "x"}), ("decline", {}), ("reject", {})):
        response = act(pre, "c0", action, **extra)
        assert response.status_code == 409 and "already on a job" in response.json()["detail"], (action, response.text)
    assert act(pre, "c0", "forward", orderId="order", feedback="x").status_code == 409
    assert placements(pre, "c0")[0].current_stage == 3        # nothing moved


def test_declining_a_candidate_whose_job_is_still_at_prescreening_closes_that_job_like_the_call_dialog(pre):
    apps = mapped_candidates(pre, 1)
    as_user(pre, "asha")
    response = act(pre, "c0", "decline", feedback="Not interested any more.")
    assert response.status_code == 200, response.text
    (_id, _order, status, _stage), = placements(pre, "c0")
    assert status == "closed"
    assert tabs(pre)["declined"] == ["c0"] and "c0" not in tabs(pre)["current"]


def test_only_people_who_may_work_the_candidate_can_prescreen_and_a_manager_can_for_anyone(pre):
    owned_candidates(pre, 1)
    as_user(pre, "ravi")                                        # another recruiter's candidate
    for action, extra in (("save", {"feedback": "x"}), ("decline", {}), ("reject", {}), ("forward", {"orderId": "order", "feedback": "x"})):
        assert act(pre, "u100", action, **extra).status_code == 403, action
    assert placements(pre, "u100") == [] and tabs(pre, "asha")["declined"] == []
    as_user(pre, "manager")
    assert act(pre, "u100", "decline", feedback="Checked by the manager.").status_code == 200
    assert tabs(pre, "asha")["declined"] == ["u100"]
    assert act(pre, "u100", "unknown").status_code == 422        # only the four actions exist


def test_every_candidate_is_on_exactly_one_list_after_a_mix_of_prescreening_actions(pre):
    owned_candidates(pre, 5)
    as_user(pre, "asha")
    act(pre, "u100", "save", feedback="Working on him.")
    act(pre, "u101", "decline", feedback="No.")
    act(pre, "u102", "reject", feedback="Not suitable.")
    act(pre, "u103", "forward", orderId="order", feedback="Passed.")
    after, moved = tabs(pre), followed(pre)
    everyone = {f"u{n}" for n in range(100, 105)}
    # Today's list + Declined + Rejected + Follow-up together hold every candidate once: nobody missing, nobody twice
    seen = after["current"] + after["declined"] + after["rejected"] + list(moved)
    assert sorted(seen) == sorted(everyone)
    assert after["declined"] == ["u101"] and after["rejected"] == ["u102"] and moved == {"u103": 2}
    assert set(after["current"]) == {"u100", "u104"}
    assert set(after["all"]) == everyone - {"u103"}       # 'All' is Today's list plus the outcome tabs; Follow-up has its own list
