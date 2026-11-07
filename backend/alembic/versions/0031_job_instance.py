"""Which installation saved a queued document (INSTANCE_NAME), so a reading worker only takes files it can read.

Additive and nullable: one new column on document_extraction_jobs, no backfill (existing rows stay unstamped and are
claimed by any installation that can read their file). The statement is idempotent (IF NOT EXISTS). The downgrade drops
only this column.
"""
from alembic import op

revision = "0031_job_instance"
down_revision = "0030_auto_assign_setting"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE document_extraction_jobs ADD COLUMN IF NOT EXISTS instance VARCHAR(80)")
    op.execute("COMMENT ON COLUMN document_extraction_jobs.instance IS "
               "'The installation (INSTANCE_NAME) that saved the file; null on rows written before this column existed.'")


def downgrade():
    op.execute("ALTER TABLE document_extraction_jobs DROP COLUMN IF EXISTS instance")
