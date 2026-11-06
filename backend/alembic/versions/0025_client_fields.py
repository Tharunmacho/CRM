"""Client form: the contact person's designation and the company's website.

The single "New client" form now collects every client and company field, so two columns that had no home are added:

* `crm_client_contacts.designation`  VARCHAR(120) NULL  the contact person's job title (for example "HR Manager")
* `crm_employer_companies.website`   VARCHAR(255) NULL  the company's web address, saved by the API as https://...

Additive and idempotent (ADD COLUMN IF NOT EXISTS), nullable with no default, so existing rows are untouched and it is
safe on a database where it ran partly and when run twice. The downgrade drops the two columns (IF EXISTS), which
discards the designations and websites typed since; nothing else depends on them.
"""
from alembic import op

revision = "0025_client_fields"
down_revision = "0024_lead_sheet"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_client_contacts ADD COLUMN IF NOT EXISTS designation VARCHAR(120)")
    op.execute("ALTER TABLE crm_employer_companies ADD COLUMN IF NOT EXISTS website VARCHAR(255)")
    op.execute("COMMENT ON COLUMN crm_client_contacts.designation IS 'Job title of the client contact person, for example HR Manager.'")
    op.execute("COMMENT ON COLUMN crm_employer_companies.website IS 'Company web address, saved as an http(s) URL; shown as a link on the client page.'")


def downgrade():
    op.execute("ALTER TABLE crm_employer_companies DROP COLUMN IF EXISTS website")
    op.execute("ALTER TABLE crm_client_contacts DROP COLUMN IF EXISTS designation")
