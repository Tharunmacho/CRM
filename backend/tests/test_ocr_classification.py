"""Content routing contracts; no provider, database or file names are involved."""
import pytest

from ocr_hard_cases import valid_aadhaar
from app.integrations.ocr.classification import classify_page, classify_pages
from app.integrations.ocr.validators import identity_evidence


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


def test_aadhaar_mixed_with_a_resume_goes_to_both_kinds():
    # Nothing is held back for a person to sort out: the sheet is read as a resume and as an Aadhaar card.
    page = classify_page(CV + AADHAAR)
    assert page.document_type == "resume" and page.also == ("aadhaar",)
    assert classify_pages([CV, CV + AADHAAR])[1].also == ("aadhaar",)


def test_aadhaar_mixed_with_a_passport_belongs_to_both():
    assert classify_page(PASSPORT + AADHAAR).also == ("aadhaar",)


def test_aadhaar_pages_are_excluded_from_resume_and_stop_continuation():
    result = classify_pages([CV, CONTINUATION, AADHAAR, AADHAAR_BACK, CONTINUATION])
    assert [page.document_type for page in result] == ["resume", "resume", "aadhaar", "aadhaar", "unknown"]


# ---- detection robustness, mixed pages and back sides ---------------------------------

GARBLED_MRZ = (
    "PKINDKUMARKKRAHULKKKKKKKKKKKKKKKKKKKKKK\n"
    "Z1234567<4IND9001015M3001012<<<<<<<<<<<<<<04"
)
INDIA_PASSPORT_DATA = """REPUBLIC OF INDIA
PASSPORT
Type P Country Code IND Passport No. Z1234567
Surname KUMAR
Given Name(s) RAHUL
Nationality INDIAN
Date of Birth 01/01/1990
Place of Issue COCHIN
"""
PASSPORT_ADDRESS = """Name of Father / Legal Guardian  SURESH KUMAR
Name of Mother  LATHA KUMAR
Address 12 EXAMPLE ROAD KOCHI PIN 682001
Old Passport No. NA
File No. COH/1234/2020
"""
VISA = """VISA
Type of visa: Employment
Valid until 01/01/2030
Number of entries: multiple
Issued at DOHA
"""
AADHAAR_VID_BACK = """Address: C/O Suresh Kumar, 12 Example Road, Kochi, Kerala - 682001
VID : 1234 5678 9012 3456
Download Date: 01/01/2026
"""


@pytest.mark.parametrize("text", [GARBLED_MRZ, INDIA_PASSPORT_DATA, PASSPORT_ADDRESS])
def test_passport_detection_tolerates_garbled_chevrons_republic_of_india_and_address_pages(text):
    assert classify_page(text).document_type == "passport"


@pytest.mark.parametrize("passport", [PASSPORT, INDIA_PASSPORT_DATA, PASSPORT_ADDRESS, GARBLED_MRZ])
@pytest.mark.parametrize("grouped", [False, True])
def test_passport_incidental_checksum_valid_number_is_not_an_aadhaar_copy(passport, grouped):
    number = valid_aadhaar(73)  # Synthetic checksum-valid noise, unrelated to any actual identity.
    printed = " ".join(number[index:index + 4] for index in range(0, 12, 4)) if grouped else number
    text = passport + "\nPrinted reference: " + printed
    assert identity_evidence(text)["aadhaar"] is True
    page = classify_page(text)
    assert page.document_type == "passport" and page.also == ()


@pytest.mark.parametrize("passport", ["", PASSPORT])
def test_checksum_valid_aadhaar_structure_survives_on_its_own_and_on_a_mixed_passport_page(passport):
    number = valid_aadhaar(73)
    card = AADHAAR.replace("0000 0000 0000", " ".join(number[index:index + 4] for index in range(0, 12, 4)))
    assert identity_evidence(card)["aadhaar"] is True
    page = classify_page(passport + card)
    assert page.document_type == ("passport" if passport else "aadhaar")
    assert page.also == (("aadhaar",) if passport else ())


def test_checksum_number_fallback_remains_available_without_another_document_structure():
    text = "Alex Example\nDate of birth: 01 January 1990\nMale\n" + valid_aadhaar(73)
    page = classify_page(text)
    assert page.document_type == "aadhaar" and page.also == ()


