"""WhatsApp numbers to ignore: whatsapp_ignored_numbers.

An admin keeps a list of numbers the CRM must never work on (staff, agents, clients, spam). A file from such a number is kept as an
"ignored" receipt (visible, never read, never a candidate) and the number gets no automatic acknowledgement. The key is the number as
the CRM compares numbers (digits only, the agency's calling code dropped).
Downgrade drops the table.
"""
import sqlalchemy as sa
from alembic import op

revision = "0059_whatsapp_ignored_numbers"
down_revision = "0058_job_requirements"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: a database built from the models, or an interrupted run, already has the table.
    if "whatsapp_ignored_numbers" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "whatsapp_ignored_numbers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("phone_key", sa.String(40), nullable=False, unique=True,
                  comment="The number as the CRM compares numbers (normalize_phone): digits only, the agency's calling code dropped."),
        sa.Column("label", sa.String(200), nullable=False, server_default=sa.text("''"),
                  comment="Who this is or why it is ignored (staff, agent, client, spam ...)."),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("auth_users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        comment="WhatsApp numbers the CRM never works on: their files are kept as ignored (no reading, no candidate) and they get no automatic reply. An admin keeps the list.",
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS whatsapp_ignored_numbers")
