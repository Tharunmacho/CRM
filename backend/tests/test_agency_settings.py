"""Agency settings and the managed currency list (migration 0017), against the guarded disposable PostgreSQL DB.

The seeds must equal the values that used to be compiled into the code, writes are admin-only and audited,
the order form and the stage-8 advance accept exactly the ACTIVE currencies, retired codes stay readable on stored
records, and a database without the two tables keeps working on the last-resort defaults.
"""
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    AgencySettings, Application, ApplicationStage, AuditEntry, Candidate, Client, Company, Currency, Designation,
    Order, Role, Source, User,
)
from test_database_naming import _migrate, schema_database
from test_application_workflow import attachment, complete, mapped, reach, save

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", agency_timezone="Asia/Kolkata", storage_dir="unused",
)):
    from app.api.routers import applications, data, settings as subject

from app.domain.stages import CURRENCY_CODES, StageOptions, validate_payload
from app.services import agency_settings as service, change_feed
from app.services.candidate_identity import normalize_phone

# The values the application used to carry in its source code; the migration must seed exactly these.
OLD_NAME, OLD_SHORT_NAME = "Care India Overseas", "Care India"
OLD_ORDER_CURRENCY, OLD_ADVANCE_CURRENCY, OLD_CALLING_CODE = "AED", "INR", "+91"
OLD_ACKNOWLEDGEMENT = ("Thank you for contacting Care India Overseas. We have received and saved "
                       "your candidate documents. Our recruitment team will review them and contact "
                       "you when there is a suitable next step.")
OLD_CODES = ["INR", "USD", "EUR", "SGD", "AED", "SAR", "QAR", "OMR", "KWD", "BHD", "GBP"]
GOOD = {"name": "Example Agency", "shortName": "Example", "defaultOrderCurrency": "SAR", "defaultAdvanceCurrency": "USD",
        "homeCallingCode": "+44", "defaultNationality": "", "acknowledgementText": "Thank you for writing to Example Agency."}


@pytest.fixture(autouse=True)
def keep_loggers_enabled():
    """Alembic's env.py reconfigures logging and switches off loggers that already exist; give them back."""
    before = {name: logger.disabled for name, logger in logging.root.manager.loggerDict.items() if isinstance(logger, logging.Logger)}
    yield
    for name, was_disabled in before.items():
        logging.getLogger(name).disabled = was_disabled


