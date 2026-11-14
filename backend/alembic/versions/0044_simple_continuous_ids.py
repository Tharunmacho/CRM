"""Simple, continuous public IDs: ``CI-001`` for every candidate, ``CI-T-001`` for every traveller.

Replaces the financial-year formats of 0040 (``CI-26-27-078``) and 0041 (``CI-T-26-27-012``) with two plain series that
never restart:

* ``crm_candidates.care_id``      ``CI-001``, ``CI-002`` ... ``CI-999``, ``CI-1000`` ...  (at least three digits)
* ``crm_candidates.traveller_id`` ``CI-T-001``, ``CI-T-002`` ...  given only when a placement is deployed

Upgrade, all inside the migration's single transaction:

1. The per-financial-year counter tables are replaced by two single-row counters (``crm_candidate_id_counters`` and
   ``crm_traveller_id_counters``, one row keyed ``GLOBAL``).  They are reserved with the same one-statement
   ``INSERT .. ON CONFLICT DO UPDATE .. RETURNING`` as before, so concurrent creations still serialise on one row lock
   and a rolled-back transaction consumes nothing.
2. Every candidate present now is renumbered contiguously from ``CI-001`` in their current Care India ID order (financial
   year, then serial, ties by ``created_at`` and ``id``; a candidate without an ID goes last).  A gap left by a candidate
   deleted beforehand therefore closes.  Travellers are renumbered contiguously from ``CI-T-001`` in their current
   Traveller ID order (financial year of the travel date, then serial), which keeps their relative travel order.
   Numbering is done in two steps (clear, then assign from a mapping computed beforehand) so the partial unique indexes
   can never collide, even if the migration body is run on already-converted data.
   (A candidate with a deployed placement but no Traveller ID, which should not exist, is numbered after the others.)
3. Both counters are set to the new highest number, so the next candidate gets max + 1 and the next person to travel
   gets the next CI-T number.  Numbers are never reused: the counters only grow, and deleting a candidate leaves a hole
   rather than renumbering anyone.
4. The format check constraints are replaced, the DB-side ``crm_assign_care_id`` trigger function issues the new format
   from the new counter, and the immutability triggers are switched off only while the renumbering runs.

Downgrade restores the old formats as far as the data allows: ``care_id`` becomes ``CI-YY-YY-NNN`` with the financial
year of ``created_at`` (India time) and the serial counted inside that year in the current number order;
``traveller_id`` becomes ``CI-T-YY-YY-NNN`` with the financial year of the travel date (the latest stage 11 ``travelDate``,
else the stage completion day, else the placement's last update, else the candidate's last update) ordered by travel date
then current number.  The ORIGINAL numbers are not recovered when rows were deleted or numbers moved since 0043: the
downgrade is deterministic and gives a valid, unique, gap-free old-format numbering, not a byte-for-byte restore.
"""
from alembic import op


revision = "0044_simple_continuous_ids"
down_revision = "0043_stage_stop_exits"
branch_labels = None
depends_on = None

OLD_CARE_CHECK = (
    "care_id IS NULL OR (care_id ~ '^[C][I]-[0-9]{2}-[0-9]{2}-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$' "
    "AND ((substring(care_id from 4 for 2)::integer + 1) % 100) = substring(care_id from 7 for 2)::integer)"
)
OLD_TRAVELLER_CHECK = (
    "traveller_id IS NULL OR (traveller_id ~ '^[C][I]-[T]-[0-9]{2}-[0-9]{2}-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$' "
    "AND ((substring(traveller_id from 6 for 2)::integer + 1) % 100) = substring(traveller_id from 9 for 2)::integer)"
)
NEW_CARE_CHECK = "care_id IS NULL OR care_id ~ '^[C][I]-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$'"
NEW_TRAVELLER_CHECK = "traveller_id IS NULL OR traveller_id ~ '^[C][I]-[T]-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$'"


def _drop_constraints():
    op.execute("ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS ck_crm_candidates_care_id_format")
    op.execute("ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS ck_crm_candidates_traveller_id_format")


def _add_constraints(care_check, traveller_check):
    op.execute(f"ALTER TABLE crm_candidates ADD CONSTRAINT ck_crm_candidates_care_id_format CHECK ({care_check})")
    op.execute(f"ALTER TABLE crm_candidates ADD CONSTRAINT ck_crm_candidates_traveller_id_format CHECK ({traveller_check})")


