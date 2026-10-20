"""initial schema

Generated from app.db.models. Nineteen tables across eight modules: identity, clients and
orders, candidates, applications and stages, tasks and remarks, to-do, ingestion, and
platform plumbing.

Revision ID: 0001_initial
Revises:
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TYPE application_status AS ENUM ('active', 'closed', 'deployed')
        """
    )
    op.execute(
        """
        CREATE TYPE candidate_source AS ENUM ('email', 'whatsapp', 'walk_in', 'referral', 'other')
        """
    )
    op.execute(
        """
        CREATE TYPE candidate_status AS ENUM ('new', 'in_process', 'not_interested', 'deployed', 'needs_review')
        """
    )
    op.execute(
        """
        CREATE TYPE closure_reason AS ENUM ('prescreen_failed', 'not_interested', 'resume_not_received', 'interview_rejected', 'medical_unfit', 'unmapped')
        """
    )
    op.execute(
        """
        CREATE TYPE order_status AS ENUM ('open', 'on_hold', 'filled', 'closed')
        """
    )
    op.execute(
        """
        CREATE TYPE task_kind AS ENUM ('initial_call', 'stage', 'remedical')
        """
    )
    op.execute(
        """
        CREATE TYPE todo_priority AS ENUM ('high', 'normal', 'low')
        """
    )
    op.execute(
        """
        CREATE TYPE user_role AS ENUM ('admin', 'manager', 'staff')
        """
    )
    op.execute(
        """
        CREATE TABLE clients (
        	name VARCHAR(200) NOT NULL, 
        	country VARCHAR(100), 
        	city VARCHAR(100), 
        	industry VARCHAR(120), 
        	contact_person VARCHAR(160), 
        	contact_email VARCHAR(255), 
        	phone VARCHAR(40), 
        	address TEXT, 
        	notes TEXT, 
        	status VARCHAR(20) DEFAULT 'active' NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE designations (
        	title VARCHAR(160) NOT NULL, 
        	category VARCHAR(120), 
        	description TEXT, 
        	required_skills JSONB DEFAULT '[]'::jsonb NOT NULL, 
        	min_experience INTEGER DEFAULT 0 NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ingestion_failures (
        	file_name VARCHAR(255) NOT NULL, 
        	source VARCHAR(255), 
        	reason TEXT, 
        	attempts INTEGER DEFAULT 0 NOT NULL, 
        	failed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	resolved_at TIMESTAMP WITH TIME ZONE, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE mail_accounts (
        	address VARCHAR(255) NOT NULL, 
        	provider VARCHAR(20) NOT NULL, 
        	auto_ingest BOOLEAN DEFAULT true NOT NULL, 
        	last_sync_at TIMESTAMP WITH TIME ZONE, 
        	secrets JSONB, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (address)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE users (
        	name VARCHAR(160) NOT NULL, 
        	email VARCHAR(255) NOT NULL, 
        	phone VARCHAR(40), 
        	role user_role NOT NULL, 
        	password_hash VARCHAR(255), 
        	active BOOLEAN DEFAULT true NOT NULL, 
        	receives_auto_assign BOOLEAN DEFAULT false NOT NULL, 
        	on_leave BOOLEAN DEFAULT false NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (email)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE assignment_pointer (
        	id INTEGER DEFAULT 1 NOT NULL, 
        	last_user_id VARCHAR(36), 
        	PRIMARY KEY (id), 
        	CONSTRAINT ck_single_pointer_row CHECK (id = 1), 
        	FOREIGN KEY(last_user_id) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_log (
        	actor_id VARCHAR(36), 
        	action VARCHAR(200) NOT NULL, 
        	target VARCHAR(300), 
        	entity_type VARCHAR(60), 
        	entity_id VARCHAR(36), 
        	meta JSONB, 
        	at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(actor_id) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_at ON audit_log (at)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_entity_id ON audit_log (entity_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_log_entity_type ON audit_log (entity_type)
        """
    )
    op.execute(
        """
        CREATE TABLE candidates (
        	ref VARCHAR(40) NOT NULL, 
        	name VARCHAR(200) NOT NULL, 
        	phone VARCHAR(40) NOT NULL, 
        	whatsapp VARCHAR(40), 
        	email VARCHAR(255), 
        	dob DATE, 
        	gender VARCHAR(20), 
        	nationality VARCHAR(80), 
        	current_city VARCHAR(120), 
        	languages JSONB DEFAULT '[]'::jsonb NOT NULL, 
        	passport_no VARCHAR(40), 
        	passport_issue DATE, 
        	passport_expiry DATE, 
        	passport_place VARCHAR(120), 
        	designations JSONB DEFAULT '[]'::jsonb NOT NULL, 
        	total_experience INTEGER DEFAULT 0 NOT NULL, 
        	gulf_experience INTEGER DEFAULT 0 NOT NULL, 
        	skills JSONB DEFAULT '[]'::jsonb NOT NULL, 
        	expected_salary NUMERIC(12, 2), 
        	source candidate_source NOT NULL, 
        	source_detail VARCHAR(255), 
        	status candidate_status DEFAULT 'new' NOT NULL, 
        	needs_review BOOLEAN DEFAULT false NOT NULL, 
        	owner_id VARCHAR(36), 
        	resume_text TEXT, 
        	resume_tsv TSVECTOR, 
        	call_called BOOLEAN, 
        	call_interested VARCHAR(20), 
        	call_remarks TEXT, 
        	call_recorded_at TIMESTAMP WITH TIME ZONE, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (ref), 
        	FOREIGN KEY(owner_id) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_candidates_email_lower ON candidates (lower(email))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_candidates_owner_id ON candidates (owner_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_candidates_passport_upper ON candidates (upper(passport_no))
        """
    )
    op.execute(
        """
        CREATE INDEX ix_candidates_phone ON candidates (phone)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_candidates_resume_tsv ON candidates USING gin (resume_tsv)
        """
    )
    op.execute(
        """
        CREATE TABLE notifications (
        	user_id VARCHAR(36) NOT NULL, 
        	kind VARCHAR(40) NOT NULL, 
        	title VARCHAR(200) NOT NULL, 
        	detail TEXT, 
        	link_to VARCHAR(255), 
        	read BOOLEAN DEFAULT false NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_notifications_user_id ON notifications (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE orders (
        	ref VARCHAR(40) NOT NULL, 
        	client_id VARCHAR(36) NOT NULL, 
        	designation_id VARCHAR(36) NOT NULL, 
        	openings INTEGER DEFAULT 1 NOT NULL, 
        	country VARCHAR(100), 
        	city VARCHAR(100), 
        	salary NUMERIC(12, 2), 
        	currency VARCHAR(8) DEFAULT 'AED' NOT NULL, 
        	benefits JSONB DEFAULT '[]'::jsonb NOT NULL, 
        	contract_period VARCHAR(60), 
        	interview_date DATE, 
        	deadline DATE, 
        	status order_status DEFAULT 'open' NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (ref), 
        	FOREIGN KEY(client_id) REFERENCES clients (id), 
        	FOREIGN KEY(designation_id) REFERENCES designations (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_orders_client_id ON orders (client_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_orders_designation_id ON orders (designation_id)
        """
    )
    op.execute(
        """
        CREATE TABLE sessions (
        	user_id VARCHAR(36) NOT NULL, 
        	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_sessions_user_id ON sessions (user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE todo_items (
        	owner_id VARCHAR(36) NOT NULL, 
        	title VARCHAR(300) NOT NULL, 
        	notes TEXT, 
        	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        	priority todo_priority DEFAULT 'normal' NOT NULL, 
        	done BOOLEAN DEFAULT false NOT NULL, 
        	link_to VARCHAR(255), 
        	link_label VARCHAR(160), 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_todo_items_owner_id ON todo_items (owner_id)
        """
    )
    op.execute(
        """
        CREATE TABLE applications (
        	candidate_id VARCHAR(36) NOT NULL, 
        	order_id VARCHAR(36) NOT NULL, 
        	current_stage INTEGER DEFAULT 1 NOT NULL, 
        	status application_status DEFAULT 'active' NOT NULL, 
        	closure_reason closure_reason, 
        	draft JSONB DEFAULT '{}'::jsonb NOT NULL, 
        	draft_saved_at TIMESTAMP WITH TIME ZONE, 
        	draft_saved_by VARCHAR(36), 
        	lock_version INTEGER DEFAULT 0 NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	PRIMARY KEY (id), 
        	CONSTRAINT ck_application_stage_range CHECK (current_stage between 1 and 11), 
        	FOREIGN KEY(candidate_id) REFERENCES candidates (id) ON DELETE CASCADE, 
        	FOREIGN KEY(order_id) REFERENCES orders (id), 
        	FOREIGN KEY(draft_saved_by) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_applications_candidate_id ON applications (candidate_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_applications_order_id ON applications (order_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_one_active_application_per_candidate ON applications (candidate_id) WHERE status = 'active'
        """
    )
    op.execute(
        """
        CREATE TABLE candidate_files (
        	candidate_id VARCHAR(36) NOT NULL, 
        	slot VARCHAR(40) NOT NULL, 
        	version INTEGER NOT NULL, 
        	file_name VARCHAR(255) NOT NULL, 
        	size_kb INTEGER, 
        	object_key VARCHAR(500), 
        	uploaded_by VARCHAR(36), 
        	uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (candidate_id, slot, version), 
        	FOREIGN KEY(candidate_id) REFERENCES candidates (id) ON DELETE CASCADE, 
        	FOREIGN KEY(uploaded_by) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_candidate_files_candidate_id ON candidate_files (candidate_id)
        """
    )
    op.execute(
        """
        CREATE TABLE mail_messages (
        	account_id VARCHAR(36) NOT NULL, 
        	provider_message_id VARCHAR(255) NOT NULL, 
        	from_name VARCHAR(200), 
        	from_address VARCHAR(255), 
        	subject TEXT, 
        	body TEXT, 
        	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        	unread BOOLEAN DEFAULT true NOT NULL, 
        	candidate_id VARCHAR(36), 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (account_id, provider_message_id), 
        	FOREIGN KEY(account_id) REFERENCES mail_accounts (id) ON DELETE CASCADE, 
        	FOREIGN KEY(candidate_id) REFERENCES candidates (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mail_messages_account_id ON mail_messages (account_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mail_received ON mail_messages (received_at)
        """
    )
    op.execute(
        """
        CREATE TABLE application_stages (
        	application_id VARCHAR(36) NOT NULL, 
        	stage INTEGER NOT NULL, 
        	attempt INTEGER DEFAULT 1 NOT NULL, 
        	data JSONB DEFAULT '{}'::jsonb NOT NULL, 
        	outcome VARCHAR(60), 
        	amount NUMERIC(12, 2), 
        	completed_by VARCHAR(36), 
        	completed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	reopened_reason TEXT, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	UNIQUE (application_id, stage, attempt), 
        	CONSTRAINT ck_stage_range CHECK (stage between 1 and 11), 
        	FOREIGN KEY(application_id) REFERENCES applications (id) ON DELETE CASCADE, 
        	FOREIGN KEY(completed_by) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_application_stages_application_id ON application_stages (application_id)
        """
    )
    op.execute(
        """
        CREATE TABLE mail_attachments (
        	message_id VARCHAR(36) NOT NULL, 
        	file_name VARCHAR(255) NOT NULL, 
        	size_kb INTEGER, 
        	kind VARCHAR(20), 
        	object_key VARCHAR(500), 
        	content_hash VARCHAR(64), 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(message_id) REFERENCES mail_messages (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mail_attachments_content_hash ON mail_attachments (content_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_mail_attachments_message_id ON mail_attachments (message_id)
        """
    )
    op.execute(
        """
        CREATE TABLE tasks (
        	kind task_kind NOT NULL, 
        	title VARCHAR(200) NOT NULL, 
        	candidate_id VARCHAR(36) NOT NULL, 
        	application_id VARCHAR(36), 
        	stage INTEGER, 
        	owner_id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	completed_at TIMESTAMP WITH TIME ZONE, 
        	cancelled_at TIMESTAMP WITH TIME ZONE, 
        	escalated BOOLEAN DEFAULT false NOT NULL, 
        	escalated_at TIMESTAMP WITH TIME ZONE, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(candidate_id) REFERENCES candidates (id) ON DELETE CASCADE, 
        	FOREIGN KEY(application_id) REFERENCES applications (id) ON DELETE CASCADE, 
        	FOREIGN KEY(owner_id) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_tasks_application_id ON tasks (application_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_tasks_candidate_id ON tasks (candidate_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_tasks_open_by_age ON tasks (created_at) WHERE completed_at is null and cancelled_at is null
        """
    )
    op.execute(
        """
        CREATE INDEX ix_tasks_owner_id ON tasks (owner_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_tasks_owner_open ON tasks (owner_id, created_at) WHERE completed_at is null and cancelled_at is null
        """
    )
    op.execute(
        """
        CREATE TABLE remarks (
        	task_id VARCHAR(36) NOT NULL, 
        	body TEXT NOT NULL, 
        	author_id VARCHAR(36) NOT NULL, 
        	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        	id VARCHAR(36) NOT NULL, 
        	PRIMARY KEY (id), 
        	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
        	FOREIGN KEY(author_id) REFERENCES users (id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_remarks_created_at ON remarks (created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_remarks_task_id ON remarks (task_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS remarks CASCADE")
    op.execute("DROP TABLE IF EXISTS tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS mail_attachments CASCADE")
    op.execute("DROP TABLE IF EXISTS application_stages CASCADE")
    op.execute("DROP TABLE IF EXISTS mail_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS candidate_files CASCADE")
    op.execute("DROP TABLE IF EXISTS applications CASCADE")
    op.execute("DROP TABLE IF EXISTS todo_items CASCADE")
    op.execute("DROP TABLE IF EXISTS sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS orders CASCADE")
    op.execute("DROP TABLE IF EXISTS notifications CASCADE")
    op.execute("DROP TABLE IF EXISTS candidates CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.execute("DROP TABLE IF EXISTS assignment_pointer CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS mail_accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS ingestion_failures CASCADE")
    op.execute("DROP TABLE IF EXISTS designations CASCADE")
    op.execute("DROP TABLE IF EXISTS clients CASCADE")
    op.execute("DROP TYPE IF EXISTS application_status")
    op.execute("DROP TYPE IF EXISTS candidate_source")
    op.execute("DROP TYPE IF EXISTS candidate_status")
    op.execute("DROP TYPE IF EXISTS closure_reason")
    op.execute("DROP TYPE IF EXISTS order_status")
    op.execute("DROP TYPE IF EXISTS task_kind")
    op.execute("DROP TYPE IF EXISTS todo_priority")
    op.execute("DROP TYPE IF EXISTS user_role")
