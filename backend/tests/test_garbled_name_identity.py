"""A name the reader could not read is not another person; a document cut from a scan already accepted is not rejected on a name.

Synthetic names only. The pure rules (`identity_findings`) and the whole reading outcome (`apply_job_output` against the disposable
PostgreSQL of the application-form fixture).
"""
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob
from app.services import application_form, form_extraction as fx
from app.services.candidate_review import OCR_FLAG_ACTION
from app.services.document_identity import (IDENTITY_REJECTED_ACTION, IDENTITY_REVIEW_MESSAGE, identity_findings, name_readable)
from test_application_form import as_user, desk, form, url  # noqa: F401  (fixtures and helpers)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_identity import _job_output, _passport_of, _resume_of, candidate as plain_candidate
from test_identity_confirmation import _file, _job, _quiet  # noqa: F401  (autouse fixture)

GARBAGE = "GguGoof m uLL"          # a Tamil / English card whose name line was read as Latin nonsense


def aadhaar_of(name, **extra):
    return {"aadhaar": {"name": name, "aadhaar_number": "000000001234", "date_of_birth": "1992-03-04", "gender": "male",
                        "address": "S/O Murugan, 12 Example Street, Example Town", **extra}}


def read(env, output, *, slot="aadhaar", name="Ganesh_Ramani-Aadhaar.pdf", before=None, audit=None):
    """A document read the way the worker does it. Returns (attachment id, job id)."""
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name = "Ganesh Ramani"
        if before:
            before(candidate)
        attachment = _file(db, slot, name)
        job = _job(db, attachment, output)
        if audit:
            audit(db, attachment)
            db.flush()
        application_form.apply_job_output(db, job, output)
        db.commit()
        return attachment.id, job.id


# ------------------------------------------------------------------------------ what counts as a name


@pytest.mark.parametrize("name", [GARBAGE, "Ggugoof m ull", "xqzvk Mrtn", "AbcDefG Hij", "Rr uLL"])
def test_junk_names_are_unreadable_on_an_aadhaar_card(name):
    assert name_readable(name, "aadhaar") is False


@pytest.mark.parametrize("name", ["Ganesh Ramani", "GANESH RAMANI", "Priya Dharshini K", "Thirunavukkarasu Murugesan", "R Ganesh", ""])
def test_real_names_and_an_absent_name_are_readable(name):
    assert name_readable(name, "aadhaar") is True and name_readable(name, "passport") is True


@pytest.mark.parametrize("name", ["Anne-Marie O'Neil", "Fiona McKay", "Mary-Jane Van Der Berg"])
def test_names_with_capitals_inside_are_not_taken_for_junk(name):
    assert name_readable(name, "passport") is True       # (a card is judged strictly by the name lexicon; these are not Indian names)


def test_junk_is_never_a_different_person():
    person = plain_candidate("Ganesh Ramani")
    assert identity_findings(person, {"name": GARBAGE}, kind="aadhaar") == []
    assert identity_findings(person, {"name": GARBAGE}) == []                        # whatever the kind
    assert [f["field"] for f in identity_findings(person, {"name": "Ramesh Kumar"}, kind="aadhaar")] == ["name"]   # a real name still is


def test_a_stored_junk_name_does_not_make_the_next_document_a_stranger():
    person = plain_candidate("Folder Name")
    person.field_sources = {fx.PROVISIONAL_NAME: True}
    person.ocr_data = {"aadhaar": {"aadhaar": {"name": GARBAGE, "aadhaar_number": "000000001234"}}}
    assert identity_findings(person, {"name": "Ganesh Ramani"}, kind="passport") == []


# ------------------------------------------------------------------------------ cut from a scan that was already accepted


def test_a_clear_name_difference_alone_does_not_reject_a_piece_of_a_scan_already_accepted():
    person = plain_candidate("Ganesh Ramani")
    values = {"name": "Ramesh Kumar"}
    assert [f["field"] for f in identity_findings(person, values, kind="aadhaar")] == ["name"]
    assert identity_findings(person, values, kind="aadhaar", same_source=True) == []                 # no second signal differs


