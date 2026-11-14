"""Manual intake persistence/restart tests with disposable Postgres and fake OCR."""
from copy import deepcopy
import hashlib
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
from app.services.document_storage import read_bytes, put_bytes
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
                         owner_id="owner", created_by="owner", source=Source.walk_in, skills=["Human skill"]))
        db.commit()
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None,
        ocr_languages="eng", ocr_max_pages=300, ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600,
        veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key")
    env = SimpleNamespace(sessions=sessions, settings=settings, actor=SimpleNamespace(id="owner", role=Role.staff),
                          submissions=[], polls=[], statuses={}, results={}, errors={})
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
            return SimpleNamespace(status=env.statuses.get(mode, "succeeded"), result=deepcopy(env.results[mode]), error=env.errors.get(mode))
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


def review_only_resume_job(env, data=None):
    """A maintenance-held reread, not an attachable candidate intake."""
    data = data or pdf(CV, CV)
    identifier = str(uuid4())
    digest = hashlib.sha256(data).hexdigest()
    key = put_bytes(env.settings, "manual/owner/" + identifier + "/review-only-resume.pdf", data, "application/pdf")
    draft = {"reviewOnlyResumeReread": {
        "candidateId": "candidate", "candidateRef": "C-SYNTHETIC", "sourceAttachmentId": "derived-resume",
        "sourceJobId": "original-mixed-job", "sourceHash": digest, "originalResumePages": [3, 4],
        "expectedPageCount": 2, "holdForAdminReview": True,
    }}
    with env.sessions() as db:
        db.add(DocumentExtractionJob(id=identifier, owner_id="owner", group_id=str(uuid4()), kind="review_resume",
            filename="review-only-resume.pdf", content_type="application/pdf", object_key=key, content_hash=digest,
            size_kb=max(1, (len(data) + 1023) // 1024), status="pending", state={"provider_generations": {"resume": 1}},
            draft=draft, queued_at=manual_extraction.now()))
        db.commit()
    return identifier, data


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


def test_review_only_resume_reread_never_touches_or_attaches_a_candidate(intake, monkeypatch):
    identifier, data = review_only_resume_job(intake)
    calls = []

    def reread(page_pdfs, *, state, save, settings, designations):
        calls.append((page_pdfs, dict(state), list(designations)))
        return {
            "status": "complete",
            "coverage": {"complete": True},
            "extractions": {
                "resume": {
                    "raw": {"name": "Human reviewed name", "experience": []},
                    "fields": {"name": "Human reviewed name"},
                    "selected_pages": [1, 2],
                    "source": {"selected_pages": [1, 2]},
                }
            },
        }

    monkeypatch.setattr(manual_extraction, "extract_selected_pages", reread)
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **k: pytest.fail("normal reader must not run"))
    process(intake, identifier)

    assert len(calls) == 1 and calls[0][0] == {"resume": data}
    with intake.sessions() as db:
        job = db.get(DocumentExtractionJob, identifier)
        candidate = db.get(Candidate, "candidate")
        assert job.status == "needs_review" and job.candidate_id is None and job.attachment_id is None
        assert job.result["reviewOnlyResumeReread"] == {
            "holdForAdminReview": True, "providerStatus": "complete", "foundKinds": ["resume"],
            "selectedPages": [1, 2], "providerSourcePages": [1, 2], "coverageComplete": True,
            "readyForIdentityReview": True, "issues": [],
        }
        assert "No candidate fields or documents were changed" in job.last_error
        assert candidate.name == "Human reviewed name" and candidate.skills == ["Human skill"]
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0

    response = intake.client.post("/api/ocr/jobs/" + identifier + "/attach", json={"candidateId": "candidate"})
    assert response.status_code == 409 and "review-only" in response.json()["detail"]


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
    # The reading result carries the provider's document untouched, with provenance beside it.
    scan = detail["result"]["scans"]["resume"]
    assert scan["raw"] == intake.results["resume"]
    assert scan["source"]["selected_pages"] == [1] and scan["source"]["local_inspection"]["selected_pages"] == [1]
    assert scan["source"]["source_sha256"]
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



