"""Keep the employer WhatsApp contact separate from its phone and agency contact.

Revision ID: 0011_company_whatsapp
Revises: 0010_manual_document_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_company_whatsapp"
down_revision = "0010_manual_document_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crm_employer_companies", sa.Column("whatsapp", sa.String(40), nullable=True,
        comment="Employer WhatsApp contact, separate from its main phone and client contact."))


def downgrade():
    op.drop_column("crm_employer_companies", "whatsapp")
