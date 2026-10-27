"""A managed job category list for job titles, replacing free typing with a list the agency controls.

Additive only: one new table, no change to any existing table, column or enum. `crm_job_designations.category` stays
free text (a job title keeps whatever category it already has, such as one typed before this list existed); the list
only feeds the suggestions and the renaming tool. Every statement is idempotent (IF NOT EXISTS / ON CONFLICT DO
NOTHING), so running it twice, or after a partial run, is safe. Names are unique ignoring case.

The downgrade drops only this table; edits made in it are lost, no job title is touched.
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0019_job_categories"
down_revision = "0018_ack_whatsapp_template"
branch_labels = None
depends_on = None

# Typical overseas-recruitment fields for Gulf employers, in the order they are suggested.
CATEGORIES = [
    "Construction", "Skilled trades", "Electrical and plumbing", "Mechanical and maintenance",
    "Welding and fabrication", "Hospitality and catering", "Housekeeping and cleaning", "Healthcare and nursing",
    "Driving and logistics", "Security", "Oil and gas", "Manufacturing and factory", "Retail and sales",
    "Office and administration", "Domestic and household", "Agriculture and farming", "General labour",
]


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS job_categories (
        id VARCHAR(36) NOT NULL,
        name VARCHAR(120) NOT NULL,
        is_active BOOLEAN DEFAULT true NOT NULL,
        sort_order INTEGER DEFAULT 0 NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_job_categories_name CHECK (char_length(btrim(name)) BETWEEN 2 AND 120)
    )""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_job_categories_name_lower ON job_categories (lower(name))")
    op.execute("COMMENT ON TABLE job_categories IS 'Admin-managed job category list for job titles; names are unique ignoring case and inactive ones stay valid on saved job titles.'")
    bind = op.get_bind()
    for index, name in enumerate(CATEGORIES, start=1):
        bind.execute(sa.text("INSERT INTO job_categories (id, name, sort_order) VALUES (:id, :name, :order) ON CONFLICT DO NOTHING"),
                     {"id": str(uuid.uuid4()), "name": name, "order": index * 10})


def downgrade():
    op.execute("DROP TABLE IF EXISTS job_categories")
