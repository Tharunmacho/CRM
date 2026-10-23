"""Conservative page classification from content, independent of filenames.

This adapts the reference project's seed/continuation approach without its
nationality rules or its permissive fallback for unclassified pages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PageClassification:
    document_type: str
    confidence: float
    reason: str
    # A page that carries evidence of more than one document (a passport and an
    # Aadhaar copy scanned together) belongs to every kind listed here as well.
    also: tuple[str, ...] = ()
    # The page had no evidence of its own and took the kind of the document next to it.
    inherited: bool = False


_TITLE = re.compile(
    r"^\s*(?:curriculum\s+vitae|résumé|resume|c\.?\s*v\.?|"
    r"bio[\s-]*data|personal\s+profile|candidate\s+profile)\s*[:\-]?\s*$",
    re.I | re.M,
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.I)
_PHONE = re.compile(r"\+?\d[\d ()-]{8,}\d")
_DATES = re.compile(r"\b(?:19|20)\d{2}\s*(?:[-–—]|to)\s*(?:(?:19|20)\d{2}|present|current|now)\b", re.I)
_ROLE = re.compile(
    r"\b(?:engineer|developer|manager|supervisor|technician|operator|welder|"
    r"fitter|driver|electrician|plumber|carpenter|nurse|accountant|teacher|"
    r"chef|assistant|foreman|mechanic|mason|rigger|scaffolder|labou?rer)\b", re.I,
)
_WORK = re.compile(r"\b(?:managed|installed|maintained|operated|developed|supervised|"
                   r"responsible|inspected|repaired|fabricated|coordinated|welding)\b", re.I)
_HEADINGS = (
    "career objective", "objective", "professional summary", "profile summary",
    "work experience", "professional experience", "employment history",
    "work history", "education", "educational qualifications", "qualifications",
    "technical skills", "key skills", "skills", "personal details",
    "personal information", "languages", "projects", "job responsibilities",
    "duties and responsibilities", "declaration", "references", "certifications",
)
_HEADING_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in sorted(_HEADINGS, key=len, reverse=True))
    + r")\s*(?::.*)?$", re.I | re.M,
)
_OTHER = re.compile(
    r"\b(?:this\s+is\s+to\s+certify|certificate\s+of\s+(?:completion|training|"
    r"achievement|participation)|experience\s+certificate|service\s+certificate|"
    r"to\s+whom\s+it\s+may\s+concern|relieving\s+letter|tax\s+invoice|"
    r"invoice\s+(?:no|number)|payment\s+receipt|hall\s+ticket|admit\s+card|"
    r"unique\s+identification\s+authority|identity\s+card|emirates\s+id|"
    r"resident\s+identity|driving\s+licen[cs]e)\b", re.I,
)

# ---- Passport -------------------------------------------------------------------
_PASSPORT_WORD = re.compile(r"\bpassport\b|पासपोर्ट", re.I)
_PASSPORT_TITLE = re.compile(
    r"^\s*[^\w\n]*(?:p\s?a\s?s\s?s\s?p\s?o\s?r\s?t|पासपोर्ट)(?:\s*/?\s*[^\x00-\x7f]+)?\s*$", re.I | re.M,
)
_INDIA_REPUBLIC = re.compile(r"\brepublic\s+of\s+india\b|भारत\s*गणराज्य", re.I)
_ISSUER = re.compile(r"\b(?:republic|kingdom|government|ministry|issuing\s+authority)\b", re.I)
_PASSPORT_FIELDS = re.compile(r"\b(?:surname|given\s+names?|date\s+of\s+birth|date\s+of\s+expiry|"
                              r"place\s+of\s+birth|passport\s+(?:no|number)|nationality)\b", re.I)
_PASSPORT_DATA = re.compile(r"\bcountry\s+code\b|\bcode\s+of\s+issuing\s+state\b|\btype\s+p\b", re.I)
# Printed on the back (address) page of an Indian passport.
_PASSPORT_BACK_STRONG = (
    re.compile(r"\blegal\s+guardian\b", re.I),
    re.compile(r"\bold\s+passport\b", re.I),
    re.compile(r"\bemigration\s+check\b", re.I),
    re.compile(r"\bpassport\s+file\b|\bfile\s+no\b", re.I),
)
_PASSPORT_BACK_LABELS = (
    re.compile(r"\bname\s+of\s+(?:father|mother|spouse)\b", re.I),
    re.compile(r"\baddress\b", re.I),
    re.compile(r"\bpin\b", re.I),
)
_VISA = re.compile(r"\bvisa\b", re.I)
_VISA_FIELDS = (
    re.compile(r"\bentry\s+permit\b", re.I),
    re.compile(r"\bnumber\s+of\s+entries\b|\b(?:single|multiple)\s+entry\b", re.I),
    re.compile(r"\bvalid\s+(?:until|from|for|upto|up\s+to)\b", re.I),
    re.compile(r"\b(?:type\s+of\s+visa|visa\s+(?:type|no|number|category)|class\s+of\s+visa)\b", re.I),
    re.compile(r"\bsponsor\b|\bduration\s+of\s+stay\b|\bissued\s+(?:at|on)\b", re.I),
)
# Tesseract reads the "<" filler of a machine-readable zone as « K C L ( [ or drops it.
_MRZ_FILLER = str.maketrans({"«": "<", "‹": "<", "〈": "<", "⟨": "<"})
_MRZ_LINE1 = re.compile(r"^P[<K(\[CL1Il]?[A-Z]{3}[A-Z<]{4,}$")
_MRZ_LINE1_GARBLED = re.compile(
    r"^P[<K(\[CL1Il]?[A-Z]{3}[A-Z]+(?:<<|K{5,}|C{5,}|L{5,}|\({5,}|\[{5,}|<[KCL(\[]{4,})"
)
_MRZ_LINE2 = re.compile(
    r"[A-Z0-9<]{9}[0-9O<][A-Z<]{3}[0-9OIBS]{6}[0-9OIBS<][MFX<K][0-9OIBS]{6}[0-9OIBS<]"
)


def _mrz(text: str) -> bool:
    """A TD3 passport machine-readable zone, tolerating the usual OCR chevron damage."""
    for raw in text.upper().translate(_MRZ_FILLER).splitlines():
        line = re.sub(r"\s+", "", raw)
        if len(line) < 28:
            continue
        if _MRZ_LINE2.search(line):
            return True
        if line.count("<") >= 2 and _MRZ_LINE1.match(line):
            return True
        if _MRZ_LINE1_GARBLED.match(line) and len(raw.strip()) >= 28:
            return True
    return False


def _passport_back_page(text: str) -> bool:
    strong = sum(bool(p.search(text)) for p in _PASSPORT_BACK_STRONG)
    labels = sum(bool(p.search(text)) for p in _PASSPORT_BACK_LABELS)
    return strong >= 1 and strong + labels >= 3


def _is_passport(text: str) -> bool:
    if _mrz(text):
        return True
    fields = len({re.sub(r"\s+", " ", match.lower()) for match in _PASSPORT_FIELDS.findall(text)})
    title = bool(_PASSPORT_TITLE.search(text))
    word = bool(_PASSPORT_WORD.search(text))
    india = bool(_INDIA_REPUBLIC.search(text))
    return bool(
        (title and fields >= 2 and _ISSUER.search(text))
        or (title and india)
        or (word and india and fields >= 2)
        or (word and _PASSPORT_DATA.search(text) and fields >= 2)
        or _passport_back_page(text)
    )


def _is_visa(text: str) -> bool:
    return bool(_VISA.search(text)) and sum(bool(p.search(text)) for p in _VISA_FIELDS) >= 2


# ---- Aadhaar --------------------------------------------------------------------
_AADHAAR_AUTHORITY = re.compile(
    r"\b(?:unique\s+identification\s+authority\s+of\s+india|uidai)\b"
    r"|भारतीय\s+विशिष्ट\s+पहचान\s+प्राधिकरण", re.I,
)
_AADHAAR_BRAND = re.compile(r"\baadhaa?r\b|आधार", re.I)
_AADHAAR_ISSUER = re.compile(r"\bgovernment\s+of\s+india\b|भारत\s*सरकार", re.I)
_AADHAAR_STATEMENT = re.compile(
    r"\baadhaa?r\s+is\s+(?:a\s+)?proof\s+of\s+identity\b"
    r"|\bmera\s+aadhaa?r,?\s*meri\s+pehchan\b", re.I,
)
_AADHAAR_NUMBER = re.compile(
    r"(?<!\w)(?:\d{4}|x{4})[\s-]*(?:\d{4}|x{4})[\s-]*\d{4}(?!\w)", re.I,
)
# A 16-digit Virtual ID and an enrolment number are printed on the card/letter only.
_AADHAAR_VID = re.compile(r"\bvid\b\s*[:\-]?\s*(?:\d{4}[\s-]*){4}(?!\d)", re.I)
_AADHAAR_ENROLMENT = re.compile(r"(?<!\d)\d{4}/\d{5}/\d{5}(?!\d)")
_AADHAAR_FIELDS = (
    re.compile(r"\b(?:date\s+of\s+birth|year\s+of\s+birth|dob)\b|जन्म", re.I),
    re.compile(r"\b(?:male|female|transgender)\b|पुरुष|महिला", re.I),
    re.compile(r"\baddress\b|\b[cwsd]/o\b|पता", re.I),
    re.compile(r"\benrol(?:l)?ment\s+(?:no|number|id)\b|\bvid\b", re.I),
)


def _aadhaar_structure(text: str) -> bool:
    # A résumé's "Aadhaar number" line is not card evidence. Require the issuing
    # authority, a card-only identifier (VID, enrolment number) or an
    # Aadhaar-specific statement/issuer combination as well.
    return bool(
        _AADHAAR_AUTHORITY.search(text)
        or _AADHAAR_STATEMENT.search(text)
        or _AADHAAR_VID.search(text)
        or _AADHAAR_ENROLMENT.search(text)
        or (_AADHAAR_BRAND.search(text) and _AADHAAR_ISSUER.search(text))
    )


def _is_aadhaar(text: str) -> bool:
    return len(text.strip()) >= 40 and _aadhaar_structure(text) and (
        bool(_AADHAAR_NUMBER.search(text))
        or sum(bool(pattern.search(text)) for pattern in _AADHAAR_FIELDS) >= 2
    )


def _signals(text: str) -> tuple[bool, bool, bool, int]:
    headings = set(m.group(1).lower() for m in _HEADING_RE.finditer(text))
    titled = bool(_TITLE.search(text))
    contact = bool(_EMAIL.search(text) or _PHONE.search(text))
    career = bool(_DATES.search(text) or _ROLE.search(text))
    seed = len(text.strip()) >= 40 and (
        titled or (contact and len(headings) >= 2)
        or (len(headings) >= 3 and career)
    )
    continuation = len(text.strip()) >= 60 and (
        (len(headings) >= 1 and career)
        or (len(headings) >= 2)
        or (len(set(_WORK.findall(text.lower()))) >= 2 and bool(_ROLE.search(text)))
    )
    return seed, continuation, titled, len(headings)


def _other_evidence(text: str) -> bool:
    """Evidence that a page belongs to some other identity/supporting document."""
    return bool(
        _OTHER.search(text) or _mrz(text) or _PASSPORT_TITLE.search(text)
        or _aadhaar_structure(text) or _passport_back_page(text) or _is_visa(text)
    )


def classify_page(text: str) -> PageClassification:
    """Unknown means review is needed; it never means an empty/blank page."""
    text = text or ""
    seed, _, _, _ = _signals(text)
    passport = _is_passport(text)
    aadhaar = _is_aadhaar(text)
    # A visa page is a supporting document; a résumé that merely lists visa details is not.
    supporting = bool(_OTHER.search(text)) or (_is_visa(text) and not seed)
    if seed and (passport or aadhaar or supporting):
        return PageClassification("unknown", 0.0, "Mixed resume and supporting-document evidence on one page.")
    if passport and aadhaar:
        # A passport and an Aadhaar copy scanned onto one sheet is a common layout.
        # The page goes to both extractions rather than being held back.
        return PageClassification(
            "passport", 0.9, "Passport and Aadhaar evidence on one page; sent to both extractions.",
            also=("aadhaar",),
        )
    if aadhaar:
        return PageClassification("aadhaar", 0.95, "Aadhaar issuing authority with card number or identity fields.")
    if passport:
        return PageClassification("passport", 0.95, "Passport document structure or machine-readable zone.")
    if supporting:
        return PageClassification("other", 0.9, "Certificate, identity document, invoice or other supporting document.")
    if seed:
        return PageClassification("resume", 0.9, "Resume title or multiple career sections with contact/work evidence.")
    return PageClassification("unknown", 0.0, "Insufficient evidence to classify this page safely.")


def _kinds(page: PageClassification) -> set[str]:
    return {kind for kind in (page.document_type, *page.also) if kind in {"passport", "aadhaar"}}


def classify_pages(texts: Sequence[str]) -> list[PageClassification]:
    """Grow resume seeds through evidenced, contiguous continuation pages."""
    result = [classify_page(text) for text in texts]
    changed = True
    while changed:
        changed = False
        for index, text in enumerate(texts):
            if result[index].document_type != "unknown" or not _signals(text)[1]:
                continue
            # A mixed page must never become a continuation through proximity.
            if _other_evidence(text):
                continue
            neighbors = result[max(0, index - 1):index] + result[index + 1:index + 2]
            if any(page.document_type == "resume" for page in neighbors):
                result[index] = PageClassification("resume", 0.75, "Career-section continuation adjoining a resume page.")
                changed = True
    # The back of an ID card or passport often carries nothing that identifies it by
    # itself. A readable page directly beside one identity document (and beside no
    # other kind) with no evidence of its own is that document's other side. It is
    # reported as inherited so the extraction carries a warning for a person to check.
    settled = list(result)
    for index, text in enumerate(texts):
        if settled[index].document_type != "unknown" or len((text or "").strip()) < 40:
            continue
        seed, continuation, _, _ = _signals(text)
        if seed or continuation or _other_evidence(text) or _is_passport(text) or _is_aadhaar(text):
            continue
        neighbors = settled[max(0, index - 1):index] + settled[index + 1:index + 2]
        known = {kind for page in neighbors for kind in _kinds(page)}
        # Any other document beside it (a resume, a certificate) makes the side ambiguous.
        if len(known) != 1 or any(page.document_type in {"resume", "other"} for page in neighbors):
            continue
        kind = next(iter(known))
        result[index] = PageClassification(
            kind, 0.6, f"Assigned to the adjoining {kind} document; no content of its own identified it.",
            inherited=True,
        )
    return result
