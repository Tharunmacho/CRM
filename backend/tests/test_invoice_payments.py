"""Receipt accounting uses synthetic data and the guarded disposable PostgreSQL fixture."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.api.routers import candidate_invoices, invoice_payments
from app.db.models import ApplicationStage, Candidate, CandidateInvoice, InvoicePayment, InvoiceSettings, Role
from app.services import candidate_invoices as invoices, invoice_payments as service
from test_application_form import as_user, desk
from test_application_workflow import mapped
from test_database_naming import schema_database


@pytest.fixture
def ledger(desk, monkeypatch):
    desk.client.app.include_router(candidate_invoices.router)
    desk.client.app.include_router(invoice_payments.router)
    monkeypatch.setattr(service, "get_settings", lambda: desk.settings)
    monkeypatch.setattr(invoices, "get_settings", lambda: desk.settings)
    # Fees, GST and receipts are the admin's alone, so the ledger flows run as the admin.
    as_user(desk, "admin", Role.admin)
    return desk


def prepare(env, *, candidate="candidate", fee="10000", issued=False):
    app = mapped(env, candidate=candidate)
    with env.sessions() as db:
        row = CandidateInvoice(candidate_id=candidate, application_id=app, scope_key=app,
            service_fee=Decimal(fee) if fee else None, status="issued" if issued else "pending_ticket",
            number="CI/26-27/TEST" if issued else None,
            snapshot={"amounts": invoices.amounts(fee)} if issued else None)
        db.add(row)
        db.commit()
        return app, row.id


def body(**updates):
    return {"amount": "2000.00", "date": "2026-01-02", "method": "cash", "reference": "Receipt 1",
            "note": "Actual cash collected", "idempotencyKey": "receipt-0001", **updates}


def endpoint(candidate="candidate"):
    return f"/api/candidates/{candidate}/invoice/payments"


def received(env, **updates):
    response = env.client.post(endpoint(), json=body(**updates))
    assert response.status_code == 200, response.text
    return response.json()


def test_get_without_fee_is_read_only_and_rejects_collection(ledger):
    mapped(ledger)
    response = ledger.client.get(endpoint())
    assert response.status_code == 200
    assert response.json()["total"] is None and response.json()["canRecord"] is False
    assert response.headers["cache-control"] == "private, no-store"
    assert ledger.client.post(endpoint(), json=body()).status_code == 422
    with ledger.sessions() as db:
        assert db.scalar(select(func.count()).select_from(CandidateInvoice)) == 0
        assert db.scalar(select(func.count()).select_from(InvoicePayment)) == 0


def test_deposit_before_ticket_counts_received_but_not_invoiced_and_is_idempotent(ledger):
    prepare(ledger)
    data = received(ledger)
    assert (data["serviceFee"], data["gst"], data["total"], data["totalInvoiced"]) == ("10000.00", "1800.00", "11800.00", "0.00")
    assert (data["received"], data["balance"]) == ("2000.00", "9800.00")
    assert data["payments"][0]["createdBy"] == {"id": "admin", "name": "Admin"}
    assert received(ledger)["payments"] == data["payments"]
    assert ledger.client.post(endpoint(), json=body(amount="2001")).status_code == 409
    paid = received(ledger, amount="9800", idempotencyKey="receipt-0002")
    assert paid["balance"] == "0.00" and paid["canRecord"] is False
    assert ledger.client.post(endpoint(), json=body(amount="0.01", idempotencyKey="receipt-0003")).status_code == 422
    # A retry after the balance reaches zero remains a successful replay.
    assert received(ledger)["received"] == "11800.00"
    with ledger.sessions() as db:
        assert db.scalar(select(func.count()).select_from(InvoicePayment)) == 2


@pytest.mark.parametrize("values", [{"amount": "0"}, {"amount": "-1"}, {"amount": "1.001"}, {"amount": True},
    {"amount": "NaN"}, {"date": "2999-01-01"}, {"method": "refund"}, {"idempotencyKey": "short"}])
def test_rejects_invalid_receipts(ledger, values):
    prepare(ledger)
    assert ledger.client.post(endpoint(), json=body(**values)).status_code == 422
    with ledger.sessions() as db:
        assert db.scalar(select(func.count()).select_from(InvoicePayment)) == 0


def test_receipts_enforce_assignment_and_application_scope(ledger):
    app, _ = prepare(ledger)
    other_app, _ = prepare(ledger, candidate="second-candidate")
    assert ledger.client.get(endpoint(), params={"applicationId": other_app}).status_code == 404
    assert ledger.client.post(endpoint(), params={"applicationId": app}, json=body(applicationId=other_app)).status_code == 422
    # Neither another recruiter, the owning recruiter nor a manager may see or record receipts.
    for user_id, role in (("other", Role.staff), ("staff", Role.staff), ("manager", Role.manager)):
        as_user(ledger, user_id, role)
        assert ledger.client.get(endpoint()).status_code == 403
        assert ledger.client.post(endpoint(), json=body()).status_code == 403
        assert ledger.client.get("/api/invoices/finance-summary").status_code == 403
    as_user(ledger, "admin", Role.admin)
    assert ledger.client.get(endpoint()).status_code == 200
    assert ledger.client.get("/api/invoices/finance-summary").json()["total"] == 2


def test_agreed_fee_cannot_be_cleared_or_reduced_below_actual_receipts(ledger):
    app, _ = prepare(ledger)
    received(ledger, amount="8000")
    current = ledger.client.get("/api/candidates/candidate/invoice").json()
    for fee in (None, "5000"):
        response = ledger.client.put("/api/candidates/candidate/invoice", json={"serviceFee": fee,
            "applicationId": app, "lockVersion": current["lockVersion"]})
        assert response.status_code == 422, response.text
    response = ledger.client.put("/api/candidates/candidate/invoice", json={"serviceFee": "9000",
        "applicationId": app, "lockVersion": current["lockVersion"]})
    assert response.status_code == 200, response.text
    assert response.json()["paymentSummary"]["balance"] == "2620.00"


def test_issued_amounts_stay_frozen_when_company_rates_change(ledger):
    prepare(ledger, issued=True)
    with ledger.sessions() as db:
        db.add(InvoiceSettings(id=1, cgst_rate=10, sgst_rate=10))
        db.commit()
    data = received(ledger)
    assert data["totalInvoiced"] == "11800.00" and data["balance"] == "9800.00"


def test_reserved_pending_invoice_uses_reserved_rates_with_current_fee(ledger):
    _, identifier = prepare(ledger)
    with ledger.sessions() as db:
        row = db.get(CandidateInvoice, identifier)
        row.snapshot = {"amounts": invoices.amounts("5000", "5", "5")}
        db.add(InvoiceSettings(id=1, cgst_rate=20, sgst_rate=20))
        db.commit()
    data = received(ledger)
    assert data["total"] == "11000.00" and data["balance"] == "9000.00"
    report = ledger.client.get("/api/invoices/finance-summary").json()
    assert report["totals"]["agreedTotal"] == "11000.00"


def test_admin_void_retains_original_receipt_and_excludes_it_from_totals(ledger):
    prepare(ledger)
    data = received(ledger)
    entry = data["payments"][0]
    url = endpoint() + f"/{entry['id']}/void"
    for user_id, role in (("staff", Role.staff), ("manager", Role.manager)):
        as_user(ledger, user_id, role)
        assert ledger.client.post(url, json={"reason": "Entered in error"}).status_code == 403
    as_user(ledger, "admin", Role.admin)
    assert ledger.client.post(url, json={"reason": " "}).status_code == 422
    voided = ledger.client.post(url, json={"reason": "Entered in error"})
    assert voided.status_code == 200, voided.text
    data = voided.json()
    assert data["received"] == "0.00" and data["balance"] == "11800.00"
    assert data["payments"][0]["amount"] == "2000.00" and data["payments"][0]["voidReason"] == "Entered in error"
    assert ledger.client.post(url, json={"reason": "Entered in error"}).json() == data
    assert ledger.client.post(url, json={"reason": "Different explanation"}).status_code == 409
    report = ledger.client.get("/api/invoices/finance-summary").json()
    assert report["totals"]["received"] == "0.00"
    # A replay cannot resurrect a voided receipt.
    assert received(ledger)["received"] == "0.00"


@pytest.mark.parametrize("statement", ["UPDATE crm_invoice_payments SET amount=1 WHERE id=:id",
    "UPDATE crm_invoice_payments SET note='replacement' WHERE id=:id", "DELETE FROM crm_invoice_payments WHERE id=:id"])
def test_database_prevents_rewriting_or_deleting_receipt_history(ledger, statement):
    prepare(ledger)
    identifier = received(ledger)["payments"][0]["id"]
    with ledger.sessions() as db, pytest.raises(DBAPIError):
        db.execute(text(statement), {"id": identifier})
        db.commit()


def advance(env, app, identifier="advance-1", **updates):
    data = {"collected": "yes", "amount": "3000", "amountCurrency": "INR", "date": "2026-01-02",
            "mode": "bank", "receiptNo": "BANK-1", **updates}
    with env.sessions() as db:
        db.add(ApplicationStage(id=identifier, application_id=app, stage=8, attempt=1, data=data))
        db.commit()


def test_advance_is_only_counted_after_confirmation_and_reopen_cannot_duplicate_it(ledger):
    app, _ = prepare(ledger)
    advance(ledger, app)
    data = ledger.client.get(endpoint()).json()
    assert data["received"] == "0.00" and len(data["availableAdvances"]) == 1
    source = data["availableAdvances"][0]
    assert ledger.client.post(endpoint(), json=body(**{**source, "amount": "4000"})).status_code == 422
    data = received(ledger, **source)
    assert data["received"] == "3000.00" and not data["availableAdvances"]
    with ledger.sessions() as db:
        original = db.get(ApplicationStage, "advance-1")
        original.reopened_reason = "Correction"
        db.add(ApplicationStage(id="advance-2", application_id=app, stage=8, attempt=2, data=original.data))
        db.commit()
    duplicate = ledger.client.post(endpoint(), json=body(**{**source, "sourceStageId": "advance-2", "idempotencyKey": "receipt-0002"}))
    assert duplicate.status_code == 409
    as_user(ledger, "admin", Role.admin)
    identifier = data["payments"][0]["id"]
    assert ledger.client.post(endpoint() + f"/{identifier}/void", json={"reason": "Wrong source confirmation"}).status_code == 200
    assert not ledger.client.get(endpoint()).json()["availableAdvances"]
    assert ledger.client.post(endpoint(), json=body(**{**source, "sourceStageId": "advance-2", "idempotencyKey": "receipt-0002"})).status_code == 409


@pytest.mark.parametrize("updates", [{"amount": None}, {"amountCurrency": "AED"}, {"date": None}, {"mode": None}, {"collected": "no"}])
def test_incomplete_or_non_inr_historical_advances_are_not_inferred(ledger, updates):
    app, _ = prepare(ledger)
    advance(ledger, app, **updates)
    assert ledger.client.get(endpoint()).json()["availableAdvances"] == []
    assert ledger.client.post(endpoint(), json=body(sourceStageId="advance-1")).status_code == 422


def test_finance_summary_distinguishes_agreed_invoiced_received_and_authorized_totals(ledger):
    prepare(ledger)
    prepare(ledger, candidate="second-candidate", fee="5000", issued=True)
    received(ledger)
    report = ledger.client.get("/api/invoices/finance-summary", params={"limit": 1}).json()
    assert report["total"] == 2 and len(report["rows"]) == 1
    assert report["totals"] == {"count": 2, "feeSubtotal": "15000.00", "gst": "2700.00", "agreedTotal": "17700.00",
        "totalInvoiced": "5900.00", "received": "2000.00", "balance": "15700.00", "credit": "0.00"}
    partial = ledger.client.get("/api/invoices/finance-summary", params={"status": "partial"}).json()
    assert partial["total"] == 1 and partial["rows"][0]["candidateId"] == "candidate"
    assert partial["totals"]["balance"] == "9800.00"
    assert ledger.client.get("/api/invoices/finance-summary", params={"search": "%"}).json()["total"] == 0
    assert ledger.client.get("/api/invoices/finance-summary", params={"search": "CI/26"}).json()["total"] == 1
    with ledger.sessions() as db:
        db.get(Candidate, "second-candidate").owner_id = "other"
        db.commit()
    # The admin sees every candidate's totals whoever owns it; nobody else sees any.
    assert ledger.client.get("/api/invoices/finance-summary").json()["totals"]["agreedTotal"] == "17700.00"
    as_user(ledger, "manager", Role.manager)
    assert ledger.client.get("/api/invoices/finance-summary").status_code == 403


def test_missing_fee_summary_never_invents_a_balance_or_payment(ledger):
    prepare(ledger, fee=None)
    result = ledger.client.get("/api/invoices/finance-summary").json()
    assert result["rows"][0]["fee"] is None and result["rows"][0]["balance"] is None
    assert result["totals"]["agreedTotal"] == "0.00" and result["totals"]["received"] == "0.00"


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_receipts_cannot_overcollect_or_duplicate_a_request(ledger, same_key):
    app, _ = prepare(ledger)
    def post(index):
        values = body(amount="8000", idempotencyKey="same-request" if same_key else f"request-{index}")
        values["amount"], values["date"] = Decimal(values["amount"]), date.fromisoformat(values["date"])
        with ledger.sessions() as db:
            try:
                service.record(db, "candidate", ledger.actor, values, app)
                return 200
            except HTTPException as error:
                return error.status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(post, (1, 2)))
    assert sorted(outcomes) == ([200, 200] if same_key else [200, 422])
    with ledger.sessions() as db:
        assert db.scalar(select(func.count()).select_from(InvoicePayment)) == 1
    assert ledger.client.get(endpoint()).json()["received"] == "8000.00"
