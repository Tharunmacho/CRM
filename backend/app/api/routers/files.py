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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.db.session import get_db
from app.core.deps import current_user, ensure_can_work
from app.db.models import Application, Attachment, AuditEntry, Candidate, Client, Company, Role, User
from app.services import document_storage

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

ENTITIES = {"client": Client, "company": Company, "candidate": Candidate, "application": Application}

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

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Keep something recognisable for the download, drop anything path-like."""
    cleaned = _SAFE.sub("-", name.replace("\\", "/").rsplit("/", 1)[-1]).strip("-.")
    return cleaned[:150] or "file"


def _entity(db: Session, entity_type: str, entity_id: str, *, lock: bool = False):
    model = ENTITIES.get(entity_type)
    if model is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Files cannot be added to this kind of record ({entity_type}).")
    if not entity_id or not entity_id.strip() or len(entity_id) > 36:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "We could not tell which record this file belongs to.")
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


@router.post("", status_code=status.HTTP_201_CREATED)
def upload(
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    slot: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if entity_type not in ENTITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Files cannot be added to this kind of record ({entity_type}).")
    if not entity_id or not entity_id.strip() or len(entity_id) > 36:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "We could not tell which record this file belongs to.")
    slot = slot.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", slot):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose which document this file is for.")

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

    # Sharded by the first two characters so one directory never holds every file.
    key = f"{uuid.uuid4().hex}-{_safe_name(file.filename or 'file')}"
    target = STORAGE / key[:2] / key
    object_key = document_storage.storage_key(settings, f"{key[:2]}/{key}")
    storage_attempted = False
    try:
        record = _entity(db, entity_type, entity_id, lock=True)
        if entity_type == "application":
            ensure_can_work(db, record, user)
        if entity_type == "candidate" and user.role == Role.staff and record.owner_id != user.id:
            entered_at = getattr(record, "created_at", None)
            initial_upload = getattr(record, "created_by", None) == user.id and entered_at and entered_at >= datetime.now(timezone.utc) - timedelta(hours=24)
            if not initial_upload:
                raise HTTPException(403, "Only the assigned recruiter or a supervisor can upload documents for this candidate")
        if entity_type == "candidate" and slot == "source_bundle":
            raise HTTPException(422, "The original upload is kept as it was received and cannot be replaced by hand")
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
            content_hash=hashlib.sha256(payload).hexdigest(),
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
            meta={"attachment_id": row.id, "slot": slot, "version": row.version},
            at=row.uploaded_at,
        ))
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

    return {
        "id": row.id,
        "name": row.file_name,
        "sizeKb": row.size_kb,
        "version": row.version,
        "key": row.object_key,
        "url": f"/api/files/{row.id}",
        "uploadedAt": row.uploaded_at.isoformat(),
    }


@router.get("/{attachment_id}")
def download(
    attachment_id: str,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FileResponse:
    row = db.get(Attachment, attachment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

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
    _entity(db, entity_type, entity_id)
    rows = db.scalars(
        select(Attachment)
        .where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
        .order_by(Attachment.slot, Attachment.version)
    )
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
