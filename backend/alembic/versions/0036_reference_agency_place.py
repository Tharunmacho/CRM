"""Optional agency Place/City, independently of its full postal address.

Additive and repeatable; existing rows retain their names, contacts and address.
"""
from alembic import op

revision = "0036_reference_agency_place"
down_revision = "0035_invoice_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_reference_agencies ADD COLUMN IF NOT EXISTS place VARCHAR(200)")
    op.execute("COMMENT ON COLUMN crm_reference_agencies.place IS 'Optional place or city; never inferred as a full postal address.'")


def downgrade():
    op.execute("ALTER TABLE crm_reference_agencies DROP COLUMN IF EXISTS place")
