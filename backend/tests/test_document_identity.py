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
    person.ocr_data = {"aadhaar": {"aadhaar": {"name": "Ramesh Kumar", "aadhaar_number": "000000001234", "address": "Wrong address"}}}
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
    scoped = {row.action: row.meta for row in audit}
    assert scoped["Flagged for document review"] == {
        "reasons": ["ocr_warning"], "attachment_id": "file", "document_job_id": "job"}
    assert scoped["Rejected document autofill: identity mismatch"]["attachment_id"] == "file"
    assert scoped["Rejected document autofill: identity mismatch"]["document_job_id"] == "job"


def _mixed_resume_with_noisy_passport(number="AA145B38"):
    return {
        "name": "Elangovan Ramasamy",
        "contact": {"phones": ["9876500891"]},
        "personal_info": {"father_name": "Ramasamy", "marital_status": "Married", "date_of_birth": "1986-03-12",
                          "gender": "male", "languages_known": ["Tamil", "English"]},
        "passport_details": {"passport_number": number, "place_of_issue": "Chennai", "date_of_issue": "2015-01-01",
                             "date_of_expiry": "2025-01-01"},
        "education": [{"degree": "ITI", "year": "2004"}],
        "experience": [{"company": "Example Engineering", "title": "Fitter", "duration_months": 60}],
    }


def _passport(number="AA145638"):
    return {"mrz": {"passport_number": number, "given_names": "ELANGOVAN", "surname": "RAMASAMY",
                     "date_of_birth": "1986-03-12", "sex": "M", "nationality": "IND"}, "fields": []}


def _mixed_form_job(person):
    return SimpleNamespace(id="mixed-job", candidate_id=person.id, owner_id="staff", status="completed", last_error=None,
        draft={"applicationForm": {"slot": "resume", "attachmentId": "mixed-file"}})


def _mixed_output(passport_number="AA145638"):
    resume = _mixed_resume_with_noisy_passport()
    passport = _passport(passport_number)
    return {"status": "complete", "inspection": {"resume_pages": [3, 4], "passport_pages": [5]},
            "coverage": {"complete": True}, "extractions": {
                "resume": {"fields": {**fx.resume_values(resume)}, "raw": resume, "selected_pages": [3, 4]},
                "passport": {"fields": {**fx.passport_values(passport)}, "raw": passport, "selected_pages": [5]},
            }}


def test_mixed_resume_passport_noisy_copy_applies_resume_but_preserves_passport_guard(monkeypatch):
    """A copied passport number in the resume cannot discard the resume's own pages."""
    from app.services import application_form
    person = candidate("Elangovan Ramasamy")
    person.passport_no = "AA145638"  # trusted separately uploaded passport
    audit = []
    db = SimpleNamespace(scalar=lambda *args: person, add=audit.append)
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args: {})

    job = _mixed_form_job(person)
    # Create the exact old false-positive pair first; the replay must resolve
    # this job and attachment, not an arbitrary candidate warning.
    application_form.apply_job_output(db, job, _mixed_output("ZZ999999"))
    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    output = _mixed_output()
    application_form.apply_job_output(db, job, output)

    assert job.status == "completed"
    assert job.draft["applicationForm"]["result"]["status"] == "done"
    assert person.passport_no == "AA145638"
    assert (person.father_name, person.marital_status, person.phone, person.languages) == (
        "Ramasamy", "Married", "9876500891", ["Tamil", "English"])
    assert person.education and person.work_history and person.total_experience == 5
    # The exact provider output remains available for audit, but later stored
    # OCR replays cannot revive its noisy copied passport fields.
    assert person.ocr_data["resume"]["passport_details"]["passport_number"] == "AA145B38"
    ignored = fx.ignored_fields_for(person.ocr_data, "resume", output["extractions"]["resume"]["raw"])
    assert ignored == fx.PASSPORT_DOCUMENT_COLUMNS
    replay = fx.apply_stored_ocr(person, force=True)
    assert not replay.conflicts and person.passport_no == "AA145638"
    resolution = {row.action: row.meta for row in audit if row.action.startswith("Resolved document")}
    assert resolution == {
        "Resolved document identity mismatch": {
            "attachment_id": "mixed-file", "document_job_id": "mixed-job",
            "resolves": "Rejected document autofill: identity mismatch"},
        "Resolved document review flag": {
            "attachment_id": "mixed-file", "document_job_id": "mixed-job",
            "resolves": "Flagged for document review"},
    }