@pytest.fixture
def desk(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    service.reset_cache()
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("staff", Role.staff))])
        db.add(Client(id="client", name="Synthetic contact"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Synthetic employer"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add_all([Order(id=key, ref=key, company_id="company", designation_id="designation", openings=2)
                    for key in ("order", "other-order")])
        db.add_all([Candidate(id=key, ref=key, name=key, phone=f"90000000{n:02}", source=Source.walk_in, owner_id="staff")
                    for n, key in enumerate(("candidate", "second-candidate"))])
        db.commit()
    monkeypatch.setattr(applications, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path)))
    monkeypatch.setattr(data, "get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
    env = SimpleNamespace(sessions=sessions, storage=tmp_path, engine=schema_database)

    def sign_in(key):
        with sessions() as db:
            env.actor = db.get(User, key)

    env.sign_in = sign_in
    sign_in("admin")
    app = FastAPI()
    for router in (subject.router, data.router, applications.router):
        app.include_router(router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: env.actor
    with TestClient(app) as client:
        env.client = client
        yield env
    service.reset_cache()


def put(env, **changes):
    return env.client.put("/api/settings", json={**GOOD, **changes})


def codes(env):
    return [item["code"] for item in env.client.get("/api/settings").json()["currencies"]]


def add_currency(env, code, name="Test currency", **extra):
    response = env.client.post("/api/settings/currencies", json={"code": code, "name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def set_active(env, code, active):
    return env.client.put(f"/api/settings/currencies/{code}", json={"name": "Test currency", "active": active})


# ------------------------------------------------------------------ migration

def test_seeds_equal_the_values_that_used_to_be_in_the_code(desk):
    with desk.sessions() as db:
        row = db.get(AgencySettings, 1)
        assert (row.name, row.short_name) == (OLD_NAME, OLD_SHORT_NAME)
        assert (row.default_order_currency, row.default_advance_currency) == (OLD_ORDER_CURRENCY, OLD_ADVANCE_CURRENCY)
        assert row.home_calling_code == OLD_CALLING_CODE
        assert row.default_nationality is None  # the 'Indian' pre-fill is gone and stays gone
        assert row.acknowledgement_text == OLD_ACKNOWLEDGEMENT
        listed = db.execute(select(Currency.code, Currency.active).order_by(Currency.sort_order)).all()
    assert [code for code, _ in listed] == OLD_CODES == list(CURRENCY_CODES)
    assert all(active for _, active in listed)


def test_migration_is_additive_idempotent_and_reversible(schema_database):
    engine = schema_database
    _migrate(engine, "0016_intake_communications")
    before = set(inspect(engine).get_table_names())
    assert not {"agency_settings", "currencies"} & before
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO auth_users (id,name,email,role,active,receives_auto_assign,on_leave) VALUES ('u','U','u@example.test','admin',true,false,false)"))
    _migrate(engine, "head")
    assert set(inspect(engine).get_table_names()) - before == {"agency_settings", "currencies"}
    with engine.begin() as connection:
        connection.execute(text("UPDATE agency_settings SET name = 'Edited', default_nationality = 'Kenyan'"))
        connection.execute(text("UPDATE currencies SET active = false WHERE code = 'GBP'"))
        # Pretend the revision was lost after the tables were made: running 0017 again must be harmless.
        connection.execute(text("UPDATE alembic_version SET version_num = '0016_intake_communications'"))
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name, default_nationality FROM agency_settings")).one() == ("Edited", "Kenyan")
        assert connection.scalar(text("SELECT count(*) FROM currencies")) == 11
        assert connection.scalar(text("SELECT active FROM currencies WHERE code='GBP'")) is False  # an admin's edit survives
    _migrate(engine, "0016_intake_communications", downgrade=True)
    assert set(inspect(engine).get_table_names()) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_users")) == 1
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT name FROM agency_settings")) == OLD_NAME


def test_singleton_and_code_rules_are_enforced_by_the_database(desk):
    from sqlalchemy.exc import IntegrityError
    for statement in (
        "INSERT INTO agency_settings (id,name,default_order_currency,default_advance_currency,home_calling_code,acknowledgement_text) VALUES (2,'x','AED','INR','+91','text text text')",
        "UPDATE agency_settings SET home_calling_code = '91'",
        "UPDATE agency_settings SET default_order_currency = 'ZZZ'",
        "INSERT INTO currencies (code,name) VALUES ('usd','lower case')",
    ):
        with pytest.raises(IntegrityError), desk.engine.begin() as connection:
            connection.execute(text(statement))


# ------------------------------------------------------------------ permissions and validation

def test_everyone_signed_in_reads_but_only_an_admin_writes(desk):
    for who in ("staff", "manager", "admin"):
        desk.sign_in(who)
        body = desk.client.get("/api/settings").json()
        assert body["name"] == OLD_NAME and body["timezone"] == "Asia/Kolkata" and body["slaHours"] == 48 and body["editable"] is True
    for who in ("staff", "manager"):
        desk.sign_in(who)
        assert put(desk).status_code == 403
        assert desk.client.post("/api/settings/currencies", json={"code": "JPY", "name": "Yen"}).status_code == 403
        assert desk.client.put("/api/settings/currencies/AED", json={"name": "Dirham"}).status_code == 403
        assert desk.client.delete("/api/settings/currencies/AED").status_code == 403
    with desk.sessions() as db:
        assert db.get(AgencySettings, 1).name == OLD_NAME and db.get(Currency, "JPY") is None
        assert db.scalar(select(func.count()).select_from(AuditEntry)) == 0


def test_the_public_profile_shows_only_the_name_and_needs_no_session(desk):
    desk.client.app.dependency_overrides.pop(subject.current_user)
    body = desk.client.get("/api/settings/public").json()
    assert body == {"name": OLD_NAME, "shortName": OLD_SHORT_NAME}
    assert desk.client.get("/api/settings").status_code == 401


@pytest.mark.parametrize(("changes", "field"), [
    ({"name": "   "}, "name"),
    ({"name": "x" * 121}, "name"),
    ({"shortName": "x" * 61}, "shortName"),
    ({"defaultOrderCurrency": "ZZZ"}, "defaultOrderCurrency"),
    ({"defaultAdvanceCurrency": ""}, "defaultAdvanceCurrency"),
    ({"homeCallingCode": "91"}, "homeCallingCode"),
    ({"homeCallingCode": "+12345"}, "homeCallingCode"),
    ({"homeCallingCode": "+9a"}, "homeCallingCode"),
    ({"defaultNationality": "x" * 81}, "defaultNationality"),
    ({"acknowledgementText": "short"}, "acknowledgementText"),
    ({"acknowledgementText": "x" * 2001}, "acknowledgementText"),
])
def test_invalid_settings_are_refused_with_a_message_for_the_field(desk, changes, field):
    response = put(desk, **changes)
    assert response.status_code == 422
    errors = {item["field"]: item["message"] for item in response.json()["detail"]["errors"]}
    assert field in errors and errors[field]
    with desk.sessions() as db:
        assert db.get(AgencySettings, 1).name == OLD_NAME


def test_a_default_currency_must_be_active(desk):
    add_currency(desk, "JPY")
    assert set_active(desk, "JPY", False).status_code == 200
    assert put(desk, defaultOrderCurrency="JPY").status_code == 422
    assert put(desk, defaultAdvanceCurrency="JPY").status_code == 422


def test_saving_changes_everything_is_audited_and_the_calling_code_applies(desk):
    assert normalize_phone("+91 90000 00001") == "9000000001"
    response = put(desk, defaultNationality="Kenyan")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["shortName"], body["defaultOrderCurrency"], body["defaultAdvanceCurrency"], body["homeCallingCode"], body["defaultNationality"]) == \
        ("Example Agency", "Example", "SAR", "USD", "+44", "Kenyan")
    assert desk.client.get("/api/settings").json()["name"] == "Example Agency"
    with desk.sessions() as db:
        row = db.get(AgencySettings, 1)
        assert row.updated_by == "admin" and row.acknowledgement_text == GOOD["acknowledgementText"]
        entry = db.scalar(select(AuditEntry).where(AuditEntry.entity_type == "agency_settings"))
        assert entry.actor_id == "admin" and entry.at is not None and entry.action == "Updated agency settings"
        assert entry.meta["changes"]["name"] == {"from": OLD_NAME, "to": "Example Agency"}
        assert entry.meta["changes"]["acknowledgement_text"] == {"changed": True}  # the long text is not copied into the log
    # +44 is now the home code: a UK number loses its code, an Indian one keeps it.
    assert normalize_phone("+44 7700 900123") == "7700900123"
    assert normalize_phone("+91 90000 00001") == "919000000001"
    assert put(desk, defaultNationality="Kenyan").status_code == 200  # saving the same values changes nothing
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_type == "agency_settings")) == 1


def test_bootstrap_carries_the_same_settings(desk):
    put(desk)
    bootstrap = desk.client.get("/api/bootstrap").json()["settings"]
    direct = desk.client.get("/api/settings").json()
    for payload in (bootstrap, direct):
        payload.pop("updatedAt")
    assert bootstrap == direct
    assert bootstrap["name"] == "Example Agency" and bootstrap["currencies"][0] == {
        "code": "INR", "name": "Indian Rupee", "symbol": None, "active": True, "sortOrder": 10}


# ------------------------------------------------------------------ the currency list

def test_currencies_can_be_added_changed_and_deleted_with_an_audit_trail(desk):
    after = add_currency(desk, " jpy ", "Japanese Yen", symbol="¥")
    assert after["currencies"][-1] == {"code": "JPY", "name": "Japanese Yen", "symbol": "¥", "active": True, "sortOrder": 120}
    assert desk.client.post("/api/settings/currencies", json={"code": "JPY", "name": "Again"}).status_code == 409
    for bad in ({"code": "yen!", "name": "x"}, {"code": "JP", "name": "x"}, {"code": "JPX", "name": ""}, {"code": "JPX", "name": "x", "symbol": "123456789"}):
        assert desk.client.post("/api/settings/currencies", json=bad).status_code == 422
    changed = desk.client.put("/api/settings/currencies/JPY", json={"name": "Yen", "symbol": "", "active": False, "sortOrder": 5})
    assert changed.status_code == 200
    assert next(item for item in changed.json()["currencies"] if item["code"] == "JPY") == {"code": "JPY", "name": "Yen", "symbol": None, "active": False, "sortOrder": 5}
    assert desk.client.put("/api/settings/currencies/NOPE", json={"name": "x"}).status_code == 404
    deleted = desk.client.delete("/api/settings/currencies/JPY")
    assert deleted.status_code == 200 and "JPY" not in [item["code"] for item in deleted.json()["currencies"]]
    with desk.sessions() as db:
        actions = [(row.action, row.target, row.actor_id) for row in db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "currency").order_by(AuditEntry.at, AuditEntry.id))]
    assert actions == [("Added currency", "JPY", "admin"), ("Updated currency", "JPY", "admin"), ("Deleted currency", "JPY", "admin")]


