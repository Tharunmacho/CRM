"""Who a WhatsApp sender is linked to, and which failures of one delivered item are skipped instead of redelivered (disposable PostgreSQL).

The sender of a file is not automatically its candidate: an agent or a relative forwards other people's documents. The sender's number is copied to a
candidate and the chat is linked to a candidate only when the number really is theirs. A malformed or duplicate item is skipped; a lost connection or a
deadlock is a 5xx so Meta redelivers.
"""
from copy import deepcopy
import hashlib
import hmac
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.db.models import Candidate, User, WhatsAppConversation, WhatsAppMessage
from app.services.whatsapp_intake import process_message, receive_payload
from test_workflow_scheduler import workspace  # noqa: F401  (fixture used by `setup`)
from test_database_naming import schema_database  # noqa: F401  (fixture used by `workspace`)
from auto_assign import round_robin_on  # noqa: F401  (fixture: these tests exercise the round-robin, which is opt-in)
from test_email_intake import extraction, pdf as make_pdf
from test_whatsapp_intake import setup, payload  # noqa: F401  (fixtures/helpers shared with the intake tests)

pytestmark = pytest.mark.usefixtures("round_robin_on")

SENDER = "919999999999"
PHONE = "12345"


def pdf_for(name):
    return make_pdf(f"Page one: certificate {name}", f"Page two: resume {name}", f"Page three: passport {name}")


def arrive(sessions, settings, provider_id, *, sender=SENDER):
    body = payload(message_id=provider_id)
    message = body["entry"][0]["changes"][0]["value"]["messages"][0]
    message["from"] = sender
    body["entry"][0]["changes"][0]["value"]["contacts"][0]["wa_id"] = sender
    with sessions() as db:
        assert receive_payload(db, body, settings) == 1
        return db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == provider_id))


def read(sessions, settings, message_id, name, *, phone=None, email=None):
    output = extraction(fields={"name": name, "phone": phone, "email": email or f"{name.split()[0].lower()}@example.test"})
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (pdf_for(name), "application/pdf"),
                    extract=lambda *_, **__: deepcopy(output))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        return row.candidate_id


def chat(sessions, sender=SENDER):
    with sessions() as db:
        row = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.peer_number == sender))
        return None if row is None else row.candidate_id


def whatsapp_of(sessions, candidate_id):
    with sessions() as db:
        return db.get(Candidate, candidate_id).whatsapp


# ---------------------------------------------------------------- the sender is not automatically the candidate

def test_a_sender_who_forwards_two_different_people_is_linked_only_to_the_first_and_not_copied_onto_the_second(setup):
    sessions, settings = setup
    first = read(sessions, settings, arrive(sessions, settings, "wamid.first"), "First Person")
    assert whatsapp_of(sessions, first) == SENDER and chat(sessions) == first   # a sender with one candidate and no number in the document: still the candidate
    second = read(sessions, settings, arrive(sessions, settings, "wamid.second"), "Second Person")
    assert second != first
    assert not whatsapp_of(sessions, second), "the forwarder's number must not become the second candidate's WhatsApp number"
    assert chat(sessions) == first, "the chat is never re-pointed at the second candidate"
    from app.services.whatsapp_chat import candidate_for_number
    with sessions() as db:
        assert candidate_for_number(db, SENDER).id == first   # the number is still unambiguous


def test_a_document_with_its_own_different_number_is_never_given_the_senders_and_does_not_link_the_chat(setup):
    sessions, settings = setup
    candidate = read(sessions, settings, arrive(sessions, settings, "wamid.agent"), "Agent Forwarded", phone="+91 90000 00077")
    assert not whatsapp_of(sessions, candidate)
    assert chat(sessions) is None, "the sender's number is not this candidate's number: the chat stays unlinked for a supervisor"
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(WhatsAppConversation).where(WhatsAppConversation.peer_number == SENDER)) == 1


def test_a_sender_whose_document_carries_the_same_number_is_the_candidate_and_is_linked(setup):
    sessions, settings = setup
    candidate = read(sessions, settings, arrive(sessions, settings, "wamid.self"), "Self Sender", phone="+91 99999 99999")
    assert chat(sessions) == candidate
    assert whatsapp_of(sessions, candidate) == SENDER


# ---------------------------------------------------------------- a number somebody else already holds

def test_a_number_another_candidate_already_holds_is_not_copied_and_does_not_link_the_chat(setup):
    sessions, settings = setup
    other_sender = "918888888888"
    holder = read(sessions, settings, arrive(sessions, settings, "wamid.holder", sender=other_sender), "Holder Person", phone="+91 99999 99999")
    assert chat(sessions, other_sender) is None   # that sender is not the holder: unlinked
    newcomer = read(sessions, settings, arrive(sessions, settings, "wamid.newcomer"), "Newcomer Person")
    assert newcomer != holder
    assert not whatsapp_of(sessions, newcomer), "the number already belongs to another candidate: copying it would make it ambiguous"
    assert chat(sessions) is None, "the chat is not linked to the newcomer when someone else holds the sender's number"
    from app.services.whatsapp_chat import candidate_for_number, number_holders
    with sessions() as db:
        assert [row.id for row in number_holders(db, SENDER)] == [holder]
        assert candidate_for_number(db, SENDER).id == holder   # still unambiguous


