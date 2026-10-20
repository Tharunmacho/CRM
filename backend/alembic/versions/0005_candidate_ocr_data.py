"""candidate OCR output

The raw Veris OCR output for the resume and passport scanned when a candidate is
entered, kept whole as JSONB because no two resumes come back with the same shape.

Revision ID: 0005_candidate_ocr_data
Revises: 0004_companies
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005_candidate_ocr_data"
down_revision: str | None = "0004_companies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS ocr_data JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE candidates DROP COLUMN IF EXISTS ocr_data")
