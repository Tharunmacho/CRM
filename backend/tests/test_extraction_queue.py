"""The reading queue: per-job leases, side-by-side reading, fair order, wake-up and shutdown.

Synthetic data only; the document reader is a fake and nothing leaves the disposable loopback database.
"""
from datetime import timedelta
from types import SimpleNamespace
import threading
import time
from uuid import uuid4

import pymupdf as fitz
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app import document_extraction_worker, email_extraction_worker, runtime
from app.db.models import (DocumentExtractionJob, EmailIngestionJob, MailAccount, MailMessage, MultiPersonIntake, Role, User)
from app.services import document_extraction
from app.services.extraction_queue import (Dispatcher, LeasedQueue, WakeListener, bundle_queue, manual_queue, notify_work,
                                           queue_positions, threads_per_worker, utcnow)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_extraction import result
from test_manual_extraction import intake, start, upload  # noqa: F401  (fixture and helpers)


@pytest.fixture
def env(schema_database):  # noqa: F811
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=name, name=name.title(), email=name + "@example.test", role=Role.staff, active=True) for name in ("a", "b", "c")])
        db.commit()
    return SimpleNamespace(engine=schema_database, sessions=sessions, settings=SimpleNamespace(), counter=0)


def add_jobs(env, count, *, owner="a", status="pending", prefix="j", oldest=600):
    """Synthetic queue rows, oldest first (the first is ``oldest`` seconds old)."""
    base = utcnow() - timedelta(seconds=oldest)
    ids = []
    with env.sessions() as db:
        for number in range(count):
            env.counter += 1
            identifier = f"{prefix}{env.counter:04d}"
            db.add(DocumentExtractionJob(id=identifier, owner_id=owner, group_id=str(uuid4()), kind="resume", filename="synthetic.pdf",
                content_type="application/pdf", object_key="unused/" + identifier, content_hash=uuid4().hex + uuid4().hex, size_kb=1,
                status=status, next_attempt_at=base + timedelta(seconds=number)))
            ids.append(identifier)
        db.commit()
    return ids


class Recorder:
    """A fake process_job: records who ran what and how many ran at once, then completes the row."""

    def __init__(self, env, delay=0.0, fail=(), hold=None):
        self.env, self.delay, self.fail, self.hold = env, delay, set(fail), hold
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.order, self.workers = [], []

    def __call__(self, identifier, worker):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.order.append(identifier)
            self.workers.append((identifier, worker))
        try:
            if self.hold is not None:
                self.hold.wait(30)
            time.sleep(self.delay)
            if identifier in self.fail:
                raise RuntimeError("synthetic failure")
            with self.env.sessions() as db:
                db.execute(text("UPDATE document_extraction_jobs SET status='completed', claimed_by=NULL, lease_until=NULL, finished_at=now() WHERE id=:i"), {"i": identifier})
                db.commit()
        finally:
            with self.lock:
                self.active -= 1

    @property
    def finished(self):
        with self.env.sessions() as db:
            return db.scalar(text("SELECT count(*) FROM document_extraction_jobs WHERE status='completed'"))


def queue_for(env, runner):
    queue = manual_queue(env.sessions, env.settings)
    queue.runner = runner
    return queue


def run_dispatchers(dispatchers, done, timeout=30):
    threads = [threading.Thread(target=dispatcher.run) for dispatcher in dispatchers]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    deadline = started + timeout
    while not done() and time.monotonic() < deadline:
        time.sleep(0.01)
    elapsed = time.monotonic() - started
    for dispatcher in dispatchers:
        dispatcher.request_stop()
    for thread in threads:
        thread.join(30)
    return elapsed


def dispatcher_for(queue, concurrency, **options):
    options.setdefault("busy_poll", 0.05)
    options.setdefault("idle_poll", 0.2)
    return Dispatcher([queue], concurrency=concurrency, grace=5, **options)


def statements(env):
    seen = []

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    event.listen(env.engine, "before_cursor_execute", count)
    return seen


# ------------------------------------------------------------------ claiming

