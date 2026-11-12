"""Assigned-candidate receipts and authorized finance summaries."""
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services import invoice_payments as service

router = APIRouter(prefix="/api", tags=["invoice-payments"])


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    applicationId: str | None = Field(default=None, min_length=1, max_length=36)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    date: date
    method: Literal["cash", "bank", "upi", "cheque"]
    reference: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)
    idempotencyKey: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    sourceStageId: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("amount", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Enter the actual received amount")
        return value


class VoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=1000)


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"


@router.get("/candidates/{candidate_id}/invoice/payments")
def read_payments(candidate_id: str, response: Response, applicationId: str | None = Query(default=None, max_length=36),
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    _private(response)
    return service.state(db, candidate_id, user, applicationId)


@router.post("/candidates/{candidate_id}/invoice/payments")
def record_payment(candidate_id: str, body: PaymentIn, response: Response,
                   applicationId: str | None = Query(default=None, max_length=36),
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    _private(response)
    if body.applicationId and applicationId and body.applicationId != applicationId:
        raise HTTPException(422, "Choose one application for this receipt")
    return service.record(db, candidate_id, user, body.model_dump(), body.applicationId or applicationId)


@router.post("/candidates/{candidate_id}/invoice/payments/{payment_id}/void")
def void_payment(candidate_id: str, payment_id: str, body: VoidIn, response: Response,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    _private(response)
    return service.void(db, candidate_id, payment_id, user, body.reason)


@router.get("/invoices/finance-summary")
def finance_summary(response: Response, search: str = Query(default="", max_length=200),
                    status: Literal["all", "unpaid", "partial", "paid"] = "all",
                    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000),
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    _private(response)
    return service.finance_summary(db, user, search=search, status=status, limit=limit, offset=offset)
