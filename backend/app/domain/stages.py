"""
The eleven stages, server side.

The browser mirrors these rules in `frontend/src/shared/workflow/stages.ts`, but
the client is never trusted: whether a stage may be completed, and whether completing it
closes the application, is decided here.

Only stages 1, 2, 4 and 6 can close an application. Stage 6 may repeat after an unfit
result. Completing stage 11 deploys the candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

TOTAL_STAGES = 11


@dataclass(frozen=True)
class Evaluation:
    can_complete: bool
    missing: list[str]
    outcome: str
    closes: str | None = None  # a ClosureReason value
    closes_label: str | None = None


def _missing(data: dict[str, Any], required: list[tuple[str, str]]) -> list[str]:
    return [label for key, label in required if data.get(key) in (None, "", [], {})]


def _ok(outcome: str) -> Evaluation:
    return Evaluation(True, [], outcome)


def _need(missing: list[str]) -> Evaluation:
    return Evaluation(False, missing, "")


def _close(reason: str, label: str, outcome: str) -> Evaluation:
    return Evaluation(True, [], outcome, reason, label)


# ---------------------------------------------------------------- the rules

def _stage1(d: dict) -> Evaluation:
    if d.get("result") == "failed":
        return _close("prescreen_failed", "Failed prescreening", "Failed")
    if not d.get("result"):
        return _need(["Result"])
    return _ok("Passed")


def _stage2(d: dict) -> Evaluation:
    if d.get("forwarded") == "no":
        reason = d.get("reason")
        if not reason:
            return _need(["Reason not forwarded"])
        if reason == "resume_not_received":
            return _close("resume_not_received", "Resume not received", "Not forwarded")
        return _close("not_interested", "Candidate not interested", "Not forwarded")
    missing = _missing(d, [("forwarded", "Forwarded to client"), ("dateForwarded", "Date forwarded")])
    return _ok("Forwarded") if not missing else _need(missing)


def _stage3(d: dict) -> Evaluation:
    if d.get("scheduled") != "yes":
        return _need(["Interview scheduled"])
    missing = _missing(d, [("date", "Date"), ("mode", "Mode")])
    return _ok("Scheduled") if not missing else _need(missing)


def _stage4(d: dict) -> Evaluation:
    missing = _missing(d, [("result", "Result"), ("remarks", "Interview remarks")])
    if missing:
        return _need(missing)
    if d.get("result") == "rejected":
        return _close("interview_rejected", "Rejected at interview", "Rejected")
    return _ok("Selected")


def _stage5(d: dict) -> Evaluation:
    if d.get("obtained") != "yes":
        return _need(["Offer letter obtained"])
    missing = _missing(d, [("file", "Offer letter")])
    return _ok("Obtained") if not missing else _need(missing)


def _stage6(d: dict) -> Evaluation:
    if d.get("applied") != "yes":
        return _need(["Applied for medical"])
    result = d.get("result")
    if not result:
        return _need(["Result"])
    if result == "fit":
        return _ok("Fit")
    reapplying = d.get("reapplying")
    if reapplying == "yes":
        missing = _missing(d, [("retestDate", "New medical date")])
        return _need(missing or ["A fit result is needed to move on"])
    if reapplying == "no":
        return _close("medical_unfit", "Medically unfit, not reapplying", "Unfit")
    return _need(["Reapplying"])


def _stage7(d: dict) -> Evaluation:
    if d.get("collected") != "yes":
        return _need(["Original passport collected"])
    missing = _missing(d, [("collectedDate", "Date collected"), ("heldBy", "Held by")])
    return _ok("Collected") if not missing else _need(missing)


def _stage8(d: dict) -> Evaluation:
    if d.get("collected") != "yes":
        return _need(["Advance collected"])
    missing = _missing(d, [("amount", "Amount"), ("receipt", "Receipt")])
    return _ok("Collected") if not missing else _need(missing)


def _stage9(d: dict) -> Evaluation:
    if d.get("arrived") != "yes":
        return _need(["Visa arrived"])
    return _ok("Arrived")


def _stage10(d: dict) -> Evaluation:
    if d.get("done") != "yes":
        return _need(["Stamping done"])
    missing = _missing(d, [("file", "Stamped visa copy")])
    return _ok("Stamped") if not missing else _need(missing)


def _stage11(d: dict) -> Evaluation:
    missing = _missing(
        d,
        [("bookedBy", "Booked by"), ("travelDate", "Travel date"), ("file", "Ticket copy")],
    )
    return _ok("Booked") if not missing else _need(missing)


@dataclass(frozen=True)
class StageDef:
    no: int
    key: str
    title: str
    task_title: str
    closer: bool
    repeatable: bool
    evaluate: Callable[[dict], Evaluation]


STAGES: list[StageDef] = [
    StageDef(1, "prescreening", "Prescreening", "Prescreen the candidate", True, False, _stage1),
    StageDef(2, "forwarded", "Forwarded to client", "Forward CV to client", True, False, _stage2),
    StageDef(3, "interview_scheduling", "Interview scheduling", "Schedule the interview", False, False, _stage3),
    StageDef(4, "interview_result", "Interview result", "Record the interview result", True, False, _stage4),
    StageDef(5, "offer_letter", "Offer letter", "Obtain the offer letter", False, False, _stage5),
    StageDef(6, "medical", "Medical", "Complete the medical", True, True, _stage6),
    StageDef(7, "passport", "Passport", "Collect the original passport", False, False, _stage7),
    StageDef(8, "advance", "Advance payment", "Collect the advance", False, False, _stage8),
    StageDef(9, "visa_arrival", "Visa arrival", "Follow up the visa", False, False, _stage9),
    StageDef(10, "visa_stamping", "Visa stamping", "Get the visa stamped", False, False, _stage10),
    StageDef(11, "air_ticket", "Air ticket", "Book the air ticket", False, False, _stage11),
]


def stage_by_no(no: int) -> StageDef:
    if not 1 <= no <= TOTAL_STAGES:
        raise ValueError(f"stage {no} is out of range")
    return STAGES[no - 1]
