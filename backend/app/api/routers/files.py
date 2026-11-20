"""
File upload and download.

Files are written to disk under STORAGE_DIR and recorded in `attachments`. The upload
happens as soon as a file is chosen rather than when the surrounding form is submitted:
a half-finished form that loses its connection should not take the passport scan with
it, and a stage can be saved repeatedly without re-uploading.

Replacing a file writes the next version. Nothing is overwritten and nothing is deleted
from disk, so an earlier scan can always be produced if a client disputes a document.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.db.session import get_db
from app.core.deps import current_user, ensure_can_work, ensure_can_work_candidate
from app.core.roles import is_admin_level
from app.db.models import Application, Attachment, AuditEntry, Candidate, Client, Company, Lead, User
from app.services import document_storage, file_ownership
from app.services.invoice_access import can_see_invoices, ensure_can_see_invoices
from app.services.parallel_reads import parallel_reads

router = APIRouter(prefix="/api/files", tags=["files"])
logger = logging.getLogger(__name__)

settings = get_settings()
STORAGE = Path(settings.storage_dir)

MAX_BYTES = 25 * 1024 * 1024  # a passport scan or a signed PDF, not a video

ALLOWED = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ENTITIES = {"client": Client, "company": Company, "candidate": Candidate, "application": Application, "lead": Lead}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# A workflow-stage upload is evidence for the candidate too.  Keep a candidate
# document record that points to the *same immutable bytes*, so a recruiter can
# always find the file in the candidate's Documents section without uploading
# it again.  Stage 8 already uses the candidate passport directly and stage 9
# has no corresponding candidate-document slot.
_STAGE_FILE_DOCUMENT_SLOTS = {
    "stage_5_file": "signed_offer",
    "stage_7_file": "pcc",
    "stage_10_file": "visa",
    "stage_11_file": "visa_stamping",
    "stage_12_file": "air_ticket",
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Keep something recognisable for the download, drop anything path-like."""
    cleaned = _SAFE.sub("-", name.replace("\\", "/").rsplit("/", 1)[-1]).strip("-.")
    return cleaned[:150] or "file"


def _entity_model(entity_type: str, entity_id: str):
    model = ENTITIES.get(entity_type)
    if model is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Files cannot be added to this kind of record ({entity_type}).")
    if not entity_id or not entity_id.strip() or len(entity_id) > 36:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "We could not tell which record this file belongs to.")
    return model


def _entity(db: Session, entity_type: str, entity_id: str, *, lock: bool = False):
    model = _entity_model(entity_type, entity_id)
    statement = select(model).where(model.id == entity_id)
    if lock:
        # Lock the parent even when the slot has no versions yet. Locking only a
        # previous attachment leaves simultaneous first uploads unprotected.
        statement = statement.with_for_update()
    record = db.scalar(statement)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The record for this file was not found.")
    return record


def _content_type(content_type: str | None, filename: str) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not media_type or media_type == "application/octet-stream":
        media_type = CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")
    return media_type


def _discard_uncommitted_file(target: Path) -> None:
    try:
        target.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove an uncommitted document from storage.")


def _uploaded_file(row: Attachment) -> dict:
    return {"id": row.id, "name": row.file_name, "sizeKb": row.size_kb, "version": row.version,
            "key": row.object_key, "url": f"/api/files/{row.id}", "uploadedAt": row.uploaded_at.isoformat()}


def _already_uploaded(db: Session, row: Attachment, slot: str, deferred: bool, response: Response | None) -> dict:
    """The very same bytes are already filed in this field (any version): nothing is stored. HTTP 200 with `alreadyUploaded`, so the
    screen shows a quiet message instead of a new version. A reading that had failed for that file is put back in the queue."""
    from app.services.application_form import READ_SLOTS, requeue_failed
    if not deferred and slot in READ_SLOTS:
        requeue_failed(db, row.entity_id, row.id)
    db.commit()
    if response is not None:
        response.status_code = status.HTTP_200_OK
    existing = _uploaded_file(row)
    return {**existing, "alreadyUploaded": True, "existing": existing, "message": f"This file is already uploaded (version {row.version})."}


def _invoice_tickets_of(db: Session, pieces: list, user: User) -> None:
    """After a scan was filed and committed: a part that landed in the air ticket field starts the invoice request like a single
    ticket upload does. A failing invoice request cannot undo the filing, and never fails the upload."""
    for piece in pieces:
        if piece.slot == "air_ticket" and not piece.duplicate:
            try:
                from app.services.candidate_invoices import on_ticket_uploaded
                on_ticket_uploaded(db, piece.attachment, user)
            except Exception:
                try:
                    db.rollback()
                except SQLAlchemyError:
                    pass
                logger.exception("Ticket saved; its invoice can be retried from the candidate form")


