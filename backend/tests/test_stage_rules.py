"""Pure validation of mandatory PRD outcomes and malformed supplied values."""
import pytest

from app.domain.stages import stage_by_no, validate_payload


@pytest.mark.parametrize(("stage", "data"), [
    (1, {"result": "unsupported"}),
    (4, {"result": "unsupported", "remarks": "reason"}),
    (4, {"result": "selected", "remarks": "   "}),
    (5, {"obtained": "yes", "file": "not-an-attachment"}),
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
    (5, {"obtained": "yes", "file": {"id": "uploaded"}}),
    (6, {"result": "fit"}),
    (7, {"collected": "yes"}),
    (8, {"collected": "yes", "amount": 100, "receipt": {"id": "uploaded"}}),
    (9, {"arrived": "yes", "googleReview": False}),
    (10, {"done": "yes", "file": {"id": "uploaded"}}),
    (11, {"file": {"id": "uploaded"}}),
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
    (6, {"result": "unfit", "reapplying": "no"}, "medical_unfit"),
])
def test_exact_negative_exits(stage, data, reason):
    result = stage_by_no(stage).evaluate(data)
    assert result.can_complete and result.closes == reason


def test_drafts_allow_incomplete_values_and_normalize_money_and_whitespace():
    assert validate_payload(1, {"remarks": "  In progress  "}) == {"remarks": "In progress"}
    assert validate_payload(8, {"amount": "10.25"}) == {"amount": 10.25, "amountCurrency": "INR"}