def test_a_supervisor_link_on_the_senders_chat_stops_the_number_being_copied_onto_another_person(setup):
    sessions, settings = setup
    existing = read(sessions, settings, arrive(sessions, settings, "wamid.existing", sender="918888888888"), "Existing Person", phone="+91 90000 00055")
    with sessions() as db:   # a supervisor linked the sender's chat to that candidate by hand (the sender has no files of its own yet)
        db.add(WhatsAppConversation(id="by-hand", phone_number_id=PHONE, peer_number=SENDER, candidate_id=existing))
        db.commit()
    newcomer = read(sessions, settings, arrive(sessions, settings, "wamid.newcomer2"), "Another Person")
    assert not whatsapp_of(sessions, newcomer), "the sender is already linked to a different candidate, so it is a forwarder"
    assert chat(sessions) == existing, "the hand-made link is never replaced"


# ---------------------------------------------------------------- resolve links only when the number is the candidate's

def resolve_with(sessions, settings, monkeypatch, provider_id, *, sender, candidate_phone):
    from app.api.routers import whatsapp as router
    monkeypatch.setattr(router, "attach_original", lambda *_: set())   # the files are not what is under test
    message_id = arrive(sessions, settings, provider_id, sender=sender)
    with sessions() as db:
        db.get(Candidate, "candidate").phone = candidate_phone
        db.get(Candidate, "candidate").whatsapp = None
        row = db.get(WhatsAppMessage, message_id)
        row.status, row.object_key = "needs_review", "retained/original.pdf"
        db.commit()
        router.resolve(message_id, router.ResolveIn(candidateId="candidate", reason="Reviewed by an administrator"), db, db.get(User, "admin"))
        db.commit()


def test_resolve_links_the_chat_when_the_senders_number_is_the_candidates(setup, monkeypatch):
    sessions, settings = setup
    resolve_with(sessions, settings, monkeypatch, "wamid.resolve-yes", sender=SENDER, candidate_phone="+91 99999 99999")
    assert chat(sessions) == "candidate"


def test_resolve_leaves_the_chat_unlinked_when_the_number_is_somebody_elses(setup, monkeypatch):
    sessions, settings = setup
    resolve_with(sessions, settings, monkeypatch, "wamid.resolve-no", sender="918888888888", candidate_phone="+91 90000 00099")
    assert chat(sessions, "918888888888") is None


# ---------------------------------------------------------------- one bad item is skipped, a lost connection is redelivered

def client(sessions, settings, monkeypatch):
    from app.api.routers import whatsapp
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    app = FastAPI(); app.include_router(whatsapp.router)

    def db():
        with sessions() as session:
            yield session
    app.dependency_overrides[whatsapp.get_db] = db
    return TestClient(app, raise_server_exceptions=False)   # a server error is the answer under test, not an exception to re-raise here


def delivery(*ids):
    messages = [{"id": identifier, "from": SENDER, "timestamp": "1792570000", "type": "text", "text": {"body": identifier}} for identifier in ids]
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": PHONE, "display_phone_number": "+91 11111 11111"}, "messages": messages}}]}]}


def post(test_client, settings, body):
    raw = json.dumps(body).encode()
    signature = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return test_client.post("/api/whatsapp/webhook", content=raw, headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"})


def stored_count(sessions):
    with sessions() as db:
        return db.scalar(select(func.count()).select_from(WhatsAppMessage))


def failing_store(monkeypatch, error):
    from app.services import whatsapp_intake
    real = whatsapp_intake._store_message

    def store(db, channel, phone_id, message, contacts):
        if message["id"] == "wamid.fails":
            raise error
        return real(db, channel, phone_id, message, contacts)
    monkeypatch.setattr(whatsapp_intake, "_store_message", store)


def test_a_lost_connection_inside_one_message_is_a_5xx_and_commits_nothing_so_meta_redelivers(setup, monkeypatch):
    sessions, settings = setup
    failing_store(monkeypatch, OperationalError("INSERT", {}, Exception("server closed the connection unexpectedly")))
    response = post(client(sessions, settings, monkeypatch), settings, delivery("wamid.before", "wamid.fails", "wamid.after"))
    assert response.status_code == 500
    assert stored_count(sessions) == 0, "nothing of a delivery that will be redelivered is committed"


def test_a_data_error_inside_one_message_is_skipped_and_the_delivery_is_still_acknowledged(setup, monkeypatch):
    sessions, settings = setup
    failing_store(monkeypatch, DataError("INSERT", {}, Exception("invalid input")))
    response = post(client(sessions, settings, monkeypatch), settings, delivery("wamid.before", "wamid.fails", "wamid.after"))
    assert response.status_code == 200 and response.json() == {"received": 2}
    assert stored_count(sessions) == 2


def test_which_failures_are_about_the_item_and_which_are_transient():
    from app.services.whatsapp_chat import is_data_error
    for error in (ValueError("x"), TypeError("x"), AttributeError("x"), KeyError("x"), IndexError("x"), OverflowError("x"), DataError("s", {}, Exception("x")),
                  IntegrityError("s", {}, SimpleNamespace(sqlstate="23505"))):
        assert is_data_error(error), type(error).__name__
    for error in (OperationalError("s", {}, Exception("connection lost")), IntegrityError("s", {}, SimpleNamespace(sqlstate="40001")),
                  RuntimeError("x"), OSError("x")):
        assert not is_data_error(error), type(error).__name__
