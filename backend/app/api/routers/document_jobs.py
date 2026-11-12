"""Owner-scoped manual upload jobs, original downloads and draft recovery."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from uuid import UUID, uuid4
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routers.files import ALLOWED, MAX_BYTES, _content_type, _safe_name
from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import AuditEntry, DocumentExtractionJob, User
from app.db.session import get_db
from app.services.document_extraction import request_extraction_retry
from app.services.document_storage import delete_bytes, file_response, put_bytes
from app.services.extraction_queue import notify_work, queue_positions
from app.services.instance_guard import instance_name
from app.services.manual_extraction import READY, attach_original, job_out, now, owned_job
from app.services.worker_heartbeat import reader_status
from app.services.candidate_intake_binding import ensure_intake_candidate_access, saved_candidate_id
from app.services.document_person_split import ensure_single_person_source
from app.api.routers.candidates import lock_assignment_cursor

router = APIRouter(prefix="/api/ocr/jobs", tags=["document-intake"])


def _group(value):
    try: return str(UUID(value))
    except (ValueError, TypeError): raise HTTPException(422, "Something went wrong with this upload. Refresh the page and try again.")


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
        job = DocumentExtractionJob(id=identifier, owner_id=user.id, group_id=group_id, kind=kind, instance=instance_name(settings),
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
        raise HTTPException(409, "This upload changed while it was being sent. Please try again.")
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
            raise HTTPException(503, "We could not confirm the upload. Your file is safe. Try the same upload again.") from None
        if key:
            try: delete_bytes(settings, key)
            except Exception: pass
        raise HTTPException(503, "We could not save your file. Keep it and try again.") from None


@router.get("")
def list_jobs(response: Response, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    jobs = db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.owner_id == user.id,
        DocumentExtractionJob.status.notin_(["attached", "dismissed"]),
        # Files read for a candidate's application form belong to that candidate, not to an unsaved intake.
        DocumentExtractionJob.candidate_id.is_(None)).order_by(DocumentExtractionJob.updated_at.desc()).limit(limit)).all()
    queue, reader = queue_positions(db, jobs), reader_status(db)
    return [job_out(job, queue=queue, reader=reader) for job in jobs]


@router.get("/groups/{group_id}")
def group(group_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    jobs = db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.group_id == _group(group_id),
        DocumentExtractionJob.owner_id == user.id).order_by(DocumentExtractionJob.created_at, DocumentExtractionJob.id)).all()
    if not jobs: raise HTTPException(404, "Document intake not found")
    queue, reader = queue_positions(db, jobs), reader_status(db)
    return [job_out(job, detail=True, queue=queue, reader=reader) for job in jobs]


FORM_KEYS = {"name", "phone", "whatsapp", "email", "dob", "gender", "nationality", "currentCity", "languages",
             "passportNo", "passportIssue", "passportExpiry", "passportPlace", "designation", "totalExperience",
             "gulfExperience", "skills", "expectedSalary", "source", "sourceDetail",
             "fatherName", "maritalStatus", "currentAddress", "experienceIndia", "experienceAbroad", "education", "workHistory",
             # screening details (application form); education / workHistory travel as JSON-encoded strings
             "nativePlace", "familyMemberNumber", "jobDurationPreference", "countryPreference", "jobPreference",
             "serviceChargesStatus", "bestTimeToContact", "interestLevel", "nextFollowUpDate", "whatsappSameAsPhone"}


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: dict[str, str] = Field(default_factory=dict)
    editedFields: list[str] = Field(default_factory=list, max_length=40)
    jobIds: list[str] = Field(default_factory=list, max_length=60)
    savedCandidateId: str | None = Field(default=None, max_length=36)

    @field_validator("form")
    @classmethod
    def bounded_form(cls, value):
        if set(value) - FORM_KEYS or any(len(item) > 20000 for item in value.values()):
            raise ValueError("The draft has details we do not support, or a value that is too long.")
        return value

    @field_validator("editedFields")
    @classmethod
    def bounded_keys(cls, value):
        if set(value) - FORM_KEYS: raise ValueError("One of the edited details is not supported.")
        return value


# Autosave runs while somebody types, against a hosted database where every statement is a round trip, so a save is
# TWO statements (plus BEGIN / COMMIT):
#   1. the guard: locks the intake's job rows (FOR UPDATE, in id order, like every other writer of them) and answers every
#      validation question at once: how many jobs, do the named ones belong to it, is it a person-by-person review, which
#      saved candidate is it bound to, and is the stored draft already what is being sent;
#   2. one UPDATE of the draft, with the audit line (once per hour per intake) in the same statement.
# The row locks make the assignment-cursor lock unnecessary here: nothing in a draft save decides identity or assignment,
# and a concurrent "save candidate" waits on the same job rows, so it still wins the binding.
_DRAFT_GUARD = text("""
    WITH mine AS (SELECT id, candidate_id, draft, filename FROM document_extraction_jobs
                  WHERE owner_id = CAST(:owner AS varchar) AND group_id = CAST(:grp AS varchar) ORDER BY id FOR UPDATE)
    SELECT (SELECT count(*) FROM mine) AS jobs,
           (SELECT filename FROM mine ORDER BY id LIMIT 1) AS filename,
           (SELECT count(*) FROM mine WHERE id = ANY(CAST(:ids AS text[]))) AS matched,
           EXISTS (SELECT 1 FROM document_person_intakes WHERE source_type = 'manual' AND source_id = CAST(:grp AS varchar)) AS split,
           (SELECT COALESCE(array_agg(DISTINCT b), CAST(ARRAY[] AS text[])) FROM (
                SELECT candidate_id AS b FROM mine UNION ALL SELECT draft ->> 'savedCandidateId' FROM mine) bound WHERE b IS NOT NULL) AS bound,
           (SELECT COALESCE(array_agg(COALESCE(draft ->> 'savedCandidateId', '')), CAST(ARRAY[] AS text[])) FROM mine) AS marks,
           (SELECT COALESCE(bool_and((draft - 'savedCandidateId') = (CAST(:draft AS jsonb) - 'savedCandidateId')), false) FROM mine) AS same_form
