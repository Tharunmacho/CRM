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
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
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
    whatsapp: Mapped[str | None] = mapped_column(String(40))
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))

    companies: Mapped[list["Company"]] = relationship(back_populates="client")


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
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(Text)
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
    __table_args__ = (
        CheckConstraint("status IN ('pending','processing','retry','needs_review','completed','ignored','failed')",
                        name="ck_email_ingestion_jobs_status"),
        Index("ix_email_ingestion_jobs_ready", "next_attempt_at",
              postgresql_where=text("status IN ('pending','processing','retry')")),
        {"comment": "Durable email intake queue and per-document OCR checkpoints; links source messages to candidate outcomes."},
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
