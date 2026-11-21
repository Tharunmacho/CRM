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
from app.db.models import AuditEntry, Base, JobRequirement, JobRequirementField, Role, Session as LoginSession, User
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
    assert {c["name"] for c in catalog.get_columns("crm_job_requirements")} == set(JobRequirement.__table__.columns.keys())
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
    assert not {"crm_job_requirements", "crm_job_requirement_fields"} & set(inspect(engine).get_table_names())
    _migrate(engine, "head")
    assert {"crm_job_requirements", "crm_job_requirement_fields"} <= set(inspect(engine).get_table_names())


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
    assert rows[0] == ["S.No", "DOC", "Client name", "Country"] + ["Name of the Position", "No. of Requirements", "Place of Work", "Period of Contract", "Qualification", "Age Preference", "Additional Skills", "Basic Salary", "Working Hours", "Overtime", "OT Calculation", "Food", "Accommodation", "Insurance", "Transport", "Remarks / Special Conditions"] + [
        "Deadline", "Assigned to", "Status", "Closed reason", "Closed on", "Added by", "Last updated", "Visa type"]
    assert len(rows) == 3
    assert rows[1][:3] == ["1", "2026-11-01", "Beta"] and rows[1][21] == "REC2 Person" and rows[1][22] == "Active"   # newest DOC first
    older = rows[2]
    assert older[0] == "2" and older[2].startswith("'=")                                                                      # no formula injection
    assert older[4:7] == ["Welder", "5", "Doha"] and older[19] == "Need welders" and older[20] == "2026-10-30" and older[21] == "REC Person"
    assert older[22:24] == ["Closed", "Filled"] and older[24] == today_in_agency().isoformat() and older[25] == "REC Person" and older[27] == "Employment"
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
