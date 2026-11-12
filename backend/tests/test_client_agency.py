"""Clients with their own team and allocated CRM staff, reference agencies, and the routing rules that follow from them.

Synthetic data only; runs on the isolated crm_schema_test* PostgreSQL database (see test_database_naming).
Covers: migrations 0026 and 0033, the client and company API (staffContacts, staffIds, state), reference-agency CRUD, the desk snapshot
shape, the live-change topics, rule 1 (mapping a candidate to an order of a client with staff) and rule 2 (intake by designation:
manual entry, e-mail, WhatsApp, the waiting-list sweep).
"""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.orm import Session

from app.api.routers import applications, candidates, data, reference_agencies
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import change_feed, client_routing
from app.services.candidate_review import assign_waiting_candidates
from app.services.desk_snapshot import read_snapshot
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from auto_assign import round_robin_on  # noqa: F401  (fixture: these tests exercise the round-robin, which is opt-in)
import pytest as _pytest

pytestmark = _pytest.mark.usefixtures("round_robin_on")
from test_email_intake import extraction, fake_extraction, intake, receipt, run  # noqa: F401  (fixtures and helpers)
from test_waiting_candidates import ingest  # noqa: F401
from test_whatsapp_intake import setup  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture the WhatsApp setup builds on)


# ------------------------------------------------------------------------------------------------ fixture

@pytest.fixture
def env(schema_database):
    _migrate(schema_database, "head")
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([
            m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
            m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
            m.User(id="s1", name="Asha", email="s1@example.test", role=m.Role.staff, receives_auto_assign=True),
            m.User(id="s2", name="Ravi", email="s2@example.test", role=m.Role.staff, receives_auto_assign=True),
            m.User(id="s3", name="Zed", email="s3@example.test", role=m.Role.staff, receives_auto_assign=True),
            m.User(id="away", name="Away", email="away@example.test", role=m.Role.staff, receives_auto_assign=True, on_leave=True),
            m.User(id="gone", name="Gone", email="gone@example.test", role=m.Role.staff, active=False),
        ])
        db.commit()
        app = FastAPI()
        for router in (candidates.router, data.router, applications.router, reference_agencies.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, engine=schema_database, app=app)


