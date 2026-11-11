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
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
# A year, a month and year in words, MM/YYYY (or YYYY/MM) on either side of a range.
_DATE_TOKEN = (rf"(?:{_MONTH}\.?[\s,'-]*(?:(?:19|20)\d{{2}}|\d{{2}})|\d{{1,2}}[/.-](?:19|20)\d{{2}}"
               rf"|(?:19|20)\d{{2}}[/.-]\d{{1,2}}|(?:19|20)\d{{2}})")
_DATES = re.compile(
    rf"\b{_DATE_TOKEN}\s*(?:[-–—]|to|till|until)\s*(?:{_DATE_TOKEN}|present|current|now|ongoing|till\s*date|date)\b", re.I,
)
_ROLE = re.compile(
    r"\b(?:engineer|developer|manager|supervisor|technician|operator|welder|"
    r"fitter|driver|electrician|plumber|carpenter|nurse|accountant|teacher|"
    r"chef|assistant|foreman|mechanic|mason|rigger|scaffolder|labou?rer|helper|painter|cook|cleaner|"
    r"security\s+guard|steward|storekeeper|inspector|fabricator|machinist|"
    r"steel[\s-]+fixer|bar[\s-]+bender|reinforcement[\s-]+fixer)s?\b", re.I,
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
    "personal particulars", "personal data", "about me", "areas of expertise", "machinery handled",
    "equipment handled", "site experience", "passport details", "visa status", "career summary",
    "professional profile", "strengths", "achievements", "hobbies", "interests", "training", "summary",
    "experience", "key projects", "safety training", "core competencies", "academic qualifications",
)
_HEADING_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in sorted(_HEADINGS, key=len, reverse=True))
    + r")\s*(?::.*)?$", re.I | re.M,
)
_BULLET_PREFIX = re.compile(r"^[\s•●▪·*\-–—>]+|^\d{1,2}[.)]\s+")
_SPACED_LETTERS = re.compile(r"^(?:[A-Za-z]\s+){3,}[A-Za-z](?=\s*:|\s*$)")
_HEADING_SET = frozenset(_HEADINGS)
_HEADING_SQUASHED = {h.replace(" ", ""): h for h in _HEADINGS}

# A handwritten recruitment application is often the only page with the
# candidate's contact details and work history. Its printed field labels survive
# OCR even when the handwritten values do not. Treat that form as resume content
# so it reaches the extractor alongside the CV; identity applications must not
# qualify on shared labels such as name, date of birth and passport number.
_RECRUITMENT_FORM_FIELDS = (
    re.compile(r"\bpost\s+applied(?:\s+for)?\b", re.I),
    re.compile(r"\binterview\s+date\b", re.I),
    re.compile(r"\beducational\s+qualifications?\b", re.I),
    re.compile(r"\blanguages\s+known\b", re.I),
    re.compile(r"\bsalary\s+(?:expected|offered)\b", re.I),
    re.compile(r"\bname\s+of\s+employers?\b", re.I),
    re.compile(r"\bapplicant['’]?s?\s+name\b", re.I),
    re.compile(r"\bsignature\s+of\s+(?:the\s+)?applicant\b", re.I),
)


def _recruitment_form(text: str) -> bool:
    matches = [bool(pattern.search(text)) for pattern in _RECRUITMENT_FORM_FIELDS]
    # Require several labels, including two specifically about recruitment or
    # employment. Neither a passport application nor an ID card meets this rule.
    return sum(matches) >= 4 and sum(matches[index] for index in (0, 1, 4, 5)) >= 2


