"""Admin-managed form option lists (migration 0022): seeds, migration, API, role rules, validation and live feed."""
import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app.api.routers import application_form, candidates, form_options
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import change_feed, form_options as service
from test_candidate_workflows import db
from test_database_naming import BACKEND, _migrate, schema_database


def client_for(db, who):
    app = FastAPI()
    for module in (candidates, form_options, application_form):
        app.include_router(module.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: db.get(m.User, who)
    return TestClient(app)


def find(payload, category):
    return next(item for item in payload["categories"] if item["key"] == category)


# ------------------------------------------------------------------ seeds and migration

def test_migration_seeds_equal_the_service_defaults_and_every_category_is_registered():
    path = BACKEND / "alembic" / "versions" / "0022_form_options.py"
    spec = importlib.util.spec_from_file_location("form_options_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migrated = [{"category": category, "value": value, "label": label, "hint": hint, "tone": tone, "sort_order": position * 10}
                for category, rows in module.SEEDS for position, (value, label, hint, tone) in enumerate(rows, start=1)]
    # 0022 is an applied migration and keeps the label it seeded; the default was reworded later (the UI maps the legacy label).
    renamed = {("candidate_source", "referral"): ("Referral / agent", "Referral / agency")}
    migrated = [{**row, "label": renamed[(row["category"], row["value"])][1]} if (row["category"], row["value"]) in renamed
                and row["label"] == renamed[(row["category"], row["value"])][0] else row for row in migrated]
    assert migrated == service.DEFAULT_OPTIONS
    assert {row["category"] for row in service.DEFAULT_OPTIONS} == set(service.CATEGORIES)
    assert {row["value"] for row in service.DEFAULT_OPTIONS if row["category"] == "candidate_source"} == {s.value for s in m.Source}


def test_migration_adds_the_table_seeds_it_and_is_reversible(schema_database):
    engine = schema_database
    _migrate(engine, "0021_application_form")
    assert "crm_form_options" not in inspect(engine).get_table_names()
    checks = "SELECT conname, convalidated FROM pg_constraint WHERE conrelid='crm_candidates'::regclass AND conname LIKE 'ck_candidates_%' ORDER BY 1"
    with engine.begin() as connection:
        assert [row[0] for row in connection.execute(text(checks))] == ["ck_candidates_interest_level", "ck_candidates_service_charges"]
        connection.execute(text("INSERT INTO crm_candidates (id,ref,name,phone,source,languages,designations,skills,total_experience,gulf_experience,status,needs_review,service_charges_status) "
                                "VALUES ('c1','CND-1','C','9000000001','walk_in','[]','[]','[]',0,0,'new',false,'agreed')"))
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.execute(text(checks)).all() == []  # the lists are rows now, not constraints
        rows = connection.execute(text("SELECT category, value, label, hint, tone, sort_order FROM crm_form_options ORDER BY category, sort_order")).all()
        assert len(rows) == len(service.DEFAULT_OPTIONS)
        assert connection.scalar(text("SELECT count(*) FROM crm_form_options WHERE is_system AND is_active")) == len(rows)
        assert connection.scalar(text("SELECT col_description('crm_form_options'::regclass, 0)")) is not None
    with engine.begin() as connection:
        connection.execute(text("UPDATE crm_form_options SET label='Edited by admin' WHERE category='interest_level' AND value='interested'"))
        connection.execute(text("INSERT INTO crm_candidates (id,ref,name,phone,source,languages,designations,skills,total_experience,gulf_experience,status,needs_review,service_charges_status) "
                                "VALUES ('c2','CND-2','D','9000000002','walk_in','[]','[]','[]',0,0,'new',false,'waived')"))  # a value an admin added
        connection.execute(text("UPDATE alembic_version SET version_num='0021_application_form'"))
    _migrate(engine, "head")  # a repeat is harmless and keeps the admin's edit
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT label FROM crm_form_options WHERE category='interest_level' AND value='interested'")) == "Edited by admin"
        assert connection.scalar(text("SELECT count(*) FROM crm_form_options")) == len(service.DEFAULT_OPTIONS)
    _migrate(engine, "0021_application_form", downgrade=True)
    assert "crm_form_options" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        restored = connection.execute(text(checks)).all()
        assert [(name, valid) for name, valid in restored] == [("ck_candidates_interest_level", False), ("ck_candidates_service_charges", False)]
        assert connection.scalar(text("SELECT service_charges_status FROM crm_candidates WHERE id='c2'")) == "waived"  # kept
    _migrate(engine, "head")
    assert "crm_form_options" in inspect(engine).get_table_names()


# ------------------------------------------------------------------ reading

def test_everyone_signed_in_reads_grouped_active_options_with_an_etag(db):
    staff = client_for(db, "a")
    response = staff.get("/api/form-options")
    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]
    data = response.json()
    assert [c["key"] for c in data["categories"]] == list(service.CATEGORIES)
    interest = find(data, "interest_level")
    assert interest["strict"] is True and interest["allowCustom"] is False and interest["fixedValues"] is False
    assert [o["value"] for o in interest["options"]] == ["more_interested", "interested", "somewhat_interested", "not_interested"]
    assert interest["options"][0]["tone"] == "success" and interest["options"][0]["isSystem"] is True
    assert find(data, "candidate_source")["fixedValues"] is True
    assert find(data, "gender")["allowCustom"] is True
    tag = response.headers["etag"]
    assert staff.get("/api/form-options", headers={"If-None-Match": tag}).status_code == 304
    assert staff.get("/api/form-options", headers={"If-None-Match": '"other"'}).status_code == 200
    assert staff.get("/api/form-options", params={"includeInactive": True}).status_code == 403
    assert client_for(db, "admin").get("/api/form-options", params={"includeInactive": True}).status_code == 200


