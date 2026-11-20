"""After a scan with several documents is read, every page is filed: each kind in its own slot (named like every stored file), the
pages that are none of them under Other documents, blank pages only listed, the whole scan kept as the source evidence, and nothing
filed twice (by page content against what a slot already holds, and when the same reading is applied again).

DB-backed (disposable PostgreSQL, the application-form fixture); synthetic documents; no real reader.
"""
import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob
from app.services import application_form, document_storage
from app.services.bulk_candidate_import import cut_pdf_pages
from test_application_form import as_user, desk, form, url  # noqa: F401  (fixtures and helpers)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_extraction import pdf
from test_document_identity import _job_output, _passport_of, _resume_of

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango "
         "uniform victor whiskey xray yankee zulu amber basalt cobalt dahlia ember fjord garnet harbor indigo jasper kestrel lagoon marble "
         "nectar orchid pewter quartz russet saffron tundra umber velvet willow yarrow zephyr").split()


def words(start: int, count: int = 36) -> str:
    """Enough distinct words (the duplicate check needs 25) for one synthetic page; a different start is a different page."""
    return " ".join(WORDS[(start + n) % len(WORDS)] for n in range(count))


TEXTS = {
    1: "Synthetic resume page one: welder with ten years of experience on offshore sites.",
    2: "Synthetic resume page two: certificates, references and a declaration signed at the end.",
    3: "Synthetic passport data page. " + words(0),
    4: "Synthetic passport address page. " + words(7),
    5: "Synthetic aadhaar card. " + words(13),
    6: "Page of the file that is read fine but is nothing the classifier knows about (kept as plain text).",
    7: "Another unrelated medical certificate page, kept with the others under other documents.",
    8: "",
}
PAGES = [{"page_number": n, "status": "blank" if n == 8 else "read",
          "document_type": {1: "resume", 2: "resume", 3: "passport", 4: "passport", 5: "aadhaar", 6: "unknown", 7: "other", 8: "other"}[n]}
         for n in range(1, 9)]


@pytest.fixture
def storage(desk, monkeypatch):
    monkeypatch.setattr(application_form, "get_settings", lambda: desk.settings)
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    return desk


def whole_scan() -> bytes:
    return pdf(*[TEXTS[n] or " " for n in range(1, 9)])


def store(env, data: bytes, name: str) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    key = document_storage.put_bytes(env.settings, f"test/{uuid.uuid4()}-{name}", data, "application/pdf")
    return {"object_key": key, "file_name": name, "content_type": "application/pdf", "size_kb": 1, "content_hash": digest}


def attachment(db, env, slot, name, data, version=None):
    document = store(env, data, name)
    count = len(list(db.scalars(select(Attachment.id).where(Attachment.entity_id == "candidate", Attachment.slot == slot))))
    row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id="candidate", slot=slot, version=version or count + 1, file_name=name,
                     size_kb=1, content_type="application/pdf", object_key=document["object_key"], content_hash=document["content_hash"], uploaded_by="staff")
    db.add(row)
    db.flush()
    return row


def reading(env, *, slot="resume", other=True, existing=None):
    """The scan is stored as the candidate's Resume, read, and its derived pages are in the job state (as the worker leaves them)."""
    whole = whole_scan()
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name = "Ganesh Ramani"
        if existing:
            existing(db, env)
        source = attachment(db, env, slot, "Ganesh_Ramani-Resume.pdf", whole)
        pieces = {"resume": [1, 2], "passport": [3, 4], "aadhaar": [5], **({"other": [6, 7]} if other else {})}
        documents = {kind: {**store(env, cut_pdf_pages(whole, pages), kind + ".pdf"), "selected_pages": pages} for kind, pages in pieces.items()}
        extractions = {"resume": {"selected_pages": [1, 2], "raw": _resume_of("Ganesh Ramani"), "fields": {}},
                       "passport": {"selected_pages": [3, 4], "raw": _passport_of("GANESH", "RAMANI", "K1234567"), "fields": {}},
                       "aadhaar": {"selected_pages": [5], "raw": {"aadhaar": {"name": "GANESH RAMANI", "aadhaar_number": "000000001234"}}, "fields": {}}}
        output = {"status": "complete", "coverage": {"complete": True, "pages": {"not_sent": []}}, "extractions": extractions,
                  "inspection": {"page_count": 8, "pages": PAGES, "resume_pages": [1, 2], "passport_pages": [3, 4], "aadhaar_pages": [5]}}
        job = DocumentExtractionJob(id=str(uuid.uuid4()), owner_id="staff", group_id=str(uuid.uuid4()), kind=slot, filename=source.file_name,
                                    content_type="application/pdf", object_key=source.object_key, content_hash=source.content_hash, size_kb=1,
                                    status="completed", candidate_id="candidate", attachment_id=source.id,
                                    state={"output": json.loads(json.dumps(output, default=str)), "documents": documents},
                                    draft={"applicationForm": {"candidateId": "candidate", "slot": slot, "attachmentId": source.id}})
        db.add(job)
        db.commit()
        return source.id, job.id, output