def _headings_in(text: str) -> set[str]:
    """The section headings on a page, tolerating what OCR does to them: a bullet or number in front, letters spaced
    apart ("E D U C A T I O N"), a colon with text after it, and a capitalised heading glued to its first body line."""
    found: set[str] = set()
    for raw in text.splitlines():
        line = _BULLET_PREFIX.sub("", raw).strip()
        if not line:
            continue
        spaced = _SPACED_LETTERS.match(line)
        if spaced:  # collapse the spaced run, keep what follows
            line = spaced.group(0).replace(" ", "") + line[spaced.end():]
        head = line.split(":", 1)[0].strip()
        if len(head) <= 40:
            key = " ".join(re.sub(r"[^a-z&\s]", " ", head.lower()).split())
            if key in _HEADING_SET:
                found.add(key)
                continue
        # Spaced out words ("W O R K  E X P E R I E N C E"): compare with the spaces removed.
        squashed = re.sub(r"[^a-z]", "", head.lower())
        if spaced and squashed in _HEADING_SQUASHED:
            found.add(_HEADING_SQUASHED[squashed])
            continue
        # "EDUCATION B.E. Computer Science ..." : capitals up to four words, then ordinary body text.
        words = line.split()
        run = 0
        while run < min(len(words), 4) and words[run].isupper():
            run += 1
        if 0 < run < len(words) and any(ch.islower() for ch in " ".join(words[run:])):
            for length in range(run, 0, -1):
                key = " ".join(re.sub(r"[^a-z&\s]", " ", " ".join(words[:length]).lower()).split())
                if key in _HEADING_SET:
                    found.add(key)
                    break
    return found


_OTHER = re.compile(
    r"\b(?:this\s+is\s+to\s+certify|certificate\s+of\s+(?:completion|training|"
    r"achievement|participation)|experience\s+certificate|service\s+certificate|"
    r"to\s+whom\s+it\s+may\s+concern|relieving\s+letter|tax\s+invoice|"
    r"invoice\s+(?:no|number)|payment\s+receipt|hall\s+ticket|admit\s+card|"
    r"unique\s+identification\s+authority)\b", re.I,
)
# Gulf and Indian CVs list a licence or an Emirates ID in their personal details, so these words only count against a
# page that has no resume structure of its own (see _supporting_marker).
_OTHER_ID = re.compile(
    r"\b(?:identity\s+card|emirates\s+id|resident\s+identity|driving\s+licen[cs]e)\b", re.I,
)
_SITE_BADGE_ROLE = re.compile(r"\bproject\s*(?:id|no)\b|\bposition\s*/\s*trade\b", re.I)
_SITE_BADGE_FIELDS = (
    re.compile(r"\bfile\s*(?:num|no|number)\b", re.I),
    re.compile(r"\biqama\s*(?:num|no|number)\b", re.I),
    re.compile(r"\bblood\s+group\b", re.I),
    re.compile(r"\bissuing\s+auth(?:ority)?\b", re.I),
    re.compile(r"\bvalidity\b", re.I),
)

# ---- Passport -------------------------------------------------------------------
_PASSPORT_WORD = re.compile(r"\bp[a@]ssp[o0]rt\b|पासपोर्ट", re.I)
_PASSPORT_TITLE = re.compile(
    r"^\s*[^\w\n]*(?:p\s?a\s?s\s?s\s?p\s?o\s?r\s?t|पासपोर्ट)(?:\s*/?\s*[^\x00-\x7f]+)?\s*$", re.I | re.M,
)
_INDIA_REPUBLIC = re.compile(r"\brepublic\s+of\s+india\b|भारत\s*गणराज्य", re.I)
_ISSUER = re.compile(r"\b(?:republic|kingdom|government|ministry|issuing\s+authority)\b", re.I)
_PASSPORT_FIELDS = re.compile(r"\b(?:surname|given\s+names?(?:\s*\(s\))?|date\s+of\s+birth|date\s+of\s+expiry|"
                              r"date\s+of\s+issue|place\s+of\s+(?:birth|issue)|p[a@]ssp[o0]rt\s+(?:no|number)|nationality)\b", re.I)
