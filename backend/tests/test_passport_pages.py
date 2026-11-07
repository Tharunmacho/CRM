"""Which pages the passport job carries: only pages with passport details of their own, one copy of a data page that was
scanned twice, a narrower job after the reader failed, and a duplicate job that is already failed. Synthetic only."""

from copy import deepcopy
import logging
from types import SimpleNamespace

import pytest

import ocr_hard_cases as hard
from app.integrations.ocr.classification import passport_plan
from app.services import document_extraction as subject
from test_document_extraction import pdf
from test_ocr_classification import AADHAAR, AADHAAR_BACK, CV, INDIA_PASSPORT_DATA, PASSPORT_ADDRESS
from test_veris_handoff import unreadable, world  # noqa: F401  (the fixture)

NEIGHBOUR = "Observations. Nothing is printed on this page other than this synthetic placeholder sentence for the test."


def data_page(n: int = 1, *, bad_composite: bool = False) -> str:
    """A passport data page with a machine-readable zone (valid check digits unless asked otherwise)."""
    line1, line2, _ = hard.valid_mrz(n)
    if bad_composite:
        line2 = line2[:-1] + str((int(line2[-1]) + 1) % 10)
    return f"{INDIA_PASSPORT_DATA}\n{line1}\n{line2}"


@pytest.fixture(autouse=True)
def reader_agrees_with_the_local_zone(world):
    """The fake reader reads the synthetic zone the way the local check does (so no cross-check conflict sends the file to review)."""
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2], "all_check_digits_valid": True}
    world.veris.build = lambda mode, index, texts: {"mrz": mrz} if mode == "passport" else {}


def sent_pages(state: dict, kind: str = "passport") -> list[int]:
    return state["provider_jobs"][kind]["selected_pages"]


# ------------------------------------------------------------------------------ the plan itself


def test_the_plan_drops_inherited_neighbours_and_keeps_every_page_when_none_has_evidence_of_its_own():
    own, back = data_page(1), PASSPORT_ADDRESS
    plan = passport_plan([(1, NEIGHBOUR, True, 0.4), (2, own, False, 0.5), (3, back, False, 0.5), (4, NEIGHBOUR, True, 0.6)])
    assert plan.pages == [2, 3] and plan.ranked == [2, 3] and plan.duplicates == []
    assert passport_plan([(1, NEIGHBOUR, True, 0.4), (2, NEIGHBOUR, True, 0.4)]) is None
    # A single caption of the passport is evidence of its own even where the page took its kind from the bundle.
    assert passport_plan([(5, "Old passport No. NA\nEmigration check required " * 3, True, 0.5)]).pages == [5]


def test_two_scans_of_one_data_page_keep_the_better_one_and_name_the_other():
    plan = passport_plan([(1, data_page(1, bad_composite=True), False, 0.9), (2, data_page(1), False, 0.5)])
    assert plan.pages == [2] and plan.duplicates == [{"page": 1, "of": 2}] and plan.ranked == [2, 1]
    equal = passport_plan([(1, data_page(1), False, 0.5), (2, data_page(1), False, 0.7)])  # same evidence: the clearer read
    assert equal.pages == [2] and equal.duplicates == [{"page": 1, "of": 2}]
    other = passport_plan([(1, data_page(1), False, 0.5), (2, data_page(2), False, 0.7)])  # different numbers: both stay
    assert other.pages == [1, 2] and other.duplicates == []


# ------------------------------------------------------------------------------ the job


@pytest.mark.parametrize("early", [True, False])
def test_inherited_neighbours_are_not_sent_and_early_and_final_plans_agree(world, early):
    world.settings.veris_early_dispatch = early
    output = world.extract(pdf(NEIGHBOUR, data_page(1), NEIGHBOUR))

    pages = world.state["inspection"]["pages"]
    assert [p["inherited"] for p in pages] == [True, False, True]  # (the neighbours do take the passport kind)
    assert output["status"] == "complete"
    assert len(world.veris.submissions) == 1 and len(world.veris.submissions[0]["texts"]) == 1
    assert hard.valid_mrz(1)[2] in world.veris.submissions[0]["texts"][0]
    assert set(world.state["provider_jobs"]) == {"passport"} and sent_pages(world.state) == [2]  # no "passport+"
    assert output["coverage"]["pages"]["sent"] == [2]
    reasons = {p["page"]: p["reason"] for p in output["coverage"]["pages"]["not_sent"]}
    assert set(reasons) == {1, 3} and all("no passport details of its own" in r for r in reasons.values())
    assert all(not p["blocking"] for p in output["coverage"]["pages"]["not_sent"])