def act_as(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()


def post(env, path, payload, status=201):
    result = env.client.post("/api/" + path, json=payload)
    assert result.status_code == status, result.text
    return result.json()


def new_client(env, name="Synthetic contact", staff=(), **extra):
    return post(env, "clients", {"name": name, "company": {"name": f"{name} Co"}, "staffIds": list(staff), **extra})


def open_order(env, client, title="Welder", status="open"):
    designation = env.db.scalar(select(m.Designation).where(m.Designation.title == title))
    if designation is None:
        designation = m.Designation(title=title)
        env.db.add(designation)
        env.db.commit()
    order = post(env, "orders", {"companyId": client["company"]["id"], "designationId": designation.id, "openings": 5, "currency": "AED"})
    if status != "open":
        env.client.put("/api/orders/" + order["id"], json={"companyId": client["company"]["id"], "designationId": designation.id,
                                                           "openings": 5, "currency": "AED", "status": status}).raise_for_status()
    return order


def add_candidate(env, who="admin", phone="9000000001", **extra):
    act_as(env, who)
    response = env.client.post("/api/candidates", json={"name": "Synthetic candidate", "phone": phone, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def give_candidates(env, owner_id, count, start=100):
    """Load a person with `count` candidates (direct rows, no workflow)."""
    for number in range(count):
        env.db.add(m.Candidate(ref=f"CND-LOAD-{owner_id}-{number}", name=f"Load {owner_id} {number}", phone=f"90001{start + number:05d}",
                               source=m.Source.walk_in, owner_id=owner_id))
    env.db.commit()


def raw_candidate(env, owner_id, ref="CND-RAW", phone="9000099999", designations=None):
    candidate = m.Candidate(ref=ref, name="Raw candidate", phone=phone, source=m.Source.walk_in, owner_id=owner_id, designations=designations or [])
    env.db.add(candidate)
    env.db.commit()
    return candidate


# ------------------------------------------------------------------------------------------------ migration

def test_migration_0026_adds_state_team_staff_links_and_agencies_keeps_rows_and_downgrades(schema_database):
    engine = schema_database
    _migrate(engine, "0025_client_fields")
    assert "state" not in {c["name"] for c in inspect(engine).get_columns("crm_employer_companies")}
    assert {"crm_client_staff", "crm_reference_agencies"}.isdisjoint(inspect(engine).get_table_names())
    with engine.begin() as c:
        c.execute(text("INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Asha', 'u1@example.test', 'staff')"))
        c.execute(text("INSERT INTO crm_client_contacts (id, name, email) VALUES ('k1', 'Old contact', 'old@example.test')"))
        c.execute(text("INSERT INTO crm_employer_companies (id, client_id, name, contact_person) VALUES ('c1', 'k1', 'Old employer', 'Old contact')"))
    _migrate(engine, "head")
    companies = {c["name"]: c for c in inspect(engine).get_columns("crm_employer_companies")}
    clients = {c["name"]: c for c in inspect(engine).get_columns("crm_client_contacts")}
    assert companies["state"]["nullable"] is True and companies["state"]["type"].length == 100
    assert clients["staff_contacts"]["nullable"] is False and clients["staff_contacts"]["comment"]
    assert {"client_id", "user_id"} == {c["name"] for c in inspect(engine).get_columns("crm_client_staff")}
    assert inspect(engine).get_pk_constraint("crm_client_staff")["constrained_columns"] == ["client_id", "user_id"]
    agencies = {c["name"] for c in inspect(engine).get_columns("crm_reference_agencies")}
    assert {"id", "name", "agency_name", "phone", "email", "address", "state", "staff_contacts", "notes", "created_at", "updated_at"} <= agencies
    with engine.connect() as c:  # existing rows are untouched and the new list defaults to empty
        assert c.execute(text("SELECT name, email, staff_contacts FROM crm_client_contacts WHERE id = 'k1'")).one() == ("Old contact", "old@example.test", [])
        assert c.execute(text("SELECT name, state FROM crm_employer_companies WHERE id = 'c1'")).one() == ("Old employer", None)
    with engine.begin() as c:
        c.execute(text("UPDATE crm_employer_companies SET state = 'Kerala' WHERE id = 'c1'"))
        c.execute(text("INSERT INTO crm_client_staff (client_id, user_id) VALUES ('k1', 'u1')"))
        c.execute(text("UPDATE alembic_version SET version_num = '0025_client_fields'"))
    _migrate(engine, "head")  # running it again changes nothing and keeps what was entered
    with engine.connect() as c:
        assert c.scalar(text("SELECT state FROM crm_employer_companies WHERE id = 'c1'")) == "Kerala"
        assert c.scalar(text("SELECT count(*) FROM crm_client_staff")) == 1
    with engine.begin() as c:  # the links go with the client and with the user
        c.execute(text("DELETE FROM auth_users WHERE id = 'u1'"))
        assert c.scalar(text("SELECT count(*) FROM crm_client_staff")) == 0
        c.execute(text("INSERT INTO auth_users (id, name, email, role) VALUES ('u2', 'Ravi', 'u2@example.test', 'staff')"))
        c.execute(text("INSERT INTO crm_client_staff (client_id, user_id) VALUES ('k1', 'u2')"))
        c.execute(text("DELETE FROM crm_employer_companies WHERE id = 'c1'"))
        c.execute(text("DELETE FROM crm_client_contacts WHERE id = 'k1'"))
        assert c.scalar(text("SELECT count(*) FROM crm_client_staff")) == 0
        c.execute(text("INSERT INTO crm_client_contacts (id, name) VALUES ('k2', 'Another')"))
    _migrate(engine, "0025_client_fields", downgrade=True)
    assert "state" not in {c["name"] for c in inspect(engine).get_columns("crm_employer_companies")}
    assert "staff_contacts" not in {c["name"] for c in inspect(engine).get_columns("crm_client_contacts")}
    assert {"crm_client_staff", "crm_reference_agencies"}.isdisjoint(inspect(engine).get_table_names())
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM crm_client_contacts WHERE id = 'k2'")) == 1
    _migrate(engine, "head")


# ------------------------------------------------------------------------------------------------ client API

def test_client_saves_team_allocated_staff_company_state_and_address(env):
    created = post(env, "clients", {
        "name": " Khalid Rahman ", "email": "K@Example.test", "phone": "+97140000001",
        "staffContacts": [{"name": "  Maya Singh ", "designation": " Recruiter ", "phone": " +97150000001 ", "email": " MAYA@Example.TEST "},
                          {"name": "Tom Lee", "designation": "", "phone": None}],
        "staffIds": ["s2", "manager", "s2"],
        "company": {"name": "Gulf Build LLC", "state": " Dubai ", "address": "Plot 4, Al Quoz"},
    })
    assert created["staffContacts"] == [
        {"name": "Maya Singh", "designation": "Recruiter", "phone": "+97150000001", "email": "maya@example.test"},
        {"name": "Tom Lee", "designation": None, "phone": None, "email": None}]
    assert created["staffIds"] == ["manager", "s2"]  # ordered by name: Manager, Ravi
    assert created["company"]["state"] == "Dubai" and created["company"]["address"] == "Plot 4, Al Quoz"
    link = env.db.scalars(select(m.ClientStaff.user_id).where(m.ClientStaff.client_id == created["id"]))
    assert set(link) == {"s2", "manager"}
    audit = env.db.scalar(select(m.AuditEntry).where(m.AuditEntry.entity_type == "client", m.AuditEntry.action == "Created client"))
    assert audit.meta["changes"]["staffIds"] == {"from": [], "to": ["manager", "s2"]}
    assert audit.meta["changes"]["staff_contacts"]["to"][0]["name"] == "Maya Singh"


def test_client_without_the_new_fields_still_works_and_reads_empty(env):
    legacy = post(env, "clients", {"name": "Old payload"})
    assert legacy["staffContacts"] == [] and legacy["staffIds"] == []
    no_state = new_client(env, "Plain")
    assert no_state["company"]["state"] is None


def test_put_replaces_the_team_and_staff_but_keeps_what_it_does_not_mention(env):
    client = new_client(env, staff=["s1", "s2"], staffContacts=[{"name": "A"}])
    path = "/api/clients/" + client["id"]
    kept = env.client.put(path, json={"name": "Synthetic contact", "status": "inactive"})
    assert kept.status_code == 200
    assert set(kept.json()["staffIds"]) == {"s1", "s2"} and kept.json()["staffContacts"][0]["name"] == "A"
    replaced = env.client.put(path, json={"name": "Synthetic contact", "staffIds": ["s3"], "staffContacts": [{"name": "B"}, {"name": "C", "phone": "1"}]})
    assert replaced.status_code == 200 and replaced.json()["staffIds"] == ["s3"]
    assert [c["name"] for c in replaced.json()["staffContacts"]] == ["B", "C"]
    cleared = env.client.put(path, json={"name": "Synthetic contact", "staffIds": [], "staffContacts": []})
    assert cleared.json()["staffIds"] == [] and cleared.json()["staffContacts"] == []
    assert env.db.scalar(select(func.count()).select_from(m.ClientStaff)) == 0
    history = env.client.get("/api/master-data/client/" + client["id"] + "/audit").json()
    moves = [h["meta"]["changes"]["staffIds"] for h in history if "staffIds" in h["meta"]["changes"]]
    assert moves[0] == {"from": ["s3"], "to": []} and moves[1] == {"from": ["s1", "s2"], "to": ["s3"]}
    # Only the allocation moved: a save that changes nothing writes no audit entry.
    before = len(history)
    env.client.put(path, json={"name": "Synthetic contact", "staffIds": []})
    assert len(env.client.get("/api/master-data/client/" + client["id"] + "/audit").json()) == before


@pytest.mark.parametrize("staff_ids,fragment", [
    (["nobody"], "not a CRM user"), (["gone"], "deactivated"), (["admin"], "only staff or managers"),
])
def test_staff_ids_must_be_active_staff_or_managers(env, staff_ids, fragment):
    rejected = env.client.post("/api/clients", json={"name": "N", "company": {"name": "C"}, "staffIds": ["s1", *staff_ids]})
    assert rejected.status_code == 422
    assert fragment in rejected.json()["detail"]["errors"][0]["message"] and rejected.json()["detail"]["errors"][0]["field"] == "staffIds"
    assert env.db.scalar(select(m.Client).where(m.Client.name == "N")) is None  # nothing half-saved
    client = new_client(env, staff=["s1"])
    assert env.client.put("/api/clients/" + client["id"], json={"name": "X", "staffIds": staff_ids}).status_code == 422
    assert env.db.scalars(select(m.ClientStaff.user_id).where(m.ClientStaff.client_id == client["id"])).all() == ["s1"]


def test_staff_contacts_are_validated(env):
    path = "/api/clients"
    def attempt(contacts):
        return env.client.post(path, json={"name": "N", "staffContacts": contacts}).status_code
    assert attempt([{"name": "ok"}] * 50) == 201
    assert attempt([{"name": "ok"}] * 51) == 422
    assert attempt([{"name": "   "}]) == 422 and attempt([{"phone": "1"}]) == 422
    assert attempt([{"name": "x" * 161}]) == 422
    assert attempt([{"name": "x", "phone": "1" * 41}]) == 422
    assert attempt([{"name": "x", "email": "e" * 256}]) == 422
    assert attempt([{"name": "x", "designation": "d" * 121}]) == 422
    assert attempt([{"name": "x", "surprise": 1}]) == 422
    assert attempt([{"name": "x", "phone": "1" * 40, "email": "a" * 255, "designation": "d" * 120}]) == 201


def test_company_state_is_saved_cleared_and_limited(env):
    client = new_client(env)
    company = client["company"]
    base = {"clientId": client["id"], "name": "Synthetic contact Co"}
    changed = env.client.put("/api/companies/" + company["id"], json={**base, "state": " Kerala "})
    assert changed.status_code == 200 and changed.json()["state"] == "Kerala"
    assert env.client.put("/api/companies/" + company["id"], json={**base, "state": ""}).json()["state"] is None
    assert env.client.put("/api/companies/" + company["id"], json={**base, "state": "x" * 101}).status_code == 422
    assert post(env, "companies", {**base, "name": "Second", "state": "Goa"})["state"] == "Goa"


def test_delete_client_removes_its_staff_links_and_a_deleted_user_leaves_staff_ids(env):
    first, second = new_client(env, "First", staff=["s1", "s2"]), new_client(env, "Second", staff=["s2", "s3"])
    assert env.client.delete("/api/clients/" + first["id"]).status_code == 200
    assert env.db.scalar(select(func.count()).select_from(m.ClientStaff).where(m.ClientStaff.client_id == first["id"])) == 0
    assert env.db.scalar(select(func.count()).select_from(m.ClientStaff)) == 2
    env.db.execute(text("DELETE FROM auth_users WHERE id = 's2'"))
    env.db.commit()
    snapshot = read_snapshot(env.db, "admin")
    assert next(c for c in snapshot["clients"] if c["id"] == second["id"])["staff_ids"] == ["s3"]


def test_a_deactivated_or_demoted_user_is_not_listed_but_the_link_is_kept(env):
    client = new_client(env, staff=["s1", "s2", "manager"])
    env.db.get(m.User, "s1").active = False
    env.db.get(m.User, "manager").role = m.Role.admin
    env.db.commit()
    listed = next(c for c in read_snapshot(env.db, "admin")["clients"] if c["id"] == client["id"])
    assert listed["staff_ids"] == ["s2"]
    assert env.db.scalar(select(func.count()).select_from(m.ClientStaff)) == 3


# ------------------------------------------------------------------------------------------------ desk snapshot

def test_desk_snapshot_carries_staff_contacts_staff_ids_and_state_in_one_statement(env):
    statements = []
    @event.listens_for(env.engine, "before_cursor_execute")
    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    try:
        empty = read_snapshot(env.db, "staff")
        env.db.rollback()
        baseline = len(statements)
        for number in range(6):
            new_client(env, f"Client {number}", staff=["s1", "s2"] if number % 2 else [], staffContacts=[{"name": f"T{number}"}])
        statements.clear()
        snapshot = read_snapshot(env.db, "staff")
        assert len(statements) == baseline  # the same number of statements however many clients there are
    finally:
        event.remove(env.engine, "before_cursor_execute", count)
    assert empty["clients"] == []
    clients = {c["name"]: c for c in snapshot["clients"]}
    assert len(clients) == 6
    assert clients["Client 1"]["staff_ids"] == ["s1", "s2"] and clients["Client 0"]["staff_ids"] == []
    assert clients["Client 3"]["staff_contacts"] == [{"name": "T3", "designation": None, "phone": None, "email": None}]
    assert all("state" in c for c in snapshot["companies"])
    payload = data._bootstrap(env.db, env.db.get(m.User, "s1"))
    wire = next(c for c in payload["clients"] if c["name"] == "Client 1")
    assert wire["staffIds"] == ["s1", "s2"] and wire["staffContacts"] == [{"name": "T1", "designation": None, "phone": None, "email": None}]
    assert "state" in payload["companies"][0]


# ------------------------------------------------------------------------------------------------ reference agencies

def test_reference_agency_crud_audit_and_order(env):
    act_as(env, "s1")  # any signed-in role may use it, like creating a client
    first = post(env, "reference-agencies", {
        "name": " Priya Nair ", "agencyName": " Blue Ocean Agency ", "phone": "+919000000010", "email": "P@Example.test",
        "address": "12 Harbour Road", "state": " Kerala ", "staffContacts": [{"name": "Dev", "designation": "Coordinator"}]})
    assert first["name"] == "Priya Nair" and first["agencyName"] == "Blue Ocean Agency" and first["email"] == "p@example.test"
    assert first["state"] == "Kerala" and first["staffContacts"][0]["designation"] == "Coordinator" and first["createdAt"]
    assert set(first) >= {"id", "name", "agencyName", "phone", "email", "address", "state", "staffContacts", "notes", "createdAt"}
    # Two branches may share a name.
    second = post(env, "reference-agencies", {"name": "Other", "agencyName": "Blue Ocean Agency"})
    assert second["phone"] is None and second["staffContacts"] == []
    listed = env.client.get("/api/reference-agencies").json()
    assert [i["id"] for i in listed["items"]] == [second["id"], first["id"]]  # newest first
    act_as(env, "manager")
    changed = env.client.put("/api/reference-agencies/" + first["id"], json={"name": "Priya Nair", "agencyName": "Blue Ocean Group", "staffContacts": []})
    assert changed.status_code == 200 and changed.json()["agencyName"] == "Blue Ocean Group" and changed.json()["staffContacts"] == []
    history = env.client.get("/api/master-data/reference_agency/" + first["id"] + "/audit").json()
    assert [h["action"] for h in history] == ["Updated reference_agency", "Created reference_agency"]
    assert history[0]["meta"]["changes"]["agency_name"]["to"] == "Blue Ocean Group"
    assert env.client.delete("/api/reference-agencies/" + first["id"]).status_code == 204
    assert env.client.delete("/api/reference-agencies/" + first["id"]).status_code == 404
    assert [i["id"] for i in env.client.get("/api/reference-agencies").json()["items"]] == [second["id"]]
    deleted = env.db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Deleted reference agency"))
    assert deleted.entity_type == "reference_agency" and deleted.entity_id == first["id"] and deleted.actor_id == "manager"
    assert env.client.put("/api/reference-agencies/missing", json={"name": "x", "agencyName": "y"}).status_code == 404


@pytest.mark.parametrize("bad", [
    {}, {"name": " ", "agencyName": " "},
    {"name": "N", "agencyName": "A", "email": "not-an-email"}, {"name": "N", "agencyName": "A", "phone": "1" * 41},
    {"name": "N", "agencyName": "A", "state": "s" * 101}, {"name": "N", "agencyName": "A", "staffContacts": [{"phone": "1"}]},
    {"name": "N", "agencyName": "A", "staffContacts": [{"name": "x"}] * 51}, {"name": "N", "agencyName": "A", "colour": "red"},
])
def test_reference_agency_validation(env, bad):
    assert env.client.post("/api/reference-agencies", json=bad).status_code == 422
    assert env.db.scalar(select(func.count()).select_from(m.ReferenceAgency)) == 0


def test_reference_agency_specialisations_and_preferred_countries_round_trip(env):
    plain = post(env, "reference-agencies", {"name": "N", "agencyName": "Plain"})
    assert plain["specialisations"] == [] and plain["preferredCountries"] == []  # both default to empty lists
    created = post(env, "reference-agencies", {
        "name": "N", "agencyName": "Focus", "specialisations": ["Medical", "Oil and gas", "Medical"],
        "preferredCountries": ["Gulf", "Singapore", "Gulf"]})
    assert created["specialisations"] == ["Oil and gas", "Medical"]  # distinct, in the order the form lists them
    assert created["preferredCountries"] == ["Singapore", "Gulf"]
    row = env.db.get(m.ReferenceAgency, created["id"])
    assert row.specialisations == ["Oil and gas", "Medical"] and row.preferred_countries == ["Singapore", "Gulf"]
    url = "/api/reference-agencies/" + created["id"]
    # "All sectors" means every sector, so it stands alone.
    all_sectors = env.client.put(url, json={"name": "N", "agencyName": "Focus", "specialisations": ["Medical", "All sectors", "Hospitality"],
                                           "preferredCountries": ["Others"]})
    assert all_sectors.status_code == 200 and all_sectors.json()["specialisations"] == ["All sectors"]
    assert all_sectors.json()["preferredCountries"] == ["Others"]
    # A PUT that does not mention them keeps them; an explicit empty list clears them.
    kept = env.client.put(url, json={"name": "N2", "agencyName": "Focus"})
    assert kept.json()["specialisations"] == ["All sectors"] and kept.json()["preferredCountries"] == ["Others"]
    cleared = env.client.put(url, json={"name": "N2", "agencyName": "Focus", "specialisations": [], "preferredCountries": []})
    assert cleared.json()["specialisations"] == [] and cleared.json()["preferredCountries"] == []
    listed = {i["id"]: i for i in env.client.get("/api/reference-agencies").json()["items"]}
    assert listed[created["id"]]["specialisations"] == [] and listed[plain["id"]]["preferredCountries"] == []
    history = env.client.get("/api/master-data/reference_agency/" + created["id"] + "/audit").json()
    assert history[0]["meta"]["changes"]["specialisations"] == {"from": ["All sectors"], "to": []}
    assert history[-1]["meta"]["changes"]["preferred_countries"]["to"] == ["Singapore", "Gulf"]


@pytest.mark.parametrize("bad", [
    {"specialisations": ["Banking"]}, {"specialisations": "Medical"}, {"specialisations": [None]}, {"specialisations": ["medical"]},
    {"specialisations": ["Medical"] * 13}, {"preferredCountries": ["Mars"]}, {"preferredCountries": "Gulf"},
    {"preferredCountries": ["Gulf"] * 11},
])
def test_reference_agency_focus_lists_reject_unknown_values(env, bad):
    assert env.client.post("/api/reference-agencies", json={"name": "N", "agencyName": "A", **bad}).status_code == 422
    assert env.db.scalar(select(func.count()).select_from(m.ReferenceAgency)) == 0


def test_migration_0033_adds_agency_focus_lists_additively_and_reversibly(schema_database):
    engine = schema_database
    _migrate(engine, "0032_job_ready_at")
    before = {c["name"] for c in inspect(engine).get_columns("crm_reference_agencies")}
    assert {"specialisations", "preferred_countries"}.isdisjoint(before)
    with engine.begin() as c:
        c.execute(text("INSERT INTO crm_reference_agencies (id, name, agency_name) VALUES ('a1', 'Old contact', 'Old agency')"))
    _migrate(engine, "head")
    columns = {c["name"]: c for c in inspect(engine).get_columns("crm_reference_agencies")}
    assert before <= set(columns)  # nothing renamed or dropped
    for name in ("specialisations", "preferred_countries"):
        assert columns[name]["nullable"] is False and columns[name]["default"] and columns[name]["comment"]
    with engine.begin() as c:
        # An older writer does not name the new columns: its INSERT still works and the lists are empty.
        c.execute(text("INSERT INTO crm_reference_agencies (id, name, agency_name, staff_contacts) VALUES ('a2', 'Older writer', 'Agency', '[]')"))
        c.execute(text("UPDATE crm_reference_agencies SET specialisations = '[\"Medical\"]' WHERE id = 'a1'"))
    with engine.connect() as c:
        assert c.execute(text("SELECT name, specialisations, preferred_countries FROM crm_reference_agencies WHERE id = 'a2'")).one() == ("Older writer", [], [])
        assert c.scalar(text("SELECT specialisations FROM crm_reference_agencies WHERE id = 'a1'")) == ["Medical"]
    with Session(engine) as db:  # the ORM reads old rows as empty lists
        assert db.get(m.ReferenceAgency, "a2").preferred_countries == []
    with engine.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = '0032_job_ready_at'"))
    _migrate(engine, "head")  # running it again is a no-op and keeps what was entered
    with engine.connect() as c:
        assert c.scalar(text("SELECT specialisations FROM crm_reference_agencies WHERE id = 'a1'")) == ["Medical"]
    _migrate(engine, "0032_job_ready_at", downgrade=True)
    assert {"specialisations", "preferred_countries"}.isdisjoint({c["name"] for c in inspect(engine).get_columns("crm_reference_agencies")})
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM crm_reference_agencies")) == 2
    _migrate(engine, "head")


def test_reference_agencies_need_a_signed_in_user(schema_database):
    _migrate(schema_database, "head")
    app = FastAPI()
    app.include_router(reference_agencies.router)
    with Session(schema_database) as db:
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            assert client.get("/api/reference-agencies").status_code == 401
            assert client.post("/api/reference-agencies", json={"name": "N", "agencyName": "A"}).status_code == 401
            assert client.delete("/api/reference-agencies/x").status_code == 401


# ------------------------------------------------------------------------------------------------ live topics

def test_change_feed_announces_staff_links_on_clients_and_agencies_on_their_own_topic():
    assert change_feed.TOPIC_TABLES["crm_client_staff"] == ("clients",)
    assert change_feed.TOPIC_TABLES["crm_reference_agencies"] == ("reference_agencies",)
    assert "reference_agencies" in change_feed.CLIENT_TOPICS
    for statement, table in (("INSERT INTO crm_client_staff (client_id, user_id) VALUES (%(a)s, %(b)s)", "crm_client_staff"),
                             ("DELETE FROM crm_client_staff WHERE crm_client_staff.client_id = %(a)s", "crm_client_staff"),
                             ("INSERT INTO crm_reference_agencies (id, name) VALUES (%(a)s, %(b)s)", "crm_reference_agencies"),
                             ("UPDATE crm_reference_agencies SET name=%(name)s WHERE id = %(id)s", "crm_reference_agencies")):
        verb, found = change_feed.classify_statement(statement)
        assert found == table
    pending = change_feed.Pending()
    pending.note_table("INSERT", "crm_client_staff")
    pending.note_table("UPDATE", "crm_reference_agencies")
    import json
    body = json.loads(pending.payload(1))
    assert "clients" in body["t"] and "reference_agencies" in body["t"] and "reports" in body["t"]
    assert change_feed.visible_to(body, "staff", "s1")["topics"] == ["clients", "reference_agencies", "reports"]


# ------------------------------------------------------------------------------------------------ rule 1: mapping

def map_candidate(env, who, candidate_id, order_id):
    act_as(env, who)
    return env.client.post("/api/applications", json={"candidate_id": candidate_id, "order_id": order_id})


def test_mapping_hands_the_candidate_to_the_least_loaded_client_staff_member(env):
    client = new_client(env, staff=["s1", "s2"])
    order = open_order(env, client)
    give_candidates(env, "s1", 2)  # Ravi (s2) is the lighter of the two
    candidate = raw_candidate(env, "s3")
    env.db.add(m.Task(kind=m.TaskKind.initial_call, title="Initial call", candidate_id=candidate.id, owner_id="s3"))
    env.db.commit()
    result = map_candidate(env, "s3", candidate.id, order["id"])  # the current owner maps their own candidate
    assert result.status_code == 201, result.text
    env.db.expire_all()
    saved = env.db.get(m.Candidate, candidate.id)
    assert saved.owner_id == "s2"
    application = env.db.scalar(select(m.Application).where(m.Application.candidate_id == candidate.id))
    assert application.owner_id == "s2"
    stage_task = env.db.scalar(select(m.Task).where(m.Task.application_id == application.id))
    assert stage_task.owner_id == "s2"
    # Assignment history, as the admin reassignment writes it.
    act_as(env, "admin")
    history = env.client.get(f"/api/candidates/{candidate.id}/assignment-history").json()["items"]
    assert len(history) == 1 and history[0]["fromUserId"] == "s3" and history[0]["toUserId"] == "s2" and history[0]["actorId"] == "s3"
    assert "Synthetic contact" in history[0]["reason"] and order["ref"] in history[0]["reason"]
    # The new owner and the admin are told; nobody else.
    notices = env.db.scalars(select(m.Notification).where(m.Notification.link_to == f"/candidates/{candidate.id}", m.Notification.kind == "assignment")).all()
    assert {n.user_id for n in notices} == {"s2", "admin"} and all("Candidate assigned" == n.title for n in notices)
    # Open tasks followed the candidate.
    assert {t.owner_id for t in env.db.scalars(select(m.Task).where(m.Task.candidate_id == candidate.id, m.Task.cancelled_at.is_(None)))} == {"s2"}
    assert env.db.scalar(select(func.count()).select_from(m.TaskEvent).where(m.TaskEvent.kind == "assignment")) >= 1


def test_mapping_tie_break_is_name_then_id_and_counts_candidates_owned(env):
    client = new_client(env, staff=["s3", "s1", "s2"])
    order = open_order(env, client)
    outcomes = []
    for number in range(3):
        candidate = raw_candidate(env, "admin", ref=f"CND-T{number}", phone=f"900008888{number}")
        assert map_candidate(env, "admin", candidate.id, order["id"]).status_code == 201
        env.db.expire_all()
        outcomes.append(env.db.get(m.Candidate, candidate.id).owner_id)
    # Everyone starts at 0: Asha (s1), then Ravi (s2), then Zed (s3) as each one's count goes up.
    assert outcomes == ["s1", "s2", "s3"]


def test_mapping_without_client_staff_or_with_the_owner_already_one_of_them_changes_nothing(env):
    plain = new_client(env)
    order = open_order(env, plain)
    candidate = raw_candidate(env, "s3")
    assert map_candidate(env, "s3", candidate.id, order["id"]).status_code == 201
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "s3"
    assert env.db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == "Candidate reassigned")) == 0

    staffed = new_client(env, "Staffed", staff=["s1", "s2"])
    other_order = open_order(env, staffed)
    own = raw_candidate(env, "s2", ref="CND-OWN", phone="9000077777")
    give_candidates(env, "s2", 5)  # heavily loaded, but they already own it
    assert map_candidate(env, "s2", own.id, other_order["id"]).status_code == 201
    env.db.expire_all()
    assert env.db.get(m.Candidate, own.id).owner_id == "s2"
    assert env.db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == "Candidate reassigned")) == 0