def test_mixed_packet_still_rejects_an_actual_passport_mismatch(monkeypatch):
    """Only the resume's copied passport fields are ignored; passport pages stay strict."""
    from app.services import application_form
    person = candidate("Elangovan Ramasamy")
    person.passport_no = "AA145638"
    audit = []
    db = SimpleNamespace(scalar=lambda *args: person, add=audit.append)
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args: {})

    job = _mixed_form_job(person)
    application_form.apply_job_output(db, job, _mixed_output("ZZ999999"))

    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    assert job.draft["applicationForm"]["result"]["identityMismatch"] is True
    assert person.father_name is None and person.ocr_data is None


def test_shared_resume_passport_page_keeps_the_normal_passport_guard(monkeypatch):
    """Only genuinely separate page groups get the narrow resume exception."""
    from app.services import application_form
    person = candidate("Elangovan Ramasamy")
    person.passport_no = "AA145638"
    audit = []
    db = SimpleNamespace(scalar=lambda *args: person, add=audit.append)
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args: {})
    output = _mixed_output()
    output["inspection"] = {"resume_pages": [3], "passport_pages": [3]}
    output["extractions"]["resume"]["selected_pages"] = [3]
    output["extractions"]["passport"]["selected_pages"] = [3]

    job = _mixed_form_job(person)
    application_form.apply_job_output(db, job, output)

    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    assert person.father_name is None and person.ocr_data is None


def test_partial_verified_resume_only_fills_empty_fields_and_keeps_replay_ignores(monkeypatch):
    """The maintenance path cannot replace a trusted extracted address on a later force replay."""
    from app.services import application_form
    person = candidate("Alex Example")
    person.passport_no = "AA145638"
    person.current_address = "Trusted passport address"
    person.field_sources = {"personal.currentAddress": "extracted"}
    audit = []
    db = SimpleNamespace(scalar=lambda *args: person, add=audit.append)
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args, **kwargs: {})
    raw = {
        "name": "Alex Example", "contact": {"phones": ["9000000000"], "address": "Old resume address"},
        "personal_info": {"father_name": "Ganesh Example"},
        "passport_details": {"passport_number": "ZZ999999", "place_of_issue": "Wrong place"},
    }
    job = SimpleNamespace(id="partial-job", candidate_id=person.id, owner_id="staff", status="needs_review",
                          last_error=IDENTITY_REVIEW_MESSAGE,
                          draft={"applicationForm": {"slot": "resume", "attachmentId": "partial-file"}})
    output = {"status": "needs_review", "coverage": {"complete": False}, "extractions": {
        "resume": {"fields": {"name": "Alex Example", "passport_no": "ZZ999999"}, "raw": raw}}}
    application_form.apply_job_output(db, job, output, _partial_verified_resume_replay=True)

    assert person.father_name == "Ganesh Example"
    assert person.current_address == "Trusted passport address" and person.passport_no == "AA145638"
    ignored = fx.ignored_fields_for(person.ocr_data, "resume", raw)
    assert fx.PASSPORT_DOCUMENT_COLUMNS <= ignored and "current_address" in ignored
    fx.apply_stored_ocr(person, force=True)
    assert person.current_address == "Trusted passport address" and person.passport_no == "AA145638"
    assert job.status == "needs_review"


def test_local_resume_contacts_wait_for_identity_check(monkeypatch):
    from app.services.application_form import apply_local_patterns
    person = candidate("Original Person")
    db = SimpleNamespace(scalar=lambda *args: person, commit=lambda: None)
    assert apply_local_patterns(db, person.id, {"resume": {"email": "wrong@example.test", "mobile": "9999999999"}}) == []
    assert person.email is None and person.phone is None


# ------------------------------------------------------------------ a stand-in (folder) name must not switch the name guard off

def _provisional(name="Folder Name"):
    person = candidate(name)
    person.field_sources = {fx.PROVISIONAL_NAME: True}
    return person


def _stub_db(person, added):
    return SimpleNamespace(scalar=lambda *args, **kwargs: person, scalars=lambda *args, **kwargs: [], get=lambda *args, **kwargs: None, add=added.append)