def test_the_key_is_the_same_whether_the_passport_was_sent_early_or_after_the_whole_read(world):
    keys = []
    for early in (True, False):
        world.settings.veris_early_dispatch = early
        world.veris.submissions.clear()
        world.state.clear()
        world.extract(pdf(CV, data_page(1), NEIGHBOUR))
        keys.append([s["key"] for s in world.veris.submissions if s["mode"] == "passport"])
    assert keys[0] == keys[1] and len(keys[0]) == 1


def test_a_data_page_scanned_twice_is_sent_once_and_the_copy_is_recorded(world):
    output = world.extract(pdf(data_page(1, bad_composite=True), data_page(1)))

    assert sent_pages(world.state) == [2] and len(world.veris.submissions) == 1
    assert world.state["passport_plan"]["duplicates"] == [{"page": 1, "of": 2}]
    reasons = {p["page"]: p["reason"] for p in output["coverage"]["pages"]["not_sent"]}
    assert reasons == {1: "Duplicate of page 2; the copy with the better reading was sent."}
    assert output["status"] == "complete"


# ------------------------------------------------------------------------------ after a failure


def passport_submissions(world) -> list[dict]:
    return [s for s in world.veris.submissions if s["mode"] == "passport"]


def test_a_passport_of_several_pages_is_one_job_per_page_strongest_first_and_a_failed_page_is_retried_alone(world):
    world.settings.veris_parallel_jobs = 1
    world.veris.script[("passport", 0)] = "failed"  # the strongest page (the one with the valid zone) fails alone
    world.veris.errors[("passport", 0)] = unreadable(False)
    data = pdf(CV, PASSPORT_ADDRESS, data_page(1))  # the data page is page 3: it ranks first although it comes later
    state: dict = {}

    first = world.extract(data, state=state)

    sent = passport_submissions(world)
    assert [len(s["texts"]) for s in sent] == [1, 1] and hard.valid_mrz(1)[2] in sent[0]["texts"][0]  # one page per job, strongest first
    assert first["status"] == "needs_review" and set(first["extractions"]) == {"resume"}
    assert sent_pages(state) == [2, 3] and [c["selected_pages"] for c in state["provider_jobs"]["passport"]["chunks"]] == [[3], [2]]

    assert subject.request_extraction_retry(state) == ["passport"]  # only the page that failed
    output = world.extract(data, state=state)

    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport"}
    sent = passport_submissions(world)
    assert len(sent) == 3 and hard.valid_mrz(1)[2] in sent[2]["texts"][0]  # the failed page alone, under a new key
    assert len({s["key"] for s in sent}) == 3 and [s["mode"] for s in world.veris.submissions].count("resume") == 1
    assert output["extractions"]["passport"]["coverage"]["pages_sent"] == [2, 3]


def test_a_single_page_that_failed_falls_back_to_its_duplicate_scan(world):
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = unreadable(False)
    data = pdf(CV, data_page(1), data_page(1, bad_composite=True))
    state: dict = {}
    world.extract(data, state=state)
    assert sent_pages(state) == [2] and state["passport_plan"]["ranked"] == [2, 3]

    assert subject.request_extraction_retry(state) == ["passport"]
    output = world.extract(data, state=state)
    assert sent_pages(state) == [3] and output["status"] == "complete"


def test_retry_clears_a_stale_late_part_and_records_the_pages_the_reader_failed_on():
    state = {
        "provider_jobs": {
            "passport": {"status": "failed", "selected_pages": [8, 9], "idempotency_key": "a"},
            "passport+": {"status": "succeeded", "selected_pages": [10, 11, 12], "idempotency_key": "b", "result": {}},
            "resume": {"status": "succeeded", "selected_pages": [1], "idempotency_key": "c", "result": {}},
        },
    }
    assert subject.request_extraction_retry(state) == ["passport"]
    assert set(state["provider_jobs"]) == {"resume"}
    assert state["passport_plan"]["tried"] == [[8, 9, 10, 11, 12]]
    assert [r["selected_pages"] for r in state["provider_job_history"]["passport+"]] == [[10, 11, 12]]
    assert state["provider_generations"] == {"passport": 1}


