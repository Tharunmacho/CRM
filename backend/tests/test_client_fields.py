"""The client's designation and the company's website: normalisation, the API, the desk snapshot and migration 0025."""
import pytest
from sqlalchemy import inspect, select, text

from app.db.models import AuditEntry, Client, Company, User
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_master_data import desk, post, subject  # noqa: F401  (fixture)


@pytest.mark.parametrize("raw,expected", [
    ("", None), ("   ", None),
    ("www.Gulf-Build.com", "https://www.gulf-build.com"),
    ("  gulfbuild.ae/careers?x=1 ", "https://gulfbuild.ae/careers?x=1"),
    ("https://Example.com/", "https://example.com"),
    ("http://example.com/Path/", "http://example.com/Path/"),
    ("example.com:8080", "https://example.com:8080"),
    ("HTTPS://EXAMPLE.COM", "https://example.com"),
])
def test_website_is_tidied_to_one_address(raw, expected):
    assert subject.normalize_website(raw) == expected


@pytest.mark.parametrize("raw", [
    "gulf build.com", "javascript:alert(1)", "JaVaScRiPt://x.com", "data:text/html,hi", "mailto:a@b.com", "ftp://example.com",
    "vbscript:x", "file:///etc/passwd", "https://user:pass@example.com", "localhost", "https://", ".com", "a..com", "-a.com",
    "https://exa mple.com", "http://[::1", "x" * 250 + ".com",
])
def test_website_rejects_unsafe_or_malformed_addresses(raw):
    with pytest.raises(ValueError):
        subject.normalize_website(raw)


def test_new_client_form_saves_designation_and_website(desk):
    created = post(desk, "clients", {"name": "Khalid Rahman", "designation": "  HR Manager ", "email": "k@example.test",
                                     "company": {"name": "Gulf Build LLC", "website": "WWW.GulfBuild.com"}})
    assert created["designation"] == "HR Manager"
    assert created["company"]["website"] == "https://www.gulfbuild.com"
    with desk.sessions() as db:
        assert db.get(Client, created["id"]).designation == "HR Manager"
        assert db.get(Company, created["company"]["id"]).website == "https://www.gulfbuild.com"
        snapshot = subject.read_snapshot(db, "staff")
        assert next(c for c in snapshot["clients"] if c["id"] == created["id"])["designation"] == "HR Manager"
        assert next(c for c in snapshot["companies"] if c["id"] == created["company"]["id"])["website"] == "https://www.gulfbuild.com"
        audit = db.scalar(select(AuditEntry).where(AuditEntry.entity_type == "company", AuditEntry.action == "Created company"))
        assert audit.meta["changes"]["website"] == {"from": None, "to": "https://www.gulfbuild.com"}
        # The bootstrap payload carries both, in camelCase like the rest.
        payload = subject._bootstrap(db, db.get(User, "staff"))
    assert next(c for c in payload["clients"] if c["id"] == created["id"])["designation"] == "HR Manager"
    assert next(c for c in payload["companies"] if c["id"] == created["company"]["id"])["website"] == "https://www.gulfbuild.com"


def test_new_client_without_the_new_fields_leaves_them_empty(desk):
    created = post(desk, "clients", {"name": "Plain", "company": {"name": "Plain Co", "website": "  "}})
    assert created["designation"] is None and created["company"]["website"] is None
    legacy = post(desk, "clients", {"name": "Old payload"})
    assert legacy["designation"] is None


def test_designation_can_be_edited_cleared_and_is_audited(desk):
    client = post(desk, "clients", {"name": "Contact"})
    updated = desk.client.put("/api/clients/" + client["id"], json={"name": "Contact", "designation": "Director"})
    assert updated.status_code == 200 and updated.json()["designation"] == "Director"
    cleared = desk.client.put("/api/clients/" + client["id"], json={"name": "Contact", "designation": " "})
    assert cleared.status_code == 200 and cleared.json()["designation"] is None
    history = desk.client.get("/api/master-data/client/" + client["id"] + "/audit").json()
    assert history[1]["meta"]["changes"]["designation"] == {"from": None, "to": "Director"}
    assert history[0]["meta"]["changes"]["designation"] == {"from": "Director", "to": None}
    assert desk.client.put("/api/clients/" + client["id"], json={"name": "Contact", "designation": "x" * 121}).status_code == 422


