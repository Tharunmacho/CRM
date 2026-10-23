"""Any number of mailboxes, across Gmail, Microsoft 365, Zoho and generic IMAP/SMTP."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test_mail_workflow import mailbox, schema_database  # noqa: F401  (fixtures)
from app.api.routers import mail_accounts
from app.db.models import AuditEntry, MailAccount, MailMessage, Role
from app.integrations.mail.imap import MailAdapterError, MailboxCursor
from app.services import mail_oauth, mail_providers

SERVER = {"imap_host": "mail.example.test", "imap_port": 993, "smtp_host": "smtp.example.test", "smtp_port": 587}


class FakeMailbox:
    def __init__(self, config):
        self.config = config

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def probe(self): return MailboxCursor("777", 12)
    def folders(self): return [{"name": "INBOX", "role": "inbox", "total": 1, "unread": 1}]


@pytest.fixture
def imap_connect(mailbox, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mail_providers, "assert_public_host", lambda host: None)
    monkeypatch.setattr(mail_accounts, "WritableMailbox", FakeMailbox)
    return mailbox


def test_generic_imap_account_is_encrypted_audited_and_resolves_to_a_live_config(imap_connect):
    env = imap_connect
    response = env.client.post("/api/inbox/accounts/imap", json={"address": "Hiring@Example.test", "password": "synthetic-password", **SERVER})
    assert response.status_code == 200, response.text
    assert "synthetic-password" not in response.text
    body = response.json()
    assert body["provider"] == "imap" and body["address"] == "hiring@example.test" and body["autoIngest"] is True
    with env.sessions() as db:
        row = db.scalar(select(MailAccount).where(MailAccount.address == "hiring@example.test"))
        assert row.sync_state["last_uid"] == 12 and row.sync_state["uid_validity"] == "777" and row.sync_requested
        assert "synthetic-password" not in str(row.secrets)
        config = mail_providers.account_config(db, env.settings, row)
        assert (config.host, config.port, config.use_ssl, config.password) == ("mail.example.test", 993, True, "synthetic-password")
        assert (config.smtp_host, config.smtp_port, config.smtp_use_ssl) == ("smtp.example.test", 587, False)
        assert "synthetic-password" not in str(list(db.scalars(select(AuditEntry.meta))))
    listed = next(item for item in env.client.get("/api/inbox").json()["accounts"] if item["address"] == "hiring@example.test")
    assert listed["configured"] and listed["providerLabel"] == "IMAP / SMTP"


def test_accounts_are_not_limited_and_the_unified_inbox_filters_per_account(imap_connect):
    env = imap_connect
    for number in range(6):
        response = env.client.post("/api/inbox/accounts/imap", json={"address": f"desk{number}@example.test", "password": "synthetic", **SERVER})
        assert response.status_code == 200, response.text
        with env.sessions() as db:
            account = db.scalar(select(MailAccount).where(MailAccount.address == f"desk{number}@example.test"))
            db.add(MailMessage(account_id=account.id, provider_message_id=f"m{number}", from_address="c@example.test",
                subject=f"Resume {number}", body="", received_at=datetime(2026, 10, 21, 9, number, tzinfo=timezone.utc),
                folder="INBOX", unread=number % 2 == 0))
            db.commit()
    snapshot = env.client.get("/api/inbox?scope=stored&limit=100").json()
    desks = sorted((item for item in snapshot["accounts"] if item["address"].startswith("desk")), key=lambda item: item["address"])
    assert len(desks) == 6 and {item["messageCount"] for item in desks} == {1}
    assert sum(item["unreadCount"] for item in desks) == 3
    filtered = env.client.get("/api/inbox", params={"scope": "stored", "account_ids": desks[2]["id"]}).json()
    assert [item["subject"] for item in filtered["messages"]] == ["Resume 2"]
    several = env.client.get("/api/inbox", params={"scope": "stored", "account_ids": f"{desks[0]['id']},{desks[1]['id']}"}).json()
    assert {item["subject"] for item in several["messages"]} == {"Resume 0", "Resume 1"}


def test_account_auto_ingest_toggle_is_per_account(imap_connect):
    env = imap_connect
    ids = [env.client.post("/api/inbox/accounts/imap", json={"address": f"{name}@example.test", "password": "synthetic", **SERVER}).json()["id"] for name in ("a", "b")]
    assert env.client.patch(f"/api/inbox/accounts/{ids[0]}", json={"auto_ingest": False}).json()["autoIngest"] is False
    state = {item["id"]: item["autoIngest"] for item in env.client.get("/api/inbox").json()["accounts"]}
    assert state[ids[0]] is False and state[ids[1]] is True


@pytest.mark.parametrize("changes", [{"imap_port": 25}, {"smtp_port": 25}, {"imap_host": "not a host"}, {"smtp_host": ""}])
def test_imap_connection_rejects_insecure_ports_and_malformed_hosts(imap_connect, changes):
    response = imap_connect.client.post("/api/inbox/accounts/imap", json={"address": "x@example.test", "password": "synthetic", **SERVER, **changes})
    assert response.status_code == 422


@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.0.8", "::1"])
def test_private_and_local_servers_are_refused(host):
    with pytest.raises(MailAdapterError):
        mail_providers.assert_public_host(host)


def test_imap_connection_requires_admin_and_does_not_hijack_oauth_addresses(imap_connect):
    env = imap_connect
    body = {"address": "inbox@example.test", "password": "synthetic", **SERVER}
    with env.sessions() as db:
        db.get(MailAccount, "account").provider = "gmail"
        db.commit()
    taken = env.client.post("/api/inbox/accounts/imap", json=body)
    assert taken.status_code == 409 and "Gmail" in taken.json()["detail"]
    env.actor.role = Role.staff
    assert env.client.post("/api/inbox/accounts/imap", json=body).status_code == 403
    assert env.client.get("/api/inbox/providers").status_code == 403


def test_provider_readiness_lists_every_connection_method(mailbox):  # noqa: F811
    items = mailbox.client.get("/api/inbox/providers").json()["providers"]
    assert {item["provider"]: item["ready"] for item in items} == {"gmail": True, "microsoft": True, "zoho": True, "imap": True}


def test_oauth_accounts_resolve_through_the_shared_config_and_refresh_tokens(mailbox, monkeypatch):  # noqa: F811
    env = mailbox
    expired = {"access_token": "old", "refresh_token": "refresh", "expires_at": "2000-01-01T00:00:00+00:00"}
    refreshed = []

    def token_post(url, *, data, timeout):
        refreshed.append(data)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"access_token": "fresh", "expires_in": 3600})

    monkeypatch.setattr(mail_oauth.httpx, "post", token_post)
    for provider, host in (("gmail", "imap.gmail.com"), ("microsoft", "outlook.office365.com")):
        with env.sessions() as db:
            account = db.get(MailAccount, "account")
            account.provider, account.secrets = provider, {"encrypted": mail_oauth.encrypt(env.settings, expired)}
            db.commit()
            config = mail_providers.account_config(db, env.settings, account)
        assert (config.host, config.access_token, config.password) == (host, "fresh", "")
    assert [item["grant_type"] for item in refreshed] == ["refresh_token", "refresh_token"]