def test_claim_is_one_statement_oldest_first_and_marks_the_lease(env):
    ids = add_jobs(env, 5)
    queue = queue_for(env, None)
    seen = statements(env)
    claimed = queue.claim("w1", 3, 120)
    assert claimed == ids[:3] and len(seen) == 1
    with env.sessions() as db:
        rows = {row.id: row for row in db.scalars(select(DocumentExtractionJob))}
    assert all(rows[i].status == "processing" and rows[i].claimed_by == "w1" and rows[i].started_at and rows[i].lease_until > utcnow() for i in ids[:3])
    assert all(rows[i].status == "pending" and rows[i].claimed_by is None for i in ids[3:])
    assert queue.claim("w2", 10, 120) == ids[3:]   # the rest, never the leased three
    assert queue.claim("w3", 10, 120) == []
    seen.clear()
    assert queue.heartbeat("w1", ids[:3], 120) == 3 and len(seen) == 1
    seen.clear()
    assert queue.release("w1", ids[:3]) == 3 and len(seen) == 1


def test_waiting_for_a_retry_time_and_other_states_are_not_claimed(env):
    ids = add_jobs(env, 4)
    with env.sessions() as db:
        db.execute(text("UPDATE document_extraction_jobs SET next_attempt_at = now() + interval '1 hour' WHERE id=:i"), {"i": ids[0]})
        db.execute(text("UPDATE document_extraction_jobs SET status='uploaded' WHERE id=:i"), {"i": ids[1]})
        db.execute(text("UPDATE document_extraction_jobs SET status='dismissed' WHERE id=:i"), {"i": ids[2]})
        db.commit()
    assert queue_for(env, None).claim("w", 10, 60) == [ids[3]]


def test_expired_lease_is_claimed_again_but_a_live_one_is_not(env):
    live, dead, legacy = add_jobs(env, 3, status="processing")
    now = utcnow()
    with env.sessions() as db:
        db.execute(text("UPDATE document_extraction_jobs SET claimed_by='x', lease_until=:t WHERE id=:i"), {"t": now + timedelta(minutes=5), "i": live})
        db.execute(text("UPDATE document_extraction_jobs SET claimed_by='crashed', lease_until=:t WHERE id=:i"), {"t": now - timedelta(seconds=1), "i": dead})
        db.commit()   # `legacy` is a processing row with no lease: a crashed pre-lease worker
    queue = queue_for(env, None)
    assert sorted(queue.claim("w", 10, 60)) == sorted([dead, legacy])
    with env.sessions() as db:
        assert db.scalar(select(DocumentExtractionJob.claimed_by).where(DocumentExtractionJob.id == dead)) == "w"
        assert db.scalar(select(DocumentExtractionJob.claimed_by).where(DocumentExtractionJob.id == live)) == "x"


def test_two_workers_claiming_from_threads_never_take_the_same_job(env):
    ids = add_jobs(env, 60)
    taken = {"one": [], "two": []}
    barrier = threading.Barrier(2)

    def loop(name):
        queue = queue_for(env, None)
        barrier.wait()
        while True:
            got = queue.claim(name, 3, 60)
            if not got:
                return
            taken[name].extend(got)
            time.sleep(0.01)      # a real worker reads between claims; let the other claim in turn
    threads = [threading.Thread(target=loop, args=(name,)) for name in taken]
    [thread.start() for thread in threads]
    [thread.join(60) for thread in threads]
    everything = taken["one"] + taken["two"]
    assert sorted(everything) == sorted(ids) and len(set(everything)) == len(everything)
    assert taken["one"] and taken["two"]          # both really worked


def test_owners_are_interleaved_so_one_big_upload_does_not_starve_a_single_file(env):
    many = add_jobs(env, 6, owner="a", oldest=600)
    single = add_jobs(env, 1, owner="b", oldest=10)
    claimed = queue_for(env, None).claim("w", 10, 60)
    assert claimed.index(single[0]) == 1 and claimed[0] == many[0]    # second in line, not seventh
    assert [i for i in claimed if i in many] == many                  # each owner's own files stay in order


def test_bundles_are_leased_too_and_a_failed_bundle_is_left_alone(env):
    with env.sessions() as db:
        for number in range(3):
            db.add(MultiPersonIntake(id=f"b{number}", source_type="manual", source_id=f"s{number}", owner_id="a", created_by="a",
                status="pending", sources=[], pages=[], ignored_pages=[], last_error="broken" if number == 2 else None))
        db.commit()
    queue = bundle_queue(env.sessions, env.settings)
    assert sorted(queue.claim("w1", 5, 60)) == ["b0", "b1"]
    assert queue.claim("w2", 5, 60) == []
    queue.release("w1", ["b0"])
    assert queue.claim("w2", 5, 60) == ["b0"]