def _immutability_triggers(enabled):
    verb = "ENABLE" if enabled else "DISABLE"
    op.execute(f"ALTER TABLE crm_candidates {verb} TRIGGER crm_candidates_keep_care_id_immutable")
    op.execute(f"ALTER TABLE crm_candidates {verb} TRIGGER crm_candidates_keep_traveller_id_immutable")


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS crm_candidate_id_counters (
            series VARCHAR(16) PRIMARY KEY,
            last_serial BIGINT NOT NULL CONSTRAINT ck_crm_candidate_id_counters_last_serial CHECK (last_serial >= 0)
        )
    """)
    op.execute("COMMENT ON TABLE crm_candidate_id_counters IS 'Single continuous counter (row GLOBAL) for immutable Care India candidate IDs: CI-001, CI-002, ... Never restarts, never reused.'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS crm_traveller_id_counters (
            series VARCHAR(16) PRIMARY KEY,
            last_serial BIGINT NOT NULL CONSTRAINT ck_crm_traveller_id_counters_last_serial CHECK (last_serial >= 0)
        )
    """)
    op.execute("COMMENT ON TABLE crm_traveller_id_counters IS 'Single continuous counter (row GLOBAL) for immutable Care India traveller IDs: CI-T-001, CI-T-002, ... Never restarts, never reused.'")

    _drop_constraints()
    _immutability_triggers(False)

    # The order each person holds today, computed BEFORE anything is cleared. Old format: (financial year, serial).
    # Already-converted format: serial only. Nothing parseable: last, by entry time.
    op.execute("DROP TABLE IF EXISTS crm_renumber_candidates")
    op.execute("""
        CREATE TEMP TABLE crm_renumber_candidates AS
        SELECT id, row_number() OVER (
                   ORDER BY CASE WHEN care_id ~ '^CI-[0-9]{2}-[0-9]{2}-[0-9]+$' THEN substring(care_id from 4 for 2)::integer
                                 WHEN care_id ~ '^CI-[0-9]+$' THEN 0
                                 ELSE 100 END,
                             CASE WHEN care_id ~ '^CI-([0-9]{2}-[0-9]{2}-)?[0-9]+$' THEN substring(care_id from '[0-9]+$')::bigint
                                  ELSE 0 END,
                             created_at, id
               )::bigint AS serial
        FROM crm_candidates
    """)
    op.execute("DROP TABLE IF EXISTS crm_renumber_travellers")
    # Everyone who holds a Traveller ID, in their current order; then (safety net) anyone with a deployed placement who
    # somehow has none yet, by entry time, so every traveller ends up with a number.
    op.execute("""
        CREATE TEMP TABLE crm_renumber_travellers AS
        SELECT id, row_number() OVER (ORDER BY grp, year_key, serial_key, created_at, id)::bigint AS serial
        FROM (
            SELECT id, created_at, 0 AS grp,
                   CASE WHEN traveller_id ~ '^CI-T-[0-9]{2}-[0-9]{2}-[0-9]+$' THEN substring(traveller_id from 6 for 2)::integer
                        WHEN traveller_id ~ '^CI-T-[0-9]+$' THEN 0
                        ELSE 100 END AS year_key,
                   CASE WHEN traveller_id ~ '^CI-T-([0-9]{2}-[0-9]{2}-)?[0-9]+$' THEN substring(traveller_id from '[0-9]+$')::bigint
                        ELSE 0 END AS serial_key
            FROM crm_candidates
            WHERE traveller_id IS NOT NULL
            UNION ALL
            SELECT candidate.id, candidate.created_at, 1, 0, 0
            FROM crm_candidates candidate
            WHERE candidate.traveller_id IS NULL
              AND EXISTS (SELECT 1 FROM crm_applications application
                          WHERE application.candidate_id = candidate.id AND application.status = 'deployed')
        ) numbered
    """)

    # Step 1: clear, so step 2 can never meet a number it is about to replace. Step 2: assign.
    op.execute("UPDATE crm_candidates SET care_id = NULL, traveller_id = NULL WHERE care_id IS NOT NULL OR traveller_id IS NOT NULL")
    op.execute("""
        UPDATE crm_candidates candidate
        SET care_id = 'CI-' || CASE WHEN renumber.serial < 1000 THEN lpad(renumber.serial::text, 3, '0') ELSE renumber.serial::text END
        FROM crm_renumber_candidates renumber
        WHERE candidate.id = renumber.id
    """)
    op.execute("""
        UPDATE crm_candidates candidate
        SET traveller_id = 'CI-T-' || CASE WHEN renumber.serial < 1000 THEN lpad(renumber.serial::text, 3, '0') ELSE renumber.serial::text END
        FROM crm_renumber_travellers renumber
        WHERE candidate.id = renumber.id
    """)

    # The next number is always one past the highest ever issued: set the counters to the new maximum.
    op.execute("""
        INSERT INTO crm_candidate_id_counters (series, last_serial)
        SELECT 'GLOBAL', COALESCE(max(serial), 0) FROM crm_renumber_candidates
        ON CONFLICT (series) DO UPDATE SET last_serial = EXCLUDED.last_serial
    """)
    op.execute("""
        INSERT INTO crm_traveller_id_counters (series, last_serial)
        SELECT 'GLOBAL', COALESCE(max(serial), 0) FROM crm_renumber_travellers
        ON CONFLICT (series) DO UPDATE SET last_serial = EXCLUDED.last_serial
    """)
    op.execute("DROP TABLE crm_renumber_candidates")
    op.execute("DROP TABLE crm_renumber_travellers")

    _add_constraints(NEW_CARE_CHECK, NEW_TRAVELLER_CHECK)
    _immutability_triggers(True)
    op.execute("COMMENT ON COLUMN crm_candidates.care_id IS 'Immutable Care India public candidate ID: CI-001, CI-002, ... one continuous series (no financial year), given at creation. Legacy ref remains unchanged.'")
    op.execute("COMMENT ON COLUMN crm_candidates.traveller_id IS 'Immutable Care India traveller ID: CI-T-001, CI-T-002, ... one continuous series, given once when the placement is deployed. Separate series from care_id.'")

    # DB-side backstop for direct inserts that omit the ID: same counter, same format as app.services.care_india_id.
    op.execute("""
        CREATE OR REPLACE FUNCTION crm_assign_care_id() RETURNS trigger AS $$
        DECLARE
            serial bigint;
        BEGIN
            IF NEW.care_id IS NULL OR btrim(NEW.care_id) = '' THEN
                INSERT INTO crm_candidate_id_counters (series, last_serial)
                VALUES ('GLOBAL', 1)
                ON CONFLICT (series) DO UPDATE
                SET last_serial = crm_candidate_id_counters.last_serial + 1
                RETURNING last_serial INTO serial;
                NEW.care_id := 'CI-' || CASE WHEN serial < 1000 THEN lpad(serial::text, 3, '0') ELSE serial::text END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("DROP TABLE IF EXISTS crm_candidate_fiscal_counters")
    op.execute("DROP TABLE IF EXISTS crm_traveller_fiscal_counters")


def downgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS crm_candidate_fiscal_counters (
            fiscal_year VARCHAR(9) PRIMARY KEY,
            last_serial BIGINT NOT NULL CONSTRAINT ck_crm_candidate_fiscal_counters_last_serial CHECK (last_serial >= 0)
        )
    """)
    op.execute("COMMENT ON TABLE crm_candidate_fiscal_counters IS 'Per-financial-year counters for immutable Care India public candidate IDs.'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS crm_traveller_fiscal_counters (
            fiscal_year VARCHAR(9) PRIMARY KEY,
            last_serial BIGINT NOT NULL CONSTRAINT ck_crm_traveller_fiscal_counters_last_serial CHECK (last_serial >= 0)
        )
    """)
    op.execute("COMMENT ON TABLE crm_traveller_fiscal_counters IS 'Per-financial-year counters for immutable Care India traveller IDs (CI-T-YY-YY-NNN), keyed by travel date.'")

    _drop_constraints()
    _immutability_triggers(False)

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
    op.execute("DROP TABLE IF EXISTS crm_renumber_candidates")
    op.execute("""
        CREATE TEMP TABLE crm_renumber_candidates AS
        WITH dated AS (
            SELECT id, created_at, care_id,
                   CASE WHEN EXTRACT(MONTH FROM created_at AT TIME ZONE 'Asia/Kolkata') >= 4
                        THEN EXTRACT(YEAR FROM created_at AT TIME ZONE 'Asia/Kolkata')::integer
                        ELSE EXTRACT(YEAR FROM created_at AT TIME ZONE 'Asia/Kolkata')::integer - 1
                   END AS fiscal_start
            FROM crm_candidates
        )
        SELECT id, fiscal_start,
               row_number() OVER (
                   PARTITION BY fiscal_start
                   ORDER BY CASE WHEN care_id ~ '^CI-[0-9]+$' THEN substring(care_id from '[0-9]+$')::bigint END NULLS LAST,
                            created_at, id
               )::bigint AS serial
        FROM dated
    """)
    op.execute("DROP TABLE IF EXISTS crm_renumber_travellers")
    op.execute("""
        CREATE TEMP TABLE crm_renumber_travellers AS
        WITH latest_ticket AS (
            SELECT DISTINCT ON (stage.application_id) stage.application_id, stage.data, stage.completed_at
            FROM crm_application_stages stage
            WHERE stage.stage = 11
            ORDER BY stage.application_id, stage.attempt DESC
        ), travel AS (
            SELECT DISTINCT ON (candidate.id)
                   candidate.id, candidate.created_at, candidate.traveller_id,
                   COALESCE(pg_temp.crm_safe_day(ticket.data ->> 'travelDate'),
                            (ticket.completed_at AT TIME ZONE 'Asia/Kolkata')::date,
                            (application.updated_at AT TIME ZONE 'Asia/Kolkata')::date,
                            (candidate.updated_at AT TIME ZONE 'Asia/Kolkata')::date) AS travel_day
            FROM crm_candidates candidate
            LEFT JOIN crm_applications application ON application.candidate_id = candidate.id
            LEFT JOIN latest_ticket ticket ON ticket.application_id = application.id
            WHERE candidate.traveller_id IS NOT NULL
            ORDER BY candidate.id, (application.status = 'deployed') DESC NULLS LAST, application.created_at
        ), fiscal AS (
            SELECT travel.*,
                   CASE WHEN EXTRACT(MONTH FROM travel_day) >= 4
                        THEN EXTRACT(YEAR FROM travel_day)::integer
                        ELSE EXTRACT(YEAR FROM travel_day)::integer - 1 END AS fiscal_start
            FROM travel
        )
        SELECT id, fiscal_start,
               row_number() OVER (
                   PARTITION BY fiscal_start
                   ORDER BY travel_day,
                            CASE WHEN traveller_id ~ '^CI-T-[0-9]+$' THEN substring(traveller_id from '[0-9]+$')::bigint END NULLS LAST,
                            created_at, id
               )::bigint AS serial
        FROM fiscal
    """)
    op.execute("DROP FUNCTION IF EXISTS pg_temp.crm_safe_day(text)")

    op.execute("UPDATE crm_candidates SET care_id = NULL, traveller_id = NULL WHERE care_id IS NOT NULL OR traveller_id IS NOT NULL")
    op.execute("""
        UPDATE crm_candidates candidate
        SET care_id = format('CI-%s-%s-%s', right(renumber.fiscal_start::text, 2), right((renumber.fiscal_start + 1)::text, 2),
                             CASE WHEN renumber.serial < 1000 THEN lpad(renumber.serial::text, 3, '0') ELSE renumber.serial::text END)
        FROM crm_renumber_candidates renumber
        WHERE candidate.id = renumber.id
    """)
    op.execute("""
        UPDATE crm_candidates candidate
        SET traveller_id = format('CI-T-%s-%s-%s', right(renumber.fiscal_start::text, 2), right((renumber.fiscal_start + 1)::text, 2),
                                  CASE WHEN renumber.serial < 1000 THEN lpad(renumber.serial::text, 3, '0') ELSE renumber.serial::text END)
        FROM crm_renumber_travellers renumber
        WHERE candidate.id = renumber.id
    """)
    op.execute("""
        INSERT INTO crm_candidate_fiscal_counters (fiscal_year, last_serial)
        SELECT format('%s-%s', fiscal_start, fiscal_start + 1), max(serial) FROM crm_renumber_candidates GROUP BY fiscal_start
        ON CONFLICT (fiscal_year) DO UPDATE SET last_serial = EXCLUDED.last_serial
    """)
    op.execute("""
        INSERT INTO crm_traveller_fiscal_counters (fiscal_year, last_serial)
        SELECT format('%s-%s', fiscal_start, fiscal_start + 1), max(serial) FROM crm_renumber_travellers GROUP BY fiscal_start
        ON CONFLICT (fiscal_year) DO UPDATE SET last_serial = EXCLUDED.last_serial
    """)
    op.execute("DROP TABLE crm_renumber_candidates")
    op.execute("DROP TABLE crm_renumber_travellers")

    _add_constraints(OLD_CARE_CHECK, OLD_TRAVELLER_CHECK)
    _immutability_triggers(True)
    op.execute("COMMENT ON COLUMN crm_candidates.care_id IS 'Immutable Care India public candidate ID: CI-YY-YY-NNN, sequenced by the April-March India financial year. Legacy ref remains unchanged.'")
    op.execute("COMMENT ON COLUMN crm_candidates.traveller_id IS 'Immutable Care India traveller ID: CI-T-YY-YY-NNN, sequenced by the April-March India financial year of the travel date. Separate series from care_id.'")
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
    op.execute("DROP TABLE IF EXISTS crm_candidate_id_counters")
    op.execute("DROP TABLE IF EXISTS crm_traveller_id_counters")
