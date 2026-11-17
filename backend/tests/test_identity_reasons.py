"""A rejected document says why; a folder stand-in name never causes a false rejection and is replaced by the first real name."""
import json
from types import SimpleNamespace

import pytest
from app.services import application_form
from app.services import form_extraction as fx
from app.services.document_identity import (
    IDENTITY_REVIEW_MESSAGE, best_known_name, identity_findings, name_disagreement, names_differ, plausible_full_name)
from test_document_identity import _form_job, _job_output, _passport_of, _provisional, _resume_of, _stub_db, candidate

NO_OTHER_OWNER = lambda *args, **kwargs: []   # noqa: E731


@pytest.fixture(autouse=True)
def _no_owner(monkeypatch):
    monkeypatch.setattr("app.services.document_identity.identity_matches", NO_OTHER_OWNER)


@pytest.fixture
def renames(monkeypatch):
    calls = []
    monkeypatch.setattr(application_form, "rename_imported_files", lambda db, person, old, new: calls.append((old, new)) or 0)
    return calls


def _result(job):
    return job.draft["applicationForm"]["result"]


# ------------------------------------------------------------------ what counts as the same name

@pytest.mark.parametrize("left,right", [
    ("RASHIDKHAN NAWAZKHAN MANSUR", "Rashid Khan Nawazkhan"),       # written without a space
    ("Rashidkhan Nawazkhan Mansur", "rashid khan-alnoor"),
    ("Rashiid", "Rashid"),                                         # a one-letter slip, nothing else in common
    ("Mohammed Nawaz", "Nawaz M"),                              # order and an initial
    ("Mansur Rashidkhan F", "Rashid Khan Nawazkhan Mansur"),
])
def test_one_person_written_differently_is_not_a_different_name(left, right):
    assert not names_differ(left, right) and not names_differ(right, left)


@pytest.mark.parametrize("left,right", [("Arun Kumar", "Bala Rajan"), ("Unrelated Applicant", "Original Person"), ("Rashid", "Zubair")])
def test_two_people_are_still_different_names(left, right):
    assert names_differ(left, right)


def test_plausible_full_names():
    assert plausible_full_name("Rashidkhan Nawazkhan Mansur") and plausible_full_name("Arun K.")
    assert not plausible_full_name("Rashid") and not plausible_full_name("A1 B2") and not plausible_full_name("")


# ------------------------------------------------------------------ every rejection says why

def test_a_name_reason_carries_the_name_read_and_the_name_it_was_compared_with(renames):
    person, added = candidate("Rashid Khan"), []
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(resume=_resume_of("Zubair Hussain")))
    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    assert _result(job)["identityMismatch"] is True and _result(job)["duplicateOf"] is None
    assert _result(job)["identityReasons"] == [{"field": "name", "documentKind": "resume", "found": "Zubair Hussain",
                                                 "expected": "Rashid Khan", "expectedFrom": "candidate"}]
    audit = next(row for row in added if getattr(row, "action", "") == "Rejected document autofill: identity mismatch")
    assert audit.meta["reasons"] == _result(job)["identityReasons"] and audit.meta["conflicting_fields"] == ["name"]
    assert renames == []


def test_a_passport_reason_names_only_the_field_never_a_number(renames):
    person, added = candidate("Arun Kumar"), []
    person.passport_no = "K1234567"
    job = _form_job()
    output = _job_output(resume=_resume_of("Arun Kumar", passport_details={"passport_number": "Z9999999"}))
    application_form.apply_job_output(_stub_db(person, added), job, output)
    assert job.status == "needs_review"
    assert _result(job)["identityReasons"] == [{"field": "passport_no", "documentKind": "resume"}]
    saved = json.dumps([_result(job), [getattr(row, "meta", None) for row in added]], default=str)
    assert "Z9999999" not in saved and "K1234567" not in saved and "9000000001" not in saved


