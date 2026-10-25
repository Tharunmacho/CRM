"""Agency settings and the managed currency list.

Everyone signed in reads them (they are also part of /api/bootstrap, so the desk needs no extra request);
only an administrator changes them. Every change is written to the audit log with the user and time, and the
change feed announces the `settings` topic so every open screen refreshes.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.deps import current_user, require_role
from app.db.models import AgencySettings, AuditEntry, Currency, Role, User
from app.db.session import get_db
from app.services import agency_settings as agency

router = APIRouter(prefix="/api/settings", tags=["Agency settings"])
admin = require_role(Role.admin)

NOT_INSTALLED = "Agency settings are not available yet. Ask your administrator to apply the latest database update."
IN_USE = "This currency is used by existing orders. Mark it inactive instead."


class Loose(BaseModel):
    """Types only; the endpoint checks the values so every message is plain English and tied to a field."""
    model_config = ConfigDict(extra="forbid")


class SettingsIn(Loose):
    name: str | None = None
    shortName: str | None = None
    defaultOrderCurrency: str | None = None
    defaultAdvanceCurrency: str | None = None
    homeCallingCode: str | None = None
    defaultNationality: str | None = None
    acknowledgementText: str | None = None


class CurrencyIn(Loose):
    code: str | None = None
    name: str | None = None
    symbol: str | None = None
    active: bool | None = None
    sortOrder: int | None = None


def _refuse(errors: dict[str, str]) -> None:
    if errors:
        raise HTTPException(422, {"message": "Check the highlighted fields.",
                                  "errors": [{"field": field, "message": message} for field, message in errors.items()]})


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _require_tables(db: Session) -> None:
    if not agency.tables_present(db):
        raise HTTPException(503, NOT_INSTALLED)


def _audit(db: Session, user: User, action: str, target: str, kind: str, entity_id: str, meta: dict | None = None) -> None:
    db.add(AuditEntry(actor_id=user.id, action=action, target=target, entity_type=kind, entity_id=entity_id, meta=meta))


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A related record changed while saving. Refresh and try again.") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "The settings could not be saved. Please try again.") from None


@router.get("/public")
def public_profile(db: Session = Depends(get_db)) -> dict:
    """Only the agency's name, for the sign-in page. No session needed; nothing else is disclosed."""
    return agency.public_name(db) or {}


