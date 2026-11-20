"""Soft delete for notes and a "cleared" mark for the Tasks page's remark cell.

``workflow_task_remarks`` was append-only. The Tasks page now lets the note's author, the candidate's recruiter and any Admin / MD delete a
note, and lets "Select remark" in the Remarks dropdown go back to no remark. Neither erases a row, so history stays traceable:

* ``deleted_at`` (timestamptz, NULL) and ``deleted_by`` (-> auth_users, ON DELETE SET NULL): a deleted note. Every reader (the Tasks rows and
  their Excel file, Follow-up, the candidate's profile, reports, exports, the update checks) ignores it. An audit entry "Deleted a note"
  keeps the text, who and when.
* ``cleared_at`` (timestamptz, NULL): the candidate's remark cell was cleared at this moment. The note itself stays in the history and in
  the day's notes; it is only no longer the remark the Remarks cell (and the Excel's Remarks column) shows. A remark written afterwards is a
  new row and shows as before.

Plain, idempotent (ADD COLUMN IF NOT EXISTS), additive and safe on an empty database or while older code shares the database: the older code
does not know the columns and keeps working (it would still show a deleted note, so apply this before the matching release).
The downgrade removes the deleted notes for good (their text is in the audit entries written when they were deleted; a note whose delete
had no entry is kept), then drops the columns; cleared remarks simply show again.
"""
from alembic import op

revision = "0053_remark_soft_delete"
down_revision = "0052_speed_indexes_tasks"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE workflow_task_remarks ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE workflow_task_remarks ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(36) REFERENCES auth_users (id) ON DELETE SET NULL")
    op.execute("ALTER TABLE workflow_task_remarks ADD COLUMN IF NOT EXISTS cleared_at TIMESTAMP WITH TIME ZONE")
    op.execute("COMMENT ON COLUMN workflow_task_remarks.deleted_at IS 'Set when someone deleted this note (soft delete): every screen, report and export ignores it; "
               "the audit log keeps its text.'")
    op.execute("COMMENT ON COLUMN workflow_task_remarks.deleted_by IS 'Who deleted this note.'")
    op.execute("COMMENT ON COLUMN workflow_task_remarks.cleared_at IS 'Set when the Tasks page''s Select remark cleared the candidate''s remark cell: the note stays in the "
               "history and the day''s notes, but is no longer the shown remark.'")


def downgrade():
    op.execute("""DELETE FROM workflow_task_remarks r WHERE r.deleted_at IS NOT NULL AND EXISTS (
        SELECT 1 FROM system_audit_log a WHERE a.action = 'Deleted a note' AND a.meta ->> 'remarkId' = r.id)""")
    op.execute("ALTER TABLE workflow_task_remarks DROP COLUMN IF EXISTS cleared_at")
    op.execute("ALTER TABLE workflow_task_remarks DROP COLUMN IF EXISTS deleted_by")
    op.execute("ALTER TABLE workflow_task_remarks DROP COLUMN IF EXISTS deleted_at")
