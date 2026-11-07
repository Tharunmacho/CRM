"""The application-form fields on the Add candidate APIs: create/correct bodies, detail read, scan preview, draft keys."""
from veriis.models import PassportResponse, ResumeResponse

from sqlalchemy import select

from app.db import models as m
from app.integrations.ocr.mapping import OcrFields, passport_fields, resume_fields
from app.services import manual_extraction
from test_candidate_workflows import db, make_client
from test_database_naming import schema_database

RESUME = {
    "request_id": "r", "processing_time_ms": 1, "name": "Alex Example",
    "contact": {"phones": ["+91 90000 11111"], "address": "12 Example Road\nKochi, Kerala 682001"},
    "personal_info": {"father_name": "Suresh Example", "marital_status": "married"},
    "education": [{"degree": "Diploma", "institution": "Govt Polytechnic", "end": "2010"}],
    "experience": [{"title": "Technician", "company": "ACME", "country": "UAE", "is_overseas": True, "start": "2018", "end": "2025",
                    "duration_months": 84, "duration_human": "7 years"}],
    "total_experience_months": 96, "indian_experience_months": 12, "overseas_experience_months": 84,
}


def test_scan_preview_carries_the_form_fields_in_camel_case():
    fields, _ = resume_fields(ResumeResponse.model_validate(RESUME), designations=[])
    wire = fields.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert wire["fatherName"] == "Suresh Example" and wire["maritalStatus"] == "Married" and wire["experienceIndia"] == 1
    assert wire["currentAddress"] == "12 Example Road, Kochi, Kerala 682001"
    assert wire["education"] == [{"level": "Diploma", "passedYear": 2010, "institute": "Govt Polytechnic"}]
    assert wire["workHistory"][0]["country"] == "UAE" and wire["gulfExperience"] == 7 and wire["totalExperience"] == 8
    passport = PassportResponse.model_validate({
        "request_id": "p", "confidence": 0.9, "processing_time_ms": 1,
        "mrz": {"surname": "EXAMPLE", "given_names": "ALEX", "passport_number": "P1234567", "nationality": "IND", "all_check_digits_valid": True},
        "fields": [{"label": "Name of Father / Legal Guardian", "value": "SURESH EXAMPLE", "category": "x", "page": 1, "source": "ocr", "confidence": 0.9}]})
    assert passport_fields(passport)[0].father_name == "Suresh Example"


def test_old_stored_scan_results_gain_the_new_keys_when_read_back():
    extraction = {"fields": {"name": "Alex Example", "totalExperience": None, "total_experience": 8}, "raw": RESUME}
    merged = manual_extraction._with_form_fields("resume", extraction)
    assert merged["father_name"] == "Suresh Example" and merged["total_experience"] == 8
    assert OcrFields.model_validate(merged).model_dump(by_alias=True, exclude_none=True)["fatherName"] == "Suresh Example"
    assert manual_extraction._with_form_fields("resume", {"fields": {"name": "x"}, "raw": None}) == {"name": "x"}


