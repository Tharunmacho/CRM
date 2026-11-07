"""The short-lived read memo: it must be invisible except for being faster.

Unit tests need no database. The integration tests use the guarded disposable PostgreSQL fixture and turn the
cache on with CRM_RESPONSE_CACHE=force (it is off under pytest otherwise, so no other test can see a stale answer).
"""
import importlib.util
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import SESSION_COOKIE
from app.db.models import Candidate, MailAccount, MailMessage, Role, User
from app.services import response_cache as cache
from test_bootstrap_performance import (
    assert_select_budget, desk_client, desk_engine, recorded_statements, schema_database,  # noqa: F401
)


@pytest.fixture
def memo(monkeypatch):
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    cache.clear()
    yield cache
    cache.clear()


# ---------------------------------------------------------------- the memo itself (no database)

def test_cache_is_off_under_pytest_unless_forced(monkeypatch):
    monkeypatch.delenv("CRM_RESPONSE_CACHE", raising=False)
    calls = []
    assert cache.cached("off", ("t",), 60, lambda: calls.append(1) or len(calls)) == 1
    assert cache.cached("off", ("t",), 60, lambda: calls.append(1) or len(calls)) == 2  # computed again: no memo
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "0")
    assert cache.enabled() is False


def test_hit_then_topic_bump_then_ttl(memo):
    builds = []
    build = lambda: builds.append(1) or f"v{len(builds)}"  # noqa: E731
    assert memo.cached("k", ("candidates",), 60, build) == "v1"
    assert memo.cached("k", ("candidates",), 60, build) == "v1"  # hit
    memo.bump(["tasks"])  # an unrelated topic changes: still a hit
    assert memo.cached("k", ("candidates",), 60, build) == "v1"
    memo.bump(["candidates"])
    assert memo.cached("k", ("candidates",), 60, build) == "v2"  # rebuilt after its topic changed
    assert memo.cached("short", ("candidates",), 0.05, build) == "v3"
    time.sleep(0.08)
    assert memo.cached("short", ("candidates",), 0.05, build) == "v4"  # expired by time alone


def test_keys_are_separate_so_one_user_never_receives_anothers_entry(memo):
    assert memo.cached(("attention", "alice", "admin"), ("mail",), 60, lambda: "alice's") == "alice's"
    assert memo.cached(("attention", "bob", "manager"), ("mail",), 60, lambda: "bob's") == "bob's"
    assert memo.cached(("attention", "alice", "admin"), ("mail",), 60, lambda: "WRONG") == "alice's"


def test_a_change_during_the_build_leaves_the_entry_stale(memo):
    def build():
        memo.bump(["candidates"])  # a commit lands while the value is being computed
        return "built from the old state"

    assert memo.cached("racy", ("candidates",), 60, build) == "built from the old state"
    assert memo.cached("racy", ("candidates",), 60, lambda: "fresh") == "fresh"


def test_concurrent_callers_share_one_build(memo):
    started, release, builds = threading.Event(), threading.Event(), []

    def slow():
        builds.append(1)
        started.set()
        release.wait(2)
        return "once"

    results = []
    threads = [threading.Thread(target=lambda: results.append(memo.cached("flight", ("x",), 60, slow))) for _ in range(5)]
    threads[0].start()
    assert started.wait(2)
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.1)
    release.set()
    for thread in threads:
        thread.join(3)
    assert results == ["once"] * 5 and len(builds) == 1


def test_a_failed_build_is_not_remembered(memo):
    def boom():
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        memo.cached("fail", ("x",), 60, boom)
    assert memo.cached("fail", ("x",), 60, lambda: "recovered") == "recovered"


def test_clear_and_resync_notification_forget_everything(memo):
    memo.cached("a", ("x",), 60, lambda: 1)
    memo.on_notification({"t": ["*"]})  # the live listener reconnected: notifications may have been missed
    assert memo.cached("a", ("x",), 60, lambda: 2) == 2


def test_notification_from_another_process_bumps_its_topics(memo):
    memo.cached("a", ("mail",), 60, lambda: 1)
    memo.on_notification({"t": ["mail", "reports"], "at": 1})
    assert memo.cached("a", ("mail",), 60, lambda: 2) == 2


def test_statements_map_to_topics_and_noise_maps_to_none():
    assert {"candidates", "reports"} <= set(cache.topics_for_statement("UPDATE crm_candidates SET name=%(name)s WHERE id = %(id)s"))
    assert "mail" in cache.topics_for_statement("INSERT INTO email_messages (id) VALUES (%(id)s)")
    assert cache.topics_for_statement("SELECT * FROM crm_candidates") == ()
    assert cache.topics_for_statement("UPDATE email_accounts SET worker_seen_at=now() WHERE id=%(id)s") == ()  # heartbeat noise
    assert cache.topics_for_statement("INSERT INTO system_audit_log (id) VALUES (%(id)s)") == ()


