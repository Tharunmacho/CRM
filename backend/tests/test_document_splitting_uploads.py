"""A scan that holds several documents is filed, page by page, in the candidate fields it belongs to, on every upload path.

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL). The pages are synthetic documents with a text layer
(tests/ocr_synthetic.composite_pdf), so no external reader is needed.
"""
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pymupdf as fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob, Role, Source, User
from app.integrations.ocr import inspection
from app.services import bulk_candidate_import as bulk, document_splitting as splitting
from app.services.document_storage import put_bytes, read_bytes
from ocr_synthetic import composite_pdf
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixtures)

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import application_form as form_router, document_jobs, files as files_router


@pytest.fixture
def desk(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("staff", Role.staff), ("other", Role.staff), ("manager", Role.manager))])
        db.flush()
        db.add(Candidate(id="candidate", ref="CND-1", name="Alex Example", phone="9000000001", source=Source.walk_in, owner_id="staff"))
        db.commit()
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None, ocr_languages="eng",
                               agency_timezone="Asia/Kolkata", ocr_max_pages=300, ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600)
    env = SimpleNamespace(sessions=sessions, settings=settings, storage=tmp_path, actor=SimpleNamespace(id="staff", name="Staff", role=Role.staff))
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(files_router, "STORAGE", tmp_path)
    monkeypatch.setattr(files_router, "settings", settings)
    monkeypatch.setattr(bulk, "get_settings", lambda: settings)          # never the real .env
    monkeypatch.setattr(document_jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(form_router.service, "get_settings", lambda: settings)
    app = FastAPI()
    for router in (files_router.router, form_router.router, document_jobs.router):
        app.include_router(router)

    def database():
        with sessions() as db:
            yield db

    for module in (files_router, form_router, document_jobs):
        app.dependency_overrides[module.get_db] = database
        app.dependency_overrides[module.current_user] = lambda: env.actor
    with TestClient(app) as client:
        env.client = client
        yield env


def send(env, data, slot="passport", name="scan.pdf", defer="true", candidate="candidate", kind="application/pdf"):
    return env.client.post("/api/files", data={"entity_type": "candidate", "entity_id": candidate, "slot": slot, "defer_extraction": defer},
                           files={"file": (name, data, kind)})


def rows(env, candidate="candidate"):
    with env.sessions() as db:
        return list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate)
                               .order_by(Attachment.slot, Attachment.version)))


def pdf_of(env, row):
    path = next(env.storage.glob(f"*/{row.object_key.rsplit('/', 1)[-1]}"))
    return path.read_bytes()


def text_of(data):
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count, " ".join(page.get_text() for page in document).lower()


SCAN = ["unrelated", "passport", "aadhaar"]


# ------------------------------------------------------------------ the profile / generic upload endpoint

