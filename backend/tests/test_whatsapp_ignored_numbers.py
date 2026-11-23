"""WhatsApp numbers to ignore: the admin's list, its enforcement on arrival, on the worker and on acknowledgements, and migration 0059.

Disposable PostgreSQL only (TEST_DATABASE_URL, a loopback crm_schema_test* database); synthetic provider calls, nothing leaves the machine.
"""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import AuditEntry, Candidate, IntakeAcknowledgement, User, WhatsAppIgnoredNumber, WhatsAppMessage
from app.services.intake_acknowledgements import whatsapp_suppression
from app.services.whatsapp_ignore import IGNORED_ACK_REASON, IGNORED_REASON, ignored_keys, is_ignored, key_of
from app.services.whatsapp_intake import process_message, receive_payload
from test_database_naming import _migrate, schema_database  # noqa: F401
from test_workflow_scheduler import workspace  # noqa: F401
from auto_assign import round_robin_on  # noqa: F401
from test_whatsapp_intake import payload, setup  # noqa: F401

pytestmark = pytest.mark.usefixtures("round_robin_on")

SAME_NUMBER = ("+91 98765 43210", "919876543210", "09876543210", "9876543210", "+91-98765-43210", "(0) 98765 43210")


def payload_from(sender, message_id="wamid.from.sender", **kwargs):
    body = deepcopy(payload(message_id, **kwargs))
    value = body["entry"][0]["changes"][0]["value"]
    value["messages"][0]["from"] = sender
    value["contacts"][0]["wa_id"] = sender
    return body


def api(sessions, monkeypatch):
    from app.api.routers import whatsapp
    app = FastAPI(); app.include_router(whatsapp.router)

    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[whatsapp.get_db] = database
    return app, whatsapp


# ------------------------------------------------------------------------------------------------ the comparison key

@pytest.mark.parametrize("typed", SAME_NUMBER)
def test_every_way_of_writing_one_number_gives_one_key(typed):
    assert key_of(typed) == "9876543210"


def test_a_foreign_number_keeps_its_country_code_and_unusable_input_has_no_key():
    assert key_of("+44 7700 900123") == key_of("447700900123") == "447700900123"
    assert key_of("+44 7700 900123") != key_of("7700900123")          # two different people: the foreign number is not collapsed onto an Indian one
    for bad in ("", "   ", None, "abc", "98765", "+1", "12345678901234567890", "98765 43210 ext 4", "9876543210 / 9876543211"):
        assert key_of(bad) == "", bad


# ------------------------------------------------------------------------------------------------ the admin API

def test_only_admin_level_users_manage_the_list_and_every_change_is_audited(setup, monkeypatch):
    sessions, _settings = setup
    app, whatsapp = api(sessions, monkeypatch)
    with sessions() as db, TestClient(app) as client:
        for role in ("owner", "other"):      # recruiters (staff) are refused
            app.dependency_overrides[whatsapp.current_user] = lambda role=role: db.get(User, role)
            assert client.get("/api/whatsapp/ignored-numbers").status_code == 403
            assert client.post("/api/whatsapp/ignored-numbers", json={"number": "9876543210"}).status_code == 403
            assert client.delete("/api/whatsapp/ignored-numbers/anything").status_code == 403
        app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, "manager")    # an MD is admin-level
        assert client.get("/api/whatsapp/ignored-numbers").json() == {"items": []}
        added = client.post("/api/whatsapp/ignored-numbers", json={"number": " +91 98765 43210 ", "label": " Recruiter Asha "})
        assert added.status_code == 201
        entry = added.json()
        assert entry["number"] == "9876543210" and entry["label"] == "Recruiter Asha" and entry["id"]
        app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, "admin")
        listed = client.get("/api/whatsapp/ignored-numbers").json()["items"]
        assert [(item["id"], item["number"], item["label"]) for item in listed] == [(entry["id"], "9876543210", "Recruiter Asha")]
        assert client.delete(f"/api/whatsapp/ignored-numbers/{entry['id']}").json() == {"ok": True}
        assert client.get("/api/whatsapp/ignored-numbers").json() == {"items": []}
    with sessions() as db:
        actions = list(db.scalars(select(AuditEntry.action).where(AuditEntry.entity_type == "whatsapp_ignored_number").order_by(AuditEntry.at)))
        assert actions == ["Added a WhatsApp number to the ignore list", "Removed a WhatsApp number from the ignore list"]
        assert db.scalar(select(func.count()).select_from(WhatsAppIgnoredNumber)) == 0


def test_duplicates_in_any_format_invalid_numbers_and_unknown_ids_are_refused(setup, monkeypatch):
    sessions, _settings = setup
    app, whatsapp = api(sessions, monkeypatch)
    with sessions() as db, TestClient(app) as client:
        app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, "admin")
        assert client.post("/api/whatsapp/ignored-numbers", json={"number": "9876543210"}).status_code == 201
        for typed in SAME_NUMBER:
            assert client.post("/api/whatsapp/ignored-numbers", json={"number": typed}).status_code == 409, typed
        for bad in ("abc", "98765", "98765 43210 ext 4", "9" * 20):
            assert client.post("/api/whatsapp/ignored-numbers", json={"number": bad}).status_code == 422, bad
        assert client.post("/api/whatsapp/ignored-numbers", json={"number": ""}).status_code == 422
        assert client.post("/api/whatsapp/ignored-numbers", json={"number": "9876543211", "label": "x" * 201}).status_code == 422
        assert client.delete("/api/whatsapp/ignored-numbers/not-an-id").status_code == 404
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(WhatsAppIgnoredNumber)) == 1


