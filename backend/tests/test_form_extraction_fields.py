"""Every usual resume detail reaches the form, in the shapes real resumes print it (no database needed).

The owner's case: a scanned resume was read, the screen said "Filled 13 fields", yet Date of birth and Mobile number were
empty although the resume prints them. Each parametrised row below is one way such a value used to be dropped silently.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from app.integrations.ocr.mapping import find_date_in, parse_loose_date, phone_candidates, pick_email, pick_phone, resume_fields
from app.services import form_extraction as fx
from test_application_form_extraction import person


def raw_resume(**sections):
    """The reader's raw shape (veriis ResumeResponse as stored in ocr_data["resume"]), with only the sections given."""
    base = {"name": "SURESH KUMAR", "contact": {}, "personal_info": {}, "passport_details": {}, "experience": [], "education": [], "skills": []}
    for key, value in sections.items():
        base[key] = {**base[key], **value} if isinstance(base.get(key), dict) and isinstance(value, dict) else value
    return base


# ------------------------------------------------------------------ date of birth

@pytest.mark.parametrize("written", [
    "12/03/1995", "12-03-1995", "12.03.1995", "12 / 03 / 1995", "12/3/1995", "1995-03-12", "1995/03/12", "1995-03-12T00:00:00Z",
    "12-Mar-1995", "12 Mar 1995", "12 MARCH 1995", "12th March 1995", "12th March, 1995", "12th of March 1995", "March 12, 1995",
    "Mar 12 1995", "12 Mar, 1995", "12-March-1995", "DOB: 12/03/1995", "Date of Birth : 12 March 1995", "D.O.B. 12.03.1995",
    "12/03/1995 (29 years)", "12/03/1995 Age: 29", "12/03/95", "12-03-95", "12.03.95", "12-Mar-95", "12 Mar'95", "12-03-1995.",
])
def test_date_of_birth_formats_all_read_day_first(written):
    raw = raw_resume(personal_info={"date_of_birth": written})
    assert fx.resume_values(raw)["dob"] == date(1995, 3, 12)


@pytest.mark.parametrize("written, expected", [
    ("03/25/1995", date(1995, 3, 25)),      # 25 can only be the day: month first is the only reading
    ("12 Sept 1995", date(1995, 9, 12)),
    ("5/1/05", date(2005, 1, 5)),           # two-digit year: the century that makes a working-age person
    ("5/1/70", date(1970, 1, 5)),
])
def test_date_of_birth_edge_formats(written, expected):
    assert fx.resume_values(raw_resume(personal_info={"date_of_birth": written}))["dob"] == expected


@pytest.mark.parametrize("written", ["1995", "March 1995", "31/02/1995", "not given", "N/A", "", "12/03/12", "12/03/2099", "00/00/0000"])
def test_date_of_birth_that_is_not_one_exact_plausible_day_stays_empty(written):
    assert "dob" not in fx.resume_values(raw_resume(personal_info={"date_of_birth": written}))


def test_two_digit_year_is_read_only_when_a_role_is_given_and_never_for_the_other_dates_century():
    assert parse_loose_date("12/03/95") is None
    assert parse_loose_date("12/03/35", "other") == date(2035, 3, 12)       # an expiry, not 1935
    assert parse_loose_date("12/03/99", "other") == date(1999, 3, 12)       # an issue date
    assert find_date_in("DOB 12/03/1995 Gender Male", "birth") == date(1995, 3, 12)


def test_passport_dates_in_a_resume_read_two_digit_years():
    raw = raw_resume(passport_details={"passport_number": "k 1234567", "date_of_issue": "06/01/20", "date_of_expiry": "05-01-30",
                                       "place_of_issue": "COCHIN"})
    values = fx.resume_values(raw)
    assert (values["passport_issue"], values["passport_expiry"]) == (date(2020, 1, 6), date(2030, 1, 5))


# ------------------------------------------------------------------ mobile number

