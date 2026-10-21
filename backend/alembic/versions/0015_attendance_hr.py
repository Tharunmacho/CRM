"""Reference attendance policy, approval and durable cover workflows."""
from alembic import op

revision = "0015_attendance_hr"
down_revision = "0014_attendance_ordering"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE staff_attendance_hr_requests (\n\tuser_id VARCHAR(36) NOT NULL, \n\trequest_key VARCHAR(64) NOT NULL, \n\tattendance_date DATE NOT NULL, \n\tkind VARCHAR(30) NOT NULL, \n\tminutes INTEGER DEFAULT 0 NOT NULL, \n\treason TEXT NOT NULL, \n\tstatus VARCHAR(24) DEFAULT 'pending' NOT NULL, \n\tapprovals JSONB DEFAULT '{}'::jsonb NOT NULL, \n\temergency_override BOOLEAN DEFAULT false NOT NULL, \n\tcover_user_id VARCHAR(36), \n\tcover_status VARCHAR(20), \n\tcover_note TEXT, \n\tcover_started_at TIMESTAMP WITH TIME ZONE, \n\tcover_settled_at TIMESTAMP WITH TIME ZONE, \n\tconverted_from_paid_leave BOOLEAN DEFAULT false NOT NULL, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_attendance_hr_request_minutes CHECK (minutes BETWEEN 0 AND 1440), \n\tCONSTRAINT ck_attendance_hr_request_kind CHECK (kind IN ('late','early_exit','early_check_in','official_duty','work_from_home','paid_leave','unpaid_leave','weekly_off','extra_ot')), \n\tCONSTRAINT ck_attendance_hr_request_status CHECK (status IN ('awaiting_cover','pending','approved','rejected')), \n\tFOREIGN KEY(user_id) REFERENCES auth_users (id), \n\tUNIQUE (request_key), \n\tFOREIGN KEY(cover_user_id) REFERENCES auth_users (id)\n)")
    op.execute('CREATE INDEX ix_staff_attendance_hr_requests_attendance_date ON staff_attendance_hr_requests (attendance_date)')
    op.execute('CREATE INDEX ix_staff_attendance_hr_requests_cover_user_id ON staff_attendance_hr_requests (cover_user_id)')
    op.execute('CREATE INDEX ix_staff_attendance_hr_requests_user_id ON staff_attendance_hr_requests (user_id)')
    op.execute("COMMENT ON TABLE staff_attendance_hr_requests IS 'Attendance permissions, leave and overtime with durable request keys, staged approvals and accepted cover lifecycle.'")
    op.execute("CREATE TABLE staff_attendance_weekly_off (\n\tuser_id VARCHAR(36) NOT NULL, \n\tweek_start DATE NOT NULL, \n\tday VARCHAR(12) NOT NULL, \n\tapproved_request_id VARCHAR(36), \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_attendance_weekly_off UNIQUE (user_id, week_start), \n\tCONSTRAINT ck_attendance_weekly_off_day CHECK (day IN ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')), \n\tFOREIGN KEY(user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(approved_request_id) REFERENCES staff_attendance_hr_requests (id)\n)")
    op.execute('CREATE INDEX ix_staff_attendance_weekly_off_user_id ON staff_attendance_weekly_off (user_id)')
    op.execute("COMMENT ON TABLE staff_attendance_weekly_off IS 'One weekly off per employee and Monday-based week, optionally fixed by an approved rotation request.'")
    op.execute('CREATE TABLE staff_attendance_work_timings (\n\tuser_id VARCHAR(36) NOT NULL, \n\teffective_from DATE NOT NULL, \n\tshift JSONB NOT NULL, \n\treason TEXT NOT NULL, \n\tactor_id VARCHAR(36) NOT NULL, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_attendance_work_timing UNIQUE (user_id, effective_from), \n\tFOREIGN KEY(user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(actor_id) REFERENCES auth_users (id)\n)')
    op.execute('CREATE INDEX ix_staff_attendance_work_timings_user_id ON staff_attendance_work_timings (user_id)')
    op.execute("COMMENT ON TABLE staff_attendance_work_timings IS 'Effective-dated recurring shift hours; independent from one-date duty plans.'")
    op.execute('CREATE TABLE staff_attendance_duty_plans (\n\tuser_id VARCHAR(36) NOT NULL, \n\tattendance_date DATE NOT NULL, \n\tshift JSONB NOT NULL, \n\treason TEXT NOT NULL, \n\tactor_id VARCHAR(36) NOT NULL, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_attendance_duty_plan UNIQUE (user_id, attendance_date), \n\tFOREIGN KEY(user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(actor_id) REFERENCES auth_users (id)\n)')
    op.execute('CREATE INDEX ix_staff_attendance_duty_plans_attendance_date ON staff_attendance_duty_plans (attendance_date)')
    op.execute('CREATE INDEX ix_staff_attendance_duty_plans_user_id ON staff_attendance_duty_plans (user_id)')
    op.execute("COMMENT ON TABLE staff_attendance_duty_plans IS 'Explicit single-date working roster overriding a weekly off or holiday, never approved leave.'")
    op.execute("CREATE TABLE staff_attendance_calendar (\n\tuser_id VARCHAR(36) NOT NULL, \n\tattendance_date DATE NOT NULL, \n\tstatus VARCHAR(4) NOT NULL, \n\treason TEXT NOT NULL, \n\tactor_id VARCHAR(36) NOT NULL, \n\trequest_id VARCHAR(36), \n\tconverted_from_paid_leave BOOLEAN DEFAULT false NOT NULL, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_attendance_calendar_day UNIQUE (user_id, attendance_date), \n\tCONSTRAINT ck_attendance_calendar_status CHECK (status IN ('WO','H','PL','UL')), \n\tFOREIGN KEY(user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(actor_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(request_id) REFERENCES staff_attendance_hr_requests (id)\n)")
    op.execute('CREATE INDEX ix_staff_attendance_calendar_attendance_date ON staff_attendance_calendar (attendance_date)')
    op.execute('CREATE INDEX ix_staff_attendance_calendar_user_id ON staff_attendance_calendar (user_id)')
    op.execute("COMMENT ON TABLE staff_attendance_calendar IS 'Explicit holiday, weekly-off and approved paid/unpaid leave calendar. One paid leave day per month is serialized by the employee lock.'")
    op.execute("CREATE TABLE staff_attendance_adjustments (\n\tuser_id VARCHAR(36) NOT NULL, \n\tattendance_date DATE NOT NULL, \n\tkind VARCHAR(24) NOT NULL, \n\treason TEXT NOT NULL, \n\tactor_id VARCHAR(36) NOT NULL, \n\tstatus VARCHAR(4), \n\tminutes INTEGER DEFAULT 0 NOT NULL, \n\taction VARCHAR(12), \n\toccurred_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_attendance_adjustment_kind CHECK (kind IN ('add_punch','time_recovery','status_override')), \n\tCONSTRAINT ck_attendance_adjustment_minutes CHECK (minutes BETWEEN 0 AND 1440), \n\tFOREIGN KEY(user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(actor_id) REFERENCES auth_users (id)\n)")
    op.execute('CREATE INDEX ix_staff_attendance_adjustments_attendance_date ON staff_attendance_adjustments (attendance_date)')
    op.execute('CREATE INDEX ix_staff_attendance_adjustments_user_id ON staff_attendance_adjustments (user_id)')
    op.execute("COMMENT ON TABLE staff_attendance_adjustments IS 'Append-only attendance regularisation and reasoned status overrides; raw attendance sessions remain intact.'")
    op.execute("CREATE TABLE staff_attendance_cover_items (\n\trequest_id VARCHAR(36) NOT NULL, \n\tcandidate_id VARCHAR(36) NOT NULL, \n\tfrom_user_id VARCHAR(36) NOT NULL, \n\tto_user_id VARCHAR(36) NOT NULL, \n\ttask_ids JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsettled_at TIMESTAMP WITH TIME ZONE, \n\toutcome VARCHAR(20), \n\tid VARCHAR(36) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_attendance_cover_item UNIQUE (request_id, candidate_id), \n\tFOREIGN KEY(request_id) REFERENCES staff_attendance_hr_requests (id), \n\tFOREIGN KEY(candidate_id) REFERENCES crm_candidates (id) ON DELETE CASCADE, \n\tFOREIGN KEY(from_user_id) REFERENCES auth_users (id), \n\tFOREIGN KEY(to_user_id) REFERENCES auth_users (id)\n)")
    op.execute('CREATE INDEX ix_staff_attendance_cover_items_candidate_id ON staff_attendance_cover_items (candidate_id)')
    op.execute('CREATE INDEX ix_staff_attendance_cover_items_request_id ON staff_attendance_cover_items (request_id)')
    op.execute("COMMENT ON TABLE staff_attendance_cover_items IS 'Durable temporary leave-cover ownership ledger. Completed or subsequently reassigned work is not returned.'")


def downgrade():
    op.drop_table("staff_attendance_cover_items")
    op.drop_table("staff_attendance_adjustments")
    op.drop_table("staff_attendance_calendar")
    op.drop_table("staff_attendance_duty_plans")
    op.drop_table("staff_attendance_work_timings")
    op.drop_table("staff_attendance_weekly_off")
    op.drop_table("staff_attendance_hr_requests")
