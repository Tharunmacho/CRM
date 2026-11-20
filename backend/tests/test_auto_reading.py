"""Nobody presses "Extract details": stored documents are read by themselves and every page of a bundle ends in its own field.

* an upload to the resume / passport / Aadhaar / education fields of an existing candidate is queued for the reader at once;
* `POST /api/candidates/{id}/read-pending` queues what is stored but unread (deferred uploads, imported files), once per stored file,
  within the caller's permissions and a cap, and files the other documents of files that were read before;
* after a bundle is read, the pages the content classifier identifies (education / experience certificates, signed offer, visa,
  medical, police clearance, air ticket) are cut out of it and filed in their own fields from the reading the job saved; nothing is
  read again, the whole file is kept, nothing is filed twice and a failure never holds back the three identity documents.

DB-backed (disposable PostgreSQL); synthetic documents with a text layer; the online reader is faked (nothing is sent anywhere).
"""
import hashlib
import json
import uuid
from copy import deepcopy

import pymupdf as fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import undefer

from app.api.routers import candidate_import, files as files_router
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob, Role
from app.integrations.ocr import inspection
from app.services import application_form, document_extraction, document_splitting as splitting, intake_documents, manual_extraction
from app.services import form_extraction as fx
from app.services.document_storage import put_bytes, read_bytes
from ocr_synthetic import composite_pdf
import people_fixtures as pf
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_document_identity import _passport_of, _resume_of
from test_document_splitting_uploads import desk, form_router, document_jobs  # noqa: F401  (desk is a fixture)

# The pages of one candidate's whole file; the real local engine cuts it (native text, no Tesseract), as in test_email_intake_split.
BUNDLE = ["resume_1", "resume_2", "passport_data", "passport_back", "aadhaar_front", "degree", "offer", "evisa", "medical", "pcc", "air_ticket"]
OWN_FIELDS = {"education_certificate": [6], "signed_offer": [7], "visa": [8], "medical": [9], "pcc": [10], "air_ticket": [11]}
NOTE = ("Minutes of the weekly meeting about the canteen rota, the parking arrangements and the next newsletter; "
        "no document of the candidate is written on this sheet.")
RAW = {"resume": _resume_of("Alex Example"), "passport": _passport_of("ALEX", "EXAMPLE", "K1234567"),
       "aadhaar": {"aadhaar": {"name": "ALEX EXAMPLE", "aadhaar_number": "000000001234"}}}


def bundle(tokens=BUNDLE, *notes) -> bytes:
    data = pf.build_pdf([pf.page("tharun", token) for token in tokens], "text")
    if not notes:
        return data
    with fitz.open(stream=data, filetype="pdf") as document:
        for note in notes:
            document.new_page().insert_text((50, 80), note, fontsize=9)
        return document.tobytes(no_new_id=True)


def page_texts(data: bytes) -> list[str]:
    with fitz.open(stream=data, filetype="pdf") as document:
        return [page.get_text() for page in document]


@pytest.fixture
def world(desk, monkeypatch):
    """The upload, application-form and read-pending routes on the disposable database; the candidate is Alex Example (staff's)."""
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    app = FastAPI()
    for router in (files_router.router, form_router.router, candidate_import.router, candidate_import.candidate_reading_router):
        app.include_router(router)

    def database():
        with desk.sessions() as db:
            yield db

    for module in (files_router, form_router, candidate_import):
        app.dependency_overrides[module.get_db] = database
        app.dependency_overrides[module.current_user] = lambda: desk.actor
    with TestClient(app) as client:
        desk.api = client
        yield desk