def test_create_accepts_camel_and_snake_names_and_old_bodies_still_work(db):
    api = make_client(db)
    old = api.post("/api/candidates", json={"name": "Old Client", "phone": "9000000001"})
    assert old.status_code == 201, old.text
    body = {"name": "Form Client", "phone": "9000000002", "fatherName": "Suresh", "maritalStatus": "Married", "currentAddress": "12 Example Road",
            "experienceIndia": 3, "experienceAbroad": 2, "total_experience": 5, "passportNumber": "k1234567",
            "education": [{"level": "BE", "passedYear": 2014, "institute": "College"}, {}],
            "workHistory": [{"company": "ACME", "designation": "Welder", "country": "UAE", "period": "2018 - 2021"}]}
    created = api.post("/api/candidates", json=body)
    assert created.status_code == 201, created.text
    db.expire_all()
    c = db.get(m.Candidate, created.json()["id"])
    assert (c.father_name, c.marital_status, c.experience_india, c.gulf_experience, c.passport_no) == ("Suresh", "Married", 3, 2, "K1234567")
    assert c.education == [{"level": "BE", "passedYear": 2014, "institute": "College"}] and c.work_history[0]["company"] == "ACME"
    assert c.field_sources["personal.fatherName"] == "manual" and c.field_sources["education"] == "manual"
    detail = api.get(f"/api/candidates/{c.id}").json()
    assert detail["fatherName"] == "Suresh" and detail["experienceIndia"] == 3 and detail["currentAddress"] == "12 Example Road"
    assert detail["education"][0]["passedYear"] == 2014 and detail["workHistory"][0]["designation"] == "Welder"
    plain = api.get(f"/api/candidates/{old.json()['id']}").json()
    assert plain["education"] == [] and plain["fatherName"] is None
    # snake_case wins when both spellings arrive; bad rows are rejected
    both = api.post("/api/candidates", json={"name": "N", "phone": "9000000003", "father_name": "Snake", "fatherName": "Camel"})
    assert db.get(m.Candidate, both.json()["id"]).father_name == "Snake"
    assert api.post("/api/candidates", json={"name": "N", "phone": "9000000004", "education": [{"level": "x", "passedYear": 1800}]}).status_code == 422
    assert api.post("/api/candidates", json={"name": "N", "phone": "9000000004", "education": [{"level": "x", "bogus": 1}]}).status_code == 422
    assert api.post("/api/candidates", json={"name": "N", "phone": "9000000004", "workHistory": [{}] * 31}).status_code == 422


def test_create_with_scanned_documents_fills_empty_form_fields_and_keeps_what_was_typed(db):
    api = make_client(db)
    created = api.post("/api/candidates", json={
        "name": "Alex Example", "phone": "9000000011", "maritalStatus": "Single",
        "ocr_data": {"resume": RESUME}})
    assert created.status_code == 201, created.text
    db.expire_all()
    c = db.get(m.Candidate, created.json()["id"])
    assert c.father_name == "Suresh Example" and c.education[0]["level"] == "Diploma" and c.experience_india == 1
    assert c.marital_status == "Single"  # typed over the scan's "Married"
    sources = c.field_sources
    assert sources["personal.fatherName"] == "extracted" and "personal.maritalStatus" not in sources
    assert sources["education"] == "extracted"


def test_correction_marks_changed_fields_manual_and_validates(db):
    api = make_client(db)
    saved = api.post("/api/candidates", json={"name": "Alex", "phone": "9000000021", "ocr_data": {"resume": RESUME}}).json()
    patched = api.patch(f"/api/candidates/{saved['id']}", json={"fatherName": "Corrected Father", "currentAddress": "New address"})
    assert patched.status_code == 200, patched.text
    db.expire_all()
    c = db.get(m.Candidate, saved["id"])
    assert c.father_name == "Corrected Father"
    assert c.field_sources["personal.fatherName"] == "manual" and c.field_sources["personal.currentAddress"] == "manual"
    assert c.field_sources["education"] == "extracted"  # untouched fields keep their mark
    assert api.patch(f"/api/candidates/{saved['id']}", json={"workHistory": [{"company": "A" * 300}]}).status_code == 422


ALL_FORM_KEYS = ["fatherName", "maritalStatus", "currentAddress", "experienceIndia", "experienceAbroad", "education", "workHistory",
                 "nativePlace", "familyMemberNumber", "jobDurationPreference", "countryPreference", "jobPreference",
                 "serviceChargesStatus", "bestTimeToContact", "interestLevel", "nextFollowUpDate", "whatsappSameAsPhone"]


def test_draft_accepts_the_new_form_keys():
    import pytest
    from pydantic import ValidationError
    from app.api.routers.document_jobs import DraftIn
    draft = DraftIn(form={key: "[]" if key in ("education", "workHistory") else "x" for key in ALL_FORM_KEYS}, editedFields=ALL_FORM_KEYS)
    assert set(draft.form) == set(ALL_FORM_KEYS) and draft.editedFields == ALL_FORM_KEYS
    with pytest.raises(ValidationError):
        DraftIn(form={"notAFormKey": "x"})
    with pytest.raises(ValidationError):
        DraftIn(form={"fatherName": "x" * 20001})


