"""Outbound WhatsApp hardening: plain error messages, safe retries, stale queue, template values, typed numbers, the 24-hour margin."""
from datetime import timedelta

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from app.db.models import User, WhatsAppConversation, WhatsAppSendAttempt, WhatsAppTemplate
from app.integrations import whatsapp as provider
from app.services import whatsapp_chat as chat
from test_database_naming import schema_database  # noqa: F401  (fixture used by `workspace`)
from test_whatsapp_chat import NOW, PEER, PHONE, add_template, queue, setup, status_payload  # noqa: F401
from test_workflow_scheduler import workspace  # noqa: F401  (fixture used by `setup`)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """The retry pauses are real seconds in production; here they are recorded and skipped."""
    pauses = []
    monkeypatch.setattr(chat, "_sleep", pauses.append)
    monkeypatch.setattr(chat, "RETRY_DELAYS", (1.0, 3.0))
    return pauses


# ------------------------------------------------------------------ 1. Meta error codes in plain words

@pytest.mark.parametrize("code,words", [
    (131047, "24-hour"), ("131030", "allowed list"), (190, "access token"), (133010, "not registered"),
    (130429, "too fast"), (80007, "too fast"), (131056, "too fast"), (132000, "template"), (132012, "template"), (132016, "template"),
    (132018, "line breaks"),
])
def test_meta_codes_read_as_plain_words_with_the_code_kept_for_support(code, words):
    text = provider.describe_error(code)
    assert words in text and f"code {code}" in text


def test_an_unknown_code_keeps_the_generic_sentence_and_never_quotes_the_provider():
    assert provider.describe_error(999999) == provider.GENERIC_REJECTION
    assert provider.describe_error(None) == provider.GENERIC_REJECTION
    assert str(provider.SendRejected(131047)) == provider.describe_error(131047)


def test_only_nothing_was_sent_refusals_are_retryable():
    for code in ("130429", "80007", "131056", "429", "connect_failed"):
        assert provider.SendRejected(code).retryable is True, code
    for code in ("131047", "131030", "190", "133010", "132000", "100"):
        assert provider.SendRejected(code).retryable is False, code


def _client(status, body=None, error=None):
    calls = []
    def transport(request):
        calls.append(request)
        if error: raise error
        return httpx.Response(status, json=body or {})
    return httpx.Client(transport=httpx.MockTransport(transport)), calls


def test_http_429_and_a_failed_connection_are_not_sent_and_retryable(setup):
    _, settings = setup
    client, calls = _client(429, {})
    with client, pytest.raises(provider.SendRejected) as busy: provider.send_message(settings, {"type": "text"}, client=client)
    assert busy.value.retryable and busy.value.code == "429" and "too fast" in str(busy.value) and len(calls) == 1
    client, calls = _client(0, error=httpx.ConnectError("no route"))
    with client, pytest.raises(provider.SendRejected) as down: provider.send_message(settings, {"type": "text"}, client=client)
    assert down.value.retryable and down.value.code == "connect_failed" and "Nothing was sent" in str(down.value)


def test_a_timeout_after_the_connection_or_a_5xx_stays_unknown_and_is_never_retryable(setup):
    _, settings = setup
    for client in (_client(503, {})[0], _client(0, error=httpx.ReadTimeout("slow"))[0]):
        with client, pytest.raises(provider.SendUnknown): provider.send_message(settings, {"type": "text"}, client=client)


def test_a_provider_refusal_shows_the_mapped_message_not_the_provider_text(setup):
    _, settings = setup
    client, _ = _client(400, {"error": {"code": 131047, "message": "secret-provider-detail", "error_data": {"details": "another-secret"}}})
    with client, pytest.raises(provider.SendRejected) as refused: provider.send_message(settings, {"type": "text"}, client=client)
    assert "24-hour" in str(refused.value) and "secret-provider-detail" not in str(refused.value) and "another-secret" not in str(refused.value)


# ------------------------------------------------------------------ 2. retries that cannot duplicate a message

def test_a_too_fast_refusal_is_tried_again_after_a_pause_and_then_accepted(setup, monkeypatch, no_waiting):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    calls = []
    def sending(config, payload, **kwargs):
        calls.append(payload)
        if len(calls) < 3: raise provider.SendRejected("130429")
        return "wamid.after-waiting"
    monkeypatch.setattr(provider, "send_message", sending)
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    assert len(calls) == 3 and no_waiting == [1.0, 3.0]
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "accepted" and row.provider_message_id == "wamid.after-waiting"


