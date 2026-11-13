"""Recruiter edits of a lead's India and abroad experience.

Those two answers are read from the uploaded sheet's own questions (crm_leads.raw); these nullable columns hold a
recruiter's correction, which wins when set. Additive and repeatable: existing rows keep NULL and show the sheet answer.
"""
from alembic import op

revision = "0039_lead_experience_edits"
down_revision = "0038_candidate_source_claims"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS experience_india_years NUMERIC(5,1)")
    op.execute("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS experience_india_text VARCHAR(200)")
    op.execute("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS experience_abroad_years NUMERIC(5,1)")
    op.execute("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS experience_abroad_text VARCHAR(200)")
    op.execute("COMMENT ON COLUMN crm_leads.experience_india_text IS 'Recruiter-edited India experience; NULL = use the sheet answer.'")
    op.execute("COMMENT ON COLUMN crm_leads.experience_abroad_text IS 'Recruiter-edited abroad experience; NULL = use the sheet answer.'")


def downgrade():
    for column in ("experience_india_years", "experience_india_text", "experience_abroad_years", "experience_abroad_text"):
        op.execute(f"ALTER TABLE crm_leads DROP COLUMN IF EXISTS {column}")
