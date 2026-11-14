"""Invoices, the agreed fee, CGST/SGST and receipts belong to the admin alone.

A recruiter (role staff) or a manager gets a calm 403 from every invoice endpoint and finds no invoice, fee, tax figure,
invoice number or invoice file link in any other payload; the automatic invoice after an air-ticket upload keeps working
whoever uploads the ticket. Disposable PostgreSQL and synthetic documents only.
"""
import json

import pytest
from sqlalchemy import select

from app.api.routers import candidates, invoice_payments
from app.db.models import Attachment, CandidateInvoice, Role
from app.services import application_form, candidate_exports, invoice_payments as payments_service
from app.services.invoice_access import INVOICE_DENIED
from test_application_form import as_user, desk, form, upload, url
from test_application_workflow import mapped
from test_candidate_invoices import admin, billing, configure, fee, read
from test_database_naming import schema_database
from test_document_extraction import pdf

DENIED = "Only the admin can see or change invoices."
OUTSIDERS = (("staff", Role.staff), ("other", Role.staff), ("manager", Role.manager))


@pytest.fixture
def access(billing, monkeypatch):
    billing.client.app.include_router(invoice_payments.router)
    billing.client.app.include_router(candidates.router)
    monkeypatch.setattr(payments_service, "get_settings", lambda: billing.settings)
    return billing


def issued(env):
    """An invoice issued after the owning recruiter uploads the ticket; the admin entered the fee."""
    app = mapped(env)
    configure(env)
    fee(env, "10000", app=app)
    upload(env, "air_ticket", pdf("Synthetic ticket"))
    data = read(env, app=app)
    assert data["status"] == "issued" and data["invoice"]["number"] == "CI/26-27/001"
    return app, data


def test_the_message_is_one_calm_sentence():
    assert INVOICE_DENIED == DENIED


def test_every_invoice_endpoint_answers_403_to_recruiters_and_managers_and_works_for_the_admin(access):
    app, data = issued(access)
    as_user(access, "admin", Role.admin)
    receipt = {"amount": "500.00", "date": "2026-01-02", "method": "cash", "reference": "R1", "note": "", "idempotencyKey": "receipt-0001"}
    recorded = access.client.post("/api/candidates/candidate/invoice/payments", json=receipt)
    assert recorded.status_code == 200, recorded.text
    payment_id = recorded.json()["payments"][0]["id"]
    settings = access.client.get("/api/invoice-settings").json()
    fee_body = {"serviceFee": "10000.00", "applicationId": app, "lockVersion": data["lockVersion"]}
    calls = [
        ("GET", "/api/candidates/candidate/invoice", None),
        ("PUT", "/api/candidates/candidate/invoice", fee_body),
        ("POST", "/api/candidates/candidate/invoice/reconcile", {"applicationId": app}),
        ("GET", "/api/invoice-settings", None),
        ("PUT", "/api/invoice-settings", {**settings["settings"], "lockVersion": settings["lockVersion"]}),
        ("GET", "/api/candidates/candidate/invoice/payments", None),
        ("POST", "/api/candidates/candidate/invoice/payments", {**receipt, "idempotencyKey": "receipt-0002"}),
        ("POST", f"/api/candidates/candidate/invoice/payments/{payment_id}/void", {"reason": "Entered in error"}),
        ("GET", "/api/invoices/finance-summary", None),
    ]
    for user_id, role in OUTSIDERS:
        as_user(access, user_id, role)
        for method, path, body in calls:
            response = access.client.request(method, path, json=body)
            assert response.status_code == 403, (user_id, method, path, response.text)
            assert response.json()["detail"] == DENIED, (user_id, method, path)
            # No fee, tax or number rides along with the refusal.
            assert "CI/26-27" not in response.text and "1800" not in response.text
    as_user(access, "admin", Role.admin)
    for method, path, body in calls[:6]:
        assert access.client.request(method, path, json=body).status_code == 200, (method, path)
    # Nothing was changed by the refused calls.
    with access.sessions() as db:
        assert len(db.scalars(select(CandidateInvoice)).all()) == 1


def test_the_automatic_invoice_is_issued_whoever_uploads_the_ticket(access):
    app = mapped(access)
    configure(access)
    fee(access, "5000", app=app)
    as_user(access, "manager", Role.manager)
    response = upload(access, "air_ticket", pdf("Ticket uploaded by a manager"))
    assert response.status_code == 202, response.text
    assert read(access, app=app)["status"] == "issued"
    with access.sessions() as db:
        assert db.scalar(select(CandidateInvoice.issued_by)) == "manager"


def test_the_application_form_carries_no_invoice_for_recruiters_and_managers(access):
    app, data = issued(access)
    attachment_id = data["invoice"]["downloadUrl"].rsplit("/", 1)[-1]
    for user_id, role in OUTSIDERS[::2]:
        as_user(access, user_id, role)
        payload = form(access, applicationId=app)
        assert "invoice" not in payload["documents"] and "invoice" not in payload["documentOrder"]
        assert "invoice_generated" not in [row["key"] for row in payload["tracking"]["rows"]]
        assert "invoice" not in [item["slot"] for item in payload["deployment"]["missing"]]
        assert "documents.invoice" not in [item["key"] for item in payload["completeness"]["missing"]]
        assert "invoice" not in {item["slot"] for item in payload["completeness"]["optionalDocuments"]}
        text = json.dumps(payload)
        for secret in ("CI/26-27/001", attachment_id, "11800", "1800.00", "CGST", "SGST"):
            assert secret not in text, (user_id, secret)
        assert "invoice" not in text.lower(), user_id
    as_user(access, "admin", Role.admin)
    payload = form(access, applicationId=app)
    assert payload["documents"]["invoice"]["uploaded"] and payload["documents"]["invoice"]["current"]["url"].endswith(attachment_id)
    assert "invoice" in payload["documentOrder"]
    assert "invoice_generated" in [row["key"] for row in payload["tracking"]["rows"]]


