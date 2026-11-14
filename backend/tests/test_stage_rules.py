"""Pure validation of mandatory PRD outcomes and malformed supplied values."""
import pytest

from app.domain.stages import blocked_message, stage_by_no, validate_payload


@pytest.mark.parametrize(("stage", "data"), [
    (1, {"result": "unsupported"}),
    (4, {"result": "unsupported", "remarks": "reason"}),
    (5, {"result": "agreed", "file": "not-an-attachment"}),
    (5, {"result": "maybe"}),
    (6, {"result": "pending"}),
    (8, {"collected": "yes", "amount": -100, "receipt": {"id": "receipt"}}),
    (8, {"collected": "yes", "amount": True, "receipt": {"id": "receipt"}}),
    (8, {"collected": "yes", "amount": "NaN", "receipt": {"id": "receipt"}}),
    (8, {"collected": "yes", "amount": 1.234, "receipt": {"id": "receipt"}}),
    (8, {"collected": "yes", "amount": 100, "amountCurrency": "XXX", "receipt": {"id": "receipt"}}),
    (10, {"done": "yes", "file": {"id": ""}}),
    (11, {"bookedBy": "unsupported", "travelDate": "not-a-date", "file": {"id": "ticket"}}),
    (3, {"scheduled": "yes", "date": "2026-02-30"}),
    (3, {"scheduled": "yes", "date": 0}),
    (3, {"scheduled": "yes", "date": "2026-10-21", "time": "25:00"}),
    (9, {"arrived": "yes", "googleReview": "false"}),
    (1, {"result": "passed", "arbitrary": "value"}),
])
def test_invalid_values_never_complete(stage, data):
    assert not stage_by_no(stage).evaluate(data).can_complete


@pytest.mark.parametrize(("stage", "data"), [
    (1, {"result": "passed"}),
    (2, {"forwarded": "yes"}),
    (3, {"scheduled": "yes", "date": "2026-10-21"}),
    (4, {"result": "selected", "remarks": " Meets requirements "}),
    (5, {"result": "agreed", "file": {"id": "uploaded"}}),
    (6, {"result": "fit"}),
    (7, {"collected": "yes"}),
    (8, {"collected": "yes", "amount": 100, "receipt": {"id": "uploaded"}}),
    (9, {"arrived": "yes", "googleReview": False}),
    (10, {"done": "yes", "file": {"id": "uploaded"}}),
    (11, {"file": {"id": "uploaded"}, "travelDate": "2026-10-21"}),
])
def test_prd_completion_columns_do_not_gain_extra_blockers(stage, data):
    assert stage_by_no(stage).evaluate(data).can_complete


def test_medical_repeat_is_recordable_but_does_not_advance():
    result = stage_by_no(6).evaluate({
        "applied": "yes", "appliedDate": "2026-10-21", "result": "unfit",
        "reapplying": "yes", "retestDate": "2026-10-25",
    })
    assert result.can_complete and result.repeats and not result.closes
    assert result.outcome == "Unfit: reapplying"
    assert not stage_by_no(6).evaluate({
        "result": "unfit", "reapplying": "yes", "retestDate": "2026-10-20", "appliedDate": "2026-10-21",
    }).can_complete


@pytest.mark.parametrize(("stage", "data", "reason"), [
    (1, {"result": "failed"}, "prescreen_failed"),
    (2, {"forwarded": "no", "reason": "not_interested"}, "not_interested"),
    (2, {"forwarded": "no", "reason": "resume_not_received"}, "resume_not_received"),
    (4, {"result": "rejected", "remarks": "Interview feedback"}, "interview_rejected"),
    (5, {"result": "declined"}, "offer_declined"),
    (5, {"result": "not_received"}, "offer_not_received"),
    (6, {"result": "unfit"}, "medical_unfit"),
    (6, {"result": "unfit", "reapplying": "no"}, "medical_unfit"),
])
def test_exact_negative_exits(stage, data, reason):
    result = stage_by_no(stage).evaluate(data)
    assert result.can_complete and result.closes == reason


def test_drafts_allow_incomplete_values_and_normalize_money_and_whitespace():
    assert validate_payload(1, {"remarks": "  In progress  "}) == {"remarks": "In progress"}
    assert validate_payload(8, {"amount": "10.25"}) == {"amount": 10.25, "amountCurrency": "INR"}


@pytest.mark.parametrize("stage", [1, 2, 3, 7, 8, 9, 10])
def test_feedback_only_stage_move_never_invents_documents_results_or_dates(stage):
    data = validate_payload(stage, {"feedback": "  Recruiter confirmed this manual move.  "})
    assert data["feedback"] == "Recruiter confirmed this manual move."
    result = stage_by_no(stage).evaluate(data)
    assert result.can_complete and result.outcome == "Completed manually"
    assert not result.closes and not result.repeats
    assert not ({"file", "receipt", "travelDate", "date", "result", "amount"} & data.keys())


