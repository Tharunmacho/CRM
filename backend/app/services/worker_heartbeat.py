"""Which reading workers are alive: a cheap heartbeat table, and the honest answer screens show from it.

Every reading worker process (app/document_extraction_worker.py, app/email_extraction_worker.py) runs a ``Heartbeat``
thread that upserts ONE row of ``crm_worker_heartbeats`` every 30 seconds (a single autocommit ``INSERT ... ON CONFLICT DO UPDATE``)
and removes the row on a clean stop. The API turns those rows into

    {"alive": bool, "workers": int, "busy": int, "lastSeenSeconds": int | None, "outdated": bool}

``alive`` means at least one worker of the kind reported within ``ALIVE_SECONDS``; ``outdated`` means a live worker runs a
different code version than this API process (an old reader that was never restarted). The table is on the change-feed
ignore list, so heartbeats never wake a browser.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import threading
import time

from sqlalchemy import delete, select

from app.db.models import WorkerHeartbeat

log = logging.getLogger("worker_heartbeat")

# Bumped by hand whenever the reading code changes in a way an old running worker must not be trusted with.
# A deploy that sets CRM_CODE_VERSION (or GIT_COMMIT / SOURCE_COMMIT / COMMIT_SHA) uses that instead.
READER_VERSION = "2026.11.11-reader-2"
VERSION_ENV = ("CRM_CODE_VERSION", "GIT_COMMIT", "SOURCE_COMMIT", "COMMIT_SHA", "GIT_SHA")

# Against a hosted database every statement is a round trip, so a reader reports in every 30 s and counts as alive for
# three beats (one may be lost); a job that waits STALL_SECONDS with no live reader is reported as stalled.
BEAT_SECONDS = 30.0
ALIVE_SECONDS = 90
KEEP_SECONDS = 24 * 3600
CACHE_SECONDS = 3.0
# A job that waits this long while no reader is alive is reported as ``reader_not_running``.
STALL_SECONDS = 120


def code_version() -> str:
    for name in VERSION_ENV:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value[:12]
    return READER_VERSION


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _upsert(db, values: dict) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(WorkerHeartbeat.__table__).values(**values)
    update = {name: statement.excluded[name] for name in ("seen_at", "threads", "busy", "version", "kind")}
    db.execute(statement.on_conflict_do_update(index_elements=["worker_id"], set_=update))


def beat(sessions, *, worker_id: str, kind: str, started_at: datetime, threads: int, busy: int,
         version: str | None = None, now: datetime | None = None) -> None:
    """Report in: one upsert on this worker's own row."""
    now = now or datetime.now(timezone.utc)
    with sessions() as db:
        db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})  # one statement: no BEGIN / COMMIT round trips
        _upsert(db, {"worker_id": worker_id, "kind": kind, "started_at": started_at, "seen_at": now,
                     "threads": int(threads), "busy": int(busy), "version": version or code_version()})


def forget(sessions, worker_id: str) -> None:
    """A worker that stops cleanly takes its row away, so screens notice at once."""
    with sessions() as db:
        db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id))
        db.commit()


def prune(sessions, *, now: datetime | None = None) -> int:
    """Drop rows of workers that vanished without saying goodbye more than a day ago."""
    now = now or datetime.now(timezone.utc)
    with sessions() as db:
        count = db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < now - timedelta(seconds=KEEP_SECONDS))).rowcount
        db.commit()
    return count


