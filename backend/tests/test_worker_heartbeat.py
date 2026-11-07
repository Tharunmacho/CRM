"""Worker heartbeat: reading workers report in, the API says honestly whether a reader is alive.

Synthetic data only; everything runs on the disposable loopback database.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading
import time

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import WorkerHeartbeat
from app.services import worker_heartbeat as hb
from app.services.change_feed import classify_statement
from app.services.extraction_queue import Dispatcher, manual_queue
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_manual_extraction import intake, start, upload  # noqa: F401  (fixture and helpers)

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
KEYS = {"alive", "workers", "busy", "lastSeenSeconds", "outdated"}


@pytest.fixture(autouse=True)
def fresh_cache():
    hb.reset_cache()
    yield
    hb.reset_cache()


def row(age, busy=0, version=None):
    return SimpleNamespace(seen_at=NOW - timedelta(seconds=age), busy=busy, version=version or hb.code_version())


def job(status="pending", waited=0, lease=None):
    return SimpleNamespace(status=status, queued_at=NOW - timedelta(seconds=waited), created_at=NOW - timedelta(seconds=waited), lease_until=lease)


# ------------------------------------------------------------------ pure logic (injected clock)

def test_no_rows_means_no_reader():
    assert hb.summarize([], now=NOW) == {"alive": False, "workers": 0, "busy": 0, "lastSeenSeconds": None, "outdated": False}


def test_alive_means_seen_within_three_beats_and_counts_only_live_workers():
    assert hb.BEAT_SECONDS == 30 and hb.ALIVE_SECONDS == 90 and hb.STALL_SECONDS > hb.ALIVE_SECONDS
    reader = hb.summarize([row(3, busy=2), row(hb.ALIVE_SECONDS - 1, busy=1), row(hb.ALIVE_SECONDS + 1, busy=5)], now=NOW)
    assert set(reader) == KEYS
    assert reader == {"alive": True, "workers": 2, "busy": 3, "lastSeenSeconds": 3, "outdated": False}
    stale = hb.summarize([row(hb.ALIVE_SECONDS + 1), row(400)], now=NOW)
    assert stale["alive"] is False and stale["workers"] == 0 and stale["lastSeenSeconds"] == hb.ALIVE_SECONDS + 1 and stale["outdated"] is False


def test_an_old_reader_is_outdated_but_a_dead_old_reader_is_not_counted():
    assert hb.summarize([row(2, version="old-build")], now=NOW)["outdated"] is True
    assert hb.summarize([row(2), row(5, version="old-build")], now=NOW)["outdated"] is True
    assert hb.summarize([row(120, version="old-build")], now=NOW)["outdated"] is False
    assert hb.summarize([row(2, version="abc")], now=NOW, version="abc")["outdated"] is False


def test_naive_database_timestamps_are_read_as_utc():
    naive = SimpleNamespace(seen_at=(NOW - timedelta(seconds=4)).replace(tzinfo=None), busy=0, version=hb.code_version())
    assert hb.summarize([naive], now=NOW)["lastSeenSeconds"] == 4


def test_version_comes_from_the_environment_or_a_constant(monkeypatch):
    for name in hb.VERSION_ENV:
        monkeypatch.delenv(name, raising=False)
    assert hb.code_version() == hb.READER_VERSION
    monkeypatch.setenv("SOURCE_COMMIT", "0123456789abcdef0123")
    assert hb.code_version() == "0123456789ab"


def test_waiting_reason_names_a_dead_reader_only_after_the_stall_window():
    dead = {"alive": False, "workers": 0, "busy": 0, "lastSeenSeconds": None, "outdated": False}
    live = {**dead, "alive": True, "workers": 1}
    assert hb.waiting_reason(job(waited=hb.STALL_SECONDS + 1), dead, now=NOW) == "reader_not_running"
    assert hb.waiting_reason(job("retry", waited=300), dead, now=NOW) == "reader_not_running"
    assert hb.waiting_reason(job(waited=20), dead, now=NOW) is None
    assert hb.waiting_reason(job(waited=900), live, now=NOW) == "queue"
    assert hb.waiting_reason(job(waited=900), None, now=NOW) is None
    for status in ("completed", "needs_review", "failed", "uploaded", "attached"):
        assert hb.waiting_reason(job(status, waited=900), dead, now=NOW) is None
    # a job whose reader died mid-read (lease lapsed) is stuck too; one with a live lease is being read
    assert hb.waiting_reason(job("processing", waited=900, lease=NOW - timedelta(seconds=5)), dead, now=NOW) == "reader_not_running"
    assert hb.waiting_reason(job("processing", waited=900, lease=NOW + timedelta(seconds=60)), dead, now=NOW) is None


def test_heartbeat_tables_never_wake_a_browser():
    assert classify_statement("INSERT INTO crm_worker_heartbeats (worker_id) VALUES (%(w)s) ON CONFLICT (worker_id) DO UPDATE SET busy = 1") is None
    assert classify_statement("DELETE FROM crm_worker_heartbeats WHERE seen_at < now()") is None


# ------------------------------------------------------------------ database

@pytest.fixture
def db_env(schema_database):  # noqa: F811
    _migrate(schema_database, "head")
    return SimpleNamespace(engine=schema_database, sessions=sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False))


def test_beat_upserts_one_row_per_worker_and_forget_removes_it(db_env):
    sessions = db_env.sessions
    hb.beat(sessions, worker_id="doc:a:1", kind="document", started_at=NOW, threads=4, busy=1, now=NOW)
    hb.beat(sessions, worker_id="doc:a:1", kind="document", started_at=NOW, threads=4, busy=3, now=NOW + timedelta(seconds=10))
    hb.beat(sessions, worker_id="mail:a:2", kind="email", started_at=NOW, threads=2, busy=0, now=NOW)
    with sessions() as db:
        rows = {r.worker_id: r for r in db.scalars(select(WorkerHeartbeat))}
    assert set(rows) == {"doc:a:1", "mail:a:2"}
    assert rows["doc:a:1"].busy == 3 and rows["doc:a:1"].threads == 4 and rows["doc:a:1"].version == hb.code_version()
    assert rows["doc:a:1"].started_at == NOW and rows["doc:a:1"].seen_at == NOW + timedelta(seconds=10)
    hb.forget(sessions, "doc:a:1")
    with sessions() as db:
        assert [r.worker_id for r in db.scalars(select(WorkerHeartbeat))] == ["mail:a:2"]


def test_beat_is_one_cheap_statement(db_env):
    from sqlalchemy import event
    seen = []
    event.listen(db_env.engine, "before_cursor_execute", lambda conn, cursor, statement, *rest: seen.append(statement))
    hb.beat(db_env.sessions, worker_id="doc:x:1", kind="document", started_at=NOW, threads=1, busy=0)
    writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE"))]
    assert len(writes) == 1 and "ON CONFLICT" in writes[0].upper()


def test_old_rows_are_pruned_after_a_day(db_env):
    sessions = db_env.sessions
    hb.beat(sessions, worker_id="gone", kind="document", started_at=NOW - timedelta(days=3), threads=1, busy=0, now=NOW - timedelta(hours=25))
    hb.beat(sessions, worker_id="recent", kind="document", started_at=NOW, threads=1, busy=0, now=NOW - timedelta(hours=2))
    assert hb.prune(sessions, now=NOW) == 1
    with sessions() as db:
        assert [r.worker_id for r in db.scalars(select(WorkerHeartbeat))] == ["recent"]


def test_reader_status_reads_the_table_with_an_injected_clock_and_caches_for_three_seconds(db_env):
    sessions = db_env.sessions
    with sessions() as db:
        assert hb.reader_status(db, now=NOW)["alive"] is False
    hb.beat(sessions, worker_id="doc:a:1", kind="document", started_at=NOW, threads=4, busy=2, now=NOW - timedelta(seconds=7))
    hb.beat(sessions, worker_id="mail:a:1", kind="email", started_at=NOW, threads=4, busy=0, now=NOW - timedelta(seconds=1))
    with sessions() as db:
        assert hb.reader_status(db, now=NOW) == {"alive": True, "workers": 1, "busy": 2, "lastSeenSeconds": 7, "outdated": False}
        assert hb.reader_status(db, kind="email", now=NOW)["lastSeenSeconds"] == 1
        assert hb.reader_status(db, now=NOW + timedelta(seconds=hb.ALIVE_SECONDS + 10))["alive"] is False
    # cached: the first live read is reused for 3 s even though the table changed underneath
    hb.reset_cache()
    with sessions() as db:
        first = hb.reader_status(db)
        hb.forget(sessions, "doc:a:1")
        hb.forget(sessions, "mail:a:1")
        assert hb.reader_status(db) == first


def test_a_missing_table_gives_none_without_breaking_the_transaction(db_env):
    with db_env.engine.begin() as connection:
        connection.execute(text("DROP TABLE crm_worker_heartbeats"))
    with db_env.sessions() as db:
        assert hb.reader_status(db, now=NOW) is None
        assert db.execute(text("SELECT 1")).scalar() == 1     # the session is still usable
    # a worker whose heartbeat cannot be written keeps going
    beat = hb.Heartbeat(db_env.sessions, worker_id="w", kind="document", threads=1, busy=lambda: 0)
    assert beat.once() is False and beat.failing is True


def test_a_dispatcher_reports_in_while_running_and_leaves_when_it_stops(db_env):
    stop = threading.Event()
    dispatcher = Dispatcher([manual_queue(db_env.sessions, SimpleNamespace())], concurrency=3, stop=stop, idle_poll=0.1, busy_poll=0.05,
                            grace=2, presence="document", presence_seconds=0.1, worker="doc:test:1")
    thread = threading.Thread(target=dispatcher.run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with db_env.sessions() as db:
                found = db.get(WorkerHeartbeat, "doc:test:1")
            if found:
                break
            time.sleep(0.05)
        assert found is not None and found.kind == "document" and found.threads == 3 and found.busy == 0
        with db_env.sessions() as db:
            assert hb.reader_status(db)["alive"] is True
    finally:
        dispatcher.request_stop()
        thread.join(15)
    with db_env.sessions() as db:
        assert db.get(WorkerHeartbeat, "doc:test:1") is None
        hb.reset_cache()
        assert hb.reader_status(db)["alive"] is False


def test_the_worker_modules_start_with_a_heartbeat_but_one_off_cycles_do_not(db_env):
    from app import document_extraction_worker, email_extraction_worker
    settings = SimpleNamespace(extraction_workers=1, extraction_max_parallel=8, extraction_concurrency=2)
    assert document_extraction_worker.build_dispatcher(sessions=db_env.sessions, settings=settings).presence == "document"
    assert email_extraction_worker.build_dispatcher(sessions=db_env.sessions, settings=settings).presence == "email"


# ------------------------------------------------------------------ the API

def back_date(env, identifier, seconds):
    with env.sessions() as db:
        db.execute(text("UPDATE document_extraction_jobs SET queued_at = now() - make_interval(secs => :s) WHERE id = :i"),
                   {"s": seconds, "i": identifier})
        db.commit()


def test_job_responses_carry_the_reader_and_a_waiting_reason(intake):  # noqa: F811
    queued = upload(intake)
    start(intake, queued["id"])
    path = "/api/ocr/jobs"
    # nobody has reported in: the reader is not alive; the reason appears only after 60 s of waiting
    listed = intake.client.get(path).json()[0]
    assert listed["reader"] == {"alive": False, "workers": 0, "busy": 0, "lastSeenSeconds": None, "outdated": False}
    assert listed["status"] == "pending" and listed["waitingReason"] is None
    back_date(intake, queued["id"], 120)
    hb.reset_cache()
    for body in (intake.client.get(path).json()[0],
                 intake.client.get(path + "/groups/" + queued["groupId"]).json()[0],
                 intake.client.get(path + "/" + queued["id"]).json()):
        assert set(body["reader"]) == KEYS and body["reader"]["alive"] is False
        assert body["waitingReason"] == "reader_not_running"
        assert body["aheadOfYou"] == 0 and body["queuePosition"] == 1     # the existing fields are unchanged
    # a reader reports in: the same job now simply waits its turn in the queue
    hb.beat(intake.sessions, worker_id="doc:t:1", kind="document", started_at=NOW, threads=4, busy=1)
    hb.reset_cache()
    body = intake.client.get(path + "/" + queued["id"]).json()
    assert body["reader"]["alive"] is True and body["reader"]["workers"] == 1 and body["reader"]["busy"] == 1
    assert body["reader"]["outdated"] is False and body["waitingReason"] == "queue" and body["aheadOfYou"] == 0
    # an old reader is flagged
    hb.beat(intake.sessions, worker_id="doc:t:1", kind="document", started_at=NOW, threads=4, busy=0, version="very-old")
    hb.reset_cache()
    assert intake.client.get(path + "/" + queued["id"]).json()["reader"]["outdated"] is True
    # a finished job never waits
    with intake.sessions() as db:
        db.execute(text("UPDATE document_extraction_jobs SET status='completed' WHERE id=:i"), {"i": queued["id"]})
        db.commit()
    assert intake.client.get(path + "/" + queued["id"]).json()["waitingReason"] is None


def test_inbox_health_carries_both_readers(db_env, monkeypatch):
    from app.api.routers import inbox
    from app.core import config
    # Other test modules import the router while `get_settings` is patched with a stub; use the real one here, whatever the import order.
    monkeypatch.setattr(inbox, "get_settings", config.get_settings)
    hb.beat(db_env.sessions, worker_id="doc:t:1", kind="document", started_at=NOW, threads=4, busy=0)
    with db_env.sessions() as db:
        payload = inbox._health(db)
    assert payload["reader"]["alive"] is True and payload["reader"]["workers"] == 1
    assert set(payload["reader"]) == KEYS and payload["mailReader"]["alive"] is False
    assert "worker" in payload and "counts" in payload


# ------------------------------------------------------------------ migration

def test_migration_0029_is_additive_idempotent_and_reversible(schema_database):  # noqa: F811
    engine = schema_database
    _migrate(engine, "0028_extraction_leases")
    assert "crm_worker_heartbeats" not in inspect(engine).get_table_names()
    _migrate(engine, "head")
    columns = {c["name"]: c for c in inspect(engine).get_columns("crm_worker_heartbeats")}
    assert set(columns) == {"worker_id", "kind", "started_at", "seen_at", "threads", "busy", "version"}
    assert not columns["worker_id"]["nullable"] and columns["version"]["nullable"]
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO crm_worker_heartbeats (worker_id, kind, started_at, seen_at) VALUES ('keep', 'document', now(), now())"))
        # the revision was lost after the table was made: running 0029 again must be harmless and keep the row
        connection.execute(text("UPDATE alembic_version SET version_num = '0028_extraction_leases'"))
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM crm_worker_heartbeats")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) != "0028_extraction_leases"   # moved on to head
        assert connection.scalar(text("SELECT obj_description('crm_worker_heartbeats'::regclass)"))
    _migrate(engine, "0028_extraction_leases", downgrade=True)
    assert "crm_worker_heartbeats" not in inspect(engine).get_table_names()
    _migrate(engine, "head")
    assert "crm_worker_heartbeats" in inspect(engine).get_table_names()