def apply(env, job_id, output):
    with env.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        application_form.apply_job_output(db, job, output)
        db.commit()
        return job.draft["applicationForm"]


def files(env):
    with env.sessions() as db:
        rows = db.scalars(select(Attachment).where(Attachment.entity_id == "candidate").order_by(Attachment.slot, Attachment.version)).all()
        return [(row.slot, row.version, row.file_name) for row in rows]


def test_local_pages_are_attached_without_a_matching_provider_result(storage):
    _source, job_id, output = reading(storage)
    output["inspection"]["document_pages"] = {"resume": [1, 2], "passport": [3, 4], "aadhaar": [5]}
    output["extractions"].pop("aadhaar")
    output["extractions"]["passport"]["selected_pages"] = [3]
    form = apply(storage, job_id, output)
    assert {"resume", "passport", "aadhaar"} <= set(form["attachedDocuments"])
    with storage.sessions() as db:
        for kind, pages in output["inspection"]["document_pages"].items():
            row = db.get(Attachment, form["attachedDocuments"][kind])
            assert document_storage.read_bytes(storage.settings, row.object_key) == cut_pdf_pages(whole_scan(), pages)


def test_every_derived_file_is_named_like_the_others_and_the_whole_scan_becomes_the_source_evidence(storage):
    source_id, job_id, output = reading(storage)
    meta = apply(storage, job_id, output)

    assert files(storage) == [
        ("aadhaar", 1, "Ganesh_Ramani-Aadhaar_card.pdf"), ("other", 1, "Ganesh_Ramani-Other_documents.pdf"),
        ("passport", 1, "Ganesh_Ramani-Passport.pdf"), ("resume", 2, "Ganesh_Ramani-Resume.pdf"),
        ("source_bundle", 1, "Ganesh_Ramani-Resume.pdf")]
    assert not any(name in ("resume.pdf", "passport.pdf", "aadhaar.pdf", "other.pdf") for _slot, _v, name in files(storage))
    with storage.sessions() as db:
        bundle = db.get(Attachment, source_id)
        assert bundle.slot == "source_bundle" and bundle.content_hash == hashlib.sha256(whole_scan()).hexdigest()   # the whole scan, untouched
        resume = db.scalar(select(Attachment).where(Attachment.slot == "resume"))
        assert resume.id == meta["attachedDocuments"]["resume"] and resume.id != source_id        # the Resume slot holds the resume pages only
        assert resume.content_hash != bundle.content_hash
    assert set(meta["attachedDocuments"]) == {"resume", "passport", "aadhaar", "other"}


def test_the_pages_of_other_kinds_and_the_blank_page_are_accounted_for_in_plain_words(storage):
    _source, job_id, output = reading(storage)
    notes = apply(storage, job_id, output)["result"]["coverage"]["filing"]
    assert "Pages 6, 7 of this file were filed under Other documents." in notes
    assert "Page 6 was filed under Other documents (not recognised as a resume page)." in notes
    assert "Page 8 is blank and was not filed." in notes
    with storage.sessions() as db:
        other = db.scalar(select(Attachment).where(Attachment.slot == "other"))
        import pymupdf as fitz
        with fitz.open(stream=document_storage.read_bytes(storage.settings, other.object_key), filetype="pdf") as opened:
            assert opened.page_count == 2 and "classifier" in opened[0].get_text() and "medical" in opened[1].get_text()   # pages 6 and 7, merged as one file
        audit = db.scalar(select(AuditEntry.meta).where(AuditEntry.action == "Separated a document from a combined upload",
                                                        AuditEntry.meta["slot"].astext == "other"))
        assert audit["split_pages"] == [6, 7] and audit["original_name"] == "Ganesh_Ramani-Resume.pdf"


def test_reading_again_adds_nothing_twice(storage):
    _source, job_id, output = reading(storage)
    first = apply(storage, job_id, output)
    before = files(storage)
    second = apply(storage, job_id, output)
    third = apply(storage, job_id, output)
    assert files(storage) == before
    assert second["attachedDocuments"] == first["attachedDocuments"] == third["attachedDocuments"]
    with storage.sessions() as db:
        assert len(list(db.scalars(select(AuditEntry.id).where(AuditEntry.action == "Separated a document from a combined upload")))) == 4


def passport_cut_from_another_scan(db, env):
    """The candidate already holds the same passport (pages 3-4), cut from the signed-offer scan (other bytes, same pages)."""
    scan = pdf("Signed offer letter of the synthetic company, first page of the other scan.", TEXTS[3], TEXTS[4])
    return attachment(db, env, "passport", "Ganesh_Ramani-Passport.pdf", cut_pdf_pages(scan, [2, 3]))


