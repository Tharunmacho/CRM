"""Inbox and worker boundaries using in-memory fakes, no mail or SQL connections."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError
from app.integrations.mail.imap import MailConfig, mailbox_source_id


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def account(id="account-1", address="first@example.test", **changes):
    return SimpleNamespace(
        **{
            "id": id, "address": address, "provider": "imap", "auto_ingest": False,
            "last_sync_at": None, "last_error": None, "worker_seen_at": None,
            "disconnected_at": None,
            "extraction_seen_at": None,
            "sync_requested": False, "sync_state": {
                "uid_validity": "123", "last_uid": 17,
                "source_id": mailbox_source_id(MailConfig(
                    host="imappro.zoho.com", port=993, username=address, password="synthetic-only",
                )),
            },
            "secrets": {"password": "PRIVATE-DATABASE-PASSWORD"}, **changes,
        }
    )


def message():
    return SimpleNamespace(
        id="message-1", account_id="account-1", from_name="Applicant",
        from_address="applicant@example.test", subject="Synthetic application", body="Synthetic body",
        received_at=NOW, unread=True, candidate_id=None, folder="INBOX", starred=False,
        to_addresses=[], cc_addresses=[], remote_state="synced", remote_error=None,
    )


def job(id="job-1", **changes):
    return SimpleNamespace(**{
        "id": id, "message_id": "message-1", "status": "pending", "attempts": 0,
        "last_error": None, "candidate_id": None, "updated_at": NOW,
        "next_attempt_at": NOW, "inspection": [], "state": {}, **changes,
    })


class Rows(list):
    """Small iterable result with the SQLAlchemy result API used by these routes."""

    def all(self):
        return list(self)


class Database:
    def __init__(self):
        self.accounts, self.messages, self.attachments, self.jobs = [], [], [], []
        self.bundles, self.person_groups, self.acknowledgements = [], [], []
        self.pending, self.queries, self.events = [], [], []
        self.collision = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def table(self, model):
        return {
            "MailAccount": self.accounts, "MailMessage": self.messages,
            "MailAttachment": self.attachments, "EmailIngestionJob": self.jobs,
            "MultiPersonIntake": self.bundles, "DocumentPersonGroup": self.person_groups,
            "IntakeAcknowledgement": self.acknowledgements,
        }[model.__name__]

    def _query(self, statement):
        self.queries.append(str(statement))
        entity = statement.column_descriptions[0]["entity"]
        return self.table(entity), statement.compile().params

    @staticmethod
    def _project(statement, row):
        description = statement.column_descriptions[0]
        if description["name"] == description["entity"].__name__:
            return row
        return getattr(row, description["name"])

    @staticmethod
    def _matches(row, params, names):
        for name in names:
            key = f"{name}_1"
            if key not in params:
                continue
            value = params[key]
            actual = getattr(row, name)
            if (actual not in value) if isinstance(value, (list, tuple)) else (actual != value):
                return False
        return True

    def scalars(self, statement):
        rows, params = self._query(statement)
        model = statement.column_descriptions[0]["entity"].__name__
        if model == "EmailIngestionJob" and "account_id_1" in params:
            message_ids = {item.id for item in self.messages if item.account_id in params["account_id_1"]}
            rows = [row for row in rows if row.message_id in message_ids]
        if model == "IntakeAcknowledgement":
            rows = [row for row in rows if self._matches(row, params, ("source_kind", "status"))]
            if "started_at_1" in params:
                rows = [row for row in rows if row.started_at and row.started_at < params["started_at_1"]]
        return Rows(self._project(statement, row) for row in rows)

    def scalar(self, statement):
        if statement.column_descriptions[0]["entity"] is None:
            self.queries.append(str(statement))
            return len(self.messages)
        rows, params = self._query(statement)
        for row in rows:
            if not self._matches(row, params, ("address", "message_id", "source_type", "source_id")):
                continue
            return self._project(statement, row)
        return None

    def execute(self, statement):
        self.queries.append(str(statement))
        if statement.column_descriptions[0]["entity"].__name__ == "MultiPersonIntake":
            params = statement.compile().params
            rows = Rows()
            for bundle in self.bundles:
                if not self._matches(bundle, params, ("source_type", "source_id")):
                    continue
                groups = sorted((group for group in self.person_groups if group.bundle_id == bundle.id), key=lambda group: group.ordinal)
                rows.extend((bundle.id, bundle.source_id, bundle.status, group.candidate_id if group else None) for group in (groups or [None]))
            return rows
        if statement.column_descriptions[0]["name"] == "account_id":
            return Rows()  # per-account message/unread totals; not under test here
        entity = statement.column_descriptions[0]["entity"].__name__
        if entity == "MailAccount":  # account list with a has-credentials flag; never the secret itself
            return Rows((account, account.secrets is not None) for account in self.accounts)
        if entity == "MailMessage" and len(statement.column_descriptions) == 3:  # page + body head + window total
            return Rows((message, (message.body or "")[:600], len(self.messages)) for message in self.messages)
        counts = {}
        for item in self.jobs:
            counts[item.status] = counts.get(item.status, 0) + 1
        return Rows(counts.items())

    def get(self, model, id):
        self.events.append(("get", model.__name__, id))
        return next((row for row in self.table(model) if row.id == id), None)

    def add(self, row):
        self.pending.append(row)

    def rollback(self):
        self.events.append("rollback")
        self.pending.clear()

    def commit(self):
        self.events.append("commit")
        if self.collision:
            collide, self.collision = self.collision, None
            collide()
            raise IntegrityError("Synthetic unique collision", {}, RuntimeError("duplicate"))
        for row in self.pending:
            if row.id is None:
                row.id = f"generated-{len(self.table(type(row))) + 1}"
            if type(row).__name__ == "EmailIngestionJob":
                row.status, row.attempts = row.status or "pending", row.attempts or 0
            self.table(type(row)).append(row)
        self.pending.clear()


@pytest.fixture
def environment(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://unused@127.0.0.1:1/unused",
        storage_dir=str(tmp_path), email_ingestion_enabled=True, email_poll_seconds=120,
        email_accounts_json=json.dumps([
            {"host": "imappro.zoho.com", "smtp_host": "smtppro.zoho.com", "username": "first@example.test", "password": "PRIVATE-FIRST"},
            {"host": "imappro.zoho.in", "smtp_host": "smtppro.zoho.in", "username": "second@example.test", "password": "PRIVATE-SECOND"},
        ]),
        imap_server=None, imap_username=None, imap_password=None, imap_port=993,
        imap_folder="INBOX", imap_use_ssl=True,
    )
    with patch("app.core.config.get_settings", return_value=settings):
        from app.api.routers import inbox
        from app.api.routers import mail_actions
        from app import email_worker
    from app.db.models import Role

    monkeypatch.setattr(inbox, "get_settings", lambda: settings)
    monkeypatch.setattr(mail_actions, "get_settings", lambda: settings)
    monkeypatch.setattr(email_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(email_worker, "now", lambda: NOW)
    monkeypatch.setattr(email_worker, "STOP", threading.Event())
    database, probes = Database(), []

    class Mailbox:
        def __init__(self, config):
            self.config = config

        def __enter__(self):
            assert database.events[-1] == "rollback"
            return self

        def __exit__(self, *args):
            pass

        def probe(self):
            probes.append(self.config)
            return SimpleNamespace(uid_validity="validity-9", last_uid=500)

        def __getattr__(self, name):
            raise AssertionError(f"Connecting must not import historical mail: {name}")

    monkeypatch.setattr(inbox, "IMAPMailbox", Mailbox)
    application = FastAPI()
    application.include_router(inbox.router)
    user = SimpleNamespace(id="user-1", role=Role.admin)
    application.dependency_overrides[inbox.get_db] = lambda: database
    application.dependency_overrides[inbox.current_user] = lambda: user
    with TestClient(application) as client:
        yield SimpleNamespace(
            inbox=inbox, worker=email_worker, database=database, settings=settings,
            client=client, application=application, user=user, Role=Role, probes=probes,
        )


@pytest.mark.parametrize(("method", "path", "body"), [
    ("get", "/api/inbox/health", None),
    ("post", "/api/inbox/connect", {}),
    ("patch", "/api/inbox/accounts/account-1", {"auto_ingest": True}),
    ("post", "/api/inbox/jobs/job-1/retry", None),
])
def test_admin_operations_reject_staff_before_business_queries(environment, method, path, body):
    environment.user.role = environment.Role.staff
    response = environment.client.request(method, path, json=body)
    assert response.status_code == 403
    assert environment.database.queries == environment.database.events == []
    assert environment.probes == []


def test_unauthenticated_inbox_is_rejected_without_business_queries(environment):
    del environment.application.dependency_overrides[environment.inbox.current_user]
    assert environment.client.get("/api/inbox").status_code == 401
    assert environment.database.queries == environment.database.events == []


def test_inbox_excludes_secrets_cursors_and_deferred_job_state(environment):
    database = environment.database
    database.accounts.append(account())
    database.messages.append(message())
    database.jobs.append(job(state={"provider_token": "PRIVATE-PROVIDER-RESULT"}))

    response = environment.client.get("/api/inbox")

    assert response.status_code == 200
    body = response.json()
    assert body["availableAccounts"] == ["first@example.test", "second@example.test"]
    assert "PRIVATE" not in response.text
    assert "sync_state" not in response.text and "provider_token" not in response.text
    assert not any("email_ingestion_jobs.state" in query for query in database.queries)
    assert not any("email_accounts.secrets" in query.replace("email_accounts.secrets IS NOT NULL", "") for query in database.queries)
    assert not any("email_accounts.sync_state" in query for query in database.queries)


def test_staff_inbox_does_not_resolve_server_account_configuration(environment, monkeypatch):
    environment.user.role = environment.Role.staff
    monkeypatch.setattr(environment.inbox, "configs", lambda: pytest.fail("Staff must not load credentials"))
    assert environment.client.get("/api/inbox").json()["availableAccounts"] == []


def test_connect_uses_selected_account_and_future_only_probe_cursor(environment):
    response = environment.client.post("/api/inbox/connect", json={
        "address": " SECOND@example.test ", "auto_ingest": False,
    })
    assert response.status_code == 200
    assert environment.probes[0].host == "imappro.zoho.in"
    connected, = environment.database.accounts
    assert connected.sync_state == {
        "uid_validity": "validity-9", "last_uid": 500,
        "source_id": mailbox_source_id(environment.probes[0]),
        "storage_id": environment.worker.storage_identity(environment.settings.storage_dir, environment.settings),
    }
    assert connected.auto_ingest is False
    assert connected.sync_requested is True
    assert response.json()["address"] == "second@example.test"
    assert "PRIVATE" not in response.text


def test_reconnect_preserves_cursor_and_does_not_probe_or_toggle_auto_ingest(environment):
    existing = account()
    before = deepcopy(existing.sync_state)
    environment.database.accounts.append(existing)
    response = environment.client.post("/api/inbox/connect", json={"address": existing.address, "auto_ingest": True})
    assert response.status_code == 200
    assert existing.sync_state == before
    assert existing.auto_ingest is False
    assert environment.probes == []
    assert "commit" not in environment.database.events


@pytest.mark.parametrize("binding", [None, "another-source"])
def test_reconnect_cannot_silently_rebind_or_reset_an_old_cursor(environment, binding):
    existing = account(sync_state={"uid_validity": "123", "last_uid": 999, "source_id": binding})
    environment.database.accounts.append(existing)
    before = deepcopy(existing.sync_state)
    response = environment.client.post("/api/inbox/connect", json={"address": existing.address})
    assert response.status_code == 409
    assert existing.sync_state == before
    assert environment.probes == []
    assert "commit" not in environment.database.events


def test_manual_sync_missing_account_preserves_queue_state(environment):
    response = environment.client.post("/api/inbox/accounts/account-1/sync")
    assert response.status_code == 404
    assert environment.database.events == []


def test_invalid_configuration_fails_without_returning_credential_values(environment):
    environment.settings.email_accounts_json = '{"password":"PRIVATE-INVALID"}'
    response = environment.client.post("/api/inbox/connect", json={})
    assert response.status_code == 503
    assert "PRIVATE" not in response.text
    assert environment.probes == []


@pytest.mark.parametrize("provider", ["zoho", "imap"])
def test_manual_ingest_is_idempotent_even_when_auto_ingestion_is_disabled(environment, provider):
    database = environment.database
    database.accounts.append(account(provider=provider, auto_ingest=False))
    database.messages.append(message())
    database.attachments.append(SimpleNamespace(id="attachment-1", message_id="message-1"))
    environment.user.role = environment.Role.staff

    assert environment.client.post("/api/inbox/messages/message-1/ingest").status_code == 202
    assert environment.client.post("/api/inbox/messages/message-1/ingest").status_code == 202
    assert len(database.jobs) == 1
    assert database.jobs[0].message_id == "message-1"
    assert database.accounts[0].auto_ingest is False


def test_concurrent_enqueue_unique_collision_returns_existing_job(environment):
    database = environment.database
    database.accounts.append(account())
    database.messages.append(message())
    database.attachments.append(SimpleNamespace(id="attachment-1", message_id="message-1"))
    database.collision = lambda: database.jobs.append(job(status="processing"))
    response = environment.client.post("/api/inbox/messages/message-1/ingest")
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    assert len(database.jobs) == 1
    assert "rollback" in database.events


def test_retry_rechecks_local_inspection_but_preserves_paid_job_ids_and_results(environment):
    environment.database.accounts.append(account())
    environment.database.messages.append(message())
    provider = {"resume": {"job_id": "accepted-job", "result": {"name": "Synthetic"}, "status": "succeeded"}}
    item = job(status="needs_review", attempts=2, state={"attachment-1": {
        "source_sha256": "synthetic-hash", "provider_jobs": deepcopy(provider),
        "output": {"status": "needs_review"}, "inspection": {"complete": False},
        "inspection_signature": "old-settings", "extractions": {"resume": {}},
    }})
    environment.database.jobs.append(item)

    assert environment.client.post("/api/inbox/jobs/job-1/retry").status_code == 202
    state = item.state["attachment-1"]
    assert state == {"source_sha256": "synthetic-hash", "provider_jobs": provider}
    assert item.status == "pending" and item.attempts == 0


def test_explicit_retry_restarts_only_confirmed_failed_provider_jobs(environment):
    environment.database.accounts.append(account())
    environment.database.messages.append(message())
    failed = {"job_id": "failed-job", "status": "failed", "idempotency_key": "old-key"}
    pending = {"job_id": "running-job", "status": "running", "idempotency_key": "same-key"}
    item = job(status="failed", state={"attachment-1": {
        "provider_jobs": {"resume": deepcopy(failed), "passport": deepcopy(pending)},
    }})
    environment.database.jobs.append(item)

    assert environment.client.post("/api/inbox/jobs/job-1/retry").status_code == 202
    document = item.state["attachment-1"]
    assert document["provider_jobs"] == {"passport": pending}
    assert document["provider_job_history"] == {"resume": [failed]}
    assert document["provider_generations"] == {"resume": 1}


def test_dashboard_health_uses_grouped_counts_without_message_bodies_or_job_state(environment):
    environment.database.accounts.append(account(worker_seen_at=NOW, last_sync_at=NOW))
    environment.database.jobs.extend([job(), job("job-2", status="failed"), job("job-3", status="failed")])
    response = environment.client.get("/api/inbox/health")
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {
        "pending": 1, "processing": 0, "retry": 0, "needs_review": 0,
        "failed": 2, "completed": 0, "ignored": 0,
    }
    assert body["worker"]["enabled"] is True
    assert body["worker"]["lastHeartbeat"]
    assert len(environment.database.queries) == 2
    assert "GROUP BY" in environment.database.queries[1]
    assert not any(word in " ".join(environment.database.queries) for word in (
        "email_messages", "email_ingestion_jobs.state", "email_accounts.secrets",
    ))
    assert "PRIVATE" not in response.text


def test_worker_processes_queued_jobs_without_any_http_api_call(environment, monkeypatch):
    database = environment.database
    database.accounts.extend([
        account("due", sync_requested=True), account("recent", last_sync_at=NOW),
    ])
    incoming = message()
    incoming.account_id = "due"
    database.messages.append(incoming)
    database.jobs.append(job())
    calls = []
    monkeypatch.setattr(environment.worker, "receive_account", lambda id, **kw: calls.append(("receive", id)))
    monkeypatch.setattr(environment.worker, "process_job", lambda id, **kw: calls.append(("process", id)))

    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)

    assert calls == [("receive", "due"), ("process", "job-1")]
    assert all(item.worker_seen_at == NOW for item in database.accounts)


def test_worker_contains_job_failure_applies_backoff_and_does_not_log_secrets(environment, monkeypatch, caplog):
    database = environment.database
    database.accounts.append(account(last_sync_at=NOW))
    database.messages.append(message())
    database.jobs.extend([job(), job("job-2")])
    handled = []
    def process(id, **kwargs):
        handled.append(id)
        if id == "job-1":
            raise RuntimeError("PRIVATE-PASSWORD and candidate document text")
    monkeypatch.setattr(environment.worker, "process_job", process)

    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)

    assert handled == ["job-1", "job-2"]
    assert database.jobs[0].status == "retry"
    assert database.jobs[0].attempts == 1
    assert database.jobs[0].next_attempt_at == NOW + timedelta(seconds=90)
    assert "PRIVATE" not in caplog.text
    assert "PRIVATE" not in database.jobs[0].last_error


@pytest.mark.parametrize("prior_attempts, expected_status", [(2, "retry"), (3, "failed")])
def test_worker_unexpected_error_budget_includes_three_retries(environment, monkeypatch, prior_attempts, expected_status):
    database = environment.database
    database.accounts.append(account(last_sync_at=NOW))
    database.messages.append(message())
    item = job(attempts=prior_attempts)
    database.jobs.append(item)

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic failure outside intake recovery")

    monkeypatch.setattr(environment.worker, "process_job", fail)
    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)

    assert item.attempts == prior_attempts + 1
    assert item.status == expected_status
    assert item.next_attempt_at == NOW + timedelta(seconds=90 * 2 ** prior_attempts)


def test_worker_mailbox_error_does_not_block_other_accounts_or_expose_raw_error(environment, monkeypatch, caplog):
    database = environment.database
    database.accounts.extend([account("bad"), account("good")])
    calls = []
    def receive(id, **kwargs):
        calls.append(id)
        if id == "bad":
            raise ValueError("PRIVATE-MAILBOX-PASSWORD")
    monkeypatch.setattr(environment.worker, "receive_account", receive)
    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)
    assert calls == ["bad", "good"]
    assert "PRIVATE" not in caplog.text
    assert "PRIVATE" not in database.accounts[0].last_error


def test_disabled_worker_does_not_touch_database_or_external_services(environment):
    environment.settings.email_ingestion_enabled = False
    environment.worker.run_cycle(
        sessions=lambda: pytest.fail("Disabled worker touched storage"), settings=environment.settings,
    )


def test_worker_binds_first_use_without_changing_mailbox_cursor(environment, monkeypatch):
    database = environment.database
    mailbox = account(last_sync_at=NOW)
    cursor = deepcopy(mailbox.sync_state)
    database.accounts.append(mailbox)
    monkeypatch.setattr(environment.worker, "receive_account", lambda *a, **k: pytest.fail("Unexpected sync"))

    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)

    identity = environment.worker.storage_identity(environment.settings.storage_dir)
    assert mailbox.sync_state == {**cursor, "storage_id": identity}
    assert mailbox.worker_seen_at == NOW


def test_wrong_volume_never_fetches_or_processes_mail_and_preserves_cursor(environment, monkeypatch):
    database = environment.database
    mailbox = account(sync_requested=True, sync_state={
        "uid_validity": "123", "last_uid": 17, "storage_id": "another-volume",
    })
    database.accounts.append(mailbox)
    database.messages.append(message())
    queued = job(attempts=2, state={"saved": "checkpoint"})
    database.jobs.append(queued)
    before_cursor = deepcopy(mailbox.sync_state)
    before_job = deepcopy(vars(queued))
    monkeypatch.setattr(environment.worker, "receive_account", lambda *a, **k: pytest.fail("Wrong volume fetched mail"))
    monkeypatch.setattr(environment.worker, "process_job", lambda *a, **k: pytest.fail("Wrong volume processed job"))

    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)

    assert mailbox.last_error == environment.worker.STORAGE_MISMATCH
    assert mailbox.sync_state == before_cursor
    assert mailbox.worker_seen_at is None
    assert mailbox.last_sync_at is None
    assert mailbox.sync_requested is True
    assert vars(queued) == before_job
    # Acknowledgements are independent of document storage. Volume mismatch must
    # still return before scanning the ingestion queue or acquiring job locks.
    assert not any("email_ingestion_jobs" in query or "email_messages" in query for query in database.queries)
    assert len([query for query in database.queries if "intake_acknowledgements" not in query]) == 1


def test_only_matching_volume_accounts_are_eligible_in_mixed_queue(environment, monkeypatch):
    database = environment.database
    identity = environment.worker.storage_identity(environment.settings.storage_dir)
    own = account("own", sync_requested=True, last_error=environment.worker.STORAGE_MISMATCH,
                  sync_state={"uid_validity": "123", "last_uid": 17, "storage_id": identity})
    foreign = account("foreign", sync_requested=True,
                      sync_state={"uid_validity": "456", "last_uid": 81, "storage_id": "another-volume"})
    database.accounts.extend([own, foreign])
    for index, owner in enumerate([own, foreign]):
        incoming = message()
        incoming.id, incoming.account_id = f"message-{index}", owner.id
        database.messages.append(incoming)
        database.jobs.append(job(f"job-{index}", message_id=incoming.id))
    calls = []
    monkeypatch.setattr(environment.worker, "receive_account", lambda id, **kw: calls.append(("receive", id)))
    monkeypatch.setattr(environment.worker, "process_job", lambda id, **kw: calls.append(("process", id)))

    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings)

    assert calls == [("receive", "own"), ("process", "job-0")]
    assert own.last_error is None
    assert own.worker_seen_at == NOW
    assert foreign.last_error == environment.worker.STORAGE_MISMATCH
    assert foreign.worker_seen_at is None
    queue_query = next(query for query in database.queries if "email_messages.account_id IN" in query)
    assert "JOIN email_messages" in queue_query
    assert "email_messages.account_id IN" in queue_query


def test_receiver_and_extractor_cycles_do_not_block_each_other(environment, monkeypatch):
    database = environment.database
    database.accounts.append(account(sync_requested=True))
    database.messages.append(message())
    database.jobs.append(job())
    calls = []
    monkeypatch.setattr(environment.worker, "receive_account", lambda identifier, **kwargs: calls.append(("receive", identifier)))
    monkeypatch.setattr(environment.worker, "process_job", lambda identifier, **kwargs: calls.append(("extract", identifier)))
    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings, mode="receive")
    assert calls == [("receive", "account-1")]
    calls.clear()
    environment.worker.run_cycle(sessions=lambda: database, settings=environment.settings, mode="extract")
    assert calls == [("extract", "job-1")]