def test_a_currency_that_is_used_cannot_be_deleted_only_switched_off(desk):
    message = "This currency is used by existing orders. Mark it inactive instead."
    with desk.sessions() as db:
        db.get(Order, "order").currency = "QAR"
        stage = ApplicationStage(application_id="x", stage=8, attempt=1, data={"amountCurrency": "KWD"}, outcome="Collected")
        db.add(Application(id="app-1", candidate_id="candidate", order_id="other-order", current_stage=8, status="closed",
                           draft={"amountCurrency": "BHD"}, lock_version=0))
        db.flush()
        stage.application_id = "app-1"
        db.add(stage)
        db.add(ApplicationStage(application_id="app-1", stage=8, attempt=2, data={}, outcome="Collected", amount=5, amount_currency="OMR"))
        db.commit()
    for used in ("QAR", "KWD", "BHD", "OMR"):  # an order, stage JSON, a saved draft, the stage's currency column
        refused = desk.client.delete(f"/api/settings/currencies/{used}")
        assert refused.status_code == 409 and refused.json()["detail"] == message, used
        assert set_active(desk, used, False).status_code == 200  # switching it off is the way out
    assert desk.client.delete("/api/settings/currencies/EUR").status_code == 200  # never used
    assert "EUR" not in codes(desk) and "QAR" in codes(desk)