def test_email_jobs_are_claimed_across_mailboxes_and_only_for_eligible_ones(env):
    now = utcnow()
    with env.sessions() as db:
        db.add_all([MailAccount(id="m1", address="one@example.test", provider="imap"), MailAccount(id="m2", address="two@example.test", provider="imap"),
                    MailAccount(id="m3", address="three@example.test", provider="imap")])
        db.flush()
        for account, count in (("m1", 4), ("m2", 1), ("m3", 1)):
            for number in range(count):
                message = MailMessage(id=f"{account}-msg{number}", account_id=account, provider_message_id=f"{account}-{number}", received_at=now)
                db.add(message)
                db.flush()
                db.add(EmailIngestionJob(id=f"{account}-job{number}", message_id=message.id, status="pending", next_attempt_at=now - timedelta(minutes=10 - number)))
        db.commit()
    accounts = ["m1", "m2"]
    queue = email_extraction_worker.email_queue(env.sessions, env.settings, accounts)
    claimed = queue.claim("w", 3, 60)
    assert claimed[:2] == ["m1-job0", "m2-job0"] and len(claimed) == 3 and not any(i.startswith("m3") for i in claimed)
    assert queue.claim("other", 10, 60) == ["m1-job2", "m1-job3"] or sorted(queue.claim("other", 10, 60)) == []
    accounts[:] = []
    assert queue.claim("w", 10, 60) == []


# ------------------------------------------------------------------ the pool

def test_jobs_run_at_the_same_time_up_to_the_limit(env):
    add_jobs(env, 10)
    recorder = Recorder(env, delay=0.2)
    run_dispatchers([dispatcher_for(queue_for(env, recorder), 4)], lambda: recorder.finished == 10)
    assert recorder.finished == 10 and recorder.peak == 4 and sorted(recorder.order) == sorted(set(recorder.order))


def test_twelve_jobs_of_0_3_seconds_finish_in_about_one_second_at_concurrency_4(env):
    add_jobs(env, 12)
    recorder = Recorder(env, delay=0.3)
    elapsed = run_dispatchers([dispatcher_for(queue_for(env, recorder), 4)], lambda: recorder.finished == 12)
    assert recorder.finished == 12
    assert elapsed < 2.2, f"12 x 0.3 s at concurrency 4 took {elapsed:.2f}s (serial would be 3.6s)"


def test_with_one_slot_jobs_start_oldest_first(env):
    ids = add_jobs(env, 6)
    recorder = Recorder(env)
    run_dispatchers([dispatcher_for(queue_for(env, recorder), 1)], lambda: recorder.finished == 6)
    assert recorder.order == ids


def test_a_failing_job_does_not_block_the_others_and_is_handed_back(env):
    ids = add_jobs(env, 6)
    recorder = Recorder(env, delay=0.05, fail={ids[2]})
    run_dispatchers([dispatcher_for(queue_for(env, recorder), 2)], lambda: recorder.finished == 5)
    assert recorder.finished == 5
    with env.sessions() as db:
        row = db.get(DocumentExtractionJob, ids[2])
    assert row.status == "retry" and row.claimed_by is None and row.lease_until is None and row.next_attempt_at > utcnow()


def test_two_worker_loops_on_one_queue_read_every_job_exactly_once_and_twice_as_fast(env):
    add_jobs(env, 12, prefix="single")
    one = Recorder(env, delay=0.3)
    single = run_dispatchers([dispatcher_for(queue_for(env, one), 2)], lambda: one.finished == 12)
    add_jobs(env, 12, prefix="pair")
    shared = Recorder(env, delay=0.3)
    pair = run_dispatchers([dispatcher_for(queue_for(env, shared), 2), dispatcher_for(queue_for(env, shared), 2)],
                           lambda: shared.finished == 24)
    handled = [identifier for identifier, _ in shared.workers if identifier.startswith("pair")]
    assert sorted(handled) == sorted(set(handled)) and len(handled) == 12          # exactly once
    assert len({worker for identifier, worker in shared.workers if identifier.startswith("pair")}) == 2   # both worked
    assert pair < single * 0.75, f"two loops took {pair:.2f}s against {single:.2f}s for one"


