"""The content classifier for the non-identity candidate documents (synthetic pages only: invented people, no real data)."""
import pytest

from app.integrations.ocr.kind_classifier import KINDS, classify_kind, classify_kinds
from ocr_synthetic import aadhaar_lines, page_lines, passport_lines

OFFER = """ACME TRADING COMPANY
Date: 12 March 2026
Dear Mr. Tester Sample
Passport No. Z7000137
Subject: Employment Offer
After reviewing your credentials it is our pleasure to extend to you this offer of employment as a Picker.
Working Hours: 9+1 Hours, paid overtime if required
Working Days: 6 working days, 1 day off
Basic Monthly Salary: KD 130 (One hundred and thirty Kuwaiti Dinars)
Accommodation: Provided by the company
Transportation: Provided by the company
Annual Leave: 30 days paid annual leave as per labour law
Contract: 3 Years.
Air ticket one way from India. The offer is subject to a medical examination, medical insurance and a valid visa.
If you choose to accept this offer, please sign this letter and return it to us, indicating the anticipated commencement date.
Best regards, Human Resource Department
"""
EVISA = """STATE OF KUWAIT eVISA
Visa Type: Private Sector Work Visa      Visa Number 123456789
Expiry Date 2027-01-05     Issue Date 2026-10-06
Nationality INDIA     Passport No. Z7000137
Name TESTER SAMPLE     Occupation WORKER
Ministry of Interior | Electronic Visa
Entry Stamp      Exit Stamp
VBKWTSAMPLE<<TESTER<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
1234567891IND9001015M3001012<<<<<<<<<<<<<<04
"""
MEDICAL = """Detailed candidate report            Wafid GAMCA
Medical center name: Shifa Diagnostic Center     GCC Slip No. 109102026801984
Candidate information: Name TESTER SAMPLE, Male, Indian, travelling to Kuwait
MEDICAL EXAMINATION: GENERAL INVESTIGATION
Blood pressure 120/80   Pulse 76   Chest X-Ray NAD
LABORATORY INVESTIGATION   Haemoglobin 15.8
HIV Negative   HBs Ag Negative   VDRL Negative   Malaria Absent
Mentioned above is the medical report for Mr. TESTER SAMPLE who is Fit for the above mentioned job according to the GCC criteria.
Doctor's name and signature
"""
PCC = """GOVERNMENT OF INDIA
MINISTRY OF EXTERNAL AFFAIRS
REGIONAL PASSPORT OFFICE, EXAMPLEPUR
File Number : EX3085061235026     PCC Issuance Date : 14/08/2026
POLICE CLEARANCE CERTIFICATE
It is certified that there is no adverse information against Mr. TESTER SAMPLE, holder of Indian Passport No Z7000137,
which would render him ineligible for EMPLOYMENT/EMPLOYMENT VISA/WORK PERMIT for STATE OF KUWAIT.
This PCC is not valid for any other purpose or country.
Regional Passport Officer      Stamp of the Office of Issue      Signature of Passport Authority
"""
TICKET = """Booking Confirmed
Your Booking is confirmed with us. Booking Reference: AB12CD
Journey Details   Flight No. J9432
Departure: Bangalore (BLR) Terminal 1     Arrival: Kuwait International Airport Terminal 5
Passenger Details: Mr. TESTER SAMPLE, Adult
Baggage Details: Cabin 7kg  Checked-in 20kg
Jazeera Airways. This booking is non-refundable. Check-in counters open 3 hours before departure.
"""
INSURANCE = """CERTIFICATE OF INSURANCE
Pravasi Bharatiya Bima Yojana (PBBY)
Policy Number: 1234567890     Policy Period: 01/11/2026 to 31/10/2028
Insured: TESTER SAMPLE     Nominee: PARENT SAMPLE
Sum Insured: INR 10,00,000     Premium paid: INR 275
The insurance company will pay in case of accidental death or permanent disability. Claims are settled within 30 days.
"""
INVOICE = """TAX INVOICE
Invoice No: INV-2026-0042      Invoice Date: 20/10/2026
Bill To: TESTER SAMPLE
GSTIN: 29ABCDE1234F1Z5
Description of services     Quantity     Unit Price     Amount
Recruitment service charge  1            25000          25000
Sub Total 25000     Grand Total 25000
Bank details: IFSC EXMP0001234
"""
RECEIPT = """ADVANCE RECEIPT
Receipt No. 0153        Date 02/09/2026
Received with thanks from Mr. TESTER SAMPLE a sum of Rs. 10,000 (Rupees ten thousand only)
towards advance service charge.
Mode of payment: UPI    Transaction ID 6012345678
"""
UNDERTAKING = """UNDERTAKING LETTER
I, TESTER SAMPLE, hereby undertake that I will abide by the terms and conditions of the employment, and I
agree to bear all expenses if I leave the job without notice. This is of my own free will.
Candidate Signature:            Candidate Thumb Impression:
Witness:
"""
SELF_DECLARATION = """Self-Declaration
From: TESTER SAMPLE, Contact no 9000000000
To: HR MANAGER, ACME TRADING COMPANY, KUWAIT
I hereby declare that I understand the terms of employment: Place of Employment Kuwait, Post applied for Picker,
Basic Salary 130 KD, Working Hours 9+1 Hours, Period of Contract 3 Years, Accommodation Provided.
I also agree with my willful consent to obey all the above terms & conditions of the employment contract.
Candidate Signature        Candidate Thumb Impression
"""
EMIGRATION = """OFFICE OF THE PROTECTOR OF EMIGRANTS
EMIGRATION CLEARANCE CERTIFICATE
Under the Emigration Act, 1983. ECR passport holder, registered recruiting agent RA-1234.
Clearance is granted to TESTER SAMPLE for employment in Kuwait. Pre-departure orientation completed.
"""
SSLC = """GOVERNMENT OF KARNATAKA
Karnataka Secondary Education Examination Board
S.S.L.C. Certificate
This is to certify that the candidate has PASSED S.S.L.C. Examination.
Register No.: 20140246704     Month & Year: APRIL-2014
Name: TESTER SAMPLE
SUBJECTS       MAX    MIN    OBTAINED
KANNADA        125    40     88
ENGLISH        100    35     65
TOTAL MARKS    625    219    440      FIRST CLASS
SCHOOL & ADDRESS: EXAMPLE HIGH SCHOOL, EXAMPLEPUR
"""
DEGREE = """EXAMPLE UNIVERSITY
This is to certify that TESTER SAMPLE has been admitted to the degree of Bachelor of Technology in Mechanical Engineering
in the First Class with Distinction. Provisional Certificate. Date of issue 12/06/2018. Convocation 2018.
"""
EXPERIENCE = """TO WHOM IT MAY CONCERN
This is to certify that Mr. Tester Sample, holding Passport No Z7000137, was employed with our company in the capacity of
Warehouse Associate. He worked with us from 18-OCT-2023 to 06-DEC-2025, during which time he demonstrated dedication.
His key responsibilities included managing warehouse operations. We wish him all the best in his future endeavours.
This letter has been issued at his request, without any liability on the part of the company.
General Manager
"""
GOOGLE_REVIEW = """Google Maps
Careindia Overseas Pvt Ltd
Tester Sample  Local Guide
★★★★★  a week ago
Great service, they arranged my job in Kuwait. Reviewed on Google. 5 stars
"""
APPLICATION_FORM = """JOB APPLICATION FORM
Post Applied For:                     Interview Date:
Applicant's Name:
Educational Qualifications:           Languages Known:
Salary Expected:                      Name of Employers:
Signature of Applicant
"""
CV = """CURRICULUM VITAE
Tester Sample
tester@example.test   +91 90000 00000
Career Objective
Seeking a position as a warehouse supervisor.
Work Experience
Warehouse Associate, Example Logistics, 2019 - 2025
Handled inbound stock, health insurance claims data entry and the insurance policy register.
Education
Bachelor of Technology, Example University, 2018
Personal Details
Passport details: Z7000137, visa status: employment visa
"""
PASSPORT_PAGE = "\n".join(passport_lines(1) + ["Visa: entry permit, visa number, work visa, valid until 2027, number of entries single"])

