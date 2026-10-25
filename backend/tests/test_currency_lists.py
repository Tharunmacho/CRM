"""The compiled-in currency list is only the last-resort fallback; the real list is the `currencies` table.

The order form and the stage-8 advance read the active currencies from the database (see test_agency_settings.py).
These checks pin the fallback that keeps an un-migrated database working.
"""
import pytest

from app.api.routers.candidates import CandidateIn
from app.domain.stages import CURRENCY_CODES, validate_payload


def test_fallback_list_includes_every_previously_stored_advance_currency():
    # GBP is on the order form; the advance used to reject it. Earlier codes stay valid.
    for code in ("INR", "USD", "EUR", "SGD", "AED", "SAR", "QAR", "OMR", "KWD", "BHD", "GBP"):
        assert code in CURRENCY_CODES


@pytest.mark.parametrize("code", CURRENCY_CODES)
def test_stage_eight_accepts_every_fallback_currency(code):
    assert validate_payload(8, {"amount": "10", "amountCurrency": code})["amountCurrency"] == code


def test_stage_eight_still_rejects_a_currency_nobody_offers():
    with pytest.raises(Exception):
        validate_payload(8, {"amount": "10", "amountCurrency": "XXX"})


def test_new_candidate_nationality_is_left_empty_unless_provided():
    base = {"name": "A Person", "phone": "+10000000000"}
    assert CandidateIn(**base).nationality is None
    assert CandidateIn(**base, nationality="  ").nationality is None
    assert CandidateIn(**base, nationality="Kenyan").nationality == "Kenyan"
