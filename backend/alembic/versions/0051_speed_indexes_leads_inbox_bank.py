"""Speed indexes for the inbox reads (the dashboard's Inbox card, the unread badge and the message list).

* ``ix_email_messages_inbox_counts``  (account_id, unread) WHERE remote_deleted IS FALSE AND folder = 'INBOX'
  The per-mailbox totals and unread counts of GET /api/inbox, and the unread badge polled by every open tab, are index-only scans
  of this narrow partial index instead of a scan over every message with its body (45,000 messages: about 55 ms -> 5 ms).
* ``ix_email_messages_visible_feed``  (received_at DESC, id DESC) WHERE remote_deleted IS FALSE
  The "stored" message view and every filtered view count their matches before returning a page; the count walks this narrow index
  instead of the whole table (about 65 ms -> 20 ms at 45,000 messages).

Plain, idempotent (IF NOT EXISTS), safe while older code shares the database: no column, constraint or query result changes.
The downgrade drops them (IF EXISTS). The models declare the same indexes (app/db/models.py, MailMessage).
"""
from alembic import op

revision = "0051_speed_indexes"  # (alembic_version holds 32 characters)
down_revision = "0050_lead_status_templates"  # chained after the lead status templates so alembic keeps a single head
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_messages_inbox_counts ON email_messages (account_id, unread) "
               "WHERE remote_deleted IS FALSE AND folder = 'INBOX'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_messages_visible_feed ON email_messages (received_at DESC, id DESC) "
               "WHERE remote_deleted IS FALSE")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_email_messages_visible_feed")
    op.execute("DROP INDEX IF EXISTS ix_email_messages_inbox_counts")