@router.get("")
def read_settings(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return agency.read_payload(db)


@router.put("")
def save_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    _require_tables(db)
    errors: dict[str, str] = {}
    name = _clean(body.name)
    if not name:
        errors["name"] = "Enter the agency name."
    elif len(name) > 120:
        errors["name"] = "Use 120 characters or fewer."
    short = _clean(body.shortName) or None
    if short and len(short) > 60:
        errors["shortName"] = "Use 60 characters or fewer."
    active = {code for code, on in db.execute(select(Currency.code, Currency.active)).all() if on}
    chosen = {}
    for field, label in (("defaultOrderCurrency", "order"), ("defaultAdvanceCurrency", "advance")):
        code = _clean(getattr(body, field)).upper()
        chosen[field] = code
        if code not in active:
            errors[field] = f"Choose an active currency for the default {label} currency."
    calling = re.sub(r"\s+", "", _clean(body.homeCallingCode))
    if not agency.CALLING_CODE.match(calling):
        errors["homeCallingCode"] = "Enter a plus sign and 1 to 4 digits, for example +91."
    nationality = _clean(body.defaultNationality) or None
    if nationality and len(nationality) > 80:
        errors["defaultNationality"] = "Use 80 characters or fewer."
    text = _clean(body.acknowledgementText)
    if len(text) < 10:
        errors["acknowledgementText"] = "Write at least 10 characters."
    elif len(text) > 2000:
        errors["acknowledgementText"] = "Use 2,000 characters or fewer."
    elif "\x00" in text:
        errors["acknowledgementText"] = "The text cannot contain hidden special characters."
    _refuse(errors)

    row = db.scalar(select(AgencySettings).where(AgencySettings.id == 1).with_for_update())
    new = row is None
    if new:
        row = AgencySettings(id=1)
    values = {"name": name, "short_name": short, "default_order_currency": chosen["defaultOrderCurrency"],
              "default_advance_currency": chosen["defaultAdvanceCurrency"], "home_calling_code": calling,
              "default_nationality": nationality, "acknowledgement_text": text}
    changes = {}
    for key, value in values.items():
        previous = getattr(row, key)
        if previous != value:
            # The acknowledgement text can be long; the audit log records that it changed, not the whole text.
            changes[key] = {"changed": True} if key == "acknowledgement_text" else {"from": previous, "to": value}
            setattr(row, key, value)
    if new or changes:
        row.updated_by = user.id
        row.updated_at = func.now()
        db.add(row)
        _audit(db, user, "Created agency settings" if new else "Updated agency settings", name, "agency_settings", "1", {"changes": changes})
        _commit(db)
        agency.forget_public_name()
    return agency.read_payload(db)


def _currency_errors(body: CurrencyIn, *, creating: bool) -> tuple[dict[str, str], dict]:
    errors: dict[str, str] = {}
    code = _clean(body.code).upper()
    if creating and not agency.CURRENCY_CODE.match(code):
        errors["code"] = "Enter the 3-letter currency code, for example AED."
    name = _clean(body.name)
    if not name:
        errors["name"] = "Enter the currency name."
    elif len(name) > 80:
        errors["name"] = "Use 80 characters or fewer."
    symbol = _clean(body.symbol) or None
    if symbol and len(symbol) > 8:
        errors["symbol"] = "Use 8 characters or fewer."
    if body.sortOrder is not None and not 0 <= body.sortOrder <= 100000:
        errors["sortOrder"] = "Enter a number from 0 to 100,000."
    return errors, {"code": code, "name": name, "symbol": symbol}


@router.post("/currencies", status_code=201)
def add_currency(body: CurrencyIn, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    _require_tables(db)
    errors, values = _currency_errors(body, creating=True)
    _refuse(errors)
    if db.get(Currency, values["code"]) is not None:
        raise HTTPException(409, f"{values['code']} is already in the list.")
    order = body.sortOrder if body.sortOrder is not None else (db.scalar(select(func.max(Currency.sort_order))) or 0) + 10
    db.add(Currency(**values, active=True if body.active is None else body.active, sort_order=order))
    _audit(db, user, "Added currency", values["code"], "currency", values["code"], {"name": values["name"]})
    _commit(db)
    return agency.read_payload(db)


def _locked_currency(db: Session, code: str) -> Currency:
    row = db.scalar(select(Currency).where(Currency.code == code.upper()).with_for_update())
    if row is None:
        raise HTTPException(404, "Currency not found.")
    return row


def _default_use(db: Session, code: str) -> str | None:
    row = db.get(AgencySettings, 1)
    if row is None:
        return None
    if row.default_order_currency == code:
        return "the default order currency"
    if row.default_advance_currency == code:
        return "the default advance currency"
    return None


@router.put("/currencies/{code}")
def change_currency(code: str, body: CurrencyIn, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    _require_tables(db)
    errors, values = _currency_errors(body, creating=False)
    _refuse(errors)
    row = _locked_currency(db, code)
    active = row.active if body.active is None else body.active
    if row.active and not active:
        used_as = _default_use(db, row.code)
        if used_as:
            raise HTTPException(409, f"{row.code} is {used_as}. Choose another default before you mark it inactive.")
        if agency.active_count(db) <= 1:
            raise HTTPException(409, "Keep at least one active currency.")
    changes = {}
    for key, value in (("name", values["name"]), ("symbol", values["symbol"]), ("active", active),
                       ("sort_order", row.sort_order if body.sortOrder is None else body.sortOrder)):
        if getattr(row, key) != value:
            changes[key] = {"from": getattr(row, key), "to": value}
            setattr(row, key, value)
    if changes:
        row.updated_at = func.now()
        _audit(db, user, "Updated currency", row.code, "currency", row.code, {"changes": changes})
        _commit(db)
    return agency.read_payload(db)


@router.delete("/currencies/{code}")
def delete_currency(code: str, db: Session = Depends(get_db), user: User = Depends(admin)) -> dict:
    _require_tables(db)
    row = _locked_currency(db, code)
    used_as = _default_use(db, row.code)
    if used_as:
        raise HTTPException(409, f"{row.code} is {used_as}. Choose another default before you delete it.")
    if agency.currency_in_use(db, row.code):
        raise HTTPException(409, IN_USE)
    if row.active and agency.active_count(db) <= 1:
        raise HTTPException(409, "Keep at least one active currency.")
    db.execute(delete(Currency).where(Currency.code == row.code))
    _audit(db, user, "Deleted currency", row.code, "currency", row.code, {"name": row.name})
    _commit(db)
    return agency.read_payload(db)
