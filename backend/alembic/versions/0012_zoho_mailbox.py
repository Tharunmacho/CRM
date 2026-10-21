"""Durable Zoho mailbox state, drafts, attachments and explicit send attempts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_zoho_mailbox"
down_revision = "0011_company_whatsapp"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("email_messages", sa.Column("bcc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("email_messages", sa.Column("folder", sa.String(255), nullable=False, server_default="INBOX"))
    op.add_column("email_messages", sa.Column("remote_uid", sa.BigInteger(), nullable=True))
    op.add_column("email_messages", sa.Column("uid_validity", sa.String(80), nullable=True))
    op.add_column("email_messages", sa.Column("to_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("email_messages", sa.Column("cc_addresses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("email_messages", sa.Column("starred", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_messages", sa.Column("remote_deleted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_messages", sa.Column("previous_folder", sa.String(255), nullable=True))
    op.add_column("email_messages", sa.Column("remote_state", sa.String(20), nullable=False, server_default="synced"))
    op.add_column("email_messages", sa.Column("remote_error", sa.Text(), nullable=True))
    op.add_column("email_messages", sa.Column("remote_operation", postgresql.JSONB(), nullable=True))
    op.execute("""
        CREATE TABLE email_drafts (
            account_id VARCHAR(36) NOT NULL,
            actor_id VARCHAR(36) NOT NULL,
            mode VARCHAR(20) DEFAULT 'compose' NOT NULL,
            source_message_id VARCHAR(36),
            to_addresses JSONB DEFAULT '[]'::jsonb NOT NULL,
            cc_addresses JSONB DEFAULT '[]'::jsonb NOT NULL,
            bcc_addresses JSONB DEFAULT '[]'::jsonb NOT NULL,
            subject TEXT DEFAULT '' NOT NULL,
            body TEXT DEFAULT '' NOT NULL,
            lock_version INTEGER DEFAULT '0' NOT NULL,
            status VARCHAR(20) DEFAULT 'draft' NOT NULL,
            remote_uid BIGINT,
            remote_uid_validity VARCHAR(80),
            remote_folder VARCHAR(255),
            remote_state VARCHAR(20) DEFAULT 'pending' NOT NULL,
            last_error TEXT,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_email_drafts_mode CHECK (mode IN ('compose','reply','reply_all','forward','edit_draft')),
            CONSTRAINT ck_email_drafts_status CHECK (status IN ('draft','sending','sent','unknown','failed','discarded')),
            FOREIGN KEY(account_id) REFERENCES email_accounts (id),
            FOREIGN KEY(actor_id) REFERENCES auth_users (id),
            FOREIGN KEY(source_message_id) REFERENCES email_messages (id)
        )
    """)
    op.execute('CREATE INDEX ix_email_drafts_account_id ON email_drafts (account_id)')
    op.execute("COMMENT ON TABLE email_drafts IS 'Durable Zoho message drafts with recipients, optimistic versions, mailbox synchronization and send state.'")
    op.execute("""
        CREATE TABLE email_draft_attachments (
            draft_id VARCHAR(36) NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            content_type VARCHAR(120) NOT NULL,
            size_bytes BIGINT NOT NULL,
            object_key VARCHAR(500) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            id VARCHAR(36) NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(draft_id) REFERENCES email_drafts (id) ON DELETE CASCADE
        )
    """)
    op.execute('CREATE INDEX ix_email_draft_attachments_draft_id ON email_draft_attachments (draft_id)')
    op.execute("COMMENT ON TABLE email_draft_attachments IS 'Immutable draft attachment bytes referenced by durable Zoho drafts and send snapshots.'")
    op.execute("""
        CREATE TABLE email_send_attempts (
            draft_id VARCHAR(36) NOT NULL,
            actor_id VARCHAR(36) NOT NULL,
            request_key VARCHAR(64) NOT NULL,
            snapshot JSONB NOT NULL,
            status VARCHAR(20) DEFAULT 'pending' NOT NULL,
            provider_message_id VARCHAR(255),
            last_error TEXT,
            refused_recipients JSONB DEFAULT '[]'::jsonb NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            sent_at TIMESTAMP WITH TIME ZONE,
            id VARCHAR(36) NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_email_send_attempts_status CHECK (status IN ('pending','sending','sent','partial','unknown','failed')),
            FOREIGN KEY(draft_id) REFERENCES email_drafts (id),
            FOREIGN KEY(actor_id) REFERENCES auth_users (id),
            UNIQUE (request_key)
        )
    """)
    op.execute('CREATE INDEX ix_email_send_attempts_draft_id ON email_send_attempts (draft_id)')
    op.execute("COMMENT ON TABLE email_send_attempts IS 'Idempotent human-requested Zoho sends with immutable content snapshots and explicit uncertain/partial delivery outcomes.'")


def downgrade():
    op.drop_column("email_messages", "bcc_addresses")
    op.drop_table("email_send_attempts")
    op.drop_table("email_draft_attachments")
    op.drop_table("email_drafts")
    op.drop_column("email_messages", "remote_operation")
    op.drop_column("email_messages", "remote_error")
    op.drop_column("email_messages", "remote_state")
    op.drop_column("email_messages", "previous_folder")
    op.drop_column("email_messages", "remote_deleted")
    op.drop_column("email_messages", "starred")
    op.drop_column("email_messages", "cc_addresses")
    op.drop_column("email_messages", "to_addresses")
    op.drop_column("email_messages", "uid_validity")
    op.drop_column("email_messages", "remote_uid")
    op.drop_column("email_messages", "folder")
