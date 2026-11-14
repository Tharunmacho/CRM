"""Add immutable, human-facing Care India candidate IDs.

``crm_candidates.ref`` is deliberately untouched: it is the historic CRM
reference used in integrations and messages.  ``care_id`` is a separate,
financial-year sequence intended for people to read and quote.
"""
from alembic import op


revision = "0040_care_india_candidate_ids"
down_revision = "0039_lead_experience_edits"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_candidates ADD COLUMN IF NOT EXISTS care_id VARCHAR(40)")
    op.execute("COMMENT ON COLUMN crm_candidates.care_id IS 'Immutable Care India public candidate ID: CI-YY-YY-NNN, sequenced by the April-March India financial year. Legacy ref remains unchanged.'")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_candidates_care_id ON crm_candidates (care_id) WHERE care_id IS NOT NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS crm_candidate_fiscal_counters (
            fiscal_year VARCHAR(9) PRIMARY KEY,
            last_serial BIGINT NOT NULL CONSTRAINT ck_crm_candidate_fiscal_counters_last_serial CHECK (last_serial >= 0)
        )
    """)
    op.execute("COMMENT ON TABLE crm_candidate_fiscal_counters IS 'Per-financial-year counters for immutable Care India public candidate IDs.'")

    # Existing candidates retain their CND reference and receive a deterministic
    # Care India ID from the India-local time at which they entered the CRM.
    # Ordering by id makes ties stable across reruns.
    op.execute("""
        WITH ranked AS (
            SELECT id,
                   CASE WHEN EXTRACT(MONTH FROM created_at AT TIME ZONE 'Asia/Kolkata') >= 4
                        THEN EXTRACT(YEAR FROM created_at AT TIME ZONE 'Asia/Kolkata')::integer
                        ELSE EXTRACT(YEAR FROM created_at AT TIME ZONE 'Asia/Kolkata')::integer - 1
                   END AS fiscal_start,
                   row_number() OVER (
                       PARTITION BY CASE WHEN EXTRACT(MONTH FROM created_at AT TIME ZONE 'Asia/Kolkata') >= 4
                                         THEN EXTRACT(YEAR FROM created_at AT TIME ZONE 'Asia/Kolkata')::integer
                                         ELSE EXTRACT(YEAR FROM created_at AT TIME ZONE 'Asia/Kolkata')::integer - 1
                                    END
                       ORDER BY created_at, id
                   )::bigint AS serial
            FROM crm_candidates
            WHERE care_id IS NULL
        ), updated AS (
            UPDATE crm_candidates candidate
            SET care_id = format('CI-%s-%s-%s',
                                 right(ranked.fiscal_start::text, 2),
                                 right((ranked.fiscal_start + 1)::text, 2),
                                 lpad(ranked.serial::text, 3, '0'))
            FROM ranked
            WHERE candidate.id = ranked.id
            RETURNING ranked.fiscal_start, ranked.serial
        )
        INSERT INTO crm_candidate_fiscal_counters (fiscal_year, last_serial)
        SELECT format('%s-%s', fiscal_start, fiscal_start + 1), max(serial)
        FROM updated
        GROUP BY fiscal_start
        ON CONFLICT (fiscal_year) DO UPDATE
        SET last_serial = GREATEST(crm_candidate_fiscal_counters.last_serial, EXCLUDED.last_serial)
    """)
    # An explicitly supplied ID is rare, but it must still be a valid Care
    # India April--March identifier.  The trigger below only fills a blank;
    # this check prevents a direct integration from storing an arbitrary value.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_crm_candidates_care_id_format'
                  AND conrelid = 'crm_candidates'::regclass
            ) THEN
                ALTER TABLE crm_candidates
                ADD CONSTRAINT ck_crm_candidates_care_id_format CHECK (
                    care_id IS NULL OR (
                        care_id ~ '^[C][I]-[0-9]{2}-[0-9]{2}-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$'
                        AND ((substring(care_id from 4 for 2)::integer + 1) % 100)
                            = substring(care_id from 7 for 2)::integer
                    )
                );
            END IF;
        END $$
    """)

    # Direct SQL imports are rare, but a database guard makes it impossible for
    # a future intake path to accidentally omit the public identity.  Explicit
    # app-side allocation is still used so API responses receive the value in
    # the same transaction.
    op.execute("""
        CREATE OR REPLACE FUNCTION crm_assign_care_id() RETURNS trigger AS $$
        DECLARE
            local_day date;
            fiscal_start integer;
            fiscal_label text;
            serial bigint;
        BEGIN
            IF NEW.care_id IS NULL OR btrim(NEW.care_id) = '' THEN
                local_day := (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date;
                fiscal_start := CASE WHEN EXTRACT(MONTH FROM local_day) >= 4
                                     THEN EXTRACT(YEAR FROM local_day)::integer
                                     ELSE EXTRACT(YEAR FROM local_day)::integer - 1 END;
                fiscal_label := format('%s-%s', fiscal_start, fiscal_start + 1);
                INSERT INTO crm_candidate_fiscal_counters (fiscal_year, last_serial)
                VALUES (fiscal_label, 1)
                ON CONFLICT (fiscal_year) DO UPDATE
                SET last_serial = crm_candidate_fiscal_counters.last_serial + 1
                RETURNING last_serial INTO serial;
                NEW.care_id := format('CI-%s-%s-%s', right(fiscal_start::text, 2),
                                      right((fiscal_start + 1)::text, 2), lpad(serial::text, 3, '0'));
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS crm_candidates_assign_care_id ON crm_candidates")
    op.execute("CREATE TRIGGER crm_candidates_assign_care_id BEFORE INSERT ON crm_candidates FOR EACH ROW EXECUTE FUNCTION crm_assign_care_id()")
    op.execute("""
        CREATE OR REPLACE FUNCTION crm_keep_care_id_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.care_id IS NOT NULL AND NEW.care_id IS DISTINCT FROM OLD.care_id THEN
                RAISE EXCEPTION 'Care India ID is immutable' USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS crm_candidates_keep_care_id_immutable ON crm_candidates")
    op.execute("CREATE TRIGGER crm_candidates_keep_care_id_immutable BEFORE UPDATE OF care_id ON crm_candidates FOR EACH ROW EXECUTE FUNCTION crm_keep_care_id_immutable()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS crm_candidates_keep_care_id_immutable ON crm_candidates")
    op.execute("DROP TRIGGER IF EXISTS crm_candidates_assign_care_id ON crm_candidates")
    op.execute("DROP FUNCTION IF EXISTS crm_keep_care_id_immutable()")
    op.execute("DROP FUNCTION IF EXISTS crm_assign_care_id()")
    op.execute("ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS ck_crm_candidates_care_id_format")
    op.execute("DROP TABLE IF EXISTS crm_candidate_fiscal_counters")
    op.execute("DROP INDEX IF EXISTS uq_crm_candidates_care_id")
    op.execute("ALTER TABLE crm_candidates DROP COLUMN IF EXISTS care_id")
