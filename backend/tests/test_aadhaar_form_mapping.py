"""Aadhaar copies in the application form: what is mapped, the fill rule, the precedence, and the privacy of the number.

The first half needs no database (stand-in candidates, synthetic cards). The second half is DB-backed against the guarded
disposable PostgreSQL like test_application_form.py and uses its fake document reader.
"""
import json
import logging
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import AuditEntry, Candidate, Source
from app.services import form_extraction as fx
from test_application_form import desk, form, url  # noqa: F401  (fixtures and helpers)
from test_application_form_extraction import RESUME as BASE_RESUME, person
from test_database_naming import schema_database  # noqa: F401

# Synthetic numbers only. The "full number" must never appear in anything the CRM shows, stores outside ocr_data, or logs.
FULL = "999988887777"
CARD = {
    "request_id": "a1", "document_type": "aadhaar", "page_count": 1, "languages_used": "eng", "total_processing_time_ms": 3, "pages": [],
    "aadhaar": {"name": "ALEX EXAMPLE", "aadhaar_number": FULL, "masked_aadhaar_number": "XXXX XXXX 7777", "aadhaar_number_valid": True,
                "date_of_birth": "1990-01-01", "gender": "male", "mobile_number": "XXXXXX1234", "address": "1 Example Road, Chennai, Tamil Nadu 600001",
                "care_of": "S/O: Ramesh Example", "pincode": "600001", "vid": "1111222233334444", "enrollment_id": "0000/00000/00000", "document_side": "both"},
}
RESUME = {**BASE_RESUME, "name": "Alex Example"}


def card(**identity):
    return {**CARD, "aadhaar": {**CARD["aadhaar"], **identity}}


# ------------------------------------------------------------------ mapping

def test_aadhaar_maps_name_birth_date_gender_address_and_the_fathers_name():
    values = fx.aadhaar_values(CARD)
    assert values == {"name": "Alex Example", "dob": date(1990, 1, 1), "gender": "male", "father_name": "Ramesh Example",
                      "current_address": "1 Example Road, Chennai, Tamil Nadu 600001"}


@pytest.mark.parametrize("care_of, father", [
    ("S/O Ramesh Example", "Ramesh Example"), ("S/o: Ramesh Example", "Ramesh Example"), ("D/O. Ramesh Example", "Ramesh Example"),
    ("W/O Ramesh Example", "Ramesh Example"),  # a wife's card names her husband: the form field takes it, the recruiter checks it
    ("Son of Ramesh Example", "Ramesh Example"), ("S/O RAMESH EXAMPLE, ", "Ramesh Example"),
    ("C/O Ramesh Example", None),  # "care of" names no relation
    ("Ramesh Example", None), ("S/O", None), ("S/O 12345", None), (None, None), (7, None), ({}, None),
])
def test_fathers_name_comes_only_from_an_s_o_d_o_or_w_o_line(care_of, father):
    values = fx.aadhaar_values(card(care_of=care_of, address=None))
    assert values.get("father_name") == father


def test_a_relation_clause_at_the_start_of_the_address_is_used_for_the_father_and_dropped_from_the_address():
    values = fx.aadhaar_values(card(care_of=None, address="S/O Ramesh Example, 1 Example Road, Chennai"))
    assert values["father_name"] == "Ramesh Example"
    assert values["current_address"] == "1 Example Road, Chennai"


def test_a_birth_year_alone_is_not_a_date_and_the_cards_numbers_are_never_returned():
    values = fx.aadhaar_values(card(date_of_birth=None, year_of_birth=1990))
    assert "dob" not in values
    assert FULL not in json.dumps(values, default=str) and "1111222233334444" not in json.dumps(values, default=str)
    assert not {"phone", "whatsapp", "passport_no"} & set(fx.aadhaar_values(CARD))  # the card's mobile number is not the phone


@pytest.mark.parametrize("raw", [None, [], "x", 3, {}, {"aadhaar": None}, {"aadhaar": []}, {"aadhaar": {"name": 5, "address": ["a"], "gender": {}}}])
def test_aadhaar_values_never_raise_on_junk(raw):
    assert isinstance(fx.aadhaar_values(raw), dict)


# ------------------------------------------------------------------ the fill rule and precedence

