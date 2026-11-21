"""Job requirements sheet: crm_job_requirements and crm_job_requirement_fields.

Recruiters keep a spreadsheet-like list of client requirements by hand (DOC date, client, country, the requirement form, an ad flyer,
Active / Closed) and may add columns of their own; the values of those columns sit in ``custom`` keyed by the field id.
Downgrade drops both tables (the uploaded ad files stay in storage).
Each requirement is assigned to an owner (a recruiter or the MD) and records why / when it was closed.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_job_requirements"
down_revision = "0057_traveller_number_format"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: a database built from the models (the first revision creates every table of the day) or an interrupted run already has them.
    if "crm_job_requirement_fields" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "crm_job_requirement_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False, unique=True, comment="Slug of the label."),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("type", sa.String(10), nullable=False, server_default=sa.text("'text'"), comment="text, number, date or yes_no."),
        sa.Column("sort", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("type IN ('text','number','date','yes_no')", name="ck_crm_job_requirement_fields_type"),
        comment="Recruiter-added columns of the Job requirements sheet (label, value type and order).",
    )
    op.create_table(
        "crm_job_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("doc_date", sa.Date, comment="DOC: the date the requirement was received; today (agency time zone) when not sent."),
        sa.Column("client_name", sa.String(200), nullable=False, server_default=sa.text("''")),
        sa.Column("country", sa.String(100), nullable=False, server_default=sa.text("''")),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
                  comment="The requirement form: the 16 sheet items (position, vacancies, placeOfWork, contractPeriod, qualification, agePreference, additionalSkills, basicSalary, workingHours, overtime, otCalculation, food, accommodation, insurance, transport, remarks) and deadline."),
        sa.Column("status", sa.String(10), nullable=False, server_default=sa.text("'active'"), comment="active or closed."),
        sa.Column("custom", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
                  comment="Values of the recruiter-added columns, {field id: value}."),
        sa.Column("ad_object_key", sa.String(500), comment="Storage key of the uploaded ad image or PDF."),
        sa.Column("ad_file_name", sa.String(255)),
        sa.Column("ad_content_type", sa.String(100)),
        sa.Column("ad_size_kb", sa.Integer),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL"),
                  comment="The recruiter or MD this requirement is assigned to; the creator by default."),
        sa.Column("closed_reason", sa.String(20),
                  comment="Why it was closed: filled, client_cancelled, expired or other; empty while active or when skipped."),
        sa.Column("closed_at", sa.DateTime(timezone=True), comment="When the status last became closed; cleared on reopening."),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active','closed')", name="ck_crm_job_requirements_status"),
        sa.CheckConstraint("closed_reason IS NULL OR closed_reason IN ('filled','client_cancelled','expired','other')",
                           name="ck_crm_job_requirements_closed_reason"),
        comment="Job requirements recruiters keep by hand: client, country, requirement form, ad flyer, status and recruiter-added columns.",
    )
    op.create_index("ix_crm_job_requirements_status_doc_date", "crm_job_requirements", ["status", sa.text("doc_date DESC")])
    op.create_index("ix_crm_job_requirements_owner", "crm_job_requirements", ["owner_id"])


def downgrade():
    op.execute("DROP TABLE IF EXISTS crm_job_requirements")
    op.execute("DROP TABLE IF EXISTS crm_job_requirement_fields")
