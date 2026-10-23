"""Identity heuristics that decide whether a file holds one person or several."""
import pytest

from app.integrations.ocr.person_split import incompatible, page_identity
from app.services.candidate_identity import name_relation


RESUME_PAGE = """Name: Rahul Kumar
Mobile: +91 90000 00001
Passport No: Z1234567 Date of Issue: 01/01/2020
"""
PASSPORT_PAGE = """Passport No: Z1234567
Surname KUMAR
"""


def test_passport_number_on_a_resume_line_does_not_absorb_the_next_label():
    identity = page_identity(RESUME_PAGE)
    assert identity["passports"] == ["Z1234567"]
    assert not incompatible(identity, page_identity(PASSPORT_PAGE))


def test_different_passport_numbers_are_still_incompatible():
    other = page_identity("Passport No: K7654321")
    assert incompatible(page_identity(RESUME_PAGE), other)


def test_references_block_does_not_make_the_page_mixed():
    text = RESUME_PAGE + """
References
Name: John Doe
Mobile: +91 90000 00002
Name: Jane Roe
Mobile: +91 90000 00003
"""
    identity = page_identity(text)
    assert identity["names"] == ["Rahul Kumar"]
    assert not identity["mixed"]


def test_several_names_without_a_second_contact_are_not_proof_of_two_people():
    text = "Name: Rahul Kumar\nName: Nominee Person\nMobile: +91 90000 00001\n"
    assert not page_identity(text)["mixed"]


def test_two_candidates_with_their_own_contacts_on_one_page_are_mixed():
    text = ("Name: Rahul Kumar\nMobile: +91 90000 00001\n"
            "Name: Priya Nair\nMobile: +91 90000 00002\n")
    assert page_identity(text)["mixed"]


@pytest.mark.parametrize("left, right, want", [
    ("Rahul Kumar Singh", "Singh Rahul", "partial"),
    ("Rahul K Singh", "Rahul Kumar Singh", "same"),
    ("R Singh", "Rahul Singh", "same"),
    ("Rahul Kumar", "Rahul Kumar", "same"),
    ("Rahul Kumar", "Priya Nair", "different"),
    ("", "Priya Nair", "same"),
])
def test_name_relation_allows_order_initials_and_missing_middle_names(left, right, want):
    assert name_relation(left, right) == want


def test_a_name_variant_across_pages_is_not_a_different_person():
    a = page_identity("Name: Rahul Kumar Singh\nPhone: +91 90000 00001")
    b = page_identity("Name: Singh Rahul\nPhone: +91 90000 00001")
    assert not incompatible(a, b)