def test_a_combined_scan_put_in_the_passport_field_is_filed_by_content_and_nothing_is_lost(desk):
    scan = composite_pdf(SCAN)
    response = send(desk, scan, slot="passport", name="Alex all documents.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    pieces = {piece["slot"]: piece for piece in body["split"]["pieces"]}
    assert set(pieces) == {"passport", "aadhaar", "other"}
    assert pieces["passport"]["pages"] == [2] and pieces["aadhaar"]["pages"] == [3] and pieces["other"]["pages"] == [1]
    assert pieces["passport"]["label"] == "Passport" and pieces["passport"]["fileName"] == "Alex_Example-Passport.pdf"
    assert body["split"]["message"] == ("This file held several documents: Passport (page 2), Aadhaar card (page 3) and "
                                       "Other documents (page 1) were filed in their own fields.")
    assert body["name"] == "Alex_Example-Passport.pdf"          # the answer is the file of the field the person chose
    stored = {row.slot: row for row in rows(desk)}
    assert set(stored) == {"passport", "aadhaar", "other", "source_bundle"} and all(row.version == 1 for row in stored.values())
    assert pdf_of(desk, stored["source_bundle"]) == scan and stored["source_bundle"].file_name == "Alex-all-documents.pdf"   # the whole scan is kept as received
    assert stored["source_bundle"].content_hash == hashlib.sha256(scan).hexdigest()
    total = 0
    for slot, wanted in (("passport", "passport"), ("aadhaar", "aadhaar"), ("other", "meeting")):
        count, text = text_of(pdf_of(desk, stored[slot]))
        total += count
        assert count == 1 and (wanted in text or wanted == "meeting")
    assert total == 3                                           # every page of the scan is in some field
    assert "aadhaar" not in text_of(pdf_of(desk, stored["passport"]))[1]
    with desk.sessions() as db:
        audit = {entry.meta["slot"]: entry.meta for entry in db.scalars(select(AuditEntry).where(
            AuditEntry.action == "Uploaded document", AuditEntry.entity_id == "candidate"))}
    assert audit["passport"]["original_name"] == "Alex all documents.pdf" and audit["passport"]["split_pages"] == [2]
    assert audit["aadhaar"]["split_pages"] == [3] and audit["other"]["split_pages"] == [1]
    assert audit["passport"]["extraction_deferred"] is True


def test_reading_is_queued_once_for_the_combined_original_across_piece_actions(desk):
    response = send(desk, composite_pdf(SCAN), slot="passport", defer="false")
    assert response.status_code == 201, response.text
    with desk.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == "candidate")))
        assert len(jobs) == 1
        piece_ids = set(jobs[0].draft["packetPieceIds"])
    [job] = jobs
    stored = {row.slot: row for row in rows(desk)}
    assert job.attachment_id == stored["source_bundle"].id and job.status == "pending"
    assert piece_ids == {stored["passport"].id, stored["aadhaar"].id}
    again = desk.client.post("/api/candidates/candidate/application-form/extract", json={"attachmentIds": [stored["aadhaar"].id]})
    assert again.status_code == 200 and again.json()["queued"] == 0, again.text
    # deferred (the screens' default): stored only, nothing is read until a person asks
    send(desk, composite_pdf(["passport", "aadhaar", "unrelated"]), slot="aadhaar", defer="true")
    with desk.sessions() as db:
        assert len(list(db.scalars(select(DocumentExtractionJob)))) == 1


def test_profile_queues_deferred_resume_and_degree_on_their_single_original(desk):
    from app.api.routers import candidate_import
    from test_email_intake_split import everything
    data = everything(["resume_1", "resume_2", "degree"])
    uploaded = send(desk, data, slot="resume", name="resume-and-degree.pdf", defer="true")
    assert uploaded.status_code == 201, uploaded.text
    stored = {row.slot: row for row in rows(desk)}
    assert {"source_bundle", "resume", "education_certificate"} <= stored.keys()
    with desk.sessions() as db:
        candidate, user = db.get(Candidate, "candidate"), db.get(User, "staff")
        first = candidate_import.queue_unread(db, user, [candidate], retry_failed=False, limit=1)
        db.commit()
        assert first[0] == 1 and not first[2]
        assert candidate_import.queue_unread(db, user, [candidate], retry_failed=False)[0] == 0
        [job] = list(db.scalars(select(DocumentExtractionJob)))
        assert job.attachment_id == stored["source_bundle"].id and job.object_key == stored["source_bundle"].object_key
    response = desk.client.post("/api/candidates/candidate/application-form/extract", json={"attachmentIds": [stored["education_certificate"].id]})
    assert response.status_code == 200 and response.json()["queued"] == 0, response.text


def test_the_same_combined_scan_again_adds_no_new_versions(desk):
    scan = composite_pdf(SCAN)
    assert send(desk, scan).status_code == 201
    again = send(desk, scan)
    assert again.status_code == 201 and len(again.json()["split"]["pieces"]) == 3
    assert all(row.version == 1 for row in rows(desk)) and len(rows(desk)) == 4      # three pieces and the original, none of them twice


def test_a_piece_goes_in_the_field_the_person_chose_when_the_scan_did_not_show_it_elsewhere(desk):
    send(desk, composite_pdf(["unrelated", "passport"]), slot="signed_offer", name="offer and passport.pdf")
    stored = {row.slot: row for row in rows(desk)}
    assert set(stored) == {"passport", "signed_offer", "source_bundle"}           # the pages nothing was found on stay in the field that was chosen
    assert text_of(pdf_of(desk, stored["signed_offer"]))[0] == 1 and "passport" in text_of(pdf_of(desk, stored["passport"]))[1]
    assert stored["signed_offer"].file_name == "Alex_Example-Signed_offer.pdf"


