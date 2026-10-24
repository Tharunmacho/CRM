import logging
import time
from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine, event, exc
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
log = logging.getLogger("db")

# A pooled connection the database has already dropped (restarts, firewalls) must never reach a
# request. SQLAlchemy's pool_pre_ping verifies every checkout with its own round trip, which on a
# hosted database (150-350 ms away) doubles the cost of each request; so connections are verified
# only after they have sat idle (see `_verify_if_idle`).
# TCP keepalives and a recycle age stop firewalls/NAT in front of a remote database from
# silently dropping idle pooled connections ("server closed the connection unexpectedly").
engine = create_engine(
    settings.database_url,
    # parallel_reads fans a request out over up to ~10 connections; connections above pool_size are
    # closed on return and cost ~1 s to reopen against a hosted database, so keep enough resident.
    pool_size=12,
    max_overflow=6,
    # Reuse the most recently returned connection first. With the default first-in-first-out queue
    # a quiet desk rotates through every pooled connection, so each one sits idle past
    # IDLE_VERIFY_SECONDS and every checkout pays the SELECT 1 ping below (one extra round trip per
    # request). Last-in-first-out keeps one hot connection for sequential requests; bursts still fan
    # out over the others. TCP keepalives and the idle ping protect the connections that rest.
    pool_use_lifo=True,
    pool_recycle=1800,
    future=True,
    connect_args={"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 4,
                  "connect_timeout": 10},
)

CONNECT_ATTEMPTS = 3
IDLE_VERIFY_SECONDS = 10


@event.listens_for(engine, "checkin")
def _mark_used(dbapi_connection, connection_record):
    connection_record.info["last_used"] = time.monotonic()


@event.listens_for(engine, "connect")
def _stamp_new_connection(dbapi_connection, connection_record):
    """A connection that has only just been opened is known to be alive; do not ping it on first use."""
    connection_record.info["last_used"] = time.monotonic()


@event.listens_for(engine, "checkout")
def _verify_if_idle(dbapi_connection, connection_record, connection_proxy):
    """Ping only connections that were idle long enough for the network to have dropped them."""
    if time.monotonic() - connection_record.info.get("last_used", 0.0) < IDLE_VERIFY_SECONDS:
        return
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        dbapi_connection.rollback()
    except Exception:
        raise exc.DisconnectionError() from None  # the pool discards it and opens a fresh connection


@event.listens_for(engine, "do_connect")
def _connect_with_retry(dialect, connection_record, cargs, cparams):
    """A remote server can refuse or drop one brand-new connection; the next one succeeds."""
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return dialect.dbapi.connect(*cargs, **cparams)
        except Exception as exc:
            if attempt == CONNECT_ATTEMPTS or not _transient(exc):
                raise
            log.warning("Database connection attempt %s failed (%s); retrying.", attempt, type(exc).__name__)
            time.sleep(0.3 * attempt)


def _transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("server closed the connection", "connection reset", "timeout expired", "could not connect", "connection refused"))


def connect_with_retry(attempts: int = CONNECT_ATTEMPTS):
    """Open (and initialise) the first connection, tolerating a transient drop."""
    for attempt in range(1, attempts + 1):
        try:
            return engine.connect()
        except OperationalError:
            if attempt == attempts:
                raise
            time.sleep(0.3 * attempt)


def warm_pool(count: int = 6) -> None:
    """Open `count` connections at once and leave them in the pool, so the first page after a restart
    does not pay for several ~1 s connection set-ups against a hosted database."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return  # tests must never open connections to whatever DATABASE_URL points at

    def open_one(_):
        try:
            return engine.connect()
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=count) as workers:
        opened = list(workers.map(open_one, range(count)))
    for connection in opened:
        if connection is not None:
            connection.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# A transaction costs two extra round trips on top of its statements: psycopg sends BEGIN as its own
# message before the first statement, and the pool's rollback-on-return is another. Against a hosted
# database (150-350 ms away) a one-query GET therefore took ~3 round trips instead of 1. A GET
# does not need a transaction, so it gets autocommit connections (same pool, no BEGIN, nothing to
# roll back). Each statement then sees its own committed snapshot, which is also what READ COMMITTED
# gave a multi-statement GET before. A GET that writes (see `writes_on_get`) keeps a real transaction.
read_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
ReadSessionLocal = sessionmaker(bind=read_engine, autoflush=False, expire_on_commit=False)
_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE")


def writes_on_get(endpoint):
    """Mark a GET handler that changes data: it keeps an ordinary transaction (commit/rollback)."""
    endpoint.writes_on_get = True
    return endpoint


@event.listens_for(read_engine, "before_cursor_execute")
def _flag_writes_on_reads(conn, cursor, statement, parameters, context, executemany):
    head = statement.lstrip()[:6].upper()
    if head in _WRITE_VERBS or " FOR UPDATE" in statement.upper()[-60:]:
        log.error("A write ran on an autocommit read request; mark the handler with @writes_on_get: %s", statement[:120])


def get_db(request: Request) -> Generator[Session, None, None]:
    endpoint = request.scope.get("endpoint")
    read_only = request.method in ("GET", "HEAD") and not getattr(endpoint, "writes_on_get", False)
    db = (ReadSessionLocal if read_only else SessionLocal)()
    try:
        yield db
    finally:
        db.close()
