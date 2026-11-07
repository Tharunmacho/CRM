"""Which Veris fields reached the candidate and the application form, and which did not.

Every non-empty value Veris returned is classified by its path:

* mapped     - read into a candidate column or a form row (see mapping.py and services/form_extraction.py)
* technical  - request ids, timings, derived duplicates and the like; kept in the stored document, nothing to fill in
* content    - a real detail about the person with no field to go to (LinkedIn, certifications, job descriptions ...);
               kept in the stored document and listed for a person to read
* sensitive  - an identifier that is deliberately never copied (full Aadhaar number, VID, card mobile number ...);
               kept in the stored document, listed WITHOUT its value
* unknown    - anything this app has never seen (a new Veris field): listed, and the extraction goes to review

The stored Veris document is never reduced; this is only the account of what happened to each part of it.
"""

from __future__ import annotations

import re
from typing import Any

# ``[]`` stands for any list index; ``[0]`` only for the first element (the only one that is read).
_RESUME = {
    "mapped": {
        "name", "contact.emails[0]", "contact.phones[0]", "contact.whatsapp", "contact.address",
        "personal_info.date_of_birth", "personal_info.gender", "personal_info.nationality",
        "personal_info.father_name", "personal_info.marital_status", "personal_info.languages_known[]",
        "passport_details.passport_number", "passport_details.date_of_issue",
        "passport_details.date_of_expiry", "passport_details.place_of_issue",
        "designation", "skills[]", "warnings[]",
        "education[].degree", "education[].institution", "education[].start", "education[].end",
        "experience[].title", "experience[].company", "experience[].country", "experience[].location",
        "experience[].start", "experience[].end", "experience[].duration_months", "experience[].duration_human",
        "experience[].is_overseas",
        "total_experience_months", "indian_experience_months", "overseas_experience_months",
    },
    "technical": {
        "request_id", "processing_time_ms", "highest_qualification",
        "total_experience_years", "total_experience_human", "indian_experience_years",
        "indian_experience_human", "overseas_experience_years", "overseas_experience_human",
    },
    "content": {
        "contact.emails[]", "contact.phones[]", "contact.linkedin", "contact.github", "contact.social[].platform",
        "contact.social[].url", "contact.websites[]", "personal_info.mother_name", "personal_info.place_of_birth",
        "personal_info.religion", "industry", "projects[].name", "projects[].description", "achievements[]",
        "certifications[]", "experience[].description",
    },
    "sensitive": set(),
}
_PASSPORT = {
    "mapped": {
        "mrz.given_names", "mrz.surname", "mrz.nationality", "mrz.date_of_birth", "mrz.sex",
        "mrz.passport_number", "mrz.expiry_date", "mrz.date_of_issue", "mrz.all_check_digits_valid",
        "mrz_source", "warnings[]",
    },
    "technical": {
        "request_id", "processing_time_ms", "confidence", "raw_mrz", "mrz.document_type",
        "mrz.individual_check_digits.passport_number", "mrz.individual_check_digits.date_of_birth",
        "mrz.individual_check_digits.expiry_date", "mrz.individual_check_digits.personal_number",
        "mrz.individual_check_digits.composite", "fields[].category", "fields[].page", "fields[].source",
        "fields[].confidence",
    },
    "content": {"mrz.issuing_country"},
    "sensitive": {"mrz.personal_number"},
}
_AADHAAR = {
    "mapped": {
        "aadhaar.name", "aadhaar.date_of_birth", "aadhaar.gender", "aadhaar.address", "aadhaar.care_of",
        "aadhaar.year_of_birth", "aadhaar.aadhaar_number_valid", "aadhaar.masked_aadhaar_number",
        "aadhaar.aadhaar_number", "warnings[]",
    },
    "technical": {
        "request_id", "page_count", "document_type", "languages_used", "total_processing_time_ms",
        "aadhaar.document_side",
    },
    "content": {"aadhaar.pincode"},
    "sensitive": {"aadhaar.mobile_number", "aadhaar.vid", "aadhaar.enrollment_id"},
}
REGISTRY = {"resume": _RESUME, "passport": _PASSPORT, "aadhaar": _AADHAAR}

# Printed passport fields (`fields[]`) that passport_values() reads, by normalised label.
_PASSPORT_LABELS = {
    "name", "given names", "given name", "surname", "date of birth", "dob", "sex", "gender", "nationality",
    "passport no", "passport number", "date of issue", "date of expiry", "expiry date", "date of expiration",
    "place of issue", "name of father", "name of father legal guardian", "fathers name", "father name",
    "father s name", "name of father or legal guardian", "legal guardian", "address", "permanent address",
    "present address", "residential address",
}


def _label(value: Any) -> str:
    return " ".join(re.findall(r"[a-z]+", str(value or "").casefold()))


def _leaves(value: Any, path: str = ""):
    """(path, value) of every non-empty scalar; ``pages`` is the document's text and is accounted for separately."""
    if isinstance(value, dict):
        for name, item in value.items():
            if not path and name == "pages":
                continue
            yield from _leaves(item, f"{path}.{name}" if path else str(name))
    elif isinstance(value, list):
        for position, item in enumerate(value):
            yield from _leaves(item, f"{path}[{position}]")
    elif value is None or value is False or value == 0 or (isinstance(value, str) and not value.strip()):
        return
    else:
        yield path, value