def test_pieces_the_reader_names_as_other_documents_go_to_their_own_fields(desk):
    """When the classifier names the remaining pages (signed offer, visa ...) each goes to that field, whatever field was chosen."""
    scan = composite_pdf(SCAN)
    named = bulk.SplitResult([bulk.Piece(["passport"], b"%PDF-passport", [2]), bulk.Piece(["signed_offer"], b"%PDF-offer", [1]),
                              bulk.Piece(["visa", "pcc"], b"%PDF-visa", [3])], True, "note")
    with patch.object(bulk, "split_by_content", return_value=named), \
            patch.object(splitting, "_page_count", return_value=3):
        plan = splitting.plan_split("passport", "scan.pdf", scan)
    assert [piece.slots for piece in plan.pieces] == [["passport"], ["signed_offer"], ["visa", "pcc"]]


def test_a_scan_that_is_one_document_throughout_is_stored_as_it_came(desk):
    scan = composite_pdf(["passport", "passport"])
    response = send(desk, scan, slot="passport", name="my passport.pdf")
    assert response.status_code == 201 and "split" not in response.json()
    [row] = rows(desk)
    assert row.slot == "passport" and row.file_name == "my-passport.pdf" and row.content_hash == hashlib.sha256(scan).hexdigest()
    assert pdf_of(desk, row) == scan                             # byte for byte
    assert not [r for r in rows(desk) if r.slot == "source_bundle"]   # nothing was cut, so there is no separate original


def test_a_normal_two_page_passport_or_aadhaar_scan_is_never_read_page_by_page_unless_its_name_says_several_kinds(desk):
    for slot in ("passport", "aadhaar"):
        with patch.object(bulk, "split_by_content", side_effect=AssertionError("an ordinary front + back scan must not be read")):
            response = send(desk, composite_pdf([slot, "unrelated"]), slot=slot, name="scan.pdf")
        assert response.status_code == 201 and "split" not in response.json()
    assert {row.slot for row in rows(desk)} == {"passport", "aadhaar"}
    # a name that says several kinds of document is read, and cut
    with patch.object(bulk, "split_by_content", wraps=bulk.split_by_content) as named:
        response = send(desk, composite_pdf(["passport", "aadhaar"]), slot="passport", name="passport and aadhaar.pdf")
    assert named.call_count == 1 and response.status_code == 201 and "split" in response.json(), response.text
    # three pages are always read
    with patch.object(bulk, "split_by_content", wraps=bulk.split_by_content) as reading:
        assert send(desk, composite_pdf(SCAN), slot="passport", name="scan.pdf").status_code == 201
    assert reading.call_count == 1


def test_the_database_connection_is_not_held_while_the_scan_is_read_and_permission_is_checked_again_after(desk):
    pool = desk.sessions.kw["bind"].pool
    seen = []
    original = splitting.plan_split

    def watched(*args, **kwargs):
        seen.append(pool.checkedout())          # no pooled connection (and so no open transaction) while the slow reading runs
        return original(*args, **kwargs)

    with patch.object(splitting, "plan_split", watched):
        assert send(desk, composite_pdf(SCAN), slot="passport").status_code == 201
    assert seen == [0]
    # the owner changed while the scan was being read: the locked part refuses, nothing is stored
    def reassign(*args, **kwargs):
        with desk.sessions() as other:
            other.get(Candidate, "candidate").owner_id = "other"
            other.commit()
        return original(*args, **kwargs)

    before = len(rows(desk))
    with patch.object(splitting, "plan_split", reassign):
        refused = send(desk, composite_pdf(["passport", "aadhaar", "unrelated"]), slot="passport")
    assert refused.status_code == 403 and len(rows(desk)) == before


def test_a_reading_that_fails_stores_the_file_whole_in_the_chosen_field_and_the_upload_succeeds(desk):
    scan = composite_pdf(SCAN)
    with patch.object(inspection, "inspect_document", side_effect=inspection.InspectionError("timed out", retryable=True)):
        response = send(desk, scan, slot="passport")
    assert response.status_code == 201 and "split" not in response.json()
    [row] = rows(desk)
    assert row.slot == "passport" and pdf_of(desk, row) == scan
    with patch.object(bulk, "split_by_content", side_effect=RuntimeError("boom")):
        again = send(desk, composite_pdf(["passport", "unrelated"]), slot="medical")
    assert again.status_code == 201 and {row.slot for row in rows(desk)} == {"passport", "medical"}