def test_aadhaar_fills_empty_fields_stamps_extracted_and_remembers_the_source():
    c = person()
    result = fx.apply_values(c, "aadhaar", fx.aadhaar_values(CARD))
    assert {"personal.candidateName", "personal.dateOfBirth", "personal.gender", "personal.fatherName", "personal.currentAddress"} <= set(result.filled)
    assert (c.name, c.dob, c.gender, c.father_name) == ("Alex Example", date(1990, 1, 1), "male", "Ramesh Example")
    assert fx.public_sources(c.field_sources) == {key: "extracted" for key in result.filled}
    assert set(c.field_sources["_fromAadhaar"]) == {"personal.candidateName", "personal.dateOfBirth", "personal.gender", "personal.fatherName"}


def test_aadhaar_never_overwrites_a_manual_value():
    c = person(name="A. Example", dob=date(1991, 2, 2), father_name="Typed Father", current_address="Typed address")
    fx.mark_manual(c, ["name", "dob", "father_name", "current_address"])
    result = fx.apply_values(c, "aadhaar", fx.aadhaar_values(CARD))
    assert (c.name, c.dob, c.father_name, c.current_address) == ("A. Example", date(1991, 2, 2), "Typed Father", "Typed address")
    assert result.filled == ["personal.gender"]  # the only field that was still empty
    assert {"personal.candidateName", "personal.dateOfBirth", "personal.fatherName", "personal.currentAddress"} <= set(result.kept)


def test_precedence_is_passport_over_aadhaar_over_resume_for_identity_in_any_order():
    passport = {"name": "Alex Example", "dob": date(1989, 9, 9), "gender": "female", "father_name": "Passport Father"}
    aadhaar = fx.aadhaar_values(CARD)
    resume = {"name": "Alex Example", "dob": date(1988, 8, 8), "gender": "male", "father_name": "Resume Father"}
    for order in (("resume", "aadhaar", "passport"), ("passport", "aadhaar", "resume"), ("aadhaar", "passport", "resume"),
                  ("passport", "resume", "aadhaar"), ("aadhaar", "resume", "passport"), ("resume", "passport", "aadhaar")):
        c = person()
        values = {"resume": resume, "aadhaar": aadhaar, "passport": passport}
        for kind in order:
            fx.apply_values(c, kind, values[kind])
        assert (c.name, c.dob, c.father_name) == ("Alex Example", date(1989, 9, 9), "Passport Father"), order
    for order in (("resume", "aadhaar"), ("aadhaar", "resume")):
        c = person()
        for kind in order:
            fx.apply_values(c, kind, {"resume": resume, "aadhaar": aadhaar}[kind])
        assert (c.name, c.dob, c.gender, c.father_name) == ("Alex Example", date(1990, 1, 1), "male", "Ramesh Example"), order


def test_the_resume_still_wins_experience_and_education_which_an_aadhaar_never_states():
    c = person()
    fx.apply_values(c, "aadhaar", fx.aadhaar_values(CARD))
    fx.apply_values(c, "resume", fx.resume_values(RESUME))
    assert c.total_experience == 5 and c.education and c.work_history
    assert c.name == "Alex Example"  # the resume did not take the name back from the card


def test_an_aadhaar_address_only_fills_an_empty_current_address():
    c = person()
    fx.apply_values(c, "resume", fx.resume_values(RESUME))
    kept = c.current_address
    result = fx.apply_values(c, "aadhaar", fx.aadhaar_values(CARD))
    assert c.current_address == kept and "personal.currentAddress" in result.kept
    empty = person()
    fx.apply_values(empty, "aadhaar", fx.aadhaar_values(CARD))
    assert empty.current_address == "1 Example Road, Chennai, Tamil Nadu 600001"
    # ... and a resume read later may still replace the extracted Aadhaar address with where the person lives now
    fx.apply_values(empty, "resume", fx.resume_values(RESUME))
    assert empty.current_address == "12 Gandhi Road, Kochi, Kerala 682001"


def test_stored_documents_replay_applies_resume_then_aadhaar_then_passport():
    c = person(ocr_data={"resume": RESUME, "aadhaar": CARD})
    result = fx.apply_stored_ocr(c, None, force=True)
    assert c.name == "Alex Example" and c.father_name == "Ramesh Example" and c.dob == date(1990, 1, 1)
    assert "personal.fatherName" in result.filled
    again = fx.apply_stored_ocr(c, None, force=True)
    assert again.filled == []  # idempotent
    automatic = person(ocr_data={"aadhaar": CARD})
    fx.apply_stored_ocr(automatic)  # the save hook fills the form-only columns: father's name and address, not the older columns
    assert automatic.father_name == "Ramesh Example" and automatic.current_address and automatic.name == ""