def test_an_identifier_owned_by_another_candidate_says_who_and_which_one(monkeypatch, renames):
    other = SimpleNamespace(id="other-id", care_id="CI-157", name="Rashid Khan")
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [(other, ["phone number"])])
    person, added = _provisional("rashid khan-alnoor"), []
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(resume=_resume_of("Rashidkhan Nawazkhan Mansur")))
    result = _result(job)
    assert job.status == "needs_review" and result["identityMismatch"] is True
    assert result["duplicateOf"] == {"candidateId": "other-id", "careId": "CI-157", "name": "Rashid Khan"}
    assert result["identityReasons"] == [{"field": "candidate", "documentKind": "resume", "matchedOn": ["phone number"]}]
    assert "9000000001" not in json.dumps(result)
    # an identifier owned by someone else is not about the name: the stand-in stays until a document that is accepted names the person
    assert person.name == "rashid khan-alnoor" and fx.provisional_name(person) and renames == []


def test_the_owner_is_found_even_when_the_reader_mapping_and_the_stored_output_disagree(monkeypatch):
    """The reader's own output holds the e-mail that is owned elsewhere; the mapped fields hold another one."""
    other = SimpleNamespace(id="other-id", care_id="CI-158", name="Rashid Khan")
    monkeypatch.setattr("app.services.document_identity.identity_matches",
                        lambda db, values, **kwargs: [(other, ["email"])] if values.get("email") == "taken@example.test" else [])
    person, added = _provisional(), []
    raw = _resume_of("Arun Kumar", contact={"emails": ["taken@example.test"]})
    output = {"status": "complete", "coverage": {"complete": True},
              "extractions": {"resume": {"fields": {"name": "Arun Kumar", "email": "mapped@example.test"}, "raw": raw}}}
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, output)
    assert _result(job)["duplicateOf"]["careId"] == "CI-158"


# ------------------------------------------------------------------ a stand-in name never blocks a resume

def test_a_resume_in_capitals_replaces_the_folder_stand_in_and_renames_the_files(renames):
    person, added = _provisional("rashid khan-alnoor"), []
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(resume=_resume_of("RASHIDKHAN NAWAZKHAN MANSUR")))
    assert job.status == "completed" and "identityMismatch" not in _result(job)
    assert person.name == "Rashidkhan Nawazkhan Mansur" and not fx.provisional_name(person)
    assert renames == [("rashid khan-alnoor", "Rashidkhan Nawazkhan Mansur")]


def test_a_passport_read_alone_replaces_the_stand_in(renames):
    person, added = _provisional("rashid khan-alnoor"), []
    job = _form_job(slot="passport")
    passport = _passport_of("RASHIDKHAN NAWAZKHAN", "MANSUR", "K1234567")
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(passport=passport))
    assert job.status == "completed" and person.name == "Rashidkhan Nawazkhan Mansur" and person.passport_no == "K1234567"
    assert renames == [("rashid khan-alnoor", "Rashidkhan Nawazkhan Mansur")]


def test_separate_reads_agree_when_one_writes_the_name_without_a_space(renames):
    person, added = _provisional("rashid khan-alnoor"), []
    db = _stub_db(person, added)
    application_form.apply_job_output(db, first := _form_job("first"), _job_output(resume=_resume_of("RASHIDKHAN NAWAZKHAN MANSUR")))
    passport = _passport_of("RASHID KHAN NAWAZKHAN", "MANSUR", "K1234567")
    application_form.apply_job_output(db, second := _form_job("second", slot="passport"), _job_output(passport=passport))
    assert first.status == "completed" and second.status == "completed"
    assert person.name == "Rashid Khan Nawazkhan Mansur" and person.passport_no == "K1234567"        # the passport outranks the resume
    assert renames[0] == ("rashid khan-alnoor", "Rashidkhan Nawazkhan Mansur")


