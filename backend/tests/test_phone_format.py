"""Canonical candidate phone numbers: written as '+91XXXXXXXXXX' when readable with confidence, as typed otherwise."""
import pytest

from app.services.candidate_identity import normalize_phone
from app.services.phone_format import canonical_phone, format_phone


@pytest.mark.parametrize("typed, expected", [
    ("+91 98765 43210", "+919876543210"),
    ("098765 43210", "+919876543210"),
    ("9876543210", "+919876543210"),
    ("(098765)-43210", "+919876543210"),
    ("919876543210", "+919876543210"),
    ("0091 9876543210", "+919876543210"),
    (" 98765-43210 ", "+919876543210"),
    ("+971 50 123 4567", "+971501234567"),
    ("00 971501234567", "+971501234567"),
])
def test_readable_numbers_become_canonical(typed, expected):
    assert canonical_phone(typed, "91") == expected


@pytest.mark.parametrize("typed", ["", "abc", "98765", "0000000000", "12345678901", "+91 98765 43210 / 98765 00000", "98765 43210 ext 4", "+0123456789"])
def test_unreadable_numbers_are_kept_exactly_as_typed(typed):
    assert canonical_phone(typed, "91") == typed


def test_a_national_number_without_a_home_code_is_left_alone_and_none_passes_through():
    assert canonical_phone("9876543210", None) == "9876543210"
    assert canonical_phone("+91 98765 43210", None) == "+919876543210"
    assert canonical_phone(None, "91") is None


def test_the_home_code_is_the_agencys_not_always_91():
    assert canonical_phone("9876543210", "44") == "+449876543210"
    assert canonical_phone("501234567", "971") == "501234567"   # not ten digits: never guessed at


@pytest.mark.parametrize("typed", ["+91 98765 43210", "098765 43210", "9876543210", "919876543210", "0091-9876543210"])
def test_duplicate_matching_still_sees_every_spelling_as_the_same_digits(typed):
    assert normalize_phone(canonical_phone(typed, "91"), "91") == normalize_phone(typed, "91") == "9876543210"


def test_the_export_writes_the_whatsapp_number_only_when_it_differs_from_the_phone():
    from types import SimpleNamespace
    from app.services.candidate_search import whatsapp_if_different
    candidate = lambda phone, whatsapp: SimpleNamespace(phone=phone, whatsapp=whatsapp)  # noqa: E731
    assert whatsapp_if_different(candidate("+919876543210", "098765 43210"), "91") == ""
    assert whatsapp_if_different(candidate("+919876543210", None), "91") == ""
    assert whatsapp_if_different(candidate("+919876543210", "98765 43211"), "91") == "+91 98765 43211"
    assert whatsapp_if_different(candidate("", "98765 43211"), "91") == "+91 98765 43211"


def test_display_grouping():
    assert format_phone("+919876543210", "91") == "+91 98765 43210"
    assert format_phone("098765 43210", "91") == "+91 98765 43210"
    assert format_phone("+971501234567", "91") == "+971 501 234 567"
    assert format_phone("12345", "91") == "12345"
    assert format_phone("  call after 5  ", "91") == "call after 5"
    assert format_phone(None, "91") is None