@pytest.mark.parametrize(("stage", "data", "outcome"), [
    (3, {"scheduled": "yes"}, "Scheduled"),
    (4, {"result": "selected"}, "Selected"),
    (5, {"result": "agreed"}, "Agreed"),
    (6, {"result": "fit"}, "Fit"),
    (8, {"collected": "yes"}, "Collected"),
    (10, {"done": "yes"}, "Stamped"),
])
def test_optional_documents_and_details_do_not_gate_a_manual_move(stage, data, outcome):
    result = stage_by_no(stage).evaluate(data)
    assert result.can_complete and result.outcome == outcome


@pytest.mark.parametrize(("data", "missing"), [
    ({}, ["Air ticket", "Travel date"]),
    ({"file": {"id": "uploaded"}}, ["Travel date"]),
    ({"travelDate": "2026-10-21"}, ["Air ticket"]),
])
def test_ticket_stage_requires_real_ticket_and_travel_date_before_deployment(data, missing):
    result = stage_by_no(11).evaluate(data)
    assert not result.can_complete
    assert result.missing == missing


def test_ticket_stage_deploys_only_with_real_ticket_and_travel_date():
    result = stage_by_no(11).evaluate({"file": {"id": "uploaded"}, "travelDate": "2026-10-21"})
    assert result.can_complete
    assert result.outcome == "Booked"


@pytest.mark.parametrize(("stage", "data"), [
    (2, {"forwarded": "no"}),
    (6, {"result": "unfit", "reapplying": "yes"}),
])
def test_explicit_negative_outcomes_still_require_a_decision(stage, data):
    assert not stage_by_no(stage).evaluate({"feedback": "Follow up needed", **data}).can_complete


# ---------------------------------------------------- decision stages: interview result, offer letter, medical

DECISIONS = [
    # stage, data, outcome, closes (None = the candidate goes to the next stage)
    (4, {"result": "selected"}, "Selected", None),
    (4, {"result": "rejected"}, "Rejected", "interview_rejected"),
    (5, {"result": "agreed"}, "Agreed", None),
    (5, {"result": "declined"}, "Declined", "offer_declined"),
    (5, {"result": "not_received"}, "Not received", "offer_not_received"),
    (6, {"result": "fit"}, "Fit", None),
    (6, {"result": "unfit"}, "Unfit", "medical_unfit"),
]


@pytest.mark.parametrize(("stage", "data", "outcome", "closes"), DECISIONS)
def test_each_decision_either_advances_or_stops_the_candidate(stage, data, outcome, closes):
    result = stage_by_no(stage).evaluate({"feedback": "Recruiter note", **data})
    assert result.can_complete and result.outcome == outcome
    assert result.closes == closes and not result.repeats


@pytest.mark.parametrize(("stage", "label"), [(4, "Interview result"), (5, "Offer letter result"), (6, "Medical result")])
def test_a_decision_stage_cannot_be_moved_without_the_choice(stage, label):
    for data in ({}, {"feedback": "Recruiter note"}, {"feedback": "Recruiter note", "applied": "yes"} if stage == 6 else {"feedback": "Recruiter note"}):
        result = stage_by_no(stage).evaluate(data)
        assert not result.can_complete and result.missing == [label]
        assert blocked_message(stage, result.missing) != "This stage is not complete."


def test_blocked_message_is_generic_when_something_else_is_wrong():
    assert blocked_message(4, ["Stage feedback"]) == "This stage is not complete."
    assert blocked_message(2, ["Reason not forwarded"]) == "This stage is not complete."


def test_only_the_positive_outcomes_ever_reach_the_next_stage():
    advancing = [(stage, outcome) for stage, data, outcome, closes in DECISIONS if closes is None]
    assert advancing == [(4, "Selected"), (5, "Agreed"), (6, "Fit")]


def test_an_older_offer_answer_does_not_stand_in_for_the_new_choice():
    # `obtained` is still accepted so old records and reopened drafts load, but it no longer decides anything.
    assert not stage_by_no(5).evaluate({"obtained": "yes", "file": {"id": "uploaded"}}).can_complete
    assert validate_payload(5, {"obtained": "yes", "result": "agreed"}) == {"obtained": "yes", "result": "agreed"}


def test_unfit_stops_unless_a_retest_is_planned():
    assert stage_by_no(6).evaluate({"result": "unfit"}).closes == "medical_unfit"
    retest = stage_by_no(6).evaluate({"result": "unfit", "reapplying": "yes", "retestDate": "2026-11-20"})
    assert retest.repeats and not retest.closes and retest.outcome == "Unfit: reapplying"


def test_every_decision_stage_can_close_an_application():
    assert [stage_by_no(n).closer for n in (4, 5, 6)] == [True, True, True]
