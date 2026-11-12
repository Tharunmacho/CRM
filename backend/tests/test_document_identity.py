from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.services.document_identity import identity_conflicts, IDENTITY_REVIEW_MESSAGE
from app.services import form_extraction as fx


def candidate(name="Alex Example"):
    values = {column: ([] if spec.kind in ("list", "rows") else 0 if spec.kind == "int" else None)
              for column, spec in fx.SPECS.items()}
    values.update(id="synthetic", name=name, field_sources={}, needs_review=False, ocr_data=None, resume_text="")
    return SimpleNamespace(**values)


@pytest.mark.parametrize("incoming", ["Example Alex", "Alex M Example", "A. Example"])
def test_name_variations_do_not_reject_matching_documents(incoming):
    assert identity_conflicts(candidate(), {"name": incoming}) == []


def test_different_person_rejects_entire_document_not_only_name():
    person = candidate("Original Person")
    before = deepcopy(person.__dict__)
    result = fx.apply_values(person, "resume", {"name": "Unrelated Applicant", "father_name": "Wrong Parent",
        "education": [{"level": "Wrong qualification"}], "total_experience": 7, "nationality": "Wrong nationality"})
    assert result.filled == [] and "personal.candidateName" in result.conflicts
    assert person.needs_review is True
    assert {key: value for key, value in person.__dict__.items() if key != "needs_review"} == {key: value for key, value in before.items() if key != "needs_review"}


def test_anonymous_back_page_can_fill_complementary_details_but_conflicting_passport_cannot():
    person = candidate()
    person.passport_no = "A1234567"
    assert identity_conflicts(person, {"current_address": "Synthetic address"}) == []
    assert identity_conflicts(person, {"name": "Alex Example", "passport_no": "B7654321"}) == ["passport_no"]
    assert identity_conflicts(candidate("Name needs review"), {"name": "Readable Name"}) == []


def test_existing_other_candidate_identifier_rejects_all_enrichment(monkeypatch):
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [(candidate("Another Person"), ["passport number"])])
    assert identity_conflicts(candidate(), {"passport_no": "A1234567"}, object()) == ["candidate"]


def test_stored_wrong_ocr_cannot_backfill_fields_or_masked_aadhaar():
    person = candidate("Original Person")
    person.ocr_data = {"aadhaar": {"aadhaar": {"name": "Unrelated Applicant", "aadhaar_number": "000000001234", "address": "Wrong address"}}}
    result = fx.apply_stored_ocr(person, force=True)
    assert result.filled == []
    assert person.current_address is None and fx.aadhaar_of(person) is None


def test_form_job_rejects_mismatch_before_merging_raw_data_or_deriving_documents(monkeypatch):
    from app.services.application_form import apply_job_output
    person = candidate("Original Person")
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    audit = []
    db = SimpleNamespace(scalar=lambda *args: person, add=audit.append)
    job = SimpleNamespace(id="job", candidate_id=person.id, owner_id="staff", status="completed", last_error=None,
        draft={"applicationForm": {"slot": "resume", "attachmentId": "file"}})
    output = {"status": "complete", "coverage": {"complete": True}, "extractions": {"resume": {
        "fields": {"name": "Unrelated Applicant"}, "raw": {"name": "Unrelated Applicant", "personal_info": {"father_name": "Wrong Parent"}}}}}
    apply_job_output(db, job, output)
    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    assert job.draft["applicationForm"]["result"]["identityMismatch"] is True
    assert job.draft["applicationForm"]["result"]["filled"] == []
    assert person.father_name is None and person.ocr_data is None and person.resume_text == ""
    assert {row.action for row in audit} == {"Rejected document autofill: identity mismatch", "Flagged for document review"}


def test_local_resume_contacts_wait_for_identity_check(monkeypatch):
    from app.services.application_form import apply_local_patterns
    person = candidate("Original Person")
    db = SimpleNamespace(scalar=lambda *args: person, commit=lambda: None)
    assert apply_local_patterns(db, person.id, {"resume": {"email": "wrong@example.test", "mobile": "9999999999"}}) == []
    assert person.email is None and person.phone is None
