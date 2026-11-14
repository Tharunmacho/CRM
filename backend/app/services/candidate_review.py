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
OCR_FLAG_RESOLVED_ACTION = "Resolved document review flag"
UNNAMED = "Name needs review"
NO_NAME_REASON = "We could not read a name from this file. Open it and enter the details, or upload a clearer copy."
NO_CONTACT_REASON = ("We could not read a phone number or email from this file. "
                     "Open it and enter the details, or upload a clearer copy.")
WAITING_TITLE = "Candidate waiting for a recruiter"


def has_extraction_warnings(results) -> bool:
    """True when any extracted document carried a warning (same rule as combine_extractions)."""
    for result in results or []:
        if result.get("warnings"):
            return True
        for extraction in (result.get("extractions") or {}).values():
            if extraction.get("warnings"):
                return True
    return False


def has_real_name(name) -> bool:
    """A name a person can be called by: not blank and never the old placeholder."""
    text = (name or "").strip()
    return bool(text) and text.casefold() != UNNAMED.casefold()


def candidate_refusal(values) -> str | None:
    """Why a candidate row may NOT be written yet, in plain English, or None when it may.

    A candidate exists only once a source has been read and gave a real identity: a name,
    plus a way to reach the person (phone or email) or, for a passport / Aadhaar-only
    document, the passport number. A missing phone alone (name and email, or name and
    passport) is fine: the candidate is created and flagged for review.
    """
    if not has_real_name(values.get("name")):
        return NO_NAME_REASON
    if not any(str(values.get(key) or "").strip() for key in ("phone", "email", "passport_no")):
        return NO_CONTACT_REASON
    return None


def profile_gaps(candidate) -> set[str]:
    gaps = set()
    if not (candidate.name or "").strip() or candidate.name.strip() == UNNAMED:
        gaps.add("missing_name")
    if not (candidate.phone or "").strip():
        gaps.add("missing_phone")
    return gaps


def _document_review_scope(meta) -> tuple[str, str] | None:
    """Return an exact document-review scope, never a best-effort match.

    Old generic OCR flags deliberately remain unscoped. They cannot be
    automatically cleared by a later document replay because doing so could
    hide an unrelated warning for the same candidate.
    """
    meta = meta if isinstance(meta, dict) else {}
    attachment_id, document_job_id = meta.get("attachment_id"), meta.get("document_job_id")
    if not isinstance(attachment_id, str) or not attachment_id.strip():
        return None
    if not isinstance(document_job_id, str) or not document_job_id.strip():
        return None
    return attachment_id, document_job_id


def record_review_flag(db, candidate, *, ocr_warning: bool, attachment_id: str | None = None,
                       document_job_id: str | None = None) -> None:
    """Remember that the documents, not the missing owner, need a person's attention."""
    if ocr_warning:
        meta = {"reasons": ["ocr_warning"]}
        scope = _document_review_scope({"attachment_id": attachment_id, "document_job_id": document_job_id})
        if scope:
            meta.update({"attachment_id": scope[0], "document_job_id": scope[1]})
        db.add(AuditEntry(action=OCR_FLAG_ACTION, target=candidate.name, entity_type="candidate",
                          entity_id=candidate.id, meta=meta))


def resolve_document_review_flag(db, candidate, *, actor_id: str | None, attachment_id: str | None,
                                 document_job_id: str | None) -> bool:
    """Append evidence that one specific OCR warning was resolved by a safe replay."""
    scope = _document_review_scope({"attachment_id": attachment_id, "document_job_id": document_job_id})
    if not scope:
        return False
    db.add(AuditEntry(actor_id=actor_id, action=OCR_FLAG_RESOLVED_ACTION, target=candidate.name,
                      entity_type="candidate", entity_id=candidate.id,
                      meta={"attachment_id": scope[0], "document_job_id": scope[1],
                            "resolves": OCR_FLAG_ACTION}))
    return True


def review_reasons(db, candidates) -> dict[str, set[str]]:
    """Quality reasons still blocking each candidate, excluding the missing owner."""
    ids = [candidate.id for candidate in candidates]
    flags, resolutions = set(), set()
    if ids:
        # Some worker sessions intentionally disable autoflush. A just-added
        # resolution must be visible before deciding whether a candidate can
        # leave review, while the surrounding transaction remains atomic.
        db.flush()
        rows = db.execute(select(AuditEntry.entity_id, AuditEntry.action, AuditEntry.meta).where(
            AuditEntry.entity_type == "candidate", AuditEntry.action.in_((OCR_FLAG_ACTION, OCR_FLAG_RESOLVED_ACTION)),
            AuditEntry.entity_id.in_(ids))).all()
        for candidate_id, action, meta in rows:
            scope = _document_review_scope(meta)
            if action == OCR_FLAG_RESOLVED_ACTION and scope:
                resolutions.add((candidate_id, *scope))
            elif action == OCR_FLAG_ACTION:
                flags.add((candidate_id, *scope) if scope else (candidate_id, None, None))
    flagged = {candidate_id for candidate_id, attachment_id, document_job_id in flags
               if attachment_id is None or (candidate_id, attachment_id, document_job_id) not in resolutions}
    return {candidate.id: profile_gaps(candidate) | ({"ocr_warning"} if candidate.id in flagged else set())
            for candidate in candidates}


def clear_resolved_document_review(db, candidate) -> bool:
    """Lift only a resolved, document-caused review state.

    Ownerless candidates remain in review for assignment. Any remaining
    profile gap, legacy generic flag, or different scoped document warning
    also keeps the review state intact.
    """
    if not candidate.needs_review or not getattr(candidate, "owner_id", None):
        return False
    return release_ownership_review(candidate, review_reasons(db, [candidate]).get(candidate.id, set()))


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


def notify_candidate_received(db, candidate, owner, *, channel: str, kind: str | None = None, results) -> None:
    """Receipt notices for a candidate created by intake, plus the review marker and the
    best-effort WhatsApp conversation link. The owner is told it was assigned to them; only a candidate nobody
    could take alerts the admins and managers. (`kind` is accepted for older callers and ignored.)"""
    record_review_flag(db, candidate, ocr_warning=has_extraction_warnings(results))
    from app.services.assignment_notifications import notify_new_candidate
    notify_new_candidate(db, candidate, owner, channel=channel)
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
    from app.services import client_routing
    from app.services.assignment_notifications import notify_assignment_changes

    pointer = lock_assignment_cursor(db)
    waiting = list(db.scalars(select(Candidate).where(Candidate.owner_id.is_(None))
                              .order_by(Candidate.created_at, Candidate.id).with_for_update()))
    reasons = review_reasons(db, [candidate for candidate in waiting if candidate.needs_review])
    routed = bool(waiting) and client_routing.any_allocations(db)  # one statement; the per-candidate lookups are skipped when no client has staff
    now = datetime.now(timezone.utc)
    changes, assigned, cleared, per_owner = [], 0, 0, {}
    for candidate in waiting:
        # Client staff routing first (designation match); otherwise the ordinary round-robin.
        owner = (client_routing.route_new_candidate(db, candidate.designations) if routed else None) or next_owner(db, pointer=pointer, force=True)
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
        notify_assignment_changes(db, changes, actor_id=actor_id, actor_name=actor_name, how="from the waiting list")
        db.add(AuditEntry(actor_id=actor_id, action="Assigned waiting candidates", target=f"{assigned} candidates",
                          entity_type="candidate", meta={"count": assigned, "perOwner": per_owner}))
    return {"assigned": assigned, "reviewCleared": cleared, "remaining": len(waiting) - assigned}
