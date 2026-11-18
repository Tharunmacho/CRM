"""Statement budgets and a measurement table for the inbox / WhatsApp / mail-account / invoice read paths.

The production database is a network hop away (150-350 ms per SQL statement), so what matters for a request is how
many statements it sends and how many of them have to wait for one another. This module seeds a realistic volume
(three mailboxes, 2,000 cached messages, 500 intake jobs, 300 WhatsApp conversations, 200 reference agencies...),
calls every read endpoint of the slice as an admin and as a recruiter through the real routers, counts the
statements with a SQLAlchemy listener and pins them (measured + a little headroom), then grows the data to ten
mailboxes and five times the messages and checks that no request got more statements.

Everything is synthetic (invented names, example.test addresses). The mail adapter is faked, no mail server, WhatsApp
service or object store is contacted. Like the other database-backed tests it needs an explicit, disposable
`TEST_DATABASE_URL` (loopback, a `crm_schema_test*` database; the whole `public` schema is reset) and is skipped
without it. It never reads backend/.env.

Run it with `-s` to print the table:  pytest tests/test_api_statement_budgets_inbox.py -s
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_URL = os.environ.get("TEST_DATABASE_URL")
if not _URL:
    pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB", allow_module_level=True)
if not os.environ.get("CRM_ENV_FILE"):
    # Never fall back to backend/.env: point the settings at the disposable database through a throwaway file.
    _env = Path(tempfile.mkdtemp(prefix="crm-budget-")) / "test.env"
    _env.write_text(f"DATABASE_URL={_URL}\n", encoding="utf-8")
    os.environ["CRM_ENV_FILE"] = str(_env)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, insert, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core import deps  # noqa: E402
from app.core.security import SESSION_COOKIE  # noqa: E402
from app.db.models import (  # noqa: E402
    Candidate, CandidateInvoice, EmailIngestionJob, InvoiceSettings, MailAccount, MailAttachment, MailDraft,
    MailMessage, MailReply, ReferenceAgency, Role, Session as SessionRow, Source, User, WhatsAppChannel,
    WhatsAppConversation, WhatsAppMessage, WhatsAppSendAttempt, WhatsAppTemplate, WorkerHeartbeat,
)
from app.db.session import get_db  # noqa: E402
from app.integrations.mail.base import InboundMessage  # noqa: E402
from app.integrations.mail.imap import MailboxCursor  # noqa: E402
from test_database_naming import _guard_test_url, _migrate  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
FAR = NOW + timedelta(days=3650)
LATENCY = float(os.environ.get("CRM_BUDGET_LATENCY_MS", "20")) / 1000  # simulated network hop per round trip


# ----------------------------------------------------------------------------------------------- measurement

class Meter:
    """Counts statements and transaction round trips on one engine; optionally sleeps a hop per round trip."""

    def __init__(self, engine):
        self.engine, self.statements, self.extra, self.sleep = engine, [], 0, 0.0
        self.lock = threading.Lock()
        self.on = False

    def _trip(self):
        if self.sleep:
            time.sleep(self.sleep)

    def install(self):
        engine = self.engine

        @event.listens_for(engine, "before_cursor_execute")
        def started(conn, cursor, statement, parameters, context, executemany):
            if not self.on:
                return
            raw = cursor.connection
            begins = (not raw.autocommit) and raw.info.transaction_status == 0  # psycopg sends BEGIN first
            with self.lock:
                self.statements.append(" ".join(statement.split()))
                self.extra += 1 if begins else 0
            if begins:
                self._trip()
            self._trip()

        @event.listens_for(engine, "commit")
        def committing(conn):
            if self.on and conn.connection.dbapi_connection.info.transaction_status != 0:
                with self.lock:
                    self.extra += 1
                self._trip()

        @event.listens_for(engine, "rollback")
        def rolling(conn):
            if self.on and conn.connection.dbapi_connection.info.transaction_status != 0:
                with self.lock:
                    self.extra += 1
                self._trip()

        @event.listens_for(engine.pool, "reset")
        def returned(dbapi_connection, record, reset_state):
            if self.on and dbapi_connection.info.transaction_status != 0:  # rollback-on-return of an open transaction
                with self.lock:
                    self.extra += 1
                self._trip()

    @contextmanager
    def measure(self, sleep=0.0):
        deps._AUTH_CACHE.clear()  # a fresh request pays for its sign-in lookup, like the first call after 30 s idle
        self.statements, self.extra, self.sleep, self.on = [], 0, sleep, True
        try:
            yield self
        finally:
            self.on = False


def seed_ids(prefix, count):
    return [f"{prefix}-{index:05d}" for index in range(count)]


def bulk(db, model, rows, chunk=2000):
    for start in range(0, len(rows), chunk):
        db.execute(insert(model), rows[start:start + chunk])


def seed_mailboxes(db, first, count):
    accounts = []
    for index in range(first, first + count):
        provider = ("zoho", "gmail", "imap")[index % 3]
        accounts.append({"id": f"box-{index:02d}", "address": f"desk{index:02d}@mail{index:02d}.example.test", "provider": provider,
                         "auto_ingest": True, "sync_state": {"uid_validity": "1", "last_uid": 500, "folders": []},
                         "worker_seen_at": NOW, "extraction_seen_at": NOW, "last_sync_at": NOW,
                         "secrets": {"encrypted": "x"} if provider == "imap" else None})
    bulk(db, MailAccount, accounts)


def seed_messages(db, boxes, per_box, candidates, start=0):
    """`per_box` messages in each mailbox; every 4th has one or two attachments, every 4th-ish an ingestion job."""
    messages, attachments, jobs = [], [], []
    statuses = ("completed", "completed", "completed", "needs_review", "failed", "ignored", "pending", "completed", "retry", "completed")
    for box in boxes:
        for index in range(start, start + per_box):
            message_id = f"msg-{box}-{index:05d}"
            folder = "INBOX" if index % 8 else "Sent"
            messages.append({
                "id": message_id, "account_id": box, "provider_message_id": f"{folder}:1:{index}",
                "from_name": f"Sender {index % 97}", "from_address": f"sender{index % 97}@people.example.test",
                "subject": f"Application for welder position {index}", "body": ("Dear team, please find my resume attached. " * 30),
                "folder": folder, "remote_uid": index, "uid_validity": "1", "received_at": NOW - timedelta(minutes=index),
                "unread": index % 3 == 0, "to_addresses": [f"{box}@example.test"], "cc_addresses": [], "bcc_addresses": [],
                "starred": False, "remote_deleted": index % 50 == 49, "remote_state": "synced", "automation_headers": {},
                "candidate_id": candidates[index % len(candidates)] if index % 4 == 0 else None,
            })
            if index % 4 == 0:
                for number in range(1 + index % 2):
                    attachments.append({"id": f"att-{message_id}-{number}", "message_id": message_id, "file_name": f"resume-{index}-{number}.pdf",
                                        "size_kb": 120, "kind": "document", "object_key": f"mail/{message_id}/{number}",
                                        "content_hash": f"{index:08x}{number:08x}".ljust(64, "0")})
                jobs.append({"id": f"job-{message_id}", "message_id": message_id, "status": statuses[index % len(statuses)], "attempts": 1,
                             "last_error": "One or more document pages need review" if index % 10 == 3 else None,
                             "candidate_id": candidates[index % len(candidates)] if statuses[index % len(statuses)] == "completed" else None,
                             "state": {"documents": {}}, "inspection": [{"page": 1}], "next_attempt_at": NOW,
                             "created_at": NOW - timedelta(minutes=index), "updated_at": NOW - timedelta(minutes=index)})
    bulk(db, MailMessage, messages)
    bulk(db, MailAttachment, attachments)
    bulk(db, EmailIngestionJob, jobs)


@pytest.fixture(scope="module")
def world():
    url = _guard_test_url(_URL)
    engine = create_engine(url, pool_size=8, max_overflow=4, connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    with engine.connect() as connection:
        database, server = connection.execute(text("SELECT current_database(), host(inet_server_addr())")).one()
        assert database == url.database and server in {"127.0.0.1", "::1"}

    def reset():
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))

    reset()
    try:
        _migrate(engine, "head")
        sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with sessions() as db:
            bulk(db, User, [
                {"id": "u-admin", "name": "Asha Admin", "email": "asha@agency.example.test", "role": Role.admin},
                {"id": "u-manager", "name": "Maya Manager", "email": "maya@agency.example.test", "role": Role.manager},
                {"id": "u-staff", "name": "Ravi Recruiter", "email": "ravi@agency.example.test", "role": Role.staff},
            ])
            bulk(db, SessionRow, [{"id": f"s-{name}", "user_id": f"u-{name}", "expires_at": FAR} for name in ("admin", "manager", "staff")])
            candidates = seed_ids("cand", 300)
            bulk(db, Candidate, [{"id": cid, "ref": f"CND-{index:05d}", "care_id": f"CI-{index + 1:03d}", "name": f"Candidate {index}",
                                  "phone": f"90000{index:05d}", "source": Source.email if index % 2 else Source.walk_in,
                                  "owner_id": "u-staff" if index % 3 == 0 else "u-manager", "needs_review": index % 11 == 0}
                                 for index, cid in enumerate(candidates)])
            seed_mailboxes(db, 0, 3)
            seed_messages(db, ["box-00", "box-01", "box-02"], 667, candidates)  # about 2,000 messages
            # WhatsApp: one channel, 300 conversations, 1,500 received files and a send history on a few.
            bulk(db, WhatsAppChannel, [{"phone_number_id": "wa-phone-1", "display_number": "+91 90000 99999", "enabled": True,
                                        "auto_ingest": True, "worker_seen_at": NOW}])
            conversations = [{"id": f"conv-{index:04d}", "phone_number_id": "wa-phone-1", "peer_number": f"9190001{index:05d}",
                              "display_name": f"Contact {index}", "candidate_id": candidates[index] if index % 2 else None,
                              "last_inbound_at": NOW - timedelta(minutes=index), "last_message_at": NOW - timedelta(minutes=index),
                              "last_preview": "Hello, my documents are attached"} for index in range(300)]
            bulk(db, WhatsAppConversation, conversations)
            statuses = ("completed", "completed", "received", "needs_review", "failed", "ignored", "pending", "completed")
            bulk(db, WhatsAppMessage, [{"id": f"wam-{index:05d}", "provider_message_id": f"wamid.{index}", "phone_number_id": "wa-phone-1",
                                        "sender_number": f"9190001{index % 300:05d}", "sender_name": f"Contact {index % 300}",
                                        "message_type": "document" if index % 2 else "text", "body": "Please see my resume" * 3,
                                        "file_name": f"cv-{index}.pdf" if index % 2 else None, "object_key": f"wa/{index}" if index % 2 else None,
                                        "media_id": f"media-{index}", "received_at": NOW - timedelta(minutes=index // 3),
                                        "status": statuses[index % len(statuses)], "attempts": 0, "next_attempt_at": NOW,
                                        "processing_state": {}, "created_at": NOW, "updated_at": NOW - timedelta(minutes=index // 3),
                                        "candidate_id": candidates[index % 300] if statuses[index % len(statuses)] == "completed" else None}
                                       for index in range(1500)])
            bulk(db, WhatsAppSendAttempt, [{"id": f"wsa-{index:04d}", "conversation_id": "conv-0001", "request_key": f"request-key-{index:04d}",
                                            "sender_user_id": "u-admin", "payload_hash": "h" * 64, "message_type": "text", "body": "Thank you",
                                            "state": "sent", "created_at": NOW - timedelta(minutes=index), "updated_at": NOW} for index in range(40)])
            bulk(db, WhatsAppTemplate, [{"id": f"tpl-{index}", "phone_number_id": "wa-phone-1", "name": f"template_{index}", "language": "en",
                                         "category": "UTILITY", "status": "APPROVED", "components": []} for index in range(25)])
            bulk(db, ReferenceAgency, [{"id": f"ref-{index:04d}", "name": f"Contact {index}", "agency_name": f"Agency {index} Overseas",
                                        "phone": f"98400{index:05d}", "email": f"agency{index}@refs.example.test", "place": "Chennai",
                                        "staff_contacts": [{"name": "Desk", "phone": "9840000000"}], "specialisations": ["Medical"],
                                        "preferred_countries": ["Gulf"]} for index in range(200)])
            bulk(db, MailDraft, [{"id": f"draft-{index:03d}", "account_id": "box-00", "actor_id": "u-admin", "mode": "compose",
                                  "to_addresses": ["someone@people.example.test"], "cc_addresses": [], "bcc_addresses": [],
                                  "subject": f"Draft {index}", "body": "Hello", "status": "draft", "lock_version": 0, "remote_state": "pending"}
                                 for index in range(30)])
            bulk(db, MailReply, [{"id": "reply-1", "message_id": "msg-box-00-00004", "account_id": "box-00", "actor_id": "u-admin",
                                  "request_key": "reply-request-key-0001", "body": "Thanks", "status": "sent"}])
            bulk(db, WorkerHeartbeat, [{"worker_id": f"{kind}-worker", "kind": kind, "started_at": NOW - timedelta(hours=1), "seen_at": NOW,
                                        "threads": 2, "busy": 0, "version": "test"} for kind in ("document", "email")])
            db.add(InvoiceSettings(id=1, profile={}, invoice_prefix="CI/26-27/", next_number=1, enabled=True))
            db.commit()
            db.execute(text("ANALYZE"))
            db.commit()
        yield SimpleNamespace(engine=engine, sessions=sessions, candidates=candidates)
    finally:
        if not os.environ.get("CRM_BUDGET_KEEP"):  # (leave the data for EXPLAIN by hand in the disposable database)
            reset()
        engine.dispose()


class FakeMailbox:
    """The mailbox adapter, answering from memory: a folder list and one page of 25 messages."""

    def __init__(self, config=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def folders(self):
        return [{"name": name, "role": role, "total": 100, "unread": 5} for name, role in
                (("INBOX", "inbox"), ("Sent", "sent"), ("Drafts", "drafts"), ("Trash", "trash"), ("Archive", "archive"))]

    def page(self, folder, search="", offset=0, limit=25, unread=None):
        incoming = [InboundMessage(provider_id=f"INBOX:1:{index}", subject=f"Application for welder position {index}",
                                   from_name=f"Sender {index % 97}", from_address=f"sender{index % 97}@people.example.test",
                                   body="Dear team, please find my resume attached.", received_at=NOW - timedelta(minutes=index), unread=True,
                                   attachments=(), uid=index, uid_validity="1", folder="INBOX") for index in range(1, 26)]
        return MailboxCursor("1", 500), 2000, incoming


@pytest.fixture(scope="module")
def client(world):
    from app.api.routers import (candidate_invoices, inbox, invoice_payments, reference_agencies, whatsapp, whatsapp_chat)

    app = FastAPI()
    for module in (inbox, whatsapp, whatsapp_chat, reference_agencies, candidate_invoices, invoice_payments):
        app.include_router(module.router)
    read_sessions = sessionmaker(bind=world.engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False, expire_on_commit=False)

    def production_like_db(request: Request):
        # The same rule as app.db.session.get_db: a GET gets an autocommit session unless the handler is marked writes_on_get.
        endpoint = request.scope.get("endpoint")
        read_only = request.method in ("GET", "HEAD") and not getattr(endpoint, "writes_on_get", False)
        db = (read_sessions if read_only else world.sessions)()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = production_like_db
    world.meter = Meter(world.engine)
    world.meter.install()
    mailbox_patches = [patch("app.api.routers.zoho_mailbox.WritableMailbox", FakeMailbox),
                       patch("app.services.mail_providers.account_config", lambda db, settings, account: object())]
    for item in mailbox_patches:
        item.start()
    try:
        with TestClient(app, base_url="https://testserver") as test_client:
            yield test_client
    finally:
        for item in mailbox_patches:
            item.stop()


# ----------------------------------------------------------------------------------------------- the endpoints

ENDPOINTS = [
    # name, path, roles allowed to call it (others would get 403 and are not measured)
    ("baseline: signed-in GET (providers)", "/api/inbox/providers", ("admin",)),
    ("inbox list (50)", "/api/inbox?limit=50", ("admin", "staff")),
    ("inbox list, search", "/api/inbox?limit=50&q=welder%2042", ("admin", "staff")),
    ("inbox list, one mailbox unread", "/api/inbox?limit=50&account_ids=box-01&unread=true", ("admin", "staff")),
    ("inbox single message", "/api/inbox?message_id=msg-box-00-00004&limit=1", ("admin", "staff")),
    ("inbox health", "/api/inbox/health", ("admin",)),
    ("inbox revision", "/api/inbox/revision", ("admin", "staff")),
    ("inbox pulse", "/api/inbox/pulse", ("admin", "staff")),
    ("inbox unread-count", "/api/inbox/unread-count", ("admin", "staff")),
    ("inbox attention", "/api/inbox/attention", ("admin", "manager")),
    ("inbox ingested list", "/api/inbox/ingested", ("admin", "staff")),
    ("inbox ingested, filtered", "/api/inbox/ingested?status=review&q=welder", ("admin", "staff")),
    ("inbox ingested summary", "/api/inbox/ingested/summary", ("admin", "staff")),
    ("mailbox folders (cached)", "/api/inbox/accounts/box-00/folders", ("admin", "staff")),
    ("mailbox page (live)", "/api/inbox/accounts/box-00/mailbox?folder=INBOX&limit=25", ("admin", "staff")),
    ("message replies", "/api/inbox/messages/msg-box-00-00004/replies", ("admin", "staff")),
    ("candidate email evidence", "/api/inbox/candidates/cand-00004/evidence", ("admin", "staff")),
    ("mailbox history status", "/api/inbox/accounts/box-00/history", ("admin",)),
    ("drafts list", "/api/inbox/drafts", ("admin", "staff")),
    ("one draft", "/api/inbox/drafts/draft-001", ("admin",)),
    ("draft attempts", "/api/inbox/drafts/draft-001/attempts", ("admin",)),
    ("whatsapp inbox", "/api/whatsapp?page_size=20", ("admin", "staff")),
    ("whatsapp inbox, filtered", "/api/whatsapp?page_size=20&status=failed&q=contact", ("admin", "staff")),
    ("whatsapp health", "/api/whatsapp/health", ("admin",)),
    ("whatsapp conversations", "/api/whatsapp/conversations?page_size=20", ("admin", "staff")),
    ("whatsapp conversation of a candidate", "/api/whatsapp/candidates/cand-00001/conversation", ("admin", "staff")),
    ("whatsapp chat messages", "/api/whatsapp/conversations/conv-0001/messages?limit=40", ("admin", "staff")),
    ("whatsapp templates", "/api/whatsapp/templates", ("admin", "staff")),
    ("reference agencies", "/api/reference-agencies", ("admin", "staff")),
    ("invoice settings", "/api/invoice-settings", ("admin",)),
    ("candidate invoice", "/api/candidates/cand-00003/invoice", ("admin",)),
    ("candidate invoice payments", "/api/candidates/cand-00003/invoice/payments", ("admin",)),
    ("invoice finance summary", "/api/invoices/finance-summary", ("admin",)),
]
SESSION = {"admin": "s-admin", "manager": "s-manager", "staff": "s-staff"}
CASES = [(name, path, role) for name, path, roles in ENDPOINTS for role in roles]

# Filled by the measuring test and read by the budget test (they run in this order, in this module).
RESULTS: dict[tuple[str, str], dict] = {}


def call(client, world, path, role, sleep=0.0):
    client.cookies.set(SESSION_COOKIE, SESSION[role])
    with world.meter.measure(sleep) as meter:
        started = time.perf_counter()
        response = client.get(path, headers={"Accept-Encoding": "identity"})
        elapsed = (time.perf_counter() - started) * 1000
    assert response.status_code == 200, (path, role, response.status_code, response.text[:200])
    return {"statements": len(meter.statements), "trips": len(meter.statements) + meter.extra, "ms": elapsed,
            "kb": len(response.content) / 1024, "sql": list(meter.statements)}


def table(rows, title):
    lines = [f"\n{title}", f"{'endpoint':42} {'role':8} {'stmts':>5} {'trips':>5} {'ms':>7} {'sim ms':>7} {'KB':>7}"]
    for (name, role), item in rows.items():
        lines.append(f"{name:42} {role:8} {item['statements']:>5} {item['trips']:>5} {item['ms']:>7.1f} {item.get('sim', 0):>7.0f} {item['kb']:>7.1f}")
    print("\n".join(lines))


def test_measure_every_read_endpoint(client, world):
    for name, path, role in CASES:
        call(client, world, path, role)  # warm the plans and imports once
        item = call(client, world, path, role)
        item["sim"] = call(client, world, path, role, sleep=LATENCY)["ms"]
        RESULTS[(name, role)] = item
    table(RESULTS, f"Seeded: 3 mailboxes, ~2,000 messages ({LATENCY * 1000:.0f} ms simulated hop in 'sim ms'; production is ~300 ms)")


# Statement budgets: what each request sends today (measured) with no slack, so a change that adds a round trip fails here
# and has to say so. "trips" counts BEGIN / COMMIT / ROLLBACK as the round trips they are on a real connection;
# autocommit GETs have none. Cold answers: the 3 s reader-heartbeat memo is why "inbox health" may send two more.
BUDGET = {  # (name, role) -> (max statements, max round trips)
    ("baseline: signed-in GET (providers)", "admin"): (1, 1),
    ("inbox list (50)", "admin"): (7, 7),
    ("inbox list (50)", "staff"): (7, 7),
    ("inbox list, search", "admin"): (7, 7),
    ("inbox list, search", "staff"): (7, 7),
    ("inbox list, one mailbox unread", "admin"): (7, 7),
    ("inbox list, one mailbox unread", "staff"): (7, 7),
    ("inbox single message", "admin"): (6, 6),
    ("inbox single message", "staff"): (6, 6),
    ("inbox health", "admin"): (5, 5),
    ("inbox revision", "admin"): (2, 2),
    ("inbox revision", "staff"): (2, 2),
    ("inbox pulse", "admin"): (3, 3),
    ("inbox pulse", "staff"): (3, 3),
    ("inbox unread-count", "admin"): (2, 2),
    ("inbox unread-count", "staff"): (2, 2),
    ("inbox attention", "admin"): (5, 5),
    ("inbox attention", "manager"): (5, 5),
    ("inbox ingested list", "admin"): (2, 2),
    ("inbox ingested list", "staff"): (2, 2),
    ("inbox ingested, filtered", "admin"): (2, 2),
    ("inbox ingested, filtered", "staff"): (2, 2),
    ("inbox ingested summary", "admin"): (4, 4),
    ("inbox ingested summary", "staff"): (4, 4),
    ("mailbox folders (cached)", "admin"): (2, 4),
    ("mailbox folders (cached)", "staff"): (2, 4),
    ("mailbox page (live)", "admin"): (6, 12),
    ("mailbox page (live)", "staff"): (6, 12),
    ("message replies", "admin"): (2, 2),
    ("message replies", "staff"): (2, 2),
    ("candidate email evidence", "admin"): (3, 3),
    ("candidate email evidence", "staff"): (3, 3),
    ("mailbox history status", "admin"): (2, 2),
    ("drafts list", "admin"): (5, 5),
    ("drafts list", "staff"): (5, 5),
    ("one draft", "admin"): (4, 4),
    ("draft attempts", "admin"): (3, 3),
    ("whatsapp inbox", "admin"): (5, 5),
    ("whatsapp inbox", "staff"): (5, 5),
    ("whatsapp inbox, filtered", "admin"): (5, 5),
    ("whatsapp inbox, filtered", "staff"): (5, 5),
    ("whatsapp health", "admin"): (3, 3),
    ("whatsapp conversations", "admin"): (3, 3),
    ("whatsapp conversations", "staff"): (3, 3),
    ("whatsapp conversation of a candidate", "admin"): (2, 2),
    ("whatsapp conversation of a candidate", "staff"): (2, 2),
    ("whatsapp chat messages", "admin"): (3, 3),
    ("whatsapp chat messages", "staff"): (3, 3),
    ("whatsapp templates", "admin"): (2, 2),
    ("whatsapp templates", "staff"): (2, 2),
    ("reference agencies", "admin"): (2, 2),
    ("reference agencies", "staff"): (2, 2),
    ("invoice settings", "admin"): (2, 2),
    ("candidate invoice", "admin"): (6, 6),
    ("candidate invoice payments", "admin"): (4, 4),
    ("invoice finance summary", "admin"): (4, 4),
}


def test_statement_budgets(client, world):
    if not RESULTS:
        test_measure_every_read_endpoint(client, world)
    assert set(RESULTS) == set(BUDGET), "every measured endpoint needs a budget (and every budget an endpoint)"
    over = [(key, item["statements"], item["trips"], BUDGET[key]) for key, item in RESULTS.items()
            if item["statements"] > BUDGET[key][0] or item["trips"] > BUDGET[key][1]]
    assert not over, over


# ----------------------------------------------------------------------------------------------- what the answers say

def get_json(client, path, role="admin"):
    client.cookies.set(SESSION_COOKIE, SESSION[role])
    response = client.get(path, headers={"Accept-Encoding": "identity"})
    assert response.status_code == 200, (path, response.status_code, response.text[:200])
    return response.json()


def count_where(world, *clauses):
    with world.engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM email_messages WHERE remote_deleted IS FALSE AND " + " AND ".join(clauses))).scalar()


def test_inbox_list_pages_totals_and_order_are_exact(client, world):
    everything = count_where(world, "folder = 'INBOX'")
    first = get_json(client, "/api/inbox?limit=50")
    assert first["total"] == everything and len(first["messages"]) == 50 and first["hasMore"] is True
    stamps = [item["receivedAt"] for item in first["messages"]]
    assert stamps == sorted(stamps, reverse=True) and {item["folder"] for item in first["messages"]} == {"INBOX"}
    assert set(first) >= {"intake", "accounts", "total", "offset", "limit", "hasMore", "scope", "messages", "jobs", "worker"}
    second = get_json(client, "/api/inbox?limit=50&offset=50")
    assert not {m["id"] for m in first["messages"]} & {m["id"] for m in second["messages"]}
    last = get_json(client, f"/api/inbox?limit=50&offset={everything - 10}")
    assert len(last["messages"]) == 10 and last["hasMore"] is False and last["total"] == everything
    past = get_json(client, f"/api/inbox?limit=50&offset={everything + 500}")
    assert past["messages"] == [] and past["total"] == everything and past["hasMore"] is False
    one_box = get_json(client, "/api/inbox?limit=50&account_ids=box-01")
    assert one_box["total"] == count_where(world, "folder = 'INBOX'", "account_id = 'box-01'")
    assert {m["accountId"] for m in one_box["messages"]} == {"box-01"}
    nobody = get_json(client, "/api/inbox?limit=50&account_ids=")
    assert nobody["total"] == 0 and nobody["messages"] == []
    unread = get_json(client, "/api/inbox?limit=50&unread=true")
    assert unread["total"] == count_where(world, "folder = 'INBOX'", "unread IS TRUE") and all(m["unread"] for m in unread["messages"])
    stored = get_json(client, "/api/inbox?limit=50&scope=stored")
    assert stored["total"] == count_where(world, "TRUE") and {m["folder"] for m in stored["messages"]} == {"INBOX", "Sent"}
    found = get_json(client, "/api/inbox?limit=50&q=position%20121")
    assert found["total"] == count_where(world, "folder = 'INBOX'", "subject ILIKE '%position 121%'") and found["total"] > 0
    missing = get_json(client, "/api/inbox?limit=50&q=no-such-words-anywhere")
    assert missing["total"] == 0 and missing["messages"] == []
    one = get_json(client, "/api/inbox?message_id=msg-box-00-00004&limit=1")
    assert [m["id"] for m in one["messages"]] == ["msg-box-00-00004"] and one["total"] == 1
    assert one["messages"][0]["bodyComplete"] is True and len(one["messages"][0]["attachments"]) == 1
    assert len(first["messages"][0]["body"]) <= 600 and first["messages"][0]["bodyComplete"] is False


def test_the_plain_inbox_page_asks_the_database_for_no_window_count(client, world):
    call(client, world, "/api/inbox?limit=50", "admin")
    plain = call(client, world, "/api/inbox?limit=50", "admin")["sql"]
    assert not [sql for sql in plain if "OVER (" in sql.upper() or "OVER()" in sql.upper()], plain
    filtered = call(client, world, "/api/inbox?limit=50&unread=true", "admin")["sql"]
    window = next((sql for sql in filtered if "count(*) OVER ()" in sql), None)
    assert window, "a filtered page needs its matches counted"
    assert "SELECT email_messages.id AS id, count(*) OVER () AS total FROM email_messages" in window, "the window count must run over ids only"


def test_inbox_health_reports_both_readers_on_an_autocommit_read(client, world):
    from app.services import worker_heartbeat

    worker_heartbeat._cache.clear()
    health = get_json(client, "/api/inbox/health")
    assert set(health["reader"]) >= {"alive"} and set(health["mailReader"]) >= {"alive"}


def test_an_old_pending_reply_is_settled_in_its_own_transaction(client, world):
    with world.sessions() as db:
        db.execute(insert(MailReply), [{"id": "reply-old", "message_id": "msg-box-00-00004", "account_id": "box-00", "actor_id": "u-admin",
                                        "request_key": "reply-request-key-0002", "body": "Maybe", "status": "sending",
                                        "created_at": NOW - timedelta(minutes=10)}])
        db.commit()
    replies = get_json(client, "/api/inbox/messages/msg-box-00-00004/replies")
    assert sorted(item["status"] for item in replies) == ["sent", "unknown"]
    assert "Check the Sent folder" in next(item for item in replies if item["status"] == "unknown")["lastError"]
    with world.engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM email_replies WHERE id = 'reply-old'")).scalar() == "unknown"
    quiet = call(client, world, "/api/inbox/messages/msg-box-00-00004/replies", "admin")
    assert quiet["statements"] == 2  # sign-in + the replies: nothing is left to settle
    client.cookies.set(SESSION_COOKIE, SESSION["admin"])
    assert client.get("/api/inbox/messages/no-such-message/replies").status_code == 404


def test_the_chat_page_joins_each_message_to_its_own_row_and_pages_backwards(client, world):
    from urllib.parse import quote

    newest = get_json(client, "/api/whatsapp/conversations/conv-0001/messages?limit=10")
    assert newest["conversation"]["id"] == "conv-0001" and len(newest["messages"]) == 10 and newest["nextBefore"]
    stamps = [item["at"] for item in newest["messages"]]
    assert stamps == sorted(stamps) and {item["direction"] for item in newest["messages"]} <= {"inbound", "outbound"}
    older = get_json(client, f"/api/whatsapp/conversations/conv-0001/messages?limit=10&before={quote(newest['nextBefore'])}")
    assert not {item["id"] for item in newest["messages"]} & {item["id"] for item in older["messages"]}
    assert max(item["at"] for item in older["messages"]) <= min(stamps)
    everything = get_json(client, "/api/whatsapp/conversations/conv-0001/messages?limit=100")
    assert {item["direction"] for item in everything["messages"]} == {"inbound", "outbound"}
    assert any(item["state"] == "sent" and item["type"] == "text" for item in everything["messages"] if item["direction"] == "outbound")
    assert all(item["fileUrl"] is None or item["fileUrl"].startswith("/api/whatsapp/") for item in everything["messages"])
    client.cookies.set(SESSION_COOKIE, SESSION["admin"])
    assert client.get("/api/whatsapp/conversations/no-such/messages").status_code == 404
    assert client.get("/api/whatsapp/conversations/conv-0001/messages?before=invalid").status_code == 422


def test_a_candidates_conversation_comes_back_in_one_read_with_candidate_and_channel(client, world):
    found = get_json(client, "/api/whatsapp/candidates/cand-00001/conversation")["conversation"]
    assert found["candidate"]["id"] == "cand-00001" and found["phoneNumberId"] == "wa-phone-1" and found["channelEnabled"] is False
    assert get_json(client, "/api/whatsapp/candidates/cand-00002/conversation") == {"conversation": None}


def test_the_whatsapp_inbox_pages_and_filters(client, world):
    page = get_json(client, "/api/whatsapp?page_size=20")
    assert page["total"] == 1500 and len(page["messages"]) == 20 and len(page["channels"]) == 1
    assert get_json(client, "/api/whatsapp?page_size=20&status=failed")["total"] == sum(1 for index in range(1500) if index % 8 == 4)


def test_draft_lists_and_one_draft_read_their_files_together(client, world):
    with world.sessions() as db:
        db.execute(text("INSERT INTO email_draft_attachments (id, draft_id, file_name, content_type, size_bytes, object_key, content_hash) "
                        "VALUES ('file-1', 'draft-001', 'cv.pdf', 'application/pdf', 10, 'k', 'h')"))
        db.commit()
    listing = get_json(client, "/api/inbox/drafts")
    assert listing["total"] == 30 and len(listing["drafts"]) == 30
    with_file = next(item for item in listing["drafts"] if item["id"] == "draft-001")
    assert [item["name"] for item in with_file["attachments"]] == ["cv.pdf"]
    assert get_json(client, "/api/inbox/drafts/draft-001")["attachments"] == with_file["attachments"]
    assert get_json(client, "/api/inbox/drafts/draft-001/attempts") == {"attempts": []}
    assert get_json(client, "/api/inbox/drafts?search=draft%202&limit=5")["total"] == 11  # Draft 2, Draft 20 .. 29
    client.cookies.set(SESSION_COOKIE, SESSION["staff"])
    assert client.get("/api/inbox/drafts/draft-001").status_code == 404  # someone else's draft


def test_the_signed_in_check_is_one_statement_and_then_remembered(client, world):
    cold = call(client, world, "/api/inbox/providers", "admin")
    assert cold["statements"] == 1 and "auth_sessions" in cold["sql"][0]
    client.cookies.set(SESSION_COOKIE, SESSION["admin"])
    world.meter.statements, world.meter.on = [], True  # no cache reset: the second call is answered from the 30 s memo
    try:
        assert client.get("/api/inbox/providers").status_code == 200
    finally:
        world.meter.on = False
    assert world.meter.statements == []


def test_the_live_feed_checks_the_session_with_one_autocommit_round_trip(client, world, monkeypatch):
    from app.api.routers import events

    monkeypatch.setattr(events, "ReadSessionLocal", sessionmaker(bind=world.engine.execution_options(isolation_level="AUTOCOMMIT"),
                                                                 autoflush=False, expire_on_commit=False))
    with world.meter.measure() as meter:
        assert events._identify("s-staff") == ("u-staff", "staff")
        assert events._identify("no-such-session") is None
    assert len(meter.statements) == 2 and meter.extra == 0  # one SELECT each, no BEGIN / ROLLBACK


def test_cost_does_not_grow_with_mailboxes_or_messages(client, world):
    """Ten mailboxes and five times the mail: the statement counts must not change (only the rows each one reads)."""
    if not RESULTS:
        test_measure_every_read_endpoint(client, world)
    with world.sessions() as db:
        seed_mailboxes(db, 3, 7)
        boxes = [f"box-{index:02d}" for index in range(3, 10)]
        seed_messages(db, boxes, 667, world.candidates)
        seed_messages(db, ["box-00", "box-01", "box-02"], 2600, world.candidates, start=667)
        db.commit()
        db.execute(text("ANALYZE"))
        db.commit()
        assert db.execute(text("select count(*) from email_messages")).scalar() > 10_000
    grown = {}
    for name, path, role in CASES:
        call(client, world, path, role)
        best = min((call(client, world, path, role) for _ in range(3)), key=lambda item: item["ms"])
        best["sim"] = call(client, world, path, role, sleep=LATENCY)["ms"]
        grown[(name, role)] = best
    table(grown, "Grown: 10 mailboxes, ~10,000 messages, 1,200+ intake jobs")
    worse = [(key, RESULTS[key]["statements"], item["statements"]) for key, item in grown.items() if item["statements"] > RESULTS[key]["statements"]]
    assert not worse, worse
