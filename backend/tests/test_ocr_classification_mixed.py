"""Classification of mixed pages, bilingual passport pages and OCR-damaged resume layouts.

All text is synthetic. Each case records a lesson from real bundles, written without any real document in it.
"""
import pytest

from app.integrations.ocr.classification import _DATES, classify_page, classify_pages
from test_ocr_classification import AADHAAR, CV, PASSPORT

FILLER = "Stamp seal ref 12 34 56 78 90 blotted ink unreadable corner"
GULF_DETAILS = "\nPersonal details\nDriving licence: Yes (UAE)\nEmirates ID: held\nNationality: Example\n"


# ---- licence / identity-card words are weak on a page that is a resume ----------------

def test_cv_listing_a_licence_and_emirates_id_in_personal_details_stays_a_resume():
    """Lesson: Gulf CVs state a licence in the personal-details block. Those words are not a licence card; the page
    was sent to review as 'unknown' and its work history was held back."""
    page = classify_page(CV + GULF_DETAILS)
    assert page.document_type == "resume" and page.also == ()


@pytest.mark.parametrize("text", [
    "Driving licence\nGovernment of Example\nTest Person 1234567890\nClass LMV",
    "Emirates ID\nTest Person\nID number 000-0000-0000000-0\nNationality Example",
    "Identity card\nGovernment of Example\nTest Person 1234567890",
])
def test_a_standalone_licence_or_identity_card_page_is_still_supporting(text):
    assert classify_page(text).document_type == "other"


def test_certificate_wording_still_beats_resume_structure_on_a_mixed_page():
    assert classify_page(CV + "\nThis is to certify that Test Person completed the course.").document_type == "unknown"


def test_a_continuation_page_listing_a_licence_is_still_a_continuation():
    page = "JOB RESPONSIBILITIES\nWelder 2018 - 2025\nDriving licence: Yes\nMaintained hydraulic equipment on site daily.\n"
    assert classify_pages([CV, page])[1].document_type == "resume"


# ---- a CV that states passport details is not a passport -------------------------------

def test_cv_listing_passport_number_place_of_issue_and_expiry_is_not_a_passport_page():
    """Lesson: 'a CV that lists a passport number is NOT a passport page'. Place of issue and date of expiry are in
    every passport-details block; only the passport's own wording makes a page a passport."""
    block = ("\nPassport details\nPassport No: A1234567\nPlace of issue: Example City\n"
             "Date of expiry: 01/01/2030\nNationality: Example\nDate of birth: 01/01/1990\n")
    page = classify_page(CV + block)
    assert page.document_type == "resume" and page.also == ()


def test_standalone_passport_details_listing_with_issue_and_expiry_is_a_passport():
    text = ("Passport No: A1234567\nPlace of issue: Example City\nDate of expiry: 01/01/2030\n"
            "Nationality: Example\nDate of birth: 01/01/1990\n")
    assert classify_page(text).document_type == "passport"


# ---- missing passport markers ---------------------------------------------------------

REQUEST_PAGE = ("These are to request and require in the name of the President of the Republic of India all those "
                "whom it may concern to allow the bearer to pass freely without let or hindrance.")


@pytest.mark.parametrize("text", [
    REQUEST_PAGE,
    REQUEST_PAGE.upper(),
    "THESE ARE TO REQUEST AND REQUIRE\nIN THE NAME OF THE PRESIDENT OF THE REPUBLIC OF INDIA the holder is to be afforded help",
    "These  are to  request   and  require all whom it may concern to allow the bearer to pass without hindrance",
])
def test_request_page_is_a_passport_even_with_ocr_spacing_and_case_noise(text):
    """Lesson: the request page was readable English and still scored nothing because nothing looked for it."""
    assert classify_page(text).document_type == "passport"


@pytest.mark.parametrize("text", [
    "Passport / Passeport\nType / Type P\nCountry code / Code du pays EXA\nSurname / Nom TEST\n",
    "Surname Nom\nTEST\nGiven names Prénoms\nPERSON\nNationality\nEXAMPLE\n",
    "Passeport\nSurname / Nom TEST\nCode du pays EXA\n",
])
def test_bilingual_french_labels_identify_a_passport_data_page(text):
    assert classify_page(text).document_type == "passport"


