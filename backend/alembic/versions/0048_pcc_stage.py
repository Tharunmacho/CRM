"""PCC becomes its own stage: twelve stages, stage 7 is the police clearance certificate.

The old stage 6 was labelled "Medical/PCC" on the client's sheet but only ever stored the medical. The workflow now has
twelve stages: 6 Medical, 7 PCC (new), 8 Passport, 9 Advance payment, 10 Visa arrival + Google review, 11 Visa stamping,
12 Air ticket. Every stored stage number >= 7 therefore moves up by one, wherever it is stored:

* ``crm_applications.current_stage`` and the range check ``ck_application_stage_range`` (1..12)
* ``crm_application_stages.stage`` (the stage records and attempts) and its range check ``ck_stage_range`` (1..12)
* ``workflow_tasks.stage``, ``workflow_daily_work_items.stage``, ``crm_application_feedback.stage``
* ``document_attachments.slot`` of the stage documents (``stage_7_copy`` -> ``stage_8_copy`` ... ``stage_11_file`` -> ``stage_12_file``)
* the audit trail: ``system_audit_log.action`` ("Recorded stage 8: Collected"), ``meta.stage``, ``meta.superseded_attempts[].stage``
  of the stage entries and ``meta.completed_stages`` of a bulk import; and the stage shown in stored escalation notices
  (``system_notifications.detail``, " · Stage 9 · ")
* the closure reason enum gains ``pcc_not_cleared`` (the PCC stage's own stopping exit); existing values keep their meaning
  and move with their stage (passport_not_available is now stage 8 ... not_travelling stage 12).

Stage records and attachment slots are shifted one stage number at a time, DESCENDING (11 -> 12 first, 7 -> 8 last), so the
unique keys ``(application_id, stage, attempt)`` and ``(entity_type, entity_id, slot, version)`` never collide.

PCC data. The old stage-6 record held no PCC field (the old stage form had none), so there is nothing to invent: every
application that had moved PAST stage 6 (now at stage >= 8, closed or deployed ones included) gets a completed stage-7 PCC
record, attempt 1, copied from the PCC fields of its effective stage-6 record when it has any (``pccStatus``,
``pccAppliedDate``, ``pccReceivedDate`` or the nested ``pcc`` object that this migration's own downgrade writes) and otherwise
completed with no data ("Completed manually"). Its completion time is the stage-6 record's, so the stage timeline stays in
order. An application AT stage 6 (or closed there) stays at stage 6 with its record untouched. The PCC fields found are
moved out of the stage-6 record into the new one.

Fast and safe: set-based SQL only (no per-row python), one transaction (alembic's), and an empty database is a no-op.
Running the body again on converted data does nothing (it detects the 1..12 range check).

Downgrade merges PCC back and shifts down: each effective PCC record (data, outcome) is nested as ``pcc`` into the effective
stage-6 record (nothing is written for an empty "Completed manually" PCC record), the PCC records are deleted, and stage
numbers >= 8 move down by one, ascending. An application sitting at PCC becomes: still open -> stage 7 (Passport, the stage
after the old combined Medical/PCC), with its open PCC task turned into the Passport task and its saved draft cleared (the draft
holds PCC answers the old Passport form cannot show); closed at PCC -> closed at stage 6. The PCC stage documents
(``stage_7_file``) are kept under the slot ``stage_6_pcc``; PCC notes, tasks and daily-work items go back to stage 6. Applications
closed with ``pcc_not_cleared`` become ``unmapped`` (PostgreSQL cannot remove an enum value, so the unused one stays in the type).
Superseded (reopened) PCC attempts are not merged: only the effective one is. PCC data of an application that has no effective
stage-6 record has nowhere to go, so it is lost (it can only exist for a placement entered without a medical).
"""
import re

from alembic import op

revision = "0048_pcc_stage"
down_revision = "0047_agency_active_flag"
branch_labels = None
depends_on = None

