"""A scan that holds several documents is split at upload by what is on each page (synthetic documents, no database)."""
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest

from app.integrations.ocr import inspection
from app.integrations.ocr.inspection import InspectedPage, Inspection
from ocr_synthetic import composite_pdf

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", tesseract_cmd=None,
                           ocr_languages="eng", agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.services import bulk_candidate_import as bulk


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(bulk, "get_settings", lambda: SETTINGS)   # never the real .env


def _pages(data: bytes) -> int:
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count


def _text(data: bytes) -> str:
    with fitz.open(stream=data, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)


def _by_kind(result) -> dict[str, "bulk.Piece"]:
    return {piece.kinds[0]: piece for piece in result.pieces}


def _assert_nothing_lost(result, count):
    assert result.split or all(not piece.pages for piece in result.pieces)   # not split: the one piece is the whole scan
    assert result.split is False or {number for piece in result.pieces for number in piece.pages} == set(range(1, count + 1))


def test_a_combined_scan_is_split_into_passport_aadhaar_and_the_signed_offer_remainder():
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    result = bulk.split_by_content(scan, "application/pdf", ["signed_offer", "passport", "aadhaar"], "Rashid-signed offer,aadhar,pp.pdf")
    assert result.split and result.note and "split" in result.note
    pieces = _by_kind(result)
    assert set(pieces) == {"passport", "aadhaar", "signed_offer"}
    assert pieces["passport"].pages == [2] and pieces["aadhaar"].pages == [3] and pieces["signed_offer"].pages == [1]
    assert all(_pages(piece.data) == 1 for piece in result.pieces)
    assert "REPUBLIC OF INDIA" in _text(pieces["passport"].data).upper() or "PASSPORT" in _text(pieces["passport"].data).upper()
    assert "aadhaar" not in _text(pieces["passport"].data).lower()
    _assert_nothing_lost(result, 3)


def test_a_file_named_for_one_document_is_stored_as_it_is_without_being_read():
    scan = composite_pdf(["passport"])
    with patch.object(inspection, "inspect_document", side_effect=AssertionError("must not be read")):
        result = bulk.split_by_content(scan, "application/pdf", ["passport"], "Rashid-passport.pdf")
        # a scan is not cut when the name already says what it is; images are one page and kept whole
        assert not result.split and result.pieces[0].data is scan and result.pieces[0].kinds == ["passport"]
        photo = bulk.split_by_content(b"JPEG", "image/jpeg", [], "x.jpg")
        assert not photo.split and photo.pieces[0].data == b"JPEG"
        two = bulk.split_by_content(b"JPEG", "image/png", ["passport", "aadhaar"], "x.png")
        assert not two.split and two.pieces[0].kinds == ["passport", "aadhaar"]


def test_a_scan_that_is_one_document_throughout_is_the_original_bytes_untouched():
    scan = composite_pdf(["passport", "passport"])
    result = bulk.split_by_content(scan, "application/pdf", ["passport", "aadhaar"], "pp,aadhar.pdf")
    # the Aadhaar card was not found on any page: the file stays whole under both names, as before
    assert not result.split and result.pieces[0].data is scan and result.pieces[0].kinds == ["passport", "aadhaar"]


def test_a_failed_or_timed_out_reading_never_fails_the_import_the_file_is_stored_whole():
    scan = composite_pdf(["unrelated", "passport"])
    with patch.object(inspection, "inspect_document", side_effect=inspection.InspectionError("timed out", retryable=True)):
        named = bulk.split_by_content(scan, "application/pdf", ["signed_offer", "passport"], "offer,pp.pdf")
        assert not named.split and named.pieces[0].data is scan and named.pieces[0].kinds == ["signed_offer", "passport"]
        assert named.note and "stored whole" in named.note
        with patch.object(bulk, "classify_content", return_value=[]):
            nameless = bulk.split_by_content(scan, "application/pdf", [], "scan0001.pdf")
        assert not nameless.split and nameless.pieces[0].kinds == ["other"] and nameless.note
        with patch.object(bulk, "classify_content", return_value=["passport"]):
            assert bulk.split_by_content(scan, "application/pdf", [], "scan0001.pdf").pieces[0].kinds == ["passport"]
    broken = bulk.split_by_content(b"not a pdf at all", "application/pdf", ["visa", "medical"], "visa medical.pdf")
    assert not broken.split and broken.pieces[0].data == b"not a pdf at all" and broken.pieces[0].kinds == ["visa", "medical"]


