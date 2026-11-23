"""crm_applications.closed_at: when a placement was closed.

Reports and the Declined / Rejected lists dated a closure by `updated_at`, which any bulk edit or import touches, so an old closure could
read as "today". Every closure now stamps `closed_at` (and a reopening clears it). Existing closed rows are backfilled with the completion
time of their closing stage record, else their last update (the best the old data allows). Downgrade drops the column.
"""
import sqlalchemy as sa
from alembic import op

revision = "0060_application_closed_at"
down_revision = "0059_whatsapp_ignored_numbers"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: a database built from the models, or an interrupted run, already has the column.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("crm_applications")}
    if "closed_at" not in columns:
        op.add_column("crm_applications", sa.Column("closed_at", sa.DateTime(timezone=True),
                                                    comment="When the placement was closed; cleared on reopening."))
    op.execute("""
        UPDATE crm_applications a
           SET closed_at = COALESCE(
                 (SELECT max(s.completed_at) FROM crm_application_stages s
                   WHERE s.application_id = a.id AND s.stage = a.current_stage
                     AND (s.reopened_reason IS NULL OR s.reopened_reason = '')),
                 a.updated_at)
         WHERE a.status = 'closed' AND a.closed_at IS NULL
    """)


def downgrade():
    op.execute("ALTER TABLE crm_applications DROP COLUMN IF EXISTS closed_at")
