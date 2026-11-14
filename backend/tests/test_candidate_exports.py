"""Candidate inspection exports use only isolated PostgreSQL fixtures and local synthetic files."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import PurePosixPath
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from sqlalchemy import event, select

from app.api.routers import candidate_exports as routes
from app.db.models import Application, ApplicationFeedback, ApplicationStage, ApplicationStatus, Attachment, Candidate, Remark, Role, Task, TaskKind
from app.services import candidate_exports as subject
from app.services.application_form import required_for_deployment
from app.services.candidate_search import Search, export_candidates
from test_application_form import as_user, desk  # noqa: F401
from test_database_naming import schema_database  # noqa: F401


@pytest.fixture
def exports(desk, monkeypatch):
    desk.client.app.include_router(routes.router)
    monkeypatch.setattr(subject, "get_settings", lambda: desk.settings)
    return desk


def add_file(env, identifier, slot, *, version=1, entity_type="candidate", entity_id="candidate", name="scan.pdf", data=b"synthetic document", time=None, store=True):
    key = f"test/{identifier}.pdf"
    if store:
        target = env.storage / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with env.sessions() as db:
        row = Attachment(id=identifier, entity_type=entity_type, entity_id=entity_id, slot=slot, version=version,
                         file_name=name, size_kb=max(1, len(data) // 1024), content_type="application/pdf", object_key=key,
                         uploaded_by="staff", uploaded_at=time or datetime.now(timezone.utc))
        db.add(row)
        db.commit()
    return key


def add_app(env, identifier, *, candidate="candidate", order="order", stage=5, status=ApplicationStatus.active):
    with env.sessions() as db:
        db.add(Application(id=identifier, candidate_id=candidate, order_id=order, owner_id="staff", current_stage=stage, status=status))
        db.commit()


def get(env, suffix="inspection.xlsx", **params):
    return env.client.get(f"/api/candidates/candidate/{suffix}", params=params)


def sheets(payload):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(payload)) as archive:
        names = [row.attrib["name"] for row in ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)]
        result = {}
        for index, name in enumerate(names, 1):
            tree = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            assert tree.findall(".//s:f", ns) == [], "User-supplied text must never become an Excel formula"
            result[name] = [["".join(cell.itertext()) for cell in row] for row in tree.findall("s:sheetData/s:row", ns)]
        return result


def test_inspection_contains_full_sections_true_missing_checklist_and_safe_excel_text(exports):
    add_app(exports, "shown", stage=5)
    with exports.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name = '=HYPERLINK("https://invalid.test","A&B <C>")'
        candidate.current_address = "Address\x01 with <angle> & ampersand"
        candidate.education = [{"level": "Diploma", "passedYear": 2010, "institute": "Institute & College"}]
        candidate.work_history = [{"company": "Former Employer", "designation": "Welder", "country": "UAE", "period": "2020-2025"}]
        candidate.resume_text = "Resume line " * 6000
        db.add(ApplicationStage(application_id="shown", stage=1, attempt=1, outcome="Passed", completed_by="staff",
                                data={"answer": "=1+1", "object_key": "private-storage-key", "nested": {"storageKey": "do-not-export", "note": "A&B"}}))
        db.commit()
    add_file(exports, "resume", "resume")
    response = get(exports, applicationId="shown")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    workbook = sheets(response.content)
    assert {"Personal", "Contact", "Source and recruiter", "Passport", "Education", "Work history", "Screening", "Placements", "Stage history", "Document checklist", "Document manifest", "Resume text"} <= set(workbook)
    all_cells = [cell for rows in workbook.values() for row in rows for cell in row]
    assert '=HYPERLINK("https://invalid.test","A&B <C>")' in all_cells
    assert "Address with <angle> & ampersand" in all_cells
    assert "Former Employer" in all_cells and "Institute & College" in all_cells
    assert "private-storage-key" not in all_cells and "do-not-export" not in all_cells
    assert "Invoices" not in workbook
    assert "".join(row[1] for row in workbook["Resume text"][1:]) == "Resume line " * 6000
    checklist = {row[0]: row for row in workbook["Document checklist"][1:]}
    assert sum(row[1] == "True" for row in checklist.values()) == 10  # the recruiter's checklist: the Invoice is the admin's alone
    assert "Invoice" not in checklist
    assert checklist["Resume"][4] == "Uploaded"
    assert checklist["Application form"][4] == "Optional"
    assert checklist["Signed offer"][4] == "Missing"
    assert checklist["Medical"][4] == "Needed later"
    assert checklist["Education certificate"][4] == "Optional"


def test_placement_invoice_is_visible_only_in_its_form_and_checklist(exports):
    as_user(exports, "admin", Role.admin)  # invoices are the admin's alone (test_invoice_access.py)
    add_app(exports, "shown", stage=11)
    add_app(exports, "history", order="other-order", status=ApplicationStatus.closed)
    add_file(exports, "placement-invoice", "invoice", entity_type="application", entity_id="shown")
    for app_id, expected in (("shown", True), ("history", False)):
        response = exports.client.get("/api/candidates/candidate/application-form", params={"applicationId": app_id})
        assert response.status_code == 200, response.text
        form = response.json()
        assert form["documents"]["invoice"]["uploaded"] is expected
        assert next(row for row in form["tracking"]["rows"] if row["key"] == "invoice_generated")["checked"] is expected
        archive_response = get(exports, "documents.zip", applicationId=app_id)
        assert archive_response.status_code == 200, archive_response.text
        with ZipFile(BytesIO(archive_response.content)) as archive:
            listing = json.loads(archive.read("manifest.json"))
            assert next(row for row in listing["checklist"] if row["slot"] == "invoice")["uploaded"] is expected
            assert any(row["attachmentId"] == "placement-invoice" and row["includedInZip"] for row in listing["documents"])


def test_placement_invoice_takes_precedence_over_newer_generic_upload(exports):
    as_user(exports, "admin", Role.admin)
    add_app(exports, "shown", stage=11)
    add_file(exports, "placement-invoice", "invoice", entity_type="application", entity_id="shown", time=datetime.now(timezone.utc) - timedelta(days=1))
    add_file(exports, "generic-invoice", "invoice")
    response = exports.client.get("/api/candidates/candidate/application-form", params={"applicationId": "shown"})
    assert response.status_code == 200, response.text
    assert response.json()["documents"]["invoice"]["current"]["attachmentId"] == "placement-invoice"


def test_zip_includes_latest_normal_all_other_all_invoice_and_every_own_application(exports):
    as_user(exports, "admin", Role.admin)
    old = datetime.now(timezone.utc) - timedelta(days=1)
    add_app(exports, "shown", stage=11)
    add_app(exports, "history", order="other-order", status=ApplicationStatus.closed)
    add_app(exports, "foreign", candidate="second-candidate")
    add_file(exports, "resume-old", "resume", version=1, time=old, data=b"old resume")
    add_file(exports, "resume-new", "resume", version=2, name="../../..\\CON <resume>.pdf", data=b"current resume")
    add_file(exports, "other-1", "other", version=1, name="same.pdf", data=b"other one")
    add_file(exports, "other-2", "other", version=2, name="same.pdf", data=b"other two")
    add_file(exports, "invoice-1", "invoice", version=1, data=b"original invoice")
    add_file(exports, "invoice-2", "invoice", version=2, data=b"later invoice")
    add_file(exports, "receipt-old", "receipt", entity_type="application", entity_id="shown", version=1, time=old)
    add_file(exports, "receipt-new", "receipt", entity_type="application", entity_id="shown", version=2, data=b"receipt")
    add_file(exports, "historical-ticket", "ticket", entity_type="application", entity_id="history", data=b"history")
    add_file(exports, "foreign-candidate", "passport", entity_id="second-candidate", data=b"foreign")
    add_file(exports, "foreign-application", "ticket", entity_type="application", entity_id="foreign", data=b"foreign")
    response = get(exports, "documents.zip", applicationId="shown")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(name.casefold() for name in names))
        assert all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts and "\\" not in name for name in names)
        assert {"inspection.xlsx", "manifest.json"} <= set(names)
        listing = json.loads(archive.read("manifest.json"))
        docs = {row["attachmentId"]: row for row in listing["documents"]}
        assert "foreign-candidate" not in docs and "foreign-application" not in docs
        assert not docs["resume-old"]["includedInZip"] and not docs["receipt-old"]["includedInZip"]
        for identifier in ("resume-new", "other-1", "other-2", "invoice-1", "invoice-2", "receipt-new", "historical-ticket"):
            assert docs[identifier]["includedInZip"]
            assert docs[identifier]["archivePath"] in names
        assert archive.read(docs["resume-new"]["archivePath"]) == b"current resume"
        assert docs["historical-ticket"]["applicationId"] == "history"
        assert docs["historical-ticket"]["orderId"] == "other-order"
        assert not any("object_key" in row or "objectKey" in row for row in docs.values())
        assert "test/resume-new.pdf" not in json.dumps(listing)
        assert "passport" in {row["slot"] for row in listing["missingRequiredBeforeDeployment"]}
        assert "invoice" not in {row["slot"] for row in listing["missingRequiredBeforeDeployment"]}
        assert sheets(archive.read("inspection.xlsx"))["Document checklist"]


def test_context_cannot_select_another_candidates_application_or_unrelated_order(exports):
    add_app(exports, "foreign", candidate="second-candidate")
    for suffix in ("inspection.xlsx", "documents.zip"):
        assert get(exports, suffix, applicationId="foreign").status_code == 404
        assert get(exports, suffix, orderId="other-order").status_code == 404
    assert exports.client.get("/api/candidates/missing/inspection.xlsx").status_code == 404


def test_all_identity_files_and_optional_application_form_are_kept_in_export(exports):
    for slot in ("passport", "aadhaar"):
        for version in (1, 2):
            add_file(exports, f"{slot}-{version}", slot, version=version, data=f"{slot} page {version}".encode())
    add_file(exports, "saved-application-form", "application_form")
    response = get(exports, "documents.zip")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        listing = json.loads(archive.read("manifest.json"))
        assert all(row["includedInZip"] for row in listing["documents"])
        assert len(listing["documents"]) == 5
        checklist = {row["slot"]: row for row in listing["checklist"]}
        for slot in ("aadhaar", "application_form"):
            assert not checklist[slot]["mandatoryBeforeDeployment"] and not checklist[slot]["requiredNow"]
        assert checklist["application_form"]["status"] == "Uploaded"


def test_legacy_alias_timestamp_ties_keep_checklist_current_file_in_zip(exports):
    at = datetime.now(timezone.utc)
    add_file(exports, "alias-a", "education", time=at, name="legacy.pdf", data=b"legacy")
    add_file(exports, "alias-z", "education_certificate", time=at, name="current.pdf", data=b"current")
    response = get(exports, "documents.zip")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        listing = json.loads(archive.read("manifest.json"))
        current = next(row["current"] for row in listing["checklist"] if row["slot"] == "education_certificate")
        included = [row for row in listing["documents"] if row["slot"] == "education_certificate" and row["includedInZip"]]
        assert len(included) == 1 and included[0]["attachmentId"] == current["attachmentId"]
        assert archive.read(included[0]["archivePath"])


def test_downloads_require_a_signed_in_session(exports):
    exports.client.app.dependency_overrides.pop(routes.current_user)
    assert get(exports).status_code == 401
    assert get(exports, "documents.zip").status_code == 401


def test_full_exports_preserve_stage_and_application_file_ownership(exports):
    add_app(exports, "shown")
    add_file(exports, "stage-file", "receipt", entity_type="application", entity_id="shown")
    as_user(exports, "other", Role.staff)
    assert get(exports).status_code == 403
    assert get(exports, "documents.zip").status_code == 403
    as_user(exports, "manager", Role.manager)
    assert get(exports).status_code == 200
    assert get(exports, "documents.zip").status_code == 200


def test_full_export_ownership_is_enforced_without_application_attachments(exports):
    # A workbook's private stage answers must not become public merely because
    # this candidate has no stage file; the owner has access in both cases.
    add_app(exports, "shown")
    assert get(exports).status_code == 200
    assert get(exports, "documents.zip").status_code == 200
    as_user(exports, "other", Role.staff)
    assert get(exports).status_code == 403
    assert get(exports, "documents.zip").status_code == 403


def test_missing_object_fails_before_zip_response_and_closes_temporary_file(exports, monkeypatch):
    add_file(exports, "missing", "passport", store=False)
    created = []
    original = subject.SpooledTemporaryFile
    def track(*args, **kwargs):
        spool = original(*args, **kwargs)
        created.append(spool)
        return spool
    monkeypatch.setattr(subject, "SpooledTemporaryFile", track)
    response = get(exports, "documents.zip")
    assert response.status_code == 503
    assert "package could not be completed" in response.json()["detail"]
    assert "passport" not in response.headers.get("content-disposition", "")
    assert created and all(spool.closed for spool in created)


def test_package_streams_bounded_reads_rolls_to_disk_and_closes_after_response(exports, monkeypatch):
    add_file(exports, "large", "resume", data=b"x" * 10000)
    created = []
    original = subject.SpooledTemporaryFile
    def track(*args, **kwargs):
        kwargs["max_size"] = 1
        spool = original(*args, **kwargs)
        created.append(spool)
        return spool
    monkeypatch.setattr(subject, "SpooledTemporaryFile", track)
    response = get(exports, "documents.zip")
    assert response.status_code == 200
    assert created and all(spool._rolled and spool.closed for spool in created)
    monkeypatch.setattr(subject, "MAX_FILE_BYTES", 100)
    response = get(exports, "documents.zip")
    assert response.status_code == 413
    assert all(spool.closed for spool in created)


def test_s3_download_reads_fixed_chunks_and_closes_the_body(exports, monkeypatch):
    add_file(exports, "remote", "passport", store=False)
    with exports.sessions() as db:
        db.get(Attachment, "remote").object_key = "s3://synthetic/candidate-passport"
        db.commit()
    calls = []
    class Body(BytesIO):
        def read(self, size=-1):
            assert size == subject.CHUNK_BYTES
            calls.append(size)
            return super().read(size)
    body = Body(b"remote document" * 10000)
    client = SimpleNamespace(get_object=lambda **_kwargs: {"Body": body})
    monkeypatch.setattr(subject.document_storage, "_s3", lambda settings: client)
    response = get(exports, "documents.zip")
    assert response.status_code == 200 and body.closed and len(calls) > 2


def test_corrupt_local_storage_path_cannot_escape_storage(exports):
    add_file(exports, "unsafe", "resume", store=False)
    with exports.sessions() as db:
        db.get(Attachment, "unsafe").object_key = "../outside.pdf"
        db.commit()
    response = get(exports, "documents.zip")
    assert response.status_code == 503
    assert "../outside" not in response.text


def test_data_bank_export_has_twelve_shared_document_columns_in_one_metadata_query(exports):
    add_file(exports, "resume", "resume")
    add_file(exports, "invoice", "invoice")
    # A stage file must not satisfy the candidate's mandatory document slot.
    add_app(exports, "shown")
    add_file(exports, "app-passport", "passport", entity_type="application", entity_id="shown")
    queries = []
    engine = exports.sessions.kw["bind"]
    def observe(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
    event.listen(engine, "before_cursor_execute", observe)
    try:
        with exports.sessions() as db:
            payload = export_candidates(db, Search())
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    rows = sheets(payload)["Candidates"]
    headings = rows[0]
    assert headings[-12:-1] == [label + " document" for _slot, label in required_for_deployment()]
    person = next(row for row in rows[1:] if row[headings.index("CRM reference")] == "candidate")
    assert person[headings.index("Resume document")] == "Uploaded"
    assert person[headings.index("Invoice document")] == "Uploaded"
    assert person[headings.index("Passport document")] == "Missing"
    assert "Passport" in person[-1] and "Resume" not in person[-1]
    assert len([statement for statement in queries if "document_attachments" in statement]) == 1


def test_data_bank_export_names_agency_and_the_actual_profile_issue(exports):
    with exports.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.phone = ""
        candidate.needs_review = True
        candidate.source_detail = "Bulk import"
        db.commit()
        rows = sheets(export_candidates(db, Search()))["Candidates"]
        headings = rows[0]
        person = next(row for row in rows[1:] if row[headings.index("CRM reference")] == "candidate")
        assert person[headings.index("Agency")] == ""
        assert person[headings.index("Profile review issues")] == "Phone number missing"
        candidate.source_detail = "Example Referral Agency"
        db.commit()
        rows = sheets(export_candidates(db, Search()))["Candidates"]
        person = next(row for row in rows[1:] if row[headings.index("CRM reference")] == "candidate")
        assert person[headings.index("Agency")] == "Example Referral Agency"


def test_exports_keep_exact_reviewed_source_wording(exports):
    with exports.sessions() as db:
        db.get(Candidate, "candidate").source_claims = {"experienceIndia": "1.5 years", "expectedSalary": "Negotiable"}
        db.commit()
        bank = sheets(export_candidates(db, Search()))["Candidates"]
        headings = bank[0]
        person = next(row for row in bank[1:] if row[headings.index("CRM reference")] == "candidate")
        assert person[headings.index("Exact source wording")] == "expectedSalary: Negotiable; experienceIndia: 1.5 years"
    response = get(exports)
    assert response.status_code == 200, response.text
    assert sheets(response.content)["Reviewed source wording"][1:] == [["expectedSalary", "Negotiable"], ["experienceIndia", "1.5 years"]]


def test_full_history_retains_more_than_fifty_notes_saved_drafts_and_scoped_task_notes(exports):
    add_app(exports, "shown", stage=5)
    now = datetime.now(timezone.utc)
    with exports.sessions() as db:
        application = db.get(Application, "shown")
        application.draft = {"stage": 5, "data": {"salary": "1500", "comment": "Offer reviewed", "objectKey": "private-draft-key"}}
        application.draft_saved_at = now
        application.draft_saved_by = "manager"
        db.add_all([ApplicationFeedback(id=f"note-{index:03}", candidate_id="candidate", application_id="shown",
                    author_id="staff", kind="update", stage=5, body=f"Candidate progress {index}",
                    created_at=now + timedelta(seconds=index), updated_at=now + timedelta(seconds=index)) for index in range(55)])
        db.add(ApplicationFeedback(id="foreign-note", candidate_id="second-candidate", author_id="other", kind="update", body="Foreign feedback"))
        for identifier, owner, candidate_id in (("mine", "staff", "candidate"), ("previous", "other", "candidate"), ("foreign", "staff", "second-candidate")):
            db.add(Task(id=identifier, candidate_id=candidate_id, owner_id=owner, kind=TaskKind.initial_call, title=identifier))
        db.flush()
        db.add_all([Remark(id=identifier + "-remark", task_id=identifier, author_id=owner, body=body) for identifier, owner, body in
                    (("mine", "staff", "My candidate task note"), ("previous", "other", "Other recruiter task note"), ("foreign", "staff", "Foreign candidate task note"))])
        db.commit()
    response = get(exports, applicationId="shown")
    assert response.status_code == 200, response.text
    book = sheets(response.content)
    assert len(book["Recruiter notes"]) == 56
    assert {row[5] for row in book["Recruiter notes"][1:]} == {f"Candidate progress {index}" for index in range(55)}
    assert {row[0] for row in book["Tasks"][1:]} == {"mine"}
    assert [row[2] for row in book["Task notes"][1:]] == ["My candidate task note"]
    draft = book["Saved stage drafts"]
    assert any(row[3] == "Manager" and row[4:] == ["data / salary", "1500"] for row in draft[1:])
    assert "private-draft-key" not in str(book)
    assert "Foreign feedback" not in str(book) and "Foreign candidate task note" not in str(book)
    as_user(exports, "manager", Role.manager)
    manager_book = sheets(get(exports).content)
    assert {row[0] for row in manager_book["Tasks"][1:]} == {"mine", "previous"}
    assert {row[2] for row in manager_book["Task notes"][1:]} == {"My candidate task note", "Other recruiter task note"}


def test_history_limit_fails_explicitly_instead_of_truncating_export(exports, monkeypatch):
    with exports.sessions() as db:
        db.add_all([ApplicationFeedback(candidate_id="candidate", author_id="staff", kind="update", body=f"Note {index}") for index in range(4)])
        db.commit()
    monkeypatch.setattr(subject, "MAX_HISTORY_ROWS", 3)
    for suffix in ("inspection.xlsx", "documents.zip"):
        response = get(exports, suffix)
        assert response.status_code == 413
        assert "no records were omitted" in response.json()["detail"]