def test_company_website_create_update_clear_and_validation(desk):
    client = post(desk, "clients", {"name": "Contact"})
    company = post(desk, "companies", {"name": "Employer", "clientId": client["id"], "website": "employer.example"})
    assert company["website"] == "https://employer.example"
    base = {"name": "Employer", "clientId": client["id"]}
    changed = desk.client.put("/api/companies/" + company["id"], json={**base, "website": "http://employer.example/jobs"})
    assert changed.status_code == 200 and changed.json()["website"] == "http://employer.example/jobs"
    history = desk.client.get("/api/master-data/company/" + company["id"] + "/audit").json()
    assert history[0]["meta"]["changes"]["website"] == {"from": "https://employer.example", "to": "http://employer.example/jobs"}
    for bad in ("javascript:alert(1)", "not a site", "ftp://employer.example", "x" * 300):
        assert desk.client.put("/api/companies/" + company["id"], json={**base, "website": bad}).status_code == 422, bad
        assert desk.client.post("/api/companies", json={**base, "website": bad}).status_code == 422, bad
        assert desk.client.post("/api/clients", json={"name": "N", "company": {"name": "C", "website": bad}}).status_code == 422, bad
    cleared = desk.client.put("/api/companies/" + company["id"], json={**base, "website": ""})
    assert cleared.status_code == 200 and cleared.json()["website"] is None
    with desk.sessions() as db:  # a rejected value leaves no client behind either
        assert db.scalar(select(Client).where(Client.name == "N")) is None


def test_migration_0025_adds_two_nullable_columns_keeps_rows_and_downgrades(schema_database):
    engine = schema_database
    _migrate(engine, "0024_lead_sheet")
    assert "designation" not in {c["name"] for c in inspect(engine).get_columns("crm_client_contacts")}
    assert "website" not in {c["name"] for c in inspect(engine).get_columns("crm_employer_companies")}
    with engine.begin() as c:
        c.execute(text("INSERT INTO crm_client_contacts (id, name, email) VALUES ('k1', 'Old contact', 'old@example.test')"))
        c.execute(text("INSERT INTO crm_employer_companies (id, client_id, name, contact_person) VALUES ('c1', 'k1', 'Old employer', 'Old contact')"))
    _migrate(engine, "0025_client_fields")
    clients = {c["name"]: c for c in inspect(engine).get_columns("crm_client_contacts")}
    companies = {c["name"]: c for c in inspect(engine).get_columns("crm_employer_companies")}
    assert clients["designation"]["nullable"] is True and clients["designation"]["type"].length == 120
    assert companies["website"]["nullable"] is True and companies["website"]["type"].length == 255
    assert clients["designation"]["comment"] and companies["website"]["comment"]
    with engine.connect() as c:
        assert c.execute(text("SELECT name, email, designation FROM crm_client_contacts WHERE id = 'k1'")).one() == ("Old contact", "old@example.test", None)
        assert c.execute(text("SELECT name, contact_person, website FROM crm_employer_companies WHERE id = 'c1'")).one() == ("Old employer", "Old contact", None)
    with engine.begin() as c:
        c.execute(text("UPDATE crm_client_contacts SET designation = 'CEO' WHERE id = 'k1'"))
        c.execute(text("UPDATE crm_employer_companies SET website = 'https://old.example' WHERE id = 'c1'"))
        c.execute(text("UPDATE alembic_version SET version_num = '0024_lead_sheet'"))
    _migrate(engine, "head")  # a repeat is harmless and keeps what was typed
    with engine.connect() as c:
        assert c.scalar(text("SELECT designation FROM crm_client_contacts WHERE id = 'k1'")) == "CEO"
        assert c.scalar(text("SELECT website FROM crm_employer_companies WHERE id = 'c1'")) == "https://old.example"
    _migrate(engine, "0024_lead_sheet", downgrade=True)
    assert "designation" not in {c["name"] for c in inspect(engine).get_columns("crm_client_contacts")}
    assert "website" not in {c["name"] for c in inspect(engine).get_columns("crm_employer_companies")}
    with engine.connect() as c:  # the rows themselves survive both ways
        assert c.scalar(text("SELECT count(*) FROM crm_client_contacts WHERE id = 'k1'")) == 1
        assert c.scalar(text("SELECT count(*) FROM crm_employer_companies WHERE id = 'c1'")) == 1
    _migrate(engine, "head")
    with engine.connect() as c:
        assert c.scalar(text("SELECT designation FROM crm_client_contacts WHERE id = 'k1'")) is None
