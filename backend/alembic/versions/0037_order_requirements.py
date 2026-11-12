"""Optional requirements specific to an employer's job order.

No defaults or backfill: existing jobs keep unspecified requirements. Shared
designation responsibilities, skills and experience remain in their original
columns. Repeating this additive migration preserves all saved values.
"""
from alembic import op

revision = "0037_order_requirements"
down_revision = "0036_reference_agency_place"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_job_orders ADD COLUMN IF NOT EXISTS age_requirement VARCHAR(120)")
    op.execute("ALTER TABLE crm_job_orders ADD COLUMN IF NOT EXISTS gender_requirement VARCHAR(120)")
    op.execute("ALTER TABLE crm_job_orders ADD COLUMN IF NOT EXISTS education_requirement VARCHAR(2000)")
    op.execute("COMMENT ON COLUMN crm_job_orders.age_requirement IS 'Optional employer-supplied age requirement; free text, not a matching gate.'")
    op.execute("COMMENT ON COLUMN crm_job_orders.gender_requirement IS 'Optional employer-supplied gender requirement; no inferred default.'")
    op.execute("COMMENT ON COLUMN crm_job_orders.education_requirement IS 'Optional employer-supplied education requirement, specific to this job order.'")


def downgrade():
    # Explicit rollback discards only the three newly captured requirements.
    op.execute("ALTER TABLE crm_job_orders DROP COLUMN IF EXISTS education_requirement")
    op.execute("ALTER TABLE crm_job_orders DROP COLUMN IF EXISTS gender_requirement")
    op.execute("ALTER TABLE crm_job_orders DROP COLUMN IF EXISTS age_requirement")
