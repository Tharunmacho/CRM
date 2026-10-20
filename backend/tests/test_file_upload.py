"""Upload validation and blocking work isolation without a real database."""

import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest
from starlette.datastructures import Headers


class FakeSession:
    def __init__(self):
        self.previous = None
        self.reads = 0
        self.rows = []
        self.commits = 0

    def _require_worker_thread(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError("Blocking upload work must not run on the event loop")

    def scalar(self, statement):
        self._require_worker_thread()
        self.reads += 1
        return self.previous

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        self._require_worker_thread()
        self.commits += 1
        for row in self.rows:
            row.id = "synthetic-attachment"


@pytest.fixture
def upload_environment(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://unused@127.0.0.1:1/unused",
        storage_dir=str(tmp_path),
    )
    # Settings and engine imports remain detached from backend/.env even when
    # this module runs alone; the request dependency is replaced below.
    with patch("app.core.config.get_settings", return_value=settings):
        from app.api.routers import files

    monkeypatch.setattr(files, "STORAGE", tmp_path)
    monkeypatch.setattr(files, "MAX_BYTES", 1024)
    database = FakeSession()
    user = SimpleNamespace(id="synthetic-user")
    application = FastAPI()
    application.include_router(files.router)
    application.dependency_overrides[files.get_db] = lambda: database
    application.dependency_overrides[files.current_user] = lambda: user
    with TestClient(application) as client:
        yield SimpleNamespace(
            client=client, files=files, database=database, user=user, storage=tmp_path
        )


def post_file(environment, payload, content_type="application/pdf"):
    return environment.client.post(
        "/api/files",
        data={"entity_type": "candidate", "entity_id": "candidate-1", "slot": "resume"},
        files={"file": ("resume.pdf", payload, content_type)},
    )


@pytest.mark.parametrize(
    ("payload", "content_type", "status"),
    [
        (b"", "application/pdf", 400),
        (b"x" * 1025, "application/pdf", 413),
        (b"not an allowed document", "text/plain", 415),
    ],
)
def test_invalid_upload_does_not_write_files_or_database(
    upload_environment, payload, content_type, status
):
    response = post_file(upload_environment, payload, content_type)

    assert response.status_code == status
    assert upload_environment.database.reads == 0
    assert upload_environment.database.rows == []
    assert upload_environment.database.commits == 0
    assert list(upload_environment.storage.iterdir()) == []


@pytest.mark.parametrize("previous_version", [None, 3])
def test_allowed_upload_preserves_response_storage_and_version_in_worker_thread(
    upload_environment, previous_version
):
    environment = upload_environment
    if previous_version is not None:
        environment.database.previous = SimpleNamespace(version=previous_version)
    payload = b"%PDF-1.7 synthetic document"

    response = post_file(environment, payload)

    assert response.status_code == 201
    result = response.json()
    assert set(result) == {
        "id", "name", "sizeKb", "version", "key", "url", "uploadedAt"
    }
    assert result["id"] == "synthetic-attachment"
    assert result["name"] == "resume.pdf"
    assert result["sizeKb"] == 1
    assert result["version"] == (previous_version or 0) + 1
    assert result["url"] == "/api/files/synthetic-attachment"
    stored = environment.storage / result["key"]
    assert stored.resolve().is_relative_to(environment.storage.resolve())
    assert stored.read_bytes() == payload
    assert environment.database.reads == 1
    assert environment.database.commits == 1
    row, = environment.database.rows
    assert row.content_hash == hashlib.sha256(payload).hexdigest()
    assert row.uploaded_by == environment.user.id
    assert row.uploaded_at.isoformat() == result["uploadedAt"]


def test_oversized_upload_reads_only_the_limit_plus_one_byte(upload_environment):
    environment = upload_environment
    stream = BytesIO(b"x" * (environment.files.MAX_BYTES * 20))
    document = UploadFile(
        file=stream,
        filename="oversized.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )
    try:
        with pytest.raises(HTTPException) as error:
            environment.files.upload(
                entity_type="candidate",
                entity_id="candidate-1",
                slot="resume",
                file=document,
                db=environment.database,
                user=environment.user,
            )

        assert error.value.status_code == 413
        assert stream.tell() == environment.files.MAX_BYTES + 1
        assert environment.database.reads == 0
        assert environment.database.rows == []
        assert list(environment.storage.iterdir()) == []
    finally:
        stream.close()
