"""Persistent uploads-volume identity shared by mail receipt and worker selection."""

import os
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import UUID, uuid4


MARKER_NAME = ".email-storage-id"
STORAGE_MISMATCH = (
    "Mailbox files belong to another storage volume. "
    "Mount/copy the original uploads volume before running this worker."
)


class StorageIdentityError(ValueError):
    """The volume marker cannot safely identify existing mail files."""


def _read_identity(marker: Path) -> str:
    try:
        with marker.open("rb") as handle:
            value = handle.read(128).decode("ascii").strip()
        return str(UUID(value))
    except (UnicodeError, ValueError):
        raise StorageIdentityError(
            "The uploads storage identity is invalid. Restore the original .email-storage-id file."
        ) from None


def storage_identity(storage_dir: str | Path, settings=None) -> str:
    """Create once without replacement; copying the entire volume preserves it."""
    if settings is not None and getattr(settings, "storage_backend", "local") == "s3":
        bucket = getattr(settings, "s3_bucket", "")
        if not bucket:
            raise StorageIdentityError("An S3 bucket is required before receiving mail.")
        endpoint = (getattr(settings, "s3_endpoint_url", None) or "aws:s3").rstrip("/")
        prefix = getattr(settings, "s3_prefix", "crm").strip("/")
        namespace = json.dumps([endpoint, bucket, prefix], separators=(",", ":"))
        return "s3:" + hashlib.sha256(namespace.encode()).hexdigest()
    directory = Path(storage_dir)
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / MARKER_NAME
    try:
        return _read_identity(marker)
    except FileNotFoundError:
        pass

    # Publish complete, flushed contents atomically. Exclusive linking chooses
    # one winner on shared volumes and never overwrites an existing identity.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".email-storage-", suffix=".tmp", dir=directory, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(f"{uuid4()}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, marker)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _read_identity(marker)
