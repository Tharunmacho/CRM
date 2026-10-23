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

from veriis.models import AadhaarResponse, PassportResponse, ResumeResponse

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
    current_city: str | None = None
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


_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat", "haryana",
    "himachal pradesh", "jharkhand", "karnataka", "kerala", "madhya pradesh", "maharashtra", "manipur",
    "meghalaya", "mizoram", "nagaland", "odisha", "orissa", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "tamilnadu", "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal", "delhi", "new delhi",
    "jammu and kashmir", "ladakh", "puducherry", "pondicherry", "chandigarh",
}
_COUNTRY_WORDS = {"india", "bharat"}
_PIN = re.compile(r"\b\d{6}\b")
_PRESENT = re.compile(r"\b(?:present|current|currently|till\s+date|to\s+date|now|ongoing|date)\b", re.I)


def _city_name(value: str) -> str | None:
    value = " ".join(value.split())
    if not value or len(value) > 60 or any(c.isdigit() for c in value):
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", value):
        return None
    if value.casefold() in _STATES or value.casefold() in _COUNTRY_WORDS:
        return None
    return value.title() if value.isupper() else value


def current_city_from(r: ResumeResponse) -> str | None:
    """A city only when the resume states one unambiguously; otherwise blank.

    An address qualifies when the segment before its state (optionally followed by a
    PIN and the country) is a plain place name. Failing that, the location of the
    job the resume says is current, when that job is in India.
    """
    address = r.contact.address or ""
    segments = [" ".join(_PIN.sub(" ", part).split()).strip(" -.") for part in re.split(r"[,\n]", address)]
    segments = [part for part in segments if part and part.casefold() not in _COUNTRY_WORDS]
    if len(segments) >= 2 and segments[-1].casefold() in _STATES:
        city = _city_name(segments[-2])
        if city:
            return city
    for entry in r.experience:
        if entry.is_overseas or not entry.location or not (entry.end is None or _PRESENT.search(entry.end or "")):
            continue
        parts = [" ".join(part.split()) for part in entry.location.split(",") if part.strip()]
        parts = [part for part in parts if part.casefold() not in _COUNTRY_WORDS]
        if parts and len(parts) <= 2 and (len(parts) == 1 or parts[-1].casefold() in _STATES):
            city = _city_name(parts[0])
            if city:
                return city
    return None


def _tokens(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


def match_designation(title: str | None, designations: list[str]) -> str | None:
    """The master title for a resume's own wording, or None unless it is clear.

    Exact (case/punctuation-insensitive) first; otherwise the longest master title whose
    every word appears in the resume title ("Senior Mechanical Engineer" -> "Mechanical
    Engineer"). Two equally specific candidates are ambiguous, so nothing is chosen.
    """
    wanted = _tokens(title or "")
    if not wanted:
        return None
    exact = [d for d in designations if _tokens(d) == wanted]
    if exact:
        return exact[0]
    contained = [(len(_tokens(d)), d) for d in designations if _tokens(d) and set(_tokens(d)) <= set(wanted)]
    if not contained:
        return None
    best = max(size for size, _ in contained)
    top = {d for size, d in contained if size == best}
    return next(iter(top)) if len(top) == 1 else None


def _low_confidence(kind: str, pages) -> list[str]:
    """Veris reports 0 when it has no per-page confidence; only a real, low value counts."""
    low = [p.page_number for p in pages or [] if 0 < getattr(p, "average_confidence", 0) < 0.5]
    if not low:
        return []
    listed = ", ".join(str(n) for n in low[:10])
    return [f"The OCR returned low confidence for {kind} page(s) {listed}. Compare the details against the original."]


def resume_fields(r: ResumeResponse, designations: list[str]) -> tuple[OcrFields, list[str]]:
    """
    `designations` are the titles the form's select offers. A resume's title only fills
    the field when it matches one of them (exactly, or by every word of a master title
    appearing in it), or the select would show "Choose one" and save nothing. The
    resume's own wording is always kept in the stored Veris output.

    Not copied to a Candidate column (kept in the stored verbatim Veris output): address, linkedin,
    github, social, websites, extra emails/phones, father/mother name, place of birth,
    religion, marital status, industry, highest qualification, experience entries,
    Indian/overseas breakdowns, projects, education, achievements, certifications.
    """
    designation = match_designation(r.designation, designations)

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
        current_city=current_city_from(r),
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
    return fields, [*r.warnings, *_low_confidence("resume", r.pages)]


def _printed(p: PassportResponse, *labels: str) -> str | None:
    """The first non-empty printed value whose label is one of `labels`."""
    wanted = {" ".join(re.findall(r"[a-z]+", label.casefold())) for label in labels}
    for item in p.fields:
        if " ".join(re.findall(r"[a-z]+", item.label.casefold())) in wanted and _text(item.value):
            return _text(item.value)
    return None


def passport_fields(p: PassportResponse) -> tuple[OcrFields, list[str]]:
    """
    Candidate columns come only from the MRZ block. Not copied (kept in raw): document
    type, issuing country, surname/given names as separate values, personal number,
    per-field check digits, raw_mrz and confidence. The visual `fields` supply only the
    printed Place of Issue and Date of Issue (never the identity columns).
    """
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
        # The machine-readable zone carries neither the place nor (usually) the date of
        # issue; those are printed on the page and come from the visual fields.
        passport_issue=mrz.date_of_issue or parse_loose_date(_printed(p, "Date of Issue")),
        passport_expiry=mrz.expiry_date,
        passport_place=_printed(p, "Place of Issue"),
    )

    warnings = [*p.warnings, *_low_confidence("passport", p.pages)]
    if p.mrz_source != "mrz":
        warnings.append(
            "The passport details were read from the printed page, not its machine-readable "
            "zone. Compare every value against the scan."
        )
    if not mrz.all_check_digits_valid:
        warnings.append(
            "The passport's check digits do not add up, so the number or a date may be "
            "misread. Compare them against the scan."
        )
    return fields, warnings


def aadhaar_fields(response: AadhaarResponse) -> tuple[OcrFields, list[str]]:
    """
    Only stated candidate details. Never copied to a column (kept in raw): the Aadhaar
    number and its validity, masked number, year of birth, address, care_of, pincode, VID,
    enrollment id and document side.
    """
    identity = response.aadhaar
    fields = OcrFields(
        name=_person_name(identity.name),
        # The mobile number linked to an Aadhaar is often the holder's relative's or an old
        # SIM, and is masked on many copies: it is neither the candidate's phone nor an
        # identity key. It stays in the stored Veris output only.
        dob=identity.date_of_birth,
        gender=identity.gender,
    )
    warnings = [*response.warnings, *_low_confidence("Aadhaar", response.pages)]
    if identity.year_of_birth and not identity.date_of_birth:
        warnings.append("Only a birth year was read from Aadhaar. Enter the full date of birth manually.")
    if identity.aadhaar_number_valid is False:
        warnings.append("The Aadhaar number did not pass validation. Compare it against the original document.")
    # A full address does not establish a separate city, and the issuer does
    # not establish citizenship. Neither field is inferred from the card.
    return fields, warnings


def resume_text_from(ocr_data: dict[str, Any] | None) -> str | None:
    """The resume's page text, which is what the Data Bank searches."""
    pages = ((ocr_data or {}).get("resume") or {}).get("pages") or []
    pages = sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("page_number") if type(p.get("page_number")) is int else 0)
    texts = [t.strip() for t in (p.get("text") for p in pages) if isinstance(t, str) and t.strip()]
    return "\n\n".join(texts) or None
