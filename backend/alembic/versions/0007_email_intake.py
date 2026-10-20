"""Durable email intake checkpoints and worker state.

Revision ID: 0007_email_intake
Revises: 0006_domain_table_names
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_email_intake"
down_revision = "0006_domain_table_names"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("email_accounts", sa.Column("sync_state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("email_accounts", sa.Column("last_error", sa.Text()))
    op.add_column("email_accounts", sa.Column("sync_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_accounts", sa.Column("worker_seen_at", sa.DateTime(timezone=True)))
    op.create_table(
        "email_ingestion_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("email_messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text()),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("crm_candidates.id")),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("inspection", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending','processing','retry','needs_review','completed','ignored','failed')",
                           name="ck_email_ingestion_jobs_status"),
        comment="Durable email intake queue and per-document OCR checkpoints; links source messages to candidate outcomes.",
    )
    op.create_index("ix_email_ingestion_jobs_ready", "email_ingestion_jobs", ["next_attempt_at"],
                    postgresql_where=sa.text("status IN ('pending','processing','retry')"))


def downgrade():
    op.drop_table("email_ingestion_jobs")
    for column in ("worker_seen_at", "sync_requested", "last_error", "sync_state"):
        op.drop_column("email_accounts", column)
