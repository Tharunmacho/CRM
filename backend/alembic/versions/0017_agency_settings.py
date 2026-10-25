"""Agency settings and a managed currency list, replacing values compiled into the code.

Additive only: two new tables, no change to any existing table, column or enum. Every statement is
idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING), so running it twice, or after a partial run, is safe.
The seed rows hold exactly the values the application used to carry in its source, so nothing changes on screen
until an administrator edits them under Users > Agency settings.

The downgrade drops only these two tables; edits made in them are lost, nothing else is touched.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_agency_settings"
down_revision = "0016_intake_communications"
branch_labels = None
depends_on = None

# (code, name, sort_order): the codes the order form and the stage-8 advance offered, in their old order.
CURRENCIES = [
    ("INR", "Indian Rupee", 10), ("USD", "US Dollar", 20), ("EUR", "Euro", 30), ("SGD", "Singapore Dollar", 40),
    ("AED", "UAE Dirham", 50), ("SAR", "Saudi Riyal", 60), ("QAR", "Qatari Riyal", 70), ("OMR", "Omani Rial", 80),
    ("KWD", "Kuwaiti Dinar", 90), ("BHD", "Bahraini Dinar", 100), ("GBP", "British Pound", 110),
]
AGENCY = {
    "name": "Care India Overseas",
    "short_name": "Care India",
    "order": "AED",
    "advance": "INR",
    "code": "+91",
    "text": ("Thank you for contacting Care India Overseas. We have received and saved "
             "your candidate documents. Our recruitment team will review them and contact "
             "you when there is a suitable next step."),
}


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS currencies (
        code VARCHAR(3) NOT NULL,
        name VARCHAR(80) NOT NULL,
        symbol VARCHAR(8),
        active BOOLEAN DEFAULT true NOT NULL,
        sort_order INTEGER DEFAULT 0 NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (code),
        CONSTRAINT ck_currencies_code CHECK (code ~ '^[A-Z]{3}$'),
        CONSTRAINT ck_currencies_name CHECK (length(btrim(name)) > 0)
    )""")
    op.execute("COMMENT ON TABLE currencies IS 'Admin-managed currency list for orders and the stage-8 advance; inactive codes stay valid on stored records.'")
    bind = op.get_bind()
    for code, name, order in CURRENCIES:
        bind.execute(sa.text("INSERT INTO currencies (code, name, sort_order) VALUES (:code, :name, :order) ON CONFLICT (code) DO NOTHING"),
                     {"code": code, "name": name, "order": order})

    op.execute("""CREATE TABLE IF NOT EXISTS agency_settings (
        id INTEGER NOT NULL,
        name VARCHAR(120) NOT NULL,
        short_name VARCHAR(60),
        default_order_currency VARCHAR(3) NOT NULL,
        default_advance_currency VARCHAR(3) NOT NULL,
        home_calling_code VARCHAR(5) NOT NULL,
        default_nationality VARCHAR(80),
        acknowledgement_text TEXT NOT NULL,
        updated_by VARCHAR(36),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_agency_settings_singleton CHECK (id = 1),
        CONSTRAINT ck_agency_settings_name CHECK (length(btrim(name)) > 0),
        CONSTRAINT ck_agency_settings_calling_code CHECK (home_calling_code ~ '^\\+[0-9]{1,4}$'),
        FOREIGN KEY (default_order_currency) REFERENCES currencies (code),
        FOREIGN KEY (default_advance_currency) REFERENCES currencies (code),
        FOREIGN KEY (updated_by) REFERENCES auth_users (id)
    )""")
    op.execute("COMMENT ON TABLE agency_settings IS 'Singleton agency profile and defaults: name, default currencies, home calling code and the default acknowledgement text.'")
    bind.execute(sa.text("""INSERT INTO agency_settings
        (id, name, short_name, default_order_currency, default_advance_currency, home_calling_code, default_nationality, acknowledgement_text)
        VALUES (1, :name, :short_name, :order, :advance, :code, NULL, :text) ON CONFLICT (id) DO NOTHING"""), AGENCY)


def downgrade():
    op.execute("DROP TABLE IF EXISTS agency_settings")
    op.execute("DROP TABLE IF EXISTS currencies")
