"""Latest explicit document checklist confirmations, retained as audit history."""
from sqlalchemy import select

from app.db.models import AuditEntry, User

ACTION = "Changed document checklist status"


def latest_statement(candidate_ids=None):
    slot = AuditEntry.meta["slot"].astext
    query = select(AuditEntry.entity_id.label("candidate_id"), slot.label("slot"),
                   AuditEntry.meta["uploaded"].astext.label("uploaded"), AuditEntry.meta["note"].astext.label("note"), AuditEntry.actor_id,
                   AuditEntry.at, User.name.label("actor_name")).outerjoin(User, User.id == AuditEntry.actor_id).where(
        AuditEntry.entity_type == "candidate", AuditEntry.action == ACTION, slot.is_not(None))
    if candidate_ids is not None:
        query = query.where(AuditEntry.entity_id.in_(candidate_ids))
    return query.distinct(AuditEntry.entity_id, slot).order_by(AuditEntry.entity_id, slot, AuditEntry.at.desc(), AuditEntry.id.desc())


def statuses_by_candidate(rows):
    result = {}
    for row in rows or []:
        row = row if isinstance(row, dict) else row._mapping
        uploaded = row["uploaded"]
        if uploaded not in ("true", "false", True, False):
            continue
        at = row["at"]
        result.setdefault(row["candidate_id"], {})[row["slot"]] = {
            "uploaded": uploaded in ("true", True), "by": {"id": row["actor_id"], "name": row["actor_name"] or "Former team member"},
            "at": at.isoformat() if hasattr(at, "isoformat") else at, "note": row["note"] or "",
        }
    return result


def read_manual_document_statuses(db, candidate_ids):
    return statuses_by_candidate(db.execute(latest_statement(candidate_ids)).all())


def manual_document_statuses(db, candidate_id):
    return read_manual_document_statuses(db, [candidate_id]).get(candidate_id, {})