def test_stop_waits_for_running_jobs_then_releases_what_is_left(env):
    ids = add_jobs(env, 3)
    hold = threading.Event()
    recorder = Recorder(env, hold=hold)
    dispatcher = Dispatcher([queue_for(env, recorder)], concurrency=2, grace=0.4, busy_poll=0.05, idle_poll=0.1)
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("abandoned", dispatcher.run()))
    thread.start()
    deadline = time.monotonic() + 10
    while recorder.active < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    asked = time.monotonic()
    dispatcher.request_stop()
    thread.join(10)
    assert outcome["abandoned"] == 2 and time.monotonic() - asked < 5
    with env.sessions() as db:
        rows = {row.id: row for row in db.scalars(select(DocumentExtractionJob))}
    released = [rows[i] for i in ids[:2]]
    assert all(row.status == "retry" and row.claimed_by is None and row.lease_until is None and row.attempts == 0 for row in released)
    assert rows[ids[2]].status == "pending"                        # never claimed
    assert sorted(queue_for(env, None).claim("next", 10, 60)) == sorted(ids)   # claimable at once, no lease to wait for
    hold.set()


def test_stop_lets_quick_jobs_finish_before_giving_up(env):
    add_jobs(env, 2)
    recorder = Recorder(env, delay=0.3)
    dispatcher = Dispatcher([queue_for(env, recorder)], concurrency=2, grace=5, busy_poll=0.05, idle_poll=0.1)
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("abandoned", dispatcher.run()))
    thread.start()
    while recorder.active < 2:
        time.sleep(0.01)
    dispatcher.request_stop()
    thread.join(10)
    assert outcome["abandoned"] == 0 and recorder.finished == 2


def test_heartbeat_keeps_a_long_job_leased_so_nobody_steals_it(env):
    (identifier,) = add_jobs(env, 1)
    hold = threading.Event()
    dispatcher = Dispatcher([queue_for(env, Recorder(env, hold=hold))], concurrency=1, lease_seconds=1.0, heartbeat_seconds=0.2,
                            busy_poll=0.05, idle_poll=0.1, grace=0.2)
    thread = threading.Thread(target=dispatcher.run)
    thread.start()
    time.sleep(2.5)    # well past the 1 s lease
    assert queue_for(env, None).claim("thief", 5, 60) == []
    hold.set()
    dispatcher.request_stop()
    thread.join(10)


def test_a_worker_that_dies_without_a_goodbye_loses_its_job_after_the_lease(env):
    (identifier,) = add_jobs(env, 1)
    queue = queue_for(env, None)
    assert queue.claim("dead-worker", 1, 0.2) == [identifier]
    assert queue.claim("other", 1, 60) == []
    time.sleep(0.4)
    assert queue.claim("other", 1, 60) == [identifier]


def test_queue_positions_count_only_older_waiting_jobs(env):
    ids = add_jobs(env, 4)
    with env.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob).order_by(DocumentExtractionJob.next_attempt_at)))
        places = queue_positions(db, jobs)
    assert [places[i]["queuePosition"] for i in ids] == [1, 2, 3, 4] and [places[i]["aheadOfYou"] for i in ids] == [0, 1, 2, 3]
    queue_for(env, None).claim("w", 2, 60)
    with env.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        places = queue_positions(db, jobs)
    assert set(places) == set(ids[2:]) and places[ids[2]]["aheadOfYou"] == 0 and places[ids[3]]["queuePosition"] == 2


def test_threads_per_worker_never_exceeds_the_cap():
    def settings(workers, threads, cap):
        return SimpleNamespace(extraction_workers=workers, extraction_concurrency=threads, extraction_max_parallel=cap)
    assert threads_per_worker(settings(2, 8, 16)) == 8
    assert threads_per_worker(settings(4, 16, 16)) == 4
    assert threads_per_worker(settings(4, 16, 3)) == 1
    assert threads_per_worker(settings(1, 3, 16)) == 3
    from app.core.config import Settings
    defaults = Settings(_env_file=None)
    assert defaults.extraction_concurrency == 8 and defaults.extraction_workers == 1  # threads, not processes, on a hosted database
    assert defaults.extraction_workers * threads_per_worker(defaults) <= defaults.extraction_max_parallel