def test_the_defaults_and_the_last_active_currency_are_protected(desk):
    assert set_active(desk, "AED", False).status_code == 409   # the default order currency
    assert desk.client.delete("/api/settings/currencies/INR").status_code == 409   # the default advance currency
    for code in codes(desk):
        if code not in {"AED", "INR"}:
            assert set_active(desk, code, False).status_code == 200
    assert put(desk, defaultOrderCurrency="INR", defaultAdvanceCurrency="INR").status_code == 200
    assert set_active(desk, "AED", False).status_code == 200
    refused = set_active(desk, "INR", False)
    assert refused.status_code == 409  # it is the default; and it is the only one left


# ------------------------------------------------------------------ orders

def order_body(**extra):
    return {"companyId": "company", "designationId": "designation", "openings": 1, **extra}


def test_orders_accept_exactly_the_active_currencies(desk):
    assert desk.client.post("/api/orders", json=order_body(currency="JPY")).status_code == 422
    add_currency(desk, "JPY")
    created = desk.client.post("/api/orders", json=order_body(currency="JPY"))
    assert created.status_code == 201 and created.json()["currency"] == "JPY"
    assert set_active(desk, "JPY", False).status_code == 200
    refused = desk.client.post("/api/orders", json=order_body(currency="JPY"))
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"][0]["field"] == "currency"
    # An old order keeps its retired currency: editing other terms is fine, switching to another retired code is not.
    order_id = created.json()["id"]
    same = desk.client.put(f"/api/orders/{order_id}", json=order_body(currency="JPY", openings=3))
    assert same.status_code == 200 and same.json()["currency"] == "JPY" and same.json()["openings"] == 3
    assert desk.client.put(f"/api/orders/{order_id}", json=order_body(currency="GBP")).status_code == 200
    assert set_active(desk, "GBP", False).status_code == 200
    assert desk.client.put(f"/api/orders/{order_id}", json=order_body(currency="JPY")).status_code == 422
    assert desk.client.get("/api/bootstrap").json()["orders"]  # stored values stay readable


