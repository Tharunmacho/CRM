"""Daily candidate quota per recruiter (hourly slots, carry-over) and admin-managed daily goals.

Additive only: three new tables, nothing existing is changed. Every statement is idempotent (IF NOT EXISTS / ON
CONFLICT DO NOTHING), so a repeat or a partial earlier run is safe.

* `workflow_daily_work_items`: which candidate (through its open stage task) was handed to which recruiter on which
  agency day, in which morning/evening slot and with what hourly deadline, plus every lifecycle timestamp
  (allocated, started, overdue, postponed, completed, released) so reports can be computed later.
* `workflow_daily_goals`: the targets, as data. A row without a user is the agency default (seeded: 5 candidates a
  day with 3 in the morning, 2 forwarded to the client, 5 stage moves); a row with a user overrides it for that person.
* `workflow_daily_goal_progress`: the target in force and the achieved count per person, day and metric.

The downgrade drops only these three tables; no task, application or candidate is touched.
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0020_daily_work_quota"
down_revision = "0019_job_categories"
branch_labels = None
depends_on = None

DEFAULT_GOALS = [("candidates", 5, 3), ("forwarded_to_client", 2, None), ("stage_moves", 5, None)]
METRICS = "('candidates','forwarded_to_client','stage_moves')"


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS workflow_daily_work_items (
        id VARCHAR(36) NOT NULL,
        owner_id VARCHAR(36) NOT NULL,
        work_date DATE NOT NULL,
        task_id VARCHAR(36) NOT NULL,
        application_id VARCHAR(36),
        candidate_id VARCHAR(36) NOT NULL,
        stage INTEGER,
        slot_no INTEGER NOT NULL,
        session VARCHAR(10) NOT NULL,
        due_at TIMESTAMP WITH TIME ZONE NOT NULL,
        status VARCHAR(10) DEFAULT 'open' NOT NULL,
        allocated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE,
        completed_at TIMESTAMP WITH TIME ZONE,
        completed_by VARCHAR(36),
        overdue_at TIMESTAMP WITH TIME ZONE,
        postponed_at TIMESTAMP WITH TIME ZONE,
        released_at TIMESTAMP WITH TIME ZONE,
        release_reason VARCHAR(20),
        overdue_notified_at TIMESTAMP WITH TIME ZONE,
        postponed_notified_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT ck_daily_work_session CHECK (session IN ('morning','evening')),
        CONSTRAINT ck_daily_work_status CHECK (status IN ('open','completed','released')),
        CONSTRAINT ck_daily_work_slot CHECK (slot_no >= 1),
        FOREIGN KEY (owner_id) REFERENCES auth_users (id),
        FOREIGN KEY (task_id) REFERENCES workflow_tasks (id) ON DELETE CASCADE,
        FOREIGN KEY (application_id) REFERENCES crm_applications (id) ON DELETE CASCADE,
        FOREIGN KEY (candidate_id) REFERENCES crm_candidates (id) ON DELETE CASCADE,
        FOREIGN KEY (completed_by) REFERENCES auth_users (id)
    )""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_work_task_owner_live ON workflow_daily_work_items (task_id, owner_id) WHERE status <> 'released'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_work_owner_date ON workflow_daily_work_items (owner_id, work_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_daily_work_open ON workflow_daily_work_items (owner_id) WHERE status = 'open'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_daily_work_items_task_id ON workflow_daily_work_items (task_id)")
    op.execute("COMMENT ON TABLE workflow_daily_work_items IS 'Auto-allocated daily candidate quota per recruiter with hourly slot deadlines and every lifecycle timestamp; completion comes from the linked stage task.'")
    for column, text_ in (
        ("work_date", "Agency-timezone day the item was allocated."),
        ("stage", "Stage the candidate was at when allocated."),
        ("slot_no", "1-based place in the day''s quota."),
        ("session", "morning or evening."),
        ("due_at", "Hourly slot deadline."),
        ("status", "open, completed or released."),
        ("started_at", "First draft save or owner remark; set to completed_at if the work was done in one step."),
        ("overdue_at", "The deadline it missed (equals due_at); null when finished on time."),
        ("postponed_at", "Start of the day after work_date, set when it was still unfinished then."),
        ("release_reason", "task_cancelled or reassigned."),
    ):
        op.execute(f"COMMENT ON COLUMN workflow_daily_work_items.{column} IS '{text_}'")

    op.execute("""CREATE TABLE IF NOT EXISTS workflow_daily_goals (
        id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36),
        metric VARCHAR(30) NOT NULL,
        target INTEGER NOT NULL,
        morning_target INTEGER,
        updated_by VARCHAR(36),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_daily_goal_metric CHECK (metric IN """ + METRICS + """),
        CONSTRAINT ck_daily_goal_target CHECK (target BETWEEN 0 AND 200),
        CONSTRAINT ck_daily_goal_morning CHECK (morning_target IS NULL OR (metric = 'candidates' AND morning_target BETWEEN 0 AND target)),
        FOREIGN KEY (user_id) REFERENCES auth_users (id) ON DELETE CASCADE,
        FOREIGN KEY (updated_by) REFERENCES auth_users (id) ON DELETE SET NULL
    )""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_goal_scope_metric ON workflow_daily_goals (COALESCE(user_id, ''), metric)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_daily_goals_user_id ON workflow_daily_goals (user_id)")
    op.execute("COMMENT ON TABLE workflow_daily_goals IS 'Daily staff goals: agency defaults (user_id null) and per-person overrides, edited by the admin.'")
    op.execute("COMMENT ON COLUMN workflow_daily_goals.user_id IS 'Null for the agency default.'")
    op.execute("COMMENT ON COLUMN workflow_daily_goals.morning_target IS 'Candidates metric only: how many of the target are morning slots.'")

    op.execute("""CREATE TABLE IF NOT EXISTS workflow_daily_goal_progress (
        id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36) NOT NULL,
        goal_date DATE NOT NULL,
        metric VARCHAR(30) NOT NULL,
        target INTEGER NOT NULL,
        morning_target INTEGER,
        actual INTEGER DEFAULT 0 NOT NULL,
        morning_actual INTEGER,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_daily_goal_progress UNIQUE (user_id, goal_date, metric),
        CONSTRAINT ck_daily_goal_progress_metric CHECK (metric IN """ + METRICS + """),
        FOREIGN KEY (user_id) REFERENCES auth_users (id) ON DELETE CASCADE
    )""")
    op.execute("COMMENT ON TABLE workflow_daily_goal_progress IS 'Daily goal snapshot per recruiter, agency day and metric: the target in force and the actual count, for weekly reports.'")

    bind = op.get_bind()
    for metric, target, morning in DEFAULT_GOALS:
        bind.execute(sa.text(
            "INSERT INTO workflow_daily_goals (id, user_id, metric, target, morning_target) "
            "SELECT CAST(:id AS varchar), NULL, CAST(:metric AS varchar), CAST(:target AS integer), CAST(:morning AS integer) "
            "WHERE NOT EXISTS (SELECT 1 FROM workflow_daily_goals WHERE user_id IS NULL AND metric = CAST(:metric AS varchar))"),
            {"id": str(uuid.uuid4()), "metric": metric, "target": target, "morning": morning})


def downgrade():
    op.execute("DROP TABLE IF EXISTS workflow_daily_goal_progress")
    op.execute("DROP TABLE IF EXISTS workflow_daily_goals")
    op.execute("DROP TABLE IF EXISTS workflow_daily_work_items")
