"""Job Requirement Form: positions, general details, several recruiters per requirement and creatable picker names.

* crm_job_requirements.positions: the client's job positions, each with its requirement rows (qualification, maximum age, experience ...).
* crm_job_requirements.general: general employment details (working hours, contract period, place of work, food, accommodation, visa ...).
* crm_job_requirements.owner_ids: every recruiter / MD the requirement is assigned to (a list of user ids); existing rows are back-filled from
  owner_id, which stays and mirrors the first one.
* crm_job_requirement_options: names staff created for the form's pickers (requirement fields and general particulars).
All JSONB lists are empty by default; the old flat ``details`` keys stay as they were. Idempotent (a database built from the models already
has these). Downgrade drops the columns and the table (their content is lost; owner_id keeps the first recruiter).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061_job_requirement_positions"
down_revision = "0060_application_closed_at"
branch_labels = None
depends_on = None

LISTS = (
    ("positions", "The client's job positions: [{id, title, vacancies, salary, requirements: [{id, field, details}]}]; vacancies and salary may be text."),
    ("general", "General employment details: [{id, label, details, remarks}] (working hours, contract period, place of work, food, accommodation, visa ...)."),
    ("owner_ids", "Every recruiter or MD the requirement is assigned to, in order: a list of user ids. owner_id mirrors the first one."),
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("crm_job_requirements")}
    for name, comment in LISTS:
        if name not in existing:
            op.add_column("crm_job_requirements", sa.Column(name, postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), comment=comment))
    op.execute("COMMENT ON COLUMN crm_job_requirements.owner_id IS 'The first recruiter or MD this requirement is assigned to (owner_ids[0]); the creator by default.'")
    op.execute("UPDATE crm_job_requirements SET owner_ids = jsonb_build_array(owner_id) WHERE owner_id IS NOT NULL AND owner_ids = '[]'::jsonb")
    if "crm_job_requirement_options" not in inspector.get_table_names():
        op.create_table(
            "crm_job_requirement_options",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("kind", sa.String(12), nullable=False, comment="requirement (a field under a job position) or particular (a general employment detail)."),
            sa.Column("label", sa.String(100), nullable=False, comment="The name shown in the picker; saved rows keep their own copy of the text."),
            sa.Column("standard", sa.Boolean, nullable=False, server_default=sa.text("false"), comment="Only for particulars: shown as a default row on every new form."),
            sa.Column("sort", sa.Integer, nullable=False, server_default=sa.text("0"), comment="Order within its kind, lowest first."),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("kind IN ('requirement','particular')", name="ck_crm_job_requirement_options_kind"),
            comment="Names staff created for the Job Requirement Form pickers (requirement fields and general particulars), with the ones shown by default.",
        )
        op.create_index("uq_crm_job_requirement_options_kind_label", "crm_job_requirement_options", ["kind", sa.text("lower(label)")], unique=True)


def downgrade():
    op.execute("DROP TABLE IF EXISTS crm_job_requirement_options")
    for name in ("positions", "general", "owner_ids"):
        op.execute(f"ALTER TABLE crm_job_requirements DROP COLUMN IF EXISTS {name}")
    op.execute("COMMENT ON COLUMN crm_job_requirements.owner_id IS 'The recruiter or MD this requirement is assigned to; the creator by default.'")