def test_mapping_skips_client_staff_who_cannot_work_today(env):
    # Allocated: a person on leave, a deactivated one, and the only available one is a manager.
    client = new_client(env, staff=["s1", "manager"])
    env.db.get(m.User, "s1").on_leave = True
    env.db.commit()
    order = open_order(env, client)
    candidate = raw_candidate(env, "s3")
    assert map_candidate(env, "admin", candidate.id, order["id"]).status_code == 201
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "manager"
    # Nobody available: the candidate stays with its owner.
    env.db.get(m.User, "manager").active = False
    env.db.commit()
    again = raw_candidate(env, "s3", ref="CND-AGAIN", phone="9000066666")
    assert map_candidate(env, "admin", again.id, order["id"]).status_code == 201
    env.db.expire_all()
    assert env.db.get(m.Candidate, again.id).owner_id == "s3"


def test_an_admin_reassignment_after_mapping_is_not_undone(env):
    client = new_client(env, staff=["s2"])
    order = open_order(env, client)
    candidate = raw_candidate(env, "s3")
    assert map_candidate(env, "s3", candidate.id, order["id"]).status_code == 201
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "s2"
    act_as(env, "admin")
    moved = env.client.post("/api/candidates/reassign", json={"candidateIds": [candidate.id], "toUserId": "s1", "reason": "Workload"})
    assert moved.status_code == 200, moved.text
    # Later work on the application, a new recruiter's save, a repeat read: none of it routes again.
    application = env.db.scalar(select(m.Application).where(m.Application.candidate_id == candidate.id))
    act_as(env, "s1")
    assert env.client.put(f"/api/applications/{application.id}/draft", json={"data": {}, "lock_version": application.lock_version}).status_code in (200, 422)
    act_as(env, "admin")
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "s1"
    history = env.client.get(f"/api/candidates/{candidate.id}/assignment-history").json()["items"]
    assert [(h["fromUserId"], h["toUserId"]) for h in history] == [("s2", "s1"), ("s3", "s2")]


