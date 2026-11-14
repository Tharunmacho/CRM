"""A stopping exit at every stage: eight new closure reasons.

Every stage from 1 to 10 can now end with Rejected or Declined (and its own wording: Interview not possible, Passport not
available, Advance not paid, Visa rejected, Visa stamping refused), and stage 11 can end with Not travelling. Each closes the
application exactly like the existing negative exits (Failed prescreening, Rejected at interview, Unfit ...).

* `closure_reason` enum gains `stage_rejected`, `stage_declined`, `interview_not_possible`, `passport_not_available`,
  `advance_not_paid`, `visa_rejected`, `visa_stamping_refused` and `not_travelling`.

Additive and idempotent (IF NOT EXISTS). Existing rows are untouched. PostgreSQL cannot remove a value from an enum type, so the
downgrade turns applications closed with one of the new values into `unmapped` (the neutral "removed from the order" reason)
and leaves the unused values in the type.
"""
from alembic import op

revision = "0043_stage_stop_exits"
down_revision = "0042_offer_closure_reasons"
branch_labels = None
depends_on = None

NEW = ("stage_rejected", "stage_declined", "interview_not_possible", "passport_not_available",
       "advance_not_paid", "visa_rejected", "visa_stamping_refused", "not_travelling")


def upgrade():
    for value in NEW:
        op.execute(f"ALTER TYPE closure_reason ADD VALUE IF NOT EXISTS '{value}'")


def downgrade():
    listed = ", ".join(f"'{value}'" for value in NEW)
    op.execute(f"UPDATE crm_applications SET closure_reason = 'unmapped' WHERE closure_reason::text IN ({listed})")