def test_the_reading_is_bounded():
    scan = composite_pdf(["unrelated", "passport"])
    with patch.object(inspection, "inspect_document", wraps=inspection.inspect_document) as read:
        bulk.split_by_content(scan, "application/pdf", ["visa", "passport"], "visa,pp.pdf")
    assert read.call_args.kwargs["max_pages"] == 40 and read.call_args.kwargs["page_timeout"] == 8 \
        and read.call_args.kwargs["document_timeout"] == 120


def test_an_unnamed_scan_is_split_by_content_and_unidentified_pages_become_other_documents():
    scan = composite_pdf(["unrelated", "resume:0", "resume:1", "certificate", "passport", "aadhaar", "aadhaar_back"])
    result = bulk.split_by_content(scan, "application/pdf", [], "scan0001.pdf")
    pieces = _by_kind(result)
    assert result.split and "passport" in pieces and "aadhaar" in pieces and "resume" in pieces
    assert pieces["passport"].pages == [5] and pieces["aadhaar"].pages == [6, 7]       # the back of the card follows its front
    assert {2, 3} <= set(pieces["resume"].pages)
    assert pieces["other"].pages == [1, 4]                                                 # no clue in the name: the rest is "other"
    _assert_nothing_lost(result, 7)


def test_a_page_that_is_both_a_resume_and_a_passport_goes_to_both():
    scan = composite_pdf(["unrelated", "resume:0", "resume:1+passport"])
    result = bulk.split_by_content(scan, "application/pdf", ["signed_offer", "resume", "passport"], "offer,cv,pp.pdf")
    pieces = _by_kind(result)
    assert pieces["passport"].pages == [3] and pieces["resume"].pages == [2, 3]
    assert pieces["signed_offer"].pages == [1]
    _assert_nothing_lost(result, 3)


def test_a_blank_or_photo_page_stays_with_the_identity_document_it_follows():
    with fitz.open() as document:
        document.insert_pdf(fitz.open(stream=composite_pdf(["unrelated", "passport"]), filetype="pdf"))
        document.new_page()                                   # a blank sheet after the passport
        data = document.tobytes()
    result = bulk.split_by_content(data, "application/pdf", ["signed_offer", "passport"], "offer,pp.pdf")
    pieces = _by_kind(result)
    assert pieces["passport"].pages == [2, 3] and pieces["signed_offer"].pages == [1]
    _assert_nothing_lost(result, 3)
    assert _pages(pieces["passport"].data) == 2


def _fake(data: bytes, kinds: list[tuple[str, str]]) -> Inspection:
    """An inspection with the given (status, document_type) per page of a real PDF."""
    pages = [InspectedPage(number, status, kind, "native_text", "") for number, (status, kind) in enumerate(kinds, 1)]
    return Inspection(pages, len(pages), "pdf", [], data)


def test_pages_that_were_not_read_or_not_identified_go_to_the_remainder_never_dropped():
    data = composite_pdf(["unrelated"] * 5)
    fake = _fake(data, [("read", "resume"), ("read", "resume"), ("read", "unknown"), ("read", "passport"), ("unread", "unknown")])
    result = bulk._split_inspected(fake, data, ["signed_offer"], "x.pdf")
    pieces = _by_kind(result)
    assert pieces["resume"].pages == [1, 2] and pieces["passport"].pages == [4] and pieces["signed_offer"].pages == [3, 5]
    _assert_nothing_lost(result, 5)
    assert [_pages(piece.data) for piece in result.pieces] == [len(piece.pages) for piece in result.pieces]


def test_no_page_is_lost_whatever_the_mix():
    import itertools
    kinds = ["resume", "passport", "aadhaar", "other"]
    data = composite_pdf(["unrelated"] * 4)
    for combo in itertools.product(kinds, repeat=4):
        for named in ([], ["visa"], ["passport", "aadhaar"], ["signed_offer", "passport", "aadhaar", "resume"]):
            result = bulk._split_inspected(_fake(data, [("read", kind) for kind in combo]), data, named, "x.pdf")
            _assert_nothing_lost(result, 4)
            assert all(piece.pages == sorted(piece.pages) and _pages(piece.data) == (len(piece.pages) or 4) for piece in result.pieces)