def test_a_second_signal_brings_the_rejection_back_for_a_clearly_different_name():
    person = plain_candidate("Ganesh Ramani")
    person.passport_no, person.dob = "K1234567", "1990-01-01"
    assert [f["field"] for f in identity_findings(person, {"name": "Ramesh Kumar", "passport_no": "Z9999999"}, same_source=True)] == ["name", "passport_no"]
    assert [f["field"] for f in identity_findings(person, {"name": "Ramesh Kumar", "dob": "1985-05-05"}, same_source=True)] == ["name"]
    assert identity_findings(person, {"name": "Ramesh Kumar", "dob": "1990-01-01"}, same_source=True) == []   # the birth date agrees


@pytest.mark.parametrize("incoming", ["Ganeshkumar Ramani", "Ganesh Ramanian", "Ramani", "Ganesh R"])
def test_a_slip_or_an_extension_is_never_clearly_another_person_even_with_a_second_signal(incoming):
    person = plain_candidate("Ganesh Ramani")
    person.dob = "1990-01-01"
    assert identity_findings(person, {"name": incoming, "dob": "1985-05-05"}, same_source=True) == []


# ------------------------------------------------------------------------------ the whole reading


def test_the_junk_name_aadhaar_is_accepted_with_its_other_fields_and_a_soft_note(desk):
    attachment_id, job_id = read(desk, _job_output(aadhaar=aadhaar_of(GARBAGE)))
    with desk.sessions() as db:
        candidate, job = db.get(Candidate, "candidate"), db.get(DocumentExtractionJob, job_id)
        assert job.status == "completed" and job.last_error is None
        assert candidate.name == "Ganesh Ramani"                                       # never replaced by junk
        assert candidate.father_name == "Murugan" and candidate.dob is not None and candidate.current_address
        assert GARBAGE not in json.dumps(candidate.ocr_data)                              # stored as "not read", not as a name
        assert candidate.ocr_data["aadhaar"]["aadhaar"]["name"] is None and candidate.ocr_data["aadhaar"]["aadhaar"]["name_not_read"] is True
        saved = job.draft["applicationForm"]["result"]
        assert saved["status"] == "needs_review" and "identityMismatch" not in saved and saved["nameUnclear"] == ["aadhaar"]
        assert saved["message"] == "The name on the card could not be read clearly (check it against the document)."
        assert saved["coverage"]["issues"] == [saved["message"]]
        flags = [meta for meta in db.scalars(select(AuditEntry.meta).where(AuditEntry.action == OCR_FLAG_ACTION))]
        assert any("name_unclear" in meta["reasons"] and meta["attachment_id"] == attachment_id for meta in flags)
        assert not list(db.scalars(select(AuditEntry).where(AuditEntry.action == IDENTITY_REJECTED_ACTION)))
    view = form(desk)["documents"]["aadhaar"]
    assert view["identityMismatch"] is False and view["uploaded"] is True
    assert view["extraction"]["status"] == "needs_review" and view["extraction"]["message"].startswith("The name on the card")


def test_a_real_different_persons_aadhaar_is_still_rejected(desk):
    attachment_id, job_id = read(desk, _job_output(aadhaar=aadhaar_of("RAMESH KUMAR")))
    with desk.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
        assert db.get(Candidate, "candidate").father_name is None
    assert form(desk)["documents"]["aadhaar"]["identityMismatch"] is True


def sibling_scan(db, *, passport_status="completed"):
    """The candidate already holds a passport cut from the SAME scan as the Aadhaar piece, read and accepted."""
    passport = _file(db, "passport", "Ganesh_Ramani-Passport.pdf")
    job = _job(db, passport, _job_output(passport=_passport_of("GANESH", "RAMANI", "K1234567")))
    job.status = passport_status
    db.add(AuditEntry(action="Uploaded document", target=passport.file_name, entity_type="candidate", entity_id="candidate",
                      meta={"attachment_id": passport.id, "slot": "passport", "original_name": "signed offer, pp, aadhaar.pdf", "split_pages": [3, 4]}))
    return passport


def cut_from_the_same_scan(db, attachment):
    db.add(AuditEntry(action="Uploaded document", target=attachment.file_name, entity_type="candidate", entity_id="candidate",
                      meta={"attachment_id": attachment.id, "slot": "aadhaar", "original_name": "signed offer, pp, aadhaar.pdf", "split_pages": [2]}))