def test_reviewed_original_only_attachment_preserves_cached_reading_without_filing_unreviewed_slots(intake):
    data = pdf(CV, PASSPORT)
    job = upload(intake, data)
    start(intake, job["id"]); process(intake, job["id"])
    with intake.sessions() as db:
        before = deepcopy(db.get(DocumentExtractionJob, job["id"]).state)
        assert set(before["documents"]) == {"resume", "passport"}
    endpoint = "/api/ocr/jobs/" + job["id"] + "/attach"
    response = intake.client.post(endpoint, json={"candidateId": "candidate", "originalOnly": True})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "attached"
    # A repeated request, even an older client using the default, cannot add more files.
    assert intake.client.post(endpoint, json={"candidateId": "candidate"}).status_code == 200
    with intake.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_id == "candidate")))
        assert len(rows) == 1 and rows[0].slot == "source_bundle"
        assert read_bytes(intake.settings, rows[0].object_key) == data
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.candidate_id == "candidate" and saved.attachment_id == rows[0].id
        assert saved.state["documents"] == before["documents"]
        assert saved.state["provider_jobs"] == before["provider_jobs"]
        assert saved.state["attached_original_only"] is True
        assert db.get(Candidate, "candidate").name == "Human reviewed name"
    assert len(intake.submissions) == 2

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


@pytest.mark.parametrize("original_only", [False, True])
def test_saved_files_attach_only_to_a_candidate_the_person_may_work(intake, original_only):
    job = upload(intake)
    endpoint = "/api/ocr/jobs/" + job["id"] + "/attach"
    with intake.sessions() as db:
        # Entered by this person, since given to someone else: no longer theirs to add files to.
        db.add(Candidate(id="theirs", ref="C-THEIRS", name="Theirs", phone="777777", owner_id="other", created_by="owner", source=Source.walk_in))
        db.commit()
    refused = intake.client.post(endpoint, json={"candidateId": "theirs", "originalOnly": original_only})
    assert refused.status_code == 403 and "assigned to Other" in refused.json()["detail"]
    with intake.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0


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


def test_a_failed_passport_still_delivers_the_resume_with_a_notice_and_retry_reads_only_the_passport(intake, caplog):
    import logging
    caplog.set_level(logging.INFO, logger=document_extraction.log.name)
    job = upload(intake, pdf(CV, PASSPORT))
    start(intake, job["id"])
    intake.statuses["passport"] = "failed"
    intake.errors["passport"] = SimpleNamespace(code="document_unreadable", message="Synthetic", retryable=False)
    process(intake, job["id"])
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] == "needs_review" and detail["attempts"] == 0
    scans = detail["result"]["scans"]
    assert scans["resume"]["fields"] and scans["resume"]["extraction_status"] == "complete"
    assert scans["passport"]["fields"] == {} and scans["passport"]["extraction_status"] == "needs_review"
    assert any(w.startswith("The passport pages could not be read. Enter the passport details by hand") for w in detail["result"]["warnings"])
    assert [mode for mode, _ in intake.submissions] == ["resume", "passport"]
    assert detail["lastError"].startswith("The passport pages could not be read. Enter the passport details by hand")  # what the row shows
    start(intake, job["id"])  # the explicit Retry
    intake.statuses.clear()
    process(intake, job["id"])
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] == "completed" and detail["result"]["scans"]["passport"]["fields"]
    assert [mode for mode, _ in intake.submissions] == ["resume", "passport", "passport"]  # the resume was not paid for again