def test_a_failed_mapping_leaves_the_owner_alone(env):
    client = new_client(env, staff=["s2"])
    order = open_order(env, client)
    env.client.put("/api/orders/" + order["id"], json={"companyId": client["company"]["id"], "designationId": order["designationId"],
                                                       "openings": 5, "currency": "AED", "status": "on_hold"}).raise_for_status()
    candidate = raw_candidate(env, "s3")
    refused = map_candidate(env, "admin", candidate.id, order["id"])
    assert refused.status_code == 409
    env.db.expire_all()
    assert env.db.get(m.Candidate, candidate.id).owner_id == "s3"
    assert env.db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == "Candidate reassigned")) == 0


# ------------------------------------------------------------------------------------------------ rule 2: intake

def test_manual_entry_with_a_matching_designation_goes_to_the_clients_staff(env):
    client = new_client(env, staff=["s3"])
    open_order(env, client, "Welder")
    routed = add_candidate(env, "admin", designations=["  WELDER "])
    assert routed["ownerId"] == "s3"
    task = env.db.get(m.Task, routed["taskId"])
    assert task.owner_id == "s3"
    saved = env.db.get(m.Candidate, routed["id"])
    assert saved.owner_id == saved.intake_owner_id == "s3"
    assert {n.user_id for n in env.db.scalars(select(m.Notification).where(m.Notification.link_to == f"/candidates/{saved.id}"))} == {"s3", "admin"}
    pointer = env.db.get(m.AssignmentPointer, 1)
    assert pointer is None or pointer.last_user_id is None  # the round-robin did not move


