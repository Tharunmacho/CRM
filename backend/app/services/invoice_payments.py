"""Actual receipts, explicit historical advance carry-in and candidate fee balances."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import Numeric, case, cast, func, or_, select

from app.core.config import get_settings
from app.db.models import ApplicationStage, AuditEntry, Candidate, CandidateInvoice, InvoicePayment, InvoiceSettings, Role, User
from app.services import candidate_invoices as invoices
from app.services.invoice_access import can_see_invoices, ensure_can_see_invoices


def _received(db, invoice_id):
    return db.scalar(select(func.coalesce(func.sum(InvoicePayment.amount), 0)).where(
        InvoicePayment.invoice_id == invoice_id, InvoicePayment.voided_at.is_(None))) or Decimal(0)


def ensure_fee_covers_receipts(db, row, fee, cgst_rate, sgst_rate):
    """Called under the shared candidate lock before changing an agreed service fee."""
    received = _received(db, row.id)
    if received and (fee is None or Decimal(invoices.amounts(fee, cgst_rate, sgst_rate)["total"]) < received):
        raise HTTPException(422, "The agreed total cannot be lower than recorded receipts. Correct any mistaken receipt first.")


def _sums(db, row):
    if not row or row.service_fee is None:
        return None
    snapshot = (row.snapshot or {}).get("amounts") or {}
    if row.status == "issued" and snapshot:
        return snapshot
    config = db.get(InvoiceSettings, 1)
    return invoices.amounts(row.service_fee, snapshot.get("cgstRate", config.cgst_rate if config else 9),
                            snapshot.get("sgstRate", config.sgst_rate if config else 9))


def summary(db, row, user=None):
    sums = _sums(db, row)
    received = _received(db, row.id) if row else Decimal(0)
    total = Decimal(sums["total"]) if sums else None
    balance = max(total - received, Decimal(0)) if total is not None else None
    return {"invoiceId": row.id if row else None, "invoiceNumber": row.number if row else None,
            "invoiceStatus": row.status if row else "pending_fee", "currency": "INR",
            "serviceFee": sums["subtotal"] if sums else None,
            "gst": invoices.money(Decimal(sums["cgst"]) + Decimal(sums["sgst"])) if sums else None,
            "total": invoices.money(total) if total is not None else None,
            "totalInvoiced": invoices.money(total) if row and row.status == "issued" and total is not None else "0.00",
            "received": invoices.money(received), "balance": invoices.money(balance) if balance is not None else None,
            "credit": invoices.money(max(received - total, Decimal(0))) if total is not None else "0.00",
            "canRecord": bool(row and total is not None and balance > 0),
            "canVoid": can_see_invoices(user)}


def _scope(db, candidate_id, user, application_id, *, lock=False):
    candidate = invoices._candidate(db, candidate_id, user, lock=lock)
    app = invoices._application(db, candidate_id, application_id)
    row = invoices._record(db, candidate_id, app, lock=lock)
    if row is None and app:
        row = invoices._record(db, candidate_id, None, lock=lock)
    return candidate, app, row


def _today():
    return datetime.now(ZoneInfo(get_settings().agency_timezone)).date()


def _advance(stage):
    data = stage.data or {}
    if stage.stage != 8 or stage.reopened_reason or data.get("collected") != "yes":
        return None
    currency = (data.get("amountCurrency") or stage.amount_currency or "").upper()
    if currency != "INR" or data.get("mode") not in {"cash", "bank", "upi", "cheque"}:
        return None
    try:
        amount = Decimal(str(data.get("amount")))
        paid_on = date.fromisoformat(data.get("date", ""))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")) or paid_on > _today():
            return None
    except (InvalidOperation, ValueError, TypeError):
        return None
    return {"sourceStageId": stage.id, "amount": invoices.money(amount), "date": paid_on.isoformat(),
            "method": data["mode"], "reference": str(data.get("receiptNo") or "").strip()}


def _advances(db, app):
    if not app or db.scalar(select(InvoicePayment.id).where(InvoicePayment.source_application_id == app.id)):
        return []
    rows = db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == app.id,
        ApplicationStage.stage == 8, ApplicationStage.reopened_reason.is_(None)).order_by(ApplicationStage.attempt.desc()))
    for stage in rows:
        advance = _advance(stage)
        if advance:
            return [advance]
    return []


def state(db, candidate_id, user, application_id=None):
    ensure_can_see_invoices(user)
    _, app, row = _scope(db, candidate_id, user, application_id)
    result = summary(db, row, user)
    payments = []
    if row:
        entries = db.execute(select(InvoicePayment, User.name).outerjoin(User, User.id == InvoicePayment.created_by)
            .where(InvoicePayment.invoice_id == row.id).order_by(InvoicePayment.paid_on.desc(), InvoicePayment.created_at.desc()))
        for entry, name in entries:
            payments.append({"id": entry.id, "amount": invoices.money(entry.amount), "date": entry.paid_on.isoformat(),
                "method": entry.method, "reference": entry.reference, "note": entry.note,
                "createdAt": entry.created_at.isoformat(), "createdBy": {"id": entry.created_by, "name": name},
                "sourceStageId": entry.source_stage_id,
                "voidedAt": entry.voided_at.isoformat() if entry.voided_at else None, "voidReason": entry.void_reason})
    return {**result, "payments": payments, "availableAdvances": _advances(db, app)}


def record(db, candidate_id, user, values, application_id=None):
    ensure_can_see_invoices(user)
    try:
        candidate, app, row = _scope(db, candidate_id, user, application_id, lock=True)
        if row is None or row.service_fee is None:
            raise HTTPException(422, "Save the actual agreed service fee before recording a receipt.")
        amount, paid_on = values["amount"], values["date"]
        source = values.get("sourceStageId")
        normalized = (amount, paid_on, values["method"], values.get("reference") or "", values.get("note") or "", source)
        existing = db.scalar(select(InvoicePayment).where(InvoicePayment.invoice_id == row.id,
            InvoicePayment.idempotency_key == values["idempotencyKey"]))
        if existing:
            actual = (existing.amount, existing.paid_on, existing.method, existing.reference, existing.note, existing.source_stage_id)
            if actual != normalized:
                raise HTTPException(409, "This receipt request was already used with different details. Refresh before recording another payment.")
            db.commit()
            return state(db, candidate_id, user, application_id)
        if paid_on > _today():
            raise HTTPException(422, "A received payment cannot be dated in the future.")
        if source:
            stage = db.get(ApplicationStage, source)
            advance = _advance(stage) if stage and app and stage.application_id == app.id else None
            if not advance:
                raise HTTPException(422, "This recorded advance is not eligible. Record the actual receipt details separately.")
            if db.scalar(select(InvoicePayment.id).where(InvoicePayment.source_application_id == app.id)):
                raise HTTPException(409, "This placement's recorded advance has already been added to the ledger.")
            if (invoices.money(amount), paid_on.isoformat(), values["method"], values.get("reference") or "") != (
                    advance["amount"], advance["date"], advance["method"], advance["reference"]):
                raise HTTPException(422, "The advance details changed. Refresh and confirm the recorded advance again.")
        balance = summary(db, row, user)["balance"]
        if balance is None or amount > Decimal(balance):
            raise HTTPException(422, "The receipt exceeds the remaining agreed total, including GST.")
        payment = InvoicePayment(id=str(uuid.uuid4()), invoice_id=row.id, amount=amount, paid_on=paid_on,
            method=values["method"], reference=values.get("reference") or "", note=values.get("note") or "",
            idempotency_key=values["idempotencyKey"], source_stage_id=source,
            source_application_id=app.id if source else None, created_by=user.id)
        db.add(payment)
        db.add(AuditEntry(actor_id=user.id, action="Recorded candidate fee receipt", entity_type="candidate", entity_id=candidate_id,
            target=candidate.name, meta={"payment_id": payment.id, "invoice_id": row.id, "amount": invoices.money(amount),
                                       "source_stage_id": source}))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return state(db, candidate_id, user, application_id)


def void(db, candidate_id, payment_id, user, reason):
    ensure_can_see_invoices(user)
    try:
        candidate = invoices._candidate(db, candidate_id, user, lock=True)
        entry = db.scalar(select(InvoicePayment).join(CandidateInvoice).where(InvoicePayment.id == payment_id,
            CandidateInvoice.candidate_id == candidate_id).with_for_update(of=InvoicePayment).execution_options(populate_existing=True))
        if entry is None:
            raise HTTPException(404, "Receipt not found for this candidate")
        row = db.get(CandidateInvoice, entry.invoice_id)
        app_id = row.application_id
        if entry.voided_at:
            if entry.void_reason != reason:
                raise HTTPException(409, "This receipt has already been voided; its recorded reason is retained.")
        else:
            entry.voided_at, entry.voided_by, entry.void_reason = datetime.now(timezone.utc), user.id, reason
            db.add(AuditEntry(actor_id=user.id, action="Voided candidate fee receipt", entity_type="candidate", entity_id=candidate_id,
                target=candidate.name, meta={"payment_id": entry.id, "invoice_id": entry.invoice_id, "reason": reason,
                                           "amount": invoices.money(entry.amount)}))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return state(db, candidate_id, user, app_id)


def finance_summary(db, user, *, search="", status="all", limit=50, offset=0):
    """Aggregate the whole matching set (admin only); pagination never changes its totals."""
    ensure_can_see_invoices(user)
    config = db.get(InvoiceSettings, 1)
    paid = select(InvoicePayment.invoice_id, func.sum(InvoicePayment.amount).label("received")).where(
        InvoicePayment.voided_at.is_(None)).group_by(InvoicePayment.invoice_id).subquery()
    snapshot = CandidateInvoice.snapshot["amounts"]
    issued = CandidateInvoice.status == "issued"
    fee = case((issued, cast(snapshot["subtotal"].astext, Numeric)), else_=CandidateInvoice.service_fee)
    central_rate = func.coalesce(cast(snapshot["cgstRate"].astext, Numeric), Decimal(str(config.cgst_rate if config else 9)))
    state_rate = func.coalesce(cast(snapshot["sgstRate"].astext, Numeric), Decimal(str(config.sgst_rate if config else 9)))
    central = case((issued, cast(snapshot["cgst"].astext, Numeric)), else_=func.round(fee * central_rate / 100, 2))
    state_tax = case((issued, cast(snapshot["sgst"].astext, Numeric)), else_=func.round(fee * state_rate / 100, 2))
    gross = case((issued, cast(snapshot["total"].astext, Numeric)), else_=fee + central + state_tax)
    received = func.coalesce(paid.c.received, 0)
    balance = case((gross.is_(None), None), else_=func.greatest(gross - received, 0))
    credit = case((gross.is_(None), 0), else_=func.greatest(received - gross, 0))
    payment_status = case((balance == 0, "paid"), (received > 0, "partial"), else_="unpaid")
    name = func.coalesce(Candidate.name, CandidateInvoice.snapshot["recipient"]["name"].astext, "Deleted candidate")
    ref = func.coalesce(Candidate.ref, CandidateInvoice.snapshot["recipient"]["ref"].astext, "")
    query = select(CandidateInvoice.id.label("invoiceId"), CandidateInvoice.candidate_id.label("candidateId"),
        CandidateInvoice.application_id.label("applicationId"), name.label("name"), ref.label("ref"),
        CandidateInvoice.number.label("invoiceNo"), CandidateInvoice.status.label("status"),
        payment_status.label("paymentStatus"), fee.label("fee"), (central + state_tax).label("gst"), gross.label("total"),
        case((issued, gross), else_=0).label("totalInvoiced"), received.label("received"), balance.label("balance"), credit.label("credit"),
        CandidateInvoice.issued_at.label("issuedAt"), CandidateInvoice.attachment_id.label("attachmentId"),
        CandidateInvoice.created_at.label("createdAt")).outerjoin(Candidate, Candidate.id == CandidateInvoice.candidate_id)
    query = query.outerjoin(paid, paid.c.invoice_id == CandidateInvoice.id)
    search = search.strip()
    if search:
        escaped = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(name.ilike(escaped, escape="\\"), ref.ilike(escaped, escape="\\"),
                               CandidateInvoice.number.ilike(escaped, escape="\\")))
    if status != "all":
        query = query.where(payment_status == status)
    matches = query.subquery()
    sums = db.execute(select(func.count().label("count"), *[
        func.coalesce(func.sum(matches.c[field]), 0).label(label) for field, label in (
            ("fee", "feeSubtotal"), ("gst", "gst"), ("total", "agreedTotal"), ("totalInvoiced", "totalInvoiced"),
            ("received", "received"), ("balance", "balance"), ("credit", "credit"))])).mappings().one()
    totals = {key: int(value) if key == "count" else invoices.money(value) for key, value in sums.items()}
    rows = []
    for record in db.execute(select(matches).order_by(matches.c.createdAt.desc(), matches.c.invoiceId).offset(offset).limit(limit)).mappings():
        row = dict(record)
        for key in ("fee", "gst", "total", "totalInvoiced", "received", "balance", "credit"):
            row[key] = invoices.money(row[key]) if row[key] is not None else None
        row["issuedAt"] = row["issuedAt"].isoformat() if row["issuedAt"] else None
        attachment = row.pop("attachmentId")
        row["downloadUrl"] = f"/api/files/{attachment}" if attachment else None
        row.pop("createdAt")
        rows.append(row)
    return {"totals": totals, "rows": rows, "total": totals["count"], "limit": limit, "offset": offset}
