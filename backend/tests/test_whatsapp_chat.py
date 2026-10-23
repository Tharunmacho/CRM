"""Outbound WhatsApp contracts using isolated PostgreSQL and synthetic transport."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import threading
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import event, func, select

from app.db.models import (Candidate, User, WhatsAppChannel, WhatsAppConversation,
    WhatsAppDeliveryEvent, WhatsAppMessage, WhatsAppSendAttempt, WhatsAppTemplate)
from app.integrations import whatsapp as provider
from app.services import whatsapp_chat as chat
from test_workflow_scheduler import workspace
from test_database_naming import schema_database

NOW = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
PHONE = "12345"
PEER = "919999999999"


@pytest.fixture
def setup(workspace, tmp_path, monkeypatch):
    settings = SimpleNamespace(whatsapp_enabled=True, whatsapp_phone_number_id=PHONE, whatsapp_graph_version="v24.0",
        whatsapp_access_token="synthetic-access-token", whatsapp_app_secret="synthetic-app-secret", whatsapp_verify_token="synthetic-verify-token",
        whatsapp_business_account_id="98765", storage_dir=str(tmp_path), storage_backend="local", ocr_document_timeout_seconds=600)
    monkeypatch.setattr(chat, "utcnow", lambda: NOW)
    with workspace() as db:
        db.add(WhatsAppChannel(phone_number_id=PHONE, enabled=True)); db.flush()
        db.add(WhatsAppConversation(id="conversation", phone_number_id=PHONE, peer_number=PEER,
            candidate_id="candidate", last_inbound_at=NOW - timedelta(hours=1), last_message_at=NOW - timedelta(hours=1)))
        db.commit()
    return workspace, settings


def queue(sessions, settings, *, key="synthetic-request-1", body="Hello", actor="owner", **kwargs):
    with sessions() as db:
        row = chat.enqueue(db, PHONE, PEER, body, key, actor_id=actor, conversation_id="conversation", settings=settings, **kwargs)
        db.commit()
        return row.id


def status_payload(identifier="wamid.synthetic", state="delivered", *, callback=None, peer=PEER, phone=PHONE, at=NOW):
    status = {"id": identifier, "status": state, "recipient_id": peer, "timestamp": str(int(at.timestamp()))}
    if callback: status["biz_opaque_callback_data"] = callback
    if state == "failed": status["errors"] = [{"code": 131047, "message": "Do not expose provider detail"}]
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": phone}, "statuses": [status]}}]}]}


def client_for(sessions, settings, monkeypatch, actor="owner"):
    from app.api.routers import whatsapp, whatsapp_chat
    app = FastAPI(); app.include_router(whatsapp.router); app.include_router(whatsapp_chat.router)
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    monkeypatch.setattr(whatsapp_chat, "get_settings", lambda: settings)
    def db():
        with sessions() as session: yield session
    def user():
        with sessions() as session: return session.get(User, actor)
    app.dependency_overrides[whatsapp_chat.get_db] = db
    app.dependency_overrides[whatsapp_chat.current_user] = user
    return TestClient(app)


@pytest.mark.parametrize("actor,allowed", [("admin", True), ("manager", True), ("owner", True), ("other", False)])
def test_current_linked_owner_and_supervisors_can_send(setup, actor, allowed):
    sessions, settings = setup
    if allowed: assert queue(sessions, settings, actor=actor)
    else:
        with pytest.raises(HTTPException) as error: queue(sessions, settings, actor=actor)
        assert error.value.status_code == 403


def test_exact_reply_window_and_payload_idempotency(setup):
    sessions, settings = setup
    attempt = queue(sessions, settings)
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=24); db.commit()
    assert queue(sessions, settings) == attempt  # Recovery remains possible after expiry.
    with pytest.raises(HTTPException) as changed: queue(sessions, settings, body="Changed")
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as closed: queue(sessions, settings, key="different-request")
    assert closed.value.status_code == 409 and "24-hour" in closed.value.detail
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(WhatsAppSendAttempt)) == 1
        conversation = db.get(WhatsAppConversation, "conversation")
        assert not chat.window(conversation, NOW)[0]
        assert chat.window(conversation, NOW - timedelta(microseconds=1))[0]
        conversation.last_inbound_at = None
        assert not chat.window(conversation, NOW)[0]


def test_future_receipt_replay_never_extends_service_window(setup):
    sessions, settings = setup
    payload = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": PHONE}, "messages": [{"id": "future"}]}}]}]}
    with sessions() as db:
        db.add(WhatsAppMessage(id="inbound", provider_message_id="future", phone_number_id=PHONE, sender_number=PEER,
            message_type="text", received_at=NOW + timedelta(days=10), created_at=NOW, body="Hi", status="received")); db.commit()
        chat.record_inbound_payload(db, payload, settings, at=NOW); db.commit()
        assert db.get(WhatsAppConversation, "conversation").last_inbound_at == NOW
        chat.record_inbound_payload(db, payload, settings, at=NOW + timedelta(days=1)); db.commit()
        assert db.get(WhatsAppConversation, "conversation").last_inbound_at == NOW


def test_sending_is_durable_before_provider_and_timeout_never_retries(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    calls = []
    def sending(config, payload, **kwargs):
        with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "sending"
        assert payload["biz_opaque_callback_data"] == identifier
        calls.append(payload)
        raise provider.SendUnknown("synthetic timeout")
    monkeypatch.setattr(provider, "send_message", sending)
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    chat.dispatch_queued(sessions=sessions, settings=settings)
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "unknown"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["owner", "inactive", "window"])
def test_dispatch_rechecks_changed_authority_and_window(setup, monkeypatch, change):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    with sessions() as db:
        if change == "owner": db.get(Candidate, "candidate").owner_id = "other"
        elif change == "inactive": db.get(User, "owner").active = False
        else: db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=24)
        db.commit()
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: pytest.fail("Changed authority must prevent sending"))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "cancelled"


def test_paused_channel_keeps_queue_and_accepts_signed_status_even_global_disable(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    with sessions() as db:
        db.get(WhatsAppChannel, PHONE).enabled = False; db.commit()
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, identifier).state == "queued"
        assert not chat.conversation_json(db, db.get(WhatsAppConversation, "conversation"), db.get(User, "owner"), settings)["canSend"]
        db.get(WhatsAppSendAttempt, identifier).state = "unknown"; db.commit()
    settings.whatsapp_enabled = False
    payload = status_payload(callback=identifier)
    payload["entry"][0]["changes"][0]["value"]["messages"] = [{"id": "paused-incoming", "from": PEER,
        "type": "text", "timestamp": str(int(NOW.timestamp())), "text": {"body": "Intake remains paused"}}]
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    with client_for(sessions, settings, monkeypatch) as client:
        assert client.post("/api/whatsapp/webhook", content=body).status_code == 401
        for _ in range(2):
            assert client.post("/api/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": signature}).status_code == 200
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, identifier).state == "delivered"
        assert db.scalar(select(func.count()).select_from(WhatsAppDeliveryEvent)) == 1
        assert db.scalar(select(func.count()).select_from(WhatsAppMessage)) == 0


def test_early_webhook_and_out_of_order_status_never_downgrade_read(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    def sending(config, payload, **kwargs):
        with sessions() as db:
            chat.reconcile_payload(db, status_payload(state="read", callback=identifier), settings); db.commit()
        raise provider.SendUnknown("response lost after delivery")
    monkeypatch.setattr(provider, "send_message", sending)
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        for state in ("sent", "failed", "delivered", "read"):
            chat.reconcile_payload(db, status_payload(state=state, callback=identifier), settings); db.commit()
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "read" and row.last_error is None and row.read_at == NOW
        assert row.provider_message_id == "wamid.synthetic"
        assert db.scalar(select(func.count()).select_from(WhatsAppDeliveryEvent)) == 4


def test_wrong_recipient_or_conflicting_callback_cannot_attach_receipt(setup):
    sessions, settings = setup
    first = queue(sessions, settings)
    second = queue(sessions, settings, key="another-request")
    with sessions() as db:
        db.get(WhatsAppSendAttempt, first).state = "unknown"
        db.get(WhatsAppSendAttempt, second).state = "unknown"
        db.get(WhatsAppSendAttempt, second).provider_message_id = "wamid.synthetic"
        db.commit()
        for value in (status_payload(callback=first), status_payload(callback=first, peer="918888888888"), status_payload(callback=first, phone="other")):
            chat.reconcile_payload(db, value, settings); db.commit()
        assert db.get(WhatsAppSendAttempt, first).state == "unknown"
        assert db.get(WhatsAppSendAttempt, second).state == "unknown"


def test_unassociated_receipt_is_reconciled_when_http_receipt_arrives(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    def sending(config, payload, **kwargs):
        with sessions() as db:
            chat.reconcile_payload(db, status_payload(), settings); db.commit()
        return "wamid.synthetic"
    monkeypatch.setattr(provider, "send_message", sending)
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, identifier).state == "delivered"
        assert db.scalar(select(WhatsAppDeliveryEvent.attempt_id)) == identifier


def test_crashed_sending_is_unknown_but_stale_preparation_can_recover(setup, monkeypatch):
    sessions, settings = setup
    sent = queue(sessions, settings)
    prepared = queue(sessions, settings, key="prepare-request")
    with sessions() as db:
        for identifier, state in ((sent, "sending"), (prepared, "preparing")):
            row = db.get(WhatsAppSendAttempt, identifier); row.state, row.updated_at = state, NOW - timedelta(minutes=4)
        db.commit()
    calls = []
    monkeypatch.setattr(provider, "send_message", lambda s, p, **kw: calls.append(p) or "wamid.recovered")
    chat.dispatch_queued(sessions=sessions, settings=settings)
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, sent).state == "unknown"
        assert db.get(WhatsAppSendAttempt, prepared).state == "accepted"
    assert len(calls) == 1 and calls[0]["biz_opaque_callback_data"] == prepared


def add_template(db, *, status="APPROVED", components=None):
    row = WhatsAppTemplate(phone_number_id=PHONE, name="recruitment_update", language="en", category="UTILITY", status=status,
        components=components or [{"type": "BODY", "text": "Hello {{1}}. Please contact Care India."}])
    db.add(row); db.commit(); return row


@pytest.mark.parametrize("fresh_status,expected", [("APPROVED", "accepted"), ("PAUSED", "cancelled")])
def test_template_outside_window_revalidates_provider_approval(setup, monkeypatch, fresh_status, expected):
    sessions, settings = setup
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = None
        add_template(db)
    identifier = queue(sessions, settings, body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": ["Candidate"]})
    calls = []
    monkeypatch.setattr(provider, "fetch_templates", lambda *a, **kw: [{"name": "recruitment_update", "language": "en", "status": fresh_status,
        "category": "UTILITY", "components": [{"type": "BODY", "text": "Hello {{1}}. Please contact Care India."}]}])
    monkeypatch.setattr(provider, "send_message", lambda s, p, **kw: calls.append(p) or "wamid.template")
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == expected
    assert len(calls) == int(fresh_status == "APPROVED")
    if calls: assert calls[0]["template"]["components"][0]["parameters"] == [{"type": "text", "text": "Candidate"}]
    if calls: assert "text" not in calls[0] and "body" not in calls[0]


def test_queued_template_history_persists_readable_snapshot_and_replay_identity(setup, monkeypatch):
    sessions, settings = setup
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = None
        add_template(db, components=[{"type": "HEADER", "format": "TEXT", "text": "Care India"},
            {"type": "BODY", "text": "Hello {{1}}. Role: {{2}}. Thank you, {{1}}."},
            {"type": "FOOTER", "text": "Reply to discuss."}])
    intent = {"name": "recruitment_update", "language": "en", "parameters": ["Alex {{2}}", "TIG Welder"]}
    identifier = queue(sessions, settings, body="", kind="template", template=intent)
    expected = "Care India\n\nHello Alex {{2}}. Role: TIG Welder. Thank you, Alex {{2}}.\n\nReply to discuss."
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "queued" and row.body == expected
        assert db.get(WhatsAppConversation, "conversation").last_preview == expected
        fingerprint = row.payload_hash
        approved = db.scalar(select(WhatsAppTemplate))
        approved.components = [{"type": "BODY", "text": "Changed later: {{1}} / {{2}}"}]
        approved.status = "PAUSED"
        db.commit()
    # An uncertain queue response can be recovered unchanged even if the cache
    # or approval changed afterwards. It must not enqueue another message.
    assert queue(sessions, settings, body="", kind="template", template=intent) == identifier
    with client_for(sessions, settings, monkeypatch) as client:
        history = client.get("/api/whatsapp/conversations/conversation/messages").json()["messages"]
        assert history[0]["id"] == identifier and history[0]["body"] == expected
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, identifier).payload_hash == fingerprint
        assert db.scalar(select(func.count()).select_from(WhatsAppSendAttempt)) == 1


def test_changed_approved_template_cannot_send_different_text_than_queued_history(setup, monkeypatch):
    sessions, settings = setup
    with sessions() as db: add_template(db)
    identifier = queue(sessions, settings, body="", kind="template",
        template={"name": "recruitment_update", "language": "en", "parameters": ["Alex"]})
    monkeypatch.setattr(provider, "fetch_templates", lambda *a, **kw: [{"name": "recruitment_update", "language": "en",
        "status": "APPROVED", "components": [{"type": "BODY", "text": "Changed content for {{1}}"}]}])
    monkeypatch.setattr(provider, "send_message", lambda *a, **kw: pytest.fail("A different template must be reviewed before sending"))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "cancelled" and "changed" in row.last_error
        assert row.body == "Hello Alex. Please contact Care India."


@pytest.mark.parametrize("components,params", [([{"type": "BODY", "text": "Hello {{1}}"}], []),
    ([{"type": "BODY", "text": "Hello {{name}}"}], ["Name"]),
    ([{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Hello"}], [])])
def test_invalid_or_unsupported_template_parameters_are_explicitly_blocked(setup, components, params):
    sessions, settings = setup
    with sessions() as db: add_template(db, components=components)
    with pytest.raises(HTTPException) as error:
        queue(sessions, settings, body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": params})
    assert error.value.status_code == 422


def test_bounded_conversation_and_message_paging_avoids_candidate_ocr(setup, monkeypatch):
    sessions, settings = setup
    with sessions() as db:
        db.get(Candidate, "candidate").resume_text = "large OCR" * 1000
        db.add_all([WhatsAppMessage(id=f"message-{n}", provider_message_id=f"wamid.{n}", phone_number_id=PHONE,
            sender_number=PEER, message_type="text", body=f"Message {n}", received_at=NOW + timedelta(seconds=n), status="received") for n in range(5)])
        db.commit()
    statements = []
    engine = sessions.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, executemany): statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with client_for(sessions, settings, monkeypatch) as client:
            response = client.get("/api/whatsapp/conversations?page_size=1")
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1
            assert response.json()["conversations"][0]["candidate"]["ownerId"] == "owner"
            assert not any("crm_candidates.resume_text" in statement for statement in statements)
            first = client.get("/api/whatsapp/conversations/conversation/messages?limit=2").json()
            assert [r["body"] for r in first["messages"]] == ["Message 3", "Message 4"]
            second = client.get("/api/whatsapp/conversations/conversation/messages", params={"limit": 2, "before": first["nextBefore"]}).json()
            assert [r["body"] for r in second["messages"]] == ["Message 1", "Message 2"]
            assert client.get("/api/whatsapp?page_size=2").status_code == 200
            assert not any("whatsapp_messages.processing_state" in statement for statement in statements)
            assert client.get("/api/whatsapp/conversations/conversation/messages?before=invalid").status_code == 422
    finally: event.remove(engine, "before_cursor_execute", capture)


def test_media_recovery_does_not_reupload_and_retains_original_payload(setup, monkeypatch):
    sessions, settings = setup
    from app.api.routers import whatsapp_chat as router
    uploads = []
    original = router.put_bytes
    monkeypatch.setattr(router, "put_bytes", lambda *a: uploads.append(a[1]) or original(*a))
    with client_for(sessions, settings, monkeypatch) as client:
        path = "/api/whatsapp/conversations/conversation/send-media"
        response = client.post(path, data={"requestKey": "media-request", "caption": "Document"}, files={"file": ("../../resume.pdf", b"%PDF synthetic", "application/pdf")})
        assert response.status_code == 200, response.text
        identifier = response.json()["message"]["id"]
        assert response.json()["message"]["fileName"] == "resume.pdf"
        with sessions() as db:
            db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=25); db.commit()
        recovered = client.get("/api/whatsapp/conversations/conversation/send-requests/media-request")
        assert recovered.status_code == 200 and recovered.json()["message"]["id"] == identifier
        repeated = client.post(path, data={"requestKey": "media-request", "caption": "Document"}, files={"file": ("resume.pdf", b"%PDF synthetic", "application/pdf")})
        assert repeated.status_code == 200 and repeated.json()["message"]["id"] == identifier
        changed = client.post(path, data={"requestKey": "media-request", "caption": "Changed"}, files={"file": ("resume.pdf", b"different", "application/pdf")})
        assert changed.status_code == 409
        assert len(uploads) == 1
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW; db.commit()
    monkeypatch.setattr(provider, "upload_media", lambda *a, **kw: "777")
    sent = []
    monkeypatch.setattr(provider, "send_message", lambda s, p, **kw: sent.append(p) or "wamid.media")
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    assert sent[0]["document"] == {"id": "777", "caption": "Document", "filename": "resume.pdf"}


@pytest.mark.parametrize("status,expected", [(400, provider.SendRejected), (503, provider.SendUnknown), (200, provider.SendUnknown)])
def test_provider_rejection_or_uncertainty_never_leaks_response_body(setup, status, expected):
    _, settings = setup
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": 131047, "message": "secret-provider-detail"}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(expected) as error: provider.send_message(settings, {"type": "text"}, client=client)
        assert "secret-provider-detail" not in str(error.value)
    assert len(calls) == 1


def test_template_paging_never_follows_untrusted_next_url(setup):
    _, settings = setup
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [], "paging": {"next": "https://malicious.example/steal", "cursors": {"after": "next-page"}}} if len(calls) == 1 else {"data": []})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert provider.fetch_templates(settings, client=client) == []
    assert len(calls) == 2 and all(r.url.host == "graph.facebook.com" for r in calls)
    assert calls[1].url.params["after"] == "next-page"


def test_competing_dispatch_workers_make_one_message_request(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    entered, finish = threading.Event(), threading.Event()
    calls, failures = [], []
    def sending(config, payload, **kwargs):
        calls.append(payload)
        entered.set()
        assert finish.wait(10)
        return "wamid.once"
    monkeypatch.setattr(provider, "send_message", sending)
    def worker():
        try: chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
        except Exception as exc: failures.append(exc)
    first = threading.Thread(target=worker)
    first.start()
    try:
        assert entered.wait(10)
        chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    finally:
        finish.set(); first.join(10)
    assert not failures and not first.is_alive() and len(calls) == 1
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "accepted"


def test_corrupted_outbound_file_is_not_uploaded_or_sent(setup, monkeypatch):
    sessions, settings = setup
    from app.services.document_storage import put_bytes
    key = put_bytes(settings, "whatsapp/changed-file", b"changed")
    identifier = queue(sessions, settings, kind="document", body="", media={"object_key": key, "file_name": "document.pdf",
        "content_type": "application/pdf", "content_hash": hashlib.sha256(b"original").hexdigest(), "size_bytes": 8})
    monkeypatch.setattr(provider, "upload_media", lambda *a, **kw: pytest.fail("Corrupt bytes cannot be uploaded"))
    monkeypatch.setattr(provider, "send_message", lambda *a, **kw: pytest.fail("Corrupt bytes cannot be sent"))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "failed"


def test_incoming_audio_is_chat_media_and_never_candidate_ocr(setup, monkeypatch):
    sessions, settings = setup
    from app.services.whatsapp_intake import receive_payload
    value = {"metadata": {"phone_number_id": PHONE}, "messages": [{"id": "wamid.audio", "from": PEER,
        "timestamp": str(int(NOW.timestamp())), "type": "audio", "audio": {"id": "777", "mime_type": "audio/ogg"}}]}
    with sessions() as db:
        assert receive_payload(db, {"entry": [{"changes": [{"value": value}]}]}, settings) == 1
        row = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.provider_message_id == "wamid.audio"))
        assert row.status == "received" and row.media_id == "777"
        identifier = row.id
    downloads = []
    monkeypatch.setattr(provider, "download_media", lambda *a, **kw: downloads.append(kw) or (b"synthetic-audio", "audio/ogg"))
    with client_for(sessions, settings, monkeypatch) as client:
        for _ in range(2):
            response = client.get(f"/api/whatsapp/chat-media/{identifier}")
            assert response.status_code == 200 and response.content == b"synthetic-audio"
    assert downloads == [{"chat": True}]


def test_conflicting_callback_is_not_reapplied_after_http_response(setup, monkeypatch):
    sessions, settings = setup
    first = queue(sessions, settings)
    second = queue(sessions, settings, key="other-conflicting")
    def sending(config, payload, **kwargs):
        with sessions() as db:
            chat.reconcile_payload(db, status_payload(callback=second), settings); db.commit()
        return "wamid.synthetic"
    monkeypatch.setattr(provider, "send_message", sending)
    chat.dispatch_attempt(first, sessions=sessions, settings=settings)
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, first).state == "accepted"
        assert db.get(WhatsAppSendAttempt, second).state == "queued"
        assert db.scalar(select(WhatsAppDeliveryEvent.attempt_id)) is None


def test_disconnect_during_media_preparation_pauses_before_any_message_request(setup, monkeypatch):
    sessions, settings = setup
    from app.services.document_storage import put_bytes
    content = b"synthetic-file"
    key = put_bytes(settings, "whatsapp/prepared-file", content)
    identifier = queue(sessions, settings, kind="document", body="", media={"object_key": key, "file_name": "file.pdf",
        "content_type": "application/pdf", "content_hash": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)})
    def upload(*args, **kwargs):
        with sessions() as db:
            db.get(WhatsAppChannel, PHONE).enabled = False; db.commit()
        return "777"
    monkeypatch.setattr(provider, "upload_media", upload)
    monkeypatch.setattr(provider, "send_message", lambda *a, **kw: pytest.fail("Disconnect must stop recipient delivery"))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        assert db.get(WhatsAppSendAttempt, identifier).state == "queued"
        assert db.get(WhatsAppSendAttempt, identifier).provider_message_id is None
