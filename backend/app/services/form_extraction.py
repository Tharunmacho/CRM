"""Read OCR output (Veris resume / passport results, kept whole in `ocr_data`) into the application-form columns.

Two halves:

* Parsing (`resume_values`, `passport_values`, `aadhaar_values`): defensive, dict-based, never raising. Veris output is validated by the
  extraction pipeline, but what is stored is a plain JSON blob that has been through older versions, hand-built test data
  and half-read documents, so every key may be missing, null, a different type or empty text.
* Applying (`apply_values`, `apply_stored_ocr`): the fill rule. A document read fills a field only when the field is
  empty or still marked "extracted" in `field_sources`; a value a person entered ("manual", or an old value with no mark
  that differs from the document) is never overwritten. The passport is the authority for identity and passport fields:
  a resume cannot replace a value that came from a passport. The Aadhaar card comes second for name, date of birth,
  gender and father's name (passport > Aadhaar > resume); a resume still wins everything about work and education, which
  an Aadhaar card never states. An Aadhaar address only fills an EMPTY current address.

`field_sources` (JSONB on the candidate) holds `{"personal.fatherName": "extracted"|"manual", ...}` plus two internal
bookkeeping keys that start with an underscore and never leave the server:
`_fromPassport` / `_fromAadhaar` (form fields whose extracted value came from a passport / an Aadhaar card), `_applied`
(ids of documents already applied, so a repeated save or backfill is a no-op and a stale primary document cannot undo a
newer upload) and `_aadhaar` (only the LAST FOUR digits of the Aadhaar number, so the form can show "XXXX XXXX 1234"
without ever reading the stored number; the full number stays in `ocr_data` and is never copied to a column).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, attributes

from app.integrations.ocr.mapping import (
    _DEMONYMS, _gender, _passport_no, _person_name, _unique, _years, parse_loose_date,
)

UNNAMED = "Name needs review"
# Documents that fill the form, in the order they are applied: later ones outrank earlier ones for identity fields.
SLOT_KINDS = ("resume", "aadhaar", "passport")
MAX_EDUCATION = 20
MAX_HISTORY = 30


@dataclass(frozen=True)
class Spec:
    key: str      # API path, e.g. "personal.fatherName" (the key used in fieldSources)
    column: str   # Candidate attribute
    kind: str     # text | date | list | int | rows


SPECS: dict[str, Spec] = {s.column: s for s in (
    Spec("personal.candidateName", "name", "text"),
    Spec("personal.fatherName", "father_name", "text"),
    Spec("personal.dateOfBirth", "dob", "date"),
    Spec("personal.maritalStatus", "marital_status", "text"),
    Spec("personal.gender", "gender", "text"),
    Spec("personal.nationality", "nationality", "text"),
    Spec("personal.languagesKnown", "languages", "list"),
    Spec("personal.mobileNumber", "phone", "text"),
    Spec("personal.currentAddress", "current_address", "text"),
    Spec("personal.whatsapp", "whatsapp", "text"),
    Spec("personal.email", "email", "text"),
    Spec("personal.currentCity", "current_city", "text"),
    Spec("passport.passportNumber", "passport_no", "text"),
    Spec("passport.placeOfIssue", "passport_place", "text"),
    Spec("passport.issueDate", "passport_issue", "date"),
    Spec("passport.expiryDate", "passport_expiry", "date"),
    Spec("education", "education", "rows"),
    Spec("experience.history", "work_history", "rows"),
    Spec("experience.totalExperience", "total_experience", "int"),
    Spec("experience.experienceIndia", "experience_india", "int"),
    Spec("experience.experienceAbroad", "gulf_experience", "int"),
    # Screening details and plain profile lists. A document read never produces most of these; they are here so that
    # a person's entry is stamped "manual" like every other form field, and so a resume's skills / job title follow the
    # same fill rule (empty or still "extracted", never over a person's entry).
    Spec("screening.nativePlace", "native_place", "text"),
    Spec("screening.familyMemberNumber", "family_member_number", "text"),
    Spec("screening.jobDurationPreference", "job_duration_preference", "text"),
    Spec("screening.countryPreference", "country_preference", "text"),
    Spec("screening.bestTimeToContact", "best_time_to_contact", "text"),
    Spec("screening.serviceChargesStatus", "service_charges_status", "text"),
    Spec("screening.interestLevel", "interest_level", "text"),
    Spec("screening.nextFollowUpDate", "next_follow_up_date", "date"),
    Spec("screening.skills", "skills", "list"),
    Spec("screening.jobPreference", "designations", "list"),
)}
BY_KEY = {spec.key: spec for spec in SPECS.values()}
# Identity and passport fields: the passport outranks a resume for these.
PASSPORT_AUTHORITY = frozenset({"name", "dob", "gender", "nationality", "father_name",
                                "passport_no", "passport_place", "passport_issue", "passport_expiry"})
# Identity fields an Aadhaar card outranks a resume for (the passport outranks both).
AADHAAR_AUTHORITY = frozenset({"name", "dob", "gender", "father_name"})
# An Aadhaar address is the registered one, not necessarily where the person lives now: it fills only an empty current address.
AADHAAR_FILLS_EMPTY_ONLY = frozenset({"current_address"})
# Values another candidate may already own; a read never moves one onto a second person.
IDENTITY = frozenset({"phone", "email", "passport_no"})
# Columns that exist only since the application form. The automatic hook that follows every save of ocr_data fills just
# these (the older columns are the business of the code that stored the document and may have been cleared on purpose);
# a form upload, the refresh button and the backfill fill every column.
NEW_COLUMNS = frozenset({"father_name", "marital_status", "current_address", "education", "work_history", "experience_india"})
# A reviewer may confirm that pages are one person while recording a DOB/gender
# discrepancy for staff to resolve.  In that narrow case raw OCR is retained, but
# a later refresh may only fill content that cannot change the person's identity.
REVIEWED_CONTENT_ONLY_COLUMNS = frozenset({"education", "work_history", "total_experience", "experience_india",
                                           "gulf_experience", "skills", "designations"})


# ------------------------------------------------------------------ tiny helpers

def _s(value: Any, limit: int = 200) -> str | None:
    """Collapsed text, or None. Numbers become text; containers and booleans are not text."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("\x00", " ").split())
    return cleaned[:limit] or None


