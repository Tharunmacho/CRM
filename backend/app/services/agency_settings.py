"""The agency's identity and defaults, read from the database instead of the source code.

Two tables hold them (migration 0017): the singleton `agency_settings` row (name, default currencies, home calling
code, default acknowledgement text) and the `currencies` list. They are optional at run time on purpose: when they
are missing (a database that has not had the migration yet) or empty, the compiled-in last-resort values below keep
orders, the stage-8 advance and phone matching working exactly as before. They are never the first choice.

The managed job category list (migration 0019, table `job_categories`) rides along but has its own presence flag: a
database that has 0017 and not yet 0019 keeps its agency settings and simply reports no category list.

The timezone and the 48-hour task rule are not settings: moving the timezone shifts every worker's "agency day"
bookkeeping and the SLA is fixed by the product requirements. They are reported read-only for display.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.orm import Session

from app.core import config
from app.db.models import AgencySettings, Application, ApplicationStage, Currency, Designation, JobCategory, Order
from app.domain.stages import CURRENCY_CODES, FALLBACK_ADVANCE_CURRENCY, StageOptions

SLA_HOURS = 48  # fixed by the PRD; shown read-only

FALLBACK_NAME = "Recruitment CRM"
FALLBACK_ORDER_CURRENCY = "AED"
FALLBACK_HOME_CALLING_CODE = "+91"
FALLBACK_ACKNOWLEDGEMENT = ("Thank you for contacting us. We have received and saved your candidate documents. "
                            "Our recruitment team will review them and contact you when there is a suitable next step.")
CALLING_CODE = re.compile(r"^\+\d{1,4}$")
CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")

# Whether the two tables exist: asked once, then remembered (a positive answer for long, a negative one briefly,
# so a migration applied while the process runs is noticed).
_PRESENT_FOR, _ABSENT_FOR = 600.0, 20.0
_lock = threading.Lock()
_state: dict[str, Any] = {"present": None, "until": 0.0}
_cat_state: dict[str, Any] = {"present": None, "until": 0.0}
CATEGORY_MIN, CATEGORY_MAX = 2, 120
# The home calling code as plain digits, for phone matching code that has no database handle.
_HOME_REFRESH = 60.0
_home: dict[str, Any] = {"digits": FALLBACK_HOME_CALLING_CODE.lstrip("+"), "at": 0.0}


def reset_cache() -> None:
    """Forget what was learned about the tables and the calling code (tests, and after a restore)."""
    with _lock:
        _state.update(present=None, until=0.0)
        _cat_state.update(present=None, until=0.0)
        _home.update(digits=FALLBACK_HOME_CALLING_CODE.lstrip("+"), at=0.0)
        _public.update(value=None, until=0.0)


def is_missing_table(error: BaseException) -> bool:
    """True for PostgreSQL's 'relation does not exist' raised for one of the two settings tables."""
    original = getattr(error, "orig", error)
    return getattr(original, "sqlstate", None) == "42P01" and any(name in str(original) for name in ("agency_settings", "currencies"))


def is_missing_categories(error: BaseException) -> bool:
    """True for PostgreSQL's 'relation does not exist' raised for the job category table."""
    original = getattr(error, "orig", error)
    return getattr(original, "sqlstate", None) == "42P01" and "job_categories" in str(original)


def mark_categories_present(present: bool) -> None:
    with _lock:
        _cat_state.update(present=present, until=time.monotonic() + (_PRESENT_FOR if present else _ABSENT_FOR))


def assume_categories_present() -> bool:
    return not (_cat_state["present"] is False and time.monotonic() < _cat_state["until"])


def categories_present(db: Session) -> bool:
    if _cat_state["present"] is not None and time.monotonic() < _cat_state["until"]:
        return bool(_cat_state["present"])
    if db.get_bind().dialect.name != "postgresql":
        return True
    found = bool(db.scalar(text("SELECT to_regclass('job_categories') IS NOT NULL")))
    mark_categories_present(found)
    return found


def mark_present(present: bool) -> None:
    with _lock:
        _state.update(present=present, until=time.monotonic() + (_PRESENT_FOR if present else _ABSENT_FOR))


