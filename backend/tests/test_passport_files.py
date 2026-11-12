"""Passport front/back uploads remain separate and their reading results stay visible."""
from sqlalchemy import select, func

from app.db.models import Attachment, DocumentExtractionJob
from app.services import manual_extraction
from test_application_form import desk, form, url, upload, pdf, PASSPORT
from test_database_naming import schema_database
from test_ocr_classification import AADHAAR, AADHAAR_BACK


def uploaded_pages(desk, *, profile=False, deferred=False, slot="passport"):
    contents = (pdf(AADHAAR), pdf(AADHAAR_BACK)) if slot == "aadhaar" else (pdf(PASSPORT + "\nFront page synthetic"), pdf(PASSPORT + "\nBack page synthetic"))
    files = []
    for side, data in zip(("front", "back"), contents):
        if profile:
            response = desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": slot, "defer_extraction": str(deferred).lower()},
                                        files={"file": (f"passport-{side}.pdf", data, "application/pdf")})
            assert response.status_code == 201, response.text
            files.append(response.json())
        else:
            response = desk.client.post(url() + "/documents", data={"slot": slot, "defer_extraction": str(deferred).lower()},
                                        files={"file": (f"passport-{side}.pdf", data, "application/pdf")})
            assert response.status_code == 202, response.text
            current = response.json()["document"]["current"]
            files.append({**current, "id": current["attachmentId"]})
    return files, contents


def jobs_by_file(desk):
    with desk.sessions() as db:
        return {job.attachment_id: job.id for job in db.scalars(select(DocumentExtractionJob))}


def test_passport_front_and_back_are_queued_and_fill_complementary_fields(desk):
    assert desk.client.put(url(), json={"personal": {"fatherName": "Typed Parent"},
                                       "passport": {"placeOfIssue": "Typed Place"}}).status_code == 200
    files, _ = uploaded_pages(desk, deferred=True)
    stored = form(desk)
    assert stored["processing"] is False and stored["passport"]["passportNumber"] is None
    assert stored["capabilities"]["deferredDocumentReading"] is True
    assert all(file["extractionPending"] and file["extraction"] is None for file in stored["documents"]["passport"]["files"])
    assert jobs_by_file(desk) == {}
    started = desk.client.post(url() + "/extract", json={"attachmentIds": [file["id"] for file in files]})
    assert started.status_code == 202 and started.json()["queued"] == 2
    repeated = desk.client.post(url() + "/extract", json={"attachmentIds": [file["id"] for file in files]})
    assert repeated.status_code == 202 and repeated.json()["queued"] == 0 and repeated.json()["existing"] == 2
    document = form(desk)["documents"]["passport"]
    assert document["multiple"] is True and document["versions"] == 2
    assert {file["attachmentId"] for file in document["files"]} == {file["id"] for file in files}
    assert all(file["extraction"]["status"] == "processing" for file in document["files"])
    jobs = jobs_by_file(desk)
    assert set(jobs) == {file["id"] for file in files}

    desk.extra = {"passport": {"mrz": {"given_names": "ALEX", "surname": "EXAMPLE", "passport_number": "AB123456",
                                        "date_of_birth": "1990-01-01", "expiry_date": "2030-01-01",
                                        "all_check_digits_valid": True}}}
    manual_extraction.process_job(jobs[files[0]["id"]], sessions=desk.sessions, settings=desk.settings)
    front = form(desk)
    assert front["passport"]["passportNumber"] == "AB123456"
    assert front["documents"]["passport"]["extraction"]["status"] == "processing"

    desk.results.clear()
    desk.extra = {"passport": {"mrz": {}, "fields": [
        {"label": "Name of Father", "value": "Scanned Parent", "page": 1, "category": "identity", "source": "ocr", "confidence": .95},
        {"label": "Address", "value": "12 Synthetic Road, Chennai", "page": 1, "category": "identity", "source": "ocr", "confidence": .95},
        {"label": "Place of Issue", "value": "Scanned Place", "page": 1, "category": "identity", "source": "ocr", "confidence": .95},
    ]}}
    manual_extraction.process_job(jobs[files[1]["id"]], sessions=desk.sessions, settings=desk.settings)
    result = form(desk)
    with desk.sessions() as db:
        last = db.get(DocumentExtractionJob, jobs[files[1]["id"]])
        assert last.status == "completed", {"status": last.status, "error": last.last_error, "draft": last.draft}
    assert result["passport"]["passportNumber"] == "AB123456"
    assert result["passport"]["expiryDate"] == "2030-01-01"
    assert result["personal"]["dateOfBirth"] == "1990-01-01"
    assert result["personal"]["currentAddress"] == "12 Synthetic Road, Chennai", result["documents"]["passport"]
    assert result["personal"]["fatherName"] == "Typed Parent"
    assert result["passport"]["placeOfIssue"] == "Typed Place"
    assert all(file["extraction"]["status"] == "done" for file in result["documents"]["passport"]["files"])
    combined = result["documents"]["passport"]["extraction"]
    assert combined["status"] == "done"
    assert {"passport.passportNumber", "personal.currentAddress"} <= set(combined["filled"])
    assert {"personal.fatherName", "passport.placeOfIssue"} <= set(combined["kept"])


