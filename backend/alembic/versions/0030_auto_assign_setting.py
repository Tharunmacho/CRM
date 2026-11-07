"""Agency setting: automatic recruiter assignment of new candidates (off by default).

Additive only: one new NOT NULL boolean column on agency_settings with a default of false, so the existing
row and every reader keep working. New candidates are no longer handed to a recruiter automatically;
an administrator can switch the old round-robin back on in Agency settings. The statement is idempotent
(IF NOT EXISTS). The downgrade drops only this column.
"""
from alembic import op

revision = "0030_auto_assign_setting"
down_revision = "0029_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE agency_settings ADD COLUMN IF NOT EXISTS auto_assign BOOLEAN NOT NULL DEFAULT false")
    op.execute("COMMENT ON COLUMN agency_settings.auto_assign IS "
               "'When true, new candidates are given to the next free recruiter (round-robin); when false they wait for a manual assignment.'")


def downgrade():
    op.execute("ALTER TABLE agency_settings DROP COLUMN IF EXISTS auto_assign")
