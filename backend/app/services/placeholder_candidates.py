"""Clean-up of the placeholder candidates older versions wrote for files that were never read.

Such a row has a blank name or exactly "Name needs review", no phone, no email and nothing a person
did on it. The list shows only references and ids (no personal data). Deleting keeps the source
files: the email, WhatsApp message or saved upload goes back to its needs-review state so a person
can enter the details or upload a clearer copy.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select, update

from app.db.models import (
    Application, ApplicationFeedback, Attachment, AuditEntry, Candidate, DailyWorkItem, DocumentExtractionJob,
    DocumentPersonGroup, EmailIngestionJob, Lead, MailMessage, Task, TaskEvent, TaskKind, WhatsAppConversation, WhatsAppMessage,
)
from app.services.candidate_review import NO_NAME_REASON, UNNAMED

# Anything in these tables means somebody (or a later stage) used the candidate.
_ACTIVITY = (
    (Application, "candidate_id"), (ApplicationFeedback, "candidate_id"), (DailyWorkItem, "candidate_id"),
    (TaskEvent, "candidate_id"), (DocumentPersonGroup, "candidate_id"),
    (Lead, "candidate_id"), (Lead, "matched_candidate_id"),
)


def _blank(column):
    return func.coalesce(func.btrim(column), "") == ""


def placeholder_candidates(db):
    """Candidates that qualify for clean-up, oldest first."""
    candidates = db.scalars(select(Candidate).where(
        or_(_blank(Candidate.name), func.lower(func.btrim(Candidate.name)) == UNNAMED.lower()),
        _blank(Candidate.phone), _blank(Candidate.email),
    ).order_by(Candidate.created_at, Candidate.ref)).all()
    found = []
    for candidate in candidates:
        if any(db.scalar(select(func.count()).select_from(model).where(getattr(model, column) == candidate.id)) for model, column in _ACTIVITY):
            continue
        # Only the untouched first-call task the intake itself created is not activity.
        if db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate.id, or_(
                Task.completed_at.is_not(None), Task.cancelled_at.is_not(None), Task.escalated.is_(True),
                Task.kind != TaskKind.initial_call))):
            continue
        # The source file link is not a document of its own; any other stored document is.
        if db.scalar(select(func.count()).select_from(Attachment).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot != "source_bundle")):
            continue
        found.append(candidate)
    return found


def delete_placeholder_candidates(db, candidates, *, actor_id=None):
    """Remove the given rows; the caller commits. Source receipts return to needs-review with a plain reason."""
    for candidate in candidates:
        cid = candidate.id
        db.execute(update(EmailIngestionJob).where(EmailIngestionJob.candidate_id == cid).values(
            candidate_id=None, status="needs_review", last_error=NO_NAME_REASON))
        db.execute(update(MailMessage).where(MailMessage.candidate_id == cid).values(candidate_id=None))
        db.execute(update(WhatsAppMessage).where(WhatsAppMessage.candidate_id == cid).values(
            candidate_id=None, status="needs_review", last_error=NO_NAME_REASON))
        db.execute(update(WhatsAppConversation).where(WhatsAppConversation.candidate_id == cid).values(candidate_id=None))
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == cid)):
            draft = dict(job.draft or {})
            draft.pop("savedCandidateId", None)
            job.draft, job.candidate_id, job.status, job.last_error = draft, None, "needs_review", NO_NAME_REASON
        # Saved uploads that only remember the candidate in their draft.
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.draft["savedCandidateId"].astext == cid)):
            draft = dict(job.draft or {})
            draft.pop("savedCandidateId", None)
            job.draft = draft
        db.execute(Attachment.__table__.delete().where(Attachment.entity_type == "candidate", Attachment.entity_id == cid))
        db.add(AuditEntry(actor_id=actor_id, action="Removed placeholder candidate (name never read)", target=candidate.ref,
                          entity_type="candidate", entity_id=cid, meta={"ref": candidate.ref}))
        db.delete(candidate)
    db.flush()
    return len(candidates)
