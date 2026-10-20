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
_PASSPORT_TITLE = re.compile(r"^\s*passport\s*$", re.I | re.M)
_MRZ = re.compile(r"(?m)^\s*P<[A-Z<]{3}[A-Z<]{8,}")
_ISSUER = re.compile(r"\b(?:republic|kingdom|government|ministry|issuing\s+authority)\b", re.I)
_PASSPORT_FIELDS = re.compile(r"\b(?:surname|given\s+names|date\s+of\s+birth|date\s+of\s+expiry|"
                              r"place\s+of\s+birth|passport\s+(?:no|number)|nationality)\b", re.I)
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
_AADHAAR_FIELDS = (
    re.compile(r"\b(?:date\s+of\s+birth|year\s+of\s+birth|dob)\b|जन्म", re.I),
    re.compile(r"\b(?:male|female|transgender)\b|पुरुष|महिला", re.I),
    re.compile(r"\baddress\b|\b[cwsd]/o\b|पता", re.I),
    re.compile(r"\benrol(?:l)?ment\s+(?:no|number|id)\b|\bvid\b", re.I),
)


def _aadhaar_structure(text: str) -> bool:
    # A résumé's "Aadhaar number" line is not card evidence. Require the issuing
    # authority or an Aadhaar-specific statement/issuer combination as well.
    return bool(
        _AADHAAR_AUTHORITY.search(text)
        or _AADHAAR_STATEMENT.search(text)
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


def classify_page(text: str) -> PageClassification:
    """Unknown means review is needed; it never means an empty/blank page."""
    text = text or ""
    seed, _, _, _ = _signals(text)
    passport = bool(_MRZ.search(text)) or (
        bool(_PASSPORT_TITLE.search(text)) and bool(_ISSUER.search(text))
        and len(set(_PASSPORT_FIELDS.findall(text.lower()))) >= 2
    )
    aadhaar = _is_aadhaar(text)
    supporting = bool(_OTHER.search(text))
    if seed and (passport or aadhaar or supporting):
        return PageClassification("unknown", 0.0, "Mixed resume and supporting-document evidence on one page.")
    if passport and aadhaar:
        return PageClassification("unknown", 0.0, "Mixed passport and Aadhaar evidence on one page.")
    if aadhaar:
        return PageClassification("aadhaar", 0.95, "Aadhaar issuing authority with card number or identity fields.")
    if passport:
        return PageClassification("passport", 0.95, "Passport document structure or machine-readable zone.")
    if supporting:
        return PageClassification("other", 0.9, "Certificate, identity document, invoice or other supporting document.")
    if seed:
        return PageClassification("resume", 0.9, "Resume title or multiple career sections with contact/work evidence.")
    return PageClassification("unknown", 0.0, "Insufficient evidence to classify this page safely.")


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
            if _OTHER.search(text) or _MRZ.search(text) or _PASSPORT_TITLE.search(text) or _aadhaar_structure(text):
                continue
            neighbors = result[max(0, index - 1):index] + result[index + 1:index + 2]
            if any(page.document_type == "resume" for page in neighbors):
                result[index] = PageClassification("resume", 0.75, "Career-section continuation adjoining a resume page.")
                changed = True
    return result
