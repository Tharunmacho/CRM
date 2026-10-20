"""Persistent uploads-volume identity shared by mail receipt and worker selection."""

import os
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


def storage_identity(storage_dir: str | Path) -> str:
    """Create once without replacement; copying the entire volume preserves it."""
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
