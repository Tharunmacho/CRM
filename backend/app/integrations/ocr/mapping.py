"""
Veris OCR output → the add-candidate form.

The rule throughout: a value the OCR could not be sure of is left blank rather than
guessed. A blank field is obvious to the recruiter checking the form; a wrong one that
looks plausible gets saved.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from veriis.models import PassportResponse, ResumeResponse

from app.api.schemas import Wire


class OcrFields(Wire):
    """Named after the form's own fields, so the page can apply them without a lookup."""

    name: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    dob: date | None = None
    gender: str | None = None
    nationality: str | None = None
    languages: list[str] | None = None
    passport_no: str | None = None
    passport_issue: date | None = None
    passport_expiry: date | None = None
    passport_place: str | None = None
    designation: str | None = None
    total_experience: int | None = None
    gulf_experience: int | None = None
    skills: list[str] | None = None


# Day first throughout: these are Indian resumes, where 05/12/1990 is the 5th of December.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d-%b-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
)

_ORDINAL = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)

# The countries the agency recruits from. Anything else keeps its ISO code, which is
# still readable and easier to correct than a wrong guess.
_DEMONYMS = {
    "IND": "Indian",
    "NPL": "Nepalese",
    "LKA": "Sri Lankan",
    "BGD": "Bangladeshi",
    "PAK": "Pakistani",
    "PHL": "Filipino",
}


def parse_loose_date(value: str | None) -> date | None:
    """A resume's free-text date, or None when it does not read as one exact day."""
    if not value:
        return None
    cleaned = _ORDINAL.sub(r"\1", " ".join(value.split()))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _text(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


def _gender(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    if v in {"m", "male"}:
        return "male"
    if v in {"f", "female"}:
        return "female"
    return None


def _unique(values: list[str]) -> list[str] | None:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        v = " ".join(v.split())
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    return out or None


def _person_name(value: str | None) -> str | None:
    """Capitals become "Arjun Menon"; mixed case is trusted, so "McKay" survives."""
    name = _text(value)
    if name and name.isupper():
        return " ".join(w.capitalize() for w in name.split())
    return name


def _years(months: int) -> int | None:
    """Completed years. Zero months means nothing was read, not a fresher."""
    return months // 12 if months > 0 else None


def _passport_no(value: str | None) -> str | None:
    return "".join((value or "").split()).upper() or None


def resume_fields(r: ResumeResponse, designations: list[str]) -> tuple[OcrFields, list[str]]:
    """
    `designations` are the titles the form's select offers. A resume's title only fills
    the field when it matches one of them, or the select would show "Choose one" and
    save nothing.
    """
    wanted = (r.designation or "").strip().casefold()
    designation = next((d for d in designations if d.strip().casefold() == wanted), None) if wanted else None

    email = r.contact.emails[0].strip().lower() if r.contact.emails else None
    pp = r.passport_details
    info = r.personal_info

    fields = OcrFields(
        name=_person_name(r.name),
        phone=_text(r.contact.phones[0]) if r.contact.phones else None,
        whatsapp=_text(r.contact.whatsapp),
        email=email or None,
        dob=parse_loose_date(info.date_of_birth),
        gender=_gender(info.gender),
        nationality=_text(info.nationality),
        languages=_unique(info.languages_known),
        passport_no=_passport_no(pp.passport_number),
        passport_issue=parse_loose_date(pp.date_of_issue),
        passport_expiry=parse_loose_date(pp.date_of_expiry),
        passport_place=_text(pp.place_of_issue),
        designation=designation,
        total_experience=_years(r.total_experience_months),
        gulf_experience=_years(r.overseas_experience_months),
        skills=_unique(r.skills),
    )
    return fields, list(r.warnings)


def passport_fields(p: PassportResponse) -> tuple[OcrFields, list[str]]:
    mrz = p.mrz
    # MRZ names are upper case; the form wants them as a person would write them.
    words = f"{mrz.given_names or ''} {mrz.surname or ''}".replace("<", " ").split()
    name = " ".join(w.capitalize() for w in words) or None

    code = (mrz.nationality or "").strip().upper()

    fields = OcrFields(
        name=name,
        dob=mrz.date_of_birth,
        gender=_gender(mrz.sex),
        nationality=_DEMONYMS.get(code, code) or None,
        passport_no=_passport_no(mrz.passport_number),
        passport_issue=mrz.date_of_issue,
        passport_expiry=mrz.expiry_date,
    )

    warnings = list(p.warnings)
    if not mrz.all_check_digits_valid:
        warnings.append(
            "The passport's check digits do not add up, so the number or a date may be "
            "misread. Compare them against the scan."
        )
    return fields, warnings


def trim_raw(response: Any) -> dict[str, Any]:
    """
    The Veris output as stored on the candidate, minus the per-line word boxes: they
    are layout data nothing here reads, and most of the payload's size.
    """
    data: dict[str, Any] = response.model_dump(mode="json")
    for page in data.get("pages") or []:
        page.pop("lines", None)
    return data


def resume_text_from(ocr_data: dict[str, Any] | None) -> str | None:
    """The resume's page text, which is what the Data Bank searches."""
    pages = ((ocr_data or {}).get("resume") or {}).get("pages") or []
    texts = [t.strip() for t in (p.get("text") or "" for p in pages) if t.strip()]
    return "\n\n".join(texts) or None
