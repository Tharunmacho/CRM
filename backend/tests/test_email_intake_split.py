"""One PDF that holds everything about a candidate, sent to the mailbox: every document in it is filed in its own field.

The mailbox, the online reader and the local inspection are synthetic, exactly as in test_email_intake.py (no IMAP, no Veris, no
.env, only the disposable PostgreSQL). The reader is faked to return what it returns for the identity pages (the resume with its
education certificate, the passport, the Aadhaar card); the OTHER documents are cut out of the real PDF by the real local split
engine (`bulk_candidate_import.split_by_content`, native text layer, no Tesseract).
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from sqlalchemy import select

from app.db.models import Attachment, AuditEntry, Candidate, EmailIngestionJob, MailMessage
from app.integrations.ocr import inspection
from app.services import bulk_candidate_import as bulk
from auto_assign import round_robin_on  # noqa: F401  (fixture)
import people_fixtures as pf
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_email_intake import intake, png, receipt, receipt_many, row_count, run, subject  # noqa: F401  (intake is a fixture)
from test_whatsapp_intake import receipt as whatsapp_receipt, setup  # noqa: F401  (setup is a fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("round_robin_on")

REAL_BUILD = subject.intake_documents.build_split   # test_email_intake's fixture stubs it; these tests use the real one
PERSON = pf.person("tharun")
NAME = f"{PERSON.given} {PERSON.surname}"
FIELDS = {"name": NAME, "phone": f"+91 {PERSON.phone}", "email": PERSON.email, "designation": "Welder"}
EVERYTHING = ["resume_1", "resume_2", "passport_data", "passport_back", "aadhaar_front", "degree", "offer", "evisa", "medical", "pcc",
              "air_ticket"]
# What the reader receives: the resume pages INCLUDING the degree (the page classifier types an education certificate as resume).
READER_PAGES = {"resume": [1, 2, 6], "passport": [3, 4], "aadhaar": [5]}
FILED_BY_SPLIT = {"education_certificate": [6], "signed_offer": [7], "visa": [8], "medical": [9], "pcc": [10], "air_ticket": [11]}
LABELS = {"education_certificate": "Education_certificate", "signed_offer": "Signed_offer", "visa": "Visa", "medical": "Medical",
          "pcc": "Police_clearance_certificate_PCC", "air_ticket": "Air_ticket"}


@pytest.fixture
def splitting(intake, monkeypatch):
    monkeypatch.setattr(subject.intake_documents, "build_split", REAL_BUILD)
    return intake


def everything(tokens=EVERYTHING, person="tharun") -> bytes:
    return pf.build_pdf([pf.page(person, token) for token in tokens], "text")


def reader_output(types, selected, fields=None):
    """What the document reader returns for these pages: `types` is the local classification of each page."""
    pages = [{"page_number": number, "status": "read", "document_type": kind, "method": "native"} for number, kind in enumerate(types, 1)]
    kinds = lambda wanted: [number for number, kind in enumerate(types, 1) if kind == wanted]  # noqa: E731
    report = {"page_count": len(types), "pages": pages, "resume_pages": kinds("resume"), "passport_pages": kinds("passport"),
              "aadhaar_pages": kinds("aadhaar"), "unread_pages": [], "unknown_pages": [], "source_format": "pdf", "complete": True}
    return {"status": "complete", "inspection": report, "warnings": [], "extractions": {
        kind: {"fields": {**FIELDS, **(fields or {})} if kind == "resume" else {"name": NAME},
               "raw": {"pages": [{"page_number": 1, "text": f"Synthetic {kind} evidence"}]}, "selected_pages": numbers,
               "source": {"local_inspection": {"selected_pages": numbers}, "selected_pages": numbers}}
        for kind, numbers in selected.items()}}


def everything_output(fields=None):
    return reader_output(["resume", "resume", "passport", "passport", "aadhaar", "resume", "other", "other", "other", "other", "other"],
                         READER_PAGES, fields)


def install_reader(monkeypatch, outputs):
    calls = []

    def extract(data, *, state, save, **kwargs):
        calls.append(data)
        output = deepcopy(outputs[data])
        state.update(source_sha256=hashlib.sha256(data).hexdigest(), inspection=deepcopy(output["inspection"]))
        save(state)
        return output
    monkeypatch.setattr(subject, "extract_attachment", extract)
    return calls


NOTE = ("Minutes of the weekly meeting about the canteen rota, the parking arrangements and the next newsletter; "
        "no document of the candidate is written on this sheet.")


def with_notes(data, *notes):
    """The PDF with plain, unclassifiable pages added at the end."""
    with fitz.open(stream=data, filetype="pdf") as document:
        for note in notes:
            document.new_page().insert_text((50, 80), note, fontsize=9)
        return document.tobytes(no_new_id=True)


def stored(env, row):
    return (Path(env.settings.storage_dir) / row.object_key).read_bytes()


def page_texts(data):
    with fitz.open(stream=data, filetype="pdf") as document:
        return [page.get_text() for page in document]


def slots(db, candidate_id):
    rows = {}
    for row in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id)
                          .order_by(Attachment.slot, Attachment.version)):
        rows.setdefault(row.slot, []).append(row)
    return rows


def audit_of(db, candidate_id):
    return {(entry.meta["slot"], entry.meta["version"]): entry.meta for entry in db.scalars(select(AuditEntry).where(
        AuditEntry.action == "Uploaded document", AuditEntry.entity_id == candidate_id))}


def piece_name(slot):
    return f"{bulk.name_slug(NAME)}-{LABELS[slot]}.pdf"


def uploads(db):
    return row_count(db, Attachment), row_count(db, AuditEntry)


# ---------------------------------------------------------------------------------------------------------- the single big PDF

def test_one_pdf_with_everything_files_every_document_in_its_own_field_and_loses_no_page(splitting, monkeypatch):
    data = everything()
    install_reader(monkeypatch, {data: everything_output()})
    job_id, part_id = receipt(splitting, data=data)

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed" and job.last_error is None, job.last_error
        assert row_count(db, Candidate) == 1
        candidate = db.get(Candidate, job.candidate_id)
        assert candidate.name == NAME and candidate.resume_text == "Synthetic resume evidence"
        rows = slots(db, candidate.id)
        assert set(rows) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}
        assert all(len(versions) == 1 and versions[0].version == 1 for versions in rows.values())
        # Nothing is lost: the whole original is kept as it came ...
        original = rows["source_bundle"][0]
        assert stored(splitting, original) == data and original.content_hash == hashlib.sha256(data).hexdigest()
        assert len(page_texts(stored(splitting, original))) == 11
        # ... the reader's documents are what they were (the resume holds its degree page) ...
        assert [len(page_texts(stored(splitting, rows[kind][0]))) for kind in ("resume", "passport", "aadhaar")] == [3, 2, 1]
        # ... and every other document is in its own field, with its own pages and the standard name.
        original_pages = page_texts(data)
        audit = audit_of(db, candidate.id)
        for slot, pages in FILED_BY_SPLIT.items():
            row = rows[slot][0]
            assert row.file_name == piece_name(slot) and row.content_type == "application/pdf" and row.uploaded_by is None
            assert hashlib.sha256(stored(splitting, row)).hexdigest() == row.content_hash
            assert page_texts(stored(splitting, row)) == [original_pages[number - 1] for number in pages]
            assert audit[(slot, 1)]["split_pages"] == pages and audit[(slot, 1)]["original_name"] == "misleading-name.jpg"
            assert audit[(slot, 1)]["email_message_id"] == job.message_id and audit[(slot, 1)]["attachment_id"] == row.id
        # Page accounting: every page is in the original AND in at least one field.
        in_fields = {number for pages in READER_PAGES.values() for number in pages} | {number for pages in FILED_BY_SPLIT.values() for number in pages}
        assert in_fields == set(range(1, 12))


def test_a_retried_commit_adds_no_duplicate_versions_and_does_not_read_the_pages_again(splitting, monkeypatch):
    data = everything()
    install_reader(monkeypatch, {data: everything_output()})
    job_id, _ = receipt(splitting, data=data)
    run(splitting, job_id)
    with splitting.sessions() as db:
        before = uploads(db)
        message_id = db.get(EmailIngestionJob, job_id).message_id
        assert before[0] == 10    # source_bundle, resume, passport, aadhaar and six pieces
    # The commit is repeated for the same receipt (a retry after a crash): the reader and the page reading must not run again.
    monkeypatch.setattr(subject, "extract_attachment", lambda *a, **kw: pytest.fail("the reader must not run again"))
    monkeypatch.setattr(bulk, "split_by_content", lambda *a, **kw: pytest.fail("the pages must not be read again"))
    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        job.status, job.candidate_id = "retry", None
        db.get(MailMessage, message_id).candidate_id = None
        db.commit()

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed" and row_count(db, Candidate) == 1
        assert row_count(db, Attachment) == before[0]
        assert row_count(db, AuditEntry) - before[1] == 1       # only the "Added email documents" line of the second commit
        assert all(len(versions) == 1 for versions in slots(db, job.candidate_id).values())


def test_two_attachments_resume_and_all_documents_are_each_processed(splitting, monkeypatch):
    resume = everything(["resume_1", "resume_2"])
    bundle = everything(["passport_data", "passport_back", "aadhaar_front", "degree", "offer", "evisa", "medical", "pcc", "air_ticket"])
    calls = install_reader(monkeypatch, {
        resume: reader_output(["resume", "resume"], {"resume": [1, 2]}),
        bundle: reader_output(["passport", "passport", "aadhaar", "other", "other", "other", "other", "other", "other"],
                              {"passport": [1, 2], "aadhaar": [3]}),
    })
    job_id, (resume_id, bundle_id) = receipt_many(splitting, [("resume.pdf", resume), ("all documents.pdf", bundle)])

    run(splitting, job_id)

    assert sorted(calls) == sorted([resume, bundle])
    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        rows = slots(db, job.candidate_id)
        assert set(rows) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}
        assert {stored(splitting, row) for row in rows["source_bundle"]} == {resume, bundle}
        audit = audit_of(db, job.candidate_id)
        for slot in FILED_BY_SPLIT:
            assert len(rows[slot]) == 1 and audit[(slot, 1)]["original_name"] == "all documents.pdf"
        # In the nine-page file the degree is page 4 and the ticket page 9; the plain resume adds no piece of its own.
        assert audit[("education_certificate", 1)]["split_pages"] == [4] and audit[("air_ticket", 1)]["split_pages"] == [9]
        assert "other" not in rows


def test_the_same_file_attached_twice_is_stored_and_read_once(splitting, monkeypatch):
    data = everything()
    install_reader(monkeypatch, {data: everything_output()})
    reads = []
    monkeypatch.setattr(bulk, "split_by_content", (lambda real: lambda *a, **kw: (reads.append(1), real(*a, **kw))[1])(bulk.split_by_content))
    job_id, _ = receipt_many(splitting, [("first.pdf", data), ("second copy.pdf", data)])

    run(splitting, job_id)

    assert len(reads) == 1
    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        rows = slots(db, job.candidate_id)
        assert all(len(versions) == 1 for versions in rows.values()) and set(rows) >= set(FILED_BY_SPLIT)


def test_pages_nobody_could_classify_go_to_other_documents_and_no_page_is_lost(splitting, monkeypatch):
    data = with_notes(everything(["resume_1", "resume_2", "passport_data"]), NOTE, NOTE + " Second sheet.")
    install_reader(monkeypatch, {data: reader_output(["resume", "resume", "passport", "other", "other"], {"resume": [1, 2], "passport": [3]})})
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        rows = slots(db, job.candidate_id)
        assert set(rows) == {"source_bundle", "resume", "passport", "other"}
        assert audit_of(db, job.candidate_id)[("other", 1)]["split_pages"] == [4, 5]
        assert len(page_texts(stored(splitting, rows["other"][0]))) == 2 and rows["other"][0].file_name == f"{bulk.name_slug(NAME)}-Other_documents.pdf"
        assert len(page_texts(stored(splitting, rows["source_bundle"][0]))) == 5


def test_a_page_the_reader_left_out_is_still_in_a_field(splitting, monkeypatch):
    # The reader was given only the first passport page (it may omit a duplicate); the second page is not lost.
    data = everything(["resume_1", "resume_2", "passport_data", "passport_back"])
    install_reader(monkeypatch, {data: reader_output(["resume", "resume", "passport", "passport"], {"resume": [1, 2], "passport": [3]})})
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        rows = slots(db, job.candidate_id)
        assert page_texts(stored(splitting, rows["passport"][0])) == page_texts(data)[2:4]
        assert set(rows) == {"source_bundle", "resume", "passport"}


@pytest.mark.parametrize("failure", ["engine_raises", "reading_fails"])
def test_a_failing_split_still_creates_the_candidate_and_keeps_the_original(splitting, monkeypatch, failure):
    data = everything()
    install_reader(monkeypatch, {data: everything_output()})
    if failure == "engine_raises":
        monkeypatch.setattr(bulk, "split_by_content", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    else:   # the engine's own never-raises path: the local reading fails or times out
        monkeypatch.setattr(inspection, "inspect_document", lambda *a, **kw: (_ for _ in ()).throw(inspection.InspectionError("timed out", retryable=True)))
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed" and row_count(db, Candidate) == 1
        assert "could not be split into its documents" in job.last_error and "stored whole" in job.last_error
        candidate = db.get(Candidate, job.candidate_id)
        assert not candidate.needs_review or candidate.status is not None
        rows = slots(db, candidate.id)
        assert set(rows) == {"source_bundle", "resume", "passport", "aadhaar"}
        assert stored(splitting, rows["source_bundle"][0]) == data


def test_an_email_for_an_existing_candidate_adds_the_documents_as_new_versions_without_overwriting(splitting, monkeypatch):
    data = everything()
    output = everything_output({"current_address": "Reader address"})
    output["inspection"]["document_pages"] = {"resume": [1, 2], "passport": [3, 4], "aadhaar": [5]}
    output["extractions"]["passport"]["selected_pages"] = [3]  # only the identity page was necessary to read the details
    install_reader(monkeypatch, {data: output})
    with splitting.sessions() as db:
        db.add(Candidate(id="known", ref="CND-9", name=NAME, phone=PERSON.phone, email=PERSON.email, owner_id="staff-b",
                         current_address="Typed by a recruiter", source=subject.Source.walk_in))
        db.add(Attachment(entity_type="candidate", entity_id="known", slot="visa", version=1, file_name="old-visa.pdf", size_kb=1,
                          content_type="application/pdf", object_key="old/visa.pdf", content_hash="0" * 64))
        db.commit()
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        assert row_count(db, Candidate) == 1 and job.candidate_id == "known"
        candidate = db.get(Candidate, "known")
        assert candidate.owner_id == "staff-b" and candidate.current_address == "Typed by a recruiter"
        rows = slots(db, "known")
        assert [row.version for row in rows["visa"]] == [1, 2] and rows["visa"][0].file_name == "old-visa.pdf"   # the old one is kept
        assert rows["visa"][1].file_name == piece_name("visa")
        assert set(rows) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}
        original = page_texts(data)
        for kind, numbers in {**output["inspection"]["document_pages"], **FILED_BY_SPLIT}.items():
            assert page_texts(stored(splitting, rows[kind][-1])) == [original[n - 1] for n in numbers]


# ------------------------------------------------------------------------------------------------------ images, Word, single pages

def test_an_image_stays_whole_in_the_field_its_content_says(splitting, monkeypatch):
    visa_text = page_texts(everything())[7]
    picture = png(420, 420)
    resume = everything(["resume_1", "resume_2"])
    install_reader(monkeypatch, {
        resume: reader_output(["resume", "resume"], {"resume": [1, 2]}),
        picture: {"status": "ignored", "inspection": reader_output(["other"], {})["inspection"], "warnings": [], "extractions": {}},
    })
    monkeypatch.setattr(inspection, "inspect_document", lambda *a, **kw: SimpleNamespace(
        pages=[SimpleNamespace(text=visa_text, status="read")]))
    job_id, _ = receipt_many(splitting, [("cv.pdf", resume), ("scan.jpg", picture)])

    run(splitting, job_id)

    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        rows = slots(db, job.candidate_id)
        assert set(rows) == {"source_bundle", "resume", "visa"}
        visa = rows["visa"][0]
        assert stored(splitting, visa) == picture and visa.content_type == "image/png" and visa.file_name == f"{bulk.name_slug(NAME)}-Visa.png"
        assert audit_of(db, job.candidate_id)[("visa", 1)]["split_pages"] == [1]


def test_word_single_page_and_unreadable_files_are_not_split():
    store = lambda index, content: pytest.fail("nothing to store")  # noqa: E731
    docx = b"PK\x03\x04" + b"\x00" * 30
    for data in (docx, everything(["resume_1"]), b"not a document at all", b""):
        state = REAL_BUILD(data, store, original_name="x.bin")
        assert state["pieces"] == [] and state["source_sha256"] == hashlib.sha256(data).hexdigest()


def test_a_whole_file_of_one_kind_is_filed_in_that_field_unless_the_reader_took_it():
    keys = []
    store = lambda index, content: keys.append(index) or f"key-{index}"  # noqa: E731
    visas = everything(["evisa", "evisa"])
    state = REAL_BUILD(visas, store, extracted=False, original_name="visa.pdf")
    assert [(piece["slots"], piece["pages"]) for piece in state["pieces"]] == [(["visa"], [1, 2])] and state["note"] is None
    # An attachment nothing could be told about is the original bundle alone when the reader has taken pages of it.
    with fitz.open() as document:
        document.new_page().insert_text((50, 80), NOTE, fontsize=9)
        document.new_page().insert_text((50, 80), NOTE + " Second sheet.", fontsize=9)
        nothing = document.tobytes(no_new_id=True)
    assert REAL_BUILD(nothing, store, covered=[1, 2], extracted=True)["pieces"] == []
    assert [piece["slots"] for piece in REAL_BUILD(nothing, store, extracted=False)["pieces"]] == [["other"]]


def test_standalone_education_is_filed_without_creating_a_resume_attachment():
    output = reader_output(["resume"], {"resume": [1]})
    output["inspection"]["document_pages"] = {"resume": [], "passport": [], "aadhaar": []}
    assert subject.intake_documents.filing_pages(output) == {}
    contents = []
    data = everything(["degree"])
    result = REAL_BUILD(data, lambda index, content: contents.append(content) or f"piece-{index}")
    assert [(p["slots"], p["pages"]) for p in result["pieces"]] == [(["education_certificate"], [1])]
    assert contents == [data]


# --------------------------------------------------------------------------------------------------------------------- WhatsApp

def test_whatsapp_combined_pdf_is_filed_the_same_way(setup, monkeypatch):
    from app.services.whatsapp_intake import process_message
    from app.db.models import WhatsAppMessage
    monkeypatch.setattr(subject.intake_documents, "build_split", REAL_BUILD)
    sessions, settings = setup
    data = everything()
    message_id = whatsapp_receipt(sessions, settings)
    output = everything_output()
    output["inspection"]["document_pages"] = {"resume": [1, 2], "passport": [3, 4], "aadhaar": [5]}
    output["extractions"]["passport"]["selected_pages"] = [3]

    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (data, "application/pdf"),
                    extract=lambda *_, **__: deepcopy(output))

    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        rows = slots(db, row.candidate_id)
        assert set(rows) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}
        for kind, pages in output["inspection"]["document_pages"].items():
            content = (Path(settings.storage_dir) / rows[kind][0].object_key).read_bytes()
            assert page_texts(content) == [page_texts(data)[n - 1] for n in pages]
        audit = audit_of(db, row.candidate_id)
        for slot, pages in FILED_BY_SPLIT.items():
            assert audit[(slot, 1)]["split_pages"] == pages and audit[(slot, 1)]["whatsapp_message_id"] == message_id
            assert rows[slot][0].file_name == piece_name(slot)
        count = row_count(db, Attachment)
    # Replaying the completed receipt adds nothing.
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: pytest.fail("no refetch"))
    with sessions() as db:
        assert row_count(db, Attachment) == count