def test_visa_page_is_supporting_not_unknown_but_a_resume_listing_visa_stays_resume():
    assert classify_page(VISA).document_type == "other"
    assert classify_page(CV + "\nVisa status: Employment visa\nValid until 01/01/2030\nType of visa: work").document_type == "resume"


def test_aadhaar_virtual_id_page_is_recognized_without_issuer_text():
    assert classify_page(AADHAAR_VID_BACK).document_type == "aadhaar"


def test_resume_personal_details_do_not_look_like_a_passport_address_page():
    text = CV + "\nName of Father: Suresh\nName of Mother: Latha\nAddress: Kochi PIN 682001"
    assert classify_page(text).document_type == "resume"


def test_page_with_passport_and_aadhaar_goes_to_both_kinds():
    page = classify_page(PASSPORT + AADHAAR)
    assert page.document_type == "passport" and page.also == ("aadhaar",)


def test_unknown_back_side_inherits_the_adjoining_identity_document():
    back = "Some printed text with address details and a barcode. " * 3
    result = classify_pages([CV, AADHAAR, back])
    assert result[2].document_type == "aadhaar" and result[2].inherited


def test_unknown_page_between_different_documents_or_beside_a_resume_stays_unknown():
    back = "Some printed text with address details and a barcode. " * 3
    assert classify_pages([PASSPORT, back, AADHAAR])[1].document_type == "unknown"
    assert classify_pages([CV, back])[1].document_type == "unknown"


@pytest.mark.parametrize("role", ["Interior Designer", "Clinical Research Coordinator", "Architect"])
def test_compact_labelled_employment_page_continues_resume_without_occupation_whitelist(role):
    continuation = f"""Example Interiors Pvt Ltd
Job title
: {role}
Duration
: November 2024 - February 2026
Location
: Example City, India
Software Skills
1. Design software
2. Spreadsheet software
"""
    assert classify_page(continuation).document_type == "unknown"
    assert [page.document_type for page in classify_pages([CV, continuation])] == ["resume", "resume"]
    assert [page.document_type for page in classify_pages([CV, CERTIFICATE, continuation])] == ["resume", "other", "unknown"]


def test_employment_certificate_with_job_labels_is_not_a_resume_continuation():
    certificate = """EXPERIENCE CERTIFICATE
This is to certify that Alex Example worked here.
Job title: Interior Designer
Duration: November 2024 - February 2026
Location: Example City, India
"""
    assert [page.document_type for page in classify_pages([CV, certificate])] == ["resume", "other"]


# --- education_certificate(): the weak path needs two independent signals and never fires on the ordinary word 'be' ---
from app.integrations.ocr.classification import education_certificate

UNDERTAKING_NAMING_COLLEGE = """UNDERTAKING LETTER
I, Alex Example, hereby state that I studied at Sri Venkateswara College and that I will be responsible for my own
travel and accommodation. I will be present for the interview when asked and I will be bound by the rules of the company.
Signature of the candidate
"""
FEE_RECEIPT = """AL NOOR COLLEGE
RECEIPT
Received with thanks from Alex Example the sum of Rs. 5000 towards the term amount. This will be adjusted in the next
instalment and the balance will be shown on the next receipt. Cashier signature.
"""
OFFER_LETTER_NAMING_DEGREE = """OFFER LETTER
Dear Alex Example, we are pleased to offer you the position of Mechanical Technician. A Bachelor of Engineering from a
recognised University is required and the joining date will be confirmed by the company in writing.
"""
EXPERIENCE_LETTER_WITH_DEGREE = """TO WHOMSOEVER IT MAY CONCERN
This is to certify that Alex Example has worked with us as a technician from 2018 to 2024. He holds a B.E. Mechanical from
Example University. His conduct was good.
"""
RESUME_EDUCATION_SECTION = """CURRICULUM VITAE
Alex Example, alex@example.test
Career Objective
To work as a mechanical engineer where my skills will be used well.
Education
B.E Mechanical Engineering, Example University, 2018, First Class
Work Experience
Technician 2018 - 2024
"""
REAL_DEGREE_WEAK = """EXAMPLE UNIVERSITY
Alex Example, Register Number 1234567, passed the B.E. Mechanical Engineering examination held in April 2018
and secured First Class. The university awarded the degree.
"""
REAL_BTECH_WEAK = """EXAMPLE COLLEGE OF ENGINEERING
Roll No 99887766. Alex Example has passed the B.Tech examination in Civil Engineering with Distinction.
"""
REAL_BE_BRANCH_WEAK = """EXAMPLE UNIVERSITY
Alex Example, Reg No 123456, was awarded BE Mechanical in the examination of April 2018 with Distinction.
"""
UNIVERSITY_ONLY_PROSE = """EXAMPLE UNIVERSITY
The university offers a Bachelor of Science programme and the campus has a library and a hostel for all the students
who live far from home.
"""