def test_a_single_french_word_on_a_correspondence_page_is_not_a_passport():
    text = "Letter to the office about the sale of the old car. Prénoms were left out of the form by mistake."
    assert classify_page(text).document_type == "unknown"


def test_hindi_labels_identify_an_indian_passport_page():
    assert classify_page("पासपोर्ट\nउपनाम / Surname TEST\nदिया गया नाम PERSON").document_type == "passport"


# ---- resume layouts damaged by OCR --------------------------------------------------

SPACED = """E D U C A T I O N
Diploma in Welding, Test Institute
Test Person
+971 50 000 0000
W O R K  E X P E R I E N C E
03/2018 - till date  Welder at Example Contracting
S k i l l s : TIG and MIG welding
"""


def test_spaced_out_headings_still_count_as_headings():
    assert classify_page(SPACED).document_type == "resume"


def test_headings_glued_to_body_text_and_bulleted_headings_count():
    text = ("Test Person test@example.test\nWORK EXPERIENCE Welder at Example Co 2018 - 2025\n"
            "EDUCATION Diploma in welding\n• SKILLS\nTIG and MIG welding\n")
    assert classify_page(text).document_type == "resume"


@pytest.mark.parametrize("text", [
    "03/2018 - 06/2021", "03/2018 to till date", "Jan 2019 - Present", "2015 to date", "2018/03 - 2021/06",
    "01/2019 – Current", "2018 - 2025",
])
def test_date_ranges_in_month_year_and_open_ended_forms(text):
    assert _DATES.search(text)


def test_month_year_ranges_alone_make_a_headed_page_a_continuation():
    page = "SITE EXPERIENCE\n03/2018 - till date  Example Contracting LLC, shutdown projects at refinery units.\n"
    assert classify_pages([CV, page])[1].document_type == "resume"
    assert classify_pages([CV, page.replace("03/2018 - till date", "Various years")])[1].document_type == "unknown"


@pytest.mark.parametrize("role", ["helper", "painter", "cook", "cleaner", "security guard", "steward", "nurse"])
def test_trade_and_service_job_titles_count_as_career_evidence(role):
    page = f"EXPERIENCE\nWorked as {role} at Example Company for several years on site.\n"
    assert classify_pages([CV, page])[1].document_type == "resume"


# ---- bundle-wide adoption of a lone marker -----------------------------------------------

LONE_PASSPORT_BACK = "Name of Father / Legal Guardian  TEST PARENT\nsome faint printed text lines here"
LONE_AADHAAR = "Enrolment No: 1234/12345/12345\nPlease keep this slip safe with you at all times."


def test_lone_passport_marker_is_adopted_when_the_bundle_holds_a_proven_passport_pages_away():
    result = classify_pages([PASSPORT, FILLER, FILLER, FILLER, LONE_PASSPORT_BACK])
    assert result[4].document_type == "passport" and result[4].inherited
    assert [p.document_type for p in result[2:4]] == ["unknown"] * 2


def test_lone_marker_without_a_proven_page_in_the_bundle_adopts_nothing():
    """Lesson: one caption is weak on its own. With no passport in the bundle it must not create one."""
    assert classify_pages([FILLER, FILLER, FILLER, LONE_PASSPORT_BACK])[3].document_type == "unknown"
    assert classify_pages([CV, FILLER, FILLER, LONE_PASSPORT_BACK])[3].document_type == "unknown"


def test_lone_marker_of_one_kind_is_not_adopted_into_a_bundle_proving_another_kind():
    assert classify_pages([AADHAAR, FILLER, FILLER, LONE_PASSPORT_BACK])[3].document_type == "unknown"
    assert classify_pages([PASSPORT, FILLER, FILLER, LONE_AADHAAR])[3].document_type == "unknown"


def test_lone_aadhaar_enrolment_number_is_adopted_into_a_bundle_with_a_proven_aadhaar():
    result = classify_pages([AADHAAR, FILLER, FILLER, LONE_AADHAAR])
    assert result[3].document_type == "aadhaar"


def test_a_resume_like_page_is_never_adopted_into_an_identity_kind():
    page = (
        "JOB RESPONSIBILITIES\nWelder 2018 - 2025\nMaintained hydraulic equipment and repaired machinery on site.\n"
        "Old passport no: NA\n"
    )
    assert classify_pages([PASSPORT, FILLER, FILLER, page])[3].document_type != "passport"