def aadhaar_cut_from_another_scan(db, env):
    scan = pdf("Signed offer letter again.", TEXTS[5])
    return attachment(db, env, "aadhaar", "Ganesh_Ramani-Aadhaar_card.pdf", cut_pdf_pages(scan, [2]))


def test_the_same_passport_and_aadhaar_already_filed_are_not_filed_again(storage):
    ids = {}

    def existing(db, env):
        ids["passport"] = passport_cut_from_another_scan(db, env).id
        ids["aadhaar"] = aadhaar_cut_from_another_scan(db, env).id

    _source, job_id, output = reading(storage, existing=existing)
    meta = apply(storage, job_id, output)
    rows = files(storage)
    assert [row for row in rows if row[0] == "passport"] == [("passport", 1, "Ganesh_Ramani-Passport.pdf")]
    assert [row for row in rows if row[0] == "aadhaar"] == [("aadhaar", 1, "Ganesh_Ramani-Aadhaar_card.pdf")]
    assert meta["attachedDocuments"]["passport"] == ids["passport"] and meta["attachedDocuments"]["aadhaar"] == ids["aadhaar"]
    notes = meta["result"]["coverage"]["filing"]
    assert "Passport pages 3, 4 of this file are already filed in Ganesh_Ramani-Passport.pdf; not added again." in notes
    assert "Aadhaar card page 5 of this file is already filed in Ganesh_Ramani-Aadhaar_card.pdf; not added again." in notes
    assert [row for row in rows if row[0] in ("resume", "other", "source_bundle")]               # the rest is filed as usual
    apply(storage, job_id, output)
    assert files(storage) == rows                                                               # and a second reading changes nothing


def test_a_passport_that_really_differs_is_filed_as_a_new_version(storage):
    def existing(db, env):
        attachment(db, env, "passport", "Ganesh_Ramani-Passport.pdf", pdf("A different synthetic passport: other pages entirely, nothing in common.",
                                                                             "Second page of that different passport, with its own wording."))
    _source, job_id, output = reading(storage, existing=existing)
    apply(storage, job_id, output)
    assert [row[:2] for row in files(storage) if row[0] == "passport"] == [("passport", 1), ("passport", 2)]


def test_a_resume_that_is_the_whole_scan_is_not_split_off(storage):
    """A file that is all resume (no other kind, no unrecognised page) stays the Resume file as it came."""
    whole = pdf(TEXTS[1], TEXTS[2])
    with storage.sessions() as db:
        db.get(Candidate, "candidate").name = "Ganesh Ramani"
        source = attachment(db, storage, "resume", "Ganesh_Ramani-Resume.pdf", whole)
        output = {"status": "complete", "coverage": {"complete": True, "pages": {"not_sent": []}},
                  "extractions": {"resume": {"selected_pages": [1, 2], "raw": _resume_of("Ganesh Ramani"), "fields": {}}},
                  "inspection": {"page_count": 2, "resume_pages": [1, 2], "passport_pages": [], "aadhaar_pages": [],
                                 "pages": [{"page_number": 1, "status": "read", "document_type": "resume"}, {"page_number": 2, "status": "read", "document_type": "resume"}]}}
        job = DocumentExtractionJob(id=str(uuid.uuid4()), owner_id="staff", group_id=str(uuid.uuid4()), kind="resume", filename=source.file_name,
                                    content_type="application/pdf", object_key=source.object_key, content_hash=source.content_hash, size_kb=1,
                                    status="completed", candidate_id="candidate", attachment_id=source.id,
                                    state={"output": json.loads(json.dumps(output, default=str)),
                                           "documents": {"resume": {**store(storage, whole, "resume.pdf"), "selected_pages": [1, 2]}}},
                                    draft={"applicationForm": {"candidateId": "candidate", "slot": "resume", "attachmentId": source.id}})
        db.add(job)
        db.commit()
        job_id = job.id
    apply(storage, job_id, output)
    assert files(storage) == [("resume", 1, "Ganesh_Ramani-Resume.pdf")]


def test_the_same_passport_scanned_differently_is_still_caught_by_its_pages(storage):
    """Not the same bytes (another scan of the same pages, set in another size): the pages are compared by content."""
    import pymupdf as fitz

    def another_scan(db, env):
        with fitz.open() as document:
            for number in (3, 4):
                document.new_page().insert_textbox(fitz.Rect(30, 60, 560, 790), TEXTS[number], fontsize=10.5)
            attachment(db, env, "passport", "Ganesh_Ramani-Passport.pdf", document.tobytes(no_new_id=True))

    _source, job_id, output = reading(storage, existing=another_scan)
    notes = apply(storage, job_id, output)["result"]["coverage"]["filing"]
    assert [row for row in files(storage) if row[0] == "passport"] == [("passport", 1, "Ganesh_Ramani-Passport.pdf")]
    assert "Passport pages 3, 4 of this file are already filed in Ganesh_Ramani-Passport.pdf; not added again." in notes
