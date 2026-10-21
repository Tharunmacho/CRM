"""Historical recruiter ownership and durable SLA events.

Revision ID: 0008_workflow_history
Revises: 0007_email_intake
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_workflow_history"
down_revision = "0007_email_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crm_candidates", sa.Column("created_by", sa.String(36), sa.ForeignKey("auth_users.id"), comment="CRM user who entered the candidate; null for automatic intake or legacy rows."))
    for table, column, description in (
        ("crm_candidates", "intake_owner_id", "Recruiter assigned at intake. Legacy rows use ownership at migration."),
        ("crm_applications", "owner_id", "Recruiter at mapping. Legacy rows use candidate ownership at migration."),
        ("crm_application_stages", "owner_id", "Responsible recruiter for this recorded stage attempt."),
    ):
        op.add_column(table, sa.Column(column, sa.String(36), sa.ForeignKey("auth_users.id"), comment=description))
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.execute("UPDATE crm_candidates SET intake_owner_id = owner_id")
    op.execute("UPDATE crm_applications a SET owner_id = c.owner_id FROM crm_candidates c WHERE c.id = a.candidate_id")
    op.execute("UPDATE crm_application_stages s SET owner_id = a.owner_id FROM crm_applications a WHERE a.id = s.application_id")
    op.add_column("workflow_tasks", sa.Column("sla_started_at", sa.DateTime(timezone=True), comment="Beginning of the current SLA window; reassignment preserves it."))
    op.add_column("workflow_tasks", sa.Column("due_at", sa.DateTime(timezone=True), comment="Deadline of the current 48-hour SLA window."))
    op.create_index("ix_workflow_tasks_due_at", "workflow_tasks", ["due_at"])
    op.execute("UPDATE workflow_tasks SET sla_started_at = created_at, due_at = created_at + interval '48 hours'")
    op.add_column("personal_todos", sa.Column("reminded_at", sa.DateTime(timezone=True), comment="Delivery time of the current due-date reminder."))
    op.create_table(
        "workflow_task_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("workflow_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("crm_candidates.id", ondelete="SET NULL")),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("auth_users.id")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("event_key", sa.String(180), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("meta", postgresql.JSONB()),
        sa.CheckConstraint("kind IN ('escalation','missed_update','sla_reset','reminder')", name="ck_workflow_task_events_kind"),
        comment="Append-only task SLA events with ownership at event time and a unique scheduler delivery key.",
    )
    for column in ("task_id", "candidate_id", "owner_id", "occurred_at"):
        op.create_index(f"ix_workflow_task_events_{column}", "workflow_task_events", [column])
    op.create_table(
        "workflow_settings",
        sa.Column("id", sa.Integer(), primary_key=True, server_default="1"),
        sa.Column("escalation_repeat_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_daily_check_date", sa.Date()),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("auth_users.id")),
        sa.CheckConstraint("id = 1", name="ck_workflow_settings_singleton"),
        sa.CheckConstraint("escalation_repeat_hours IN (0, 24)", name="ck_workflow_settings_repeat"),
        comment="Agency task automation configuration and scheduler checkpoint; repeat 0 disables repeat reminders, 24 enables daily reminders.",
    )


def downgrade():
    op.drop_column("crm_candidates", "created_by")
    op.drop_table("workflow_settings")
    op.drop_table("workflow_task_events")
    op.drop_column("personal_todos", "reminded_at")
    op.drop_index("ix_workflow_tasks_due_at", table_name="workflow_tasks")
    op.drop_column("workflow_tasks", "due_at")
    op.drop_column("workflow_tasks", "sla_started_at")
    for table, column in (("crm_application_stages", "owner_id"), ("crm_applications", "owner_id"), ("crm_candidates", "intake_owner_id")):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_column(table, column)
