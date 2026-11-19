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


# ---- real-world wording that used to fall through to "Other documents" (invented names and companies) ----------------------------


def test_an_experience_letter_with_only_ordinary_wording_is_still_an_experience_certificate():
    text = ("Alpha Valve Services Pvt Ltd\nTO WHOM IT MAY CONCERN\nThis is to certify that Mr. Rashid Demo has worked with our "
            "company as Instrument Technician from 01/02/2019 to 30/06/2022. His conduct was good.\nHR Manager")
    assert classify_kind(text).kind == "experience_certificate"


def test_a_letterhead_with_a_gst_number_is_not_an_invoice():
    text = ("GSTIN NO. 09AAZCA1471H1Z8 BETA INDUSTRIES PRIVATE LIMITED CIN U01611UP2023\nTO WHOM IT MAY CONCERN\nThis is to certify that "
            "Mr. Dinesh Demo was employed with us as Accountant from 03/2023 to date. Experience letter issued at his request.")
    assert classify_kind(text).kind == "experience_certificate"


def test_a_resume_that_mentions_invoices_is_not_an_invoice():
    text = ("Profile: six years of experience in accounts payable and receivable, invoice processing, vendor reconciliation and "
            "month-end closing in a manufacturing company. Skills: SAP, Tally, Excel. Declaration: all the above is true.")
    assert classify_kind(text).kind != "invoice"


def test_a_university_marks_statement_is_an_education_certificate():
    text = ("GAMMA UNIVERSITY, DEMO CITY\nMARKS STATEMENT\nB.Com Part III Examination 2022\nRoll No 160582059791\n"
            "Total marks obtained 1450 of 2000 Second Class")
    assert classify_kind(text).kind == "education_certificate"


# ---- training certificates and letters from an employer or an institute are experience, not education or "other" ------------------
# (wording taken from a real combined PDF's OCR, with invented names)

INTERN_TRAINING = ("Grand Palm Hotels Group Intern Training This certificate is awarded to Mr. Rahul Demo Student of Example Institute "
                   "For successfully completing Intern Training At Grand Palm Chennai Resort From 11th October 2019 to 11th March 2020 "
                   "with 100 % Attendance Overall Performance Rating: Excellent Performance Asha Demo Assistant Human Resources Manager")
INSTITUTE_TRAINING_LETTER = ("EXAMPLE INSTITUTE OF HOTEL MANAGEMENT 21/01/2021 TOWHOMSOEVERITMAYCONCERN Dear Sir/Madam, This is to certify "
                             "that RAHUL DEMO, pursuing 1 year Craft course specializing in KITCHEN and is a Bonafide student of our "
                             "Institute (2019-2020) We do not have any objection to his undergoing BAKERY Exclusive on the job training "
                             "for the period 6 months in your prestigious hotel Placement Officer & Training Manager")
HOTEL_TRAINING_LETTER = ("Sunrise Hotel Demo City 24 June 2022 TO WHOMSOEVER IT MAY CONCERN is to certify that Mr. R. DEMO, Student of "
                         "Example Institute of Technology, has undergone his On Job Training in our hotel from October 2021 to 31st "
                         "March 2022, he has shown keen interest to learn the skills involved. We wish him success in his future.")


@pytest.mark.parametrize("text", [INTERN_TRAINING, INSTITUTE_TRAINING_LETTER, HOTEL_TRAINING_LETTER])
def test_a_training_certificate_or_letter_about_training_at_an_employer_is_an_experience_certificate(text):
    guess = classify_kind(text)
    assert guess.kind == "experience_certificate" and not guess.inherited


@pytest.mark.parametrize("text", [
    "Certificate of Internship. This is to certify that Mr. Amit Demo completed his internship with Delta Foods from 01/06/2023 to 30/11/2023.",
    "INDUSTRIAL EXPOSURE TRAINING CERTIFICATE. Mr. Amit Demo has completed Industrial Exposure Training at Delta Residency, "
    "from 06/09/2022 to 06/03/2023. Training period: 6 months. HR Manager",
    "LETTER OF RECOMMENDATION. To whom it may concern, Mr. Amit Demo worked in our kitchen as a commis chef and we recommend him. General Manager",
])
def test_internship_industrial_exposure_and_recommendation_wording_is_experience(text):
    assert classify_kind(text).kind == "experience_certificate"


