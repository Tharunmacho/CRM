"""The OCR regression gate: hard synthetic pages must keep reading, and the page accounting must keep holding.

Fails when a page ends 'unread' without an explicit reason, when word recall drops below the threshold of a
case, when a checksum-valid MRZ or Aadhaar number planted in a page is not recovered, when a photo-only page is
not reported as such, or when the read/blank/unread identity breaks. Synthetic people only (tests/ocr_hard_cases.py).
"""
import random
import threading
import time

import pytest

import ocr_hard_cases as hard
import ocr_synthetic as synth
from app.integrations.ocr import inspection as subject
from app.integrations.ocr import second_opinion

TESSERACT = subject.find_tesseract()
needs_tesseract = pytest.mark.skipif(TESSERACT is None, reason="Tesseract is not installed")
needs_second_engine = pytest.mark.skipif(not second_opinion.available(), reason="the second reading engine is not installed")
WORKERS = 4


def assert_accounted_for(report, expected_total):
    c = report.completeness
    assert c["pages_total"] == expected_total == report.page_count == len(report.pages)
    assert [p.page_number for p in report.pages] == list(range(1, expected_total + 1))
    assert c["pages_read"] + c["pages_blank"] + c["pages_unread"] == expected_total, "read + blank + unread must equal the total"
    assert c["pages_photo"] <= c["pages_blank"], "photo pages are counted inside blank"
    for page in report.pages:
        if page.status == "unread":
            assert page.reason.strip(), f"page {page.page_number} is unread without a reason"
    assert all(item["reason"] for item in c["unread"])


def read(image_or_images, **options):
    images = image_or_images if isinstance(image_or_images, list) else [image_or_images]
    return subject.inspect_document(hard.to_pdf(images), tesseract_cmd=TESSERACT, page_workers=options.pop("page_workers", WORKERS), **options)


CASES = [
    ("passport_textured", lambda: hard.passport_page_textured(1)),
    ("aadhaar_card_table_8", lambda: hard.aadhaar_card_on_table(8.0, 1)),
    ("aadhaar_card_table_minus12", lambda: hard.aadhaar_card_on_table(-12.0, 2)),
    ("fax_low_contrast", lambda: hard.fax_low_contrast(3)),
    ("stamp_over_text", lambda: hard.stamp_over_text(4)),
    ("turned_90", lambda: hard.sideways(90, 5)),
    ("turned_180", lambda: hard.sideways(180, 6)),
    ("turned_270", lambda: hard.sideways(270, 7)),
    ("two_documents_one_page", lambda: hard.two_documents_on_one_page(2)),
    ("photo_only", lambda: hard.photo_only(4)),
]
CARD_CASES = {"aadhaar_card_table_8", "aadhaar_card_table_minus12"}


@needs_tesseract
@pytest.mark.parametrize("name,build", [pytest.param(n, b, marks=needs_second_engine if n in CARD_CASES else ()) for n, b in CASES],
                         ids=[n for n, _ in CASES])
def test_hard_case_is_read_recovered_and_accounted_for(name, build):
    case = build()
    report = read(case.image, page_workers=1)
    assert_accounted_for(report, 1)
    page = report.pages[0]
    if case.photo_only:
        # A picture with no text is a photo, with an explicit reason, and it never blocks the document.
        assert page.status == "blank" and page.method == "photo_no_text"
        assert page.reason.startswith("Photo / no text")
        assert report.complete and report.completeness["pages_photo"] == 1 and report.unread_pages == []
        return
    assert page.status == "read", f"{name}: {page.status} ({page.reason})"
    assert synth.recall(case.truth, page.text) >= case.min_recall, f"{name}: recall {synth.recall(case.truth, page.text):.2f}"
    flags = report.completeness["patterns"]
    if case.mrz_number:
        assert report.patterns.mrz is not None and report.patterns.mrz.valid, f"{name}: the MRZ was not recovered with valid checksums"
        assert report.patterns.mrz.document_number == case.mrz_number
        assert flags["mrz_found"] and flags["mrz_checksums_valid"] and flags["mrz_pages"] == [1]
    if case.aadhaar:
        assert report.patterns.aadhaar is not None and report.patterns.aadhaar.valid, f"{name}: the Aadhaar number was not recovered"
        assert report.patterns.aadhaar.number == case.aadhaar
        assert flags["aadhaar_found"] and flags["aadhaar_checksum_valid"]


@needs_tesseract
def test_the_record_says_how_each_page_was_read_without_holding_text():
    case = hard.stamp_over_text(4)
    report = read([case.image, hard.photo_only(4).image], page_workers=2)
    detail = {d["page"]: d for d in report.completeness["pages_detail"]}
    assert set(detail) == {1, 2} and all(d["engine"] in {"tesseract", "rapidocr", "both"} for d in detail.values())
    assert detail[1]["setting"] and detail[1]["variants"] and detail[1]["seconds"] >= 0
    dumped = str(report.completeness) + str(report.to_dict())
    assert "candidate" not in dumped.lower() and "sample.test" not in dumped, "the record must not carry page text"
    assert report.completeness["photo"] == [2]


@needs_tesseract
@needs_second_engine
def test_a_combined_file_in_shuffled_order_is_classified_page_by_page():
    resume = [synth.render_lines(synth.page_lines(i)).convert("RGB") for i in (0, 1)]
    passport, card = hard.passport_page_textured(1), hard.aadhaar_card_on_table(8.0, 1)
    items = [("resume", resume[0]), ("resume", resume[1]), ("passport", passport.image), ("aadhaar", card.image)]
    random.Random(11).shuffle(items)
    report = read([image for _, image in items], page_workers=WORKERS)
    assert_accounted_for(report, 4)
    for number, (kind, _) in enumerate(items, 1):
        page = report.pages[number - 1]
        assert page.status == "read", f"page {number} ({kind}): {page.reason}"
        assert number in {"resume": report.resume_pages, "passport": report.passport_pages, "aadhaar": report.aadhaar_pages}[kind], \
            f"page {number} should be {kind}, found {page.document_type}"
    assert report.patterns.mrz.valid and report.patterns.aadhaar.valid
    assert report.complete


@needs_tesseract
def test_one_hundred_mixed_pages_are_all_accounted_for_with_bounded_memory():
    kinds = ([("native", i) for i in range(60)] + [("scan", i) for i in range(60, 85)] + [("blank", None)] * 15)
    random.Random(3).shuffle(kinds)
    subject._GAUGE.reset()
    started = time.monotonic()
    report = subject.inspect_document(synth.mixed_pdf(kinds), tesseract_cmd=TESSERACT, page_workers=WORKERS)
    elapsed = time.monotonic() - started
    assert_accounted_for(report, 100)
    assert report.completeness["pages_blank"] == 15 and report.unread_pages == []
    assert subject._GAUGE.peak <= 2 * WORKERS and subject._GAUGE.alive == 0
    assert elapsed < 240, elapsed


def test_selfcheck_prints_structure_and_no_content(tmp_path, capsys):
    from app.integrations.ocr import selfcheck

    path = tmp_path / "sample.pdf"
    path.write_bytes(synth.native_pdf([0, 1, None]))
    assert selfcheck.run([path]) == 0
    out = capsys.readouterr().out
    assert "pages 3" in out and "read 2" in out and "blank 1" in out
    for secret in ("candidate001", "sample.test", "Welding", "Z7000137"):
        assert secret not in out