@pytest.mark.parametrize("phones, whatsapp, expected", [
    (["+91 98765 43210"], None, "+91 98765 43210"),
    (["98765 43210"], None, "98765 43210"),
    (["098765 43210"], None, "098765 43210"),
    (["+91-98765-43210"], None, "+91-98765-43210"),
    (["Mobile: 98765 43210"], None, "98765 43210"),
    (["Mob No. : +91 98765 43210"], None, "+91 98765 43210"),
    (["(M) 98765 43210"], None, "98765 43210"),
    (["98765 43210 (WhatsApp)"], None, "98765 43210"),
    (["98765 43210 / 98765 43211"], None, "98765 43210"),
    (["98765 43210, 98765 43211"], None, "98765 43210"),
    (["98765 43210 and 98765 43211"], None, "98765 43210"),
    (["98765 43210 98765 43211"], None, "98765 43210"),                 # two numbers, nothing between them
    (["0484 2345678", "98765 43210"], None, "98765 43210"),            # a landline first, the mobile second
    (["+91 484 2345678", "+91 98765 43210"], None, "+91 98765 43210"),
    (["+971 50 123 4567", "98765 43210"], None, "+971 50 123 4567"),   # a Gulf number first stays first
    ([], "+91 98765 43210", "+91 98765 43210"),                         # only a WhatsApp number printed
    ([], "WhatsApp: 98765 43210", "98765 43210"),
    (["0484 2345678"], None, "0484 2345678"),                           # a landline when there is nothing else
    ("98765 43210", None, "98765 43210"),                               # the reader gave text, not a list
])
def test_mobile_number_formats(phones, whatsapp, expected):
    values = fx.resume_values(raw_resume(contact={"phones": phones, "whatsapp": whatsapp}))
    assert values["phone"] == expected
    assert len(values["phone"]) <= 40                                    # the column is 40 characters


@pytest.mark.parametrize("phones", [["12345"], ["N/A"], [""], ["call me"], ["1234 5678 9012 3456 7890 1234 5678 90"]])
def test_unusable_phone_text_gives_no_phone(phones):
    assert "phone" not in fx.resume_values(raw_resume(contact={"phones": phones}))


def test_a_whatsapp_number_fills_both_the_whatsapp_and_the_mobile_field():
    values = fx.resume_values(raw_resume(contact={"phones": [], "whatsapp": "+91 98765 43210"}))
    assert values["phone"] == values["whatsapp"] == "+91 98765 43210"


def test_helpers_never_raise_on_junk():
    for junk in (None, 5, True, [None, 3, {}], {"a": 1}, "\x00\x00"):
        pick_phone(junk, junk), pick_email(junk), phone_candidates(junk)


# ------------------------------------------------------------------ e-mail

@pytest.mark.parametrize("emails, expected", [
    (["Suresh.Kumar@Gmail.com"], "suresh.kumar@gmail.com"),
    (["Email: suresh@yahoo.co.in"], "suresh@yahoo.co.in"),
    (["suresh @ gmail.com"], "suresh@gmail.com"),
    (["gmail.com", "suresh@gmail.com"], "suresh@gmail.com"),            # the first entry is not an address
    ("suresh@gmail.com", "suresh@gmail.com"),
])
def test_email_formats(emails, expected):
    assert fx.resume_values(raw_resume(contact={"emails": emails}))["email"] == expected


# ------------------------------------------------------------------ the other personal details

