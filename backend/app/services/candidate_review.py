"""Why a candidate is waiting, and the part of that wait an assignment can clear.

``Candidate.needs_review`` conflates two different situations: the intake could not
trust the extracted data (missing name or phone, OCR warnings) and nobody was eligible
to own the new candidate. Assigning a recruiter resolves only the second. The reason is
derived from the profile itself (no name / no phone) plus one append-only audit marker
for OCR warnings, so no schema change is needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import AuditEntry, Candidate, CandidateStatus, Notification, Role, Task, TaskKind, User

OCR_FLAG_ACTION = "Flagged for document review"
UNNAMED = "Name needs review"
WAITING_TITLE = "Candidate received — needs assignment"


def has_extraction_warnings(results) -> bool:
    """True when any extracted document carried a warning (same rule as combine_extractions)."""
    for result in results or []:
        if result.get("warnings"):
            return True
        for extraction in (result.get("extractions") or {}).values():
            if extraction.get("warnings"):
                return True
    return False


def profile_gaps(candidate) -> set[str]:
    gaps = set()
    if not (candidate.name or "").strip() or candidate.name.strip() == UNNAMED:
        gaps.add("missing_name")
    if not (candidate.phone or "").strip():
        gaps.add("missing_phone")
    return gaps


def record_review_flag(db, candidate, *, ocr_warning: bool) -> None:
    """Remember that the documents, not the missing owner, need a person's attention."""
    if ocr_warning:
        db.add(AuditEntry(action=OCR_FLAG_ACTION, target=candidate.name, entity_type="candidate",
                          entity_id=candidate.id, meta={"reasons": ["ocr_warning"]}))


def review_reasons(db, candidates) -> dict[str, set[str]]:
    """Quality reasons still blocking each candidate, excluding the missing owner."""
    ids = [candidate.id for candidate in candidates]
    flagged = set(db.scalars(select(AuditEntry.entity_id).where(
        AuditEntry.entity_type == "candidate", AuditEntry.action == OCR_FLAG_ACTION, AuditEntry.entity_id.in_(ids)))) if ids else set()
    return {candidate.id: profile_gaps(candidate) | ({"ocr_warning"} if candidate.id in flagged else set())
            for candidate in candidates}


def release_ownership_review(candidate, reasons: set[str]) -> bool:
    """Clear review (and the review status) when the missing owner was its only cause."""
    if not candidate.needs_review or reasons:
        return False
    candidate.needs_review = False
    if candidate.status == CandidateStatus.needs_review:
        candidate.status = CandidateStatus.new
    return True


def start_initial_call(db, candidate, owner_id: str, at: datetime) -> bool:
    """The 48-hour initial-call task, once, for a candidate that has no work yet."""
    if db.scalar(select(Task.id).where(Task.candidate_id == candidate.id).limit(1)) or candidate.call_recorded_at:
        return False
    db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest", candidate_id=candidate.id,
                owner_id=owner_id, created_at=at, sla_started_at=at, due_at=at + timedelta(hours=48)))
    return True


def notify_candidate_received(db, candidate, owner, *, channel: str, kind: str, results) -> None:
    """Receipt notices for a candidate created by intake, plus the review marker and the
    best-effort WhatsApp conversation link. Ownerless candidates get a distinct notice."""
    record_review_flag(db, candidate, ocr_warning=has_extraction_warnings(results))
    # PRD: the owner and the admin are told about an assignment. Admins and managers both hear about a candidate nobody could take.
    told = [Role.admin] if owner else [Role.admin, Role.manager]
    recipients = set(db.scalars(select(User.id).where(User.role.in_(told), User.active.is_(True))))
    if owner:
        recipients.add(owner.id)
    for recipient in recipients:
        db.add(Notification(
            user_id=recipient, kind=kind,
            title=f"Candidate received by {channel}" if owner else WAITING_TITLE,
            detail=candidate.name if owner else f"{candidate.name} · received by {channel}, no recruiter is available yet",
            link_to=f"/candidates/{candidate.id}"))
    from app.services.whatsapp_chat import link_conversations_for_candidate
    link_conversations_for_candidate(db, candidate)


def eligible_for_assignment(user) -> bool:
    return bool(user and user.role == Role.staff and user.active and user.receives_auto_assign and not user.on_leave)


def waiting_count(db) -> int:
    return db.scalar(select(func.count()).select_from(Candidate).where(Candidate.owner_id.is_(None))) or 0


def assign_waiting_candidates(db, actor_id: str | None, actor_name: str) -> dict:
    """Round-robin every owner-less candidate to the eligible staff, oldest first.

    Runs in the caller's transaction and under the same assignment-cursor lock as intake
    and reassignment, so no candidate is handed to two recruiters. Stops quietly when
    nobody is eligible and reports how many are still waiting.
    """
    from app.api.routers.candidates import lock_assignment_cursor, next_owner
    from app.services.assignment_notifications import notify_assignment_changes

    pointer = lock_assignment_cursor(db)
    waiting = list(db.scalars(select(Candidate).where(Candidate.owner_id.is_(None))
                              .order_by(Candidate.created_at, Candidate.id).with_for_update()))
    reasons = review_reasons(db, [candidate for candidate in waiting if candidate.needs_review])
    now = datetime.now(timezone.utc)
    changes, assigned, cleared, per_owner = [], 0, 0, {}
    for candidate in waiting:
        owner = next_owner(db, pointer=pointer)
        if owner is None:
            break
        candidate.owner_id = owner.id
        if candidate.intake_owner_id is None:
            candidate.intake_owner_id = owner.id
        cleared += release_ownership_review(candidate, reasons.get(candidate.id, set()))
        start_initial_call(db, candidate, owner.id, now)
        db.add(AuditEntry(actor_id=actor_id, action="Candidate assigned from waiting list", target=candidate.name,
                          entity_type="candidate", entity_id=candidate.id, at=now,
                          meta={"fromUserId": None, "toUserId": owner.id, "reason": "Assigned from the waiting list"}))
        changes.append((candidate.id, candidate.name, None, owner.id))
        per_owner[owner.id] = per_owner.get(owner.id, 0) + 1
        assigned += 1
    if changes:
        notify_assignment_changes(db, changes, reason=f"Assigned from the waiting list by {actor_name}.")
        db.add(AuditEntry(actor_id=actor_id, action="Assigned waiting candidates", target=f"{assigned} candidates",
                          entity_type="candidate", meta={"count": assigned, "perOwner": per_owner}))
    return {"assigned": assigned, "reviewCleared": cleared, "remaining": len(waiting) - assigned}