def test_a_late_part_that_failed_alone_is_still_restarted_alone():
    state = {"provider_jobs": {
        "passport": {"status": "succeeded", "selected_pages": [2], "idempotency_key": "a", "result": {}},
        "passport+": {"status": "failed", "selected_pages": [3], "idempotency_key": "b"},
    }}
    assert subject.request_extraction_retry(state) == ["passport+"]
    assert set(state["provider_jobs"]) == {"passport"} and "passport_plan" not in state


# ------------------------------------------------------------------------------ a further scan of the data page


PRINTED_DATA_PAGE = INDIA_PASSPORT_DATA  # the details page again, scanned too badly for its zone to be found


def read_in_full(world):
    """The fake reader reads number, birth date and name from whichever page it is given."""
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2],
           "date_of_birth": "1990-01-01", "all_check_digits_valid": True}
    world.veris.build = lambda mode, index, texts: {"mrz": mrz} if mode == "passport" else {}


def test_a_second_scan_of_the_data_page_is_not_paid_for_once_the_first_gave_number_birth_date_and_name(world):
    read_in_full(world)
    data = pdf(CV, data_page(1), PRINTED_DATA_PAGE)
    state: dict = {}

    output = world.extract(data, state=state)

    assert output["status"] == "complete"
    sent = passport_submissions(world)
    assert len(sent) == 1 and hard.valid_mrz(1)[2] in sent[0]["texts"][0]  # the strong page only
    assert state["passport_skipped"] == [{"page": 3, "of": 2}] and sent_pages(state) == [2]
    reasons = {p["page"]: (p["reason"], p["blocking"]) for p in output["coverage"]["pages"]["not_sent"]}
    assert reasons == {3: ("Not needed: details already read from page 2.", False)}
    assert output["coverage"]["pages"]["sent"] == [1, 2] and output["extractions"]["passport"]["selected_pages"] == [2]

    again = world.extract(data, state=deepcopy(world.saved[-1]))  # a later visit plans the same pages and pays nothing
    assert again["status"] == "complete" and len(passport_submissions(world)) == 1


def test_the_second_scan_is_read_when_the_first_result_lacks_the_birth_date(world):
    data = pdf(CV, data_page(1), PRINTED_DATA_PAGE)  # (the default fake result has no birth date)
    output = world.extract(data)
    assert output["status"] == "complete" and len(passport_submissions(world)) == 2
    assert "passport_skipped" not in world.state


def test_the_address_page_is_always_read_even_when_the_data_page_gave_everything(world):
    read_in_full(world)
    output = world.extract(pdf(CV, data_page(1), PASSPORT_ADDRESS))
    assert output["status"] == "complete" and len(passport_submissions(world)) == 2
    assert "passport_skipped" not in world.state


def test_a_second_scan_waits_while_the_first_is_still_running_and_is_skipped_when_it_is_in(world):
    read_in_full(world)
    world.veris.script[("passport", 0)] = "running"
    data = pdf(CV, data_page(1), PRINTED_DATA_PAGE)
    state: dict = {}
    with pytest.raises(subject.ExtractionPending):
        world.extract(data, state=state)
    assert len(passport_submissions(world)) == 1  # the second scan is held back, not sent beside it

    world.veris.script.clear()
    output = world.extract(data, state=deepcopy(world.saved[-1]))
    assert output["status"] == "complete" and len(passport_submissions(world)) == 1
    assert world.saved[-1]["passport_skipped"] == [{"page": 3, "of": 2}]


# ------------------------------------------------------------------------------ the reader's resource limit


def too_heavy(retryable: bool = False, code: str = "pdf_resource_limit"):
    return SimpleNamespace(code=code, message="Synthetic reason", retryable=retryable)