def _mirror_stage_document_on_candidate(db: Session, application: Application, row: Attachment, user: User) -> Attachment | None:
    """Record a stage upload in the matching candidate document slot without copying its bytes.

    The application attachment remains the immutable stage evidence.  This
    second row gives the same file a stable, visible home under the candidate.
    A matching candidate file is reused so retries never create duplicates.
    """
    candidate_slot = _STAGE_FILE_DOCUMENT_SLOTS.get(row.slot)
    if candidate_slot is None:
        return None
    candidate = _entity(db, "candidate", application.candidate_id, lock=True)
    matching = db.scalar(select(Attachment).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
        Attachment.slot == candidate_slot, Attachment.content_hash == row.content_hash, Attachment.deleted_at.is_(None),
    ).order_by(Attachment.version.desc()).limit(1))
    if matching is not None:
        return matching
    previous = db.scalar(select(Attachment).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
        Attachment.slot == candidate_slot,
    ).order_by(Attachment.version.desc()).limit(1))
    mirrored = Attachment(
        id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=candidate_slot,
        version=(previous.version if previous else 0) + 1,
        file_name=row.file_name, size_kb=row.size_kb, content_type=row.content_type,
        object_key=row.object_key, content_hash=row.content_hash,
        uploaded_by=user.id, uploaded_at=row.uploaded_at,
    )
    db.add(mirrored)
    db.add(AuditEntry(
        actor_id=user.id, action="Linked stage document to candidate", target=row.file_name,
        entity_type="candidate", entity_id=candidate.id,
        meta={"attachment_id": mirrored.id, "slot": candidate_slot, "version": mirrored.version,
              "stage_attachment_id": row.id},
        at=row.uploaded_at,
    ))
    return mirrored