def _d(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = _s(value, 40)
    return parse_loose_date(text) if text else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _first(entry: dict, *names: str, limit: int = 200) -> str | None:
    for name in names:
        found = _s(entry.get(name), limit)
        if found:
            return found
    return None


def _months(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PRESENT = re.compile(r"\b(?:present|current|currently|till\s+date|to\s+date|now|ongoing|pursuing|continuing|appearing)\b", re.I)


def _passed_year(end: str | None, start: str | None = None) -> int | None:
    """The year a course was completed: the last year in `end`. Still studying or unknown gives None."""
    for text in (end,):
        if text and not _PRESENT.search(text):
            years = _YEAR.findall(text)
            if years:
                return int(years[-1])
    if not end and start and not _PRESENT.search(start):
        # A single year given as the only date ("2016") is the year of passing on most resumes.
        years = _YEAR.findall(start)
        if len(years) == 1 and re.fullmatch(r"\s*(?:19|20)\d{2}\s*", start):
            return int(years[0])
    return None


# ------------------------------------------------------------------ resume

_MARITAL = {
    "single": "Single", "unmarried": "Single", "bachelor": "Single", "never married": "Single",
    "married": "Married", "divorced": "Divorced", "widowed": "Widowed", "widow": "Widowed", "widower": "Widowed",
    "separated": "Separated",
}


def normalize_marital(value: Any) -> str | None:
    text = _s(value, 60)
    if not text:
        return None
    key = re.sub(r"[^a-z ]", "", text.casefold()).strip()
    if key in _MARITAL:
        return _MARITAL[key]
    return text.title() if len(text) <= 30 else None


def education_rows(raw: dict, limit: int | None = MAX_EDUCATION) -> list[dict]:
    """The form's education rows; ``limit=None`` returns every distinct one (used to see what the form's capacity cuts off)."""
    rows: list[dict] = []
    seen: set[tuple] = set()
    for entry in _list(raw.get("education")):
        if isinstance(entry, str):
            level, institute, passed = _s(entry, 200), None, _passed_year(_s(entry), None)
        elif isinstance(entry, dict):
            level = _first(entry, "degree", "qualification", "course", "level", "title", "name", "program", "programme")
            institute = _first(entry, "institution", "institute", "university", "college", "school", "board", limit=300)
            place = _first(entry, "location", "address", "city", limit=120)
            if institute and place and place.casefold() not in institute.casefold():
                institute = f"{institute}, {place}"[:300]
            end = _first(entry, "end", "end_date", "to", "year", "passing_year", "year_of_passing", "graduation_year", "completed", limit=60)
            start = _first(entry, "start", "start_date", "from", limit=60)
            passed = _passed_year(end, start)
        else:
            continue
        if not (level or institute):
            continue
        fingerprint = ((level or "").casefold(), (institute or "").casefold(), passed)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append({"level": level, "passedYear": passed, "institute": institute})
        if limit and len(rows) >= limit:
            break
    if not rows:
        highest = _s(raw.get("highest_qualification"), 200)
        if highest:
            rows.append({"level": highest, "passedYear": None, "institute": None})
    return rows


def _period(entry: dict) -> str | None:
    start, end = _first(entry, "start", "start_date", "from", limit=40), _first(entry, "end", "end_date", "to", limit=40)
    human = _first(entry, "duration_human", "duration", limit=60)
    months = _months(entry.get("duration_months"))
    span = None
    if start or end:
        span = f"{start or '?'} - {end or 'Present'}"
    if span and human and months:
        return f"{span} ({human})"[:120]
    if span:
        return span[:120]
    return human[:120] if human and (months or entry.get("duration_months") is None) and human != "0 months" else None


def history_rows(raw: dict, limit: int | None = MAX_HISTORY) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()
    for entry in _list(raw.get("experience")):
        if not isinstance(entry, dict):
            continue
        company = _first(entry, "company", "employer", "organization", "organisation", "company_name")
        designation = _first(entry, "title", "designation", "position", "role", "job_title")
        if not (company or designation):
            continue
        country = _first(entry, "country", limit=80)
        if not country:
            location = _first(entry, "location", limit=120) or ""
            if entry.get("is_overseas") is not True and re.search(r"\bindia\b", location, re.I):
                country = "India"
        period = _period(entry)
        fingerprint = ((company or "").casefold(), (designation or "").casefold(), (period or "").casefold())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append({"company": company, "designation": designation, "country": country, "period": period})
        if limit and len(rows) >= limit:
            break
    return rows


def _address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parts = [" ".join(part.split()) for part in re.split(r"[\r\n]+", value.replace("\x00", " "))]
    joined = ", ".join(part.strip(" ,") for part in parts if part.strip(" ,"))
    return joined[:500] or None


def _experience_years(raw: dict) -> tuple[int | None, int | None, int | None]:
    entries = [e for e in _list(raw.get("experience")) if isinstance(e, dict)]
    total = _months(raw.get("total_experience_months")) or sum(_months(e.get("duration_months")) for e in entries)
    abroad = _months(raw.get("overseas_experience_months")) or sum(_months(e.get("duration_months")) for e in entries if e.get("is_overseas") is True)
    india = _months(raw.get("indian_experience_months"))
    if not india and total and total > abroad:
        india = total - abroad
    return _years(total), _years(india), _years(abroad)


def resume_values(raw: Any) -> dict[str, Any]:
    """Candidate-column values read from one stored Veris resume result. Anything not found is absent."""
    raw = _dict(raw)
    contact, info, passport = _dict(raw.get("contact")), _dict(raw.get("personal_info")), _dict(raw.get("passport_details"))
    out: dict[str, Any] = {}
    name = _person_name(_s(raw.get("name")))
    if name:
        out["name"] = name
    phones = [p for p in (_s(p, 40) for p in _list(contact.get("phones"))) if p]
    if phones:
        out["phone"] = phones[0]
    whatsapp = _s(contact.get("whatsapp"), 40)
    if whatsapp:
        out["whatsapp"] = whatsapp
    emails = [e for e in (_s(e, 255) for e in _list(contact.get("emails"))) if e]
    if emails:
        out["email"] = emails[0].lower()
    out_address = _address(contact.get("address"))
    if out_address:
        out["current_address"] = out_address
    father = _person_name(_first(info, "father_name", "fathers_name", "father"))
    if father:
        out["father_name"] = father
    marital = normalize_marital(info.get("marital_status"))
    if marital:
        out["marital_status"] = marital
    dob = _d(info.get("date_of_birth"))
    if dob:
        out["dob"] = dob
    gender = _gender(_s(info.get("gender"), 20))
    if gender:
        out["gender"] = gender
    nationality = _s(info.get("nationality"), 80)
    if nationality:
        out["nationality"] = nationality
    languages = _unique([l for l in (_s(l, 80) for l in _list(info.get("languages_known"))) if l])
    if languages:
        out["languages"] = languages[:30]
    skills = _unique([k for k in (_s(k, 200) for k in _list(raw.get("skills"))) if k])
    if skills:
        out["skills"] = skills[:200]
    designation = _s(raw.get("designation"), 200)
    if designation:
        out["designations"] = [designation]
    number = _passport_no(_s(passport.get("passport_number"), 40))
    if number:
        out["passport_no"] = number
    for column, key in (("passport_issue", "date_of_issue"), ("passport_expiry", "date_of_expiry")):
        found = _d(passport.get(key))
        if found:
            out[column] = found
    place = _s(passport.get("place_of_issue"), 120)
    if place:
        out["passport_place"] = place
    education = education_rows(raw)
    if education:
        out["education"] = education
    history = history_rows(raw)
    if history:
        out["work_history"] = history
    total, india, abroad = _experience_years(raw)
    for column, years in (("total_experience", total), ("experience_india", india), ("gulf_experience", abroad)):
        if years:
            out[column] = years
    city = _city_of(raw)
    if city:
        out["current_city"] = city
    return out


def _city_of(raw: dict) -> str | None:
    """The resume's current city by the same rule as the Add Candidate form (needs the validated model)."""
    try:
        from veriis.models import ResumeResponse
        from app.integrations.ocr.mapping import current_city_from
        return current_city_from(ResumeResponse.model_validate({"request_id": "x", "processing_time_ms": 0, **raw}))
    except Exception:
        return None


# ------------------------------------------------------------------ passport

def _label_key(label: Any) -> str:
    return " ".join(re.findall(r"[a-z]+", str(label or "").casefold()))


def _printed(raw: dict, wanted: tuple[str, ...], contains: tuple[str, ...] = (), keep_lines: bool = False) -> str | None:
    """First non-empty printed value whose label is one of `wanted` or contains one of `contains`."""
    for item in _list(raw.get("fields")):
        if not isinstance(item, dict):
            continue
        key = _label_key(item.get("label"))
        if key in wanted or any(word in key.split() for word in contains):
            value = item.get("value").strip()[:400] if keep_lines and isinstance(item.get("value"), str) else _s(item.get("value"), 400)
            if value:
                return value
    return None


def passport_values(raw: Any) -> dict[str, Any]:
    """Candidate-column values read from one stored Veris passport result."""
    raw = _dict(raw)
    mrz = _dict(raw.get("mrz"))
    out: dict[str, Any] = {}
    words = f"{_s(mrz.get('given_names')) or ''} {_s(mrz.get('surname')) or ''}".replace("<", " ").split()
    if words:
        out["name"] = " ".join(w.capitalize() for w in words)
    elif _printed(raw, ("name", "given names", "given name")):
        given = _printed(raw, ("given names", "given name", "name"))
        surname = _printed(raw, ("surname",))
        out["name"] = _person_name(" ".join(part for part in (given, surname) if part))
    dob = _d(mrz.get("date_of_birth")) or _d(_printed(raw, ("date of birth", "dob")))
    if dob:
        out["dob"] = dob
    gender = _gender(_s(mrz.get("sex"), 20)) or _gender(_printed(raw, ("sex", "gender")))
    if gender:
        out["gender"] = gender
    code = (_s(mrz.get("nationality"), 10) or "").upper()
    nationality = _DEMONYMS.get(code, code) or _printed(raw, ("nationality",))
    if nationality:
        out["nationality"] = nationality
    number = _passport_no(_s(mrz.get("passport_number"), 40)) or _passport_no(_printed(raw, ("passport no", "passport number", "passport no.")))
    if number:
        out["passport_no"] = number
    issue = _d(mrz.get("date_of_issue")) or _d(_printed(raw, ("date of issue",)))
    if issue:
        out["passport_issue"] = issue
    expiry = _d(mrz.get("expiry_date")) or _d(_printed(raw, ("date of expiry", "expiry date", "date of expiration")))
    if expiry:
        out["passport_expiry"] = expiry
    place = _printed(raw, ("place of issue",))
    if place:
        out["passport_place"] = place[:120]
    father = _printed(raw, ("name of father", "name of father legal guardian", "fathers name", "father name",
                            "father s name", "name of father or legal guardian", "legal guardian"), contains=("father",))
    if father:
        out["father_name"] = _person_name(father[:200])
    address = _printed(raw, ("address", "permanent address", "present address", "residential address"), keep_lines=True)
    if address:
        out["current_address"] = _address(address)
    return {key: value for key, value in out.items() if value not in (None, "", [])}


# ------------------------------------------------------------------ Aadhaar

# "S/O Ramesh", "D/o: Ramesh Kumar", "W/O. Ramesh", "Son of Ramesh". "C/O" (care of) names no relation, so it is not matched.
_RELATION = re.compile(r"^\s*(?:[sdw]\s*/\s*o|son\s+of|daughter\s+of|wife\s+of)(?![a-z])\s*[:.\-]?\s*(?P<name>.*)$", re.I)


def relative_name(value: Any) -> str | None:
    """The name after an S/O, D/O or W/O prefix (father, or husband for W/O), or None when the text names no relation."""
    text = _s(value, 300)
    match = _RELATION.match(text) if text else None
    if not match:
        return None
    name = re.split(r"[,;]", match.group("name"), maxsplit=1)[0].strip(" .:-")
    return _person_name(name[:200]) if len(name) >= 2 and not any(ch.isdigit() for ch in name) else None


def aadhaar_values(raw: Any) -> dict[str, Any]:
    """Candidate-column values read from one stored Veris Aadhaar result.

    Name, date of birth (a bare year of birth is not a date and is left blank), gender, the address (with a leading
    S/O, D/O or W/O clause removed) and the father's name from the S/O, D/O or W/O line. The Aadhaar number, VID,
    enrolment id and the card's mobile number are NEVER returned: there is no column for them (the mobile number is
    often a relative's or an old SIM).
    """
    identity = _dict(_dict(raw).get("aadhaar"))
    out: dict[str, Any] = {}
    name = _person_name(_s(identity.get("name")))
    if name:
        out["name"] = name
    dob = _d(identity.get("date_of_birth"))
    if dob:
        out["dob"] = dob
    gender = _gender(_s(identity.get("gender"), 20))
    if gender:
        out["gender"] = gender
    address_text = identity.get("address")
    father = relative_name(identity.get("care_of")) or (relative_name(address_text) if isinstance(address_text, str) else None)
    if father:
        out["father_name"] = father
    address = _address(address_text)
    if address and _RELATION.match(address) and "," in address:
        address = address.split(",", 1)[1].strip(" ,") or None
    if address and not _RELATION.match(address):
        out["current_address"] = address
    return out


def aadhaar_last4(raw: Any) -> str | None:
    """The last four digits of the card's number, from the full number or its masked form; None when neither is usable."""
    identity = _dict(_dict(raw).get("aadhaar"))
    digits = re.sub(r"\D", "", str(identity.get("aadhaar_number") or ""))
    if len(digits) == 12:
        return digits[-4:]
    shown = re.search(r"(\d{4})\s*$", str(identity.get("masked_aadhaar_number") or ""))
    return shown.group(1) if shown else None


def masked_aadhaar(last4: str | None) -> str | None:
    return f"XXXX XXXX {last4}" if last4 and re.fullmatch(r"\d{4}", last4) else None


def values_for(kind: str, raw: Any) -> dict[str, Any]:
    if kind == "resume":
        return resume_values(raw)
    if kind == "passport":
        return passport_values(raw)
    if kind == "aadhaar":
        return aadhaar_values(raw)
    return {}


# ------------------------------------------------------------------ applying

@dataclass
class ApplyResult:
    filled: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def merge(self, other: "ApplyResult") -> None:
        for name in ("filled", "kept", "conflicts"):
            current = getattr(self, name)
            current.extend(item for item in getattr(other, name) if item not in current)


def sources_of(candidate) -> dict:
    raw = candidate.field_sources
    return dict(raw) if isinstance(raw, dict) else {}


def public_sources(sources: dict | None) -> dict[str, str]:
    return {k: v for k, v in (sources or {}).items() if not k.startswith("_") and v in ("extracted", "manual")}


def _norm(kind: str, value: Any) -> Any:
    if kind == "text":
        return (_s(value, 1000) or "").casefold()
    if kind == "list":
        return [x.casefold() for x in (value or [])]
    if kind == "rows":
        return [{k: (str(v).casefold() if v is not None else None) for k, v in sorted(row.items())} for row in (value or []) if isinstance(row, dict)]
    return value


def _same(spec: Spec, current: Any, new: Any) -> bool:
    return _norm(spec.kind, current) == _norm(spec.kind, new)


def _is_empty(spec: Spec, current: Any, source: str | None) -> bool:
    if spec.column == "name":
        return not (current or "").strip() or current.strip() == UNNAMED
    if spec.kind == "int":
        return not current and source != "manual"
    if spec.kind == "text":
        return not (current or "").strip()
    return current in (None, "", [], {})


def _clean(spec: Spec, value: Any) -> Any:
    if spec.kind == "text":
        return _s(value, 1000) if spec.column == "current_address" else _s(value, 255)
    if spec.kind == "date":
        return _d(value)
    if spec.kind == "int":
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 80 else None
    if spec.kind == "list":
        return [x for x in (_s(i, 200) for i in _list(value)) if x] or None
    if spec.kind == "rows":
        return [row for row in _list(value) if isinstance(row, dict)] or None
    return value


def _conflicts(db: Session | None, candidate, column: str, value: Any) -> bool:
    """True when another candidate already owns this phone / e-mail / passport number."""
    if db is None:
        return False
    from app.services.candidate_identity import identity_matches
    try:
        return bool(identity_matches(db, {column: value}, exclude_id=getattr(candidate, "id", None), lock=False))
    except Exception:
        return False


def apply_values(candidate, kind: str, values: dict[str, Any], db: Session | None = None, *, fill_all: bool = True) -> ApplyResult:
    """Apply one document's values to a candidate under the fill rule. `kind` is resume | passport.

    With `fill_all=False` only the application-form-only columns (NEW_COLUMNS) are filled; every other column is merely
    stamped "extracted" when it already equals the document (so it counts as read, not typed)."""
    from app.services.document_identity import identity_conflicts
    conflicts = identity_conflicts(candidate, values, db)
    if conflicts:
        if hasattr(candidate, "needs_review"):
            candidate.needs_review = True
        return ApplyResult(conflicts=[SPECS[key].key if key in SPECS else "document.identity" for key in conflicts])
    sources = sources_of(candidate)
    from_passport = set(sources.get("_fromPassport") or [])
    from_aadhaar = set(sources.get("_fromAadhaar") or [])
    result = ApplyResult()
    for column, spec in SPECS.items():
        if column not in values:
            continue
        new = _clean(spec, values[column])
        if new in (None, "", [], {}):
            continue
        current = getattr(candidate, column)
        source = sources.get(spec.key)
        same = _same(spec, current, new)
        empty = _is_empty(spec, current, source)
        authority = column in PASSPORT_AUTHORITY
        if kind != "passport" and authority and spec.key in from_passport and not empty:
            if not same:
                result.kept.append(spec.key)
            continue
        # Passport > Aadhaar > resume for identity: a resume never replaces what an Aadhaar card gave.
        if kind == "resume" and column in AADHAAR_AUTHORITY and spec.key in from_aadhaar and not empty:
            if not same:
                result.kept.append(spec.key)
            continue
        if same and not empty:
            if source is None:
                sources[spec.key] = "extracted"
                if kind == "passport" and authority:
                    from_passport.add(spec.key)
                elif kind == "aadhaar" and column in AADHAAR_AUTHORITY:
                    from_aadhaar.add(spec.key)
            continue
        if kind == "aadhaar" and column in AADHAAR_FILLS_EMPTY_ONLY and not empty:
            if not same:
                result.kept.append(spec.key)
            continue
        if not fill_all and column not in NEW_COLUMNS:
            continue
        if empty or source == "extracted":
            if column in IDENTITY and _conflicts(db, candidate, column, new):
                result.conflicts.append(spec.key)
                continue
            setattr(candidate, column, new)
            sources[spec.key] = "extracted"
            if kind == "passport" and authority:
                from_passport.add(spec.key)
                from_aadhaar.discard(spec.key)
            elif kind == "aadhaar" and column in AADHAAR_AUTHORITY:
                from_aadhaar.add(spec.key)
                from_passport.discard(spec.key)
            else:
                from_passport.discard(spec.key)
                from_aadhaar.discard(spec.key)
            result.filled.append(spec.key)
        else:
            result.kept.append(spec.key)
    sources["_fromPassport"] = sorted(from_passport)
    if sources.get("_fromPassport") == []:
        sources.pop("_fromPassport")
    if from_aadhaar:
        sources["_fromAadhaar"] = sorted(from_aadhaar)
    else:
        sources.pop("_fromAadhaar", None)
    if sources != sources_of(candidate):
        candidate.field_sources = sources
    return result


def note_aadhaar(candidate, raw: Any) -> None:
    """Remember ONLY the last four digits of an Aadhaar card's number (`_aadhaar` in field_sources), so the application form
    can show "XXXX XXXX 1234" from a column that is already loaded. The number itself stays in `ocr_data`."""
    last4 = aadhaar_last4(raw)
    if not last4:
        return
    sources = sources_of(candidate)
    if sources.get("_aadhaar") != {"last4": last4}:
        sources["_aadhaar"] = {"last4": last4}
        candidate.field_sources = sources


def aadhaar_of(candidate) -> str | None:
    """"XXXX XXXX 1234" when an Aadhaar card was read for this candidate, else None. Never the full number."""
    noted = _dict(sources_of(candidate).get("_aadhaar"))
    return masked_aadhaar(noted.get("last4") if isinstance(noted.get("last4"), str) else None)


def document_id(raw: Any) -> str:
    try:
        blob = json.dumps(raw, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(raw)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def mark_applied(candidate, kind: str, raw: Any) -> None:
    sources = sources_of(candidate)
    applied = {k: list(v) for k, v in _dict(sources.get("_applied")).items() if isinstance(v, list)}
    ids = applied.setdefault(kind, [])
    identifier = document_id(raw)
    if identifier not in ids:
        ids.append(identifier)
        del ids[:-20]
        sources["_applied"] = applied
        candidate.field_sources = sources


def already_applied(candidate, kind: str, raw: Any) -> bool:
    return document_id(raw) in (_dict(sources_of(candidate).get("_applied")).get(kind) or [])


def mark_stored_applied(candidate) -> None:
    """Treat every document already stored on the candidate as applied (its values were set when it was stored, or a
    person has had the chance to change them), so replaying the primary document later cannot undo a newer upload."""
    for kind in SLOT_KINDS:
        for raw in stored_documents(candidate.ocr_data, kind):
            mark_applied(candidate, kind, raw)


def apply_document(candidate, kind: str, raw: Any, db: Session | None = None) -> ApplyResult:
    """Apply a freshly read document, then remember it. Marks the candidate so the ocr_data hook does not repeat it."""
    result = apply_values(candidate, kind, values_for(kind, raw), db)
    if kind == "aadhaar" and not result.conflicts:
        note_aadhaar(candidate, raw)
    mark_applied(candidate, kind, raw)
    _flags(candidate)["form_explicit"] = True
    return result


def _flags(candidate) -> dict:
    try:
        return attributes.instance_state(candidate).info
    except Exception:  # not a mapped instance (unit tests use plain objects)
        return candidate.__dict__.setdefault("_form_flags", {})


def is_explicit(candidate) -> bool:
    return bool(_flags(candidate).get("form_explicit"))


def stored_documents(ocr_data: Any, kind: str) -> list[dict]:
    """Every stored document of a kind, oldest first: the primary one, then the additional ones."""
    ocr_data = _dict(ocr_data)
    documents = [ocr_data[kind]] if isinstance(ocr_data.get(kind), dict) else []
    for entry in _list(ocr_data.get("additional_documents")):
        if isinstance(entry, dict) and isinstance(entry.get(kind), dict):
            documents.append(entry[kind])
    return documents


def apply_stored_ocr(candidate, db: Session | None = None, *, force: bool = False, fill_all: bool | None = None) -> ApplyResult:
    """Fill the form from the OCR output stored on the candidate.

    Default: the primary document of each kind, once (documents already applied are skipped, so this is safe to call on
    every save). `force=True` replays every stored document of each kind in the order stored (resume first, passport
    last, as the passport outranks the resume), which is what the manual refresh and the backfill use. `fill_all`
    defaults to `force`: the automatic path fills only the application-form-only columns, a replay fills every column
    (the backfill passes False to touch the new columns alone).
    """
    fill_all = force if fill_all is None else fill_all
    before = {column: copy.deepcopy(getattr(candidate, column)) for column in SPECS}
    result = ApplyResult()
    ocr_data = candidate.ocr_data
    reviewed = _dict(ocr_data).get("person_split")
    content_only = isinstance(reviewed, dict) and bool(reviewed.get("contentOnlyAutofill")) \
        and bool(reviewed.get("ocrIdentityConflictFields"))
    for kind in SLOT_KINDS:
        documents = stored_documents(ocr_data, kind)
        if not force:
            documents = documents[:1]
        for raw in documents:
            if not force and already_applied(candidate, kind, raw):
                continue
            values = values_for(kind, raw)
            if content_only:
                values = {key: value for key, value in values.items() if key in REVIEWED_CONTENT_ONLY_COLUMNS}
            applied = apply_values(candidate, kind, values, db, fill_all=fill_all)
            result.merge(applied)
            if kind == "aadhaar" and not applied.conflicts:
                note_aadhaar(candidate, raw)
            mark_applied(candidate, kind, raw)
    # A replay may set a field once per stored document; report only the ones whose final value differs from the start.
    result.filled = [spec.key for column, spec in SPECS.items() if spec.key in result.filled and before[column] != getattr(candidate, column)]
    return result


# ------------------------------------------------------------------ typed-by-hand helpers

def form_columns_from(body) -> dict[str, Any]:
    """The application-form-only columns of a validated candidate body, ready for Candidate(...)."""
    def rows(items):
        return [row for row in (item.model_dump() for item in items) if any(v not in (None, "") for v in row.values())]
    return {
        "father_name": body.father_name, "marital_status": body.marital_status, "current_address": body.current_address,
        "education": rows(body.education), "work_history": rows(body.work_history), "experience_india": body.experience_india,
        "native_place": body.native_place, "family_member_number": body.family_member_number,
        "job_duration_preference": body.job_duration_preference, "country_preference": body.country_preference,
        "best_time_to_contact": body.best_time_to_contact, "service_charges_status": body.service_charges_status,
        "interest_level": body.interest_level, "next_follow_up_date": body.next_follow_up_date,
    }


def mark_manual(candidate, columns) -> None:
    """A person entered or changed these columns: later document reads must not overwrite them.
    A column that is now empty (or a zero count) loses its mark so a document may fill it again."""
    sources = sources_of(candidate)
    from_passport = set(sources.get("_fromPassport") or [])
    from_aadhaar = set(sources.get("_fromAadhaar") or [])
    for column in columns:
        spec = SPECS.get(column)
        if spec is None:
            continue
        value = getattr(candidate, column)
        if value in (None, "", [], {}, 0) or (spec.kind == "text" and not str(value).strip()):
            sources.pop(spec.key, None)
        else:
            sources[spec.key] = "manual"
        from_passport.discard(spec.key)
        from_aadhaar.discard(spec.key)
    if from_passport:
        sources["_fromPassport"] = sorted(from_passport)
    else:
        sources.pop("_fromPassport", None)
    if from_aadhaar:
        sources["_fromAadhaar"] = sorted(from_aadhaar)
    else:
        sources.pop("_fromAadhaar", None)
    if sources != sources_of(candidate):
        candidate.field_sources = sources