def test_a_refusal_that_keeps_coming_ends_failed_with_the_plain_message(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    calls = []
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(provider.SendRejected("80007")))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    assert len(calls) == 3   # the first try and the two retries, never more
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "failed" and row.error_code == "80007" and "too fast" in row.last_error


def test_a_final_refusal_is_one_request_and_a_plain_message(setup, monkeypatch, no_waiting):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    calls = []
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(provider.SendRejected(190)))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    assert len(calls) == 1 and no_waiting == []
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "failed" and row.error_code == "190" and "access token" in row.last_error


def test_an_uncertain_send_is_never_sent_again(setup, monkeypatch, no_waiting):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    calls = []
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(provider.SendUnknown("lost")))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    assert len(calls) == 1 and no_waiting == []
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "unknown"


def test_preparation_is_tried_again_a_little_but_a_missing_setting_is_final(no_waiting):
    tries = []
    def flaky():
        tries.append(1)
        if len(tries) < 3: raise provider.WhatsAppError("upload failed")
        return "media-1"
    assert chat._prepare_with_retries(flaky) == "media-1" and len(tries) == 3 and no_waiting == [1.0, 3.0]
    tries.clear(); no_waiting.clear()
    with pytest.raises(provider.WhatsAppError):
        chat._prepare_with_retries(lambda: tries.append(1) or (_ for _ in ()).throw(provider.WhatsAppError("always")))
    assert len(tries) == 3
    tries.clear(); no_waiting.clear()
    with pytest.raises(provider.TemplatesNotConfigured):
        chat._prepare_with_retries(lambda: tries.append(1) or (_ for _ in ()).throw(provider.TemplatesNotConfigured("not set")))
    assert len(tries) == 1 and no_waiting == []


# ------------------------------------------------------------------ webhook failures read the same way

def test_a_failed_delivery_status_is_stored_as_the_plain_message(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: "wamid.synthetic")
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        chat.reconcile_payload(db, status_payload(state="failed", callback=identifier), settings); db.commit()
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "failed" and row.error_code == "131047"
        assert "24-hour" in row.last_error and "Do not expose provider detail" not in row.last_error


# ------------------------------------------------------------------ 3. nothing stale is sent after a reconnect

APPROVED_LISTING = [{"name": "recruitment_update", "language": "en", "status": "APPROVED", "category": "UTILITY",
                     "components": [{"type": "BODY", "text": "Hello {{1}}. Please contact Care India."}]}]


def _make_stale(sessions, identifier, **changes):
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        row.created_at = NOW - timedelta(hours=2)
        for name, value in changes.items(): setattr(row, name, value)
        db.commit()


