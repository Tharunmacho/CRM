"""Upload validation and blocking work isolation without a real database."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest
from starlette.datastructures import Headers
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import Application, Attachment, AuditEntry, Candidate, Role


class FakeSession:
    def __init__(self):
        self.previous = None
        self.reads = 0
        self.rows = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.statements = []
        self.committed_count = 0
        self.commit_error = None
        self.flush_error = None
        self.entities = {
            Candidate: SimpleNamespace(id="candidate-1", owner_id="synthetic-user"),
            Application: SimpleNamespace(id="application-1", candidate_id="candidate-1"),
        }

    def _require_worker_thread(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError("Blocking upload work must not run on the event loop")

    def scalar(self, statement):
        self._require_worker_thread()
        self.reads += 1
        self.statements.append(statement)
        model = statement.column_descriptions[0]["entity"]
        if model is not Attachment:
            return self.entities.get(model)
        if self.previous is not None:
            return self.previous
        rows = self.scalars(statement)
        return max(rows, key=lambda row: row.version, default=None)

    def scalars(self, statement):
        self._require_worker_thread()
        params = statement.compile().params
        return sorted(
            (row for row in self.rows if isinstance(row, Attachment)
             and row.entity_type == params["entity_type_1"]
             and row.entity_id == params["entity_id_1"]),
            key=lambda row: (row.slot, row.version),
        )

    def get(self, model, identifier):
        self._require_worker_thread()
        if model is Attachment:
            return next((row for row in self.rows
                         if isinstance(row, Attachment) and row.id == identifier), None)
        return self.entities.get(model)

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        self._require_worker_thread()
        self.commits += 1
        if self.commit_error:
            raise self.commit_error
        self.committed_count = len(self.rows)

    def flush(self):
        self._require_worker_thread()
        self.flushes += 1
        if self.flush_error:
            raise self.flush_error

    def rollback(self):
        self.rollbacks += 1
        del self.rows[self.committed_count:]


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
    user = SimpleNamespace(id="synthetic-user", role=Role.admin)
    application = FastAPI()
    application.include_router(files.router)
    application.dependency_overrides[files.get_db] = lambda: database
    application.dependency_overrides[files.current_user] = lambda: user
    with TestClient(application) as client:
        yield SimpleNamespace(
            client=client, files=files, database=database, user=user, storage=tmp_path,
            application=application,
        )


def post_file(environment, payload, content_type="application/pdf", *, filename="resume.pdf", **fields):
    return environment.client.post(
        "/api/files",
        data={"entity_type": "candidate", "entity_id": "candidate-1", "slot": "resume", **fields},
        files={"file": (filename, payload, content_type)},
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
    assert result["id"]
    assert result["name"] == "resume.pdf"
    assert result["sizeKb"] == 1
    assert result["version"] == (previous_version or 0) + 1
    assert result["url"] == f"/api/files/{result['id']}"
    stored = environment.storage / result["key"]
    assert stored.resolve().is_relative_to(environment.storage.resolve())
    assert stored.read_bytes() == payload
    assert environment.database.reads == 2
    assert environment.database.commits == 1
    row, audit = environment.database.rows
    assert row.content_hash == hashlib.sha256(payload).hexdigest()
    assert row.uploaded_by == environment.user.id
    assert row.uploaded_at.isoformat() == result["uploadedAt"]
    assert isinstance(audit, AuditEntry)
    assert audit.actor_id == environment.user.id
    assert audit.entity_id == "candidate-1"
    assert audit.entity_type == "candidate"
    assert audit.meta == {"attachment_id": row.id, "slot": "resume", "version": row.version}
    assert audit.at == row.uploaded_at
    assert "FOR UPDATE" in str(environment.database.statements[0])


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


@pytest.mark.parametrize("fields", [
    {"entity_type": "unknown"},
    {"entity_id": " "},
    {"entity_id": "x" * 37},
    {"slot": " "},
    {"slot": "x" * 41},
    {"slot": "../resume"},
])
def test_invalid_record_reference_or_slot_is_rejected_before_writes(upload_environment, fields):
    response = post_file(upload_environment, b"%PDF-1.7 document", **fields)

    assert response.status_code == 400
    assert upload_environment.database.reads == 0
    assert upload_environment.database.rows == []
    assert list(upload_environment.storage.iterdir()) == []


def test_missing_record_cannot_receive_or_list_files(upload_environment):
    environment = upload_environment
    environment.database.entities.pop(Candidate)

    response = post_file(environment, b"%PDF-1.7 document")
    history = environment.client.get("/api/files", params={
        "entity_type": "candidate", "entity_id": "candidate-1",
    })

    assert response.status_code == history.status_code == 404
    assert environment.database.rows == []
    assert list(environment.storage.iterdir()) == []


def test_application_upload_uses_workflow_ownership(upload_environment):
    environment = upload_environment
    environment.user.role = Role.staff
    environment.database.entities[Candidate].owner_id = "another-recruiter"

    denied = post_file(environment, b"%PDF-1.7 document",
                       entity_type="application", entity_id="application-1", slot="offerLetter")
    assert denied.status_code == 403
    assert environment.database.rows == []
    assert list(environment.storage.iterdir()) == []

    environment.database.entities[Candidate].owner_id = environment.user.id
    allowed = post_file(environment, b"%PDF-1.7 document",
                        entity_type="application", entity_id="application-1", slot="offerLetter")
    assert allowed.status_code == 201


def test_candidate_intake_can_upload_after_round_robin_assignment(upload_environment):
    environment = upload_environment
    environment.user.role = Role.staff
    environment.database.entities[Candidate].owner_id = "assigned-recruiter"
    environment.database.entities[Candidate].created_by = environment.user.id
    environment.database.entities[Candidate].created_at = datetime.now(timezone.utc)

    response = post_file(environment, b"%PDF-1.7 document")

    assert response.status_code == 201


def test_candidate_upload_denies_other_staff_and_expired_creator_window(upload_environment):
    environment = upload_environment
    environment.user.role = Role.staff
    record = environment.database.entities[Candidate]
    record.owner_id = "another-recruiter"
    assert post_file(environment, b"%PDF document").status_code == 403
    record.created_by = environment.user.id
    record.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    assert post_file(environment, b"%PDF document").status_code == 403


def test_source_bundle_cannot_be_replaced_by_manual_upload(upload_environment):
    assert post_file(upload_environment, b"%PDF document", slot="source_bundle").status_code == 422


@pytest.mark.parametrize("content_type", ["application/octet-stream", None])
def test_browser_generic_type_uses_supported_document_extension(upload_environment, content_type):
    environment = upload_environment
    response = post_file(environment, b"%PDF-1.7 document", content_type, filename="RESUME.PDF")

    assert response.status_code == 201
    assert environment.database.rows[0].content_type == "application/pdf"
    assert environment.client.get(response.json()["url"]).headers["content-type"] == "application/pdf"


def test_unknown_generic_file_is_rejected(upload_environment):
    response = post_file(upload_environment, b"unrecognized", "application/octet-stream", filename="file.bin")

    assert response.status_code == 415
    assert upload_environment.database.rows == []


def test_upload_replacement_keeps_each_version_viewable_and_downloadable(upload_environment):
    environment = upload_environment
    payloads = [b"%PDF-1.7 first scan", b"%PDF-1.7 corrected scan"]
    versions = [post_file(environment, payload).json() for payload in payloads]
    history = environment.client.get("/api/files", params={
        "entity_type": "candidate", "entity_id": "candidate-1",
    })

    assert history.status_code == 200
    assert [entry["version"] for entry in history.json()] == [1, 2]
    assert [entry["id"] for entry in history.json()] == [entry["id"] for entry in versions]
    assert versions[0]["key"] != versions[1]["key"]
    for version, payload in zip(versions, payloads):
        preview = environment.client.get(version["url"])
        download = environment.client.get(version["url"], params={"download": "true"})
        assert preview.status_code == download.status_code == 200
        assert preview.content == download.content == payload
        assert preview.headers["content-disposition"].startswith("inline;")
        assert download.headers["content-disposition"].startswith("attachment;")
        assert preview.headers["content-type"] == "application/pdf"
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.headers["cache-control"] == "private, no-store"


def test_missing_bytes_and_unknown_attachment_report_distinct_failures(upload_environment):
    environment = upload_environment
    uploaded = post_file(environment, b"%PDF-1.7 document").json()
    (environment.storage / uploaded["key"]).unlink()

    assert environment.client.get(uploaded["url"]).status_code == 410
    assert environment.client.get("/api/files/unknown").status_code == 404


def test_download_cannot_resolve_outside_document_storage(upload_environment, monkeypatch):
    environment = upload_environment
    outside = environment.storage / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7 outside storage")
    document_storage = environment.storage / "documents"
    document_storage.mkdir()
    monkeypatch.setattr(environment.files, "STORAGE", document_storage)
    environment.database.rows.append(Attachment(
        id="outside-file", object_key="../outside.pdf", file_name="outside.pdf",
        content_type="application/pdf",
    ))

    response = environment.client.get("/api/files/outside-file")

    assert response.status_code == 410
    assert outside.read_bytes() == b"%PDF-1.7 outside storage"


@pytest.mark.parametrize(("failure", "expected_status"), [
    (IntegrityError("synthetic insert", {}, Exception("version collision")), 409),
    (OperationalError("synthetic commit", {}, Exception("database unavailable")), 503),
])
def test_commit_failure_rolls_back_upload_and_audit_without_losing_previous_version(
    upload_environment, failure, expected_status,
):
    environment = upload_environment
    first = post_file(environment, b"%PDF-1.7 saved scan").json()
    environment.database.commit_error = failure

    response = post_file(environment, b"%PDF-1.7 replacement")

    assert response.status_code == expected_status
    assert environment.database.rollbacks == 1
    assert len(environment.database.rows) == 2  # First version and its audit only.
    assert [path for path in environment.storage.rglob("*") if path.is_file()] == [
        environment.storage / first["key"],
    ]
    assert environment.client.get(first["url"]).content == b"%PDF-1.7 saved scan"


def test_metadata_failure_does_not_write_bytes(upload_environment):
    environment = upload_environment
    environment.database.flush_error = IntegrityError("synthetic insert", {}, Exception("constraint"))

    response = post_file(environment, b"%PDF-1.7 document")

    assert response.status_code == 409
    assert environment.database.commits == 0
    assert environment.database.rows == []
    assert list(environment.storage.iterdir()) == []


def test_unknown_commit_outcome_keeps_immutable_bytes_for_recovery(upload_environment):
    environment = upload_environment
    environment.database.commit_error = OperationalError(
        "synthetic commit", {}, Exception("connection dropped"), connection_invalidated=True,
    )
    payload = b"%PDF-1.7 potentially committed document"

    response = post_file(environment, payload)

    assert response.status_code == 503
    retained = [path for path in environment.storage.rglob("*") if path.is_file()]
    assert len(retained) == 1
    assert retained[0].read_bytes() == payload
    assert environment.database.rollbacks == 1


def test_partial_storage_failure_removes_incomplete_file_and_rolls_back(upload_environment, monkeypatch):
    environment = upload_environment
    original_write = type(environment.storage).write_bytes

    def partial_write(path, data):
        original_write(path, data[:4])
        raise OSError("synthetic disk full")

    monkeypatch.setattr(type(environment.storage), "write_bytes", partial_write)

    response = post_file(environment, b"%PDF-1.7 document")

    assert response.status_code == 503
    assert environment.database.rows == []
    assert environment.database.commits == 0
    assert environment.database.rollbacks == 1
    assert not any(path.is_file() for path in environment.storage.rglob("*"))


def test_files_require_a_session_for_upload_history_and_download(upload_environment):
    environment = upload_environment
    environment.application.dependency_overrides.pop(environment.files.current_user)

    assert post_file(environment, b"%PDF-1.7 document").status_code == 401
    assert environment.client.get("/api/files", params={
        "entity_type": "candidate", "entity_id": "candidate-1",
    }).status_code == 401
    assert environment.client.get("/api/files/unknown").status_code == 401


def test_stage_files_follow_the_stage_modal_rule_staff_see_only_their_own_candidates(upload_environment):
    """PRD: staff work the stage modal on their own candidates only; admin and manager on any candidate."""
    environment = upload_environment
    environment.user.role = Role.staff
    environment.database.entities[Candidate].owner_id = environment.user.id
    saved = post_file(environment, b"%PDF-1.7 document", entity_type="application", entity_id="application-1", slot="offerLetter")
    assert saved.status_code == 201
    url = saved.json()["url"]
    listing = "/api/files?entity_type=application&entity_id=application-1"
    assert environment.client.get(listing).status_code == 200
    assert environment.client.get(url).status_code == 200

    environment.database.entities[Candidate].owner_id = "another-recruiter"
    assert environment.client.get(listing).status_code == 403
    assert environment.client.get(url).status_code == 403
    for role in (Role.manager, Role.admin):
        environment.user.role = role
        assert environment.client.get(listing).status_code == 200
        assert environment.client.get(url).status_code == 200
    # A candidate's own documents are not stage files: every role keeps reading those.
    environment.user.role = Role.staff
    assert environment.client.get("/api/files?entity_type=candidate&entity_id=candidate-1").status_code == 200