def store(env, slot, name, data, *, content_type="application/pdf", candidate="candidate", audit=None) -> Attachment:
    """A file that is already in the candidate's documents and unread (what an earlier import or deferred upload left)."""
    key = put_bytes(env.settings, f"test/{uuid.uuid4()}-{name}", data, content_type)
    with env.sessions() as db:
        previous = db.scalar(select(func.max(Attachment.version)).where(Attachment.entity_id == candidate, Attachment.slot == slot)) or 0
        row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate, slot=slot, version=previous + 1, file_name=name,
                         size_kb=max(1, len(data) // 1024), content_type=content_type, object_key=key,
                         content_hash=hashlib.sha256(data).hexdigest(), uploaded_by="staff")
        db.add(row)
        db.flush()
        db.add(AuditEntry(actor_id="staff", action="Uploaded document", target=name, entity_type="candidate", entity_id=candidate,
                          meta={"attachment_id": row.id, "slot": slot, "version": row.version, **(audit or {})}))
        db.commit()
        return row


def read_pending(env, candidate="candidate"):
    return env.api.post(f"/api/candidates/{candidate}/read-pending")


def jobs(env, candidate="candidate"):
    with env.sessions() as db:
        return list(db.scalars(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.draft), undefer(DocumentExtractionJob.state))
                               .where(DocumentExtractionJob.candidate_id == candidate).order_by(DocumentExtractionJob.created_at)))


def slots(env, candidate="candidate"):
    found: dict[str, list[Attachment]] = {}
    with env.sessions() as db:
        for row in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate)
                              .order_by(Attachment.slot, Attachment.version)):
            found.setdefault(row.slot, []).append(row)
    return found


def bytes_of(env, row) -> bytes:
    return read_bytes(env.settings, row.object_key)


def install_reader(monkeypatch, calls=None, submissions=None):
    """The online reader, faked: the local reading of the file is the real one (kept by the job like the real run does, so the pages are not
    read again). The pages go to the reader the way a candidate's packet is submitted (`candidate_filing`): the local page plan decides the
    groups, the education pages ride with the resume in ONE resume-mode submission, passport and Aadhaar are their own. Every submission
    is recorded in `submissions` as (mode, pages); what the reader answers for the three modes is fixed."""
    def extract(data, *, state, save, **kwargs):
        if calls is not None:
            calls.append(hashlib.sha256(data).hexdigest())
        inspected = inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
        report = inspected.to_dict()
        documents, supporting = document_extraction._candidate_page_plan(inspected, {}, True, {})
        report.update(document_pages=documents, supporting_pages=supporting)
        state.update(source_sha256=hashlib.sha256(data).hexdigest(), inspection=deepcopy(report))
        save(state)
        sent = {kind: list(pages) for kind, pages in documents.items() if pages}
        if "resume" in sent:
            sent["resume"] = sorted({*sent["resume"], *(int(n) for n, kind in supporting.items() if kind == "education_certificate")})
        if submissions is not None:
            submissions.extend((kind, pages) for kind, pages in sent.items())
        extractions = {kind: {"fields": {}, "raw": RAW[kind], "selected_pages": pages,
                              "source": {"local_inspection": {"selected_pages": pages}, "selected_pages": pages}}
                       for kind, pages in sent.items()}
        return {"status": "complete", "inspection": report, "warnings": [], "coverage": {"complete": True, "pages": {"not_sent": []}},
                "extractions": extractions}
    monkeypatch.setattr(manual_extraction, "extract_attachment", extract)


def run_worker(env):
    with env.sessions() as db:
        ids = list(db.scalars(select(DocumentExtractionJob.id).where(DocumentExtractionJob.status.in_(manual_extraction.READY))
                              .order_by(DocumentExtractionJob.created_at)))
    for identifier in ids:
        manual_extraction.process_job(identifier, sessions=env.sessions, settings=env.settings)
    return ids


# ------------------------------------------------------------------ (a) an upload is read at once

def test_a_form_upload_with_no_flag_is_queued_for_the_reader_at_once(world):
    response = world.api.post("/api/candidates/candidate/application-form/documents", data={"slot": "passport"},
                              files={"file": ("passport.pdf", composite_pdf(["passport"]), "application/pdf")})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["extraction"]["status"] == "processing" and body["document"].get("extractionPending") in (False, None)
    [job] = jobs(world)
    assert job.kind == "passport" and job.status == "pending"


