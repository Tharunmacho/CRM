"""What an idle reading worker costs a hosted database, proven with a fake clock and a fake database.

Every statement against the hosted PostgreSQL is a 300-1000 ms round trip on a shared server, so an idle worker must
be almost silent: one read-only peek every 15 s (the NOTIFY wake-up does the real work), a heartbeat every 30 s, no
claiming UPDATE (which also announced itself to every open browser) unless the peek found work, and an exponential
back-off when the database cannot be reached. Nothing here touches a database: the fakes only count statements.
"""
from datetime import timedelta
import threading
import time
from types import SimpleNamespace

import pytest

from app.services import worker_heartbeat
from app.services.extraction_queue import Dispatcher, LeasedQueue, utcnow


class VirtualTime:
    """A clock that only moves when somebody waits on it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class VirtualEvent:
    """threading.Event whose ``wait`` advances the virtual clock instead of sleeping (unless already set)."""

    def __init__(self, clock: VirtualTime, ends_at: float | None = None, on_wait=None):
        self.clock, self.flag, self.ends_at, self.on_wait, self.waits = clock, False, ends_at, on_wait, []

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def is_set(self):
        return self.flag

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait(self, timeout)
        if not self.flag and timeout is not None:
            self.clock.now += timeout
        if self.ends_at is not None and self.clock.now >= self.ends_at:
            self.stop_event.set()
        return self.flag


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def one(self):
        return self.rows[0]

    def all(self):
        return self.rows

    def scalar(self):
        return self.rows[0][0] if self.rows else None

    @property
    def rowcount(self):
        return len(self.rows)


class FakeDatabase:
    """Records every statement; answers the peek from ``ready`` and a claim from ``jobs``."""

    def __init__(self, queues=2, clock=None):
        self.statements, self.ready, self.jobs, self.down = [], [False] * queues, [], False
        self.lock = threading.Lock()
        self.clock, self.claimed_at = clock, []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connection(self, **options):
        return self

    def commit(self):
        pass

    def execute(self, statement, params=None):
        if self.down:
            raise ConnectionError("the database cannot be reached")
        sql = str(statement)
        with self.lock:
            self.statements.append(sql)
        if sql.lstrip().startswith("SELECT EXISTS") or ("EXISTS (SELECT 1" in sql and sql.lstrip().startswith("SELECT")):
            return FakeResult([tuple(self.ready)])
        if sql.lstrip().startswith("WITH ranked"):
            rows, self.jobs = self.jobs, []
            if rows and self.clock:
                self.claimed_at.append(self.clock.now)
            return FakeResult(rows)
        return FakeResult([(1,)])

    def count(self, prefix):
        return sum(1 for statement in self.statements if statement.lstrip().startswith(prefix))


def queues_over(database, runner=lambda identifier, worker: None):
    first = LeasedQueue(name="documents", table="document_extraction_jobs", ready="({a}.status = 'pending')", runner=runner, sessions=database)
    second = LeasedQueue(name="bundles", table="document_person_intakes", ready="({a}.status = 'pending')", runner=runner, sessions=database)
    return [first, second]


def run_virtual(database, *, seconds, wake_alive=True, on_wait=None, queues=None, **options):
    """Run the real Dispatcher loop for ``seconds`` of virtual time. Returns (dispatcher, wake event)."""
    clock = VirtualTime()
    stop = threading.Event()
    wake = VirtualEvent(clock, ends_at=seconds, on_wait=on_wait)
    wake.stop_event = stop
    dispatcher = Dispatcher(queues or queues_over(database), concurrency=8, worker="doc:test:1", stop=stop, wake=wake, clock=clock,
                            wake_alive=lambda: wake_alive, grace=0, heartbeat_seconds=3600, **options)
    dispatcher.run()
    return dispatcher, wake, clock


def test_an_idle_worker_asks_the_database_about_four_times_a_minute_and_never_claims():
    database = FakeDatabase()
    _, wake, clock = run_virtual(database, seconds=600)
    minutes = clock.now / 60
    peeks = database.count("SELECT")
    assert database.count("WITH ranked") == 0 and database.count("UPDATE") == 0, "an idle poll must not run the claiming UPDATE"
    assert peeks / minutes <= 4.5, f"{peeks / minutes:.1f} idle peeks a minute"
    # the heartbeat adds one autocommit upsert per BEAT_SECONDS; together an idle reader stays at or under ~8 statements a minute
    assert peeks / minutes + 60 / worker_heartbeat.BEAT_SECONDS <= 8
    assert set(wake.waits) == {15.0}, "the safety-net poll is 15 s while the NOTIFY wake-up is connected"


def test_without_the_notify_connection_the_poll_is_quicker_but_still_bounded():
    database = FakeDatabase()
    _, wake, clock = run_virtual(database, seconds=300, wake_alive=False)
    assert set(wake.waits) == {5.0}
    assert database.count("SELECT") / (clock.now / 60) <= 12.5


def test_a_notify_starts_the_work_at_once_not_at_the_next_poll():
    clock = VirtualTime()
    database = FakeDatabase(clock=clock)
    started, delivered = [], []
    queues = queues_over(database, lambda identifier, worker: started.append(identifier))

    def deliver(event, timeout):
        # once 100 s of idle time have passed a document is queued and Postgres delivers the NOTIFY: the sleeping wait ends at once
        if not database.claimed_at and not database.jobs and not event.flag and clock.now >= 100:
            database.ready = [True, False]
            database.jobs = [SimpleNamespace(id="job-1", rn=1, due=utcnow())]
            clock.now += 0.3
            delivered.append(clock.now)
            event.set()

    stop = threading.Event()
    wake = VirtualEvent(clock, ends_at=130, on_wait=deliver)
    wake.stop_event = stop
    dispatcher = Dispatcher(queues, concurrency=8, worker="doc:test:1", stop=stop, wake=wake, clock=clock, wake_alive=lambda: True,
                            grace=0, heartbeat_seconds=3600)
    dispatcher.run()
    assert database.claimed_at, "the job was claimed"
    assert database.claimed_at[0] - delivered[0] < 1.0, "work starts within a second of the NOTIFY, not at the next 15 s poll"
    deadline = time.monotonic() + 5
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started == ["job-1"]


def test_polling_is_quick_only_for_a_couple_of_seconds_after_work_was_found():
    database = FakeDatabase()
    database.ready = [True, False]
    database.jobs = [SimpleNamespace(id="job-1", rn=1, due=utcnow())]
    _, wake, clock = run_virtual(database, seconds=60)
    quick = [seconds for seconds in wake.waits if seconds == 0.5]
    assert 1 <= len(quick) <= 6, "0.5 s polling lasts ACTIVE_SECONDS, then it is the 15 s safety net again"
    assert wake.waits[-1] == 15.0


def test_an_unreachable_database_backs_off_exponentially_up_to_a_minute():
    database = FakeDatabase()
    database.down = True
    clock = VirtualTime()
    stop = threading.Event()
    waits = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            clock.now += timeout or 0
            if clock.now >= 900:
                self.set()
            return self.is_set()

    stop = Stop()
    dispatcher = Dispatcher(queues_over(database), concurrency=8, worker="doc:test:1", stop=stop, wake=VirtualEvent(clock), clock=clock, grace=0, heartbeat_seconds=3600)
    dispatcher.run()
    assert waits[:6] == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0]
    assert max(waits) == 60.0
    assert len(database.statements) == 0 and len(waits) < 25, "a dead database is asked about once a minute, not every poll"


def test_a_database_that_comes_back_resets_the_back_off():
    database = FakeDatabase()
    database.down = True
    clock = VirtualTime()
    waits = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            clock.now += timeout or 0
            if len(waits) == 3:
                database.down = False
            if len(waits) >= 5:
                self.set()
            return self.is_set()

    dispatcher = Dispatcher(queues_over(database), concurrency=8, worker="doc:test:1", stop=Stop(), wake=VirtualEvent(clock, ends_at=40), clock=clock,
                            grace=0, heartbeat_seconds=3600)
    dispatcher.wake.stop_event = dispatcher.stop
    dispatcher.run()
    assert waits[:3] == [2.0, 4.0, 8.0] and dispatcher.failures == 0


def test_lease_renewal_for_every_running_job_is_one_statement():
    database = FakeDatabase()
    documents, bundles = queues_over(database)
    dispatcher = Dispatcher([documents, bundles], concurrency=8, worker="doc:test:1", grace=0)
    for number, queue in enumerate((documents, documents, documents, bundles)):
        dispatcher.inflight[object()] = (queue, f"id-{number}")
    assert dispatcher.renew_leases() == 1  # the fake answers 1; the point is the statement count below
    renewals = [statement for statement in database.statements if "lease_until" in statement]
    assert len(renewals) == 1 and "document_extraction_jobs" in renewals[0] and "document_person_intakes" in renewals[0]
    dispatcher.inflight.clear()
    assert dispatcher.renew_leases() == 0 and len(database.statements) == 1, "nothing running: nothing asked"


def test_the_peek_covers_every_queue_in_one_statement():
    database = FakeDatabase()
    database.ready = [False, True]
    dispatcher = Dispatcher(queues_over(database), concurrency=8, worker="doc:test:1", grace=0)
    ready = dispatcher.peek()
    assert [queue.name for queue in ready] == ["bundles"] and len(database.statements) == 1


def test_the_heartbeat_and_the_alive_window_are_sized_for_a_slow_database():
    assert worker_heartbeat.BEAT_SECONDS == 30
    assert worker_heartbeat.ALIVE_SECONDS == 90, "three beats: one slow or lost write does not make a live reader look dead"
    assert worker_heartbeat.STALL_SECONDS > worker_heartbeat.ALIVE_SECONDS


def test_a_failing_heartbeat_slows_down_instead_of_hammering():
    waits = []
    beat = worker_heartbeat.Heartbeat(lambda: (_ for _ in ()).throw(ConnectionError("down")), worker_id="doc:x:1", kind="document", threads=1,
                                      busy=lambda: 0, every=30.0)

    class Halt:
        def wait(self, timeout):
            waits.append(timeout)
            return len(waits) >= 5

    beat.halt = Halt()
    beat._run()
    assert waits == [30.0, 60.0, 120.0, 120.0, 120.0]
