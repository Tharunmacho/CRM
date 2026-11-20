"""Speed indexes for the Tasks page and the reports.

* ``ix_crm_application_stages_completed_at``  (completed_at)
  "Stage moves between two dates": the Daily work report, the weekly staff report and the done-today marks read stage records by the
  day they were completed; without this they scan every stage record ever written.
* ``ix_workflow_tasks_completed_at``  (completed_at) WHERE completed_at IS NOT NULL
  Tasks completed in a date range (the reports' "done" counts) read this narrow index, not the open tasks and the whole history.
* ``ix_system_audit_log_entity_at``  (entity_type, entity_id, at DESC)
  The newest audit entry of one record (who closed a placement on the Tasks page's Declined / Rejected / Travelled views, a record's
  history, the checklist marks) comes straight off the index in order.
* ``ix_staff_attendance_checked_in_at``  (checked_in_at)
  The first punch of everyone and the sessions of a date range across all staff (the weekly staff report).

Plain, idempotent (IF NOT EXISTS), safe while older code shares the database: no column, constraint or query result changes.
The downgrade drops them (IF EXISTS). The models declare the same indexes (app/db/models.py).
"""
from alembic import op

revision = "0052_speed_indexes_tasks"  # (alembic_version holds 32 characters)
down_revision = "0051_speed_indexes"  # chained after 0051 so alembic heads stay single
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_application_stages_completed_at ON crm_application_stages (completed_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_tasks_completed_at ON workflow_tasks (completed_at) WHERE completed_at IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_system_audit_log_entity_at ON system_audit_log (entity_type, entity_id, at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_attendance_checked_in_at ON staff_attendance_sessions (checked_in_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_staff_attendance_checked_in_at")
    op.execute("DROP INDEX IF EXISTS ix_system_audit_log_entity_at")
    op.execute("DROP INDEX IF EXISTS ix_workflow_tasks_completed_at")
    op.execute("DROP INDEX IF EXISTS ix_crm_application_stages_completed_at")
