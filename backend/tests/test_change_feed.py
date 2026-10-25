"""Real-time change capture: what a commit announces, what a rollback never does, and who may be told.

The database cases need TEST_DATABASE_URL (a loopback crm_schema_test* PostgreSQL, see test_database_naming.py)
because NOTIFY only exists there. The rest is pure logic and always runs.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Client, Notification, Role, TodoItem, User
from app.services import change_feed
from app.services.change_feed import (CHANNEL, CLIENT_TOPICS, IGNORED_TABLES, MAX_PAYLOAD_BYTES, Pending, TOPIC_TABLES,
                                      classify_statement, merge_events, visible_to)
from app.services.live_hub import CLOSE, MAX_STREAMS_PER_USER, LiveHub
from test_database_naming import schema_database  # noqa: F401  (fixture)


# ------------------------------------------------------------------ pure logic

def test_every_table_is_pushed_or_deliberately_ignored():
    unhandled = set(Base.metadata.tables) - set(TOPIC_TABLES) - set(IGNORED_TABLES) - {"auth_sessions"}
    assert not unhandled, f"decide a topic (or ignore) for: {sorted(unhandled)}"
    assert all(topic in CLIENT_TOPICS or topic == "reports" for topics in TOPIC_TABLES.values() for topic in topics)


@pytest.mark.parametrize("statement,expected", [
    ("INSERT INTO crm_client_contacts (id, name) VALUES (%(id)s, %(name)s)", ("INSERT", "crm_client_contacts")),
    ('UPDATE "crm_candidates" SET name=%(name)s, updated_at=now() WHERE id = %(id)s', ("UPDATE", "crm_candidates")),
    ("DELETE FROM crm_employer_companies WHERE client_id = %(c)s", ("DELETE", "crm_employer_companies")),
    ("  update workflow_tasks set completed_at=now() where id=1", ("UPDATE", "workflow_tasks")),
    ("SELECT * FROM crm_candidates", None),
    ("INSERT INTO system_audit_log (id) VALUES (1)", None),
    ("UPDATE staff_presence_clients SET last_sequence=2", None),
    ("INSERT INTO something_unknown (a) VALUES (1)", None),
    ("INSERT INTO auth_sessions (id) VALUES (1)", None),
    ("DELETE FROM auth_sessions WHERE id = 1", ("DELETE", "auth_sessions")),
])
def test_statements_are_classified(statement, expected):
    assert classify_statement(statement) == expected


def test_worker_heartbeats_and_checkpoints_are_not_news():
    assert classify_statement("UPDATE email_accounts SET worker_seen_at=now(), updated_at=now() WHERE id=1") is None
    assert classify_statement("UPDATE staff_presence_intervals SET last_seen_at=%(t)s WHERE id=1") is None
    assert classify_statement("UPDATE email_ingestion_jobs SET state=%(s)s, attempts=2, next_attempt_at=now() WHERE id=1") is None
    # ...but the same row changing something visible is.
    assert classify_statement("UPDATE email_ingestion_jobs SET status=%(s)s, state=%(x)s WHERE id=1") == ("UPDATE", "email_ingestion_jobs")
    assert classify_statement("UPDATE email_accounts SET last_error=%(e)s, worker_seen_at=now() WHERE id=1") == ("UPDATE", "email_accounts")
    # An UPDATE of a dynamic SET list we cannot read is treated as a real change.
    assert classify_statement("UPDATE crm_candidates SET (a, b) = (1, 2) WHERE id=1") == ("UPDATE", "crm_candidates")


def test_topics_are_mapped_and_reports_follow_business_changes():
    pending = Pending()
    pending.note_table("UPDATE", "crm_candidates")
    pending.note_table("INSERT", "workflow_task_remarks")
    body = json.loads(pending.payload(now_ms=5))
    assert body == {"t": ["candidates", "reports", "tasks"], "at": 5}
    only_personal = Pending()
    only_personal.note_table("INSERT", "system_notifications")
    assert json.loads(only_personal.payload(now_ms=1))["t"] == ["notifications"]  # not a report input
    assert Pending().payload() is None


def test_payload_is_small_and_holds_only_opaque_ids():
    pending = Pending()
    pending.note_table("INSERT", "crm_candidates")
    for index in range(5000):
        pending.note_object("crm_candidates", f"00000000-0000-0000-0000-{index:012d}", None, None)
    raw = pending.payload()
    assert len(raw.encode()) < MAX_PAYLOAD_BYTES
    assert "ids" not in json.loads(raw)  # too many to list: topics only
    few = Pending()
    few.note_table("UPDATE", "crm_applications")
    few.note_object("crm_applications", "app-1", None, "cand-1")
    body = json.loads(few.payload(now_ms=1))
    assert body["ids"] == {"applications": ["app-1"], "candidates": ["cand-1"]}
    assert set(body) <= {"t", "ids", "u", "at"}


def test_personal_topics_name_their_owner_only_when_known():
    pending = Pending()
    pending.note_table("INSERT", "personal_todos")
    pending.note_object("personal_todos", "t1", "alice", None)
    assert json.loads(pending.payload(now_ms=1))["u"] == {"todos": ["alice"]}
    unknown = Pending()
    unknown.note_table("UPDATE", "system_notifications")  # a bulk UPDATE: owner not known
    assert "u" not in json.loads(unknown.payload(now_ms=1))


def test_role_filtering_of_topics_and_ids():
    payload = {"t": ["candidates", "todos", "notifications", "users", "reports", "_auth", "bogus"],
               "ids": {"candidates": ["c1"], "todos": ["t1"]}, "u": {"todos": ["alice"], "notifications": ["alice"]}, "at": 9}
    alice = visible_to(payload, "staff", "alice")
    bob = visible_to(payload, "staff", "bob")
    boss = visible_to(payload, "manager", "bob")
    assert alice["topics"] == ["candidates", "todos", "notifications", "users", "reports"]
    assert "todos" not in bob["topics"] and "notifications" not in bob["topics"]  # someone else's personal rows
    assert "users" in bob["topics"]  # every workspace shows colleagues' names
    assert all(topic in CLIENT_TOPICS for topic in alice["topics"])  # control topics and unknown names never leave
    assert "ids" not in alice and "ids" not in bob  # recruiters get topic names only
    assert boss["ids"] == {"candidates": ["c1"]}
    assert visible_to({"t": ["todos"], "u": {"todos": ["alice"]}}, "admin", "bob") is None
    assert visible_to({"t": ["_auth"]}, "admin", "bob") is None


def test_unscoped_personal_events_still_refresh_everyone():
    assert visible_to({"t": ["todos"]}, "staff", "bob")["topics"] == ["todos"]


def test_events_merge_for_coalescing():
    merged = merge_events({"topics": ["tasks"], "at": 1, "ids": {"tasks": ["a"]}}, {"topics": ["tasks", "orders"], "at": 3, "ids": {"tasks": ["b"]}})
    assert merged == {"topics": ["tasks", "orders"], "at": 3, "ids": {"tasks": ["a", "b"]}}
    resync = merge_events({"topics": ["*"], "resync": True, "at": 2}, {"topics": ["tasks"], "at": 1, "ids": {"tasks": ["a"]}})
    assert resync["resync"] is True and "ids" not in resync


def test_other_dialects_are_untouched(tmp_path):
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    Base.metadata.create_all(engine, tables=[Client.__table__])
    with sessionmaker(bind=engine)() as db:
        db.add(Client(id="c", name="n"))
        db.commit()  # must not try pg_notify on SQLite
        assert db.get(Client, "c") is not None


# ------------------------------------------------------------------ hub fan-out (no database)

def test_hub_delivers_per_role_caps_streams_and_closes():
    async def scenario():
        hub = LiveHub()
        staff, boss = hub.subscribe("alice", "staff"), hub.subscribe("root", "admin")
        hub.deliver({"t": ["todos", "candidates"], "u": {"todos": ["root"]}, "ids": {"candidates": ["c1"]}, "at": 1})
        assert (await staff.queue.get()) == {"topics": ["candidates"], "at": 1}
        assert (await boss.queue.get()) == {"topics": ["todos", "candidates"], "at": 1, "ids": {"candidates": ["c1"]}}
        got = [hub.subscribe("alice", "staff") for _ in range(MAX_STREAMS_PER_USER)]
        assert got.count(None) == 1  # the cap: alice already holds one stream
        hub.stop()
        assert (await staff.queue.get()) is CLOSE
    asyncio.run(scenario())


def test_slow_subscriber_is_resynchronised_not_blocked():
    async def scenario():
        hub = LiveHub()
        slow = hub.subscribe("alice", "staff")
        for index in range(200):
            hub.deliver({"t": ["tasks"], "at": index})
        assert slow.queue.qsize() <= 64 and slow.behind is True
    asyncio.run(scenario())


# ------------------------------------------------------------------ against PostgreSQL

class Listener:
    def __init__(self, engine):
        url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        self.connection = psycopg.connect(url, autocommit=True)
        self.connection.execute(f"LISTEN {CHANNEL}")

    def messages(self, wait: float = 0.6) -> list[dict]:
        return [json.loads(n.payload) for n in self.connection.notifies(timeout=wait)]

    def close(self):
        self.connection.close()


@pytest.fixture
def feed(schema_database):
    change_feed.install()  # app.db.session does this at import in the running app
    Base.metadata.create_all(schema_database)
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add(User(id="alice", name="Alice", email="a@example.test", role=Role.staff))
        db.commit()
    listener = Listener(schema_database)
    listener.messages(0.3)  # drain what the setup announced
    yield SimpleNamespace(engine=schema_database, sessions=sessions, listener=listener)
    listener.close()


def test_commit_announces_topics_and_ids_exactly_once(feed):
    with feed.sessions() as db:
        db.add(Client(id="client-1", name="Private Person Name", email="private@example.test"))
        db.commit()
    messages = feed.listener.messages()
    assert len(messages) == 1
    body = messages[0]
    assert body["t"] == ["clients", "reports"]
    assert body["ids"] == {"clients": ["client-1"]}
    assert "Private" not in json.dumps(body) and "example.test" not in json.dumps(body)
    assert len(json.dumps(body)) < MAX_PAYLOAD_BYTES


def test_rolled_back_work_announces_nothing(feed):
    with feed.sessions() as db:
        db.add(Client(id="gone", name="Never saved"))
        db.flush()
        db.rollback()
    with feed.sessions() as db:
        db.add(Client(id="gone-2", name="Also never saved"))
        db.flush()
    assert feed.listener.messages() == []


def test_core_and_bulk_statements_are_seen_without_orm_events(feed):
    with feed.sessions() as db:
        db.add_all([Client(id="c1", name="One"), Client(id="c2", name="Two")])
        db.commit()
    feed.listener.messages()
    with feed.sessions() as db:
        db.execute(update(Client).where(Client.id == "c1").values(name="One renamed"))
        db.commit()
    assert [m["t"] for m in feed.listener.messages()] == [["clients", "reports"]]
    with feed.sessions() as db:
        db.execute(delete(Client))
        db.commit()
    messages = feed.listener.messages()
    assert [m["t"] for m in messages] == [["clients", "reports"]]
    with feed.sessions() as db:
        db.execute(text("UPDATE crm_client_contacts SET name = 'x'"))  # raw SQL on an empty table still names the topic
        db.commit()
    assert feed.listener.messages()[0]["t"] == ["clients", "reports"]


def test_one_transaction_with_several_tables_is_one_notification(feed):
    with feed.sessions() as db:
        db.add(Client(id="c9", name="Nine"))
        db.add(TodoItem(id="todo-1", owner_id="alice", title="Call", due_at=__import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc)))
        db.add(Notification(id="n1", user_id="alice", kind="x", title="Hello"))
        db.commit()
    messages = feed.listener.messages()
    assert len(messages) == 1
    assert messages[0]["t"] == ["clients", "notifications", "reports", "todos"]
    assert messages[0]["u"] == {"todos": ["alice"], "notifications": ["alice"]}


def test_read_only_and_audit_only_transactions_say_nothing(feed):
    with feed.sessions() as db:
        db.execute(text("SELECT 1"))
        db.commit()
    with feed.sessions() as db:
        db.execute(text("INSERT INTO system_audit_log (id, action) VALUES ('a1', 'noted')"))
        db.commit()
    assert feed.listener.messages() == []


def test_account_removal_raises_the_control_topic(feed):
    with feed.sessions() as db:
        db.execute(text("INSERT INTO auth_sessions (id, user_id, expires_at) VALUES ('s1', 'alice', now() + interval '1 day')"))
        db.commit()
    assert feed.listener.messages() == []  # signing in is not news
    with feed.sessions() as db:
        db.execute(text("DELETE FROM auth_sessions WHERE id = 's1'"))
        db.commit()
    assert feed.listener.messages()[0]["t"] == ["_auth"]
    with feed.sessions() as db:
        db.get(User, "alice").active = False
        db.commit()
    assert set(feed.listener.messages()[0]["t"]) == {"_auth", "reports", "users"}


def test_hub_listener_delivers_a_commit_to_a_subscriber_quickly_and_reconnects(feed):
    async def scenario():
        hub = LiveHub()
        url = feed.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        assert hub.start(asyncio.get_running_loop(), url)
        try:
            for _ in range(50):
                if hub.listening:
                    break
                await asyncio.sleep(0.05)
            assert hub.listening
            admin = hub.subscribe("root", "admin")
            staff = hub.subscribe("alice", "staff")
            started = time.perf_counter()
            await asyncio.get_running_loop().run_in_executor(None, _write_client, feed, "live-1")
            event = await asyncio.wait_for(admin.queue.get(), 2)
            elapsed = time.perf_counter() - started
            assert event["topics"] == ["clients", "reports"] and event["ids"] == {"clients": ["live-1"]}
            assert elapsed < 1.0
            assert (await asyncio.wait_for(staff.queue.get(), 2)) == {"topics": ["clients", "reports"], "at": event["at"]}

            # Drop the listener's server connection: it must come back and tell everyone to resynchronise.
            killer = psycopg.connect(url, autocommit=True)
            killer.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname = current_database() AND pid <> pg_backend_pid() AND query LIKE 'LISTEN%'")
            killer.close()
            resync = await asyncio.wait_for(admin.queue.get(), 8)
            assert resync.get("resync") is True and resync["topics"] == ["*"]
            await asyncio.get_running_loop().run_in_executor(None, _write_client, feed, "live-2")
            after = await asyncio.wait_for(admin.queue.get(), 3)
            assert after["ids"] == {"clients": ["live-2"]}
        finally:
            hub.stop()
    asyncio.run(scenario())


def _write_client(feed, client_id):
    with feed.sessions() as db:
        db.add(Client(id=client_id, name="Live"))
        db.commit()


# ------------------------------------------------------------------ the SSE endpoint over a real socket

class _Server:
    """The events router on a throwaway app served by uvicorn in a thread, so streaming is genuine.

    Startup and shutdown mirror main.py: the hub starts on the server's own event loop and stops with it.
    """

    def __init__(self, feed, identities):
        import socket
        import threading

        import uvicorn
        from fastapi import FastAPI

        from app.api.routers import events as events_router
        from app.services.live_hub import hub

        self.events, self.hub, self.identities = events_router, hub, identities
        conninfo = feed.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        app = FastAPI()
        app.include_router(events_router.router)

        @app.on_event("startup")
        async def start():
            hub.start(asyncio.get_running_loop(), conninfo)

        @app.on_event("shutdown")
        def stop():
            hub.stop()

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.original = self.events._identify
        self.events._identify = lambda session_id: self.identities.get(session_id)
        self.thread.start()
        for _ in range(200):
            if self.server.started and self.hub.listening:
                break
            time.sleep(0.05)
        assert self.hub.listening
        return self

    def __exit__(self, *exc):
        self.events._identify = self.original
        self.server.should_exit = True
        self.thread.join(8)
        assert not self.thread.is_alive(), "open event streams must not block shutdown"

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/api/events"


class _Reader:
    """Parses one SSE response incrementally; keeps its position between calls."""

    def __init__(self, response):
        self.lines = response.iter_lines()

    def read(self, limit=8, until=None):
        frames, name, data = [], None, []
        for line in self.lines:
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and name:
                frames.append((name, json.loads("".join(data))))
                name, data = None, []
                if len(frames) >= limit or (until and until(frames)):
                    break
        return frames

    def next_change(self):
        found = self.read(40, until=lambda f: f[-1][0] == "change")
        return found[-1][1] if found and found[-1][0] == "change" else None


def test_event_stream_end_to_end(feed, monkeypatch):
    import httpx

    from app.api.routers import events as events_router

    monkeypatch.setattr(events_router, "HEARTBEAT_SECONDS", 0.4)
    identities = {"admin-cookie": ("root", "admin"), "staff-cookie": ("alice", "staff")}
    with _Server(feed, identities) as server:
        assert httpx.get(server.url).status_code == 401  # no cookie
        assert httpx.get(server.url, cookies={"crm_session": "nobody"}).status_code == 401

        with httpx.stream("GET", server.url, cookies={"crm_session": "admin-cookie"}, timeout=10) as admin,              httpx.stream("GET", server.url, cookies={"crm_session": "staff-cookie"}, timeout=10) as staff:
            assert admin.status_code == 200
            assert admin.headers["content-type"].startswith("text/event-stream")
            assert "no-cache" in admin.headers["cache-control"] and admin.headers["x-accel-buffering"] == "no"
            assert "content-encoding" not in admin.headers  # nothing compresses (and so buffers) the stream
            admin_in, staff_in = _Reader(admin), _Reader(staff)
            assert admin_in.read(1)[0][0] == "hello" and staff_in.read(1)[0][0] == "hello"

            _write_client(feed, "stream-1")
            change = admin_in.next_change()
            assert change["topics"] == ["clients", "reports"] and change["ids"] == {"clients": ["stream-1"]}
            staff_change = staff_in.next_change()
            assert "ids" not in staff_change  # topic names only for a recruiter

            # Heartbeats keep arriving while nothing changes.
            assert any(name == "ping" for name, _ in admin_in.read(3))

            # A personal row reaches its owner only.
            with feed.sessions() as db:
                db.add(TodoItem(id="td", owner_id="alice", title="t", due_at=__import__("datetime").datetime(2030, 1, 1, tzinfo=__import__("datetime").timezone.utc)))
                db.commit()
            owner_event = staff_in.next_change()
            assert "todos" in owner_event["topics"]
            quiet = admin_in.read(10, until=lambda f: f[-1][0] == "ping")
            assert not any(name == "change" for name, _ in quiet)  # someone else's to-do is not announced to an admin

            # Sign-out: the next re-check ends the stream with a goodbye.
            del identities["staff-cookie"]
            _write_client(feed, "stream-2")  # any traffic; the heartbeat re-check closes the stream
            tail = staff_in.read(20, until=lambda f: f[-1][0] == "bye")
            assert any(name == "bye" for name, _ in tail)

        # The per-user cap answers 429 rather than piling up connections.
        streams = [httpx.stream("GET", server.url, cookies={"crm_session": "admin-cookie"}, timeout=10) for _ in range(8)]
        opened = [s.__enter__() for s in streams]
        try:
            assert all(r.status_code == 200 for r in opened)
            assert httpx.get(server.url, cookies={"crm_session": "admin-cookie"}).status_code == 429
        finally:
            for s in streams:
                s.__exit__(None, None, None)