POSITIVE = {
    "signed_offer": OFFER, "visa": EVISA, "medical": MEDICAL, "pcc": PCC, "air_ticket": TICKET, "insurance": INSURANCE,
    "invoice": INVOICE, "advance_receipt": RECEIPT, "undertaking_letter": UNDERTAKING, "emigration": EMIGRATION,
    "education_certificate": SSLC, "experience_certificate": EXPERIENCE, "google_review": GOOGLE_REVIEW,
}


@pytest.mark.parametrize("kind,text", sorted(POSITIVE.items()))
def test_each_kind_is_recognised_by_its_wording(kind, text):
    guess = classify_kind(text)
    assert guess.kind == kind, guess.reason
    assert guess.confidence >= 0.7 and kind in KINDS and not guess.inherited and not guess.alongside_identity


def test_the_signed_self_declaration_is_the_undertaking_not_the_offer_it_quotes():
    assert classify_kind(SELF_DECLARATION).kind == "undertaking_letter"


def test_a_degree_certificate_is_education_and_an_experience_letter_is_not():
    assert classify_kind(DEGREE).kind == "education_certificate"
    letter = EXPERIENCE.replace("General Manager", "General Manager. Mr. Sample holds a B.Tech from Example University and a Diploma in Welding.")
    assert classify_kind(letter).kind == "experience_certificate"


