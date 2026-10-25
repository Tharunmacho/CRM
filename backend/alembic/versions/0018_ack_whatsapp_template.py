"""Optional approved WhatsApp template for automatic acknowledgements.

Additive only: one new nullable JSONB column on intake_acknowledgement_settings. No existing column, row or
constraint changes, and rows keep working untouched (NULL means "send the saved plain text", as before).
The statement is idempotent (IF NOT EXISTS), so running it twice or after a partial run is safe.

The application reads and writes the column defensively: until this migration is applied, automatic replies keep
sending plain text and the settings screen simply cannot choose a template.

The downgrade drops only this column; a chosen template is lost, nothing else is touched.
"""
from alembic import op

revision = "0018_ack_whatsapp_template"
down_revision = "0017_agency_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE intake_acknowledgement_settings ADD COLUMN IF NOT EXISTS whatsapp_template JSONB")
    op.execute("COMMENT ON COLUMN intake_acknowledgement_settings.whatsapp_template IS "
               "'Approved WhatsApp template {name, language, parameters} sent instead of plain text; NULL sends the saved text.'")


def downgrade():
    op.execute("ALTER TABLE intake_acknowledgement_settings DROP COLUMN IF EXISTS whatsapp_template")