def test_manual_entry_without_a_match_keeps_the_round_robin(env):
    client = new_client(env, staff=["s3"])
    open_order(env, client, "Welder")
    first = add_candidate(env, "admin", phone="9000000011", designations=["Plumber"])
    second = add_candidate(env, "admin", phone="9000000012")  # no designation at all
    third = add_candidate(env, "admin", phone="9000000013", designations=["Plumber", "Cook"])
    assert [first["ownerId"], second["ownerId"], third["ownerId"]] == ["s1", "s2", "s3"]  # strict ring, as before


def test_clients_without_staff_closed_orders_and_inactive_clients_do_not_capture(env):
    no_staff = new_client(env, "NoStaff")
    open_order(env, no_staff, "Welder")
    closed = new_client(env, "Closed", staff=["s3"])
    open_order(env, closed, "Rigger", status="closed")
    on_hold = new_client(env, "Hold", staff=["s3"])
    open_order(env, on_hold, "Fitter", status="on_hold")
    gone = new_client(env, "Inactive", staff=["s3"])
    open_order(env, gone, "Mason")
    env.client.put("/api/clients/" + gone["id"], json={"name": "Inactive", "status": "inactive"}).raise_for_status()
    owners = [add_candidate(env, "admin", phone=f"900000002{n}", designations=[title])["ownerId"]
              for n, title in enumerate(["Welder", "Rigger", "Fitter", "Mason"])]
    assert owners == ["s1", "s2", "s3", "s1"]  # all ordinary round-robin


