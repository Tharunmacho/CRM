"""A passport or Aadhaar page the local reader cannot read, but that still shows cues of one (a suspected identity page).

The page is sent to the paid reader as that kind, filed with the kind when the reader confirms it with the number, and filed under
Other documents ("Looked like a passport but could not be read.") otherwise. The cost is bounded: a few pages per file, never a
page that is confidently another document, never a second data page when a confidently read one exists. All names and numbers
are invented; Veris is a fake (no network, no key).
"""

from copy import deepcopy

import pytest

from app.integrations.ocr import classification as cls
from app.services import document_extraction as subject
from ocr_synthetic import composite_pdf
from test_combined_documents import combined  # noqa: F401  (the fixture: extract_attachment with a fake reader)

# ---- invented page texts ------------------------------------------------------------------------------------------------

DATA_PAGE = """REPUBLICOFINDIA
Type P  Nationality INDIAN
Surnarne EXAMPLE
Given Name(s) SAMPLE
Dateof Birth 01/01/1990
Placeoflssue EXAMPLEPUR
Dateoflssue 02/02/2020
EXAMPLE<<SAMPLE<<<<<<<<<<<
"""
ADDRESS_PAGE = """Parent Example   Spouse Example
12, EXAMPLE ROAD, EXAMPLEPUR, EXAMPLE DISTRICT
PIN:530001, ANDHRA PRADESH, INDIA
"""
# The same data page after the zone's own first line survived: the page is already a passport to the local check.
READ_PAGE = "REPUBLIC OF INDIA\nPassport No. Z1234567\nSurname EXAMPLE\nGiven Name(s) SAMPLE\nDate of Birth 01/01/1990\nP<INDEXAMPLE<<SAMPLE<<<<<<<<<<<<<<<<<<<<<\nZ1234567<4IND9001015M3001012<<<<<<<<<<<<<<04\n"
RESUME = ("CURRICULUM VITAE\nSample Example\nEmail: sample@example.org  Phone: +91 90000 00000\nWORK EXPERIENCE\nSite Engineer 2015 - 2020\n"
          "Managed installation and maintenance of equipment on site.\nEDUCATION\nB.Tech Mechanical\nSKILLS\nWelding\n")
QATAR_ID = "State of Qatar Resident Identity Card\nName SAMPLE EXAMPLE\nID No. 29000000000\nPassport No. Z1234567\nDate of Birth 1990-01-01\nNationality India\nExpiry 2030-01-01\n"
ID_CARD_ZONE = "IDBHR9111052553<<<<<<<<<<<<<<<\n9001015M3001012IND<<<<<<<<<<<4\nEXAMPLE<<SAMPLE<<<<<<<<<<<<<<<<<\n"
CERTIFICATE = "This is to certify that Sample Example has completed the training course on site safety with distinction. Date of Birth 01/01/1990 Nationality Indian"
VISA = "STATE OF KUWAIT Electronic Visa Entry permit Number of entries Single entry Valid until 01/01/2030 Nationality INDIA Date of Issue 01/01/2029"
AADHAAR_BACK_LOST = "Aadhaar\nSample Example\nDOB: 01/01/1990\nMALE\n1234 5678 9012\n"


def suspects(texts, results=None, **limits):
    return [(s.page, s.kind) for s in cls.suspected_identity_pages(texts, results, **limits)]


# ---- the rule, on page texts --------------------------------------------------------------------------------------------

def test_a_damaged_passport_data_page_is_suspected_and_so_is_its_address_page():
    texts = [RESUME, DATA_PAGE, ADDRESS_PAGE]
    assert [cls.classify_page(t).document_type for t in texts] == ["resume", "unknown", "unknown"]
    found = cls.suspected_identity_pages(texts)
    assert [(s.page, s.kind) for s in found] == [(2, "passport"), (3, "passport")]
    assert "republic of india" in found[0].cues and "address page" in found[1].cues


def test_the_address_page_comes_before_or_after_its_data_page():
    assert suspects([ADDRESS_PAGE, DATA_PAGE]) == [(1, "passport"), (2, "passport")]


def test_an_address_page_alone_is_not_suspected():
    assert suspects([RESUME, ADDRESS_PAGE]) == []
    assert suspects([RESUME, ADDRESS_PAGE, RESUME]) == []


