"""The WhatsApp webhook must answer 200 to every validly signed delivery it can read, and one odd message must never cost the others.

Meta redelivers a payload for ever when the answer is not 200 and may disable the webhook, so a bad timestamp, a null field, a NUL character or a
message type the CRM does not use is skipped (or stored as a harmless row), never turned into a 400 or 500 for the whole delivery.
"""
import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Candidate, WhatsAppChannel, WhatsAppMessage
import pytest

from test_workflow_scheduler import workspace  # noqa: F401  (fixture used by `setup`)
from test_database_naming import schema_database  # noqa: F401  (fixture used by `workspace`)
from auto_assign import round_robin_on  # noqa: F401  (fixture: the intake `setup` seeds a round-robin owner)
from test_whatsapp_intake import setup, payload  # noqa: F401  (fixtures/helpers shared with the intake tests)

pytestmark = pytest.mark.usefixtures("round_robin_on")

PHONE = "12345"
SENDER = "919999999999"


def client_for(sessions, settings, monkeypatch):
    from app.api.routers import whatsapp
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    app = FastAPI(); app.include_router(whatsapp.router)

    def db():
        with sessions() as session:
            yield session
    app.dependency_overrides[whatsapp.get_db] = db
    return TestClient(app)


def post(client, settings, body, *, secret=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    signature = "sha256=" + hmac.new((secret or settings.whatsapp_app_secret).encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"})


def change(messages=None, statuses=None, *, contacts=None, phone=PHONE):
    value = {"metadata": {"phone_number_id": phone, "display_phone_number": "+91 11111 11111"}}
    if contacts is not None: value["contacts"] = contacts
    if messages is not None: value["messages"] = messages
    if statuses is not None: value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}


def text(message_id, body, **extra):
    return {"id": message_id, "from": SENDER, "timestamp": "1792570000", "type": "text", "text": {"body": body}, **extra}


def stored(sessions, message_id):
    with sessions() as db:
        row = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.provider_message_id == message_id))
        return None if row is None else (row.body, row.sender_name, row.status, row.message_type)


def test_a_signed_event_for_another_product_is_acknowledged_not_refused(setup, monkeypatch):
    sessions, settings = setup
    with client_for(sessions, settings, monkeypatch) as client:
        response = post(client, settings, {"object": "page", "entry": [{"id": "1", "changes": []}]})
        assert response.status_code == 200 and response.json() == {"received": 0}
        # unsigned or wrongly signed is still refused, whatever the content
        assert client.post("/api/whatsapp/webhook", content=json.dumps({"object": "page"}).encode()).status_code == 401
        assert post(client, settings, {"object": "page"}, secret="not-the-secret").status_code == 401


def test_a_signed_body_that_is_not_json_is_still_a_400(setup, monkeypatch):
    sessions, settings = setup
    with client_for(sessions, settings, monkeypatch) as client:
        assert post(client, settings, b"this is not json").status_code == 400
        assert post(client, settings, b"[1, 2, 3]").status_code == 400


def test_the_size_cap_is_four_mebibytes_and_an_oversize_call_is_refused_before_it_is_read(setup, monkeypatch):
    sessions, settings = setup
    with client_for(sessions, settings, monkeypatch) as client:
        too_big = b" " * (4 * 1024 * 1024 + 1)
        assert client.post("/api/whatsapp/webhook", content=too_big).status_code == 413
        # a 1.5 MiB delivery (over the old 1 MiB cap) is accepted when it is signed and valid
        padded = json.dumps(change([text("wamid.big", "x" * (1536 * 1024))])).encode()
        assert len(padded) > 1024 * 1024
        assert post(client, settings, padded).json() == {"received": 1}


