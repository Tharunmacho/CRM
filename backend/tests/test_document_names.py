"""The candidate's name from the passport / Aadhaar card / resume, exactly as printed (every name and number here is invented).

Each test is one cause of a wrong or missing name seen on real imports: dots added to initials, a filler `<` read as a K, a zone name
cut at 39 letters, a certificate or test-form heading taken for a person, a line from the middle of a resume taken for a heading, and
documents that name different people."""
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from app.integrations.ocr.validators import mrz_check_digit, mrz_name_words, names_corroborated, reconcile_mrz_name
from app.services import bulk_candidate_import as bulk, people_by_content as pbc
from app.services.people_by_content import FileRead, PageRead, display_name, identify_people, name_tokens, plausible_name
from identify_fixtures import FakeReader, aadhaar_text, mrz_lines, passport_text, resume_text


def passport_with_zone(given_zone: str, surname_zone: str, printed_given: str, printed_surname: str, number: str = "P2345678", *, cut: bool = False) -> str:
    """A passport page whose zone says `given_zone` / `surname_zone` exactly as the reader gave them (with its slips) and whose
    printed lines say the real names. The zone's second line is correct, so the zone counts as verified."""
    names = f"{surname_zone}<<{given_zone}"
    line1 = f"P<IND{names}".ljust(44, "<")[:44] if cut else f"P<IND{names}".ljust(44, "<")
    _, line2 = mrz_lines("A", "B", number)
    return "\n".join(["REPUBLIC OF INDIA", f"Type P    Country Code IND    Passport No. {number}", f"Surname {printed_surname}",
                      f"Given Name(s) {printed_given}", "Nationality INDIAN", "Sex M    Date of Birth 01/01/1990", "", line1, line2])


def upload(kind: str, *texts: str):
    with fitz.open() as document:
        for _ in texts:
            document.new_page()
        data = document.tobytes()
    facts = [SimpleNamespace(status="read" if text else "blank", text=text, confidence=1.0, method="native_text") for text in texts]
    return bulk.Upload(path=f"{kind}.pdf", kinds=[kind], data=data, content_type="application/pdf", facts=facts)


def name_of(*uploads) -> str | None:
    return bulk.read_identity(list(uploads), budget=5).name


# ---- initials are kept as printed -----------------------------------------------------------------------------------------

def test_initials_are_kept_as_printed_without_a_dot():
    assert display_name(("rathish", "v", "r")) == "Rathish V R"
    assert display_name(("dinesh", "p")) == "Dinesh P"
    assert name_of(upload("resume", "ARAVIND S R\nMobile: +91 9000011111\nEmail: aravind.sr@example.com\n\nCAREER OBJECTIVE\nSite engineer")) == "Aravind S R"


# ---- a filler '<' read as a K ---------------------------------------------------------------------------------------------

def test_a_zone_filler_read_as_a_k_inside_and_after_the_given_names_is_dropped():
    page = passport_with_zone("ILAN<KKOVAR<K", "", "ILAN KOVAR", "")
    assert [h.tokens for h in pbc.extract(page, "passport").names] == [("ilan", "kovar")]
    assert name_of(upload("passport", page)) == "Ilan Kovar"


def test_a_lone_k_that_the_page_prints_next_to_the_name_is_an_initial_and_stays():
    page = passport_with_zone("PAVAN<K", "NAIRO", "PAVAN K", "NAIRO")
    assert name_of(upload("passport", page)) == "Pavan K Nairo"


def test_a_lone_k_is_kept_when_nothing_on_the_page_proves_it_a_slip():
    assert reconcile_mrz_name(["ILAN", "K"], [], "") == (["ILAN", "K"], [])


# ---- the zone's own separator and digits ----------------------------------------------------------------------------------

def test_a_k_where_the_double_filler_between_surname_and_given_names_belongs_is_that_separator():
    assert mrz_name_words(f"P<INDOKORO<NWOSU<K<AMARA<ELIAS<DANE".ljust(44, "<"))[:2] == (["OKORO", "NWOSU"], ["AMARA", "ELIAS", "DANE"])
    page = passport_with_zone("AMARA<ELIAS<DANE", "OKORO<NWOSU", "AMARA ELIAS DANE", "OKORO NWOSU").replace("OKORO<NWOSU<<AMARA", "OKORO<NWOSU<K<AMARA")
    assert [h.tokens for h in pbc.extract(page, "passport").names] == [("amara", "elias", "dane", "okoro", "nwosu")]


