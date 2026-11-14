"""Typed draft contracts and completion rules for the PRD's eleven stages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, time as Time
from decimal import Decimal
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

TOTAL_STAGES = 11
YesNo = Literal["yes", "no"]
# Last-resort values, used only when the database has no currency list or agency settings (for example a
# database that has not had migration 0017 yet). The real list and defaults are the `currencies` and
# `agency_settings` tables, administered under Users > Agency settings; see services/agency_settings.py.
CURRENCY_CODES = ("INR", "USD", "EUR", "SGD", "AED", "SAR", "QAR", "OMR", "KWD", "BHD", "GBP")
FALLBACK_ADVANCE_CURRENCY = "INR"


@dataclass(frozen=True)
class StageOptions:
    """Reference data a stage's values are checked against, read from the database by the caller."""
    currencies: frozenset[str] | None = None   # active currency codes; None means CURRENCY_CODES
    default_currency: str | None = None        # advance currency when none is chosen


def _context(options: "StageOptions | None") -> dict | None:
    return None if options is None else {"currencies": options.currencies, "default_currency": options.default_currency}


class FileRef(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=36)
    name: str | None = None
    url: str | None = None
    sizeKb: int | None = None
    version: int | None = None


class UserRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)


class StageInput(BaseModel):
    # Drafts may be incomplete, but submitted values must have the correct type.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Optional while drafting; the transition API requires fresh written feedback.
    feedback: str | None = Field(default=None, max_length=10000)
    # The stopping exit every stage offers: instead of moving on, the application closes here (see STOP_EXITS for what each stage allows).
    stop: Literal["rejected", "declined", "not_possible", "not_available", "not_paid", "visa_rejected", "refused", "not_travelling"] | None = None

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_unset(cls, value, info):
        if isinstance(value, str) and not value.strip():
            return None
        if value is not None and info.field_name in {
            "date", "dateForwarded", "appliedDate", "retestDate", "collectedDate", "travelDate",
        }:
            if not isinstance(value, (str, Date)) or (isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                raise ValueError("Use a date in YYYY-MM-DD format.")
        if value is not None and info.field_name == "time":
            if not isinstance(value, (str, Time)) or (isinstance(value, str) and not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value)):
                raise ValueError("Use a time in HH:MM format.")
        return value


class Prescreen(StageInput):
    result: Literal["passed", "failed"] | None = None
    remarks: str | None = Field(default=None, max_length=10000)


class Forward(StageInput):
    forwarded: YesNo | None = None
    dateForwarded: Date | None = None
    reason: Literal["not_interested", "resume_not_received"] | None = None


class InterviewSchedule(StageInput):
    scheduled: YesNo | None = None
    date: Date | None = None
    time: Time | None = None
    mode: Literal["in_person", "online"] | None = None
    venue: str | None = Field(default=None, max_length=1000)


class InterviewResult(StageInput):
    result: Literal["selected", "rejected"] | None = None
    remarks: str | None = Field(default=None, max_length=10000)


class Offer(StageInput):
    # The explicit outcome that moves (or stops) the candidate. `obtained` is the older yes/no answer; it is still accepted so
    # records and reopened drafts written before `result` existed keep loading, but it no longer decides anything.
    result: Literal["agreed", "declined", "not_received"] | None = None
    obtained: YesNo | None = None
    file: FileRef | None = None


class Medical(StageInput):
    applied: YesNo | None = None
    appliedDate: Date | None = None
    result: Literal["fit", "unfit"] | None = None
    reapplying: YesNo | None = None
    retestDate: Date | None = None


class Passport(StageInput):
    copy_: FileRef | None = Field(default=None, alias="copy")
    collected: YesNo | None = None
    collectedDate: Date | None = None
    heldBy: UserRef | None = None


class Advance(StageInput):
    collected: YesNo | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    amountCurrency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    date: Date | None = None
    mode: Literal["cash", "bank", "upi", "cheque"] | None = None
    receiptNo: str | None = Field(default=None, max_length=100)
    receipt: FileRef | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def amount_is_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Enter a monetary amount.")
        return value

    @field_validator("amountCurrency")
    @classmethod
    def currency_is_offered(cls, value, info):
        allowed = (info.context or {}).get("currencies") or CURRENCY_CODES
        if value is not None and value not in allowed:
            raise PydanticCustomError("currency_not_offered", "Choose one of the available currencies.")
        return value

    @model_validator(mode="after")
    def default_currency(self, info):
        if self.amountCurrency is None:
            self.amountCurrency = (info.context or {}).get("default_currency") or FALLBACK_ADVANCE_CURRENCY
        return self


class VisaArrival(StageInput):
    arrived: YesNo | None = None
    date: Date | None = None
    file: FileRef | None = None
    googleReview: StrictBool | None = None


class VisaStamping(StageInput):
    done: YesNo | None = None
    date: Date | None = None
    file: FileRef | None = None