def test_the_same_bytes_split_the_same_way_so_a_copy_of_a_scan_is_kept_once():
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    names = ["signed_offer", "passport", "aadhaar"]
    first, second = bulk.split_by_content(scan, "application/pdf", names, "a.pdf"), bulk.split_by_content(scan, "application/pdf", names, "b.pdf")
    assert [piece.data for piece in first.pieces] == [piece.data for piece in second.pieces]


# ---- the other documents are sorted by content too (kind_classifier) -------------------------------------------------

import test_kind_classifier as texts   # the synthetic page texts of the kind classifier tests


def _text_pdf(pages: list[str]) -> bytes:
    """A PDF with a text layer holding exactly these page texts (so inspection reads them without any OCR)."""
    with fitz.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_textbox(fitz.Rect(30, 30, 565, 810), text, fontsize=8, fontname="helv")
        return document.tobytes(deflate=True)


def _fake_text(data: bytes, rows: list[tuple[str, str, str]], inherited: tuple[int, ...] = ()) -> Inspection:
    """An inspection of a real PDF with a (status, document_type, text) per page."""
    pages = [InspectedPage(number, status, kind, "native_text", text, inherited=number in inherited)
             for number, (status, kind, text) in enumerate(rows, 1)]
    return Inspection(pages, len(pages), "pdf", [], data)


def test_a_scan_of_several_documents_is_cut_into_one_piece_per_kind_by_content():
    scan = _text_pdf([texts.OFFER, "\n".join(texts.passport_lines(1)), texts.EVISA, texts.MEDICAL, texts.PCC, texts.TICKET,
                      "Minutes of the garden committee. Agenda: bench repairs and the summer picnic. Next meeting Monday evening."])
    result = bulk.split_by_content(scan, "application/pdf", [], "scan0001.pdf")
    pieces = _by_kind(result)
    assert result.split and {kind: piece.pages for kind, piece in pieces.items()} == {
        "signed_offer": [1], "passport": [2], "visa": [3], "medical": [4], "pcc": [5], "air_ticket": [6], "other": [7]}
    assert all(_pages(piece.data) == len(piece.pages) for piece in result.pieces)
    assert result.page_kinds[3]["kind"] == "visa" and 2 not in result.page_kinds     # the visa's zone did not make it a passport page
    _assert_nothing_lost(result, 7)


def test_pages_of_the_same_kind_go_to_one_piece_and_each_page_is_in_the_right_one():
    scan = _text_pdf([texts.MEDICAL, texts.PCC, texts.MEDICAL.replace("TESTER", "OTHER"), texts.OFFER])
    pieces = _by_kind(bulk.split_by_content(scan, "application/pdf", [], "x.pdf"))
    assert pieces["medical"].pages == [1, 3] and pieces["pcc"].pages == [2] and pieces["signed_offer"].pages == [4]


def test_the_name_visa_medical_pcc_is_cut_by_content_and_a_page_nobody_claimed_goes_to_other():
    scan = _text_pdf([texts.EVISA, texts.MEDICAL, "scrawl", texts.PCC])
    result = bulk.split_by_content(scan, "application/pdf", ["visa", "medical", "pcc"], "visa,medical,pcc s.pdf")
    pieces = _by_kind(result)
    assert pieces["visa"].pages == [1] and pieces["medical"].pages == [2] and pieces["pcc"].pages == [4]
    assert pieces["other"].pages == [3]
    _assert_nothing_lost(result, 4)


def test_a_name_claim_no_page_supports_keeps_the_pages_nobody_claimed():
    scan = _text_pdf([texts.MEDICAL, "a faded unreadable stamp", texts.PCC])
    result = bulk.split_by_content(scan, "application/pdf", ["visa", "medical", "pcc"], "visa,medical,pcc.pdf")
    pieces = _by_kind(result)
    assert pieces["medical"].pages == [1] and pieces["pcc"].pages == [3] and pieces["visa"].pages == [2]
    _assert_nothing_lost(result, 3)


def test_a_claim_with_no_page_left_over_keeps_the_whole_scan_as_before():
    scan = _text_pdf([texts.MEDICAL, texts.PCC])
    result = bulk.split_by_content(scan, "application/pdf", ["medical", "pcc", "visa"], "m,p,v.pdf")
    pieces = {tuple(piece.pages): piece.kinds for piece in result.pieces}
    assert pieces[(1,)] == ["medical"] and pieces[(2,)] == ["pcc"] and pieces[(1, 2)] == ["visa"]


