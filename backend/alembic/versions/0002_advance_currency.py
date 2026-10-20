"""advance currency

The advance is collected from the candidate in India while the order's salary is paid
abroad, so it carries its own currency instead of inheriting the order's.

Revision ID: 0002_advance_currency
Revises: 0001_initial
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002_advance_currency"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE application_stages ADD COLUMN amount_currency VARCHAR(8)")
    # Existing advances predate the choice and were recorded in rupees.
    op.execute(
        "UPDATE application_stages SET amount_currency = 'INR' "
        "WHERE stage = 8 AND amount IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE application_stages DROP COLUMN amount_currency")
