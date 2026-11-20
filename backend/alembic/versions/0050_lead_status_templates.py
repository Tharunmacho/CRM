"""Lead sheet status templates: three new built-in statuses and a shared list the team adds to.

Two additive changes, nothing existing is altered, moved or deleted:

1. ``crm_leads.status_label`` (VARCHAR(40), nullable): the name of the status template a recruiter picked (Ringing, a custom status ...).
   ``crm_leads.status`` is NOT touched: it keeps holding one of the nine built-in codes (the CHECK constraint, the indexes, every report,
   filter and dashboard count stay exactly as they were). A lead with a template has the code the template counts as (see
   ``app/services/lead_status_templates.py``: tone neutral -> contacted, positive -> interested, warning -> contacted (an attempt that
   did not connect), negative -> not_interested) plus the template's name here. NULL = the built-in status as it is, so every
   existing lead is unchanged.
2. ``crm_lead_status_templates`` (id, name, tone, active, builtin, sort_order, created_by, created_at, updated_at): the choices added to the
   built-in statuses, shared by every user. ``name`` is unique ignoring case, 1 to 40 characters, no comma. Seeded with the three new
   built-in statuses: Ringing (warning), No incoming calls (warning), Not eligible (negative); built-in rows are never deleted.

Idempotent (ADD COLUMN IF NOT EXISTS, CREATE TABLE / INDEX IF NOT EXISTS, INSERT .. ON CONFLICT DO NOTHING): running it twice, or after a
partial run, is harmless, and a seeded row an administrator already switched off is left as it is. Safe on an empty database.

The downgrade is safe for the data: a lead that holds a template keeps its built-in status code (Ringing and No incoming calls read
"Contacted" again, Not eligible reads "Not interested", a custom template reads as the status it counted as), and before the label is
dropped a history entry on the lead records the name it had. Then the table and the column are dropped (custom templates are lost).
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0050_lead_status_templates"
down_revision = "0049_purge_user_tombstones"
branch_labels = None
depends_on = None

# (name, tone, sort_order): the built-in templates, in the order the dropdown shows them after the original statuses.
BUILTIN = [("Ringing", "warning", 10), ("No incoming calls", "warning", 20), ("Not eligible", "negative", 30)]


def upgrade():
    op.execute("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS status_label VARCHAR(40)")
    op.execute("COMMENT ON COLUMN crm_leads.status_label IS 'Name of the status template the recruiter picked (Ringing, a custom status ...); "
               "NULL = the built-in status as it is. status holds the code the template counts as.'")
    op.execute("""CREATE TABLE IF NOT EXISTS crm_lead_status_templates (
        id VARCHAR(36) NOT NULL,
        name VARCHAR(40) NOT NULL,
        tone VARCHAR(10) DEFAULT 'neutral' NOT NULL,
        active BOOLEAN DEFAULT true NOT NULL,
        builtin BOOLEAN DEFAULT false NOT NULL,
        sort_order INTEGER DEFAULT 0 NOT NULL,
        created_by VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY (created_by) REFERENCES auth_users (id) ON DELETE SET NULL,
        CONSTRAINT ck_crm_lead_status_templates_name CHECK (char_length(btrim(name)) BETWEEN 1 AND 40 AND position(',' in name) = 0),
        CONSTRAINT ck_crm_lead_status_templates_tone CHECK (tone IN ('neutral','positive','warning','negative'))
    )""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_lead_status_templates_name_lower ON crm_lead_status_templates (lower(name))")
    op.execute("COMMENT ON TABLE crm_lead_status_templates IS 'Shared Lead sheet status choices added to the built-in ones; "
               "a lead keeps the template''s name when the template is hidden or deleted.'")
    bind = op.get_bind()
    for name, tone, order in BUILTIN:
        bind.execute(sa.text("INSERT INTO crm_lead_status_templates (id, name, tone, builtin, sort_order) VALUES (:id, :name, :tone, true, :order) "
                             "ON CONFLICT DO NOTHING"), {"id": str(uuid.uuid4()), "name": name, "tone": tone, "order": order})


def downgrade():
    # Say on each lead what the label was (its built-in status stays and still counts the same), then drop what 0050 added.
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'crm_leads' AND column_name = 'status_label') THEN
        INSERT INTO crm_lead_activities (id, lead_id, user_id, kind, summary, meta)
        SELECT gen_random_uuid()::text, l.id, NULL, 'status',
               'Status "' || l.status_label || '" no longer exists; the lead stays at its built-in status.',
               jsonb_build_object('from', l.status, 'to', l.status, 'fromLabel', l.status_label, 'downgrade', true)
        FROM crm_leads l WHERE l.status_label IS NOT NULL;
      END IF;
    END $$""")
    op.execute("DROP TABLE IF EXISTS crm_lead_status_templates")
    op.execute("ALTER TABLE crm_leads DROP COLUMN IF EXISTS status_label")
