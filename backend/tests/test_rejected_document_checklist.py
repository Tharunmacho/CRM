"""Wrong-person evidence stays reviewable but cannot satisfy an accepted document checklist."""
from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app.api.routers import candidates, travelled
from app.db.models import Attachment, AuditEntry, Candidate, CandidateStatus, DocumentExtractionJob
from app.services import application_form, candidate_search, form_extraction as fx, manual_extraction
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


def _partial_resume_state(*, address: str, local_anchor: bool, overlap: bool = False):
    resume_pages = [3, 4]
    passport_pages = [4] if overlap else [5]
    resume = {
        "name": "Alex Example",
        "contact": {"phones": ["9000000000"], "address": address},
        "personal_info": {"father_name": "Suresh Example", "marital_status": "Married",
                          "languages_known": ["English", "Tamil"]},
        # This copied number is deliberately wrong. It must stay in raw OCR for
        # audit, but it must never touch a trusted passport field.
        "passport_details": {"passport_number": "ZZ999999", "place_of_issue": "Wrong place"},
        "education": [{"degree": "Diploma Mechanical", "year": "2010"}],
        "experience": [{"company": "Example Engineering", "title": "Fitter", "duration_months": 60}],
    }
    passport = {"mrz": {"passport_number": "ZZ999999", "given_names": "OTHER", "surname": "PERSON"}}
    output = {
        "status": "needs_review",
        "inspection": {"resume_pages": resume_pages, "passport_pages": passport_pages, "unknown_pages": [1, 2]},
        "coverage": {"complete": False, "issues": ["Pages 1-2 need review"]},
        "warnings": ["Pages 1-2 could not be classified."],
        "extractions": {
            "resume": {"fields": {"name": "Alex Example", "passport_no": "ZZ999999"},
                       "raw": resume, "selected_pages": resume_pages},
            "passport": {"fields": {"name": "Other Person", "passport_no": "ZZ999999"},
                         "raw": passport, "selected_pages": passport_pages},
        },
    }
    state = {
        "output": output,
        "documents": {
            "resume": {"object_key": "test/derived-resume.pdf", "file_name": "resume.pdf",
                       "content_type": "application/pdf", "size_kb": 1, "content_hash": "derived-resume-hash",
                       "selected_pages": resume_pages},
            "passport": {"object_key": "test/weak-passport.pdf", "file_name": "passport.pdf",
                         "content_type": "application/pdf", "size_kb": 1, "content_hash": "weak-passport-hash",
                         "selected_pages": passport_pages},
        },
    }
    if local_anchor:
        state["local_patterns"] = {"passport": {"number_ok": True, "mrz_checksums_valid": True,
                                                   "passport_number": "AA145638"}}
    return state


def _install_partial_resume_packet(env, *, local_anchor: bool, address: str, overlap: bool = False,
                                   trusted_anchor: bool | None = None):
    source_key = add_file(env, "mixed-source", "resume", data=b"mixed source")
    trusted_anchor = not local_anchor if trusted_anchor is None else trusted_anchor
    trusted_key = None
    if trusted_anchor:
        trusted_key = add_file(env, "trusted-passport", "passport", data=b"trusted passport")
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.passport_no = "AA145638"
        candidate.passport_place = "Trusted passport location"
        candidate.current_address = "Trusted passport address" if address != "[illegible] scan" else None
        if candidate.current_address:
            candidate.field_sources = {"personal.currentAddress": "extracted"}
        candidate.needs_review = True
        source = db.get(Attachment, "mixed-source")
        source.content_hash = "source-hash"
        job = DocumentExtractionJob(
            id="partial-resume-job", owner_id="staff", group_id="partial-group", kind="resume",
            filename="mixed-resume.pdf", content_type="application/pdf", object_key=source_key,
            content_hash="source-hash", size_kb=1, status="needs_review", candidate_id=candidate.id,
            attachment_id=source.id, last_error=IDENTITY_REVIEW_MESSAGE,
            draft={"applicationForm": {"candidateId": candidate.id, "slot": "resume", "attachmentId": source.id}},
            state=_partial_resume_state(address=address, local_anchor=local_anchor, overlap=overlap),
        )
        db.add(job)
        db.add(AuditEntry(action=IDENTITY_REJECTED_ACTION, target=candidate.name, entity_type="candidate",
                          entity_id=candidate.id, meta={"attachment_id": source.id, "document_job_id": job.id,
                                                        "conflicting_fields": ["passport_no"]}))
        record_review_flag(db, candidate, ocr_warning=True)  # historic, unscoped warning must remain
        if trusted_key:
            trusted = db.get(Attachment, "trusted-passport")
            trusted.content_hash = "trusted-hash"
            db.add(DocumentExtractionJob(
                id="trusted-passport-job", owner_id="staff", group_id="trusted-group", kind="passport",
                filename="passport.pdf", content_type="application/pdf", object_key=trusted_key,
                content_hash="trusted-hash", size_kb=1, status="completed", candidate_id=candidate.id,
                attachment_id=trusted.id,
                draft={"applicationForm": {"candidateId": candidate.id, "slot": "passport", "attachmentId": trusted.id}},
                state={"output": {"status": "complete", "extractions": {"passport": {
                    "raw": {"mrz": {"passport_number": "AA145638", "given_names": "ALEX", "surname": "EXAMPLE"}}
                }}}},
            ))
        db.commit()
    return source_key


