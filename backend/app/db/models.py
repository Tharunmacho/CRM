"""
Database schema.

Mirrors the UI types in frontend/src/shared/model/types.ts. The rules the
spec cares about are enforced here rather than in application code:

* a candidate can hold only one *active* application at a time — a partial unique index,
  so two users matching the same candidate from different order pages cannot race past it
* the eleven stages are heterogeneous, so each stage record keeps its payload in JSONB
  and denormalises only what the reports aggregate on
* the 48-hour escalation scan runs every 15 minutes forever, so open tasks carry their
  own partial index
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class PkMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------- enums

class Role(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    staff = "staff"


class CandidateStatus(str, enum.Enum):
    new = "new"
    in_process = "in_process"
    not_interested = "not_interested"
    deployed = "deployed"
    needs_review = "needs_review"


class Source(str, enum.Enum):
    email = "email"
    whatsapp = "whatsapp"
    walk_in = "walk_in"
    referral = "referral"
    other = "other"


class OrderStatus(str, enum.Enum):
    open = "open"
    on_hold = "on_hold"
    filled = "filled"
    closed = "closed"


class ApplicationStatus(str, enum.Enum):
    active = "active"
    closed = "closed"
    deployed = "deployed"


class ClosureReason(str, enum.Enum):
    prescreen_failed = "prescreen_failed"
    not_interested = "not_interested"
    resume_not_received = "resume_not_received"
    interview_rejected = "interview_rejected"
    medical_unfit = "medical_unfit"
    unmapped = "unmapped"
    changed_job = "changed_job"  # moved to another job from the application form before prescreening was passed (migration 0027)


class TaskKind(str, enum.Enum):
    initial_call = "initial_call"
    stage = "stage"
    remedical = "remedical"


class Priority(str, enum.Enum):
    high = "high"
    normal = "normal"
    low = "low"


def pg_enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


# --------------------------------------------------------------------- users

class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "auth_users"
    __table_args__ = {
        "comment": (
            "CRM sign-in accounts, password hashes, roles and account status. "
            "These are separate from connected email-provider accounts."
        )
    }

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        comment="CRM sign-in email address; unique per account.",
    )
    phone: Mapped[str | None] = mapped_column(String(40))
    role: Mapped[Role] = mapped_column(
        pg_enum(Role, "user_role"),
        nullable=False,
        comment="CRM permission role: admin, manager or staff.",
    )
    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        comment=(
            "One-way password hash used to verify CRM sign-in; "
            "never store a plaintext password here."
        ),
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    receives_auto_assign: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    on_leave: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class Session(Base, PkMixin):
    """Own the sessions so an admin can force a logout and read a user's activity."""

    __tablename__ = "auth_sessions"
    __table_args__ = {
        "comment": (
            "CRM sign-in sessions and expiry times, referenced by the session cookie. "
            "Deleting a row revokes that session."
        )
    }

    user_id: Mapped[str] = mapped_column(
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="CRM account that owns this session; references auth_users.id.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Time after which this CRM session is no longer accepted.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ------------------------------------------------------------------- clients

class Client(Base, PkMixin, TimestampMixin):
    """
    A person the agency deals with, not an organisation. One client can own several
    companies, whose employer details live on `crm_employer_companies`.
    """

    __tablename__ = "crm_client_contacts"
    __table_args__ = {
        "comment": "Client contact people who own employer-company records."
    }

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    designation: Mapped[str | None] = mapped_column(String(120))  # the contact person's job title (migration 0025)
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    # People on the client's own team: [{name, designation?, phone?, email?}], at most 50 (migration 0026).
    staff_contacts: Mapped[list[dict]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, server_default=text("'[]'"))

    companies: Mapped[list["Company"]] = relationship(back_populates="client")


class ClientStaff(Base):
    """A CRM user (staff or manager) who looks after a client end to end. Candidates for the client's orders are routed to them."""

    __tablename__ = "crm_client_staff"
    __table_args__ = {
        "comment": "CRM staff allocated to a client; matched and incoming candidates for that client go to them."
    }

    client_id: Mapped[str] = mapped_column(
        ForeignKey("crm_client_contacts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True, index=True)


class ReferenceAgency(Base, PkMixin, TimestampMixin):
    """An agency that refers candidates. Same details as a client, kept apart from the client chain."""

    __tablename__ = "crm_reference_agencies"
    __table_args__ = {
        "comment": "Reference agencies that refer candidates: contact person, agency name, address and their own team."
    }

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    agency_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    place: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(100))
    staff_contacts: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)
    # Migration 0033: fixed-set selections, validated by the API. Older rows and older writers leave them as [].
    specialisations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    preferred_countries: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


class Company(Base, PkMixin, TimestampMixin):
    """The employer an order is placed for. Always owned by a client."""

    __tablename__ = "crm_employer_companies"
    __table_args__ = {
        "comment": "Employer companies linked to client contacts; recruitment orders belong to these companies."
    }

    client_id: Mapped[str] = mapped_column(ForeignKey("crm_client_contacts.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(120))
    contact_person: Mapped[str | None] = mapped_column(String(160))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(255))  # normalised https:// URL (migration 0025)
    phone: Mapped[str | None] = mapped_column(String(40))
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(100))  # the agency's state (migration 0026)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))

    client: Mapped[Client] = relationship(back_populates="companies")
    orders: Mapped[list["Order"]] = relationship(back_populates="company")


class Designation(Base, PkMixin, TimestampMixin):
    __tablename__ = "crm_job_designations"
    __table_args__ = {
        "comment": "Job titles and their skills and experience requirements, referenced by recruitment orders."
    }

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    min_experience: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Order(Base, PkMixin, TimestampMixin):
    __tablename__ = "crm_job_orders"
    __table_args__ = {
        "comment": "Employer recruitment orders with job designation, openings and employment terms."
    }

    ref: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("crm_employer_companies.id"), nullable=False, index=True)
    designation_id: Mapped[str] = mapped_column(
        ForeignKey("crm_job_designations.id"), nullable=False, index=True
    )
    openings: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    salary: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), server_default=text("'AED'"))
    benefits: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    contract_period: Mapped[str | None] = mapped_column(String(60))
    age_requirement: Mapped[str | None] = mapped_column(String(120))
    gender_requirement: Mapped[str | None] = mapped_column(String(120))
    education_requirement: Mapped[str | None] = mapped_column(String(2000))
    interview_date: Mapped[datetime | None] = mapped_column(Date)
    deadline: Mapped[datetime | None] = mapped_column(Date)
    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"), nullable=False, server_default=text("'open'")
    )

    company: Mapped[Company] = relationship(back_populates="orders")


# ---------------------------------------------------------------- candidates