def test_the_server_log_names_the_readers_failure_code_when_an_attempt_fails(intake, caplog):
    job = upload(intake, pdf(CV, PASSPORT))
    start(intake, job["id"])
    intake.statuses["resume"] = intake.statuses["passport"] = "failed"  # nothing usable: the whole attempt fails
    intake.errors["resume"] = intake.errors["passport"] = SimpleNamespace(code="document_unreadable", message="Synthetic", retryable=True)
    process(intake, job["id"])
    assert any("reader codes: " in r.getMessage() and "=document_unreadable" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
    assert intake.client.get("/api/ocr/jobs/" + job["id"]).json()["status"] == "retry"


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
    committed = False
    def uncertain_commit(db):
        nonlocal committed
        commit(db)
        committed = True
        raise SQLAlchemyError("Synthetic lost commit response")
    def read(db, *args, **kwargs):
        if not recovery_available and committed:
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


def test_dismiss_retry_and_draft_save_are_audited_with_user_and_time(intake):
    from app.db.models import AuditEntry
    job = upload(intake)
    path = "/api/ocr/jobs/groups/" + job["groupId"]
    for name in ("A", "B"):
        draft = {"form": {"name": name}, "editedFields": ["name"], "jobIds": [job["id"]]}
        assert intake.client.put(path + "/draft", json=draft).status_code == 200
    start(intake, job["id"])
    assert intake.client.post("/api/ocr/jobs/" + job["id"] + "/dismiss").status_code == 200
    assert intake.client.post("/api/ocr/jobs/" + job["id"] + "/dismiss").status_code == 200
    with intake.sessions() as db:
        rows = list(db.scalars(select(AuditEntry).where(AuditEntry.action != "Saved document intake")))
    actions = sorted(row.action for row in rows)
    assert actions == ["Asked for an uploaded document to be read again", "Removed an uploaded intake file",
        "Saved candidate details from an uploaded intake"]
    assert all(row.actor_id == "owner" and row.at is not None and row.target == "synthetic.pdf" for row in rows)


# A passport plan sends only the passport pages that carry details of their own: fewer than the pages the local check found.
def _passport_bundle():
    from test_passport_pages import NEIGHBOUR, data_page
    import ocr_hard_cases as hard
    return pdf(NEIGHBOUR, data_page(1), NEIGHBOUR), hard.valid_mrz(1)[2]


def _agreeing_passport_result(env, number):
    reading = result("passport", 1)
    reading["mrz"] = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": number, "all_check_digits_valid": True}
    env.results["passport"] = reading


def _passport_pages_the_local_check_found(env, identifier):
    with env.sessions() as db:
        state = db.get(DocumentExtractionJob, identifier).state
    return state["inspection"]["passport_pages"], state["provider_jobs"]["passport"]["selected_pages"]


def test_a_passport_plan_that_sends_fewer_pages_than_were_checked_still_completes_with_only_those_pages_saved(intake):
    data, number = _passport_bundle()
    _agreeing_passport_result(intake, number)
    job = upload(intake, data)
    start(intake, job["id"]); process(intake, job["id"])

    inspected, sent = _passport_pages_the_local_check_found(intake, job["id"])
    assert inspected == [1, 2, 3] and sent == [2]  # the neighbours took the passport kind but only page 2 was sent
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] in ("completed", "needs_review"), detail
    assert "no longer match" not in (detail["lastError"] or "")
    assert detail["result"]["scans"]["passport"]["source"]["selected_pages"] == [2]
    assert detail["result"]["scans"]["passport"]["fields"]  # the passport scan reached the form
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.status in ("completed", "needs_review") and saved.attempts == 0
        with fitz.open(stream=read_bytes(intake.settings, saved.state["documents"]["passport"]["object_key"]), filetype="pdf") as doc:
            assert doc.page_count == 1  # exactly the sent page, not the three inspected ones
            assert number in doc[0].get_text().replace(" ", "")
    attached = intake.client.post("/api/ocr/jobs/" + job["id"] + "/attach", json={"candidateId": "candidate"})
    assert attached.status_code == 200, attached.text
    with intake.sessions() as db:
        row = db.scalar(select(Attachment).where(Attachment.entity_id == "candidate", Attachment.slot == "passport"))
        with fitz.open(stream=read_bytes(intake.settings, row.object_key), filetype="pdf") as doc:
            assert doc.page_count == 1


