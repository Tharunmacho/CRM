"""Content routing contracts; no provider, database or file names are involved."""
import pytest

from app.integrations.ocr.classification import classify_page, classify_pages


CV = """CURRICULUM VITAE
Alex Example
alex@example.test
Work Experience
Mechanical technician 2018 - 2025
Installed and maintained industrial equipment.
Education
Diploma in Mechanical Engineering
Technical Skills
Welding and equipment inspection
"""
CONTINUATION = """JOB RESPONSIBILITIES
Mechanical technician 2018 - 2025
Maintained hydraulic equipment and repaired industrial machinery.
Supervised installation and coordinated safety inspections across the site.
"""
CERTIFICATE = """CERTIFICATE OF COMPLETION
This is to certify that Alex Example has successfully completed
the industrial safety training programme with the Example Training Centre.
Certificate number TRAIN-001. Authorised signatory.
"""
PASSPORT = """PASSPORT
REPUBLIC OF EXAMPLE
Surname: EXAMPLE
Given names: ALEX
Passport number: P1234567
Date of birth: 01 January 1990
Date of expiry: 01 January 2030
Nationality: EXAMPLE
"""
AADHAAR = """AADHAAR
GOVERNMENT OF INDIA
Alex Example
Date of birth: 01 January 1990
Male
0000 0000 0000
Mera Aadhaar, Meri Pehchan
Synthetic card for testing only
"""
AADHAAR_BACK = """UNIQUE IDENTIFICATION AUTHORITY OF INDIA
AADHAAR
Address:
C/O Example Parent, Example Street, Example City
PIN code: 000000
XXXX XXXX 0000
Synthetic card for testing only
"""


@pytest.mark.parametrize("title", ["CURRICULUM VITAE", "RESUME", "BIO-DATA", "CV"])
def test_common_resume_titles_are_recognized(title):
    assert classify_page(CV.replace("CURRICULUM VITAE", title)).document_type == "resume"


def test_resume_without_title_uses_independent_career_and_contact_signals():
    assert classify_page(CV.replace("CURRICULUM VITAE\n", "")).document_type == "resume"


def test_continuation_needs_adjacent_resume_and_cannot_cross_supporting_pages():
    result = classify_pages([CONTINUATION, CV, CONTINUATION, CERTIFICATE, CONTINUATION])
    assert [p.document_type for p in result] == ["resume", "resume", "resume", "other", "unknown"]


def test_certificate_with_contact_job_and_dates_is_not_resume():
    text = CERTIFICATE + "\nalex@example.test\nMechanical technician 2018 - 2025"
    assert classify_page(text).document_type == "other"


def test_mixed_resume_certificate_on_same_page_requires_review():
    assert classify_page(CV + CERTIFICATE).document_type == "unknown"
    assert classify_pages([CV, CV + CERTIFICATE])[1].document_type == "unknown"


@pytest.mark.parametrize("text", [
    "Tax invoice\nInvoice number 100\nalex@example.test\nAmount due 1000",
    "Identity card\nGovernment of Example\nAlex Example 1234567890",
    "To whom it may concern\nAlex was employed as a welder from 2018 - 2025.",
])
def test_supporting_documents_are_excluded(text):
    assert classify_page(text).document_type == "other"


@pytest.mark.parametrize("text", [PASSPORT, "P<UTOEXAMPLE<<ALEX<<<<<<<<<<<<<<<<<<<\n1234567890UTO9001011M3001012"])
def test_passport_structure_or_mrz_is_recognized(text):
    assert classify_page(text).document_type == "passport"


def test_resume_listing_passport_details_is_still_a_resume():
    assert classify_page(CV + "\nPassport details\nPassport No: A123456\nNationality: Example").document_type == "resume"


@pytest.mark.parametrize("text", ["", "???", "Some readable but inconclusive correspondence about a meeting next week."])
def test_missing_or_inconclusive_text_never_assumes_blank_or_resume(text):
    assert classify_page(text).document_type == "unknown"


def test_nationality_does_not_change_routing():
    for nationality in ("Indian", "Nepalese", "Filipino", "Kenyan"):
        assert classify_page(CV + f"\nNationality: {nationality}").document_type == "resume"


@pytest.mark.parametrize("text", [
    AADHAAR, AADHAAR_BACK,
    AADHAAR.replace("0000 0000 0000", "000000000000"),
    AADHAAR.replace("0000 0000 0000", "XXXX XXXX 0000"),
    AADHAAR.replace("AADHAAR", "AADHAR").replace("Aadhaar", "Aadhar"),
])
def test_aadhaar_front_back_masking_and_common_spelling_use_card_evidence(text):
    assert classify_page(text).document_type == "aadhaar"


def test_resume_listing_aadhaar_number_remains_a_resume():
    assert classify_page(CV + "\nAadhaar number: 0000 0000 0000").document_type == "resume"


@pytest.mark.parametrize("text", [
    "Aadhaar number: 0000 0000 0000\nDate of birth: 01 January 1990\nMale",
    "Information about Aadhaar services and application appointments for residents.",
    "0000 0000 0000\nDate of birth: 01 January 1990\nMale\nExample person",
])
def test_aadhaar_mention_or_twelve_digit_number_alone_is_insufficient(text):
    assert classify_page(text).document_type == "unknown"


@pytest.mark.parametrize("other", [CV, PASSPORT])
def test_aadhaar_mixed_with_another_document_requires_review(other):
    assert classify_page(other + AADHAAR).document_type == "unknown"
    assert classify_pages([CV, other + AADHAAR])[1].document_type == "unknown"


def test_aadhaar_pages_are_excluded_from_resume_and_stop_continuation():
    result = classify_pages([CV, CONTINUATION, AADHAAR, AADHAAR_BACK, CONTINUATION])
    assert [page.document_type for page in result] == ["resume", "resume", "aadhaar", "aadhaar", "unknown"]