def assume_present() -> bool:
    """Optimistic view for the bootstrap read: try the tables unless they were recently found missing."""
    return not (_state["present"] is False and time.monotonic() < _state["until"])


def tables_present(db: Session) -> bool:
    if _state["present"] is not None and time.monotonic() < _state["until"]:
        return bool(_state["present"])
    if db.get_bind().dialect.name != "postgresql":
        return True
    found = bool(db.scalar(text("SELECT to_regclass('agency_settings') IS NOT NULL AND to_regclass('currencies') IS NOT NULL")))
    mark_present(found)
    return found


# ---------------------------------------------------------------- the wire shape

def currency_out(row: dict) -> dict:
    return {"code": row["code"], "name": row["name"], "symbol": row.get("symbol"),
            "active": bool(row["active"]), "sortOrder": row.get("sort_order", 0)}


def category_out(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "active": bool(row["is_active"]), "sortOrder": row.get("sort_order", 0)}


def build_payload(agency: dict | None, currencies: list[dict] | None, categories: list[dict] | None = None) -> dict:
    """The settings object the desk receives, from raw rows (snake_case) or from nothing at all.

    `categories` is None when the server has no category table yet (the desk then falls back to the categories already
    used on job titles and shows no editor); a list, even an empty one, means the table exists.
    """
    available = agency is not None
    if available:
        values = {
            "name": agency["name"], "shortName": agency.get("short_name"),
            "defaultOrderCurrency": agency["default_order_currency"],
            "defaultAdvanceCurrency": agency["default_advance_currency"],
            "homeCallingCode": agency["home_calling_code"],
            "defaultNationality": agency.get("default_nationality"),
            "acknowledgementText": agency["acknowledgement_text"],
            "updatedAt": agency.get("updated_at"), "updatedBy": agency.get("updated_by"),
        }
    else:
        values = {
            "name": FALLBACK_NAME, "shortName": None, "defaultOrderCurrency": FALLBACK_ORDER_CURRENCY,
            "defaultAdvanceCurrency": FALLBACK_ADVANCE_CURRENCY, "homeCallingCode": FALLBACK_HOME_CALLING_CODE,
            "defaultNationality": None, "acknowledgementText": FALLBACK_ACKNOWLEDGEMENT, "updatedAt": None, "updatedBy": None,
        }
    rows = sorted((currency_out(item) for item in currencies or []), key=lambda item: (item["sortOrder"], item["code"]))
    if not rows:
        rows = [{"code": code, "name": code, "symbol": None, "active": True, "sortOrder": (index + 1) * 10}
                for index, code in enumerate(CURRENCY_CODES)]
        available = False
    category_rows = sorted((category_out(item) for item in categories or []), key=lambda item: (item["sortOrder"], item["name"].lower()))
    return {**values, "currencies": rows, "categories": category_rows, "categoriesEditable": categories is not None,
            "timezone": config.get_settings().agency_timezone, "slaHours": SLA_HOURS,
            "editable": available}


def note_snapshot(agency: dict | None) -> None:
    """A fresh read of the row (the bootstrap) also refreshes the calling code kept for phone matching."""
    if agency and CALLING_CODE.match(agency.get("home_calling_code") or ""):
        with _lock:
            _home.update(digits=agency["home_calling_code"].lstrip("+"), at=time.monotonic())


# ---------------------------------------------------------------- reads (own transaction state untouched)

def agency_row(db: Session) -> AgencySettings | None:
    return db.get(AgencySettings, 1) if tables_present(db) else None


def read_payload(db: Session) -> dict:
    if not tables_present(db):
        return build_payload(None, None)
    agency = db.get(AgencySettings, 1)
    currencies = db.scalars(select(Currency)).all()
    categories = db.scalars(select(JobCategory)).all() if categories_present(db) else None
    raw = None if agency is None else {
        "name": agency.name, "short_name": agency.short_name, "default_order_currency": agency.default_order_currency,
        "default_advance_currency": agency.default_advance_currency, "home_calling_code": agency.home_calling_code,
        "default_nationality": agency.default_nationality, "acknowledgement_text": agency.acknowledgement_text,
        "updated_at": agency.updated_at, "updated_by": agency.updated_by,
    }
    note_snapshot(raw)
    return build_payload(raw, [{"code": c.code, "name": c.name, "symbol": c.symbol, "active": c.active,
                                "sort_order": c.sort_order} for c in currencies],
                         None if categories is None else [{"id": c.id, "name": c.name, "is_active": c.is_active, "sort_order": c.sort_order} for c in categories])


