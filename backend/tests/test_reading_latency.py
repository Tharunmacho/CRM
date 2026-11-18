"""Where a reading job's time goes, and what was done about the avoidable part.

The hosted database is a network hop away (~300 ms per statement) and drops now and then; the object store is across the internet.
So these tests pin round trips and waiting, never wall time of the real services: a fake reader, a fake clock, disposable PostgreSQL.
Synthetic data only.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import re
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError

from app import document_extraction_worker
from app.db import session as session_module
from app.db.models import DocumentExtractionJob
from app.services import document_diagnostics, extraction_queue, manual_extraction
from app.services.extraction_queue import Dispatcher, manual_queue, notify_work, utcnow
from test_database_naming import schema_database  # noqa: F401
from test_document_extraction import pdf
from test_extraction_queue import Recorder, add_jobs, dispatcher_for, env, queue_for, run_dispatchers  # noqa: F401  (fixture and helpers)
from test_manual_extraction import intake, process, start, upload  # noqa: F401  (fixture and helpers)
from test_ocr_classification import CV, PASSPORT


@contextmanager
def round_trips(engine):
    """Every statement, plus the BEGIN and COMMIT that a non-autocommit connection costs as separate trips."""
    seen = []

    def statement(conn, cursor, text_, parameters, context, executemany):
        seen.append(("statement", " ".join(text_.split())))

    def transaction(kind):
        def hook(conn):
            if not getattr(conn.connection.driver_connection, "autocommit", False):
                seen.append((kind, ""))
        return hook
    begin, commit = transaction("BEGIN"), transaction("COMMIT")
    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "begin", begin)
    event.listen(engine, "commit", commit)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "begin", begin)
        event.remove(engine, "commit", commit)


def engine_of(fixture):
    return fixture.sessions.kw["bind"]


# ------------------------------------------------------------------ waking and polling

def test_a_document_queued_in_the_same_process_starts_within_a_second_without_any_poll(env):
    """The loop sleeps (idle poll 30 s, no recent-activity polling); the commit that queues a document wakes it at once."""
    recorder = Recorder(env)
    dispatcher = dispatcher_for(queue_for(env, recorder), 2, busy_poll=30, idle_poll=30)
    dispatcher.RECENT_SECONDS = 0
    thread = threading.Thread(target=dispatcher.run)
    thread.start()
    try:
        time.sleep(0.5)                      # it has looked once and gone to sleep
        assert recorder.order == []
        queued = time.monotonic()
        with env.sessions() as db:
            from uuid import uuid4
            db.add(DocumentExtractionJob(id="wake-1", owner_id="a", group_id=str(uuid4()), kind="resume", filename="synthetic.pdf",
                content_type="application/pdf", object_key="unused/wake-1", content_hash=uuid4().hex + uuid4().hex, size_kb=1,
                status="pending", next_attempt_at=utcnow() - timedelta(seconds=1)))
            notify_work(db, "manual")
            db.commit()
        while not recorder.order and time.monotonic() - queued < 5:
            time.sleep(0.01)
        latency = time.monotonic() - queued
    finally:
        dispatcher.request_stop()
        thread.join(30)
    assert recorder.order == ["wake-1"]
    assert latency < 1.0, latency


def test_the_wake_up_waits_for_the_commit_not_the_notify_call(env):
    woke = threading.Event()
    extraction_queue.register_wake(woke)
    try:
        with env.sessions() as db:
            notify_work(db, "manual")
            assert not woke.is_set()         # not yet: the rows are not visible to anyone else
            db.commit()
            assert woke.is_set()
        woke.clear()
        with env.sessions() as db:
            notify_work(db, "manual")
            db.rollback()
        assert not woke.is_set()
    finally:
        extraction_queue.unregister_wake(woke)


def test_with_no_dispatcher_in_the_process_notify_costs_nothing_extra(env):
    assert not extraction_queue._LOCAL_WAKES
    with env.sessions() as db:
        notify_work(db, "manual")
        assert not event.contains(db, "after_commit", extraction_queue.wake_local)
        db.rollback()


def test_the_loop_keeps_looking_every_two_seconds_for_a_while_after_work_then_falls_back_to_the_idle_poll():
    now = [1000.0]
    dispatcher = Dispatcher([], concurrency=2, clock=lambda: now[0], idle_poll=15.0)
    assert dispatcher.poll_seconds(started=0, full=False) == 15.0         # nothing ever happened: the idle poll
    dispatcher._recent_until = now[0] + dispatcher.RECENT_SECONDS          # work was just found or finished
    assert dispatcher.poll_seconds(started=0, full=False) == 2.0
    now[0] += dispatcher.RECENT_SECONDS - 1
    assert dispatcher.poll_seconds(started=0, full=False) == 2.0
    now[0] += 2
    assert dispatcher.poll_seconds(started=0, full=False) == 15.0
    dispatcher.wake_alive = lambda: False                                  # no NOTIFY connection: never slower than 2 s
    assert dispatcher.poll_seconds(started=0, full=False) == 2.0


class FakeStop:
    """A stop flag whose wait() only records the pause it was asked for (a fake clock for the backoff)."""

    def __init__(self, stop_after):
        self.pauses, self.stop_after, self._set = [], stop_after, False

    def wait(self, delay=None):
        self.pauses.append(delay)
        if len(self.pauses) >= self.stop_after:
            self._set = True
        return self._set

    def is_set(self):
        return self._set

    def set(self):
        self._set = True


def test_backoff_after_a_dropped_database_is_one_two_four_eight_seconds_then_stays_at_eight():
    dispatcher = Dispatcher([], concurrency=1)
    sequence = []
    for dispatcher.failures in (1, 2, 3, 4, 5, 9):
        sequence.append(dispatcher.backoff_seconds())
    assert sequence == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    stop = FakeStop(stop_after=6)             # the loop's own waits, with the database refusing every time
    looping = Dispatcher([], concurrency=1, stop=stop, grace=0)

    def refuse(pool, *, peek=True):
        raise OperationalError("x", {}, Exception("server closed the connection"))
    looping.claim_round = refuse
    looping.run()
    assert stop.pauses == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_the_backoff_resets_to_one_second_after_one_good_round():
    outcomes = iter(["fail", "fail", "ok", "fail"])

    def claim_round(pool, *, peek=True):
        if next(outcomes) == "fail":
            raise OperationalError("x", {}, Exception("gone"))
        return 0
    stop = FakeStop(stop_after=3)
    dispatcher = Dispatcher([], concurrency=1, stop=stop, grace=0, idle_poll=0.01, busy_poll=0.01)
    dispatcher.claim_round = claim_round
    dispatcher.run()
    assert stop.pauses == [1.0, 2.0, 1.0]


def test_handing_a_claim_back_is_tried_again_while_the_database_is_away():
    attempts = []

    class Queue:
        name = "documents"

        def runner(self, identifier, worker):
            raise OperationalError("x", {}, Exception("server closed the connection"))

        def release(self, worker, identifiers, *, delay=0.0):
            attempts.append(identifiers)
            if len(attempts) < 3:
                raise OperationalError("x", {}, Exception("still gone"))
    stop = FakeStop(stop_after=10**6)
    dispatcher = Dispatcher([], concurrency=1, stop=stop, grace=0)
    dispatcher._run_one(Queue(), "job-1")
    assert attempts == [["job-1"]] * 3 and stop.pauses == [1.0, 2.0]


def test_a_claim_is_one_statement_and_still_commits_so_the_browsers_hear_that_reading_started(env):
    ids = add_jobs(env, 3)
    queue = queue_for(env, None)
    with round_trips(engine_of(env)) as trips:
        assert queue.claim("w", 3, 60) == ids
    assert [kind for kind, _ in trips].count("statement") == 1 and [kind for kind, _ in trips].count("COMMIT") == 1


# ------------------------------------------------------------------ the pool notices a dropped connection before using it

def test_a_pooled_connection_the_network_dropped_is_replaced_at_checkout_not_in_the_middle_of_a_save(schema_database):
    """Pre-ping, but only for a connection that rested: the checkout hook pings it (one cheap trip) and replaces a dead one.

    ``pool_pre_ping`` itself would add a round trip to EVERY checkout (the API's requests included), so the engine verifies
    idle connections instead (app/db/session.py); the reading worker lowers the idle threshold to a few seconds."""
    from sqlalchemy import create_engine
    engine = create_engine(schema_database.url, pool_size=2, max_overflow=0)
    for name, hook in (("checkin", session_module._mark_used), ("connect", session_module._stamp_new_connection),
                       ("checkout", session_module._verify_if_idle)):
        event.listen(engine, name, hook)
    before = session_module.IDLE_VERIFY_SECONDS
    try:
        session_module.verify_idle_after(0)
        with engine.connect() as connection:
            first_backend = connection.scalar(text("SELECT pg_backend_pid()"))
        with schema_database.connect() as killer:
            killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": first_backend})
            killer.commit()
        time.sleep(0.2)
        with engine.connect() as connection:           # would raise OperationalError without the checkout ping
            assert connection.scalar(text("SELECT 1")) == 1
            assert connection.scalar(text("SELECT pg_backend_pid()")) != first_backend
    finally:
        session_module.verify_idle_after(before)
        engine.dispose()
    assert session_module.IDLE_VERIFY_SECONDS == before


def test_the_reading_worker_checks_rested_connections_sooner_than_the_api():
    assert document_extraction_worker.WORKER_VERIFY_IDLE_SECONDS <= 10 < session_module.IDLE_VERIFY_SECONDS + 1
    assert session_module.engine.pool.__class__.__name__ in {"QueuePool", "AssertionPool"}
    options = session_module.engine.pool
    assert options._recycle == 1800                    # and a connection is never kept longer than 30 minutes


# ------------------------------------------------------------------ one reading job: statements, trips, timings

def read_job(intake, *pages):
    job = upload(intake, pdf(*pages))
    start(intake, job["id"])
    queue = manual_queue(intake.sessions, intake.settings)
    with round_trips(engine_of(intake)) as trips:
        assert queue.claim("worker-1", 1, 180) == [job["id"]]
        manual_extraction.process_job(job["id"], sessions=intake.sessions, settings=intake.settings, claimed_by="worker-1")
    return job, trips


def test_progress_of_one_job_is_saved_in_a_handful_of_statements(intake):
    job, trips = read_job(intake, CV, PASSPORT)
    own = [item for item in trips if not item[1].startswith("SELECT document_extraction_jobs.state")]   # the fake reader's own looks
    saves = [item for item in own if item[1].startswith("UPDATE document_extraction_jobs SET state = state ||")]
    # Before: eight progress saves for a resume + passport file (one per step, one per stored document).
    # Now: the local reading, the reader job keys, its result, the page report, the mapped result, and ONE for output + documents.
    assert 5 <= len(saves) <= 6, [item[1][:60] for item in saves]
    # Everything else this job costs against the database, claim included. Before: 17 round trips for this file (the claim:
    # BEGIN + UPDATE + COMMIT, the read, eight saves, the closing transaction of five); now 15 (the same claim and read, six
    # saves, the closing transaction). The fake reader's own looks at the row are not counted.
    statements = [text_ for kind, text_ in own if kind == "statement"]
    assert len(statements) <= 11, statements
    assert [kind for kind, _ in own].count("COMMIT") == 2          # the claim and the closing transaction; the saves are single trips
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.status == "completed" and set(saved.state["documents"]) == {"resume", "passport"} and saved.state["output"]


def test_the_job_records_where_its_time_went_and_the_report_says_it_in_one_line(intake):
    job, _ = read_job(intake, CV, PASSPORT)
    with intake.sessions() as db:
        state = db.scalar(select(DocumentExtractionJob.state).where(DocumentExtractionJob.id == job["id"]))
    timings = state["timings"]
    [run] = timings["runs"]
    assert timings["queuedAt"] and timings["firstClaimedAt"] == run["claimedAt"]
    assert run["waitSeconds"] is not None and run["waitSeconds"] >= 0 and not run["afterEarlierRun"]
    assert run["outcome"] == "read" and run["totalSeconds"] >= 0 and run["endedAt"]
    assert set(run["readerSeconds"]) == {"resume", "passport"}
    assert run["saves"] >= 5 and run["saveSeconds"] >= 0 and "localReadSeconds" in run and "downloadSeconds" in run
    view = document_diagnostics._timings(state)
    [line] = document_diagnostics.timing_lines(view)
    assert re.fullmatch(r"timing, attempt 1 of 1: waited [\d.]+ s in the queue, fetched the file [\d.]+ s, read pages locally [\d.]+ s, "
                        r"reader [\d.]+ s \(resume [\d.]+ s, passport [\d.]+ s\), saved [\d.]+ s \(\d+ saves\), "
                        r"stored the cut documents [\d.]+ s, took [\d.]+ s in all - read", line), line


def test_a_job_that_is_still_waiting_for_the_reader_keeps_its_timings_for_the_next_attempt(intake):
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    intake.statuses["resume"] = "running"
    queue = manual_queue(intake.sessions, intake.settings)
    assert queue.claim("w", 1, 180) == [job["id"]]
    manual_extraction.process_job(job["id"], sessions=intake.sessions, settings=intake.settings, claimed_by="w")
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        [first] = saved.state["timings"]["runs"]
        assert saved.status == "retry" and first["outcome"] == "waiting for the reader" and first["endedAt"]
        assert saved.state["provider_jobs"]["resume"]["job_id"] == "job-resume"      # the jsonb merge kept every other key
        db.execute(text("UPDATE document_extraction_jobs SET next_attempt_at = now() - interval '1 second', ready_at = now() - interval '1 second' WHERE id=:i"),
                   {"i": job["id"]})
        db.commit()
    intake.statuses["resume"] = "succeeded"
    assert queue.claim("w", 1, 180) == [job["id"]]
    manual_extraction.process_job(job["id"], sessions=intake.sessions, settings=intake.settings, claimed_by="w")
    with intake.sessions() as db:
        runs = db.scalar(select(DocumentExtractionJob.state).where(DocumentExtractionJob.id == job["id"]))["timings"]["runs"]
    assert [run["afterEarlierRun"] for run in runs] == [False, True] and [run["outcome"] for run in runs] == ["waiting for the reader", "read"]
    [first_line, second_line] = document_diagnostics.timing_lines(document_diagnostics._timings({"timings": {"runs": runs}}))
    assert "in the queue" in first_line and "before this attempt, after the previous one ended" in second_line


def test_the_timing_report_holds_numbers_and_the_three_reader_kinds_only():
    state = {"timings": {"queuedAt": "2026-11-17T16:21:40+00:00", "runs": [{
        "waitSeconds": 3.2, "localReadSeconds": 12.0, "readerSeconds": {"resume": 38.0, "passport": 22.0, "Alex Example": 5.0, "9999": 1.0},
        "saveSeconds": 2.1, "saves": 6, "totalSeconds": 60.0, "outcome": "read", "name": "Alex Example", "passport": "P0000000",
        "claimedAt": "2026-11-17T16:22:57+00:00", "phone": "9876543210"}]}}
    view = document_diagnostics._timings(state)
    assert set(view["runs"][0]["readerSeconds"]) == {"resume", "passport"}
    flat = json.dumps(view) + "\n".join(document_diagnostics.timing_lines(view))
    for private in ("Alex", "Example", "P0000000", "9876543210", "name", "phone", "9999"):
        assert private not in flat
    [line] = document_diagnostics.timing_lines(view)
    assert line == ("timing, attempt 1 of 1: waited 3.2 s in the queue, read pages locally 12 s, reader 38 s (resume 38 s, passport 22 s), "
                    "saved 2.1 s (6 saves), took 60 s in all - read")
    assert document_diagnostics._timings({"timings": "junk"}) is None and document_diagnostics.timing_lines(None) == []


def test_a_dropped_connection_while_saving_progress_costs_seconds_not_an_attempt(intake, monkeypatch):
    pauses, failures = [], []
    monkeypatch.setattr(manual_extraction, "_pause", pauses.append)

    def drop(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE document_extraction_jobs SET state = state ||") and len(failures) < 2:
            failures.append(statement)
            raise OperationalError(statement, {}, Exception("server closed the connection unexpectedly"))
    event.listen(engine_of(intake), "before_cursor_execute", drop)
    try:
        job, _ = read_job(intake, CV)
    finally:
        event.remove(engine_of(intake), "before_cursor_execute", drop)
    assert len(failures) == 2 and pauses == [1.0, 2.0]
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.status == "completed" and saved.attempts == 0


def test_a_connection_that_stays_down_is_not_retried_forever(intake, monkeypatch):
    pauses = []
    monkeypatch.setattr(manual_extraction, "_pause", pauses.append)
    calls = []

    def always():
        calls.append(1)
        raise OperationalError("x", {}, Exception("server closed the connection unexpectedly"))
    with pytest.raises(OperationalError):
        manual_extraction.with_db_retry(always)
    assert len(calls) == 4 and pauses == [1.0, 2.0, 4.0]
    with pytest.raises(ValueError):                      # anything else is not a connection problem: no retry
        manual_extraction.with_db_retry(lambda: (_ for _ in ()).throw(ValueError("no")))


def test_an_object_that_is_not_in_the_shared_store_hands_the_job_back_without_an_attempt(intake, monkeypatch):
    from botocore.exceptions import ClientError
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    with intake.sessions() as db:
        db.execute(text("UPDATE document_extraction_jobs SET object_key = 's3://bucket/crm/missing.pdf' WHERE id=:i"), {"i": job["id"]})
        db.commit()
    asked = []
    monkeypatch.setattr(manual_extraction, "read_bytes", lambda settings, key: asked.append(key) or (_ for _ in ()).throw(
        ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")))
    from app.services import instance_guard
    monkeypatch.setattr(instance_guard, "stored_here", lambda *a: pytest.fail("a shared-store key needs no existence check"))
    queue = manual_queue(intake.sessions, intake.settings)
    assert queue.claim("w", 1, 180) == [job["id"]]
    manual_extraction.process_job(job["id"], sessions=intake.sessions, settings=intake.settings, claimed_by="w")
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert asked == ["s3://bucket/crm/missing.pdf"] and saved.status == "retry" and saved.attempts == 0