PCC_KEYS = "'pccStatus', 'pccAppliedDate', 'pccReceivedDate'"
PCC_TASK_TITLE = "Collect the police clearance (PCC)"
PASSPORT_TASK_TITLE = "Collect original passport"
AUDIT_ACTION = r"^(Recorded|Saved|Reopened) stage [0-9]+"
AUDIT_NUMBER = r"^[A-Za-z]+ stage ([0-9]+)"  # (no "(?:": a colon followed by a name reads as a bind parameter)
NOTICE_NUMBER = r" . Stage ([0-9]+) . "

UP = "CASE WHEN {x} >= 7 THEN {x} + 1 ELSE {x} END"
DOWN = "CASE WHEN {x} = 7 THEN 6 WHEN {x} >= 8 THEN {x} - 1 ELSE {x} END"


def _range_check(name):
    return op.get_bind().exec_driver_sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s", (name,)).scalar()


def _top(definition):
    """The upper bound of a 'stage between 1 and N' check as PostgreSQL prints it (stage <= N), or None."""
    found = re.search(r"<=\s*(\d+)", definition or "")
    return int(found.group(1)) if found else None


def _drop_checks():
    op.execute("ALTER TABLE crm_applications DROP CONSTRAINT IF EXISTS ck_application_stage_range")
    op.execute("ALTER TABLE crm_application_stages DROP CONSTRAINT IF EXISTS ck_stage_range")


def _add_checks(top):
    op.execute(f"ALTER TABLE crm_applications ADD CONSTRAINT ck_application_stage_range CHECK (current_stage between 1 and {top})")
    op.execute(f"ALTER TABLE crm_application_stages ADD CONSTRAINT ck_stage_range CHECK (stage between 1 and {top})")


def _audit_and_notices(mapping):
    """The stage numbers written in the audit trail and in stored escalation notices, through `mapping` (a CASE template)."""
    number = f"CAST(substring(action from '{AUDIT_NUMBER}') AS integer)"
    op.execute(f"""
        UPDATE system_audit_log
        SET action = regexp_replace(action, '{AUDIT_ACTION}', '\\1 stage ' || CAST(({mapping.format(x=number)}) AS text))
        WHERE action ~ '{AUDIT_ACTION}' AND ({mapping.format(x=number)}) <> {number}
    """)
    for attribute in ("stage",):
        value = f"CAST(meta->>'{attribute}' AS integer)"
        op.execute(f"""
            UPDATE system_audit_log
            SET meta = jsonb_set(meta, '{{{attribute}}}', to_jsonb({mapping.format(x=value)}))
            WHERE action ~ '{AUDIT_ACTION}' AND jsonb_typeof(meta->'{attribute}') = 'number' AND ({mapping.format(x=value)}) <> {value}
        """)
    element = "CAST(item->>'stage' AS integer)"
    op.execute(f"""
        UPDATE system_audit_log
        SET meta = jsonb_set(meta, '{{superseded_attempts}}', (
            SELECT COALESCE(jsonb_agg(
                CASE WHEN jsonb_typeof(item->'stage') = 'number' THEN jsonb_set(item, '{{stage}}', to_jsonb({mapping.format(x=element)})) ELSE item END
                ORDER BY position), '[]'::jsonb)
            FROM jsonb_array_elements(meta->'superseded_attempts') WITH ORDINALITY AS entries(item, position)))
        WHERE action ~ '^Reopened stage' AND jsonb_typeof(meta->'superseded_attempts') = 'array'
    """)
    completed = "CAST(meta->>'completed_stages' AS integer)"
    if mapping is UP:
        op.execute(f"""
            UPDATE system_audit_log SET meta = jsonb_set(meta, '{{completed_stages}}', to_jsonb({completed} + 1))
            WHERE action = 'Imported candidate with documents and stage history'
              AND jsonb_typeof(meta->'completed_stages') = 'number' AND {completed} >= 7
        """)
    else:
        op.execute(f"""
            UPDATE system_audit_log SET meta = jsonb_set(meta, '{{completed_stages}}', to_jsonb({completed} - 1))
            WHERE action = 'Imported candidate with documents and stage history'
              AND jsonb_typeof(meta->'completed_stages') = 'number' AND {completed} >= 8
        """)
    notice = f"CAST(substring(detail from '{NOTICE_NUMBER}') AS integer)"
    op.execute(f"""
        UPDATE system_notifications
        SET detail = regexp_replace(detail, '( . Stage )[0-9]+( . )', '\\1' || CAST(({mapping.format(x=notice)}) AS text) || '\\2')
        WHERE detail ~ '{NOTICE_NUMBER}' AND ({mapping.format(x=notice)}) <> {notice}
    """)