# ------------------------------------------------------------------ privacy of the number

def test_only_the_last_four_digits_are_ever_kept_beside_the_candidate():
    c = person()
    fx.apply_document(c, "aadhaar", CARD)
    assert c.field_sources["_aadhaar"] == {"last4": "7777"}
    assert fx.aadhaar_of(c) == "XXXX XXXX 7777"
    assert FULL not in json.dumps(c.field_sources, default=str)
    assert FULL not in json.dumps(fx.public_sources(c.field_sources))
    assert "_aadhaar" not in fx.public_sources(c.field_sources)


@pytest.mark.parametrize("identity, last4", [
    ({"aadhaar_number": "9999 8888 7777"}, "7777"), ({"aadhaar_number": "1234", "masked_aadhaar_number": "XXXX XXXX 4321"}, "4321"),
    ({"aadhaar_number": None, "masked_aadhaar_number": None}, None), ({"aadhaar_number": "12345"}, None), ({}, None),
])
def test_last_four_from_the_number_or_its_masked_form(identity, last4):
    assert fx.aadhaar_last4({"aadhaar": identity}) == last4
    assert fx.masked_aadhaar(last4) == (f"XXXX XXXX {last4}" if last4 else None)
    assert fx.masked_aadhaar("12") is None and fx.masked_aadhaar("abcd") is None


# ------------------------------------------------------------------ the application form API (DB-backed)

def _store_card(desk, candidate="candidate"):
    with desk.sessions() as db:
        c = db.get(Candidate, candidate)
        c.name = fx.UNNAMED  # a name typed by hand with no mark is never replaced; the placeholder is
        c.ocr_data = {"aadhaar": CARD}
        db.commit()


def test_form_payload_shows_the_masked_number_and_the_mapped_details_but_never_the_full_number(desk):
    assert form(desk)["aadhaar"] is None
    _store_card(desk)
    data = form(desk)
    assert data["aadhaar"] == {"maskedNumber": "XXXX XXXX 7777"}
    assert data["personal"]["fatherName"] == "Ramesh Example" and data["personal"]["currentAddress"].startswith("1 Example Road")
    assert data["fieldSources"]["personal.fatherName"] == "extracted"
    text = json.dumps(data)
    assert FULL not in text and "1111222233334444" not in text and "0000/00000/00000" not in text


def test_refreshing_from_stored_documents_fills_identity_from_the_card_and_audits_without_the_number(desk):
    _store_card(desk)
    response = desk.client.post(url() + "/refresh")
    assert response.status_code in (200, 202), response.text
    with desk.sessions() as db:
        c = db.get(Candidate, "candidate")
        assert c.name == "Alex Example" and c.dob == date(1990, 1, 1) and c.gender == "male" and c.father_name == "Ramesh Example"
        entries = db.scalars(select(AuditEntry)).all()
        blob = json.dumps([[e.action, e.target, e.meta] for e in entries], default=str)
        assert "Filled application form from stored documents" in blob
        assert FULL not in blob and "7777" not in blob


def test_reading_an_aadhaar_file_does_not_log_the_number(desk, caplog):
    _store_card(desk)
    with caplog.at_level(logging.DEBUG):
        desk.client.post(url() + "/refresh")
        form(desk)
    assert FULL not in caplog.text and "1111222233334444" not in caplog.text


def test_a_manual_edit_survives_a_later_aadhaar_read_and_the_masked_number_stays(desk):
    saved = desk.client.put(url(), json={"personal": {"fatherName": "Typed Father"}})
    assert saved.status_code == 200, saved.text
    _store_card(desk)
    desk.client.post(url() + "/refresh")
    data = form(desk)
    assert data["personal"]["fatherName"] == "Typed Father" and data["fieldSources"]["personal.fatherName"] == "manual"
    assert data["personal"]["candidateName"] == "Alex Example"
    assert data["aadhaar"] == {"maskedNumber": "XXXX XXXX 7777"}