_PASSPORT_DATA = re.compile(r"\bcountry\s+code\b|\bcode\s+of\s+issuing\s+state\b|\btype\s+p\b", re.I)
# The request page printed inside every Indian passport; no other document carries this wording.
_PASSPORT_REQUEST = re.compile(
    r"\bthese\s*are\s*to\s*request\s*and\s*require\b|\brequest\s*and\s*require\b"
    r"|\bpresident\s*of\s*the\s*republic\s*of\s*india\b", re.I,
)
# Bilingual (English / French) captions of the data page. The slash is often lost to OCR, so it is optional.
_PASSPORT_FRENCH = (
    re.compile(r"\bpasseport\b", re.I),
    re.compile(r"\bsurname\s*/?\s*nom\b", re.I),
    re.compile(r"\bgiven\s+names?\s*(?:\(s\))?\s*/?\s*pr[eé]noms?\b", re.I),
    re.compile(r"\bcode\s+du\s+pays\b", re.I),
    re.compile(r"\bdate\s+of\s+(?:birth|expiry|issue)\s*/?\s*date\s+de\b", re.I),
    re.compile(r"\bplace\s+of\s+birth\s*/?\s*lieu\b", re.I),
)
# Hindi captions of the Indian data page (no \b: Devanagari combining marks do not count as word characters).
_PASSPORT_DEVANAGARI = (
    re.compile(r"उपनाम"), re.compile(r"दिया\s+गया\s+नाम"), re.compile(r"राष्ट्र\s*कोड"),
    re.compile(r"समाप्ति\s+की\s+तिथि"), re.compile(r"कानूनी\s*अभिभावक"),
)
_PASSPORT_ISSUE_EXPIRY = re.compile(
    r"\bplace\s+of\s+issue\b[\s\S]{0,240}?\bdate\s+of\s+expiry\b|\bdate\s+of\s+expiry\b[\s\S]{0,240}?\bplace\s+of\s+issue\b",
    re.I,
)
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
    french = sum(bool(p.search(text)) for p in _PASSPORT_FRENCH)
    hindi = sum(bool(p.search(text)) for p in _PASSPORT_DEVANAGARI)
    return bool(
        _PASSPORT_REQUEST.search(text)
        or french >= 2 or (french >= 1 and (word or fields >= 2))
        or hindi >= 2 or (hindi >= 1 and (word or fields >= 2))
        or (title and fields >= 2 and _ISSUER.search(text))
        or (title and india)
        or (word and india and fields >= 2)
        # The word "passport" can be lost to OCR on the photo page; the printed field names still say what it is.
        or (india and fields >= 3)
        or (word and _PASSPORT_DATA.search(text) and fields >= 2)
        or _passport_back_page(text)
    )


def _passport_loose(text: str) -> bool:
    """The word passport with a place of issue and a date of expiry and several more captions, on a page with no
    resume structure. A resume's passport-details block says the same, which is why this never applies to one."""
    if not (_PASSPORT_ISSUE_EXPIRY.search(text) and _PASSPORT_WORD.search(text)) or _structure_strength(text) >= 3:
        return False
    return len({re.sub(r"\s+", " ", m.lower()) for m in _PASSPORT_FIELDS.findall(text)}) >= 4


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


_NOISY_DIGIT = "0-9OoIlBS"
_AADHAAR_NUMBER_NOISY = re.compile(
    rf"(?<![\w])([{_NOISY_DIGIT}]{{4}})[\s-]+([{_NOISY_DIGIT}]{{4}})[\s-]+([{_NOISY_DIGIT}]{{4}})(?![\w])")


def _aadhaar_number(text: str) -> bool:
    """A 12-digit number in groups of four, tolerating OCR letters for digits (O for 0, l or I for 1, S for 5, B for 8).
    At least nine of the twelve characters must be real digits, so ordinary words never count."""
    if _AADHAAR_NUMBER.search(text):
        return True
    return any(sum(c.isdigit() for c in "".join(m.groups())) >= 9 for m in _AADHAAR_NUMBER_NOISY.finditer(text))


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
        # The card's heading can be lost to OCR: the issuer with the grouped number is still the card.
        or (_AADHAAR_ISSUER.search(text) and _aadhaar_number(text))
    )


