"""Real disposable PostgreSQL mailbox contracts; all provider/storage inputs synthetic."""
from dataclasses import replace
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, func

from test_mail_workflow import mailbox, schema_database
from app.api.routers import mail_compose, zoho_mailbox
from app.db.models import AuditEntry, EmailIngestionJob, MailAccount, MailAttachment, MailDraft, MailDraftAttachment, MailMessage, MailSendAttempt, Role
from app.integrations.mail.base import InboundMessage, InboundAttachment
from app.integrations.mail.imap import MailboxCursor, provider_message_id, mailbox_source_id
from app.integrations.mail.mailbox import MailAdapterError, MailMutationError
from app.integrations.mail.smtp import MailSendError
from app.services import mail_drafts, zoho_mail
from app.services.mail_oauth import encrypt, decrypt


class Provider:
    def __init__(self):
        self.messages, self.drafts, self.sent, self.calls, self.errors = {}, {}, {}, [], {}
        self.next_uid = 100
        self.folder_names = ["INBOX", "Sent", "Drafts", "Trash", "Archive", "Spam", "Applicants"]

    def mailbox(self, config):
        self.config = config
        return self

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def probe(self): return MailboxCursor("101", 50)

    def folders(self):
        roles = {"INBOX": "inbox", "Sent": "sent", "Drafts": "drafts", "Trash": "trash", "Archive": "archive", "Spam": "spam"}
        return [{"name": name, "role": roles.get(name, "custom"), "total": 1, "unread": 1} for name in self.folder_names]

    def create_folder(self, name): self.folder_names.append(name)

    def find_message_id(self, folder, message_id):
        if self.errors.get("search"):
            raise self.errors["search"]
        values = self.drafts if folder == "Drafts" else self.sent if folder == "Sent" else self.messages.get(folder, {})
        found = []
        for uid, item in values.items():
            header = str(item["Message-ID"]) if hasattr(item, "get") else item.message_id_header
            if header == message_id:
                found.append(uid)
        return MailboxCursor("101", 500), found

    def append_draft(self, folder, raw):
        self.calls.append(("append", folder))
        if self.errors.get("append"):
            raise self.errors["append"]
        self.next_uid += 1
        self.drafts[self.next_uid] = BytesParser(policy=policy.default).parsebytes(raw)
        return "101", self.next_uid

    def remove_draft(self, folder, uid, validity):
        self.calls.append(("remove", folder, uid, validity))
        if self.errors.get("remove"):
            raise self.errors["remove"]
        self.drafts.pop(uid, None)

    def page(self, folder, search="", offset=0, limit=25, unread=None):
        if self.errors.get("page"):
            raise self.errors["page"]
        items = sorted(self.messages.get(folder, {}).values(), key=lambda item: item.uid, reverse=True)
        if search:
            items = [item for item in items if search.lower() in item.subject.lower()]
        if unread is not None:
            items = [item for item in items if item.unread == unread]
        return MailboxCursor("101", 500), len(items), items[offset:offset + limit]

    def list_uids(self, *, after_uid, uid_validity, limit):
        return sorted(uid for uid in self.messages.get("INBOX", {}) if uid > after_uid)[:limit]

    def fetch(self, uid, *, uid_validity):
        return self.messages["INBOX"][uid]

    def select_folder(self, folder, expected=None):
        return MailboxCursor("101", 500)

    def set_flags(self, folder, uid, validity, unread=None, starred=None):
        self.calls.append(("flags", folder, uid, validity))
        if self.errors.get("flags"):
            raise self.errors["flags"]
        item = self.messages[folder][uid]
        item = replace(item, unread=item.unread if unread is None else unread, starred=item.starred if starred is None else starred)
        self.messages[folder][uid] = item
        return item

    def move(self, folder, uid, validity, destination):
        self.calls.append(("move", folder, uid, validity, destination))
        if self.errors.get("move"):
            raise self.errors["move"]
        self.next_uid += 1
        item = self.messages[folder].pop(uid)
        self.messages.setdefault(destination, {})[self.next_uid] = replace(item, folder=destination, uid=self.next_uid,
            provider_id=provider_message_id(destination, "101", self.next_uid))
        return "101", self.next_uid