class Ticket(StageInput):
    bookedBy: Literal["employer", "agency"] | None = None
    travelDate: Date | None = None
    airline: str | None = Field(default=None, max_length=200)
    pnr: str | None = Field(default=None, max_length=80)
    file: FileRef | None = None


SCHEMAS = [Prescreen, Forward, InterviewSchedule, InterviewResult, Offer, Medical,
           Passport, Advance, VisaArrival, VisaStamping, Ticket]
FILE_FIELDS = {5: ("file",), 7: ("copy",), 8: ("receipt",), 9: ("file",), 10: ("file",), 11: ("file",)}


def validate_payload(stage: int, data: dict[str, Any], options: StageOptions | None = None) -> dict[str, Any]:
    """Normalize supplied optional details without inventing an outcome or date."""
    normalized = SCHEMAS[stage - 1].model_validate(data, context=_context(options)).model_dump(mode="json", by_alias=True, exclude_none=True)
    if "amount" in normalized:
        normalized["amount"] = float(normalized["amount"])
    return normalized


@dataclass(frozen=True)
class Evaluation:
    can_complete: bool
    missing: list[str]
    outcome: str
    closes: str | None = None
    closes_label: str | None = None
    repeats: bool = False


def _need(*labels: str) -> Evaluation:
    return Evaluation(False, list(labels), "")


def _ok(outcome: str) -> Evaluation:
    return Evaluation(True, [], outcome)


def _close(reason: str, label: str, outcome: str) -> Evaluation:
    return Evaluation(True, [], outcome, reason, label)


# The stopping exit of every stage: stage -> choice -> (closure reason, closure label, outcome written on the stage record).
# Rejected and Declined are available at stages 1-10; each stage adds its own wording; stage 11 only has Not travelling,
# because completing it is the one way to Deployed. Choosing one closes the application like the older negative exits.
_GENERIC_STOPS = {
    "rejected": ("stage_rejected", "Rejected", "Rejected"),
    "declined": ("stage_declined", "Candidate declined", "Declined"),
}
_OWN_STOPS: dict[int, dict[str, tuple[str, str, str]]] = {
    1: {"rejected": ("prescreen_failed", "Failed prescreening", "Failed")},
    3: {"not_possible": ("interview_not_possible", "Interview not possible", "Interview not possible")},
    4: {"rejected": ("interview_rejected", "Rejected at interview", "Rejected")},
    5: {"declined": ("offer_declined", "Offer declined", "Declined")},
    7: {"not_available": ("passport_not_available", "Passport not available", "Passport not available")},
    8: {"not_paid": ("advance_not_paid", "Advance not paid", "Not paid")},
    9: {"visa_rejected": ("visa_rejected", "Visa rejected", "Visa rejected")},
    10: {"refused": ("visa_stamping_refused", "Visa stamping refused", "Stamping refused")},
    11: {"not_travelling": ("not_travelling", "Not travelling", "Not travelling")},
}
STOP_EXITS: dict[int, dict[str, tuple[str, str, str]]] = {
    no: ({"not_travelling": _OWN_STOPS[11]["not_travelling"]} if no == TOTAL_STAGES
         else {**_GENERIC_STOPS, **_OWN_STOPS.get(no, {})})
    for no in range(1, TOTAL_STAGES + 1)
}
STOP_OUTCOMES = {no: {outcome for _reason, _label, outcome in exits.values()} for no, exits in STOP_EXITS.items()}


def _stopped(stage: int, choice: str) -> Evaluation:
    exit_ = STOP_EXITS[stage].get(choice)
    if exit_ is None:
        return _need("Stop reason: choose one of the options for this stage")
    return _close(*exit_)


def _file(d: dict, field: str) -> bool:
    return isinstance(d.get(field), dict) and bool(d[field].get("id"))


def _stage1(d: dict) -> Evaluation:
    if d.get("result") == "failed":
        return _close("prescreen_failed", "Failed prescreening", "Failed")
    return _ok("Passed") if d.get("result") == "passed" else _ok("Completed manually")


def _stage2(d: dict) -> Evaluation:
    if d.get("forwarded") == "no":
        if d.get("reason") == "resume_not_received":
            return _close("resume_not_received", "Resume not received", "Not forwarded")
        if d.get("reason") == "not_interested":
            return _close("not_interested", "Candidate not interested", "Not forwarded")
        return _need("Reason not forwarded")
    return _ok("Forwarded") if d.get("forwarded") == "yes" else _ok("Completed manually")


def _stage3(d: dict) -> Evaluation:
    return _ok("Scheduled") if d.get("scheduled") == "yes" else _ok("Completed manually")


# Stages 4, 5 and 6 are decisions, not paperwork: a candidate only moves on with the positive answer (Selected, Agreed,
# Fit). Every other answer stops them here, and an unanswered stage cannot be moved at all. The prompt is the one sentence
# the API and the stage screen give when the choice has not been made.
DECISION_STAGES = {
    4: ("Interview result", "Choose the interview result, Selected or Rejected, before moving this candidate."),
    5: ("Offer letter result", "Choose the offer letter result, Agreed, Declined or Not received, before moving this candidate."),
    6: ("Medical result", "Choose the medical result, Fit or Unfit, before moving this candidate."),
}