# ------------------------------------------------------------------ administration

def test_only_an_admin_changes_the_lists(db):
    option = db.scalar(select(m.FormOption).where(m.FormOption.category == "interest_level", m.FormOption.value == "interested"))
    for who in ("a", "b"):
        api = client_for(db, who)
        assert api.post("/api/form-options", json={"category": "gender", "value": "x", "label": "X"}).status_code == 403
        assert api.put(f"/api/form-options/{option.id}", json={"label": "Hacked"}).status_code == 403
        assert api.delete(f"/api/form-options/{option.id}").status_code == 403
        assert api.post("/api/form-options/reorder", json={"category": "gender", "ids": [option.id]}).status_code == 403


def test_admin_creates_edits_reorders_hides_and_deletes_options(db):
    admin = client_for(db, "admin")
    created = admin.post("/api/form-options", json={"category": "service_charges_status", "value": "waived", "label": "Waived",
                                                    "hint": "Agency waived the charges", "tone": "info"})
    assert created.status_code == 201, created.text
    new = created.json()
    assert (new["value"], new["isSystem"], new["isActive"], new["sortOrder"]) == ("waived", False, True, 50)
    assert admin.post("/api/form-options", json={"category": "service_charges_status", "value": "waived", "label": "Again"}).status_code == 409
    assert admin.post("/api/form-options", json={"category": "nope", "value": "x", "label": "X"}).status_code == 422
    assert admin.post("/api/form-options", json={"category": "candidate_source", "value": "tv", "label": "TV"}).status_code == 422  # enum-backed
    assert admin.post("/api/form-options", json={"category": "gender", "value": "<b>", "label": "X"}).status_code == 422
    assert admin.post("/api/form-options", json={"category": "gender", "value": "x", "label": "X", "tone": "purple"}).status_code == 422

    changed = admin.put(f"/api/form-options/{new['id']}", json={"label": "Charges waived", "hint": None, "tone": "success", "sortOrder": 5})
    assert changed.status_code == 200 and (changed.json()["label"], changed.json()["hint"], changed.json()["tone"]) == ("Charges waived", None, "success")
    assert admin.put(f"/api/form-options/{new['id']}", json={"value": "renamed"}).status_code == 422  # the stored value never changes
    assert admin.put("/api/form-options/missing", json={"label": "x"}).status_code == 404

    listed = find(admin.get("/api/form-options").json(), "service_charges_status")["options"]
    assert [o["value"] for o in listed][0] == "waived"  # sortOrder 5 puts it first
    order = [o["id"] for o in reversed(listed)]
    reordered = admin.post("/api/form-options/reorder", json={"category": "service_charges_status", "ids": order})
    assert reordered.status_code == 200
    assert [o["id"] for o in find(reordered.json(), "service_charges_status")["options"]] == order
    assert admin.post("/api/form-options/reorder", json={"category": "service_charges_status", "ids": ["nope"]}).status_code == 422

    default = next(o for o in listed if o["value"] == "not_discussed")
    assert admin.put(f"/api/form-options/{default['id']}", json={"isActive": False}).status_code == 422
    assert admin.delete(f"/api/form-options/{default['id']}").status_code == 422

    hidden = admin.put(f"/api/form-options/{new['id']}", json={"isActive": False}).json()
    assert hidden["isActive"] is False
    assert "waived" not in [o["value"] for o in find(admin.get("/api/form-options").json(), "service_charges_status")["options"]]
    assert "waived" in [o["value"] for o in find(admin.get("/api/form-options", params={"includeInactive": True}).json(), "service_charges_status")["options"]]

    gone = admin.delete(f"/api/form-options/{new['id']}").json()
    assert gone["deleted"] is True and db.get(m.FormOption, new["id"]) is None
    audited = {row.action for row in db.scalars(select(m.AuditEntry).where(m.AuditEntry.entity_type == "form_option"))}
    assert {"Added a form option", "Changed a form option", "Reordered a form list", "Deleted a form option"} <= audited