def test_a_piece_of_a_scan_already_accepted_is_not_rejected_on_a_name_alone(desk):
    def accepted_sibling(db, attachment):
        sibling_scan(db)
        cut_from_the_same_scan(db, attachment)
    attachment_id, job_id = read(desk, _job_output(aadhaar=aadhaar_of("RAMESH KUMAR")), audit=accepted_sibling)
    with desk.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        assert job.status == "completed" and db.get(Candidate, "candidate").father_name == "Murugan"
    assert form(desk)["documents"]["aadhaar"]["identityMismatch"] is False


def test_the_same_piece_with_no_accepted_sibling_is_rejected(desk):
    attachment_id, job_id = read(desk, _job_output(aadhaar=aadhaar_of("RAMESH KUMAR")), audit=lambda db, attachment: cut_from_the_same_scan(db, attachment))
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review"


def test_a_piece_of_such_a_scan_is_rejected_when_the_birth_date_differs_too(desk):
    def differs(candidate):
        candidate.dob = fx._d("1990-01-01", "birth")
    def accepted_sibling(db, attachment):
        sibling_scan(db)
        cut_from_the_same_scan(db, attachment)
    attachment_id, job_id = read(desk, _job_output(aadhaar=aadhaar_of("RAMESH KUMAR")), before=differs, audit=accepted_sibling)
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review"
    reasons = form(desk)["documents"]["aadhaar"]["extraction"]["identityReasons"]
    assert [reason["field"] for reason in reasons] == ["name"]


def test_a_rejected_sibling_is_no_evidence(desk):
    def rejected_sibling(db, attachment):
        passport = sibling_scan(db, passport_status="needs_review")
        job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.attachment_id == passport.id))
        job.draft = {"applicationForm": {"candidateId": "candidate", "slot": "passport", "attachmentId": passport.id,
                                         "result": {"status": "needs_review", "identityMismatch": True, "kindsFound": ["passport"], "filled": []}}}
        cut_from_the_same_scan(db, attachment)
    attachment_id, job_id = read(desk, _job_output(aadhaar=aadhaar_of("RAMESH KUMAR")), audit=rejected_sibling)
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review"


def test_a_rejected_card_read_again_after_the_fix_is_resolved_with_the_soft_note(desk):
    """The owner's case: the card was rejected as 'wrong document'; read again, the junk name no longer rejects it."""
    output = _job_output(aadhaar=aadhaar_of("RAMESH KUMAR"))
    attachment_id, job_id = read(desk, output)
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review"
    junk = _job_output(aadhaar=aadhaar_of(GARBAGE))
    with desk.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        job.state = {"output": json.loads(json.dumps(junk, default=str))}
        application_form.apply_job_output(db, job, junk)
        db.commit()
    with desk.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        assert job.status == "completed" and job.draft["applicationForm"]["result"]["status"] == "needs_review"
    view = form(desk)["documents"]["aadhaar"]
    assert view["identityMismatch"] is False and view["uploaded"] is True


# ------------------------------------------------------------------------------ every needs-review line says why


def passport_needing_review():
    raw = {"mrz": {"passport_number": "K1234567", "given_names": "GANESH", "surname": "RAMANI", "date_of_birth": "1990-01-01",
                   "expiry_date": "2030-01-01", "sex": "M", "nationality": "IND", "all_check_digits_valid": False,
                   "individual_check_digits": {"passport_number": False}}, "mrz_source": "mrz", "fields": []}
    output = _job_output(passport=raw)
    output.update(status="needs_review", warnings=[], coverage={"complete": False, "issues": [], "pages": {"not_sent": []}})
    return output


def test_a_passport_that_needs_review_says_which_check_raised_it(desk):
    output = passport_needing_review()
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name = "Ganesh Ramani"
        attachment = _file(db, "passport", "Ganesh_Ramani-Passport.pdf")
        job = _job(db, attachment, output)
        job.status = "needs_review"
        application_form.apply_job_output(db, job, output)
        db.commit()
        job_id = job.id
    extraction = form(desk)["documents"]["passport"]["extraction"]
    assert extraction["status"] == "needs_review"
    assert "Review: the passport number failed its check digit." in extraction["message"]
    assert "Review: the passport number failed its check digit." in extraction["coverage"]["issues"]
    assert "Review: the reader could not read the place" not in extraction["message"]
    from app.services import document_diagnostics as diagnostics
    with desk.sessions() as db:
        report = diagnostics.build(db, SimpleNamespace(id="staff", role=desk.actor.role, name="Staff", active=True), "candidate")
    assert "needs review because: Review: the passport number failed its check digit." in report["report"]