def test_an_industrial_training_institute_certificate_is_education_not_experience():
    text = ("GOVERNMENT OF DEMOSTATE Directorate of Employment and Training. Industrial Training Institute, Demopur. "
            "NATIONAL TRADE CERTIFICATE. Roll No 00160809106589. Trade: Fitter. Passed the All India Trade Test examination. First Class.")
    assert classify_kind(text).kind == "education_certificate"


def test_a_degree_or_marksheet_stays_education_even_when_it_mentions_training():
    text = ("EXAMPLE UNIVERSITY. This is to certify that Mr. Amit Demo has been admitted to the degree of Bachelor of Science in Catering. "
            "Provisional Certificate. Course includes industrial training. Date of issue 12/06/2018. Convocation 2018.")
    assert classify_kind(text).kind == "education_certificate"


def test_a_safety_course_card_or_attendance_certificate_is_not_taken_for_experience():
    text = ("Venture Demo Safety Training Centre. Certificate of attendance. This is to certify that Mr. Amit Demo has attended the Basic "
            "H2S awareness course on 16 Nov 2025. Valid for 2 years. Training Manager")
    assert classify_kind(text).kind is None


def test_a_resume_page_with_a_board_of_education_line_is_not_an_education_certificate():
    resume = ("ABOUT ME I want to work where I can have a good career. WORK EXPERIENCE 11/10/2019 - 11/03/2020 Chennai Kitchen Trainee "
              "Example Hotel Assisting the chef with preparation. 01/10/2021 - 31/03/2022 On Job Trainee Sample Hotels Serving food items. "
              "CONTACT demo@example.test (+91) 9000000000 EDUCATION AND TRAINING Intermediate Education (12th Standard) Board of "
              "Intermediate Education - A.P S.S.C (10th) Board of Secondary Education - A.P")
    assert classify_kind(resume).kind not in {"education_certificate", "experience_certificate"}


# ---- an unreadable page inside a run of certificates takes their kind and is flagged -----------------------------------------------

GIBBERISH = " ".join(["xn", "vs", "Wc", "NS", "fF", "Fems", "SEZ", "OD", "ir", "nN", "Pg", "fai", "Sex", "oniw", "WieA", "Rien"] * 6)


def test_an_unreadable_page_between_two_experience_letters_takes_that_kind_and_is_flagged():
    letter = "Alpha Valve Services\n" + EXPERIENCE
    guesses = classify_kinds([letter, GIBBERISH, INTERN_TRAINING])
    assert [g.kind for g in guesses] == ["experience_certificate"] * 3
    assert not guesses[0].inherited and guesses[1].inherited and not guesses[2].inherited
    assert guesses[1].confidence <= 0.55 and "check it" in guesses[1].reason


def test_an_unreadable_page_after_the_last_certificate_of_a_run_is_taken_with_it_unless_the_next_page_is_another_kind():
    assert classify_kinds([INTERN_TRAINING, GIBBERISH])[1].kind == "experience_certificate"
    assert classify_kinds([INTERN_TRAINING, GIBBERISH, "Some unrelated words " * 5])[1].kind == "experience_certificate"
    assert classify_kinds([INTERN_TRAINING, GIBBERISH, MEDICAL])[1].kind is None
    assert classify_kinds([SSLC, GIBBERISH])[1].kind == "education_certificate"


def test_the_inherited_page_does_not_pass_the_kind_on_and_a_readable_unknown_page_is_never_filled_in():
    guesses = classify_kinds([INTERN_TRAINING, GIBBERISH, GIBBERISH])
    assert guesses[1].inherited and guesses[2].kind is None
    plain = "Result normal remarks none thank you for choosing our centre signature and stamp of the examining doctor. " * 3
    assert classify_kinds([INTERN_TRAINING, plain])[1].kind is None
    assert classify_kinds([GIBBERISH, INTERN_TRAINING])[0].kind is None          # nothing before it to continue


def test_an_unreadable_page_with_a_passport_zone_or_after_a_non_certificate_is_not_filled_in():
    zone = GIBBERISH + "\nP<INDDEMO<<RAHUL<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<\nZ70001373IND9001015M3001012<<<<<<<<<<<<<<04"
    assert classify_kinds([INTERN_TRAINING, zone])[1].kind is None
    assert classify_kinds([MEDICAL, GIBBERISH])[1].kind is None


# ---------------------------------------------------------------------------------- training / tuning on real bundles (invented people)

