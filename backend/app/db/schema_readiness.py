"""Read-only schema readiness shared by HTTP health and database error handling."""
from functools import lru_cache
from pathlib import Path

from alembic import command
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


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def is_behind(current: list[str]) -> bool:
    """True when the database is simply missing newer migrations this code ships.

    Every saved revision must be one this code knows. A database that is unversioned,
    or that carries a revision this code has never heard of (it was upgraded by newer
    code), is never treated as upgradeable: that needs a person, not a startup hook.
    """
    if not current:
        return False
    known = {revision.revision for revision in ScriptDirectory.from_config(_config()).walk_revisions()}
    return all(revision in known for revision in current)


def upgrade_to_head() -> None:
    """Apply pending migrations. Alembic runs each one in a transaction on PostgreSQL."""
    command.upgrade(_config(), "head")


def schema_status(connection) -> dict:
    """Inspect revision metadata only; never create, migrate, or seed a database."""
    expected = expected_revisions()
    current = tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
    return {
        "status": "ok" if current == expected else "migration_required",
        "current": list(current),
        "expected": list(expected),
    }