class Heartbeat:
    """Background thread: ``beat`` every ``every`` seconds, independent of the reading loop, until ``stop()``.

    ``busy`` is a callable (current number of documents being read). Any failure is logged once and retried, never raised:
    a worker must keep reading even when the heartbeat table is unreachable or not migrated yet.
    """

    def __init__(self, sessions, *, worker_id: str, kind: str, threads: int, busy, every: float = BEAT_SECONDS, version: str | None = None):
        self.sessions, self.worker_id, self.kind, self.threads, self.busy = sessions, worker_id, kind, threads, busy
        self.every, self.version = every, version
        self.started_at = datetime.now(timezone.utc)
        self.halt = threading.Event()
        self.thread: threading.Thread | None = None
        self.failing = False
        self._next_prune = 0.0

    def once(self) -> bool:
        try:
            beat(self.sessions, worker_id=self.worker_id, kind=self.kind, started_at=self.started_at,
                 threads=self.threads, busy=self.busy(), version=self.version)
            if time.monotonic() >= self._next_prune:
                self._next_prune = time.monotonic() + 600
                prune(self.sessions)
            if self.failing:
                log.info("Heartbeat is working again.")
            self.failing = False
            return True
        except Exception as exc:
            if not self.failing:
                log.warning("Could not write the worker heartbeat (%s); reading continues. Run the database migrations if this keeps happening.",
                            type(exc).__name__)
            self.failing = True
            return False

    def _run(self) -> None:
        self.once()
        failures = 0
        # An unreachable database is not asked more often because of it: the wait doubles (up to 4 x ``every``).
        while not self.halt.wait(self.every * min(2 ** failures, 4)):
            failures = 0 if self.once() else failures + 1

    def start(self) -> "Heartbeat":
        self.thread = threading.Thread(target=self._run, daemon=True, name="worker-heartbeat")
        self.thread.start()
        return self

    def stop(self) -> None:
        self.halt.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        try:
            forget(self.sessions, self.worker_id)
        except Exception:
            log.debug("Could not remove the worker heartbeat row.", exc_info=True)


# --------------------------------------------------------------------------------------------- reading it

def summarize(rows, *, now: datetime, version: str | None = None) -> dict:
    """Pure: heartbeat rows (``seen_at``, ``busy``, ``version``) -> the ``reader`` object. ``now`` is injectable."""
    current = version or code_version()
    live, newest = [], None
    for row in rows:
        seen = _utc(row.seen_at)
        if seen is None:
            continue
        age = (now - seen).total_seconds()
        newest = age if newest is None or age < newest else newest
        if age <= ALIVE_SECONDS:
            live.append(row)
    return {
        "alive": bool(live), "workers": len(live), "busy": sum(int(row.busy or 0) for row in live),
        "lastSeenSeconds": None if newest is None else max(0, int(newest)),
        "outdated": any((row.version or "") != current for row in live),
    }


_cache: dict = {}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def reader_status(db, *, kind: str = "document", now: datetime | None = None, version: str | None = None) -> dict | None:
    """The ``reader`` object for ``kind`` workers, or ``None`` when the heartbeat table cannot be read.

    One cheap statement, cached for ``CACHE_SECONDS`` per database and kind (skipped when ``now`` is injected).
    The read runs in a savepoint so a missing table cannot poison the caller's transaction.
    """
    try:
        key = (str(db.get_bind().url), kind, version)
    except Exception:  # not a real session: report nothing rather than fail the page
        return None
    if now is None:
        with _lock:
            hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return dict(hit[1]) if hit[1] is not None else None
    moment = now or datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            rows = db.execute(select(WorkerHeartbeat.seen_at, WorkerHeartbeat.busy, WorkerHeartbeat.version)
                              .where(WorkerHeartbeat.kind == kind).order_by(WorkerHeartbeat.seen_at.desc()).limit(50)).all()
        value = summarize(rows, now=moment, version=version)
    except Exception:
        log.debug("Could not read worker heartbeats.", exc_info=True)
        value = None
    if now is None:
        with _lock:
            _cache[key] = (time.monotonic(), value)
    return dict(value) if value is not None else None


def waiting_reason(job, reader: dict | None, *, ahead=None, now: datetime | None = None) -> str | None:
    """Why a waiting job is not being read: ``reader_not_running`` (no live reader and it has waited past ``STALL_SECONDS``),
    ``queue`` (other work comes first) or ``None`` (not waiting, or nothing known about the reader)."""
    lapsed = getattr(job, "lease_until", None)
    moment = now or datetime.now(timezone.utc)
    waiting = job.status in ("pending", "retry") or (job.status == "processing" and (lapsed is None or _utc(lapsed) < moment))
    if not waiting or reader is None:
        return None
    if not reader["alive"]:
        since = _utc(getattr(job, "queued_at", None) or job.created_at)
        if since is not None and (moment - since).total_seconds() > STALL_SECONDS:
            return "reader_not_running"
        return None
    return "queue"
