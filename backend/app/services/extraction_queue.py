"""Read many documents side by side: per-job claiming with leases, a refilling thread pool, wake-up and shutdown.

Why this exists. The reading queue used to run under ONE Postgres advisory lock and process at most ten jobs per
cycle strictly one after another, then sleep three seconds. Ten resumes uploaded together were read one by one.

How it works now.

* A job is claimed with a single statement: ``UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)``. Two
  workers (threads or processes) can never take the same job; neither waits for the other.
* A claim is a lease (``claimed_by`` + ``lease_until``). The pool renews the leases of what it is reading. When a worker
  dies the lease simply runs out and the job is claimed again (status ``processing`` with an expired lease is ready).
* Ready jobs are taken oldest first, interleaved by owner (everyone's first file, then everyone's second ...), so one
  person's twenty files do not hold back a single new upload.
* ``Dispatcher`` keeps ``concurrency`` jobs running and claims a new one the moment a slot frees. It wakes at once on a
  Postgres NOTIFY (``crm_extraction_wake``) sent when a document is queued, so polling is only a safety net: every
  ``busy_poll`` seconds for a couple of seconds after work was found, otherwise every ``idle_poll`` seconds (15 by
  default; ``degraded_poll`` seconds while the NOTIFY listener is not connected). Against a hosted database every
  statement costs a round trip, so an idle round is ONE read-only ``SELECT EXISTS`` over all the queues (``peek``); the
  claiming ``UPDATE`` only runs when the peek found something. A worker that cannot reach the database backs off
  exponentially (2 s, 4 s ... 60 s) instead of retrying every poll.
* On stop it waits for the running jobs for ``grace`` seconds, then releases the claims it still holds so that no
  job waits for a lease to expire.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
import socket
import threading
import time
from typing import Callable
from uuid import uuid4

from sqlalchemy import text

log = logging.getLogger("extraction_queue")

WAKE_CHANNEL = "crm_extraction_wake"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_worker_id(kind: str = "doc") -> str:
    return f"{kind}:{socket.gethostname()[:30]}:{os.getpid()}:{uuid4().hex[:6]}"[:80]


def threads_per_worker(settings) -> int:
    """Threads for one worker process: the configured concurrency, scaled so workers x threads stays under the cap."""
    workers = max(1, int(getattr(settings, "extraction_workers", 1)))
    cap = max(1, int(getattr(settings, "extraction_max_parallel", 16)))
    return max(1, min(int(getattr(settings, "extraction_concurrency", 4)), cap // workers))


def notify_work(db, source: str = "manual") -> None:
    """Wake sleeping workers. Delivered by Postgres only when the surrounding transaction commits."""
    try:
        db.execute(text("SELECT pg_notify(:channel, :source)"), {"channel": WAKE_CHANNEL, "source": source})
    except Exception:  # never fail a request because a wake-up could not be queued; workers poll anyway
        log.debug("Could not queue the worker wake-up.", exc_info=True)


@dataclass(eq=False)
class LeasedQueue:
    """One durable queue table claimed row by row.

    ``ready`` is a SQL predicate on the alias ``{a}`` using ``:now``; ``set_clause`` is what claiming changes besides the
    lease; ``release_set`` is what handing a claim back changes.
    """

    name: str
    table: str
    ready: str
    runner: Callable[[str, str], None]          # (identifier, worker_id)
    sessions: Callable
    order_col: str = "next_attempt_at"
    order_expr: str = ""   # when set, ``{a}`` is the alias: the due time to order by (instead of the column order_col)
    owner_expr: str = "j.owner_id"
    join: str = ""
    where: str = ""
    set_clause: str = "status = 'processing', updated_at = :now"
    release_set: str = "status = 'retry', next_attempt_at = :due, updated_at = :now, claimed_by = NULL, lease_until = NULL"
    release_where: str = "status = 'processing'"
    extra_params: dict = field(default_factory=dict)

    def _ready(self, alias: str) -> str:
        return self.ready.format(a=alias)

    def _order(self, alias: str) -> str:
        return self.order_expr.format(a=alias) if self.order_expr else f"{alias}.{self.order_col}"

    def claim(self, worker: str, limit: int, lease_seconds: float, *, now: datetime | None = None) -> list[str]:
        """Claim up to ``limit`` ready jobs (oldest first, fair across owners) in ONE statement."""
        if limit <= 0:
            return []
        now = now or utcnow()
        statement = text(f"""
            WITH ranked AS (
                SELECT j.id, row_number() OVER (PARTITION BY {self.owner_expr} ORDER BY {self._order('j')}, j.id) AS rn
                FROM {self.table} j {self.join}
                WHERE {self._ready('j')} {self.where}
            ), pick AS (
                SELECT j.id, ranked.rn, {self._order('j')} AS due
                FROM {self.table} j JOIN ranked ON ranked.id = j.id
                WHERE {self._ready('j')}
                ORDER BY ranked.rn, {self._order('j')}, j.id
                LIMIT :limit FOR UPDATE OF j SKIP LOCKED
            )
            UPDATE {self.table} t SET {self.set_clause}, claimed_by = :worker, lease_until = :lease
            FROM pick WHERE t.id = pick.id AND {self._ready('t')}
            RETURNING t.id, pick.rn, pick.due
        """)
        params = {"now": now, "lease": now + timedelta(seconds=lease_seconds), "worker": worker, "limit": limit, **self.extra_params}
        with self.sessions() as db:
            rows = db.execute(statement, params).all()
            db.commit()
        return [row.id for row in sorted(rows, key=lambda row: (row.rn, row.due, row.id))]

    def peek_sql(self, index: int) -> str:
        """``EXISTS(...)`` for 'is any job ready?'. Read only: it never locks, never writes, never wakes a browser."""
        return f"EXISTS (SELECT 1 FROM {self.table} j {self.join} WHERE {self._ready('j')} {self.where}) AS q{index}"

    def heartbeat(self, worker: str, identifiers: list[str], lease_seconds: float, *, now: datetime | None = None) -> int:
        if not identifiers:
            return 0
        now = now or utcnow()
        with self.sessions() as db:
            db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            return db.execute(text(f"UPDATE {self.table} SET lease_until = :lease WHERE id = ANY(:ids) AND claimed_by = :worker"),
                              {"lease": now + timedelta(seconds=lease_seconds), "ids": list(identifiers), "worker": worker}).rowcount

    def release(self, worker: str, identifiers: list[str], *, delay: float = 0.0, now: datetime | None = None) -> int:
        """Hand claims back so another worker takes them at once (no attempt is counted)."""
        if not identifiers:
            return 0
        now = now or utcnow()
        with self.sessions() as db:
            count = db.execute(text(f"UPDATE {self.table} SET {self.release_set} WHERE id = ANY(:ids) AND claimed_by = :worker AND {self.release_where}"),
                               {"ids": list(identifiers), "worker": worker, "now": now, "due": now + timedelta(seconds=delay)}).rowcount
            db.commit()
        return count


def manual_queue(sessions, settings) -> LeasedQueue:
    from app.services.manual_extraction import process_job
    from app.services.instance_guard import instance_name
    return LeasedQueue(
        name="documents", table="document_extraction_jobs", sessions=sessions,
        # Only this installation's jobs, and older rows that carry no name (see services/instance_guard.py).
        where="AND (j.instance IS NULL OR j.instance = :instance)", extra_params={"instance": instance_name(settings)},
        # Due time = ready_at for rows stamped by an installation (whose next_attempt_at is hidden from older readers, see
        # migration 0032), next_attempt_at for older rows.
        order_expr="COALESCE({a}.ready_at, {a}.next_attempt_at)",
        ready="(({a}.status IN ('pending','retry') AND COALESCE({a}.ready_at, {a}.next_attempt_at) <= :now) "
              "OR ({a}.status = 'processing' AND ({a}.lease_until IS NULL OR {a}.lease_until < :now)))",
        set_clause="status = 'processing', started_at = :now, updated_at = :now",
        runner=lambda identifier, worker: process_job(identifier, sessions=sessions, settings=settings, claimed_by=worker))


def bundle_queue(sessions, settings) -> LeasedQueue:
    from app.services.document_person_split import process_bundle
    return LeasedQueue(
        name="bundles", table="document_person_intakes", sessions=sessions, order_col="created_at",
        owner_expr="COALESCE(j.owner_id, j.source_type)",
        ready="({a}.status = 'pending' AND {a}.last_error IS NULL AND ({a}.lease_until IS NULL OR {a}.lease_until < :now))",
        set_clause="updated_at = :now",
        release_set="claimed_by = NULL, lease_until = NULL", release_where="status = 'pending'",
        runner=lambda identifier, worker: process_bundle(identifier, sessions=sessions, settings=settings))


class WakeListener(threading.Thread):
    """LISTEN for queued work and set ``event``; on any trouble the loop's polling still finds the work."""

    def __init__(self, url: str, event: threading.Event, channel: str = WAKE_CHANNEL):
        super().__init__(daemon=True, name="extraction-wake")
        self.url, self.event, self.channel = url, event, channel
        self.halt = threading.Event()
        self.listening = threading.Event()

    def run(self):
        import psycopg
        failures = 0
        while not self.halt.is_set():
            try:
                with psycopg.connect(self.url, autocommit=True, connect_timeout=10, keepalives=1, keepalives_idle=30,
                                     keepalives_interval=10, keepalives_count=4) as connection:
                    connection.execute(f"LISTEN {self.channel}")
                    self.listening.set()
                    if failures:
                        self.event.set()  # anything queued while the connection was down is looked for now
                    failures = 0
                    while not self.halt.is_set():
                        for _ in connection.notifies(timeout=2.0, stop_after=1):
                            self.event.set()
            except Exception as exc:
                self.listening.clear()
                failures += 1
                log.debug("Wake-up listener lost its connection (%s); polling continues.", type(exc).__name__)
                self.halt.wait(min(60, 5 * 2 ** min(failures - 1, 4)))

    def stop(self):
        self.halt.set()