@pytest.mark.parametrize("info, expected", [
    ({"father_name": "Ramesh Kumar"}, {"father_name": "Ramesh Kumar"}),
    ({"father_name": "S/O Ramesh Kumar"}, {"father_name": "Ramesh Kumar"}),
    ({"father_name": "Mr. Ramesh Kumar"}, {"father_name": "Ramesh Kumar"}),
    ({"father_name": "LATE RAMESH KUMAR"}, {"father_name": "Ramesh Kumar"}),
    ({"fathers_name": "RAMESH KUMAR"}, {"father_name": "Ramesh Kumar"}),
    ({"marital_status": "Married"}, {"marital_status": "Married"}),
    ({"marital_status": "UNMARRIED"}, {"marital_status": "Single"}),
    ({"marital_status": "Marital Status: Single"}, {"marital_status": "Single"}),
    ({"marital_status": "Bachelor"}, {"marital_status": "Single"}),
    ({"gender": "Male"}, {"gender": "male"}), ({"gender": "M"}, {"gender": "male"}), ({"gender": "MALE."}, {"gender": "male"}),
    ({"gender": "Sex: Female"}, {"gender": "female"}), ({"gender": "F"}, {"gender": "female"}), ({"gender": "Male (M)"}, {"gender": "male"}),
    ({"nationality": "Indian"}, {"nationality": "Indian"}), ({"nationality": "INDIAN"}, {"nationality": "Indian"}),
    ({"languages_known": ["English", "Hindi", "Tamil"]}, {"languages": ["English", "Hindi", "Tamil"]}),
    ({"languages_known": ["English, Hindi & Tamil"]}, {"languages": ["English", "Hindi", "Tamil"]}),   # one text the reader did not split
    ({"languages_known": "English, Hindi"}, {"languages": ["English", "Hindi"]}),                      # a text instead of a list
])
def test_personal_detail_formats(info, expected):
    values = fx.resume_values(raw_resume(personal_info=info))
    assert {key: values[key] for key in expected} == expected


def test_address_with_line_breaks_and_the_rest_of_the_form():
    values = fx.resume_values(raw_resume(
        contact={"address": "H.No 12, Gandhi Road\nKochi,\nKerala 682001"}, designation="Welder",
        skills=["Arc welding", "MIG"], highest_qualification="ITI"))
    assert values["current_address"] == "H.No 12, Gandhi Road, Kochi, Kerala 682001"
    assert values["designations"] == ["Welder"] and values["skills"] == ["Arc welding", "MIG"]
    assert values["education"] == [{"level": "ITI", "passedYear": None, "institute": None}]


# ------------------------------------------------------------------ the page text, beside a label

SCANNED_PAGE = """CURRICULUM VITAE
SURESH KUMAR
Email : suresh.kumar@gmail.com
Mobile No : +91 98765 43210
PERSONAL DETAILS
Father's Name : Ramesh Kumar
Date of Birth : 12th March, 1995
Marital Status : Married
Gender : Male
Nationality : Indian
Reference: Mr. Ravi, Mobile: 99887 76655
"""


def test_details_the_reader_left_out_are_read_from_the_page_text_beside_their_label():
    raw = raw_resume(pages=[{"page_number": 1, "text": SCANNED_PAGE}])
    values = fx.resume_values(raw)
    assert values["dob"] == date(1995, 3, 12)
    assert values["phone"] == "+91 98765 43210"             # the candidate's own, not the reference's (the first labelled one)
    assert values["email"] == "suresh.kumar@gmail.com"
    assert values["father_name"] == "Ramesh Kumar"
    assert values["marital_status"] == "Married" and values["gender"] == "male" and values["nationality"] == "Indian"


def test_the_readers_structured_value_always_wins_over_the_page_text():
    raw = raw_resume(personal_info={"date_of_birth": "01/01/1990", "gender": "Female"}, contact={"phones": ["+91 90000 11111"]},
                     pages=[{"page_number": 1, "text": SCANNED_PAGE}])
    values = fx.resume_values(raw)
    assert values["dob"] == date(1990, 1, 1) and values["gender"] == "female" and values["phone"] == "+91 90000 11111"


def test_page_text_without_a_label_or_with_nonsense_gives_nothing():
    text = "Worked 12/03/1995 to 2000 at ABC. Call 98765 43210 for details. Born in a small town."
    values = fx.resume_values(raw_resume(pages=[{"page_number": 1, "text": text}]))
    assert not {"dob", "phone", "father_name", "marital_status", "gender", "nationality"} & set(values)


