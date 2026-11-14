"""Invoice generation tests use only the guarded disposable PostgreSQL DB and synthetic documents."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal

import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.api.routers import candidate_invoices as routes
from app.db.models import Application, ApplicationStatus, Attachment, CandidateInvoice, InvoiceSettings, Role
from app.services import candidate_invoices as service
from test_application_form import as_user, desk, form, upload
from test_application_workflow import complete, mapped, reach, save
from test_database_naming import schema_database
from test_document_extraction import pdf


@pytest.fixture
def billing(desk, monkeypatch):
    desk.client.app.include_router(routes.router)
    monkeypatch.setattr(service, "get_settings", lambda: desk.settings)
    return desk


@contextmanager
def admin(env):
    """Fees, invoices and receipts are the admin's alone; ticket uploads stay with the recruiter."""
    before = (env.actor.id, env.actor.role)
    as_user(env, "admin", Role.admin)
    try:
        yield
    finally:
        as_user(env, *before)


def configure(env):
    as_user(env, "admin", Role.admin)
    original = env.client.get("/api/invoice-settings").json()
    response = env.client.put("/api/invoice-settings", json={**original["settings"], "lockVersion": original["lockVersion"]})
    assert response.status_code == 200, response.text
    as_user(env, "staff", Role.staff)
    return response.json()


def read(env, candidate="candidate", app=None):
    with admin(env):
        response = env.client.get(f"/api/candidates/{candidate}/invoice", params={"applicationId": app} if app else {})
    assert response.status_code == 200, response.text
    return response.json()


def fee(env, amount, candidate="candidate", app=None):
    current = read(env, candidate, app)
    with admin(env):
        response = env.client.put(f"/api/candidates/{candidate}/invoice", json={"serviceFee": amount,
            "applicationId": app, "lockVersion": current["lockVersion"]})
    assert response.status_code == 200, response.text
    return response.json()


def retry(env, candidate="candidate", app=None):
    with admin(env):
        response = env.client.post(f"/api/candidates/{candidate}/invoice/reconcile", json={"applicationId": app})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("fee_value", "tax", "gross"), [("5000", "450.00", "5900.00"),
    ("30000", "2700.00", "35400.00"), ("5000.05", "450.00", "5900.05"), ("5000.06", "450.01", "5900.08")])
def test_gst_uses_decimal_rounding_for_each_component(fee_value, tax, gross):
    result = service.amounts(fee_value)
    assert result["cgst"] == result["sgst"] == tax
    assert result["total"] == gross


def test_reading_settings_and_invoice_never_creates_rows_or_generates_bills(billing):
    mapped(billing)
    assert read(billing)["status"] == "pending_ticket"
    as_user(billing, "admin", Role.admin)
    data = billing.client.get("/api/invoice-settings").json()
    assert data["configured"] is False
    assert data["settings"]["gstin"] == "33AABCC8349LIZF"
    assert data["settings"]["invoicePrefix"] == "CI/26-27/" and data["settings"]["nextNumber"] == 1
    with billing.sessions() as db:
        assert db.scalar(select(func.count()).select_from(InvoiceSettings)) == 0
        assert db.scalar(select(func.count()).select_from(CandidateInvoice)) == 0