# ---------------------------------------------------------------- invalidation by real commits (disposable PostgreSQL)

def test_a_commit_in_this_process_empties_dependent_entries_after_the_commit(memo, desk_engine):
    memo.cached("c", ("candidates",), 60, lambda: "before")
    memo.cached("m", ("mail",), 60, lambda: "mail before")
    with Session(desk_engine) as db:
        db.execute(update(Candidate).values(name="Renamed"))
        assert memo.cached("c", ("candidates",), 60, lambda: "x") == "before"  # not committed yet: nothing to announce
        db.commit()
    assert memo.cached("c", ("candidates",), 60, lambda: "after") == "after"
    assert memo.cached("m", ("mail",), 60, lambda: "WRONG") == "mail before"  # unrelated topic untouched


def test_a_rolled_back_write_announces_nothing(memo, desk_engine):
    memo.cached("c", ("candidates",), 60, lambda: "before")
    with Session(desk_engine) as db:
        db.execute(update(Candidate).values(name="Never saved"))
        db.rollback()
    assert memo.cached("c", ("candidates",), 60, lambda: "WRONG") == "before"


def test_a_write_that_matched_no_row_leaves_the_memo_alone(memo, desk_engine):
    """An idle reading worker's claim UPDATE matches nothing. It used to empty the documents / mail entries (the polled
    pulse among them) every few seconds, so the 10 s memo never served a second poll."""
    from sqlalchemy import text
    memo.cached("pulse", ("candidates", "mail", "documents", "whatsapp"), 10, lambda: "before")
    with Session(desk_engine) as db:
        db.execute(text("UPDATE document_extraction_jobs SET status = 'processing', claimed_by = 'w1' WHERE id = 'no-such-job'"))
        db.execute(text("UPDATE email_ingestion_jobs SET status = 'processing' WHERE id = 'no-such-job'"))
        db.commit()
    assert memo.cached("pulse", ("candidates", "mail", "documents", "whatsapp"), 10, lambda: "WRONG") == "before"
    with Session(desk_engine) as db:
        db.execute(update(Candidate).values(name="Renamed"))  # a write that changes rows still empties it
        db.commit()
    assert memo.cached("pulse", ("candidates", "mail", "documents", "whatsapp"), 10, lambda: "after") == "after"


# ---------------------------------------------------------------- the desk snapshot

def test_desk_snapshot_is_served_from_memory_until_something_it_holds_changes(memo, desk_engine, desk_client):
    first = desk_client.get("/api/auth/session?workspace=true&compact=true")
    with recorded_statements(desk_engine) as statements:
        second = desk_client.get("/api/auth/session?workspace=true&compact=true")
    assert second.json() == first.json()
    assert second.headers["cache-control"] == "no-store"
    assert_select_budget(statements, 1)  # only the sign-in check: the desk itself costs no statement
    desk_client.cookies.set(SESSION_COOKIE, "schema-existing-session")

    with desk_engine.begin() as connection:
        connection.execute(update(Candidate).values(name="Renamed after the snapshot was kept"))
    third = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    assert third["candidates"][0]["name"] == "Renamed after the snapshot was kept"


def test_desk_snapshot_never_crosses_users_or_survives_a_role_change(memo, desk_engine, desk_client):
    admin = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    assert admin["me"]["id"] == "schema-user"
    desk_client.cookies.set(SESSION_COOKIE, "other-existing-session")
    staff = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    assert staff["me"]["id"] == "schema-staff"
    assert {row["id"] for row in staff["notifications"]} == {"other-notification"}
    assert {row["id"] for row in admin["notifications"]}.isdisjoint({"other-notification"})
    assert admin != staff

    # Demote the first user: their next snapshot (kept for up to a minute) must reflect it at once.
    desk_client.cookies.set(SESSION_COOKIE, "schema-existing-session")
    with desk_engine.begin() as connection:
        connection.execute(update(User).where(User.id == "schema-user").values(role=Role.staff))
    demoted = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    assert demoted["me"]["role"] == "staff"


def test_desk_snapshot_is_identical_with_and_without_the_memo(memo, desk_client, monkeypatch):
    cached_first = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "0")
    assert desk_client.get("/api/auth/session?workspace=true&compact=true").json() == cached_first
    assert desk_client.get("/api/bootstrap?compact=true").json() == cached_first


