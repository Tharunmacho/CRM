"""Offer letter outcomes: two new closure reasons.

Stage 5 (Offer letter) now records an explicit outcome. Agreed moves the candidate on; Declined and Not received stop
them there, closing the application with `offer_declined` or `offer_not_received`.

* `closure_reason` enum gains `offer_declined` and `offer_not_received`.

Additive and idempotent (IF NOT EXISTS). Existing rows are untouched: applications already past stage 5, and any record
written before this revision, keep their outcome. PostgreSQL cannot remove a value from an enum type, so the downgrade turns
applications closed with either value into `unmapped` (the neutral "removed from the order" reason) and leaves the unused
values in the type.
"""
from alembic import op

revision = "0042_offer_closure_reasons"
down_revision = "0041_care_india_traveller_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE closure_reason ADD VALUE IF NOT EXISTS 'offer_declined'")
    op.execute("ALTER TYPE closure_reason ADD VALUE IF NOT EXISTS 'offer_not_received'")


def downgrade():
    op.execute("UPDATE crm_applications SET closure_reason = 'unmapped' WHERE closure_reason::text IN ('offer_declined', 'offer_not_received')")