@router.post("", status_code=status.HTTP_201_CREATED)
def upload(
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    slot: str = Form(...),
    file: UploadFile = File(...),
    defer_extraction: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    response: Response = None,  # type: ignore[assignment]  # FastAPI fills it in; direct callers may leave it out
) -> dict:
    if entity_type not in ENTITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Files cannot be added to this kind of record ({entity_type}).")
    if not entity_id or not entity_id.strip() or len(entity_id) > 36:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "We could not tell which record this file belongs to.")
    deferred = defer_extraction is True  # direct internal callers retain the automatic default
    slot = slot.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", slot):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose which document this file is for.")
    if slot == "invoice":
        ensure_can_see_invoices(user)  # invoice files are the admin's; the automatic invoice never comes through this route

    # FastAPI runs this synchronous handler in its worker pool, including the
    # database and filesystem work below. Read one extra byte to detect oversized
    # files without loading their entire contents into application memory.
    payload = file.file.read(MAX_BYTES + 1)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Files must be under {MAX_BYTES // (1024 * 1024)} MB.",
        )
    content_type = _content_type(file.content_type, file.filename or "file")
    if content_type not in ALLOWED:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Upload a PDF, Word document, or an image.",
        )
    if entity_type == "candidate" and slot == "photo" and not content_type.startswith("image/"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The photo must be an image (JPG, PNG, WebP or HEIC).")

    # A scan that holds several documents is cut by what is on each page and every part is filed in its own field. The analysis is
    # slow (local reading, bounded), so it runs before any lock is taken, and only for someone who may work on this candidate.
    split_plan = None
    if entity_type == "candidate":
        from app.services import document_splitting
        if document_splitting.worth_analysing(slot, file.filename or "file", content_type, payload):
            early = _entity(db, entity_type, entity_id)
            ensure_can_work_candidate(early, user, db)
            # The same bytes again, or a personal file that is another candidate's, is answered BEFORE the slow reading (the check is
            # repeated under the lock below, which is what makes it race-safe).
            seen = file_ownership.check_file(db, user, early, slot, hashlib.sha256(payload).hexdigest(), file.filename or "file", lock=False)
            if seen.same_slot is not None:
                return _already_uploaded(db, seen.same_slot, slot, deferred, response)
            # The reading takes up to ~2 minutes of OCR: give the pooled connection (and the transaction the check above
            # opened) back first. The session reconnects by itself for the locked work below, which checks permission again.
            db.close()
            split_plan = document_splitting.plan_split(slot, file.filename or "file", payload, content_type)
    written: list[str] = []
    stored_pieces: list = []

    # Sharded by the first two characters so one directory never holds every file.
    key = f"{uuid.uuid4().hex}-{_safe_name(file.filename or 'file')}"
    target = STORAGE / key[:2] / key
    object_key = document_storage.storage_key(settings, f"{key[:2]}/{key}")
    storage_attempted = False
    try:
        record = _entity(db, entity_type, entity_id, lock=True)
        if entity_type == "application":
            ensure_can_work(db, record, user)
        if entity_type == "candidate":
            # Only the recruiter the candidate is assigned to (or an MD / admin) adds or replaces its documents.
            # Having entered the candidate no longer counts: an admin may have given it to someone else since.
            ensure_can_work_candidate(record, user, db)
        if entity_type == "lead":
            # A lead's resume: only the lead's assigned recruiter (staff or MD) or an admin.
            from app.services import lead_sheet
            if slot != "resume":
                raise HTTPException(422, "Only a resume can be added to a lead.")
            lead_sheet.ensure_can_work_lead(db, record, user)
        if entity_type == "candidate" and slot == "source_bundle":
            raise HTTPException(422, "The original upload is kept as it was received and cannot be replaced by hand")
        digest = hashlib.sha256(payload).hexdigest()
        notes: list[str] = []
        if entity_type == "candidate":
            # Never ingest a file twice (services/file_ownership.py): the same bytes already in this field (any version) are not stored
            # again, and a personal document that is another candidate's is refused with 409, before anything is written.
            seen = file_ownership.check_file(db, user, record, slot, digest, file.filename or "file")
            if seen.same_slot is not None:
                return _already_uploaded(db, seen.same_slot, slot, deferred, response)
            notes = seen.notes
            if split_plan is not None:
                file_ownership.check_pieces(db, user, record, [(hashlib.sha256(piece.data).hexdigest(), target_slot)
                                                               for piece in split_plan.pieces for target_slot in piece.slots], file.filename or "file")
        if split_plan is not None:
            stored_pieces = document_splitting.store_pieces(db, user, record, split_plan, file.filename or "file", settings=settings,
                                                            written=written, deferred=deferred)
            # The whole scan is kept as received (the pieces are cuts of it), so nothing is lost and it can be cut again.
            document_splitting.store_source_bundle(db, user, record, payload, content_type, file.filename or "file",
                                                   settings=settings, written=written)
            if not deferred:
                from app.services.application_form import queue_reading, requeue_failed
                queued: set[str] = set()
                for piece in stored_pieces:
                    if piece.slot not in document_splitting.READ_SLOTS:
                        continue
                    if piece.duplicate:
                        requeue_failed(db, entity_id, piece.attachment.id)
                    elif piece.attachment.object_key not in queued:   # one reading per stored file, however many fields it fills
                        queued.add(piece.attachment.object_key)
                        queue_reading(db, user, record, piece.attachment, "application/pdf")
            row = next((piece.attachment for piece in stored_pieces if piece.slot == slot), stored_pieces[0].attachment)
            answer = {**_uploaded_file(row), "split": document_splitting.report(stored_pieces), **({"notes": notes} if notes else {})}
            db.flush()
            db.commit()
            _invoice_tickets_of(db, stored_pieces, user)
            return answer
        previous = db.scalar(
            select(Attachment)
            .where(
                Attachment.entity_type == entity_type,
                Attachment.entity_id == entity_id,
                Attachment.slot == slot,
            )
            .order_by(Attachment.version.desc())
            .limit(1)
        )
        from app.services.bulk_candidate_import import SENT_TO_READER
        row = Attachment(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=entity_id,
            slot=slot,
            version=(previous.version if previous else 0) + 1,
            file_name=_safe_name(file.filename or "file"),
            size_kb=max(1, round(len(payload) / 1024)),
            content_type=content_type,
            object_key=object_key,
            content_hash=digest,
            uploaded_by=user.id,
            uploaded_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.add(AuditEntry(
            actor_id=user.id,
            action="Uploaded document",
            target=row.file_name,
            entity_type=entity_type,
            entity_id=entity_id,
            meta={"attachment_id": row.id, "slot": slot, "version": row.version,
                  **({"extraction_deferred": True} if deferred and entity_type == "candidate" and slot in SENT_TO_READER else {})},
            at=row.uploaded_at,
        ))
        if entity_type == "application":
            _mirror_stage_document_on_candidate(db, record, row, user)
        if entity_type == "lead":
            lead_sheet.note_resume_uploaded(db, record, user, row)
        if entity_type == "candidate" and slot in SENT_TO_READER and not deferred:
            # A resume or passport stored for a candidate is read, and what it says fills the application form
            # (empty fields only; see services/form_extraction.py). The extraction worker picks the job up.
            from app.services.application_form import queue_reading
            queue_reading(db, user, record, row, content_type)
        # Detect metadata/constraint failures before writing bytes. The parent
        # remains locked through the commit, including the first upload of a slot.
        db.flush()
        if object_key.startswith("s3://"):
            storage_attempted = True
            document_storage.put_bytes(settings, object_key, payload, content_type)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.warning("Could not confirm the document transaction rollback.")
        if isinstance(exc, DBAPIError) and exc.connection_invalidated:
            # A connection lost during COMMIT may have committed successfully.
            # Keep these immutable bytes for recovery instead of risking a row
            # that points at a document we have just deleted.
            logger.warning("Document transaction outcome is unknown; stored bytes retained.")
        else:
            if written:
                from app.services.bulk_candidate_import import discard_written
                discard_written(settings, written)
            if object_key.startswith("s3://") and storage_attempted:
                try: document_storage.delete_bytes(settings, object_key)
                except Exception: logger.warning("An uncommitted remote document requires storage reconciliation.")
            elif not object_key.startswith("s3://"):
                _discard_uncommitted_file(target)
        if isinstance(exc, IntegrityError):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "The document changed during upload. Please retry."
            ) from exc
        if isinstance(exc, (OSError, SQLAlchemyError, BotoCoreError, ClientError)):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The document could not be saved. Please retry the upload.",
            ) from exc
        raise

    if (entity_type == "candidate" and slot == "air_ticket") or (entity_type == "application" and slot == "stage_12_file"):
        # The ticket is already committed. Invoice generation has its own durable pending request,
        # so a PDF/storage problem cannot erase a successful ticket upload or advance a stage.
        try:
            from app.services.candidate_invoices import on_ticket_uploaded
            on_ticket_uploaded(db, row, user)
        except Exception:
            db.rollback()
            logger.exception("Ticket saved; its invoice can be retried from the candidate form")

    return {**_uploaded_file(row), **({"notes": notes} if notes else {})}


