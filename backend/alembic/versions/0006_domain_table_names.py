"""Group tables by domain and describe their purpose in database browsers.

Revision ID: 0006_domain_table_names
Revises: 0005_candidate_ocr_data

Only names and comments change. Renaming preserves table/index OIDs, data, foreign
keys, ownership and grants. Existing constraint labels and their backing indexes
are deliberately retained: their enforcement does not depend on the table name.
The descriptive unique index uq_one_active_application_per_candidate also keeps
its existing name. Application API names and persisted entity-type values do not
change.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_domain_table_names"
down_revision: str | None = "0005_candidate_ocr_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen migration data: never import the evolving application models here.
TABLE_RENAMES = (
    (
        "users",
        "auth_users",
        "CRM sign-in accounts, password hashes, roles and account status. "
        "These are separate from connected email-provider accounts.",
    ),
    (
        "sessions",
        "auth_sessions",
        "CRM sign-in sessions and expiry times, referenced by the session cookie. "
        "Deleting a row revokes that session.",
    ),
    (
        "clients",
        "crm_client_contacts",
        "Client contact people who own employer-company records.",
    ),
    (
        "companies",
        "crm_employer_companies",
        "Employer companies linked to client contacts; recruitment orders belong to these companies.",
    ),
    (
        "designations",
        "crm_job_designations",
        "Job titles and their skills and experience requirements, referenced by recruitment orders.",
    ),
    (
        "orders",
        "crm_job_orders",
        "Employer recruitment orders with job designation, openings and employment terms.",
    ),
    (
        "candidates",
        "crm_candidates",
        "Recruitment candidate profiles, ownership, contact details and extracted document data.",
    ),
    (
        "attachments",
        "document_attachments",
        "Versioned document metadata and storage keys for CRM records; file contents live in file storage.",
    ),
    (
        "applications",
        "crm_applications",
        "Candidate-to-job-order placements with current workflow stage, status and saved draft.",
    ),
    (
        "application_stages",
        "crm_application_stages",
        "Recorded attempts and outcomes for each stage of a candidate application.",
    ),
    (
        "tasks",
        "workflow_tasks",
        "Assigned recruitment work, including initial calls and application-stage tasks.",
    ),
    (
        "remarks",
        "workflow_task_remarks",
        "Dated staff updates attached to recruitment workflow tasks.",
    ),
    (
        "todo_items",
        "personal_todos",
        "Private to-do items belonging to individual CRM users.",
    ),
    (
        "mail_accounts",
        "email_accounts",
        "Connected email-provider accounts for the inbox and ingestion; these are not CRM sign-in accounts.",
    ),
    (
        "mail_messages",
        "email_messages",
        "Provider email messages associated with connected inbox accounts and, optionally, candidates.",
    ),
    (
        "mail_attachments",
        "email_attachments",
        "Attachment metadata from provider email messages, including storage keys and deduplication hashes.",
    ),
    (
        "ingestion_failures",
        "ingestion_failures",
        "Failed document or message ingestion attempts and their resolution status.",
    ),
    (
        "notifications",
        "system_notifications",
        "In-app notifications addressed to CRM users.",
    ),
    (
        "audit_log",
        "system_audit_log",
        "Recorded CRM actions with actor, affected entity and event metadata.",
    ),
    (
        "assignment_pointer",
        "workflow_assignment_cursor",
        "Singleton round-robin assignment cursor; id 1 records the last assigned CRM user.",
    ),
)

INDEX_RENAMES = (
    ("ix_sessions_user_id", "ix_auth_sessions_user_id"),
    ("ix_companies_client_id", "ix_crm_employer_companies_client_id"),
    ("ix_orders_company_id", "ix_crm_job_orders_company_id"),
    ("ix_orders_designation_id", "ix_crm_job_orders_designation_id"),
    ("ix_candidates_email_lower", "ix_crm_candidates_email_lower"),
    ("ix_candidates_owner_id", "ix_crm_candidates_owner_id"),
    ("ix_candidates_passport_upper", "ix_crm_candidates_passport_upper"),
    ("ix_candidates_phone", "ix_crm_candidates_phone"),
    ("ix_candidates_resume_tsv", "ix_crm_candidates_resume_tsv"),
    ("ix_attachments_content_hash", "ix_document_attachments_content_hash"),
    ("ix_attachments_owner", "ix_document_attachments_owner"),
    ("ix_applications_candidate_id", "ix_crm_applications_candidate_id"),
    ("ix_applications_order_id", "ix_crm_applications_order_id"),
    ("ix_application_stages_application_id", "ix_crm_application_stages_application_id"),
    ("ix_tasks_application_id", "ix_workflow_tasks_application_id"),
    ("ix_tasks_candidate_id", "ix_workflow_tasks_candidate_id"),
    ("ix_tasks_open_by_age", "ix_workflow_tasks_open_by_age"),
    ("ix_tasks_owner_id", "ix_workflow_tasks_owner_id"),
    ("ix_tasks_owner_open", "ix_workflow_tasks_owner_open"),
    ("ix_remarks_created_at", "ix_workflow_task_remarks_created_at"),
    ("ix_remarks_task_id", "ix_workflow_task_remarks_task_id"),
    ("ix_todo_items_owner_id", "ix_personal_todos_owner_id"),
    ("ix_mail_messages_account_id", "ix_email_messages_account_id"),
    ("ix_mail_received", "ix_email_messages_received_at"),
    ("ix_mail_attachments_content_hash", "ix_email_attachments_content_hash"),
    ("ix_mail_attachments_message_id", "ix_email_attachments_message_id"),
    ("ix_notifications_user_id", "ix_system_notifications_user_id"),
    ("ix_audit_log_at", "ix_system_audit_log_at"),
    ("ix_audit_log_entity_id", "ix_system_audit_log_entity_id"),
    ("ix_audit_log_entity_type", "ix_system_audit_log_entity_type"),
)

COLUMN_COMMENTS = (
    ("auth_users", "email", "CRM sign-in email address; unique per account."),
    ("auth_users", "role", "CRM permission role: admin, manager or staff."),
    (
        "auth_users",
        "password_hash",
        "One-way password hash used to verify CRM sign-in; never store a plaintext password here.",
    ),
    (
        "auth_sessions",
        "user_id",
        "CRM account that owns this session; references auth_users.id.",
    ),
    ("auth_sessions", "expires_at", "Time after which this CRM session is no longer accepted."),
)


def _limit_waits() -> None:
    # LOCAL applies only to Alembic's PostgreSQL migration transaction.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def _rename_index(old: str, new: str) -> None:
    # Identifiers come only from the frozen constants above.
    op.execute(f'ALTER INDEX public."{old}" RENAME TO "{new}"')


def upgrade() -> None:
    _limit_waits()
    for old, new, comment in TABLE_RENAMES:
        if old != new:
            op.rename_table(old, new, schema="public")
        op.create_table_comment(new, comment, schema="public")

    for old, new in INDEX_RENAMES:
        _rename_index(old, new)

    for table, column, comment in COLUMN_COMMENTS:
        op.alter_column(table, column, comment=comment, schema="public")


def downgrade() -> None:
    _limit_waits()
    # These comments were introduced by this revision; remove them on rollback.
    for table, column, _ in COLUMN_COMMENTS:
        op.alter_column(table, column, comment=None, schema="public")

    for old, new in reversed(INDEX_RENAMES):
        _rename_index(new, old)

    for old, new, _ in reversed(TABLE_RENAMES):
        op.drop_table_comment(new, schema="public")
        if old != new:
            op.rename_table(new, old, schema="public")
