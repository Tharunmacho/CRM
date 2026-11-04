"""Daily work items for owned candidates that are not matched to an order yet.

0020 created `workflow_daily_work_items` with one kind of item: a matched candidate's open stage task. Every recruiter
must get their daily quota even when most of their candidates are not matched to an order, so an item can now also
stand for an owned, unmatched candidate worked through the initial call (`kind = 'call'`, no task).

Additive and idempotent (IF NOT EXISTS / DO blocks), so it is safe on a database that already applied 0020, on one
where it ran partly, and when run twice. Existing rows keep working: they become `kind = 'stage'`.

* adds `kind` (default 'stage', checked), makes `task_id` nullable (a stage item still needs it, checked);
* drops the unique one-open-item-per-candidate index if an earlier draft of 0020 created it (the rule is kept by the
  allocation code, and a hard constraint rejected legitimate history), and adds a plain partial index instead;
* refreshes the column comments.

Downgrade removes the new column and constraints and restores NOT NULL on `task_id`. It refuses (with a clear
message) while any `call` item exists, because those rows have no task to restore and deleting them would erase
allocation history that reports read: delete or archive them deliberately first.
"""
from alembic import op

revision = "0023_daily_work_call_items"
down_revision = "0022_form_options"
branch_labels = None
depends_on = None

TABLE = "workflow_daily_work_items"


def _add_check(name: str, expression: str) -> None:
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}' AND conrelid = '{TABLE}'::regclass) THEN
            ALTER TABLE {TABLE} ADD CONSTRAINT {name} CHECK ({expression});
        END IF;
    END $$""")


def upgrade():
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS kind VARCHAR(10) DEFAULT 'stage' NOT NULL")
    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN task_id DROP NOT NULL")
    _add_check("ck_daily_work_kind", "kind IN ('stage','call')")
    _add_check("ck_daily_work_stage_task", "kind = 'call' OR task_id IS NOT NULL")
    op.execute("DROP INDEX IF EXISTS uq_daily_work_one_open_per_candidate")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_daily_work_candidate_open ON {TABLE} (candidate_id) WHERE status = 'open'")
    for column, text_ in (
        ("kind", "stage (an open application-stage task) or call (an owned candidate not yet matched, worked through the initial call)."),
        ("task_id", "The stage task for kind stage; null for kind call."),
        ("stage", "Stage the candidate was at when allocated; null for kind call."),
    ):
        op.execute(f"COMMENT ON COLUMN {TABLE}.{column} IS '{text_}'")


def downgrade():
    calls = op.get_bind().exec_driver_sql(f"SELECT count(*) FROM {TABLE} WHERE kind = 'call'").scalar()
    if calls:
        raise RuntimeError(
            f"Cannot downgrade 0023: {calls} daily work item(s) of kind 'call' have no stage task. "
            f"Delete or archive them first (DELETE FROM {TABLE} WHERE kind = 'call'), then downgrade again.")
    op.execute("DROP INDEX IF EXISTS ix_daily_work_candidate_open")
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_daily_work_stage_task")
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_daily_work_kind")
    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN task_id SET NOT NULL")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS kind")
    op.execute(f"COMMENT ON COLUMN {TABLE}.stage IS 'Stage the candidate was at when allocated.'")
    op.execute(f"COMMENT ON COLUMN {TABLE}.task_id IS NULL")