def test_a_multi_page_passport_job_that_is_too_heavy_is_sent_again_page_by_page_in_the_same_pass(world, caplog):
    caplog.set_level(logging.INFO, logger=subject.log.name)
    world.settings.veris_passport_one_page = False  # the old way: one job for the whole passport
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = too_heavy()
    data = pdf(CV, data_page(1), PASSPORT_ADDRESS)
    state: dict = {}

    output = world.extract(data, state=state)

    sent = passport_submissions(world)
    assert [len(s["texts"]) for s in sent] == [2, 1, 1]  # the refused job, then each page alone, in page order
    assert hard.valid_mrz(1)[2] in sent[1]["texts"][0] and "Old Passport" in sent[2]["texts"][0]
    assert len({s["key"] for s in sent}) == 3
    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport"}
    record = state["provider_jobs"]["passport"]
    assert [c["selected_pages"] for c in record["chunks"]] == [[2], [3]] and [c["positions"] for c in record["chunks"]] == [[1], [2]]
    assert state["provider_job_history"]["passport"][0]["provider_error"]["code"] == "pdf_resource_limit"
    assert [p["page_number"] for p in output["extractions"]["passport"]["raw"]["pages"]] == [1, 2]  # merged, per-page results kept
    assert [s["mode"] for s in world.veris.submissions].count("resume") == 1  # nothing else was paid again
    assert "the reader refused 2 pages as too heavy (code=pdf_resource_limit); sending them one page at a time" in caplog.text


def test_other_limit_codes_split_too_but_a_retryable_failure_does_not(world):
    world.settings.veris_passport_one_page = False
    data = pdf(CV, data_page(1), PASSPORT_ADDRESS)
    for code in ("file_too_large", "page_limit_exceeded", "resource_exhausted"):
        world.veris.submissions.clear()
        world.veris.script = {("passport", 0): "failed"}
        world.veris.errors = {("passport", 0): too_heavy(code=code)}
        assert world.extract(data, state={})["status"] == "complete"
        assert [len(s["texts"]) for s in passport_submissions(world)] == [2, 1, 1]
    world.veris.submissions.clear()
    world.veris.script = {("passport", 0): "failed"}
    world.veris.errors = {("passport", 0): too_heavy(retryable=True)}
    with pytest.raises(subject.ExtractionFailure):  # a retryable failure is left to the automatic retry as before
        world.extract(data, state={})
    assert len(passport_submissions(world)) == 1


def test_a_single_page_that_is_too_heavy_is_terminal_and_degrades(world):
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = too_heavy()
    state: dict = {}

    output = world.extract(pdf(CV, data_page(1)), state=state)

    assert len(passport_submissions(world)) == 1  # nothing smaller to send
    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume"}
    assert any("passport" in w for w in output["warnings"])
    assert state["provider_jobs"]["passport"]["provider_error"]["code"] == "pdf_resource_limit"


def test_an_aadhaar_front_and_back_that_are_too_heavy_are_read_one_side_at_a_time(world):
    world.veris.script[("aadhaar", 0)] = "failed"
    world.veris.errors[("aadhaar", 0)] = too_heavy()
    output = world.extract(pdf(CV, AADHAAR, AADHAAR_BACK), state={})
    cards = [s for s in world.veris.submissions if s["mode"] == "aadhaar"]
    assert [len(s["texts"]) for s in cards] == [2, 1, 1]
    assert output["status"] == "complete" and "aadhaar" in output["extractions"]


def test_a_page_that_fails_after_the_split_is_terminal_for_that_page_only(world):
    world.settings.veris_passport_one_page = False
    world.veris.script = {("passport", 0): "failed", ("passport", 2): "failed"}
    world.veris.errors = {("passport", 0): too_heavy(), ("passport", 2): unreadable(False)}
    output = world.extract(pdf(CV, data_page(1), PASSPORT_ADDRESS), state={})
    assert [len(s["texts"]) for s in passport_submissions(world)] == [2, 1, 1]  # no third round of splitting
    assert output["status"] == "needs_review" and set(output["extractions"]) == {"resume"}


def test_a_prepared_job_above_the_byte_limit_is_split_before_it_is_sent(world):
    world.settings.veris_passport_one_page = False
    world.settings.veris_job_max_bytes = 100_000  # (a typed two-page file is about 2 KB: raise the limit's pressure)
    world.monkeypatch.setattr(subject, "prepare_job", lambda content, rotations=None, **o: SimpleNamespace(
        content=content + b" " * 150_000, pages=2, original_bytes=len(content), rotated=0, recompressed=0, fallback=False))
    output = world.extract(pdf(CV, data_page(1), PASSPORT_ADDRESS), state={})
    # Nothing was refused: the two-page passport job never left; its pages went one by one, and the résumé (one page) went whole.
    assert [len(s["texts"]) for s in passport_submissions(world)] == [1, 1]
    assert output["status"] == "complete"