def test_a_template_that_waited_more_than_an_hour_is_cancelled_not_sent(setup, monkeypatch):
    sessions, settings = setup
    with sessions() as db: add_template(db)
    old = queue(sessions, settings, key="old-template-key", body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": ["Asha"]})
    young = queue(sessions, settings, key="young-template-key", body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": ["Ben"]})
    _make_stale(sessions, old)
    sent = []
    monkeypatch.setattr(provider, "fetch_templates", lambda *a, **k: APPROVED_LISTING)
    monkeypatch.setattr(provider, "send_message", lambda s, p, **k: sent.append(p) or "wamid.young")
    chat.dispatch_queued(sessions=sessions, settings=settings)
    with sessions() as db:
        stale = db.get(WhatsAppSendAttempt, old)
        assert stale.state == "cancelled" and stale.last_error == chat.STALE_QUEUE_MESSAGE
        assert db.get(WhatsAppSendAttempt, young).state == "accepted"
    assert len(sent) == 1 and sent[0]["template"]["components"][0]["parameters"] == [{"type": "text", "text": "Ben"}]   # only the young one went out


def test_an_automatic_reply_that_waited_more_than_an_hour_is_cancelled(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    _make_stale(sessions, identifier, sender_user_id=None)
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: pytest.fail("A stale automatic reply must not be sent"))
    chat.dispatch_queued(sessions=sessions, settings=settings)
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "cancelled" and row.last_error == chat.STALE_QUEUE_MESSAGE


def test_a_message_a_person_typed_is_not_cancelled_for_waiting(setup, monkeypatch):
    sessions, settings = setup
    identifier = queue(sessions, settings)
    _make_stale(sessions, identifier)
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: "wamid.typed")
    chat.dispatch_queued(sessions=sessions, settings=settings)
    with sessions() as db: assert db.get(WhatsAppSendAttempt, identifier).state == "accepted"


# ------------------------------------------------------------------ 4. templates need the business account id

def test_a_template_cannot_be_queued_without_the_business_account_id(setup):
    sessions, settings = setup
    with sessions() as db: add_template(db)
    bare = type(settings)(**{**vars(settings), "whatsapp_business_account_id": None})
    with pytest.raises(HTTPException) as refused:
        queue(sessions, bare, key="no-account-key", body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": ["Asha"]})
    assert refused.value.status_code == 409 and "WHATSAPP_BUSINESS_ACCOUNT_ID is not set" in refused.value.detail


def test_loading_templates_without_the_business_account_id_says_which_setting_is_missing(setup):
    _, settings = setup
    bare = type(settings)(**{**vars(settings), "whatsapp_business_account_id": ""})
    with pytest.raises(provider.TemplatesNotConfigured) as missing: provider.fetch_templates(bare)
    assert "WHATSAPP_BUSINESS_ACCOUNT_ID is not set" in str(missing.value)


def test_a_queued_template_fails_with_that_reason_when_the_setting_is_gone_at_dispatch(setup, monkeypatch, no_waiting):
    sessions, settings = setup
    with sessions() as db: add_template(db)
    identifier = queue(sessions, settings, key="gone-account-key", body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": ["Asha"]})
    bare = type(settings)(**{**vars(settings), "whatsapp_business_account_id": None})
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: pytest.fail("Nothing may be sent"))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=bare)
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "failed" and "WHATSAPP_BUSINESS_ACCOUNT_ID is not set" in row.last_error
    assert no_waiting == []   # a missing setting is not retried


# ------------------------------------------------------------------ 5. template values Meta accepts

def test_template_values_lose_line_breaks_tabs_and_runs_of_spaces():
    assert chat.clean_template_value("Asha\nKumar\t(CI 01)") == "Asha Kumar (CI 01)"
    assert chat.clean_template_value("a     b") == "a b"
    assert chat.clean_template_value("  a   b  ") == "a   b"   # three spaces are allowed
    assert chat.clean_template_value("\r\n") == ""


def test_a_template_is_queued_and_sent_with_cleaned_values(setup, monkeypatch):
    sessions, settings = setup
    with sessions() as db: add_template(db)
    identifier = queue(sessions, settings, key="clean-values-key", body="", kind="template",
                       template={"name": "recruitment_update", "language": "en", "parameters": ["Asha\nKumar     Singh"]})
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.template["parameters"] == ["Asha Kumar Singh"] and row.body.startswith("Hello Asha Kumar Singh.")
    sent = []
    monkeypatch.setattr(provider, "fetch_templates", lambda *a, **k: APPROVED_LISTING)
    monkeypatch.setattr(provider, "send_message", lambda s, p, **k: sent.append(p) or "wamid.clean")
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    assert sent and sent[0]["template"]["components"][0]["parameters"] == [{"type": "text", "text": "Asha Kumar Singh"}]


def test_a_value_that_is_only_line_breaks_is_still_refused(setup):
    sessions, settings = setup
    with sessions() as db: add_template(db)
    with pytest.raises(HTTPException) as refused:
        queue(sessions, settings, key="blank-value-key", body="", kind="template", template={"name": "recruitment_update", "language": "en", "parameters": ["\n \t"]})
    assert refused.value.status_code == 422


# ------------------------------------------------------------------ 6. numbers people type

@pytest.mark.parametrize("typed,expected", [
    ("+91 98765 43210", "919876543210"), ("+91-98765-43210", "919876543210"), ("(98765) 43210", "919876543210"),
    ("098765 43210", "919876543210"), ("9876543210", "919876543210"), ("919876543210", "919876543210"),
    ("00 44 7700 900123", "447700900123"), ("+44 7700 900123", "447700900123"), ("  +91.98765.43210 ", "919876543210"),
])
def test_typed_numbers_become_the_digits_whatsapp_uses(typed, expected):
    assert chat.typed_peer_number(typed, "91") == expected


@pytest.mark.parametrize("typed", ["", "abc", "12345", "+1", "98765 43210 x", "++91 98765 43210", "91+9876543210", "1234567890123456"])
def test_numbers_that_are_not_numbers_are_still_refused(typed):
    with pytest.raises(HTTPException) as refused: chat.typed_peer_number(typed, "91")
    assert refused.value.status_code == 422


def test_opening_a_conversation_with_any_spelling_reaches_the_same_chat(setup):
    sessions, _ = setup
    with sessions() as db:
        first = chat.open_conversation(db, PHONE, "+91 98765 43210"); db.commit()
        again = chat.open_conversation(db, PHONE, "9876543210")
        assert first.peer_number == "919876543210" and again.id == first.id
        assert len(list(db.scalars(select(WhatsAppConversation).where(WhatsAppConversation.peer_number == "919876543210")))) == 1


# ------------------------------------------------------------------ 7. the 24-hour window has a safety margin at dispatch

def test_dispatch_stops_five_minutes_before_the_window_ends(setup, monkeypatch):
    sessions, settings = setup
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=23, minutes=50); db.commit()
    identifier = queue(sessions, settings)   # ten minutes of window left when it is queued
    with sessions() as db:   # time passes before the worker gets to it: two minutes left
        db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=23, minutes=58); db.commit()
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: pytest.fail("Two minutes before the end is too late to start a send"))
    chat.dispatch_attempt(identifier, sessions=sessions, settings=settings)
    with sessions() as db:
        row = db.get(WhatsAppSendAttempt, identifier)
        assert row.state == "cancelled" and "window" in row.last_error
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=23, minutes=50); db.commit()
    again = queue(sessions, settings, key="ten-minutes-left-key")
    monkeypatch.setattr(provider, "send_message", lambda *a, **k: "wamid.in-time")
    chat.dispatch_attempt(again, sessions=sessions, settings=settings)
    with sessions() as db: assert db.get(WhatsAppSendAttempt, again).state == "accepted"