# ---- precision: a stray word never decides ---------------------------------------------------------------------------

def test_an_offer_letter_that_mentions_medical_visa_ticket_and_insurance_stays_the_offer():
    assert classify_kind(OFFER).kind == "signed_offer"


def test_a_passport_page_that_mentions_a_visa_stays_a_passport_page():
    guess = classify_kind(PASSPORT_PAGE)
    assert guess.kind is None and "Passport" in guess.reason


def test_a_resume_that_mentions_insurance_visa_and_a_degree_stays_a_resume():
    guess = classify_kind(CV)
    assert guess.kind is None and guess.reason == "Resume page." and not guess.alongside_identity


def test_an_aadhaar_card_is_never_another_kind():
    assert classify_kind("\n".join(aadhaar_lines(1) + ["Address: policy number insurance premium sum insured invoice"])).kind is None


def test_a_police_certificate_that_names_an_employment_visa_is_the_pcc_not_a_visa():
    guess = classify_kind(PCC)
    assert guess.kind == "pcc"


@pytest.mark.parametrize("text", [
    "", "   ", "Invoice", "Please send the invoice by Friday so that accounts can pay it this month, thank you very much indeed.",
    "Minutes of the neighbourhood garden committee. Agenda: bench repairs, watering rota, summer picnic. Next meeting is on Monday.",
    "The visa was approved last week and he will travel soon, we hope that everything goes well for the whole family at home.",
    "Our flight was late but the hotel was good and the staff were kind, the food was excellent and the room was clean and bright.",
    "\n".join(page_lines(0)),  # a resume page of the suite's own
])
def test_a_page_with_one_stray_word_or_none_is_left_unclassified(text):
    assert classify_kind(text).kind is None


def test_two_documents_on_one_sheet_are_not_guessed():
    guess = classify_kind(MEDICAL + "\n" + PCC)
    assert guess.kind is None and "Ambiguous" in guess.reason


# ---- OCR damage --------------------------------------------------------------------------------------------------------