def test_desk_snapshot_is_compressed_once_for_gzip_clients_and_plain_otherwise(memo, desk_client):
    plain = desk_client.get("/api/bootstrap", headers={"accept-encoding": "identity"})
    zipped = desk_client.get("/api/bootstrap", headers={"accept-encoding": "gzip"})
    assert "content-encoding" not in plain.headers
    assert zipped.headers["content-encoding"] == "gzip"
    assert zipped.headers["vary"] == "Accept-Encoding"
    assert zipped.content == plain.content  # httpx has already decoded the gzip body
    assert int(zipped.headers["content-length"]) < len(plain.content)


def test_desk_carries_the_unread_badge_and_follows_mail_changes(memo, desk_engine, desk_client):
    from datetime import datetime, timezone
    with Session(desk_engine) as db:
        db.add(MailAccount(id="badge-account", address="badge@example.test", provider="imap", sync_state={}))
        db.flush()
        db.add_all([MailMessage(id=f"badge-{n}", account_id="badge-account", provider_message_id=f"p{n}", unread=True,
                                received_at=datetime(2026, 11, 1, tzinfo=timezone.utc)) for n in range(2)])
        db.commit()
    first = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    badge = first["inboxUnread"]  # the same shape /api/inbox/pulse returns as `unread`; the fixture holds one more unread mail
    assert badge["accounts"]["badge-account"] == 2 and badge["total"] == sum(badge["accounts"].values())
    with Session(desk_engine) as db:
        db.execute(update(MailMessage).where(MailMessage.id == "badge-0").values(unread=False))
        db.commit()
    second = desk_client.get("/api/auth/session?workspace=true&compact=true").json()
    assert second["inboxUnread"]["accounts"]["badge-account"] == 1  # reading a mail empties the kept desk
    assert second["inboxUnread"]["total"] == badge["total"] - 1


# ---------------------------------------------------------------- compression middleware

@pytest.fixture
def web(tmp_path):
    # No static directory, so the SPA catch-all route is not registered ahead of the probe routes added below.
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused@127.0.0.1:1/compress_tests",
                        static_dir=str(tmp_path / "missing"), storage_dir=str(tmp_path / "uploads"), redis_url=None)
    spec = importlib.util.spec_from_file_location("app._compress_test", Path(__file__).parents[1] / "app" / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch("app.core.config.get_settings", return_value=settings):
        spec.loader.exec_module(module)
    big = '{"rows":[' + ",".join(['{"name":"candidate person","city":"Kochi"}'] * 400) + "]}"

    @module.app.get("/api/zz-json")
    def json_reply():
        return Response(big, media_type="application/json")

    @module.app.get("/api/zz-small")
    def small_reply():
        return Response('{"a":1}', media_type="application/json")

    @module.app.get("/api/zz-sheet")
    def sheet_reply():
        return Response(b"PK" + b"0" * 5000, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="x.xlsx"'})

    @module.app.get("/api/zz-download")
    def download_reply():
        return Response("a,b\n" * 2000, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="x.csv"'})

    @module.app.get("/api/zz-stream")
    def events():
        def chunks():
            for index in range(3):
                yield f"data: {index}\n\n".encode()
        return StreamingResponse(chunks(), media_type="text/event-stream")

    with patch.object(module.engine, "connect", side_effect=AssertionError("no database in this test")):
        client = TestClient(module.app)
        try:
            yield client, big
        finally:
            client.close()


def test_large_json_replies_are_gzipped_and_decode_to_the_same_bytes(web):
    client, big = web
    reply = client.get("/api/zz-json", headers={"accept-encoding": "gzip"})
    assert reply.headers["content-encoding"] == "gzip" and reply.text == big
    assert int(reply.headers["content-length"]) < len(big) / 4  # the wire size, before httpx decodes it
    raw = client.get("/api/zz-json", headers={"accept-encoding": "identity"})
    assert "content-encoding" not in raw.headers and raw.text == big


def test_small_attachment_and_event_stream_replies_are_never_compressed(web):
    client, _ = web
    headers = {"accept-encoding": "gzip"}
    assert "content-encoding" not in client.get("/api/zz-small", headers=headers).headers
    assert "content-encoding" not in client.get("/api/zz-sheet", headers=headers).headers
    assert "content-encoding" not in client.get("/api/zz-download", headers=headers).headers
    stream = client.get("/api/zz-stream", headers=headers)
    assert "content-encoding" not in stream.headers
    assert stream.text == "data: 0\n\ndata: 1\n\ndata: 2\n\n"