@pytest.mark.parametrize("slot", ["resume", "aadhaar", "education_certificate"])
def test_every_readable_field_is_queued_by_a_plain_upload(world, slot):
    data = composite_pdf(["aadhaar"]) if slot == "aadhaar" else composite_pdf(["certificate"])
    assert world.api.post("/api/candidates/candidate/application-form/documents", data={"slot": slot},
                          files={"file": (slot + ".pdf", data, "application/pdf")}).status_code == 202
    [job] = jobs(world)
    assert job.status == "pending" and job.draft["applicationForm"]["slot"] == slot


def test_a_file_stored_with_the_reading_deferred_is_queued_by_read_pending_and_never_twice(world):
    response = world.api.post("/api/candidates/candidate/application-form/documents", data={"slot": "passport", "defer_extraction": "true"},
                              files={"file": ("passport.pdf", composite_pdf(["passport"]), "application/pdf")})
    assert response.status_code == 202 and jobs(world) == []         # an explicit deferral still stores only
    first = read_pending(world)
    assert first.status_code == 200, first.text
    assert first.json() == {"candidateId": "candidate", "allowed": True, "queued": 1, "readable": 1, "more": False, "filed": [],
                            "message": None, "changed": True}
    again = read_pending(world).json()
    assert again["queued"] == 0 and again["readable"] == 1 and again["changed"] is False
    assert len(jobs(world)) == 1                                       # one reading per stored file, however often the form is opened


# ------------------------------------------------------------------ (b) what is stored and unread

def test_read_pending_queues_each_stored_readable_file_once_and_leaves_the_rest_alone(world):
    for slot, name, data in (("resume", "Resume.pdf", bundle(["resume_1", "resume_2"])), ("passport", "Passport.pdf", composite_pdf(["passport"])),
                             ("aadhaar", "Aadhaar.pdf", composite_pdf(["aadhaar"])), ("education_certificate", "Degree.pdf", composite_pdf(["certificate"])),
                             ("medical", "Medical.pdf", composite_pdf(["unrelated"]))):
        store(world, slot, name, data)
    result = read_pending(world).json()
    assert result["queued"] == 4 and result["readable"] == 4 and result["changed"] is True
    assert sorted((job.draft["applicationForm"]["slot"], job.status) for job in jobs(world)) == [
        ("aadhaar", "pending"), ("education_certificate", "pending"), ("passport", "pending"), ("resume", "pending")]
    assert read_pending(world).json()["queued"] == 0 and len(jobs(world)) == 4


def test_one_stored_file_in_two_fields_is_read_once(world):
    scan = composite_pdf(["passport", "aadhaar"])
    store(world, "passport", "scan.pdf", scan)
    row = store(world, "aadhaar", "scan.pdf", scan)
    with world.sessions() as db:                                       # the very same stored bytes serve both fields
        db.execute(update(Attachment).where(Attachment.slot == "passport").values(object_key=row.object_key, uploaded_at=row.uploaded_at))
        db.commit()
    assert read_pending(world).json()["queued"] == 1 and len(jobs(world)) == 1


def test_a_failed_reading_is_not_queued_again_by_read_pending_but_read_again_still_does(world):
    store(world, "passport", "Passport.pdf", composite_pdf(["passport"]))
    assert read_pending(world).json()["queued"] == 1
    with world.sessions() as db:
        db.execute(update(DocumentExtractionJob).values(status="failed"))
        db.commit()
    assert read_pending(world).json()["queued"] == 0                   # an automatic call never retries a failure in a loop
    assert [job.status for job in jobs(world)] == ["failed"]
    assert world.api.post("/api/candidate-import/read-again", json={"candidateIds": ["candidate"]}).json()["queued"] == 1
    assert [job.status for job in jobs(world)] == ["pending"]


