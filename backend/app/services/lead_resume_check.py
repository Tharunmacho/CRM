"""Read a lead's resume before creation, bind the result to identity and exact bytes, and reuse it."""
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

from app.db.models import Attachment, AuditEntry, DocumentExtractionJob
from app.services import lead_sheet
from app.services.document_identity import IDENTITY_REVIEW_MESSAGE, identity_conflicts
from app.services.form_extraction import values_for
from app.services.manual_extraction import READY, owned_job


def identity_key(lead):
    values = {key: str(getattr(lead, key, None) or "") for key in ("id", "name", "phone", "email", "dob")}
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def source_attachment(db, lead):
    return db.scalar(select(Attachment).where(Attachment.entity_type == "lead", Attachment.entity_id == lead.id,
                     Attachment.slot == "resume").order_by(Attachment.version.desc(), Attachment.uploaded_at.desc()).limit(1))


def candidate_identity(lead, overrides=None):
    overrides = overrides or {}
    return {"name": str(overrides.get("name", lead.name) or "").strip(),
            "passport_no": str(overrides.get("passport_no", overrides.get("passportNo", "")) or "").strip()}


def _status(db, lead, job):
    context = (job.draft or {}).get("leadCheck") or {}
    result = {"jobId": job.id, "status": "needs_review", "canConvert": False, "message": None}
    if context.get("leadId") != lead.id or context.get("identityKey") != identity_key(lead):
        return {**result, "message": "The lead details changed. Check the resume again before converting."}
    if context.get("sourceAttachmentId"):
        current = source_attachment(db, lead)
        if current is None or current.id != context["sourceAttachmentId"] or current.content_hash != job.content_hash:
            return {**result, "message": "The saved resume changed. Check it again before converting."}
    if job.candidate_id or job.status in {"attached", "dismissed"}:
        return {**result, "message": "This reading is no longer available for this conversion. Check the resume again."}
    if job.status in READY or job.status == "uploaded":
        return {**result, "status": "processing" if job.status == "processing" else "pending", "message": "Checking that the resume belongs to this lead."}
    if job.status == "failed":
        return {**result, "status": "failed", "message": "The resume could not be checked. Try checking it again or choose a clearer file."}
    output = (job.state or {}).get("output") or {}
    extractions = output.get("extractions") or {}
    expected = SimpleNamespace(**(context.get("candidateIdentity") or candidate_identity(lead)), id=lead.matched_candidate_id)
    named = False
    for kind, extracted in extractions.items():
        values = values_for(kind, extracted.get("raw") or {})
        named = named or bool(str(values.get("name") or "").strip())
        if identity_conflicts(expected, values, db) or identity_conflicts(expected, extracted.get("fields") or {}, db):
            return {**result, "status": "rejected", "message": IDENTITY_REVIEW_MESSAGE}
    if output.get("splitSuggested"):
        return {**result, "status": "rejected", "message": "This file contains more than one person. Upload only this lead's resume."}
    if not named or not extractions.get("resume") or job.status != "completed" or output.get("status") != "complete":
        return {**result, "message": "The resume's identity could not be verified. Upload a clear resume showing this lead's name."}
    return {**result, "status": "verified", "canConvert": True, "message": "Resume checked. It matches this lead."}


def start(db, user, lead_id, resume=None, overrides=None):
    from app.services.lead_upload_delete import resume_check_guard
    with resume_check_guard(db, lead_id):
        return _start(db, user, lead_id, resume, overrides)


def _start(db, user, lead_id, resume=None, overrides=None):
    from app.api.routers import document_jobs
    from app.core.config import get_settings
    from app.services.document_storage import read_bytes
    lead = lead_sheet.load_lead(db, lead_id)
    lead_sheet.ensure_can_work_lead(db, lead, user)
    if lead.candidate_id or lead.status == "converted":
        raise HTTPException(409, "This lead is already a candidate.")
    original = source_attachment(db, lead)
    supplied = resume is not None and bool(resume.filename)
    if not supplied:
        if original is None:
            raise HTTPException(422, "Choose a resume before checking it.")
        resume = UploadFile(file=BytesIO(read_bytes(get_settings(), original.object_key)), filename=original.file_name,
                            headers=Headers({"content-type": original.content_type or "application/pdf"}))
    group = str(uuid5(NAMESPACE_URL, f"crm-lead-resume:{lead.id}:{user.id}"))
    saved = document_jobs.create(kind="resume", group_id=group, file=resume, db=db, user=user)
    job = owned_job(db, saved["id"], user.id, lock=True)
    if job.candidate_id:
        raise HTTPException(409, "This resume is already attached to a candidate.")
    job.draft = {**(job.draft or {}), "leadCheck": {"leadId": lead.id, "identityKey": identity_key(lead),
                 "candidateIdentity": candidate_identity(lead, overrides),
                 "sourceAttachmentId": None if supplied else original.id, "contentHash": job.content_hash}}
    db.commit()
    if job.status in {"uploaded", "failed"}:
        document_jobs.retry(job.id, db=db, user=user)
    return _status(db, lead, job)


