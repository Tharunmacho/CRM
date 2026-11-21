"""New stage order and a Google review stage: thirteen stages.

Before (twelve stages):  1 Prescreening, 2 Forwarded, 3 Interview scheduling, 4 Interview result, 5 Offer letter, 6 Medical, 7 PCC,
                          8 Passport, 9 Advance payment, 10 Visa arrival + Google review, 11 Visa stamping, 12 Air ticket.
After (thirteen stages): 1 Prescreening, 2 Forwarded, 3 Interview scheduling, 4 Interview result, 5 Offer letter, 6 Advance payment,
                          7 Passport, 8 Medical, 9 PCC, 10 Visa arrival, 11 Google review (new), 12 Visa stamping, 13 Air ticket.

So an OLD stage number maps to a NEW one as: 6 -> 8, 7 -> 9, 8 -> 7, 9 -> 6, 10 -> 10, 11 -> 12, 12 -> 13 (1..5 stay). Every place that
stores a stage number is renumbered through that map:

* ``crm_application_stages.stage`` (stage records and attempts) and its range check ``ck_stage_range`` (1..13)
* ``crm_applications.current_stage`` and ``ck_application_stage_range`` (1..13) - see "In-flight candidates" below
* ``workflow_tasks.stage``, ``workflow_daily_work_items.stage``, ``crm_application_feedback.stage``
* ``document_attachments.slot`` of the stage documents (``stage_9_receipt`` -> ``stage_6_receipt``, ``stage_8_copy`` -> ``stage_7_copy``,
  ``stage_7_file`` -> ``stage_9_file``, ``stage_11_file`` -> ``stage_12_file``, ``stage_12_file`` -> ``stage_13_file``)
* the audit trail (``system_audit_log.action`` "Recorded stage 8: Collected", ``meta.stage``, ``meta.superseded_attempts[].stage``,
  ``meta.completed_stages`` of a bulk import) and the stage shown in stored escalation notices (``system_notifications.detail``)

Stage records and attachment slots are renumbered in two steps (everything moves out of the way first), so the unique keys
``(application_id, stage, attempt)`` and ``(entity_type, entity_id, slot, version)`` can never collide halfway.

Google review. Every application that had already got past the visa arrival (old current stage >= 11, closed and deployed ones included)
gets a completed stage-11 record, attempt 1, carrying ``googleReview: true`` ("Obtained") when its effective visa record had the tick,
otherwise empty ("Completed manually"). The tick is COPIED, not moved: the visa stage still accepts ``googleReview`` and the Tasks list
reads both places, so an application still at the visa stage loses nothing.

In-flight candidates. Closed and deployed applications keep the stage they stopped at (renumbered). An ACTIVE application that was
working on one of the four reordered stages (old 6..9) is put on the first of the new stages 6..9 that it has NOT completed yet, so
nobody is told an advance or a passport is done when it never was; stages it has already completed are kept and are skipped when it moves
on (the stage screen moves to the next stage without a completed record). Its saved draft is cleared only when it moves to a different
stage, and its open task and daily-work item are moved to the new stage (and given that stage's title).

Fast and safe: set-based SQL only (no per-row python), one transaction (alembic's), an empty database is a no-op, and running the body
again on converted data does nothing (it detects the 1..13 range check).

Downgrade reverses all of this: the ticked Google review is copied back onto the visa record, the Google review records are deleted
(their documents are parked under ``stage_10_review``), numbers go back through the reverse map, and an active application on a reordered
stage is put on the first of the old stages 6..9 (Medical, PCC, Passport, Advance) it has not completed; one at the Google review
continues at Visa stamping.
"""
import re

from alembic import op

revision = "0056_stage_order_google_review"
down_revision = "0055_remark_kind"
branch_labels = None
depends_on = None

TITLES = {6: "Collect the advance", 7: "Collect original passport", 8: "Complete the medical", 9: "Collect the police clearance (PCC)"}
REVIEW_TASK_TITLE = "Get the Google review"
STAMP_TASK_TITLE = "Get the visa stamped"
BLOCK_TITLES = "('Collect the advance', 'Collect original passport', 'Complete the medical', 'Collect the police clearance (PCC)')"
AUDIT_ACTION = r"^(Recorded|Saved|Reopened) stage [0-9]+"
AUDIT_NUMBER = r"^[A-Za-z]+ stage ([0-9]+)"  # (no "(?:": a colon followed by a name reads as a bind parameter)
NOTICE_NUMBER = r" . Stage ([0-9]+) . "