@pytest.fixture
def zoho(mailbox, monkeypatch):
    env = mailbox
    env.settings.imap_server, env.settings.smtp_server = "imappro.zoho.in", "smtppro.zoho.in"
    env.settings.smtp_port, env.settings.smtp_use_ssl = 465, True
    for module in (zoho_mailbox, mail_compose):
        monkeypatch.setattr(module, "get_settings", lambda: env.settings)
    provider = Provider()
    for module in (zoho_mailbox, mail_compose, mail_drafts):
        monkeypatch.setattr(module, "WritableMailbox", provider.mailbox)
    env.provider = provider
    config = zoho_mail.make_config("inbox@example.test", "synthetic-app-password", region="in")
    with env.sessions() as db:
        account = db.get(MailAccount, "account")
        account.provider = "zoho"
        account.secrets = {"encrypted": encrypt(env.settings, {"password": "synthetic-app-password", "region": "in", "account_type": "organization"})}
        account.sync_state = {"source_id": mailbox_source_id(config), "uid_validity": "101", "last_uid": 50}
        message = db.get(MailMessage, "message-0")
        message.remote_uid, message.uid_validity, message.folder = 1, "101", "INBOX"
        message.to_addresses, message.cc_addresses = [account.address, "other@example.test"], ["cc@example.test"]
        message.provider_message_id = provider_message_id("INBOX", "101", 1)
        db.commit()
        provider.messages["INBOX"] = {1: InboundMessage(provider_id=message.provider_message_id,
            subject=message.subject, from_name="Candidate", from_address=message.from_address,
            body=message.body, received_at=message.received_at, unread=True, attachments=(), uid=1,
            uid_validity="101", folder="INBOX", message_id_header=message.message_id_header,
            to_addresses=tuple(message.to_addresses), cc_addresses=tuple(message.cc_addresses))}
    return env


def new_draft(env, mode="compose", source=None):
    response = env.client.post("/api/inbox/drafts", json={"account_id": "account", "mode": mode, "source_message_id": source})
    assert response.status_code == 201, response.text
    return response.json()