def test_failed_front_page_stays_visible_after_back_page_finishes(desk):
    files, _ = uploaded_pages(desk)
    jobs = jobs_by_file(desk)
    with desk.sessions() as db:
        first = db.get(DocumentExtractionJob, jobs[files[0]["id"]])
        first.status, first.attempts, first.last_error = "failed", 4, "Front page could not be read."
        second = db.get(DocumentExtractionJob, jobs[files[1]["id"]])
        second.status = "completed"
        second.draft = {"applicationForm": {"result": {"filled": ["personal.currentAddress"], "kept": [], "kindsFound": ["passport"]}}}
        db.commit()
    document = form(desk)["documents"]["passport"]
    assert document["extraction"]["status"] == "needs_review"
    results = {file["attachmentId"]: file["extraction"] for file in document["files"]}
    assert results[files[0]["id"]]["status"] == "failed"
    assert "Front page" in results[files[0]["id"]]["message"]
    assert results[files[1]["id"]]["status"] == "done"


import pytest

@pytest.mark.parametrize("profile", [False, True])
def test_retrying_older_front_page_reuses_file_and_job(desk, profile):
    files, contents = uploaded_pages(desk, profile=profile)
    jobs = jobs_by_file(desk)
    with desk.sessions() as db:
        failed = db.get(DocumentExtractionJob, jobs[files[0]["id"]])
        failed.status, failed.attempts, failed.last_error = "failed", 4, "Temporary reader failure"
        db.commit()
    if profile:
        response = desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": "passport"},
                                    files={"file": ("renamed-front.pdf", contents[0], "application/pdf")})
        assert response.status_code == 201 and response.json()["id"] == files[0]["id"]
    else:
        response = upload(desk, "passport", contents[0], "renamed-front.pdf")
        assert response.status_code == 202
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == "candidate")) == 2
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 2
        assert db.get(DocumentExtractionJob, jobs[files[0]["id"]]).status == "pending"
    assert len(form(desk)["documents"]["passport"]["files"]) == 2


@pytest.mark.parametrize("profile", [False, True])
def test_deferred_upload_never_starts_reader_and_failed_start_is_atomic(desk, profile):
    files, _ = uploaded_pages(desk, profile=profile, deferred=True)
    assert jobs_by_file(desk) == {}
    bad = desk.client.post(url() + "/extract", json={"attachmentIds": [files[0]["id"], "not-this-candidate"]})
    assert bad.status_code == 422 and jobs_by_file(desk) == {}
    bad_scope = desk.client.post(url() + "/extract?applicationId=not-this-candidate", json={"attachmentIds": [files[0]["id"]]})
    assert bad_scope.status_code == 404 and jobs_by_file(desk) == {}
    desk.actor.id = "other"
    refused = desk.client.post(url() + "/extract", json={"attachmentIds": [file["id"] for file in files]})
    assert refused.status_code == 403 and jobs_by_file(desk) == {}



def test_profile_and_bootstrap_show_saved_unread_files_until_explicit_start(desk):
    from app.api.routers import candidates
    from app.services.desk_snapshot import read_snapshot
    desk.client.app.include_router(candidates.router)
    files, _ = uploaded_pages(desk, profile=True, deferred=True)
    response = desk.client.get("/api/candidates/candidate")
    assert response.status_code == 200, response.text
    versions = next(slot["versions"] for slot in response.json()["documents"] if slot["key"] == "passport")
    assert len(versions) == 2 and all(file["extractionPending"] for file in versions)
    with desk.sessions() as db:
        snapshot = read_snapshot(db, "staff")
        pending = [file for file in snapshot["files"] if file["entity_id"] == "candidate"]
        assert len(pending) == 2 and all(file["extraction_pending"] for file in pending)
    started = desk.client.post(url() + "/extract", json={"attachmentIds": [files[0]["id"]]})
    assert started.status_code == 202 and started.json()["queued"] == 1
    updated = desk.client.get("/api/candidates/candidate").json()
    flags = {file["id"]: file["extractionPending"] for slot in updated["documents"] for file in slot["versions"]}
    assert flags[files[0]["id"]] is False and flags[files[1]["id"]] is True


