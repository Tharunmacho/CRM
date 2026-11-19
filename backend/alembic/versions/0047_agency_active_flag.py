"""Reference agencies: an "Active agency" flag (agencies that are very active at present).

* ``crm_reference_agencies.active_agency``  BOOLEAN NOT NULL DEFAULT false

Additive and idempotent (ADD COLUMN IF NOT EXISTS), and safe while older code still shares the database: nothing is renamed,
dropped or backfilled, the column has a server default so an older writer's INSERT (which does not name it) still works,
and an older reader's SELECT/UPDATE of the columns it knows is unaffected. Every existing agency reads back as not active;
the owner marks the active ones in the CRM. The downgrade drops the column (IF EXISTS), which discards the marks entered since.
"""
from alembic import op

revision = "0047_agency_active_flag"
down_revision = "0046_doc_remark_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_reference_agencies ADD COLUMN IF NOT EXISTS active_agency BOOLEAN NOT NULL DEFAULT false")
    op.execute("COMMENT ON COLUMN crm_reference_agencies.active_agency IS "
               "'True for an agency that is very active at present; active agencies are listed first.'")


def downgrade():
    op.execute("ALTER TABLE crm_reference_agencies DROP COLUMN IF EXISTS active_agency")