def test_an_order_without_a_currency_gets_the_agency_default(desk):
    assert desk.client.post("/api/orders", json=order_body()).json()["currency"] == "AED"
    assert put(desk).status_code == 200
    assert desk.client.post("/api/orders", json=order_body()).json()["currency"] == "SAR"


# ------------------------------------------------------------------ stage 8

def to_stage_eight(env):
    app_id = mapped(env)
    reach(env, app_id, 8)
    return app_id


def advance_draft(env, app_id, **values):
    return save(env, app_id, {"collected": "yes", "amount": "100", "mode": "upi", **values})


def test_the_advance_accepts_exactly_the_active_currencies(desk):
    app_id = to_stage_eight(desk)
    assert set_active(desk, "GBP", False).status_code == 200
    refused = advance_draft(desk, app_id, amountCurrency="GBP")
    assert refused.status_code == 422 and "available currencies" in json.dumps(refused.json())
    assert advance_draft(desk, app_id, amountCurrency="AED").json()["draft"]["amountCurrency"] == "AED"
    assert advance_draft(desk, app_id, amountCurrency="XXX").status_code == 422
    add_currency(desk, "JPY")
    receipt = attachment(desk, app_id, 8, "receipt")
    result = complete(desk, app_id, {"collected": "yes", "amount": "1500", "amountCurrency": "JPY", "receipt": receipt})
    assert result["currentStage"] == 9
    with desk.sessions() as db:
        record = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == 8))
        assert (record.amount_currency, record.data["amountCurrency"]) == ("JPY", "JPY")
    # The retired code stays readable on what was already stored.
    assert set_active(desk, "JPY", False).status_code == 200
    stages = [row for item in desk.client.get("/api/bootstrap").json()["applications"] if item["id"] == app_id for row in item["stages"]]
    assert any(row["data"].get("amountCurrency") == "JPY" for row in stages)


def test_the_advance_currency_defaults_to_the_agency_setting(desk):
    app_id = to_stage_eight(desk)
    assert advance_draft(desk, app_id).json()["draft"]["amountCurrency"] == "INR"
    assert put(desk).status_code == 200  # default advance currency USD
    assert advance_draft(desk, app_id).json()["draft"]["amountCurrency"] == "USD"


def test_stage_validation_is_pure_and_falls_back_without_options():
    assert validate_payload(8, {"amount": "10"})["amountCurrency"] == "INR"
    assert validate_payload(8, {"amount": "10"}, StageOptions(frozenset({"JPY"}), "JPY"))["amountCurrency"] == "JPY"
    with pytest.raises(Exception):
        validate_payload(8, {"amount": "10", "amountCurrency": "INR"}, StageOptions(frozenset({"JPY"}), "JPY"))