def test_ticket_upload_automatically_issues_actual_fee_invoice_and_keeps_stage_unchanged(billing):
    app = mapped(billing)
    configure(billing)
    assert fee(billing, "5000.00", app=app)["status"] == "pending_ticket"
    upload(billing, "air_ticket", pdf("Synthetic ticket"))
    data = read(billing, app=app)
    assert data["status"] == "issued" and data["serviceFee"] == "5000.00"
    assert data["invoice"]["number"] == "CI/26-27/001"
    assert data["invoice"]["total"] == "5900.00"
    with admin(billing):
        response = billing.client.get(data["invoice"]["downloadUrl"])
    assert response.status_code == 200
    with pymupdf.open(stream=response.content, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "33AABCC8349LIZF" in text and "5900.00" in text and "TAX INVOICE" in text
        assert "Received sum" not in text
    with billing.sessions() as db:
        assert db.get(Application, app).current_stage == 1
        row = db.scalar(select(CandidateInvoice))
        attachment = db.get(Attachment, row.attachment_id)
        assert (attachment.entity_type, attachment.entity_id, attachment.slot) == ("application", app, "invoice")
        assert db.get(InvoiceSettings, 1).next_number == 2


def test_ticket_with_missing_fee_waits_and_fee_save_issues_once(billing):
    app = mapped(billing)
    configure(billing)
    upload(billing, "air_ticket", pdf("Ticket"))
    assert read(billing)["status"] == "pending_fee"
    issued = fee(billing, "30000", app=app)
    assert issued["status"] == "issued" and issued["invoice"]["total"] == "35400.00"
    for _ in range(2):
        assert retry(billing, app=app)["invoice"]["id"] == issued["invoice"]["id"]
    upload(billing, "air_ticket", pdf("Replacement ticket"))
    assert read(billing)["invoice"] == issued["invoice"]
    with billing.sessions() as db:
        assert db.scalar(select(func.count()).select_from(CandidateInvoice)) == 1
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.slot == "invoice")) == 1
        assert db.get(InvoiceSettings, 1).next_number == 2


def test_missing_configuration_waits_then_settings_save_automatically_issues(billing):
    app = mapped(billing)
    upload(billing, "air_ticket", pdf("Ticket"))
    waiting = fee(billing, "10000", app=app)
    assert waiting["status"] == "pending_configuration" and waiting["invoice"]["total"] == "11800.00"
    assert waiting["invoice"]["number"] is None
    configure(billing)
    assert read(billing)["status"] == "issued"


def test_pdf_failure_keeps_saved_fee_and_reserved_number_for_idempotent_retry(billing, monkeypatch):
    app = mapped(billing)
    configure(billing)
    fee(billing, "5000", app=app)
    original = service.invoice_pdf.render
    monkeypatch.setattr(service.invoice_pdf, "render", lambda *_: (_ for _ in ()).throw(OSError("synthetic PDF failure")))
    upload(billing, "air_ticket", pdf("Ticket"))
    waiting = read(billing)
    assert waiting["status"] == "pending_generation" and waiting["serviceFee"] == "5000.00"
    assert waiting["invoice"]["number"] == "CI/26-27/001" and waiting["error"]
    with billing.sessions() as db:
        assert db.get(InvoiceSettings, 1).next_number == 2
    monkeypatch.setattr(service.invoice_pdf, "render", original)
    issued = retry(billing, app=app)
    assert issued["status"] == "issued" and issued["invoice"]["number"] == "CI/26-27/001"
    with billing.sessions() as db:
        assert db.get(InvoiceSettings, 1).next_number == 2


def test_issued_amounts_and_number_cannot_be_edited_by_api_or_database(billing):
    app = mapped(billing)
    configure(billing)
    upload(billing, "air_ticket", pdf("Ticket"))
    issued = fee(billing, "5000", app=app)
    with admin(billing):
        refused = billing.client.put("/api/candidates/candidate/invoice", json={"serviceFee": "6000", "applicationId": app,
            "lockVersion": issued["lockVersion"]})
    assert refused.status_code == 409
    with billing.sessions() as db:
        row = db.scalar(select(CandidateInvoice))
        row.service_fee = Decimal("6000")
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    assert read(billing)["invoice"] == issued["invoice"]


def test_ambiguous_candidate_ticket_cannot_invoice_another_placement(billing):
    first = mapped(billing)
    with billing.sessions() as db:
        db.get(Application, first).status = ApplicationStatus.deployed
        db.add(Application(id="another-placement", candidate_id="candidate", order_id="other-order", owner_id="staff", current_stage=1))
        db.commit()
    configure(billing)
    upload(billing, "air_ticket", pdf("Unallocated generic ticket"))
    assert read(billing)["status"] == "pending_assignment"
    assert fee(billing, "5000", app="another-placement")["status"] == "pending_ticket"
    response = billing.client.post("/api/files", data={"entity_type": "application", "entity_id": "another-placement", "slot": "stage_11_file"},
        files={"file": ("ticket.pdf", pdf("Specific new placement ticket"), "application/pdf")})
    assert response.status_code == 201, response.text
    assert read(billing, app="another-placement")["status"] == "issued"
    assert read(billing, app=first)["status"] == "pending_ticket"