# ------------------------------------------------------------------------------ the reader's duplicate flag


def test_the_duplicate_flag_is_kept_on_the_job_and_logged(world, caplog):
    caplog.set_level(logging.INFO, logger=subject.log.name)
    world.veris.duplicates = {("passport", 0)}
    state: dict = {}
    world.extract(pdf(CV, data_page(1)), state=state)
    assert state["provider_jobs"]["passport"]["duplicate"] is True and state["provider_jobs"]["resume"]["duplicate"] is False
    text = " | ".join(r.getMessage() for r in caplog.records)
    assert "passport: done in" in text and "(job pass" in text and "the reader already had it" in text
    assert text.count("the reader already had it") == 1  # only the passport job was a duplicate
    assert len(world.veris.submissions) == 2  # a duplicate that worked is simply used


def test_a_duplicate_job_that_is_already_failed_is_sent_again_once_under_a_new_key(world):
    world.veris.duplicates = {("passport", 0)}
    world.veris.script[("passport", 0)] = "failed"
    world.veris.errors[("passport", 0)] = unreadable(False)
    state: dict = {}

    output = world.extract(pdf(CV, data_page(1)), state=state)

    passport = [s for s in world.veris.submissions if s["mode"] == "passport"]
    assert len(passport) == 2 and passport[0]["key"] != passport[1]["key"]
    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport"}
    assert state["provider_generations"]["passport"] == 1
    earlier = state["provider_job_history"]["passport"]
    assert len(earlier) == 1 and earlier[0]["duplicate"] is True and earlier[0]["status"] == "failed"
    assert [s["mode"] for s in world.veris.submissions].count("resume") == 1  # nothing else is paid again


def test_a_duplicate_that_fails_again_is_not_sent_a_third_time(world):
    world.veris.duplicates = {("passport", 0), ("passport", 1), ("passport", 2)}
    for number in range(3):
        world.veris.script[("passport", number)] = "failed"
        world.veris.errors[("passport", number)] = unreadable(False)
    state: dict = {}

    output = world.extract(pdf(CV, data_page(1)), state=state)
    again = world.extract(pdf(CV, data_page(1)), state=deepcopy(state))

    assert [s["mode"] for s in world.veris.submissions].count("passport") == 2  # the original and one new key, never a loop
    assert output["status"] == again["status"] == "needs_review" and set(output["extractions"]) == {"resume"}


def test_a_key_the_reader_already_has_with_other_file_contents_is_sent_again_once_under_a_new_key(world):
    from veriis import VerisOCRError

    # 409: the same upload key was used before with other bytes (the file is now sent as a prepared copy).
    world.veris.submit_faults = [VerisOCRError("conflict", status=409, code="idempotency_conflict")]
    state: dict = {}

    output = world.extract(pdf(CV, data_page(1)), state=state)

    assert output["status"] == "complete" and set(output["extractions"]) == {"resume", "passport"}
    sent = [(s["mode"], s["key"]) for s in world.veris.submissions]
    assert len(sent) == len({key for _, key in sent}) == 2  # every kind sent once, under its own key
    kind = next(iter(state["provider_job_history"]))
    assert state["provider_job_history"][kind][0]["key_conflict"] is True
    assert state["provider_generations"][kind] == 1


def test_a_saved_job_that_was_never_accepted_takes_the_current_key_instead_of_failing():
    from app.services import document_extraction as extraction

    record = {"selected_pages": [1], "idempotency_key": "crm-from-an-older-key-format", "status": "submitting", "key_conflict": True}
    units = extraction._check_cached("passport", [1], record, "digest", 0)

    assert record["idempotency_key"] == extraction._request_key("digest", "passport", [1], 0)
    assert "key_conflict" not in record and len(units) == 1
    accepted = dict(record, job_id="job-1", idempotency_key="crm-other")
    with pytest.raises(extraction.ExtractionFailure):
        extraction._check_cached("passport", [1], accepted, "digest", 0)
