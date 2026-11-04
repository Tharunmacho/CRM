"""Application-form details on the candidate: father's name, marital status, address, education, work history.

Additive only: fifteen new columns on `crm_candidates`, no table, enum or existing column is touched. Every statement is
idempotent (ADD COLUMN IF NOT EXISTS), so a repeat or a partial earlier run is safe. Existing rows get the defaults
(NULL / 0 / empty list / empty object) and are filled later by reading the stored OCR output
(`python -m app.cli backfill-application-forms`) or by the next document read; nothing is rewritten here.

* `father_name`, `marital_status`, `current_address`: the personal-details rows of the printed form.
* `experience_india`: whole years of experience in India (the form's "Experience in India"; "Experience Abroad" is the
  existing `gulf_experience`, "Total Experience" the existing `total_experience`).
* `education`: JSONB list of {level, passedYear, institute}.
* `work_history`: JSONB list of {company, designation, country, period}.
* Screening details from the telemarketer's intake form: `native_place`, `family_member_number`, `job_duration_preference`,
  `country_preference`, `best_time_to_contact`, `service_charges_status` (not_discussed / agreed / pending / declined,
  default not_discussed), `interest_level` (more_interested / interested / somewhat_interested / not_interested; the finer
  grading beside the call outcome in `call_interested`), `next_follow_up_date`. The rest of that form reuses existing columns.
* `field_sources`: JSONB map of form field -> "extracted" | "manual". A document read only fills empty fields or ones
  marked "extracted", so a value a person typed is never overwritten.

The downgrade drops only these fifteen columns, their checks and one index.
"""
from alembic import op

revision = "0021_application_form"
down_revision = "0020_daily_work_quota"
branch_labels = None
depends_on = None

COLUMNS = (
    ("father_name", "VARCHAR(200)", "Father's name for the application form."),
    ("marital_status", "VARCHAR(30)", "Marital status for the application form."),
    ("current_address", "TEXT", "Current postal address for the application form."),
    ("experience_india", "INTEGER DEFAULT 0 NOT NULL", "Whole years of experience in India; experience abroad is gulf_experience."),
    ("education", "JSONB DEFAULT '[]'::jsonb", "Education rows [{level, passedYear, institute}] read from the resume or typed in the form."),
    ("work_history", "JSONB DEFAULT '[]'::jsonb", "Previous employment rows [{company, designation, country, period}] read from the resume or typed in the form."),
    ("native_place", "VARCHAR(120)", "Native place (screening details)."),
    ("family_member_number", "VARCHAR(40)", "Phone number of a family member to reach the candidate through."),
    ("job_duration_preference", "VARCHAR(60)", "How long a contract the candidate wants, as text (for example 2 years)."),
    ("country_preference", "VARCHAR(200)", "Countries the candidate wants to work in, as text."),
    ("best_time_to_contact", "VARCHAR(60)", "When the candidate prefers to be called."),
    ("service_charges_status", "VARCHAR(20) DEFAULT 'not_discussed' NOT NULL", "Whether service charges were discussed: not_discussed, agreed, pending or declined."),
    ("interest_level", "VARCHAR(20)", "How interested the candidate is: more_interested, interested, somewhat_interested or not_interested. The call outcome stays in call_interested."),
    ("next_follow_up_date", "DATE", "Day the recruiter should follow up with the candidate next."),
    ("field_sources", "JSONB DEFAULT '{}'::jsonb", "Where each application-form value came from: extracted (document) or manual (typed). Extraction never overwrites manual."),
)


CHECKS = (
    ("ck_candidates_service_charges", "service_charges_status IN ('not_discussed','agreed','pending','declined')"),
    ("ck_candidates_interest_level", "interest_level IS NULL OR interest_level IN ('more_interested','interested','somewhat_interested','not_interested')"),
)


def upgrade():
    for name, definition, _ in COLUMNS:
        op.execute(f"ALTER TABLE crm_candidates ADD COLUMN IF NOT EXISTS {name} {definition}")
    for name, _, comment in COLUMNS:
        op.execute(f"COMMENT ON COLUMN crm_candidates.{name} IS '{comment.replace(chr(39), chr(39) * 2)}'")
    for name, rule in CHECKS:
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}' AND conrelid = 'crm_candidates'::regclass) THEN
                ALTER TABLE crm_candidates ADD CONSTRAINT {name} CHECK ({rule});
            END IF;
        END $$""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_candidates_follow_up ON crm_candidates (next_follow_up_date) WHERE next_follow_up_date IS NOT NULL")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_crm_candidates_follow_up")
    for name, _, _ in reversed(COLUMNS):
        op.execute(f"ALTER TABLE crm_candidates DROP COLUMN IF EXISTS {name}")