@pytest.mark.parametrize("text", [
    UNDERTAKING_NAMING_COLLEGE, FEE_RECEIPT, OFFER_LETTER_NAMING_DEGREE, EXPERIENCE_LETTER_WITH_DEGREE, RESUME_EDUCATION_SECTION,
    UNIVERSITY_ONLY_PROSE,
])
def test_education_certificate_is_not_claimed_by_the_word_be_or_by_a_college_name_alone(text):
    assert education_certificate(text) is False
    assert "Education certificate" not in classify_page(text).reason


@pytest.mark.parametrize("text", [REAL_DEGREE_WEAK, REAL_BTECH_WEAK, REAL_BE_BRANCH_WEAK])
def test_education_certificate_still_fires_on_a_degree_with_a_university_and_an_award_marker(text):
    assert education_certificate(text) is True
    assert classify_page(text).document_type == "resume" and "Education certificate" in classify_page(text).reason


NOISY_PASSPORT = """TypeP Country Code IND = Pasaport No. 2932527
Surname SAMPLEKAM
Govern Name(s) DINESH TEST
Nationality INDUAN
SexF Oate of Bet 1606/1906
Prace of Bert) EXAMPLEPUR
Prace of issue BENGALURU
Date of issue 16022019 Oate of Expry 1502/2029
Pc INDSAMPL EKAM DINES H TEST 444444444000
2932527 <GINDSEOE165F 290215 3< 44444406466"""
DAMAGED_AADHAAR = "4354 2821 1673"  # one digit misread (valid number 4254 2821 1673): the checksum passes only after a repair


def test_the_damaged_number_used_here_is_a_repaired_match_not_an_exact_one():
    from app.integrations.ocr.validators import find_aadhaar_numbers
    found = find_aadhaar_numbers(DAMAGED_AADHAAR)
    assert len(found) == 1 and found[0].valid and found[0].repaired
    assert identity_evidence(NOISY_PASSPORT)["aadhaar"] is True and not identity_evidence(NOISY_PASSPORT)["passport"]


def test_a_noisy_passport_page_with_a_repairable_number_is_never_an_aadhaar_card():
    page = classify_page(NOISY_PASSPORT)
    assert page.document_type != "aadhaar" and "aadhaar" not in page.also


@pytest.mark.parametrize("words", ["Passport No.", "Pasaport No.", "Nationality INDIAN", "Surname KUMAR", "Given Name(s) RAHUL",
                                   "Republic of India", "P<INDKUMAR<<RAHUL<<<<<<<<<<", "INDSAMPLEKAM"])
@pytest.mark.parametrize("number", [DAMAGED_AADHAAR, " ".join(valid_aadhaar(73)[i:i + 4] for i in range(0, 12, 4))])
def test_passport_words_beside_a_checksum_number_do_not_make_an_aadhaar_card(words, number):
    text = f"Name RAHUL KUMAR\n{words}\nRef {number}\nSome other printed text on the page of the book"
    assert classify_page(text).document_type != "aadhaar"


def test_a_repaired_number_alone_is_not_an_aadhaar_card_but_with_a_card_caption_it_is():
    assert classify_page("Alex Example\nReference " + DAMAGED_AADHAAR + "\nPrinted by the office").document_type != "aadhaar"
    assert classify_page("Alex Example\nDate of birth: 01 January 1990\nMale\n" + DAMAGED_AADHAAR).document_type == "aadhaar"


def test_an_exact_number_with_card_captions_still_makes_an_aadhaar_card():
    text = "Alex Example\nDate of birth: 01 January 1990\nMale\n" + valid_aadhaar(73)
    assert classify_page(text).document_type == "aadhaar"


def test_a_lowercase_be_never_counts_as_the_b_e_degree():
    assert education_certificate("EXAMPLE COLLEGE\nThe students will be given their results later. It will be posted on the notice board of the college "
                                 "and the class will be told. Marks will be shown.") is False
