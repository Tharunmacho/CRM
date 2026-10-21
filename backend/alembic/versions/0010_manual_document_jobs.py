"""Durable manual document extraction and recoverable candidate form groups.

Revision ID: 0010_manual_document_jobs
Revises: 0009_communication_channels
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_manual_document_jobs"
down_revision = "0009_communication_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_workflow_task_events_kind", "workflow_task_events", type_="check")
    op.create_check_constraint("ck_workflow_task_events_kind", "workflow_task_events", "kind IN ('escalation','missed_update','sla_reset','reminder','assignment')")
    op.create_table("document_extraction_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("auth_users.id"), nullable=False),
        sa.Column("group_id", sa.String(36), nullable=False), sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(150), nullable=False), sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_kb", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("draft", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_error", sa.Text()),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("crm_candidates.id")),
        sa.Column("attachment_id", sa.String(36), sa.ForeignKey("document_attachments.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "group_id", "kind", "content_hash", name="uq_document_extraction_source"),
        sa.CheckConstraint("status IN ('uploaded','pending','processing','retry','completed','needs_review','failed','attached','dismissed')", name="ck_document_extraction_jobs_status"),
        comment="Manual candidate document uploads, durable OCR checkpoints and recruiter form drafts grouped by an intake session.")
    for column in ("owner_id", "group_id"):
        op.create_index(f"ix_document_extraction_jobs_{column}", "document_extraction_jobs", [column])
    op.create_index("ix_document_extraction_jobs_ready", "document_extraction_jobs", ["status", "next_attempt_at"])


def downgrade():
    op.drop_table("document_extraction_jobs")
    op.execute("DELETE FROM workflow_task_events WHERE kind = 'assignment'")
    op.drop_constraint("ck_workflow_task_events_kind", "workflow_task_events", type_="check")
    op.create_check_constraint("ck_workflow_task_events_kind", "workflow_task_events", "kind IN ('escalation','missed_update','sla_reset','reminder')")
