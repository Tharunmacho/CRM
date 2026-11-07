"""Application form v2: recruiter feedback notes and the "changed job" closure reason.

* `crm_application_feedback`  one row per note a recruiter writes on a candidate's application form ("why is this taking
  time"): who, when, which kind (update / delay / issue / client / candidate), an optional reason code, the text and the
  stage the application was on. Both parents delete it (candidate ON DELETE CASCADE, application ON DELETE SET NULL);
  deleting the author keeps the note (author_id SET NULL). Index on (candidate_id, created_at DESC) for the form's list.
* `closure_reason` enum gains `changed_job`: the reason an application is closed when a candidate that has not passed
  prescreening is moved to another job from the application form.

The new document slots (education_certificate, signed_offer, medical, ...) need no schema change: `document_attachments.slot`
is free text.

Additive and idempotent (IF NOT EXISTS), so it is safe on a database where it ran partly and when run twice. Existing rows
are untouched. The downgrade drops the table (the notes are lost) and turns applications closed as `changed_job` into
`unmapped`; PostgreSQL cannot remove a value from an enum type, so the unused `changed_job` value stays in the type.
"""
from alembic import op

revision = "0027_application_form_v2"
down_revision = "0026_client_agency"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE closure_reason ADD VALUE IF NOT EXISTS 'changed_job'")
    op.execute("""CREATE TABLE IF NOT EXISTS crm_application_feedback (
        id VARCHAR(36) NOT NULL,
        candidate_id VARCHAR(36) NOT NULL,
        application_id VARCHAR(36),
        author_id VARCHAR(36),
        kind VARCHAR(20) NOT NULL,
        reason VARCHAR(30),
        body TEXT NOT NULL,
        stage INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_application_feedback_kind CHECK (kind IN ('update', 'delay', 'issue', 'client', 'candidate')),
        CONSTRAINT ck_application_feedback_body CHECK (char_length(body) BETWEEN 1 AND 2000),
        FOREIGN KEY (candidate_id) REFERENCES crm_candidates (id) ON DELETE CASCADE,
        FOREIGN KEY (application_id) REFERENCES crm_applications (id) ON DELETE SET NULL,
        FOREIGN KEY (author_id) REFERENCES auth_users (id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_application_feedback_candidate_created "
               "ON crm_application_feedback (candidate_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_application_feedback_author_id ON crm_application_feedback (author_id, created_at)")
    op.execute("COMMENT ON TABLE crm_application_feedback IS 'Notes recruiters write on a candidate''s application form to explain progress, delays and problems.'")
    for column, text in (
        ("id", "Unique feedback note id."),
        ("candidate_id", "The candidate the note is about; the note is deleted with the candidate."),
        ("application_id", "The application (order) the note was written for, when there was one."),
        ("author_id", "The CRM user who wrote the note; cleared if that account is removed."),
        ("kind", "update, delay, issue, client or candidate."),
        ("reason", "Optional short reason code: waiting_candidate, waiting_client, waiting_documents, waiting_visa, waiting_payment or other."),
        ("body", "The note, 1 to 2000 characters."),
        ("stage", "The stage number the application was on when the note was written."),
        ("created_at", "When the note was written."),
        ("updated_at", "When the note was last edited (edits are allowed for 15 minutes)."),
    ):
        op.execute(f"COMMENT ON COLUMN crm_application_feedback.{column} IS '{text.replace(chr(39), chr(39) * 2)}'")


def downgrade():
    op.execute("DROP TABLE IF EXISTS crm_application_feedback")
    op.execute("UPDATE crm_applications SET closure_reason = 'unmapped' WHERE closure_reason::text = 'changed_job'")
