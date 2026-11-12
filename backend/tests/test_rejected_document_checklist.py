"""Wrong-person evidence stays reviewable but cannot satisfy an accepted document checklist."""
from io import BytesIO
import json
from zipfile import ZipFile

from sqlalchemy import select

from app.api.routers import candidates, travelled
from app.db.models import DocumentExtractionJob
from app.services import application_form, candidate_search, manual_extraction
from app.services.desk_snapshot import read_snapshot
from test_application_form import desk, form, upload, url
from test_candidate_exports import exports, get, sheets, add_file
from test_database_naming import schema_database
from test_document_extraction import pdf
from test_ocr_classification import CV


def test_rejected_identity_file_is_retained_and_excluded_from_every_checklist(exports):
    env = exports
    env.client.app.include_router(candidates.router)
    # An earlier manual receipt cannot bless a later, known wrong-person file.
    assert env.client.put(url() + "/documents/resume/status", json={"uploaded": True, "note": "Paper received"}).status_code == 200
    env.extra = {"resume": {"name": "Other Person"}}
    response = upload(env, "resume", pdf(CV), "wrong-resume.pdf")
    assert response.status_code == 202
    identifier = response.json()["document"]["current"]["attachmentId"]
    with env.sessions() as db:
        job_id = db.scalar(select(DocumentExtractionJob.id).where(DocumentExtractionJob.attachment_id == identifier))
    manual_extraction.process_job(job_id, sessions=env.sessions, settings=env.settings)
    state = form(env)
    doc = state["documents"]["resume"]
    assert doc["identityMismatch"] and doc["current"]["identityMismatch"] and doc["extraction"]["identityMismatch"]
    assert doc["fileUploaded"] and not doc["uploaded"] and not doc["extractionPending"]
    assert "documents.resume" in {item["key"] for item in state["completeness"]["missing"]}
    assert state["personal"]["candidateName"] == "Alex Example" and state["personal"]["fatherName"] is None
    assert env.client.get(f"/api/files/{identifier}").status_code == 200
    assert env.client.post(url() + "/extract", json={"attachmentIds": [identifier]}).status_code == 409
    assert env.client.put(url() + "/documents/resume/status", json={"uploaded": True}).status_code == 409
    profile = env.client.get("/api/candidates/candidate").json()
    resume = next(item for item in profile["documents"] if item["key"] == "resume")
    assert not resume["uploaded"] and resume["fileUploaded"] and resume["versions"][0]["identityMismatch"]
    with env.sessions() as db:
        snapshot = read_snapshot(db, "staff")
        assert next(item for item in snapshot["files"] if item["id"] == identifier)["identity_mismatch"]
        assert "resume" in {item["slot"] for item in application_form.missing_required_documents(db, "candidate")}
        rows = [{"candidateId": "candidate"}]
        travelled._add_documents(db, rows)
        assert "Resume" in rows[0]["documents"]["missing"]
        bank = sheets(candidate_search.export_candidates(db, candidate_search.Search()))["Candidates"]
        person = next(item for item in bank[1:] if item[0] == "candidate")
        assert person[bank[0].index("Resume document")] == "Wrong document"
    response = get(env, "documents.zip")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        resume = next(item for item in manifest["checklist"] if item["slot"] == "resume")
        assert not resume["uploaded"] and resume["status"].startswith("Wrong document")
        file = next(item for item in manifest["documents"] if item["attachmentId"] == identifier)
        assert file["identityMismatch"] and file["includedInZip"] and archive.read(file["archivePath"])
    # A subsequently supplied correct document counts; the old rejection is not erased.
    add_file(env, "correct-resume", "resume", version=2)
    corrected = form(env)["documents"]["resume"]
    assert corrected["uploaded"] and corrected["identityMismatch"]
    assert corrected["history"][0]["identityMismatch"]