def test_several_matching_clients_pick_the_least_loaded_across_all_of_them(env):
    first = new_client(env, "First", staff=["s1", "s2"])
    second = new_client(env, "Second", staff=["s2", "s3"])
    open_order(env, first, "Welder")
    open_order(env, second, "welder")
    give_candidates(env, "s1", 3)
    give_candidates(env, "s2", 1)
    assert add_candidate(env, "manager", phone="9000000031", designations=["Welder"])["ownerId"] == "s3"  # 0 owned
    # s2 and s3 now own one each: the tie goes to the lower name, Ravi.
    assert add_candidate(env, "manager", phone="9000000032", designations=["Welder"])["ownerId"] == "s2"


def test_an_admins_explicit_choice_wins_and_staff_creators_are_routed_unless_they_are_the_clients_staff(env):
    client = new_client(env, staff=["s3", "s2"])
    open_order(env, client, "Welder")
    chosen = env.client.post("/api/candidates", json={"name": "Chosen", "phone": "9000000041", "designations": ["Welder"], "assign_to": "s1"})
    assert chosen.status_code == 201 and chosen.json()["ownerId"] == "s1"
    # Staff member s1 adds a Welder: it is the client's team that works it.
    routed = add_candidate(env, "s1", phone="9000000042", designations=["Welder"])
    assert routed["ownerId"] in ("s2", "s3")
    # s2 is one of that client's staff, so what they add themselves stays theirs even though s3 is lighter.
    give_candidates(env, "s2", 4)
    kept = add_candidate(env, "s2", phone="9000000043", designations=["Welder"])
    assert kept["ownerId"] == "s2"
    # And a staff member adding something no client asks for keeps it, as before.
    assert add_candidate(env, "s1", phone="9000000044", designations=["Plumber"])["ownerId"] == "s1"


