"""Owner-scoped manual upload jobs, original downloads and draft recovery."""
from copy import deepcopy
import hashlib
from uuid import UUID, uuid4
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routers.files import ALLOWED, MAX_BYTES, _content_type, _safe_name
from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import AuditEntry, DocumentExtractionJob, User
from app.db.session import get_db
from app.services.document_extraction import request_extraction_retry
from app.services.document_storage import delete_bytes, file_response, put_bytes
from app.services.manual_extraction import READY, attach_original, job_out, now, owned_job
from app.services.candidate_intake_binding import ensure_intake_candidate_access, saved_candidate_id
from app.services.document_person_split import ensure_single_person_source
from app.api.routers.candidates import lock_assignment_cursor

router = APIRouter(prefix="/api/ocr/jobs", tags=["document-intake"])


def _group(value):
    try: return str(UUID(value))
    except (ValueError, TypeError): raise HTTPException(422, "A valid intake group ID is required")


@router.post("", status_code=202)
def create(kind: Literal["resume", "passport", "aadhaar", "photo", "education", "experience"] = Form(...), group_id: str = Form(...),
           file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    group_id = _group(group_id)
    payload = file.file.read(MAX_BYTES + 1)
    if not payload: raise HTTPException(400, "The document is empty")
    if len(payload) > MAX_BYTES: raise HTTPException(413, "Documents must be under 25 MB")
    filename = _safe_name(file.filename or "document")
    media = _content_type(file.content_type, filename)
    if media not in ALLOWED: raise HTTPException(415, "Upload a PDF, Word document or image")
    digest = hashlib.sha256(payload).hexdigest()
    statement = select(DocumentExtractionJob).where(DocumentExtractionJob.owner_id == user.id,
        DocumentExtractionJob.group_id == group_id, DocumentExtractionJob.kind == kind, DocumentExtractionJob.content_hash == digest)
    existing = db.scalar(statement)
    if existing is not None: return job_out(existing, detail=True)
    identifier = str(uuid4())
    key = None
    commit_attempted = False
    settings = get_settings()
    try:
        lock_assignment_cursor(db)
        # A replacement upload must be possible before dismissing an incorrect
        # original. Only an already frozen person review prevents new sources.
        ensure_single_person_source(db, "manual", group_id, check_evidence=False)
        key = put_bytes(settings, "manual/" + user.id + "/" + identifier + "/original", payload, media)
        recent = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.owner_id == user.id,
            DocumentExtractionJob.group_id == group_id).order_by(DocumentExtractionJob.updated_at.desc()).limit(1))
        job = DocumentExtractionJob(id=identifier, owner_id=user.id, group_id=group_id, kind=kind,
            filename=filename, content_type=media, object_key=key, content_hash=digest,
            size_kb=max(1, (len(payload) + 1023) // 1024), status="uploaded", draft=deepcopy(recent.draft or {}) if recent else {})
        db.add(job)
        db.add(AuditEntry(actor_id=user.id, action="Saved document intake", target=filename,
            entity_type="document_extraction_job", entity_id=identifier))
        db.flush()
        commit_attempted = True
        db.commit()
        return job_out(job, detail=True)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing = db.scalar(statement)
        if key and (existing is None or existing.object_key != key):
            try: delete_bytes(settings, key)
            except Exception: pass
        if existing is not None: return job_out(existing, detail=True)
        raise HTTPException(409, "The intake changed while uploading. Please retry.")
    except Exception:
        try: db.rollback()
        except SQLAlchemyError: pass
        if commit_attempted:
            # Losing the commit response does not mean the insert failed. Keep
            # its original and recover the unique intake if the DB is reachable.
            try:
                existing = db.scalar(statement)
                if existing is not None: return job_out(existing, detail=True)
            except Exception: pass
            raise HTTPException(503, "The upload could not be confirmed. Its original is retained; retry the same intake.") from None
        if key:
            try: delete_bytes(settings, key)
            except Exception: pass
        raise HTTPException(503, "The original could not be saved. Keep this file and retry.") from None


@router.get("")
def list_jobs(response: Response, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    jobs = db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.owner_id == user.id,
        DocumentExtractionJob.status.notin_(["attached", "dismissed"])).order_by(DocumentExtractionJob.updated_at.desc()).limit(limit)).all()
    return [job_out(job) for job in jobs]


@router.get("/groups/{group_id}")
def group(group_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    jobs = db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.group_id == _group(group_id),
        DocumentExtractionJob.owner_id == user.id).order_by(DocumentExtractionJob.created_at, DocumentExtractionJob.id)).all()
    if not jobs: raise HTTPException(404, "Document intake not found")
    return [job_out(job, detail=True) for job in jobs]


