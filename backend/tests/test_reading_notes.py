"""Plain-English reading notes: what a recruiter sees instead of the reading's raw technical warnings."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.services.reading_notes import (
    CHECK_ORIGINAL_NOTE, PAGES_NOTE, PASSPORT_GENERAL_NOTE, plain_issues, plain_notes,
)

IDENTITY = "Different candidate identities were found. Open Review people and pages before saving."
IDENTITY_2 = "This document contains different candidate identities. Open Review people and pages before saving any candidate."

# One resume + passport + Aadhaar upload, exactly as the reading produced it.
THIRTEEN = [
    "structured LLM structured complete resume — validated before persistence",
    "MRZ region detected but its structure could not be parsed",
    "MRZ region detected but yielded no data — extracting from visible bio fields",
    "back-page vision LLM returned no usable fields — image quality may be too low or the page may not be a passport back page",
    "The passport details were read from the printed page, not the coded lines at the bottom of it, so they may be less accurate. "
    "Compare every value against the scan.",
    "The passport's check digits do not add up, so the number or a date may be misread. Compare them against the scan.",
    "The address was reread from an enlarged local crop; verify it against the card.",
    "The passport read locally and by the document reader disagree on date_of_expiry. Compare them with the original before relying on either.",
    "Different parts of the passport gave different values for mrz.expiry_date, mrz.date_of_issue. The first was kept; check them against the original.",
    "The document reader returned details this app has not seen before. They are saved with the document and listed under Not on the form.",
    "We could not tell what kind of pages 6 are. Check them in the saved original.",
    "Pages 2, 3, 4, 5, 9, 10, 12, 13 have no identifying content and were assigned to the adjoining document "
    "(for example the back of a card). Check them.",
    "Pages 6 could not be read or classified and were not extracted. Review them in the stored original.",
]


def test_the_thirteen_line_example_collapses_to_two_plain_notes():
    notes = plain_notes(THIRTEEN)
    assert notes == [PASSPORT_GENERAL_NOTE, PAGES_NOTE]
    assert len(notes) <= 3


@pytest.mark.parametrize("kind,expected", [
    ("passport", [PASSPORT_GENERAL_NOTE, PAGES_NOTE]),
    ("resume", [PAGES_NOTE]),
    ("aadhaar", [PAGES_NOTE]),
])
def test_per_kind_notes_are_not_a_copy_of_the_whole_list(kind, expected):
    assert plain_notes(THIRTEEN, kind=kind) == expected


@pytest.mark.parametrize("line", [
    THIRTEEN[0], THIRTEEN[1], THIRTEEN[2], THIRTEEN[3], THIRTEEN[4], THIRTEEN[6], THIRTEEN[9],
])
def test_pure_implementation_remarks_are_dropped(line):
    assert plain_notes([line]) == []


def test_dropped_remarks_are_logged_at_debug_on_the_reading_logger(caplog):
    with caplog.at_level(logging.DEBUG, logger="crm.reading"):
        plain_notes([THIRTEEN[1], THIRTEEN[6], THIRTEEN[11]])
    messages = [r.getMessage() for r in caplog.records if r.name == "crm.reading" and r.levelno == logging.DEBUG]
    assert any("MRZ region detected" in m for m in messages)
    assert any("enlarged local crop" in m for m in messages)
    assert any("assigned to the adjoining document" in m for m in messages)


def test_failed_check_digits_name_the_number_and_dates():
    assert plain_notes([THIRTEEN[5]]) == ["Passport: please check the number and dates against the passport."]


def test_new_mapping_wording_is_recognised_too():
    from app.integrations.ocr.mapping import passport_fields
    from test_ocr_mapping import passport
    _, warnings = passport_fields(passport({"passport_number": "M1234567", "all_check_digits_valid": False}))
    assert plain_notes(warnings) == [PASSPORT_GENERAL_NOTE]


def test_a_cross_check_disagreement_names_the_field_in_words():
    assert plain_notes([THIRTEEN[7]]) == ["Passport: please check the date of expiry against the passport."]


def test_parts_that_differ_name_each_field_once_without_the_internal_prefix():
    assert plain_notes([THIRTEEN[8]]) == ["Passport: please check the date of expiry and date of issue against the passport."]


def test_several_passport_messages_become_one_note_with_every_field():
    notes = plain_notes([THIRTEEN[7], THIRTEEN[8],
                         "The passport read locally and by the document reader disagree on passport_number, date_of_birth. Compare them."])
    assert notes == ["Passport: please check the date of expiry, date of issue, number and date of birth against the passport."]


def test_failed_check_digits_win_over_specific_fields_because_they_cover_them():
    assert plain_notes([THIRTEEN[7], THIRTEEN[5]]) == [PASSPORT_GENERAL_NOTE]


def test_unreadable_and_unclassified_pages_make_one_note():
    assert plain_notes([THIRTEEN[10], THIRTEEN[12]]) == [PAGES_NOTE]
    assert plain_notes(["Pages 3, 4 could not be read. Review them in the stored original."]) == [PAGES_NOTE]
    assert plain_notes(["Some pages are unread or unclassified. Review the document before extraction."]) == [PAGES_NOTE]
    assert plain_notes(["Pages 7 could not be identified and were read along with the passport. Check them in the stored original."]) == [PAGES_NOTE]


def test_pages_given_to_the_neighbouring_document_alone_are_not_actionable():
    assert plain_notes([THIRTEEN[11]]) == []


def test_identity_conflicts_are_kept_verbatim_and_gate_saving():
    assert plain_notes(THIRTEEN + [IDENTITY]) == [PASSPORT_GENERAL_NOTE, PAGES_NOTE, IDENTITY]
    assert plain_notes([IDENTITY_2, IDENTITY_2]) == [IDENTITY_2]
    # on every kind: it is about the whole file
    for kind in ("resume", "passport", "aadhaar"):
        assert IDENTITY in plain_notes([IDENTITY], kind=kind)
    assert plain_notes(["This file seems to hold more than one person."]) == ["This file seems to hold more than one person."]


@pytest.mark.parametrize("line", [
    "Back side is blurred",
    "The passport pages could not be read. Enter the passport details by hand, or upload a clearer copy.",
    "The Aadhaar number does not look right. Compare it with the original document.",
    "Only a birth year was read from Aadhaar. Enter the full date of birth manually.",
    "No résumé, passport or Aadhaar pages were identified.",
    "Something the reader said that we have never seen",
    "5 of 35 work history rows in the resume do not fit the form. They are saved with the document; read them there.",
    "Different parts of the resume gave different values for name. The first was kept; check them against the original.",
])
def test_unrecognised_warnings_are_never_swallowed(line):
    assert plain_notes([line]) == [line]


def test_other_kinds_warnings_are_left_off_a_kind_and_generic_ones_stay():
    raw = ["The Aadhaar number does not look right. Compare it with the original document.", "Back side is blurred", PAGES_NOTE]
    assert plain_notes(raw, kind="passport") == ["Back side is blurred", PAGES_NOTE]
    assert plain_notes(raw, kind="aadhaar") == raw


def test_notes_are_deduplicated_ordered_and_stable_when_applied_twice():
    raw = ["b note", "a note", "b note", "", "  ", None, 5]
    assert plain_notes(raw) == ["b note", "a note"]
    once = plain_notes(THIRTEEN + [IDENTITY, "Back side is blurred"])
    assert plain_notes(once) == once


def test_empty_inputs():
    assert plain_notes([]) == [] and plain_notes(None) == []


def test_issues_never_become_an_empty_list_when_there_was_a_reason():
    technical = "The document reader returned details this app has not seen before. They are saved with the document."
    assert plain_issues([technical]) == [CHECK_ORIGINAL_NOTE]
    assert plain_issues([]) == []
    assert plain_issues([technical, THIRTEEN[7]]) == ["Passport: please check the date of expiry against the passport."]


# ------------------------------------------------------------------ the boundaries

class _NoCandidateDb:
    """apply_job_output only reads the candidate; with none found it records the outcome on the job."""
    def scalar(self, *_a, **_k):
        return None


def _job(draft_slot="passport"):
    return SimpleNamespace(candidate_id="c1", owner_id="u1", status="needs_review", draft={"applicationForm": {"slot": draft_slot}})


def test_application_form_result_shows_plain_notes_and_keeps_raw_in_the_output():
    from app.services.application_form import apply_job_output
    output = {"warnings": list(THIRTEEN), "extractions": {}, "coverage": {
        "issues": [THIRTEEN[7], THIRTEEN[9]], "notes": [THIRTEEN[9], "Some details the document reader found have no place on the form."]}}
    job = _job()
    apply_job_output(_NoCandidateDb(), job, output)
    result = job.draft["applicationForm"]["result"]
    # no document found: the first plain note is the message
    assert result["status"] == "unreadable" and result["message"] == PASSPORT_GENERAL_NOTE
    assert result["coverage"]["issues"] == ["Passport: please check the date of expiry against the passport."]
    assert result["coverage"]["notes"] == []
    # storage (the job's output that diagnostics read) is untouched
    assert output["warnings"] == THIRTEEN


def test_application_form_falls_back_to_a_sentence_when_every_warning_was_technical():
    from app.services.application_form import apply_job_output
    job = _job("resume")
    apply_job_output(_NoCandidateDb(), job, {"warnings": [THIRTEEN[0], THIRTEEN[1]], "extractions": {}})
    assert job.draft["applicationForm"]["result"]["message"].startswith("We could not find a resume in this file")


def test_application_form_keeps_an_identity_conflict_message():
    from app.services.application_form import apply_job_output
    job = _job()
    apply_job_output(_NoCandidateDb(), job, {"warnings": [THIRTEEN[1], IDENTITY], "extractions": {}})
    assert job.draft["applicationForm"]["result"]["message"] == IDENTITY


def test_manual_scan_results_are_plain_per_kind_and_whole_file():
    from app.services.manual_extraction import _scan_results
    inspection = {"warnings": [THIRTEEN[10], THIRTEEN[11]], "resume_pages": [1], "passport_pages": [2], "aadhaar_pages": [3]}
    output = {"inspection": inspection, "warnings": list(THIRTEEN), "extractions": {}, "status": "needs_review", "coverage": None}
    result = _scan_results(output)
    assert result["warnings"] == [PASSPORT_GENERAL_NOTE, PAGES_NOTE]
    assert result["scans"]["resume"]["warnings"] == [PAGES_NOTE]
    assert result["scans"]["passport"]["warnings"] == [PASSPORT_GENERAL_NOTE, PAGES_NOTE]
    assert result["scans"]["aadhaar"]["warnings"] == [PAGES_NOTE]
    assert result["inspection"]["warnings"] == [PAGES_NOTE]
    assert result["scans"]["passport"]["inspection"]["warnings"] == [PAGES_NOTE]
    # the stored inspection report keeps its raw lines
    assert inspection["warnings"] == [THIRTEEN[10], THIRTEEN[11]]


def test_scan_endpoint_response_is_plain():
    from app.api.routers.ocr import _response
    out = _response({"warnings": [THIRTEEN[11]]}, list(THIRTEEN), "needs_review", kind="passport")
    assert out["warnings"] == [PASSPORT_GENERAL_NOTE, PAGES_NOTE]
    assert out["inspection"]["warnings"] == []