class Dispatcher:
    """Keeps ``concurrency`` claimed jobs running and refills a slot the moment one frees."""

    ACTIVE_SECONDS = 2.0        # how long after finding work the loop keeps polling at ``busy_poll``
    BACKOFF_BASE = 2.0          # first wait after the database could not be reached; doubles up to BACKOFF_MAX
    BACKOFF_MAX = 60.0

    def __init__(self, queues: list[LeasedQueue], *, concurrency: int, worker: str | None = None,
                 stop: threading.Event | None = None, lease_seconds: float = 180, busy_poll: float = 0.5,
                 idle_poll: float = 15.0, degraded_poll: float = 5.0, grace: float = 30.0,
                 heartbeat_seconds: float | None = None,
                 before_claim: Callable[[], None] | None = None, wake: threading.Event | None = None,
                 presence: str | None = None, presence_seconds: float | None = None,
                 wake_alive: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic):
        self.queues = queues
        self.concurrency = max(1, concurrency)
        self.worker = worker or new_worker_id()
        self.stop = stop or threading.Event()
        self.lease_seconds = lease_seconds
        self.busy_poll, self.idle_poll, self.degraded_poll, self.grace = busy_poll, idle_poll, degraded_poll, grace
        self.heartbeat_seconds = heartbeat_seconds if heartbeat_seconds is not None else max(1.0, lease_seconds / 3)
        self.before_claim = before_claim
        self.wake = wake or threading.Event()
        # Is the NOTIFY wake-up connected? While it is, polling is only a safety net and may be slow; without
        # it the loop polls every ``degraded_poll`` seconds. Default: assume the wake-up works.
        self.wake_alive = wake_alive or (lambda: True)
        self.clock = clock
        self.inflight: dict = {}
        self._busy_until = 0.0
        self._started_last = False  # the previous round started jobs, so more may be waiting: claim without peeking
        self.abandoned = 0
        self.idle_rounds = 0  # consecutive claim rounds that found nothing
        self.failures = 0     # consecutive rounds that could not reach the database
        # ``presence`` is the worker kind ("document" / "email"): when set, run() reports in to crm_worker_heartbeats
        # every few seconds from its own thread, so screens can tell that a reader is alive (services/worker_heartbeat.py).
        self.presence, self.presence_seconds, self.heartbeat_thread = presence, presence_seconds, None

    # -- helpers
    def _running(self) -> dict[LeasedQueue, list[str]]:
        grouped: dict[LeasedQueue, list[str]] = {}
        for queue, identifier in list(self.inflight.values()):
            grouped.setdefault(queue, []).append(identifier)
        return grouped

    def _run_one(self, queue: LeasedQueue, identifier: str) -> None:
        try:
            queue.runner(identifier, self.worker)
        except Exception as exc:
            # The runners record their own failures; anything that escapes (database down ...) is not the document's
            # fault, so hand the claim back for a quick retry instead of waiting for the lease to run out.
            log.warning("Reading %s %s stopped unexpectedly (%s); it will be tried again.", queue.name, identifier, type(exc).__name__, exc_info=True)
            try:
                queue.release(self.worker, [identifier], delay=5)
            except Exception:
                log.warning("Could not release %s %s; its lease will expire.", queue.name, identifier)

    def _reap(self) -> None:
        for future in [future for future in self.inflight if future.done()]:
            self.inflight.pop(future, None)

    def _submit(self, pool, queue: LeasedQueue, identifier: str) -> None:
        future = pool.submit(self._run_one, queue, identifier)
        self.inflight[future] = (queue, identifier)
        future.add_done_callback(lambda _: self.wake.set())

    def peek(self, *, now: datetime | None = None) -> list[LeasedQueue]:
        """The queues that have a ready job, from ONE read-only statement over all of them (autocommit: one round trip)."""
        statement = text("SELECT " + ", ".join(queue.peek_sql(index) for index, queue in enumerate(self.queues)))
        params: dict = {"now": now or utcnow()}
        for queue in self.queues:
            params.update(queue.extra_params)
        with self.queues[0].sessions() as db:
            db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            row = db.execute(statement, params).one()
        return [queue for index, queue in enumerate(self.queues) if row[index]]

    def claim_round(self, pool, *, peek: bool = True) -> int:
        """Fill free slots from every queue that has work. Returns how many jobs were started."""
        started = 0
        free = self.concurrency - len(self.inflight)
        if free <= 0:
            return 0
        queues = self.queues
        if peek and not self._started_last:
            queues = self.peek()
            if not queues:
                return 0
        quota = -(-free // len(queues))
        full = []
        for queue in queues:
            take = min(quota, free)
            if take <= 0:
                break
            identifiers = queue.claim(self.worker, take, self.lease_seconds)
            for identifier in identifiers:
                self._submit(pool, queue, identifier)
            free -= len(identifiers)
            started += len(identifiers)
            if len(identifiers) == take:
                full.append(queue)
        for queue in full:  # a queue that filled its share may have more: use slots the others left free
            if free <= 0:
                break
            identifiers = queue.claim(self.worker, free, self.lease_seconds)
            for identifier in identifiers:
                self._submit(pool, queue, identifier)
            free -= len(identifiers)
            started += len(identifiers)
        return started

    def renew_leases(self, *, now: datetime | None = None) -> int:
        """Renew every lease this worker holds, whatever the queue, in ONE autocommit statement."""
        grouped = self._running()
        if not grouped:
            return 0
        now = now or utcnow()
        lease = now + timedelta(seconds=self.lease_seconds)
        parts, params = [], {"lease": lease, "worker": self.worker}
        for index, (queue, identifiers) in enumerate(grouped.items()):
            parts.append(f"u{index} AS (UPDATE {queue.table} SET lease_until = :lease WHERE id = ANY(:ids{index}) AND claimed_by = :worker RETURNING 1)")
            params[f"ids{index}"] = list(identifiers)
        statement = text("WITH " + ", ".join(parts) + " SELECT " + " + ".join(f"(SELECT count(*) FROM u{i})" for i in range(len(parts))) + " AS renewed")
        with next(iter(grouped)).sessions() as db:
            db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
            return int(db.execute(statement, params).scalar() or 0)

    def _heartbeat_loop(self, halt: threading.Event) -> None:
        failures = 0
        while not halt.wait(self.heartbeat_seconds if not failures else min(self.heartbeat_seconds, self.BACKOFF_BASE * 2 ** min(failures, 4))):
            try:
                self.renew_leases()
                failures = 0
            except Exception as exc:
                failures += 1
                log.warning("Could not renew reading leases (%s).", type(exc).__name__)

    def request_stop(self) -> None:
        self.stop.set()
        self.wake.set()

    def poll_seconds(self, *, started: int, full: bool) -> float:
        """How long to sleep before the next look. A NOTIFY or a finished job cuts any sleep short."""
        fallback = self.idle_poll if self.wake_alive() else min(self.idle_poll, self.degraded_poll)
        if full:
            return fallback  # every slot is taken: nothing can be claimed until a job ends (its callback wakes us)
        if started or self.clock() < self._busy_until:
            return self.busy_poll
        return fallback

    def backoff_seconds(self) -> float:
        return min(self.BACKOFF_MAX, self.BACKOFF_BASE * 2 ** min(self.failures - 1, 6))

    # -- main loop
    def run(self) -> int:
        """Run until ``stop`` is set. Returns the number of jobs still running when it gave up waiting."""
        pool = ThreadPoolExecutor(self.concurrency, thread_name_prefix="extract")
        halt = threading.Event()
        beat = threading.Thread(target=self._heartbeat_loop, args=(halt,), daemon=True, name="extraction-heartbeat")
        beat.start()
        if self.presence:
            from app.services.worker_heartbeat import BEAT_SECONDS, Heartbeat
            self.heartbeat_thread = Heartbeat(
                self.queues[0].sessions, worker_id=self.worker, kind=self.presence, threads=self.concurrency,
                busy=lambda: len(self.inflight), every=self.presence_seconds or BEAT_SECONDS).start()
        try:
            while not self.stop.is_set():
                self.wake.clear()
                self._reap()
                started = 0
                try:
                    if self.before_claim:
                        self.before_claim()
                    started = self.claim_round(pool)
                    self.failures = 0
                except Exception as exc:
                    self.failures += 1
                    delay = self.backoff_seconds()
                    log.error("Could not claim reading jobs (%s); saved jobs are retained. Trying again in %.0fs.", type(exc).__name__, delay)
                    self.stop.wait(delay)  # not ``wake``: a finished job or a NOTIFY must not turn a dead database into a hot loop
                    continue
                self._started_last = bool(started)
                self.idle_rounds = 0 if started else self.idle_rounds + 1
                if started:
                    self._busy_until = self.clock() + self.ACTIVE_SECONDS
                full = len(self.inflight) >= self.concurrency
                self.wake.wait(self.poll_seconds(started=started, full=full))
        finally:
            self.abandoned = self._shutdown(pool)
            halt.set()
            if self.heartbeat_thread is not None:
                self.heartbeat_thread.stop()
        return self.abandoned

    def _shutdown(self, pool) -> int:
        running = list(self.inflight)
        if running and self.grace > 0:
            log.info("Stopping: waiting up to %ss for %s running job(s).", self.grace, len(running))
            wait(running, timeout=self.grace)
        self._reap()
        left = self._running()
        count = sum(len(ids) for ids in left.values())
        for queue, identifiers in left.items():
            try:
                released = queue.release(self.worker, identifiers)
                log.info("Released %s %s claim(s) so another worker takes them.", released, queue.name)
            except Exception as exc:
                log.warning("Could not release %s claims (%s); the leases will expire.", queue.name, type(exc).__name__)
        pool.shutdown(wait=False, cancel_futures=True)
        return count


def queue_positions(db, jobs) -> dict[str, dict]:
    """``{job id: {"queuePosition": n, "aheadOfYou": k}}`` for waiting jobs, in two cheap statements at most."""
    waiting = [job.id for job in jobs if job.status in ("pending", "retry")]
    if not waiting:
        return {}
    rows = db.execute(text("""
        SELECT j.id,
               (SELECT count(*) FROM document_extraction_jobs o
                 WHERE o.status IN ('pending','retry') AND COALESCE(o.ready_at, o.next_attempt_at) <= now()
                   AND (COALESCE(o.ready_at, o.next_attempt_at), o.id) < (COALESCE(j.ready_at, j.next_attempt_at), j.id)) AS ahead
        FROM document_extraction_jobs j WHERE j.id = ANY(:ids)"""), {"ids": waiting}).all()
    return {row.id: {"queuePosition": row.ahead + 1, "aheadOfYou": row.ahead} for row in rows}
