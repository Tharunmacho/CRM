"""Add the immutable Care India TRAVELLER ID, a series separate from the Data Bank Care India ID.

``crm_candidates.care_id`` (CI-YY-YY-NNN) numbers everybody who enters the Data Bank.  ``traveller_id``
(CI-T-YY-YY-NNN) numbers only the people who travelled, by the Indian April--March financial year of the TRAVEL DATE,
with its own counter table, so the serial restarts at 001 every 1 April.  Both carry ``CI`` because that is the agency.

Existing travelled candidates (an application with status 'deployed') are numbered here, ordered by travel date and then
by when the candidate entered the CRM.  The travel date is the latest stage 11 attempt's ``travelDate``; a placement with
no usable date falls back to the day that attempt was completed (India time), and failing that the application's last update.
"""
from alembic import op


revision = "0041_care_india_traveller_ids"
down_revision = "0040_care_india_candidate_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_candidates ADD COLUMN IF NOT EXISTS traveller_id VARCHAR(40)")
    op.execute("COMMENT ON COLUMN crm_candidates.traveller_id IS 'Immutable Care India traveller ID: CI-T-YY-YY-NNN, sequenced by the April-March India financial year of the travel date. Separate series from care_id.'")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_candidates_traveller_id ON crm_candidates (traveller_id) WHERE traveller_id IS NOT NULL")
    op.execute("""
        CREATE TABLE IF NOT EXISTS crm_traveller_fiscal_counters (
            fiscal_year VARCHAR(9) PRIMARY KEY,
            last_serial BIGINT NOT NULL CONSTRAINT ck_crm_traveller_fiscal_counters_last_serial CHECK (last_serial >= 0)
        )
    """)
    op.execute("COMMENT ON TABLE crm_traveller_fiscal_counters IS 'Per-financial-year counters for immutable Care India traveller IDs (CI-T-YY-YY-NNN), keyed by travel date.'")

    # A stored travel date is free text from the stage form; only a real calendar day may decide the financial year.
    op.execute("""
        CREATE OR REPLACE FUNCTION pg_temp.crm_safe_day(value text) RETURNS date AS $$
        BEGIN
            IF value IS NULL OR value !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
                RETURN NULL;
            END IF;
            RETURN value::date;
        EXCEPTION WHEN others THEN
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE
    """)
    # One row per travelled candidate (their earliest deployed placement), numbered inside each financial year by travel
    # date, then entry time, reference and id so a rerun gives the same answer. Candidates that already hold an ID are
    # left alone and the new numbers continue after the counter.
    op.execute("""
        WITH latest_ticket AS (
            SELECT DISTINCT ON (stage.application_id) stage.application_id, stage.data, stage.completed_at
            FROM crm_application_stages stage
            WHERE stage.stage = 11 AND stage.reopened_reason IS NULL
            ORDER BY stage.application_id, stage.attempt DESC
        ), travelled AS (
            SELECT DISTINCT ON (candidate.id)
                   candidate.id,
                   candidate.created_at,
                   candidate.ref,
                   COALESCE(pg_temp.crm_safe_day(ticket.data ->> 'travelDate'),
                            (ticket.completed_at AT TIME ZONE 'Asia/Kolkata')::date,
                            (application.updated_at AT TIME ZONE 'Asia/Kolkata')::date) AS travel_day
            FROM crm_candidates candidate
            JOIN crm_applications application ON application.candidate_id = candidate.id
            LEFT JOIN latest_ticket ticket ON ticket.application_id = application.id
            WHERE application.status = 'deployed' AND candidate.traveller_id IS NULL
            ORDER BY candidate.id,
                     COALESCE(pg_temp.crm_safe_day(ticket.data ->> 'travelDate'),
                              (ticket.completed_at AT TIME ZONE 'Asia/Kolkata')::date,
                              (application.updated_at AT TIME ZONE 'Asia/Kolkata')::date),
                     application.created_at
        ), fiscal AS (
            SELECT travelled.*,
                   CASE WHEN EXTRACT(MONTH FROM travel_day) >= 4
                        THEN EXTRACT(YEAR FROM travel_day)::integer
                        ELSE EXTRACT(YEAR FROM travel_day)::integer - 1 END AS fiscal_start
            FROM travelled
        ), ranked AS (
            SELECT fiscal.id, fiscal.fiscal_start,
                   (row_number() OVER (PARTITION BY fiscal.fiscal_start
                                       ORDER BY fiscal.travel_day, fiscal.created_at, fiscal.ref, fiscal.id)
                    + COALESCE(counter.last_serial, 0))::bigint AS serial
            FROM fiscal
            LEFT JOIN crm_traveller_fiscal_counters counter
                   ON counter.fiscal_year = format('%s-%s', fiscal.fiscal_start, fiscal.fiscal_start + 1)
        ), updated AS (
            UPDATE crm_candidates candidate
            SET traveller_id = format('CI-T-%s-%s-%s',
                                      right(ranked.fiscal_start::text, 2),
                                      right((ranked.fiscal_start + 1)::text, 2),
                                      lpad(ranked.serial::text, 3, '0'))
            FROM ranked
            WHERE candidate.id = ranked.id
            RETURNING ranked.fiscal_start, ranked.serial
        )
        INSERT INTO crm_traveller_fiscal_counters (fiscal_year, last_serial)
        SELECT format('%s-%s', fiscal_start, fiscal_start + 1), max(serial)
        FROM updated
        GROUP BY fiscal_start
        ON CONFLICT (fiscal_year) DO UPDATE
        SET last_serial = GREATEST(crm_traveller_fiscal_counters.last_serial, EXCLUDED.last_serial)
    """)
    op.execute("DROP FUNCTION IF EXISTS pg_temp.crm_safe_day(text)")

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_crm_candidates_traveller_id_format'
                  AND conrelid = 'crm_candidates'::regclass
            ) THEN
                ALTER TABLE crm_candidates
                ADD CONSTRAINT ck_crm_candidates_traveller_id_format CHECK (
                    traveller_id IS NULL OR (
                        traveller_id ~ '^[C][I]-[T]-[0-9]{2}-[0-9]{2}-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$'
                        AND ((substring(traveller_id from 6 for 2)::integer + 1) % 100)
                            = substring(traveller_id from 9 for 2)::integer
                    )
                );
            END IF;
        END $$
    """)

    # Assignment is explicit and app-side, in the transaction that deploys the placement. The database only guarantees
    # that a number, once given, can never change or be removed (a reversed placement keeps its traveller ID).
    op.execute("""
        CREATE OR REPLACE FUNCTION crm_keep_traveller_id_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.traveller_id IS NOT NULL AND NEW.traveller_id IS DISTINCT FROM OLD.traveller_id THEN
                RAISE EXCEPTION 'Traveller ID is immutable' USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS crm_candidates_keep_traveller_id_immutable ON crm_candidates")
    op.execute("CREATE TRIGGER crm_candidates_keep_traveller_id_immutable BEFORE UPDATE OF traveller_id ON crm_candidates FOR EACH ROW EXECUTE FUNCTION crm_keep_traveller_id_immutable()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS crm_candidates_keep_traveller_id_immutable ON crm_candidates")
    op.execute("DROP FUNCTION IF EXISTS crm_keep_traveller_id_immutable()")
    op.execute("ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS ck_crm_candidates_traveller_id_format")
    op.execute("DROP TABLE IF EXISTS crm_traveller_fiscal_counters")
    op.execute("DROP INDEX IF EXISTS uq_crm_candidates_traveller_id")
    op.execute("ALTER TABLE crm_candidates DROP COLUMN IF EXISTS traveller_id")