def test_a_digit_inside_a_zone_name_is_the_letter_it_was_misread_from():
    assert mrz_name_words("P<INDTAHORA<<ELAN<MAH0ON".ljust(44, "<"))[:2] == (["TAHORA"], ["ELAN", "MAHOON"])


def test_a_word_the_reader_split_in_two_is_joined_when_the_page_prints_it_whole():
    assert reconcile_mrz_name(["RAMESH", "J", "OHAN"], ["VERMA"], "Given Name(s) RAMESH JOHAN Surname VERMA") == (["RAMESH", "JOHAN"], ["VERMA"])
    assert reconcile_mrz_name(["RAMESH", "J", "OHAN"], ["VERMA"], "") == (["RAMESH", "J", "OHAN"], ["VERMA"]), "no proof: as read"


# ---- a name cut at the 39 letters the zone holds ---------------------------------------------------------------------------

def test_the_last_word_of_a_full_zone_is_completed_from_the_printed_name():
    page = passport_with_zone("LUMINARA<BELVEDERES", "TALMEDERI<KOSTAVRANOS", "LUMINARA BELVEDERES", "TALMEDERI KOSTAVRANOS", cut=True)
    assert name_of(upload("passport", page)) == "Luminara Belvederes Talmederi Kostavranos"
    assert reconcile_mrz_name(["LUMINARA", "BELVEDERE"], ["TALMEDERI"], "Given Name(s) LUMINARA BELVEDERES", False) == (["LUMINARA", "BELVEDERE"], ["TALMEDERI"])


# ---- a zone that failed its check digits is used only when the printed page says the same ------------------------------------

def test_a_zone_that_failed_its_check_digits_names_the_holder_when_the_printed_name_agrees():
    zone = ("P<INDOKORO<NWOSU<<AMARA<ELIAS".ljust(44, "<"), "XX12345<0IND9901017M3101012".ljust(44, "<"))
    broken = chr(10).join(["REPUBLIC OF INDIA", "Surname OKORO", "NWOSU", "Given Name(s) AMARA ELIAS", *zone])
    assert [h.tokens for h in pbc.extract(broken, "passport").names] == [("amara", "elias", "okoro", "nwosu")]
    stranger = broken.replace("AMARA<ELIAS", "ZYGMUNT<BRELLO")
    assert not [h for h in pbc.extract(stranger, "passport").names if h.source == "passport" and "zygmunt" in h.tokens]
    assert names_corroborated(["OKORO", "PANTHALATHODI"], "SURNAME OKORO PANTHA LATHODI")


# ---- organisations, forms and certificates are never a person -------------------------------------------------------------

@pytest.mark.parametrize("line", ["Tamilnadu Adi Dravidar Housing Corporation", "Listening Reading Spoken Spoken Writing", "Coastal Marine Academy",
                                  "Northern Welfare Association", "Tamilnadu Adi Dravidar Housing"])
def test_an_organisation_or_a_test_form_heading_is_not_a_name(line):
    assert name_tokens(line) is None


def test_a_certificate_filed_as_a_resume_gives_no_name():
    certificate = ("Tamilnadu Adi Dravidar Housing\nSelf Employment Scheme\nThis is to certify that the applicant has completed the training\n"
                   "Bachelor of Science Degree Certificate\nProvisional certificate issued by the University of Madras, Reg.No. 12345")
    assert pbc.extract(certificate, "resume").names == []
    assert name_of(upload("resume", certificate)) is None


def test_the_wrong_heading_of_a_resume_page_never_beats_the_passport():
    cv = "Northern Welfare Association\nMobile: +91 9000022222\nEmail: x.y@example.com\nProfile summary"
    passport = passport_text("Neel Harun", "Voss", "P3456789")
    assert name_of(upload("resume", cv), upload("passport", passport)) == "Neel Harun Voss"


# ---- only the first page of a resume has the name ---------------------------------------------------------------------------

def test_the_top_of_a_later_resume_page_is_not_a_name():
    later = "Maintaining Petty Cash\nHandled the daily ledger and the bank reconciliation for two branches."
    assert pbc.extract(later, "resume", heading_ok=False).names == []
    first = resume_text("Karthigeyan Rameshwaran", phone="9000033333")
    assert name_of(upload("resume", first, later)) == "Karthigeyan Rameshwaran"
    assert pbc.extract(later, "resume").names == [], "not even as the first page of a file: no contact details, no resume wording"