def test_waiting_list_sweep_routes_by_designation_then_round_robin(env):
    client = new_client(env, staff=["s3"])
    open_order(env, client, "Welder")
    for number, designations in enumerate((["Welder"], ["Plumber"], ["Welder"])):
        env.db.add(m.Candidate(ref=f"CND-W{number}", name=f"Waiting {number}", phone=f"900005000{number}", source=m.Source.walk_in,
                               designations=designations, owner_id=None, needs_review=True, status=m.CandidateStatus.needs_review))
    env.db.commit()
    result = assign_waiting_candidates(env.db, "admin", "Admin")
    env.db.commit()
    assert result["assigned"] == 3
    owners = {c.ref: c.owner_id for c in env.db.scalars(select(m.Candidate).where(m.Candidate.ref.like("CND-W%")))}
    assert owners["CND-W0"] == "s3" and owners["CND-W2"] == "s3"  # loads: s3 owned 0, then 1 (still the only candidate of the client's staff)
    assert owners["CND-W1"] == "s1"  # the ring starts at the top for the one that matched nothing


def test_routing_helpers_report_who_matches(env):
    client = new_client(env, staff=["s1", "away"])
    open_order(env, client, "Welder")
    assert client_routing.any_allocations(env.db) is True
    assert client_routing.matching_staff_ids(env.db, ["welder"]) == {"s1", "away"}
    assert client_routing.matching_staff_ids(env.db, ["Plumber", "", None, 4]) == set()
    assert client_routing.matching_staff_ids(env.db, None) == set()
    assert client_routing.route_new_candidate(env.db, ["Welder"]).id == "s1"  # "away" is on leave
    assert client_routing.route_new_candidate(env.db, ["Welder"], keep_user_id="s1") is None
    assert client_routing.route_new_candidate(env.db, ["Plumber"]) is None


