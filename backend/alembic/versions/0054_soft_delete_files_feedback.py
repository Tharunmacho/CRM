"""Soft delete for uploaded documents and application-form notes.

* ``document_attachments.deleted_at`` / ``deleted_by``: a removed file stays in the table and in storage (every earlier version too);
  every reader skips rows where ``deleted_at`` is set, so the slot falls back to its previous version, or shows empty.
* ``crm_application_feedback.deleted_at`` / ``deleted_by`` / ``edited_at``: a removed note is hidden, not deleted (the audit entry keeps
  its text); ``edited_at`` is the "edited" mark of a changed note.

Plain, idempotent (ADD COLUMN IF NOT EXISTS), safe on an empty database and while older code shares the database: the new columns are
nullable and older code ignores them. The downgrade drops them. The models declare the same columns (app/db/models.py).
"""
from alembic import op

revision = "0054_soft_delete_files_fb"  # (alembic_version holds 32 characters)
down_revision = "0053_remark_soft_delete"  # chained after the notes soft delete
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE document_attachments ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE document_attachments ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(36) REFERENCES auth_users (id) ON DELETE SET NULL")
    op.execute("COMMENT ON COLUMN document_attachments.deleted_at IS 'When the file was removed from its document slot; the bytes and earlier versions are kept. NULL while the file is in use.'")
    op.execute("COMMENT ON COLUMN document_attachments.deleted_by IS 'The CRM user who removed the file.'")
    op.execute("ALTER TABLE crm_application_feedback ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE crm_application_feedback ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(36) REFERENCES auth_users (id) ON DELETE SET NULL")
    op.execute("ALTER TABLE crm_application_feedback ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP WITH TIME ZONE")
    op.execute("COMMENT ON COLUMN crm_application_feedback.deleted_at IS 'When the note was removed; it is hidden everywhere but the audit trail keeps its text. NULL while the note is shown.'")
    op.execute("COMMENT ON COLUMN crm_application_feedback.deleted_by IS 'The CRM user who removed the note.'")
    op.execute("COMMENT ON COLUMN crm_application_feedback.edited_at IS 'When the note was last edited by hand; NULL for a note never edited.'")


def downgrade():
    op.execute("ALTER TABLE crm_application_feedback DROP COLUMN IF EXISTS edited_at")
    op.execute("ALTER TABLE crm_application_feedback DROP COLUMN IF EXISTS deleted_by")
    op.execute("ALTER TABLE crm_application_feedback DROP COLUMN IF EXISTS deleted_at")
    op.execute("ALTER TABLE document_attachments DROP COLUMN IF EXISTS deleted_by")
    op.execute("ALTER TABLE document_attachments DROP COLUMN IF EXISTS deleted_at")
