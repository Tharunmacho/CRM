"""
Veris OCR output → the add-candidate form.

The rule throughout: a value the OCR could not be sure of is left blank rather than
guessed. A blank field is obvious to the recruiter checking the form; a wrong one that
looks plausible gets saved.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
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
    # Application-form details (see services/form_extraction.py for how they are read).
    father_name: str | None = None
    marital_status: str | None = None
    current_address: str | None = None
    experience_india: int | None = None
    education: list[dict[str, Any]] | None = None
    work_history: list[dict[str, Any]] | None = None


# Day first throughout (see parse_loose_date): these are Indian resumes, where 05/12/1990 is the 5th of December.
_ORDINAL = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)

# ISO 3166-1 alpha-3 code -> demonym for the countries the agency recruits from. Any other
# code is stored exactly as printed on the passport, which is easier to correct than a
# wrong guess.
_DEMONYMS = {
    "IND": "Indian",
    "NPL": "Nepalese",
    "LKA": "Sri Lankan",
    "BGD": "Bangladeshi",
    "PAK": "Pakistani",
    "PHL": "Filipino",
    "IDN": "Indonesian",
    "KEN": "Kenyan",
    "UGA": "Ugandan",
    "ETH": "Ethiopian",
    "EGY": "Egyptian",
    "NGA": "Nigerian",
    "GHA": "Ghanaian",
}


_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december")
_MONTH_BY_WORD = {**{name: index for index, name in enumerate(_MONTH_NAMES, 1)},
                  **{name[:3]: index for index, name in enumerate(_MONTH_NAMES, 1)}, "sept": 9}
_DATE_LABEL = re.compile(r"^\s*(?:date\s*of\s*birth|birth\s*date|birthday|d\s*\.?\s*o\s*\.?\s*b\.?)\s*[:;\-–—.=]*\s*", re.IGNORECASE)
_ISO_DATE = r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"
_NUMERIC_DATE = r"(?<![\d/.-])(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4}|\d{2})(?![\d/-])"
_DAY_MONTH_YEAR = r"(?<!\d)(\d{1,2})[-/. ]?([a-z]{3,9})\.?[-/. ]?(\d{4}|\d{2})(?!\d)"
_MONTH_DAY_YEAR = r"(?<![a-z])([a-z]{3,9})\.?[-/. ]?(\d{1,2})[-/. ](\d{4}|\d{2})(?!\d)"


def _date_text(value: Any) -> str:
    """One line of text for the date patterns: label, ordinal, comma, "of", "(29 years)", odd spacing and an apostrophe year removed."""
    text = unicodedata.normalize("NFKC", str(value))
    text = _DATE_LABEL.sub("", text)
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    text = re.sub(r"\b(?:age|aged)\b.*$", " ", text, flags=re.IGNORECASE)
    text = _ORDINAL.sub(r"\1", text)
    text = re.sub(r"\bof\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[’'`´]\s*(\d{2})(?!\d)", r" \1", text)
    text = re.sub(r"\s*([/\-.])\s*", r"\1", text)
    return " ".join(text.replace(",", " ").lower().split()).strip(" .:;-")


def _expand_year(text: str, role: str | None) -> int | None:
    """A four-digit year as is. A two-digit year only when the role says which century is plausible: a date of birth is a
    person aged 16 to 90; any other date (issue, expiry) falls within 40 years back and 20 years ahead. None otherwise."""
    if len(text) == 4:
        return int(text)
    if role is None:
        return None
    this_year = date.today().year
    for century in (2000, 1900):
        year = century + int(text)
        if (16 <= this_year - year <= 90) if role == "birth" else (this_year - 40 <= year <= this_year + 20):
            return year
    return None


def _day(year: int | None, month: int, day: int) -> date | None:
    if year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _find_date(text: str, role: str | None, whole: bool) -> date | None:
    """The first exact day in already-cleaned `text` (all of it when `whole`). Day first throughout, except that a figure
    above 12 in the middle can only be the day (03/25/1995 is 25 March)."""
    find = (lambda pattern: re.fullmatch(pattern, text)) if whole else (lambda pattern: re.search(pattern, text))
    match = find(_ISO_DATE)
    if match:
        return _day(int(match[1]), int(match[2]), int(match[3]))
    match = find(_NUMERIC_DATE)
    if match:
        first, second = int(match[1]), int(match[2])
        year = _expand_year(match[3], role)
        found = _day(year, second, first)
        return found if found or first > 12 or second <= 12 else _day(year, first, second)
    match = find(_DAY_MONTH_YEAR)
    if match and match[2] in _MONTH_BY_WORD:
        return _day(_expand_year(match[3], role), _MONTH_BY_WORD[match[2]], int(match[1]))
    match = find(_MONTH_DAY_YEAR)
    if match and match[1] in _MONTH_BY_WORD:
        return _day(_expand_year(match[3], role), _MONTH_BY_WORD[match[1]], int(match[2]))
    return None


def parse_loose_date(value: str | None, role: str | None = None) -> date | None:
    """A resume's free-text date, or None when it does not read as one exact day.

    Reads 1995-03-12, 12/03/1995, 12-03-1995, 12.03.1995, 12 / 03 / 1995, 12 Mar 1995, 12th March, 1995, 12-Mar-95,
    March 12 1995, Mar'95 style years and a leading "DOB:" / "Date of Birth:" label, day first. A two-digit year is read only
    when `role` is given: "birth" (a person aged 16 to 90) or "other" (an issue / expiry date). A bare year is not a date."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    text = _date_text(value)
    return _find_date(re.sub(r"[t ]\d{1,2}:\d{2}.*$", "", text) if re.match(r"\d{4}-", text) else text, role, whole=True) if text else None