def test_delete_hides_instead_when_the_option_is_in_use_or_came_with_the_product(db):
    admin = client_for(db, "admin")
    seeded = db.scalar(select(m.FormOption).where(m.FormOption.category == "gender", m.FormOption.value == "other"))
    result = admin.delete(f"/api/form-options/{seeded.id}").json()
    assert (result["deleted"], result["deactivated"]) == (False, True) and db.get(m.FormOption, seeded.id).is_active is False
    custom = admin.post("/api/form-options", json={"category": "best_time_to_contact", "value": "Night", "label": "Night"}).json()
    db.add(m.Candidate(id="used", ref="CND-U", name="Used", phone="9000000077", source=m.Source.walk_in, best_time_to_contact="Night"))
    db.commit()
    result = admin.delete(f"/api/form-options/{custom['id']}").json()
    assert (result["deleted"], result["deactivated"], result["inUse"]) == (False, True, 1)
    assert db.get(m.FormOption, custom["id"]) is not None


# ------------------------------------------------------------------ candidates accept only active values

def test_candidate_fields_take_only_active_options_but_keep_values_already_stored(db):
    admin = client_for(db, "admin")
    waived = admin.post("/api/form-options", json={"category": "service_charges_status", "value": "waived", "label": "Waived"}).json()
    body = {"name": "Choice Test", "phone": "9000000041"}
    assert admin.post("/api/candidates", json={**body, "serviceChargesStatus": "waived", "interestLevel": "interested"}).status_code == 201
    assert admin.post("/api/candidates", json={"name": "N", "phone": "9000000042", "serviceChargesStatus": "unknown"}).status_code == 422
    assert admin.post("/api/candidates", json={"name": "N", "phone": "9000000042", "interestLevel": "keen"}).status_code == 422
    stored = db.scalar(select(m.Candidate).where(m.Candidate.phone == "9000000041"))

    admin.put(f"/api/form-options/{waived['id']}", json={"isActive": False})
    interested = db.scalar(select(m.FormOption).where(m.FormOption.category == "interest_level", m.FormOption.value == "interested"))
    admin.put(f"/api/form-options/{interested.id}", json={"isActive": False})
    db.expire_all()
    # shown and kept: reading, and saving the unchanged value, still work
    form = admin.get(f"/api/candidates/{stored.id}/application-form").json()
    assert form["screening"]["serviceChargesStatus"] == "waived" and form["screening"]["interestLevel"] == "interested"
    kept = admin.put(f"/api/candidates/{stored.id}/application-form", json={"screening": {"serviceChargesStatus": "waived", "interestLevel": "interested"}})
    assert kept.status_code == 200, kept.text
    assert admin.patch(f"/api/candidates/{stored.id}", json={"serviceChargesStatus": "waived"}).status_code == 200