@pytest.mark.parametrize("data,kind,slot", [
    (composite_pdf(["passport"]), "application/pdf", "passport"),                 # one page cannot hold several documents
    (b"\xff\xd8\xff JPEG", "image/jpeg", "passport"),                             # an image is one page
    (composite_pdf(SCAN), "application/pdf", "photo"),                            # the photo field takes images only
    (composite_pdf(SCAN), "application/pdf", "source_bundle"),                    # the original upload is kept as received
])
def test_files_that_cannot_hold_several_documents_are_never_analysed(desk, data, kind, slot):
    with patch.object(bulk, "split_by_content", side_effect=AssertionError("must not be analysed")):
        response = send(desk, data, slot=slot, kind=kind, name="x.jpg" if kind == "image/jpeg" else "x.pdf")
    assert "split" not in response.json()


def test_who_may_work_on_the_candidate_is_decided_before_anything_is_read_and_the_refusal_is_unchanged(desk):
    desk.actor.id, desk.actor.role = "other", Role.staff                          # not the assigned recruiter
    plain = send(desk, b"%PDF-1.4 not really a scan", slot="passport")
    with patch.object(bulk, "split_by_content", side_effect=AssertionError("a refused user's file must not be read")):
        refused = send(desk, composite_pdf(SCAN), slot="passport")
    assert plain.status_code == refused.status_code == 403 and plain.json() == refused.json()
    assert rows(desk) == []
    missing = send(desk, composite_pdf(SCAN), slot="passport", candidate="nobody")
    assert missing.status_code == 404
    desk.actor.id, desk.actor.role = "manager", Role.manager                      # a manager may
    assert send(desk, composite_pdf(SCAN), slot="passport").status_code == 201


def test_a_failure_while_storing_the_pieces_leaves_no_rows_and_no_files(desk):
    original = bulk._store_bytes
    calls = {"n": 0}

    def failing(settings, payload, filename, content_type, written):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return original(settings, payload, filename, content_type, written)

    with patch.object(bulk, "_store_bytes", failing):
        response = send(desk, composite_pdf(SCAN))
    assert response.status_code == 503 and rows(desk) == []
    assert [path for path in desk.storage.glob("*/*") if path.is_file()] == []   # the piece already written was taken away again


def test_a_part_that_lands_in_the_air_ticket_field_starts_the_invoice_request_like_a_single_ticket_upload(desk):
    named = bulk.SplitResult([bulk.Piece(["passport"], b"%PDF-1.4 passport", [2, 3]), bulk.Piece(["air_ticket"], b"%PDF-1.4 ticket", [1])], True, "note")
    with patch.object(bulk, "split_by_content", return_value=named),             patch("app.services.candidate_invoices.on_ticket_uploaded") as invoice:
        response = send(desk, composite_pdf(SCAN), slot="passport")
    assert response.status_code == 201, response.text
    assert [call.args[1].slot for call in invoice.call_args_list] == ["air_ticket"]
    assert {row.slot for row in rows(desk)} == {"passport", "air_ticket", "source_bundle"}
    # an invoice request that fails cannot undo the filing
    with patch.object(bulk, "split_by_content", return_value=bulk.SplitResult(
            [bulk.Piece(["passport"], b"%PDF-1.4 passport two", [2, 3]), bulk.Piece(["air_ticket"], b"%PDF-1.4 ticket two", [1])], True, "n")),             patch("app.services.candidate_invoices.on_ticket_uploaded", side_effect=RuntimeError("pdf")):
        assert send(desk, composite_pdf(SCAN), slot="passport").status_code == 201
    assert sorted(row.version for row in rows(desk) if row.slot == "air_ticket") == [1, 2]


# ------------------------------------------------------------------ the application form's slot upload

