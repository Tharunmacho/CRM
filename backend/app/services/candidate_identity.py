"""Shared manual/email/WhatsApp identity and conservative enrichment rules.

Callers serialize lookup+create with the assignment cursor lock. Ambiguous OR
matches must be reviewed; never choose an arbitrary first candidate to merge.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from sqlalchemy import case, func, or_, select
from app.db.models import Candidate


def text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalize_phone(value):
    digits = re.sub(r"\D", "", text(value))
    if digits.startswith("00"):
        digits = digits[2:]
    # Domestic Indian prefixes are equivalent. Other country codes are retained,
    # so unrelated international numbers are not collapsed to the same last ten.
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else ""


def normalize_email(value):
    return text(value).lower()


def normalize_passport(value):
    return re.sub(r"\s", "", text(value)).upper()


def normalized_labels(values):
    seen, result = set(), []
    for raw in values or []:
        label = " ".join(text(raw).split())
        key = label.lower()
        if label and key not in seen:
            result.append(label); seen.add(key)
    return result


def normalize_candidate_values(values):
    cleaned = {key: text(val) if isinstance(val, str) else val for key, val in values.items()}
    if "email" in cleaned:
        cleaned["email"] = normalize_email(cleaned["email"]) or None
    if "passport_no" in cleaned:
        cleaned["passport_no"] = normalize_passport(cleaned["passport_no"]) or None
    for key in ("skills", "designations", "languages"):
        if key in cleaned:
            cleaned[key] = normalized_labels(cleaned[key])
    return cleaned


def identity_matches(db, fields, exclude_id=None, lock=True):
    clauses = []
    phone, email, passport = normalize_phone(fields.get("phone")), normalize_email(fields.get("email")), normalize_passport(fields.get("passport_no"))
    if phone:
        digits = func.regexp_replace(Candidate.phone, r"\D", "", "g")
        international = case((func.left(digits, 2) == "00", func.substr(digits, 3)), else_=digits)
        normalized = case(((func.length(international) == 12) & (func.left(international, 2) == "91"), func.substr(international, 3)),
                          ((func.length(international) == 11) & (func.left(international, 1) == "0"), func.substr(international, 2)), else_=international)
        clauses.append(normalized == phone)
    if email:
        clauses.append(func.lower(func.trim(Candidate.email)) == email)
    if passport:
        clauses.append(func.upper(func.regexp_replace(Candidate.passport_no, r"\s", "", "g")) == passport)
    if not clauses:
        return []
    query = select(Candidate).where(or_(*clauses))
    if exclude_id:
        query = query.where(Candidate.id != exclude_id)
    if lock:
        query = query.with_for_update()
    result = []
    for candidate in db.scalars(query):
        labels = []
        if phone and normalize_phone(candidate.phone) == phone:
            labels.append("phone number")
        if email and normalize_email(candidate.email) == email:
            labels.append("email")
        if passport and normalize_passport(candidate.passport_no) == passport:
            labels.append("passport number")
        result.append((candidate, labels))
    return result


def enrich_empty(candidate, values, ocr_data=None):
    changed = []
    for key, val in normalize_candidate_values(values).items():
        if val in (None, "", [], {}) or key in ("owner_id", "intake_owner_id", "status", "source", "source_detail", "created_by"):
            continue
        if getattr(candidate, key) in (None, "", [], {}):
            setattr(candidate, key, val); changed.append(key)
    if ocr_data:
        merged = merge_ocr_data(candidate.ocr_data, ocr_data)
        if merged != (candidate.ocr_data or {}):
            candidate.ocr_data = merged
    return changed


_RESERVED = {"_sources", "additional_documents"}


def _provenance(data, source):
    """Where a stored document came from: its own ``_source`` note, or for rows saved
    before provenance moved out of the payload, the keys that used to sit inside it."""
    if isinstance(source, dict) and source:
        return source
    if isinstance(data, dict):
        pages = (data.get("local_inspection") or {}).get("selected_pages")
        if data.get("source_sha256"):
            return {"source_sha256": data["source_sha256"], "selected_pages": pages}
    return {}


def _same_document(left, right, left_source=None, right_source=None):
    """The same read of the same bytes. The provenance note is not part of the content,
    and a verbatim Veris result is never rewritten to decide this."""
    if not (isinstance(left, dict) and isinstance(right, dict)):
        return left == right
    lp, rp = _provenance(left, left_source), _provenance(right, right_source)
    if lp.get("source_sha256") and rp.get("source_sha256"):
        return lp["source_sha256"] == rp["source_sha256"] and lp.get("selected_pages") is not None and lp.get("selected_pages") == rp.get("selected_pages")
    left, right = ({k: v for k, v in item.items() if k != "source_file"} for item in (left, right))
    return left == right


def merge_ocr_data(existing, incoming):
    """Add stored Veris output without discarding any. Each ``ocr_data[kind]`` is the
    verbatim Veris result; our provenance for it lives in ``_sources[kind]``. The first
    document of a kind stays primary (it is the one the candidate's columns were filled
    from) and every other distinct document of that kind is kept in
    ``additional_documents`` as ``{kind: raw, "_source": {...}}``. Rows saved in the old
    shape (provenance inside the document) are kept untouched and still deduplicate."""
    merged = copy.deepcopy(existing or {})
    extra = [entry for entry in merged.pop("additional_documents", None) or [] if isinstance(entry, dict)]
    sources = merged.pop("_sources", None)
    sources = sources if isinstance(sources, dict) else {}
    incoming = incoming or {}
    incoming_sources = incoming.get("_sources") if isinstance(incoming.get("_sources"), dict) else {}
    documents = [(kind, data, incoming_sources.get(kind)) for kind, data in incoming.items() if kind not in _RESERVED]
    for entry in incoming.get("additional_documents") or []:
        if isinstance(entry, dict):
            documents.extend((kind, data, entry.get("_source")) for kind, data in entry.items() if kind != "_source")
    for kind, data, source in documents:
        known = ([(merged[kind], sources.get(kind))] if kind in merged else []) + [(item[kind], item.get("_source")) for item in extra if kind in item]
        if any(_same_document(item, data, item_source, source) for item, item_source in known):
            continue
        if kind in merged:
            extra.append({kind: copy.deepcopy(data), **({"_source": copy.deepcopy(source)} if source else {})})
        else:
            merged[kind] = copy.deepcopy(data)
            if source:
                sources[kind] = copy.deepcopy(source)
    if sources:
        merged["_sources"] = sources
    if extra:
        merged["additional_documents"] = extra
    return merged


def _name_tokens(value):
    return [t for t in re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKC", str(value or "")).casefold())]


def _token_match(a, b):
    return a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def name_relation(left, right):
    """'same', 'partial' or 'different' for two renderings of a person's name.

    Order, a missing middle name and initials ("Rahul K Singh", "Singh Rahul",
    "R. Singh") do not make two names different. Only names with no word in common
    (and no initial that fits a word) are 'different'.
    """
    a, b = _name_tokens(left), _name_tokens(right)
    if not a or not b:
        return "same"
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    free = list(large)
    matched = 0
    for token in small:
        hit = next((other for other in free if _token_match(token, other)), None)
        if hit is not None:
            free.remove(hit)
            matched += 1
    if matched == 0:
        return "different"
    return "same" if matched == len(small) == len(large) else "partial"
