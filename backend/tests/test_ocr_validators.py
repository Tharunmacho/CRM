"""Validators for what the local OCR reads: MRZ check digits and repair, Aadhaar Verhoeff, dates, contacts, and the
cross-check hook the Veris hand-off calls. Synthetic identifiers only."""
from datetime import date
from types import SimpleNamespace

import pytest

import ocr_hard_cases as hard
from app.integrations.ocr import validators as V
from app.integrations.ocr.read_merge import verhoeff_valid

LINE1, LINE2, NUMBER = hard.valid_mrz(1)


# ---------------------------------------------------------------- MRZ

def test_check_digit_follows_the_icao_9303_weights():
    # The published specimen: document number L898902C3 has check digit 6.
    assert V.mrz_check_digit("L898902C3") == 6
    assert V.mrz_check_digit("740812") == 2 and V.mrz_check_digit("120415") == 9


def test_a_valid_td3_mrz_parses_and_all_four_checks_pass():
    result = V.parse_mrz_td3(LINE1, LINE2)
    assert result.valid and not result.repaired and all(result.checks.values())
    assert result.document_number == NUMBER and result.surname == "SAMPLE1" and result.given_names == "TESTER"
    assert result.birth_date == date(1990, 1, 1) and result.expiry_date == date(2031, 1, 2)


@pytest.mark.parametrize("damage", [
    lambda s: s.replace("900101", "9O0101"),         # O for 0 in the birth date
    lambda s: s.replace("310102", "31O1O2"),         # two of them in the expiry
    lambda s: s.replace("K30", "K3O"),               # the passport number: letter O for digit 0
    lambda s: s.replace("IND9", "IND9").replace("<<<<<", "<<KKK"),  # filler misread as K
])
def test_common_misreads_are_repaired_until_the_checksums_pass(damage):
    result = V.find_mrz(LINE1 + "\n" + damage(LINE2))
    assert result is not None and result.valid, result
    assert result.document_number == NUMBER


def test_a_wrong_digit_is_not_invented():
    broken = LINE2.replace("900101", "400101")  # a digit that no look-alike rule explains
    result = V.find_mrz(LINE1 + "\n" + broken)
    assert result is None or not result.valid


def test_filler_runs_are_restored_without_touching_names():
    from app.integrations.ocr.read_merge import normalise_mrz_line

    assert normalise_mrz_line("P<INDERIKSSON<<ANNA" + "K" * 21) .endswith("<" * 21)
    assert "ERIKSSON" in normalise_mrz_line("P<INDERIKSSON<<ANNA<<<<<<<<<<<<<<<<<<<")


# ---------------------------------------------------------------- Aadhaar

def test_aadhaar_numbers_pass_verhoeff_and_one_digit_misreads_are_repaired():
    number = hard.valid_aadhaar(7)
    assert verhoeff_valid(number) and number[0] in "23456789"
    found = V.find_aadhaar_numbers(f"Aadhaar {number[:4]} {number[4:8]} {number[8:]} end")
    assert len(found) == 1 and found[0].valid and found[0].number == number and not found[0].repaired

    swapped = number[:5] + ("1" if number[5] == "7" else "7") + number[6:]   # 7/1 look alike
    maybe = V.check_aadhaar(swapped)
    assert maybe is not None
    # A repair is accepted only when exactly one change makes the number valid, and it is marked as a repair.
    assert maybe.repaired or not maybe.valid

    letters = number.replace("0", "O")                                       # letters read for zeros
    assert V.check_aadhaar(letters).number == number


def test_numbers_that_cannot_be_aadhaar_are_rejected():
    assert V.check_aadhaar("123456789012").valid is False       # first digit rule
    assert V.check_aadhaar("22345678901") is None               # eleven digits


# ---------------------------------------------------------------- dates, contacts

def test_dates_must_run_in_order():
    assert V.dates_sane(date(1990, 1, 1), date(2021, 1, 1), date(2031, 1, 1))
    assert not V.dates_sane(date(1990, 1, 1), date(1980, 1, 1), date(2031, 1, 1))
    assert not V.dates_sane(date(1990, 1, 1), date(2021, 1, 1), date(2020, 1, 1))
    assert not V.dates_sane(date(2999, 1, 1))


def test_indian_mobile_numbers_and_emails():
    assert V.find_mobiles("call +91 98765 43210 or 9876543210 today") == ["9876543210"]
    assert V.find_mobiles("not a number 12345 67890") == []       # does not start with 6-9
    assert V.find_emails("write to Tester.Sample@Example.test, thanks") == ["tester.sample@example.test"]
    assert V.find_emails("no at-sign here") == []


# ---------------------------------------------------------------- the record

def test_scan_pages_records_where_each_pattern_was_found_and_the_flags_hold_no_values():
    aadhaar = hard.valid_aadhaar(11)
    texts = {1: "CURRICULUM VITAE tester.sample@example.test 9876543210", 2: LINE1 + "\n" + LINE2,
             3: f"Government of India {aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}"}
    patterns = V.scan_pages(texts)
    flags = patterns.flags()
    assert flags["mrz_pages"] == [2] and flags["mrz_checksums_valid"]
    assert flags["aadhaar_pages"] == [3] and flags["aadhaar_checksum_valid"]
    assert flags["email_pages"] == [1] and flags["mobile_pages"] == [1] and flags["dates_sane"] is True
    shown = str(flags)
    assert NUMBER not in shown and aadhaar not in shown and "tester.sample" not in shown