def test_a_later_passport_of_another_person_is_rejected_once_the_real_name_is_set(renames):
    person, added = _provisional("rashid khan-alnoor"), []
    db = _stub_db(person, added)
    application_form.apply_job_output(db, _form_job("first"), _job_output(resume=_resume_of("RASHIDKHAN NAWAZKHAN MANSUR")))
    second = _form_job("second", slot="passport")
    application_form.apply_job_output(db, second, _job_output(passport=_passport_of("BALA", "RAJAN", "K7654321")))
    assert second.status == "needs_review" and _result(second)["identityMismatch"] is True
    assert _result(second)["identityReasons"] == [{"field": "name", "documentKind": "passport", "found": "Bala Rajan",
                                                    "expected": "Rashidkhan Nawazkhan Mansur", "expectedFrom": "candidate"}]
    assert person.name == "Rashidkhan Nawazkhan Mansur" and person.passport_no is None


def test_two_documents_of_two_people_in_one_job_still_name_the_candidate_from_the_stronger_one(renames):
    person, added = _provisional("rashid khan-alnoor"), []
    job = _form_job()
    output = _job_output(resume=_resume_of("Arun Kumar"), passport=_passport_of("BALA", "RAJAN", "K1234567"))
    application_form.apply_job_output(_stub_db(person, added), job, output)
    assert job.status == "needs_review" and _result(job)["identityMismatch"] is True
    assert person.name == "Bala Rajan" and not fx.provisional_name(person) and person.passport_no is None
    assert renames == [("rashid khan-alnoor", "Bala Rajan")]
    audit = [row for row in added if getattr(row, "action", "") == "Named candidate from a document"]
    assert len(audit) == 1 and audit[0].meta["previous_name"] == "rashid khan-alnoor"


def test_a_stray_name_on_a_page_that_is_no_passport_is_not_a_second_person(renames):
    """A "passport" page with neither a number nor a machine-readable zone is not evidence about who the candidate is."""
    person, added = _provisional(), []
    stray = {"mrz": {}, "fields": [{"label": "Name", "value": "Referee Person"}]}
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(resume=_resume_of("Arun Kumar"), passport=stray))
    assert not _result(job).get("identityMismatch") and job.status == "completed"
    assert person.name == "Arun Kumar" and name_disagreement(_job_output(resume=_resume_of("Arun Kumar"), passport=stray)["extractions"]) is None


def test_a_document_is_never_compared_with_itself():
    """The mapped name and the stored output's name of ONE resume disagree: that is one document, not two people."""
    person, added = _provisional(), []
    output = {"status": "complete", "coverage": {"complete": True}, "extractions": {
        "resume": {"fields": {"name": "Rashid Khan"}, "raw": _resume_of("Nawaz Mansur")}}}
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, output)
    assert job.status == "completed" and not _result(job).get("identityMismatch")


def test_stored_names_still_guard_a_stand_in_and_tolerate_spacing_and_slips():
    person = _provisional("rashid khan-alnoor")
    person.ocr_data = {"resume": _resume_of("Rashidkhan Nawazkhan Mansur")}
    assert identity_findings(person, {"name": "Rashid Khan Nawazkhan"}) == []
    assert identity_findings(person, {"name": "Mansur Nawazkhan Rashidkhan"}) == []
    finding = identity_findings(person, {"name": "Bala Rajan"})
    assert finding == [{"field": "name", "found": "Bala Rajan", "expected": "Rashidkhan Nawazkhan Mansur", "expectedFrom": "stored_document"}]
    slip = _provisional("folder")
    slip.ocr_data = {"resume": _resume_of("Rashid")}
    assert identity_findings(slip, {"name": "Rashiid"}) == []


def test_a_legacy_stand_in_with_stored_documents_takes_the_stored_name_when_a_later_document_disagrees(renames):
    person, added = _provisional("rashid khan-alnoor"), []
    person.ocr_data = {"passport": _passport_of("ARUN", "KUMAR", "K1234567")}
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(resume=_resume_of("Bala Rajan")))
    assert job.status == "needs_review" and _result(job)["identityReasons"][0]["expectedFrom"] == "stored_document"
    assert person.name == "Arun Kumar" and renames == [("rashid khan-alnoor", "Arun Kumar")]
    assert best_known_name({}, SimpleNamespace(ocr_data=None)) is None