def test_older_passport_failure_is_not_hidden_after_twenty_newer_readings(desk):
    from datetime import datetime, timedelta, timezone
    with desk.sessions() as db:
        for n in range(25):
            stamp = datetime.now(timezone.utc) + timedelta(seconds=n)
            attachment = Attachment(id=f"page-{n}", entity_type="candidate", entity_id="candidate", slot="passport",
                                    version=n + 1, file_name=f"passport-page-{n}.pdf", object_key=f"synthetic-{n}", uploaded_at=stamp)
            db.add(attachment)
            db.flush()
            db.add(DocumentExtractionJob(id=f"read-{n}", owner_id="staff", group_id=f"group-{n}", candidate_id="candidate",
                                        attachment_id=attachment.id, kind="passport", filename=attachment.file_name,
                                        content_type="application/pdf", object_key=attachment.object_key, content_hash=f"hash-{n}", size_kb=1,
                                        status="failed" if n == 0 else "completed", last_error="First page needs review" if n == 0 else None,
                                        created_at=stamp))
        db.commit()
    document = form(desk)["documents"]["passport"]
    assert len(document["files"]) == 25
    assert document["extraction"]["status"] == "needs_review"
    first = next(file for file in document["files"] if file["attachmentId"] == "page-0")
    assert first["extraction"]["status"] == "failed"
    assert first["extractionPending"] is True


@pytest.mark.parametrize("profile", [False, True])
def test_aadhaar_front_back_remain_optional_and_wait_for_explicit_start(desk, profile):
    before = form(desk)["completeness"]
    files, contents = uploaded_pages(desk, profile=profile, deferred=True, slot="aadhaar")
    stored = form(desk)
    document = stored["documents"]["aadhaar"]
    assert document["optional"] and not document["required"] and document["multiple"]
    assert len(document["files"]) == 2 and all(file["extractionPending"] for file in document["files"])
    assert stored["aadhaar"] is None and jobs_by_file(desk) == {}
    assert stored["completeness"]["total"] == before["total"]
    path = "/api/files" if profile else url() + "/documents"
    params = {"slot": "aadhaar", "defer_extraction": "true"}
    if profile:
        params.update(entity_type="candidate", entity_id="candidate")
    repeated = desk.client.post(path, data=params, files={"file": ("renamed-front.pdf", contents[0], "application/pdf")})
    assert repeated.status_code in (201, 202)
    assert len(form(desk)["documents"]["aadhaar"]["files"]) == 2 and jobs_by_file(desk) == {}
    started = desk.client.post(url() + "/extract", json={"attachmentIds": [file["id"] for file in files]})
    assert started.status_code == 202 and started.json()["queued"] == 2
    jobs = jobs_by_file(desk)
    desk.extra = {"aadhaar": {"aadhaar": {"name": "ALEX EXAMPLE", "date_of_birth": "1990-01-01", "gender": "male", "masked_aadhaar_number": "XXXX XXXX 4321"}}}
    manual_extraction.process_job(jobs[files[0]["id"]], sessions=desk.sessions, settings=desk.settings)
    desk.results.clear()
    desk.extra = {"aadhaar": {"aadhaar": {"address": "24 Synthetic Road, Chennai", "masked_aadhaar_number": "XXXX XXXX 4321"}}}
    manual_extraction.process_job(jobs[files[1]["id"]], sessions=desk.sessions, settings=desk.settings)
    finished = form(desk)
    assert finished["personal"]["dateOfBirth"] == "1990-01-01"
    assert finished["personal"]["currentAddress"] == "24 Synthetic Road, Chennai"
    assert finished["aadhaar"]["maskedNumber"] == "XXXX XXXX 4321"
    assert all(file["extraction"]["status"] == "done" and not file["extractionPending"] for file in finished["documents"]["aadhaar"]["files"])


def test_deferred_duplicate_failed_file_waits_for_explicit_retry(desk):
    from app.api.routers import candidates
    desk.client.app.include_router(candidates.router)
    files, contents = uploaded_pages(desk, profile=True)
    jobs = jobs_by_file(desk)
    with desk.sessions() as db:
        failed = db.get(DocumentExtractionJob, jobs[files[0]["id"]])
        failed.status, failed.attempts, failed.last_error = "failed", 4, "Temporary reader failure"
        db.commit()
    response = desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": "passport", "defer_extraction": "true"},
                                files={"file": ("front-again.pdf", contents[0], "application/pdf")})
    assert response.status_code == 201 and response.json()["id"] == files[0]["id"]
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, jobs[files[0]["id"]]).status == "failed"
    versions = next(slot["versions"] for slot in desk.client.get("/api/candidates/candidate").json()["documents"] if slot["key"] == "passport")
    assert next(file for file in versions if file["id"] == files[0]["id"])["extractionPending"] is True
    started = desk.client.post(url() + "/extract", json={"attachmentIds": [files[0]["id"]]})
    assert started.status_code == 202 and started.json()["queued"] == 1
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, jobs[files[0]["id"]]).status == "pending"
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 2
