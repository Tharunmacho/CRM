"""The owner's bell when a linked candidate writes on WhatsApp (disposable PostgreSQL).

A candidate's recruiter is told once an hour that the candidate replied (text, or a photo/file/voice note), never twice for a redelivered
message, never for reactions or receipts, and never when the chat is not linked to a candidate or the candidate has no active owner.
The phone number is not written into the notice.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Candidate, Notification, User
from app.services.whatsapp_chat import record_inbound_payload
from app.services.whatsapp_intake import receive_payload
from test_workflow_scheduler import workspace  # noqa: F401  (fixture used by `setup`)
from test_database_naming import schema_database  # noqa: F401  (fixture used by `workspace`)
from test_whatsapp_intake import setup  # noqa: F401  (fixture shared with the intake tests)

KNOWN = "919000000001"        # the workspace candidate's own number (9000000001), owned by the staff user "owner"
STRANGER = "919888877776"     # nobody's number
KIND = "whatsapp_reply"


def body_of(message_id, sender, kind="text", text="Hello, I have updated my passport.", stamp=1792570000):
    message = {"id": message_id, "from": sender, "timestamp": str(stamp), "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    elif kind == "image":
        message["image"] = {"id": "555", "mime_type": "image/jpeg"}
    elif kind == "reaction":
        message["reaction"] = {"emoji": "👍", "message_id": "wamid.earlier"}
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "12345", "display_phone_number": "+91 00000 00000"},
        "contacts": [{"wa_id": sender, "profile": {"name": "Some Sender"}}],
        "messages": [message]}}]}]}


def deliver(sessions, settings, payload, at=None):
    with sessions() as db:
        receive_payload(db, payload, settings)
        record_inbound_payload(db, payload, settings, at=at)
        db.commit()


def notices(sessions):
    with sessions() as db:
        return list(db.scalars(select(Notification).where(Notification.kind == KIND).order_by(Notification.created_at, Notification.id)))


def test_a_linked_candidates_text_tells_the_owner_once_without_the_phone_number(setup):
    sessions, settings = setup
    deliver(sessions, settings, body_of("wamid.one", KNOWN))
    [notice] = notices(sessions)
    assert notice.user_id == "owner" and notice.link_to == "/candidates/candidate" and not notice.read
    assert notice.title.startswith("Synthetic candidate") and notice.title.endswith("replied on WhatsApp")
    assert "Hello, I have updated my passport." in notice.detail
    for shown in (notice.title, notice.detail, notice.link_to):
        assert "9000000001" not in shown and KNOWN not in shown


def test_a_long_text_shows_only_its_first_eighty_characters(setup):
    sessions, settings = setup
    deliver(sessions, settings, body_of("wamid.long", KNOWN, text="word " * 60))
    [notice] = notices(sessions)
    assert notice.detail.startswith("“word word") and notice.detail.endswith("…”")
    assert len(notice.detail) <= 80 + 3


def test_a_second_message_within_the_hour_adds_no_second_notice_but_one_after_the_hour_does(setup):
    sessions, settings = setup
    deliver(sessions, settings, body_of("wamid.a", KNOWN, stamp=1792570000))
    deliver(sessions, settings, body_of("wamid.b", KNOWN, stamp=1792570060))
    assert len(notices(sessions)) == 1
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    deliver(sessions, settings, body_of("wamid.c", KNOWN, text="Anything new?", stamp=1792570120), at=later)
    found = notices(sessions)
    assert len(found) == 2 and "Anything new?" in found[-1].detail


def test_a_redelivered_payload_never_notifies_twice_even_after_the_hour(setup):
    sessions, settings = setup
    first = body_of("wamid.replay", KNOWN)
    deliver(sessions, settings, first)
    assert len(notices(sessions)) == 1
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    deliver(sessions, settings, first, at=later)      # Meta delivers the very same message again
    deliver(sessions, settings, first, at=later)
    assert len(notices(sessions)) == 1


def test_a_photo_says_so_and_a_reaction_or_a_receipt_says_nothing(setup):
    sessions, settings = setup
    deliver(sessions, settings, body_of("wamid.photo", KNOWN, kind="image"))
    [notice] = notices(sessions)
    assert notice.detail == "Sent a photo."
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    deliver(sessions, settings, body_of("wamid.react", KNOWN, kind="reaction", stamp=1792570500), at=later)
    status_only = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "12345"}, "statuses": [{"id": "wamid.out", "status": "delivered", "timestamp": "1792570600", "recipient_id": KNOWN}]}}]}]}
    with sessions() as db:
        record_inbound_payload(db, status_only, settings, at=later)
        db.commit()
    assert len(notices(sessions)) == 1


def test_a_chat_that_is_not_linked_to_a_candidate_notifies_nobody(setup):
    sessions, settings = setup
    deliver(sessions, settings, body_of("wamid.stranger", STRANGER))
    assert notices(sessions) == []


@pytest.mark.parametrize("change", ["no_owner", "inactive_owner"])
def test_a_candidate_without_an_active_owner_notifies_nobody(setup, change):
    sessions, settings = setup
    with sessions() as db:
        if change == "no_owner":
            db.get(Candidate, "candidate").owner_id = None
        else:
            db.get(User, "owner").active = False
        db.commit()
    deliver(sessions, settings, body_of("wamid.ownerless", KNOWN))
    assert notices(sessions) == []
