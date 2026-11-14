"""Reject enrichment from a document whose known identity conflicts with its target."""
from app.services.candidate_identity import identity_matches, name_relation, normalize_passport
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


def identity_conflicts(record, values: dict, db=None) -> list[str]:
    """Return field names only. Initials, reordered names and missing middle names are tolerated.

    Missing identity is not proof of a mismatch: complementary back pages may carry only an address.
    Contact details alone may differ, but identifiers already owned by another candidate cannot enrich this one.
    """
    conflicts = []
    current_name = str(getattr(record, "name", None) or "").strip()
    incoming_name = str(values.get("name") or "").strip()
    if current_name and current_name.casefold() != UNNAMED.casefold() and incoming_name \
            and name_relation(current_name, incoming_name) == "different":
        conflicts.append("name")
    current_passport = normalize_passport(getattr(record, "passport_no", None))
    incoming_passport = normalize_passport(values.get("passport_no"))
    if current_passport and incoming_passport and current_passport != incoming_passport:
        conflicts.append("passport_no")
    # Fail closed on database errors; an identity check must not silently approve an uncertain match.
    if db is not None and any(values.get(key) for key in ("phone", "email", "passport_no")):
        if identity_matches(db, values, exclude_id=getattr(record, "id", None), lock=False):
            conflicts.append("candidate")
    return conflicts