# ---------------------------------------------------------------- the hook the Veris hand-off calls

def test_local_patterns_has_the_shape_the_hand_off_stores():
    out = V.local_patterns("passport", LINE1 + "\n" + LINE2)
    assert out["passport_number"] == NUMBER and out["date_of_birth"] == "1990-01-01" and out["mrz_checksums_valid"] is True
    assert V.local_patterns("passport", "no machine readable zone here") == {}
    aadhaar = hard.valid_aadhaar(12)
    assert V.local_patterns("aadhaar", f"{aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}")["aadhaar_number"] == aadhaar
    import json
    json.dumps(out)  # stored in the extraction state


def test_cross_check_reports_agreements_conflicts_and_gaps_by_field_name_only():
    local = V.local_patterns("passport", LINE1 + "\n" + LINE2)
    payload = {"mrz": {"passport_number": NUMBER, "surname": "SAMPLE1", "given_names": "TESTER",
                       "date_of_birth": "1990-01-01", "expiry_date": "2031-01-03"}}
    out = V.cross_check(local, payload)
    assert set(out) >= {"agreements", "conflicts"}
    assert {"passport_number", "date_of_birth", "name"} <= set(out["agreements"])
    assert out["conflicts"] == ["date_of_expiry"]
    assert NUMBER not in str(out)

    payload["mrz"]["passport_number"] = "K9999999"
    assert "passport_number" in V.cross_check(local, payload)["conflicts"]


def test_a_local_field_whose_own_check_digit_failed_is_not_held_against_veris():
    broken = LINE2.replace("900101", "400101")      # only the birth date is damaged; the number's own check still passes
    local = V.local_patterns("passport", LINE1 + "\n" + broken)
    assert local["number_ok"] and not local["birth_ok"]
    out = V.cross_check(local, {"mrz": {"passport_number": NUMBER, "date_of_birth": "1990-01-01"}})
    assert "passport_number" in out["agreements"] and "date_of_birth" in out["veris_only"]


def test_aadhaar_cross_check_ignores_a_masked_number():
    number = hard.valid_aadhaar(13)
    local = V.local_patterns("aadhaar", f"{number[:4]} {number[4:8]} {number[8:]}")
    assert V.cross_check(local, {"aadhaar": {"aadhaar_number": number}})["agreements"] == ["aadhaar_number"]
    assert V.cross_check(local, {"aadhaar": {"aadhaar_number": "999999999999"}})["conflicts"] == ["aadhaar_number"]
    masked = V.cross_check(local, {"aadhaar": {"aadhaar_number": "XXXX XXXX 1234"}})
    assert "aadhaar_number" not in masked["conflicts"] + masked["agreements"]


# ---------------------------------------------------------------- through the real hand-off with the fake Veris

def test_the_real_validators_work_through_the_veris_hand_off(monkeypatch):
    from test_combined_documents import FakeVeris  # the fake Veris used by the hand-off tests
    from copy import deepcopy

    import ocr_synthetic as synth
    from app.services import document_extraction as subject
    from test_document_extraction import pdf

    body = [line for line in synth.passport_lines(1) if not line.startswith(("P<", "Z70")) and line]
    data = pdf("\n".join(body + ["", LINE1, LINE2]))

    veris = FakeVeris()
    state: dict = {}
    settings = SimpleNamespace(
        tesseract_cmd=None, ocr_languages="eng", ocr_max_pages=300, ocr_page_timeout_seconds=20,
        ocr_document_timeout_seconds=600, veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key",
        ocr_provider_wait_seconds=0.0, veris_parallel_jobs=4, veris_chunk_pages=20, veris_chunk_overlap_pages=1)

    class Provider:
        def __init__(self, **options):
            self.jobs = veris

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(subject, "VerisOCR", Provider)

    def run(veris_number):
        veris.build = lambda mode, index, texts: {"mrz": {
            "given_names": "TESTER", "surname": "SAMPLE1", "passport_number": veris_number, "date_of_birth": "1990-01-01",
            "expiry_date": "2031-01-02", "all_check_digits_valid": True}} if mode == "passport" else {}
        state.clear()
        return subject.extract_attachment(data, state=state, save=lambda s: deepcopy(s), settings=settings, designations=["Welder"])

    agree = run(NUMBER)
    check = agree["coverage"]["kinds"]["passport"]["crossCheck"]
    assert "passport_number" in check["verified"] and not check["conflicts"]
    assert state["local_patterns"]["passport"]["passport_number"] == NUMBER

    clash = run("K1111111")
    check = clash["coverage"]["kinds"]["passport"]["crossCheck"]
    assert "passport_number" in check["conflicts"] and clash["status"] == "needs_review"
    assert "K1111111" not in str(clash["coverage"]) + str(clash["warnings"])