def test_the_screen_and_the_queue_use_the_same_five_minute_margin(setup):
    """A message the screen offers must never be one dispatch will cancel: the margin applies to what is shown and to what is queued."""
    sessions, settings = setup
    with sessions() as db:
        db.get(WhatsAppConversation, "conversation").last_inbound_at = NOW - timedelta(hours=23, minutes=58); db.commit()
    with pytest.raises(HTTPException) as late: queue(sessions, settings)   # two minutes left
    assert late.value.status_code == 409 and "24-hour" in late.value.detail
    with sessions() as db:
        row = db.get(WhatsAppConversation, "conversation")
        view = chat.conversation_json(db, row, db.get(User, "admin"), settings, at=NOW)
        assert view["serviceWindowOpen"] is False
        assert view["serviceWindowExpiresAt"] == row.last_inbound_at + timedelta(hours=24)   # the real expiry is still shown
        row.last_inbound_at = NOW - timedelta(hours=23, minutes=50); db.commit()
    assert queue(sessions, settings, key="ten-minutes-key")   # ten minutes left: accepted
    with sessions() as db:
        row = db.get(WhatsAppConversation, "conversation")
        assert chat.conversation_json(db, row, db.get(User, "admin"), settings, at=NOW)["serviceWindowOpen"] is True


# ------------------------------------------------------------------ 3. an inbound international number keeps its own conversation

def test_a_ten_digit_international_sender_is_not_read_as_a_home_number_when_replying(setup):
    """+372 1234567 is ten digits with its country code. The typed-number rule would turn it into 91 3721234567 (a different conversation);
    the automatic-reply path must take it as it is, and only the screen's 'open chat' reads what a person typed."""
    sessions, settings = setup
    foreign = "3721234567"
    with sessions() as db:
        db.add(WhatsAppConversation(id="estonian", phone_number_id=PHONE, peer_number=foreign, last_inbound_at=None, last_message_at=NOW))
        add_template(db)
    with sessions() as db:
        assert chat.open_conversation(db, PHONE, foreign, strict=True).id == "estonian"
        assert chat.open_conversation(db, PHONE, "+372 1234567").id == "estonian"   # a plus sign states the country code
        typed = chat.open_conversation(db, PHONE, foreign)   # typed on the screen: read as a home number
        assert typed.peer_number == "913721234567" and typed.id != "estonian"
        db.rollback()
    with sessions() as db:
        row = chat.enqueue(db, PHONE, foreign, "", "estonian-key-1", actor_id="admin", settings=settings, kind="template",
                           template={"name": "recruitment_update", "language": "en", "parameters": ["Candidate"]})
        db.commit()
        assert row.conversation_id == "estonian"
        assert db.scalar(select(WhatsAppConversation.id).where(WhatsAppConversation.peer_number == "913721234567")) is None