@pytest.mark.parametrize(
    ("local_anchor", "address"),
    [(True, "Short resume address"), (False, "[illegible] scan")],
)
def test_partial_mixed_resume_replay_applies_only_verified_resume_and_keeps_review(exports, local_anchor, address):
    """A partial replay never adopts weak passport pages, overwrites data, or clears generic review."""
    env = exports
    source_key = _install_partial_resume_packet(env, local_anchor=local_anchor, address=address)
    with env.sessions() as db:
        original_state = deepcopy(db.get(DocumentExtractionJob, "partial-resume-job").state)
        assert application_form.replay_saved_partial_resume(db, candidate_id="candidate", job_id="partial-resume-job")
        db.commit()

    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        job = db.get(DocumentExtractionJob, "partial-resume-job")
        source = db.get(Attachment, "mixed-source")
        resumes = list(db.scalars(select(Attachment).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == "resume")))
        passports = list(db.scalars(select(Attachment).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == "passport")))
        audits = list(db.scalars(select(AuditEntry).where(
            AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate.id)))

        assert job.status == "needs_review" and job.last_error == application_form._PARTIAL_RESUME_REPLAY_MESSAGE
        assert job.state == original_state  # Full source output and unknown pages remain available for review.
        assert job.draft["applicationForm"]["result"]["status"] == "needs_review"
        assert job.draft["applicationForm"]["attachedDocuments"].keys() == {"resume"}
        assert source.slot == "source_bundle" and source.object_key == source_key and source.content_hash == "source-hash"
        assert len(resumes) == 1 and resumes[0].content_hash == "derived-resume-hash"
        assert all(row.content_hash != "weak-passport-hash" for row in passports)
        assert candidate.passport_no == "AA145638" and candidate.passport_place == "Trusted passport location"
        assert candidate.father_name == "Suresh Example" and candidate.marital_status == "Married"
        assert candidate.education and candidate.work_history and candidate.total_experience == 5
        assert "passport" not in candidate.ocr_data and "resume" in candidate.ocr_data
        assert candidate.ocr_data["resume"]["passport_details"]["passport_number"] == "ZZ999999"
        ignored = fx.ignored_fields_for(candidate.ocr_data, "resume", candidate.ocr_data["resume"])
        assert fx.PASSPORT_DOCUMENT_COLUMNS <= ignored
        if address == "[illegible] scan":
            assert candidate.current_address is None and "current_address" in ignored
        else:
            assert candidate.current_address == "Trusted passport address" and "current_address" in ignored
        # A later stored-OCR refresh must retain the raw audit text but never
        # revive the copied passport fields or the unreadable address.
        fx.apply_stored_ocr(candidate, db, force=True)
        assert candidate.passport_no == "AA145638" and candidate.passport_place == "Trusted passport location"
        if address == "[illegible] scan":
            assert candidate.current_address is None
        else:
            assert candidate.current_address == "Trusted passport address"
        assert candidate.needs_review and review_reasons(db, [candidate])[candidate.id] == {"ocr_warning"}
        assert any(row.action == IDENTITY_RESOLVED_ACTION and row.meta == {
            "attachment_id": "mixed-source", "document_job_id": "partial-resume-job",
            "resolves": IDENTITY_REJECTED_ACTION} for row in audits)
        assert not any(row.action == "Resolved document review flag" for row in audits)
        assert any(row.action == "Retained mixed upload as source bundle" for row in audits)
    accepted = form(env)["documents"]["resume"]
    assert accepted["uploaded"] and not accepted["identityMismatch"]
    assert accepted["current"]["attachmentId"] == resumes[0].id


@pytest.mark.parametrize("invalid_pages", ("overlap", "selected_mismatch"))
def test_partial_mixed_resume_replay_rejects_inconsistent_pages_without_changes(exports, invalid_pages):
    """A shared or divergent page set is never safe enough for the maintenance exception."""
    env = exports
    _install_partial_resume_packet(env, local_anchor=True, address="Short resume address", overlap=invalid_pages == "overlap")
    with env.sessions() as db:
        if invalid_pages == "selected_mismatch":
            job = db.get(DocumentExtractionJob, "partial-resume-job")
            state = deepcopy(job.state)
            state["output"]["extractions"]["resume"]["selected_pages"] = [3]
            job.state = state
            db.commit()
        assert not application_form.replay_saved_partial_resume(db, candidate_id="candidate", job_id="partial-resume-job")
        candidate = db.get(Candidate, "candidate")
        source = db.get(Attachment, "mixed-source")
        job = db.get(DocumentExtractionJob, "partial-resume-job")
        audits = list(db.scalars(select(AuditEntry).where(
            AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate.id)))
        assert source.slot == "resume" and candidate.father_name is None
        assert job.last_error == IDENTITY_REVIEW_MESSAGE
        assert not any(row.action == IDENTITY_RESOLVED_ACTION for row in audits)


@pytest.mark.parametrize("reason", ("no_anchor", "wrong_audit_scope"))
def test_partial_mixed_resume_replay_requires_its_own_anchor_and_rejection(exports, reason):
    """A generic warning, another attachment, or another job can never authorize this repair."""
    env = exports
    _install_partial_resume_packet(env, local_anchor=reason != "no_anchor", address="Short resume address",
                                   trusted_anchor=False)
    with env.sessions() as db:
        if reason == "wrong_audit_scope":
            rejected = db.scalar(select(AuditEntry).where(
                AuditEntry.entity_type == "candidate", AuditEntry.entity_id == "candidate",
                AuditEntry.action == IDENTITY_REJECTED_ACTION))
            rejected.meta = {**rejected.meta, "document_job_id": "a-different-job"}
            db.commit()
        assert not application_form.replay_saved_partial_resume(db, candidate_id="candidate", job_id="partial-resume-job")
        candidate = db.get(Candidate, "candidate")
        source = db.get(Attachment, "mixed-source")
        assert source.slot == "resume" and candidate.father_name is None and candidate.ocr_data is None
