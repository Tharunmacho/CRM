"""Read-only connection/schema diagnostic: python -m app.db_check."""
import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.core.config import BACKEND_ROOT, get_settings
from app.db.schema_readiness import schema_status


def main() -> int:
    engine = None
    try:
        settings = get_settings()
        url = make_url(settings.database_url)
        source = (
            "DATABASE_URL in process environment"
            if any(key.casefold() == "database_url" for key in os.environ)
            else os.environ.get("CRM_ENV_FILE", str(BACKEND_ROOT / ".env"))
        )
        # Never render the URL: its username, password and query can hold secrets.
        print("[crm] Configuration: " + json.dumps(source), flush=True)
        print("[crm] Database endpoint: " + json.dumps({"host": url.host, "port": url.port or 5432}), flush=True)
        # A separate engine avoids application retry hooks and worker initialization.
        # Even when CRM_AUTO_MIGRATE is set, this command only reads metadata.
        engine = create_engine(
            url, poolclass=NullPool,
            connect_args={
                "connect_timeout": 5,
                "options": "-c default_transaction_read_only=on -c statement_timeout=5000",
            },
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            state = schema_status(connection)
        print("[crm] PostgreSQL connection: OK")
        print("[crm] Schema: " + json.dumps(state))
        if state["status"] != "ok":
            print("[crm] Deploy matching code and migrations before starting the backend.")
            return 1
        return 0
    except Exception as error:
        reason = type(getattr(error, "orig", None) or error.__cause__ or error).__name__
        print(f"[crm] Database check failed ({reason}).")
        print(
            "[crm] Verify DATABASE_URL points to the current database after any hosting migration. "
            "For timeouts/refusals, check the database service, port, firewall and SSH tunnel/VPN. "
            "For authentication failures, verify the current database credentials."
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
