"""Clients and reference agencies: the agency's state, each client's own team, the CRM staff allocated to a client.

* `crm_employer_companies.state`      VARCHAR(100) NULL   the agency's state
* `crm_client_contacts.staff_contacts` JSONB NOT NULL DEFAULT '[]'   people on the client's own team (max 50, checked by the API)
* `crm_client_staff`                  (client_id, user_id) primary key, both ON DELETE CASCADE: the CRM users who look after a client
* `crm_reference_agencies`            agencies that refer candidates: contact person, agency name, phone, email, address, state, team

Additive and idempotent (IF NOT EXISTS), so it is safe on a database where it ran partly and when run twice. Existing
rows are untouched: the new columns are nullable or default to an empty list. The downgrade drops the two tables and the
two columns (IF EXISTS), which discards the staff allocations, reference agencies and team lists entered since.
"""
from alembic import op

revision = "0026_client_agency"
down_revision = "0025_client_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE crm_employer_companies ADD COLUMN IF NOT EXISTS state VARCHAR(100)")
    op.execute("ALTER TABLE crm_client_contacts ADD COLUMN IF NOT EXISTS staff_contacts JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("""CREATE TABLE IF NOT EXISTS crm_client_staff (
        client_id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36) NOT NULL,
        PRIMARY KEY (client_id, user_id),
        FOREIGN KEY (client_id) REFERENCES crm_client_contacts (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES auth_users (id) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_crm_client_staff_user_id ON crm_client_staff (user_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS crm_reference_agencies (
        id VARCHAR(36) NOT NULL,
        name VARCHAR(200) NOT NULL,
        agency_name VARCHAR(200) NOT NULL,
        phone VARCHAR(40),
        email VARCHAR(255),
        address TEXT,
        state VARCHAR(100),
        staff_contacts JSONB NOT NULL DEFAULT '[]'::jsonb,
        notes TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id)
    )""")
    op.execute("COMMENT ON TABLE crm_client_staff IS 'CRM staff allocated to a client; matched and incoming candidates for that client go to them.'")
    op.execute("COMMENT ON TABLE crm_reference_agencies IS 'Reference agencies that refer candidates: contact person, agency name, address and their own team.'")
    op.execute("COMMENT ON COLUMN crm_employer_companies.state IS 'The state the employer agency is in.'")
    op.execute("COMMENT ON COLUMN crm_client_contacts.staff_contacts IS 'People on the client''s own team: a list of {name, designation, phone, email}, at most 50.'")
    op.execute("COMMENT ON COLUMN crm_reference_agencies.staff_contacts IS 'People on the agency''s own team: a list of {name, designation, phone, email}, at most 50.'")


def downgrade():
    op.execute("DROP TABLE IF EXISTS crm_reference_agencies")
    op.execute("DROP TABLE IF EXISTS crm_client_staff")
    op.execute("ALTER TABLE crm_client_contacts DROP COLUMN IF EXISTS staff_contacts")
    op.execute("ALTER TABLE crm_employer_companies DROP COLUMN IF EXISTS state")