WORKING_UNDER_ME = """C/A | M/s. Nova Partners & Co.
CHARTERED ACCOUNTANTS
Date: 02-06-2026
It is here by confirmed that Mr Tester Sample S/o Mohd Sample R/o Example Town, Distt Sampleganj has been
working under me as an Accountant from 1st July 2023 till date. I wish him good luck in future.
For :- M/s Nova Partners & Co.
Chartered Accountants.
"""
CONTINUES_AS = """Example Inspection Company Ltd.
Dated : 15/07/2025
TO WHOM IT MAY CONCERN
Mr. TESTER SAMPLE, NDT Inspector with employee number 4411 and ITC Level II certification meeting our company requirements.
Example Inspection Company hereby certify that Mr. Tester Sample to be continue as a company NDT Inspector since May 2024 to July 2025.
Operation Manager
"""
DIPLOMA_AWARD = """Example Computer Education Centre
Having duly examined
TESTER SAMPLE
Son/Daughter/Wife of SAMPLE SENIOR
during 6 months of study on the specified curriculum
and having found the candidate's performance to be GOOD
have pleasure in recognising this attainment
with the award of Diploma in Financial Accounting
Given on the 12th day of MAR 2015 at EXAMPLEPUR
Centre Director          Secretary
"""
PROFICIENCY = """Certificate of Proficiency
CERT NO 123456
This is to certify that Tester Sample
Date of birth: 5 December 1997
has demonstrated proficiency as a Painting Inspector Grade 2 in accordance with the requirements published in the document
and amendments in force on the examination date.
Date of issue 26 May 2026
Issued by: Example Certification Ltd
"""
NDT_LEVEL = """EXAMPLE NDT SOLUTIONS
NDT PERSONNEL CERTIFICATION
This is to certify that TESTER SAMPLE has met the certification requirements and has demonstrated proficiency
by qualifying certification examination and is hereby certified to NDT LEVEL II IN VISUAL TESTING
Training course and examination conducted as per the written practice which meets SNT-TC-1A guidelines.
Certificate No : EXN/VT/044     Certificate Issue Date 01.12.2025
Examination Marks Obtained (%)  General 82.5   Specific 80   Practical 84
"""
SAFETY_COURSE = """EXAMPLE GULF SAFETY & SECURITY TRAINING CENTRE
Certificate of Proficiency
This is to certify that TESTER SAMPLE was successfully assessed against and met learning outcomes for
BASIC FIRST AID AND CPR   Date of issue: Nov. 16, 2025   Expiry date: 15.Nov.2027
Cert. No.: 918tUjg7B
"""
TRAINING_PROGRAMME = """File No: EXT/CQC/21/0001
EXAMPLE TRAINING & INSPECTION CENTRE AN ISO 9001:2015 CERTIFIED
CERTIFICATE
This is to certify that Mr. TESTER SAMPLE
has succes sfully completed the training program in Welding Inspection,
Welding techniques, Application, Inspection & Safety as WELDING QA/QC
Principal Director
"""
APOSTILLE_DEGREE = """GOVERNMENT OF INDIA APOSTILLE (Convention de La Haye du 5 octobre 1961)
This public document DEGREE has been signed by REGISTRAR acting in the capacity of VICE CHANCELLOR
bears the seal of the Sub Divisional Magistrate, NEW DELHI
Certified at NEW DELHI, INDIA 17 Mar 2022   MINISTRY OF EXTERNAL AFFAIRS
"""
SUBJECT_MARKS = """CONSOLIDATED STATEMENT OF GRADES
ENGLISH 72
MATHEMATICS 65
PHYSICAL SCIENCES 70
ENGINEERING MECHANICS 61
ENGINEERING DRAWING 80
MAXIMUM MARKS = 100    PASSING MINIMUM = 35 MARKS
"""
DECLARED_PASSED = """National Institute of Labour Education & Management
THE SIX MONTH DIPLOMA IN ENVIRONMENT
with Registration No. EP 16 has been declared passed in the prescribed examination
held in April 2007 conducted by the Institute.
Executive Council of the Institute
"""
VERIFICATION_REPORT = """VERIFICATION SUMMARY
EDUCATION   PROVIDED DETAILS   VERIFICATION RESULTS
ISSUING AUTHORITY   Example University   VERIFIED CORRECT
DOCUMENT TYPE   Bachelor of Engineering - Civil Engineering   VERIFIED CORRECT
EMPLOYMENT   Example Builders   VERIFIED CORRECT
"""
MAIL_THREAD = """Thanks and Regards
Sample Person
Mob. 0500000000
This email has been classified as Company General Use on 11/25/2019 8:24 AM
Subject: FW: CV TESTER SAMPLE ENGINEER
Importance: High
I already forwarded to the department for their review and approval. Please send your review of the CV by tomorrow.
Regards, Sample
"""
GARBLED_OFFER = """EXAMPLE INTERNATIONAL COMPANY
KUWAIT
JgB OFFHR LETTHR
&AME                  TESTER SAMPLE
PSSITION TITLH        ENVIRONMENTAL SUPERVISOR
&A$IC SALARY          KD 450.000/-
TAKH HOMH $ALARY PER MONTH   KD 505.000/-
& considered as accepted by the employee upon signing this offer
This job offer is valid only for 30 days.
"""