def _is_aadhaar(text: str) -> bool:
    return len(text.strip()) >= 40 and _aadhaar_structure(text) and (
        _aadhaar_number(text)
        or sum(bool(pattern.search(text)) for pattern in _AADHAAR_FIELDS) >= 2
    )


def _signals(text: str) -> tuple[bool, bool, bool, int]:
    headings = _headings_in(text)
    titled = bool(_TITLE.search(text))
    contact = bool(_EMAIL.search(text) or _PHONE.search(text))
    career = bool(_DATES.search(text) or _ROLE.search(text))
    lines = sum(1 for line in text.splitlines() if line.strip())
    seed = len(text.strip()) >= 40 and (
        titled or _recruitment_form(text) or (contact and len(headings) >= 2)
        or (len(headings) >= 3 and career)
        # A long page with career, education or skills sections and contact details is a résumé even when the title is lost.
        or (lines >= 12 and len(headings) >= 2 and (contact or career))
        or (lines >= 12 and len(headings) >= 1 and contact and career)
    )
    continuation = len(text.strip()) >= 60 and (
        (len(headings) >= 1 and career)
        or (len(headings) >= 2)
        or (len(set(_WORK.findall(text.lower()))) >= 2 and bool(_ROLE.search(text)))
    )
    return seed, continuation, titled, len(headings)


def _structure_strength(text: str) -> int:
    """How much of a resume's structure is on the page: a title, section headings, contact details, a date range,
    a job title. Counts kinds of evidence, not quantity."""
    strength = 2 if _TITLE.search(text) else 0
    strength += 3 if _recruitment_form(text) else 0
    strength += min(len(_headings_in(text)), 3)
    strength += bool(_EMAIL.search(text)) + bool(_PHONE.search(text))
    strength += bool(_DATES.search(text)) + bool(_ROLE.search(text))
    return strength


def _supporting_marker(text: str) -> bool:
    """Wording of a certificate, invoice or identity document. A licence or ID-card word is only weak evidence: a
    resume's personal-details block states one. It counts against a page with little resume structure, and against
    a structured page only when several of those words crowd it."""
    if _OTHER.search(text) or (
        _SITE_BADGE_ROLE.search(text) and sum(bool(pattern.search(text)) for pattern in _SITE_BADGE_FIELDS) >= 3
    ):
        return True
    weak = {re.sub(r"\s+", " ", m.group(0).lower()) for m in _OTHER_ID.finditer(text)}
    if not weak:
        return False
    strength = _structure_strength(text)
    if strength >= 5:
        return False
    return len(weak) >= 3 if strength >= 3 else True


def _other_evidence(text: str) -> bool:
    """Evidence that a page belongs to some other identity/supporting document."""
    evidence = _evidence(text)
    return bool(
        evidence["passport"] or evidence["aadhaar"]
        or _supporting_marker(text) or _mrz(text) or _PASSPORT_TITLE.search(text)
        or _aadhaar_structure(text) or _passport_back_page(text) or _is_visa(text)
    )


def _evidence(text: str) -> dict:
    """Checksum-backed evidence (validators.identity_evidence): a passport machine-readable zone whose check digits
    pass, or a 12-digit number that passes the Verhoeff check. Ordinary text cannot pass these by accident, so
    this holds even when the rest of the page was read badly."""
    if not text or len(text) < 20:
        return {"passport": False, "aadhaar": False}
    try:
        from . import validators
        return validators.identity_evidence(text)
    except Exception:  # an aid; never a reason to fail a page
        return {"passport": False, "aadhaar": False}


def needs_more_reading(text: str, index: int | None = None) -> bool:
    """Whether reading this page again under other settings could still change what it is classified as.

    False once the page is classified, holds checksum-valid identity evidence, or is a full page of readable text with
    no sign of a passport or an Aadhaar card (it will be a continuation or something else, and other settings would
    not change that). The local reader exists to classify pages and to pick the pages Veris is sent, so it stops
    escalating there."""
    text = text or ""
    stripped = text.strip()
    if len(stripped) < 40:
        return True
    evidence = _evidence(text)
    if evidence["passport"] or evidence["aadhaar"]:
        return False
    if classify_page(text).document_type != "unknown":
        return False
    lines = sum(1 for line in stripped.splitlines() if line.strip())
    return not (len(stripped) >= 300 and lines >= 8 and _reads_like_language(stripped) and not identity_hint(text))


