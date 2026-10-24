"""Typed draft contracts and completion rules for the PRD's eleven stages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, time as Time
from decimal import Decimal
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

TOTAL_STAGES = 11
YesNo = Literal["yes", "no"]
Currency = Literal["INR", "USD", "EUR", "SGD", "AED", "SAR", "QAR", "OMR", "KWD", "BHD"]


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
    amountCurrency: Currency | None = "INR"
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


def validate_payload(stage: int, data: dict[str, Any]) -> dict[str, Any]:
    """Normalize supplied values; completion alone requires PRD mandatory fields."""
    normalized = SCHEMAS[stage - 1].model_validate(data).model_dump(mode="json", by_alias=True, exclude_none=True)
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


def _file(d: dict, field: str) -> bool:
    return isinstance(d.get(field), dict) and bool(d[field].get("id"))


def _stage1(d: dict) -> Evaluation:
    if d.get("result") == "failed":
        return _close("prescreen_failed", "Failed prescreening", "Failed")
    return _ok("Passed") if d.get("result") == "passed" else _need("Result")


def _stage2(d: dict) -> Evaluation:
    if d.get("forwarded") == "no":
        if d.get("reason") == "resume_not_received":
            return _close("resume_not_received", "Resume not received", "Not forwarded")
        if d.get("reason") == "not_interested":
            return _close("not_interested", "Candidate not interested", "Not forwarded")
        return _need("Reason not forwarded")
    return _ok("Forwarded") if d.get("forwarded") == "yes" else _need("Forwarded to client")


def _stage3(d: dict) -> Evaluation:
    if d.get("scheduled") != "yes":
        return _need("Interview scheduled")
    return _ok("Scheduled") if d.get("date") else _need("Interview date")


def _stage4(d: dict) -> Evaluation:
    if not d.get("result") or not d.get("remarks", "").strip():
        return _need("Result and interview remarks")
    return (_close("interview_rejected", "Rejected at interview", "Rejected")
            if d["result"] == "rejected" else _ok("Selected"))


def _stage5(d: dict) -> Evaluation:
    if d.get("obtained") != "yes":
        return _need("Offer letter obtained")
    return _ok("Obtained") if _file(d, "file") else _need("Offer letter upload")


def _stage6(d: dict) -> Evaluation:
    if d.get("applied") == "no":
        return _need("Medical application")
    if d.get("result") == "fit":
        return _ok("Fit")
    if d.get("result") != "unfit":
        return _need("Medical result")
    if d.get("reapplying") == "no":
        return _close("medical_unfit", "Medically unfit, not reapplying", "Unfit")
    if d.get("reapplying") == "yes":
        if not d.get("retestDate"):
            return _need("New medical date")
        if d.get("appliedDate") and d["retestDate"] < d["appliedDate"]:
            return _need("A retest date on or after the previous medical date")
        return Evaluation(True, [], "Unfit: reapplying", repeats=True)
    return _need("Reapplying")


def _stage7(d: dict) -> Evaluation:
    return _ok("Collected") if d.get("collected") == "yes" else _need("Original passport collected")


def _stage8(d: dict) -> Evaluation:
    if d.get("collected") != "yes":
        return _need("Advance collected")
    if not d.get("amount"):
        return _need("Positive advance amount")
    return _ok("Collected") if _file(d, "receipt") else _need("Receipt upload")


def _stage9(d: dict) -> Evaluation:
    return _ok("Arrived") if d.get("arrived") == "yes" else _need("Visa arrived")


def _stage10(d: dict) -> Evaluation:
    if d.get("done") != "yes":
        return _need("Stamping done")
    return _ok("Stamped") if _file(d, "file") else _need("Stamped visa upload")


def _stage11(d: dict) -> Evaluation:
    return _ok("Booked") if _file(d, "file") else _need("Ticket upload")


def _validated(stage: int, evaluate: Callable[[dict], Evaluation]):
    def check(data: dict) -> Evaluation:
        try:
            clean = validate_payload(stage, data)
        except ValidationError as exc:
            return _need(*(f"{' / '.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors()))
        return evaluate(clean)
    return check


@dataclass(frozen=True)
class StageDef:
    no: int
    key: str
    title: str
    task_title: str
    closer: bool
    repeatable: bool
    evaluate: Callable[[dict], Evaluation]


_DEFINITIONS = [
    ("prescreening", "Prescreening", "Prescreen the candidate", True, False, _stage1),
    ("forwarded", "Forwarded to client", "Forward CV to client", True, False, _stage2),
    ("interview_scheduling", "Interview scheduling", "Schedule the interview", False, False, _stage3),
    ("interview_result", "Interview result", "Record the interview result", True, False, _stage4),
    ("offer_letter", "Offer letter", "Obtain the offer letter", False, False, _stage5),
    ("medical", "Medical", "Complete the medical", True, True, _stage6),
    ("passport", "Passport", "Collect original passport", False, False, _stage7),
    ("advance", "Advance payment", "Collect the advance", False, False, _stage8),
    ("visa_arrival", "Visa arrival + Google review", "Follow up the visa", False, False, _stage9),
    ("visa_stamping", "Visa stamping", "Get the visa stamped", False, False, _stage10),
    ("air_ticket", "Air ticket", "Book the air ticket", False, False, _stage11),
]
STAGES = [StageDef(no, key, title, task_title, closer, repeatable, _validated(no, evaluate))
          for no, (key, title, task_title, closer, repeatable, evaluate) in enumerate(_DEFINITIONS, 1)]


def stage_by_no(no: int) -> StageDef:
    if not 1 <= no <= TOTAL_STAGES:
        raise ValueError(f"stage {no} is out of range")
    return STAGES[no - 1]
