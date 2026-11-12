"""Admin-managed option lists for the candidate / application form (table `crm_form_options`, migration 0022).

The lists used to be hardcoded in the screens (service charges, interest level, marital status, ...). They are rows now, so
an administrator can rename, reorder, hide or add a choice and every open form follows through the live feed.

Two kinds of category (`CATEGORIES`):

* strict: the stored value must be an active option ("service_charges_status", "interest_level", "candidate_source"). A value a
  candidate already has stays valid and shown even after its option is hidden; only new choices are checked.
* suggestions (not strict): the lists are what a select offers, but free text stays allowed because documents read by the
  OCR produce wording of their own ("B.Tech Civil Engineering", "2 yrs").

`candidate_source` is backed by a PostgreSQL enum, so its values are fixed: only label, hint, order and visibility can change.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import cast, func, literal_column, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db.models import Candidate, FormOption

# key -> (label, strict, fixed values, Candidate column that stores the value or None)
CATEGORIES: dict[str, dict[str, Any]] = {
    "service_charges_status": {"label": "Service charges", "strict": True, "fixed": False, "column": "service_charges_status"},
    "interest_level": {"label": "Interest level", "strict": True, "fixed": False, "column": "interest_level"},
    "candidate_source": {"label": "Source", "strict": True, "fixed": True, "column": "source"},
    "job_duration_preference": {"label": "Job duration preference", "strict": False, "fixed": False, "column": "job_duration_preference"},
    "best_time_to_contact": {"label": "Best time to contact", "strict": False, "fixed": False, "column": "best_time_to_contact"},
    "marital_status": {"label": "Marital status", "strict": False, "fixed": False, "column": "marital_status"},
    "gender": {"label": "Gender", "strict": False, "fixed": False, "column": "gender"},
    "education_level": {"label": "Educational level / course", "strict": False, "fixed": False, "column": None},
    "language": {"label": "Languages", "strict": False, "fixed": False, "column": None},
}
# Never hidden or removed: the stored default of a column.
PROTECTED = {("service_charges_status", "not_discussed")}
TONES = ("neutral", "success", "info", "warning", "danger")
VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/+&()',-]{0,59}$")


def _rows(category: str, items: list[tuple]) -> list[dict]:
    """items: (value, label[, hint[, tone]]) in display order."""
    out = []
    for position, item in enumerate(items, start=1):
        value, label, *rest = item
        out.append({"category": category, "value": value, "label": label, "hint": rest[0] if len(rest) > 0 else None,
                    "tone": rest[1] if len(rest) > 1 else None, "sort_order": position * 10})
    return out


# The seeds; migration 0022 carries its own copy (a migration never imports application code) and a test keeps them equal.
DEFAULT_OPTIONS: list[dict] = [
    *_rows("service_charges_status", [
        ("not_discussed", "Not discussed", "Service charges have not been talked about yet", "neutral"),
        ("agreed", "Agreed", "The candidate agreed to the service charges", "success"),
        ("pending", "Pending", "The candidate is thinking it over", "warning"),
        ("declined", "Declined", "The candidate does not accept the service charges", "danger")]),
    *_rows("interest_level", [
        ("more_interested", "More interested", "Keen to go abroad now", "success"),
        ("interested", "Interested", "Wants to go and is open to offers", "info"),
        ("somewhat_interested", "Somewhat interested", "May go if the offer is right", "warning"),
        ("not_interested", "Not interested", "Does not want to go abroad", "danger")]),
    *_rows("candidate_source", [
        ("email", "Email", None, None), ("whatsapp", "WhatsApp", None, None), ("walk_in", "Walk-in", None, None),
        ("referral", "Referral / agency", None, None), ("other", "Other", None, None)]),
    *_rows("job_duration_preference", [
        ("1 year", "1 year"), ("2 years", "2 years"), ("3 years", "3 years"), ("4 years", "4 years"),
        ("5 years", "5 years"), ("Any", "Any duration")]),
    *_rows("best_time_to_contact", [
        ("Morning", "Morning", "9 am to 12 pm"), ("Afternoon", "Afternoon", "12 pm to 4 pm"),
        ("Evening", "Evening", "4 pm to 8 pm"), ("Anytime", "Anytime")]),
    *_rows("marital_status", [("Single", "Single"), ("Married", "Married"), ("Divorced", "Divorced"),
                              ("Widowed", "Widowed"), ("Separated", "Separated")]),
    *_rows("gender", [("male", "Male"), ("female", "Female"), ("other", "Other")]),
    *_rows("education_level", [(name, name) for name in (
        "10th / SSLC", "12th / HSC", "ITI", "Diploma", "B.E / B.Tech", "B.Sc", "B.Com", "BA", "BBA", "BCA",
        "M.E / M.Tech", "M.Sc", "M.Com", "MBA", "MCA", "Nursing", "Other")]),
    *_rows("language", [(name, name) for name in (
        "English", "Hindi", "Malayalam", "Tamil", "Telugu", "Kannada", "Arabic", "Urdu", "Bengali", "Marathi", "Gujarati",
        "Punjabi", "Nepali", "Sinhala")]),
]


# ------------------------------------------------------------------ reading

def option_out(row: FormOption) -> dict:
    return {"id": row.id, "value": row.value, "label": row.label, "hint": row.hint, "tone": row.tone,
            "sortOrder": row.sort_order, "isActive": row.is_active, "isSystem": row.is_system}


def all_options(db: Session, *, include_inactive: bool = False) -> dict:
    statement = select(FormOption).order_by(FormOption.category, FormOption.sort_order, FormOption.label)
    if not include_inactive:
        statement = statement.where(FormOption.is_active.is_(True))
    grouped: dict[str, list[dict]] = {key: [] for key in CATEGORIES}
    for row in db.scalars(statement):
        if row.category in grouped:
            grouped[row.category].append(option_out(row))
    return {"categories": [
        {"key": key, "label": meta["label"], "strict": meta["strict"], "fixedValues": meta["fixed"],
         "allowCustom": not meta["strict"], "options": grouped[key]}
        for key, meta in CATEGORIES.items()]}


def etag_of(payload: dict) -> str:
    return '"' + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24] + '"'


# ------------------------------------------------------------------ checking candidate values

def ensure_allowed(db: Session, category: str, value: Any, *, current: Any = None, default: Any = None) -> None:
    """Raise 422 unless `value` may be stored. Empty, unchanged and default values always pass; free-text categories too."""
    meta = CATEGORIES[category]
    if not meta["strict"] or value in (None, "") or value == current or value == default:
        return
    value = value.value if hasattr(value, "value") else value
    option = db.scalar(select(FormOption).where(FormOption.category == category, FormOption.value == value))
    if option is None or not option.is_active:
        raise HTTPException(422, f"{meta['label']}: choose one of the available options.")


def inactive_values(db: Session, category: str) -> set[str]:
    return set(db.scalars(select(FormOption.value).where(FormOption.category == category, FormOption.is_active.is_(False))))


# ------------------------------------------------------------------ usage (for "deactivate instead of delete")

def usage_count(db: Session, row: FormOption) -> int:
    column = CATEGORIES[row.category]["column"]
    if column:
        return db.scalar(select(func.count()).select_from(Candidate).where(getattr(Candidate, column) == row.value)) or 0
    if row.category == "language":
        return db.scalar(select(func.count()).select_from(Candidate).where(Candidate.languages.contains([row.value]))) or 0
    if row.category == "education_level":
        probe = func.jsonb_path_exists(Candidate.education, literal_column("'$[*] ? (@.level == $v)'"),
                                       cast(json.dumps({"v": row.value}), JSONB))
        return db.scalar(select(func.count()).select_from(Candidate).where(probe)) or 0
    return 0


# ------------------------------------------------------------------ data-derived suggestions

def suggestions(db: Session) -> dict:
    """Lists derived from the data already in the system, for free-text fields that have no fixed choices."""
    from app.services.parallel_reads import parallel_reads
    from app.db.models import Company, Designation, Order

    def countries(session):
        found: dict[str, int] = {}
        for column in (Order.country, Company.country):
            for name, count in session.execute(select(column, func.count()).where(column.is_not(None)).group_by(column)):
                found[name.strip()] = found.get(name.strip(), 0) + count
        for text_value in session.scalars(select(Candidate.country_preference).where(Candidate.country_preference.is_not(None)).distinct().limit(500)):
            for part in re.split(r"[,/;]", text_value):
                part = " ".join(part.split())
                if part:
                    found[part] = found.get(part, 0) + 1
        return [name for name, _ in sorted(found.items(), key=lambda pair: (-pair[1], pair[0].casefold())) if name][:100]

    def designations(session):
        return list(session.scalars(select(Designation.title).order_by(Designation.title).limit(300)))

    def skills(session):
        element = func.jsonb_array_elements_text(Candidate.skills).table_valued("value").alias("skill")
        label = func.trim(element.c.value)
        rows = session.execute(select(label, func.count().label("n")).select_from(Candidate.__table__.join(element, true()))
                               .where(label != "").group_by(label).order_by(func.count().desc(), label).limit(200)).all()
        return _dedupe([row[0] for row in rows])

    def institutes(session):
        element = func.jsonb_array_elements(Candidate.education).table_valued("value").alias("edu")
        label = func.trim(element.c.value.op("->>")("institute"))
        rows = session.execute(select(label, func.count().label("n")).select_from(Candidate.__table__.join(element, true()))
                               .where(label.is_not(None), label != "").group_by(label).order_by(func.count().desc(), label).limit(100)).all()
        return _dedupe([row[0] for row in rows])

    def companies(session):
        element = func.jsonb_array_elements(Candidate.work_history).table_valued("value").alias("job")
        label = func.trim(element.c.value.op("->>")("company"))
        rows = session.execute(select(label, func.count().label("n")).select_from(Candidate.__table__.join(element, true()))
                               .where(label.is_not(None), label != "").group_by(label).order_by(func.count().desc(), label).limit(100)).all()
        return _dedupe([row[0] for row in rows])

    found = parallel_reads(db, countries, designations, skills, institutes, companies)
    return dict(zip(("countries", "jobPreferences", "skills", "institutes", "companies"), found))


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out