# ------------------------------------------------------------------------------------------------ enforcement

def add_ignored(sessions, number, label="test"):
    with sessions() as db:
        db.add(WhatsAppIgnoredNumber(phone_key=key_of(number), label=label))
        db.commit()


def test_a_file_from_an_ignored_number_is_kept_as_ignored_and_never_read(setup):
    sessions, settings = setup
    add_ignored(sessions, "+91 99999 99999")
    with sessions() as db:
        assert receive_payload(db, payload("wamid.ignored"), settings) == 1      # stored: visible and auditable
        row = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.provider_message_id == "wamid.ignored"))
        assert (row.status, row.last_error, row.media_id) == ("ignored", IGNORED_REASON, "777")
        assert db.scalar(select(func.count()).select_from(WhatsAppMessage).where(WhatsAppMessage.status.in_(("pending", "processing", "retry")))) == 0
        message_id = row.id
    # even if the worker is asked for it (a manual retry), nothing is downloaded and no candidate is made
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: pytest.fail("An ignored sender's file must never be fetched"),
                    extract=lambda *_, **__: pytest.fail("An ignored sender's file must never be read"))
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1      # only the fixture's candidate


def test_every_format_of_an_ignored_number_is_ignored_and_other_senders_are_not(setup):
    sessions, settings = setup
    add_ignored(sessions, "9876543210")
    with sessions() as db:
        for index, sender in enumerate(("919876543210", "9876543210", "09876543210")):
            assert receive_payload(db, payload_from(sender, f"wamid.fmt.{index}"), settings) == 1
        assert receive_payload(db, payload_from("919876543211", "wamid.other"), settings) == 1
        statuses = dict(db.execute(select(WhatsAppMessage.provider_message_id, WhatsAppMessage.status)).all())
        assert [statuses[f"wamid.fmt.{i}"] for i in range(3)] == ["ignored"] * 3
        assert statuses["wamid.other"] == "pending"


def test_a_number_added_after_its_file_arrived_is_caught_by_the_worker(setup):
    sessions, settings = setup
    with sessions() as db:
        assert receive_payload(db, payload("wamid.early"), settings) == 1
        message_id = db.scalar(select(WhatsAppMessage.id))
        assert db.get(WhatsAppMessage, message_id).status == "pending"
    add_ignored(sessions, "919999999999")
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: pytest.fail("fetched"), extract=lambda *_, **__: pytest.fail("read"))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert (row.status, row.last_error) == ("ignored", IGNORED_REASON)


def test_removing_a_number_puts_it_back_to_work(setup):
    sessions, settings = setup
    add_ignored(sessions, "9999999999")
    with sessions() as db:
        assert receive_payload(db, payload("wamid.first"), settings) == 1
        db.execute(text("DELETE FROM whatsapp_ignored_numbers"))
        db.commit()
        assert receive_payload(db, payload("wamid.second"), settings) == 1
        statuses = dict(db.execute(select(WhatsAppMessage.provider_message_id, WhatsAppMessage.status)).all())
        assert statuses == {"wamid.first": "ignored", "wamid.second": "pending"}


def test_an_ignored_number_is_never_acknowledged_but_others_still_can_be(setup):
    sessions, _settings = setup
    add_ignored(sessions, "9876543210")
    with sessions() as db:
        assert whatsapp_suppression(db, "12345", "919876543210") == IGNORED_ACK_REASON
        assert whatsapp_suppression(db, "12345", "9876543210") == IGNORED_ACK_REASON
        assert whatsapp_suppression(db, "12345", "919876543211") is None
        assert is_ignored(db, "+91 98765 43210") and not is_ignored(db, "+91 98765 43211") and not is_ignored(db, "")
        assert ignored_keys(db) == frozenset({"9876543210"})


def test_staff_can_still_message_an_ignored_number_by_hand(setup):
    """The list only governs what arrives: enqueue (the chat's manual send) does not look at it."""
    import inspect as pyinspect
    from app.services import whatsapp_chat
    assert "whatsapp_ignore" not in pyinspect.getsource(whatsapp_chat.enqueue)


# ------------------------------------------------------------------------------------------------ migration

def test_migration_0059_up_and_down(schema_database):  # noqa: F811
    engine = schema_database
    _migrate(engine, "0058_job_requirements")
    assert "whatsapp_ignored_numbers" not in inspect(engine).get_table_names()
    _migrate(engine, "0059_whatsapp_ignored_numbers")
    catalog = inspect(engine)
    assert "whatsapp_ignored_numbers" in catalog.get_table_names()
    assert {c["name"] for c in catalog.get_columns("whatsapp_ignored_numbers")} == set(WhatsAppIgnoredNumber.__table__.columns.keys())
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO whatsapp_ignored_numbers (id, phone_key) VALUES ('i1', '9876543210')"))
        row = connection.execute(text("SELECT label, created_at IS NOT NULL AS stamped FROM whatsapp_ignored_numbers")).one()
        assert (row.label, row.stamped) == ("", True)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO whatsapp_ignored_numbers (id, phone_key) VALUES ('i2', '9876543210')"))
    _migrate(engine, "0058_job_requirements", downgrade=True)
    assert "whatsapp_ignored_numbers" not in inspect(engine).get_table_names()
    _migrate(engine, "head")
    assert "whatsapp_ignored_numbers" in inspect(engine).get_table_names()
