"""Reference agencies: the sectors an agency specialises in and the countries it prefers.

* `crm_reference_agencies.specialisations`      JSONB NOT NULL DEFAULT '[]'   a list of sector names (see the API for the fixed set)
* `crm_reference_agencies.preferred_countries`  JSONB NOT NULL DEFAULT '[]'   a list of country/region names (fixed set, checked by the API)

Additive and idempotent (ADD COLUMN IF NOT EXISTS), and safe while older code still shares the database: nothing is renamed,
dropped or backfilled, both columns have a server default so an older writer's INSERT (which does not name them) still works,
and an older reader's SELECT/UPDATE of the columns it knows is unaffected. Existing agencies read back as empty lists.
The downgrade drops the two columns (IF EXISTS), which discards the selections entered since.
"""
from alembic import op

revision = "0033_agency_focus"
down_revision = "0032_job_ready_at"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_reference_agencies ADD COLUMN IF NOT EXISTS specialisations JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE crm_reference_agencies ADD COLUMN IF NOT EXISTS preferred_countries JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("COMMENT ON COLUMN crm_reference_agencies.specialisations IS "
               "'Sectors the agency specialises in: Oil and gas, Hospitality, Construction, General worker, Medical, or All sectors on its own.'")
    op.execute("COMMENT ON COLUMN crm_reference_agencies.preferred_countries IS "
               "'Countries the agency prefers to place people in: Singapore, Malaysia, Europe, Gulf, Others.'")


def downgrade():
    op.execute("ALTER TABLE crm_reference_agencies DROP COLUMN IF EXISTS preferred_countries")
    op.execute("ALTER TABLE crm_reference_agencies DROP COLUMN IF EXISTS specialisations")