class Candidate(Base, PkMixin, TimestampMixin):
    __tablename__ = "crm_candidates"

    ref: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), nullable=False)
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    dob: Mapped[datetime | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    nationality: Mapped[str | None] = mapped_column(String(80))
    current_city: Mapped[str | None] = mapped_column(String(120))
    languages: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    passport_no: Mapped[str | None] = mapped_column(String(40))
    passport_issue: Mapped[datetime | None] = mapped_column(Date)
    passport_expiry: Mapped[datetime | None] = mapped_column(Date)
    passport_place: Mapped[str | None] = mapped_column(String(120))

    designations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    total_experience: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    gulf_experience: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skills: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    expected_salary: Mapped[float | None] = mapped_column(Numeric(12, 2))

    source: Mapped[Source] = mapped_column(pg_enum(Source, "candidate_source"), nullable=False)
    source_detail: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[CandidateStatus] = mapped_column(
        pg_enum(CandidateStatus, "candidate_status"),
        nullable=False,
        server_default=text("'new'"),
    )
    needs_review: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    owner_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"), index=True)
    intake_owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_users.id"), index=True,
        comment="Recruiter assigned at intake; retained when current ownership changes. Legacy rows use the owner at migration.",
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("auth_users.id"),
        comment="CRM user who entered this candidate; null for automatic intake or legacy records.",
    )

    # Application-form details (migration 0021). Filled by reading the resume/passport or typed in the form;
    # field_sources says which ("extracted" or "manual"), and a document read never overwrites a "manual" value.
    # experience_abroad on the form is gulf_experience above. The three JSONB columns are deferred like
    # ocr_data: the bootstrap loads every candidate and never reads them.
    father_name: Mapped[str | None] = mapped_column(String(200))
    marital_status: Mapped[str | None] = mapped_column(String(30))
    current_address: Mapped[str | None] = mapped_column(Text)
    experience_india: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    education: Mapped[list | None] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), deferred=True)
    work_history: Mapped[list | None] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), deferred=True)
    field_sources: Mapped[dict | None] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), deferred=True)
    # Screening details from the telemarketer's intake form (also migration 0021). Job preference is `designations`,
    # the agent is `source_detail`, the assigned staff `owner_id`, the telemarketer notes `call_remarks`.
    native_place: Mapped[str | None] = mapped_column(String(120))
    family_member_number: Mapped[str | None] = mapped_column(String(40))
    job_duration_preference: Mapped[str | None] = mapped_column(String(60))
    country_preference: Mapped[str | None] = mapped_column(String(200))
    best_time_to_contact: Mapped[str | None] = mapped_column(String(60))
    service_charges_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'not_discussed'"))
    interest_level: Mapped[str | None] = mapped_column(String(20))
    next_follow_up_date: Mapped[date | None] = mapped_column(Date)

    # Parsed resume text, plus the index the Data Bank's full-text search runs on.
    resume_text: Mapped[str | None] = mapped_column(Text)
    resume_tsv: Mapped[str | None] = mapped_column(TSVECTOR)

    # What Veris OCR returned for the documents scanned on entry, as
    # {"resume": {...}, "passport": {...}}. Every resume comes back shaped differently,
    # so the output is kept whole rather than spread over columns. Deferred because the
    # bootstrap loads every candidate and never reads it. none_as_null so a hand-filled
    # candidate is SQL NULL rather than a JSON null.
    ocr_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), deferred=True)

    call_called: Mapped[bool | None] = mapped_column(Boolean)
    call_interested: Mapped[str | None] = mapped_column(String(20))
    call_remarks: Mapped[str | None] = mapped_column(Text)
    call_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Duplicate detection on ingest runs against these three.
        Index("ix_crm_candidates_phone", "phone"),
        Index("ix_crm_candidates_email_lower", func.lower(email)),
        Index("ix_crm_candidates_passport_upper", func.upper(passport_no)),
        Index("ix_crm_candidates_resume_tsv", "resume_tsv", postgresql_using="gin"),
        Index("ix_crm_candidates_follow_up", "next_follow_up_date", postgresql_where=text("next_follow_up_date IS NOT NULL")),
        {"comment": "Recruitment candidate profiles, ownership, contact details and extracted document data."},
    )


class Attachment(Base, PkMixin):
    """
    Every uploaded file, for any record.

    One table rather than one per owner: a client agreement, a candidate's passport scan
    and a stage 8 receipt differ only in what they hang off. Nothing is overwritten —
    replacing a file writes the next version and the history stays readable.
    """

    __tablename__ = "document_attachments"

    # "client" | "candidate" | "application"
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # resume, passport, photo, agreement, receipt, ticket…
    slot: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_kb: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(120))
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Lets ingestion skip a file whose contents were already processed.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_document_attachments_owner", "entity_type", "entity_id", "slot"),
        UniqueConstraint("entity_type", "entity_id", "slot", "version"),
        {"comment": "Versioned document metadata and storage keys for CRM records; file contents live in file storage."},
    )


# -------------------------------------------------------------- applications

class Application(Base, PkMixin, TimestampMixin):
    __tablename__ = "crm_applications"

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("crm_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_job_orders.id"), nullable=False, index=True)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_users.id"), index=True,
        comment="Recruiter responsible when this placement was mapped; a historical reporting snapshot.",
    )
    current_stage: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[ApplicationStatus] = mapped_column(
        pg_enum(ApplicationStatus, "application_status"),
        nullable=False,
        server_default=text("'active'"),
    )
    closure_reason: Mapped[ClosureReason | None] = mapped_column(
        pg_enum(ClosureReason, "closure_reason")
    )
    # The stage being worked. Saved by an explicit Save, not as you type, and cleared
    # when the stage is completed. Files are uploaded immediately and the draft holds
    # the object key, so a lost connection cannot take an attachment with it.
    draft: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    draft_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    draft_saved_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))

    # Optimistic lock. The client sends the version it loaded; a mismatch is a 409
    # rather than one person's edits silently overwriting another's.
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        CheckConstraint("current_stage between 1 and 11", name="ck_application_stage_range"),
        # The spec's "one order at a time", enforced by the database rather than by
        # application code — this is what stops two users double-mapping a candidate.
        Index(
            "uq_one_active_application_per_candidate",
            "candidate_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        {"comment": "Candidate-to-job-order placements with current workflow stage, status and saved draft."},
    )


class ApplicationStage(Base, PkMixin):
    """
    One row per stage attempt. The payload differs per stage so it lives in JSONB;
    the few values the reports aggregate on are lifted into real columns.
    """

    __tablename__ = "crm_application_stages"

    application_id: Mapped[str] = mapped_column(
        ForeignKey("crm_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    outcome: Mapped[str | None] = mapped_column(String(60))
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2))  # stage 8, for reports
    # The advance has its own currency — it is collected in India while the salary is
    # paid abroad, so it cannot be assumed to match the order's.
    amount_currency: Mapped[str | None] = mapped_column(String(8))
    completed_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_users.id"), index=True,
        comment="Responsible recruiter recorded for this stage attempt, independent of the person completing it.",
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reopened_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("application_id", "stage", "attempt"),
        CheckConstraint("stage between 1 and 11", name="ck_stage_range"),
        {"comment": "Recorded attempts and outcomes for each stage of a candidate application."},
    )


# -------------------------------------------------------------------- tasks