# old number -> new number, and back (the Google review, new stage 11, folds into the visa stage on the way down)
UP = "CASE {x} WHEN 6 THEN 8 WHEN 7 THEN 9 WHEN 8 THEN 7 WHEN 9 THEN 6 WHEN 11 THEN 12 WHEN 12 THEN 13 ELSE {x} END"
DOWN = "CASE {x} WHEN 8 THEN 6 WHEN 9 THEN 7 WHEN 7 THEN 8 WHEN 6 THEN 9 WHEN 12 THEN 11 WHEN 13 THEN 12 WHEN 11 THEN 10 ELSE {x} END"
UP_PAIRS = ((6, 8), (7, 9), (8, 7), (9, 6), (11, 12), (12, 13))
DOWN_PAIRS = ((8, 6), (9, 7), (7, 8), (6, 9), (12, 11), (13, 12))
SLOT_UP = ((7, 9), (8, 7), (9, 6), (11, 12), (12, 13))     # stages whose documents have slots
SLOT_DOWN = ((9, 7), (7, 8), (6, 9), (12, 11), (13, 12))

# A stage the application has really been through: a live record that is not the "Unfit: reapplying" retest marker.
DONE = ("EXISTS (SELECT 1 FROM crm_application_stages s WHERE s.application_id = a.id AND s.stage = {n} "
        "AND (s.reopened_reason IS NULL OR s.reopened_reason = '') AND s.outcome IS DISTINCT FROM 'Unfit: reapplying')")


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


def _renumber_records(pairs):
    """Stage records: everything that moves goes to number + 100 first, then to its final number (no unique-key collisions)."""
    numbers = ", ".join(str(old) for old, _new in pairs)
    op.execute(f"UPDATE crm_application_stages SET stage = stage + 100 WHERE stage IN ({numbers})")
    for old, new in pairs:
        op.execute(f"UPDATE crm_application_stages SET stage = {new} WHERE stage = {old + 100}")


def _renumber_slots(pairs):
    """Stage documents: stage_<old>_<field> -> stage_<new>_<field>, through a temporary prefix."""
    for old, new in pairs:
        op.execute(f"""
            UPDATE document_attachments SET slot = 'tmpstage_{new}_' || substr(slot, {len(f'stage_{old}_') + 1})
            WHERE slot LIKE 'stage\\_{old}\\_%'
        """)
    op.execute("UPDATE document_attachments SET slot = substr(slot, 4) WHERE slot LIKE 'tmpstage\\_%'")