def test_a_letter_confirming_that_a_person_has_been_working_under_me_is_an_experience_letter():
    guess = classify_kind(WORKING_UNDER_ME)
    assert guess.kind == "experience_certificate"


def test_a_letter_certifying_that_a_person_continues_as_an_employee_is_an_experience_letter():
    assert classify_kind(CONTINUES_AS).kind == "experience_certificate"


def test_a_diploma_a_centre_awards_after_examining_the_student_is_an_education_certificate():
    assert classify_kind(DIPLOMA_AWARD).kind == "education_certificate"


def test_a_certificate_of_proficiency_and_a_training_programme_certificate_are_education_certificates():
    assert classify_kind(PROFICIENCY).kind == "education_certificate"
    assert classify_kind(TRAINING_PROGRAMME).kind == "education_certificate"


def test_safety_courses_and_ndt_personnel_certification_are_not_education_certificates():
    assert classify_kind(SAFETY_COURSE).kind is None
    assert classify_kind(NDT_LEVEL).kind is None


def test_an_apostille_on_a_degree_and_a_marks_sheet_of_subjects_are_education_certificates():
    assert classify_kind(APOSTILLE_DEGREE).kind == "education_certificate"
    assert classify_kind(SUBJECT_MARKS).kind == "education_certificate"
    assert classify_kind(DECLARED_PASSED).kind == "education_certificate"


def test_a_verification_report_quoting_degrees_and_employers_is_no_certificate():
    assert classify_kind(VERIFICATION_REPORT).kind is None


def test_a_review_of_a_cv_in_a_mail_thread_is_not_a_google_review_but_a_real_one_is():
    assert classify_kind(MAIL_THREAD).kind != "google_review"
    review = ("Google Maps  Local Guide  Tester Sample  4 stars  2 weeks ago  Great place to work, friendly managers and clean rooms. "
              "Posted publicly on Google. Write a review.")
    assert classify_kind(review).kind == "google_review"


def test_a_garbled_job_offer_that_is_accepted_by_signing_it_is_a_signed_offer():
    guess = classify_kind(GARBLED_OFFER)
    assert guess.kind == "signed_offer" and guess.confidence >= 0.7


# ---------------------------------------------------------------------------- glued text and employer praise letters
# (invented people and companies; the wording is what a second OCR engine produced on real scans: words run together)

GLUED_EXPERIENCE = "TOWHOMITMAYCONCERNTHISISTOCERTIFYTHATMRSAMPLEWORKEDASAFITTER"
GLUED_MIDDLE_LINES = """SAMPLEMARINESERVICES(PTE)LTD
Date:11Feb2010
THISISTOCERTIFYTHATMRTESTERSAMPLEWORKEDASAFITTERINOURYARD
FROM12JAN2015TO30JUN2019WEWISHHIMALLTHESUCCESS
Signed
Operations Manager"""
GLUED_RESUME = """CURRICULUMVITAE
TESTERSAMPLE
CAREEROBJECTIVE:TOSECUREACHALLENGINGPOSITIONINANORGANIZATION
WORKEXPERIENCE
WORKEDASAFITTERINSAMPLECOMPANYFROM2015TO2019
DESIGNATION:FITTER
EDUCATIONALQUALIFICATION
PERSONALDETAILS
DATEOFBIRTH
LANGUAGESKNOWN:ENGLISH,TAMIL
DECLARATION:IHEREBYDECLARETHATTHEABOVEISTRUE"""