def save(env, draft, **overrides):
    payload = {"lock_version": draft["lockVersion"], "to": ["recipient@example.test"], "cc": [], "bcc": ["blind@example.test"],
        "subject": "Job details", "body": "Please review the attachment.", **overrides}
    response = env.client.put(f"/api/inbox/drafts/{draft['id']}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_zoho_connection_encrypts_credentials_and_legacy_oauth_disabled(zoho):
    env = zoho
    response = env.client.post("/api/inbox/accounts/zoho", json={"address": "new@example.test", "password": "synthetic-private-value", "region": "in"})
    assert response.status_code == 200, response.text
    assert "password" not in response.text and "synthetic-private-value" not in response.text
    with env.sessions() as db:
        account = db.scalar(select(MailAccount).where(MailAccount.address == "new@example.test"))
        assert account.provider == "zoho" and account.sync_state["last_uid"] == 50
        assert "synthetic-private-value" not in str(account.secrets)
        assert decrypt(env.settings, account.secrets["encrypted"])["password"] == "synthetic-private-value"
        assert "synthetic-private-value" not in str(list(db.scalars(select(AuditEntry.meta))))
    assert env.client.post("/api/inbox/oauth/gmail/start", json={"address": "old@example.test"}).status_code == 410
    providers = env.client.get("/api/inbox").json()["providers"]
    assert [item["provider"] for item in providers] == ["zoho"]
    env.actor.role = Role.staff
    assert env.client.post("/api/inbox/accounts/zoho", json={"address": "new@example.test", "password": "synthetic"}).status_code == 403


@pytest.mark.parametrize("changes", [{"imap_host": "127.0.0.1"}, {"smtp_host": "smtp.gmail.com"}, {"imap_host": "imappro.zoho.in.attacker.test"}, {"smtp_port": 25}, {"region": "invalid"}])
def test_connection_rejects_non_zoho_servers_before_provider(zoho, changes):
    response = zoho.client.post("/api/inbox/accounts/zoho", json={"address": "new@example.test", "password": "synthetic", **changes})
    assert response.status_code == 422


def test_remote_paging_flags_move_restore_and_unknown_recovery(zoho):
    env = zoho
    first = env.client.get("/api/inbox/accounts/account/mailbox?folder=INBOX&limit=1")
    assert first.status_code == 200, first.text
    message = first.json()["messages"][0]
    assert message["id"] == "message-0" and message["toAddresses"] == ["inbox@example.test", "other@example.test"]
    flags = env.client.patch("/api/inbox/messages/message-0/flags", json={"unread": False, "starred": True})
    assert flags.status_code == 200 and flags.json()["starred"] and not flags.json()["unread"]
    moved = env.client.post("/api/inbox/messages/message-0/action", json={"action": "trash"})
    assert moved.status_code == 200 and moved.json()["folder"] == "Trash"
    restored = env.client.post("/api/inbox/messages/message-0/action", json={"action": "restore"})
    assert restored.status_code == 200 and restored.json()["folder"] == "INBOX"
    env.provider.errors["move"] = MailMutationError("Synthetic interrupted move", unknown=True)
    response = env.client.post("/api/inbox/messages/message-0/action", json={"action": "archive"})
    assert response.status_code == 502
    assert env.client.post("/api/inbox/messages/message-0/action", json={"action": "archive"}).status_code == 409
    env.provider.errors.pop("move")
    recovered = env.client.post("/api/inbox/messages/message-0/reconcile")
    assert recovered.status_code == 200 and recovered.json()["remoteState"] == "synced"
    with env.sessions() as db:
        assert db.get(MailAccount, "account").sync_state["last_uid"] == 50


def test_view_failure_does_not_report_cached_success_and_does_not_force_ocr(zoho):
    zoho.provider.errors["page"] = MailAdapterError("connection", "Synthetic provider unavailable")
    response = zoho.client.get("/api/inbox/accounts/account/mailbox")
    assert response.status_code == 502
    assert zoho.client.get("/api/inbox").status_code == 200


def test_draft_save_attachment_private_access_cas_and_remote_failure_preserve_text(zoho):
    env = zoho
    draft = new_draft(env)
    draft = save(env, draft)
    assert draft["remoteState"] == "synced" and draft["lockVersion"] == 1
    assert len(env.provider.drafts) == 1
    assert list(env.provider.drafts.values())[0]["Bcc"] == "blind@example.test"
    upload = env.client.post(f"/api/inbox/drafts/{draft['id']}/attachments", data={"lock_version": draft["lockVersion"]}, files={"file": ("cv.pdf", b"%PDF-1.7 synthetic", "application/pdf")})
    assert upload.status_code == 200, upload.text
    draft = upload.json()
    assert len(env.provider.drafts) == 1 and len(draft["attachments"]) == 1
    assert env.client.get(draft["attachments"][0]["downloadUrl"]).content.startswith(b"%PDF")
    assert env.client.put(f"/api/inbox/drafts/{draft['id']}", json={"lock_version": 0, "body": "stale"}).status_code == 409
    env.provider.errors["append"] = MailMutationError("Synthetic Zoho draft outage")
    changed = save(env, draft, body="Unsynced but durable text")
    assert changed["remoteState"] == "error" and changed["body"] == "Unsynced but durable text"
    assert env.client.get(f"/api/inbox/drafts/{draft['id']}").json()["body"] == "Unsynced but durable text"
    env.provider.errors.clear()
    assert env.client.post(f"/api/inbox/drafts/{draft['id']}/sync").json()["remoteState"] == "synced"
    env.actor.id, env.actor.role = "staff", Role.staff
    assert env.client.get(f"/api/inbox/drafts/{draft['id']}").status_code == 404
    assert env.client.get(draft["attachments"][0]["url"]).status_code == 404
    assert env.client.get("/api/inbox/drafts").json()["drafts"] == []


def test_reply_all_forward_and_existing_provider_draft_preserve_data(zoho):
    reply = new_draft(zoho, "reply_all", "message-0")
    assert reply["to"] == ["reply@example.test"]
    assert reply["cc"] == ["other@example.test", "cc@example.test"]
    forward = new_draft(zoho, "forward", "message-0")
    assert forward["to"] == [] and forward["attachments"][0]["name"] == "resume.pdf"
    with zoho.sessions() as db:
        message = db.get(MailMessage, "message-0")
        message.folder, message.bcc_addresses = "Drafts", ["blind@example.test"]
        db.commit()
    editing = new_draft(zoho, "edit_draft", "message-0")
    assert editing["bcc"] == ["blind@example.test"] and editing["attachments"] and editing["body"] == "Message 0"
    same = new_draft(zoho, "edit_draft", "message-0")
    assert same["id"] == editing["id"]
    zoho.actor.id = "staff"
    assert zoho.client.post("/api/inbox/drafts", json={"account_id": "account", "mode": "edit_draft", "source_message_id": "message-0"}).status_code == 409


def test_send_idempotent_attachment_snapshot_and_no_duplicate_sent_append(zoho, monkeypatch):
    env = zoho
    draft = save(env, new_draft(env))
    draft = env.client.post(f"/api/inbox/drafts/{draft['id']}/attachments", data={"lock_version": draft["lockVersion"]}, files={"file": ("terms.txt", b"synthetic terms", "text/plain")}).json()
    sent = []
    def deliver(config, message, recipients):
        sent.append((message, recipients))
        return []
    monkeypatch.setattr(mail_compose, "deliver", deliver)
    body = {"lock_version": draft["lockVersion"], "request_key": "unique-send-request"}
    first = env.client.post(f"/api/inbox/drafts/{draft['id']}/send", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["attempt"]["status"] == "sent"
    second = env.client.post(f"/api/inbox/drafts/{draft['id']}/send", json=body)
    assert second.json()["attempt"]["id"] == first.json()["attempt"]["id"] and len(sent) == 1
    assert "blind@example.test" in sent[0][1] and "Bcc" not in sent[0][0]
    assert list(sent[0][0].iter_attachments())[0].get_payload(decode=True) == b"synthetic terms"
    assert not any(call[:2] == ("append", "Sent") for call in env.provider.calls)
    with env.sessions() as db:
        attempt = db.scalar(select(MailSendAttempt))
        assert attempt.snapshot["attachments"][0]["object_key"].startswith("mail-drafts/")


@pytest.mark.parametrize("outcome", ["unknown", "partial", "failed"])
def test_send_outcomes_survive_reload_and_do_not_automatically_retry(zoho, monkeypatch, outcome):
    env = zoho
    draft = save(env, new_draft(env))
    count = []
    def deliver(*args):
        count.append(1)
        if outcome == "partial":
            return ["blind@example.test"]
        raise MailSendError("Synthetic provider outcome", unknown=outcome == "unknown")
    monkeypatch.setattr(mail_compose, "deliver", deliver)
    body = {"lock_version": draft["lockVersion"], "request_key": "uncertain-send-request"}
    result = env.client.post(f"/api/inbox/drafts/{draft['id']}/send", json=body).json()
    assert result["attempt"]["status"] == outcome
    again = env.client.post(f"/api/inbox/drafts/{draft['id']}/send", json=body).json()
    assert again["attempt"]["status"] == outcome and count == [1]
    assert env.client.get("/api/inbox/drafts?account_id=account").json()["drafts"][0]["status"] == ("unknown" if outcome == "partial" else outcome)
    assert env.client.get(f"/api/inbox/drafts/{draft['id']}/attempts").json()["attempts"][0]["status"] == outcome
    if outcome != "failed":
        assert env.client.post(f"/api/inbox/drafts/{draft['id']}/send", json={**body, "request_key": "different-send-request"}).status_code == 409
    if outcome == "unknown":
        from email.message import EmailMessage
        message = EmailMessage(); message["Message-ID"] = result["attempt"]["providerMessageId"]
        env.provider.sent[999] = message
        reconciled = env.client.post(f"/api/inbox/drafts/{draft['id']}/reconcile").json()
        assert reconciled["attempt"]["status"] == "sent" and count == [1]


def test_concurrent_same_send_request_only_submits_once(zoho, monkeypatch):
    draft = save(zoho, new_draft(zoho))
    calls = []
    monkeypatch.setattr(mail_compose, "deliver", lambda *args: calls.append(1) or [])
    barrier = Barrier(2)
    def submit():
        barrier.wait()
        return zoho.client.post(f"/api/inbox/drafts/{draft['id']}/send", json={"lock_version": draft["lockVersion"], "request_key": "concurrent-send-key"})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert all(result.status_code == 200 for result in results), [item.text for item in results]
    assert calls == [1]


def test_discard_provider_failure_retains_draft_for_retry(zoho):
    draft = save(zoho, new_draft(zoho))
    zoho.provider.errors["remove"] = MailMutationError("Synthetic provider outage")
    path = f"/api/inbox/drafts/{draft['id']}?lock_version={draft['lockVersion']}"
    assert zoho.client.delete(path).status_code == 502
    assert zoho.client.get(f"/api/inbox/drafts/{draft['id']}").json()["status"] == "draft"
    zoho.provider.errors.clear()
    assert zoho.client.delete(path).json()["status"] == "discarded"
    assert zoho.client.get("/api/inbox/drafts").json()["drafts"] == []


def test_viewed_new_email_still_enters_candidate_queue_once_on_next_poll(zoho, monkeypatch):
    from app.services import email_intake
    import hashlib
    env = zoho
    original = b"%PDF-1.7 candidate intake"
    part = InboundAttachment("new-part", "resume.pdf", "application/pdf", original, hashlib.sha256(original).hexdigest())
    incoming = replace(env.provider.messages["INBOX"][1], uid=51, provider_id=provider_message_id("INBOX", "101", 51), attachments=(part,))
    env.provider.messages["INBOX"][51] = incoming
    response = env.client.get("/api/inbox/accounts/account/mailbox")
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(EmailIngestionJob)) == 0
    monkeypatch.setattr(email_intake, "IMAPMailbox", env.provider.mailbox)
    email_intake.receive_account("account", sessions=env.sessions, settings=env.settings)
    email_intake.receive_account("account", sessions=env.sessions, settings=env.settings)
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(EmailIngestionJob)) == 1
        assert db.get(MailAccount, "account").sync_state["last_uid"] == 51


