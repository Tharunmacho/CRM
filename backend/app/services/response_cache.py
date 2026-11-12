"""A short-lived, in-process memo for read responses, emptied by the changes that make them stale.

Why: the database is a network hop away (every statement costs a round trip) and the desk polls a handful of
cheap-looking endpoints (inbox health, the unread badge, failed-intake list, pool counts...) from every open
tab. Answering them from memory for a few seconds removes most of those round trips without changing what
anybody sees: a write that matters empties the entry first.

How an entry stays correct:
  * Each entry names the change-feed topics it was built from (see `change_feed.TOPIC_TABLES`: "candidates",
    "mail", "tasks"...). Every topic has a generation counter. The entry remembers the counters it was built
    under, so any later change to one of its topics makes it unusable at once, whether the change was committed
    by this process (statement hook + connection check-in, which runs after the COMMIT) or by another process
    (the live hub hands every NOTIFY payload to `bump`).
  * The counters are read BEFORE the value is computed, so a change that lands while a value is being built
    leaves the finished entry already out of date.
  * Every entry also expires after `ttl` seconds. That bounds staleness for what no hook can see: heartbeats
    that the change feed deliberately ignores (worker_seen_at...), a second server that is not listening, a
    dropped LISTEN connection.
  * Concurrent requests for the same key share one computation (single flight): the second waits for the first
    instead of repeating its queries.
  * Callers build the key from everything the answer depends on, which includes the signed-in user (or role)
    whenever the answer differs by person. Authorisation itself is never cached here: the sign-in check and
    role dependencies run before a handler reaches `cached`.

Under pytest the cache is off (so no test sees another test's data) unless CRM_RESPONSE_CACHE=force. Set
CRM_RESPONSE_CACHE=0 to turn it off anywhere.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

from app.services import change_feed

MAX_ENTRIES = 2000
_INFO_KEY = "crm_cache_topics"

# Topics whose change also stales the dashboard / report numbers (mirrors the change feed's own rule).
_REPORT_SOURCES = change_feed.REPORT_SOURCES

_lock = threading.Lock()
_generation: dict[str, int] = {}
_epoch = 0  # bumped by clear(): invalidates everything at once
_entries: dict[Any, "_Entry"] = {}
_flights: dict[Any, threading.Lock] = {}
_stats = {"hits": 0, "misses": 0, "waits": 0}


@dataclass(slots=True)
class _Entry:
    value: Any
    expires: float
    generations: tuple


def enabled() -> bool:
    mode = os.environ.get("CRM_RESPONSE_CACHE", "").strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return False
    if mode == "force":
        return True
    return not os.environ.get("PYTEST_CURRENT_TEST")


def scope_of(db) -> str:
    """Which database a session talks to, for cache keys (one process may serve several in tests, never in production)."""
    try:
        return db.get_bind().url.render_as_string(hide_password=True)
    except Exception:
        return ""


def _generations(topics: tuple[str, ...]) -> tuple:
    return (_epoch, *(_generation.get(topic, 0) for topic in topics))


def bump(topics: Iterable[str]) -> None:
    """Something committed under these topics: every entry that depends on one of them is now out of date."""
    with _lock:
        for topic in topics:
            _generation[topic] = _generation.get(topic, 0) + 1


def clear() -> None:
    """Forget everything (a schema reset, the live listener reconnecting, tests)."""
    global _epoch
    with _lock:
        _epoch += 1
        _entries.clear()


def stats() -> dict:
    return {**_stats, "entries": len(_entries)}


def cached(key: Any, topics: Iterable[str], ttl: float, compute: Callable[[], Any]) -> Any:
    """Return the memoised value for `key`, or build it with `compute()` (once, however many callers ask).

    `key` must be hashable and include whatever the value depends on besides the topics (user, role, filters...).
    The value is shared between callers: treat it as read-only.
    """
    if not enabled():
        return compute()
    topics = tuple(topics)
    now = time.monotonic()
    entry = _entries.get(key)
    if entry is not None and entry.expires > now and entry.generations == _generations(topics):
        _stats["hits"] += 1
        return entry.value

    with _lock:
        flight = _flights.setdefault(key, threading.Lock())
    waited = flight.locked()
    with flight:
        if waited:
            _stats["waits"] += 1
        entry = _entries.get(key)
        if entry is not None and entry.expires > time.monotonic() and entry.generations == _generations(topics):
            _stats["hits"] += 1
            return entry.value
        _stats["misses"] += 1
        generations = _generations(topics)  # BEFORE computing: a change during the build leaves the entry stale
        try:
            value = compute()
        finally:
            with _lock:
                _flights.pop(key, None)
        with _lock:
            if len(_entries) >= MAX_ENTRIES:
                _drop_expired_locked()
            _entries[key] = _Entry(value, time.monotonic() + ttl, generations)
        return value


def _drop_expired_locked() -> None:
    now = time.monotonic()
    for stale in [k for k, e in _entries.items() if e.expires <= now]:
        del _entries[stale]
    if len(_entries) >= MAX_ENTRIES:  # nothing expired: start over rather than grow without bound
        _entries.clear()


# ------------------------------------------------------------------ invalidation from this process

def topics_for_statement(statement: str) -> tuple[str, ...]:
    """Cache topics a write statement touches (empty for reads and for ignored bookkeeping tables)."""
    found = change_feed.classify_statements(statement)
    if not found:
        return ()
    topics = {topic for _, table in found for topic in change_feed.TOPIC_TABLES.get(table, ())}
    if topics & _REPORT_SOURCES:
        topics.add("reports")
    return tuple(sorted(topics))


def note_transaction_topics(connection, topics):
    """Invalidate derived views after commit when their source is an explicitly scoped audit action."""
    connection.info.setdefault(_INFO_KEY, set()).update(topics)


def _note_statement(conn, cursor, statement, parameters, context, executemany):
    head = statement.lstrip()[:8].upper()
    if head[:6] in ("INSERT", "UPDATE", "DELETE") or head[:5] == "WITH ":
        topics = topics_for_statement(statement)
        if topics:
            context._crm_cache_topics = topics  # noted once the statement is known to have changed a row (_note_result)
    elif head[:6] in ("DROP S", "TRUNCA", "DROP T") or head.startswith(("CREATE S", "CREATE T")):
        clear()  # a schema or table reset (tests, restores): nothing built before it can be trusted


def _note_result(conn, cursor, statement, parameters, context, executemany):
    topics = getattr(context, "_crm_cache_topics", None)
    if not topics:
        return
    try:
        if cursor.rowcount == 0 and change_feed.rowcount_decides(statement):
            return  # matched nothing (an idle worker's empty claim): the memo stays valid
    except Exception:
        pass
    note_transaction_topics(conn, topics)


def _forget_rolled_back(conn):
    conn.info.pop(_INFO_KEY, None)


def _announce_on_checkin(dbapi_connection, connection_record):
    """The connection went back to the pool: whatever it wrote has been committed (or rolled back and noted)."""
    topics = connection_record.info.pop(_INFO_KEY, None)
    if topics:
        bump(topics)


def _clear_on_checkout(dbapi_connection, connection_record, connection_proxy):
    connection_record.info.pop(_INFO_KEY, None)  # nothing carried over from a previous borrower


_installed = False


def install() -> None:
    """Register the hooks once (safe to call repeatedly)."""
    global _installed
    if _installed:
        return
    _installed = True
    event.listen(Engine, "before_cursor_execute", _note_statement)
    event.listen(Engine, "after_cursor_execute", _note_result)
    event.listen(Engine, "rollback", _forget_rolled_back)
    event.listen(Pool, "checkout", _clear_on_checkout)
    event.listen(Pool, "checkin", _announce_on_checkin)


def on_notification(payload: dict) -> None:
    """A NOTIFY arrived from the database (any process): bump its topics. Called by the live hub."""
    topics = payload.get("t") or ()
    if change_feed.RESYNC_TOPIC in topics:
        clear()
        return
    bump(topics)


# Whoever imports the memo gets the invalidation hooks with it: a cache that nothing empties would be a bug.
install()