def test_completion_without_ticket_never_creates_an_invoice(billing):
    app = mapped(billing)
    configure(billing)
    reach(billing, app, 11)  # stages 1-10 with their required decisions (Selected, Agreed, Fit)
    # Deployment cannot be moved manually any more: it needs the ticket and the travel date, so no invoice can follow from a note.
    saved = save(billing, app, {"feedback": "Move stage 11 manually"})
    assert saved.status_code == 200, saved.text
    refused = billing.client.post(f"/api/applications/{app}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert refused.status_code == 422 and refused.json()["detail"]["missing"] == ["Air ticket", "Travel date"], refused.text
    with billing.sessions() as db:
        assert db.get(Application, app).status == ApplicationStatus.active
        assert db.scalar(select(func.count()).select_from(CandidateInvoice)) == 0
    assert read(billing)["status"] == "pending_ticket"


def test_another_recruiter_cannot_read_fees_or_write_settings(billing):
    mapped(billing)
    as_user(billing, "other", Role.staff)
    assert billing.client.get("/api/candidates/candidate/invoice").status_code == 403
    assert billing.client.get("/api/invoice-settings").status_code == 403


@pytest.mark.parametrize("value", ["4999", "30000.01", "5000.001", "NaN", True])
def test_fee_is_actual_valid_pre_gst_amount_in_confirmed_range(billing, value):
    mapped(billing)
    with admin(billing):
        response = billing.client.put("/api/candidates/candidate/invoice", json={"serviceFee": value, "lockVersion": 0})
    assert response.status_code == 422


def test_concurrent_generation_issues_only_one_invoice_for_a_placement(billing):
    app = mapped(billing)
    configure(billing)
    fee(billing, "5000", app=app)
    with billing.sessions() as db:
        db.add(Attachment(entity_type="application", entity_id=app, slot="stage_11_file", version=1,
            file_name="ticket.pdf", content_type="application/pdf", object_key="synthetic-ticket.pdf"))
        db.commit()
    def issue():
        with billing.sessions() as db:
            return service.reconcile(db, "candidate", billing.actor, app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: issue(), range(2)))
    assert all(item["status"] == "issued" for item in results)
    assert {item["invoice"]["number"] for item in results} == {"CI/26-27/001"}
    with billing.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.slot == "invoice")) == 1
        assert db.get(InvoiceSettings, 1).next_number == 2


def test_concurrent_candidates_get_different_numbers_from_the_locked_sequence(billing):
    configure(billing)
    pairs = [(candidate, mapped(billing, candidate=candidate)) for candidate in ("candidate", "second-candidate")]
    for candidate, app in pairs:
        fee(billing, "5000", candidate=candidate, app=app)
        with billing.sessions() as db:
            db.add(Attachment(entity_type="application", entity_id=app, slot="stage_11_file", version=1,
                file_name="ticket.pdf", content_type="application/pdf", object_key=f"{candidate}-ticket.pdf"))
            db.commit()
    def issue(pair):
        with billing.sessions() as db:
            return service.reconcile(db, pair[0], billing.actor, pair[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        issued = list(pool.map(issue, pairs))
    assert {item["invoice"]["number"] for item in issued} == {"CI/26-27/001", "CI/26-27/002"}
    assert all(item["status"] == "issued" for item in issued)
    with billing.sessions() as db:
        assert db.get(InvoiceSettings, 1).next_number == 3


def test_settings_refuse_reusing_reserved_numbers_and_overlong_final_numbers(billing):
    app = mapped(billing)
    configure(billing)
    upload(billing, "air_ticket", pdf("Ticket"))
    fee(billing, "5000", app=app)
    as_user(billing, "admin", Role.admin)
    current = billing.client.get("/api/invoice-settings").json()
    body = {**current["settings"], "lockVersion": current["lockVersion"], "nextNumber": 1}
    assert billing.client.put("/api/invoice-settings", json=body).status_code == 409
    body.update(invoicePrefix="ABCDEFGHIJKLM", nextNumber=10000)
    assert billing.client.put("/api/invoice-settings", json=body).status_code == 422
