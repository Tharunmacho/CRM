"""
The OCR → form mapping. Everything a recruiter sees pre-filled passes through here, so
each case below is a value that would otherwise land wrong on the form.
"""

from datetime import date

import pytest
from veriis.models import PassportResponse, ResumeResponse

from app.integrations.ocr.mapping import (
    parse_loose_date,
    passport_fields,
    resume_fields,
    resume_text_from,
    trim_raw,
)


def resume(**overrides) -> ResumeResponse:
    return ResumeResponse.model_validate({"request_id": "r1", "processing_time_ms": 10, **overrides})


def passport(mrz: dict, **overrides) -> PassportResponse:
    return PassportResponse.model_validate(
        {"request_id": "p1", "confidence": 0.9, "processing_time_ms": 10, "mrz": mrz, **overrides}
    )


# ------------------------------------------------------------------ dates

@pytest.mark.parametrize(
    "raw, want",
    [
        ("1990-05-12", date(1990, 5, 12)),
        # Indian resumes write the day first; 05/12 is the 5th of December, not May 12.
        ("05/12/1990", date(1990, 12, 5)),
        ("05-12-1990", date(1990, 12, 5)),
        ("05.12.1990", date(1990, 12, 5)),
        ("12 May 1990", date(1990, 5, 12)),
        ("12th May 1990", date(1990, 5, 12)),
        ("12-May-1990", date(1990, 5, 12)),
        ("12 September 1990", date(1990, 9, 12)),
        ("May 12, 1990", date(1990, 5, 12)),
        ("  12/05/1990 ", date(1990, 5, 12)),
    ],
)
def test_parse_loose_date_reads_common_resume_formats(raw, want):
    assert parse_loose_date(raw) == want


@pytest.mark.parametrize("raw", [None, "", "not a date", "1990", "31/02/1990"])
def test_parse_loose_date_gives_none_for_anything_it_cannot_be_sure_of(raw):
    assert parse_loose_date(raw) is None


# ----------------------------------------------------------------- resume

def test_resume_maps_contact_and_personal_details():
    fields, _ = resume_fields(
        resume(
            name="Nithin Varghese",
            contact={
                "emails": ["Nithin.V@Gmail.com", "other@example.com"],
                "phones": ["+91 98470 22310", "0484 2345678"],
                "whatsapp": "+91 98470 22311",
            },
            personal_info={
                "date_of_birth": "14/03/1994",
                "gender": "Male",
                "nationality": "Indian",
                "languages_known": ["English", "Malayalam"],
            },
        ),
        designations=[],
    )

    assert fields.name == "Nithin Varghese"
    assert fields.phone == "+91 98470 22310"
    assert fields.whatsapp == "+91 98470 22311"
    assert fields.email == "nithin.v@gmail.com"
    assert fields.dob == date(1994, 3, 14)
    assert fields.gender == "male"
    assert fields.nationality == "Indian"
    assert fields.languages == ["English", "Malayalam"]


def test_resume_name_in_capitals_is_written_as_a_person_would():
    # Resumes often head the page with the name in capitals; the form should not keep them.
    fields, _ = resume_fields(resume(name="ARJUN KUMAR MENON"), designations=[])

    assert fields.name == "Arjun Kumar Menon"


def test_resume_name_already_in_mixed_case_is_left_alone():
    # Title-casing this would turn "McKay" into "Mckay".
    fields, _ = resume_fields(resume(name="Ryan McKay"), designations=[])

    assert fields.name == "Ryan McKay"


def test_resume_maps_passport_details_with_parsed_dates():
    fields, _ = resume_fields(
        resume(
            passport_details={
                "passport_number": "m1234567",
                "date_of_issue": "02/01/2019",
                "date_of_expiry": "01/01/2029",
                "place_of_issue": "Cochin",
            }
        ),
        designations=[],
    )

    assert fields.passport_no == "M1234567"
    assert fields.passport_issue == date(2019, 1, 2)
    assert fields.passport_expiry == date(2029, 1, 1)
    assert fields.passport_place == "Cochin"


def test_resume_experience_counts_completed_years_only():
    # 6 years 11 months is "6 years" on a form that takes whole years — rounding up
    # would credit experience the candidate does not have yet.
    fields, _ = resume_fields(
        resume(total_experience_months=83, overseas_experience_months=30),
        designations=[],
    )

    assert fields.total_experience == 6
    assert fields.gulf_experience == 2