def blocked_message(stage: int, missing: list[str]) -> str:
    """The sentence for a 422 when a stage cannot be completed: the decision prompt when only that choice is absent."""
    decision = DECISION_STAGES.get(stage)
    if decision and decision[0] in missing:
        return decision[1]
    return "This stage is not complete."


def _stage4(d: dict) -> Evaluation:
    if d.get("result") == "rejected":
        return _close("interview_rejected", "Rejected at interview", "Rejected")
    return _ok("Selected") if d.get("result") == "selected" else _need(DECISION_STAGES[4][0])


def _stage5(d: dict) -> Evaluation:
    if d.get("result") == "declined":
        return _close("offer_declined", "Offer declined", "Declined")
    if d.get("result") == "not_received":
        return _close("offer_not_received", "Offer not received", "Not received")
    return _ok("Agreed") if d.get("result") == "agreed" else _need(DECISION_STAGES[5][0])


def _stage6(d: dict) -> Evaluation:
    if d.get("result") == "fit":
        return _ok("Fit")
    if d.get("result") != "unfit":
        return _need(DECISION_STAGES[6][0])
    if d.get("reapplying") == "yes":
        # The one way an Unfit candidate stays on this stage: a recorded retest. Nothing advances until a later result is Fit.
        if not d.get("retestDate"):
            return _need("New medical date")
        if d.get("appliedDate") and d["retestDate"] < d["appliedDate"]:
            return _need("A retest date on or after the previous medical date")
        return Evaluation(True, [], "Unfit: reapplying", repeats=True)
    return _close("medical_unfit", "Medically unfit", "Unfit")


def _stage7(d: dict) -> Evaluation:
    return _ok("Collected") if d.get("collected") == "yes" else _ok("Completed manually")


def _stage8(d: dict) -> Evaluation:
    return _ok("Collected") if d.get("collected") == "yes" else _ok("Completed manually")


def _stage9(d: dict) -> Evaluation:
    return _ok("Arrived") if d.get("arrived") == "yes" else _ok("Completed manually")


def _stage10(d: dict) -> Evaluation:
    return _ok("Stamped") if d.get("done") == "yes" else _ok("Completed manually")


def _stage11(d: dict) -> Evaluation:
    # A candidate becomes deployed only after the actual travel ticket is on
    # file and a travel date has been recorded.  Earlier stages can be moved
    # manually when a recruiter has documented their work, but deployment is
    # an operational status and must never be inferred from a note alone.
    missing: list[str] = []
    if not _file(d, "file"):
        missing.append("Air ticket")
    if not d.get("travelDate"):
        missing.append("Travel date")
    return _need(*missing) if missing else _ok("Booked")


def _validated(stage: int, evaluate: Callable[[dict], Evaluation]):
    def check(data: dict, options: StageOptions | None = None) -> Evaluation:
        try:
            clean = validate_payload(stage, data, options)
        except ValidationError as exc:
            return _need(*(f"{' / '.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors()))
        if clean.get("stop"):
            return _stopped(stage, clean["stop"])  # an explicit stop wins over the stage's own answers
        return evaluate(clean)
    return check


@dataclass(frozen=True)
class StageDef:
    no: int
    key: str
    title: str
    task_title: str
    closer: bool  # every stage can close the application: by its own answers or the stopping exit (STOP_EXITS)
    repeatable: bool
    evaluate: Callable[..., Evaluation]  # evaluate(data, options=None)


_DEFINITIONS = [
    ("prescreening", "Prescreening", "Prescreen the candidate", True, False, _stage1),
    ("forwarded", "Forwarded to client", "Forward CV to client", True, False, _stage2),
    ("interview_scheduling", "Interview scheduling", "Schedule the interview", True, False, _stage3),
    ("interview_result", "Interview result", "Record the interview result", True, False, _stage4),
    ("offer_letter", "Offer letter", "Obtain the offer letter", True, False, _stage5),
    ("medical", "Medical", "Complete the medical", True, True, _stage6),
    ("passport", "Passport", "Collect original passport", True, False, _stage7),
    ("advance", "Advance payment", "Collect the advance", True, False, _stage8),
    ("visa_arrival", "Visa arrival + Google review", "Follow up the visa", True, False, _stage9),
    ("visa_stamping", "Visa stamping", "Get the visa stamped", True, False, _stage10),
    ("air_ticket", "Air ticket", "Book the air ticket", True, False, _stage11),
]
STAGES = [StageDef(no, key, title, task_title, closer, repeatable, _validated(no, evaluate))
          for no, (key, title, task_title, closer, repeatable, evaluate) in enumerate(_DEFINITIONS, 1)]


def stage_by_no(no: int) -> StageDef:
    if not 1 <= no <= TOTAL_STAGES:
        raise ValueError(f"stage {no} is out of range")
    return STAGES[no - 1]