def test_half_filled_intake_with_the_new_fields_survives_autosave_and_recovery(db):
    from uuid import uuid4
    from app.api.routers import document_jobs
    group = str(uuid4())
    job = m.DocumentExtractionJob(owner_id="admin", group_id=group, kind="resume", filename="r.pdf", content_type="application/pdf",
                                  object_key="synthetic/unread", content_hash=uuid4().hex * 2, size_kb=1, status="uploaded", draft={})
    db.add(job)
    db.commit()
    api = make_client(db)
    api.app.include_router(document_jobs.router)
    form = {"name": "Half Filled", "fatherName": "Suresh", "nativePlace": "Thrissur", "interestLevel": "interested",
            "nextFollowUpDate": "2026-12-01", "education": '[{"level":"ITI","passedYear":2012,"institute":"Govt ITI"}]',
            "workHistory": '[{"company":"ACME","designation":"Welder","country":"UAE","period":"2018-2020"}]', "jobPreference": "Welder, Fitter"}
    saved = api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": form, "editedFields": ["fatherName", "nativePlace", "education"], "jobIds": [job.id]})
    assert saved.status_code == 200, saved.text
    db.expire_all()  # the draft is written with SQL now, not through this shared session's loaded objects
    recovered = api.get(f"/api/ocr/jobs/groups/{group}").json()[0]["draft"]
    assert recovered["form"] == form and recovered["editedFields"] == ["fatherName", "nativePlace", "education"]
    assert api.put(f"/api/ocr/jobs/groups/{group}/draft", json={"form": {"bogusKey": "x"}, "jobIds": [job.id]}).status_code == 422


def test_create_and_correct_carry_the_screening_details_and_the_follow_up_todo(db):
    from datetime import date, timedelta
    api = make_client(db)
    day = (date.today() + timedelta(days=2)).isoformat()
    created = api.post("/api/candidates", json={
        "name": "Screened", "phone": "9000000031", "whatsappSameAsPhone": True, "nativePlace": "Thrissur", "familyMemberNumber": "9447000000",
        "jobDurationPreference": "2 years", "countryPreference": "Qatar", "bestTimeToContact": "Evenings", "serviceChargesStatus": "pending",
        "interestLevel": "somewhat_interested", "nextFollowUpDate": day, "designations": ["Welder"],
        "assign_to": "a"})  # recruiters are assigned by hand now; the to-do goes to the owner
    assert created.status_code == 201, created.text
    db.expire_all()
    c = db.get(m.Candidate, created.json()["id"])
    assert c.whatsapp == "9000000031" and c.native_place == "Thrissur" and c.service_charges_status == "pending"
    assert c.interest_level == "somewhat_interested" and c.next_follow_up_date.isoformat() == day
    todo = db.scalar(select(m.TodoItem).where(m.TodoItem.link_to == f"/candidates/{c.id}"))
    assert todo is not None and todo.owner_id == c.owner_id and todo.title == "Follow up: Screened"
    detail = api.get(f"/api/candidates/{c.id}").json()
    assert detail["nativePlace"] == "Thrissur" and detail["interestLevel"] == "somewhat_interested" and detail["nextFollowUpDate"] == day
    # defaults for a body that knows nothing of these fields
    plain = db.get(m.Candidate, api.post("/api/candidates", json={"name": "Plain", "phone": "9000000032"}).json()["id"])
    assert plain.service_charges_status == "not_discussed" and plain.interest_level is None and plain.next_follow_up_date is None
    patched = api.patch(f"/api/candidates/{c.id}", json={"nextFollowUpDate": None, "interestLevel": "not_interested"})
    assert patched.status_code == 200, patched.text
    db.expire_all()
    assert db.get(m.Candidate, c.id).field_sources["screening.interestLevel"] == "manual"
    assert db.scalar(select(m.TodoItem).where(m.TodoItem.link_to == f"/candidates/{c.id}")) is None
    for bad in ({"serviceChargesStatus": "later"}, {"interestLevel": "keen"}):
        assert api.post("/api/candidates", json={"name": "N", "phone": "9000000033", **bad}).status_code == 422