def test_read_pending_follows_the_same_permissions_as_read_again(world):
    store(world, "passport", "Passport.pdf", composite_pdf(["passport"]))
    world.actor.id, world.actor.role = "other", Role.staff             # another recruiter's candidate
    refused = read_pending(world)
    assert refused.status_code == 200 and refused.json()["allowed"] is False and refused.json()["queued"] == 0
    assert jobs(world) == []
    assert read_pending(world, "nobody").status_code == 404
    world.actor.id, world.actor.role = "manager", Role.manager
    assert read_pending(world).json()["queued"] == 1
    assert [job.owner_id for job in jobs(world)] == ["manager"]
    world.actor.id, world.actor.role = "staff", Role.staff


def test_one_call_queues_at_most_the_cap_and_says_more_are_waiting(world, monkeypatch):
    monkeypatch.setattr(candidate_import, "MAX_AUTO_READS", 2)
    for slot in ("resume", "passport", "aadhaar"):
        store(world, slot, slot + ".pdf", composite_pdf(["passport" if slot == "passport" else "aadhaar" if slot == "aadhaar" else "unrelated"]))
    first = read_pending(world).json()
    assert first["queued"] == 2 and first["more"] is True and first["readable"] == 3
    second = read_pending(world).json()
    assert second["queued"] == 1 and second["more"] is False
    assert len(jobs(world)) == 3 and read_pending(world).json()["queued"] == 0


# ------------------------------------------------------------------ (c) every page in its own field

def test_a_stored_bundle_is_read_and_every_page_lands_in_its_own_field(world, monkeypatch):
    calls: list[str] = []
    submissions: list[tuple[str, list[int]]] = []
    install_reader(monkeypatch, calls, submissions)
    whole = bundle(BUNDLE, NOTE)                                       # + one page nothing recognises (page 12)
    store(world, "resume", "Alex_Example-Resume.pdf", whole)           # imported earlier, never read
    assert read_pending(world).json()["queued"] == 1
    run_worker(world)

    filed = slots(world)
    assert {slot: len(rows) for slot, rows in filed.items()} == {
        "resume": 1, "passport": 1, "aadhaar": 1, "education_certificate": 1, "signed_offer": 1, "visa": 1, "medical": 1, "pcc": 1,
        "air_ticket": 1, "other": 1, "source_bundle": 1}
    assert bytes_of(world, filed["source_bundle"][0]) == whole         # the whole file is kept, untouched
    marks = {"signed_offer": "offer", "visa": "visa", "medical": "medical", "pcc": "police", "air_ticket": "ticket"}
    for slot, pages in OWN_FIELDS.items():
        texts = page_texts(bytes_of(world, filed[slot][0]))
        assert len(texts) == len(pages), slot
        if slot in marks:
            assert marks[slot] in texts[0].lower(), slot
    assert page_texts(bytes_of(world, filed["other"][0])) == [page_texts(whole)[11]]   # only the doubtful page, with the reader's own file
    seen = set()                                                        # no page of the whole file is in no field
    originals = page_texts(whole)
    for slot, rows in filed.items():
        if slot != "source_bundle":
            for text in page_texts(bytes_of(world, rows[-1])):
                seen.update(number for number, original in enumerate(originals, 1) if original == text)
    assert seen == set(range(1, 13))
    assert len(calls) == 1                                              # the reader got the file once; the other pages were never sent
    # the three kinds filled the form by themselves
    with world.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.passport_no == "K1234567" and candidate.field_sources.get("passport.passportNumber") == "extracted"
        job = db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.kind == "resume").order_by(DocumentExtractionJob.created_at)).first()
        notes = job.draft["applicationForm"]["result"]["coverage"]["filing"]
        assert job.draft[application_form.OTHERS_FILED]
    assert any("Medical" in note and "were filed in their own fields" in note for note in notes)
    assert "Page 12 was filed under Other documents (not recognised as a resume page)." in notes
    # what the provider was sent: ONE resume-mode submission with the resume pages AND the education page (1, 2 and 6), the passport pages and
    # the Aadhaar page each on their own; nothing else (offer, visa, medical, police, ticket, the note) is ever submitted
    assert sorted(submissions) == [("aadhaar", [5]), ("passport", [3, 4]), ("resume", [1, 2, 6])]
    # the education certificate rode with the resume: it is filed in its own field and has no reading of its own queued
    assert [job.draft["applicationForm"]["slot"] for job in jobs(world)] == ["resume"]


