"""Preserve exact reviewed candidate wording beside typed matching values."""
from alembic import op


revision = "0038_candidate_source_claims"
down_revision = "0037_order_requirements"
branch_labels = None
depends_on = None


def upgrade():
    # Additive and repeatable: no existing numeric values or OCR evidence are rewritten.
    op.execute("ALTER TABLE crm_candidates ADD COLUMN IF NOT EXISTS source_claims JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("COMMENT ON COLUMN crm_candidates.source_claims IS 'Exact reviewed source wording that cannot safely be represented by typed candidate fields; bounded allowlisted keys only.'")


def downgrade():
    op.execute("ALTER TABLE crm_candidates DROP COLUMN IF EXISTS source_claims")
