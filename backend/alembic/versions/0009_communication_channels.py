"""Mailbox authorization/replies, WhatsApp intake and stable CRM reference allocation.

Revision ID: 0009_communication_channels
Revises: 0008_workflow_history
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_communication_channels"
down_revision = "0008_workflow_history"
branch_labels = None
depends_on = None


def stamp(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    op.add_column("email_accounts", sa.Column("extraction_seen_at", sa.DateTime(timezone=True)))
    op.add_column("email_accounts", sa.Column("disconnected_at", sa.DateTime(timezone=True)))
    op.add_column("email_accounts", sa.Column("connected_by", sa.String(36), sa.ForeignKey("auth_users.id")))
    op.add_column("email_messages", sa.Column("message_id_header", sa.Text()))
    op.add_column("email_messages", sa.Column("references_header", sa.Text()))
    op.add_column("email_messages", sa.Column("reply_to", sa.String(255)))
    op.create_table("email_oauth_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)), stamp("created_at"),
        comment="Short-lived single-use Gmail/Microsoft authorization handshakes with hashed state and encrypted PKCE verifier.")
    op.create_index("ix_email_oauth_states_expires_at", "email_oauth_states", ["expires_at"])
    op.create_table("email_replies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("email_messages.id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("email_accounts.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("auth_users.id")),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("provider_message_id", sa.String(255)), sa.Column("last_error", sa.Text()),
        stamp("created_at"), sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending','sending','sent','unknown','failed')", name="ck_email_replies_status"),
        comment="Human-requested originating-mailbox replies. Unknown outcomes require reconciliation, never automatic resend.")
    for column in ("message_id", "account_id"):
        op.create_index(f"ix_email_replies_{column}", "email_replies", [column])
    op.create_table("whatsapp_channels",
        sa.Column("phone_number_id", sa.String(80), primary_key=True),
        sa.Column("display_number", sa.String(80)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_ingest", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_received_at", sa.DateTime(timezone=True)),
        sa.Column("worker_seen_at", sa.DateTime(timezone=True)), sa.Column("last_error", sa.Text()),
        stamp("created_at"), stamp("updated_at"),
        comment="WhatsApp Business phone numbers and inbound processing health; provider credentials remain in server configuration.")
    op.create_table("whatsapp_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_message_id", sa.String(255), nullable=False, unique=True),
        sa.Column("phone_number_id", sa.String(80), sa.ForeignKey("whatsapp_channels.phone_number_id"), nullable=False),
        sa.Column("sender_number", sa.String(40), nullable=False),
        sa.Column("sender_name", sa.String(200)), sa.Column("message_type", sa.String(30), nullable=False),
        sa.Column("body", sa.Text()), sa.Column("media_id", sa.String(100)),
        sa.Column("file_name", sa.String(255)), sa.Column("content_type", sa.String(120)),
        sa.Column("object_key", sa.String(500)), sa.Column("content_hash", sa.String(64)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        stamp("next_attempt_at"), sa.Column("last_error", sa.Text()),
        sa.Column("candidate_id", sa.String(36), sa.ForeignKey("crm_candidates.id")),
        sa.Column("processing_state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        stamp("created_at"), stamp("updated_at"),
        sa.CheckConstraint("status IN ('received','pending','processing','retry','needs_review','completed','ignored','failed')", name="ck_whatsapp_messages_status"),
        comment="Inbound WhatsApp receipts, original media and durable extraction checkpoints. Sender number records provenance, not assumed identity.")
    for column in ("phone_number_id", "received_at", "candidate_id"):
        op.create_index(f"ix_whatsapp_messages_{column}", "whatsapp_messages", [column])
    op.create_index("ix_whatsapp_messages_ready", "whatsapp_messages", ["next_attempt_at"], postgresql_where=sa.text("status IN ('pending','processing','retry')"))
    op.execute("CREATE SEQUENCE crm_candidate_reference_seq")
    op.execute("SELECT setval('crm_candidate_reference_seq', GREATEST(COALESCE((SELECT max(substring(ref from '^CND-([0-9]+)$')::bigint) FROM crm_candidates), 1000) + 1, 1001), false)")
    op.execute("CREATE SEQUENCE crm_order_reference_seq")
    op.execute("SELECT setval('crm_order_reference_seq', COALESCE((SELECT max(substring(ref from '^ORD-[0-9]{4}-([0-9]+)$')::bigint) FROM crm_job_orders), 0) + 1, false)")
    op.execute("""CREATE FUNCTION crm_refresh_resume_search() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN NEW.resume_tsv := to_tsvector('simple', coalesce(NEW.resume_text, '')); RETURN NEW; END; $$""")
    op.execute("CREATE TRIGGER crm_candidates_resume_search BEFORE INSERT OR UPDATE OF resume_text ON crm_candidates FOR EACH ROW EXECUTE FUNCTION crm_refresh_resume_search()")
    op.execute("UPDATE crm_candidates SET resume_tsv = to_tsvector('simple', coalesce(resume_text, ''))")


def downgrade():
    op.execute("DROP TRIGGER crm_candidates_resume_search ON crm_candidates")
    op.execute("DROP FUNCTION crm_refresh_resume_search()")
    op.execute("DROP SEQUENCE crm_order_reference_seq")
    op.execute("DROP SEQUENCE crm_candidate_reference_seq")
    for table in ("whatsapp_messages", "whatsapp_channels", "email_replies", "email_oauth_states"):
        op.drop_table(table)
    for column in ("reply_to", "references_header", "message_id_header"):
        op.drop_column("email_messages", column)
    op.drop_column("email_accounts", "extraction_seen_at")
    op.drop_column("email_accounts", "connected_by")
    op.drop_column("email_accounts", "disconnected_at")
