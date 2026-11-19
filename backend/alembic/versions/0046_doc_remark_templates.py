"""Date of commencement on a placement, and the admin-managed remark templates of the Tasks page.

Two additive changes, nothing existing is altered, moved or deleted:

1. ``crm_applications.commencement_date`` (DATE, nullable): the day the candidate starts the job at the client, entered by the
   recruiter in the application form and shown as DOC on the Tasks page. Existing placements keep it empty.
2. ``remark_templates`` (id, text, active, sort_order, created_at, updated_at): the ready-made remarks recruiters pick instead of
   typing. ``text`` is unique ignoring case and 1 to 120 characters. Seeded with seven remarks, in this order:
   Visa in progress, Visa stamping in progress, Awaiting Gamca Medical, Offer Accepted, Offer Declined by Candidate,
   PCC Pending, Trial feedback pending. An administrator adds, renames, re-orders and switches templates off in the CRM.

Idempotent (ADD COLUMN IF NOT EXISTS, CREATE TABLE / INDEX IF NOT EXISTS, INSERT .. ON CONFLICT DO NOTHING): running it twice, or
after a partial run, is harmless, and a seeded text an administrator already renamed, switched off or re-ordered is left as it is.

The downgrade drops the table (templates the administrator added are lost; remarks already written keep their text, they never
referenced a template) and the column (the dates entered are lost).
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0046_doc_remark_templates"
down_revision = "0045_more_currencies"
branch_labels = None
depends_on = None

TEMPLATES = [
    "Visa in progress", "Visa stamping in progress", "Awaiting Gamca Medical", "Offer Accepted",
    "Offer Declined by Candidate", "PCC Pending", "Trial feedback pending",
]


def upgrade():
    op.execute("ALTER TABLE crm_applications ADD COLUMN IF NOT EXISTS commencement_date DATE")
    op.execute("COMMENT ON COLUMN crm_applications.commencement_date IS "
               "'Date of commencement: the day the candidate starts the job at the client (set in the application form).'")
    op.execute("""CREATE TABLE IF NOT EXISTS remark_templates (
        id VARCHAR(36) NOT NULL,
        text VARCHAR(120) NOT NULL,
        active BOOLEAN DEFAULT true NOT NULL,
        sort_order INTEGER DEFAULT 0 NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_remark_templates_text CHECK (char_length(btrim(text)) BETWEEN 1 AND 120)
    )""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_remark_templates_text_lower ON remark_templates (lower(text))")
    op.execute("COMMENT ON TABLE remark_templates IS 'Admin-managed remark templates recruiters pick on the Tasks page; "
               "text is unique ignoring case and inactive ones stay out of the picker.'")
    bind = op.get_bind()
    for index, text in enumerate(TEMPLATES, start=1):
        bind.execute(sa.text("INSERT INTO remark_templates (id, text, sort_order) VALUES (:id, :text, :order) ON CONFLICT DO NOTHING"),
                     {"id": str(uuid.uuid4()), "text": text, "order": index * 10})


def downgrade():
    op.execute("DROP TABLE IF EXISTS remark_templates")
    op.execute("ALTER TABLE crm_applications DROP COLUMN IF EXISTS commencement_date")