@pytest.mark.parametrize("tampered", [[2, 99], [99], []])
def test_a_selected_page_that_was_not_checked_still_fails_the_job_with_the_pages_message(intake, monkeypatch, tampered):
    data, number = _passport_bundle()
    _agreeing_passport_result(intake, number)
    job = upload(intake, data)
    start(intake, job["id"])
    real = document_extraction.extract_attachment
    def outside_the_checked_pages(*args, **kwargs):
        output = real(*args, **kwargs)
        output["extractions"]["passport"]["selected_pages"] = list(tampered)
        return output
    monkeypatch.setattr(manual_extraction, "extract_attachment", outside_the_checked_pages)
    process(intake, job["id"])
    detail = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert detail["status"] != "completed" and detail["status"] != "needs_review"
    assert "The selected pages no longer match what was checked earlier" in detail["lastError"]
    with intake.sessions() as db:
        assert "passport" not in (db.get(DocumentExtractionJob, job["id"]).state.get("documents") or {})


@pytest.mark.parametrize("needs_review", [False, True])
def test_expanded_cached_resume_preserves_immutable_old_pdf_and_saves_both_jobs_without_provider_calls(intake, monkeypatch, needs_review):
    import hashlib
    from pathlib import Path
    from app.services.document_storage import put_bytes
    from app.services.form_extraction import values_for

    single = pdf(CV)
    expanded = pdf(CV, "Second synthetic employer and another period of work")
    saved = upload(intake, expanded)
    old_key = f"manual/owner/{saved['id']}/resume.pdf"
    put_bytes(intake.settings, old_key, single, "application/pdf")
    raw = {**result("resume", 2), "total_experience_months": 30, "indian_experience_months": 30,
           "skills": ["Document skill"], "experience": [
               {"company": "First Synthetic Studio", "title": "Designer", "country": "India", "start": "2023-08", "end": "2024-11", "duration_months": 15},
               {"company": "Second Synthetic Studio", "title": "Senior Designer", "country": "India", "start": "2024-11", "end": "2026-02", "duration_months": 15}]}
    paid = {"resume": {"job_id": "paid-first", "status": "succeeded", "selected_pages": [1], "result": {"retained": "first"}},
            "resume+": {"job_id": "paid-second", "status": "succeeded", "selected_pages": [2], "result": {"retained": "second"}}}
    notice = "Different parts of the resume gave different values for industry, designation. The first was kept; check them against the original."
    output = {"status": "needs_review" if needs_review else "complete", "inspection": {"page_count": 2, "resume_pages": [1, 2]},
        "extractions": {"resume": {"raw": raw, "fields": values_for("resume", raw), "selected_pages": [1, 2]}},
        "coverage": {"complete": not needs_review, "pages": {"total": 2, "sent": [1, 2], "unresolved": []}, "issues": [notice] if needs_review else []},
        "warnings": [notice] if needs_review else []}
    with intake.sessions() as db:
        job = db.get(DocumentExtractionJob, saved["id"])
        candidate = db.get(Candidate, "candidate")
        candidate.name = "Alex Example"
        candidate.total_experience = candidate.experience_india = 1
        candidate.work_history = [{"company": "First Synthetic Studio", "designation": "Designer", "country": "India", "period": "2023-08 - 2024-11"}]
        candidate.field_sources = {"personal.candidateName": "manual", "screening.skills": "manual", "experience.history": "extracted",
                                   "experience.totalExperience": "extracted", "experience.experienceIndia": "extracted"}
        attachment = Attachment(entity_type="candidate", entity_id=candidate.id, slot="resume", version=1, file_name="synthetic.pdf",
            content_type="application/pdf", size_kb=1, object_key=job.object_key, content_hash=job.content_hash, uploaded_by="owner")
        db.add(attachment); db.flush()
        job.candidate_id, job.attachment_id, job.status = candidate.id, attachment.id, "pending"
        job.draft = {"applicationForm": {"candidateId": candidate.id, "attachmentId": attachment.id, "slot": "resume"}}
        # Exact recovery point: both provider results/output are cached, while the
        # newly expanded derivative has not been saved because the old key exists.
        job.state = {"source_sha256": job.content_hash, "output": output, "provider_jobs": deepcopy(paid), "documents": {}}
        db.commit()
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **k: pytest.fail("No paid extraction may run again"))
    monkeypatch.setattr(document_extraction, "checked_person_output", lambda data, output, state, settings: output)
    monkeypatch.setattr(manual_extraction, "restore_inspection", lambda *a, **k: SimpleNamespace(resume_pages=[1, 2], pdf_for_pages=lambda selected: expanded))
    process(intake, saved["id"])
    with intake.sessions() as db:
        job = db.get(DocumentExtractionJob, saved["id"])
        assert job.status == ("needs_review" if needs_review else "completed") and job.attempts == 0
        new_key = job.state["documents"]["resume"]["object_key"]
        assert new_key != old_key and read_bytes(intake.settings, new_key) == expanded
        assert read_bytes(intake.settings, old_key) == single
        assert job.state["provider_jobs"] == paid
        candidate = db.get(Candidate, "candidate")
        assert candidate.name == "Alex Example" and candidate.skills == ["Human skill"]
        assert {row["company"] for row in candidate.work_history} == {"First Synthetic Studio", "Second Synthetic Studio"}
        assert candidate.total_experience == candidate.experience_india == 2
        assert bool(job.draft["applicationForm"]["result"]["coverage"]["issues"]) == needs_review
        # Recreate a lost checkpoint after the blob write. Repeating identical
        # bytes must reuse the same immutable object, with no extra provider call.
        job.state = {**job.state, "documents": {}}
        job.status = "pending"
        db.commit()
    process(intake, saved["id"])
    with intake.sessions() as db:
        job = db.get(DocumentExtractionJob, saved["id"])
        assert job.status == ("needs_review" if needs_review else "completed") and job.attempts == 0
        assert job.state["documents"]["resume"]["object_key"] == new_key and job.state["provider_jobs"] == paid
        assert job.state["documents"]["resume"]["content_hash"] == hashlib.sha256(expanded).hexdigest()
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == "candidate")) == 1
    assert len(list((Path(intake.settings.storage_dir)/'manual'/'owner'/saved['id']).glob('resume*.pdf'))) == 2
    assert intake.submissions == [] and intake.polls == []


