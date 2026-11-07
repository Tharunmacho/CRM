"""Keep this installation's queued documents out of sight of older readers that share the database.

A reader that predates the instance stamp claims any job with ``status IN ('pending','processing','retry') AND
next_attempt_at <= now()``. It cannot be told to skip a job, but it can be given nothing to find: for a job stamped by an
installation (``instance`` not null) a trigger keeps the real due time in the new ``ready_at`` column and stores a far-future
timestamp (2100-01-01, a concrete value every driver reads) in ``next_attempt_at``. Current code claims on
``COALESCE(ready_at, next_attempt_at)``. Rows with no instance (written by older code) are never touched: they keep
``ready_at`` NULL and their own ``next_attempt_at``.

Additive: one nullable column and one trigger on document_extraction_jobs. Nothing is backfilled. The downgrade removes
the trigger and the column (stamped rows then keep the far-future ``next_attempt_at``: the downgrade sets it back from
``ready_at`` first).
"""
from alembic import op

revision = "0032_job_ready_at"
down_revision = "0031_job_instance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE document_extraction_jobs ADD COLUMN IF NOT EXISTS ready_at TIMESTAMPTZ")
    op.execute("COMMENT ON COLUMN document_extraction_jobs.ready_at IS "
               "'When the job is really due, for rows stamped with an instance; next_attempt_at then holds 2100-01-01 so older readers never see them.'")
    op.execute("""
        CREATE OR REPLACE FUNCTION crm_hide_job_from_old_readers() RETURNS trigger AS $$
        BEGIN
            IF NEW.instance IS NOT NULL AND NEW.next_attempt_at < TIMESTAMPTZ '2100-01-01 00:00:00+00' THEN
                NEW.ready_at := NEW.next_attempt_at;
                NEW.next_attempt_at := TIMESTAMPTZ '2100-01-01 00:00:00+00';
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS crm_hide_job_from_old_readers ON document_extraction_jobs")
    op.execute("""
        CREATE TRIGGER crm_hide_job_from_old_readers BEFORE INSERT OR UPDATE ON document_extraction_jobs
        FOR EACH ROW EXECUTE FUNCTION crm_hide_job_from_old_readers()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS crm_hide_job_from_old_readers ON document_extraction_jobs")
    op.execute("DROP FUNCTION IF EXISTS crm_hide_job_from_old_readers()")
    op.execute("UPDATE document_extraction_jobs SET next_attempt_at = ready_at WHERE ready_at IS NOT NULL "
               "AND next_attempt_at >= TIMESTAMPTZ '2100-01-01 00:00:00+00'")
    op.execute("ALTER TABLE document_extraction_jobs DROP COLUMN IF EXISTS ready_at")