def test_the_same_bundle_is_never_filed_twice(world, monkeypatch):
    calls: list[str] = []
    submissions: list[tuple[str, list[int]]] = []
    install_reader(monkeypatch, calls, submissions)
    store(world, "resume", "Alex_Example-Resume.pdf", bundle())
    read_pending(world)
    run_worker(world)
    before = {slot: [row.id for row in rows] for slot, rows in slots(world).items()}
    sent = sorted(submissions)
    assert sent == [("aadhaar", [5]), ("passport", [3, 4]), ("resume", [1, 2, 6])] and len(calls) == 1
    again = read_pending(world).json()
    assert again["queued"] == 0 and again["filed"] == [] and again["changed"] is False
    assert run_worker(world) == []                                      # nothing is left for a worker: the education pages were read with the resume
    after = {slot: [row.id for row in rows] for slot, rows in slots(world).items()}
    assert after == before and sorted(submissions) == sent and len(calls) == 1 and len(jobs(world)) == 1   # no second (paid) reading
    # a genuinely new page is still read: a separate education certificate uploaded afterwards is queued once
    store(world, "education_certificate", "Degree.pdf", composite_pdf(["certificate"]))
    assert read_pending(world).json()["queued"] == 1 and read_pending(world).json()["queued"] == 0
    assert [job.draft["applicationForm"]["slot"] for job in jobs(world)] == ["resume", "education_certificate"]


def read_job(env, whole, *, slot="resume", identity_mismatch=False, name="Alex_Example-Resume.pdf"):
    """A bundle that was read BEFORE the other documents were filed: its job is completed and holds the saved reading (its inspection and
    page kinds), the other documents were never cut out."""
    row = store(env, slot, name, whole)
    with inspection_scope() as scope:
        report = inspection.inspect_document(whole, max_pages=300, page_timeout=20, document_timeout=600).to_dict()
    state = {"output": {"status": "complete", "inspection": report}}
    intake_documents.remember_kinds(state, hashlib.sha256(whole).hexdigest(), scope)
    result = {"identityMismatch": True} if identity_mismatch else {}
    with env.sessions() as db:
        job = DocumentExtractionJob(id=str(uuid.uuid4()), owner_id="staff", group_id=str(uuid.uuid4()), kind=slot, filename=name,
                                    content_type="application/pdf", object_key=row.object_key, content_hash=row.content_hash, size_kb=1,
                                    status="completed", candidate_id="candidate", attachment_id=row.id, state=state,
                                    draft={"applicationForm": {"candidateId": "candidate", "slot": slot, "attachmentId": row.id, "result": result}})
        db.add(job)
        db.commit()
        return row, job.id, state["output"]


def inspection_scope():
    return intake_documents.reading_scope()