def test_a_hidden_option_cannot_be_chosen_for_another_candidate(db):
    admin = client_for(db, "admin")
    option = db.scalar(select(m.FormOption).where(m.FormOption.category == "interest_level", m.FormOption.value == "somewhat_interested"))
    other = admin.post("/api/candidates", json={"name": "Other", "phone": "9000000051"}).json()["id"]
    admin.put(f"/api/form-options/{option.id}", json={"isActive": False})
    assert admin.put(f"/api/candidates/{other}/application-form", json={"screening": {"interestLevel": "somewhat_interested"}}).status_code == 422
    assert admin.patch(f"/api/candidates/{other}", json={"interestLevel": "somewhat_interested"}).status_code == 422
    assert admin.post("/api/candidates", json={"name": "N", "phone": "9000000052", "interestLevel": "somewhat_interested"}).status_code == 422
    # free-text lists accept anything: they only suggest
    assert admin.put(f"/api/candidates/{other}/application-form", json={"personal": {"maritalStatus": "Engaged"}, "screening": {"bestTimeToContact": "After 9 pm"}}).status_code == 200


def test_a_hidden_source_cannot_be_chosen_for_a_new_candidate(db):
    admin = client_for(db, "admin")
    referral = db.scalar(select(m.FormOption).where(m.FormOption.category == "candidate_source", m.FormOption.value == "referral"))
    admin.put(f"/api/form-options/{referral.id}", json={"isActive": False})
    assert admin.post("/api/candidates", json={"name": "N", "phone": "9000000061", "source": "referral"}).status_code == 422
    assert admin.post("/api/candidates", json={"name": "N", "phone": "9000000062", "source": "email"}).status_code == 201
    assert admin.post("/api/candidates", json={"name": "N", "phone": "9000000063"}).status_code == 201  # the default needs no lookup


# ------------------------------------------------------------------ suggestions and live feed

def test_suggestion_lists_come_from_the_data(db):
    db.add_all([m.Client(id="cl", name="Contact"), m.Designation(id="de", title="Welder")])
    db.flush()
    db.add(m.Company(id="co", client_id="cl", name="Gulf Builders", country="UAE"))
    db.flush()
    db.add(m.Order(id="or", ref="O-1", company_id="co", designation_id="de", country="Qatar"))
    db.add_all([
        m.Candidate(id="s1", ref="S-1", name="A", phone="9000000081", source=m.Source.walk_in, skills=["Welding", "welding", "Rigging"],
                    country_preference="Qatar, Oman", education=[{"level": "ITI", "institute": "Govt ITI Kochi", "passedYear": 2012}],
                    work_history=[{"company": "ACME", "designation": "Welder"}]),
        m.Candidate(id="s2", ref="S-2", name="B", phone="9000000082", source=m.Source.walk_in, skills=["Welding"],
                    education=[{"level": "BE", "institute": "Govt ITI Kochi"}], work_history=[{"company": "ACME"}, {"company": "Gulf Cons"}])])
    db.commit()
    data = client_for(db, "a").get("/api/form-options/suggestions").json()
    assert {"UAE", "Qatar", "Oman"} <= set(data["countries"]) and data["countries"][0] == "Qatar"
    assert data["jobPreferences"] == ["Welder"]
    assert data["skills"][0] == "Welding" and "Rigging" in data["skills"] and len([s for s in data["skills"] if s.lower() == "welding"]) == 1
    assert data["institutes"] == ["Govt ITI Kochi"]
    assert data["companies"][0] == "ACME" and "Gulf Cons" in data["companies"]


def test_changes_to_the_lists_reach_open_screens_through_the_form_options_and_settings_topics():
    assert change_feed.TOPIC_TABLES["crm_form_options"] == ("form_options", "settings")
    pending = change_feed.Pending()
    pending.note_table("UPDATE", "crm_form_options")
    payload = __import__("json").loads(pending.payload(now_ms=1))
    assert payload["t"] == ["form_options", "settings"]
    for role in ("staff", "manager", "admin"):  # every signed-in user's open form hears it, with topic names only for staff
        assert change_feed.visible_to(payload, role, "someone")["topics"] == ["form_options", "settings"]
    assert change_feed.classify_statement("INSERT INTO crm_form_options (id, category) VALUES (%(id)s, %(c)s)") == ("INSERT", "crm_form_options")
    assert change_feed.classify_statement("UPDATE crm_form_options SET label=%(l)s, updated_at=now() WHERE id=%(id)s") == ("UPDATE", "crm_form_options")
    assert {"settings", "form_options"} <= change_feed.CLIENT_TOPICS