def test_a_page_the_local_check_already_reads_as_a_passport_covers_the_file():
    texts = [RESUME, READ_PAGE, DATA_PAGE, ADDRESS_PAGE]
    assert cls.classify_page(READ_PAGE).document_type == "passport"
    assert suspects(texts) == []  # a second damaged copy and the address page cost nothing


@pytest.mark.parametrize("text", [RESUME, CERTIFICATE, QATAR_ID, ID_CARD_ZONE, VISA, "Date of Birth 01/01/1990\nNationality Indian\nSigned: Sample"])
def test_a_page_that_is_something_else_is_never_suspected(text):
    assert suspects([text, text]) == []
    assert suspects([DATA_PAGE.split("\n")[0], text]) == [] or text == ADDRESS_PAGE


def test_an_address_beside_a_cv_page_with_personal_details_is_not_taken():
    cv_tail = "NAME: SAMPLE EXAMPLE  DATE OF BIRTH: 01.01.1990  WORK: COOK\nFather: Parent Example\nPIN:530001, ANDHRA PRADESH"
    assert suspects([DATA_PAGE, cv_tail]) == [(1, "passport")]


def test_at_most_four_passport_pages_and_two_aadhaar_pages_are_suspected():
    assert suspects([DATA_PAGE] * 7) == [(1, "passport"), (2, "passport"), (3, "passport"), (4, "passport")]
    assert suspects([DATA_PAGE] * 7, passport_limit=2) == [(1, "passport"), (2, "passport")]
    assert suspects([AADHAAR_BACK_LOST] * 5) == [(1, "aadhaar"), (2, "aadhaar")]


def test_a_card_page_is_suspected_for_the_number_with_its_captions_and_never_a_passport_page():
    assert suspects([RESUME, AADHAAR_BACK_LOST]) == [(2, "aadhaar")]
    assert suspects(["Account 1234 5678 9012 balance 20.00"]) == []
    # A page showing the passport's zone filler is a badly read passport, never a card.
    assert suspects([DATA_PAGE + "\n1234 5678 9012\nMALE"]) == [(1, "passport")]


def test_unreadable_or_empty_pages_are_never_suspected():
    assert suspects(["", "  ", "xq zv 7 ,,"]) == []


# ---- what the reader is sent --------------------------------------------------------------------------------------------

def passport_with_number(mode, number, texts):
    return ({"mrz": {"passport_number": "Z1234567", "surname": "EXAMPLE", "given_names": "SAMPLE", "all_check_digits_valid": True}}
            if mode == "passport" else {"aadhaar": {}})


def name_only(mode, number, texts):
    return {"mrz": {"surname": "EXAMPLE", "given_names": "SAMPLE"}} if mode == "passport" else {"aadhaar": {}}


def test_a_suspected_page_is_sent_to_the_passport_mode_only_and_filed_as_the_passport_when_the_reader_confirms_it(combined):
    combined.veris.build = passport_with_number

    output = combined.extract(composite_pdf(["resume", "certificate", "passport_damaged"], form="native"))

    assert combined.plan() == {"resume": [1], "passport": [3]}
    assert [s["mode"] for s in combined.veris.submissions].count("aadhaar") == 0
    assert output["extractions"]["passport"]["selected_pages"] == [3]
    assert output["inspection"]["passport_pages"] == [3] and output["inspection"]["unknown_pages"] == []
    assert output["inspection"]["pages"][2]["reason"] == "Confirmed as a passport by the document reader."
    assert combined.state["suspected_pages"] == {"passport": [3]}
    assert output["status"] == "needs_review"  # a person checks a page only the reader could identify


def test_the_data_page_and_its_address_page_are_sent_together_as_one_passport_job(combined):
    combined.veris.build = passport_with_number

    output = combined.extract(composite_pdf(["resume", "passport_damaged", "passport_back_damaged"], form="native"))

    assert combined.plan() == {"resume": [1], "passport": [2, 3]}
    passport_jobs = [s for s in combined.veris.submissions if s["mode"] == "passport"]
    assert len(passport_jobs) == 1 and len(passport_jobs[0]["texts"]) == 2  # a pair is one job, as for any two-page passport
    assert output["inspection"]["passport_pages"] == [2, 3]
    assert output["extractions"]["passport"]["selected_pages"] == [2, 3]


