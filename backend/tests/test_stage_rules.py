"""Pure validation of mandatory PRD outcomes and malformed supplied values."""
import pytest

from app.domain.stages import (
    ADVANCE_STAGE, MEDICAL_STAGE, PASSPORT_STAGE, PCC_STAGE, REVIEW_STAGE, STAGES, STAMPING_STAGE, STOP_EXITS, TICKET_STAGE, TOTAL_STAGES,
    VISA_STAGE, blocked_message, stage_by_no, validate_payload,
)


@pytest.mark.parametrize(("stage", "data"), [
    (1, {"result": "unsupported"}),
    (4, {"result": "unsupported", "remarks": "reason"}),
    (5, {"result": "agreed", "file": "not-an-attachment"}),
    (5, {"result": "maybe"}),
    (8, {"result": "pending"}),
    (9, {"pccStatus": "maybe"}),
    (9, {"pccStatus": "received", "pccReceivedDate": "2026-02-30"}),
    (9, {"pccStatus": "received", "file": "not-an-attachment"}),
    (9, {"collected": "yes"}),    # a Passport answer is not a PCC field
    (6, {"collected": "yes", "amount": -100, "receipt": {"id": "receipt"}}),
    (6, {"collected": "yes", "amount": True, "receipt": {"id": "receipt"}}),
    (6, {"collected": "yes", "amount": "NaN", "receipt": {"id": "receipt"}}),
    (6, {"collected": "yes", "amount": 1.234, "receipt": {"id": "receipt"}}),
    (6, {"collected": "yes", "amount": 100, "amountCurrency": "XXX", "receipt": {"id": "receipt"}}),
    (12, {"done": "yes", "file": {"id": ""}}),
    (13, {"bookedBy": "unsupported", "travelDate": "not-a-date", "file": {"id": "ticket"}}),
    (3, {"scheduled": "yes", "date": "2026-02-30"}),
    (3, {"scheduled": "yes", "date": 0}),
    (3, {"scheduled": "yes", "date": "2026-10-21", "time": "25:00"}),
    (10, {"arrived": "yes", "googleReview": "false"}),
    (11, {"googleReview": "false"}),
    (11, {"googleReview": True, "file": {"id": ""}}),
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
    (6, {"collected": "yes", "amount": 100, "receipt": {"id": "uploaded"}}),
    (7, {"collected": "yes"}),
    (8, {"result": "fit"}),
    (9, {"pccStatus": "received", "pccAppliedDate": "2026-10-01", "pccReceivedDate": "2026-10-20", "file": {"id": "uploaded"}}),
    (10, {"arrived": "yes", "googleReview": False}),
    (11, {"googleReview": True, "file": {"id": "uploaded"}}),
    (12, {"done": "yes", "file": {"id": "uploaded"}}),
    (13, {"file": {"id": "uploaded"}, "travelDate": "2026-10-21"}),
])
def test_prd_completion_columns_do_not_gain_extra_blockers(stage, data):
    assert stage_by_no(stage).evaluate(data).can_complete


def test_medical_repeat_is_recordable_but_does_not_advance():
    result = stage_by_no(8).evaluate({
        "applied": "yes", "appliedDate": "2026-10-21", "result": "unfit",
        "reapplying": "yes", "retestDate": "2026-10-25",
    })
    assert result.can_complete and result.repeats and not result.closes
    assert result.outcome == "Unfit: reapplying"
    assert not stage_by_no(8).evaluate({
        "result": "unfit", "reapplying": "yes", "retestDate": "2026-10-20", "appliedDate": "2026-10-21",
    }).can_complete


@pytest.mark.parametrize(("stage", "data", "reason"), [
    (1, {"result": "failed"}, "prescreen_failed"),
    (2, {"forwarded": "no", "reason": "not_interested"}, "not_interested"),
    (2, {"forwarded": "no", "reason": "resume_not_received"}, "resume_not_received"),
    (4, {"result": "rejected", "remarks": "Interview feedback"}, "interview_rejected"),
    (5, {"result": "declined"}, "offer_declined"),
    (5, {"result": "not_received"}, "offer_not_received"),
    (8, {"result": "unfit"}, "medical_unfit"),
    (8, {"result": "unfit", "reapplying": "no"}, "medical_unfit"),
])
def test_exact_negative_exits(stage, data, reason):
    result = stage_by_no(stage).evaluate(data)
    assert result.can_complete and result.closes == reason