def test_a_second_resume_that_starts_right_after_the_first_is_still_named_by_its_own_page():
    one, two = resume_text("Karthigeyan Rameshwaran", phone="9000033333"), resume_text("Sivadas Perumalsamy", phone="9000044444", email="mq@example.com")
    reads = {"scan.pdf": FileRead([PageRead(1, "read", one, 1.0, "native_text", "resume"), PageRead(2, "read", two, 1.0, "native_text", "resume")], 2)}
    result = identify_people([("scan.pdf", b"x")], reader=FakeReader(reads), workers=1, deep=False)
    assert sorted(p["name"] for p in result.people) == ["Karthigeyan Rameshwaran", "Sivadas Perumalsamy"]


# ---- documents that disagree -------------------------------------------------------------------------------------------------

def test_documents_that_name_different_people_leave_the_name_unread():
    assert name_of(upload("aadhaar", aadhaar_text("Ponnusamy Lakshmanan", 1)), upload("resume", resume_text("Thirumal Devarajan", phone="9000055555"))) is None


def test_documents_that_share_a_name_word_agree_and_the_passport_wins():
    text = passport_text("Santhoshan", "Kanagaru", "P4567890")
    assert name_of(upload("resume", resume_text("Santhoshan K", phone="9000066666")), upload("passport", text)) == "Santhoshan Kanagaru"


# ---- a zone read on its own, a garbled zone word, a blurry passport -----------------------------------------------------------

def test_a_zone_whose_second_line_cannot_be_read_still_names_the_holder_when_the_printed_name_agrees():
    page = chr(10).join(["REPUBLIC OF INDIA", "Surname TALVARE MENDOR", "Given Name(s) KAVIN ARUL", "P<INDTALVARE<MENDOR<<KAVIN<ARUL".ljust(44, "<"),
                         "$7654321<1IND9001017M3101012<<<<<<<<<<<<<<<<"])
    assert [h.tokens for h in pbc.extract(page, "passport").names] == [("kavin", "arul", "talvare", "mendor")]


def test_a_zone_word_garbled_by_the_reader_is_replaced_by_the_one_printed_word_it_misreads():
    page = passport_with_zone("KAVIN", "ONDRAM", "KAVIN", "SUNDARAM")
    assert [h.tokens for h in pbc.extract(page, "passport").names] == [("kavin", "sundaram")]


def test_a_well_read_zone_word_is_never_replaced_by_a_misread_printed_word():
    page = passport_with_zone("ANTONY<PITCHAI", "JEYASEELAN", "ANTONY PITCHAL", "JEYASEELAN")
    assert [h.tokens for h in pbc.extract(page, "passport").names] == [("antony", "pitchai", "jeyaseelan")]


def test_a_blurry_passport_name_that_agrees_with_the_resume_heading_keeps_the_passports_spelling():
    cv = PageRead(1, "read", resume_text("Selvam G", phone="9000077777"), 0.9, "tesseract", "resume")
    blurry = chr(10).join(["Surname GOVINDAN", "Given Name(s) SELVAM", "P<INDGOVINDAN<<SELVAM".ljust(44, "<"), "$6543210<1IND9001017M3101012<<<<<<<<<<<<<<<<"])
    reads = {"cv.pdf": FileRead([cv], 1), "pp.pdf": FileRead([PageRead(1, "read", blurry, 0.3, "tesseract", "passport")], 1)}
    result = identify_people([(path, b"x") for path in reads], reader=FakeReader(reads), workers=1, deep=False)
    assert [p["name"] for p in result.people] == ["Selvam Govindan"]


# ---- the words around a name ----------------------------------------------------------------------------------------------------

def test_an_s_is_an_initial_unless_it_starts_son_of():
    assert name_tokens("Veera Ajith Chandran S") == ("veera", "ajith", "chandran", "s")
    assert name_tokens("S Aravind") == ("s", "aravind")
    assert name_tokens("Gunavelan S/O Ramakrishnan") == name_tokens("Gunavelan S O Ramakrishnan") == ("gunavelan",)


def test_a_verb_after_a_titled_name_is_not_part_of_the_name():
    ev = pbc.extract("This is to certify that Mr. Anil Mehta worked with us as a welder from 2015 to 2020.", "unknown")
    assert [h.tokens for h in ev.names] == [("anil", "mehta")]


def test_a_name_with_one_letter_three_times_is_still_a_name():
    assert name_tokens("Amara Okoro", lenient=True) == ("amara", "okoro") and plausible_name("Amara", lenient=True)
    assert name_tokens("Aaaab Bbbbc", lenient=True) is None
