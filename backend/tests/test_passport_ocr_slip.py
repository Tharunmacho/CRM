"""One wrong character in a passport number read from a scan (a 5 read as an S, a 0 as an O) must never make one candidate's
pages look like two people. Seen on a real 24-page file: the resume had R7588777 and a work-permit page R7S88777, the file was stopped
with "different candidate identities" and nothing was split. Synthetic numbers only."""
import pytest

from app.integrations.ocr.person_split import incompatible, page_identity
from app.services.candidate_identity import passports_differ


@pytest.mark.parametrize("left,right", [
    ("R7588777", "R7S88777"),       # 5 read as S
    ("R7588777", "R75B8777"),       # 8 read as B
    ("K1000234", "K10O0234"),       # 0 read as O
    ("R7588777", "R758877"),        # a character lost
    ("R7588777", "R75887777"),      # a character added
    ("R7588777", " r7588777 "),     # spacing and case
])
def test_one_misread_character_is_the_same_passport(left, right):
    assert passports_differ(left, right) is False


@pytest.mark.parametrize("left,right", [
    ("R7588777", "R7599888"),       # several characters apart
    ("R7588777", "T1234567"),
    ("R7588777", "R7588"),          # far shorter
])
def test_numbers_that_cannot_be_one_passport_differ(left, right):
    assert passports_differ(left, right) is True


def test_a_missing_number_never_differs():
    assert passports_differ("", "R7588777") is False
    assert passports_differ(None, None) is False


def test_pages_with_one_misread_passport_digit_are_one_person():
    resume = {"names": [], "passports": ["R7588777"]}
    permit = {"names": [], "passports": ["R7S88777"]}
    assert incompatible(resume, permit) is False
    assert incompatible(permit, resume) is False


def test_pages_with_different_passports_are_still_different_people():
    one = {"names": [], "passports": ["R7588777"]}
    other = {"names": [], "passports": ["Z9912345"]}
    assert incompatible(one, other) is True


def test_a_page_with_two_numbers_matches_when_one_of_them_agrees():
    page = {"names": [], "passports": ["R7588777", "M1234567"]}
    permit = {"names": [], "passports": ["R7S88777"]}
    assert incompatible(page, permit) is False


def test_page_identity_keeps_the_misread_number_visible():
    identity = page_identity("Passport No: R7S88777\nName: Tabrej Mir\nMobile: +91 9000000001")
    assert identity["passports"] == ["R7S88777"]


# ---- which pages say who the candidate is -------------------------------------------------------------------------------

from types import SimpleNamespace

from app.services.document_extraction import _identity_evidence


def _page(text, kind, status="read"):
    return SimpleNamespace(text=text, document_type=kind, status=status)


def _split_suggested(pages):
    evidence = _identity_evidence(pages)
    return any(item["mixed"] for item in evidence) or any(
        incompatible(left, right) for index, left in enumerate(evidence) for right in evidence[index + 1:])


RESUME = "Name: Rashid Khan\nMobile: +91 9000000001\nPassport No: R7588777"
CONTRACT = "Employment contract\nName: Nawaz Ali Hebshi\nEstablishment ID: ZHR23065\nName: Rashid Khan"


def test_a_contract_naming_the_employer_does_not_make_a_second_person():
    pages = [_page(RESUME, "resume"), _page(CONTRACT, "unknown"), _page(CONTRACT, "other")]
    assert _split_suggested(pages) is False


def test_two_different_resumes_are_still_two_people():
    other = "Name: Dinesh Kumar\nMobile: +91 9000000002\nPassport No: Z9912345"
    assert _split_suggested([_page(RESUME, "resume"), _page(other, "resume")]) is True


def test_a_second_persons_passport_page_is_still_found():
    other = "Name: Dinesh Kumar\nPassport No: Z9912345"
    assert _split_suggested([_page(RESUME, "resume"), _page(other, "passport")]) is True


def test_unread_pages_say_nothing():
    assert _identity_evidence([_page(RESUME, "resume", status="unread"), _page("", "other", status="blank")]) == []


# ---- a filler '<' read as a K must not end up in the candidate's name ---------------------------------------------------

from app.integrations.ocr.validators import fix_filler_k


def test_a_stray_k_from_the_zone_filler_is_dropped_when_the_page_prints_the_name_without_it():
    page = "Surname SUNDARAM Given Name(s) SEKAR Nationality INDIAN"
    assert fix_filler_k(["SEKARK", "SUNDARAMK"], page) == ["SEKAR", "SUNDARAM"]
    assert fix_filler_k(["Vivekanandhank", "Mani"], "MANI VIVEKANANDHAN") == ["Vivekanandhan", "Mani"]


def test_a_name_that_really_ends_in_k_keeps_it():
    assert fix_filler_k(["MANIK", "ROY"], "Surname ROY Given Name MANIK") == ["MANIK", "ROY"]
    assert fix_filler_k(["MANIK", "ROY"], "") == ["MANIK", "ROY"], "nothing printed to prove it: left as read"
    assert fix_filler_k(["MARK"], "Given Name MAR MARK") == ["MARK"], "the longer word is printed too"


def test_short_words_are_never_shortened():
    assert fix_filler_k(["ARK"], "AR") == ["ARK"]


def test_a_k_glued_to_the_front_of_a_given_name_is_dropped_too():
    fixed = fix_filler_k(["Arockia", "Kbeni", "Selvaraj"], "Given Name AROCKIA BENI Surname SELVARAJ")
    assert [w.lower() for w in fixed] == ["arockia", "beni", "selvaraj"]
    assert fix_filler_k(["KARTHIK"], "KARTHIK") == ["KARTHIK"], "printed as it is read: kept"
