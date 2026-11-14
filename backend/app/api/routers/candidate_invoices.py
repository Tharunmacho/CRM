"""Authorized fee entry and automatic ticket-triggered candidate invoices."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.services import candidate_invoices as service
from app.services.invoice_access import invoice_admin

router = APIRouter(prefix="/api", tags=["candidate-invoices"])


class FeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serviceFee: Decimal | None = Field(ge=5000, le=30000, max_digits=7, decimal_places=2)
    applicationId: str | None = Field(default=None, max_length=36)
    lockVersion: int = Field(ge=0)

    @field_validator("serviceFee", mode="before")
    @classmethod
    def not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Enter the actual service fee before GST")
        return value


class ReconcileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applicationId: str | None = Field(default=None, max_length=36)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    companyName: str = Field(min_length=1, max_length=200)
    address: str = Field(min_length=1, max_length=1000)
    gstin: str = Field(min_length=15, max_length=15, pattern=r"^[A-Z0-9]{15}$")
    invoicePrefix: str = Field(default="", max_length=13, pattern=r"^[A-Za-z0-9/-]*$")
    nextNumber: int = Field(ge=1, le=999999999)
    cgstRate: Decimal = Field(ge=0, le=50, max_digits=4, decimal_places=2)
    sgstRate: Decimal = Field(ge=0, le=50, max_digits=4, decimal_places=2)
    sacCode: str = Field(min_length=1, max_length=20)
    bankName: str = Field(default="", max_length=200)
    bankAccount: str = Field(default="", max_length=100)
    bankIfsc: str = Field(default="", max_length=30)
    placeOfSupply: str = Field(default="Tamil Nadu", max_length=120)
    phone: str = Field(default=service.TEMPLATE["phone"], max_length=150)
    email: str = Field(default=service.TEMPLATE["email"], max_length=150)
    website: str = Field(default=service.TEMPLATE["website"], max_length=150)
    cin: str = Field(default=service.TEMPLATE["cin"], max_length=40)
    lockVersion: int = Field(ge=0)


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"


@router.get("/candidates/{candidate_id}/invoice")
def read_invoice(candidate_id: str, response: Response, applicationId: str | None = Query(default=None, max_length=36),
                 db: Session = Depends(get_db), user: User = Depends(invoice_admin)):
    _private(response)
    return service.state(db, candidate_id, user, applicationId)


@router.put("/candidates/{candidate_id}/invoice")
def save_fee(candidate_id: str, body: FeeIn, response: Response, applicationId: str | None = Query(default=None, max_length=36),
             db: Session = Depends(get_db), user: User = Depends(invoice_admin)):
    _private(response)
    if body.applicationId and applicationId and body.applicationId != applicationId:
        raise HTTPException(422, "Choose one application for this invoice")
    return service.save_fee(db, candidate_id, user, body.serviceFee, body.lockVersion, body.applicationId or applicationId)


@router.post("/candidates/{candidate_id}/invoice/reconcile")
def reconcile(candidate_id: str, body: ReconcileIn, response: Response,
              db: Session = Depends(get_db), user: User = Depends(invoice_admin)):
    _private(response)
    return service.reconcile(db, candidate_id, user, body.applicationId)


@router.get("/invoice-settings")
def read_settings(response: Response, db: Session = Depends(get_db), user: User = Depends(invoice_admin)):
    _private(response)
    return service.settings_state(db, user)


@router.put("/invoice-settings")
def save_settings(body: SettingsIn, response: Response, db: Session = Depends(get_db), user: User = Depends(invoice_admin)):
    _private(response)
    return service.save_settings(db, user, body.model_dump(exclude={"lockVersion"}), body.lockVersion)
