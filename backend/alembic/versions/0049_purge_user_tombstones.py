"""Purge the tombstone rows the older "Delete user" left behind.

The older delete kept an inactive auth_users row named '<name> (former user)' with the email deleted-<id>@deleted.invalid. Delete user
now removes the row for good and re-points the history to the one hidden system account "Former user" (former-user@system.invalid).
The owner wants the old tombstones gone from the database too, so this migration does, for EVERY auth_users row whose email ends with
'@deleted.invalid', exactly what ``app.services.user_admin._repoint_sql`` does and then deletes the row:

* creates the "Former user" account when it is missing (INSERT ... ON CONFLICT (email) DO NOTHING);
* closes the person's open presence interval and attendance session (same statements as the original);
* every foreign key that points at auth_users (found in the catalog, so a table added later is covered): the personal tables in
  _VANISH are DELETEd; the live assignment pointers in _BLANK are SET NULL when nullable; every other column is re-pointed to the
  Former user, and a residual unique clash drops the clashing rows of that person in that table instead of failing;
* deletes the auth_users row.

One anonymous DO block, set-based (no per-row python), in the migration's single transaction. Idempotent: with no tombstone (including an
empty database) it does nothing and creates nothing.

Nothing is imported from the app: the key lists, the 90 second offline window and the Former user name/email below are COPIES of
``_VANISH``, ``_BLANK``, ``staff_activity.OFFLINE_AFTER_SECONDS``, ``FORMER_USER_NAME`` and ``FORMER_USER_EMAIL`` and must be kept in
sync with ``backend/app/services/user_admin.py`` and ``backend/app/db/models.py`` when those change (a migration records what was true
when it ran; a later key added to the app lists does not need to be copied back here).
"""
import sqlalchemy as sa
from alembic import op

revision = "0049_purge_user_tombstones"
down_revision = "0048_pcc_stage"
branch_labels = None
depends_on = None

TOMBSTONE_SUFFIX = "%@deleted.invalid"
FORMER_USER_NAME = "Former user"
FORMER_USER_EMAIL = "former-user@system.invalid"
OFFLINE_AFTER_SECONDS = 90

# Copy of user_admin._VANISH ("table.column"): rows that only made sense while this person could sign in or be scheduled.
VANISH = ("auth_sessions.user_id", "system_notifications.user_id", "personal_todos.owner_id", "email_oauth_states.user_id",
          "crm_client_staff.user_id", "workflow_daily_goals.user_id", "workflow_daily_goal_progress.user_id", "staff_presence_clients.user_id",
          "staff_attendance_duty_plans.user_id", "staff_attendance_work_timings.user_id", "staff_attendance_calendar.user_id",
          "staff_attendance_weekly_off.user_id")
# Copy of user_admin._BLANK: live assignment pointers become "nobody" (never handed to the Former user).
BLANK = ("crm_candidates.owner_id", "crm_leads.owner_id", "crm_lead_batches.assigned_to", "workflow_assignment_cursor.last_user_id")


def _array(items):
    return ", ".join(f"'{item}'" for item in items)


def upgrade():
    # The block has no ":name" sequence (only "::type" casts), so text() finds no bind parameter; SQLAlchemy escapes the '%' signs itself.
    block = f"""
DO $block$
DECLARE
  t record;
  r record;
  fid text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM auth_users WHERE email LIKE '{TOMBSTONE_SUFFIX}') THEN
    RETURN;
  END IF;
  INSERT INTO auth_users (id, name, email, role, active, receives_auto_assign, on_leave)
    VALUES (gen_random_uuid()::text, '{FORMER_USER_NAME}', '{FORMER_USER_EMAIL}', 'staff', false, false, false)
    ON CONFLICT (email) DO NOTHING;
  SELECT id INTO fid FROM auth_users WHERE email = '{FORMER_USER_EMAIL}';
  FOR t IN SELECT id AS old_id FROM auth_users WHERE email LIKE '{TOMBSTONE_SUFFIX}' ORDER BY id LOOP
    UPDATE staff_presence_intervals SET ended_at = greatest(started_at, least(now(), last_seen_at + interval '{OFFLINE_AFTER_SECONDS} seconds'))
      WHERE user_id = t.old_id AND ended_at IS NULL;
    UPDATE staff_attendance_sessions SET checked_out_at = greatest(checked_in_at, now()) WHERE user_id = t.old_id AND checked_out_at IS NULL;
    FOR r IN
      SELECT quote_ident(n.nspname) || '.' || quote_ident(k.relname) AS tbl, a.attname AS col, a.attnotnull AS required,
             k.relname || '.' || a.attname AS key
        FROM pg_constraint c
        JOIN pg_class k ON k.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = k.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
       WHERE c.contype = 'f' AND c.confrelid = 'auth_users'::regclass AND c.conrelid <> 'auth_users'::regclass
    LOOP
      IF r.key = ANY (ARRAY[{_array(VANISH)}]) THEN
        EXECUTE format('DELETE FROM %s WHERE %I = $1', r.tbl, r.col) USING t.old_id;
      ELSIF r.key = ANY (ARRAY[{_array(BLANK)}]) AND NOT r.required THEN
        EXECUTE format('UPDATE %s SET %I = NULL WHERE %I = $1', r.tbl, r.col, r.col) USING t.old_id;
      ELSE
        BEGIN
          EXECUTE format('UPDATE %s SET %I = $1 WHERE %I = $2', r.tbl, r.col, r.col) USING fid, t.old_id;
        EXCEPTION WHEN unique_violation THEN
          EXECUTE format('DELETE FROM %s WHERE %I = $1', r.tbl, r.col) USING t.old_id;
        END;
      END IF;
    END LOOP;
    DELETE FROM auth_users WHERE id = t.old_id;
  END LOOP;
END
$block$"""
    op.execute(sa.text(block))


def downgrade():
    """Nothing to undo: the purge is irreversible by design (the tombstone rows and their ownership are gone for good)."""
