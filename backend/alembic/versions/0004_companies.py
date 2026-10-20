"""clients are people; companies are employers

A client is a person, and one person can own several companies. The employer details
move off `clients` onto a new `companies` table, and an order is placed for a company.

Any existing client row is treated as a company and given a person of the same name to
own it, so nothing is lost.

Revision ID: 0004_companies
Revises: 0003_attachments
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004_companies"
down_revision: str | None = "0003_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE companies (
            id VARCHAR(36) NOT NULL,
            client_id VARCHAR(36) NOT NULL,
            name VARCHAR(200) NOT NULL,
            country VARCHAR(100),
            city VARCHAR(100),
            industry VARCHAR(120),
            contact_person VARCHAR(160),
            contact_email VARCHAR(255),
            phone VARCHAR(40),
            address TEXT,
            notes TEXT,
            status VARCHAR(20) DEFAULT 'active' NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(client_id) REFERENCES clients (id)
        )
        """
    )
    op.execute("CREATE INDEX ix_companies_client_id ON companies (client_id)")

    # Every existing client row described an employer. Keep it as a company, owned by
    # a person carrying the same name, so existing orders still resolve.
    op.execute(
        """
        INSERT INTO companies (id, client_id, name, country, city, industry,
                               contact_person, contact_email, phone, address, notes, status,
                               created_at, updated_at)
        SELECT id, id, name, country, city, industry,
               contact_person, contact_email, phone, address, notes, status,
               created_at, updated_at
        FROM clients
        """
    )

    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    op.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS whatsapp VARCHAR(40)")
    op.execute("UPDATE clients SET email = contact_email WHERE email IS NULL")
    op.execute("UPDATE clients SET name = COALESCE(NULLIF(contact_person, ''), name)")
    for column in ("industry", "contact_person", "contact_email", "address"):
        op.execute(f"ALTER TABLE clients DROP COLUMN IF EXISTS {column}")

    op.execute("ALTER TABLE orders ADD COLUMN company_id VARCHAR(36)")
    op.execute("UPDATE orders SET company_id = client_id")
    op.execute("ALTER TABLE orders ALTER COLUMN company_id SET NOT NULL")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT fk_orders_company "
        "FOREIGN KEY (company_id) REFERENCES companies (id)"
    )
    op.execute("CREATE INDEX ix_orders_company_id ON orders (company_id)")
    op.execute("ALTER TABLE orders DROP COLUMN client_id")


def downgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN client_id VARCHAR(36)")
    op.execute("UPDATE orders SET client_id = company_id")
    op.execute("ALTER TABLE orders DROP COLUMN company_id")
    op.execute("DROP TABLE IF EXISTS companies CASCADE")