def test_an_identity_guess_with_no_evidence_of_its_own_gives_way_to_a_confident_content_kind():
    data = _text_pdf(["x"] * 3)
    rows = [("read", "aadhaar", texts.TICKET + "\nContact 2345 6789 0124"), ("read", "aadhaar", texts.TICKET.replace("AB12CD", "ZZ99XX")),
            ("read", "unknown", "nothing to see on this page, it holds only a few ordinary words and a signature line here")]
    result = bulk._split_inspected(_fake_text(data, rows, inherited=(2,)), data, ["air_ticket", "aadhaar"], "Itinerary.pdf")
    pieces = _by_kind(result)
    assert pieces["air_ticket"].pages == [1, 2] and pieces["aadhaar"].pages == [3]   # the name's claim keeps only the page nobody claimed
    _assert_nothing_lost(result, 3)


def test_a_real_aadhaar_page_beside_a_signed_offer_stays_an_aadhaar_and_the_offer_before_it_is_not_one():
    data = _text_pdf(["x"] * 2)
    rows = [("read", "aadhaar", texts.OFFER), ("read", "aadhaar", "\n".join(texts.aadhaar_lines(1)))]
    result = bulk._split_inspected(_fake_text(data, rows, inherited=(1,)), data, [], "scan.pdf")
    pieces = _by_kind(result)
    assert pieces["aadhaar"].pages == [2] and pieces["signed_offer"].pages == [1]


def test_a_certificate_on_a_resume_page_stays_in_the_resume_and_is_filed_in_its_own_field_too():
    data = _text_pdf(["x"] * 3)
    rows = [("read", "resume", "\n".join(texts.page_lines(0))), ("read", "resume", texts.APPLICATION_FORM), ("read", "other", texts.SSLC)]
    result = bulk._split_inspected(_fake_text(data, rows), data, [], "scan.pdf")
    pieces = _by_kind(result)
    assert pieces["resume"].pages == [1, 2] and pieces["application_form"].pages == [2] and pieces["education_certificate"].pages == [3]
    _assert_nothing_lost(result, 3)


def test_a_continuation_page_is_filed_with_its_document_and_the_note_asks_for_a_check():
    data = _text_pdf(["x"] * 3)
    cont = "Page 2 of 2\nResult normal remarks none thank you for choosing our centre signature and stamp of the examining doctor."
    rows = [("read", "unknown", "Page 1 of 2\n" + texts.MEDICAL), ("read", "unknown", cont), ("read", "unknown", texts.PCC)]
    result = bulk._split_inspected(_fake_text(data, rows), data, [], "scan.pdf")
    pieces = _by_kind(result)
    assert pieces["medical"].pages == [1, 2] and pieces["pcc"].pages == [3]
    assert result.page_kinds[2]["inherited"] and "should be checked" in result.note


def test_a_blank_page_never_stays_with_a_non_identity_document_it_follows():
    data = _text_pdf(["x"] * 3)
    rows = [("read", "unknown", texts.MEDICAL), ("blank", "other", ""), ("read", "unknown", texts.PCC)]
    pieces = _by_kind(bulk._split_inspected(_fake_text(data, rows), data, [], "scan.pdf"))
    assert pieces["medical"].pages == [1] and pieces["pcc"].pages == [3] and pieces["other"].pages == [2]


VISA_STAMPING = """EMBASSY OF THE STATE OF KUWAIT, NEW DELHI
VISA STAMPING CONFIRMATION
Stamping date: 05/11/2026      Slip: 18325867
Holder: TESTER SAMPLE      Passport No. Z7000137
The visa stamp has been affixed on page 14 of the passport. Good for a single entry. Duration of stay 90 days.
Use outside the State of Kuwait is not valid.
"""


def test_photo_and_blank_pages_after_a_visa_stamping_page_are_other_documents_not_part_of_it():
    data = _text_pdf(["x"] * 4)
    rows = [("read", "unknown", texts.SSLC), ("read", "unknown", VISA_STAMPING), ("blank", "other", ""), ("blank", "other", "")]
    result = bulk._split_inspected(_fake_text(data, rows), data, [], "scan11.pdf")
    pieces = _by_kind(result)
    assert pieces["education_certificate"].pages == [1] and pieces["visa_stamping"].pages == [2] and pieces["other"].pages == [3, 4]
    _assert_nothing_lost(result, 4)