class Task(Base, PkMixin):
    __tablename__ = "workflow_tasks"

    kind: Mapped[TaskKind] = mapped_column(pg_enum(TaskKind, "task_kind"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("crm_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("crm_applications.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[int | None] = mapped_column(Integer)
    owner_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Start of the current SLA window; reassignment does not reset it.",
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, comment="Deadline of the current 48-hour SLA window.",
    )

    __table_args__ = (
        # The escalation scan runs every 15 minutes forever; without this it degrades
        # into a full scan as the table grows.
        Index(
            "ix_workflow_tasks_open_by_age",
            "created_at",
            postgresql_where=text("completed_at is null and cancelled_at is null"),
        ),
        Index(
            "ix_workflow_tasks_owner_open",
            "owner_id",
            "created_at",
            postgresql_where=text("completed_at is null and cancelled_at is null"),
        ),
        {"comment": "Assigned recruitment work, including initial calls and application-stage tasks."},
    )


class Remark(Base, PkMixin):
    """Daily updates. Append-only by design — they cannot be edited or deleted."""

    __tablename__ = "workflow_task_remarks"
    __table_args__ = {
        "comment": "Dated staff updates attached to recruitment workflow tasks."
    }

    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class TaskEvent(Base, PkMixin):
    """Durable SLA history. Current task flags are not historical reports."""

    __tablename__ = "workflow_task_events"
    task_id: Mapped[str] = mapped_column(ForeignKey("workflow_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id", ondelete="SET NULL"), index=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("kind IN ('escalation','missed_update','sla_reset','reminder','assignment')", name="ck_workflow_task_events_kind"),
        {"comment": "Append-only task SLA events, with owner at event time and unique delivery key for repeat-safe scheduling."},
    )


class DailyWorkItem(Base, PkMixin):
    """
    One candidate in a recruiter's daily quota. Allocated automatically (never by hand) from the recruiter's open
    application-stage tasks; it is finished when that stage task is completed through the stage workflow. An item
    still open after its day is *postponed*: it stays in the list (oldest first) next to the new day's quota.

    Two kinds: `stage` (an open application-stage task the recruiter owns) and `call` (a candidate the recruiter owns
    who is not matched to an order yet, worked through the initial call and then matching). `status` values: open
    (to do), completed (stage kind: the task was completed by the owner; call kind: the initial call was recorded or a
    stage was completed after allocation) and released (the task was cancelled, or the task/candidate moved to someone
    else or was closed, before it was done: it no longer counts for anyone).
    Every moment is stored for reports: allocated_at, started_at (first draft save or owner remark), overdue_at (the
    hourly deadline it missed), postponed_at (start of the day after work_date, while still unfinished), completed_at
    and released_at.
    """

    __tablename__ = "workflow_daily_work_items"
    owner_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, comment="Agency-timezone day the item was allocated.")
    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'stage'"), comment="stage (an open application-stage task) or call (an owned candidate not yet matched, worked through the initial call).")
    task_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_tasks.id", ondelete="CASCADE"), index=True, comment="The stage task for kind stage; null for kind call.")
    application_id: Mapped[str | None] = mapped_column(ForeignKey("crm_applications.id", ondelete="CASCADE"))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("crm_candidates.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[int | None] = mapped_column(Integer, comment="Stage the candidate was at when allocated; null for kind call.")
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="1-based place in the day's quota.")
    session: Mapped[str] = mapped_column(String(10), nullable=False, comment="morning or evening.")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="Hourly slot deadline.")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'open'"), comment="open, completed or released.")
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="First draft save or owner remark; set to completed_at if the work was done in one step.")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    overdue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="The deadline it missed (equals due_at); null when finished on time.")
    postponed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="Start of the day after work_date, set when it was still unfinished then.")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(20), comment="task_cancelled or reassigned.")
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    postponed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_daily_work_task_owner_live", "task_id", "owner_id", unique=True, postgresql_where=text("status <> 'released'")),
        Index("ix_daily_work_owner_date", "owner_id", "work_date"),
        Index("ix_daily_work_open", "owner_id", postgresql_where=text("status = 'open'")),
        Index("ix_daily_work_candidate_open", "candidate_id", postgresql_where=text("status = 'open'")),
        CheckConstraint("kind IN ('stage','call')", name="ck_daily_work_kind"),
        CheckConstraint("kind = 'call' OR task_id IS NOT NULL", name="ck_daily_work_stage_task"),
        CheckConstraint("session IN ('morning','evening')", name="ck_daily_work_session"),
        CheckConstraint("status IN ('open','completed','released')", name="ck_daily_work_status"),
        CheckConstraint("slot_no >= 1", name="ck_daily_work_slot"),
        {"comment": "Auto-allocated daily candidate quota per recruiter with hourly slot deadlines and every lifecycle timestamp; completion comes from the linked stage task."},
    )


class DailyGoal(Base, PkMixin):
    """
    A daily target. `user_id` null is the agency default; a row with a user overrides it for that person. Metrics:
    candidates (daily quota, with morning_target the morning share and the rest evening), forwarded_to_client
    (stage 2 completed with Forwarded) and stage_moves (any stage completed). Admin-managed data, never code.
    """

    __tablename__ = "workflow_daily_goals"
    user_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), index=True, comment="Null for the agency default.")
    metric: Mapped[str] = mapped_column(String(30), nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    morning_target: Mapped[int | None] = mapped_column(Integer, comment="Candidates metric only: how many of the target are morning slots.")
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        Index("uq_daily_goal_scope_metric", func.coalesce(user_id, ""), "metric", unique=True),
        CheckConstraint("metric IN ('candidates','forwarded_to_client','stage_moves')", name="ck_daily_goal_metric"),
        CheckConstraint("target BETWEEN 0 AND 200", name="ck_daily_goal_target"),
        CheckConstraint("morning_target IS NULL OR (metric = 'candidates' AND morning_target BETWEEN 0 AND target)", name="ck_daily_goal_morning"),
        {"comment": "Daily staff goals: agency defaults (user_id null) and per-person overrides, edited by the admin."},
    )


class DailyGoalProgress(Base, PkMixin):
    """Per person, agency day and metric: the target that applied and what was achieved, kept up to date for reports."""

    __tablename__ = "workflow_daily_goal_progress"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    goal_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(30), nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    morning_target: Mapped[int | None] = mapped_column(Integer)
    actual: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    morning_actual: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "goal_date", "metric", name="uq_daily_goal_progress"),
        CheckConstraint("metric IN ('candidates','forwarded_to_client','stage_moves')", name="ck_daily_goal_progress_metric"),
        {"comment": "Daily goal snapshot per recruiter, agency day and metric: the target in force and the actual count, for weekly reports."},
    )


class TodoItem(Base, PkMixin):
    __tablename__ = "personal_todos"
    __table_args__ = {
        "comment": "Private to-do items belonging to individual CRM users."
    }

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[Priority] = mapped_column(
        pg_enum(Priority, "todo_priority"), nullable=False, server_default=text("'normal'")
    )
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="When the current due-date reminder was delivered; cleared when due date changes.",
    )
    link_to: Mapped[str | None] = mapped_column(String(255))
    link_label: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- ingestion

class MailAccount(Base, PkMixin, TimestampMixin):
    __tablename__ = "email_accounts"
    __table_args__ = {
        "comment": "Connected email-provider accounts for the inbox and ingestion; these are not CRM sign-in accounts."
    }

    address: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # gmail|microsoft|imap
    auto_ingest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # OAuth tokens / IMAP credentials, encrypted at rest. Never returned by the API.
    secrets: Mapped[dict | None] = mapped_column(JSONB)
    sync_state: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_error: Mapped[str | None] = mapped_column(Text)
    sync_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    worker_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))


