"""Plain-English notes for the people who upload documents.

Reading a file produces a raw list of warnings: remarks from the page checker, the passport zone reader, the
document reader and the cross-checks. They are useful to whoever diagnoses a reading, and bewildering to a recruiter
("MRZ region detected but its structure could not be parsed"). Storage keeps that raw list (the diagnostics read it);
every place that shows warnings to a person passes them through :func:`plain_notes` first.

The rules, in order:

* an identity conflict ("Different candidate identities ... Review people and pages") is kept word for word, because
  it decides whether anything may be saved;
* pure implementation remarks (the language model, vision reads, zone structure, "validated before persistence",
  enlarged crops, details this app has not seen before, "read from the printed page") are dropped and logged at DEBUG;
* every passport disagreement or failed check digit becomes ONE note naming what to check;
* pages that could not be read or identified become ONE note ("Some pages could not be read ...");
  pages that were only assigned to the neighbouring document are dropped (nothing the person can act on);
* anything not recognised is kept exactly as it was: an unknown warning is never swallowed.

A warning that names one kind (passport, Aadhaar, resume) belongs to that kind only: asked for another kind's notes,
it is left out. Warnings that name no kind, or several, belong to every kind.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("crm.reading")

PAGES_NOTE = "Some pages could not be read. Check the original file."
PASSPORT_GENERAL_NOTE = "Passport: please check the number and dates against the passport."
CHECK_ORIGINAL_NOTE = "Some details need a second look. Check them against the original file."

# Kept verbatim: these gate saving (the upload screen looks for the same phrases).
_IDENTITY = re.compile(r"different candidate identities|more than one person|people and pages", re.I)

# Remarks about how a reading was done; nobody can act on them.
_TECHNICAL = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bstructured\s+llm\b",
    r"validated before persistence",
    r"\bMRZ region detected\b",
    r"\bvision\s+llm\b",
    r"back-page vision",
    r"enlarged local crop",
    r"has not seen before|not seen before",
    r"no place on the form",
    r"read from the printed page, not the coded lines",
    r"were checked as a possible .+ and none was found",
    r"identified as an? .+ by the document reader, not by the local check",
))

_CHECK_DIGITS = re.compile(r"check[ -]digits?", re.I)
# "The passport read locally and by the document reader disagree on date_of_expiry. Compare ..."
_CROSS_CHECK = re.compile(r"^The (?P<kind>[\w ]+?) read locally and by the document reader disagree on (?P<fields>[\w.]+(?:\s*(?:,|and)\s*[\w.]+)*)(?:\.\s|\.$)", re.I)
# "Different parts of the passport gave different values for mrz.expiry_date, mrz.date_of_issue. The first was kept; ..."
_PARTS_DIFFER = re.compile(r"^Different parts of the (?P<kind>[\w ]+?) gave different values for (?P<fields>[\w.]+(?:\s*(?:,|and)\s*[\w.]+)*)(?:\.\s|\.$)", re.I)

# Pages the reader could not make sense of.
_UNREADABLE_PAGES = tuple(re.compile(pattern, re.I) for pattern in (
    r"^pages? \d[^.]*\bcould not be (?:read|identified)\b",
    r"^we could not tell what kind of pages?\b",
    r"^some pages are unread or unclassified",
))
# Pages with no content of their own that took the neighbouring document's kind.
_ASSIGNED_PAGES = re.compile(r"^pages? \d[^.]*\bassigned to the adjoining document", re.I)

_FIELD_WORDS = {
    "date_of_expiry": "date of expiry", "expiry_date": "date of expiry", "expiry": "date of expiry", "expires": "date of expiry",
    "date_of_issue": "date of issue", "issue_date": "date of issue", "issued": "date of issue",
    "date_of_birth": "date of birth", "birth_date": "date of birth", "dob": "date of birth",
    "passport_number": "number", "document_number": "number", "number": "number", "passport_no": "number",
    "place_of_issue": "place of issue", "surname": "name", "given_names": "name", "name": "name",
    "sex": "sex", "gender": "sex", "nationality": "nationality",
}

_KINDS = (("passport", re.compile(r"passport", re.I)),
          ("aadhaar", re.compile(r"aadhaa?r", re.I)),
          ("resume", re.compile(r"r[eé]sum[eé]|\bcv\b", re.I)))


def _kind_named(text: str) -> str | None:
    """The one kind a warning is about, or None when it names none or several."""
    named = [kind for kind, pattern in _KINDS if pattern.search(text)]
    return named[0] if len(named) == 1 else None


def _words(raw_fields: str) -> list[str]:
    out: list[str] = []
    for token in re.split(r",|\band\b", raw_fields):
        name = token.strip().rsplit(".", 1)[-1].strip().casefold().replace(" ", "_")
        if not name:
            continue
        word = _FIELD_WORDS.get(name) or name.replace("_", " ")
        if word not in out:
            out.append(word)
    return out


def _joined(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _passport_note(fields: list[str], general: bool) -> str:
    if general or not fields:
        return PASSPORT_GENERAL_NOTE
    return f"Passport: please check the {_joined(fields)} against the passport."


def plain_notes(raw_warnings: list[str], *, kind: str | None = None) -> list[str]:
    """Short, de-duplicated notes a recruiter understands, from a reading's raw warnings.

    ``kind`` (``resume`` / ``passport`` / ``aadhaar``) keeps out the warnings that are about another kind. Dropped
    technical remarks are logged at DEBUG on ``crm.reading``. Unrecognised warnings are returned unchanged.
    """
    ordered: dict[str, str | None] = {}  # note key -> text (None while it is built from several warnings)
    passport_fields: list[str] = []
    passport_general = False
    for warning in raw_warnings or []:
        if not isinstance(warning, str) or not warning.strip():
            continue
        text = warning.strip()
        named = _kind_named(text)
        if kind is not None and named is not None and named != kind:
            continue
        if _IDENTITY.search(text):
            ordered.setdefault(text, text)
        elif any(pattern.search(text) for pattern in _TECHNICAL):
            log.debug("Reading remark kept out of the notes: %s", text)
        elif named == "passport" and _CHECK_DIGITS.search(text):
            passport_general = True
            ordered.setdefault("passport", None)
        elif (match := _CROSS_CHECK.match(text) or _PARTS_DIFFER.match(text)) and _kind_named(match.group("kind")) == "passport":
            for word in _words(match.group("fields")):
                if word not in passport_fields:
                    passport_fields.append(word)
            ordered.setdefault("passport", None)
        elif any(pattern.search(text) for pattern in _UNREADABLE_PAGES):
            ordered.setdefault("pages", PAGES_NOTE)
        elif _ASSIGNED_PAGES.search(text):
            log.debug("Reading remark kept out of the notes: %s", text)
        else:
            ordered.setdefault(text, text)
    notes: list[str] = []
    for key, text in ordered.items():
        note = _passport_note(passport_fields, passport_general) if key == "passport" else text
        if note and note not in notes:
            notes.append(note)
    return notes


def plain_issues(raw_issues: list[str]) -> list[str]:
    """Like :func:`plain_notes` for the reasons a reading is incomplete: never an empty list for a non-empty one.

    A reason that was only a technical remark still means "not fully checked", so the person gets one generic line
    rather than an incomplete reading with no explanation.
    """
    notes = plain_notes(raw_issues)
    if not notes and any(isinstance(i, str) and i.strip() for i in raw_issues or []):
        return [CHECK_ORIGINAL_NOTE]
    return notes