""")

_DRAFT_WRITE = text("""
    WITH written AS (
        UPDATE document_extraction_jobs SET draft = CAST(:draft AS jsonb), updated_at = :now
        WHERE owner_id = CAST(:owner AS varchar) AND group_id = CAST(:grp AS varchar)
          AND NOT EXISTS (SELECT 1 FROM document_person_intakes WHERE source_type = 'manual' AND source_id = CAST(:grp AS varchar))
        RETURNING id),
    audited AS (
        INSERT INTO system_audit_log (id, actor_id, action, target, entity_type, entity_id)
        SELECT CAST(:audit_id AS varchar), CAST(:owner AS varchar), CAST(:action AS varchar), CAST(:target AS varchar), 'document_extraction_group', CAST(:grp AS varchar)
        WHERE EXISTS (SELECT 1 FROM written)
          AND NOT EXISTS (SELECT 1 FROM system_audit_log WHERE actor_id = CAST(:owner AS varchar) AND action = CAST(:action AS varchar)
                          AND entity_type = 'document_extraction_group' AND entity_id = CAST(:grp AS varchar) AND at >= :since)
        RETURNING id)
    SELECT (SELECT count(*) FROM written) AS written
""")


@router.put("/groups/{group_id}/draft")
def save_draft(group_id: str, body: DraftIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    group = _group(group_id)
    payload = body.model_dump()
    facts = db.execute(_DRAFT_GUARD, {"owner": user.id, "grp": group, "ids": sorted(set(body.jobIds)),
                                      "draft": json.dumps(payload, separators=(",", ":"))}).one()
    if not facts.jobs: raise HTTPException(404, "Document intake not found")
    # Keep split provenance intact even if an older tab still has autosave queued.
    if facts.split:
        raise HTTPException(409, "This upload is being checked person by person. Save the details in the review of people and pages.")
    if facts.matched != len(set(body.jobIds)): raise HTTPException(422, "The selected documents do not belong to this intake")
    # A queued pre-save draft must never erase the binding committed with the
    # candidate. This is also what makes an interrupted save recoverable.
    bound = set(facts.bound)
    if len(bound) > 1:
        raise HTTPException(409, "This upload points to different candidates. Check the saved files before you continue.")
    existing_id = next(iter(bound), None)
    if existing_id and body.savedCandidateId and body.savedCandidateId != existing_id:
        raise HTTPException(409, "This upload is already linked to a saved candidate.")
    if body.savedCandidateId and not existing_id:
        from app.db.models import Candidate
        candidate = db.get(Candidate, body.savedCandidateId)
        if candidate is None:
            raise HTTPException(404, "Saved candidate not found.")
        ensure_intake_candidate_access(candidate, user)
    if existing_id:
        payload["savedCandidateId"] = existing_id
    # Nothing changed since the last save (a second tab, a retry, a blur right after a debounced save): no write, no
    # audit line, no wake-up for anybody. The guard compared the stored draft with what was sent, jsonb to jsonb.
    if facts.same_form and all(mark == (payload.get("savedCandidateId") or "") for mark in facts.marks):
        db.rollback()  # releases the row locks
        return {"saved": True, "unchanged": True}
    moment = now()
    written = db.execute(_DRAFT_WRITE, {
        "owner": user.id, "grp": group, "draft": json.dumps(payload, separators=(",", ":")), "now": moment,
        "audit_id": str(uuid4()), "action": "Saved candidate details from an uploaded intake", "target": facts.filename,
        "since": moment - timedelta(minutes=60)}).scalar()
    if not written:
        raise HTTPException(409, "This upload is being checked person by person. Save the details in the review of people and pages.")
    db.commit()
    return {"saved": True}


@router.get("/{job_id}")
def detail(job_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    job = owned_job(db, job_id, user.id)
    return job_out(job, detail=True, queue=queue_positions(db, [job]), reader=reader_status(db))


@router.get("/{job_id}/original")
def original(job_id: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = owned_job(db, job_id, user.id)
    try: return file_response(get_settings(), job.object_key, job.filename, job.content_type, download)
    except Exception: raise HTTPException(503, "We cannot open the saved file right now because file storage is not reachable. Try again later.") from None


@router.post("/{job_id}/retry")
def retry(job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    job = owned_job(db, job_id, user.id, lock=True)
    ensure_single_person_source(db, "manual", job.group_id)
    if job.status in READY: return job_out(job, detail=True)
    if job.kind not in {"resume", "passport", "aadhaar"}:
        raise HTTPException(422, "This file is kept as an attachment. Nothing needs to be read from it.")
    if job.status == "attached": raise HTTPException(409, "These documents are already on the saved candidate")
    saved = deepcopy(job.state or {})
    request_extraction_retry(saved)
    saved.pop("output", None)
    job.state, job.result = saved, {}
    job.status, job.attempts, job.next_attempt_at = "pending", 0, now()
    job.queued_at, job.started_at, job.finished_at, job.claimed_by, job.lease_until = job.next_attempt_at, None, None, None, None
    job.last_error = None
    job.updated_at = now()
    db.add(AuditEntry(actor_id=user.id, action="Asked for an uploaded document to be read again", target=job.filename,
        entity_type="document_extraction_job", entity_id=job.id))
    notify_work(db, "manual")  # sleeping workers start reading at once (delivered when this commits)
    db.commit()
    return job_out(job, detail=True, queue=queue_positions(db, [job]), reader=reader_status(db))


@router.post("/{job_id}/dismiss")
def dismiss(job_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    lock_assignment_cursor(db)
    job = owned_job(db, job_id, user.id, lock=True)
    from app.db.models import MultiPersonIntake
    if db.scalar(select(MultiPersonIntake.id).where(MultiPersonIntake.source_type == "manual", MultiPersonIntake.source_id == job.group_id)):
        raise HTTPException(409, "This file is part of a review of people and pages. Leave pages out from inside that review and give a reason.")
    if job.status != "attached":
        was_status = job.status
        job.status, job.updated_at = "dismissed", now()
        if was_status != "dismissed":
            db.add(AuditEntry(actor_id=user.id, action="Removed an uploaded intake file", target=job.filename,
                entity_type="document_extraction_job", entity_id=job.id, meta={"kind": job.kind, "previous_status": was_status}))
        db.commit()
    return {"status": job.status}


class AttachIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidateId: str = Field(min_length=1, max_length=36)
    originalOnly: StrictBool = False


@router.post("/{job_id}/attach")
def attach(job_id: str, body: AttachIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try: return attach_original(db, user, job_id, body.candidateId, get_settings(), original_only=body.originalOnly)
    except HTTPException: raise
    except Exception:
        db.rollback()
        raise HTTPException(503, "We could not attach the saved files. They are safe. Try again for this candidate.") from None