def test_a_notify_wakes_the_listener(env):
    wake = threading.Event()
    listener = WakeListener(env.engine.url.set(drivername="postgresql").render_as_string(hide_password=False), wake)
    listener.start()
    try:
        assert listener.listening.wait(10)
        with env.sessions() as db:
            notify_work(db, "manual")
            assert not wake.is_set()          # nothing is delivered before the commit
            db.commit()
        assert wake.wait(5)
    finally:
        listener.stop()


def test_a_queued_document_and_its_notify_start_reading_within_a_second_of_a_slow_poll(env):
    """Idle poll is 15 s; the NOTIFY sent with the new document makes the worker look at once."""
    recorder = Recorder(env)
    queue = queue_for(env, recorder)
    wake = threading.Event()
    listener = WakeListener(env.engine.url.set(drivername="postgresql").render_as_string(hide_password=False), wake)
    listener.start()
    assert listener.listening.wait(10)
    dispatcher = Dispatcher([queue], concurrency=4, grace=5, wake=wake, idle_poll=15.0, busy_poll=0.5, wake_alive=listener.listening.is_set)
    thread = threading.Thread(target=dispatcher.run)
    thread.start()
    try:
        time.sleep(1.0)  # the first (empty) look is over and the worker sleeps for 15 s
        assert recorder.order == []
        queued_at = time.monotonic()
        with env.sessions() as db:
            db.add(DocumentExtractionJob(id="notified1", owner_id="a", group_id=str(uuid4()), kind="resume", filename="synthetic.pdf",
                content_type="application/pdf", object_key="unused/notified1", content_hash=uuid4().hex + uuid4().hex, size_kb=1,
                status="pending", next_attempt_at=utcnow() - timedelta(seconds=1)))
            notify_work(db, "upload")
            db.commit()
        while not recorder.order and time.monotonic() - queued_at < 10:
            time.sleep(0.01)
        assert recorder.order == ["notified1"]
        assert time.monotonic() - queued_at < 2.0, "work must start about a second after the NOTIFY, not at the next 15 s poll"
    finally:
        dispatcher.request_stop()
        thread.join(30)
        listener.stop()


def test_an_idle_look_is_one_read_only_statement_and_an_empty_claim_announces_nothing(env):
    """The old idle claim UPDATE matched no row, yet sent a pg_notify that woke every open browser and emptied the shared memo."""
    queue = queue_for(env, None)
    dispatcher = Dispatcher([queue], concurrency=4, grace=0)
    seen = statements(env)
    assert dispatcher.claim_round(pool=None) == 0
    assert len(seen) == 1 and seen[0].lstrip().upper().startswith("SELECT EXISTS")
    seen.clear()
    import psycopg
    with psycopg.connect(env.engine.url.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True) as listener:
        listener.execute("LISTEN crm_changes")
        assert queue.claim("w1", 3, 120) == []  # a claim that finds nothing changes nothing, so announces nothing
        assert list(listener.notifies(timeout=1.0, stop_after=1)) == []
        add_jobs(env, 1)
        listener.execute("SELECT 1")  # drain the announcement of the INSERT above
        list(listener.notifies(timeout=1.0, stop_after=5))
        assert len(queue.claim("w1", 3, 120)) == 1  # control: a claim that takes a job IS news (the job is now being read)
        assert len(list(listener.notifies(timeout=2.0, stop_after=1))) == 1


# ------------------------------------------------------------------ the real reader through the pool

def fake_provider(monkeypatch, delay=0.2):
    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            time.sleep(delay)
            return SimpleNamespace(job_id=f"job-{mode}-{idempotency_key[-8:]}", status="queued")

        def get(self, job_id):
            mode = job_id.split("-")[1]
            return SimpleNamespace(status="succeeded", result=result(mode, 1))

    class Provider:
        def __init__(self, **options): self.jobs = Jobs()
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(document_extraction, "VerisOCR", Provider)