def test_photo_and_blank_pages_still_follow_a_resume_page_before_them():
    data = _text_pdf(["x"] * 4)
    rows = [("read", "resume", "\n".join(texts.page_lines(0))), ("blank", "other", ""), ("read", "unknown", texts.MEDICAL), ("blank", "other", "")]
    pieces = _by_kind(bulk._split_inspected(_fake_text(data, rows), data, [], "scan.pdf"))
    assert pieces["resume"].pages == [1, 2] and pieces["medical"].pages == [3] and pieces["other"].pages == [4]


def test_an_education_certificate_typed_as_resume_leaves_the_resume_and_is_its_own_piece():
    data = _text_pdf(["x"] * 4)
    cv = "\n".join(texts.page_lines(0))
    rows = [("read", "resume", cv), ("read", "resume", texts.SSLC), ("read", "resume", cv), ("read", "unknown", texts.MEDICAL)]
    result = bulk._split_inspected(_fake_text(data, rows, inherited=(2,)), data, [], "scan.pdf")
    by_kinds = {tuple(piece.kinds): piece for piece in result.pieces}
    assert by_kinds[("resume",)].pages == [1, 3]                  # the certificate in the middle is skipped, order kept
    assert by_kinds[("education_certificate",)].pages == [2] and by_kinds[("medical",)].pages == [4]
    taken: set[str] = set()
    names = sorted(bulk.import_file_name("Sample Person", piece.kinds, "scan.pdf", taken) for piece in result.pieces)
    assert names == ["Sample_Person-Education_certificate.pdf", "Sample_Person-Medical.pdf", "Sample_Person-Resume.pdf"]
    _assert_nothing_lost(result, 4)


def test_an_education_certificate_on_a_page_the_classifier_read_with_the_resume_is_not_also_a_resume_page():
    data = _text_pdf(["x"] * 3)
    rows = [("read", "resume", "\n".join(texts.page_lines(0))), ("read", "resume", texts.SSLC), ("read", "resume", texts.SSLC.replace("TESTER", "OTHER"))]
    result = bulk._split_inspected(_fake_text(data, rows), data, [], "scan.pdf")
    pieces = _by_kind(result)
    assert pieces["resume"].pages == [1] and pieces["education_certificate"].pages == [2, 3]
    _assert_nothing_lost(result, 3)


def test_an_application_form_read_with_the_resume_is_its_own_cut_beside_the_resume_piece_with_its_own_name():
    data = _text_pdf(["x"] * 2)
    rows = [("read", "resume", texts.APPLICATION_FORM), ("read", "unknown", texts.MEDICAL)]
    result = bulk._split_inspected(_fake_text(data, rows), data, [], "scan.pdf")
    by_kinds = {tuple(piece.kinds): piece for piece in result.pieces}
    assert ("resume",) in by_kinds and ("application_form",) in by_kinds, [piece.kinds for piece in result.pieces]
    assert by_kinds[("resume",)].pages == [1] and by_kinds[("application_form",)].pages == [1]
    taken: set[str] = set()
    names = sorted(bulk.import_file_name("Sample Person", piece.kinds, "scan.pdf", taken) for piece in result.pieces)
    assert names == ["Sample_Person-Application_form.pdf", "Sample_Person-Medical.pdf", "Sample_Person-Resume.pdf"], names
    _assert_nothing_lost(result, 2)


def test_a_scan_of_one_content_kind_throughout_is_stored_whole_under_it():
    scan = _text_pdf([texts.MEDICAL, texts.MEDICAL.replace("TESTER", "OTHER")])
    result = bulk.split_by_content(scan, "application/pdf", [], "scan.pdf")
    assert not result.split and result.pieces[0].data is scan and result.pieces[0].kinds == ["medical"]


def test_no_page_is_lost_whatever_the_mix_of_kinds_and_names():
    import itertools
    contents = [("read", "resume", texts.CV), ("read", "passport", texts.PASSPORT_PAGE),
                ("read", "unknown", texts.OFFER), ("read", "other", texts.PCC), ("read", "aadhaar", texts.TICKET),
                ("read", "unknown", "scrawl"), ("blank", "other", ""), ("unread", "unknown", "")]
    data = _text_pdf(["x"] * 3)
    for combo in itertools.product(contents, repeat=3):
        for named in ([], ["passport", "aadhaar"], ["signed_offer", "medical", "pcc"]):
            result = bulk._split_inspected(_fake_text(data, list(combo)), data, named, "x.pdf")
            _assert_nothing_lost(result, 3)
            assert all(piece.pages == sorted(piece.pages) and _pages(piece.data) == (len(piece.pages) or 3) for piece in result.pieces)