def test_ocr_noise_in_the_wording_is_tolerated():
    noisy = (PCC.replace("POLICE CLEARANCE CERTIFICATE", "P0L1CE CLEARANCE CERT1F1CATE")
             .replace("no adverse information", "n0 adverse inf0rmation"))
    assert classify_kind(noisy).kind == "pcc"
    glued = EVISA.replace("Visa Type:", "VisaType").replace("Visa Number", "VisaNumber").replace("eVISA", "e-VISA")
    assert classify_kind(glued).kind == "visa"
    spaced = PCC.replace("POLICE CLEARANCE CERTIFICATE", "P O L I C E   C L E A R A N C E   C E R T I F I C A T E")
    assert classify_kind(spaced).kind == "pcc"


def test_a_page_that_does_not_read_like_language_must_clear_a_higher_bar():
    junk = " ".join(["x", "oo", "ij", "a", "ee", "ty", "ml", "q", "zz", "ww"] * 30)
    assert classify_kind(junk + " police clearance certificate pcc").kind is None
    assert classify_kind(junk + "\n" + PCC).kind == "pcc"   # plenty of evidence still wins


# ---- the identity guard -------------------------------------------------------------------------------------------------

def test_a_visa_whose_machine_readable_zone_made_it_a_passport_is_a_visa_that_overrides_that_guess():
    guess = classify_kind(EVISA)
    assert guess.kind == "visa" and guess.overrides_identity


def test_a_real_passport_zone_is_never_overridden():
    page = "\n".join(passport_lines(1)) + "\nVisa Number 123 e-visa entry permit work visa Ministry of Interior state of kuwait visa type"
    assert classify_kind(page).kind is None


def test_a_flight_itinerary_taken_for_an_aadhaar_card_by_a_bare_number_is_an_air_ticket():
    guess = classify_kind(TICKET + "\nContact 2345 6789 0124")
    assert guess.kind == "air_ticket"


def test_an_education_certificate_or_form_on_a_resume_page_is_also_filed_on_its_own_and_stays_a_resume():
    form = classify_kind(APPLICATION_FORM)
    assert form.kind == "application_form" and form.alongside_identity
    cert = classify_kind(SSLC + "\nSKILLS")
    assert cert.kind == "education_certificate"


# ---- continuation across pages ---------------------------------------------------------------------------------------

def test_a_continuation_page_with_the_same_page_marker_takes_the_kind_of_the_page_before_and_is_flagged():
    second = "Page 2 of 3\nTHICK FILM  Negative   Result  normal   remarks  none  Thank you for choosing our centre   signature stamp"
    first = "Page 1 of 3\n" + MEDICAL
    guesses = classify_kinds([first, second, "Page 3 of 3\nnothing else of note on this page, only blank boxes and lines for notes"])
    assert guesses[0].kind == "medical" and not guesses[0].inherited
    assert guesses[1].kind == "medical" and guesses[1].inherited and guesses[1].confidence <= 0.6 and "Page 2 of 3" in guesses[1].reason
    assert guesses[2].kind == "medical" and guesses[2].inherited      # chains through the same document


def test_a_page_without_the_marker_or_with_another_total_never_inherits():
    plain = "Result normal remarks none thank you for choosing our centre signature and stamp of the examining doctor."
    assert classify_kinds(["Page 1 of 3\n" + MEDICAL, plain])[1].kind is None
    assert classify_kinds(["Page 1 of 3\n" + MEDICAL, "Page 2 of 5\n" + plain])[1].kind is None
    assert classify_kinds(["Page 1 of 3\n" + MEDICAL, "Page 3 of 3\n" + plain])[1].kind is None     # not the next number
    assert classify_kinds([MEDICAL, "Page 2 of 3\n" + plain])[1].kind is None                         # the first carries no marker


def test_a_page_with_a_kind_of_its_own_keeps_it_whatever_the_marker_says():
    guesses = classify_kinds(["Page 1 of 2\n" + MEDICAL, "Page 2 of 2\n" + PCC])
    assert [g.kind for g in guesses] == ["medical", "pcc"] and not guesses[1].inherited