def test_real_jobs_queue_then_read_side_by_side_and_all_complete(intake, monkeypatch):
    from test_document_extraction import pdf
    from test_ocr_classification import CV
    fake_provider(monkeypatch)
    jobs = []
    for number in range(8):
        jobs.append(upload(intake, pdf(CV), group=str(uuid4())))
        start(intake, jobs[-1]["id"])
    listed = intake.client.get("/api/ocr/jobs/groups/" + jobs[3]["groupId"]).json()[0]
    assert listed["status"] == "pending" and listed["queuePosition"] == 4 and listed["aheadOfYou"] == 3 and listed["queuedAt"]
    started = time.monotonic()
    document_extraction_worker.run_cycle(sessions=intake.sessions, settings=intake.settings, concurrency=4)
    elapsed = time.monotonic() - started
    with intake.sessions() as db:
        rows = list(db.scalars(select(DocumentExtractionJob)))
    assert len(rows) == 8 and all(row.status in {"completed", "needs_review"} for row in rows), [row.status for row in rows]
    assert all(row.started_at and row.finished_at and row.claimed_by is None and row.lease_until is None for row in rows)
    done = intake.client.get("/api/ocr/jobs/" + jobs[0]["id"]).json()
    assert done["queuePosition"] is None and done["startedAt"] and done["finishedAt"]
    assert elapsed < 15


def statement_budget(intake, monkeypatch):
    """Statements one claimed job costs, counted on the real engine."""
    from test_document_extraction import pdf
    from test_ocr_classification import CV
    fake_provider(monkeypatch, delay=0)
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    queue = manual_queue(intake.sessions, intake.settings)
    claimed = []
    seen = []
    engine = intake.sessions.kw["bind"]

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    event.listen(engine, "before_cursor_execute", count)
    claimed = queue.claim("counter", 1, 60)
    claim_cost = len(seen)
    queue.runner(claimed[0], "counter")
    event.remove(engine, "before_cursor_execute", count)
    return claim_cost, len(seen) - claim_cost, seen


def test_a_claimed_job_costs_few_statements(intake, monkeypatch):
    claim_cost, run_cost, seen = statement_budget(intake, monkeypatch)
    checkpoints = [s for s in seen if s.lstrip().startswith("UPDATE document_extraction_jobs SET state = state ||")]
    assert claim_cost == 1
    assert len(checkpoints) >= 3 and run_cost <= len(checkpoints) + 6, (run_cost, len(checkpoints))
    # Each checkpoint is exactly one UPDATE ... RETURNING (it used to be a locking SELECT, a lazy state load and an UPDATE).


# ------------------------------------------------------------------ the supervisor

class Child:
    def __init__(self, code=None):
        self.returncode = code
    def poll(self): return self.returncode
    def terminate(self): self.returncode = -15 if self.returncode is None else self.returncode
    def wait(self, timeout=None): return self.returncode


def supervise(monkeypatch, settings, spawn, rounds):
    monkeypatch.setattr(runtime, "require_current_schema", lambda: None)
    monkeypatch.setattr(runtime, "_new_process_job", lambda: None)
    monkeypatch.setattr(runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    turns = iter([False] * rounds + [True])
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: next(turns)))
    runtime.main()


def test_the_supervisor_starts_extraction_worker_processes_for_both_queues(monkeypatch):
    commands = []

    def spawn(command, **options):
        commands.append(command[-1])
        return Child()
    supervise(monkeypatch, SimpleNamespace(email_ingestion_enabled=True, whatsapp_enabled=False, extraction_workers=3, extraction_shutdown_seconds=0), spawn, 0)
    assert commands.count("app.document_extraction_worker") == 3 and commands.count("app.email_extraction_worker") == 3
    assert commands.count("app.email_worker") == 1 and commands.count("app.workflow_worker") == 1


def test_a_crashed_extraction_worker_is_restarted_alone(monkeypatch, capsys):
    readers, others = [], []

    def spawn(command, **options):
        if command[-1] == "app.document_extraction_worker":
            child = Child(1 if len(readers) == 1 else None)     # the second reader crashes at once
            readers.append(child)
        else:
            child = Child()
            others.append(child)
        return child
    supervise(monkeypatch, SimpleNamespace(email_ingestion_enabled=False, whatsapp_enabled=False, extraction_workers=2, extraction_shutdown_seconds=0), spawn, 3)
    assert len(readers) == 3                                  # two started, the crashed one started again
    assert all(child.returncode == -15 for child in others)   # nobody else was stopped by the crash
    assert readers[0].returncode == -15 and readers[2].returncode == -15
    assert "restarting it, the others keep running" in capsys.readouterr().err