def test_the_form_upload_answers_with_the_pieces_and_the_form_shows_every_field_filled(desk):
    scan = composite_pdf(SCAN)
    response = desk.client.post("/api/candidates/candidate/application-form/documents", data={"slot": "passport"},
                                files={"file": ("everything.pdf", scan, "application/pdf")})
    assert response.status_code == 202, response.text
    body = response.json()
    assert {piece["slot"] for piece in body["split"]["pieces"]} == {"passport", "aadhaar", "other"}
    original, = body["form"]["originalFiles"]
    assert original["fileName"] == "everything.pdf"
    downloaded = desk.client.get(original["url"], params={"download": "true"})
    assert downloaded.status_code == 200 and downloaded.content == scan
    repeat = send(desk, scan, slot="passport", name="same-again.pdf")
    assert repeat.status_code == 201
    form = desk.client.get("/api/candidates/candidate/application-form").json()
    assert [file["attachmentId"] for file in form["originalFiles"]] == [original["attachmentId"]]
    documents = body["form"]["documents"]
    assert documents["passport"]["uploaded"] and documents["aadhaar"]["uploaded"] and documents["other"]["uploaded"]
    assert "were filed in their own fields" in body["split"]["message"]
    # a plain single-document upload through the same endpoint has no split in its answer
    plain = desk.client.post("/api/candidates/candidate/application-form/documents", data={"slot": "medical"},
                             files={"file": ("m.pdf", composite_pdf(["unrelated"]), "application/pdf")})
    assert plain.status_code == 202 and "split" not in plain.json()


def test_an_education_certificate_is_sent_to_the_reader_like_the_other_read_slots(desk):
    """The owner's decision: education certificates go to the online reader with the resume, passport and Aadhaar. The job kind column
    is 20 characters and 'education_certificate' is 21, so the job is stored as kind 'resume' and still shows on the education file."""
    base = "/api/candidates/candidate/application-form"
    assert splitting.READ_SLOTS == bulk.SENT_TO_READER and "education_certificate" in splitting.READ_SLOTS
    assert len("education_certificate") > 20
    # (an explicit deferral: with no flag the form upload is queued at once, see test_auto_reading.py)
    upload = desk.client.post(base + "/documents", data={"slot": "education_certificate", "defer_extraction": "true"},
                              files={"file": ("degree.pdf", composite_pdf(["unrelated"]), "application/pdf")})
    assert upload.status_code == 202, upload.text
    slot = upload.json()["form"]["documents"]["education_certificate"]
    assert slot["readable"] is True and slot["uploaded"] and slot["extractionPending"] is True     # "Extract details" is offered, nothing is read yet
    with desk.sessions() as db:
        assert list(db.scalars(select(DocumentExtractionJob))) == []                               # deferred: stored only
        deferred = [entry.meta for entry in db.scalars(select(AuditEntry).where(AuditEntry.action == "Uploaded document"))]
    assert deferred and deferred[0]["slot"] == "education_certificate" and deferred[0]["extraction_deferred"] is True
    attachment_id = slot["current"]["attachmentId"]

    started = desk.client.post(base + "/extract", json={"attachmentIds": [attachment_id]})        # a person chooses Extract details
    assert started.status_code == 202, started.text
    assert started.json()["queued"] == 1
    with desk.sessions() as db:
        job = db.scalar(select(DocumentExtractionJob))
        assert job.kind == "resume" and len(job.kind) <= 20 and job.attachment_id == attachment_id and job.status == "pending"
        assert job.draft["applicationForm"]["slot"] == "education_certificate"
        job_id = job.id
    shown = started.json()["form"]["documents"]["education_certificate"]
    assert shown["extraction"]["status"] == "processing" and shown["extraction"]["jobId"] == job_id and shown["extractionPending"] is False
    assert desk.client.post(base + "/extract", json={"attachmentIds": [attachment_id]}).json()["queued"] == 0     # nothing is queued twice
    with desk.sessions() as db:
        assert len(list(db.scalars(select(DocumentExtractionJob)))) == 1

    with desk.sessions() as db:                                                                    # a failed reading shows on the file and is put back
        db.get(DocumentExtractionJob, job_id).status = "failed"
        db.get(DocumentExtractionJob, job_id).last_error = "The reader was busy."
        db.commit()
    failed = desk.client.get(base).json()["documents"]["education_certificate"]
    assert failed["extraction"]["status"] == "failed" and failed["extractionPending"] is True
    again = desk.client.post(base + "/extract", json={"attachmentIds": [attachment_id]})
    assert again.json()["queued"] == 1
    with desk.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        assert len(jobs) == 1 and jobs[0].status == "pending" and jobs[0].kind == "resume"