def test_failed_flag_sync_preserves_confirmed_local_flags(zoho):
    zoho.provider.errors["flags"] = MailMutationError("Synthetic flag failure", unknown=True)
    assert zoho.client.patch("/api/inbox/messages/message-0/flags", json={"unread": False}).status_code == 502
    with zoho.sessions() as db:
        message = db.get(MailMessage, "message-0")
        assert message.unread and message.remote_state == "unknown"
    assert zoho.client.patch("/api/inbox/messages/message-0/flags", json={"unread": False}).status_code == 409
    assert zoho.client.post("/api/inbox/messages/message-0/reconcile").json()["remoteState"] == "synced"


def test_attachment_storage_failure_retains_draft_version_and_hides_internal_error(zoho, monkeypatch):
    draft = new_draft(zoho)
    def unavailable(*args):
        raise RuntimeError("synthetic internal endpoint data should not be exposed")
    monkeypatch.setattr(mail_compose, "put_bytes", unavailable)
    result = zoho.client.post(f"/api/inbox/drafts/{draft['id']}/attachments", data={"lock_version": 0}, files={"file": ("data.txt", b"test", "text/plain")})
    assert result.status_code == 503 and "internal endpoint" not in result.text
    current = zoho.client.get(f"/api/inbox/drafts/{draft['id']}").json()
    assert current["lockVersion"] == 0 and current["attachments"] == []


