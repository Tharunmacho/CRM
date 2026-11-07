"""Reading queue leases: several workers claim documents one by one and read them side by side.

* `document_extraction_jobs`  queued_at, started_at, finished_at (queue timeline), claimed_by, lease_until (lease)
* `email_ingestion_jobs`      claimed_by, lease_until
* `document_person_intakes`   claimed_by, lease_until

All columns are nullable and default to NULL, so existing rows are untouched: a job that was `processing` when this
runs has no lease and is simply claimed again by the next worker (exactly what a crashed worker needed before).
Additive and idempotent (IF NOT EXISTS), safe to run twice or on a database where it ran partly. The downgrade drops the
columns (IF EXISTS), which only discards queue timestamps and leases.
"""
from alembic import op

revision = "0028_extraction_leases"
down_revision = "0027_application_form_v2"
branch_labels = None
depends_on = None

COLUMNS = {
    "document_extraction_jobs": [
        ("queued_at", "TIMESTAMP WITH TIME ZONE", "When reading was last requested (entered the queue)."),
        ("started_at", "TIMESTAMP WITH TIME ZONE", "When a worker last began reading this document."),
        ("finished_at", "TIMESTAMP WITH TIME ZONE", "When reading last reached completed, needs_review or failed."),
        ("claimed_by", "VARCHAR(80)", "Worker holding the reading lease of this job."),
        ("lease_until", "TIMESTAMP WITH TIME ZONE", "When the reading lease lapses and another worker may claim the job."),
    ],
    "email_ingestion_jobs": [
        ("claimed_by", "VARCHAR(80)", "Worker holding the reading lease of this job."),
        ("lease_until", "TIMESTAMP WITH TIME ZONE", "When the reading lease lapses and another worker may claim the job."),
    ],
    "document_person_intakes": [
        ("claimed_by", "VARCHAR(80)", "Worker preparing the pages of this review."),
        ("lease_until", "TIMESTAMP WITH TIME ZONE", "When the preparation lease lapses and another worker may claim the review."),
    ],
}


def upgrade():
    for table, columns in COLUMNS.items():
        for name, kind, comment in columns:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {kind}")
            op.execute(f"COMMENT ON COLUMN {table}.{name} IS '{comment}'")
    # Finding lapsed leases must not scan finished jobs.
    op.execute("CREATE INDEX IF NOT EXISTS ix_document_extraction_jobs_lease ON document_extraction_jobs (lease_until) WHERE status = 'processing'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_ingestion_jobs_lease ON email_ingestion_jobs (lease_until) WHERE status = 'processing'")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_email_ingestion_jobs_lease")
    op.execute("DROP INDEX IF EXISTS ix_document_extraction_jobs_lease")
    for table, columns in COLUMNS.items():
        for name, _, _ in columns:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {name}")
