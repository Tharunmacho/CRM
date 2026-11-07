"""Worker heartbeats: which reading workers are alive, so screens can say so instead of waiting silently.

One small table keyed by worker id. Every reading worker process upserts its own row every ~10 seconds and removes it
when it stops cleanly; rows older than a day are deleted opportunistically. Nothing reads or writes existing tables.
Additive and idempotent (IF NOT EXISTS), safe to run twice. The downgrade drops the table (it only holds liveness).
"""
from alembic import op

revision = "0029_worker_heartbeats"
down_revision = "0028_extraction_leases"
branch_labels = None
depends_on = None

TABLE_COMMENT = ("Which background reading workers are running right now: each refreshes its own row every few seconds, "
                 "so screens can tell when no reader is alive.")
COLUMNS = {
    "worker_id": "Unique id of one worker process (kind, host, process id and a random suffix).",
    "kind": "document (uploaded files) or email (mailbox attachments).",
    "started_at": "When this worker process started.",
    "seen_at": "When this worker last reported in; a worker not seen for 30 seconds is treated as not running.",
    "threads": "How many documents this worker can read at once.",
    "busy": "How many documents it was reading at the last report.",
    "version": "The code version the worker runs, so an old worker can be told from a current one.",
}


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS crm_worker_heartbeats (
        worker_id TEXT NOT NULL PRIMARY KEY,
        kind TEXT NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
        threads INTEGER NOT NULL DEFAULT 1,
        busy INTEGER NOT NULL DEFAULT 0,
        version TEXT
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_worker_heartbeats_kind_seen ON crm_worker_heartbeats (kind, seen_at)")
    op.execute(f"COMMENT ON TABLE crm_worker_heartbeats IS '{TABLE_COMMENT}'")
    for name, comment in COLUMNS.items():
        op.execute(f"COMMENT ON COLUMN crm_worker_heartbeats.{name} IS '{comment}'")


def downgrade():
    op.execute("DROP TABLE IF EXISTS crm_worker_heartbeats")