# ---- police clearance wording under OCR noise ------------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "POLICE CLEARANC E CERTIFICATE", "POLICECLEARANCECERTIFICATE", "PO LICE CLEARANCE CERTIFICATE", "P0LICE CLEARANCE CERTIF1CATE",
    "Police Clearance Cert1ficate", "POLICE  CLEARANCE  CERTIFICATE |",
])
def test_a_police_clearance_title_is_found_whatever_ocr_did_to_its_words(title):
    page = PCC.replace("POLICE CLEARANCE CERTIFICATE", title)
    assert classify_kind(page).kind == "pcc"


def test_a_police_clearance_whose_title_was_not_read_is_still_found_by_the_rest_of_its_wording():
    page = PCC.replace("POLICE CLEARANCE CERTIFICATE", "").replace("PCC Issuance Date", "PC0 Issuance Date")
    assert classify_kind(page).kind == "pcc"


def test_the_passport_seva_wording_of_an_indian_pcc_is_enough():
    page = ("Passport Seva\nRegional Passport Office, Chennai\nPolice Verification Certificate\nFile Number MA3085061235026\n"
            "This is to certify that there is no criminal record against the applicant. Issued for the purpose of employment abroad.")
    assert classify_kind(page).kind == "pcc"


def test_a_medical_report_that_mentions_the_police_is_still_the_medical_report():
    page = MEDICAL + "\nThe candidate has no police case pending and has submitted the police verification separately."
    assert classify_kind(page).kind == "medical"


def test_an_offer_letter_that_mentions_police_verification_is_still_the_offer():
    page = OFFER + "\nThis offer is subject to police verification and a clean criminal record, issued for the purpose of employment."
    assert classify_kind(page).kind == "signed_offer"


def test_a_page_with_only_the_police_word_or_only_the_pcc_word_is_left_alone():
    assert classify_kind("The police station is near the market, please ask for the road to the bus stand and the old temple near it.").kind is None
    assert classify_kind("Please keep the PCC copy with the other papers in the blue folder on the second shelf of the office cupboard.").kind is None


def test_a_page_that_is_already_a_known_kind_needs_no_further_reading():
    from app.integrations.ocr.classification import kind_known, needs_more_reading
    for text in (PCC, MEDICAL, OFFER, TICKET):
        assert kind_known(text) and not needs_more_reading(text)
    assert not kind_known("a faded unreadable stamp and a few words " * 3)
    assert needs_more_reading("a faded unreadable stamp and a few words " * 3)


def _signal_names(text, kind):
    from app.integrations.ocr import kind_classifier as module
    return module._scores(module._normalise(text))[kind][1]


@pytest.mark.parametrize("title", [
    "POLICE CLEARANC E CERTIFICATE", "P0LICE CLEAR ANCE CERT IFICATE", "P0L1CE CLEARANCE CERT1F1CATE", "POLICE CLEARANCE CERTIFICATE",
])
def test_the_fuzzy_pcc_title_matches_a_damaged_title_on_its_own(title):
    assert "pcc title" in _signal_names(title, "pcc")


def test_the_fuzzy_pcc_title_does_not_match_unrelated_wording():
    assert "pcc title" not in _signal_names("The police officer cleared the road. Certificate of completion for the course.", "pcc")


def test_a_damaged_title_plus_one_weak_signal_is_a_pcc_on_the_title_alone_as_strong_evidence():
    text = "P0LICE CLEAR ANCE CERT IFICATE\nName of the applicant: Tester Sample\nDate of birth: 01 01 1990\nFile number: 4455\n"
    assert _signal_names(text, "pcc") == ["pcc title", "issuance"]      # the damaged title is the only strong evidence
    assert classify_kind(text).kind == "pcc"


def test_a_run_of_one_repeated_letter_is_not_a_real_word():
    from app.integrations.ocr.kind_classifier import _noisy
    assert _noisy(" ".join(["cooool", "wooow", "baaad"] * 20)) is True      # tripled letters: scan noise, not words
    assert _noisy(" ".join(["cool", "wood", "bad", "toll"] * 20)) is False