# ------------------------------------------------------------------ database without the tables

def drop_tables(env):
    with env.engine.begin() as connection:
        connection.execute(text("DROP TABLE agency_settings"))
        connection.execute(text("DROP TABLE currencies"))
    service.reset_cache()


def test_a_database_without_the_tables_keeps_working_on_the_last_resort_defaults(desk):
    drop_tables(desk)
    settings = desk.client.get("/api/settings").json()
    assert settings["editable"] is False and settings["name"] == service.FALLBACK_NAME
    assert [item["code"] for item in settings["currencies"]] == list(CURRENCY_CODES)
    assert settings["defaultOrderCurrency"] == "AED" and settings["homeCallingCode"] == "+91"
    assert put(desk).status_code == 503
    assert desk.client.post("/api/settings/currencies", json={"code": "JPY", "name": "Yen"}).status_code == 503
    assert desk.client.get("/api/settings/public").json() == {}
    boot = desk.client.get("/api/bootstrap")
    assert boot.status_code == 200 and boot.json()["settings"]["editable"] is False and boot.json()["orders"]
    assert desk.client.post("/api/orders", json=order_body(currency="GBP")).json()["currency"] == "GBP"
    assert desk.client.post("/api/orders", json=order_body()).json()["currency"] == "AED"
    assert desk.client.post("/api/orders", json=order_body(currency="XXX")).status_code == 422
    app_id = to_stage_eight(desk)
    assert advance_draft(desk, app_id).json()["draft"]["amountCurrency"] == "INR"
    assert advance_draft(desk, app_id, amountCurrency="XXX").status_code == 422
    assert normalize_phone("+91 90000 00001") == "9000000001"


def test_empty_tables_fall_back_too_and_saving_recreates_the_row(desk):
    with desk.engine.begin() as connection:
        connection.execute(text("DELETE FROM agency_settings"))
    settings = desk.client.get("/api/settings").json()
    assert settings["editable"] is False and settings["name"] == service.FALLBACK_NAME
    assert put(desk).status_code == 200
    with desk.sessions() as db:
        assert db.get(AgencySettings, 1).name == "Example Agency"
        assert db.scalar(select(AuditEntry.action).where(AuditEntry.entity_type == "agency_settings")) == "Created agency settings"
    with desk.engine.begin() as connection:
        connection.execute(text("DELETE FROM agency_settings"))  # the row's foreign keys keep the list from being emptied first
        connection.execute(text("DELETE FROM currencies"))
    service.reset_cache()
    assert desk.client.post("/api/orders", json=order_body(currency="GBP")).status_code == 201  # empty list: the fallback list


# ------------------------------------------------------------------ live updates

def test_the_two_tables_feed_the_settings_topic():
    assert change_feed.classify_statement("UPDATE agency_settings SET name=%(n)s WHERE id = 1") == ("UPDATE", "agency_settings")
    assert change_feed.classify_statement("DELETE FROM currencies WHERE code = %(c)s") == ("DELETE", "currencies")
    pending = change_feed.Pending()
    pending.note_table("UPDATE", "currencies")
    body = json.loads(pending.payload(now_ms=1))
    assert body["t"] == ["settings"]  # not a report input
    assert "settings" in change_feed.CLIENT_TOPICS
    for role in ("staff", "manager", "admin"):
        assert change_feed.visible_to(body, role, "someone")["topics"] == ["settings"]


def test_saving_settings_announces_the_settings_topic_to_every_open_screen(desk):
    change_feed.install()
    url = desk.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as listener:
        listener.execute(f"LISTEN {change_feed.CHANNEL}")
        list(listener.notifies(timeout=0.3))
        assert put(desk).status_code == 200
        add_currency(desk, "JPY")
        seen = [json.loads(note.payload) for note in listener.notifies(timeout=0.8)]
    assert seen and all(message["t"] == ["settings"] for message in seen)