def upgrade():
    if _top(_range_check("ck_application_stage_range")) == 12:
        return  # already converted
    # The PCC stage's own stopping exit. Not used below, so it may be added inside this transaction.
    op.execute("ALTER TYPE closure_reason ADD VALUE IF NOT EXISTS 'pcc_not_cleared'")
    _drop_checks()

    # Stage records and the stage documents, one number at a time, descending: 11 -> 12 first, 7 -> 8 last.
    for n in range(11, 6, -1):
        op.execute(f"UPDATE crm_application_stages SET stage = {n + 1} WHERE stage = {n}")
        op.execute(f"""
            UPDATE document_attachments SET slot = 'stage_{n + 1}_' || substr(slot, {len(f'stage_{n}_') + 1})
            WHERE slot LIKE 'stage\\_{n}\\_%'
        """)
    # The PCC documents a downgrade parked under stage_6_pcc go back to the PCC stage (stage_7_* is free now).
    op.execute("UPDATE document_attachments SET slot = 'stage_7_file' WHERE slot = 'stage_6_pcc'")

    for table, column in (("crm_applications", "current_stage"), ("workflow_tasks", "stage"),
                          ("workflow_daily_work_items", "stage"), ("crm_application_feedback", "stage")):
        op.execute(f"UPDATE {table} SET {column} = {column} + 1 WHERE {column} >= 7")
    _audit_and_notices(UP)

    # Everything that moved past stage 6 (now stage >= 8) has done the PCC: a completed stage-7 record, carrying the PCC fields
    # of its effective stage-6 record when there are any, otherwise empty.
    op.execute(f"""
        INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, outcome, completed_by, completed_at, owner_id)
        SELECT CAST(CAST(md5('0048-pcc:' || a.id) AS uuid) AS text), a.id, 7, 1, carried.data,
               COALESCE(NULLIF(left(carried.data->>'outcome', 60), ''),
                        CASE carried.data->>'pccStatus' WHEN 'received' THEN 'Received' WHEN 'pending' THEN 'PCC pending' ELSE 'Completed manually' END),
               NULL, COALESCE(six.completed_at, later.first_at, a.updated_at), COALESCE(six.owner_id, a.owner_id)
        FROM crm_applications a
        LEFT JOIN (SELECT DISTINCT ON (application_id) application_id, data, completed_at, owner_id
                   FROM crm_application_stages WHERE stage = 6 AND reopened_reason IS NULL
                   ORDER BY application_id, attempt DESC) six ON six.application_id = a.id
        LEFT JOIN (SELECT application_id, min(completed_at) AS first_at FROM crm_application_stages WHERE stage >= 8 GROUP BY application_id) later
               ON later.application_id = a.id
        CROSS JOIN LATERAL (
            SELECT (CASE WHEN jsonb_typeof(six.data->'pcc') = 'object' THEN six.data->'pcc' ELSE '{{}}'::jsonb END)
                   || COALESCE((SELECT jsonb_object_agg(kv.key, kv.value) FROM jsonb_each(COALESCE(six.data, '{{}}'::jsonb)) AS kv
                                WHERE kv.key IN ({PCC_KEYS})), '{{}}'::jsonb) AS data
        ) carried
        WHERE a.current_stage >= 8
          AND NOT EXISTS (SELECT 1 FROM crm_application_stages s WHERE s.application_id = a.id AND s.stage = 7)
    """)
    # The outcome travelled in the nested object only to give the new record its outcome: it is not a PCC answer.
    op.execute("UPDATE crm_application_stages SET data = data - 'outcome' WHERE stage = 7 AND jsonb_exists(data, 'outcome') AND data->>'outcome' = outcome")
    # The PCC fields now live on the PCC record: take them off the stage-6 records of those applications.
    op.execute(f"""
        UPDATE crm_application_stages six SET data = ((six.data - 'pcc') - 'pccStatus') - 'pccAppliedDate' - 'pccReceivedDate'
        WHERE six.stage = 6 AND jsonb_exists_any(six.data, ARRAY['pcc', {PCC_KEYS}])
          AND EXISTS (SELECT 1 FROM crm_application_stages pcc WHERE pcc.application_id = six.application_id AND pcc.stage = 7)
    """)
    _add_checks(12)


