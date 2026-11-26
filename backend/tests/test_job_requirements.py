"""Job requirements sheet: rows, the recruiter-added columns, the ad file, roles, audit trail and migration 0058.

Disposable PostgreSQL only (TEST_DATABASE_URL, a loopback crm_schema_test* database); the schema is built from the models.
Ad files go to a temporary local storage directory.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.core.security import SESSION_COOKIE, hash_password
from app.db.models import AuditEntry, Base, JobRequirement, JobRequirementField, JobRequirementOption, Role, Session as LoginSession, User
from app.db.session import get_db
from test_database_naming import _guard_test_url, _migrate, schema_database  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
API = "/api/job-requirements"
FIELDS = "/api/job-requirement-fields"


@pytest.fixture
def env(tmp_path, monkeypatch):
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB")
    engine = create_engine(_guard_test_url(raw), connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for identifier, role in (("admin", Role.admin), ("md", Role.manager), ("rec", Role.staff), ("rec2", Role.staff)):
            db.add(User(id=identifier, name=identifier.upper() + " Person", email=f"{identifier}@example.test", role=role, active=True,
                        phone="+919000000000", receives_auto_assign=False, on_leave=False, password_hash=hash_password("initial-test-password")))
        db.flush()
        for identifier in ("admin", "md", "rec", "rec2"):
            db.add(LoginSession(id=f"session-{identifier}", user_id=identifier, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()
    from app.api.routers import job_requirements
    storage = tmp_path / "uploads"
    monkeypatch.setattr(job_requirements, "get_settings", lambda: SimpleNamespace(storage_backend="local", storage_dir=str(storage), agency_timezone="Asia/Kolkata"))
    application = FastAPI()
    application.include_router(job_requirements.router)

    def database():
        with factory() as db:
            yield db
    application.dependency_overrides[get_db] = database
    try:
        with TestClient(application, base_url="https://testserver", raise_server_exceptions=False) as client:
            yield SimpleNamespace(client=client, factory=factory, engine=engine, storage=storage)
    finally:
        engine.dispose()


def act_as(env, who):
    env.client.cookies.clear()
    env.client.cookies.set(SESSION_COOKIE, f"session-{who}")


def make(env, who="rec", **body):
    act_as(env, who)
    response = env.client.post(API, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add_field(env, label, kind="text", who="rec"):
    act_as(env, who)
    response = env.client.post(FIELDS, json={"label": label, "type": kind})
    assert response.status_code == 201, response.text
    return response.json()


def audit_actions(env, entity_id):
    with env.factory() as db:
        return [row.action for row in db.scalars(select(AuditEntry).where(AuditEntry.entity_id == entity_id).order_by(AuditEntry.at, AuditEntry.id))]


def upload(env, item_id, data=PNG, name="flyer.png", kind="image/png", who="rec"):
    act_as(env, who)
    return env.client.post(f"{API}/{item_id}/ad", files={"file": (name, data, kind)})


# ------------------------------------------------------------------ rows

def test_signed_out_is_refused(env):
    env.client.cookies.clear()
    assert env.client.get(API).status_code == 401
    assert env.client.post(API, json={}).status_code == 401


def test_blank_row_defaults_doc_date_to_today_in_agency_zone(env):
    item = make(env)
    assert item["docDate"] == datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    assert (item["clientName"], item["country"], item["status"], item["custom"], item["adUrl"], item["adName"]) == ("", "", "active", {}, None, None)
    assert item["details"] == {key: "" for key in ("position", "vacancies", "placeOfWork", "contractPeriod", "qualification", "agePreference", "additionalSkills", "basicSalary", "workingHours", "overtime", "otCalculation", "food", "accommodation", "insurance", "transport", "remarks", "deadline")} and len(item["details"]) == 17
    assert item["positions"] == [] and item["general"] == []
    assert item["createdBy"] == {"id": "rec", "name": "REC Person"} and item["updatedBy"] == item["createdBy"]
    assert item["createdAt"] and item["updatedAt"]
    assert audit_actions(env, item["id"]) == ["Added job requirement"]


def test_typed_row_and_empty_body_without_json(env):
    item = make(env, docDate="2026-10-01", clientName="  Al Noor  ", country="Qatar", status="closed",
                details={"position": "Welder", "vacancies": 12, "deadline": "2026-11-05", "remarks": "Need 12 welders"})
    assert (item["docDate"], item["clientName"], item["country"], item["status"]) == ("2026-10-01", "Al Noor", "Qatar", "closed")
    assert item["details"]["position"] == "Welder" and item["details"]["vacancies"] == "12" and item["details"]["deadline"] == "2026-11-05"
    act_as(env, "rec")
    assert env.client.post(API).status_code == 201   # no body at all is a blank row too


def test_list_filters_search_total_and_order(env):
    old = make(env, docDate="2026-09-01", clientName="Zed Co", country="Oman", details={"position": "Driver"})
    newest = make(env, docDate="2026-11-01", clientName="Al Noor", country="Qatar", details={"remarks": "Need Welders urgently", "placeOfWork": "Doha site", "qualification": "ITI diploma"})
    same_day_later = make(env, docDate="2026-11-01", clientName="Beta", country="Qatar", status="closed", details={"position": "Cook"})
    act_as(env, "rec2")
    body = env.client.get(API).json()
    assert [item["id"] for item in body["items"]] == [same_day_later["id"], newest["id"], old["id"]]   # newest DOC, then newest created
    assert body["total"] == 3 and body["fields"] == []
    assert [i["id"] for i in env.client.get(API, params={"status": "active"}).json()["items"]] == [newest["id"], old["id"]]
    assert [i["id"] for i in env.client.get(API, params={"status": "closed"}).json()["items"]] == [same_day_later["id"]]
    for term, expected in (("qatar", {newest["id"], same_day_later["id"]}), ("AL NOOR", {newest["id"]}), ("welders", {newest["id"]}),
                           ("driver", {old["id"]}), ("doha", {newest["id"]}), ("iti DIPLOMA", {newest["id"]}), ("nothing-here", set())):
        found = env.client.get(API, params={"q": term}).json()
        assert {i["id"] for i in found["items"]} == expected and found["total"] == len(expected), term
    assert env.client.get(API, params={"q": "100%"}).json()["total"] == 0   # LIKE wildcards are literal
    page = env.client.get(API, params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and [i["id"] for i in page["items"]] == [newest["id"]]
    assert env.client.get(API, params={"status": "bogus"}).status_code == 422


def test_patch_merges_details_and_custom(env):
    note = add_field(env, "Agent note")
    qty = add_field(env, "Quota", "number")
    item = make(env, clientName="Al Noor", details={"position": "Welder", "basicSalary": "1500"}, custom={note["id"]: "first", qty["id"]: "5"})
    act_as(env, "rec2")
    changed = env.client.patch(f"{API}/{item['id']}", json={"country": "Qatar", "details": {"basicSalary": "1800", "food": "Provided"}, "custom": {note["id"]: "second"}})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["clientName"] == "Al Noor" and body["country"] == "Qatar"
    assert body["details"]["position"] == "Welder" and body["details"]["basicSalary"] == "1800" and body["details"]["food"] == "Provided"
    assert body["custom"] == {note["id"]: "second", qty["id"]: "5"}
    assert body["updatedBy"] == {"id": "rec2", "name": "REC2 Person"} and body["createdBy"]["id"] == "rec"
    cleared = env.client.patch(f"{API}/{item['id']}", json={"details": {"position": ""}, "custom": {qty["id"]: ""}, "docDate": None}).json()
    assert cleared["details"]["position"] == "" and cleared["details"]["basicSalary"] == "1800"
    assert cleared["custom"] == {note["id"]: "second"} and cleared["docDate"] is None
    assert env.client.patch(f"{API}/missing", json={}).status_code == 404
    assert audit_actions(env, item["id"]).count("Edited job requirement") == 2


def test_invalid_values_are_422(env):
    number = add_field(env, "Quota", "number")
    when = add_field(env, "Visa date", "date")
    yes = add_field(env, "Medical paid", "yes_no")
    item = make(env)
    url = f"{API}/{item['id']}"
    bad = [{"custom": {number["id"]: "abc"}}, {"custom": {number["id"]: "nan"}}, {"custom": {when["id"]: "01/02/2026"}},
           {"custom": {yes["id"]: "maybe"}}, {"custom": {"no-such-field": "x"}}, {"custom": {number["id"]: "x" * 2001}},
           {"details": {"mystery": "x"}}, {"details": {"position": "x" * 2001}}, {"details": {"remarks": "x" * 20001}}, {"details": {"salary": "1"}}, {"details": {"notes": "1"}}, {"details": {"text": "1"}},
           {"details": {"interviewDate": "2026-11-05"}}, {"details": {"food": "x" * 2001}},
           {"details": {"deadline": "tomorrow"}}, {"status": "paused"}, {"unknown": 1}, {"docDate": "not-a-date"}]
    for payload in bad:
        assert env.client.patch(url, json=payload).status_code == 422, payload
        assert env.client.post(API, json=payload).status_code == 422, payload
    assert env.client.patch(url, json={"details": {"remarks": "n" * 20000, "food": "t" * 2000}}).status_code == 200
    good = env.client.patch(url, json={"custom": {number["id"]: "12.5", when["id"]: "2026-12-01", yes["id"]: "yes"}}).json()["custom"]
    assert good == {number["id"]: "12.5", when["id"]: "2026-12-01", yes["id"]: "yes"}
    # nothing from the refused requests was stored
    with env.factory() as db:
        assert db.scalar(select(func.count()).select_from(JobRequirement)) == 1


def test_close_and_reopen_are_audited(env):
    item = make(env)
    act_as(env, "rec2")
    assert env.client.patch(f"{API}/{item['id']}", json={"status": "closed"}).json()["status"] == "closed"
    assert env.client.patch(f"{API}/{item['id']}", json={"status": "closed"}).json()["status"] == "closed"   # no second entry
    assert env.client.patch(f"{API}/{item['id']}", json={"status": "active"}).json()["status"] == "active"
    assert audit_actions(env, item["id"]) == ["Added job requirement", "Closed job requirement", "Reopened job requirement"]


def test_delete_is_admin_level_only(env):
    item = make(env)
    act_as(env, "rec")
    assert env.client.delete(f"{API}/{item['id']}").status_code == 403
    for who in ("admin", "md"):
        target = make(env)
        assert upload(env, target["id"]).status_code == 200
        key = Path(env.storage, *_stored_key(env, target["id"]).split("/"))
        assert key.is_file()
        act_as(env, who)
        assert env.client.delete(f"{API}/{target['id']}").status_code == 204
        assert not key.exists()
        assert env.client.get(f"{API}/{target['id']}/ad").status_code == 404
        assert "Deleted job requirement" in audit_actions(env, target["id"])
    act_as(env, "admin")
    assert env.client.delete(f"{API}/{item['id']}").status_code == 204
    assert env.client.delete(f"{API}/{item['id']}").status_code == 404


def _stored_key(env, item_id):
    with env.factory() as db:
        return db.get(JobRequirement, item_id).ad_object_key


# ------------------------------------------------------------------ ad file

def test_ad_upload_replace_download_delete(env):
    item = make(env)
    first = upload(env, item["id"], PNG, "Flyer One.png")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["adUrl"] == f"{API}/{item['id']}/ad" and body["adName"] == "Flyer One.png"
    old_key = _stored_key(env, item["id"])
    assert old_key.startswith(f"job-requirements/{item['id']}/")
    act_as(env, "rec2")
    download = env.client.get(body["adUrl"])
    assert download.status_code == 200 and download.content == PNG and download.headers["content-type"] == "image/png"
    assert "private" in download.headers["cache-control"]
    second = upload(env, item["id"], PDF, "ad.pdf", "application/pdf", who="rec2")
    assert second.status_code == 200 and second.json()["adName"] == "ad.pdf"
    assert not Path(env.storage, *old_key.split("/")).exists()   # the replaced file is removed
    act_as(env, "rec")
    pdf = env.client.get(body["adUrl"])
    assert pdf.content == PDF and pdf.headers["content-type"] == "application/pdf"
    for data, name, kind in ((JPG, "a.jpg", "image/jpeg"), (WEBP, "a.webp", "image/webp"), (GIF, "a.gif", "image/gif")):
        assert upload(env, item["id"], data, name, kind).status_code == 200, name
    assert upload(env, item["id"], JPG, "a.jpeg", "application/octet-stream").status_code == 200   # type from the extension
    key = _stored_key(env, item["id"])
    act_as(env, "rec")
    removed = env.client.delete(f"{API}/{item['id']}/ad")
    assert removed.status_code == 200 and removed.json()["adUrl"] is None and removed.json()["adName"] is None
    assert not Path(env.storage, *key.split("/")).exists()
    assert env.client.get(f"{API}/{item['id']}/ad").status_code == 404
    assert env.client.delete(f"{API}/{item['id']}/ad").status_code == 200   # idempotent
    with env.factory() as db:
        row = db.get(JobRequirement, item["id"])
        assert (row.ad_object_key, row.ad_file_name, row.ad_content_type, row.ad_size_kb) == (None, None, None, None)
    assert "Added job requirement ad" in audit_actions(env, item["id"]) and "Replaced job requirement ad" in audit_actions(env, item["id"])


def test_ad_rejects_bad_files(env):
    item = make(env)
    assert upload(env, item["id"], b"MZ\x90\x00", "run.exe", "application/octet-stream").status_code == 415
    assert upload(env, item["id"], b"<svg/>", "x.svg", "image/svg+xml").status_code == 415
    assert upload(env, item["id"], b"hello", "x.txt", "text/plain").status_code == 415
    assert upload(env, item["id"], b"not really a png", "fake.png", "image/png").status_code == 415   # magic bytes disagree
    assert upload(env, item["id"], PDF, "fake.png", "image/png").status_code == 415
    assert upload(env, item["id"], b"", "empty.png", "image/png").status_code == 422
    assert upload(env, item["id"], PNG + b"\x00" * (10 * 1024 * 1024), "big.png", "image/png").status_code == 413
    assert upload(env, "missing", PNG).status_code == 404
    assert _stored_key(env, item["id"]) is None
    assert not env.storage.exists() or not any(env.storage.rglob("*.*"))
    env.client.cookies.clear()
    assert env.client.post(f"{API}/{item['id']}/ad", files={"file": ("a.png", PNG, "image/png")}).status_code == 401
    assert env.client.get(f"{API}/{item['id']}/ad").status_code == 401


# ------------------------------------------------------------------ recruiter-added columns

def test_fields_create_duplicate_rename_delete(env):
    first = add_field(env, "  Visa   Type ")
    assert first["label"] == "Visa Type" and first["key"] == "visa_type" and first["type"] == "text" and first["sort"] == 0
    second = add_field(env, "Quota", "number")
    assert second["sort"] == 1 and second["type"] == "number"
    act_as(env, "rec2")
    assert env.client.post(FIELDS, json={"label": "VISA TYPE", "type": "text"}).status_code == 409
    other = add_field(env, "Visa-Type", "text", who="rec2")           # a different label with the same slug still gets its own key
    assert other["key"] == "visa_type_2"
    for payload in ({"label": "", "type": "text"}, {"label": "x" * 81, "type": "text"}, {"label": "Ok", "type": "color"}, {}):
        assert env.client.post(FIELDS, json=payload).status_code == 422, payload
    assert [f["label"] for f in env.client.get(API).json()["fields"]] == ["Visa Type", "Quota", "Visa-Type"]
    renamed = env.client.patch(f"{FIELDS}/{second['id']}", json={"label": "Quota total"})
    assert renamed.status_code == 200 and renamed.json()["label"] == "Quota total" and renamed.json()["key"] == "quota"
    assert env.client.patch(f"{FIELDS}/{second['id']}", json={"label": "visa type"}).status_code == 409
    assert env.client.patch(f"{FIELDS}/{second['id']}", json={"label": "QUOTA TOTAL"}).status_code == 200   # own name, other case
    assert env.client.patch(f"{FIELDS}/missing", json={"label": "x"}).status_code == 404


def test_field_delete_is_admin_level_and_removes_values(env):
    keep = add_field(env, "Keep")
    drop = add_field(env, "Drop", "number")
    a = make(env, custom={keep["id"]: "k", drop["id"]: "1"})
    b = make(env, custom={drop["id"]: "2"})
    c = make(env)
    act_as(env, "rec")
    assert env.client.delete(f"{FIELDS}/{drop['id']}").status_code == 403
    act_as(env, "md")
    assert env.client.delete(f"{FIELDS}/{drop['id']}").status_code == 204
    assert env.client.delete(f"{FIELDS}/{drop['id']}").status_code == 404
    body = env.client.get(API).json()
    assert [f["id"] for f in body["fields"]] == [keep["id"]]
    customs = {i["id"]: i["custom"] for i in body["items"]}
    assert customs == {a["id"]: {keep["id"]: "k"}, b["id"]: {}, c["id"]: {}}
    # the column is gone, so its id is now an unknown column
    assert env.client.patch(f"{API}/{a['id']}", json={"custom": {drop["id"]: "3"}}).status_code == 422
    with env.factory() as db:
        assert db.scalar(select(func.count()).select_from(JobRequirementField)) == 1


# ------------------------------------------------------------------ migration

def test_migration_0058_up_and_down(schema_database):  # noqa: F811
    engine = schema_database
    _migrate(engine, "0057_traveller_number_format")
    assert not {"crm_job_requirements", "crm_job_requirement_fields"} & set(inspect(engine).get_table_names())
    _migrate(engine, "0058_job_requirements")
    catalog = inspect(engine)
    assert {"crm_job_requirements", "crm_job_requirement_fields"} <= set(catalog.get_table_names())
    assert {c["name"] for c in catalog.get_columns("crm_job_requirements")} == set(JobRequirement.__table__.columns.keys()) - {"positions", "general", "owner_ids"}   # added by 0061
    assert {c["name"] for c in catalog.get_columns("crm_job_requirement_fields")} == set(JobRequirementField.__table__.columns.keys())
    assert "ix_crm_job_requirements_status_doc_date" in {i["name"] for i in catalog.get_indexes("crm_job_requirements")}
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO crm_job_requirements (id) VALUES ('r1')"))
        row = connection.execute(text("SELECT status, client_name, country, details, custom, doc_date FROM crm_job_requirements")).one()
        assert (row.status, row.client_name, row.country, row.details, row.custom, row.doc_date) == ("active", "", "", {}, {}, None)
        connection.execute(text("INSERT INTO crm_job_requirement_fields (id, key, label) VALUES ('f1', 'k', 'K')"))
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO crm_job_requirements (id, status) VALUES ('r2', 'paused')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO crm_job_requirement_fields (id, key, label, type) VALUES ('f2', 'k2', 'K2', 'color')"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO crm_job_requirement_fields (id, key, label) VALUES ('f3', 'k', 'Again')"))
    _migrate(engine, "0057_traveller_number_format", downgrade=True)
    assert not {"crm_job_requirements", "crm_job_requirement_fields", "crm_job_requirement_options"} & set(inspect(engine).get_table_names())
    _migrate(engine, "head")
    assert {"crm_job_requirements", "crm_job_requirement_fields", "crm_job_requirement_options"} <= set(inspect(engine).get_table_names())


# ------------------------------------------------------------------ management side: owner, closing reason, filters, summary

def today_in_agency():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def on_day(offset):
    return (today_in_agency() + timedelta(days=offset)).isoformat()


def test_owner_defaults_to_creator_and_must_be_active_recruiter_or_md(env):
    with env.factory() as db:
        db.add(User(id="gone", name="Gone Person", email="gone@example.test", role=Role.staff, active=False, phone="+919000000001",
                    receives_auto_assign=False, on_leave=False, password_hash=hash_password("initial-test-password")))
        db.commit()
    mine = make(env)
    assert (mine["ownerId"], mine["ownerName"]) == ("rec", "REC Person")
    assert make(env, who="md")["ownerId"] == "md"
    assert make(env, who="admin")["ownerId"] is None   # an Admin is not offered as an owner
    assert make(env, ownerId="rec2")["ownerName"] == "REC2 Person"
    act_as(env, "rec")
    for bad in ("admin", "gone", "nobody"):
        assert env.client.post(API, json={"ownerId": bad}).status_code == 422, bad
        assert env.client.patch(f"{API}/{mine['id']}", json={"ownerId": bad}).status_code == 422, bad
    moved = env.client.patch(f"{API}/{mine['id']}", json={"ownerId": "md"}).json()
    assert (moved["ownerId"], moved["ownerName"]) == ("md", "MD Person")
    assert env.client.patch(f"{API}/{mine['id']}", json={"ownerId": "md"}).status_code == 200   # unchanged: no second entry
    assert env.client.patch(f"{API}/{mine['id']}", json={"ownerId": ""}).json()["ownerId"] is None
    assert audit_actions(env, mine["id"]).count("Reassigned job requirement") == 2


def test_closing_reason_and_closed_at(env):
    item = make(env)
    assert (item["closedReason"], item["closedAt"]) == ("", None)
    url = f"{API}/{item['id']}"
    act_as(env, "rec2")
    assert env.client.patch(url, json={"closedReason": "filled"}).status_code == 422          # still active
    assert env.client.patch(url, json={"status": "active", "closedReason": "other"}).status_code == 422
    assert env.client.patch(url, json={"status": "closed", "closedReason": "sold"}).status_code == 422
    closed = env.client.patch(url, json={"status": "closed", "closedReason": "filled"}).json()
    assert closed["closedReason"] == "filled" and closed["closedAt"]
    first_closed_at = closed["closedAt"]
    again = env.client.patch(url, json={"closedReason": "expired"}).json()
    assert again["closedReason"] == "expired" and again["closedAt"] == first_closed_at and again["status"] == "closed"
    assert env.client.patch(url, json={"closedReason": ""}).json()["closedReason"] == ""
    reopened = env.client.patch(url, json={"status": "active"}).json()
    assert (reopened["status"], reopened["closedReason"], reopened["closedAt"]) == ("active", "", None)
    skipped = env.client.patch(url, json={"status": "closed", "closedReason": ""}).json()    # "Skip" in the dialog
    assert skipped["status"] == "closed" and skipped["closedReason"] == "" and skipped["closedAt"]
    created = make(env, status="closed", closedReason="client_cancelled")
    assert created["closedReason"] == "client_cancelled" and created["closedAt"]
    act_as(env, "rec")
    assert env.client.post(API, json={"closedReason": "filled"}).status_code == 422             # active on creation


def test_filters_owner_client_country_dates_and_due(env):
    a = make(env, docDate="2026-10-01", clientName="Al Noor", country="Qatar", details={"deadline": on_day(-2)})
    b = make(env, who="rec2", docDate="2026-10-15", clientName="AL NOOR", country="Oman", details={"deadline": on_day(3)})
    c = make(env, who="md", docDate="2026-11-01", clientName="Beta", country="Qatar", details={"deadline": on_day(30)})
    d = make(env, docDate="2026-11-10", clientName="Gamma", country="qatar", status="closed", details={"deadline": on_day(-5)})
    act_as(env, "rec")

    def ids(**params):
        return {i["id"] for i in env.client.get(API, params=params).json()["items"]}
    assert ids(owner="rec2") == {b["id"]}
    assert ids(owner="rec") == {a["id"], d["id"]}
    assert ids(client="al noor") == {a["id"], b["id"]}
    assert ids(client="Al No") == set()               # exact, not a prefix
    assert ids(country="QATAR") == {a["id"], c["id"], d["id"]}
    assert ids(**{"from": "2026-10-15", "to": "2026-11-01"}) == {b["id"], c["id"]}
    assert ids(**{"from": "2026-11-02"}) == {d["id"]}
    assert ids(to="2026-10-01") == {a["id"]}
    assert ids(due="overdue") == {a["id"]}            # the closed one is not counted
    assert ids(due="soon") == {b["id"]}
    assert ids(owner="rec", country="qatar", status="closed") == {d["id"]}
    assert env.client.get(API, params={"from": "yesterday"}).status_code == 422
    assert env.client.get(API, params={"due": "never"}).status_code == 422
    assert env.client.get(API, params={"owner": "rec", "client": "al noor"}).json()["total"] == 1


def test_summary_covers_all_statuses_and_respects_other_filters(env):
    make(env, clientName="Overdue", country="Qatar", details={"deadline": on_day(-3)})
    make(env, clientName="Today", country="Qatar", details={"deadline": on_day(0)})
    make(env, who="rec2", clientName="Edge", country="Oman", details={"deadline": on_day(7)})
    make(env, who="rec2", clientName="Far", country="Oman", details={"deadline": on_day(8)})
    make(env, who="md", clientName="Nodate", country="Oman")
    make(env, clientName="Closed late", country="Qatar", status="closed", details={"deadline": on_day(-9)})
    old = make(env, clientName="Old", country="Qatar", docDate="2026-01-01")
    with env.factory() as db:
        db.get(JobRequirement, old["id"]).created_at = datetime.now(timezone.utc) - timedelta(days=10)
        db.commit()
    act_as(env, "rec2")
    body = env.client.get(API, params={"status": "closed", "limit": 1}).json()
    summary = body["summary"]
    assert body["total"] == 1 and len(body["items"]) == 1                       # the tab filters the list ...
    assert (summary["active"], summary["closed"]) == (6, 1)                      # ... not the summary
    assert (summary["dueSoon"], summary["overdue"], summary["addedThisWeek"]) == (2, 1, 6)
    assert summary["byOwner"] == [{"id": "md", "name": "MD Person", "active": 1}, {"id": "rec", "name": "REC Person", "active": 3},
                                  {"id": "rec2", "name": "REC2 Person", "active": 2}]
    narrowed = env.client.get(API, params={"country": "oman", "status": "active"}).json()["summary"]
    assert (narrowed["active"], narrowed["closed"], narrowed["dueSoon"], narrowed["overdue"]) == (3, 0, 1, 0)
    assert [o["id"] for o in narrowed["byOwner"]] == ["md", "rec2"]
    by_owner = env.client.get(API, params={"owner": "rec"}).json()["summary"]
    assert (by_owner["active"], by_owner["closed"], by_owner["dueSoon"], by_owner["overdue"]) == (3, 1, 1, 1)
    assert env.client.get(API, params={"q": "closed late"}).json()["summary"]["closed"] == 1
    ranged = env.client.get(API, params={"from": "2026-01-01", "to": "2026-01-31"}).json()["summary"]
    assert (ranged["active"], ranged["closed"], ranged["byOwner"]) == (1, 0, [{"id": "rec", "name": "REC Person", "active": 1}])
    empty = env.client.get(API, params={"q": "zzz"}).json()
    assert empty["summary"] == {"active": 0, "closed": 0, "addedThisWeek": 0, "dueSoon": 0, "overdue": 0, "byOwner": []} and empty["total"] == 0


# ------------------------------------------------------------------ export

def test_export_csv_and_xlsx_with_filters_and_formula_escape(env):
    import csv
    import io
    extra = add_field(env, "Visa type")
    make(env, docDate="2026-10-01", clientName="=cmd|' /C calc'!A0", country="Qatar", status="closed", closedReason="filled",
         details={"position": "Welder", "vacancies": "5", "placeOfWork": "Doha", "remarks": "Need welders", "deadline": "2026-10-30"}, custom={extra["id"]: "Employment"})
    make(env, docDate="2026-11-01", clientName="Beta", country="Oman", ownerId="rec2")
    act_as(env, "rec2")
    response = env.client.get(f"{API}/export", params={"format": "csv"})
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"] and "job-requirements.csv" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == ["S.No", "DOC", "Client name", "Country", "Job positions"] + ["Name of the Position", "No. of Requirements", "Place of Work", "Period of Contract", "Qualification", "Age Preference", "Additional Skills", "Basic Salary", "Working Hours", "Overtime", "OT Calculation", "Food", "Accommodation", "Insurance", "Transport", "Remarks / Special Conditions"] + [
        "General employment details", "Deadline", "Assigned to", "Status", "Closed reason", "Closed on", "Added by", "Last updated", "Visa type"]
    assert len(rows) == 3
    assert rows[1][:3] == ["1", "2026-11-01", "Beta"] and rows[1][23] == "REC2 Person" and rows[1][24] == "Active"   # newest DOC first
    older = rows[2]
    assert older[0] == "2" and older[2].startswith("'=")                                                                      # no formula injection
    assert older[4] == "Welder (5)"                                                                                           # legacy position / vacancies fill "Job positions"
    assert older[5:8] == ["Welder", "5", "Doha"] and older[20] == "Need welders" and older[21] == "" and older[22] == "2026-10-30" and older[23] == "REC Person"
    assert older[24:26] == ["Closed", "Filled"] and older[26] == today_in_agency().isoformat() and older[27] == "REC Person" and older[29] == "Employment"
    filtered = list(csv.reader(io.StringIO(env.client.get(f"{API}/export", params={"format": "csv", "status": "closed", "country": "qatar"}).content.decode("utf-8-sig"))))
    assert len(filtered) == 2 and filtered[1][3] == "Qatar"
    workbook = env.client.get(f"{API}/export", params={"format": "xlsx", "owner": "rec2"})
    assert workbook.status_code == 200 and workbook.content[:2] == b"PK" and "spreadsheetml" in workbook.headers["content-type"]
    assert env.client.get(f"{API}/export", params={"format": "pdf"}).status_code == 422
    act_as(env, "admin")
    assert env.client.get(f"{API}/export", params={"format": "csv"}).status_code == 200
    env.client.cookies.clear()
    assert env.client.get(f"{API}/export", params={"format": "csv"}).status_code == 401


# ------------------------------------------------------------------ history

def test_history_is_readable_and_newest_first(env):
    note = add_field(env, "Agent note")
    item = make(env, clientName="Al Noor")
    url = f"{API}/{item['id']}"
    act_as(env, "rec2")
    assert env.client.patch(url, json={"clientName": "Al Noor Trading", "country": "Qatar", "details": {"basicSalary": "1500", "remarks": "x", "deadline": "2026-12-01"},
                                       "custom": {note["id"]: "hi"}}).status_code == 200
    assert env.client.patch(url, json={"ownerId": "md"}).status_code == 200
    assert upload(env, item["id"], who="rec2").status_code == 200
    assert upload(env, item["id"], JPG, "b.jpg", "image/jpeg", who="rec2").status_code == 200
    act_as(env, "md")
    assert env.client.patch(url, json={"status": "closed", "closedReason": "filled"}).status_code == 200
    assert env.client.patch(url, json={"status": "active"}).status_code == 200
    assert env.client.patch(url, json={"status": "closed"}).status_code == 200
    assert env.client.delete(f"{url}/ad").status_code == 200
    env.client.cookies.clear()
    assert env.client.get(f"{url}/history").status_code == 401
    act_as(env, "rec")
    items = env.client.get(f"{url}/history").json()["items"]
    summaries = [i["summary"] for i in items]
    assert summaries == ["Ad removed", "Closed", "Reopened", "Closed (filled)", "Ad replaced", "Ad uploaded",
                         "Assigned to MD Person (was REC Person)", "Changed Client name, Country, Basic Salary, Remarks / Special Conditions, Deadline, Agent note",
                         "Added the requirement"]
    assert [i["actorName"] for i in items][:2] == ["MD Person", "MD Person"] and items[-1]["actorName"] == "REC Person"
    assert items[-1]["actorId"] == "rec" and items[-1]["action"] == "Added job requirement" and items[0]["at"] >= items[-1]["at"]
    act_as(env, "admin")
    assert env.client.delete(f"{FIELDS}/{note['id']}").status_code == 204
    assert "a removed column" in env.client.get(f"{url}/history").json()["items"][-2]["summary"]
    assert env.client.get(f"{API}/missing/history").status_code == 404


# ------------------------------------------------------------------ deadline reminders

def notices(env, user_id):
    from app.db.models import Notification
    with env.factory() as db:
        return [(n.title, n.detail, n.link_to) for n in db.scalars(select(Notification).where(
            Notification.user_id == user_id, Notification.kind == "job_requirement_due").order_by(Notification.created_at, Notification.id))]


def run_reminders(env, now):
    from app.services.job_requirement_reminders import deliver_job_requirement_reminders
    with env.factory() as db:
        result = deliver_job_requirement_reminders(db, now=now, agency_timezone="Asia/Kolkata")
        db.commit()
        return result


def test_deadline_reminders_owner_daily_admins_once_and_idempotent(env):
    today = make(env, clientName="Today Co", country="Qatar", details={"position": "Welder", "deadline": on_day(0)})
    late = make(env, clientName="Late Co", country="Oman", details={"deadline": on_day(-2)})
    make(env, clientName="Future Co", details={"deadline": on_day(1)})
    make(env, clientName="Closed Co", status="closed", details={"deadline": on_day(-4)})
    make(env, clientName="No deadline")
    make(env, who="rec2", clientName="Ownerless Co", ownerId="", details={"deadline": on_day(-1)})
    now = datetime.now(timezone.utc)
    assert run_reminders(env, now) == {"owners": 2, "admins": 4}              # Late and Ownerless overdue: two admin-level users each
    assert run_reminders(env, now) == {"owners": 0, "admins": 0}              # a rerun adds nothing
    titles = sorted(title for title, _, link in notices(env, "rec") if link == "/job-requirements")
    assert titles == ["Job requirement deadline is today", "Job requirement is 2 days overdue"]
    assert any("Late Co" in detail and "Oman" in detail for _, detail, _ in notices(env, "rec"))
    assert any("Today Co · Welder · Qatar" in detail for _, detail, _ in notices(env, "rec"))
    assert len(notices(env, "admin")) == 2 and len(notices(env, "md")) == 2 and notices(env, "rec2") == []
    # next day: owners are told again; the admin-level users hear only about what just became overdue (Today Co)
    tomorrow = now + timedelta(days=1)
    assert run_reminders(env, tomorrow) == {"owners": 3, "admins": 2}         # Today Co newly overdue (2 admin-level users); Future Co due; Late Co again
    assert run_reminders(env, tomorrow) == {"owners": 0, "admins": 0}
    assert len(notices(env, "rec")) == 5
    # a moved deadline is a new deadline: told to the admin-level users again once it passes
    act_as(env, "rec")
    env.client.patch(f"{API}/{late['id']}", json={"details": {"deadline": on_day(-1)}})
    assert run_reminders(env, tomorrow) == {"owners": 0, "admins": 2}
    # closing the requirement stops everything
    env.client.patch(f"{API}/{today['id']}", json={"status": "closed"})
    assert run_reminders(env, now + timedelta(days=2))["owners"] == 2


def test_deadline_reminders_skip_disabled_owner_and_never_raise(env, monkeypatch):
    item = make(env, who="rec2", details={"deadline": on_day(-1)})
    with env.factory() as db:
        db.get(User, "rec2").active = False
        db.commit()
    result = run_reminders(env, datetime.now(timezone.utc))
    assert result["owners"] == 0 and result["admins"] == 2 and notices(env, "rec2") == [] and item
    from app.services import workflow_scheduler
    from app.services import job_requirement_reminders

    def boom(*args, **kwargs):
        raise RuntimeError("database trouble")
    real = job_requirement_reminders.deliver_job_requirement_reminders
    monkeypatch.setattr(job_requirement_reminders, "deliver_job_requirement_reminders", boom)
    monkeypatch.setattr(workflow_scheduler, "_job_reminder_retry_at", 0.0)
    with env.factory() as db:
        outcome = workflow_scheduler.scan_job_requirement_deadlines(db)
        assert outcome == {"owners": 0, "admins": 0, "failed": True}
        db.commit()
    monkeypatch.setattr(workflow_scheduler, "_job_reminder_retry_at", 0.0)
    monkeypatch.setattr(job_requirement_reminders, "deliver_job_requirement_reminders", real)
    with env.factory() as db:
        assert workflow_scheduler.scan_job_requirement_deadlines(db, agency_timezone="Asia/Kolkata") == {"owners": 0, "admins": 0}


def test_rows_with_the_old_form_layout_are_tolerated_and_cleaned_on_the_next_patch(env):
    item = make(env, details={"position": "Welder"})
    with env.factory() as db:
        row = db.get(JobRequirement, item["id"])
        row.details = {"position": "Welder", "salary": "1500", "benefits": "Food", "notes": "n", "text": "old text", "interviewDate": "2026-11-05",
                       "deadline": on_day(2)}
        db.commit()
    act_as(env, "rec2")
    listed = env.client.get(API).json()
    shown = listed["items"][0]["details"]
    assert shown["position"] == "Welder" and shown["deadline"] == on_day(2) and len(shown) == 17 and "salary" not in shown
    assert listed["summary"]["dueSoon"] == 1
    assert env.client.get(f"{API}/export", params={"format": "csv"}).status_code == 200
    assert env.client.get(API, params={"q": "old text"}).json()["total"] == 0
    assert env.client.patch(f"{API}/{item['id']}", json={"clientName": "Al Noor"}).status_code == 200
    with env.factory() as db:
        assert db.get(JobRequirement, item["id"]).details == {"position": "Welder", "deadline": on_day(2)}


# ------------------------------------------------------------------ the client's Job Requirement Form: positions and general details

POSITIONS = [
    {"title": "Paratha Master", "vacancies": 3, "salary": "AED 1500",
     "requirements": [{"field": "Practical Skill / Video", "details": "Working video required for dosa and paratha preparation."}]},
    {"title": "Captain", "vacancies": "To be confirmed", "salary": "To be confirmed",
     "requirements": [{"field": "Qualification", "details": "Hotel management qualification."}, {"field": "Maximum Age", "details": "Up to 30 years."},
                      {"field": "Experience", "details": "Minimum 5 years."}]},
    {"title": "Waiter", "vacancies": "", "salary": "", "requirements": []},
]
GENERAL = [
    {"label": "Working Hours", "details": "12 hours"},
    {"label": "Weekly Off", "details": "2 days per month", "remarks": "As provided by client"},
    {"label": "Visa Type", "details": "Visit cum Employment", "remarks": "Confirm visa arrangement before finalisation"},
]


def read_xlsx(data):
    """{cell ref: text} of the first sheet and its merged ranges, from the raw XML (no spreadsheet library needed)."""
    import io
    import zipfile
    from xml.etree import ElementTree

    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cells = {}
    for cell in sheet.iter(main + "c"):
        text_ = "".join(part.text or "" for part in cell.iter(main + "t")) or "".join(part.text or "" for part in cell.iter(main + "v"))
        if text_:
            cells[cell.get("r")] = text_
    return cells, [merge.get("ref") for merge in sheet.iter(main + "mergeCell")]


def sheet_rows(cells):
    """The cells as a list of [A, B, C, D] text rows (row 1 first)."""
    last = max(int("".join(ch for ch in ref if ch.isdigit())) for ref in cells)
    return [[cells.get(f"{col}{number}", "") for col in "ABCD"] for number in range(1, last + 1)]


def test_create_with_positions_and_general_returns_the_new_shape(env):
    item = make(env, clientName="Al Noor", country="Abu Dhabi", positions=POSITIONS, general=GENERAL)
    assert len(item["positions"]) == 3 and len(item["general"]) == 3
    first, captain, waiter = item["positions"]
    assert set(first) == {"id", "title", "vacancies", "salary", "requirements"}
    assert (first["title"], first["vacancies"], first["salary"]) == ("Paratha Master", "3", "AED 1500")      # a number is kept as text
    assert [set(row) for row in first["requirements"]] == [{"id", "field", "details"}]
    assert [(r["field"], r["details"]) for r in captain["requirements"]] == [("Qualification", "Hotel management qualification."),
                                                                           ("Maximum Age", "Up to 30 years."), ("Experience", "Minimum 5 years.")]
    assert captain["vacancies"] == "To be confirmed" and (waiter["vacancies"], waiter["salary"], waiter["requirements"]) == ("", "", [])
    assert [set(row) for row in item["general"]] == [{"id", "label", "details", "remarks"}] * 3
    assert [(g["label"], g["details"], g["remarks"]) for g in item["general"]][:2] == [("Working Hours", "12 hours", ""), ("Weekly Off", "2 days per month", "As provided by client")]
    ids = [first["id"], captain["id"], waiter["id"]] + [r["id"] for p in item["positions"] for r in p["requirements"]] + [g["id"] for g in item["general"]]
    assert all(isinstance(value, str) and 0 < len(value) <= 40 for value in ids) and len(set(ids)) == len(ids)
    act_as(env, "rec2")
    listed = env.client.get(API).json()["items"][0]
    assert listed["positions"] == item["positions"] and listed["general"] == item["general"]
    assert listed["details"]["position"] == ""                                                               # the legacy keys are untouched
    mixed = make(env, details={"position": "Welder", "deadline": "2026-12-01"}, positions=[{"title": "Cook"}])
    assert mixed["details"]["position"] == "Welder" and [p["title"] for p in mixed["positions"]] == ["Cook"]
    assert mixed["positions"][0]["requirements"] == [] and mixed["positions"][0]["vacancies"] == ""


def test_strings_are_stripped_and_none_becomes_empty(env):
    item = make(env, positions=[{"title": "  Chef  ", "vacancies": None, "salary": "  2000 ", "requirements": [{"field": " Age ", "details": None}]}],
                general=[{"label": " Food ", "details": None, "remarks": "  Provided  "}])
    position = item["positions"][0]
    assert (position["title"], position["vacancies"], position["salary"]) == ("Chef", "", "2000")
    assert (position["requirements"][0]["field"], position["requirements"][0]["details"]) == ("Age", "")
    assert (item["general"][0]["label"], item["general"][0]["details"], item["general"][0]["remarks"]) == ("Food", "", "Provided")


def test_patch_replaces_clears_or_leaves_the_lists_alone(env):
    item = make(env, clientName="Al Noor", positions=POSITIONS, general=GENERAL)
    url = f"{API}/{item['id']}"
    act_as(env, "rec2")
    untouched = env.client.patch(url, json={"clientName": "Al Noor Trading"}).json()
    assert untouched["positions"] == item["positions"] and untouched["general"] == item["general"]
    nulls = env.client.patch(url, json={"positions": None, "general": None}).json()                         # null is not "clear"
    assert nulls["positions"] == item["positions"] and nulls["general"] == item["general"]
    replaced = env.client.patch(url, json={"positions": [{"title": "Cook", "vacancies": "2", "salary": "AED 1200", "requirements": [{"field": "Experience", "details": "1 year"}]}]}).json()
    assert [p["title"] for p in replaced["positions"]] == ["Cook"] and replaced["positions"][0]["requirements"][0]["field"] == "Experience"
    assert replaced["general"] == item["general"]                                                           # only the sent list changed
    again = env.client.patch(url, json={"general": [{"label": "Food", "details": "Provided"}]}).json()
    assert [g["label"] for g in again["general"]] == ["Food"] and [p["title"] for p in again["positions"]] == ["Cook"]
    cleared = env.client.patch(url, json={"positions": [], "general": []}).json()
    assert cleared["positions"] == [] and cleared["general"] == []
    # an unchanged resend (same ids, same text) is not an edit
    fresh = env.client.patch(url, json={"positions": replaced["positions"], "general": item["general"]}).json()
    edits = audit_actions(env, item["id"]).count("Edited job requirement")
    assert env.client.patch(url, json={"positions": fresh["positions"], "general": fresh["general"]}).status_code == 200
    assert audit_actions(env, item["id"]).count("Edited job requirement") == edits
    # the new keys never disturb the old details: they merge as before
    assert env.client.patch(url, json={"details": {"food": "Provided"}}).json()["positions"] == fresh["positions"]
    with env.factory() as db:
        row = db.get(JobRequirement, item["id"])
        assert row.positions == fresh["positions"] and row.general == fresh["general"]


def test_ids_are_filled_kept_and_made_unique(env):
    item = make(env, positions=[{"id": "p-a", "title": "A", "requirements": [{"id": "r-1", "field": "x"}, {"id": "r-1", "field": "y"}, {"field": "z"}, {"id": "  ", "field": "w"}]},
                                {"id": "p-a", "title": "B"}, {"title": "C"}],
                general=[{"id": "g-1", "label": "one"}, {"id": "g-1", "label": "two"}])
    a, b, c = item["positions"]
    assert a["id"] == "p-a" and b["id"] != "p-a" and b["id"] and c["id"] and len({a["id"], b["id"], c["id"]}) == 3
    requirement_ids = [r["id"] for r in a["requirements"]]
    assert requirement_ids[0] == "r-1" and all(requirement_ids) and len(set(requirement_ids)) == 4
    assert [g["id"] for g in item["general"]][0] == "g-1" and item["general"][1]["id"] not in ("", "g-1")
    ids_back = env.client.patch(f"{API}/{item['id']}", json={"positions": item["positions"], "general": item["general"]}).json()
    assert ids_back["positions"] == item["positions"] and ids_back["general"] == item["general"]             # kept ids round-trip
    assert make(env, positions=[{"id": "x" * 40, "title": "A"}])["positions"][0]["id"] == "x" * 40
    act_as(env, "rec")
    for payload in ({"positions": [{"id": "x" * 41, "title": "A"}]}, {"general": [{"id": "x" * 41, "label": "A"}]},
                    {"positions": [{"title": "A", "requirements": [{"id": "x" * 41, "field": "A"}]}]}):
        assert env.client.post(API, json=payload).status_code == 422, payload


def test_completely_blank_rows_are_dropped(env):
    item = make(env, positions=[{}, {"title": "  ", "vacancies": "", "salary": None, "requirements": [{}, {"field": " ", "details": ""}]},
                                {"title": "", "requirements": [{"field": "Age", "details": "Up to 30"}]}, {"title": "Cook", "requirements": [{}, {"details": "only details"}]}],
                general=[{}, {"label": "", "details": " ", "remarks": None}, {"remarks": "only a remark"}])
    assert [p["title"] for p in item["positions"]] == ["", "Cook"]                                           # a position with no title but a requirement row stays
    assert [(r["field"], r["details"]) for r in item["positions"][0]["requirements"]] == [("Age", "Up to 30")]
    assert [(r["field"], r["details"]) for r in item["positions"][1]["requirements"]] == [("", "only details")]
    assert [g["remarks"] for g in item["general"]] == ["only a remark"]
    cleaned = env.client.patch(f"{API}/{item['id']}", json={"positions": [{}, {"title": ""}], "general": [{"label": " "}]}).json()
    assert cleaned["positions"] == [] and cleaned["general"] == []


def test_list_limits_and_extra_keys_are_422(env):
    item = make(env)
    url = f"{API}/{item['id']}"
    position = {"title": "A"}
    bad = [{"positions": [position] * 31}, {"positions": [{"title": "A", "requirements": [{"field": "f"}] * 41}]}, {"general": [{"label": "g"}] * 51},
           {"positions": [{"title": "x" * 201}]}, {"positions": [{"vacancies": "x" * 101}]}, {"positions": [{"salary": "x" * 201}]},
           {"positions": [{"requirements": [{"field": "x" * 101}]}]}, {"positions": [{"requirements": [{"details": "x" * 2001}]}]},
           {"general": [{"label": "x" * 101}]}, {"general": [{"details": "x" * 2001}]}, {"general": [{"remarks": "x" * 2001}]},
           {"positions": [{"title": "A", "note": "x"}]}, {"positions": [{"title": "A", "requirements": [{"field": "f", "extra": 1}]}]},
           {"general": [{"label": "A", "extra": 1}]}, {"positions": {"title": "A"}}, {"positions": ["A"]}, {"general": "Food"}, {"positions": [{"title": ["A"]}]}]
    for payload in bad:
        assert env.client.patch(url, json=payload).status_code == 422, payload
        assert env.client.post(API, json=payload).status_code == 422, payload
    exact = {"positions": [{"title": "t" * 200, "vacancies": "v" * 100, "salary": "s" * 200, "requirements": [{"field": "f" * 100, "details": "d" * 2000}] * 40}] * 30,
             "general": [{"label": "l" * 100, "details": "d" * 2000, "remarks": "r" * 2000}] * 50}
    done = env.client.patch(url, json=exact)
    assert done.status_code == 200, done.text
    assert len(done.json()["positions"]) == 30 and len(done.json()["positions"][0]["requirements"]) == 40 and len(done.json()["general"]) == 50
    with env.factory() as db:                                                                                # nothing from the refused requests was stored
        assert db.scalar(select(func.count()).select_from(JobRequirement)) == 1


def test_search_matches_position_titles_and_requirement_text(env):
    chef = make(env, clientName="Zed Co", positions=[{"title": "Paratha Master"}, {"title": "Captain", "requirements": [{"field": "Qualification", "details": "Hotel management diploma"}]}])
    other = make(env, clientName="Beta", details={"position": "Driver"}, positions=[{"title": "Welder", "requirements": [{"field": "Maximum Age", "details": "Up to 100% fit"}]}])
    blank = make(env, clientName="Gamma")
    act_as(env, "rec2")

    def found(term):
        body = env.client.get(API, params={"q": term}).json()
        assert body["total"] == len(body["items"]) and body["summary"]["active"] >= body["total"]
        return {i["id"] for i in body["items"]}
    assert found("paratha") == {chef["id"]} and found("CAPTAIN") == {chef["id"]} and found("welder") == {other["id"]}
    assert found("master") == {chef["id"]} and found("qualification") == {chef["id"]} and found("diploma") == {chef["id"]}   # requirement field and details
    assert found("maximum age") == {other["id"]} and found("driver") == {other["id"]}                      # the legacy keys still match
    assert found("100%") == {other["id"]} and found("100\\") == set() and found("pa_atha") == set()         # LIKE wildcards stay literal
    assert found("title") == set() and found("requirements") == set()                                       # JSON key names are not searchable text
    assert found("gamma") == {blank["id"]} and found("nothing-here") == set()


def test_history_names_the_two_lists(env):
    item = make(env, clientName="Al Noor")
    url = f"{API}/{item['id']}"
    act_as(env, "rec2")
    assert env.client.patch(url, json={"positions": [{"title": "Cook"}]}).status_code == 200
    assert env.client.patch(url, json={"general": [{"label": "Food"}], "country": "Qatar"}).status_code == 200
    assert env.client.patch(url, json={"positions": [{"title": "Cook"}], "general": []}).status_code == 200   # new ids, same text: positions changed again
    items = env.client.get(f"{url}/history").json()["items"]
    summaries = [i["summary"] for i in items]
    assert summaries == ["Changed Job positions, General employment details", "Changed Country, General employment details", "Changed Job positions",
                         "Added the requirement"]
    with env.factory() as db:
        meta = [e.meta for e in db.scalars(select(AuditEntry).where(AuditEntry.entity_id == item["id"], AuditEntry.action == "Edited job requirement").order_by(AuditEntry.at, AuditEntry.id))]
    assert meta == [{"changed": ["positions"]}, {"changed": ["country", "general"]}, {"changed": ["positions", "general"]}]


def test_export_has_job_positions_and_general_columns(env):
    import csv
    import io
    make(env, clientName="Al Noor", country="Abu Dhabi", positions=POSITIONS, general=GENERAL, details={"position": "Ignored legacy", "basicSalary": "1"})
    make(env, clientName="Legacy Co", details={"position": "Welder", "vacancies": "5", "basicSalary": "AED 900"})
    make(env, clientName="Salary only", details={"basicSalary": "AED 800"})
    make(env, clientName="Formula", positions=[{"title": "=SUM(A1)", "vacancies": "1"}], general=[{"label": "-neg", "details": "x"}])
    make(env, clientName="None Co")
    act_as(env, "rec2")
    rows = list(csv.reader(io.StringIO(env.client.get(f"{API}/export", params={"format": "csv"}).content.decode("utf-8-sig"))))
    header = rows[0]
    assert header[:5] == ["S.No", "DOC", "Client name", "Country", "Job positions"] and header[5] == "Name of the Position"
    general_at = header.index("General employment details")
    assert header[general_at - 1] == "Remarks / Special Conditions" and header[general_at + 1] == "Deadline"
    by_client = {row[2]: row for row in rows[1:]}
    assert by_client["Al Noor"][4] == "Paratha Master (3) - AED 1500; Captain (To be confirmed) - To be confirmed; Waiter"
    assert by_client["Al Noor"][general_at] == "Working Hours: 12 hours; Weekly Off: 2 days per month (As provided by client); Visa Type: Visit cum Employment (Confirm visa arrangement before finalisation)"
    assert by_client["Al Noor"][5] == "Ignored legacy"                                                      # the legacy columns are all still there
    assert by_client["Legacy Co"][4] == "Welder (5) - AED 900" and by_client["Legacy Co"][general_at] == ""
    assert by_client["Salary only"][4] == "'- AED 800"                                                      # no title: the guard still applies
    assert by_client["Formula"][4] == "'=SUM(A1) (1)" and by_client["Formula"][general_at] == "'-neg: x"    # the formula guard covers the new cells
    assert by_client["None Co"][4] == "" and by_client["None Co"][general_at] == ""
    assert len(set(len(row) for row in rows)) == 1
    workbook = env.client.get(f"{API}/export", params={"format": "xlsx"})
    cells, _ = read_xlsx(workbook.content)
    assert cells["E1"] == "Job positions" and any(value.startswith("Paratha Master (3) - AED 1500") for value in cells.values())


def test_form_download_is_the_client_layout(env):
    item = make(env, clientName="Al Noor / Zed", country="Abu Dhabi, UAE", positions=POSITIONS, general=GENERAL, details={"remarks": "Vacancies and salary details are pending confirmation."})
    env.client.cookies.clear()
    assert env.client.get(f"{API}/{item['id']}/form").status_code == 401
    for who in ("rec", "rec2", "md", "admin"):                                                               # any signed-in role
        act_as(env, who)
        assert env.client.get(f"{API}/{item['id']}/form").status_code == 200, who
    response = env.client.get(f"{API}/{item['id']}/form")
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="job-requirement-al-noor-zed.xlsx"'
    assert response.content[:2] == b"PK"
    cells, merges = read_xlsx(response.content)
    rows = sheet_rows(cells)
    assert rows[0][0] == "JOB REQUIREMENT FORM" and rows[1][0] == "Client / Restaurant: Al Noor / Zed     Country: Abu Dhabi, UAE"
    assert rows[3] == ["1. JOB POSITIONS", "", "", ""] and rows[4] == ["S.No", "Job Title", "No. of Vacancies", "Salary"]
    assert rows[5:8] == [["1", "Paratha Master", "3", "AED 1500"], ["2", "Captain", "To be confirmed", "To be confirmed"], ["3", "Waiter", "", ""]]   # empty cells stay empty
    assert rows[9] == ["2. JOB REQUIREMENTS DETAILS", "", "", ""] and rows[10] == ["S.No", "Job Title", "Requirement Field", "Details"]
    assert rows[11:16] == [["1", "Paratha Master", "Practical Skill / Video", "Working video required for dosa and paratha preparation."],
                           ["2", "Captain", "Qualification", "Hotel management qualification."], ["2", "Captain", "Maximum Age", "Up to 30 years."],
                           ["2", "Captain", "Experience", "Minimum 5 years."], ["3", "Waiter", "", ""]]                  # a position without rows still has a line
    assert rows[17] == ["3. GENERAL EMPLOYMENT DETAILS", "", "", ""] and rows[18] == ["S.No", "Particulars", "Details", "Remarks"]
    assert rows[19:22] == [["1", "Working Hours", "12 hours", ""], ["2", "Weekly Off", "2 days per month", "As provided by client"],
                           ["3", "Visa Type", "Visit cum Employment", "Confirm visa arrangement before finalisation"]]
    assert rows[-1][0] == "Client Confirmation: Vacancies and salary details are pending confirmation." and len(rows) == 25
    assert {"A1:D1", "A2:D2", "A4:D4", "A10:D10", "A18:D18", "A25:D25"} == set(merges)


def test_form_for_legacy_rows_blank_rows_and_the_formula_guard(env):
    legacy = make(env, clientName="Legacy Co", country="Qatar", details={"position": "Welder", "vacancies": "5", "basicSalary": "AED 900", "qualification": "ITI",
                                                                          "agePreference": "25-35", "additionalSkills": "Arc welding", "placeOfWork": "Doha",
                                                                          "contractPeriod": "2 years", "workingHours": "10", "overtime": "Paid", "otCalculation": "1.25x",
                                                                          "food": "Provided", "accommodation": "Provided", "insurance": "Yes", "transport": "Yes",
                                                                          "remarks": "Client confirmation: pending salary", "deadline": "2026-12-01"})
    act_as(env, "rec2")
    response = env.client.get(f"{API}/{legacy['id']}/form")
    assert response.headers["content-disposition"] == 'attachment; filename="job-requirement-legacy-co.xlsx"'
    rows = sheet_rows(read_xlsx(response.content)[0])
    assert rows[5] == ["1", "Welder", "5", "AED 900"]
    assert rows[9:12] == [["1", "Welder", "Qualification", "ITI"], ["1", "Welder", "Age Preference", "25-35"], ["1", "Welder", "Additional Skills", "Arc welding"]]
    labels = [row[1] for row in rows[15:24]]
    assert labels == ["Place of Work", "Period of Contract", "Working Hours", "Overtime", "OT Calculation", "Food", "Accommodation", "Insurance", "Transport"]
    assert rows[15][2] == "Doha" and rows[-1][0] == "Client confirmation: pending salary"                      # not prefixed twice
    blank = make(env)
    rows = sheet_rows(read_xlsx(env.client.get(f"{API}/{blank['id']}/form").content)[0])
    assert env.client.get(f"{API}/{blank['id']}/form").headers["content-disposition"] == 'attachment; filename="job-requirement-requirement.xlsx"'
    assert rows[0][0] == "JOB REQUIREMENT FORM" and rows[1][0].startswith("Client / Restaurant:") and [r[0] for r in rows if r[0][:1].isdigit()] == ["1. JOB POSITIONS", "2. JOB REQUIREMENTS DETAILS", "3. GENERAL EMPLOYMENT DETAILS"]
    assert not any("Client Confirmation" in cell for row in rows for cell in row)                            # no note, no line
    hostile = make(env, clientName="=HYPERLINK(\"x\")", positions=[{"title": "=cmd|' /C calc'!A0", "vacancies": "+1", "salary": "@x",
                                                                      "requirements": [{"field": "-f", "details": "\tx"}]}], general=[{"label": "=g", "details": "-d", "remarks": "@r"}])
    cells, _ = read_xlsx(env.client.get(f"{API}/{hostile['id']}/form").content)
    values = [value for value in cells.values() if value[:1] in ("=", "+", "-", "@", "\t", "\r")]
    assert values == []                                                                                     # every risky start is neutralised
    assert "'=cmd|' /C calc'!A0" in cells.values() and "'=g" in cells.values()
    assert env.client.get(f"{API}/missing/form").status_code == 404


def test_form_opens_in_a_real_spreadsheet_library(env):
    openpyxl = pytest.importorskip("openpyxl")
    import io
    item = make(env, clientName="Al Noor", country="UAE", positions=POSITIONS, general=GENERAL, details={"remarks": "Pending."})
    workbook = openpyxl.load_workbook(io.BytesIO(env.client.get(f"{API}/{item['id']}/form").content))
    sheet = workbook["Job Requirement Form"]
    assert sheet["A1"].value == "JOB REQUIREMENT FORM" and sheet["A1"].font.b and sheet["B6"].value == "Paratha Master" and sheet["A6"].value == 1
    assert sheet["A5"].font.b and sheet["A4"].font.b and sheet["B12"].border.left.style == "thin"
    assert {str(r) for r in sheet.merged_cells.ranges} >= {"A1:D1", "A4:D4"}
    assert sheet.column_dimensions["D"].width == 48


# ------------------------------------------------------------------ reminders and the migration

def test_reminders_name_the_positions_and_fall_back_to_the_legacy_one(env):
    make(env, clientName="Many Co", country="Qatar", details={"position": "Legacy name", "deadline": on_day(0)},
         positions=[{"title": ""}, {"title": "Paratha Master"}, {"title": "Captain"}, {"title": "Waiter"}, {"title": "Cook"}])
    make(env, clientName="Old Co", country="Oman", details={"position": "Welder", "deadline": on_day(0)})
    make(env, clientName="Untitled Co", details={"position": "Fallback", "deadline": on_day(0)}, positions=[{"requirements": [{"field": "Age"}]}])
    make(env, clientName="Empty Co", details={"deadline": on_day(0)})
    assert run_reminders(env, datetime.now(timezone.utc))["owners"] == 4
    details = [detail for _, detail, link in notices(env, "rec") if link == "/job-requirements"]
    assert any(d.startswith("Many Co · Paratha Master, Captain, Waiter · Qatar · deadline") for d in details)
    assert not any("Cook" in d or "Legacy name" in d for d in details)
    assert any(d.startswith("Old Co · Welder · Oman") for d in details) and any(d.startswith("Untitled Co · Fallback · deadline") for d in details)
    assert any(d.startswith("Empty Co · deadline") for d in details)


def test_migration_0061_adds_lists_owners_and_options_and_is_idempotent(schema_database):  # noqa: F811
    engine = schema_database
    _migrate(engine, "0060_application_closed_at")
    assert not {"positions", "general", "owner_ids"} & {c["name"] for c in inspect(engine).get_columns("crm_job_requirements")}
    assert "crm_job_requirement_options" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO auth_users (id, name, email, role, active, receives_auto_assign, on_leave) VALUES ('u1', 'U One', 'u1@example.test', 'staff', true, false, false)"))
        connection.execute(text("INSERT INTO crm_job_requirements (id, details, owner_id) VALUES ('old', '{\"position\": \"Welder\"}', 'u1')"))
        connection.execute(text("INSERT INTO crm_job_requirements (id) VALUES ('nobody')"))
        connection.execute(text("ALTER TABLE crm_job_requirements ADD COLUMN positions jsonb NOT NULL DEFAULT '[]'::jsonb"))   # a database built from the models already has one
    _migrate(engine, "0061_job_requirement_positions")
    catalog = inspect(engine)
    columns = {c["name"]: c for c in catalog.get_columns("crm_job_requirements")}
    assert {"positions", "general", "owner_ids"} <= set(columns) and columns["general"]["nullable"] is False and columns["general"]["comment"] and columns["owner_ids"]["comment"]
    assert set(columns) == set(JobRequirement.__table__.columns.keys())
    assert {c["name"] for c in catalog.get_columns("crm_job_requirement_options")} == set(JobRequirementOption.__table__.columns.keys())
    assert "uq_crm_job_requirement_options_kind_label" in {i["name"] for i in catalog.get_indexes("crm_job_requirement_options")}
    assert catalog.get_table_comment("crm_job_requirement_options")["text"]
    with engine.begin() as connection:
        row = connection.execute(text("SELECT positions, general, details, owner_ids FROM crm_job_requirements WHERE id = 'old'")).one()
        assert (row.positions, row.general, row.details, row.owner_ids) == ([], [], {"position": "Welder"}, ["u1"])   # data kept; owners back-filled from owner_id
        assert connection.scalar(text("SELECT owner_ids FROM crm_job_requirements WHERE id = 'nobody'")) == []
        assert connection.scalar(text("SELECT col_description('crm_job_requirements'::regclass, attnum) FROM pg_attribute WHERE attrelid = 'crm_job_requirements'::regclass AND attname = 'general'"))
        connection.execute(text("INSERT INTO crm_job_requirement_options (id, kind, label) VALUES ('o1', 'particular', 'Food')"))
        assert connection.scalar(text("SELECT standard FROM crm_job_requirement_options WHERE id = 'o1'")) is False
    from sqlalchemy.exc import IntegrityError
    for statement in ("INSERT INTO crm_job_requirement_options (id, kind, label) VALUES ('o2', 'particular', 'FOOD')",
                      "INSERT INTO crm_job_requirement_options (id, kind, label) VALUES ('o3', 'color', 'Red')"):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(statement))
    with engine.begin() as connection:   # the same name under the other kind is fine
        connection.execute(text("INSERT INTO crm_job_requirement_options (id, kind, label) VALUES ('o4', 'requirement', 'food')"))
    _migrate(engine, "head")   # idempotent: nothing to add, nothing lost
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM crm_job_requirement_options")) == 2
    _migrate(engine, "0060_application_closed_at", downgrade=True)
    assert not {"positions", "general", "owner_ids"} & {c["name"] for c in inspect(engine).get_columns("crm_job_requirements")}
    assert "crm_job_requirement_options" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT owner_id FROM crm_job_requirements WHERE id = 'old'")) == "u1"   # the first recruiter survives
    _migrate(engine, "head")
    assert {"positions", "general", "owner_ids"} <= {c["name"] for c in inspect(engine).get_columns("crm_job_requirements")}


# ------------------------------------------------------------------ several recruiters per requirement

def test_owner_ids_create_defaults_and_legacy_owner_id(env):
    mine = make(env)                                                                                          # nothing sent: the creator
    assert (mine["ownerIds"], mine["owners"], mine["ownerId"], mine["ownerName"]) == (["rec"], [{"id": "rec", "name": "REC Person"}], "rec", "REC Person")
    by_admin = make(env, who="admin")                                                                         # an Admin is not offered as an owner
    assert (by_admin["ownerIds"], by_admin["owners"], by_admin["ownerId"], by_admin["ownerName"]) == ([], [], None, None)
    many = make(env, ownerIds=["rec2", "md", "rec2", " ", None, "rec"])                                       # deduplicated, blanks ignored, order kept
    assert many["ownerIds"] == ["rec2", "md", "rec"] and [o["name"] for o in many["owners"]] == ["REC2 Person", "MD Person", "REC Person"]
    assert (many["ownerId"], many["ownerName"]) == ("rec2", "REC2 Person")                                    # legacy fields = the first owner
    assert make(env, ownerIds=[])["ownerIds"] == [] and make(env, ownerId="")["ownerIds"] == []
    assert make(env, ownerId="md")["ownerIds"] == ["md"]
    both = make(env, ownerId="md", ownerIds=["rec2"])                                                         # ownerIds wins
    assert both["ownerIds"] == ["rec2"] and both["ownerId"] == "rec2"
    assert make(env, ownerId="md", ownerIds=None)["ownerIds"] == ["md"]
    with env.factory() as db:
        row = db.get(JobRequirement, many["id"])
        assert row.owner_ids == ["rec2", "md", "rec"] and row.owner_id == "rec2"                              # the old column mirrors the first owner
    act_as(env, "rec")
    with env.factory() as db:
        stored = db.scalar(select(func.count()).select_from(JobRequirement))
    assert env.client.post(API, json={"ownerIds": ["rec2", "admin"]}).status_code == 422                       # no admin among them
    assert env.client.post(API, json={"ownerIds": ["nobody"]}).status_code == 422
    assert env.client.post(API, json={"ownerIds": [f"rec{n}" for n in range(16)]}).status_code == 422          # more than 15
    assert env.client.post(API, json={"ownerIds": "rec"}).status_code == 422
    with env.factory() as db:
        assert db.scalar(select(func.count()).select_from(JobRequirement)) == stored                          # refused requests stored nothing


def test_owner_ids_patch_audit_history_and_stale_people(env):
    with env.factory() as db:
        db.add(User(id="gone", name="Gone Person", email="gone@example.test", role=Role.staff, active=False, phone="+919000000001",
                    receives_auto_assign=False, on_leave=False, password_hash=hash_password("initial-test-password")))
        db.commit()
    item = make(env, ownerIds=["rec"])
    url = f"{API}/{item['id']}"
    act_as(env, "rec2")
    two = env.client.patch(url, json={"ownerIds": ["rec", "md"]}).json()
    assert two["ownerIds"] == ["rec", "md"] and two["ownerId"] == "rec"
    assert env.client.patch(url, json={"ownerIds": ["rec", "md"]}).status_code == 200                         # unchanged: no second entry
    assert env.client.patch(url, json={"ownerIds": ["rec", "gone"]}).status_code == 422                        # a disabled account cannot be added
    assert env.client.patch(url, json={"ownerIds": ["admin"]}).status_code == 422
    reordered = env.client.patch(url, json={"ownerIds": ["md", "rec"]}).json()
    assert reordered["ownerIds"] == ["md", "rec"] and reordered["ownerId"] == "md" and reordered["ownerName"] == "MD Person"
    assert env.client.patch(url, json={"clientName": "Kept"}).json()["ownerIds"] == ["md", "rec"]            # not mentioned: untouched
    assert env.client.patch(url, json={"ownerId": "rec2"}).json()["ownerIds"] == ["rec2"]                    # the legacy key still replaces
    assert env.client.patch(url, json={"ownerId": "rec2", "ownerIds": ["rec", "md", "rec2"]}).json()["ownerIds"] == ["rec", "md", "rec2"]
    cleared = env.client.patch(url, json={"ownerIds": []}).json()
    assert (cleared["ownerIds"], cleared["owners"], cleared["ownerId"], cleared["ownerName"]) == ([], [], None, None)
    with env.factory() as db:
        row = db.get(JobRequirement, item["id"])
        assert (row.owner_ids, row.owner_id) == ([], None)
        row.owner_ids, row.owner_id = ["rec", "gone"], "rec"                                                  # a disabled owner already on it may stay
        db.commit()
    assert env.client.patch(url, json={"ownerIds": ["rec", "gone", "md"]}).json()["ownerIds"] == ["rec", "gone", "md"]
    with env.factory() as db:
        meta = [e.meta for e in db.scalars(select(AuditEntry).where(AuditEntry.entity_id == item["id"], AuditEntry.action == "Reassigned job requirement").order_by(AuditEntry.at, AuditEntry.id))]
        assert meta[0] == {"from": ["REC Person"], "to": ["REC Person", "MD Person"]} and meta[4] == {"from": ["REC Person", "MD Person", "REC2 Person"], "to": []}
        db.add(AuditEntry(actor_id="rec", action="Reassigned job requirement", target="x", entity_type="job_requirement", entity_id=item["id"], meta={"from": "Old One", "to": "New One"}))
        db.add(AuditEntry(actor_id="rec", action="Reassigned job requirement", target="x", entity_type="job_requirement", entity_id=item["id"], meta={"from": None, "to": None}))
        db.commit()
    summaries = [i["summary"] for i in env.client.get(f"{url}/history").json()["items"]]
    assert "Assigned to New One (was Old One)" in summaries and "Unassigned" in summaries                     # old single-name entries stay readable
    assert "Assigned to REC Person, MD Person (was REC Person)" in summaries
    assert "Unassigned (was REC Person, MD Person, REC2 Person)" in summaries
    with env.factory() as db:
        db.get(JobRequirement, item["id"]).owner_ids = ["rec", "ghost-id"]
        db.commit()
    shown = [i for i in env.client.get(API).json()["items"] if i["id"] == item["id"]][0]
    assert shown["ownerIds"] == ["rec", "ghost-id"] and shown["owners"] == [{"id": "rec", "name": "REC Person"}]   # an unknown id is skipped in `owners`


def test_owner_filter_and_summary_count_every_owner(env):
    a = make(env, clientName="A", ownerIds=["rec", "md"])
    b = make(env, clientName="B", ownerIds=["md"])
    c = make(env, clientName="C", ownerIds=["rec2", "rec"], status="closed")
    d = make(env, clientName="D", ownerIds=[])
    act_as(env, "rec2")

    def ids(**params):
        return {i["id"] for i in env.client.get(API, params=params).json()["items"]}
    assert ids(owner="rec") == {a["id"], c["id"]} and ids(owner="md") == {a["id"], b["id"]} and ids(owner="rec2") == {c["id"]}
    assert ids(owner="rec", status="active") == {a["id"]} and ids(owner="nobody") == set()
    summary = env.client.get(API).json()["summary"]
    assert summary["active"] == 3 and summary["closed"] == 1                                                  # requirements, not (requirement, owner) pairs
    assert summary["byOwner"] == [{"id": "md", "name": "MD Person", "active": 2}, {"id": "rec", "name": "REC Person", "active": 1},
                                  {"id": "rec2", "name": "REC2 Person", "active": 0}]                          # a requirement with two owners counts for both
    mine = env.client.get(API, params={"owner": "md"}).json()
    assert mine["total"] == 2 and d["id"] not in ids(owner="md")


def test_export_lists_every_owner(env):
    import csv
    import io
    make(env, clientName="Two", ownerIds=["rec2", "md"])
    make(env, clientName="None", ownerIds=[])
    act_as(env, "rec")
    rows = list(csv.reader(io.StringIO(env.client.get(f"{API}/export", params={"format": "csv"}).content.decode("utf-8-sig"))))
    column = rows[0].index("Assigned to")
    assigned = {row[2]: row[column] for row in rows[1:]}
    assert assigned == {"Two": "REC2 Person, MD Person", "None": ""}


def test_every_owner_gets_the_reminder_once_a_day(env):
    make(env, clientName="Shared Co", country="Qatar", ownerIds=["rec", "rec2", "md"], details={"deadline": on_day(0)})
    solo = make(env, who="rec2", clientName="Solo Co", details={"deadline": on_day(0)})
    now = datetime.now(timezone.utc)
    assert run_reminders(env, now) == {"owners": 4, "admins": 0}
    assert run_reminders(env, now) == {"owners": 0, "admins": 0}                                              # a rerun adds nothing
    for who in ("rec", "rec2", "md"):
        assert any(d.startswith("Shared Co") for _, d, _ in notices(env, who)), who
    assert len(notices(env, "rec2")) == 2 and len(notices(env, "rec")) == 1 and len(notices(env, "admin")) == 0
    act_as(env, "rec")
    env.client.patch(f"{API}/{solo['id']}", json={"ownerIds": ["rec2", "rec"]})                               # a second owner is added later the same day
    assert run_reminders(env, now) == {"owners": 1, "admins": 0} and len(notices(env, "rec")) == 2
    assert run_reminders(env, now + timedelta(days=1))["owners"] == 5                                         # next day: everybody again
    told = len(notices(env, "md"))
    with env.factory() as db:                                                                                 # one disabled owner is skipped, the others are told
        db.get(User, "md").active = False
        db.commit()
    assert run_reminders(env, now + timedelta(days=2))["owners"] == 4 and len(notices(env, "md")) == told


def test_deleting_a_user_moves_them_to_former_user_in_the_owner_list(env):
    from app.services import user_admin
    item = make(env, ownerIds=["rec2", "rec"])
    other = make(env, ownerIds=["rec", "md"])
    with env.factory() as db:
        user_admin.delete_user(db, db.get(User, "admin"), "rec2")
    with env.factory() as db:
        former = db.scalar(select(User.id).where(User.name == user_admin.FORMER_USER_NAME))
        assert former
        row, untouched = db.get(JobRequirement, item["id"]), db.get(JobRequirement, other["id"])
        assert row.owner_ids == [former, "rec"] and row.owner_id == former                                    # the same rule as owner_id: "Former user"
        assert untouched.owner_ids == ["rec", "md"]
    act_as(env, "rec")
    shown = {i["id"]: i for i in env.client.get(API).json()["items"]}
    assert [o["name"] for o in shown[item["id"]]["owners"]] == ["Former user", "REC Person"] and shown[item["id"]]["ownerName"] == "Former user"


# ------------------------------------------------------------------ picker names (creatable fields)

OPTIONS = "/api/job-requirement-options"


def add_option(env, kind, label, who="rec", **extra):
    act_as(env, who)
    response = env.client.post(OPTIONS, json={"kind": kind, "label": label, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_options_create_list_rename_and_toggle(env):
    assert env.client.post(OPTIONS, json={"kind": "particular", "label": "Food"}).status_code == 401
    act_as(env, "rec")
    assert env.client.get(API).json()["options"] == []
    food = add_option(env, "particular", "  Working   Hours ", standard=True)
    assert food == {"id": food["id"], "kind": "particular", "label": "Working Hours", "standard": True, "sort": 0}
    visa = add_option(env, "particular", "Visa Type", who="rec2")
    age = add_option(env, "requirement", "Maximum Age")
    skill = add_option(env, "requirement", "Practical Skill / Video", standard=True)
    assert visa["standard"] is False and visa["sort"] == 1 and age["sort"] == 0 and skill["sort"] == 1
    assert skill["standard"] is False                                                                         # only particulars can be defaults
    listed = env.client.get(API).json()["options"]
    assert [(o["kind"], o["label"]) for o in listed] == [("particular", "Working Hours"), ("particular", "Visa Type"), ("requirement", "Maximum Age"), ("requirement", "Practical Skill / Video")]
    assert env.client.post(OPTIONS, json={"kind": "particular", "label": "WORKING hours"}).status_code == 409   # any letter case
    assert add_option(env, "requirement", "working hours")["kind"] == "requirement"                           # the other kind may reuse a name
    for payload in ({"kind": "color", "label": "Red"}, {"kind": "particular", "label": ""}, {"kind": "particular", "label": "x" * 101}, {"kind": "particular"},
                    {"label": "No kind"}, {"kind": "particular", "label": "Ok", "extra": 1}, {"kind": "particular", "label": "Ok", "standard": "maybe"}):
        assert env.client.post(OPTIONS, json=payload).status_code == 422, payload
    assert add_option(env, "particular", "x" * 100)["label"] == "x" * 100
    renamed = env.client.patch(f"{OPTIONS}/{visa['id']}", json={"label": "Visa"}).json()
    assert renamed["label"] == "Visa" and renamed["standard"] is False
    assert env.client.patch(f"{OPTIONS}/{visa['id']}", json={"label": "working HOURS"}).status_code == 409
    assert env.client.patch(f"{OPTIONS}/{visa['id']}", json={"label": "VISA"}).status_code == 200            # own name, other case
    toggled = env.client.patch(f"{OPTIONS}/{visa['id']}", json={"standard": True}).json()
    assert toggled["standard"] is True and toggled["label"] == "VISA"
    assert env.client.patch(f"{OPTIONS}/{age['id']}", json={"standard": True}).json()["standard"] is False   # no meaning for a requirement field
    assert env.client.patch(f"{OPTIONS}/{visa['id']}", json={"nope": 1}).status_code == 422
    assert env.client.patch(f"{OPTIONS}/missing", json={"label": "x"}).status_code == 404
    actions = [row.action for row in _option_audit(env)]
    assert actions.count("Added job requirement option") == 6 and "Renamed job requirement option" in actions and "Changed job requirement option default" in actions


def _option_audit(env):
    with env.factory() as db:
        return list(db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "job_requirement_option").order_by(AuditEntry.at, AuditEntry.id)))


def test_option_delete_is_admin_level_and_saved_rows_keep_their_text(env):
    age = add_option(env, "requirement", "Maximum Age")
    item = make(env, positions=[{"title": "Captain", "requirements": [{"field": "Maximum Age", "details": "30"}]}])
    act_as(env, "rec")
    assert env.client.delete(f"{OPTIONS}/{age['id']}").status_code == 403
    act_as(env, "md")
    assert env.client.delete(f"{OPTIONS}/{age['id']}").status_code == 204
    assert env.client.delete(f"{OPTIONS}/{age['id']}").status_code == 404
    assert env.client.get(API).json()["options"] == []
    assert env.client.get(API).json()["items"][0]["positions"][0]["requirements"][0]["field"] == "Maximum Age" and item["id"]
    assert "Deleted job requirement option" in [row.action for row in _option_audit(env)]
    env.client.cookies.clear()
    assert env.client.delete(f"{OPTIONS}/{age['id']}").status_code == 401


def test_remember_new_fields_adds_only_unknown_names(env):
    add_option(env, "requirement", "Qualification")
    add_option(env, "particular", "Working Hours")
    body = {"positions": [{"title": "Captain", "requirements": [{"field": "qualification", "details": "x"}, {"field": "Shift Pattern", "details": "30"},
                                                                {"field": "SHIFT PATTERN", "details": "again"}, {"field": "", "details": "no name"}]},
                          {"title": "Waiter", "requirements": [{"field": "Height"}]}],
            "general": [{"label": "WORKING HOURS", "details": "12"}, {"label": "Tea Break"}, {"label": "Laundry", "remarks": "r"}]}
    act_as(env, "rec")
    plain = env.client.post(API, json=body)                                                                    # no flag: the catalog stays as it is
    assert plain.status_code == 201 and len(env.client.get(API).json()["options"]) == 2
    flagged = env.client.post(API, json={**body, "rememberNewFields": True})
    assert flagged.status_code == 201 and "rememberNewFields" not in flagged.json()
    options = env.client.get(API).json()["options"]
    assert [(o["kind"], o["label"], o["standard"]) for o in options] == [("particular", "Working Hours", False), ("particular", "Tea Break", False), ("particular", "Laundry", False),
                                                                          ("requirement", "Qualification", False), ("requirement", "Shift Pattern", False), ("requirement", "Height", False)]
    assert [o["sort"] for o in options] == [0, 1, 2, 0, 1, 2]
    again = env.client.post(API, json={**body, "rememberNewFields": True})                                     # nothing new the second time
    assert again.status_code == 201 and len(env.client.get(API).json()["options"]) == 6
    with env.factory() as db:                                                                                 # the flag is never stored
        row = db.get(JobRequirement, flagged.json()["id"])
        assert row.positions[0]["requirements"][0]["field"] == "qualification" and not hasattr(row, "rememberNewFields")
    item = flagged.json()
    patched = env.client.patch(f"{API}/{item['id']}", json={"rememberNewFields": True, "positions": [{"title": "Cook", "requirements": [{"field": "Tool Kit"}]}],
                                                            "general": [{"label": "Bonus Plan"}]})
    assert patched.status_code == 200
    labels = {(o["kind"], o["label"]) for o in env.client.get(API).json()["options"]}
    assert ("requirement", "Tool Kit") in labels and ("particular", "Bonus Plan") in labels
    before = len(env.client.get(API).json()["options"])
    env.client.patch(f"{API}/{item['id']}", json={"rememberNewFields": True, "clientName": "Only the name"})   # no lists sent: nothing to remember
    env.client.patch(f"{API}/{item['id']}", json={"positions": [{"title": "Cook", "requirements": [{"field": "Brand New"}]}]})   # no flag
    assert len(env.client.get(API).json()["options"]) == before
    builtin = env.client.post(API, json={"rememberNewFields": True, "positions": [{"title": "Cook", "requirements": [{"field": "Language"}]}], "general": [{"label": "Overtime"}]})
    assert builtin.status_code == 201 and len(env.client.get(API).json()["options"]) == before   # the form's own names are never copied into the catalog
    assert env.client.post(API, json={"rememberNewFields": "yes please"}).status_code == 422
    actions = [row.action for row in _option_audit(env)]
    assert actions.count("Added job requirement option") == 2 + 4 + 2                                          # the two typed by hand, then the ones remembered
