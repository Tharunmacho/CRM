"""Wrong-person evidence stays reviewable but cannot satisfy an accepted document checklist."""
from io import BytesIO
import json
from zipfile import ZipFile

from sqlalchemy import select

from app.api.routers import candidates, travelled
from app.db.models import AuditEntry, Candidate, CandidateStatus, DocumentExtractionJob
from app.services import application_form, candidate_search, manual_extraction
from app.services.candidate_review import clear_resolved_document_review, record_review_flag, resolve_document_review_flag, review_reasons
from app.services.document_identity import IDENTITY_REJECTED_ACTION, IDENTITY_RESOLVED_ACTION
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
        person = next(item for item in bank[1:] if item[bank[0].index("CRM reference")] == "candidate")
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


def test_successful_replay_resolves_only_the_matching_identity_rejection(exports):
    """A corrected saved output makes its own file usable again, not another job's rejection."""
    env = exports
    env.client.app.include_router(candidates.router)
    env.extra = {"resume": {"name": "Other Person"}}
    response = upload(env, "resume", pdf(CV), "wrong-resume.pdf")
    assert response.status_code == 202
    identifier = response.json()["document"]["current"]["attachmentId"]
    with env.sessions() as db:
        job_id = db.scalar(select(DocumentExtractionJob.id).where(DocumentExtractionJob.attachment_id == identifier))
    manual_extraction.process_job(job_id, sessions=env.sessions, settings=env.settings)

    # A resolution record for a different extraction job cannot bless this
    # attachment. This catches accidental attachment-only matching.
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        db.add(AuditEntry(action=IDENTITY_RESOLVED_ACTION, target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                          meta={"attachment_id": identifier, "document_job_id": "another-job",
                                "resolves": IDENTITY_REJECTED_ACTION}))
        db.commit()
    still_wrong = form(env)["documents"]["resume"]
    assert still_wrong["identityMismatch"] and not still_wrong["uploaded"]

    # Reuse the saved job/output after the narrow identity rule is corrected.
    # The original file stays attached; only this job's rejection is resolved.
    with env.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        corrected = {"status": "complete", "coverage": {"complete": True}, "extractions": {
            "resume": {"fields": {"name": "Alex Example"}, "raw": {"name": "Alex Example"}}}}
        application_form.apply_job_output(db, job, corrected)
        candidate = db.get(Candidate, "candidate")
        db.commit()
        audits = list(db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "candidate",
            AuditEntry.entity_id == candidate.id, AuditEntry.action.in_((IDENTITY_REJECTED_ACTION, IDENTITY_RESOLVED_ACTION)))))
        assert any(row.action == IDENTITY_REJECTED_ACTION and row.meta["attachment_id"] == identifier and row.meta["document_job_id"] == job_id
                   for row in audits)
        assert any(row.action == IDENTITY_RESOLVED_ACTION and row.meta["attachment_id"] == identifier and row.meta["document_job_id"] == job_id
                   for row in audits)
        assert candidate.needs_review is False

    accepted = form(env)["documents"]["resume"]
    assert accepted["uploaded"] and not accepted["identityMismatch"] and not accepted["current"]["identityMismatch"]
    with env.sessions() as db:
        assert "resume" not in {item["slot"] for item in application_form.missing_required_documents(db, "candidate")}
    assert env.client.post(url() + "/extract", json={"attachmentIds": [identifier]}).status_code == 202


def test_scoped_document_review_resolution_keeps_unrelated_flags(exports):
    """One replay cannot clear a different document warning or a legacy generic warning."""
    env = exports
    with env.sessions() as db:
        first, second = db.get(Candidate, "candidate"), db.get(Candidate, "second-candidate")
        first.needs_review, first.status = True, CandidateStatus.needs_review
        second.needs_review, second.status = True, CandidateStatus.needs_review
        record_review_flag(db, first, ocr_warning=True, attachment_id="first-file", document_job_id="first-job")
        record_review_flag(db, first, ocr_warning=True, attachment_id="other-file", document_job_id="other-job")
        record_review_flag(db, first, ocr_warning=True)  # historic generic warning stays conservative
        record_review_flag(db, second, ocr_warning=True, attachment_id="second-file", document_job_id="second-job")
        assert resolve_document_review_flag(db, first, actor_id="staff", attachment_id="first-file", document_job_id="first-job")
        assert not clear_resolved_document_review(db, first)
        assert review_reasons(db, [first])[first.id] == {"ocr_warning"}
        assert first.needs_review and first.status == CandidateStatus.needs_review

        assert resolve_document_review_flag(db, second, actor_id="staff", attachment_id="second-file", document_job_id="second-job")
        assert clear_resolved_document_review(db, second)
        assert review_reasons(db, [second])[second.id] == set()
        assert not second.needs_review and second.status == CandidateStatus.new
        db.commit()


def test_legacy_identity_rejection_without_a_job_scope_stays_blocking(exports):
    """The new resolution query must not accidentally accept old audit evidence."""
    env = exports
    add_file(env, "legacy-wrong", "resume")
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        db.add(AuditEntry(action=IDENTITY_REJECTED_ACTION, target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                          meta={"attachment_id": "legacy-wrong"}))
        db.commit()
    resume = form(env)["documents"]["resume"]
    assert resume["identityMismatch"] and not resume["uploaded"]