def test_form_mapping_for_the_add_candidate_screen_gets_the_same_details():
    from veriis.models import ResumeResponse
    model = ResumeResponse.model_validate({"request_id": "r", "processing_time_ms": 1, "name": "Suresh Kumar",
                                           "contact": {"phones": [], "whatsapp": "+91 98765 43210"},
                                           "personal_info": {"date_of_birth": "12/03/95", "gender": "M"},
                                           "pages": [{"page_number": 1, "text": SCANNED_PAGE, "language": "en", "width": 1, "height": 1,
                                                      "average_confidence": 0.9, "processing_time_ms": 1}]})
    fields, _ = resume_fields(model, [])
    assert fields.phone == "+91 98765 43210" and fields.dob == date(1995, 3, 12) and fields.gender == "male"
    assert fields.email == "suresh.kumar@gmail.com" and fields.father_name == "Ramesh Kumar"


# ------------------------------------------------------------------ every field lands on the form

REALISTIC = [
    ("tidy", raw_resume(
        name="Suresh Kumar",
        contact={"phones": ["+91 98765 43210"], "emails": ["suresh@gmail.com"], "address": "12 Gandhi Road\nKochi, Kerala 682001"},
        personal_info={"father_name": "Ramesh Kumar", "marital_status": "Single", "date_of_birth": "12/03/1995", "gender": "Male",
                       "nationality": "Indian", "languages_known": ["English", "Hindi"]})),
    ("indian scan", raw_resume(
        name="SURESH KUMAR",
        contact={"phones": ["Mob: 098765 43210 / 98765 43211"], "emails": ["Email: Suresh@Gmail.com"], "address": "H.No 12, Gandhi Road, Kochi"},
        personal_info={"father_name": "S/O Ramesh Kumar", "marital_status": "UNMARRIED", "date_of_birth": "12th March, 1995",
                       "gender": "MALE.", "nationality": "INDIAN", "languages_known": ["English, Hindi"]})),
    ("two-digit year, whatsapp only", raw_resume(
        name="Suresh Kumar",
        contact={"phones": [], "whatsapp": "WhatsApp: 98765 43210", "emails": ["suresh@gmail.com"], "address": "12 Gandhi Road Kochi"},
        personal_info={"father_name": "Mr. Ramesh Kumar", "marital_status": "Marital Status: Single", "date_of_birth": "12-03-95",
                       "gender": "M", "nationality": "Indian", "languages_known": "English; Hindi"})),
]


@pytest.mark.parametrize("label, raw", REALISTIC, ids=[label for label, _ in REALISTIC])
def test_every_usual_resume_field_lands_on_the_candidate(label, raw):
    candidate = person()
    result = fx.apply_document(candidate, "resume", raw)
    assert candidate.name == "Suresh Kumar"
    assert candidate.father_name == "Ramesh Kumar"
    assert candidate.dob == date(1995, 3, 12)
    assert candidate.marital_status in ("Single",)
    assert candidate.gender == "male" and candidate.nationality == "Indian"
    assert candidate.languages == ["English", "Hindi"]
    assert candidate.phone.replace(" ", "").replace("-", "").lstrip("+0").endswith("9876543210")
    assert candidate.email == "suresh@gmail.com" and candidate.current_address
    for key in ("personal.dateOfBirth", "personal.mobileNumber", "personal.fatherName", "personal.maritalStatus", "personal.gender",
                "personal.nationality", "personal.languagesKnown", "personal.currentAddress", "personal.email"):
        assert key in result.filled, key
    assert result.skipped == []                      # nothing the document held was left off the form


# ------------------------------------------------------------------ never silent