def test_drafts_allow_incomplete_values_and_normalize_money_and_whitespace():
    assert validate_payload(1, {"remarks": "  In progress  "}) == {"remarks": "In progress"}
    assert validate_payload(6, {"amount": "10.25"}) == {"amount": 10.25, "amountCurrency": "INR"}


@pytest.mark.parametrize("stage", [1, 2, 3, 6, 7, 9, 10, 11, 12])
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
    (6, {"collected": "yes"}, "Collected"),
    (6, {"advanceStatus": "pending"}, "Advance pending"),
    (7, {"collected": "yes"}, "Collected"),
    (8, {"result": "fit"}, "Fit"),
    (9, {"pccStatus": "received"}, "Received"),
    (9, {"pccStatus": "pending"}, "PCC pending"),
    (11, {"googleReview": True}, "Obtained"),
    (11, {"file": {"id": "shot"}}, "Obtained"),
    (11, {}, "Completed manually"),
    (12, {"done": "yes"}, "Stamped"),
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
    result = stage_by_no(13).evaluate(data)
    assert not result.can_complete
    assert result.missing == missing


def test_ticket_stage_deploys_only_with_real_ticket_and_travel_date():
    result = stage_by_no(13).evaluate({"file": {"id": "uploaded"}, "travelDate": "2026-10-21"})
    assert result.can_complete
    assert result.outcome == "Booked"


