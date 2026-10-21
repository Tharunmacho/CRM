"""Explicit staff attendance and transparent browser presence intervals."""
from alembic import op

revision = "0013_staff_attendance"
down_revision = "0012_zoho_mailbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE staff_attendance_sessions (
            user_id VARCHAR(36) NOT NULL,
            checked_in_at TIMESTAMP WITH TIME ZONE NOT NULL,
            checked_out_at TIMESTAMP WITH TIME ZONE,
            checkin_request_key VARCHAR(64) NOT NULL,
            checkout_request_key VARCHAR(64),
            corrected_by VARCHAR(36),
            correction_reason TEXT,
            id VARCHAR(36) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_staff_attendance_time_order CHECK (checked_out_at IS NULL OR checked_out_at >= checked_in_at),
            FOREIGN KEY(user_id) REFERENCES auth_users (id),
            UNIQUE (checkin_request_key),
            UNIQUE (checkout_request_key),
            FOREIGN KEY(corrected_by) REFERENCES auth_users (id)
        )
    """)
    op.execute('CREATE UNIQUE INDEX ix_staff_attendance_one_open ON staff_attendance_sessions (user_id) WHERE checked_out_at IS NULL')
    op.execute('CREATE INDEX ix_staff_attendance_sessions_user_id ON staff_attendance_sessions (user_id)')
    op.execute('CREATE INDEX ix_staff_attendance_user_time ON staff_attendance_sessions (user_id, checked_in_at)')
    op.execute("COMMENT ON TABLE staff_attendance_sessions IS 'Explicit staff check-in/check-out intervals. Attendance is independent of browser presence; corrections are audited.'")
    op.execute("""
        CREATE TABLE staff_presence_intervals (
            user_id VARCHAR(36) NOT NULL,
            client_id VARCHAR(36) NOT NULL,
            auth_session_id VARCHAR(36),
            state VARCHAR(12) NOT NULL,
            started_at TIMESTAMP WITH TIME ZONE NOT NULL,
            ended_at TIMESTAMP WITH TIME ZONE,
            last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
            id VARCHAR(36) NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_staff_presence_state CHECK (state IN ('online','idle')),
            CONSTRAINT ck_staff_presence_time_order CHECK (ended_at IS NULL OR ended_at >= started_at),
            FOREIGN KEY(user_id) REFERENCES auth_users (id),
            FOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE SET NULL
        )
    """)
    op.execute('CREATE INDEX ix_staff_presence_intervals_auth_session_id ON staff_presence_intervals (auth_session_id)')
    op.execute('CREATE INDEX ix_staff_presence_intervals_user_id ON staff_presence_intervals (user_id)')
    op.execute('CREATE UNIQUE INDEX ix_staff_presence_one_open ON staff_presence_intervals (user_id, client_id) WHERE ended_at IS NULL')
    op.execute('CREATE INDEX ix_staff_presence_stale ON staff_presence_intervals (last_seen_at) WHERE ended_at IS NULL')
    op.execute('CREATE INDEX ix_staff_presence_user_time ON staff_presence_intervals (user_id, started_at)')
    op.execute("COMMENT ON TABLE staff_presence_intervals IS 'Visible browser heartbeat intervals for online/idle estimates. Offline time is derived within explicit attendance; multiple tabs are deduplicated.'")


def downgrade():
    op.drop_table("staff_presence_intervals")
    op.drop_table("staff_attendance_sessions")