class MailMessage(Base, PkMixin):
    """
    Mail is mirrored locally so the unified inbox can sort, search and filter in one
    query instead of fanning out to three different provider APIs on every page load.
    """

    __tablename__ = "email_messages"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(200))
    from_address: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    message_id_header: Mapped[str | None] = mapped_column(Text)
    references_header: Mapped[str | None] = mapped_column(Text)
    folder: Mapped[str] = mapped_column(String(255), nullable=False, server_default="INBOX")
    remote_uid: Mapped[int | None] = mapped_column(BigInteger)
    uid_validity: Mapped[str | None] = mapped_column(String(80))
    to_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cc_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    bcc_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    remote_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    previous_folder: Mapped[str | None] = mapped_column(String(255))
    remote_state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="synced")
    remote_error: Mapped[str | None] = mapped_column(Text)
    remote_operation: Mapped[dict | None] = mapped_column(JSONB)

    reply_to: Mapped[str | None] = mapped_column(String(255))
    automation_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unread: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id"))

    __table_args__ = (
        UniqueConstraint("account_id", "provider_message_id"),
        Index("ix_email_messages_received_at", "received_at"),
        {"comment": "Provider email messages associated with connected inbox accounts and, optionally, candidates."},
    )


class MailAttachment(Base, PkMixin):
    __tablename__ = "email_attachments"
    __table_args__ = {
        "comment": "Attachment metadata from provider email messages, including storage keys and deduplication hashes."
    }

    message_id: Mapped[str] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size_kb: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str | None] = mapped_column(String(20))
    object_key: Mapped[str | None] = mapped_column(String(500))
    # Skip a file whose hash has already been processed.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)


class MailOAuthState(Base, PkMixin):
    __tablename__ = "email_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    verifier_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = {"comment": "Short-lived, single-use Gmail/Microsoft connection handshakes; PKCE verifier is encrypted and OAuth state is hashed."}


class MailReply(Base, PkMixin):
    __tablename__ = "email_replies"
    message_id: Mapped[str] = mapped_column(ForeignKey("email_messages.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("email_accounts.id"), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('pending','sending','sent','unknown','failed')", name="ck_email_replies_status"),
        {"comment": "Human-requested replies sent from the originating mailbox. Unknown delivery outcomes must be reconciled before retrying."},
    )


class WhatsAppChannel(Base, TimestampMixin):
    __tablename__ = "whatsapp_channels"
    phone_number_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_number: Mapped[str | None] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    auto_ingest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = {"comment": "Connected WhatsApp Business phone numbers and inbound processing health. Provider credentials remain in server configuration."}


class WhatsAppMessage(Base, PkMixin, TimestampMixin):
    __tablename__ = "whatsapp_messages"
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone_number_id: Mapped[str] = mapped_column(ForeignKey("whatsapp_channels.phone_number_id"), nullable=False, index=True)
    sender_number: Mapped[str] = mapped_column(String(40), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(200))
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[str | None] = mapped_column(String(100))
    file_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120))
    object_key: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id"), index=True)
    processing_state: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True)
    __table_args__ = (
        CheckConstraint("status IN ('received','pending','processing','retry','needs_review','completed','ignored','failed')", name="ck_whatsapp_messages_status"),
        Index("ix_whatsapp_messages_ready", "next_attempt_at", postgresql_where=text("status IN ('pending','processing','retry')")),
        Index("ix_whatsapp_messages_conversation", "phone_number_id", "sender_number", "received_at", "id"),
        {"comment": "Durable inbound WhatsApp receipts, original media and OCR checkpoints. Sender number is provenance, not assumed candidate identity."},
    )


class EmailIngestionJob(Base, PkMixin, TimestampMixin):
    """Durable message receipt, OCR checkpoints and candidate commit outcome."""

    __tablename__ = "email_ingestion_jobs"
    message_id: Mapped[str] = mapped_column(
        ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id"))
    # Per attachment hash: inspection, provider job IDs and saved structured results.
    # Deferred: mailbox lists must not transfer extracted identity documents.
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True)
    inspection: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Reading lease: the worker that claimed this job and when its claim lapses (see services/extraction_queue.py).
    claimed_by: Mapped[str | None] = mapped_column(String(80), comment="Worker holding the reading lease of this job.")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When the reading lease lapses and another worker may claim the job.")
    __table_args__ = (
        CheckConstraint("status IN ('pending','processing','retry','needs_review','completed','ignored','failed')",
                        name="ck_email_ingestion_jobs_status"),
        Index("ix_email_ingestion_jobs_ready", "next_attempt_at",
              postgresql_where=text("status IN ('pending','processing','retry')")),
        Index("ix_email_ingestion_jobs_lease", "lease_until", postgresql_where=text("status = 'processing'")),
        {"comment": "Durable email intake queue and per-document OCR checkpoints; links source messages to candidate outcomes."},
    )


class DocumentExtractionJob(Base, PkMixin, TimestampMixin):
    __tablename__ = "document_extraction_jobs"
    # The installation that saved the file (INSTANCE_NAME). Null on rows written before the column existed.
    instance: Mapped[str | None] = mapped_column(String(80))
    # When the job is really due, on rows stamped with an instance (next_attempt_at then holds 2100-01-01 so older readers
    # never see them; a trigger maintains both, see migration 0032). Null on older rows.
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    filename: Mapped[str] = mapped_column(String(150), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_kb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id"))
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("document_attachments.id"))
    # Queue timeline for the progress display, and the reading lease (see services/extraction_queue.py).
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When reading was last requested (entered the queue).")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When a worker last began reading this document.")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When reading last reached completed, needs_review or failed.")
    claimed_by: Mapped[str | None] = mapped_column(String(80), comment="Worker holding the reading lease of this job.")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When the reading lease lapses and another worker may claim the job.")
    __table_args__ = (
        UniqueConstraint("owner_id", "group_id", "kind", "content_hash", name="uq_document_extraction_source"),
        CheckConstraint("status IN ('uploaded','pending','processing','retry','completed','needs_review','failed','attached','dismissed')", name="ck_document_extraction_jobs_status"),
        Index("ix_document_extraction_jobs_ready", "status", "next_attempt_at"),
        Index("ix_document_extraction_jobs_lease", "lease_until", postgresql_where=text("status = 'processing'")),
        {"comment": "Manual candidate document uploads, durable OCR checkpoints and recruiter form drafts, grouped by one intake session."},
    )


class IngestionFailure(Base, PkMixin):
    __tablename__ = "ingestion_failures"
    __table_args__ = {
        "comment": "Failed document or message ingestion attempts and their resolution status."
    }

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ----------------------------------------------------------------- plumbing