@pytest.mark.parametrize(("stage", "data"), [
    (2, {"forwarded": "no"}),
    (8, {"result": "unfit", "reapplying": "yes"}),
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
    (8, {"result": "fit"}, "Fit", None),
    (8, {"result": "unfit"}, "Unfit", "medical_unfit"),
]


@pytest.mark.parametrize(("stage", "data", "outcome", "closes"), DECISIONS)
def test_each_decision_either_advances_or_stops_the_candidate(stage, data, outcome, closes):
    result = stage_by_no(stage).evaluate({"feedback": "Recruiter note", **data})
    assert result.can_complete and result.outcome == outcome
    assert result.closes == closes and not result.repeats


@pytest.mark.parametrize(("stage", "label"), [(4, "Interview result"), (5, "Offer letter result"), (8, "Medical result")])
def test_a_decision_stage_cannot_be_moved_without_the_choice(stage, label):
    for data in ({}, {"feedback": "Recruiter note"}, {"feedback": "Recruiter note", "applied": "yes"} if stage == 8 else {"feedback": "Recruiter note"}):
        result = stage_by_no(stage).evaluate(data)
        assert not result.can_complete and result.missing == [label]
        assert blocked_message(stage, result.missing) != "This stage is not complete."


def test_blocked_message_is_generic_when_something_else_is_wrong():
    assert blocked_message(4, ["Stage feedback"]) == "This stage is not complete."
    assert blocked_message(2, ["Reason not forwarded"]) == "This stage is not complete."


def test_only_the_positive_outcomes_ever_reach_the_next_stage():
    advancing = [(stage, outcome) for stage, data, outcome, closes in DECISIONS if closes is None]
    assert advancing == [(4, "Selected"), (5, "Agreed"), (8, "Fit")]


def test_an_older_offer_answer_does_not_stand_in_for_the_new_choice():
    # `obtained` is still accepted so old records and reopened drafts load, but it no longer decides anything.
    assert not stage_by_no(5).evaluate({"obtained": "yes", "file": {"id": "uploaded"}}).can_complete
    assert validate_payload(5, {"obtained": "yes", "result": "agreed"}) == {"obtained": "yes", "result": "agreed"}


def test_unfit_stops_unless_a_retest_is_planned():
    assert stage_by_no(8).evaluate({"result": "unfit"}).closes == "medical_unfit"
    retest = stage_by_no(8).evaluate({"result": "unfit", "reapplying": "yes", "retestDate": "2026-11-20"})
    assert retest.repeats and not retest.closes and retest.outcome == "Unfit: reapplying"


def test_every_decision_stage_can_close_an_application():
    assert [stage_by_no(n).closer for n in (4, 5, 8)] == [True, True, True]


# ---------------------------------------------------- thirteen stages: Advance (6), Passport (7), Medical (8), PCC (9), Google review (11)

def test_the_workflow_has_thirteen_stages_in_order():
    assert TOTAL_STAGES == 13 and len(STAGES) == 13
    assert [stage.no for stage in STAGES] == list(range(1, 14))
    assert [stage.key for stage in STAGES] == ["prescreening", "forwarded", "interview_scheduling", "interview_result", "offer_letter", "advance",
                                              "passport", "medical", "pcc", "visa_arrival", "google_review", "visa_stamping", "air_ticket"]
    assert [stage.title for stage in STAGES][5:11] == ["Advance payment", "Passport", "Medical", "PCC", "Visa arrival", "Google review"]
    assert [stage.task_title for stage in STAGES][5:9] == ["Collect the advance", "Collect original passport", "Complete the medical", "Collect the police clearance (PCC)"]
    assert len({stage.task_title for stage in STAGES}) == 13
    assert stage_by_no(8).repeatable and not stage_by_no(9).repeatable
    assert (ADVANCE_STAGE, PASSPORT_STAGE, MEDICAL_STAGE, PCC_STAGE, VISA_STAGE, REVIEW_STAGE, STAMPING_STAGE, TICKET_STAGE) == (6, 7, 8, 9, 10, 11, 12, 13)
    with pytest.raises(ValueError):
        stage_by_no(14)


def test_stop_exits_sit_on_the_stage_that_owns_them():
    assert STOP_EXITS[6]["not_paid"][0] == "advance_not_paid"
    assert STOP_EXITS[7]["not_available"][0] == "passport_not_available"
    assert STOP_EXITS[9]["not_cleared"][0] == "pcc_not_cleared"
    assert STOP_EXITS[10]["visa_rejected"][0] == "visa_rejected"
    assert STOP_EXITS[12]["refused"][0] == "visa_stamping_refused"
    assert set(STOP_EXITS[13]) == {"not_travelling"}
    assert "not_paid" not in STOP_EXITS[8] and "not_cleared" not in STOP_EXITS[8]
    assert stage_by_no(9).evaluate({"stop": "not_cleared"}).closes == "pcc_not_cleared"
    assert stage_by_no(6).evaluate({"stop": "not_paid"}).closes == "advance_not_paid"


def test_medical_takes_only_medical_fields_and_pcc_only_pcc_fields():
    assert validate_payload(8, {"applied": "yes", "appliedDate": "2026-10-21", "result": "fit"}) == {"applied": "yes", "appliedDate": "2026-10-21", "result": "fit"}
    for pcc_only in ({"pccStatus": "received"}, {"pccAppliedDate": "2026-10-21"}, {"file": {"id": "x"}}):
        assert not stage_by_no(8).evaluate({"result": "fit", **pcc_only}).can_complete
    for medical_only in ({"result": "fit"}, {"applied": "yes"}, {"retestDate": "2026-10-21"}):
        assert not stage_by_no(9).evaluate(medical_only).can_complete
    data = validate_payload(9, {"pccStatus": "received", "pccAppliedDate": "2026-10-01", "pccReceivedDate": "2026-10-20", "file": {"id": "pcc", "name": "pcc.pdf"}})
    assert data == {"pccStatus": "received", "pccAppliedDate": "2026-10-01", "pccReceivedDate": "2026-10-20", "file": {"id": "pcc", "name": "pcc.pdf"}}


def test_pcc_needs_only_feedback_and_names_its_outcome():
    assert stage_by_no(9).evaluate({"feedback": "Applied at the police station"}).outcome == "Completed manually"
    assert stage_by_no(9).evaluate({"pccStatus": "received"}).outcome == "Received"
    pending = stage_by_no(9).evaluate({"pccStatus": "pending"})
    assert pending.can_complete and pending.outcome == "PCC pending" and not pending.closes and not pending.repeats
    assert validate_payload(9, {"feedback": "  ok  "}) == {"feedback": "ok"}


def test_google_review_is_optional_and_the_visa_stage_still_accepts_a_legacy_tick():
    assert stage_by_no(11).evaluate({"feedback": "Asked the candidate"}).outcome == "Completed manually"
    assert stage_by_no(11).evaluate({"googleReview": True}).outcome == "Obtained"
    assert stage_by_no(11).evaluate({"googleReview": False}).outcome == "Completed manually"
    assert validate_payload(11, {"googleReview": True, "file": {"id": "shot"}}) == {"googleReview": True, "file": {"id": "shot"}}
    # The tick no longer belongs to the visa stage, but a value saved there earlier is still accepted.
    assert validate_payload(10, {"arrived": "yes", "googleReview": True}) == {"arrived": "yes", "googleReview": True}
    assert stage_by_no(10).evaluate({"arrived": "yes"}).outcome == "Arrived"