def test_an_education_certificate_uploaded_without_deferral_is_queued_at_once_and_a_resume_is_not_touched(desk):
    response = send(desk, composite_pdf(["unrelated"]), slot="education_certificate", name="sslc.pdf", defer="false")
    assert response.status_code == 201, response.text
    with desk.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        row = db.scalar(select(Attachment).where(Attachment.slot == "education_certificate"))
    assert [(job.kind, job.attachment_id, job.status) for job in jobs] == [("resume", row.id, "pending")]
    other = send(desk, composite_pdf(["unrelated"]), slot="medical", name="medical.pdf", defer="false")        # still nothing for the other documents
    assert other.status_code == 201
    with desk.sessions() as db:
        assert len(list(db.scalars(select(DocumentExtractionJob)))) == 1


# ------------------------------------------------------------------ the Add-candidate intake (saved upload attached to the new candidate)

def _saved_upload(env, data, kind="resume"):
    response = env.client.post("/api/ocr/jobs", data={"kind": kind, "group_id": str(uuid4())}, files={"file": ("intake scan.pdf", data, "application/pdf")})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_attaching_a_saved_scan_files_its_pages_in_their_own_fields_too(desk):
    job = _saved_upload(desk, composite_pdf(SCAN))
    attached = desk.client.post(f"/api/ocr/jobs/{job}/attach", json={"candidateId": "candidate"})
    assert attached.status_code == 200, attached.text
    assert {piece["slot"] for piece in attached.json()["split"]["pieces"]} == {"passport", "aadhaar", "other"}
    stored = {row.slot: row for row in rows(desk)}
    assert set(stored) == {"source_bundle", "passport", "aadhaar", "other"}
    assert stored["passport"].file_name == "Alex_Example-Passport.pdf" and "passport" in text_of(pdf_of(desk, stored["passport"]))[1]
    with desk.sessions() as db:
        meta = next(e.meta for e in db.scalars(select(AuditEntry).where(AuditEntry.entity_id == "candidate")) if e.meta.get("slot") == "aadhaar")
    assert meta["original_name"] == "intake-scan.pdf" and meta["split_pages"] == [3] and meta["document_job_id"] == job


def test_a_scan_that_would_need_ocr_is_never_read_inside_the_attach_request_it_is_stored_whole_and_read_in_the_background(desk):
    scan = composite_pdf(SCAN)
    job = _saved_upload(desk, scan)
    with patch.object(splitting, "costs_ocr", return_value=True),             patch.object(bulk, "split_by_content", side_effect=AssertionError("OCR must not run inside the attach request")):
        attached = desk.client.post(f"/api/ocr/jobs/{job}/attach", json={"candidateId": "candidate"})
    assert attached.status_code == 200, attached.text
    assert "pieces" in attached.json()["split"] and attached.json()["split"]["pieces"] == []
    stored = rows(desk)
    assert [row.slot for row in stored] == ["resume"] and read_bytes(desk.settings, stored[0].object_key) == scan   # the whole scan, as received
    with desk.sessions() as db:
        queued = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.status == "pending")))
    assert [(item.candidate_id, item.attachment_id, item.kind) for item in queued] == [("candidate", stored[0].id, "resume")]


def test_a_cheap_scan_whose_reading_is_still_running_is_sorted_now_and_also_queued_for_the_reader(desk):
    job = _saved_upload(desk, composite_pdf(SCAN))
    with desk.sessions() as db:
        db.get(DocumentExtractionJob, job).status = "processing"            # the person stopped waiting: no result yet
        db.commit()
    attached = desk.client.post(f"/api/ocr/jobs/{job}/attach", json={"candidateId": "candidate"})
    assert attached.status_code == 200, attached.text
    assert {row.slot for row in rows(desk)} == {"resume", "passport", "aadhaar", "other"}                   # sorted from the text layer at once
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job).status == "attached"
        assert len(list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.status == "pending",
                                                                       DocumentExtractionJob.candidate_id == "candidate")))) == 1


def test_costs_ocr_tells_a_text_layer_from_a_scan():
    assert splitting.costs_ocr(composite_pdf(SCAN)) is False
    assert splitting.costs_ocr(b"not a pdf") is True
    with fitz.open() as blank:
        blank.new_page()
        assert splitting.costs_ocr(blank.tobytes()) is True                 # a page with no text layer has to be read as a picture