_STOPWORDS = frozenset("the and of in to at for with from is are was were by on as an a that this his her has have had be".split())


def _reads_like_language(text: str) -> bool:
    """Running English text has many small common words; a page read sideways or upside down, or a noisy scan, does
    not. Only a page that reads like language may stop the escalation on the strength of being plain text."""
    words = re.findall(r"[A-Za-z]{1,}", text)
    if len(words) < 40:
        return False
    common = sum(1 for word in words if word.lower() in _STOPWORDS)
    return common >= max(6, 0.08 * len(words))


def classify_page(text: str) -> PageClassification:
    """Unknown means review is needed; it never means an empty/blank page."""
    text = text or ""
    seed, _, _, _ = _signals(text)
    evidence = _evidence(text)
    passport = _is_passport(text) or evidence["passport"] or (not seed and _passport_loose(text))
    aadhaar = _is_aadhaar(text)
    # A valid Aadhaar number alone is not card evidence on a résumé (a candidate lists their own number) or on a
    # certificate or invoice (any long number passes a check digit one time in ten); elsewhere it is.
    if not aadhaar and evidence["aadhaar"] and not seed and not _supporting_marker(text):
        aadhaar = True
    # A visa page is a supporting document; a résumé that merely lists visa details is not.
    supporting = _supporting_marker(text) or (_is_visa(text) and not seed)
    if seed and supporting:
        return PageClassification("unknown", 0.0, "Mixed resume and supporting-document evidence on one page.")
    if seed and (passport or aadhaar):
        # A résumé with a passport or Aadhaar copy on the same sheet is read by each extraction: nothing is held back.
        return PageClassification(
            "resume", 0.85, "Resume and identity-document evidence on one page; sent to each extraction.",
            also=tuple(kind for kind, found in (("passport", passport), ("aadhaar", aadhaar)) if found),
        )
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


_IDENTITY_HINT = re.compile(
    r"\b(?:p[a@]ssp[o0]rt|republic|government|aadhaa?r|uidai|date\s+of\s+(?:birth|issue|expiry)|dob|nationality|"
    r"surname|given\s+names?|place\s+of\s+(?:birth|issue))\b", re.I)


def identity_hint(text: str) -> bool:
    """A weak sign that an unidentified page might be a passport or an Aadhaar card (used to choose pages worth
    sending to the document reader as candidates; it never classifies a page)."""
    text = text or ""
    return bool(_IDENTITY_HINT.search(text) or _mrz(text) or _aadhaar_number(text) or text.count("<<") >= 1)


def _kinds(page: PageClassification) -> set[str]:
    return {kind for kind in (page.document_type, *page.also) if kind in {"passport", "aadhaar"}}


_PASSPORT_LONE = (
    _PASSPORT_TITLE, _PASSPORT_REQUEST, *_PASSPORT_FRENCH, *_PASSPORT_DEVANAGARI,
    re.compile(r"\blegal\s+guardian\b", re.I), re.compile(r"\bold\s+passport\b", re.I),
    re.compile(r"\bemigration\s+check\b", re.I), re.compile(r"\bpassport\s+file\b", re.I),
    re.compile(r"\bmachine\s+readable\s+zone\b", re.I),
)