def _audit_and_notices(mapping):
    """The stage numbers written in the audit trail and in stored escalation notices, through `mapping` (a CASE template)."""
    number = f"CAST(substring(action from '{AUDIT_NUMBER}') AS integer)"
    op.execute(f"""
        UPDATE system_audit_log
        SET action = regexp_replace(action, '{AUDIT_ACTION}', '\\1 stage ' || CAST(({mapping.format(x=number)}) AS text))
        WHERE action ~ '{AUDIT_ACTION}' AND ({mapping.format(x=number)}) <> {number}
    """)
    value = "CAST(meta->>'stage' AS integer)"
    op.execute(f"""
        UPDATE system_audit_log
        SET meta = jsonb_set(meta, '{{stage}}', to_jsonb({mapping.format(x=value)}))
        WHERE action ~ '{AUDIT_ACTION}' AND jsonb_typeof(meta->'stage') = 'number' AND ({mapping.format(x=value)}) <> {value}
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
    notice = f"CAST(substring(detail from '{NOTICE_NUMBER}') AS integer)"
    op.execute(f"""
        UPDATE system_notifications
        SET detail = regexp_replace(detail, '( . Stage )[0-9]+( . )', '\\1' || CAST(({mapping.format(x=notice)}) AS text) || '\\2')
        WHERE detail ~ '{NOTICE_NUMBER}' AND ({mapping.format(x=notice)}) <> {notice}
    """)


def _completed_stages(up):
    """A bulk import wrote how many stages it recorded: those at or past the visa stage count the Google review (up) or not (down)."""
    completed = "CAST(meta->>'completed_stages' AS integer)"
    low, delta = (11, "+ 1") if up else (12, "- 1")
    op.execute(f"""
        UPDATE system_audit_log SET meta = jsonb_set(meta, '{{completed_stages}}', to_jsonb({completed} {delta}))
        WHERE action = 'Imported candidate with documents and stage history'
          AND jsonb_typeof(meta->'completed_stages') = 'number' AND {completed} >= {low}
    """)


def _move_open_work(titles_case):
    """Open task and daily-work item of the applications in `_moved` go to their new stage (and the new stage's title)."""
    op.execute(f"""
        UPDATE workflow_tasks t
        SET stage = m.new_cur,
            title = CASE WHEN t.title IN {BLOCK_TITLES} OR t.title IN ('{REVIEW_TASK_TITLE}', '{STAMP_TASK_TITLE}') THEN ({titles_case}) ELSE t.title END
        FROM _moved m
        WHERE t.application_id = m.id AND t.completed_at IS NULL AND t.cancelled_at IS NULL AND t.stage IS NOT NULL
    """)
    op.execute("""
        UPDATE workflow_daily_work_items item SET stage = m.new_cur
        FROM _moved m, workflow_tasks t
        WHERE t.id = item.task_id AND t.application_id = m.id AND t.completed_at IS NULL AND t.cancelled_at IS NULL
    """)


def upgrade():
    if _top(_range_check("ck_application_stage_range")) == 13:
        return  # already converted
    _drop_checks()
    op.execute("CREATE TEMP TABLE _old_cur ON COMMIT DROP AS SELECT id, current_stage AS cur, status::text AS status FROM crm_applications")

    # Stage records and the stage documents, renumbered through the map.
    _renumber_records(UP_PAIRS)
    _renumber_slots(SLOT_UP)
    # Review documents a downgrade parked go back to the review stage.
    op.execute("UPDATE document_attachments SET slot = 'stage_11_file' WHERE slot = 'stage_10_review'")

    # Google review records: everyone who got past the visa arrival.
    op.execute("""
        INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, outcome, completed_by, completed_at, owner_id)
        SELECT CAST(CAST(md5('0056-review:' || a.id) AS uuid) AS text), a.id, 11, 1,
               CASE WHEN visa.data->>'googleReview' = 'true' THEN jsonb_build_object('googleReview', true) ELSE '{}'::jsonb END,
               CASE WHEN visa.data->>'googleReview' = 'true' THEN 'Obtained' ELSE 'Completed manually' END,
               NULL, COALESCE(visa.completed_at, stamp.completed_at, a.updated_at), COALESCE(visa.owner_id, stamp.owner_id, a.owner_id)
        FROM crm_applications a
        JOIN _old_cur o ON o.id = a.id AND o.cur >= 11
        LEFT JOIN (SELECT DISTINCT ON (application_id) application_id, data, completed_at, owner_id
                   FROM crm_application_stages WHERE stage = 10 AND (reopened_reason IS NULL OR reopened_reason = '')
                   ORDER BY application_id, attempt DESC) visa ON visa.application_id = a.id
        LEFT JOIN (SELECT DISTINCT ON (application_id) application_id, completed_at, owner_id
                   FROM crm_application_stages WHERE stage = 12 AND (reopened_reason IS NULL OR reopened_reason = '')
                   ORDER BY application_id, attempt DESC) stamp ON stamp.application_id = a.id
        WHERE NOT EXISTS (SELECT 1 FROM crm_application_stages s WHERE s.application_id = a.id AND s.stage = 11)
    """)

    # Where each application is now. Active ones that were on a reordered stage (old 6..9) go to their first unfinished one.
    op.execute(f"""
        CREATE TEMP TABLE _moved ON COMMIT DROP AS
        SELECT a.id, o.cur AS old_cur, ({UP.format(x='o.cur')}) AS mapped_cur,
               COALESCE((SELECT min(n) FROM generate_series(6, 9) AS n WHERE NOT ({DONE.format(n='n')})), 10) AS new_cur
        FROM crm_applications a JOIN _old_cur o ON o.id = a.id
        WHERE o.status = 'active' AND o.cur BETWEEN 6 AND 9
    """)
    op.execute("""
        UPDATE crm_applications a SET current_stage = m.new_cur FROM _moved m WHERE a.id = m.id
    """)
    op.execute(f"""
        UPDATE crm_applications a SET current_stage = ({UP.format(x='o.cur')}) FROM _old_cur o
        WHERE o.id = a.id AND NOT EXISTS (SELECT 1 FROM _moved m WHERE m.id = a.id)
    """)
    op.execute("""
        UPDATE crm_applications a SET draft = '{}'::jsonb, draft_saved_at = NULL, draft_saved_by = NULL
        FROM _moved m WHERE a.id = m.id AND m.new_cur <> m.mapped_cur AND a.draft <> '{}'::jsonb
    """)

    # Tasks, daily-work items and notes: renumbered through the map; then the open work of moved applications goes to its new stage.
    for table in ("workflow_tasks", "workflow_daily_work_items", "crm_application_feedback"):
        op.execute(f"UPDATE {table} SET stage = ({UP.format(x='stage')}) WHERE stage IS NOT NULL AND ({UP.format(x='stage')}) <> stage")
    _move_open_work("CASE m.new_cur WHEN 6 THEN '{}' WHEN 7 THEN '{}' WHEN 8 THEN '{}' WHEN 9 THEN '{}' ELSE t.title END".format(*(TITLES[n] for n in (6, 7, 8, 9))))

    _audit_and_notices(UP)
    _completed_stages(True)
    _add_checks(13)


def downgrade():
    if _top(_range_check("ck_application_stage_range")) == 12:
        return  # already converted
    _drop_checks()
    op.execute("CREATE TEMP TABLE _new_cur ON COMMIT DROP AS SELECT id, current_stage AS cur, status::text AS status FROM crm_applications")

    # 1. A ticked Google review goes back onto the effective visa record (unless that already says something); review records go.
    op.execute("""
        UPDATE crm_application_stages visa
        SET data = visa.data || jsonb_build_object('googleReview', true)
        FROM (SELECT DISTINCT ON (application_id) application_id FROM crm_application_stages
              WHERE stage = 11 AND (reopened_reason IS NULL OR reopened_reason = '') AND data->>'googleReview' = 'true'
              ORDER BY application_id, attempt DESC) review,
             (SELECT DISTINCT ON (application_id) id, application_id FROM crm_application_stages
              WHERE stage = 10 AND (reopened_reason IS NULL OR reopened_reason = '') ORDER BY application_id, attempt DESC) target
        WHERE target.id = visa.id AND target.application_id = review.application_id AND NOT jsonb_exists(visa.data, 'googleReview')
    """)
    op.execute("DELETE FROM crm_application_stages WHERE stage = 11")
    op.execute("UPDATE document_attachments SET slot = 'stage_10_review' WHERE slot LIKE 'stage\\_11\\_%'")

    # 2. Records and documents back to the old numbers.
    _renumber_records(DOWN_PAIRS)
    _renumber_slots(SLOT_DOWN)

    # 3. Where each application is: an active one on a reordered stage (new 6..9) goes to its first unfinished OLD stage (Medical, PCC,
    #    Passport, Advance); one at the Google review continues at Visa stamping.
    old_done = ("EXISTS (SELECT 1 FROM crm_application_stages s WHERE s.application_id = a.id AND s.stage = {n} "
                "AND (s.reopened_reason IS NULL OR s.reopened_reason = '') AND s.outcome IS DISTINCT FROM 'Unfit: reapplying')")
    op.execute(f"""
        CREATE TEMP TABLE _moved ON COMMIT DROP AS
        SELECT a.id, o.cur AS new_cur_before,
               CASE WHEN o.cur = 11 THEN 11
                    ELSE COALESCE((SELECT n FROM unnest(ARRAY[6, 7, 8, 9]) WITH ORDINALITY AS t(n, pos)
                                   WHERE NOT ({old_done.format(n='n')}) ORDER BY pos LIMIT 1), 10) END AS new_cur,
               ({DOWN.format(x='o.cur')}) AS mapped_cur
        FROM crm_applications a JOIN _new_cur o ON o.id = a.id
        WHERE o.status = 'active' AND (o.cur BETWEEN 6 AND 9 OR o.cur = 11)
    """)
    op.execute("""
        UPDATE crm_applications a SET current_stage = m.new_cur FROM _moved m WHERE a.id = m.id
    """)
    op.execute(f"""
        UPDATE crm_applications a SET current_stage = ({DOWN.format(x='o.cur')}) FROM _new_cur o
        WHERE o.id = a.id AND NOT EXISTS (SELECT 1 FROM _moved m WHERE m.id = a.id)
    """)
    op.execute("""
        UPDATE crm_applications a SET draft = '{}'::jsonb, draft_saved_at = NULL, draft_saved_by = NULL
        FROM _moved m WHERE a.id = m.id AND m.new_cur <> m.mapped_cur AND a.draft <> '{}'::jsonb
    """)

    # 4. Tasks, daily-work items and notes through the reverse map; then the open work of moved applications to its stage and title.
    for table in ("workflow_tasks", "workflow_daily_work_items", "crm_application_feedback"):
        op.execute(f"UPDATE {table} SET stage = ({DOWN.format(x='stage')}) WHERE stage IS NOT NULL AND ({DOWN.format(x='stage')}) <> stage")
    _move_open_work(f"CASE m.new_cur WHEN 6 THEN '{TITLES[8]}' WHEN 7 THEN '{TITLES[9]}' WHEN 8 THEN '{TITLES[7]}' WHEN 9 THEN '{TITLES[6]}' "
                    f"WHEN 11 THEN '{STAMP_TASK_TITLE}' ELSE t.title END")

    _audit_and_notices(DOWN)
    _completed_stages(False)
    _add_checks(12)
