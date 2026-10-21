"""Start the combined CRM service from a Nixpacks image.

Builds never connect to PostgreSQL. Startup requires the matching schema; an
operator can explicitly allow migrations after backing up and stopping old APIs.
"""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("Set DATABASE_URL to the PostgreSQL connection URL before starting.")

    port = os.environ.get("PORT", "8000")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise SystemExit("PORT must be an integer from 1 to 65535.")

    os.environ.setdefault("STATIC_DIR", str(ROOT / "static"))
    os.environ.setdefault("STORAGE_DIR", str(ROOT / "data" / "uploads"))
    if not (Path(os.environ["STATIC_DIR"]) / "index.html").is_file():
        raise SystemExit("Built frontend missing. Complete the Nixpacks build before starting.")
    Path(os.environ["STORAGE_DIR"]).mkdir(parents=True, exist_ok=True)

    # Both Alembic and Uvicorn must import backend/app from this directory.
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

    migration_mode = os.environ.get("RUN_MIGRATIONS_ON_START", "0")
    if migration_mode not in {"0", "1"}:
        raise SystemExit("RUN_MIGRATIONS_ON_START must be 0 or 1.")
    if migration_mode == "1":
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            check=True,
        )

    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from app.core.config import get_settings
    from app.db.session import engine

    expected = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    try:
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    finally:
        engine.dispose()
    if current != expected:
        raise SystemExit(
            "Database migration required. Back up PostgreSQL and uploaded files, "
            "stop older CRM backends using this database, then run "
            "'python -m alembic -c backend/alembic.ini upgrade head' from the "
            "repository root, or set RUN_MIGRATIONS_ON_START=1 for the coordinated "
            "deployment. Remove that setting afterward. No schema changes were "
            "made by this startup check."
        )

    # Monitoring and durable manual uploads are always supervised. Email and
    # WhatsApp workers are selected independently by their server settings.
    os.execv(sys.executable, [sys.executable, "-m", "app.runtime"])



if __name__ == "__main__":
    main()