def _lone_marker(kind: str, text: str) -> bool:
    """A single caption that belongs to this kind of document and to almost nothing else."""
    if kind == "passport":
        return any(pattern.search(text) for pattern in _PASSPORT_LONE)
    return _aadhaar_structure(text)


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
    # The back of a passport can lie many pages from its data page. A page that carries one caption of a kind this
    # bundle has already proved (valid machine-readable zone, valid Aadhaar number, or several markers on another
    # page) is taken into that kind wherever it sits. A bundle with no proven page adopts nothing, and a page with
    # resume structure or other-document wording is never adopted.
    proven = {kind for page in settled if not page.inherited for kind in _kinds(page)}
    if proven:
        for index, text in enumerate(texts):
            if result[index].document_type != "unknown" or len((text or "").strip()) < 40:
                continue
            lone = {kind for kind in ("passport", "aadhaar") if _lone_marker(kind, text)}
            if len(lone) != 1 or not lone <= proven:  # no marker, an ambiguous one, or a kind the bundle has not proved
                continue
            seed, continuation, _, _ = _signals(text)
            if seed or continuation or _structure_strength(text) >= 3 or _supporting_marker(text) or _is_visa(text):
                continue
            kind = next(iter(lone))
            result[index] = PageClassification(
                kind, 0.6, f"One {kind} marker on this page; the bundle already holds a proven {kind} page.",
                inherited=True,
            )
    # A résumé runs on for pages that carry no heading or title of their own (scanned experience letters, plain
    # lists of jobs). A full page of readable text straight after a résumé page, with no evidence of any other
    # document, is taken to be its continuation, so no part of the résumé is left out. Reported as inherited.
    for index, text in enumerate(texts):
        if result[index].document_type != "unknown" or index == 0 or result[index - 1].document_type != "resume":
            continue
        stripped = (text or "").strip()
        if len(stripped) < 300 or sum(1 for line in stripped.splitlines() if line.strip()) < 8:
            continue
        if _other_evidence(text) or _is_passport(text) or _is_aadhaar(text):
            continue
        if not _reads_like_language(stripped):
            continue  # a page read sideways or badly is not taken for text: it is read again (or stays unclassified)
        result[index] = PageClassification(
            "resume", 0.55, "Assigned to the preceding resume; no content of its own identified it.", inherited=True,
        )
    return result


@dataclass
class PassportPlan:
    """Which of a document's passport pages are worth sending to the reader."""
    pages: list[int]       # the pages to send, in page order
    ranked: list[int]      # every page with passport evidence of its own, strongest first (duplicates last)
    duplicates: list[dict]  # [{"page": 8, "of": 11}]: a scan of the same data page, held back


def passport_plan(pages: Sequence[tuple[int, str, bool, float]]) -> PassportPlan | None:
    """The passport pages to send, from ``(page number, text, inherited, confidence)`` of every page taken for a passport.

    A page that only took the passport kind from its neighbour (``inherited``) carries nothing the reader needs and is
    not sent; a page with a passport caption of its own is (a lone caption still counts: it is the back or address
    page). Of several pages with the same machine-readable document number only the best is sent (valid check digits,
    then confidence). ``None`` when no page has evidence of its own: the caller then keeps every passport page."""
    own = [(number, text or "", confidence) for number, text, inherited, confidence in pages
           if not inherited or _lone_marker("passport", text or "")]
    if not own:
        return None
    from . import validators
    rows = []
    for number, text, confidence in own:
        try:
            mrz = validators.find_mrz(text) if text else None
        except Exception:  # an aid; never a reason to lose a page
            mrz = None
        rows.append({"page": number, "id": (mrz.document_number or "").strip("<") if mrz else "",
                     "score": (bool(mrz and mrz.valid), mrz is not None, float(confidence or 0.0))})
    best: dict[str, dict] = {}
    for row in rows:
        if row["id"] and (row["id"] not in best or row["score"] > best[row["id"]]["score"]):
            best[row["id"]] = row
    dropped = [row for row in rows if row["id"] and best[row["id"]] is not row]
    kept = [row for row in rows if row not in dropped]
    by_strength = lambda group: [row["page"] for row in sorted(group, key=lambda row: row["score"], reverse=True)]
    return PassportPlan(
        pages=sorted(row["page"] for row in kept), ranked=by_strength(kept) + by_strength(dropped),
        duplicates=sorted(({"page": row["page"], "of": best[row["id"]]["page"]} for row in dropped), key=lambda d: d["page"]),
    )
