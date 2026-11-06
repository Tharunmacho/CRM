"""Lead sheet: platform lead exports (Meta Lead Ads, Google forms, spreadsheets), de-duplicated, with a calling workflow.

Three new tables, nothing existing is altered:

* `crm_lead_batches`   one row per uploaded file (format, encoding, column mapping, totals, ignored rows with reasons)
* `crm_leads`          one row per person; unique index on `external_id` (the platform lead id); `phone_key` (last 10
                       digits) and `email_key` back the duplicate rules; calling workflow columns; `extra` and `raw`
                       keep every column the file had
* `crm_lead_activities` calls, notes, status changes, assignments and conversion per lead

Additive and idempotent (IF NOT EXISTS), so it is safe on a database where it ran partly and when run twice.
The downgrade drops the three tables and with them all imported leads: it refuses while any lead exists unless
`CRM_ALLOW_LEAD_DOWNGRADE=1` is set, so a rollback cannot erase uploaded lead data by accident.
"""
import os

from alembic import op

revision = "0024_lead_sheet"
down_revision = "0023_daily_work_call_items"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS crm_lead_batches (
        id VARCHAR(36) NOT NULL,
        file_name VARCHAR(255) NOT NULL,
        file_size INTEGER DEFAULT 0 NOT NULL,
        file_sha256 VARCHAR(64) NOT NULL,
        source VARCHAR(20) DEFAULT 'meta_ads' NOT NULL,
        file_format VARCHAR(10) NOT NULL,
        encoding VARCHAR(20),
        delimiter VARCHAR(4),
        uploaded_by VARCHAR(36),
        assigned_to VARCHAR(36),
        campaign_label VARCHAR(255),
        fill_empty BOOLEAN DEFAULT false NOT NULL,
        status VARCHAR(12) DEFAULT 'completed' NOT NULL,
        rows_total INTEGER DEFAULT 0 NOT NULL,
        rows_new INTEGER DEFAULT 0 NOT NULL,
        rows_duplicate INTEGER DEFAULT 0 NOT NULL,
        rows_invalid INTEGER DEFAULT 0 NOT NULL,
        rows_matched_candidates INTEGER DEFAULT 0 NOT NULL,
        rows_updated INTEGER DEFAULT 0 NOT NULL,
        column_map JSONB DEFAULT '{}'::jsonb NOT NULL,
        report JSONB DEFAULT '{}'::jsonb NOT NULL,
        notes TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_lead_batch_status CHECK (status IN ('completed','reverted')),
        CONSTRAINT ck_lead_batch_source CHECK (source IN ('meta_ads','other')),
        FOREIGN KEY (uploaded_by) REFERENCES auth_users (id) ON DELETE SET NULL,
        FOREIGN KEY (assigned_to) REFERENCES auth_users (id) ON DELETE SET NULL
    )""")
    op.execute("COMMENT ON TABLE crm_lead_batches IS 'One row per uploaded lead sheet (CSV, TSV or XLSX) with detected format, column mapping and import totals.'")
    op.execute("COMMENT ON COLUMN crm_lead_batches.file_sha256 IS 'Hash of the uploaded bytes; warns when the very same file is uploaded again.'")
    op.execute("COMMENT ON COLUMN crm_lead_batches.file_format IS 'csv, tsv or xlsx, detected from the content.'")
    op.execute("COMMENT ON COLUMN crm_lead_batches.assigned_to IS 'assign_to chosen at upload; null when left unassigned or spread automatically.'")
    op.execute("COMMENT ON COLUMN crm_lead_batches.column_map IS 'File header to lead field (null = kept in extra).'")
    op.execute("COMMENT ON COLUMN crm_lead_batches.report IS 'Ignored rows with reasons: {duplicates: [...], invalid: [...]} (capped).'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_lead_batches_file_sha256 ON crm_lead_batches (file_sha256)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_lead_batches_created ON crm_lead_batches (created_at)")

    op.execute("""CREATE TABLE IF NOT EXISTS crm_leads (
        id VARCHAR(36) NOT NULL,
        batch_id VARCHAR(36),
        import_row INTEGER,
        external_id VARCHAR(64),
        phone_key VARCHAR(20),
        email_key VARCHAR(255),
        created_time TIMESTAMP WITH TIME ZONE,
        name VARCHAR(200),
        phone VARCHAR(40),
        phone_country_code VARCHAR(6),
        email VARCHAR(255),
        whatsapp VARCHAR(40),
        whatsapp_same BOOLEAN,
        city VARCHAR(120),
        state VARCHAR(120),
        current_location VARCHAR(120),
        position_applied VARCHAR(200),
        country_interest VARCHAR(120),
        experience_restaurant_years NUMERIC(5, 1),
        experience_restaurant_text VARCHAR(200),
        experience_total_years NUMERIC(5, 1),
        experience_total_text VARCHAR(200),
        gender VARCHAR(20),
        marital_status VARCHAR(120),
        dob DATE,
        education_level VARCHAR(200),
        military_status VARCHAR(200),
        platform VARCHAR(30),
        is_organic BOOLEAN,
        lead_status_source VARCHAR(60),
        campaign_id VARCHAR(64),
        campaign_name VARCHAR(255),
        adset_id VARCHAR(64),
        adset_name VARCHAR(255),
        ad_id VARCHAR(64),
        ad_name VARCHAR(255),
        form_id VARCHAR(64),
        form_name VARCHAR(255),
        extra JSONB DEFAULT '{}'::jsonb NOT NULL,
        raw JSONB DEFAULT '{}'::jsonb NOT NULL,
        status VARCHAR(20) DEFAULT 'new' NOT NULL,
        owner_id VARCHAR(36),
        assigned_at TIMESTAMP WITH TIME ZONE,
        last_contacted_at TIMESTAMP WITH TIME ZONE,
        next_follow_up_at TIMESTAMP WITH TIME ZONE,
        call_count INTEGER DEFAULT 0 NOT NULL,
        notes TEXT,
        resume_received_at TIMESTAMP WITH TIME ZONE,
        candidate_id VARCHAR(36),
        matched_candidate_id VARCHAR(36),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_crm_leads_status CHECK (status IN ('new','contacted','interested','resume_requested','resume_received','converted','not_interested','invalid','wrong_number')),
        FOREIGN KEY (batch_id) REFERENCES crm_lead_batches (id) ON DELETE SET NULL,
        FOREIGN KEY (owner_id) REFERENCES auth_users (id) ON DELETE SET NULL,
        FOREIGN KEY (candidate_id) REFERENCES crm_candidates (id) ON DELETE SET NULL,
        FOREIGN KEY (matched_candidate_id) REFERENCES crm_candidates (id) ON DELETE SET NULL
    )""")
    op.execute("COMMENT ON TABLE crm_leads IS 'People who answered an ad (platform lead exports), stored once each, with the calling workflow: owner, status, follow-up and conversion.'")
    for column, text_ in (
        ("batch_id", "Upload that first brought this person in."),
        ("external_id", "Platform lead id without its l: prefix."),
        ("phone_key", "Last 10 digits of the phone (all digits when fewer)."),
        ("created_time", "When the person answered the ad, as in the file."),
        ("lead_status_source", "lead_status column of the platform file."),
        ("extra", "Columns of the file that have no lead field (different ad forms ask different questions)."),
        ("raw", "The whole original row, header to text."),
        ("candidate_id", "Data Bank candidate this lead was converted to or linked with."),
        ("matched_candidate_id", "Candidate with the same phone or e-mail found at import time (flag only, not a link)."),
    ):
        op.execute(f"COMMENT ON COLUMN crm_leads.{column} IS '{text_}'")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_leads_external_id ON crm_leads (external_id) WHERE external_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_phone_key ON crm_leads (phone_key) WHERE phone_key IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_email_key ON crm_leads (email_key) WHERE email_key IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_owner_status ON crm_leads (owner_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_campaign ON crm_leads (campaign_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_created_time ON crm_leads (created_time DESC NULLS LAST)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_batch ON crm_leads (batch_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_leads_follow_up ON crm_leads (next_follow_up_at) WHERE next_follow_up_at IS NOT NULL")

    op.execute("""CREATE TABLE IF NOT EXISTS crm_lead_activities (
        id VARCHAR(36) NOT NULL,
        lead_id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36),
        kind VARCHAR(12) NOT NULL,
        summary TEXT DEFAULT '' NOT NULL,
        meta JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_crm_lead_activity_kind CHECK (kind IN ('call','note','status','assign','import','convert','resume','edit')),
        FOREIGN KEY (lead_id) REFERENCES crm_leads (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES auth_users (id) ON DELETE SET NULL
    )""")
    op.execute("COMMENT ON TABLE crm_lead_activities IS 'History of calls, notes, status changes, assignments and conversion for each lead.'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_lead_activities_lead ON crm_lead_activities (lead_id, created_at DESC)")


def downgrade():
    if os.environ.get("CRM_ALLOW_LEAD_DOWNGRADE") != "1":
        bind = op.get_bind()
        exists = bind.exec_driver_sql("SELECT to_regclass('crm_leads') IS NOT NULL").scalar()
        count = bind.exec_driver_sql("SELECT count(*) FROM crm_leads").scalar() if exists else 0
        if count:
            raise RuntimeError(
                f"Cannot downgrade 0024: {count} lead(s) are stored and dropping the tables would erase them. "
                "Export them first, then set CRM_ALLOW_LEAD_DOWNGRADE=1 and downgrade again.")
    op.execute("DROP TABLE IF EXISTS crm_lead_activities")
    op.execute("DROP TABLE IF EXISTS crm_leads")
    op.execute("DROP TABLE IF EXISTS crm_lead_batches")
