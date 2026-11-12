"""Add invoice settings and per-placement immutable service invoices; no historical backfill."""
from alembic import op

revision = "0034_candidate_invoices"
down_revision = "0033_agency_focus"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS crm_invoice_settings (
        id INTEGER PRIMARY KEY, profile JSONB NOT NULL DEFAULT '{}', invoice_prefix VARCHAR(32) NOT NULL DEFAULT '',
        next_number INTEGER, cgst_rate NUMERIC(5,2) NOT NULL DEFAULT 9, sgst_rate NUMERIC(5,2) NOT NULL DEFAULT 9,
        enabled BOOLEAN NOT NULL DEFAULT false, lock_version INTEGER NOT NULL DEFAULT 0)""")
    op.execute("""CREATE TABLE IF NOT EXISTS crm_candidate_invoices (
        id VARCHAR(36) PRIMARY KEY, candidate_id VARCHAR(36) REFERENCES crm_candidates(id) ON DELETE SET NULL,
        application_id VARCHAR(36) REFERENCES crm_applications(id) ON DELETE SET NULL, scope_key VARCHAR(36) NOT NULL DEFAULT '',
        service_fee NUMERIC(12,2), status VARCHAR(32) NOT NULL DEFAULT 'pending_fee', number VARCHAR(48) UNIQUE,
        ticket_attachment_id VARCHAR(36) REFERENCES document_attachments(id) ON DELETE SET NULL,
        attachment_id VARCHAR(36) REFERENCES document_attachments(id) ON DELETE SET NULL, snapshot JSONB,
        issued_at TIMESTAMPTZ, issued_by VARCHAR(36) REFERENCES auth_users(id) ON DELETE SET NULL,
        lock_version INTEGER NOT NULL DEFAULT 0, error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_candidate_invoice_placement UNIQUE(candidate_id, scope_key),
        CONSTRAINT ck_candidate_invoice_fee CHECK(service_fee IS NULL OR (service_fee >= 5000 AND service_fee <= 30000)))""")
    op.execute("COMMENT ON TABLE crm_invoice_settings IS 'Reviewed issuer details and locked invoice number sequence; no invoice is issued before setup is saved.'")
    op.execute("COMMENT ON TABLE crm_candidate_invoices IS 'One automatic service invoice per candidate placement, with pending prerequisites and frozen issued evidence.'")
    op.execute("""CREATE OR REPLACE FUNCTION crm_keep_issued_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'issued' AND (NEW.service_fee IS DISTINCT FROM OLD.service_fee OR
             NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.number IS DISTINCT FROM OLD.number OR
             NEW.issued_at IS DISTINCT FROM OLD.issued_at OR NEW.status IS DISTINCT FROM OLD.status) THEN
            RAISE EXCEPTION 'Issued invoice amounts, number and snapshot cannot be changed';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("DROP TRIGGER IF EXISTS crm_keep_issued_invoice ON crm_candidate_invoices")
    op.execute("CREATE TRIGGER crm_keep_issued_invoice BEFORE UPDATE ON crm_candidate_invoices FOR EACH ROW EXECUTE FUNCTION crm_keep_issued_invoice()")


def downgrade():
    op.execute("DROP TABLE IF EXISTS crm_candidate_invoices")
    op.execute("DROP TABLE IF EXISTS crm_invoice_settings")
    op.execute("DROP FUNCTION IF EXISTS crm_keep_issued_invoice()")