def _form_job(job_id="job", slot="resume"):
    return SimpleNamespace(id=job_id, candidate_id="synthetic", owner_id="staff", status="completed", last_error=None,
                           draft={"applicationForm": {"slot": slot, "attachmentId": "file-" + job_id}})


def _resume_of(name, **extra):
    return {"name": name, "contact": {"phones": ["9000000001"]}, "education": [{"degree": "ITI Fitter", "year": "2012"}], **extra}


def _passport_of(given, surname, number):
    return {"mrz": {"passport_number": number, "given_names": given, "surname": surname, "date_of_birth": "1990-01-01",
                    "sex": "M", "nationality": "IND"}, "fields": []}


def _job_output(**documents):
    return {"status": "complete", "coverage": {"complete": True}, "extractions": {
        kind: {"fields": fx.values_for(kind, raw), "raw": raw} for kind, raw in documents.items()}}


def test_one_job_holding_two_different_people_is_rejected_even_while_the_name_is_a_stand_in(monkeypatch):
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    person, added = _provisional(), []
    output = _job_output(resume=_resume_of("Arun Kumar"), passport=_passport_of("BALA", "RAJAN", "K1234567"))
    job = _form_job()
    application_form.apply_job_output(_stub_db(person, added), job, output)
    assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
    assert job.draft["applicationForm"]["result"]["identityMismatch"] is True
    assert job.draft["applicationForm"]["result"]["filled"] == []
    assert person.needs_review is True
    # Nothing of the rejected reading is applied; only the name is taken, from the passport (it outranks the resume), so the
    # candidate is no longer called by its folder name and later documents are compared with a real name.
    assert person.passport_no is None and person.phone is None and person.ocr_data is None and person.education == []
    assert person.name == "Bala Rajan" and not fx.provisional_name(person)
    reasons = job.draft["applicationForm"]["result"]["identityReasons"]
    assert reasons == [{"field": "name", "documentKind": "resume", "found": "Arun Kumar", "expected": "Bala Rajan",
                        "expectedFrom": "other_document", "expectedKind": "passport"}]


def test_one_job_of_one_person_still_names_the_candidate_from_the_documents(monkeypatch):
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    person, added = _provisional(), []
    output = _job_output(resume=_resume_of("Arun K Kumar"), passport=_passport_of("ARUN", "KUMAR", "K1234567"))
    application_form.apply_job_output(_stub_db(person, added), job := _form_job(), output)
    assert job.status == "completed" and job.draft["applicationForm"]["result"]["status"] == "done"
    assert person.name == "Arun Kumar" and person.passport_no == "K1234567" and not fx.provisional_name(person)
    assert not person.needs_review


def test_a_second_job_with_another_persons_passport_is_rejected_after_the_first_read(monkeypatch):
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    person, added = _provisional(), []
    db = _stub_db(person, added)
    application_form.apply_job_output(db, first := _form_job("first"), _job_output(resume=_resume_of("Arun Kumar")))
    assert first.status == "completed" and person.name == "Arun Kumar"
    second = _form_job("second", slot="passport")
    application_form.apply_job_output(db, second, _job_output(passport=_passport_of("BALA", "RAJAN", "K7654321")))
    assert second.status == "needs_review" and second.draft["applicationForm"]["result"]["identityMismatch"] is True
    assert person.passport_no is None and person.name == "Arun Kumar" and person.needs_review is True
    assert "passport" not in person.ocr_data


def test_a_stand_in_name_is_compared_with_the_names_already_stored_from_other_documents():
    person = _provisional()
    person.ocr_data = {"resume": _resume_of("Arun Kumar"), "additional_documents": [{"passport": _passport_of("ARUN", "KUMAR", "K1")}]}
    assert identity_conflicts(person, {"name": "Bala Rajan"}) == ["name"]
    assert identity_conflicts(person, {"name": "Kumar Arun"}) == []              # the same person, reordered
    assert identity_conflicts(person, {"current_address": "somewhere"}) == []    # no name, no name check
    assert identity_conflicts(_provisional(), {"name": "Bala Rajan"}) == []      # nothing stored: only the folder stand-in itself is exempt
    assert identity_conflicts(candidate("Folder Name"), {"name": "Bala Rajan"}) == ["name"]   # a real name still guards as before


# ------------------------------------------------------------------ an education certificate adds education rows and nothing else

