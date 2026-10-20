"""Uploads-volume markers use temporary directories only."""

from concurrent.futures import ThreadPoolExecutor
import shutil
from uuid import UUID

import pytest

from app.services.mail_storage import MARKER_NAME, StorageIdentityError, storage_identity


def test_identity_is_persistent_and_distinct_between_volumes(tmp_path):
    first = storage_identity(tmp_path / "first")

    assert str(UUID(first)) == first
    assert storage_identity(tmp_path / "first") == first
    assert storage_identity(tmp_path / "second") != first
    assert list((tmp_path / "first").iterdir()) == [tmp_path / "first" / MARKER_NAME]


def test_copying_entire_uploads_volume_preserves_identity(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    original = storage_identity(source)
    (source / "original.bin").write_bytes(b"Synthetic document")

    shutil.copytree(source, destination)

    assert storage_identity(destination) == original
    assert (destination / "original.bin").read_bytes() == b"Synthetic document"


@pytest.mark.parametrize("contents", [b"", b"invalid-marker", b"\xff"])
def test_invalid_existing_identity_is_never_replaced(tmp_path, contents):
    marker = tmp_path / MARKER_NAME
    marker.write_bytes(contents)

    with pytest.raises(StorageIdentityError, match="invalid"):
        storage_identity(tmp_path)

    assert marker.read_bytes() == contents


def test_concurrent_creators_observe_one_complete_identity(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(storage_identity, [tmp_path] * 16))

    assert len(set(values)) == 1
    assert storage_identity(tmp_path) == values[0]
    assert list(tmp_path.iterdir()) == [tmp_path / MARKER_NAME]
