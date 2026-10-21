"""Read-only schema readiness shared by HTTP health and database error handling."""
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory


UPDATE_REQUIRED = (
    "The CRM database needs an update before this version can load. "
    "Ask your administrator to deploy the matching application and database "
    "migrations together."
)


@lru_cache(maxsize=1)
def expected_revisions() -> tuple[str, ...]:
    backend = Path(__file__).resolve().parents[2]
    return tuple(sorted(ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_heads()))


def schema_status(connection) -> dict:
    """Inspect revision metadata only; never create, migrate, or seed a database."""
    expected = expected_revisions()
    current = tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
    return {
        "status": "ok" if current == expected else "migration_required",
        "current": list(current),
        "expected": list(expected),
    }