CERT = {"name": "Alex M Example", "contact": {"phones": ["9111111111"], "emails": ["cert@example.test"]},
        "education": [{"degree": "B.Sc Physics", "institution": "Madras University", "year": "2015"}],
        "skills": ["Welding"], "experience": [{"company": "Other Co", "title": "Welder", "duration_months": 24}]}


def _read_resume_person():
    person = candidate("Alex Example")
    raw = {"name": "Alex Example", "contact": {"phones": ["9000000001"]}, "skills": ["Fitting"],
           "education": [{"degree": "ITI Fitter", "year": "2012"}, {"degree": "SSLC", "institution": "State Board", "year": "2010"}]}
    fx.apply_document(person, "resume", raw)
    person.ocr_data = {"resume": raw}
    return person, raw


def test_a_certificate_read_after_the_resume_only_adds_education_rows(monkeypatch):
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    person, resume = _read_resume_person()
    assert person.name == "Alex Example" and len(person.education) == 2 and person.field_sources["personal.candidateName"] == "extracted"
    added = []
    job = _form_job("cert", slot="education_certificate")
    application_form.apply_job_output(_stub_db(person, added), job, _job_output(resume=CERT))
    levels = [row["level"] for row in person.education]
    assert levels == ["ITI Fitter", "SSLC", "B.Sc Physics"]                      # merged, nothing deleted
    assert (person.name, person.phone, person.skills, person.work_history) == ("Alex Example", "9000000001", ["Fitting"], [])
    assert person.email is None and person.total_experience == 0 and person.designations == []
    assert person.ocr_data["resume"] == resume                                   # the real resume stays the primary resume
    assert person.ocr_data["education_certificate"] == CERT
    assert not person.resume_text
    assert not [row for row in added if type(row).__name__ == "Attachment"]      # no Resume attachment is created
    assert job.draft["applicationForm"]["result"]["filled"] == ["education"] and job.status == "completed"


def test_a_certificate_read_first_never_becomes_the_primary_resume_and_the_resume_keeps_its_rows(monkeypatch):
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    person, added = _provisional(), []
    db = _stub_db(person, added)
    application_form.apply_job_output(db, _form_job("cert", slot="education_certificate"), _job_output(resume=CERT))
    assert "resume" not in person.ocr_data and person.ocr_data["education_certificate"] == CERT
    assert person.name == "Folder Name" and fx.provisional_name(person) and person.phone is None     # not even the name
    assert [row["level"] for row in person.education] == ["B.Sc Physics"]
    resume = _resume_of("Alex Example", skills=["Fitting"])
    application_form.apply_job_output(db, _form_job("resume"), _job_output(resume=resume))
    assert person.ocr_data["resume"] == resume and "additional_documents" not in person.ocr_data
    assert person.name == "Alex Example" and person.phone == "9000000001" and person.skills == ["Fitting"]
    assert [row["level"] for row in person.education] == ["ITI Fitter", "B.Sc Physics"]    # the resume replaced its own rows, the certificate's came back


def test_a_certificate_of_another_person_is_still_rejected(monkeypatch):
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    person, _resume = _read_resume_person()
    before = deepcopy(person.education)
    stranger = {**CERT, "name": "Zed Stranger"}
    job = _form_job("cert", slot="education_certificate")
    application_form.apply_job_output(_stub_db(person, []), job, _job_output(resume=stranger))
    assert job.status == "needs_review" and job.draft["applicationForm"]["result"]["identityMismatch"] is True
    assert person.education == before and "education_certificate" not in person.ocr_data and person.needs_review is True


def test_certificate_education_rows_are_merged_without_duplicates_and_never_over_a_typed_list():
    person = candidate("Alex Example")
    person.education = [{"level": "b.sc physics", "passedYear": "2015", "institute": "madras university"}]
    person.field_sources = {"education": "extracted"}
    result = fx.apply_education_certificate(person, CERT)
    assert person.education == [{"level": "b.sc physics", "passedYear": "2015", "institute": "madras university"}] and result.filled == []
    typed = candidate("Alex Example")
    typed.education = [{"level": "Diploma", "passedYear": "2018", "institute": "Poly"}]
    typed.field_sources = {"education": "manual"}
    result = fx.apply_education_certificate(typed, CERT)
    assert [row["level"] for row in typed.education] == ["Diploma"] and result.kept == ["education"] and result.filled == []
