"""attachments

One table for every uploaded file rather than one per owner. candidate_files is
replaced by it; nothing had been written there, so no data moves.

Revision ID: 0003_attachments
Revises: 0002_advance_currency
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003_attachments"
down_revision: str | None = "0002_advance_currency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE attachments (
            id VARCHAR(36) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            entity_id VARCHAR(36) NOT NULL,
            slot VARCHAR(40) NOT NULL,
            version INTEGER DEFAULT 1 NOT NULL,
            file_name VARCHAR(255) NOT NULL,
            size_kb INTEGER,
            content_type VARCHAR(120),
            object_key VARCHAR(500) NOT NULL,
            content_hash VARCHAR(64),
            uploaded_by VARCHAR(36),
            uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(uploaded_by) REFERENCES users (id),
            CONSTRAINT uq_attachment_version UNIQUE (entity_type, entity_id, slot, version)
        )
        """
    )
    op.execute("CREATE INDEX ix_attachments_owner ON attachments (entity_type, entity_id, slot)")
    op.execute("CREATE INDEX ix_attachments_content_hash ON attachments (content_hash)")
    op.execute("DROP TABLE IF EXISTS candidate_files CASCADE")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS attachments CASCADE")