def test_a_value_the_reader_had_but_that_cannot_be_used_is_listed_as_not_filled():
    raw = raw_resume(personal_info={"date_of_birth": "sometime in 1995", "gender": "Prefer not to say"},
                     contact={"phones": ["call me"], "emails": ["none"]})
    candidate = person()
    result = fx.apply_document(candidate, "resume", raw)
    assert candidate.dob is None and not candidate.phone
    by_field = {entry["field"]: entry["reason"] for entry in result.skipped}
    assert by_field == {"personal.dateOfBirth": "unreadable", "personal.mobileNumber": "unreadable", "personal.email": "unreadable",
                        "personal.gender": "unreadable"}
    assert {"personal.dateOfBirth", "personal.mobileNumber"} <= set(result.kept)     # the list the screen already carries


def test_an_unreadable_value_is_not_reported_when_the_form_already_has_one():
    raw = raw_resume(personal_info={"date_of_birth": "sometime in 1995"})
    candidate = person(dob=date(1990, 1, 1))
    assert fx.apply_document(candidate, "resume", raw).skipped == []


def test_a_field_withheld_by_the_caller_is_listed_unless_it_is_a_passport_column():
    raw = raw_resume(contact={"address": "Kochi"}, passport_details={"passport_number": "K1234567"})
    candidate = person()
    result = fx.apply_document(candidate, "resume", raw, ignore_fields=("current_address", "passport_no"))
    assert [entry["field"] for entry in result.skipped] == ["personal.currentAddress"]
    assert candidate.current_address is None and candidate.passport_no is None


def test_a_phone_that_belongs_to_another_candidate_is_not_copied_and_the_result_says_whose(monkeypatch):
    other = SimpleNamespace(id="other", care_id="CI-157", name="Another Person", phone="98765 43210")
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *a, **k: [(other, ["phone number"])])
    candidate = person(id="mine")
    raw = raw_resume(contact={"phones": ["+91 98765 43210"]}, personal_info={"date_of_birth": "12/03/1995"})
    result = fx.apply_values(candidate, "resume", fx.resume_values(raw), db=object())
    assert not candidate.phone and candidate.dob is None                    # the identity rule is unchanged: nothing is copied
    assert result.conflicts and result.filled == []
    assert result.skipped == [{"field": "personal.mobileNumber", "reason": "owned_by_other", "candidate": "CI-157"}]
    assert "98765" not in repr(result)                                      # never the number itself


def test_an_e_mail_or_passport_owned_by_another_candidate_is_named_by_field_only(monkeypatch):
    other = SimpleNamespace(id="other", care_id="CI-9", name="Another Person")
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *a, **k: [(other, ["email", "passport number"])])
    result = fx.apply_values(person(id="mine"), "resume", fx.resume_values(
        raw_resume(contact={"emails": ["x@y.com"]}, passport_details={"passport_number": "K1234567"})), db=object())
    assert {entry["field"] for entry in result.skipped} == {"personal.email", "passport.passportNumber"}
    assert all(entry["candidate"] == "CI-9" for entry in result.skipped)


def test_apply_result_merge_keeps_skipped_without_duplicates():
    first, second = fx.ApplyResult(), fx.ApplyResult()
    first.skip("personal.mobileNumber", "unreadable")
    second.skip("personal.mobileNumber", "unreadable")
    second.skip("personal.dateOfBirth", "unreadable")
    first.merge(second)
    assert [entry["field"] for entry in first.skipped] == ["personal.mobileNumber", "personal.dateOfBirth"]
    assert first.kept == ["personal.mobileNumber", "personal.dateOfBirth"]


def test_a_replay_of_the_stored_resume_reports_what_it_could_not_fill():
    stored = raw_resume(personal_info={"date_of_birth": "sometime in 1995", "father_name": "Ramesh Kumar"})
    candidate = person(ocr_data={"resume": stored})
    result = fx.apply_stored_ocr(candidate, None, force=True)
    assert candidate.father_name == "Ramesh Kumar"
    assert {"field": "personal.dateOfBirth", "reason": "unreadable"} in result.skipped
    assert "personal.dateOfBirth" in result.kept and "personal.fatherName" in result.filled