def test_unknown_completed_move_reconciles_after_destination_was_viewed(zoho, monkeypatch):
    env = zoho
    original_move = env.provider.move
    def interrupted(*args):
        original_move(*args)
        raise MailMutationError("Synthetic lost move response", unknown=True)
    monkeypatch.setattr(env.provider, "move", interrupted)
    assert env.client.post("/api/inbox/messages/message-0/action", json={"action": "trash"}).status_code == 502
    viewed = env.client.get("/api/inbox/accounts/account/mailbox?folder=Trash")
    assert viewed.status_code == 200 and viewed.json()["messages"][0]["id"] != "message-0"
    result = env.client.post("/api/inbox/messages/message-0/reconcile")
    assert result.status_code == 200, result.text
    assert result.json()["id"] == "message-0" and result.json()["folder"] == "Trash" and result.json()["remoteState"] == "synced"
    with env.sessions() as db:
        active = list(db.scalars(select(MailMessage).where(MailMessage.folder == "Trash", MailMessage.remote_deleted.is_(False))))
        assert [item.id for item in active] == ["message-0"]


def test_other_user_cannot_adopt_an_active_crm_created_remote_draft(zoho):
    draft = save(zoho, new_draft(zoho))
    with zoho.sessions() as db:
        row = db.get(MailDraft, draft["id"])
        source = db.get(MailMessage, "message-0")
        source.folder, source.remote_uid, source.uid_validity = row.remote_folder, row.remote_uid, row.remote_uid_validity
        db.commit()
    zoho.actor.id = "staff"
    assert zoho.client.post("/api/inbox/drafts", json={"account_id": "account", "mode": "edit_draft", "source_message_id": "message-0"}).status_code == 409