def test_fields_the_reader_already_filled_from_the_saved_scan_are_left_alone(desk):
    scan = composite_pdf(SCAN)
    job = _saved_upload(desk, scan)
    derived = b"%PDF-derived passport"
    key = put_bytes(desk.settings, "manual/derived", derived, "application/pdf")
    with desk.sessions() as db:
        row = db.get(DocumentExtractionJob, job)
        row.state = {"documents": {"passport": {"object_key": key, "file_name": "derived.pdf", "content_type": "application/pdf",
                                                "size_kb": 1, "content_hash": hashlib.sha256(derived).hexdigest()}}}
        db.commit()
    attached = desk.client.post(f"/api/ocr/jobs/{job}/attach", json={"candidateId": "candidate"})
    assert attached.status_code == 200, attached.text
    passports = [row for row in rows(desk) if row.slot == "passport"]
    assert [row.file_name for row in passports] == ["derived.pdf"]                # one passport file: the reader's, not a second cut
    assert {piece["slot"] for piece in attached.json()["split"]["pieces"]} == {"aadhaar", "other"}


def test_attaching_the_original_only_or_a_single_document_scan_adds_nothing_extra(desk):
    job = _saved_upload(desk, composite_pdf(SCAN))
    with patch.object(bulk, "split_by_content", side_effect=AssertionError("original only: no analysis")):
        done = desk.client.post(f"/api/ocr/jobs/{job}/attach", json={"candidateId": "candidate", "originalOnly": True})
    assert done.status_code == 200 and "split" not in done.json() and {row.slot for row in rows(desk)} == {"source_bundle"}
    single = _saved_upload(desk, composite_pdf(["passport", "passport"]), kind="passport")
    done = desk.client.post(f"/api/ocr/jobs/{single}/attach", json={"candidateId": "candidate"})
    assert done.status_code == 200 and "split" not in done.json() and {row.slot for row in rows(desk)} == {"source_bundle"}


def test_attaching_to_a_candidate_the_person_may_not_work_is_refused_without_reading_the_scan(desk):
    job = _saved_upload(desk, composite_pdf(SCAN))
    desk.actor.id, desk.actor.role = "other", Role.staff
    with desk.sessions() as db:
        db.add(Candidate(id="theirs", ref="CND-2", name="Someone Else", phone="9000000002", source=Source.walk_in, owner_id="staff"))
        db.commit()
    # the job belongs to the staff user, so "other" cannot even see it: the refusal is the one it always was
    with patch.object(bulk, "split_by_content", side_effect=AssertionError("must not be read")):
        refused = desk.client.post(f"/api/ocr/jobs/{job}/attach", json={"candidateId": "theirs"})
    assert refused.status_code in (403, 404) and rows(desk, "theirs") == []


# ------------------------------------------------------------------ helpers

def test_the_message_names_each_document_and_its_pages():
    def piece(slot, pages):
        return splitting.StoredPiece(slot, splitting.label_of(slot), "x.pdf", pages, SimpleNamespace(id="a", version=1))
    assert splitting.filing_message([piece("passport", [2, 3]), piece("resume", [1, 8])]) == (
        "This file held several documents: Passport (pages 2-3) and Resume (pages 1, 8) were filed in their own fields.")
    assert splitting.filing_message([piece("passport", [2]), piece("aadhaar", [3]), piece("other", [1])]).startswith(
        "This file held several documents: Passport (page 2), Aadhaar card (page 3) and Other documents (page 1)")
    assert splitting.filing_message([piece("passport", [2]), piece("aadhaar", [2])]) == (
        "This file held several documents: Passport + Aadhaar card (page 2) were filed in their own fields.")
    assert splitting.filing_message([]) is None


def test_unplaced_pages_follow_the_chosen_field_only_when_it_was_not_found_elsewhere():
    assert splitting._targets(["other"], "signed_offer", {"passport"}) == ["signed_offer"]
    assert splitting._targets(["other"], "passport", {"passport", "aadhaar"}) == ["other"]
    assert splitting._targets(["other"], None, {"passport"}) == ["other"]
    assert splitting._targets(["photo", "invoice"], "passport", set()) == ["other"]    # never a field that cannot take a cut PDF
    assert splitting._targets(["passport", "aadhaar"], "passport", {"passport", "aadhaar"}) == ["passport", "aadhaar"]