def test_a_bundle_that_was_read_earlier_gets_its_other_documents_filed_once_from_the_saved_reading(world):
    whole = bundle()
    _row, job_id, _output = read_job(world, whole)
    assert set(slots(world)) == {"resume"}
    result = read_pending(world).json()
    assert result["queued"] == 0 and result["changed"] is True
    assert {piece["slot"] for piece in result["filed"]} == set(OWN_FIELDS)
    assert "were filed in their own fields" in result["message"]
    filed = slots(world)
    assert set(filed) == {"resume", *OWN_FIELDS} and all(len(rows) == 1 for rows in filed.values())
    assert bytes_of(world, filed["resume"][0]) == whole                 # the file that was read is untouched
    with world.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).draft[application_form.OTHERS_FILED]
        audit = {entry.meta["slot"]: entry.meta for entry in db.scalars(select(AuditEntry).where(
            AuditEntry.action == "Uploaded document", AuditEntry.entity_id == "candidate")) if entry.meta["slot"] != "resume"}
    assert audit["medical"]["split_pages"] == [9] and audit["medical"]["document_job_id"] == job_id
    # the education certificate waits to be read; nothing else is queued
    assert [job.draft["applicationForm"]["slot"] for job in jobs(world) if job.id != job_id] == ["education_certificate"]
    counts = {slot: len(rows) for slot, rows in slots(world).items()}
    again = read_pending(world).json()
    assert again["filed"] == [] and again["queued"] == 0 and {slot: len(rows) for slot, rows in slots(world).items()} == counts


def test_a_document_held_back_for_another_persons_name_is_never_filed(world):
    read_job(world, bundle(), identity_mismatch=True)
    assert read_pending(world).json()["filed"] == [] and set(slots(world)) == {"resume"}


def test_a_file_that_is_itself_a_piece_cut_from_a_larger_scan_is_not_cut_again(world):
    row, job_id, _output = read_job(world, bundle())
    with world.sessions() as db:
        entry = db.scalar(select(AuditEntry).where(AuditEntry.entity_id == "candidate"))
        entry.meta = {**entry.meta, "split_pages": [1, 2, 3]}
        db.commit()
    assert read_pending(world).json()["filed"] == [] and set(slots(world)) == {"resume"}


def test_a_failure_while_filing_sends_the_pages_to_other_documents_and_never_blocks_the_reading(world, monkeypatch):
    whole = bundle()
    _row, job_id, output = read_job(world, whole)
    real = splitting.store_pieces
    calls = []

    def failing_once(*args, **kwargs):
        calls.append(kwargs.get("skip_slots"))
        if len(calls) == 1:
            raise OSError("the store is down")
        return real(*args, **kwargs)
    monkeypatch.setattr(splitting, "store_pieces", failing_once)
    with world.sessions() as db:
        job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job_id))
        meta = job.draft["applicationForm"]
        stored = application_form.file_other_documents(db, db.get(DocumentExtractionJob, job_id), output, meta)
        db.commit()
    assert [piece.slot for piece in stored] == ["other"] and len(calls) == 2
    filed = slots(world)
    assert set(filed) == {"resume", "other"}
    taken = {number for kind in ("resume", "passport", "aadhaar") for number in output["inspection"][kind + "_pages"]}   # the reader files these
    kept = sorted({number for pages in OWN_FIELDS.values() for number in pages} - taken)
    assert kept and len(page_texts(bytes_of(world, filed["other"][0]))) == len(kept)    # every other page is somewhere: nothing lost


def test_blank_and_unreadable_saved_readings_file_nothing_and_do_not_raise(world):
    whole = bundle()
    _row, job_id, output = read_job(world, whole)
    with world.sessions() as db:                                        # a job saved before page kinds were kept
        job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state))
                        .where(DocumentExtractionJob.id == job_id))
        state = dict(job.state)
        state.pop("page_kinds")
        job.state = state
        db.commit()
    assert read_pending(world).json()["filed"] == [] and set(slots(world)) == {"resume"}
    with world.sessions() as db:
        assert not db.get(DocumentExtractionJob, job_id).draft.get(application_form.OTHERS_FILED)   # not marked: nothing was decided


def test_own_field_pages_are_only_the_ones_the_reader_does_not_file_itself():
    assert splitting.own_field_pages(None) == set() and splitting.own_field_pages({}) == set()
    assert splitting.own_field_pages({"resume": [1, 2], "passport": [3], "other": [4], "medical": [5, 6], "education_certificate": [7]}) == {5, 6, 7}
