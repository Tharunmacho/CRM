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
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.core.deps import current_user
from app.db.models import Attachment, User

router = APIRouter(prefix="/api/files", tags=["files"])

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

ENTITIES = {"client", "company", "candidate", "application"}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Keep something recognisable for the download, drop anything path-like."""
    cleaned = _SAFE.sub("-", Path(name).name).strip("-")
    return cleaned[:150] or "file"


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    slot: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if entity_type not in ENTITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown entity type {entity_type!r}")

    payload = await file.read()
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Files must be under {MAX_BYTES // (1024 * 1024)} MB.",
        )
    if file.content_type and file.content_type not in ALLOWED:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Upload a PDF, Word document, or an image.",
        )

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
    version = (previous.version if previous else 0) + 1

    # Sharded by the first two characters so one directory never holds every file.
    key = f"{uuid.uuid4().hex}-{_safe_name(file.filename or 'file')}"
    target = STORAGE / key[:2] / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    row = Attachment(
        entity_type=entity_type,
        entity_id=entity_id,
        slot=slot,
        version=version,
        file_name=_safe_name(file.filename or "file"),
        size_kb=max(1, round(len(payload) / 1024)),
        content_type=file.content_type,
        object_key=f"{key[:2]}/{key}",
        content_hash=hashlib.sha256(payload).hexdigest(),
        uploaded_by=user.id,
        uploaded_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()

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
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FileResponse:
    row = db.get(Attachment, attachment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    path = STORAGE / row.object_key
    if not path.is_file():
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
        media_type=row.content_type or "application/octet-stream",
        filename=row.file_name,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("")
def list_for(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[dict]:
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
