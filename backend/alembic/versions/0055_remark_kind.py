"""Notes are not remarks: ``workflow_task_remarks.kind``.

The Tasks page has two different things that were both stored as a remark: the **Remarks** dropdown (the candidate's current status line:
"Visa in progress") and the **Notes** column (free text the recruiter types, as many as they like, newest on top). A typed note must not
become the candidate's current remark, so the log gets a ``kind``:

* ``'remark'``: what the Remarks select and its "Others" box write; the Remarks cell, "Select remark" (clear), the templates and the Excel's
  Remarks column read only these.
* ``'note'``: what the NOTES column's input writes; the NOTES column, the "Note 11:40" chip and the Excel's Notes column read only these.

Every existing row stays a remark (server default ``'remark'``, no data is rewritten): the notes people wrote earlier stay in the Remarks
cell's history exactly as before. Plain, idempotent (ADD COLUMN IF NOT EXISTS, the check is added only when missing), additive and safe on an
empty database or while older code shares the database: older code never sets the column, so what it writes is a remark. The downgrade drops
the column (a note then reads as a remark again).
"""
from alembic import op

revision = "0055_remark_kind"
down_revision = "0054_soft_delete_files_fb"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE workflow_task_remarks ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'remark'")
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_workflow_task_remarks_kind' AND conrelid = 'workflow_task_remarks'::regclass) THEN
            ALTER TABLE workflow_task_remarks ADD CONSTRAINT ck_workflow_task_remarks_kind CHECK (kind IN ('remark', 'note'));
        END IF;
    END $$""")
    op.execute("COMMENT ON COLUMN workflow_task_remarks.kind IS 'remark: written by the Remarks select (the candidate''s current remark); note: typed in the NOTES column "
               "(never a remark). Existing rows are remarks.'")


def downgrade():
    op.execute("ALTER TABLE workflow_task_remarks DROP CONSTRAINT IF EXISTS ck_workflow_task_remarks_kind")
    op.execute("ALTER TABLE workflow_task_remarks DROP COLUMN IF EXISTS kind")