def test_a_suspected_page_the_reader_cannot_confirm_goes_to_other_documents_with_the_reason(combined):
    combined.veris.build = name_only  # a name and no passport number is not a confirmation

    output = combined.extract(composite_pdf(["resume", "certificate", "passport_damaged"], form="native"))

    assert combined.plan() == {"resume": [1], "passport": [3]}
    assert set(output["extractions"]) == {"resume"}  # nothing is filled from a reading that did not confirm the page
    assert output["inspection"]["passport_pages"] == [] and output["inspection"]["unknown_pages"] == [3]
    assert output["inspection"]["pages"][2]["reason"] == "Looked like a passport but could not be read."
    assert any("Page 3 looked like a passport but could not be read; it was filed under Other documents" in w for w in output["warnings"])
    assert output["status"] == "needs_review"


def test_when_the_reader_finds_nothing_at_all_the_page_is_not_a_passport(combined):
    combined.veris.build = lambda mode, number, texts: {"mrz": {}, "fields": []} if mode == "passport" else {"aadhaar": {}}

    output = combined.extract(composite_pdf(["resume", "passport_damaged"], form="native"))

    assert set(output["extractions"]) == {"resume"} and output["inspection"]["passport_pages"] == []
    assert output["inspection"]["pages"][1]["reason"] == "Looked like a passport but could not be read."


def test_a_file_with_nothing_but_a_suspected_page_is_still_sent(combined):
    combined.veris.build = passport_with_number

    output = combined.extract(composite_pdf(["passport_damaged"], form="native"))

    assert combined.plan() == {"passport": [1]} and set(output["extractions"]) == {"passport"}
    assert output["inspection"]["passport_pages"] == [1]


def test_a_confidently_read_passport_means_no_suspected_page_is_sent(combined):
    output = combined.extract(composite_pdf(["resume", "passport", "passport_damaged", "passport_back_damaged"], form="native"))

    assert combined.plan() == {"resume": [1], "passport": [2]}
    assert output["coverage"]["pages"]["probed"] == {} and combined.state["suspected_pages"] == {}


def test_a_page_of_another_document_is_never_sent_as_a_suspected_passport(combined):
    output = combined.extract(composite_pdf(["resume", "certificate", "unrelated", "aadhaar"], form="native"))

    assert combined.plan() == {"resume": [1], "aadhaar": [4]} and output["coverage"]["pages"]["probed"] == {}


def test_no_more_than_four_suspected_pages_are_paid_for(combined):
    combined.veris.build = passport_with_number

    output = combined.extract(composite_pdf(["resume"] + ["passport_damaged"] * 6, form="native"))

    jobs = [s for s in combined.veris.submissions if s["mode"] == "passport"]
    assert len(jobs) == 4 and all(len(s["texts"]) == 1 for s in jobs)  # more than two pages: one page a job, never more than four
    assert len(combined.state["suspected_pages"]["passport"]) == 4
    assert output["extractions"]["passport"]["selected_pages"] == combined.state["suspected_pages"]["passport"]


def test_the_plan_is_kept_so_a_retry_sends_the_same_pages_and_nothing_twice(combined):
    combined.veris.build = passport_with_number
    data = composite_pdf(["resume", "passport_damaged", "passport_back_damaged"], form="native")
    combined.veris.script[("passport", 0)] = "running"
    with pytest.raises(subject.ExtractionPending):
        combined.extract(data)
    assert combined.saved[-1]["probe_plan"] == {"passport": [2, 3]} and combined.saved[-1]["suspected_pages"] == {"passport": [2, 3]}

    combined.veris.script.clear()
    first = [s["key"] for s in combined.veris.submissions if s["mode"] == "passport"]
    output = combined.extract(data, state=deepcopy(combined.saved[-1]))

    keys = [s["key"] for s in combined.veris.submissions if s["mode"] == "passport"]
    assert keys[:len(first)] == first and len(keys) == len(set(keys))
    assert output["extractions"]["passport"]["selected_pages"] == [2, 3]


def test_a_probe_with_only_a_weak_identity_word_is_no_longer_sent(combined, monkeypatch):
    monkeypatch.setattr(cls, "_IDENTITY_HINT", cls.re.compile(r"garden committee", cls.re.I))

    output = combined.extract(composite_pdf(["resume", "passport", "unrelated"], form="native"))

    assert combined.plan() == {"resume": [1], "passport": [2]} and output["coverage"]["pages"]["probed"] == {}
