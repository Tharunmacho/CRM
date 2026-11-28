"""Store the section and manually entered details of an organiser entry."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0062_todo_organiser"
down_revision = "0061_job_requirement_positions"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("personal_todos")}
    for column in (sa.Column("section", sa.String(24)), sa.Column("details", JSONB())):
        if column.name not in columns:
            op.add_column("personal_todos", column)


def downgrade():
    op.drop_column("personal_todos", "details")
    op.drop_column("personal_todos", "section")