def test_resume_with_no_experience_read_leaves_the_years_blank():
    fields, _ = resume_fields(resume(), designations=[])

    assert fields.total_experience is None
    assert fields.gulf_experience is None


def test_resume_designation_is_matched_to_a_known_title_ignoring_case():
    fields, _ = resume_fields(
        resume(designation="tig welder"),
        designations=["Electrician", "TIG Welder"],
    )

    assert fields.designation == "TIG Welder"


def test_resume_designation_not_on_the_list_is_left_blank():
    # The form's designation is a select; a free-text title it does not offer would
    # silently show as "Choose one" and then save as nothing.
    fields, _ = resume_fields(
        resume(designation="Senior Pipe Fitter"),
        designations=["Electrician", "TIG Welder"],
    )

    assert fields.designation is None


def test_resume_skills_drop_duplicates_and_blanks_keeping_order():
    fields, _ = resume_fields(
        resume(skills=["TIG", "MIG", "tig", " ", "Blueprint reading"]),
        designations=[],
    )

    assert fields.skills == ["TIG", "MIG", "Blueprint reading"]


def test_resume_gender_it_does_not_recognise_is_left_blank():
    fields, _ = resume_fields(resume(personal_info={"gender": "N/A"}), designations=[])

    assert fields.gender is None


def test_resume_warnings_are_passed_through():
    _, warnings = resume_fields(resume(warnings=["Page 2 was blurred"]), designations=[])

    assert warnings == ["Page 2 was blurred"]


# --------------------------------------------------------------- passport

def test_passport_maps_the_mrz():
    fields, _ = passport_fields(
        passport(
            {
                "surname": "VARGHESE",
                "given_names": "NITHIN  KUMAR",
                "passport_number": "M1234567",
                "nationality": "IND",
                "date_of_birth": "1994-03-14",
                "sex": "M",
                "expiry_date": "2029-01-01",
                "date_of_issue": "2019-01-02",
                "all_check_digits_valid": True,
            }
        )
    )

    assert fields.name == "Nithin Kumar Varghese"
    assert fields.passport_no == "M1234567"
    assert fields.nationality == "Indian"
    assert fields.dob == date(1994, 3, 14)
    assert fields.gender == "male"
    assert fields.passport_expiry == date(2029, 1, 1)
    assert fields.passport_issue == date(2019, 1, 2)


def test_passport_with_only_a_surname_still_gives_a_name():
    fields, _ = passport_fields(passport({"surname": "ANJALI"}))

    assert fields.name == "Anjali"


def test_passport_nationality_code_without_a_demonym_is_kept_as_the_code():
    fields, _ = passport_fields(passport({"nationality": "KEN", "sex": "F"}))

    assert fields.nationality == "KEN"
    assert fields.gender == "female"


def test_passport_failed_check_digits_warn_the_recruiter():
    _, warnings = passport_fields(
        passport({"passport_number": "M1234567", "all_check_digits_valid": False})
    )

    assert any("check digit" in w.lower() for w in warnings)


def test_passport_with_valid_check_digits_adds_no_warning_of_its_own():
    _, warnings = passport_fields(
        passport({"passport_number": "M1234567", "all_check_digits_valid": True}, warnings=["glare"])
    )

    assert warnings == ["glare"]


# -------------------------------------------------------- stored output

def test_trim_raw_drops_word_boxes_but_keeps_page_text():
    r = resume(
        name="A",
        pages=[
            {
                "page_number": 1,
                "text": "Line one",
                "language": "eng",
                "width": 100,
                "height": 100,
                "average_confidence": 0.9,
                "processing_time_ms": 5,
                "lines": [
                    {"text": "Line one", "confidence": 0.9, "bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}
                ],
            }
        ],
    )

    raw = trim_raw(r)

    assert raw["name"] == "A"
    assert raw["pages"][0]["text"] == "Line one"
    assert "lines" not in raw["pages"][0]


def test_resume_text_joins_the_pages_of_the_stored_resume():
    ocr_data = {
        "resume": {"pages": [{"text": "Page one"}, {"text": "  "}, {"text": "Page two"}]},
        "passport": {"pages": [{"text": "PASSPORT"}]},
    }

    assert resume_text_from(ocr_data) == "Page one\n\nPage two"


@pytest.mark.parametrize("ocr_data", [None, {}, {"passport": {"pages": [{"text": "P"}]}}, {"resume": {}}])
def test_resume_text_is_none_without_resume_pages(ocr_data):
    assert resume_text_from(ocr_data) is None