def test_empty_recipient_send_is_rejected_without_smtp_intent(zoho, monkeypatch):
    draft = new_draft(zoho)
    monkeypatch.setattr(mail_compose, "deliver", lambda *args: pytest.fail("Invalid messages must not contact SMTP"))
    assert zoho.client.post(f"/api/inbox/drafts/{draft['id']}/send", json={"lock_version": 0, "request_key": "no-recipient-request"}).status_code == 422
    with zoho.sessions() as db:
        assert db.scalar(select(func.count()).select_from(MailSendAttempt)) == 0


def test_create_folder_is_idempotent_validated_and_available_to_staff(zoho):
    zoho.actor.id, zoho.actor.role = "staff", Role.staff
    result = zoho.client.post("/api/inbox/accounts/account/folders", json={"name": "Recruitment 2026"})
    assert result.status_code == 200 and result.json()["created"]
    assert any(item["name"] == "Recruitment 2026" for item in result.json()["folders"])
    again = zoho.client.post("/api/inbox/accounts/account/folders", json={"name": "Recruitment 2026"})
    assert again.status_code == 200 and not again.json()["created"]
    assert zoho.client.post("/api/inbox/accounts/account/folders", json={"name": "Bad\nfolder"}).status_code == 422
    with zoho.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "create_mail_folder")) == 1


def test_private_draft_search_and_pagination_do_not_hide_older_drafts(zoho):
    first = save(zoho, new_draft(zoho), subject="Older welder role", to=["older@example.test"])
    second = save(zoho, new_draft(zoho), subject="Recent welder role")
    page = zoho.client.get("/api/inbox/drafts?limit=1&search=welder").json()
    assert page["total"] == 2 and page["hasMore"] and page["drafts"][0]["id"] == second["id"]
    older = zoho.client.get("/api/inbox/drafts?limit=1&offset=1&search=welder").json()
    assert not older["hasMore"] and older["drafts"][0]["id"] == first["id"]
    recipient_search = zoho.client.get("/api/inbox/drafts?search=older%40example.test").json()
    assert recipient_search["total"] == 1 and recipient_search["drafts"][0]["id"] == first["id"]
    assert zoho.client.get("/api/inbox/drafts?limit=201").status_code == 422


def test_stored_history_attachment_filter_counts_messages_once_across_folders(zoho):
    with zoho.sessions() as db:
        db.get(MailMessage, "message-1").folder = "Sent"
        db.get(MailMessage, "message-1").unread = False
        db.add_all([MailAttachment(message_id="message-0", file_name="second.pdf", object_key="original.pdf"),
            MailAttachment(message_id="message-1", file_name="sent.pdf", object_key="original.pdf")])
        db.commit()
    default = zoho.client.get("/api/inbox?has_attachments=true").json()
    assert default["total"] == 1 and default["messages"][0]["id"] == "message-0"
    first = zoho.client.get("/api/inbox?scope=stored&has_attachments=true&limit=1").json()
    second = zoho.client.get("/api/inbox?scope=stored&has_attachments=true&limit=1&offset=1").json()
    assert first["scope"] == "stored" and first["total"] == 2 and first["hasMore"]
    assert first["messages"][0]["id"] == "message-1" and second["messages"][0]["id"] == "message-0"
    assert not second["hasMore"]
    read = zoho.client.get("/api/inbox?scope=stored&has_attachments=true&unread=false").json()
    assert read["total"] == 1 and read["messages"][0]["id"] == "message-1"
    assert zoho.client.get("/api/inbox?scope=stored&has_attachments=false").json()["total"] == 2


def test_live_unread_filter_uses_remote_counts_and_keeps_folder_paging(zoho):
    assert zoho.client.get("/api/inbox/accounts/account/mailbox?unread=true").json()["total"] == 1
    assert zoho.client.get("/api/inbox/accounts/account/mailbox?unread=false").json()["total"] == 0
    zoho.client.patch("/api/inbox/messages/message-0/flags", json={"unread": False})
    assert zoho.client.get("/api/inbox/accounts/account/mailbox?unread=true").json()["messages"] == []
    read = zoho.client.get("/api/inbox/accounts/account/mailbox?unread=false&limit=1").json()
    assert read["total"] == 1 and read["messages"][0]["id"] == "message-0" and not read["hasMore"]