def test_a_glued_experience_letter_line_is_an_experience_certificate():
    guess = classify_kind(GLUED_EXPERIENCE)
    assert guess.kind == "experience_certificate"


def test_glued_lines_in_a_mostly_spaced_page_are_read_in_squashed_form():
    text = ("Sample Marine Services Pte Ltd\nDate: 11 Feb 2010\nTOWHOMITMAYCONCERN\nThis is to confirm that Mr Tester Sample was\n"
            "serving us as a fitter. HISJOBISREGARDEDEFFICIENTANDRECOMMENDEDTOOTHERS")
    assert classify_kind(text).kind == "experience_certificate"
    assert classify_kind(GLUED_MIDDLE_LINES).kind == "experience_certificate"


def test_a_glued_resume_is_not_an_experience_certificate():
    assert classify_kind(GLUED_RESUME).kind is None


def test_squashing_is_not_used_on_normal_spaced_text():
    from app.integrations.ocr import kind_classifier as module

    spaced = "Sample Marine Services Pte Ltd\nThis is to certify that Mr Tester Sample worked as a fitter in our yard.\nHis conduct was good.\n"
    assert module._glued_form(spaced) == ""
    assert classify_kind(spaced).kind == "experience_certificate"
    # a spaced page that mentions a phrase only in glued-looking fragments of different words does not match across the gaps
    other = "a b to whom it may concern x y z " * 3 + "Boarding passenger flight list " * 5
    assert module._glued_form(other) == ""


def test_short_phrases_do_not_match_inside_unrelated_glued_words():
    from app.integrations.ocr import kind_classifier as module

    # "designation" is long enough to squash; "joined as" / "pulse" (shorter than nine letters) must not be looked for inside glue
    assert module._squash_phrase("pulse") is None
    assert module._squash_phrase("joined as") is None
    assert module._squash_phrase("designation") == "designation"
    glued = "XXPULSEJOINEDASYYQUALITYASSURANCEPLANMANAGEMENTREVIEWSCHEDULEFORTHEPROJECTTEAMMEETING"
    assert classify_kind(glued).kind is None


@pytest.mark.parametrize("text", [
    "TESTIMONY\nSAMPLE OFFSHORE PTE LTD\nTESTER SAMPLE\nSAFETY OFFICER\nHas successfully completed the upgrading work in January 2012\n"
    "for adhering to the safety procedures",
    "CERTIFICATE OF APPRECIATION\nPresented to MR TESTER SAMPLE\nfor his excellent performance and contribution as a safety attendant\nduring the 2006 turnaround\nDepartment Manager",
    "To whom it may concern\nI wish to take this opportunity in thanking Mr Tester Sample who was our safety officer during our vessel work.\n"
    "He has been an asset to the work and I wish him all the success in his future employment.\nSigned, Master",
    "This is to certify that Tester Sample was part of the structural, piping and electrical projects team for the Sample Vela rig.\n"
    "The work scope was completed to the satisfaction of the onboard management with all safety objectives reached.\nRig OIM",
])
def test_employer_appreciation_testimony_and_recommendation_letters_are_experience(text):
    assert classify_kind(text).kind == "experience_certificate"


def test_praise_words_do_not_make_an_education_or_medical_page_an_experience_letter():
    education = ("To whom it may concern\nThis is to certify that Tester Sample passed the Higher Secondary examination, Register No 123456,\n"
                 "Board of Higher Secondary Education, with distinction. We wish him all the success in his future.\nTotal marks 980")
    assert classify_kind(education).kind != "experience_certificate"
    medical = ("To whom it may concern\nMedical Examination Report. This is to certify that Tester Sample is medically fit for the job.\n"
               "Blood pressure 120/80 Haemoglobin 14 Chest x ray normal. Recommended for employment.\nDr Sample MBBS")
    assert classify_kind(medical).kind != "experience_certificate"


def test_praise_wording_on_a_project_award_is_not_enough():
    # an award for a project (no person's own work, no letter wording) stays unclassified
    award = "CERTIFICATE OF APPRECIATION\nPRESENTED TO TESTER SAMPLE\nAWARDED ON 18TH OF JUNE 2020\nFor outstanding contribution to the safe execution of the SAMPLE PROJECT\nProject Manager"
    assert classify_kind(award).kind is None
