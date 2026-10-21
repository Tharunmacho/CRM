"""Durable attendance retries and ordered browser presence checkpoints."""
from alembic import op

revision = "0014_attendance_ordering"
down_revision = "0013_staff_attendance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE staff_attendance_requests (
            kind VARCHAR(12) NOT NULL,
            request_key VARCHAR(64) NOT NULL,
            user_id VARCHAR(36) NOT NULL REFERENCES auth_users(id),
            attendance_id VARCHAR(36) NOT NULL REFERENCES staff_attendance_sessions(id),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (kind, request_key),
            CONSTRAINT ck_staff_attendance_request_kind CHECK (kind IN ('check_in','check_out'))
        )
    """)
    op.execute("CREATE INDEX ix_staff_attendance_requests_user_id ON staff_attendance_requests (user_id)")
    op.execute("CREATE INDEX ix_staff_attendance_requests_attendance_id ON staff_attendance_requests (attendance_id)")
    op.execute("COMMENT ON TABLE staff_attendance_requests IS 'Durable attendance retry ledger, including requests that return an already-open or already-closed session.'")
    op.execute("""
        INSERT INTO staff_attendance_requests (kind, request_key, user_id, attendance_id, created_at)
        SELECT 'check_in', checkin_request_key, user_id, id, checked_in_at FROM staff_attendance_sessions
        UNION ALL
        SELECT 'check_out', checkout_request_key, user_id, id, checked_out_at FROM staff_attendance_sessions
        WHERE checkout_request_key IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE staff_presence_clients (
            user_id VARCHAR(36) NOT NULL REFERENCES auth_users(id),
            client_id VARCHAR(36) NOT NULL,
            auth_session_id VARCHAR(36) REFERENCES auth_sessions(id) ON DELETE SET NULL,
            last_sequence BIGINT NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, client_id),
            CONSTRAINT ck_staff_presence_client_sequence CHECK (last_sequence >= 1)
        )
    """)
    op.execute("CREATE INDEX ix_staff_presence_clients_auth_session_id ON staff_presence_clients (auth_session_id)")
    op.execute("COMMENT ON TABLE staff_presence_clients IS 'Per-page ordering checkpoint and login binding. Leave requests persist even before the first heartbeat, rejecting delayed older updates.'")
    # Old pages must reload for the sequence/monotonic-age contract. Preserve their
    # historical intervals but bound any previously live interval at the upgrade.
    op.execute("""
        UPDATE staff_presence_intervals
        SET ended_at = GREATEST(started_at, LEAST(now(), last_seen_at + interval '90 seconds'))
        WHERE ended_at IS NULL
    """)


def downgrade():
    op.drop_table("staff_presence_clients")
    op.drop_table("staff_attendance_requests")
