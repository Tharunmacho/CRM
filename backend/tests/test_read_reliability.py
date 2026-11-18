"""Why a passport read ended without a usable answer, and what staff are told.

Synthetic only, against the fake reader of test_veris_handoff (no network, no database):
  * an expired reader result (HTTP 410 result_expired) is sent again under a new upload key instead of failing for ever;
  * the diagnostics say a merged part HAS its result (the merge drops the parts' own copies);
  * a two-page passport is ONE job (data page and back together); a heavy pair goes page by page;
  * a partial passport answer fills what it has;
  * every "needs review" line says why, in plain words.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from veriis.errors import VerisOCRError

import ocr_hard_cases as hard
from app.integrations.ocr import reader_failures
from app.services import document_diagnostics as diagnostics
from app.services import document_extraction as subject
from app.services.reading_notes import passport_reasons, review_reasons
from test_document_extraction import pdf
from test_ocr_classification import CERTIFICATE, CV, INDIA_PASSPORT_DATA, PASSPORT, PASSPORT_ADDRESS
from test_veris_handoff import UNCLASSIFIED
from test_passport_pages import data_page
from test_veris_handoff import world  # noqa: F401  (the fixture)


@pytest.fixture(autouse=True)
def reader_agrees_with_the_local_zone(world):
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2], "all_check_digits_valid": True}
    world.veris.build = lambda mode, index, texts: {"mrz": mrz} if mode == "passport" else {}


def passport_jobs(world) -> list[dict]:
    return [s for s in world.veris.submissions if s["mode"] == "passport"]


# ------------------------------------------------------------------------------ an expired result


def test_http_410_result_expired_is_its_own_plain_reason_and_can_be_retried():
    reason = reader_failures.classify(VerisOCRError("gone", status=410, code="result_expired"))
    assert reason["errorClass"] == "reader_result_expired" and reason["retriable"] is True and reason["httpStatus"] == 410
    assert "expired" in reader_failures.message(reason)
    assert reader_failures.classify(VerisOCRError("odd", code="result_expired"))["errorClass"] == "reader_result_expired"


def test_an_expired_result_sends_the_pages_again_under_a_new_key_and_the_file_is_read(world):
    """The reader answers a re-sent upload key with the earlier job, whose stored result is gone (HTTP 410). The same pages under
    the same key can never succeed, so they go again under the next key."""
    real, raised = world.veris.get, []

    def get(job_id):
        if "passport" in job_id and not raised:
            raised.append(job_id)
            raise VerisOCRError("gone", status=410, code="result_expired")
        return real(job_id)

    world.veris.get = get
    output = world.extract(pdf(CV, data_page(1)))

    assert raised and output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport"}
    jobs = passport_jobs(world)
    assert len(jobs) == 2 and jobs[0]["key"] != jobs[1]["key"]
    assert world.state["provider_job_history"]["passport"]   # the expired attempt is kept as history


def test_an_expired_result_that_expires_again_is_a_precise_failure_not_a_vague_one(world):
    real = world.veris.get

    def get(job_id):
        if "passport" in job_id:
            raise VerisOCRError("gone", status=410, code="result_expired")
        return real(job_id)

    world.veris.get = get
    with pytest.raises(subject.ExtractionFailure) as stopped:
        world.extract(pdf(data_page(1)))
    assert "expired" in str(stopped.value).lower() or "expired" in reader_failures.message(stopped.value.reason).lower()


# ------------------------------------------------------------------------------ the diagnostics


def test_a_merged_part_is_reported_as_having_its_result():
    state = {"provider_jobs": {"passport": {"status": "succeeded", "selected_pages": [1, 2], "result": {"mrz": {}},
                                            "chunks": [{"status": "succeeded", "selected_pages": [1], "coverage_status": "complete"},
                                                       {"status": "succeeded", "selected_pages": [2], "coverage_status": "complete"}]}}}
    rows = diagnostics._reader_jobs(state)
    assert [row["hasResult"] for row in rows] == [True, True]
    state["provider_jobs"]["passport"].pop("result")
    assert [row["hasResult"] for row in diagnostics._reader_jobs(state)] == [False, False]   # nothing merged: the parts really have none


# ------------------------------------------------------------------------------ one job for the data page and the back page


def test_a_two_page_passport_is_one_job_with_both_pages(world):
    output = world.extract(pdf(CV, PASSPORT_ADDRESS, data_page(1)))

    jobs = passport_jobs(world)
    assert len(jobs) == 1 and len(jobs[0]["texts"]) == 2
    assert "chunks" not in world.state["provider_jobs"]["passport"]
    assert output["status"] == "complete" and output["extractions"]["passport"]["coverage"]["pages_sent"] == [2, 3]


def test_a_heavy_pair_is_sent_page_by_page_before_it_is_sent(world):
    world.settings.veris_passport_job_max_bytes = 100_000
    world.monkeypatch.setattr(subject, "_prepared_job", lambda unit, content, rotations, **kw: content + b" " * (150_000 if len(unit.pages) > 1 else 0))
    output = world.extract(pdf(CV, PASSPORT_ADDRESS, data_page(1)))

    assert [len(s["texts"]) for s in passport_jobs(world)] == [1, 1] and output["status"] == "complete"


def test_the_setting_can_bring_back_one_job_per_page(world):
    world.settings.veris_passport_together_pages = 0
    world.extract(pdf(CV, PASSPORT_ADDRESS, data_page(1)))
    assert [len(s["texts"]) for s in passport_jobs(world)] == [1, 1]


def test_a_passport_answer_that_lacks_some_fields_still_fills_the_rest(world):
    """The reader found the number and the name but not the birth date: those are kept, the gap is named, nothing is discarded."""
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2], "all_check_digits_valid": True}
    world.veris.build = lambda mode, index, texts: {"mrz": mrz} if mode == "passport" else {}
    output = world.extract(pdf(CV, PASSPORT_ADDRESS, data_page(1)))
    fields = output["extractions"]["passport"]["fields"]
    assert fields["passport_no"] == hard.valid_mrz(1)[2] and fields["name"] == "Tester Sample1" and "dob" not in fields
    reasons = passport_reasons(output["extractions"]["passport"])
    assert "Review: the reader could not read the date of birth." in reasons
    assert "Review: the reader could not read the date of expiry." in reasons
    assert not any("passport number" in reason for reason in reasons)


# ------------------------------------------------------------------------------ every review line says why


def passport(**mrz):
    return {"raw": {"mrz": mrz, "mrz_source": mrz.pop("source", "mrz")}, "fields": {"passport_no": "A1234567", "dob": "1990-01-01",
            "passport_expiry": "2030-01-01", "name": "Tester Sample"}}


def test_each_passport_check_has_its_own_plain_message():
    assert passport_reasons(passport(all_check_digits_valid=True)) == []
    failed = passport(all_check_digits_valid=False, individual_check_digits={"passport_number": False, "expiry_date": False})
    assert passport_reasons(failed) == ["Review: the passport number failed its check digit.",
                                        "Review: the date of expiry failed its check digit."]
    assert passport_reasons(passport(all_check_digits_valid=False)) == [
        "Review: the check digits of the coded lines of the passport do not match, so a number or a date may be misread."]
    printed = {"raw": {"mrz": {"all_check_digits_valid": True}, "mrz_source": "printed"}, "fields": passport()["fields"]}
    assert "printed page" in passport_reasons(printed)[0]
    missing = passport(all_check_digits_valid=True)
    missing["fields"] = {"name": "Tester Sample"}
    assert passport_reasons(missing) == ["Review: the reader could not read the passport number.",
                                         "Review: the reader could not read the date of birth.",
                                         "Review: the reader could not read the date of expiry."]
    dated = passport(all_check_digits_valid=True)
    dated["fields"].update(passport_issue="2031-01-01", passport_expiry="2030-01-01")
    assert passport_reasons(dated) == ["Review: the date of expiry is earlier than the date of issue."]


def test_review_reasons_list_failed_kinds_pages_conflicts_and_are_never_empty():
    output = {"coverage": {"failed": ["The Aadhaar pages could not be read."], "issues": ["Different parts of the passport gave different values for mrz.expiry_date. The first was kept; check them against the original."],
                           "pages": {"not_sent": [{"page": 6, "reason": "The page could not be read.", "blocking": True}, {"page": 7, "reason": "blank", "blocking": False}]}},
              "extractions": {"passport": passport(all_check_digits_valid=False, individual_check_digits={"date_of_birth": False})},
              "warnings": []}
    reasons = review_reasons(output)
    assert reasons[0] == "The Aadhaar pages could not be read."
    assert "Review: the date of birth failed its check digit." in reasons
    assert any(reason.startswith("Check page 6 in the original file") for reason in reasons) and not any("page 7" in reason for reason in reasons)
    assert any("Passport: please check the date of expiry" in reason for reason in reasons)
    assert review_reasons({}) == ["Check the details against the original file."]   # never a bare "needs review"
    assert review_reasons(None)


def test_an_unrecognised_page_is_not_a_warning_and_an_unreadable_one_names_its_page(world):
    """Page 3 reads as plain unrelated text (read, not recognised): the file is complete. Page 4 cannot be read at all."""
    output = world.extract(pdf(CERTIFICATE, CV, UNCLASSIFIED, PASSPORT), partial=True)
    pages = {page["page"]: page for page in output["coverage"]["pages"]["not_sent"]}
    assert pages[3]["blocking"] is False and "Other documents" in pages[3]["reason"]
    assert output["status"] == "complete" and output["coverage"]["complete"] is True
    assert not any("could not be read" in warning for warning in output["warnings"])


def test_an_unreadable_page_keeps_a_warning_that_names_its_page_and_what_to_do(world):
    real = subject.inspect_document

    def lose_page_three(data, **options):
        found = real(data, **options)
        for page in found.pages:
            if page.page_number == 3:
                page.status, page.document_type, page.method, page.text = "unread", "unknown", "not_processed", ""
                page.reason = "We could not read enough text on this page"
        return found

    world.monkeypatch.setattr(subject, "inspect_document", lose_page_three)
    world.settings.veris_early_dispatch = False
    output = world.extract(pdf(CV, data_page(1), "x"), partial=True)

    from app.services.reading_notes import plain_notes
    assert output["status"] == "needs_review"
    shown = plain_notes(output["warnings"])
    assert any("Check page 3 in the original file" in line for line in shown), shown


def test_a_passport_answer_with_a_page_missing_is_used_not_thrown_away(world):
    """The back page came back without text. The data page's fields are kept and filled; the page is named for a person to check."""
    from test_document_extraction import result
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2], "all_check_digits_valid": True}

    def build(mode, index, texts):
        if mode != "passport":
            return {}
        pages = result("passport", 2)["pages"]
        pages[1]["text"] = ""
        return {"mrz": mrz, "pages": pages}

    world.veris.build = build
    output = world.extract(pdf(CV, PASSPORT_ADDRESS, data_page(1)))

    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume", "passport"}
    assert output["extractions"]["passport"]["fields"]["passport_no"] == hard.valid_mrz(1)[2]
    assert any("only part of the passport was read" in warning for warning in output["warnings"])
    assert any("only part of the passport was read" in text for text in output["coverage"]["failed"])
    assert "only part of the passport was read" in review_reasons(output)[0]


def test_one_good_page_and_one_empty_chunk_still_fill_the_passport(world):
    """The same with the page-by-page jobs: the data page's chunk is complete, the back page's chunk is empty."""
    from test_document_extraction import result
    world.settings.veris_passport_together_pages = 0
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2], "all_check_digits_valid": True}

    def build(mode, index, texts):
        if mode != "passport":
            return {}
        if hard.valid_mrz(1)[2] in " ".join(texts):
            return {"mrz": mrz}
        pages = result("passport", 1)["pages"]
        pages[0]["text"] = ""
        return {"mrz": {}, "pages": pages}

    world.veris.build = build
    output = world.extract(pdf(CV, PASSPORT_ADDRESS, data_page(1)))
    assert output["extractions"]["passport"]["fields"]["passport_no"] == hard.valid_mrz(1)[2]
    assert output["status"] == "needs_review"
