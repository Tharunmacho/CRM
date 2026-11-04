"""Admin-managed option lists for the candidate / application form (service charges, interest level, ...).

Additive: one new table, `crm_form_options`, seeded with the lists the screens used to hardcode. The only change to an
existing table is that the two CHECK constraints 0021 put on `crm_candidates` (service_charges_status, interest_level) are
dropped: the allowed values are now rows an administrator can edit, and the application checks new values against the
active ones. Values already stored are untouched. Every statement is idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING).

The downgrade drops the table (edits made in it are lost) and puts the two checks back as NOT VALID, so rows that already
hold a value an administrator added are left alone but new writes are limited to the original values again.
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0022_form_options"
down_revision = "0021_application_form"
branch_labels = None
depends_on = None

# (category, value, label, hint, tone) in display order. Kept in step with app.services.form_options.DEFAULT_OPTIONS by a test.
SEEDS = [
    ("service_charges_status", [
        ("not_discussed", "Not discussed", "Service charges have not been talked about yet", "neutral"),
        ("agreed", "Agreed", "The candidate agreed to the service charges", "success"),
        ("pending", "Pending", "The candidate is thinking it over", "warning"),
        ("declined", "Declined", "The candidate does not accept the service charges", "danger")]),
    ("interest_level", [
        ("more_interested", "More interested", "Keen to go abroad now", "success"),
        ("interested", "Interested", "Wants to go and is open to offers", "info"),
        ("somewhat_interested", "Somewhat interested", "May go if the offer is right", "warning"),
        ("not_interested", "Not interested", "Does not want to go abroad", "danger")]),
    ("candidate_source", [
        ("email", "Email", None, None), ("whatsapp", "WhatsApp", None, None), ("walk_in", "Walk-in", None, None),
        ("referral", "Referral / agent", None, None), ("other", "Other", None, None)]),
    ("job_duration_preference", [
        ("1 year", "1 year", None, None), ("2 years", "2 years", None, None), ("3 years", "3 years", None, None),
        ("4 years", "4 years", None, None), ("5 years", "5 years", None, None), ("Any", "Any duration", None, None)]),
    ("best_time_to_contact", [
        ("Morning", "Morning", "9 am to 12 pm", None), ("Afternoon", "Afternoon", "12 pm to 4 pm", None),
        ("Evening", "Evening", "4 pm to 8 pm", None), ("Anytime", "Anytime", None, None)]),
    ("marital_status", [(v, v, None, None) for v in ("Single", "Married", "Divorced", "Widowed", "Separated")]),
    ("gender", [("male", "Male", None, None), ("female", "Female", None, None), ("other", "Other", None, None)]),
    ("education_level", [(v, v, None, None) for v in (
        "10th / SSLC", "12th / HSC", "ITI", "Diploma", "B.E / B.Tech", "B.Sc", "B.Com", "BA", "BBA", "BCA",
        "M.E / M.Tech", "M.Sc", "M.Com", "MBA", "MCA", "Nursing", "Other")]),
    ("language", [(v, v, None, None) for v in (
        "English", "Hindi", "Malayalam", "Tamil", "Telugu", "Kannada", "Arabic", "Urdu", "Bengali", "Marathi", "Gujarati",
        "Punjabi", "Nepali", "Sinhala")]),
]

CHECKS = (
    ("ck_candidates_service_charges", "service_charges_status IN ('not_discussed','agreed','pending','declined')"),
    ("ck_candidates_interest_level", "interest_level IS NULL OR interest_level IN ('more_interested','interested','somewhat_interested','not_interested')"),
)


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS crm_form_options (
        id VARCHAR(36) NOT NULL,
        category VARCHAR(40) NOT NULL,
        value VARCHAR(60) NOT NULL,
        label VARCHAR(120) NOT NULL,
        hint VARCHAR(255),
        tone VARCHAR(20),
        sort_order INTEGER DEFAULT 0 NOT NULL,
        is_active BOOLEAN DEFAULT true NOT NULL,
        is_system BOOLEAN DEFAULT false NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_form_options_category_value UNIQUE (category, value),
        CONSTRAINT ck_form_options_tone CHECK (tone IS NULL OR tone IN ('neutral','success','info','warning','danger'))
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_form_options_category ON crm_form_options (category, sort_order)")
    op.execute("COMMENT ON TABLE crm_form_options IS 'Admin-managed option lists for the candidate and application form; a hidden option stays valid on candidates that already have it.'")
    op.execute("COMMENT ON COLUMN crm_form_options.value IS 'What is stored on the candidate; never changed after creation.'")
    op.execute("COMMENT ON COLUMN crm_form_options.tone IS 'neutral, success, info, warning or danger: the colour a screen gives the choice.'")
    op.execute("COMMENT ON COLUMN crm_form_options.is_system IS 'Seeded by the product; can be hidden and relabelled but not deleted.'")

    bind = op.get_bind()
    insert = sa.text(
        "INSERT INTO crm_form_options (id, category, value, label, hint, tone, sort_order, is_system) "
        "VALUES (:id, :category, :value, :label, :hint, :tone, :sort_order, true) ON CONFLICT (category, value) DO NOTHING")
    for category, rows in SEEDS:
        for position, (value, label, hint, tone) in enumerate(rows, start=1):
            bind.execute(insert, {"id": str(uuid.uuid4()), "category": category, "value": value, "label": label,
                                  "hint": hint, "tone": tone, "sort_order": position * 10})

    for name, _ in CHECKS:
        op.execute(f"ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS {name}")


def downgrade():
    for name, rule in CHECKS:
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}' AND conrelid = 'crm_candidates'::regclass) THEN
                ALTER TABLE crm_candidates ADD CONSTRAINT {name} CHECK ({rule}) NOT VALID;
            END IF;
        END $$""")
    op.execute("DROP TABLE IF EXISTS crm_form_options")