def status(db, user, lead_id, job_id):
    lead = lead_sheet.load_lead(db, lead_id)
    lead_sheet.ensure_can_work_lead(db, lead, user)
    job = owned_job(db, job_id, user.id)
    if ((job.draft or {}).get("leadCheck") or {}).get("leadId") != lead.id:
        raise HTTPException(404, "Resume check not found for this lead.")
    return _status(db, lead, job)


def require_verified(db, user, lead, resume, job_id, overrides=None):
    original = source_attachment(db, lead)
    supplied = resume is not None and bool(resume.filename)
    if not supplied and original is None:
        return None
    if not job_id:
        raise lead_sheet.fail(409, "resume_verification_required", "Check the resume before converting this lead. No candidate was created.")
    job = owned_job(db, job_id, user.id, lock=True)
    context = (job.draft or {}).get("leadCheck") or {}
    if context.get("candidateIdentity", candidate_identity(lead)) != candidate_identity(lead, overrides):
        raise lead_sheet.fail(409, "resume_verification_required", "The candidate identity changed. Check the resume again. No candidate was created.")
    outcome = _status(db, lead, job)
    if not outcome["canConvert"]:
        raise lead_sheet.fail(409, "resume_identity_mismatch" if outcome["status"] == "rejected" else "resume_verification_required",
                              outcome["message"] + " No candidate was created.")
    if supplied:
        from app.api.routers.files import MAX_BYTES
        payload = resume.file.read(MAX_BYTES + 1)
        resume.file.seek(0)
        if len(payload) > MAX_BYTES or sha256(payload).hexdigest() != job.content_hash:
            raise lead_sheet.fail(409, "resume_verification_required", "The selected resume changed. Check it again. No candidate was created.")
    elif ((job.draft or {}).get("leadCheck") or {}).get("sourceAttachmentId") != original.id or original.content_hash != job.content_hash:
        raise lead_sheet.fail(409, "resume_verification_required", "The saved resume changed. Check it again. No candidate was created.")
    return job.id


def attach_verified(db, user, candidate, job_id, lead=None):
    from app.services.application_form import apply_job_output
    job = owned_job(db, job_id, user.id, lock=True)
    if job.candidate_id or job.status != "completed":
        raise HTTPException(409, "The resume check changed. Check the resume again. No candidate was created.")
    attachment = Attachment(id=str(uuid4()), entity_type="candidate", entity_id=candidate.id, slot="resume", version=1,
                            file_name=job.filename, size_kb=job.size_kb, content_type=job.content_type,
                            object_key=job.object_key, content_hash=job.content_hash, uploaded_by=user.id)
    db.add(attachment); db.flush()
    job.candidate_id, job.attachment_id = candidate.id, attachment.id
    job.draft = {**(job.draft or {}), "applicationForm": {"candidateId": candidate.id, "slot": "resume", "attachmentId": attachment.id}}
    # Reuses saved output through the normal identity guard and field provenance rules.
    apply_job_output(db, job, (job.state or {}).get("output") or {})
    result = ((job.draft or {}).get("applicationForm") or {}).get("result") or {}
    if result.get("identityMismatch") or result.get("status") != "done":
        raise HTTPException(409, IDENTITY_REVIEW_MESSAGE)
    # Keep the verified original with its source lead as well as the candidate,
    # so a permitted reversal never deletes the only copy of that source file.
    if lead is not None:
        original = source_attachment(db, lead)
        if original is None or original.content_hash != job.content_hash:
            from datetime import datetime, timezone
            source = Attachment(id=str(uuid4()), entity_type="lead", entity_id=lead.id, slot="resume",
                        version=(original.version + 1) if original else 1, file_name=job.filename, size_kb=job.size_kb,
                        content_type=job.content_type, object_key=job.object_key, content_hash=job.content_hash, uploaded_by=user.id)
            db.add(source)
            lead.resume_received_at = datetime.now(timezone.utc)
            lead_sheet.log_activity(db, lead.id, user.id, "resume", "Kept the verified resume used for conversion",
                                    {"attachmentId": source.id, "documentJobId": job.id})
    db.add(AuditEntry(actor_id=user.id, action="Attached verified lead resume", target=attachment.file_name,
                     entity_type="candidate", entity_id=candidate.id, meta={"attachment_id": attachment.id, "document_job_id": job.id}))
    db.flush()