@pytest.mark.parametrize("value", ["false", "true", 1, None, []])
def test_original_only_requires_a_boolean_before_any_attachment(intake, value):
    job = upload(intake)
    response = intake.client.post("/api/ocr/jobs/" + job["id"] + "/attach", json={"candidateId": "candidate", "originalOnly": value})
    assert response.status_code == 422
    with intake.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0


def test_conflicting_probe_results_finish_as_review_not_repeated_selected_page_failure(intake, monkeypatch):
    from test_document_extraction import UNCLASSIFIED
    monkeypatch.setattr(document_extraction, "_probe_pages", lambda inspected: {"passport": [2], "aadhaar": [2]})
    intake.settings.veris_early_dispatch = False
    job = upload(intake, pdf(CV, UNCLASSIFIED))
    start(intake, job["id"]); process(intake, job["id"])
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.status == "needs_review" and saved.attempts == 0
        assert set(saved.state["documents"]) == {"resume"}
        assert saved.state["provider_jobs"]["passport"]["result"]
        assert saved.state["provider_jobs"]["aadhaar"]["result"]
        assert saved.result["coverage"]["complete"] is False
        # An earlier failed run could have filed a now-rejected interpretation.
        # A retry removes only its current pointer, retaining bytes/history and paid jobs.
        state = deepcopy(saved.state)
        stale = deepcopy(state["documents"]["resume"])
        stale["selected_pages"] = [2]
        state["documents"]["aadhaar"] = stale
        state.pop("output", None)
        saved.state, saved.status = state, "pending"
        db.commit()
    paid_count = len(intake.submissions)
    process(intake, job["id"])
    with intake.sessions() as db:
        saved = db.get(DocumentExtractionJob, job["id"])
        assert saved.status == "needs_review"
        assert set(saved.state["documents"]) == {"resume"}
        assert saved.state["document_history"]["aadhaar"][-1] == stale
        assert read_bytes(intake.settings, stale["object_key"])
    assert len(intake.submissions) == paid_count