def test_one_malformed_message_among_good_ones_costs_nothing(setup, monkeypatch):
    sessions, settings = setup
    messages = ["not an object", None, 7,
                text("wamid.good-1", "first"),
                {"id": "wamid.no-sender", "type": "text", "text": {"body": "no sender"}},
                text("wamid.bad-clock", "odd clock", timestamp="not-a-number"),
                text("wamid.null-text", "ignored", text=None),
                text("wamid.good-2", "second")]
    contacts = [None, "text", {"wa_id": SENDER, "profile": None}, {"wa_id": None}]
    with client_for(sessions, settings, monkeypatch) as client:
        response = post(client, settings, change(messages, contacts=contacts))
    assert response.status_code == 200, response.text
    assert response.json() == {"received": 4}
    assert stored(sessions, "wamid.good-1")[0] == "first" and stored(sessions, "wamid.good-2")[0] == "second"
    assert stored(sessions, "wamid.bad-clock")[0] == "odd clock"       # an unreadable clock reads as "now": the message is kept
    assert stored(sessions, "wamid.null-text")[0] == ""                # a null text object is an empty body, not a crash
    assert stored(sessions, "wamid.no-sender") is None and stored(sessions, "wamid.good-1")[1] is None


def test_a_message_that_fails_inside_the_database_rolls_back_alone(setup, monkeypatch):
    from app.services import whatsapp_intake
    sessions, settings = setup
    real = whatsapp_intake._store_message

    def flaky(db, channel, phone_id, message, contacts):
        if message["id"] == "wamid.explodes":
            db.add(WhatsAppMessage(provider_message_id="wamid.half-written", phone_number_id=phone_id, sender_number=SENDER, message_type="text", body="", status="ignored",
                                   received_at=whatsapp_intake.utcnow()))
            db.flush()
            raise ValueError("boom")   # a data-shaped failure of this one message (a transient database error is redelivered: test_whatsapp_linking.py)
        return real(db, channel, phone_id, message, contacts)
    monkeypatch.setattr(whatsapp_intake, "_store_message", flaky)
    with client_for(sessions, settings, monkeypatch) as client:
        response = post(client, settings, change([text("wamid.before", "a"), text("wamid.explodes", "b"), text("wamid.after", "c")]))
    assert response.status_code == 200 and response.json() == {"received": 2}
    assert stored(sessions, "wamid.before") and stored(sessions, "wamid.after")
    assert stored(sessions, "wamid.explodes") is None and stored(sessions, "wamid.half-written") is None   # the failed one left nothing behind


def test_nul_characters_are_removed_instead_of_failing_the_delivery(setup, monkeypatch):
    sessions, settings = setup
    contacts = [{"wa_id": SENDER, "profile": {"name": "Asha\x00 Devi"}}]
    document = {"id": "wamid.nul-doc", "from": SENDER, "timestamp": "1792570001", "type": "document",
                "document": {"id": "888", "filename": "re\x00sume.pdf", "mime_type": "application/pdf", "caption": "cv\x00!"}}
    with client_for(sessions, settings, monkeypatch) as client:
        response = post(client, settings, change([text("wamid.nul", "he\x00llo"), document], contacts=contacts))
    assert response.status_code == 200 and response.json() == {"received": 2}
    assert stored(sessions, "wamid.nul")[:2] == ("hello", "Asha Devi")
    with sessions() as db:
        row = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.provider_message_id == "wamid.nul-doc"))
        assert (row.body, row.file_name, row.status) == ("cv!", "resume.pdf", "pending")


def test_a_statuses_only_delivery_takes_no_channel_lock_and_is_not_a_received_message(setup, monkeypatch):
    from app.services import whatsapp_intake
    sessions, settings = setup

    def no_lock(*_args, **_kwargs):
        raise AssertionError("a statuses-only delivery must not look up or lock the channel")
    monkeypatch.setattr(whatsapp_intake, "connected_channel", no_lock)
    statuses = [{"id": "wamid.out-1", "status": "delivered", "timestamp": "1792570100", "recipient_id": SENDER},
                {"id": "wamid.out-2", "status": "read", "timestamp": None, "recipient_id": SENDER},       # a null clock: skipped
                {"id": "wamid.out-3", "status": "failed", "timestamp": "1792570102", "recipient_id": SENDER, "errors": None},
                "not an object"]
    with client_for(sessions, settings, monkeypatch) as client:
        response = post(client, settings, change(statuses=statuses))
    assert response.status_code == 200 and response.json() == {"received": 0}
    with sessions() as db:
        channel = db.get(WhatsAppChannel, PHONE)
        assert channel.last_received_at is None and channel.display_number is None
        assert db.scalar(select(func.count()).select_from(WhatsAppMessage)) == 0


