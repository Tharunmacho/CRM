"""Manual intake persistence/restart tests with disposable Postgres and fake OCR."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pymupdf as fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Attachment, Candidate, DocumentExtractionJob, Role, Source, User
from app.services import document_extraction, manual_extraction
from app.services.document_storage import read_bytes
from test_database_naming import _migrate, schema_database
from test_document_extraction import pdf, result
from test_ocr_classification import CV, PASSPORT
from app.integrations.ocr import inspection

with patch("app.core.config.get_settings", return_value=SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused")):
    from app.api.routers import document_jobs as subject


@pytest.fixture
def intake(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=value, name=value.title(), email=value + "@example.test", role=Role.staff, active=True)
                    for value in ("owner", "other")])
        db.flush()
        db.add(Candidate(id="candidate", ref="C-SYNTHETIC", name="Human reviewed name", phone="123456",
                         owner_id="other", created_by="owner", source=Source.walk_in, skills=["Human skill"]))
        db.commit()
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None,
        ocr_languages="eng", ocr_max_pages=300, ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600,
        veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key")
    env = SimpleNamespace(sessions=sessions, settings=settings, actor=SimpleNamespace(id="owner", role=Role.staff),
                          submissions=[], polls=[], statuses={}, results={})
    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            with sessions() as db:
                state = db.scalar(select(DocumentExtractionJob.state))
                assert state["provider_jobs"][mode]["idempotency_key"] == idempotency_key
            env.submissions.append((mode, idempotency_key))
            with fitz.open(stream=document.data, filetype="pdf") as sent:
                env.results.setdefault(mode, result(mode, sent.page_count))
            return SimpleNamespace(job_id="job-" + mode, status="queued")
        def get(self, job_id):
            mode = job_id.removeprefix("job-")
            with sessions() as db:
                state = db.scalar(select(DocumentExtractionJob.state))
                assert state["provider_jobs"][mode]["job_id"] == job_id
            env.polls.append(job_id)
            return SimpleNamespace(status=env.statuses.get(mode, "succeeded"), result=deepcopy(env.results[mode]))
    class Provider:
        def __init__(self, **options): self.jobs = Jobs()
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(document_extraction, "VerisOCR", Provider)
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(inspection.subprocess, "run", lambda *args, **kwargs: pytest.fail("No external programs"))
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(subject.router)
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: env.actor
    with TestClient(app) as client:
        env.client = client
        yield env


def upload(env, data=None, group=None, kind="resume"):
    response = env.client.post("/api/ocr/jobs", data={"kind": kind, "group_id": group or str(uuid4())},
        files={"file": ("synthetic.pdf", data or pdf(CV), "application/pdf")})
    assert response.status_code == 202, response.text
    return response.json()


def process(env, identifier):
    manual_extraction.process_job(identifier, sessions=env.sessions, settings=env.settings)


def start(env, identifier):
    response = env.client.post("/api/ocr/jobs/" + identifier + "/retry")
    assert response.status_code == 200, response.text


def test_original_saved_before_read_and_upload_retry_is_idempotent(intake):
    data = pdf(CV)
    job = upload(intake, data)
    assert job["status"] == "uploaded"
    process(intake, job["id"])
    assert intake.submissions == []
    repeated = upload(intake, data, group=job["groupId"])
    assert repeated["id"] == job["id"]
    original = intake.client.get(job["originalUrl"])
    assert original.status_code == 200 and original.content == data
    assert "no-store" in original.headers["cache-control"]
    with intake.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 1


def test_provider_id_result_and_form_draft_survive_worker_restart(intake, monkeypatch):
    job = upload(intake)
    draft = {"form": {"name": "Human revised name", "phone": "765432"}, "editedFields": ["name", "phone"], "jobIds": [job["id"]]}
    path = "/api/ocr/jobs/groups/" + job["groupId"]
    assert intake.client.put(path + "/draft", json=draft).status_code == 200
    intake.statuses["resume"] = "running"
    start(intake, job["id"])
    process(intake, job["id"])
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.status == "retry"
        assert saved.state["provider_jobs"]["resume"]["job_id"] == "job-resume"
        assert saved.draft["form"]["name"] == "Human revised name"
    # Fresh sessions simulate process restart; original inspection and provider
    # acceptance must be reused rather than submitting a second paid request.
    monkeypatch.setattr(document_extraction, "inspect_document", lambda *a, **k: pytest.fail("Inspection repeated"))
    intake.statuses["resume"] = "succeeded"
    process(intake, job["id"])
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] == "completed"
    assert detail["result"]["scans"]["resume"]["fields"]["totalExperience"] == 3
    assert len(intake.submissions) == 1 and intake.polls == ["job-resume", "job-resume"]
    assert intake.client.get(path).json()[0]["draft"] == {**draft, "savedCandidateId": None}


def test_attach_keeps_original_and_selected_documents_without_changing_human_fields(intake):
    data = pdf(CV, PASSPORT)
    job = upload(intake, data)
    start(intake, job["id"]); process(intake, job["id"])
    endpoint = "/api/ocr/jobs/" + job["id"] + "/attach"
    first = intake.client.post(endpoint, json={"candidateId": "candidate"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "attached"
    assert intake.client.post(endpoint, json={"candidateId": "candidate"}).status_code == 200
    with intake.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_id == "candidate")))
        assert {row.slot for row in rows} == {"source_bundle", "resume", "passport"}
        assert len(rows) == 3 and all(row.version == 1 for row in rows)
        original = next(row for row in rows if row.slot == "source_bundle")
        assert read_bytes(intake.settings, original.object_key) == data
        for row in rows:
            if row.slot != "source_bundle":
                with fitz.open(stream=read_bytes(intake.settings, row.object_key), filetype="pdf") as doc:
                    assert doc.page_count == 1
        candidate = db.get(Candidate, "candidate")
        assert candidate.name == "Human reviewed name" and candidate.skills == ["Human skill"]
    assert intake.client.get("/api/ocr/jobs").json() == []


def test_saved_original_can_attach_without_reading_and_cannot_be_moved(intake):
    job = upload(intake)
    endpoint = "/api/ocr/jobs/" + job["id"] + "/attach"
    assert intake.client.post(endpoint, json={"candidateId": "candidate"}).status_code == 200
    assert intake.submissions == []
    with intake.sessions() as db:
        db.add(Candidate(id="second", ref="C-SECOND", name="Second", phone="555555", owner_id="owner", source=Source.walk_in))
        db.commit()
    assert intake.client.post(endpoint, json={"candidateId": "second"}).status_code == 409
    with intake.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 1


def test_owner_access_and_group_draft_validation(intake):
    job = upload(intake)
    path = "/api/ocr/jobs/" + job["id"]
    group = "/api/ocr/jobs/groups/" + job["groupId"]
    assert intake.client.put(group + "/draft", json={"form": {"password": "not allowed"}}).status_code == 422
    assert intake.client.put(group + "/draft", json={"jobIds": ["unrelated"]}).status_code == 422
    intake.actor = SimpleNamespace(id="other", role=Role.staff)
    assert intake.client.get("/api/ocr/jobs").json() == []
    for endpoint in [path, path + "/original", group]:
        assert intake.client.get(endpoint).status_code == 404
    for endpoint in [path + "/retry", path + "/dismiss", path + "/attach"]:
        response = intake.client.post(endpoint, json={"candidateId": "candidate"} if endpoint.endswith("/attach") else None)
        assert response.status_code == 404
    assert intake.client.put(group + "/draft", json={"form": {"name": "Changed"}}).status_code == 404


def test_failure_budget_retains_original_and_retry_does_not_discard_checkpoint(intake, monkeypatch):
    job = upload(intake)
    start(intake, job["id"])
    def failure(*args, **kwargs):
        raise manual_extraction.ExtractionFailure("Synthetic provider unavailable")
    monkeypatch.setattr(manual_extraction, "extract_attachment", failure)
    for _ in range(4): process(intake, job["id"])
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] == "failed" and detail["attempts"] == 4
    assert intake.client.get(job["originalUrl"]).status_code == 200
    start(intake, job["id"])
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] == "pending" and detail["attempts"] == 0


def test_dismiss_prevents_worker_processing_but_keeps_owner_original(intake):
    job = upload(intake)
    assert intake.client.post("/api/ocr/jobs/" + job["id"] + "/dismiss").status_code == 200
    process(intake, job["id"])
    assert not intake.submissions
    assert intake.client.get("/api/ocr/jobs").json() == []
    assert intake.client.get(job["originalUrl"]).status_code == 200


def test_supporting_documents_are_durable_without_starting_paid_extraction(intake):
    job = upload(intake, kind="education")
    assert job["status"] == "uploaded"
    assert intake.client.post("/api/ocr/jobs/" + job["id"] + "/retry").status_code == 422
    response = intake.client.post("/api/ocr/jobs/" + job["id"] + "/attach", json={"candidateId": "candidate"})
    assert response.status_code == 200, response.text
    with intake.sessions() as db:
        slots = set(db.scalars(select(Attachment.slot)))
        assert slots == {"source_bundle", "education"}
    assert intake.submissions == []


@pytest.mark.parametrize("recovery_available", [True, False])
def test_lost_commit_response_retains_original_and_retry_recovers_same_job(intake, monkeypatch, recovery_available):
    commit, scalar = Session.commit, Session.scalar
    reads = 0
    def uncertain_commit(db):
        commit(db)
        raise SQLAlchemyError("Synthetic lost commit response")
    def read(db, *args, **kwargs):
        nonlocal reads
        reads += 1
        if not recovery_available and reads >= 3:
            raise SQLAlchemyError("Synthetic recovery connection unavailable")
        return scalar(db, *args, **kwargs)
    monkeypatch.setattr(Session, "commit", uncertain_commit)
    monkeypatch.setattr(Session, "scalar", read)
    data, group_id = pdf(CV), str(uuid4())
    response = intake.client.post("/api/ocr/jobs", data={"kind": "resume", "group_id": group_id},
        files={"file": ("synthetic.pdf", data, "application/pdf")})
    assert response.status_code == (202 if recovery_available else 503), response.text
    monkeypatch.setattr(Session, "commit", commit)
    monkeypatch.setattr(Session, "scalar", scalar)
    recovered = upload(intake, data, group=group_id)
    assert intake.client.get(recovered["originalUrl"]).content == data
    with intake.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 1


def test_known_precommit_failure_cleans_only_uncommitted_original(intake, monkeypatch):
    flush = Session.flush
    def failed_flush(db, *args, **kwargs):
        raise SQLAlchemyError("Synthetic flush rejected")
    monkeypatch.setattr(Session, "flush", failed_flush)
    response = intake.client.post("/api/ocr/jobs", data={"kind": "resume", "group_id": str(uuid4())},
        files={"file": ("synthetic.pdf", pdf(CV), "application/pdf")})
    assert response.status_code == 503
    monkeypatch.setattr(Session, "flush", flush)
    from pathlib import Path
    assert list(Path(intake.settings.storage_dir).rglob("original")) == []
    with intake.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 0
