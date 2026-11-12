"""Reverse only a new, unused candidate created by this lead; keep the lead's evidence."""
from datetime import datetime, timezone
import uuid

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import Application, ApplicationFeedback, Attachment, AuditEntry, Candidate, CandidateInvoice, DailyWorkItem, DocumentPersonGroup, EmailIngestionJob, Lead, LeadActivity, MailMessage, Remark, Role, Task, TaskEvent, TaskKind, User, WhatsAppConversation, WhatsAppMessage
from app.services import lead_followups, lead_matching, lead_sheet

ACTION = "Moved candidate back to lead"


def _admin(user):
    if user.role != Role.admin:
        raise HTTPException(403, "Only an admin can move a candidate back to the Lead sheet.")


def _check(db: Session, lead: Lead, candidate: Candidate | None):
    result = {"allowed": False, "reason": None, "candidate": {"id": candidate.id, "name": candidate.name, "ref": candidate.ref} if candidate else None,
              "restoredStatus": None}
    def refuse(message):
        result["reason"] = message
        return result
    if lead.status != "converted" or candidate is None or lead.candidate_id != candidate.id:
        return refuse("This lead is not linked to a converted candidate.")
    activity = db.scalar(select(LeadActivity).where(LeadActivity.lead_id == lead.id, LeadActivity.kind == "convert")
                         .order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc()).limit(1))
    meta = activity.meta if activity else {}
    origin = db.scalar(select(AuditEntry.id).where(AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate.id,
                       AuditEntry.action == "Converted lead to candidate", AuditEntry.meta["leadId"].astext == lead.id).limit(1))
    if not origin or not meta or meta.get("linked") or meta.get("candidateId") != candidate.id:
        return refuse("This lead was linked to an existing candidate. That candidate cannot be removed through Move back to Lead.")
    previous = meta.get("from")
    if previous not in lead_sheet.STATUS_LABELS or previous == "converted":
        return refuse("The original lead status could not be verified. Ask an administrator to review the conversion history.")
    result["restoredStatus"] = previous
    if db.scalar(select(exists().where(Lead.candidate_id == candidate.id, Lead.id != lead.id))):
        return refuse("Other leads are linked to this candidate. Keep the shared candidate on record.")
    for model in (MailMessage, WhatsAppMessage, WhatsAppConversation, EmailIngestionJob, DocumentPersonGroup):
        if db.scalar(select(exists().where(model.candidate_id == candidate.id))):
            return refuse("Another inbox or document source is linked to this candidate. Keep the shared candidate on record.")
    if db.scalar(select(exists().where(Application.candidate_id == candidate.id))):
        return refuse("This candidate has recruitment history. Candidates with job applications cannot be moved back to Lead.")
    if db.scalar(select(exists().where(CandidateInvoice.candidate_id == candidate.id))):
        return refuse("This candidate has invoice or payment records. Keep the candidate and financial history on record.")
    if db.scalar(select(exists().where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                                       Attachment.slot.in_(("invoice", "air_ticket"))))):
        return refuse("This candidate has invoice or air-ticket documents. Keep these records with the candidate.")
    task_rows = list(db.scalars(select(Task).where(Task.candidate_id == candidate.id)))
    tasks = select(Task.id).where(Task.candidate_id == candidate.id)
    # Creating the candidate can make its first untouched call task automatically.
    # All other work, including cancellation/escalation history, must stay on record.
    if (any(task.kind != TaskKind.initial_call or task.application_id or task.stage or task.completed_at or task.cancelled_at
            or task.escalated or (task.id != meta.get("initialTaskId") and task.created_at > activity.created_at) for task in task_rows)
            or db.scalar(select(exists().where(Remark.task_id.in_(tasks))))
            or db.scalar(select(exists().where(TaskEvent.task_id.in_(tasks))))
            or db.scalar(select(exists().where(DailyWorkItem.candidate_id == candidate.id,
                (DailyWorkItem.started_at.is_not(None)) | (DailyWorkItem.completed_at.is_not(None)))))
            or db.scalar(select(exists().where(ApplicationFeedback.candidate_id == candidate.id)))):
        return refuse("This candidate has completed work or recruiter notes. Keep that history on the candidate.")
    result["allowed"] = True
    return result


def check(db: Session, user: User, lead_id: str):
    _admin(user)
    lead = lead_sheet.load_lead(db, lead_id)
    return _check(db, lead, db.get(Candidate, lead.candidate_id) if lead.candidate_id else None)


def reverse(db: Session, user: User, lead_id: str, candidate_id: str, reason: str, background: BackgroundTasks):
    _admin(user)
    reason = " ".join(reason.split())
    if len(reason) < 3 or len(reason) > 1000:
        raise HTTPException(422, "Enter a reason between 3 and 1000 characters.")
    from app.api.routers.candidates import _delete_candidate_rows, _remove_stored_files, lock_assignment_cursor
    # Same order as mapping, invoices and candidate deletion: cursor, then candidate.
    lock_assignment_cursor(db)
    lead = lead_sheet.load_lead(db, lead_id, lock=True)
    if lead.candidate_id is None:
        prior = db.scalar(select(AuditEntry).where(AuditEntry.entity_type == "lead", AuditEntry.entity_id == lead_id,
                          AuditEntry.action == ACTION, AuditEntry.meta["removedCandidateId"].astext == candidate_id).limit(1))
        if prior:
            return {"lead": lead_sheet.lead_response(db, lead_id), "removedCandidateId": candidate_id,
                    "restoredStatus": lead.status, "alreadyReversed": True}
    if lead.candidate_id != candidate_id:
        raise HTTPException(409, "The linked candidate changed. Reload the lead before trying again.")
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update().execution_options(populate_existing=True))
    outcome = _check(db, lead, candidate)
    if not outcome["allowed"]:
        raise HTTPException(409, outcome["reason"])
    ref, name = candidate.ref, candidate.name
    lead.status, lead.candidate_id = outcome["restoredStatus"], None
    lead.updated_at = datetime.now(timezone.utc)
    lead_matching.clear_self_match(lead)
    db.flush()
    audit_id = str(uuid.uuid4())
    documents, _tasks, keys = _delete_candidate_rows(db, candidate_id, audit_id=audit_id, actor_id=user.id,
                                                   target=f"{name} ({ref})", ref=ref, applications=0)
    details = {"action": "unconvert", "removedCandidateId": candidate_id, "candidateRef": ref, "from": "converted",
               "to": lead.status, "reason": reason, "removedCandidateDocuments": documents}
    lead_sheet.log_activity(db, lead_id, user.id, "note", f"Moved {ref} back to Lead. {reason}", details)
    db.add(AuditEntry(actor_id=user.id, action=ACTION, target=ref, entity_type="lead", entity_id=lead_id, meta=details))
    lead_followups.sync(db, [lead_id])
    db.commit()
    if keys:
        background.add_task(_remove_stored_files, db.get_bind(), audit_id, keys)
    return {"lead": lead_sheet.lead_response(db, lead_id), "removedCandidateId": candidate_id,
            "restoredStatus": lead.status, "alreadyReversed": False}
