"""Disposable PostgreSQL contract tests; every provider request is mocked."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
import json
import smtplib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Attachment, AuditEntry, Candidate, CandidateStatus, EmailIngestionJob, MailAccount, MailAttachment, MailMessage, MailOAuthState, MailReply, Role, Source, User
from test_database_naming import _migrate, schema_database

with patch("app.core.config.get_settings", return_value=SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused")):
    from app.api.routers import inbox, mail_actions
    from app.services import mail_oauth
from app.integrations.mail import smtp
from app.integrations.mail.imap import MailAdapterError, MailConfig, IMAPMailbox, mailbox_source_id
from app.integrations.mail.base import InboundAttachment, InboundMessage


@pytest.fixture
def mailbox(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, expire_on_commit=False, autoflush=False)
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", email_ingestion_enabled=True,
        email_accounts_json=None, imap_server="imappro.zoho.in", imap_port=993, imap_username="inbox@example.test",
        imap_password="fake-app-password", imap_folder="INBOX", imap_use_ssl=True, smtp_server="smtppro.zoho.in",
        smtp_port=587, smtp_use_ssl=False, mail_token_encryption_key=Fernet.generate_key().decode(),
        gmail_client_id="fake-client", gmail_client_secret="fake-secret", gmail_redirect_uri="https://crm.example.test/api/inbox/oauth/gmail/callback",
        microsoft_client_id="fake-ms-client", microsoft_client_secret="fake-ms-secret",
        microsoft_redirect_uri="https://crm.example.test/api/inbox/oauth/microsoft/callback", microsoft_tenant="common")
    monkeypatch.setattr(inbox, "get_settings", lambda: settings)
    monkeypatch.setattr(mail_actions, "get_settings", lambda: settings)
    env = SimpleNamespace(sessions=sessions, settings=settings, actor=SimpleNamespace(id="admin", role=Role.admin))
    with sessions() as db:
        db.add_all([User(id="admin", name="Admin", email="admin@example.test", role=Role.admin), User(id="staff", name="Staff", email="staff@example.test", role=Role.staff)])
        db.add(MailAccount(id="account", address="inbox@example.test", provider="imap"))
        db.flush()
        for index in range(4):
            db.add(MailMessage(id=f"message-{index}", account_id="account", provider_message_id=str(index),
                from_address="candidate@example.test", subject="Welder" if index < 3 else "Electrician",
                body=f"Message {index}", received_at=datetime(2026, 10, 20, 10, index, tzinfo=timezone.utc),
                message_id_header=f"<original-{index}@example.test>", reply_to="reply@example.test"))
        db.flush()
        db.add(MailAttachment(id="attachment", message_id="message-0", file_name="resume.pdf", object_key="original.pdf"))
        db.commit()
    (tmp_path / "original.pdf").write_bytes(b"%PDF-1.7 test")
    app = FastAPI(); app.include_router(inbox.router)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[inbox.get_db] = database
    app.dependency_overrides[inbox.current_user] = lambda: env.actor
    with TestClient(app) as client:
        client.cookies.set("crm_session", "synthetic-admin-session")
        env.client = client
        yield env


def test_inbox_server_filters_paging_read_original_and_disconnect_retention(mailbox):
    env = mailbox
    first = env.client.get("/api/inbox?q=welder&limit=2").json()
    second = env.client.get("/api/inbox?q=welder&limit=2&offset=2").json()
    assert first["total"] == 3 and first["hasMore"] and len(first["messages"]) == 2
    assert len(second["messages"]) == 1 and not second["hasMore"]
    assert not ({row["id"] for row in first["messages"]} & {row["id"] for row in second["messages"]})
    assert env.client.get("/api/inbox?account_ids=").json()["messages"] == []
    assert env.client.get("/api/inbox?q=%25").json()["messages"] == []
    assert env.client.patch("/api/inbox/messages/message-0/read", json={"unread": False}).status_code == 200
    assert env.client.get("/api/inbox?unread=true").json()["total"] == 3
    file = env.client.get("/api/inbox/attachments/attachment")
    assert file.content.startswith(b"%PDF") and file.headers["content-disposition"].startswith("inline")
    assert env.client.get("/api/inbox/attachments/attachment?download=true").headers["content-disposition"].startswith("attachment")
    assert env.client.post("/api/inbox/accounts/account/disconnect").status_code == 200
    after = env.client.get("/api/inbox").json()
    assert after["total"] == 4 and after["accounts"][0]["connectionStatus"] == "disconnected"
    assert env.client.post("/api/inbox/accounts/account/sync").status_code == 409
    assert env.client.post("/api/inbox/messages/message-0/reply", json={"body": "Hello", "request_key": "disconnected-request"}).status_code == 409
    assert env.client.get("/api/inbox/attachments/attachment").status_code == 200


@pytest.mark.parametrize("provider", ["gmail", "microsoft"])
def test_retired_oauth_providers_never_start_or_exchange_grants(mailbox, monkeypatch, provider):
    env = mailbox
    calls = []
    def token_post(url, *, data, timeout):
        calls.append(data)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"access_token": "access-secret", "refresh_token": "refresh-secret", "expires_in": 3600})
    monkeypatch.setattr(mail_oauth.httpx, "post", token_post)
    fake = MagicMock(); fake.__enter__.return_value.probe.return_value = SimpleNamespace(uid_validity="123", last_uid=99)
    monkeypatch.setattr(mail_oauth, "IMAPMailbox", lambda _: fake)
    response = env.client.post(f"/api/inbox/oauth/{provider}/start", json={"address": "oauth@example.test", "auto_ingest": False})
    assert response.status_code == 410, response.text
    denied = env.client.get(f"/api/inbox/oauth/{provider}/callback", params={"state": "retired-grant", "code": "code"}, follow_redirects=False)
    assert "mail_error=" in denied.headers["location"] and not calls
    response_text = env.client.get("/api/inbox").text
    assert "access-secret" not in response_text and "refresh-secret" not in response_text and "fake-secret" not in response_text
    with env.sessions() as db:
        assert db.scalar(select(MailAccount).where(MailAccount.provider == provider)) is None
        assert db.scalar(select(MailOAuthState)) is None


@pytest.mark.parametrize("provider, disconnected", [
    ("zoho", True), ("imap", True), ("gmail", False),
    ("microsoft", False), ("unsupported", False),
])
def test_unavailable_sources_reject_intake_and_retry_but_keep_evidence_and_review(mailbox, provider, disconnected):
    env = mailbox
    with env.sessions() as db:
        account = db.get(MailAccount, "account")
        account.provider = provider
        account.disconnected_at = datetime.now(timezone.utc) if disconnected else None
        db.commit()

    response = env.client.post("/api/inbox/messages/message-0/ingest")
    assert response.status_code == 409, response.text
    assert ("Reconnect" if disconnected else "archived") in response.json()["detail"]
    checkpoint = {"attachment": {"provider_jobs": {"resume": {
        "job_id": "accepted-unknown", "status": "unknown", "idempotency_key": "retained-key",
    }}}}
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(EmailIngestionJob)) == 0
        db.add(EmailIngestionJob(id="retained-job", message_id="message-0", status="failed",
            attempts=3, last_error="Retained provider result", state=checkpoint))
        db.commit()

    retry = env.client.post("/api/inbox/jobs/retained-job/retry")
    assert retry.status_code == 409, retry.text
    with env.sessions() as db:
        receipt = db.get(EmailIngestionJob, "retained-job")
        assert receipt.status == "failed" and receipt.attempts == 3
        assert receipt.last_error == "Retained provider result" and receipt.state == checkpoint
        assert db.get(MailAttachment, "attachment").object_key == "original.pdf"
    history = env.client.get("/api/inbox?message_id=message-0").json()
    assert history["messages"][0]["body"] == "Message 0"
    assert history["jobs"][0]["status"] == "failed"
    assert env.client.get("/api/inbox/attachments/attachment?download=true").content == b"%PDF-1.7 test"
    review = env.client.post("/api/inbox/jobs/retained-job/review", json={
        "decision": "ignore", "reason": "Reviewed retained evidence without provider processing",
    })
    assert review.status_code == 200 and review.json()["status"] == "ignored"
    with env.sessions() as db:
        assert db.get(EmailIngestionJob, "retained-job").state["attachment"] == checkpoint["attachment"]


def test_reply_uses_origin_account_idempotency_and_retains_unknown_result(mailbox, monkeypatch):
    env = mailbox
    calls = []
    def deliver(config, message, body, identifier):
        calls.append((config.username, message.id, body))
        if body == "Unknown result":
            raise smtp.MailSendError("Provider disconnected after message submission", unknown=True)
        return f"<{identifier}@test>"
    monkeypatch.setattr(mail_actions, "send_reply", deliver)
    body = {"body": "Hello candidate", "request_key": "stable-reply-request"}
    first = env.client.post("/api/inbox/messages/message-0/reply", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "sent"
    assert env.client.post("/api/inbox/messages/message-0/reply", json=body).json()["id"] == first.json()["id"]
    assert calls == [("inbox@example.test", "message-0", "Hello candidate")]
    assert env.client.post("/api/inbox/messages/message-1/reply", json=body).status_code == 409
    unknown = {"body": "Unknown result", "request_key": "unknown-reply-request"}
    for _ in range(2):
        assert env.client.post("/api/inbox/messages/message-0/reply", json=unknown).json()["status"] == "unknown"
    assert len(calls) == 2
    assert [row["status"] for row in env.client.get("/api/inbox/messages/message-0/replies").json()] == ["sent", "unknown"]


def test_admin_only_connections_and_unsupported_callback_do_not_touch_provider(mailbox):
    env = mailbox; env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert env.client.post("/api/inbox/oauth/gmail/start", json={"address": "a@example.test"}).status_code == 403
    assert env.client.post("/api/inbox/accounts/account/disconnect").status_code == 403
    assert env.client.get("/api/inbox").json()["providers"] == []


def test_smtp_oauth_headers_and_ambiguous_disconnect(monkeypatch):
    server = MagicMock()
    monkeypatch.setattr(smtp.smtplib, "SMTP", lambda *args, **kwargs: server)
    config = MailConfig(host="imap.example.test", port=993, username="source@example.test", password="", access_token="secret", smtp_host="smtp.example.test")
    message = SimpleNamespace(reply_to="target@example.test", from_address="original@example.test", subject="Recruitment",
        message_id_header="<original@example.test>", references_header="<thread@example.test>")
    result = smtp.send_reply(config, message, "A human reply", "reply-id")
    args, kwargs = server.send_message.call_args
    assert kwargs == {"from_addr": "source@example.test", "to_addrs": ["target@example.test"]}
    assert args[0]["In-Reply-To"] == "<original@example.test>" and result == "<reply-id@crm-reply.local>"
    assert server.starttls.called and server.auth.call_args.args[0] == "XOAUTH2"
    server.send_message.side_effect = smtplib.SMTPServerDisconnected("secret-error")
    with pytest.raises(smtp.MailSendError) as exc:
        smtp.send_reply(config, message, "test", "next-id")
    assert exc.value.unknown and "secret-error" not in str(exc.value)


def test_imap_oauth_authentication_does_not_use_password(monkeypatch):
    server = MagicMock(); server.authenticate.return_value = ("OK", [])
    monkeypatch.setattr("app.integrations.mail.imap.imaplib.IMAP4_SSL", lambda *args, **kwargs: server)
    config = MailConfig(host="imap.example.test", port=993, username="source@example.test", password="", access_token="fake-token")
    with IMAPMailbox(config):
        pass
    assert not server.login.called
    mechanism, callback = server.authenticate.call_args.args
    assert mechanism == "XOAUTH2" and b"auth=Bearer fake-token" in callback(b"")


def test_concurrent_reply_request_sends_only_once(mailbox, monkeypatch):
    env = mailbox
    sends = []
    monkeypatch.setattr(mail_actions, "send_reply", lambda *args: sends.append(args[3]) or "<sent@test>")
    barrier = Barrier(2)
    def submit():
        with env.sessions() as db:
            barrier.wait(timeout=5)
            return mail_actions.reply("message-0", mail_actions.ReplyIn(body="Concurrent human reply", request_key="one-concurrent-request"), db, env.actor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert len(sends) == 1 and responses[0]["id"] == responses[1]["id"]


def test_admin_review_links_original_selected_pages_and_preserves_candidate_fields(mailbox):
    env = mailbox
    with env.sessions() as db:
        db.add(Candidate(id="verified", ref="C-VERIFIED", name="Human corrected name", phone="9999999999", owner_id="staff", source=Source.walk_in, status=CandidateStatus.in_process))
        db.add(EmailIngestionJob(id="review-job", message_id="message-0", status="needs_review", state={"attachment": {"documents": {
            "resume": {"object_key": "selected.pdf", "file_name": "resume.pdf", "content_hash": "a" * 64, "content_type": "application/pdf", "size_kb": 1},
        }}}))
        db.add(EmailIngestionJob(id="ignore-job", message_id="message-1", status="failed", state={}))
        db.commit()
    payload = {"decision": "link", "candidate_id": "verified", "reason": "Verified identity against passport and phone."}
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert env.client.post("/api/inbox/jobs/review-job/review", json=payload).status_code == 403
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    assert env.client.post("/api/inbox/jobs/review-job/review", json={**payload, "reason": "  "}).status_code == 422
    linked = env.client.post("/api/inbox/jobs/review-job/review", json=payload)
    assert linked.status_code == 200, linked.text
    assert linked.json()["documentsLinked"] == 2
    assert env.client.post("/api/inbox/jobs/review-job/review", json=payload).status_code == 409
    ignored = env.client.post("/api/inbox/jobs/ignore-job/review", json={"decision": "ignore", "reason": "This receipt is a non-recruitment invoice."})
    assert ignored.json()["status"] == "ignored"
    with env.sessions() as db:
        candidate = db.get(Candidate, "verified")
        assert candidate.name == "Human corrected name" and candidate.owner_id == "staff" and candidate.status == CandidateStatus.in_process
        assert set(db.scalars(select(Attachment.slot).where(Attachment.entity_id == "verified"))) == {"source_bundle", "resume"}
        assert db.get(MailMessage, "message-0").candidate_id == "verified"
        assert db.get(EmailIngestionJob, "review-job").state["manual_review"]["reason"] == payload["reason"]
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_id == "verified")) == 1


def test_expired_oauth_state_never_contacts_provider(mailbox, monkeypatch):
    env = mailbox
    monkeypatch.setattr(mail_oauth.httpx, "post", lambda *args, **kwargs: pytest.fail("Expired grants must not contact provider"))
    response = env.client.post("/api/inbox/oauth/gmail/start", json={"address": "expired@example.test"})
    assert response.status_code == 410
    response = env.client.get("/api/inbox/oauth/gmail/callback", params={"state": "old-expired-state", "code": "expired"}, follow_redirects=False)
    assert "mail_error=" in response.headers["location"]


def test_historical_import_is_bounded_resumable_and_does_not_rewind_new_mail_or_queue_ocr(mailbox, monkeypatch):
    from app.services import mail_history
    env = mailbox
    with env.sessions() as db:
        account = db.get(MailAccount, "account")
        config = mail_oauth.account_config(db, env.settings, account)
        account.sync_state = {"uid_validity": "123", "last_uid": 10, "source_id": mailbox_source_id(config)}
        db.commit()
    fetched = []
    class HistoricalMailbox:
        def __init__(self, config): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def history_uids(self, *, after_uid, before_uid, since, uid_validity, limit):
            assert before_uid == 10 and since == "2026-01-01" and uid_validity == "123"
            return [uid for uid in (1, 2, 3) if after_uid < uid <= before_uid][:limit]
        def fetch(self, uid, *, uid_validity):
            fetched.append(uid)
            return InboundMessage(provider_id=f"history-{uid}", subject="Historical source", from_name="Applicant",
                from_address="old@example.test", body="Historical body", received_at=datetime.now(timezone.utc), unread=True,
                attachments=(InboundAttachment(provider_attachment_id=f"part-{uid}", filename="original.pdf", mime="application/pdf",
                    data=b"%PDF-1.7 history", content_hash="b" * 64),))
    monkeypatch.setattr(mail_history, "IMAPMailbox", HistoricalMailbox)
    started = env.client.post("/api/inbox/accounts/account/history", json={"since": "2026-01-01", "limit": 2})
    assert started.status_code == 202, started.text
    assert not started.json()["auto_ingest"]
    assert env.client.post("/api/inbox/accounts/account/history", json={"since": "2026-01-01", "limit": 2}).status_code == 409
    assert env.client.post("/api/inbox/accounts/account/history/pause").json()["status"] == "paused"
    mail_history.receive_history("account", sessions=env.sessions, settings=env.settings)
    assert fetched == []
    assert env.client.post("/api/inbox/accounts/account/history/resume", json={"limit": 2}).status_code == 202
    mail_history.receive_history("account", sessions=env.sessions, settings=env.settings)
    assert fetched == [1, 2]
    assert env.client.get("/api/inbox/accounts/account/history").json()["status"] == "limited"
    env.client.post("/api/inbox/accounts/account/history/resume", json={"limit": 2})
    mail_history.receive_history("account", sessions=env.sessions, settings=env.settings)
    finished = env.client.get("/api/inbox/accounts/account/history").json()
    assert finished["status"] == "complete" and finished["imported"] == 3
    with env.sessions() as db:
        assert db.get(MailAccount, "account").sync_state["last_uid"] == 10
        assert db.scalar(select(func.count()).select_from(EmailIngestionJob)) == 0
        assert db.scalar(select(func.count()).select_from(MailAttachment)) == 4
    assert env.client.post("/api/inbox/accounts/account/history", json={"since": "2026-01-01", "limit": 501}).status_code == 422
    assert env.client.post("/api/inbox/accounts/account/history", json={"since": "2099-01-01", "limit": 2}).status_code == 422


def test_history_provider_failure_retains_checkpoint_and_requires_explicit_resume(mailbox, monkeypatch):
    from app.services import mail_history
    env = mailbox
    with env.sessions() as db:
        account = db.get(MailAccount, "account")
        config = mail_oauth.account_config(db, env.settings, account)
        account.sync_state = {"uid_validity": "123", "last_uid": 10, "source_id": mailbox_source_id(config)}
        db.commit()
    fake = MagicMock(); fake.__enter__.return_value.history_uids.side_effect = MailAdapterError("history_search", "Synthetic provider unavailable")
    monkeypatch.setattr(mail_history, "IMAPMailbox", lambda config: fake)
    env.client.post("/api/inbox/accounts/account/history", json={"since": "2026-01-01", "limit": 5})
    mail_history.receive_history("account", sessions=env.sessions, settings=env.settings)
    result = env.client.get("/api/inbox/accounts/account/history").json()
    assert result["status"] == "error" and result["processed"] == 0
    mail_history.receive_history("account", sessions=env.sessions, settings=env.settings)
    assert fake.__enter__.return_value.history_uids.call_count == 1