class ApplicationFeedback(Base, PkMixin):
    """A note a recruiter writes on a candidate's application form to say why something takes time (migration 0027)."""

    __tablename__ = "crm_application_feedback"
    __table_args__ = (
        CheckConstraint("kind in ('update', 'delay', 'issue', 'client', 'candidate')", name="ck_application_feedback_kind"),
        CheckConstraint("char_length(body) between 1 and 2000", name="ck_application_feedback_body"),
        Index("ix_crm_application_feedback_candidate_created", "candidate_id", text("created_at DESC")),
        Index("ix_crm_application_feedback_author_id", "author_id", "created_at"),
        {"comment": "Notes recruiters write on a candidate's application form to explain progress, delays and problems."},
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("crm_candidates.id", ondelete="CASCADE"), nullable=False,
        comment="The candidate the note is about; the note is deleted with the candidate.")
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("crm_applications.id", ondelete="SET NULL"), comment="The application (order) the note was written for, when there was one.")
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_users.id", ondelete="SET NULL"), comment="The CRM user who wrote the note; cleared if that account is removed.")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, comment="update, delay, issue, client or candidate.")
    reason: Mapped[str | None] = mapped_column(
        String(30), comment="Optional short reason code: waiting_candidate, waiting_client, waiting_documents, waiting_visa, waiting_payment or other.")
    body: Mapped[str] = mapped_column(Text, nullable=False, comment="The note, 1 to 2000 characters.")
    stage: Mapped[int | None] = mapped_column(Integer, comment="The stage number the application was on when the note was written.")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, comment="When the note was written.")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
        comment="When the note was last edited (edits are allowed for 15 minutes).")


class Notification(Base, PkMixin):
    __tablename__ = "system_notifications"
    __table_args__ = {
        "comment": "In-app notifications addressed to CRM users."
    }

    user_id: Mapped[str] = mapped_column(
        ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    link_to: Mapped[str | None] = mapped_column(String(255))
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEntry(Base, PkMixin):
    """Every create, edit, stage change, reassignment and upload, with user and time."""

    __tablename__ = "system_audit_log"
    __table_args__ = {
        "comment": "Recorded CRM actions with actor, affected entity and event metadata."
    }

    actor_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[str | None] = mapped_column(String(300))
    entity_type: Mapped[str | None] = mapped_column(String(60), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AssignmentPointer(Base):
    """
    Round-robin cursor. A single row, taken with SELECT ... FOR UPDATE so two resumes
    arriving in the same second cannot be handed to the same recruiter.
    """

    __tablename__ = "workflow_assignment_cursor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    last_user_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_single_pointer_row"),
        {"comment": "Singleton round-robin assignment cursor; id 1 records the last assigned CRM user."},
    )


class WorkflowSettings(Base):
    __tablename__ = "workflow_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    escalation_repeat_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_daily_check_date: Mapped[date | None] = mapped_column(Date)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_workflow_settings_singleton"),
        CheckConstraint("escalation_repeat_hours IN (0, 24)", name="ck_workflow_settings_repeat"),
        {"comment": "Agency task automation settings and scheduler checkpoint. Repeat 0 means one escalation; 24 enables daily reminders."},
    )


class Currency(Base):
    """A currency the order form and the stage-8 advance offer. Retired codes are switched off, never deleted."""

    __tablename__ = "currencies"
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(8))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z]{3}$'", name="ck_currencies_code"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_currencies_name"),
        {"comment": "Admin-managed currency list for orders and the stage-8 advance; inactive codes stay valid on stored records."},
    )


class AgencySettings(Base):
    """The agency's identity and defaults. One row (id 1); every value is administered in the CRM."""

    __tablename__ = "agency_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(60))
    default_order_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    default_advance_currency: Mapped[str] = mapped_column(ForeignKey("currencies.code"), nullable=False)
    home_calling_code: Mapped[str] = mapped_column(String(5), nullable=False)
    default_nationality: Mapped[str | None] = mapped_column(String(80))
    acknowledgement_text: Mapped[str] = mapped_column(Text, nullable=False)
    auto_assign: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"),
                                              comment="New candidates go to the next free recruiter (round-robin) when true; otherwise they wait for a manual assignment.")
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_agency_settings_singleton"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_agency_settings_name"),
        CheckConstraint(r"home_calling_code ~ '^\+[0-9]{1,4}$'", name="ck_agency_settings_calling_code"),
        {"comment": "Singleton agency profile and defaults: name, default currencies, home calling code and the default acknowledgement text."},
    )


class JobCategory(Base, PkMixin):
    """A job category offered on job titles. Admin-managed; `crm_job_designations.category` stays free text that matches by name."""

    __tablename__ = "job_categories"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        Index("ux_job_categories_name_lower", func.lower(name), unique=True),
        CheckConstraint("char_length(btrim(name)) BETWEEN 2 AND 120", name="ck_job_categories_name"),
        {"comment": "Admin-managed job category list for job titles; names are unique ignoring case and inactive ones stay valid on saved job titles."},
    )


class FormOption(Base, PkMixin, TimestampMixin):
    """One choice of an admin-managed list on the candidate / application form (service charges, interest level, ...)."""

    __tablename__ = "crm_form_options"
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str] = mapped_column(String(60), nullable=False, comment="What is stored on the candidate; never changed after creation.")
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    hint: Mapped[str | None] = mapped_column(String(255))
    tone: Mapped[str | None] = mapped_column(String(20), comment="neutral, success, info, warning or danger: the colour a screen gives the choice.")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), comment="Seeded by the product; can be hidden and relabelled but not deleted.")
    __table_args__ = (
        UniqueConstraint("category", "value", name="uq_form_options_category_value"),
        Index("ix_crm_form_options_category", "category", "sort_order"),
        CheckConstraint("tone IS NULL OR tone IN ('neutral','success','info','warning','danger')", name="ck_form_options_tone"),
        {"comment": "Admin-managed option lists for the candidate and application form; a hidden option stays valid on candidates that already have it."},
    )


class MailDraft(Base, PkMixin, TimestampMixin):
    __tablename__ = "email_drafts"
    account_id: Mapped[str] = mapped_column(ForeignKey("email_accounts.id"), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="compose")
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("email_messages.id"))
    to_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cc_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    bcc_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    remote_uid: Mapped[int | None] = mapped_column(BigInteger)
    remote_uid_validity: Mapped[str | None] = mapped_column(String(80))
    remote_folder: Mapped[str | None] = mapped_column(String(255))
    remote_state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("mode IN ('compose','reply','reply_all','forward','edit_draft')", name="ck_email_drafts_mode"),
        CheckConstraint("status IN ('draft','sending','sent','unknown','failed','discarded')", name="ck_email_drafts_status"),
        {"comment": "Durable Zoho message drafts with recipients, optimistic versions, mailbox synchronization and send state."},
    )


