"""Append-only actual receipts and supervised corrections, without historical backfill."""
from alembic import op

revision = "0035_invoice_payments"
down_revision = "0034_candidate_invoices"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS crm_invoice_payments (
        id VARCHAR(36) PRIMARY KEY,
        invoice_id VARCHAR(36) NOT NULL REFERENCES crm_candidate_invoices(id) ON DELETE RESTRICT,
        amount NUMERIC(12,2) NOT NULL, paid_on DATE NOT NULL, method VARCHAR(12) NOT NULL,
        reference VARCHAR(200) NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        idempotency_key VARCHAR(100) NOT NULL, source_stage_id VARCHAR(36) UNIQUE,
        source_application_id VARCHAR(36) UNIQUE,
        created_by VARCHAR(36) REFERENCES auth_users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), voided_at TIMESTAMPTZ,
        voided_by VARCHAR(36) REFERENCES auth_users(id) ON DELETE SET NULL, void_reason TEXT,
        CONSTRAINT uq_invoice_payment_request UNIQUE(invoice_id, idempotency_key),
        CONSTRAINT ck_invoice_payment_positive CHECK(amount > 0),
        CONSTRAINT ck_invoice_payment_method CHECK(method IN ('cash','bank','upi','cheque')),
        CONSTRAINT ck_invoice_payment_source CHECK((source_stage_id IS NULL) = (source_application_id IS NULL)),
        CONSTRAINT ck_invoice_payment_void CHECK((voided_at IS NULL AND void_reason IS NULL AND voided_by IS NULL)
            OR (voided_at IS NOT NULL AND void_reason IS NOT NULL AND length(trim(void_reason)) > 0)))""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoice_payments_invoice_date ON crm_invoice_payments(invoice_id, paid_on)")
    op.execute("COMMENT ON TABLE crm_invoice_payments IS 'Actual candidate fee receipts. Financial entries are immutable; supervised voids retain the original receipt and reason.'")
    op.execute("""CREATE OR REPLACE FUNCTION crm_keep_invoice_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Receipt history cannot be deleted; use a supervised void';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id OR NEW.invoice_id IS DISTINCT FROM OLD.invoice_id OR
             NEW.amount IS DISTINCT FROM OLD.amount OR NEW.paid_on IS DISTINCT FROM OLD.paid_on OR
             NEW.method IS DISTINCT FROM OLD.method OR NEW.reference IS DISTINCT FROM OLD.reference OR
             NEW.note IS DISTINCT FROM OLD.note OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key OR
             NEW.source_stage_id IS DISTINCT FROM OLD.source_stage_id OR
             NEW.source_application_id IS DISTINCT FROM OLD.source_application_id OR
             NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Receipt financial details cannot be changed; use a supervised void';
          END IF;
          IF OLD.voided_at IS NOT NULL AND (NEW.voided_at IS DISTINCT FROM OLD.voided_at OR
             NEW.void_reason IS DISTINCT FROM OLD.void_reason) THEN
            RAISE EXCEPTION 'A voided receipt and its reason cannot be changed';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("DROP TRIGGER IF EXISTS crm_keep_invoice_receipt ON crm_invoice_payments")
    op.execute("CREATE TRIGGER crm_keep_invoice_receipt BEFORE UPDATE OR DELETE ON crm_invoice_payments FOR EACH ROW EXECUTE FUNCTION crm_keep_invoice_receipt()")


def downgrade():
    op.execute("DROP TABLE crm_invoice_payments")
    op.execute("DROP FUNCTION crm_keep_invoice_receipt()")
