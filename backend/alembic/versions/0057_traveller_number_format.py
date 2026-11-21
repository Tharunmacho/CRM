"""Traveller numbers become ``CI 01``, ``CI 02`` ... and every past traveller is numbered by DATE OF TRAVEL, oldest first.

Before this revision the traveller ID was ``CI-T-001`` and followed the order in which people became travellers in the CRM.  The
owner wants the number to read ``CI 01`` (uppercase CI, a space, at least two digits, simply growing wider after ``CI 99``) and to
run "from past dates to current": the person who flew first is ``CI 01``.

* Order. Every candidate who already holds a traveller ID (and, as a safety net, every candidate with a deployed placement who
  somehow holds none) is numbered by the DATE OF TRAVEL of the air ticket (stage 13, ``data.travelDate``, only a real calendar day
  counts), oldest first.  A traveller with no usable date uses the day the ticket stage was completed (India time) instead, and
  ties are broken by the completion time of the ticket stage, then by the candidate reference and id, so a rerun gives the same
  answer.  A traveller with no ticket record at all goes last.
* Everything happens in this one transaction: the old check constraint and the immutability trigger are switched off, all numbers
  are rewritten in one set-based UPDATE (the new text can never equal an old ``CI-T-`` value, so the unique index is never hit
  halfway), the counter row is set to the highest number issued, and the new check constraint and the trigger are put back.
* Idempotent: when no ``CI-T-`` value is left (already converted, or an empty database) the renumbering is skipped, so the numbers
  of later travellers are never reshuffled.  ``assign_traveller_id`` still never reassigns once a number is given; this migration
  is the one deliberate renumbering.
* Downgrade: ``CI 07`` becomes ``CI-T-007`` (same number, same order) and the previous constraint comes back.
"""
from alembic import op


revision = "0057_traveller_number_format"
down_revision = "0056_stage_order_google_review"
branch_labels = None
depends_on = None

TICKET_STAGE = 13

OLD_CHECK = "traveller_id IS NULL OR traveller_id ~ '^[C][I]-[T]-(00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$'"
NEW_CHECK = "traveller_id IS NULL OR traveller_id ~ '^[C][I] (0[1-9]|[1-9][0-9]+)$'"
TRIGGER = "crm_candidates_keep_traveller_id_immutable"


def upgrade():
    op.execute(f"ALTER TABLE crm_candidates DISABLE TRIGGER {TRIGGER}")
    op.execute("ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS ck_crm_candidates_traveller_id_format")
    # A stored travel date is free text from the stage form; only a real calendar day may decide the order.
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
    op.execute("DROP TABLE IF EXISTS crm_renumber_travellers")
    op.execute(f"""
        CREATE TEMP TABLE crm_renumber_travellers AS
        WITH ticket AS (
            SELECT DISTINCT ON (stage.application_id) stage.application_id, stage.data, stage.completed_at
            FROM crm_application_stages stage
            WHERE stage.stage = {TICKET_STAGE} AND stage.reopened_reason IS NULL
            ORDER BY stage.application_id, stage.attempt DESC
        ), per_application AS (
            SELECT application.candidate_id,
                   COALESCE(pg_temp.crm_safe_day(ticket.data ->> 'travelDate'), (ticket.completed_at AT TIME ZONE 'Asia/Kolkata')::date) AS travelled_on,
                   ticket.completed_at
            FROM crm_applications application
            JOIN ticket ON ticket.application_id = application.id
        ), per_candidate AS (
            SELECT DISTINCT ON (candidate_id) candidate_id, travelled_on, completed_at
            FROM per_application
            ORDER BY candidate_id, travelled_on NULLS LAST, completed_at NULLS LAST
        )
        SELECT candidate.id,
               row_number() OVER (ORDER BY found.travelled_on NULLS LAST, found.completed_at NULLS LAST, candidate.ref, candidate.id)::bigint AS serial
        FROM crm_candidates candidate
        LEFT JOIN per_candidate found ON found.candidate_id = candidate.id
        WHERE candidate.traveller_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM crm_applications deployed WHERE deployed.candidate_id = candidate.id AND deployed.status = 'deployed')
    """)
    # Already converted (or nothing to convert): keep every number exactly as it is.
    op.execute("""
        DELETE FROM crm_renumber_travellers
        WHERE NOT EXISTS (SELECT 1 FROM crm_candidates old WHERE old.traveller_id ~ '^CI-T-')
    """)
    op.execute("""
        UPDATE crm_candidates candidate
        SET traveller_id = 'CI ' || CASE WHEN renumber.serial < 100 THEN lpad(renumber.serial::text, 2, '0') ELSE renumber.serial::text END
        FROM crm_renumber_travellers renumber
        WHERE renumber.id = candidate.id
    """)
    op.execute("""
        INSERT INTO crm_traveller_id_counters (series, last_serial)
        SELECT 'GLOBAL', max(serial) FROM crm_renumber_travellers
        HAVING count(*) > 0
        ON CONFLICT (series) DO UPDATE SET last_serial = EXCLUDED.last_serial
    """)
    op.execute("DROP TABLE crm_renumber_travellers")
    op.execute("DROP FUNCTION IF EXISTS pg_temp.crm_safe_day(text)")
    op.execute(f"ALTER TABLE crm_candidates ADD CONSTRAINT ck_crm_candidates_traveller_id_format CHECK ({NEW_CHECK})")
    op.execute(f"ALTER TABLE crm_candidates ENABLE TRIGGER {TRIGGER}")
    op.execute("COMMENT ON COLUMN crm_candidates.traveller_id IS 'Immutable Care India traveller number: CI 01, CI 02, ... CI 99, CI 100, ... one continuous series, numbered by date of travel for past travellers and given once when the air ticket is completed. Separate series from care_id.'")
    op.execute("COMMENT ON TABLE crm_traveller_id_counters IS 'Single continuous counter (row GLOBAL) for immutable Care India traveller numbers: CI 01, CI 02, ... Never restarts, never reused.'")


def downgrade():
    op.execute(f"ALTER TABLE crm_candidates DISABLE TRIGGER {TRIGGER}")
    op.execute("ALTER TABLE crm_candidates DROP CONSTRAINT IF EXISTS ck_crm_candidates_traveller_id_format")
    op.execute("""
        UPDATE crm_candidates
        SET traveller_id = 'CI-T-' || CASE WHEN substring(traveller_id from 4)::bigint < 1000
                                           THEN lpad(substring(traveller_id from 4), 3, '0') ELSE substring(traveller_id from 4) END
        WHERE traveller_id ~ '^CI [0-9]+$'
    """)
    op.execute(f"ALTER TABLE crm_candidates ADD CONSTRAINT ck_crm_candidates_traveller_id_format CHECK ({OLD_CHECK})")
    op.execute(f"ALTER TABLE crm_candidates ENABLE TRIGGER {TRIGGER}")
    op.execute("COMMENT ON COLUMN crm_candidates.traveller_id IS 'Immutable Care India traveller ID: CI-T-001, CI-T-002, ... one continuous series, given once when the placement is deployed. Separate series from care_id.'")
    op.execute("COMMENT ON TABLE crm_traveller_id_counters IS 'Single continuous counter (row GLOBAL) for immutable Care India traveller IDs: CI-T-001, CI-T-002, ... Never restarts, never reused.'")
