"""Reading OCR output into the application-form columns: messy shapes, and the fill rule (no database needed)."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.services import form_extraction as fx


def person(**values):
    """A stand-in Candidate: plain attributes, the way the rule reads them."""
    base = dict(name="", phone="", whatsapp=None, email=None, dob=None, gender=None, nationality=None, current_city=None,
                languages=[], passport_no=None, passport_issue=None, passport_expiry=None, passport_place=None,
                father_name=None, marital_status=None, current_address=None, education=None, work_history=None,
                total_experience=0, experience_india=0, gulf_experience=0, skills=[], designations=[], field_sources=None, id="c1",
                ocr_data=None, native_place=None, family_member_number=None, job_duration_preference=None, country_preference=None,
                best_time_to_contact=None, service_charges_status="not_discussed", interest_level=None, next_follow_up_date=None)
    base.update(values)
    return SimpleNamespace(**base)


RESUME = {
    "name": "ARJUN MENON",
    "contact": {"phones": ["+91 98470 22310"], "emails": ["Arjun@Example.com"], "address": "12 Gandhi Road\nKochi,\nKerala 682001"},
    "personal_info": {"father_name": "Gopalan Menon", "marital_status": "unmarried", "date_of_birth": "14/03/1994",
                      "gender": "Male", "nationality": "Indian", "languages_known": ["English", "Malayalam", "english"]},
    "passport_details": {"passport_number": "k 1234567", "date_of_expiry": "2030-01-05", "place_of_issue": "Cochin"},
    "designation": "Site Engineer",
    "highest_qualification": "B.Tech",
    "education": [
        {"degree": "B.Tech Civil Engineering", "institution": "NSS College", "start": "2012", "end": "2016"},
        {"degree": "Diploma", "institution": "Govt Polytechnic", "start": "2009", "end": "2012"},
        {"degree": "MBA", "institution": "XYZ", "start": "2020", "end": "Present"},
    ],
    "experience": [
        {"title": "Site Engineer", "company": "ABC Builders", "location": "Kochi, India", "start": "Jan 2018", "end": "Mar 2020",
         "duration_months": 26, "duration_human": "2 years 2 months", "is_overseas": False},
        {"title": "Supervisor", "company": "Gulf Cons", "country": "UAE", "start": "Apr 2020", "end": "Present",
         "duration_months": 40, "duration_human": "3 years 4 months", "is_overseas": True},
    ],
    "total_experience_months": 66, "indian_experience_months": 26, "overseas_experience_months": 40,
    "skills": ["AutoCAD", "autocad", "Estimating"],
}


def test_resume_values_map_every_form_field():
    values = fx.resume_values(RESUME)
    assert values["name"] == "Arjun Menon"
    assert values["father_name"] == "Gopalan Menon"
    assert values["marital_status"] == "Single"
    assert values["dob"] == date(1994, 3, 14)
    assert values["languages"] == ["English", "Malayalam"]
    assert values["current_address"] == "12 Gandhi Road, Kochi, Kerala 682001"
    assert values["passport_no"] == "K1234567" and values["passport_place"] == "Cochin" and values["passport_expiry"] == date(2030, 1, 5)
    assert values["education"] == [
        {"level": "B.Tech Civil Engineering", "passedYear": 2016, "institute": "NSS College"},
        {"level": "Diploma", "passedYear": 2012, "institute": "Govt Polytechnic"},
        {"level": "MBA", "passedYear": None, "institute": "XYZ"},  # still studying: no passed year
    ]
    assert values["work_history"][0] == {"company": "ABC Builders", "designation": "Site Engineer", "country": "India",
                                         "period": "Jan 2018 - Mar 2020 (2 years 2 months)"}
    assert values["work_history"][1]["country"] == "UAE" and values["work_history"][1]["period"].startswith("Apr 2020 - Present")
    assert (values["total_experience"], values["experience_india"], values["gulf_experience"]) == (5, 2, 3)
    assert values["skills"] == ["AutoCAD", "Estimating"]


@pytest.mark.parametrize("raw", [None, [], "text", 7, {}, {"education": "B.Tech", "experience": None, "contact": []},
                                 {"education": [None, 3, {"degree": None}], "experience": [{}, "x", {"company": "", "title": " "}]},
                                 {"personal_info": {"marital_status": 5, "father_name": ["a"]}, "total_experience_months": "many"}])
def test_resume_values_never_raise_on_junk(raw):
    values = fx.resume_values(raw)
    assert isinstance(values, dict)
    assert "education" not in values and "work_history" not in values


def test_education_variants_and_fallback():
    rows = fx.education_rows({"education": ["MSc Physics 2015", {"qualification": "SSLC", "school": "St Mary's", "year": "Class of 2009"},
                                            {"course": "BCA", "university": "MG University", "location": "Kottayam", "end": "05/2014"}]})
    assert rows[0] == {"level": "MSc Physics 2015", "passedYear": 2015, "institute": None}
    assert rows[1]["passedYear"] == 2009 and rows[1]["institute"] == "St Mary's"
    assert rows[2] == {"level": "BCA", "passedYear": 2014, "institute": "MG University, Kottayam"}
    assert fx.education_rows({"highest_qualification": "ITI Welder"}) == [{"level": "ITI Welder", "passedYear": None, "institute": None}]
    assert fx.education_rows({"education": [{"degree": "BE", "institution": "A", "end": "2016"}] * 3}) == [{"level": "BE", "passedYear": 2016, "institute": "A"}]


def test_work_history_period_without_dates_and_totals_from_entries():
    raw = {"experience": [{"company": "X", "duration_months": 14, "duration_human": "1 year 2 months"},
                          {"title": "Welder", "is_overseas": True, "duration_months": 24, "end": "2021"}]}
    rows = fx.history_rows(raw)
    assert rows[0] == {"company": "X", "designation": None, "country": None, "period": "1 year 2 months"}
    assert rows[1]["period"] == "? - 2021"
    assert fx._experience_years(raw) == (3, 1, 2)


@pytest.mark.parametrize("raw, expected", [("Single", "Single"), ("UNMARRIED", "Single"), ("married", "Married"), ("Widower", "Widowed"),
                                          ("divorced", "Divorced"), ("In a relationship", "In A Relationship"), ("", None), (None, None)])
def test_marital_status(raw, expected):
    assert fx.normalize_marital(raw) == expected


PASSPORT = {
    "mrz": {"surname": "MENON<<", "given_names": "ARJUN<GOPALAN", "passport_number": "K1234567", "nationality": "IND",
            "date_of_birth": "1994-03-14", "sex": "M", "expiry_date": "2030-01-05", "date_of_issue": None},
    "fields": [{"label": "Place of Issue", "value": "COCHIN"}, {"label": "Date of Issue", "value": "06/01/2020"},
               {"label": "Name of Father / Legal Guardian", "value": "GOPALAN MENON"},
               {"label": "Address", "value": "HOUSE 12, GANDHI ROAD\nKOCHI"}],
}


def test_passport_values_read_mrz_and_printed_fields():
    values = fx.passport_values(PASSPORT)
    assert values["name"] == "Arjun Gopalan Menon"
    assert values["dob"] == date(1994, 3, 14) and values["gender"] == "male" and values["nationality"] == "Indian"
    assert values["passport_no"] == "K1234567" and values["passport_expiry"] == date(2030, 1, 5)
    assert values["passport_issue"] == date(2020, 1, 6) and values["passport_place"] == "COCHIN"
    assert values["father_name"] == "Gopalan Menon"
    assert values["current_address"] == "HOUSE 12, GANDHI ROAD, KOCHI"


@pytest.mark.parametrize("raw", [None, "x", {"mrz": None, "fields": "no"}, {"mrz": {"surname": 5}, "fields": [None, {"label": None}]}])
def test_passport_values_never_raise_on_junk(raw):
    assert isinstance(fx.passport_values(raw), dict)


# ------------------------------------------------------------------ the fill rule

def test_fills_empty_and_stamps_extracted():
    c = person()
    result = fx.apply_values(c, "resume", fx.resume_values(RESUME))
    assert c.father_name == "Gopalan Menon" and c.education[0]["passedYear"] == 2016
    assert "personal.fatherName" in result.filled and "education" in result.filled
    assert fx.public_sources(c.field_sources)["personal.fatherName"] == "extracted"


def test_never_overwrites_a_manual_value():
    c = person(father_name="Typed Name", field_sources={"personal.fatherName": "manual"}, total_experience=9,
               marital_status="Married")  # no mark on marital: an old value that differs is kept too
    result = fx.apply_values(c, "resume", fx.resume_values(RESUME))
    assert c.father_name == "Typed Name" and c.marital_status == "Married" and c.total_experience == 9
    assert {"personal.fatherName", "personal.maritalStatus", "experience.totalExperience"} <= set(result.kept)


def test_manual_zero_is_protected_but_default_zero_is_empty():
    protected = person(field_sources={"experience.totalExperience": "manual"})
    fx.apply_values(protected, "resume", fx.resume_values(RESUME))
    assert protected.total_experience == 0
    default = person()
    fx.apply_values(default, "resume", fx.resume_values(RESUME))
    assert default.total_experience == 5


def test_extracted_values_are_refreshed_by_a_better_document():
    c = person()
    fx.apply_values(c, "passport", {"passport_no": "K1234567", "passport_expiry": date(2029, 1, 1)})
    fx.apply_values(c, "passport", {"passport_no": "K1234567", "passport_expiry": date(2030, 1, 5)})
    assert (c.passport_no, c.passport_expiry) == ("K1234567", date(2030, 1, 5))


def test_resume_cannot_replace_a_passport_value():
    c = person()
    fx.apply_values(c, "passport", fx.passport_values(PASSPORT))
    fx.apply_values(c, "resume", {"name": "A. Menon", "dob": date(1990, 1, 1), "nationality": "Indian", "current_address": "Somewhere"})
    assert c.name == "Arjun Gopalan Menon" and c.dob == date(1994, 3, 14)
    assert c.current_address == "Somewhere"  # an address is not a passport authority field


def test_passport_overrides_a_value_read_from_a_resume():
    c = person()
    fx.apply_values(c, "resume", {"name": "A Menon", "dob": date(1990, 1, 1)})
    fx.apply_values(c, "passport", fx.passport_values(PASSPORT))
    assert c.name == "Arjun Gopalan Menon" and c.dob == date(1994, 3, 14)
    assert fx.sources_of(c)["_fromPassport"]


def test_unmarked_equal_value_is_stamped_extracted_and_placeholder_name_is_empty():
    c = person(name="Name needs review", nationality="Indian")
    fx.apply_values(c, "resume", {"name": "Arjun Menon", "nationality": "indian"})
    assert c.name == "Arjun Menon"
    assert fx.public_sources(c.field_sources)["personal.nationality"] == "extracted"


def test_skills_and_job_title_follow_the_same_rule():
    c = person()
    fx.apply_values(c, "resume", fx.resume_values(RESUME))
    assert c.skills == ["AutoCAD", "Estimating"] and c.designations == ["Site Engineer"]
    assert fx.public_sources(c.field_sources)["screening.skills"] == "extracted"
    typed = person(skills=["Welding"])
    fx.mark_manual(typed, ["skills"])
    fx.apply_values(typed, "resume", fx.resume_values(RESUME))
    assert typed.skills == ["Welding"]
    plain = person(skills=["Welding"])  # an old value with no mark that differs is kept too
    fx.apply_values(plain, "resume", fx.resume_values(RESUME))
    assert plain.skills == ["Welding"]


def test_automatic_hook_fills_only_the_form_only_columns():
    c = person(skills=[])
    fx.apply_values(c, "resume", fx.resume_values(RESUME), fill_all=False)
    assert c.father_name and c.education and c.experience_india == 2
    assert c.name == "" and c.dob is None and c.languages == [] and c.skills == []  # older columns are left to their own code


def test_stored_documents_replay_is_idempotent_and_latest_wins():
    older = {"mrz": {**PASSPORT["mrz"], "passport_number": "K0000000"}, "fields": []}
    newer = PASSPORT
    c = person(ocr_data={"passport": older, "additional_documents": [{"passport": newer}], "resume": RESUME})
    first = fx.apply_stored_ocr(c, force=True)
    assert c.passport_no == "K1234567" and first.filled
    snapshot = dict(vars(c))
    second = fx.apply_stored_ocr(c, force=True)
    assert second.filled == [] and vars(c) == snapshot


def test_reviewed_identity_conflict_replay_fills_only_resume_content():
    """A future forced refresh must not revive OCR identity values a reviewer rejected."""
    c = person(name="Arjun Menon", dob=date(1980, 5, 25), gender="female", passport_no="R7654321",
               passport_place="Reviewer-selected place", ocr_data={
                   "resume": RESUME,
                   "passport": PASSPORT,
                   "person_split": {"contentOnlyAutofill": True, "ocrIdentityConflictFields": ["dob", "gender"]},
               })
    fx.mark_manual(c, ["name", "dob", "gender", "passport_no", "passport_place"])

    first = fx.apply_stored_ocr(c, force=True)

    # The manually reviewed identity is unchanged, including blank contact and
    # non-content form fields that the resume would otherwise provide.
    assert (c.name, c.dob, c.gender, c.passport_no, c.passport_place) == (
        "Arjun Menon", date(1980, 5, 25), "female", "R7654321", "Reviewer-selected place")
    assert c.phone == "" and c.email is None
    # A confirmed-person review may safely restore non-identity profile fields,
    # while family/identity fields remain withheld until directly reviewed.
    assert c.father_name is None
    assert c.marital_status == "Single"
    # Both documents may offer a non-identity address. The later passport
    # reading is the established precedence in a forced replay.
    assert c.current_address == "HOUSE 12, GANDHI ROAD, KOCHI"
    # Work/profile content survives and remains available to the recruiters.
    assert c.education and c.work_history
    assert (c.total_experience, c.experience_india, c.gulf_experience) == (5, 2, 3)
    assert c.skills == ["AutoCAD", "Estimating"] and c.designations == ["Site Engineer"]
    assert c.current_city == "Kochi" and c.languages == ["English", "Malayalam"]
    assert first.filled

    again = fx.apply_stored_ocr(c, force=True)
    assert again.filled == []


def test_primary_document_is_applied_once_and_stale_primary_does_not_undo_an_upload():
    c = person(ocr_data={"passport": {"mrz": {**PASSPORT["mrz"], "passport_number": "K0000000"}, "fields": []}})
    fx.apply_stored_ocr(c)
    assert c.passport_no is None  # not a form-only column: the automatic path leaves it to the intake code
    fx.mark_stored_applied(c)
    fx.apply_document(c, "passport", PASSPORT)
    assert c.passport_no == "K1234567"
    again = fx.apply_stored_ocr(c, fill_all=True)  # the older primary document must not run over the upload
    assert c.passport_no == "K1234567" and again.filled == []


def test_mark_manual_and_clear():
    c = person(father_name="A", education=[{"level": "BE"}])
    fx.mark_manual(c, ["father_name", "education", "total_experience"])
    assert fx.public_sources(c.field_sources) == {"personal.fatherName": "manual", "education": "manual"}
    c.father_name = None
    fx.mark_manual(c, ["father_name"])
    assert "personal.fatherName" not in fx.public_sources(c.field_sources)