class MailDraftAttachment(Base, PkMixin):
    __tablename__ = "email_draft_attachments"
    draft_id: Mapped[str] = mapped_column(ForeignKey("email_drafts.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = {"comment": "Immutable draft attachment bytes referenced by durable Zoho drafts and send snapshots."}


class MailSendAttempt(Base, PkMixin):
    __tablename__ = "email_send_attempts"
    draft_id: Mapped[str] = mapped_column(ForeignKey("email_drafts.id"), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    refused_recipients: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('pending','sending','sent','partial','unknown','failed')", name="ck_email_send_attempts_status"),
        {"comment": "Idempotent human-requested Zoho sends with immutable content snapshots and explicit uncertain/partial delivery outcomes."},
    )


class StaffAttendanceSession(Base, PkMixin, TimestampMixin):
    __tablename__ = "staff_attendance_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkin_request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    checkout_request_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    corrected_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("checked_out_at IS NULL OR checked_out_at >= checked_in_at", name="ck_staff_attendance_time_order"),
        Index("ix_staff_attendance_one_open", "user_id", unique=True, postgresql_where=text("checked_out_at IS NULL")),
        Index("ix_staff_attendance_user_time", "user_id", "checked_in_at"),
        {"comment": "Explicit staff check-in/check-out intervals. Attendance is independent of browser presence; corrections are audited."},
    )


class StaffPresenceInterval(Base, PkMixin):
    __tablename__ = "staff_presence_intervals"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(36), nullable=False)
    auth_session_id: Mapped[str | None] = mapped_column(ForeignKey("auth_sessions.id", ondelete="SET NULL"), index=True)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("state IN ('online','idle')", name="ck_staff_presence_state"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_staff_presence_time_order"),
        Index("ix_staff_presence_one_open", "user_id", "client_id", unique=True, postgresql_where=text("ended_at IS NULL")),
        Index("ix_staff_presence_stale", "last_seen_at", postgresql_where=text("ended_at IS NULL")),
        Index("ix_staff_presence_user_time", "user_id", "started_at"),
        {"comment": "Visible browser heartbeat intervals for online/idle estimates. Offline time is derived within explicit attendance; multiple tabs are deduplicated."},
    )


class StaffAttendanceRequest(Base):
    __tablename__ = "staff_attendance_requests"
    kind: Mapped[str] = mapped_column(String(12), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    attendance_id: Mapped[str] = mapped_column(ForeignKey("staff_attendance_sessions.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("kind IN ('check_in','check_out')", name="ck_staff_attendance_request_kind"),
        {"comment": "Durable attendance retry ledger, including requests that return an already-open or already-closed session."},
    )


class StaffPresenceClient(Base):
    __tablename__ = "staff_presence_clients"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    auth_session_id: Mapped[str | None] = mapped_column(ForeignKey("auth_sessions.id", ondelete="SET NULL"), index=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("last_sequence >= 1", name="ck_staff_presence_client_sequence"),
        {"comment": "Per-page ordering checkpoint and login binding. Leave requests persist even before the first heartbeat, rejecting delayed older updates."},
    )


class AttendanceHRRequest(Base, PkMixin, TimestampMixin):
    __tablename__ = "staff_attendance_hr_requests"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'pending'"))
    approvals: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    emergency_override: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    cover_user_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"), index=True)
    cover_status: Mapped[str | None] = mapped_column(String(20))
    cover_note: Mapped[str | None] = mapped_column(Text)
    cover_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cover_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_from_paid_leave: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    __table_args__ = (
        CheckConstraint("minutes BETWEEN 0 AND 1440", name="ck_attendance_hr_request_minutes"),
        CheckConstraint("kind IN ('late','early_exit','early_check_in','official_duty','work_from_home','paid_leave','unpaid_leave','weekly_off','extra_ot')", name="ck_attendance_hr_request_kind"),
        CheckConstraint("status IN ('awaiting_cover','pending','approved','rejected')", name="ck_attendance_hr_request_status"),
        {"comment": "Attendance permissions, leave and overtime with durable request keys, staged approvals and accepted cover lifecycle."},
    )


class AttendanceWeeklyOff(Base, PkMixin, TimestampMixin):
    __tablename__ = "staff_attendance_weekly_off"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    day: Mapped[str] = mapped_column(String(12), nullable=False)
    approved_request_id: Mapped[str | None] = mapped_column(ForeignKey("staff_attendance_hr_requests.id"))
    __table_args__ = (UniqueConstraint("user_id", "week_start", name="uq_attendance_weekly_off"),
        CheckConstraint("day IN ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')", name="ck_attendance_weekly_off_day"),
        {"comment": "One weekly off per employee and Monday-based week, optionally fixed by an approved rotation request."})


class AttendanceWorkTiming(Base, PkMixin, TimestampMixin):
    __tablename__ = "staff_attendance_work_timings"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    shift: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "effective_from", name="uq_attendance_work_timing"),
        {"comment": "Effective-dated recurring shift hours; independent from one-date duty plans."})


class AttendanceDutyPlan(Base, PkMixin, TimestampMixin):
    __tablename__ = "staff_attendance_duty_plans"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    shift: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "attendance_date", name="uq_attendance_duty_plan"),
        {"comment": "Explicit single-date working roster overriding a weekly off or holiday, never approved leave."})


class AttendanceCalendarDay(Base, PkMixin, TimestampMixin):
    __tablename__ = "staff_attendance_calendar"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(4), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    request_id: Mapped[str | None] = mapped_column(ForeignKey("staff_attendance_hr_requests.id"))
    converted_from_paid_leave: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    __table_args__ = (UniqueConstraint("user_id", "attendance_date", name="uq_attendance_calendar_day"),
        CheckConstraint("status IN ('WO','H','PL','UL')", name="ck_attendance_calendar_status"),
        {"comment": "Explicit holiday, weekly-off and approved paid/unpaid leave calendar. One paid leave day per month is serialized by the employee lock."})


class AttendanceAdjustment(Base, PkMixin):
    __tablename__ = "staff_attendance_adjustments"
    user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False, index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    status: Mapped[str | None] = mapped_column(String(4))
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    action: Mapped[str | None] = mapped_column(String(12))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("kind IN ('add_punch','time_recovery','status_override')", name="ck_attendance_adjustment_kind"),
        CheckConstraint("minutes BETWEEN 0 AND 1440", name="ck_attendance_adjustment_minutes"),
        {"comment": "Append-only attendance regularisation and reasoned status overrides; raw attendance sessions remain intact."},
    )


class AttendanceCoverItem(Base, PkMixin):
    __tablename__ = "staff_attendance_cover_items"
    request_id: Mapped[str] = mapped_column(ForeignKey("staff_attendance_hr_requests.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("crm_candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    from_user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    to_user_id: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    task_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (UniqueConstraint("request_id", "candidate_id", name="uq_attendance_cover_item"),
        {"comment": "Durable temporary leave-cover ownership ledger. Completed or subsequently reassigned work is not returned."})


# ------------------------------------------------ communication and split intake

class WhatsAppConversation(Base, PkMixin, TimestampMixin):
    __tablename__ = "whatsapp_conversations"
    phone_number_id: Mapped[str] = mapped_column(ForeignKey("whatsapp_channels.phone_number_id"), nullable=False)
    peer_number: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id"), index=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_preview: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("phone_number_id", "peer_number", name="uq_whatsapp_conversation_peer"),
        Index("ix_whatsapp_conversations_recent", "last_message_at", "id"),
        {"comment": "Business conversations with an explicit candidate link and last inbound time for the reply window."},
    )


class WhatsAppSendAttempt(Base, PkMixin, TimestampMixin):
    __tablename__ = "whatsapp_send_attempts"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("whatsapp_conversations.id"), nullable=False, index=True)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sender_user_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("whatsapp_messages.id"))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    template: Mapped[dict | None] = mapped_column(JSONB)
    object_key: Mapped[str | None] = mapped_column(String(500))
    file_name: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(120))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    provider_media_id: Mapped[str | None] = mapped_column(String(100))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    error_code: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("state IN ('queued','preparing','sending','accepted','sent','delivered','read','failed','unknown','cancelled')", name="ck_whatsapp_send_state"),
        Index("ix_whatsapp_send_queue", "created_at", postgresql_where=text("state IN ('queued','preparing','sending')")),
        {"comment": "Durable outgoing WhatsApp intents; uncertain provider outcomes are never blindly resent."},
    )