def downgrade():
    if _top(_range_check("ck_application_stage_range")) == 11:
        return  # already converted
    _drop_checks()

    # 1. Merge the effective PCC record of each application into its effective stage-6 record (nothing to merge for an empty one).
    op.execute("""
        UPDATE crm_application_stages six
        SET data = six.data || jsonb_build_object('pcc', pcc.data || jsonb_build_object('outcome', pcc.outcome))
        FROM (SELECT DISTINCT ON (application_id) application_id, data, outcome FROM crm_application_stages
              WHERE stage = 7 AND reopened_reason IS NULL ORDER BY application_id, attempt DESC) pcc,
             (SELECT DISTINCT ON (application_id) id, application_id FROM crm_application_stages
              WHERE stage = 6 AND reopened_reason IS NULL ORDER BY application_id, attempt DESC) target
        WHERE target.id = six.id AND target.application_id = pcc.application_id
          AND (pcc.data <> '{}'::jsonb OR pcc.outcome IS DISTINCT FROM 'Completed manually')
    """)
    op.execute("DELETE FROM crm_application_stages WHERE stage = 7")

    # 2. Applications sitting at PCC: closed -> closed at stage 6; open -> stage 7 (Passport) with the PCC draft cleared.
    op.execute("UPDATE crm_applications SET draft = '{}'::jsonb, draft_saved_at = NULL, draft_saved_by = NULL "
               "WHERE current_stage = 7 AND status = 'active' AND draft <> '{}'::jsonb")
    op.execute("UPDATE crm_applications SET current_stage = 6 WHERE current_stage = 7 AND status <> 'active'")
    op.execute("UPDATE crm_applications SET closure_reason = 'unmapped' WHERE closure_reason::text = 'pcc_not_cleared'")

    # 3. PCC tasks, daily-work items and notes: an open PCC task becomes the Passport task; everything else goes back to stage 6.
    op.execute("""
        UPDATE workflow_daily_work_items item SET stage = 6
        WHERE item.stage = 7 AND NOT EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.id = item.task_id
                                             AND t.completed_at IS NULL AND t.cancelled_at IS NULL)
    """)
    op.execute(f"UPDATE workflow_tasks SET title = '{PASSPORT_TASK_TITLE}' "
               f"WHERE stage = 7 AND completed_at IS NULL AND cancelled_at IS NULL AND title = '{PCC_TASK_TITLE}'")
    op.execute("UPDATE workflow_tasks SET stage = 6 WHERE stage = 7 AND (completed_at IS NOT NULL OR cancelled_at IS NOT NULL)")
    op.execute("UPDATE crm_application_feedback SET stage = 6 WHERE stage = 7")

    # 4. The PCC documents are parked under a slot of the old stage 6; then every number >= 8 moves down, ascending.
    op.execute("UPDATE document_attachments SET slot = 'stage_6_pcc' WHERE slot LIKE 'stage\\_7\\_%'")
    for n in range(8, 13):
        op.execute(f"UPDATE crm_application_stages SET stage = {n - 1} WHERE stage = {n}")
        op.execute(f"""
            UPDATE document_attachments SET slot = 'stage_{n - 1}_' || substr(slot, {len(f'stage_{n}_') + 1})
            WHERE slot LIKE 'stage\\_{n}\\_%'
        """)
    for table, column in (("crm_applications", "current_stage"), ("workflow_tasks", "stage"),
                          ("workflow_daily_work_items", "stage"), ("crm_application_feedback", "stage")):
        op.execute(f"UPDATE {table} SET {column} = {column} - 1 WHERE {column} >= 8")
    _audit_and_notices(DOWN)
    _add_checks(11)
