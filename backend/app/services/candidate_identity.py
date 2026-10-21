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
        previous = copy.deepcopy(candidate.ocr_data or {})
        for kind, data in ocr_data.items():
            if kind not in previous:
                previous[kind] = data
            elif previous[kind] != data:
                previous.setdefault("additional_documents", []).append({kind: data})
        candidate.ocr_data = previous
    return changed
