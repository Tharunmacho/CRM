"""Which stored candidate files the profile / desk snapshot offer "Extract details" for (document_reading.pending_expression).

DB-backed (disposable PostgreSQL), through the bulk import, whose files wait to be read.
"""
from sqlalchemy import select

from app.db.models import Attachment, AuditEntry, DocumentExtractionJob
from app.services.document_reading import pending_expression
from test_bulk_candidate_import import _read_again, env, send  # noqa: F401  (the fixture and helpers)
from test_database_naming import schema_database  # noqa: F401

FILES = ["Resume.pdf", "Passport.pdf", "Visa.pdf", "Medical.pdf"]


def _pending(env, candidate_id):
    with env.sessions() as db:
        return {slot: flag for slot, flag in db.execute(select(Attachment.slot, pending_expression()).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id))}


def _make_old_style(env, candidate_id):
    """An import made before the import wrote extraction_deferred: the upload audit carries bulk_import and nothing else about reading."""
    with env.sessions() as db:
        rows = list(db.scalars(select(AuditEntry).where(AuditEntry.action == "Uploaded document", AuditEntry.entity_id == candidate_id)))
        assert rows and all(row.meta.get("bulk_import") for row in rows)
        for row in rows:
            row.meta = {key: value for key, value in row.meta.items() if key != "extraction_deferred"}
        db.commit()


def test_a_new_style_import_offers_extract_details_for_its_readable_slots_only(env):
    result = send(env, "Anil", FILES, {"phone": "9876500701"})
    assert _pending(env, result["candidateId"]) == {"resume": True, "passport": True, "visa": False, "medical": False}


def test_an_import_made_before_the_deferred_flag_still_offers_extract_details_until_it_is_read(env):
    result = send(env, "Anil", FILES, {"phone": "9876500702"})
    candidate_id = result["candidateId"]
    _make_old_style(env, candidate_id)
    assert _pending(env, candidate_id) == {"resume": True, "passport": True, "visa": False, "medical": False}
    assert _read_again(env, [candidate_id]) == 2
    assert _pending(env, candidate_id) == {"resume": False, "passport": False, "visa": False, "medical": False}


def test_one_stored_file_in_several_slots_is_read_once_and_clears_every_slot_it_sits_in(env):
    result = send(env, "Ravi", ["Ravi-signed offer,pp,aadhar.pdf", "Ravi-Steel fixer.pdf"], {"phone": "9876500703"})
    candidate_id = result["candidateId"]
    for old_style in (False, True):
        if old_style:
            _make_old_style(env, candidate_id)
        before = _pending(env, candidate_id)
        assert before["passport"] and before["aadhaar"] and before["resume"] and not before["signed_offer"]
        if not old_style:
            assert _read_again(env, [candidate_id]) == 2
            after = _pending(env, candidate_id)
            assert not any(after.values())      # the scan's second slot (aadhaar) is covered by the one job for its file
            with env.sessions() as db:          # forget the jobs again, to try the old-style rows
                for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)):
                    db.delete(job)
                db.commit()


def test_a_job_for_the_same_stored_file_under_another_attachment_counts_as_read_for_an_old_style_import(env):
    result = send(env, "Anil", ["Resume.pdf"], {"phone": "9876500704"})
    candidate_id = result["candidateId"]
    _make_old_style(env, candidate_id)
    assert _pending(env, candidate_id) == {"resume": True}
    with env.sessions() as db:
        file = db.scalar(select(Attachment).where(Attachment.entity_id == candidate_id, Attachment.slot == "resume"))
        job = DocumentExtractionJob(owner_id="staff", group_id="g", kind="resume", filename=file.file_name, content_type="application/pdf",
                                    object_key=file.object_key, content_hash=file.content_hash or "h", size_kb=1,
                                    candidate_id=candidate_id, attachment_id=None, status="pending")
        db.add(job)
        db.commit()
    assert _pending(env, candidate_id) == {"resume": False}


def test_a_failed_reading_is_offered_again_and_a_plain_upload_is_never_pending(env):
    result = send(env, "Anil", ["Resume.pdf", "Medical.pdf"], {"phone": "9876500705"})
    candidate_id = result["candidateId"]
    _make_old_style(env, candidate_id)
    assert _read_again(env, [candidate_id]) == 1
    with env.sessions() as db:
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)):
            job.status = "failed"
        db.commit()
    assert _pending(env, candidate_id) == {"resume": True, "medical": False}
    # a candidate document stored by an ordinary upload (no bulk_import, no deferral) is not offered
    with env.sessions() as db:
        for row in db.scalars(select(AuditEntry).where(AuditEntry.action == "Uploaded document", AuditEntry.entity_id == candidate_id)):
            row.meta = {key: value for key, value in row.meta.items() if key != "bulk_import"}
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)):
            db.delete(job)
        db.commit()
    assert _pending(env, candidate_id) == {"resume": False, "medical": False}