class WhatsAppDeliveryEvent(Base, PkMixin):
    __tablename__ = "whatsapp_delivery_events"
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("whatsapp_send_attempts.id"), index=True)
    phone_number_id: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    callback_data: Mapped[str | None] = mapped_column(String(64))
    recipient_number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    __table_args__ = ({"comment": "Deduplicated signed provider delivery events retained even when they precede the send response."},)


class WhatsAppTemplate(Base, PkMixin):
    __tablename__ = "whatsapp_templates"
    phone_number_id: Mapped[str] = mapped_column(ForeignKey("whatsapp_channels.phone_number_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str] = mapped_column(String(30), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    components: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("phone_number_id", "name", "language", name="uq_whatsapp_template_language"),
        {"comment": "Provider-synced approved template catalogue for messages outside the customer reply window."},
    )


class IntakeAcknowledgementSettings(Base, PkMixin, TimestampMixin):
    __tablename__ = "intake_acknowledgement_settings"
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    # Added by migration 0018. Deferred so that every ordinary read of this row still works on a database
    # that has not had the migration yet; the template is read and written through intake_acknowledgements.
    whatsapp_template: Mapped[dict | None] = mapped_column(JSONB, deferred=True)
    __table_args__ = (
        UniqueConstraint("source_kind", "channel_id", name="uq_intake_ack_settings_channel"),
        CheckConstraint("source_kind IN ('email','whatsapp')", name="ck_intake_ack_settings_source"),
        {"comment": "Admin-controlled acknowledgement templates; disabled by default and applicable only to new receipts."},
    )


class IntakeAcknowledgement(Base, PkMixin, TimestampMixin):
    __tablename__ = "intake_acknowledgements"
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    outbound_id: Mapped[str | None] = mapped_column(ForeignKey("whatsapp_send_attempts.id"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_intake_ack_receipt"),
        CheckConstraint("source_kind IN ('email','whatsapp')", name="ck_intake_ack_source"),
        CheckConstraint("status IN ('pending','sending','sent','unknown','failed','suppressed','paused')", name="ck_intake_ack_status"),
        Index("ix_intake_ack_queue", "status", "created_at"),
        {"comment": "One acknowledgement intent per successfully saved source receipt, with explicit uncertain-send recovery."},
    )


class MultiPersonIntake(Base, PkMixin, TimestampMixin):
    __tablename__ = "document_person_intakes"
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("auth_users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    pages: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    ignored_pages: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    last_error: Mapped[str | None] = mapped_column(Text)
    claimed_by: Mapped[str | None] = mapped_column(String(80), comment="Worker preparing the pages of this review.")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When the preparation lease lapses and another worker may claim the review.")
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_document_person_intake_source"),
        CheckConstraint("source_type IN ('manual','email','whatsapp')", name="ck_document_person_intake_source"),
        CheckConstraint("status IN ('pending','review','completed')", name="ck_document_person_intake_status"),
        Index("ix_document_person_intakes_pending", "created_at", postgresql_where=text("status = 'pending' AND last_error IS NULL")),
        Index("ix_document_person_intakes_owner_history", "owner_id", "updated_at", "id", postgresql_where=text("source_type = 'manual' AND status = 'completed'")),
        {"comment": "Durable human review of multi-person source documents with immutable source provenance and page evidence."},
    )


class DocumentPersonGroup(Base, PkMixin, TimestampMixin):
    __tablename__ = "document_person_groups"
    bundle_id: Mapped[str] = mapped_column(ForeignKey("document_person_intakes.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    pages: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id"))
    __table_args__ = (
        UniqueConstraint("bundle_id", "ordinal", name="uq_document_person_group_ordinal"),
        CheckConstraint("status IN ('draft','committed')", name="ck_document_person_group_status"),
        Index("ix_document_person_groups_candidate", "candidate_id", "bundle_id", postgresql_where=text("status = 'committed'")),
        {"comment": "Reviewed person fields and exclusive page selections, committed atomically to candidate records."},
    )


# ---------------------------------------------------------------- lead sheet

LEAD_STATUSES = ("new", "contacted", "interested", "resume_requested", "resume_received", "converted",
                 "not_interested", "invalid", "wrong_number")
LEAD_ACTIVITY_KINDS = ("call", "note", "status", "assign", "import", "convert", "resume", "edit")


class LeadBatch(Base, PkMixin):
    __tablename__ = "crm_lead_batches"

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="Hash of the uploaded bytes; warns when the very same file is uploaded again.")
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'meta_ads'"))
    file_format: Mapped[str] = mapped_column(String(10), nullable=False, comment="csv, tsv or xlsx, detected from the content.")
    encoding: Mapped[str | None] = mapped_column(String(20))
    delimiter: Mapped[str | None] = mapped_column(String(4))
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"), comment="assign_to chosen at upload; null when left unassigned or spread automatically.")
    campaign_label: Mapped[str | None] = mapped_column(String(255))
    fill_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'completed'"))
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_new: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_invalid: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_matched_candidates: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    column_map: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="File header to lead field (null = kept in extra).")
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True, comment="Ignored rows with reasons: {duplicates: [...], invalid: [...]} (capped).")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('completed','reverted')", name="ck_lead_batch_status"),
        CheckConstraint("source IN ('meta_ads','other')", name="ck_lead_batch_source"),
        Index("ix_crm_lead_batches_created", "created_at"),
        {"comment": "One row per uploaded lead sheet (CSV, TSV or XLSX) with detected format, column mapping and import totals."},
    )