def active_currency_codes(db: Session) -> frozenset[str]:
    """Exactly the active codes; the compiled-in list only when the table is missing or empty."""
    if tables_present(db):
        rows = db.execute(select(Currency.code, Currency.active)).all()
        if rows:
            return frozenset(code for code, active in rows if active)
    return frozenset(CURRENCY_CODES)


def default_order_currency(db: Session) -> str:
    row = agency_row(db)
    return row.default_order_currency if row else FALLBACK_ORDER_CURRENCY


def default_advance_currency(db: Session) -> str:
    row = agency_row(db)
    return row.default_advance_currency if row else FALLBACK_ADVANCE_CURRENCY


def stage_options(db: Session) -> StageOptions:
    return StageOptions(currencies=active_currency_codes(db), default_currency=default_advance_currency(db))


def default_acknowledgement(db: Session) -> str:
    row = agency_row(db)
    return row.acknowledgement_text if row else FALLBACK_ACKNOWLEDGEMENT


def public_name(db: Session) -> dict | None:
    """The name shown before anyone signs in (the sign-in page). Cached briefly; never raises."""
    now = time.monotonic()
    cached = _public.get("value")
    if cached is not None and now < _public["until"]:
        return cached or None
    try:
        if not tables_present(db):
            value: dict = {}
        else:
            row = db.execute(select(AgencySettings.name, AgencySettings.short_name).where(AgencySettings.id == 1)).one_or_none()
            value = {"name": row.name, "shortName": row.short_name} if row else {}
    except Exception:
        db.rollback()
        return None
    _public.update(value=value, until=now + _HOME_REFRESH)
    return value or None


_public: dict[str, Any] = {"value": None, "until": 0.0}


def forget_public_name() -> None:
    _public.update(value=None, until=0.0)


# ---------------------------------------------------------------- phone matching

def home_digits(db: Session | None = None) -> str:
    """The agency's home calling code as digits ('91'), refreshed from the row at most once a minute."""
    if db is not None and time.monotonic() - _home["at"] > _HOME_REFRESH:
        code = db.scalar(select(AgencySettings.home_calling_code).where(AgencySettings.id == 1)) if tables_present(db) else None
        with _lock:
            if code and CALLING_CODE.match(code):
                _home.update(digits=code.lstrip("+"))
            _home["at"] = time.monotonic()
    return _home["digits"]


# ---------------------------------------------------------------- currency usage

def currency_in_use(db: Session, code: str) -> bool:
    """True when any order, stored advance or saved stage-8 draft carries this code."""
    return bool(db.scalar(select(or_(
        exists().where(Order.currency == code),
        exists().where(or_(ApplicationStage.amount_currency == code, ApplicationStage.data["amountCurrency"].astext == code)),
        exists().where(Application.draft["amountCurrency"].astext == code),
    ))))


def active_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Currency).where(Currency.active.is_(True))) or 0


# ---------------------------------------------------------------- job categories

def clean_category_name(value: str | None) -> str:
    """Trimmed, with runs of spaces collapsed, so 'Oil  and gas ' and 'Oil and gas' are the same name."""
    return re.sub(r"\s+", " ", (value or "").strip())


def category_in_use(db: Session, name: str) -> int:
    """How many job titles carry this category (compared ignoring case)."""
    return db.scalar(select(func.count()).select_from(Designation).where(func.lower(Designation.category) == name.lower())) or 0


def canonical_category(db: Session, value: str | None) -> str | None:
    """What to store on a job title: the managed spelling when the text matches one ignoring case, else the text as typed.

    Unknown names are kept on purpose (old titles and free typing stay valid). Empty text becomes None.
    """
    name = clean_category_name(value)
    if not name:
        return None
    if categories_present(db):
        managed = db.scalar(select(JobCategory.name).where(func.lower(JobCategory.name) == name.lower()))
        if managed:
            return managed
    return name