def classify(kind: str, path: str, payload: Any = None) -> str:
    registry = REGISTRY.get(kind)
    if registry is None:
        return "unknown"
    generic = re.sub(r"\[\d+\]", "[]", path)
    candidates = (path, re.sub(r"\[(?!0\])\d+\]", "[]", path), generic)
    for tier in ("mapped", "technical", "content", "sensitive"):
        if any(c in registry[tier] for c in candidates):
            return tier
    if kind == "passport" and generic in ("fields[].label", "fields[].value"):
        match = re.match(r"fields\[(\d+)\]", path)
        items = (payload or {}).get("fields") if isinstance(payload, dict) else None
        items = items if isinstance(items, list) else []
        item = items[int(match.group(1))] if match and int(match.group(1)) < len(items) else {}
        return "mapped" if isinstance(item, dict) and _label(item.get("label")) in _PASSPORT_LABELS else "content"
    return "unknown"


def field_report(kind: str, payload: Any) -> dict:
    """Counts per tier and the grouped list of everything that was not mapped."""
    counts = {"mapped": 0, "technical": 0, "content": 0, "sensitive": 0, "unknown": 0}
    groups: dict[tuple[str, str], dict] = {}
    for path, value in _leaves(payload):
        tier = classify(kind, path, payload)
        counts[tier] += 1
        if tier in ("mapped", "technical"):
            continue
        generic = re.sub(r"\[\d+\]", "[]", path)
        entry = groups.setdefault((tier, generic), {"path": generic, "count": 0, "values": [], "tier": tier})
        entry["count"] += 1
        if tier != "sensitive" and len(entry["values"]) < 5:
            text = value if isinstance(value, str) else str(value)
            entry["values"].append(text if len(text) <= 200 else text[:197] + "...")
    received = sum(counts.values())
    return {
        "received": received, "mapped": counts["mapped"], "technical": counts["technical"],
        "unmapped": sorted(groups.values(), key=lambda e: (e["tier"] != "unknown", e["path"])),
        "unknown": counts["unknown"], "pages": len((payload or {}).get("pages") or []) if isinstance(payload, dict) else 0,
    }


def schema_paths(model) -> set[str]:
    """Every leaf path of an SDK response model, in the registry's notation (used to keep the registry complete)."""
    from typing import get_args, get_origin
    from pydantic import BaseModel

    out: set[str] = set()

    def walk(cls, prefix: str) -> None:
        for name, info in cls.model_fields.items():
            if not prefix and name == "pages":
                continue
            target, path = info.annotation, f"{prefix}{name}"
            origin = get_origin(target)
            args = [a for a in get_args(target) if a is not type(None)]
            if origin is list and args:
                path, target = path + "[]", args[0]
            elif args and origin is not None and len(args) == 1:
                target = args[0]
            if isinstance(target, type) and issubclass(target, BaseModel):
                walk(target, path + ".")
            else:
                out.add(path)

    walk(model, "")
    return out


def row_report(kind: str, payload: Any) -> list[dict]:
    """Rows and list items the form could not take: more than its capacity, or entries with nothing to name them.

    Each item is {"what", "received", "mapped", "left_out"}; the entries themselves stay in the stored document."""
    if kind != "resume" or not isinstance(payload, dict):
        return []
    from app.services import form_extraction as fx

    out: list[dict] = []

    def add(what: str, received: int, mapped: int, distinct: int | None = None) -> None:
        # Entries repeated word for word are not "left out": only capacity or unnameable entries are.
        left = max(0, (distinct if distinct is not None else received) - mapped)
        if left:
            out.append({"what": what, "received": received, "mapped": mapped, "left_out": left})

    education = [e for e in payload.get("education") or [] if e]
    history = [e for e in payload.get("experience") or [] if isinstance(e, dict)]
    add("education", len(education), len(fx.education_rows(payload)), len(fx.education_rows(payload, None)))
    add("work history", len(history), len(fx.history_rows(payload)), len(fx.history_rows(payload, None)))
    nameless = [e for e in history if not fx._first(e, "company", "employer", "organization", "organisation", "company_name")
                and not fx._first(e, "title", "designation", "position", "role", "job_title")
                and any(v not in (None, "", 0, False) for v in e.values())]
    if nameless:
        out.append({"what": "work history entries without a company or job title", "received": len(history),
                    "mapped": len(history) - len(nameless), "left_out": len(nameless)})
    skills = fx._unique([k for k in (fx._s(k, 200) for k in fx._list(payload.get("skills"))) if k]) or []
    add("skills", len(skills), min(len(skills), 200))
    info = fx._dict(payload.get("personal_info"))
    languages = fx._unique([k for k in (fx._s(k, 80) for k in fx._list(info.get("languages_known"))) if k]) or []
    add("languages", len(languages), min(len(languages), 30))
    return out
