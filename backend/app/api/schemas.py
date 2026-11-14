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


class StaffContactOut(Wire):
    """One person on a client's or reference agency's own team."""

    name: str
    designation: str | None = None
    phone: str | None = None
    email: str | None = None


class ClientOut(Wire):
    """A person. Their companies are separate records."""

    id: str
    name: str
    email: str | None = None
    designation: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    country: str | None = None
    city: str | None = None
    notes: str | None = None
    status: str
    staff_contacts: list[StaffContactOut] = []
    # CRM users (active staff or manager) who look after this client; filled by the snapshot / endpoints, not a column.
    staff_ids: list[str] = []


class ReferenceAgencyOut(Wire):
    id: str
    name: str
    agency_name: str
    phone: str | None = None
    email: str | None = None
    place: str | None = None
    address: str | None = None
    state: str | None = None
    staff_contacts: list[StaffContactOut] = []
    notes: str | None = None
    specialisations: list[str] = []
    preferred_countries: list[str] = []
    created_at: datetime | None = None


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
    website: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    address: str | None = None
    state: str | None = None
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
    age_requirement: str | None = None
    gender_requirement: str | None = None
    education_requirement: str | None = None
    interview_date: date | None = None
    deadline: date | None = None
    status: str
    created_at: datetime


class DocumentSlotOut(Wire):
    key: str
    label: str
    versions: list[dict[str, Any]] = []
    uploaded: bool = False
    file_uploaded: bool = False
    manual_status: dict[str, Any] | None = None


class CallOutcomeOut(Wire):
    called: bool
    interested: str
    remarks: str | None = None
    recorded_at: datetime | None = None


class CandidateOut(Wire):
    id: str
    ref: str
    care_id: str | None = None
    # Second, separate series: CI-T-YY-YY-NNN, set when the candidate travels (stage 11 completed). Null until then.
    traveller_id: str | None = None
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
    # Application form (migration 0021). The two lists are on CandidateWithFormOut only: this class is also built from
    # whole candidate rows for the bootstrap and the Data Bank, where the deferred lists must not be loaded.
    father_name: str | None = None
    marital_status: str | None = None
    current_address: str | None = None
    experience_india: int = 0
    native_place: str | None = None
    family_member_number: str | None = None
    job_duration_preference: str | None = None
    country_preference: str | None = None
    best_time_to_contact: str | None = None
    service_charges_status: str = "not_discussed"
    interest_level: str | None = None
    next_follow_up_date: date | None = None
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


class CandidateWithFormOut(CandidateOut):
    """One candidate's full profile, including the application form's education and work-history rows."""

    education: list[dict[str, Any]] = []
    work_history: list[dict[str, Any]] = []
    # Kept off the desk/list payload: only an individual profile needs the exact source
    # wording where a numeric field would otherwise round or omit it.
    source_claims: dict[str, str] = {}


class StageRecordOut(Wire):
    id: str | None = None
    owner_id: str | None = None
    reopened_reason: str | None = None
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
    owner_id: str | None = None
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
    escalated_at: datetime | None = None
    sla_started_at: datetime | None = None
    due_at: datetime | None = None


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