def test_a_recruiter_is_not_held_back_by_a_document_they_cannot_see(access):
    mapped(access)
    as_user(access, "admin", Role.admin)
    assert "Invoice" in [item["label"] for item in form(access)["deployment"]["missing"]]
    for user_id, role in OUTSIDERS[::2]:
        as_user(access, user_id, role)
        payload = form(access)
        assert "Invoice" not in [item["label"] for item in payload["deployment"]["missing"]]
        assert all(slot != "invoice" for slot, _ in application_form.required_for_deployment(False))
        # The checklist keeps its own rules: the other documents are still counted, and the percentage stays consistent.
        assert payload["completeness"]["filled"] <= payload["completeness"]["total"]
    assert [slot for slot, _ in application_form.required_for_deployment()].count("invoice") == 1
    assert "invoice" not in {item["slot"] for item in application_form.deployment_gaps(set(), False)}
    assert "invoice" in {item["slot"] for item in application_form.deployment_gaps(set())}


def test_the_invoice_file_is_not_downloadable_listable_or_uploadable_by_others(access):
    app, data = issued(access)
    link = data["invoice"]["downloadUrl"]
    for user_id, role in OUTSIDERS:
        as_user(access, user_id, role)
        refused = access.client.get(link)
        assert refused.status_code == 403 and refused.json()["detail"] == DENIED, user_id
        if user_id == "other":
            continue  # another recruiter may not open this placement's files at all
        listing = access.client.get("/api/files", params={"entity_type": "application", "entity_id": app})
        assert listing.status_code == 200 and "invoice" not in [row["slot"] for row in listing.json()], user_id
    as_user(access, "staff", Role.staff)
    refused = access.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": "invoice"},
                                 files={"file": ("invoice.pdf", pdf("Made up invoice"), "application/pdf")})
    assert refused.status_code == 403 and refused.json()["detail"] == DENIED
    assert upload(access, "invoice", pdf("Made up invoice")).status_code == 403
    marked = access.client.put(url() + "/documents/invoice/status", json={"uploaded": True, "note": "seen"})
    assert marked.status_code == 403 and marked.json()["detail"] == DENIED
    as_user(access, "admin", Role.admin)
    assert access.client.get(link).status_code == 200
    listing = access.client.get("/api/files", params={"entity_type": "application", "entity_id": app})
    assert "invoice" in [row["slot"] for row in listing.json()]


def test_candidate_detail_has_no_invoice_document_for_recruiters_and_managers(access):
    issued(access)
    with access.sessions() as db:
        db.add(Attachment(id="old-invoice", entity_type="candidate", entity_id="candidate", slot="invoice", version=1,
                          file_name="Older-invoice.pdf", content_type="application/pdf", object_key="older-invoice.pdf"))
        db.commit()
    for user_id, role in OUTSIDERS[::2]:
        as_user(access, user_id, role)
        response = access.client.get("/api/candidates/candidate")
        assert response.status_code == 200, response.text
        assert "invoice" not in [item["key"] for item in response.json()["documents"]]
        assert "old-invoice" not in response.text and "Older-invoice" not in response.text
    as_user(access, "admin", Role.admin)
    documents = {item["key"]: item for item in access.client.get("/api/candidates/candidate").json()["documents"]}
    assert documents["invoice"]["versions"][0]["name"] == "Older-invoice.pdf"


def test_the_record_export_leaves_invoice_files_and_gaps_to_the_admin(access):
    issued(access)
    for user_id, role in OUTSIDERS[::2]:
        as_user(access, user_id, role)
        with access.sessions() as db:
            inspection = candidate_exports.load_inspection(db, "candidate", access.actor)
            listing = candidate_exports.manifest(inspection)
        assert "invoice" not in {row["slot"] for row in listing["documents"]}
        assert "invoice" not in {row["slot"] for row in listing["checklist"]}
        assert "invoice" not in {row["slot"] for row in listing["missingRequiredBeforeDeployment"]}
        assert "invoice" not in listing["filePolicy"].lower()
    as_user(access, "admin", Role.admin)
    with access.sessions() as db:
        listing = candidate_exports.manifest(candidate_exports.load_inspection(db, "candidate", access.actor))
    assert "invoice" in {row["slot"] for row in listing["documents"]} and "invoice" in {row["slot"] for row in listing["checklist"]}


def test_the_travelled_checklist_does_not_list_the_invoice_for_others(access):
    from app.api.routers import travelled
    for user_id, role, shown in (("admin", Role.admin, True), ("manager", Role.manager, False), ("staff", Role.staff, False)):
        as_user(access, user_id, role)
        rows = [{"candidateId": "candidate"}]
        with access.sessions() as db:
            travelled._add_documents(db, rows, access.actor)
        documents = rows[0]["documents"]
        assert ("Invoice" in documents["missing"]) is shown, user_id
        assert documents["required"] == len(application_form.required_for_deployment(shown))
