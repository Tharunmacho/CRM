"""
Wire format.

Field names go out in camelCase because the front end's types already use it. Renaming
on the boundary keeps every page and component untouched when the store stops being an
in-memory array and starts being HTTP.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


def camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class Wire(BaseModel):
    model_config = ConfigDict(
        alias_generator=camel,
        populate_by_name=True,
        from_attributes=True,
        ser_json_timedelta="iso8601",
    )


class UserOut(Wire):
    id: str
    name: str
    email: str
    phone: str | None = None
    role: str
    active: bool
    receives_auto_assign: bool
    on_leave: bool


class ClientOut(Wire):
    """A person. Their companies are separate records."""

    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    country: str | None = None
    city: str | None = None
    notes: str | None = None
    status: str


class FileRefOut(Wire):
    id: str
    name: str
    url: str


class CompanyOut(Wire):
    id: str
    client_id: str
    name: str
    country: str | None = None
    city: str | None = None
    industry: str | None = None
    contact_person: str | None = None
    contact_email: str | None = None
    phone: str | None = None
    address: str | None = None
    notes: str | None = None
    status: str
    # The latest signed agreement, if one was uploaded.
    agreement: FileRefOut | None = None


class DesignationOut(Wire):
    id: str
    title: str
    category: str | None = None
    description: str | None = None
    required_skills: list[str] = []
    min_experience: int = 0


class OrderOut(Wire):
    id: str
    ref: str
    company_id: str
    designation_id: str
    openings: int
    country: str | None = None
    city: str | None = None
    salary: float | None = None
    currency: str
    benefits: list[str] = []
    contract_period: str | None = None
    interview_date: date | None = None
    deadline: date | None = None
    status: str
    created_at: datetime


class DocumentSlotOut(Wire):
    key: str
    label: str
    versions: list[dict[str, Any]] = []


class CallOutcomeOut(Wire):
    called: bool
    interested: str
    remarks: str | None = None
    recorded_at: datetime


class CandidateOut(Wire):
    id: str
    ref: str
    name: str
    phone: str
    whatsapp: str | None = None
    email: str | None = None
    dob: date | None = None
    gender: str | None = None
    nationality: str | None = None
    current_city: str | None = None
    languages: list[str] = []
    passport_no: str | None = None
    passport_issue: date | None = None
    passport_expiry: date | None = None
    passport_place: str | None = None
    designations: list[str] = []
    total_experience: int = 0
    gulf_experience: int = 0
    skills: list[str] = []
    expected_salary: float | None = None
    source: str
    source_detail: str | None = None
    status: str
    needs_review: bool = False
    owner_id: str | None = None
    added_at: datetime | None = None
    resume_text: str | None = None
    photo_tone: int = 0
    documents: list[DocumentSlotOut] = []
    call_outcome: CallOutcomeOut | None = None


class StageRecordOut(Wire):
    stage: int
    attempt: int
    data: dict[str, Any]
    outcome: str | None = None
    completed_at: datetime
    completed_by: str | None = None


class ApplicationOut(Wire):
    id: str
    candidate_id: str
    order_id: str
    current_stage: int
    status: str
    closure_reason: str | None = None
    draft: dict[str, Any] = {}
    lock_version: int = 0
    stages: list[StageRecordOut] = []
    created_at: datetime
    updated_at: datetime


class TaskOut(Wire):
    id: str
    kind: str
    title: str
    candidate_id: str
    application_id: str | None = None
    stage: int | None = None
    owner_id: str
    created_at: datetime
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    escalated: bool = False


class RemarkOut(Wire):
    id: str
    task_id: str
    body: str
    author_id: str
    created_at: datetime


class TodoOut(Wire):
    id: str
    owner_id: str
    title: str
    notes: str | None = None
    due_at: datetime
    priority: str
    done: bool
    link_to: str | None = None
    link_label: str | None = None


class NotificationOut(Wire):
    id: str
    kind: str
    title: str
    detail: str | None = None
    link_to: str | None = None
    read: bool
    created_at: datetime
