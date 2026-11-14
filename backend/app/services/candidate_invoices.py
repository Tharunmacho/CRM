"""Idempotent per-placement invoices, issued only for an actual ticket and an entered fee."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import logging
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import ensure_can_work_candidate
from app.db.models import Application, ApplicationStatus, Attachment, AuditEntry, Candidate, CandidateInvoice, Company, Designation, InvoiceSettings, Order, Role
from app.services import document_storage, invoice_pdf
from app.services.invoice_access import ensure_can_see_invoices

logger = logging.getLogger(__name__)
TEMPLATE = {
    "companyName": "Care India Overseas Pvt. Ltd.",
    "address": "74/3, Dhanalakshmi Apts., I Avenue, Ashok Nagar, Chennai 600083",
    "gstin": "33AABCC8349LIZF", "sacCode": "998519", "placeOfSupply": "Tamil Nadu",
    "phone": "044 24711234 / 24746751 / 42318227", "email": "careindia@gmail.com",
    "website": "www.careindiaoverseas.com", "cin": "U74999TN1998PTC039920",
    "bankName": "", "bankAccount": "", "bankIfsc": "",
}
CENT = Decimal("0.01")


def money(value) -> str:
    return str(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def amounts(fee, cgst="9", sgst="9") -> dict:
    base = Decimal(str(fee)).quantize(CENT, rounding=ROUND_HALF_UP)
    central = (base * Decimal(str(cgst)) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    state = (base * Decimal(str(sgst)) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return {"subtotal": money(base), "cgstRate": money(cgst), "sgstRate": money(sgst),
            "cgst": money(central), "sgst": money(state), "total": money(base + central + state)}


def settings_values(row) -> dict:
    # Reviewed template values are prefilled only. Issuance requires a saved enabled settings row.
    return {**TEMPLATE, **(row.profile if row else {}), "invoicePrefix": row.invoice_prefix if row else "CI/26-27/",
            "nextNumber": row.next_number if row else 1,
            "cgstRate": money(row.cgst_rate if row else 9), "sgstRate": money(row.sgst_rate if row else 9)}


def missing_settings(row) -> list[str]:
    if row is None:
        return ["Save the reviewed company and invoice numbering settings"]
    data = settings_values(row)
    missing = [label for key, label in (("companyName", "Company name"), ("address", "Company address"),
                                       ("gstin", "GSTIN"), ("sacCode", "SAC code")) if not data.get(key, "").strip()]
    if not data.get("nextNumber"):
        missing.append("Next invoice number")
    if not row.enabled and not missing:
        missing.append("Enable the reviewed invoice settings")
    return missing


def settings_state(db, user) -> dict:
    ensure_can_see_invoices(user)
    row = db.get(InvoiceSettings, 1)
    gaps = missing_settings(row)
    return {"canEdit": user.role == Role.admin, "configured": not gaps, "missing": gaps,
            "settings": settings_values(row), "lockVersion": row.lock_version if row else 0}


def _candidate(db, candidate_id, user, *, lock=False):
    query = select(Candidate).where(Candidate.id == candidate_id)
    row = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None:
        raise HTTPException(404, "Candidate not found")
    # Invoice amounts and banking details use the same assigned-worker boundary as record exports.
    ensure_can_work_candidate(row, user, db)
    return row


def _application(db, candidate_id, application_id):
    if application_id:
        app = db.get(Application, application_id)
        if app is None or app.candidate_id != candidate_id:
            raise HTTPException(404, "Application not found for this candidate")
        return app
    choices = list(db.scalars(select(Application).where(Application.candidate_id == candidate_id,
        Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed]))))
    return choices[0] if len(choices) == 1 else None


def _record(db, candidate_id, app, *, lock=False):
    query = select(CandidateInvoice).where(CandidateInvoice.candidate_id == candidate_id,
                                          CandidateInvoice.scope_key == (app.id if app else ""))
    return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def _ticket(db, candidate_id, app, row=None):
    matches = [(Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id) & (Attachment.slot == "air_ticket")]
    if app:
        matches.append((Attachment.entity_type == "application") & (Attachment.entity_id == app.id) & (Attachment.slot == "stage_11_file"))
    options = list(db.scalars(select(Attachment).where(or_(*matches)).order_by(Attachment.uploaded_at.desc(), Attachment.version.desc())))
    options.sort(key=lambda ticket: ticket.entity_type != "application")
    placements = list(db.scalars(select(Application.id).where(Application.candidate_id == candidate_id,
        Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed]))))
    for ticket in options:
        if ticket.entity_type == "candidate" and app:
            bound = row and row.application_id == app.id and row.scope_key == app.id and row.ticket_attachment_id == ticket.id
            if not bound and placements != [app.id]:
                continue
        used_elsewhere = db.scalar(select(CandidateInvoice.id).where(
            CandidateInvoice.ticket_attachment_id == ticket.id,
            CandidateInvoice.scope_key != (app.id if app else ""), CandidateInvoice.status == "issued"))
        if not used_elsewhere:
            return ticket
    return None


def _status(app, ticket, row, config):
    if row and row.status == "issued":
        return "issued"
    if not app:
        return "pending_assignment"
    if not ticket:
        return "pending_ticket"
    if row is None or row.service_fee is None:
        return "pending_fee"
    if missing_settings(config):
        return "pending_configuration"
    return "pending_generation"


def state(db, candidate_id, user, application_id=None):
    from app.services.invoice_payments import summary as payment_summary
    _candidate(db, candidate_id, user)
    app = _application(db, candidate_id, application_id)
    row = _record(db, candidate_id, app)
    if row is None and app:
        row = _record(db, candidate_id, None)
    config = db.get(InvoiceSettings, 1)
    ticket = _ticket(db, candidate_id, app, row)
    invoice = None
    reviewed = settings_values(config)
    reserved = (row.snapshot or {}).get("amounts", {}) if row else {}
    central_rate = reserved.get("cgstRate", reviewed["cgstRate"])
    state_rate = reserved.get("sgstRate", reviewed["sgstRate"])
    if row:
        sums = reserved if row.status == "issued" else (amounts(row.service_fee, central_rate, state_rate) if row.service_fee is not None else {})
        invoice = {"id": row.id, "number": row.number, "issuedAt": row.issued_at.isoformat() if row.issued_at else None,
                   **{key: sums.get(key) for key in ("subtotal", "cgstRate", "sgstRate", "cgst", "sgst", "total")},
                   "downloadUrl": f"/api/files/{row.attachment_id}" if row.attachment_id else None,
                   "recipient": (row.snapshot or {}).get("recipient")}
    return {"candidateId": candidate_id, "applicationId": app.id if app else None,
            "canEdit": row is None or row.status != "issued", "status": _status(app, ticket, row, config),
            "serviceFee": money(row.service_fee) if row and row.service_fee is not None else None,
            "currency": "INR", "ticketUploaded": ticket is not None,
            "lockVersion": row.lock_version if row else 0, "missingConfiguration": missing_settings(config),
            "error": row.error if row else None, "invoice": invoice,
            "issuer": {key: (((row.snapshot or {}).get("issuer") if row else None) or reviewed)[key] for key in ("companyName", "gstin")},
            "taxRates": {"cgst": central_rate, "sgst": state_rate}, "paymentSummary": payment_summary(db, row, user)}


def _ensure_record(db, candidate_id, app):
    row = _record(db, candidate_id, app, lock=True)
    if row is None and app:
        unassigned = _record(db, candidate_id, None, lock=True)
        if unassigned and unassigned.status != "issued":
            unassigned.application_id, unassigned.scope_key = app.id, app.id
            unassigned.ticket_attachment_id = None
            row = unassigned
    if row is None:
        row = CandidateInvoice(id=str(uuid.uuid4()), candidate_id=candidate_id,
                               application_id=app.id if app else None, scope_key=app.id if app else "", lock_version=0)
        db.add(row)
        db.flush()
    return row


def save_fee(db, candidate_id, user, fee, lock_version, application_id=None):
    ensure_can_see_invoices(user)
    try:
        candidate = _candidate(db, candidate_id, user, lock=True)
        app = _application(db, candidate_id, application_id)
        row = _ensure_record(db, candidate_id, app)
        if row.lock_version != lock_version:
            raise HTTPException(409, "The invoice changed. Refresh it before saving the fee.")
        if row.status == "issued":
            if row.service_fee != fee:
                raise HTTPException(409, "This invoice has been issued. Its recorded fee and tax amounts cannot be changed.")
        else:
            from app.services.invoice_payments import ensure_fee_covers_receipts
            configuration = db.get(InvoiceSettings, 1)
            reserved_rates = (row.snapshot or {}).get("amounts", {})
            ensure_fee_covers_receipts(db, row, fee,
                                      reserved_rates.get("cgstRate", configuration.cgst_rate if configuration else 9),
                                      reserved_rates.get("sgstRate", configuration.sgst_rate if configuration else 9))
            row.service_fee, row.error = fee, None
            row.lock_version += 1
            ticket = _ticket(db, candidate_id, app, row)
            row.ticket_attachment_id = ticket.id if ticket else None
            row.status = _status(app, ticket, row, db.get(InvoiceSettings, 1))
            db.add(AuditEntry(actor_id=user.id, action="Saved candidate service fee", entity_type="candidate", entity_id=candidate_id,
                             target=candidate.name, meta={"invoice_id": row.id, "application_id": row.application_id,
                                                         "service_fee": money(fee) if fee is not None else None}))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return reconcile(db, candidate_id, user, application_id=application_id)


def reconcile(db, candidate_id, user, application_id=None):
    """Explicit write: durably retain pending prerequisites before rendering or writing a PDF."""
    try:
        _candidate(db, candidate_id, user, lock=True)
        app = _application(db, candidate_id, application_id)
        row = _ensure_record(db, candidate_id, app)
        if row.status != "issued":
            ticket = _ticket(db, candidate_id, app, row)
            row.ticket_attachment_id = ticket.id if ticket else None
            row.status = _status(app, ticket, row, db.get(InvoiceSettings, 1))
        identifier = row.id
        ready = row.status == "pending_generation"
        db.commit()
    except Exception:
        db.rollback()
        raise
    if ready:
        try:
            _issue(db, candidate_id, user, identifier)
        except Exception:
            db.rollback()
            logger.exception("Candidate invoice PDF could not be issued; pending request retained")
            _candidate(db, candidate_id, user, lock=True)
            row = db.get(CandidateInvoice, identifier, populate_existing=True)
            if row and row.status != "issued":
                row.error = "The invoice PDF could not be saved. Retry invoice generation."
                row.status = "pending_generation"
                db.commit()
    return state(db, candidate_id, user, application_id)


def _issue(db, candidate_id, user, identifier):
    candidate = _candidate(db, candidate_id, user, lock=True)
    row = db.get(CandidateInvoice, identifier, populate_existing=True)
    if row is None or row.status == "issued":
        db.commit()
        return
    app = _application(db, candidate_id, row.application_id)
    ticket = _ticket(db, candidate_id, app, row)
    config = db.scalar(select(InvoiceSettings).where(InvoiceSettings.id == 1).with_for_update().execution_options(populate_existing=True))
    if _status(app, ticket, row, config) != "pending_generation":
        db.commit()
        return
    values = settings_values(config)
    number = row.number or f"{config.invoice_prefix}{config.next_number:03d}"
    if len(number) > 16:
        raise ValueError("Invoice number exceeds 16 characters")
    if db.scalar(select(CandidateInvoice.id).where(CandidateInvoice.number == number, CandidateInvoice.id != row.id)):
        raise ValueError("The configured invoice number is already used")
    at = datetime.now(timezone.utc)
    order = db.get(Order, app.order_id)
    company = db.get(Company, order.company_id) if order else None
    designation = db.get(Designation, order.designation_id) if order else None
    sums = amounts(row.service_fee, config.cgst_rate, config.sgst_rate)
    snapshot = {"number": number, "date": at.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        "issuer": {key: values[key] for key in TEMPLATE},
        "recipient": {"name": candidate.name, "ref": candidate.ref, "address": candidate.current_address or "",
                      "passport": candidate.passport_no or ""},
        "orderRef": order.ref if order else "", "applicationId": app.id, "amounts": sums,
        "description": "Recruitment service fee" + (" - " + ", ".join(value for value in (
            designation.title if designation else None, company.name if company else None,
            order.country if order and order.country else company.country if company else None) if value) if order else "")}
    if row.number is None:
        # Reserve once before file generation. A failed PDF retains this number for retries;
        # another candidate cannot consume it, and no retry allocates a second invoice.
        row.number, row.snapshot = number, snapshot
        row.lock_version += 1
        config.next_number += 1
        config.lock_version += 1
        db.commit()
        candidate = _candidate(db, candidate_id, user, lock=True)
        row = db.get(CandidateInvoice, identifier, populate_existing=True)
        if row.status == "issued":
            db.commit()
            return
        app = _application(db, candidate_id, row.application_id)
        ticket = _ticket(db, candidate_id, app, row)
        fresh_config = db.get(InvoiceSettings, 1, populate_existing=True)
        readiness = _status(app, ticket, row, fresh_config)
        if readiness != "pending_generation":
            row.status = readiness
            db.commit()
            return
    else:
        snapshot["issuer"] = row.snapshot["issuer"]
    # An explicitly edited pending fee uses its existing reserved number and issuer rates.
    reserved = row.snapshot["amounts"]
    snapshot["amounts"] = sums = amounts(row.service_fee, reserved["cgstRate"], reserved["sgstRate"])
    snapshot["recipient"] = {"name": candidate.name, "ref": candidate.ref, "address": candidate.current_address or "",
                             "passport": candidate.passport_no or ""}
    payload = invoice_pdf.render(snapshot)
    config_for_storage = get_settings()
    key = document_storage.storage_key(config_for_storage, f"invoices/{row.id}/{uuid.uuid4().hex}.pdf")
    document_storage.put_bytes(config_for_storage, key, payload, "application/pdf")
    previous = db.scalar(select(func.max(Attachment.version)).where(Attachment.entity_type == "application",
                         Attachment.entity_id == app.id, Attachment.slot == "invoice")) or 0
    attachment = Attachment(id=str(uuid.uuid4()), entity_type="application", entity_id=app.id, slot="invoice", version=previous + 1,
        file_name=f"Invoice-{number.replace('/', '-')}.pdf", content_type="application/pdf", object_key=key,
        size_kb=max(1, (len(payload) + 1023) // 1024), content_hash=hashlib.sha256(payload).hexdigest(), uploaded_by=user.id, uploaded_at=at)
    db.add(attachment)
    db.flush()
    row.number, row.snapshot, row.attachment_id = number, snapshot, attachment.id
    row.ticket_attachment_id = ticket.id
    row.status, row.issued_at, row.issued_by, row.error = "issued", at, user.id, None
    row.lock_version += 1
    db.add(AuditEntry(actor_id=user.id, action="Issued candidate service invoice", entity_type="candidate", entity_id=candidate_id,
        target=candidate.name, meta={"invoice_id": row.id, "number": number, "application_id": app.id, "amounts": sums}))
    db.commit()


def on_ticket_uploaded(db, attachment, user):
    if attachment.entity_type == "candidate" and attachment.slot == "air_ticket":
        return reconcile(db, attachment.entity_id, user)
    if attachment.entity_type == "application" and attachment.slot == "stage_11_file":
        app = db.get(Application, attachment.entity_id)
        if app:
            return reconcile(db, app.candidate_id, user, application_id=app.id)


def save_settings(db, user, values, lock_version):
    ensure_can_see_invoices(user)
    try:
        # An advisory transaction lock also serializes the first singleton insert.
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(7340061)"))
        row = db.scalar(select(InvoiceSettings).where(InvoiceSettings.id == 1).with_for_update())
        if (row.lock_version if row else 0) != lock_version:
            raise HTTPException(409, "Invoice settings changed. Refresh before saving.")
        if row is None:
            row = InvoiceSettings(id=1, lock_version=0)
            db.add(row)
        row.profile = {key: values.get(key, TEMPLATE[key]) for key in TEMPLATE}
        row.invoice_prefix = values["invoicePrefix"]
        row.next_number = values["nextNumber"]
        row.cgst_rate, row.sgst_rate = values["cgstRate"], values["sgstRate"]
        row.enabled = True
        row.lock_version += 1
        if missing_settings(row):
            raise HTTPException(422, "Complete the issuer details and next invoice number before saving")
        number = f"{row.invoice_prefix}{row.next_number:03d}"
        if len(number) > 16:
            raise HTTPException(422, "Invoice number must contain at most 16 characters including its prefix")
        if db.scalar(select(CandidateInvoice.id).where(CandidateInvoice.number == number)):
            raise HTTPException(409, "That invoice number has already been issued. Choose the next unused number.")
        db.add(AuditEntry(actor_id=user.id, action="Saved invoice settings", entity_type="invoice_settings", entity_id="1",
                         meta={"invoice_prefix": row.invoice_prefix, "next_number": row.next_number}))
        db.commit()
    except Exception:
        db.rollback()
        raise
    # Settings save activates existing pending invoices. Each keeps its own locked transaction.
    pending = list(db.execute(select(CandidateInvoice.candidate_id, CandidateInvoice.application_id).where(
        CandidateInvoice.status != "issued", CandidateInvoice.candidate_id.is_not(None), CandidateInvoice.service_fee.is_not(None))).all())
    for candidate_id, application_id in pending:
        reconcile(db, candidate_id, user, application_id)
    return settings_state(db, user)