# ---- e-mail and WhatsApp intake (the shared fixtures of their own test modules)

def allocate_welder_client(sessions, user_id):
    with sessions() as db:
        db.add_all([m.Client(id="k", name="Client"), m.Designation(id="d", title="Welder")])
        db.flush()
        db.add(m.Company(id="co", client_id="k", name="Employer"))
        db.flush()
        db.add_all([m.Order(id="o", ref="ORD-1", company_id="co", designation_id="d", openings=2, status=m.OrderStatus.open),
                    m.ClientStaff(client_id="k", user_id=user_id)])
        db.commit()


def test_email_intake_routes_a_matching_designation_to_the_clients_staff(intake, monkeypatch):
    allocate_welder_client(intake.sessions, "staff-b")  # the ring would give the first candidate to staff-a
    job, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction())  # the document says designation "Welder"
    run(intake, job)
    with intake.sessions() as db:
        candidate = db.scalar(select(m.Candidate))
        assert candidate.owner_id == candidate.intake_owner_id == "staff-b"
        assert db.scalar(select(m.Task.owner_id).where(m.Task.candidate_id == candidate.id)) == "staff-b"
        pointer = db.get(m.AssignmentPointer, 1)
        assert pointer is None or pointer.last_user_id is None


def test_email_intake_without_a_match_is_the_ordinary_round_robin(intake, monkeypatch):
    allocate_welder_client(intake.sessions, "staff-b")
    job, _ = receipt(intake)
    fake_extraction(monkeypatch, extraction(fields={"designation": "Plumber"}))
    run(intake, job)
    with intake.sessions() as db:
        assert db.scalar(select(m.Candidate.owner_id)) == "staff-a"


def test_whatsapp_intake_routes_a_matching_designation_to_the_clients_staff(setup):
    sessions, settings = setup
    allocate_welder_client(sessions, "other")  # only "owner" takes part in the ordinary round-robin
    candidate = ingest(sessions, settings)  # the extraction names the designation Welder
    assert candidate.owner_id == candidate.intake_owner_id == "other"


def test_whatsapp_intake_without_a_match_is_the_ordinary_round_robin(setup):
    sessions, settings = setup
    allocate_welder_client(sessions, "other")
    candidate = ingest(sessions, settings, designation="Plumber")
    assert candidate.owner_id == "owner"


# ------------------------------------------------------------------------------------------------ new job notices

def test_creating_a_job_notifies_the_clients_allocated_staff_but_not_the_creator(env):
    client = new_client(env, "Notice Client", staff=["s1", "s2"])
    open_order(env, client, title="Plumber")
    notices = env.db.scalars(select(m.Notification).where(m.Notification.kind == "job").order_by(m.Notification.user_id)).all()
    assert [notice.user_id for notice in notices] == ["s1", "s2"]
    assert notices[0].title == "New job for Notice Client Co" and notices[0].link_to.startswith("/orders/")
    assert "Plumber" in notices[0].detail and "5 openings" in notices[0].detail

    act_as(env, "s1")  # a creator who is one of the staff is not told about their own job
    open_order(env, client, title="Plumber")
    again = env.db.scalars(select(m.Notification).where(m.Notification.kind == "job", m.Notification.user_id == "s1")).all()
    assert len(again) == 1
    assert len(env.db.scalars(select(m.Notification).where(m.Notification.kind == "job", m.Notification.user_id == "s2")).all()) == 2


def test_a_job_for_a_client_without_staff_sends_no_notice(env):
    client = new_client(env, "Nobody Client")
    open_order(env, client, title="Cook")
    assert env.db.scalars(select(m.Notification).where(m.Notification.kind == "job")).all() == []
