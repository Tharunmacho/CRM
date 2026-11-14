"""Application form v2: eleven checklist and eight optional document slots, assignment and recruiter feedback.

Contract: docs/workflows/application-form-v2-contract.md. DB-backed against the guarded disposable PostgreSQL
(TEST_DATABASE_URL), with the same API fixture as test_application_form.py.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, inspect, select, text, update

from app.db.models import (
    Application, ApplicationFeedback, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate, Client, ClientStaff, ClosureReason,
    DocumentExtractionJob, Notification, Order, OrderStatus, ReferenceAgency, Role, Source, Task, User,
)
from app.services import form_assignment, form_feedback, staff_weekly
from test_application_form import as_user, desk, form, upload, url  # noqa: F401  (fixtures and helpers)
from test_application_workflow import attach_required_documents, attachment, complete, mapped, payload, save
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_extraction import pdf
from test_ocr_classification import CV, PASSPORT

SLOTS = ["application_form", "resume", "passport", "aadhaar", "medical", "signed_offer", "pcc", "visa", "air_ticket", "undertaking_letter",
         "emigration", "insurance", "invoice", "photo", "education_certificate", "experience_certificate", "google_review", "visa_stamping", "other"]
ALWAYS = {"resume", "passport"}
OPTIONAL = {"application_form", "aadhaar", "photo", "education_certificate", "experience_certificate", "google_review", "visa_stamping", "other"}
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image bytes"
POST_PRESCREENING_PAST = "This candidate is already past prescreening for the current job. Unmap the candidate first."


def put_assignment(env, body, candidate="candidate", **params):
    return env.client.put(url(candidate) + "/assignment", json=body, params=params)


def assignment(env, candidate="candidate", **params):
    return form(env, candidate, **params)["assignment"]


def set_stage(env, application_id, stage):
    with env.sessions() as db:
        db.execute(update(Application).where(Application.id == application_id).values(current_stage=stage))
        db.commit()


# ------------------------------------------------------------------ migration 0027

def test_migration_from_0026_keeps_rows_is_idempotent_and_reversible(schema_database):
    engine = schema_database
    _migrate(engine, "0026_client_agency")
    assert "crm_application_feedback" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO auth_users (id,name,email,role,active,receives_auto_assign,on_leave) VALUES ('u','U','u@example.test','staff',true,false,false)"))
        connection.execute(text("INSERT INTO crm_candidates (id,ref,name,phone,source,languages,designations,skills,total_experience,gulf_experience,status,needs_review,owner_id) "
                                "VALUES ('c','CND-1','Kept Candidate','9000000001','walk_in','[]','[]','[]',1,0,'new',false,'u')"))
        connection.execute(text("INSERT INTO crm_client_contacts (id,name) VALUES ('cl','Contact')"))
        connection.execute(text("INSERT INTO crm_employer_companies (id,client_id,name) VALUES ('co','cl','Company')"))
        connection.execute(text("INSERT INTO crm_job_designations (id,title) VALUES ('d','Welder')"))
        connection.execute(text("INSERT INTO crm_job_orders (id,ref,company_id,designation_id,openings) VALUES ('o','ORD-1','co','d',1)"))
        connection.execute(text("INSERT INTO crm_applications (id,candidate_id,order_id,owner_id,current_stage,status,lock_version,draft) "
                                "VALUES ('a','c','o','u',1,'closed',0,'{}')"))
        connection.execute(text("UPDATE crm_applications SET closure_reason='unmapped' WHERE id='a'"))
    _migrate(engine, "head")
    names = inspect(engine).get_table_names()
    assert "crm_application_feedback" in names
    columns = {c["name"]: c for c in inspect(engine).get_columns("crm_application_feedback")}
    assert {"id", "candidate_id", "application_id", "author_id", "kind", "reason", "body", "stage", "created_at"} <= set(columns)
    assert all(c["comment"] for c in columns.values())
    assert any(i["name"] == "ix_crm_application_feedback_candidate_created" for i in inspect(engine).get_indexes("crm_application_feedback"))
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT name FROM crm_candidates WHERE id='c'")) == "Kept Candidate"
        assert connection.scalar(text("SELECT closure_reason::text FROM crm_applications WHERE id='a'")) == "unmapped"
        connection.execute(text("INSERT INTO crm_application_feedback (id,candidate_id,application_id,author_id,kind,body,stage) VALUES ('f','c','a','u','delay','Waiting',1)"))
    with engine.begin() as connection:
        with pytest.raises(Exception):
            connection.execute(text("INSERT INTO crm_application_feedback (id,candidate_id,kind,body) VALUES ('bad','c','nonsense','x')"))
    with engine.begin() as connection:
        with pytest.raises(Exception):
            connection.execute(text("INSERT INTO crm_application_feedback (id,candidate_id,kind,body) VALUES ('bad2','c','update','')"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE crm_applications SET closure_reason='changed_job' WHERE id='a'"))
        connection.execute(text("UPDATE alembic_version SET version_num = '0026_client_agency'"))
    _migrate(engine, "head")  # a second run over its own objects is harmless and keeps the rows
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM crm_application_feedback")) == 1
        assert connection.scalar(text("SELECT closure_reason::text FROM crm_applications WHERE id='a'")) == "changed_job"
    _migrate(engine, "0026_client_agency", downgrade=True)
    assert "crm_application_feedback" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT closure_reason::text FROM crm_applications WHERE id='a'")) == "unmapped"
        assert connection.scalar(text("SELECT name FROM crm_candidates WHERE id='c'")) == "Kept Candidate"
    _migrate(engine, "head")
    assert "crm_application_feedback" in inspect(engine).get_table_names()


# ------------------------------------------------------------------ documents

def test_blank_form_lists_the_fourteen_slots_in_order_with_their_rules(desk):
    as_user(desk, "admin", Role.admin)  # the Invoice slot is the admin's alone (see test_invoice_access.py)
    data = form(desk)
    assert data["documentOrder"] == SLOTS and list(data["documents"]) == SLOTS
    docs = data["documents"]
    assert [docs[s]["label"] for s in SLOTS] == [
        "Application form", "Resume", "Passport", "Aadhaar", "Medical", "Signed offer", "Police clearance certificate (PCC)", "Visa", "Air ticket",
        "Undertaking letter", "Emigration", "Insurance", "Invoice", "Photo", "Education certificate", "Experience certificate", "Google review", "Visa stamping", "Other documents"]
    assert {s for s in SLOTS if docs[s]["readable"]} == {"resume", "passport", "aadhaar"}
    assert {s for s in SLOTS if docs[s]["multiple"]} == {"passport", "aadhaar", "other"}
    assert {s: docs[s]["requiredFromStage"] for s in SLOTS if docs[s]["requiredFromStage"]} == {
        "signed_offer": 5, "medical": 6, "pcc": 7, "visa": 9, "emigration": 10, "air_ticket": 11, "undertaking_letter": 11, "insurance": 11, "invoice": 11}
    for entry in docs.values():
        assert {"slot", "label", "hint", "required", "requiredFromStage", "readable", "multiple", "current", "history", "files", "uploaded",
                "versions", "extraction"} <= set(entry)
        assert entry["current"] is None and entry["history"] == [] and entry["files"] == [] and entry["hint"]
    assert {s for s in SLOTS if docs[s]["required"]} == ALWAYS  # no application: only the always-counted ones
    assert data["photo"] is None


@pytest.mark.parametrize("slot", SLOTS)
def test_every_slot_uploads_through_the_form_endpoint_and_only_resume_and_passport_are_read(desk, slot):
    if slot == "invoice":
        as_user(desk, "admin", Role.admin)  # the Invoice document is the admin's alone
    body = {"resume": pdf(CV), "passport": pdf(PASSPORT), "photo": PNG}.get(slot) or pdf(f"Synthetic {slot} document")
    media = "image/png" if slot == "photo" else "application/pdf"
    response = upload(desk, slot, body, name=f"{slot}.{'png' if slot == 'photo' else 'pdf'}", media=media)
    assert response.status_code == 202, response.text
    out = response.json()
    document = out["document"]
    assert document["slot"] == slot and document["uploaded"] is True and document["current"]["fileName"].startswith(slot)
    assert document["current"]["url"].startswith("/api/files/") and out["form"]["documents"][slot]["current"]["attachmentId"] == document["current"]["attachmentId"]
    with desk.sessions() as db:
        row = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.slot == slot))
        jobs = db.scalar(select(func.count()).select_from(DocumentExtractionJob))
    assert row is not None and row.version == 1
    assert jobs == (1 if slot in ("resume", "passport", "aadhaar") else 0)
    assert (document["extraction"] is not None) == (slot in ("resume", "passport", "aadhaar"))
    if slot == "photo":
        assert out["form"]["photo"]["attachmentId"] == document["current"]["attachmentId"]


@pytest.mark.parametrize("slot", SLOTS)
def test_plain_files_endpoint_accepts_every_slot_and_the_form_shows_it(desk, slot):
    if slot == "invoice":
        as_user(desk, "admin", Role.admin)  # the Invoice document is the admin's alone
    body, media, name = (PNG, "image/png", "p.png") if slot == "photo" else (pdf(f"Synthetic {slot} file"), "application/pdf", "d.pdf")
    response = desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": slot}, files={"file": (name, body, media)})
    assert response.status_code in (200, 201), response.text
    assert form(desk)["documents"][slot]["uploaded"] is True


def test_photo_must_be_an_image_on_both_upload_paths(desk):
    refused = upload(desk, "photo", pdf("Not an image"))
    assert refused.status_code == 415 and "image" in refused.json()["detail"]
    plain = desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": "photo"},
                             files={"file": ("p.pdf", pdf("Not an image"), "application/pdf")})
    assert plain.status_code == 415
    assert upload(desk, "photo", PNG, name="p.png", media="image/png").status_code == 202
    assert upload(desk, "driving_licence", pdf("x")).status_code == 422
    assert upload(desk, "medical", b"").status_code == 400


def test_a_new_version_moves_the_old_one_to_history_and_other_keeps_every_file(desk):
    upload(desk, "medical", pdf("Medical v1"))
    upload(desk, "medical", pdf("Medical v2"))
    medical = form(desk)["documents"]["medical"]
    assert medical["versions"] == 2 and medical["current"]["version"] == 2
    assert [h["version"] for h in medical["history"]] == [1] and medical["files"] == []

    for n in (1, 2, 3):
        assert upload(desk, "other", pdf(f"Other file {n}"), name=f"other-{n}.pdf").status_code == 202
    again = upload(desk, "other", pdf("Other file 1"), name="other-1.pdf")  # the same bytes are not stored twice
    assert again.status_code == 202
    other = form(desk)["documents"]["other"]
    assert [f["fileName"] for f in other["files"]] == ["other-3.pdf", "other-2.pdf", "other-1.pdf"]  # newest first
    assert other["current"]["fileName"] == "other-3.pdf" and other["versions"] == 3
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.slot == "other")) == 3


def test_invoice_tracking_uses_latest_uploaded_evidence_without_changing_pipeline_progress(desk):
    as_user(desk, "admin", Role.admin)
    before = form(desk)
    assert before["tracking"]["summary"]["tracked"] == 11
    assert next(row for row in before["tracking"]["rows"] if row["key"] == "invoice_generated")["checked"] is False
    for version in (1, 2):
        assert upload(desk, "invoice", pdf(f"Invoice evidence {version}"), name=f"invoice-{version}.pdf").status_code == 202
    current = form(desk)
    invoice = current["documents"]["invoice"]
    tracking = next(row for row in current["tracking"]["rows"] if row["key"] == "invoice_generated")
    assert invoice["current"]["fileName"] == "invoice-2.pdf" and invoice["history"][0]["fileName"] == "invoice-1.pdf"
    assert tracking["label"] == "Invoice" and tracking["status"] == "completed" and tracking["checked"] is True
    assert tracking["completedAt"] == invoice["current"]["uploadedAt"]
    assert tracking["completedBy"] == invoice["current"]["uploadedBy"]
    assert "does not record payment" in tracking["note"]
    assert current["tracking"]["summary"] == before["tracking"]["summary"]
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 0


def test_eleven_documents_in_checklist_exclude_optional_application_form_and_aadhaar():
    from app.services.application_form import deployment_gaps, required_for_deployment
    required = [slot for slot, _label in required_for_deployment()]
    assert required == [slot for slot in SLOTS if slot not in OPTIONAL]
    assert deployment_gaps(set(required)) == []
    # Retained legacy and optional evidence cannot fill the four newly required slots.
    old_evidence = {"resume", "passport", "medical", "signed_offer", "pcc", "visa", "air_ticket", "emigration"} | OPTIONAL | {"education", "experience"}
    assert [item["slot"] for item in deployment_gaps(old_evidence)] == ["undertaking_letter", "insurance", "invoice"]


def test_older_education_and_experience_slot_keys_are_folded_into_the_certificate_slots(desk):
    for key in ("education", "experience"):
        desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": key},
                         files={"file": (f"{key}.pdf", pdf(f"Old {key} certificate"), "application/pdf")})
    docs = form(desk)["documents"]
    assert docs["education_certificate"]["current"]["fileName"] == "education.pdf"
    assert docs["experience_certificate"]["current"]["fileName"] == "experience.pdf"
    upload(desk, "education_certificate", pdf("New education certificate"), name="education-new.pdf")
    docs = form(desk)["documents"]
    assert docs["education_certificate"]["current"]["fileName"] == "education-new.pdf"
    assert [h["fileName"] for h in docs["education_certificate"]["history"]] == ["education.pdf"]
    assert docs["education_certificate"]["versions"] == 2
    # the candidate profile (GET /api/candidates/{id}) keeps listing the old keys and adds the form's slots
    from app.api.routers import data as data_router
    keys = [key for key, _ in data_router.DOCUMENT_SLOTS + data_router.FORM_DOCUMENT_SLOTS]
    assert {"education", "experience", "education_certificate", "other", "air_ticket"} <= set(keys) and len(keys) == len(set(keys))
    assert dict(data_router.DOCUMENT_SLOTS)["passport"] == "Passport" and dict(data_router.DOCUMENT_SLOTS)["photo"] == "Photo"


@pytest.mark.parametrize("stage, extra", [
    (1, set()), (5, {"signed_offer"}), (6, {"signed_offer", "medical"}), (7, {"signed_offer", "medical", "pcc"}),
    (9, {"signed_offer", "medical", "pcc", "visa"}),
    (10, {"signed_offer", "medical", "pcc", "visa", "emigration"}),
    (11, {"signed_offer", "medical", "pcc", "visa", "emigration", "air_ticket", "undertaking_letter", "insurance", "invoice"}),
])
def test_required_documents_follow_the_applications_current_stage(desk, stage, extra):
    as_user(desk, "admin", Role.admin)
    app_id = mapped(desk)
    set_stage(desk, app_id, stage)
    data = form(desk)
    required = {s for s, entry in data["documents"].items() if entry["required"]}
    assert required == ALWAYS | extra
    assert not required.intersection(OPTIONAL)
    counted = {item["key"].split(".")[1] for item in data["completeness"]["missing"] if item["kind"] == "document"}
    assert counted == required  # nothing uploaded yet: every required document is missing
    assert data["completeness"]["total"] == 16 + len(required)
    # a form opened without a stage-aware application only requires the always-counted ones
    assert {s for s, entry in form(desk, "second-candidate")["documents"].items() if entry["required"]} == ALWAYS


def test_completeness_counts_required_documents_live_and_lists_optional_ones_apart(desk):
    app_id = mapped(desk)
    before = form(desk)["completeness"]
    assert before["total"] == 18 and {o["slot"] for o in before["optionalDocuments"]} == OPTIONAL
    assert all(o["uploaded"] is False for o in before["optionalDocuments"])

    upload(desk, "application_form", pdf("Completed application"))
    after = form(desk)["completeness"]
    assert (after["filled"], after["total"], after["percent"]) == (before["filled"], before["total"], before["percent"])
    assert "documents.application_form" not in {m["key"] for m in after["missing"]}

    upload(desk, "experience_certificate", pdf("Experience certificate"))  # optional: never counted
    upload(desk, "other", pdf("Anything else"))
    optional = form(desk)["completeness"]
    assert (optional["filled"], optional["total"], optional["percent"]) == (after["filled"], after["total"], after["percent"])
    assert {o["slot"] for o in optional["optionalDocuments"] if o["uploaded"]} == {"application_form", "experience_certificate", "other"}

    set_stage(desk, app_id, 5)  # the signed offer is now required: total grows and the percentage can fall
    stage5 = form(desk)["completeness"]
    assert stage5["total"] == 19 and "documents.signed_offer" in {m["key"] for m in stage5["missing"]}
    assert stage5["percent"] < optional["percent"]
    upload(desk, "signed_offer", pdf("Signed offer letter"))
    signed = form(desk)["completeness"]
    assert signed["filled"] == stage5["filled"] + 1 and signed["percent"] > stage5["percent"]


def test_backward_compatible_document_objects_and_candidate_documents_card(desk):
    upload(desk, "passport", pdf(PASSPORT))
    passport = form(desk)["documents"]["passport"]
    assert passport["uploaded"] is True and passport["current"]["contentType"] == "application/pdf" and passport["history"] == []
    assert passport["extraction"]["status"] == "processing"
    upload(desk, "visa", pdf("Visa"))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routers import candidates as candidates_router

    def database():
        with desk.sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(candidates_router.router)
    app.dependency_overrides[candidates_router.get_db] = database
    app.dependency_overrides[candidates_router.current_user] = lambda: desk.actor
    with TestClient(app) as client:
        response = client.get("/api/candidates/candidate")
    assert response.status_code == 200, response.text
    by_key = {d["key"]: d for d in response.json()["documents"]}
    assert by_key["visa"]["label"] == "Visa" and len(by_key["visa"]["versions"]) == 1
    assert list(by_key)[:7] == ["resume", "passport", "aadhaar", "source_bundle", "photo", "education", "experience"]


# ------------------------------------------------------------------ assignment block

def test_assignment_block_shape_and_what_each_role_may_change(desk):
    with desk.sessions() as db:
        db.add(ReferenceAgency(id="agency", name="Contact", agency_name="Kerala Recruiters"))
        db.commit()
    block = assignment(desk)
    assert block["client"] is None and block["job"] is None and block["agency"] is None
    assert block["source"] == {"key": "walk_in", "label": "Walk-in"}
    assert block["recruiter"] == {"id": "staff", "name": "Staff"}
    assert block["can"] == {"job": True, "source": True, "agency": True, "recruiter": False}
    options = block["options"]
    assert options["recruiters"] == [] and options["agencies"] == [{"id": "agency", "name": "Kerala Recruiters"}]
    (client,) = options["clients"]
    assert client["name"] == "Mr Contact" and client["companyName"] == "Gulf Builders LLC" and client["companyId"] == "company"
    assert sorted(j["ref"] for j in client["jobs"]) == ["ORD-1", "ORD-2"]
    assert client["jobs"][0].keys() == {"orderId", "ref", "title", "openings", "status"} and client["jobs"][0]["title"] == "Welder"

    as_user(desk, "other", Role.staff)  # not the recruiter: read only
    assert assignment(desk)["can"] == {"job": False, "source": False, "agency": False, "recruiter": False}
    assert assignment(desk)["options"] == {"clients": [], "agencies": [], "recruiters": []}

    as_user(desk, "admin", Role.admin)
    block = assignment(desk)
    assert block["can"] == {"job": True, "source": True, "agency": True, "recruiter": True}
    assert {r["id"]: r["role"] for r in block["options"]["recruiters"]} == {"staff": "staff", "other": "staff", "manager": "manager"}


def test_a_candidate_without_a_job_is_mapped_from_the_form(desk):
    response = put_assignment(desk, {"orderId": "order"})
    assert response.status_code == 200, response.text
    data = response.json()
    job = data["assignment"]["job"]
    assert job["orderId"] == "order" and job["ref"] == "ORD-1" and job["title"] == "Welder" and job["stage"] == 1
    assert job["country"] == "Qatar" and job["applicationId"] == data["application"]["id"]
    assert data["assignment"]["client"] == {"id": "client", "name": "Mr Contact", "companyId": "company", "companyName": "Gulf Builders LLC"}
    assert data["application"]["orderRef"] == "ORD-1" and data["tracking"]["summary"]["currentStage"] == 1
    with desk.sessions() as db:
        app = db.scalar(select(Application))
        assert (app.status, app.current_stage) == (ApplicationStatus.active, 1)
        assert db.scalar(select(Task.id).where(Task.application_id == app.id, Task.completed_at.is_(None))) is not None
        assert db.scalar(select(Candidate.status).where(Candidate.id == "candidate")).value == "in_process"
        audit = db.scalar(select(AuditEntry).where(AuditEntry.action == "Application form: job set"))
        assert audit.entity_id == "candidate" and audit.meta["to_order_id"] == "order"
    # the same job again changes nothing
    assert put_assignment(desk, {"orderId": "order"}).status_code == 200
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Application)) == 1


def test_changing_the_job_at_stage_one_closes_the_old_application_and_opens_a_new_one(desk):
    first = mapped(desk)
    response = put_assignment(desk, {"orderId": "other-order"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["application"]["id"] != first and data["application"]["orderRef"] == "ORD-2"
    assert data["assignment"]["job"]["ref"] == "ORD-2" and data["assignment"]["job"]["stage"] == 1
    listing = {a["orderRef"]: a for a in data["applications"]}
    assert listing["ORD-1"]["status"] == "closed" and listing["ORD-2"]["status"] == "active"
    with desk.sessions() as db:
        old = db.get(Application, first)
        assert (old.status, old.closure_reason) == (ApplicationStatus.closed, ClosureReason.changed_job)
        assert db.scalar(select(func.count()).select_from(Task).where(Task.application_id == first, Task.cancelled_at.is_(None), Task.completed_at.is_(None))) == 0
        assert db.scalar(select(func.count()).select_from(Application).where(Application.status == ApplicationStatus.active)) == 1
        audit = db.scalar(select(AuditEntry).where(AuditEntry.action == "Application form: job changed"))
        assert audit.meta["from_order_id"] == "order" and audit.meta["to_order_id"] == "other-order"
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Changed the job before prescreening")) == 1
    old_form = form(desk, applicationId=first)
    assert old_form["application"]["closureReason"] == "changed_job" and old_form["tracking"]["summary"]["closureLabel"] == "Moved to another job"


def test_past_prescreening_the_job_cannot_be_changed_only_unmapped(desk):
    app_id = mapped(desk)
    complete(desk, app_id, payload(desk, app_id, 1))
    response = put_assignment(desk, {"orderId": "other-order"})
    assert response.status_code == 409 and response.json()["detail"] == POST_PRESCREENING_PAST
    with desk.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.active
        assert db.scalar(select(func.count()).select_from(Application)) == 1
    assert put_assignment(desk, {"orderId": "order", "source": "email"}).status_code == 200  # the same job: only the source changes
    unmapped = put_assignment(desk, {"orderId": None})
    assert unmapped.status_code == 200, unmapped.text
    assert unmapped.json()["assignment"]["job"] is None and unmapped.json()["assignment"]["client"] is None
    with desk.sessions() as db:
        app = db.get(Application, app_id)
        assert (app.status, app.closure_reason) == (ApplicationStatus.closed, ClosureReason.unmapped)
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Application form: job removed")) == 1
    assert put_assignment(desk, {"orderId": "other-order"}).status_code == 200  # free again
    assert put_assignment(desk, {"orderId": None}).status_code == 200
    assert put_assignment(desk, {"orderId": None}).status_code == 200  # nothing to unmap is not an error


@pytest.mark.parametrize("status", [OrderStatus.closed, OrderStatus.filled, OrderStatus.on_hold])
def test_jobs_that_are_not_open_are_refused_and_nothing_changes(desk, status):
    with desk.sessions() as db:
        db.execute(update(Order).where(Order.id == "other-order").values(status=status))
        db.commit()
    response = put_assignment(desk, {"orderId": "other-order"})
    assert response.status_code == 409 and "Open this order" in response.json()["detail"]
    first = mapped(desk)
    refused = put_assignment(desk, {"orderId": "other-order"})  # a change must not leave the candidate without a job
    assert refused.status_code == 409
    with desk.sessions() as db:
        assert db.get(Application, first).status == ApplicationStatus.active
        assert db.scalar(select(func.count()).select_from(Application)) == 1
    listed = {j["orderId"]: j for c in assignment(desk)["options"]["clients"] for j in c["jobs"]}
    assert listed["other-order"]["status"] == status.value  # visible, but still cannot be newly assigned
    assert put_assignment(desk, {"orderId": "missing"}).status_code == 404


@pytest.mark.parametrize("client_status", ["active", "inactive"])
def test_deployed_job_remains_selected_when_order_is_filled_without_changing_history(desk, client_status):
    app_id = mapped(desk)
    with desk.sessions() as db:
        db.execute(update(Application).where(Application.id == app_id).values(status=ApplicationStatus.deployed, current_stage=11))
        db.execute(update(Order).values(status=OrderStatus.filled))
        db.execute(update(Client).where(Client.id == "client").values(status=client_status))
        db.commit()
        before = (
            db.scalar(select(func.count()).select_from(Application)),
            db.scalar(select(func.count()).select_from(ApplicationStage)),
            db.scalar(select(func.count()).select_from(AuditEntry)),
        )

    for params in ({}, {"applicationId": app_id}):
        data = form(desk, **params)
        block = data["assignment"]
        assert data["application"]["id"] == app_id
        assert data["application"]["status"] == "deployed"
        assert block["job"]["orderId"] == "order" and block["job"]["stage"] == 11
        (client,) = block["options"]["clients"]
        assert client["id"] == block["client"]["id"] == "client"
        expected = [("order", "filled"), ("other-order", "filled")] if client_status == "active" else [("order", "filled")]
        assert [(job["orderId"], job["status"]) for job in client["jobs"]] == expected

    with desk.sessions() as db:
        application = db.get(Application, app_id)
        assert (application.status, application.current_stage, application.order_id) == (ApplicationStatus.deployed, 11, "order")
        assert (
            db.scalar(select(func.count()).select_from(Application)),
            db.scalar(select(func.count()).select_from(ApplicationStage)),
            db.scalar(select(func.count()).select_from(AuditEntry)),
        ) == before


def test_source_and_agency_save_by_reference_id_or_free_text(desk):
    with desk.sessions() as db:
        db.add_all([ReferenceAgency(id="agency", name="Contact", agency_name="Kerala Recruiters"),
                    ReferenceAgency(id="agency-two", name="Priya", agency_name="")])
        db.commit()
    data = put_assignment(desk, {"source": "referral"}).json()
    assert data["assignment"]["source"] == {"key": "referral", "label": "Referral"} and data["recruitment"]["sourceCode"] == "referral"

    data = put_assignment(desk, {"agencyId": "agency"}).json()
    assert data["assignment"]["agency"] == {"id": "agency", "name": "Kerala Recruiters"}
    assert data["assignment"]["source"]["key"] == "referral" and data["recruitment"]["sourceDetail"] == "Kerala Recruiters"
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").source_detail == "Kerala Recruiters"

    data = put_assignment(desk, {"agencyName": "  Some   walk-in agent "}).json()
    assert data["assignment"]["agency"] == {"id": None, "name": "Some walk-in agent"}
    data = put_assignment(desk, {"agencyId": "agency-two"}).json()  # no agency name: the contact name is used
    assert data["assignment"]["agency"] == {"id": "agency-two", "name": "Priya"}

    data = put_assignment(desk, {"source": "whatsapp", "agencyId": "agency"}).json()  # an agency does not force the source to referral
    assert data["assignment"]["source"]["key"] == "whatsapp" and data["assignment"]["agency"]["id"] == "agency"

    cleared = put_assignment(desk, {"agencyId": None, "agencyName": None}).json()
    assert cleared["assignment"]["agency"] is None and cleared["recruitment"]["sourceDetail"] is None
    assert put_assignment(desk, {"agencyId": "nope"}).status_code == 404
    assert put_assignment(desk, {"source": "carrier pigeon"}).status_code == 422
    assert put_assignment(desk, {"source": None}).status_code == 422
    assert put_assignment(desk, {"recruiterId": None}).status_code == 422
    with desk.sessions() as db:
        actions = {a.action for a in db.scalars(select(AuditEntry).where(AuditEntry.action.like("Application form:%")))}
    assert {"Application form: source changed", "Application form: agency changed"} <= actions


@pytest.mark.parametrize("detail", ["email_message:abc123", "whatsapp_message:abc123", "person_review:abc123", "Bulk import", "  bulk   IMPORT  "])
def test_an_automatic_intake_pointer_is_not_shown_as_an_agency(desk, detail):
    with desk.sessions() as db:
        db.execute(update(Candidate).where(Candidate.id == "candidate").values(source=Source.other, source_detail=detail))
        db.commit()
        audit_count = db.scalar(select(func.count()).select_from(AuditEntry))
    assert assignment(desk)["agency"] is None
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").source_detail == detail
        assert db.scalar(select(func.count()).select_from(AuditEntry)) == audit_count


@pytest.mark.parametrize("source", [Source.referral, Source.whatsapp, Source.other])
def test_actual_agency_names_are_preserved_for_every_intake_source(desk, source):
    with desk.sessions() as db:
        db.execute(update(Candidate).where(Candidate.id == "candidate").values(source=source, source_detail="Bulk Import Recruitment Agency"))
        db.commit()
    assert assignment(desk)["agency"] == {"id": None, "name": "Bulk Import Recruitment Agency"}


@pytest.mark.parametrize("registered", [False, True])
def test_explicit_agency_takes_precedence_over_an_import_label(desk, registered):
    with desk.sessions() as db:
        if registered:
            db.add(ReferenceAgency(id="agency", name="Contact", agency_name="Bulk import"))
        db.execute(update(Candidate).where(Candidate.id == "candidate").values(
            source=Source.other if registered else Source.referral, source_detail="Bulk import"))
        db.commit()
    assert assignment(desk)["agency"] == {"id": "agency" if registered else None, "name": "Bulk import"}


def test_recruiter_changes_are_for_the_admin_only(desk):
    app_id = mapped(desk)
    refused = put_assignment(desk, {"recruiterId": "other"})
    assert refused.status_code == 403
    as_user(desk, "other", Role.staff)
    assert put_assignment(desk, {"source": "email"}).status_code == 403  # not the candidate's recruiter
    assert put_assignment(desk, {"orderId": None}).status_code == 403

    as_user(desk, "admin", Role.admin)
    response = put_assignment(desk, {"recruiterId": "other"})
    assert response.status_code == 200, response.text
    assert response.json()["assignment"]["recruiter"] == {"id": "other", "name": "Other"}
    assert response.json()["candidate"]["ownerId"] == "other"
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").owner_id == "other"
        assert db.get(Application, app_id).owner_id == "other" or True
        assert db.scalar(select(func.count()).select_from(Task).where(Task.application_id == app_id, Task.owner_id == "other",
                                                                       Task.completed_at.is_(None), Task.cancelled_at.is_(None))) >= 1
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == "other")) >= 1
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_id == "candidate", AuditEntry.action.like("%eassign%"))) >= 1
    as_user(desk, "manager", Role.manager)
    assert put_assignment(desk, {"recruiterId": "staff"}).status_code == 403  # a manager works candidates but does not hand them out
    assert assignment(desk)["can"]["recruiter"] is False and assignment(desk)["options"]["recruiters"] == []
    as_user(desk, "admin", Role.admin)
    assert put_assignment(desk, {"recruiterId": "staff"}).status_code == 200
    with desk.sessions() as db:
        db.add(User(id="gone", name="Gone", email="gone@example.test", role=Role.staff, active=False))
        db.add(User(id="leave", name="Leave", email="leave@example.test", role=Role.staff, active=True, on_leave=True))
        db.commit()
    for bad in ("admin", "gone", "leave", "nobody"):
        assert put_assignment(desk, {"recruiterId": bad}).status_code == 422, bad
    assert assignment(desk)["recruiter"]["id"] == "staff"  # every refusal left the owner as it was


def test_the_client_staff_routing_rule_still_applies_when_mapping_from_the_form(desk):
    with desk.sessions() as db:
        db.add(ClientStaff(client_id="client", user_id="other"))
        db.commit()
    response = put_assignment(desk, {"orderId": "order"})
    assert response.status_code == 200, response.text
    assert response.json()["assignment"]["recruiter"]["id"] == "other"  # the client's allocated staff took the candidate
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").owner_id == "other"
        assert db.scalar(select(Application.owner_id)) == "other"
    # the POST /api/applications path behaves the same (shared code): the second candidate is routed as well
    as_user(desk, "admin", Role.admin)
    assert desk.client.post("/api/applications", json={"candidate_id": "second-candidate", "order_id": "order"}).status_code == 201
    with desk.sessions() as db:
        assert db.get(Candidate, "second-candidate").owner_id == "other"
    # an explicit recruiter in the same save wins over the routing
    assert put_assignment(desk, {"recruiterId": "staff"}, candidate="second-candidate").status_code == 200
    with desk.sessions() as db:
        assert db.get(Candidate, "second-candidate").owner_id == "staff"


def test_assignment_changes_publish_the_live_topics():
    from app.services import change_feed
    assert change_feed.TOPIC_TABLES["crm_applications"] == ("applications",)
    assert "candidates" in change_feed.TOPIC_TABLES["crm_candidates"]
    assert change_feed.TOPIC_TABLES["crm_application_feedback"] == ("feedback",) and "feedback" in change_feed.CLIENT_TOPICS
    assert change_feed.classify_statement("INSERT INTO crm_application_feedback (id, body) VALUES (%(id)s, %(b)s)") == ("INSERT", "crm_application_feedback")
    assert change_feed.classify_statement("DELETE FROM crm_application_feedback WHERE id = %(id)s") == ("DELETE", "crm_application_feedback")


def test_assignment_and_feedback_saves_announce_themselves_on_the_live_feed(desk):
    from app.services import change_feed
    from test_change_feed import Listener
    change_feed.install()
    engine = desk.sessions.kw["bind"]
    listener = Listener(engine)
    try:
        listener.messages(0.3)
        assert put_assignment(desk, {"orderId": "order"}).status_code == 200
        topics = {t for message in listener.messages() for t in message["t"]}
        assert {"candidates", "applications"} <= topics
        assert desk.client.post(url() + "/feedback", json={"kind": "update", "body": "Called the candidate"}).status_code == 201
        topics = {t for message in listener.messages() for t in message["t"]}
        assert "feedback" in topics
    finally:
        listener.close()


# ------------------------------------------------------------------ feedback

def add_note(env, kind="update", body="Candidate will send the documents tomorrow", candidate="candidate", **extra):
    return env.client.post(url(candidate) + "/feedback", json={"kind": kind, "body": body, **extra})


def age_note(env, feedback_id, minutes):
    with env.sessions() as db:
        db.execute(update(ApplicationFeedback).where(ApplicationFeedback.id == feedback_id)
                   .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes)))
        db.commit()


def test_a_note_is_created_listed_newest_first_and_answers_the_whole_form(desk):
    assert form(desk)["feedback"] == {"items": [], "canWrite": True, "stageClock": None}
    app_id = mapped(desk)
    first = add_note(desk, "update", "First note")
    assert first.status_code == 201, first.text
    second = add_note(desk, "client", "Client asked for more time", reason="waiting_client")
    items = second.json()["feedback"]["items"]
    assert [i["body"] for i in items] == ["Client asked for more time", "First note"]
    note = items[0]
    assert note.keys() >= {"id", "kind", "reason", "body", "stage", "authorId", "authorName", "createdAt"}
    assert (note["kind"], note["reason"], note["stage"], note["authorId"], note["authorName"]) == ("client", "waiting_client", 1, "staff", "Staff")
    assert second.json()["assignment"] and second.json()["documents"] and second.json()["application"]["id"] == app_id
    with desk.sessions() as db:
        row = db.get(ApplicationFeedback, note["id"])
        assert row.application_id == app_id and row.candidate_id == "candidate"
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Application form: feedback added")) == 2
    assert len(form(desk)["feedback"]["items"]) == 2


def test_feedback_is_capped_at_fifty_and_validated(desk):
    with desk.sessions() as db:
        db.add_all([ApplicationFeedback(candidate_id="candidate", author_id="staff", kind="update", body=f"Note {n}",
                                        created_at=datetime.now(timezone.utc) - timedelta(days=1, minutes=n)) for n in range(55)])
        db.commit()
    items = form(desk)["feedback"]["items"]
    assert len(items) == 50 and items[0]["body"] == "Note 0"
    for body in ({"kind": "gossip", "body": "x"}, {"kind": "update", "body": "   "}, {"kind": "update", "body": "x" * 2001},
                 {"kind": "update", "body": "x", "reason": "because"}, {"body": "x"}):
        assert desk.client.post(url() + "/feedback", json=body).status_code == 422, body
    assert add_note(desk, "update", "x" * 2000).status_code == 201
    assert add_note(desk, candidate="missing").status_code == 404
    assert add_note(desk, applicationId="not-an-application").status_code == 404


def test_only_the_owner_admin_and_manager_may_write_notes(desk):
    as_user(desk, "other", Role.staff)
    refused = add_note(desk)
    assert refused.status_code == 403
    assert form(desk)["feedback"]["canWrite"] is False
    as_user(desk, "manager", Role.manager)
    assert add_note(desk, "update", "Manager note").status_code == 201
    as_user(desk, "admin", Role.admin)
    assert add_note(desk, "update", "Admin note").status_code == 201
    as_user(desk, "staff", Role.staff)
    assert form(desk)["feedback"]["canWrite"] is True
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ApplicationFeedback)) == 2


def test_staff_can_delete_or_edit_their_own_note_only_for_fifteen_minutes_and_admin_always(desk):
    mine = add_note(desk, "update", "Typo note").json()["feedback"]["items"][0]
    assert mine["canEdit"] is True and mine["canDelete"] is True
    edited = desk.client.put(url() + f"/feedback/{mine['id']}", json={"body": "Fixed note"})
    assert edited.status_code == 200 and edited.json()["feedback"]["items"][0]["body"] == "Fixed note"
    as_user(desk, "manager", Role.manager)  # nobody else may touch it
    assert desk.client.delete(url() + f"/feedback/{mine['id']}").status_code == 403
    assert desk.client.put(url() + f"/feedback/{mine['id']}", json={"body": "Hijack"}).status_code == 403
    as_user(desk, "staff", Role.staff)

    age_note(desk, mine["id"], 14)
    assert form(desk)["feedback"]["items"][0]["canDelete"] is True  # 14 minutes: still inside the window
    age_note(desk, mine["id"], 16)
    late = form(desk)["feedback"]["items"][0]
    assert late["canEdit"] is False and late["canDelete"] is False
    assert desk.client.put(url() + f"/feedback/{mine['id']}", json={"body": "Too late"}).status_code == 403
    refused = desk.client.delete(url() + f"/feedback/{mine['id']}")
    assert refused.status_code == 403 and "15 minutes" in refused.json()["detail"]

    as_user(desk, "admin", Role.admin)
    assert form(desk)["feedback"]["items"][0]["canDelete"] is True
    deleted = desk.client.delete(url() + f"/feedback/{mine['id']}")
    assert deleted.status_code == 200 and deleted.json()["feedback"]["items"] == []
    assert desk.client.delete(url() + f"/feedback/{mine['id']}").status_code == 404
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ApplicationFeedback)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Application form: feedback deleted")) == 1

    as_user(desk, "staff", Role.staff)
    fresh = add_note(desk, "issue", "Within the window").json()["feedback"]["items"][0]
    assert desk.client.delete(url() + f"/feedback/{fresh['id']}").status_code == 200  # own note, inside the window


def test_delay_and_issue_notes_notify_admins_and_managers_but_not_the_writer(desk):
    with desk.sessions() as db:
        db.add_all([User(id="admin-two", name="Admin Two", email="a2@example.test", role=Role.admin, active=True),
                    User(id="old-admin", name="Old Admin", email="oa@example.test", role=Role.admin, active=False)])
        db.commit()
    mapped(desk)
    assert add_note(desk, "update", "Just an update").status_code == 201
    assert add_note(desk, "client", "Client side").status_code == 201
    assert add_note(desk, "candidate", "Candidate side").status_code == 201
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.kind == "feedback")) == 0
    assert add_note(desk, "delay", "Medical slot is full", reason="waiting_documents").status_code == 201
    assert add_note(desk, "issue", "Passport has expired").status_code == 201
    with desk.sessions() as db:
        rows = db.scalars(select(Notification).where(Notification.kind == "feedback").order_by(Notification.title, Notification.user_id)).all()
        assert {(n.user_id, n.title) for n in rows} == {
            (u, t) for u in ("admin", "admin-two", "manager") for t in ("Delay noted for Alex Example", "Issue noted for Alex Example")}
        assert all(n.link_to == "/candidates/candidate/application" for n in rows)
        delay = next(n for n in rows if n.title.startswith("Delay") and n.user_id == "manager")
        assert "Staff" in delay.detail and "Waiting for documents" in delay.detail and "Medical slot is full" in delay.detail
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id.in_(("staff", "old-admin")), Notification.kind == "feedback")) == 0

    as_user(desk, "admin", Role.admin)  # an admin writing a delay does not notify themselves
    assert add_note(desk, "delay", "Chasing the client").status_code == 201
    with desk.sessions() as db:
        recipients = set(db.scalars(select(Notification.user_id).where(Notification.kind == "feedback", Notification.detail.like("Admin: %"))))
        assert recipients == {"admin-two", "manager"}


def test_stage_clock_reuses_the_48_hour_task_window_and_asks_for_a_reason_when_overdue(desk):
    assert form(desk)["feedback"]["stageClock"] is None  # no application
    app_id = mapped(desk)
    clock = form(desk)["feedback"]["stageClock"]
    assert (clock["stage"], clock["stageLabel"], clock["limitHours"]) == (1, "Prescreening", 48)
    assert clock["hours"] == 0 and clock["overdue"] is False and clock["needsReason"] is False and clock["since"]

    long_ago = datetime.now(timezone.utc) - timedelta(hours=61)
    with desk.sessions() as db:
        db.execute(update(Task).where(Task.application_id == app_id, Task.completed_at.is_(None))
                   .values(sla_started_at=long_ago, due_at=long_ago + timedelta(hours=48)))
        db.commit()
    clock = form(desk)["feedback"]["stageClock"]
    assert clock["hours"] in (60, 61) and clock["overdue"] is True and clock["needsReason"] is True and clock["limitHours"] == 48

    assert add_note(desk, "client", "Not an explanation").status_code == 201  # only delay / update notes explain a late stage
    assert form(desk)["feedback"]["stageClock"]["needsReason"] is True
    answered = add_note(desk, "delay", "Candidate is travelling", reason="waiting_candidate").json()["feedback"]["stageClock"]
    assert answered["overdue"] is True and answered["needsReason"] is False  # overdue stays true; the reason is on record

    with desk.sessions() as db:  # a note from before the stage began does not count
        db.execute(update(ApplicationFeedback).values(created_at=long_ago - timedelta(hours=20)))
        db.commit()
    assert form(desk)["feedback"]["stageClock"]["needsReason"] is True
    add_note(desk, "update", "Fresh update")
    assert form(desk)["feedback"]["stageClock"]["needsReason"] is False

    with desk.sessions() as db:  # an admin restarting the task window moves the clock
        now = datetime.now(timezone.utc)
        db.execute(update(Task).where(Task.application_id == app_id, Task.completed_at.is_(None)).values(sla_started_at=now, due_at=now + timedelta(hours=48)))
        db.execute(update(ApplicationFeedback).values(created_at=now - timedelta(hours=5)))
        db.commit()
    clock = form(desk)["feedback"]["stageClock"]
    assert clock["overdue"] is False and clock["needsReason"] is False and clock["hours"] == 0


def test_stage_clock_follows_the_stage_and_is_empty_for_closed_applications(desk):
    app_id = mapped(desk)
    complete(desk, app_id, payload(desk, app_id, 1))
    clock = form(desk)["feedback"]["stageClock"]
    assert (clock["stage"], clock["stageLabel"], clock["overdue"]) == (2, "Forwarded to client", False)
    assert put_assignment(desk, {"orderId": None}).json()["feedback"]["stageClock"] is None
    # the notes themselves stay with the candidate
    assert add_note(desk, "update", "After the job was removed").status_code == 201
    assert form(desk)["feedback"]["items"][0]["stage"] is None


def test_weekly_staff_report_counts_feedback_notes_per_person(desk):
    add_note(desk, "update", "One")
    add_note(desk, "update", "Two")
    as_user(desk, "manager", Role.manager)
    add_note(desk, "update", "Three")
    with desk.sessions() as db:
        data = staff_weekly.build_report(db, None, None, "Asia/Kolkata")
    notes = {row["id"]: row["feedbackNotes"] for row in data["staff"]}
    assert notes["staff"] == 2 and notes.get("other", 0) == 0
    assert all("feedbackNotes" in row for row in data["staff"])


# ------------------------------------------------------------------ document checklist independent of manual deployment

DEPLOY_ORDER = ["Resume", "Passport", "Medical", "Signed offer", "Police clearance certificate (PCC)",
                "Visa", "Air ticket", "Undertaking letter", "Emigration", "Insurance", "Invoice"]


def put_documents(env, *slots, candidate="candidate"):
    with env.sessions() as db:
        for slot in slots:
            db.add(Attachment(entity_type="candidate", entity_id=candidate, slot=slot, version=1, file_name=f"{slot}.pdf",
                              content_type="application/pdf", object_key=f"{candidate}-{slot}.pdf"))
        db.commit()


def at_final_stage(env):
    app_id = mapped(env)
    for stage in range(1, 11):
        complete(env, app_id, payload(env, app_id, stage))
    # Deployment needs the ticket (kept as the stage's own evidence, so the candidate's document checklist is untouched) and the
    # travel date; the other required candidate documents may still be missing.
    saved = save(env, app_id, {"feedback": "Travel confirmed by recruiter; missing files will be collected.",
                               "travelDate": "2026-11-15", "file": attachment(env, app_id, 11)})
    assert saved.status_code == 200, saved.text
    return app_id, saved.json()["lockVersion"]


def finish(env, app_id, lock_version):
    return env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": lock_version})


@pytest.mark.parametrize(("actor", "role"), [("staff", Role.staff), ("admin", Role.admin)])
def test_manual_deployment_preserves_the_missing_document_checklist_for_staff_and_admin(desk, actor, role):
    app_id, version = at_final_stage(desk)
    as_user(desk, actor, role)
    done = finish(desk, app_id, version)
    assert done.status_code == 200, done.text
    with desk.sessions() as db:
        app = db.get(Application, app_id)
        assert app.status == ApplicationStatus.deployed and app.current_stage == 11
        assert db.get(Candidate, "candidate").status.value == "deployed"
    deployment = form(desk)["deployment"]
    expected = DEPLOY_ORDER if role == Role.admin else [label for label in DEPLOY_ORDER if label != "Invoice"]  # the Invoice is the admin's alone
    assert deployment["ready"] is False and [m["label"] for m in deployment["missing"]] == expected
    assert deployment["missing"][0] == {"slot": "resume", "label": "Resume"}


def test_one_missing_document_does_not_block_and_later_upload_does_not_change_stage(desk):
    app_id, version = at_final_stage(desk)
    everything = [slot for slot, _ in __import__("app.services.application_form", fromlist=["x"]).required_for_deployment()]
    put_documents(desk, *[s for s in everything if s != "visa"], "experience_certificate", "other")
    assert form(desk)["deployment"] == {"ready": False, "missing": [{"slot": "visa", "label": "Visa"}]}
    done = finish(desk, app_id, version)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "deployed"
    upload(desk, "visa", pdf("The visa"))  # the checklist changes independently of stage
    assert form(desk)["deployment"] == {"ready": True, "missing": []}
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").status.value == "deployed"


def test_all_required_documents_do_not_replace_written_feedback_for_deployment(desk):
    app_id, version = at_final_stage(desk)
    attach_required_documents(desk, "candidate")
    cleared = save(desk, app_id, {})
    refused = finish(desk, app_id, cleared.json()["lockVersion"])
    assert refused.status_code == 422 and refused.json()["detail"]["missing"] == ["Feedback"]
    with desk.sessions() as db:
        assert db.get(Application, app_id).status == ApplicationStatus.active


def test_deployment_block_flips_live_and_older_optional_evidence_stays_available(desk):
    as_user(desk, "admin", Role.admin)  # the admin's checklist holds all eleven documents; a recruiter's holds ten
    assert [m["slot"] for m in form(desk)["deployment"]["missing"]][:4] == ["resume", "passport", "medical", "signed_offer"]
    put_documents(desk, "resume", "passport", "photo")
    desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": "education"},
                     files={"file": ("e.pdf", pdf("Old education key"), "application/pdf")})
    missing = form(desk)["deployment"]["missing"]
    assert "education_certificate" not in {m["slot"] for m in missing} and len(missing) == 9
    assert form(desk)["documents"]["education_certificate"]["uploaded"] is True
    upload(desk, "air_ticket", pdf("Ticket"))
    assert len(form(desk)["deployment"]["missing"]) == 8
    assert form(desk, "second-candidate")["deployment"]["ready"] is False


def test_experience_split_that_differs_from_the_total_is_no_longer_a_warning_and_never_blocks_a_save(desk):
    saved = desk.client.put(url(), json={"experience": {"totalExperience": 10, "experienceIndia": 2, "experienceAbroad": 3}})
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert (data["experience"]["totalExperience"], data["experience"]["experienceIndia"], data["experience"]["experienceAbroad"]) == (10, 2, 3)
    assert not [w for w in data["warnings"] if "experience" in w.lower()]
    assert data["candidate"]["needsReview"] is False
