"""OPT-IN fast `alembic upgrade head` for the disposable test database: clone a once-migrated template.

Status (measured 2026-11-23 on the Windows dev machine, PostgreSQL 17, scratch server with fsync off, machine busy):
the real migrations take 2.2-4.3 s per call; the clone takes 1.5-3 s (DROP DATABASE ... FORCE 0.2-1 s + CREATE DATABASE ... TEMPLATE
of the 12 MB template 1.1-2.4 s; STRATEGY = FILE_COPY is not meaningfully faster). A side-by-side run of ten test files took 749 s
with the real migrations and 960 s with the clone, so the shortcut is OFF by default. It is kept, tested and proven equivalent
(tests/test_template_database.py) for machines where copying a database is cheaper than migrating, e.g. fast storage or many more
migrations later: set CRM_TEST_TEMPLATE=1 to use it.

Why it exists: almost every integration test starts from an empty ``public`` schema and runs ~60 alembic migrations
(`_migrate(engine, "head")` in test_database_naming.py).

How it works
------------
* With CRM_TEST_TEMPLATE=1, the first time a test asks for ``head`` on an EMPTY database, a template database named
  ``<TEST_DATABASE_URL database>_tpl`` (so two pytest processes with different test databases never collide) is created
  and migrated to head ONCE with the real alembic path. A stale template left by a crashed session is dropped and rebuilt.
* Every later request is answered by ``DROP DATABASE <test db> WITH (FORCE)`` + ``CREATE DATABASE <test db> TEMPLATE <tpl>``.
  The engine that asked is disposed first, and a class-level pool listener discards any other pooled connection to the replaced
  database, so no test sees a stale connection.
* Only ``upgrade head`` on an empty ``public`` schema is short-cut. Upgrades from an older revision, downgrades, ``base``,
  calls with an external ``connection`` and ``fresh=True`` (used by the tests that exist to prove the migrations themselves)
  keep the real alembic path.
* Every name is checked against the same rules as ``_guard_test_url``: a loopback server and ``crm_schema_test*`` names only.
* CRM_TEST_NO_TEMPLATE=1 always forces the real migrations. Any error while building or cloning falls back to the real
  migrations and switches the template off for the rest of the session.
* The template is dropped when the pytest process exits (``atexit``).
"""

from __future__ import annotations

import atexit
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, event, exc, pool as sa_pool, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

BACKEND = Path(__file__).resolve().parents[1]
NAME = re.compile(r"crm_schema_test[a-zA-Z0-9_]*")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_state = {"epoch": 0, "disabled": False, "built": {}, "warned": False, "reason": ""}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    """OPT-IN: CRM_TEST_TEMPLATE=1 turns the clone on; CRM_TEST_NO_TEMPLATE=1 always wins and forces the real migrations."""
    return not _state["disabled"] and _flag("CRM_TEST_TEMPLATE") and not _flag("CRM_TEST_NO_TEMPLATE")


def _warn(message: str) -> None:
    _state["reason"] = message
    if not _state["warned"]:
        _state["warned"] = True
        print(f"[tests] template database off for this session: {message}", file=sys.stderr)


# ---- stale-connection guard: a replaced database invalidates every older pooled connection (all engines in the process) ----
def _remember_epoch(dbapi_connection, record):
    record.info["template_epoch"] = _state["epoch"]


def _discard_stale(dbapi_connection, record, proxy):
    if record.info.get("template_epoch", 0) != _state["epoch"]:
        raise exc.DisconnectionError("the test database was replaced; reconnecting")


if not getattr(sa_pool.Pool, "_crm_template_guard", False):
    event.listen(sa_pool.Pool, "connect", _remember_epoch)
    event.listen(sa_pool.Pool, "checkout", _discard_stale)
    sa_pool.Pool._crm_template_guard = True


def _target(guard):
    raw = os.environ.get("TEST_DATABASE_URL")
    return guard(raw) if raw else None


def _admin(url):
    """An autocommit engine on the maintenance database of the SAME loopback server (never any other host)."""
    engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool,
                           connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        server = connection.execute(text("SELECT host(inet_server_addr())")).scalar()
    if server not in {"127.0.0.1", "::1"}:
        engine.dispose()
        raise RuntimeError("template databases are only managed on a loopback server")
    return engine


def _quoted(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError(f"refusing to manage a database named {name!r}")
    return f'"{name}"'


def _drop(admin, name: str) -> None:
    with admin.connect() as connection:
        try:
            connection.execute(text(f"DROP DATABASE IF EXISTS {_quoted(name)} WITH (FORCE)"))
        except exc.ProgrammingError:  # PostgreSQL < 13 has no WITH (FORCE)
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"), {"n": name})
            connection.execute(text(f"DROP DATABASE IF EXISTS {_quoted(name)}"))


def _template_name(url) -> str:
    return f"{url.database}_tpl"


def _ensure_template(url, migrate, guard):
    key = (url.host, url.port, url.database)
    if key in _state["built"]:
        return _template_name(url)
    admin = _admin(url)
    template = _template_name(url)
    try:
        _drop(admin, template)  # a crashed earlier session may have left a stale (or half-built) template
        with admin.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {_quoted(template)}"))
    finally:
        admin.dispose()
    engine = create_engine(url.set(database=template), poolclass=NullPool,
                           connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    try:
        with engine.begin() as connection:  # the SAME reset the schema_database fixture performs before migrating
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        migrate(engine, "head", fresh=True)
    finally:
        engine.dispose()
    _state["built"][key] = template
    atexit.register(_drop_template, url)
    return template


def _drop_template(url) -> None:
    try:
        admin = _admin(url)
        try:
            _drop(admin, _template_name(url))
        finally:
            admin.dispose()
    except Exception:  # pragma: no cover - best effort at interpreter exit
        pass


def _is_empty(engine) -> bool:
    with engine.connect() as connection:
        count = connection.execute(text(
            "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public'")).scalar()
    return count == 0


def clone_head(engine, migrate, guard) -> bool:
    """Make `engine`'s (empty) test database equal to a migrated head by cloning the template. False = use real migrations."""
    if not enabled():
        return False
    try:
        url = _target(guard)
        if url is None:
            return False
        mine = engine.url
        if (mine.drivername, mine.host, mine.port, mine.database) != (url.drivername, url.host, url.port, url.database):
            return False
        if not NAME.fullmatch(url.database or ""):
            return False
        if not _is_empty(engine):
            return False
        template = _ensure_template(url, migrate, guard)
    except Exception as error:  # building the template failed: use the real migrations
        _state["disabled"] = True
        _warn(f"could not prepare the template ({type(error).__name__}: {str(error).splitlines()[0][:300] if str(error) else ''})")
        return False
    try:
        engine.dispose()
        admin = _admin(url)
        try:
            _drop(admin, url.database)
            with admin.connect() as connection:
                connection.execute(text(f"CREATE DATABASE {_quoted(url.database)} TEMPLATE {_quoted(template)}"))
            _state["epoch"] += 1
        finally:
            admin.dispose()
        return True
    except Exception as error:
        _state["disabled"] = True
        _warn(f"cloning failed ({type(error).__name__}: {str(error).splitlines()[0][:300] if str(error) else ''})")
        try:  # the test database may be gone: give the real migrations an empty one to work on
            admin = _admin(url)
            try:
                with admin.connect() as connection:
                    exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar()
                    if not exists:
                        connection.execute(text(f"CREATE DATABASE {_quoted(url.database)}"))
            finally:
                admin.dispose()
            _state["epoch"] += 1
            engine.dispose()
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        except Exception:
            pass
        return False