FORM_KEYS = {"name", "phone", "whatsapp", "email", "dob", "gender", "nationality", "currentCity", "languages",
             "passportNo", "passportIssue", "passportExpiry", "passportPlace", "designation", "totalExperience",
             "gulfExperience", "skills", "expectedSalary", "source"}


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: dict[str, str] = Field(default_factory=dict)
    editedFields: list[str] = Field(default_factory=list, max_length=40)
    jobIds: list[str] = Field(default_factory=list, max_length=20)
    savedCandidateId: str | None = Field(default=None, max_length=36)

    @field_validator("form")
    @classmethod
    def bounded_form(cls, value):
        if set(value) - FORM_KEYS or any(len(item) > 20000 for item in value.values()):
            raise ValueError("The draft contains unsupported fields or a value that is too long")
        return value

    @field_validator("editedFields")
    @classmethod
    def bounded_keys(cls, value):
        if set(value) - FORM_KEYS: raise ValueError("Unsupported edited field")
        return value


@router.put("/groups/{group_id}/draft")
def save_draft(group_id: str, body: DraftIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    jobs = db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.owner_id == user.id,
        DocumentExtractionJob.group_id == _group(group_id)).order_by(DocumentExtractionJob.id).with_for_update()).all()
    if not jobs: raise HTTPException(404, "Document intake not found")
    # Keep split provenance intact even if an older tab still has autosave queued.
    from app.db.models import MultiPersonIntake
    if db.scalar(select(MultiPersonIntake.id).where(MultiPersonIntake.source_type == "manual", MultiPersonIntake.source_id == _group(group_id))):
        raise HTTPException(409, "This intake is being reviewed by person. Save details in its people-and-pages review.")
    if set(body.jobIds) - {job.id for job in jobs}: raise HTTPException(422, "The selected documents do not belong to this intake")
    # A queued pre-save draft must never erase the binding committed with the
    # candidate. This is also what makes an interrupted save recoverable.
    existing_id = saved_candidate_id(jobs)
    if existing_id and body.savedCandidateId and body.savedCandidateId != existing_id:
        raise HTTPException(409, "This intake is already linked to a saved candidate.")
    if body.savedCandidateId and not existing_id:
        from app.db.models import Candidate
        candidate = db.get(Candidate, body.savedCandidateId)
        if candidate is None:
            raise HTTPException(404, "Saved candidate not found.")
        ensure_intake_candidate_access(candidate, user)
    payload = body.model_dump()
    if existing_id:
        payload["savedCandidateId"] = existing_id
    for job in jobs:
        job.draft = deepcopy(payload)
        job.updated_at = now()
    db.commit()
    return {"saved": True}


@router.get("/{job_id}")
def detail(job_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return job_out(owned_job(db, job_id, user.id), detail=True)


@router.get("/{job_id}/original")
def original(job_id: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = owned_job(db, job_id, user.id)
    try: return file_response(get_settings(), job.object_key, job.filename, job.content_type, download)
    except Exception: raise HTTPException(503, "The saved original is unavailable. Restore its storage before retrying.") from None


@router.post("/{job_id}/retry")
def retry(job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    job = owned_job(db, job_id, user.id, lock=True)
    ensure_single_person_source(db, "manual", job.group_id)
    if job.status in READY: return job_out(job, detail=True)
    if job.kind not in {"resume", "passport", "aadhaar"}:
        raise HTTPException(422, "This document is stored as an attachment and does not need identity extraction")
    if job.status == "attached": raise HTTPException(409, "These documents are already on the saved candidate")
    saved = deepcopy(job.state or {})
    request_extraction_retry(saved)
    saved.pop("output", None)
    job.state, job.result = saved, {}
    job.status, job.attempts, job.next_attempt_at = "pending", 0, now()
    job.last_error = None
    job.updated_at = now()
    db.commit()
    return job_out(job, detail=True)


@router.post("/{job_id}/dismiss")
def dismiss(job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    job = owned_job(db, job_id, user.id, lock=True)
    from app.db.models import MultiPersonIntake
    if db.scalar(select(MultiPersonIntake.id).where(MultiPersonIntake.source_type == "manual", MultiPersonIntake.source_id == job.group_id)):
        raise HTTPException(409, "This source belongs to a saved people-and-pages review. Exclude pages with a reason inside that review.")
    if job.status != "attached":
        job.status, job.updated_at = "dismissed", now()
        db.commit()
    return {"status": job.status}


class AttachIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidateId: str = Field(min_length=1, max_length=36)


@router.post("/{job_id}/attach")
def attach(job_id: str, body: AttachIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try: return attach_original(db, user, job_id, body.candidateId, get_settings())
    except HTTPException: raise
    except Exception:
        db.rollback()
        raise HTTPException(503, "The saved documents could not be attached. The original is retained; retry for this candidate.") from None