def test_a_delivery_with_messages_does_update_the_channel(setup, monkeypatch):
    sessions, settings = setup
    with client_for(sessions, settings, monkeypatch) as client:
        assert post(client, settings, change([text("wamid.m1", "hi")])).json() == {"received": 1}
    with sessions() as db:
        channel = db.get(WhatsAppChannel, PHONE)
        assert channel.last_received_at is not None and channel.display_number == "+91 11111 11111"


def test_buttons_choices_reactions_and_places_keep_their_words_but_never_make_a_candidate(setup, monkeypatch):
    sessions, settings = setup
    messages = [
        {"id": "wamid.button", "from": SENDER, "timestamp": "1792570200", "type": "button", "button": {"text": "Yes, interested", "payload": "p1"}},
        {"id": "wamid.reply", "from": SENDER, "timestamp": "1792570201", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "b1", "title": "Call me"}}},
        {"id": "wamid.list", "from": SENDER, "timestamp": "1792570202", "type": "interactive",
         "interactive": {"type": "list_reply", "list_reply": {"id": "l1", "title": "Gulf jobs", "description": "x"}}},
        {"id": "wamid.react", "from": SENDER, "timestamp": "1792570203", "type": "reaction", "reaction": {"message_id": "wamid.x", "emoji": "\U0001F44D"}},
        {"id": "wamid.place", "from": SENDER, "timestamp": "1792570204", "type": "location", "location": {"latitude": 11.0, "longitude": 77.0, "name": "Office", "address": "Coimbatore"}},
        {"id": "wamid.pin", "from": SENDER, "timestamp": "1792570205", "type": "location", "location": {"latitude": 11.5, "longitude": 77.5}},
        {"id": "wamid.odd", "from": SENDER, "timestamp": "1792570206", "type": "interactive", "interactive": {"type": 5}},
        {"id": "wamid.unsupported", "from": SENDER, "timestamp": "1792570207", "type": "unsupported"},
    ]
    with sessions() as db:
        before = db.scalar(select(func.count()).select_from(Candidate))   # the shared fixture already holds a candidate
    with client_for(sessions, settings, monkeypatch) as client:
        assert post(client, settings, change(messages)).json() == {"received": 8}
    bodies = {key: (row[0], row[2]) for key, row in ((k, stored(sessions, "wamid." + k)) for k in ("button", "reply", "list", "react", "place", "pin", "odd", "unsupported"))}
    assert bodies == {"button": ("Yes, interested", "ignored"), "reply": ("Call me", "ignored"), "list": ("Gulf jobs", "ignored"), "react": ("\U0001F44D", "ignored"),
                      "place": ("Office, Coimbatore", "ignored"), "pin": ("11.5, 77.5", "ignored"), "odd": ("", "ignored"), "unsupported": ("", "ignored")}
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == before   # none of these ever reaches the reader or creates a candidate


def test_disabled_and_incomplete_configuration_stay_closed(setup, monkeypatch):
    from types import SimpleNamespace
    sessions, settings = setup
    off = SimpleNamespace(**{**vars(settings), "whatsapp_enabled": False})
    no_secret = SimpleNamespace(**{**vars(settings), "whatsapp_app_secret": None})
    no_phone = SimpleNamespace(**{**vars(settings), "whatsapp_phone_number_id": None})
    handshake = {"hub.mode": "subscribe", "hub.verify_token": settings.whatsapp_verify_token, "hub.challenge": "c1"}
    with client_for(sessions, off, monkeypatch) as client:
        assert client.get("/api/whatsapp/webhook", params=handshake).status_code == 403          # the handshake needs the channel switched on
        # while disabled a signed delivery is acknowledged (receipts still reconcile) but no message is stored
        assert post(client, off, change([text("wamid.off", "hi")])).json() == {"received": 0}
        assert stored(sessions, "wamid.off") is None
    for incomplete in (no_secret, no_phone):
        with client_for(sessions, incomplete, monkeypatch) as client:
            raw = json.dumps(change([text("wamid.none", "hi")])).encode()
            assert client.post("/api/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 503
    with client_for(sessions, settings, monkeypatch) as client:
        assert client.get("/api/whatsapp/webhook", params=handshake).text == "c1"