def _own_stage_files_only(db: Session, entity_type: str, entity_id: str, user: User) -> None:
    """PRD: staff work the stage modal on their own candidates only, so its files follow the same rule."""
    if entity_type == "application":
        application = db.get(Application, entity_id)
        if application is not None:
            ensure_can_work(db, application, user)
    elif entity_type == "lead":
        # A lead's files follow the lead: staff only see the leads assigned to them.
        from app.services import lead_sheet
        lead = db.get(Lead, entity_id)
        if lead is not None:
            lead_sheet.ensure_can_see_lead(lead, user)


@router.get("/{attachment_id}")
def download(
    attachment_id: str,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FileResponse:
    row = db.get(Attachment, attachment_id)
    if row is None or row.deleted_at is not None:   # a removed file is gone for everyone (its bytes are kept in storage)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if row.slot == "invoice":
        ensure_can_see_invoices(user)
    _own_stage_files_only(db, row.entity_type, row.entity_id, user)

    if row.object_key.startswith("s3://"):
        try:
            return document_storage.file_response(settings, row.object_key, row.file_name, _content_type(row.content_type, row.file_name), download)
        except (BotoCoreError, ClientError):
            raise HTTPException(503, "We cannot reach file storage right now. Please try again in a few minutes.") from None

    path = (STORAGE / row.object_key).resolve()
    if not path.is_relative_to(STORAGE.resolve()) or not path.is_file():
        # The row survives but the bytes do not — almost always a container redeployed
        # without a persistent volume mounted at STORAGE_DIR.
        raise HTTPException(
            status.HTTP_410_GONE,
            "The stored file is missing. Check that the uploads volume is mounted.",
        )

    # Inline, so a PDF or a photo opens in the browser tab instead of downloading; the
    # filename still applies when it is saved. nosniff holds the browser to the type the
    # file was stored as, rather than letting it guess from the bytes.
    return FileResponse(
        path,
        media_type=_content_type(row.content_type, row.file_name),
        filename=row.file_name,
        content_disposition_type="attachment" if download else "inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get("")
def list_for(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[dict]:
    model = _entity_model(entity_type, entity_id)
    # Does the record exist, and what are its files: two independent reads, one round trip (the record itself is never needed, so its
    # resume text and other wide columns are not read either).
    found, rows = parallel_reads(
        db,
        lambda session: session.scalar(select(model.id).where(model.id == entity_id)) is not None,
        lambda session: list(session.scalars(
            select(Attachment).where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id, Attachment.deleted_at.is_(None))
            .order_by(Attachment.slot, Attachment.version))),
    )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The record for this file was not found.")
    _own_stage_files_only(db, entity_type, entity_id, user)
    if not can_see_invoices(user):
        rows = [r for r in rows if r.slot != "invoice"]
    return [
        {
            "id": r.id,
            "slot": r.slot,
            "name": r.file_name,
            "sizeKb": r.size_kb,
            "version": r.version,
            "url": f"/api/files/{r.id}",
            "uploadedBy": r.uploaded_by,
            "uploadedAt": r.uploaded_at.isoformat(),
        }
        for r in rows
    ]


# Slots a stage form's saved draft may point at ({"id": ...}); removing the file also drops that pointer so the form does not prefill a dead link.
_DRAFT_FILE_SLOTS = {"passport", "air_ticket", "pcc"}
REMOVABLE_ENTITIES = {"candidate", "application"}


def _forget_in_drafts(db: Session, row: Attachment) -> None:
    """A stage form's saved draft keeps a small reference to its file; after the file is removed the form must not prefill it."""
    if row.entity_type == "application":
        scope = Application.id == row.entity_id
    elif row.slot in _DRAFT_FILE_SLOTS:
        scope = Application.candidate_id == row.entity_id
    else:
        return
    for application in db.scalars(select(Application).where(scope)):
        draft = application.draft if isinstance(application.draft, dict) else {}
        kept = {key: value for key, value in draft.items() if not (isinstance(value, dict) and value.get("id") == row.id)}
        if len(kept) != len(draft):
            application.draft = kept


def remove_attachment(db: Session, user: User, attachment_id: str, *, candidate_id: str | None = None) -> dict:
    """Remove a wrong document or photo: a SOFT delete, in the caller's transaction (the caller commits). The row, its bytes in storage
    and every earlier version are kept; the file is hidden from the document slot (which falls back to the previous version, if there
    is one), the exports and every reader, and the audit trail records who removed which file and when. Allowed to whoever uploaded it,
    the candidate's recruiter, an admin and an MD (403 otherwise, 404 when the file is missing or already removed).
    One read (the file with its candidate's recruiter), one UPDATE, one audit entry. `candidate_id` limits it to that candidate's files."""
    candidate_owner = func.coalesce(
        select(Candidate.owner_id).where(Candidate.id == Attachment.entity_id, Attachment.entity_type == "candidate").scalar_subquery(),
        select(Candidate.owner_id).join(Application, Application.candidate_id == Candidate.id)
        .where(Application.id == Attachment.entity_id, Attachment.entity_type == "application").scalar_subquery(),
    )
    found = db.execute(select(Attachment, candidate_owner).where(Attachment.id == attachment_id).with_for_update(of=Attachment)).one_or_none()
    if found is None or found[0].deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    row, owner_id = found
    if candidate_id is not None and (row.entity_type != "candidate" or row.entity_id != candidate_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if row.slot == "invoice":
        ensure_can_see_invoices(user)
    if row.entity_type not in REMOVABLE_ENTITIES or row.slot == "source_bundle":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This file cannot be removed here.")
    if not (is_admin_level(user) or row.uploaded_by == user.id or (owner_id is not None and owner_id == user.id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who uploaded this file, the candidate's recruiter, an admin or an MD can remove it.")
    now = datetime.now(timezone.utc)
    row.deleted_at = now
    row.deleted_by = user.id
    db.add(AuditEntry(
        actor_id=user.id, action="Removed a document", target=row.file_name, entity_type=row.entity_type, entity_id=row.entity_id,
        meta={"attachment_id": row.id, "slot": row.slot, "version": row.version, "file_name": row.file_name,
              "uploaded_by": row.uploaded_by, "removed_by": user.id, "removed_at": now.isoformat()},
        at=now,
    ))
    _forget_in_drafts(db, row)
    return {"id": row.id, "slot": row.slot, "entityType": row.entity_type, "entityId": row.entity_id, "removed": True, "removedAt": now.isoformat()}


@router.delete("/{attachment_id}")
def remove(
    attachment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Soft-delete one file (see `remove_attachment`)."""
    try:
        answer = remove_attachment(db, user, attachment_id)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The document could not be removed. Please retry.") from exc
    return answer
