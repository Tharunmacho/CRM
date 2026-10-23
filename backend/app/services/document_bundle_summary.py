"""Small batched source summaries; page OCR and document bodies stay out of inbox lists."""
from sqlalchemy import select
from app.db.models import DocumentPersonGroup, MultiPersonIntake


def source_summaries(db, source_type, source_ids):
    if isinstance(source_ids, (list, tuple, set)) and not source_ids:
        return {}  # a SELECT of ids is also accepted, so callers need not wait for them
    rows = db.execute(select(MultiPersonIntake.id, MultiPersonIntake.source_id, MultiPersonIntake.status,
                            DocumentPersonGroup.candidate_id)
        .outerjoin(DocumentPersonGroup, DocumentPersonGroup.bundle_id == MultiPersonIntake.id)
        .where(MultiPersonIntake.source_type == source_type, MultiPersonIntake.source_id.in_(source_ids))
        .order_by(MultiPersonIntake.id, DocumentPersonGroup.ordinal)).all()
    result = {}
    for identifier, source_id, status, candidate_id in rows:
        summary = result.setdefault(source_id, {"id": identifier, "status": status, "candidateIds": []})
        if candidate_id and candidate_id not in summary["candidateIds"]:
            summary["candidateIds"].append(candidate_id)
    return result