def find_date_in(text: str, role: str | None = None) -> date | None:
    """The first exact day written anywhere in a short piece of text (the stretch after a "Date of birth" label)."""
    return _find_date(_date_text(text), role, whole=False) if text else None


def _text(value: str | None) -> str | None:
    value = " ".join((value or "").split())
    return value or None


_MALE_WORDS = {"m", "male", "man", "boy", "gent", "gents", "gentleman"}
_FEMALE_WORDS = {"f", "female", "woman", "girl", "lady", "ladies"}


def _gender(value: str | None) -> str | None:
    """male / female from "Male", "M", "MALE.", "Sex: Male", "Male (M)"; None when it says neither (or both)."""
    words = re.findall(r"[a-z]+", (value or "").lower())
    male, female = any(w in _MALE_WORDS for w in words), any(w in _FEMALE_WORDS for w in words)
    if male != female and len(words) <= 4:
        return "male" if male else "female"
    return None


_NUMBER_RUN = re.compile(r"\+?\(?\d[\d\s().\-]*")
_LOOSE_MOBILE = re.compile(r"(?<!\d)(?:(?:\+|00)?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")


def phone_rank(number: str) -> int:
    """0 not a phone number, 1 a landline or a short number, 2 a number that can be a mobile (the digits decide, 7 to 15 of them)."""
    digits = re.sub(r"\D", "", number)
    digits = digits[2:] if digits.startswith("00") else digits
    if not 7 <= len(digits) <= 15:
        return 0
    national = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits[1:] if len(digits) == 11 and digits.startswith("0") else digits
    if len(national) == 10 and national[0] not in "6789" and len(national) != len(digits):
        return 1                      # an Indian trunk-prefixed or +91 landline (0484 2345678)
    return 2 if len(digits) >= 10 else 1


def phone_candidates(value: Any) -> list[str]:
    """The phone numbers in one reader value, each cleaned of its label and any note ("Mobile: 98765 00810 (WhatsApp)" gives
    "98765 00810"; "98765 00810 / 98765 00811" gives both). At most 40 characters each, in reading order."""
    if value is None or isinstance(value, bool):
        return []
    text = unicodedata.normalize("NFKC", str(value)).replace("\x00", " ")
    found: list[str] = []
    for piece in re.split(r"[,;|/\n\r]+|\s+(?:and|&)\s+|\s+(?:or)\s+", text):
        start = re.search(r"\+?\(?\d", piece)
        run = _NUMBER_RUN.match(piece, start.start()) if start else None
        number = " ".join(run.group().split()).strip(" .-(") if run else ""
        if number and phone_rank(number) and len(number) <= 40:
            found.append(number)
    if not found:    # "98765 00810 98765 00811": two numbers with nothing between them
        found = [" ".join(m.group().split()) for m in _LOOSE_MOBILE.finditer(text)]
    return list(dict.fromkeys(found))


def pick_phone(phones: Any, whatsapp: Any = None) -> str | None:
    """The mobile number to put on the form: the first reader number that can be a mobile (a landline only when there is nothing
    better), else the WhatsApp number (a resume that says only "WhatsApp: ..." has no other mobile). None when nothing usable."""
    values = phones if isinstance(phones, (list, tuple)) else [phones]
    numbers = [n for value in values for n in phone_candidates(value)]
    best = max(numbers, key=lambda n: (phone_rank(n), -numbers.index(n)), default=None)
    if best and phone_rank(best) == 2:
        return best
    on_whatsapp = next((n for n in phone_candidates(whatsapp) if phone_rank(n) == 2), None)
    return on_whatsapp or best


_EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def pick_email(emails: Any) -> str | None:
    """The first usable e-mail address, lower case ("Email: A.B@Gmail.com", "a.b @ gmail.com" and a list entry holding several)."""
    values = emails if isinstance(emails, (list, tuple)) else [emails]
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        match = _EMAIL_ADDRESS.search(re.sub(r"\s*@\s*", "@", str(value)))
        if match:
            return match.group().lower()
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
    return [f"We read the {kind} page(s) {listed} with low confidence. Compare the details with the original."]


_FORM_ONLY = {"father_name", "marital_status", "current_address", "experience_india", "education", "work_history"}
# Plain form fields the reader may leave out although the resume prints them beside a label; filled from the page text only when empty.
_PAGE_TEXT_GAPS = {"phone", "email", "dob", "gender", "nationality", "languages"}


