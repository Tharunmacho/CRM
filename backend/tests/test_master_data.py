"""Master-data contracts against the guarded disposable PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate, Client, Company, Designation, Order, Role, Source, Task, TaskKind, User
from test_database_naming import _migrate, schema_database

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import data as subject


@pytest.fixture
def desk(schema_database):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, expire_on_commit=False, autoflush=False)
    with sessions() as db:
        db.add_all([User(id=role, name=role.title(), email=role + "@example.test", role=Role(role), active=True)
                    for role in ("admin", "manager", "staff")])
        db.commit()
    env = SimpleNamespace(sessions=sessions, actor=SimpleNamespace(id="staff", role=Role.staff))
    app = FastAPI()
    app.include_router(subject.router)
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: env.actor
    with TestClient(app) as client:
        env.client = client
        yield env


def post(env, path, payload):
    result = env.client.post("/api/" + path, json=payload)
    assert result.status_code == 201, result.text
    return result.json()


def setup_order(env):
    contact = post(env, "clients", {"name": "Synthetic contact"})
    company = post(env, "companies", {"name": "Synthetic employer", "clientId": contact["id"]})
    designation = post(env, "designations", {"title": "Welder"})
    payload = {"companyId": company["id"], "designationId": designation["id"], "openings": 2,
               "salary": 1200.50, "currency": "AED", "interviewDate": "2026-11-02", "deadline": "2026-11-04"}
    order = post(env, "orders", payload)
    return contact, company, designation, order, payload


def test_staff_can_create_edit_and_audit_master_records(desk):
    contact = post(desk, "clients", {"name": " Contact ", "email": "CONTACT@EXAMPLE.TEST"})
    assert contact["name"] == "Contact" and contact["email"] == "contact@example.test"
    changed = desk.client.put("/api/clients/" + contact["id"], json={"name": "Changed contact", "status": "inactive"})
    assert changed.status_code == 200 and changed.json()["status"] == "inactive"
    history = desk.client.get("/api/master-data/client/" + contact["id"] + "/audit").json()
    assert [item["action"] for item in history] == ["Updated client", "Created client"]
    assert all(item["actorId"] == "staff" for item in history)
    assert history[0]["meta"]["changes"]["name"] == {"from": "Contact", "to": "Changed contact"}
    desk.client.put("/api/clients/" + contact["id"], json={"name": "Changed contact"})
    company = post(desk, "companies", {"name": "Employer", "clientId": contact["id"], "contactEmail": "EMPLOYER@EXAMPLE.TEST"})
    result = desk.client.put("/api/companies/" + company["id"], json={"name": "Employer 2", "clientId": contact["id"], "notes": "Saved terms", "status": "inactive"})
    assert result.status_code == 200 and result.json()["notes"] == "Saved terms"
    with desk.sessions() as db:
        assert db.get(Company, company["id"]).status == "inactive"
        assert db.scalar(select(AuditEntry).where(AuditEntry.entity_type == "company", AuditEntry.action == "Created company")).entity_id == company["id"]


def test_company_whatsapp_roundtrip_is_independent_from_phone(desk):
    contact = post(desk, "clients", {"name": "Contact", "whatsapp": "+919000000001"})
    payload = {"name": "Employer", "clientId": contact["id"], "phone": "+97140000001", "whatsapp": " +97150000001 "}
    company = post(desk, "companies", payload)
    assert company["phone"] == payload["phone"] and company["whatsapp"] == "+97150000001"
    updated = desk.client.put("/api/companies/" + company["id"], json={**payload, "whatsapp": "+97150000002"})
    assert updated.status_code == 200 and updated.json()["whatsapp"] == "+97150000002"
    with desk.sessions() as db:
        stored = db.get(Company, company["id"])
        assert stored.phone == payload["phone"] and stored.whatsapp == "+97150000002"
        snapshot = subject.read_snapshot(db, "staff")
        assert next(item for item in snapshot["companies"] if item["id"] == company["id"])["whatsapp"] == "+97150000002"
        assert db.get(Client, contact["id"]).whatsapp == "+919000000001"
    invalid = desk.client.put("/api/companies/" + company["id"], json={**payload, "whatsapp": "1" * 41})
    assert invalid.status_code == 422
    cleared = desk.client.put("/api/companies/" + company["id"], json={**payload, "whatsapp": None})
    assert cleared.status_code == 200 and cleared.json()["whatsapp"] is None


def test_new_client_form_saves_contact_and_employer_together(desk):
    payload = {"name": "Khalid Rahman", "email": "KHALID@EXAMPLE.TEST", "phone": "+97140000001", "whatsapp": "+97150000001",
               "country": "UAE", "city": "Dubai", "notes": "Prefers calls",
               "company": {"name": "Gulf Build LLC", "industry": "Construction", "country": "UAE", "city": "Dubai", "address": "Plot 4, Al Quoz"}}
    created = post(desk, "clients", payload)
    company = created["company"]
    assert created["name"] == "Khalid Rahman" and "company" in created
    assert company["clientId"] == created["id"] and company["name"] == "Gulf Build LLC"
    assert company["industry"] == "Construction" and company["address"] == "Plot 4, Al Quoz"
    assert company["contactPerson"] == "Khalid Rahman" and company["contactEmail"] == "khalid@example.test"
    assert company["phone"] == "+97140000001" and company["whatsapp"] == "+97150000001" and company["notes"] == "Prefers calls"
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action.in_(["Created client", "Created company"]))) == 2
    rejected = desk.client.put("/api/clients/" + created["id"], json={**payload})
    assert rejected.status_code == 422


def test_failed_employer_save_leaves_no_client_behind(desk, monkeypatch):
    original = subject._master_save
    def fail_on_company(db, user, row, body, kind, *args, **kwargs):
        if kind == "company":
            raise subject.HTTPException(409, "A related record changed while saving. Refresh and try again.")
        return original(db, user, row, body, kind, *args, **kwargs)
    monkeypatch.setattr(subject, "_master_save", fail_on_company)
    response = desk.client.post("/api/clients", json={"name": "Orphan", "company": {"name": "Never saved"}})
    assert response.status_code == 409
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Client).where(Client.name == "Orphan")) == 0


@pytest.mark.parametrize("path,payload", [
    ("clients", {"name": "   "}),
    ("clients", {"name": "Valid", "company": {"name": "  "}}), ("clients", {"name": "Valid", "email": 123}),
    ("clients", {"name": "Valid", "email": "invalid"}), ("clients", {"name": "Valid", "status": "archived"}),
    ("designations", {"title": "Welder", "minExperience": -1}),
    ("orders", {"companyId": "x", "designationId": "y", "openings": 0}),
    ("orders", {"companyId": "x", "designationId": "y", "salary": -1}),
    ("orders", {"companyId": "x", "designationId": "y", "salary": 1.123}),
    ("orders", {"companyId": "x", "designationId": "y", "interviewDate": "2026-02-30"}),
    ("orders", {"companyId": "x", "designationId": "y", "interviewDate": "2026-12-01", "deadline": "2026-11-01"}),
])
def test_invalid_master_fields_return_422(desk, path, payload):
    response = desk.client.post("/api/" + path, json=payload)
    assert response.status_code == 422, response.text


def test_missing_and_inactive_parents_are_explained(desk):
    assert desk.client.post("/api/companies", json={"name": "Employer", "clientId": "missing"}).status_code == 404
    contact = post(desk, "clients", {"name": "Inactive", "status": "inactive"})
    assert desk.client.post("/api/companies", json={"name": "Employer", "clientId": contact["id"]}).status_code == 409
    desk.client.put("/api/clients/" + contact["id"], json={"name": "Active"})
    company = post(desk, "companies", {"name": "Employer", "clientId": contact["id"]})
    designation = post(desk, "designations", {"title": "Welder"})
    desk.client.put("/api/clients/" + contact["id"], json={"name": "Inactive", "status": "inactive"})
    assert desk.client.post("/api/orders", json={"companyId": company["id"], "designationId": designation["id"]}).status_code == 409
    assert desk.client.put("/api/clients/missing", json={"name": "Missing"}).status_code == 404


def test_designation_skills_normalize_edits_and_deletion_is_protected(desk):
    item = post(desk, "designations", {"title": " Welder ", "requiredSkills": [" TIG ", "tig", "", "MIG"]})
    assert item["requiredSkills"] == ["TIG", "MIG"]
    result = desk.client.put("/api/designations/" + item["id"], json={"title": "Senior welder", "minExperience": 5, "requiredSkills": ["TIG"]})
    assert result.status_code == 200 and result.json()["minExperience"] == 5
    assert desk.client.delete("/api/designations/" + item["id"]).status_code == 403
    desk.actor = SimpleNamespace(id="manager", role=Role.manager)
    assert desk.client.delete("/api/designations/" + item["id"]).status_code == 204
    _, _, designation, _, _ = setup_order(desk)
    assert desk.client.delete("/api/designations/" + designation["id"]).status_code == 409


def test_order_edit_preserves_identity_and_records_changed_terms(desk):
    _, _, _, order, payload = setup_order(desk)
    response = desk.client.put("/api/orders/" + order["id"], json={**payload, "salary": 1500, "status": "on_hold", "benefits": [" Travel ", "travel"]})
    assert response.status_code == 200, response.text
    assert response.json()["ref"] == order["ref"]
    assert response.json()["benefits"] == ["Travel"]
    history = desk.client.get("/api/master-data/order/" + order["id"] + "/audit").json()
    assert history[0]["meta"]["changes"]["salary"]["to"] == 1500
    with desk.sessions() as db:
        assert float(db.get(Order, order["id"]).salary) == 1500


def test_order_history_and_deployment_status_guards(desk):
    _, _, designation, order, payload = setup_order(desk)
    second = post(desk, "designations", {"title": "Electrician"})
    with desk.sessions() as db:
        db.add(Candidate(id="candidate", ref="C-TEST", name="Synthetic candidate", phone="123456", source=Source.walk_in, owner_id="staff"))
        db.flush()
        db.add(Application(id="application", candidate_id="candidate", order_id=order["id"], owner_id="staff", status=ApplicationStatus.active))
        db.commit()
    path = "/api/orders/" + order["id"]
    assert desk.client.put(path, json={**payload, "designationId": second["id"]}).status_code == 409
    assert desk.client.put(path, json={**payload, "status": "closed"}).status_code == 409
    assert desk.client.put(path, json={**payload, "status": "filled"}).status_code == 409
    assert desk.client.put(path, json={**payload, "status": "on_hold"}).status_code == 200
    with desk.sessions() as db:
        db.get(Application, "application").status = ApplicationStatus.deployed
        db.commit()
    assert desk.client.put(path, json={**payload, "openings": 1, "status": "filled"}).status_code == 200
    assert desk.client.put(path, json={**payload, "openings": 1, "status": "open"}).status_code == 409
    assert desk.client.put(path, json={**payload, "openings": 2, "status": "open"}).status_code == 200
    with desk.sessions() as db:
        assert db.get(Application, "application").order_id == order["id"]


def test_order_references_are_atomic_and_not_reused_after_deletion(desk):
    _, _, _, first, payload = setup_order(desk)
    def create(_):
        return post(desk, "orders", payload)["ref"]
    with ThreadPoolExecutor(max_workers=4) as workers:
        refs = list(workers.map(create, range(8)))
    assert len(set(refs)) == 8 and first["ref"] not in refs
    highest = max(int(ref.rsplit("-", 1)[1]) for ref in refs)
    with desk.sessions() as db:
        last = db.scalar(select(Order).where(Order.ref.in_(refs)).order_by(Order.ref.desc()).limit(1))
        db.delete(last); db.commit()
    created = post(desk, "orders", payload)
    assert int(created["ref"].rsplit("-", 1)[1]) > highest


def test_failed_commit_rolls_back_record_and_audit(desk, monkeypatch):
    def fail(_):
        raise SQLAlchemyError("Synthetic database failure")
    monkeypatch.setattr(Session, "commit", fail)
    result = desk.client.post("/api/clients", json={"name": "Must not persist"})
    assert result.status_code == 503
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Client)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEntry)) == 0


@pytest.mark.parametrize('kind', ['clients', 'companies', 'orders'])
def test_deletion_retains_closed_application_history_and_documents(desk, kind):
    contact, company, _, order, _ = setup_order(desk)
    with desk.sessions() as db:
        db.add(Candidate(id='retained-candidate', ref='RETAINED', name='Candidate', phone='9000000001', source=Source.walk_in, owner_id='staff'))
        db.flush()
        db.add(Application(id='retained-application', candidate_id='retained-candidate', order_id=order['id'],
                           status=ApplicationStatus.closed, closure_reason='unmapped'))
        db.flush()
        db.add_all([
            ApplicationStage(application_id='retained-application', stage=1, attempt=1, data={'result': 'passed'}, outcome='Passed'),
            Attachment(entity_type='application', entity_id='retained-application', slot='stage_5_file', version=1,
                       file_name='offer.pdf', object_key='retained/offer.pdf'),
        ])
        db.commit()
    desk.actor = SimpleNamespace(id='admin', role=Role.admin)
    identifier = {'clients': contact['id'], 'companies': company['id'], 'orders': order['id']}[kind]
    response = desk.client.delete(f'/api/{kind}/{identifier}')
    assert response.status_code == 409, response.text
    assert 'history' in response.text and 'inactive' in response.text
    with desk.sessions() as db:
        assert db.get(Client, contact['id']) and db.get(Company, company['id']) and db.get(Order, order['id'])
        assert db.get(Application, 'retained-application')
        assert db.scalar(select(ApplicationStage.id).where(ApplicationStage.application_id == 'retained-application'))
        assert db.scalar(select(Attachment.entity_id)) == 'retained-application'
        assert not db.scalar(select(AuditEntry.id).where(AuditEntry.action.like('Deleted %')))


@pytest.mark.parametrize('kind,document_kind', [('clients', 'client'), ('clients', 'company'), ('companies', 'company')])
def test_deletion_retains_parent_and_nested_company_documents(desk, kind, document_kind):
    contact, company, _, order, _ = setup_order(desk)
    with desk.sessions() as db:
        db.add(Attachment(entity_type=document_kind, entity_id=contact['id'] if document_kind == 'client' else company['id'],
                          slot='agreement', version=1, file_name='agreement.pdf', object_key='retained/agreement.pdf'))
        db.commit()
    desk.actor = SimpleNamespace(id='admin', role=Role.admin)
    response = desk.client.delete(f"/api/{kind}/{contact['id'] if kind == 'clients' else company['id']}")
    assert response.status_code == 409 and 'documents' in response.text
    with desk.sessions() as db:
        assert db.get(Client, contact['id']) and db.get(Company, company['id']) and db.get(Order, order['id'])
        assert db.scalar(select(func.count()).select_from(Attachment)) == 1


def test_deleting_unused_client_still_removes_empty_descendants(desk):
    contact, company, _, order, _ = setup_order(desk)
    desk.actor = SimpleNamespace(id='admin', role=Role.admin)
    response = desk.client.delete(f"/api/clients/{contact['id']}")
    assert response.status_code == 200, response.text
    assert response.json()['deleted'] == {'clients': 1, 'companies': 1, 'orders': 1}
    with desk.sessions() as db:
        assert not db.get(Client, contact['id']) and not db.get(Company, company['id']) and not db.get(Order, order['id'])


@pytest.mark.parametrize('kind', ['client', 'company'])
def test_parent_deletion_rechecks_membership_after_concurrent_order_move(desk, kind):
    contact, company, _, order, _ = setup_order(desk)
    other_contact = post(desk, 'clients', {'name': 'Retained client'})
    other_company = post(desk, 'companies', {'name': 'Retained company', 'clientId': other_contact['id']})
    started = Event()
    waiting_pid = []

    def remove_parent():
        with desk.sessions() as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            waiting_pid.append(db.scalar(select(func.pg_backend_pid())))
            started.set()
            delete = subject.delete_client if kind == 'client' else subject.delete_company
            return delete(contact['id'] if kind == 'client' else company['id'], db, SimpleNamespace(id='admin', role=Role.admin))

    with desk.sessions() as moving, ThreadPoolExecutor(max_workers=1) as pool:
        row = moving.scalar(select(Order).where(Order.id == order['id']).with_for_update())
        row.company_id = other_company['id']
        moving.flush()
        future = pool.submit(remove_parent)
        try:
            assert started.wait(5)
            # Confirm the delete really reached the locked order before releasing
            # the move. This exercises PostgreSQL's predicate recheck after wait.
            with moving.bind.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    state = observer.scalar(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid'), {'pid': waiting_pid[0]})
                    if state == 'Lock':
                        break
                    time.sleep(0.02)
                else:
                    pytest.fail('Deletion did not wait on the concurrently moved order')
            moving.commit()
            assert future.result(timeout=5)['deleted']['orders'] == 0
        finally:
            moving.rollback()
    with desk.sessions() as db:
        assert db.get(Order, order['id']).company_id == other_company['id']
        assert db.get(Client, other_contact['id']) and db.get(Company, other_company['id'])
        assert not db.get(Company, company['id'])


def test_unreachable_call_retains_open_task_until_interest_is_confirmed(desk):
    with desk.sessions() as db:
        db.add(Candidate(id='called-candidate', ref='CALLED', name='Candidate', phone='9000000001', source=Source.walk_in, owner_id='staff'))
        db.flush()
        db.add(Task(id='initial-call', kind=TaskKind.initial_call, title='Confirm interest', candidate_id='called-candidate', owner_id='staff'))
        db.commit()
    response = desk.client.post('/api/candidates/called-candidate/call', json={'called': True, 'interested': 'unreachable', 'remarks': 'Rang without answer'})
    assert response.status_code == 200, response.text
    with desk.sessions() as db:
        candidate = db.get(Candidate, 'called-candidate')
        assert candidate.call_called and candidate.call_interested == 'unreachable'
        assert candidate.call_remarks == 'Rang without answer' and candidate.call_recorded_at
        assert db.get(Task, 'initial-call').completed_at is None
    response = desk.client.post('/api/candidates/called-candidate/call', json={'called': True, 'interested': 'yes'})
    assert response.status_code == 200, response.text
    with desk.sessions() as db:
        assert db.get(Task, 'initial-call').completed_at is not None