class Lead(Base, PkMixin, TimestampMixin):
    __tablename__ = "crm_leads"

    batch_id: Mapped[str | None] = mapped_column(ForeignKey("crm_lead_batches.id", ondelete="SET NULL"), comment="Upload that first brought this person in.")
    import_row: Mapped[int | None] = mapped_column(Integer)
    # Dedupe keys (see docs/workflows/lead-sheet-contract.md): lead id, last 10 phone digits, lower-cased e-mail.
    external_id: Mapped[str | None] = mapped_column(String(64), comment="Platform lead id without its l: prefix.")
    phone_key: Mapped[str | None] = mapped_column(String(20), comment="Last 10 digits of the phone (all digits when fewer).")
    email_key: Mapped[str | None] = mapped_column(String(255))
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="When the person answered the ad, as in the file.")

    name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    phone_country_code: Mapped[str | None] = mapped_column(String(6))
    email: Mapped[str | None] = mapped_column(String(255))
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    whatsapp_same: Mapped[bool | None] = mapped_column(Boolean)
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    current_location: Mapped[str | None] = mapped_column(String(120))
    position_applied: Mapped[str | None] = mapped_column(String(200))
    country_interest: Mapped[str | None] = mapped_column(String(120))
    experience_restaurant_years: Mapped[float | None] = mapped_column(Numeric(5, 1))
    experience_restaurant_text: Mapped[str | None] = mapped_column(String(200))
    experience_total_years: Mapped[float | None] = mapped_column(Numeric(5, 1))
    experience_total_text: Mapped[str | None] = mapped_column(String(200))
    gender: Mapped[str | None] = mapped_column(String(20))
    marital_status: Mapped[str | None] = mapped_column(String(120))
    dob: Mapped[date | None] = mapped_column(Date)
    education_level: Mapped[str | None] = mapped_column(String(200))
    military_status: Mapped[str | None] = mapped_column(String(200))

    platform: Mapped[str | None] = mapped_column(String(30))
    is_organic: Mapped[bool | None] = mapped_column(Boolean)
    lead_status_source: Mapped[str | None] = mapped_column(String(60), comment="lead_status column of the platform file.")
    campaign_id: Mapped[str | None] = mapped_column(String(64))
    campaign_name: Mapped[str | None] = mapped_column(String(255))
    adset_id: Mapped[str | None] = mapped_column(String(64))
    adset_name: Mapped[str | None] = mapped_column(String(255))
    ad_id: Mapped[str | None] = mapped_column(String(64))
    ad_name: Mapped[str | None] = mapped_column(String(255))
    form_id: Mapped[str | None] = mapped_column(String(64))
    form_name: Mapped[str | None] = mapped_column(String(255))
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="Columns of the file that have no lead field (different ad forms ask different questions).")
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), deferred=True, comment="The whole original row, header to text.")

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'new'"))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    notes: Mapped[str | None] = mapped_column(Text)
    resume_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id", ondelete="SET NULL"), comment="Data Bank candidate this lead was converted to or linked with.")
    matched_candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id", ondelete="SET NULL"), comment="Candidate with the same phone or e-mail found at import time (flag only, not a link).")

    __table_args__ = (
        Index("uq_crm_leads_external_id", "external_id", unique=True, postgresql_where=text("external_id IS NOT NULL")),
        Index("ix_crm_leads_phone_key", "phone_key", postgresql_where=text("phone_key IS NOT NULL")),
        Index("ix_crm_leads_email_key", "email_key", postgresql_where=text("email_key IS NOT NULL")),
        Index("ix_crm_leads_owner_status", "owner_id", "status"),
        Index("ix_crm_leads_campaign", "campaign_name"),
        Index("ix_crm_leads_created_time", text("created_time DESC NULLS LAST")),
        Index("ix_crm_leads_batch", "batch_id"),
        Index("ix_crm_leads_follow_up", "next_follow_up_at", postgresql_where=text("next_follow_up_at IS NOT NULL")),
        CheckConstraint("status IN ('new','contacted','interested','resume_requested','resume_received','converted',"
                        "'not_interested','invalid','wrong_number')", name="ck_crm_leads_status"),
        {"comment": "People who answered an ad (platform lead exports), stored once each, with the calling workflow: owner, status, follow-up and conversion."},
    )


class LeadActivity(Base, PkMixin):
    __tablename__ = "crm_lead_activities"

    lead_id: Mapped[str] = mapped_column(ForeignKey("crm_leads.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        Index("ix_crm_lead_activities_lead", "lead_id", text("created_at DESC")),
        CheckConstraint("kind IN ('call','note','status','assign','import','convert','resume','edit')", name="ck_crm_lead_activity_kind"),
        {"comment": "History of calls, notes, status changes, assignments and conversion for each lead."},
    )


class WorkerHeartbeat(Base):
    """One row per running reading worker process, refreshed every few seconds (migration 0029)."""

    __tablename__ = "crm_worker_heartbeats"
    __table_args__ = (
        Index("ix_crm_worker_heartbeats_kind_seen", "kind", "seen_at"),
        {"comment": "Which background reading workers are running right now: each refreshes its own row every few seconds, so screens can tell when no reader is alive."},
    )

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="Unique id of one worker process (kind, host, process id and a random suffix).")
    kind: Mapped[str] = mapped_column(Text, nullable=False, comment="document (uploaded files) or email (mailbox attachments).")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="When this worker process started.")
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="When this worker last reported in; a worker not seen for 30 seconds is treated as not running.")
    threads: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"), comment="How many documents this worker can read at once.")
    busy: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="How many documents it was reading at the last report.")
    version: Mapped[str | None] = mapped_column(Text, comment="The code version the worker runs, so an old worker can be told from a current one.")


class InvoiceSettings(Base):
    __tablename__ = "crm_invoice_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    invoice_prefix: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    next_number: Mapped[int | None] = mapped_column(Integer)
    cgst_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, server_default="9")
    sgst_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, server_default="9")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    __table_args__ = ({"comment": "Reviewed issuer details and locked invoice number sequence; no invoice is issued before setup is saved."},)


class CandidateInvoice(Base, PkMixin, TimestampMixin):
    __tablename__ = "crm_candidate_invoices"
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("crm_candidates.id", ondelete="SET NULL"))
    application_id: Mapped[str | None] = mapped_column(ForeignKey("crm_applications.id", ondelete="SET NULL"))
    scope_key: Mapped[str] = mapped_column(String(36), nullable=False, server_default="", comment="Application id, or empty while awaiting an unambiguous placement.")
    service_fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending_fee")
    number: Mapped[str | None] = mapped_column(String(48), unique=True)
    ticket_attachment_id: Mapped[str | None] = mapped_column(ForeignKey("document_attachments.id", ondelete="SET NULL"))
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("document_attachments.id", ondelete="SET NULL"))
    snapshot: Mapped[dict | None] = mapped_column(JSONB, comment="Immutable issuer, recipient, tax amounts and number as they appeared on the issued PDF.")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("candidate_id", "scope_key", name="uq_candidate_invoice_placement"),
        CheckConstraint("service_fee IS NULL OR (service_fee >= 5000 AND service_fee <= 30000)", name="ck_candidate_invoice_fee"),
        {"comment": "One automatic service invoice per candidate placement, with pending prerequisites and frozen issued evidence."},
    )


class InvoicePayment(Base, PkMixin):
    __tablename__ = "crm_invoice_payments"
    invoice_id: Mapped[str] = mapped_column(ForeignKey("crm_candidate_invoices.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    reference: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_stage_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    source_application_id: Mapped[str | None] = mapped_column(String(36), unique=True, comment="One explicitly confirmed historical advance per placement, including reopened attempts.")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[str | None] = mapped_column(ForeignKey("auth_users.id", ondelete="SET NULL"))
    void_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("invoice_id", "idempotency_key", name="uq_invoice_payment_request"),
        Index("ix_invoice_payments_invoice_date", "invoice_id", "paid_on"),
        CheckConstraint("amount > 0", name="ck_invoice_payment_positive"),
        CheckConstraint("method IN ('cash','bank','upi','cheque')", name="ck_invoice_payment_method"),
        CheckConstraint("(source_stage_id IS NULL) = (source_application_id IS NULL)", name="ck_invoice_payment_source"),
        CheckConstraint("(voided_at IS NULL AND void_reason IS NULL AND voided_by IS NULL) OR (voided_at IS NOT NULL AND void_reason IS NOT NULL AND length(trim(void_reason)) > 0)", name="ck_invoice_payment_void"),
        {"comment": "Actual candidate fee receipts. Financial entries are immutable; supervised voids retain the original receipt and reason."},
    )