def _form_values(kind: str, response) -> dict[str, Any]:
    """The application-form-only values read from a validated response (the parsing is shared with the stored-data path)."""
    from app.services import form_extraction
    return form_extraction.values_for(kind, response.model_dump(mode="json", exclude={"pages"}))


def resume_fields(r: ResumeResponse, designations: list[str]) -> tuple[OcrFields, list[str]]:
    """
    `designations` are the titles the form's select offers. A resume's title only fills
    the field when it matches one of them (exactly, or by every word of a master title
    appearing in it), or the select would show "Choose one" and save nothing. The
    resume's own wording is always kept in the stored Veris output.

    Address, father's name, marital status, education, the work history and the experience in India are
    form-only values (application form). Not copied anywhere (kept in the stored verbatim Veris output): linkedin,
    github, social, websites, extra emails/phones, mother name, place of birth,
    religion, industry, projects, achievements, certifications.
    """
    designation = match_designation(r.designation, designations)

    pp = r.passport_details
    info = r.personal_info

    fields = OcrFields(
        name=_person_name(r.name),
        phone=pick_phone(r.contact.phones, r.contact.whatsapp),
        whatsapp=_text(r.contact.whatsapp),
        email=pick_email(r.contact.emails),
        dob=parse_loose_date(info.date_of_birth, "birth"),
        gender=_gender(info.gender),
        nationality=_text(info.nationality),
        current_city=current_city_from(r),
        languages=_unique(info.languages_known),
        passport_no=_passport_no(pp.passport_number),
        passport_issue=parse_loose_date(pp.date_of_issue, "other"),
        passport_expiry=parse_loose_date(pp.date_of_expiry, "other"),
        passport_place=_text(pp.place_of_issue),
        designation=designation,
        total_experience=_years(r.total_experience_months),
        gulf_experience=_years(r.overseas_experience_months),
        skills=_unique(r.skills),
    )
    from app.services import form_extraction
    dumped = r.model_dump(mode="json", exclude={"pages"})
    dumped["pages"] = [{"page_number": p.page_number, "text": p.text} for p in r.pages]   # the page text: labelled details the reader left out
    for name, value in form_extraction.values_for("resume", dumped).items():
        if name in _FORM_ONLY:
            setattr(fields, name, value)
        elif name in ("total_experience", "gulf_experience"):
            # Worked out from the reader's totals AND from each job's dates, so a resume read without the totals
            # still fills them (the reader's own totals alone leave these blank).
            setattr(fields, name, value)
        elif name in _PAGE_TEXT_GAPS and getattr(fields, name) in (None, "", []):
            setattr(fields, name, value)   # found only in the page text, beside its label (see form_extraction.resume_values)
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
        passport_issue=mrz.date_of_issue or parse_loose_date(_printed(p, "Date of Issue"), "other"),
        passport_expiry=mrz.expiry_date,
        passport_place=_printed(p, "Place of Issue"),
    )

    for name, value in _form_values("passport", p).items():
        if name in _FORM_ONLY:
            setattr(fields, name, value)
    warnings = [*p.warnings, *_low_confidence("passport", p.pages)]
    if p.mrz_source != "mrz":
        warnings.append(
            "The passport details were read from the printed page, not the coded lines at the bottom of it, "
            "so they may be less accurate. Compare every value against the scan."
        )
    if not mrz.all_check_digits_valid:
        warnings.append(
            "The passport number or a date may be misread (its check digits do not match). "
            "Compare them against the scan."
        )
    return fields, warnings


def aadhaar_fields(response: AadhaarResponse) -> tuple[OcrFields, list[str]]:
    """
    Only stated candidate details: name, date of birth, gender, the address and the father's
    name from the S/O, D/O or W/O line (form-only values, read by services/form_extraction).
    Never copied to a column (kept in raw): the Aadhaar number and its validity, masked number,
    year of birth, pincode, VID, enrollment id, the card's mobile number and document side.
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
    for name, value in _form_values("aadhaar", response).items():
        if name in ("father_name", "current_address"):
            setattr(fields, name, value)
    warnings = [*response.warnings, *_low_confidence("Aadhaar", response.pages)]
    if identity.year_of_birth and not identity.date_of_birth:
        warnings.append("Only a birth year was read from Aadhaar. Enter the full date of birth manually.")
    if identity.aadhaar_number_valid is False:
        warnings.append("The Aadhaar number does not look right. Compare it with the original document.")
    # A full address does not establish a separate city, and the issuer does
    # not establish citizenship. Neither field is inferred from the card.
    return fields, warnings


def resume_text_from(ocr_data: dict[str, Any] | None) -> str | None:
    """The resume's page text, which is what the Data Bank searches."""
    pages = ((ocr_data or {}).get("resume") or {}).get("pages") or []
    pages = sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("page_number") if type(p.get("page_number")) is int else 0)
    texts = [t.strip() for t in (p.get("text") for p in pages) if isinstance(t, str) and t.strip()]
    return "\n\n".join(texts) or None
