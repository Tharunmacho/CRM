"""Reject enrichment from a document whose known identity conflicts with its target."""
import re

from app.services.candidate_identity import _name_tokens, identity_matches, name_relation, normalize_passport
from app.services.candidate_review import UNNAMED

IDENTITY_REVIEW_MESSAGE = "This document appears to belong to another person. No details were filled. Upload the correct document."
IDENTITY_REJECTED_ACTION = "Rejected document autofill: identity mismatch"
IDENTITY_RESOLVED_ACTION = "Resolved document identity mismatch"


def rejected_expression():
    """A retained wrong-person file is review evidence, not an accepted checklist document.

    A later successful replay resolves only the exact candidate, attachment and
    extraction job that was originally rejected. Other rejected files, and
    legacy rows without a job scope, remain blocked.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import aliased
    from app.db.models import Attachment, AuditEntry
    rejected_entry = aliased(AuditEntry)
    resolved_entry = aliased(AuditEntry)
    identifier = rejected_entry.meta["attachment_id"].astext
    document_job_id = rejected_entry.meta["document_job_id"].astext
    resolved = select(resolved_entry.id).where(
        resolved_entry.action == IDENTITY_RESOLVED_ACTION,
        resolved_entry.entity_type == "candidate",
        resolved_entry.entity_id == rejected_entry.entity_id,
        resolved_entry.meta["attachment_id"].astext == identifier,
        resolved_entry.meta["document_job_id"].astext == document_job_id,
    ).correlate(rejected_entry).exists()
    rejected = select(identifier).where(
        rejected_entry.action == IDENTITY_REJECTED_ACTION,
        rejected_entry.entity_type == "candidate", identifier.is_not(None), ~resolved)
    return Attachment.id.in_(rejected).label("identity_mismatch")


def stored_document_names(record) -> list[str]:
    """The names carried by the documents already stored on the record (resume, passport and Aadhaar, the primary ones and
    the additional ones). The folder-name stand-in is not among them."""
    from app.services import form_extraction as fx
    names: list[str] = []
    data = getattr(record, "ocr_data", None)
    if isinstance(data, dict):
        for kind in fx.SLOT_KINDS:
            for raw in fx.stored_documents(data, kind):
                name = str(fx.values_for(kind, raw).get("name") or "").strip()
                if name:
                    names.append(name)
    return names


def _edit_distance_within(left: str, right: str, limit: int) -> bool:
    if abs(len(left) - len(right)) > limit:
        return False
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        row = [i]
        for j, b in enumerate(right, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = row
    return previous[-1] <= limit


def _name_forms(tokens: list[str]) -> set[str]:
    """Each word, plus every run of 2-3 neighbouring words written as one ("rashid khan" also reads "rashidkhan")."""
    forms = set(tokens)
    for size in (2, 3):
        for start in range(len(tokens) - size + 1):
            forms.add("".join(tokens[start:start + size]))
    return forms


def names_differ(left, right) -> bool:
    """True only when two renderings of a name can not be one person's.

    `name_relation` already tolerates order, initials and a missing middle name. On top of that a name written without a space
    ("Rashidkhan Nawazkhan" and "Rashid Khan Nawazkhan") and a one-letter spelling slip ("Rashiid" and "Rashid") are the same
    person; only names with nothing in common are different."""
    if name_relation(left, right) != "different":
        return False
    a, b = _name_tokens(left), _name_tokens(right)
    forms_a, forms_b = _name_forms(a), _name_forms(b)
    if any(len(form) >= 4 and form in forms_b for form in forms_a):
        return False
    for first in forms_a:
        for second in forms_b:
            longest = max(len(first), len(second))
            if min(len(first), len(second)) >= 5 and _edit_distance_within(first, second, 2 if longest >= 9 else 1):
                return False
    return True


KIND_PRIORITY = {"passport": 0, "aadhaar": 1, "resume": 2}   # whose name outranks: the passport, then the Aadhaar card, then the resume


def _is_strong(kind: str, fields: dict, raw: dict) -> bool:
    """A name read from a page that carries no identifier of its own (a "passport" with neither a number nor a machine-readable
    zone, an "Aadhaar" with no card number) may be any stray name on the page; it is not evidence about who the candidate is."""
    from app.services import form_extraction as fx
    if kind == "passport":
        mrz = raw.get("mrz") if isinstance(raw.get("mrz"), dict) else {}
        return bool(mrz.get("given_names") or mrz.get("surname") or fx.values_for("passport", raw).get("passport_no") or fields.get("passport_no"))
    if kind == "aadhaar":
        return bool(fx.aadhaar_last4(raw))
    return True


def document_names(extractions: dict) -> list[tuple[str, str]]:
    """(kind, name): the one name each document of a job carries (the name that is applied: the stored result's own, else the
    mapped one), strong readings only."""
    from app.services import form_extraction as fx
    found: list[tuple[str, str]] = []
    for kind, extraction in (extractions or {}).items():
        extraction = extraction if isinstance(extraction, dict) else {}
        fields = extraction.get("fields") if isinstance(extraction.get("fields"), dict) else {}
        raw = extraction.get("raw") if isinstance(extraction.get("raw"), dict) else {}
        name = str(fx.values_for(kind, raw).get("name") or fields.get("name") or "").strip()
        if name and _is_strong(kind, fields, raw):
            found.append((kind, name))
    return found


def name_disagreement(extractions: dict) -> dict | None:
    """The first two documents read in one job that name two different people (a resume of A and a passport of B), or None.
    One name per document: a document is never compared with itself."""
    names = sorted(document_names(extractions), key=lambda item: -KIND_PRIORITY.get(item[0], 3))
    for index, (kind, name) in enumerate(names):
        for other_kind, other in names[index + 1:]:
            if other_kind != kind and names_differ(name, other):
                return {"found": name, "documentKind": kind, "expected": other, "expectedFrom": "other_document", "expectedKind": other_kind}
    return None


def names_conflict_within(extractions: dict) -> bool:
    """True when two documents read in one job name two different people (a resume of A and a passport of B)."""
    return name_disagreement(extractions) is not None


def plausible_full_name(name) -> bool:
    """Two or more words of letters (no digits or symbols): good enough to replace a folder stand-in."""
    text = " ".join(str(name or "").split())
    words = text.split(" ")
    return bool(text) and len(text) <= 120 and len(words) >= 2 and all(re.fullmatch(r"[^\W\d_]+(?:['.-][^\W\d_]+)*\.?", word) for word in words)


def best_known_name(extractions: dict, record=None) -> str | None:
    """The name that should replace a folder stand-in: the passport's, else the Aadhaar card's, else the resume's, among the
    documents just read and those already stored on the candidate (the stored one wins a tie). Only a plausible full name."""
    from app.services import form_extraction as fx
    ranked: list[tuple[int, int, str]] = []
    data = getattr(record, "ocr_data", None)
    if isinstance(data, dict):
        for kind in fx.SLOT_KINDS:
            for raw in fx.stored_documents(data, kind):
                name = str(fx.values_for(kind, raw).get("name") or "").strip()
                if name and _is_strong(kind, {}, raw if isinstance(raw, dict) else {}):
                    ranked.append((KIND_PRIORITY.get(kind, 3), 0, name))
    ranked.extend((KIND_PRIORITY.get(kind, 3), 1, name) for kind, name in document_names(extractions))
    for _rank, _order, name in sorted(ranked):
        if plausible_full_name(name):
            return name
    return None


def identity_findings(record, values: dict, db=None) -> list[dict]:
    """Why a document does not belong to this record, one entry per cause: {"field": "name" | "passport_no" | "candidate", ...}.

    A name finding carries the name read (`found`) and the name it was compared with (`expected`, said to come `expectedFrom`
    the "candidate" or one of the candidate's "stored_document"s). A passport finding says nothing but its field. A candidate
    finding says which identifier is already owned (`matchedOn`: "phone number" / "email" / "passport number", never the value)
    and by whom (`duplicate`). Initials, reordered names and missing middle names are tolerated."""
    findings: list[dict] = []
    current_name = str(getattr(record, "name", None) or "").strip()
    incoming_name = str(values.get("name") or "").strip()
    from app.services.form_extraction import provisional_name
    if current_name and current_name.casefold() != UNNAMED.casefold() and incoming_name and not provisional_name(record) \
            and names_differ(current_name, incoming_name):
        findings.append({"field": "name", "found": incoming_name, "expected": current_name, "expectedFrom": "candidate"})
    elif incoming_name and provisional_name(record):
        # The folder-name stand-in is the only name exempt from the check: a document still has to agree with the names the
        # candidate's other stored documents already carry.
        stored = next((name for name in stored_document_names(record) if names_differ(name, incoming_name)), None)
        if stored:
            findings.append({"field": "name", "found": incoming_name, "expected": stored, "expectedFrom": "stored_document"})
    current_passport = normalize_passport(getattr(record, "passport_no", None))
    incoming_passport = normalize_passport(values.get("passport_no"))
    if current_passport and incoming_passport and current_passport != incoming_passport:
        findings.append({"field": "passport_no"})
    # Fail closed on database errors; an identity check must not silently approve an uncertain match.
    if db is not None and any(values.get(key) for key in ("phone", "email", "passport_no")):
        found = identity_matches(db, {key: values.get(key) for key in ("phone", "email", "passport_no")},
                                 exclude_id=getattr(record, "id", None), lock=False)
        if found:
            other = found[0][0]
            findings.append({"field": "candidate", "matchedOn": list(found[0][1] or []),
                             "duplicate": {"candidateId": getattr(other, "id", None), "careId": getattr(other, "care_id", None),
                                           "name": getattr(other, "name", None)}})
    return findings


def identity_conflicts(record, values: dict, db=None) -> list[str]:
    """Return field names only (see `identity_findings` for the reasons).

    Missing identity is not proof of a mismatch: complementary back pages may carry only an address.
    Contact details alone may differ, but identifiers already owned by another candidate cannot enrich this one.
    """
    return [finding["field"] for finding in identity_findings(record, values, db)]


def public_reason(finding: dict, document_kind: str | None = None) -> dict:
    """The part of a finding that is safe to store and show: the field, the kind of document, and for a name the two names.
    Never a passport number, phone number or e-mail address."""
    reason = {"field": finding["field"], "documentKind": finding.get("documentKind") or document_kind}
    if finding["field"] == "name":
        reason.update({key: finding[key] for key in ("found", "expected", "expectedFrom", "expectedKind") if finding.get(key)})
    elif finding["field"] == "candidate" and finding.get("matchedOn"):
        reason["matchedOn"] = [label for label in finding["matchedOn"] if label in ("phone number", "email", "passport number")]
    return reason
