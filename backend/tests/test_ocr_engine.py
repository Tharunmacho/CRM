"""Local OCR engine: speed, completeness and correctness on synthetic documents only.

Tests that read pixels need Tesseract (found on PATH or in its usual Windows folder); they are
skipped, never failed, where it is absent. The pool is capped at four workers so the suite is
gentle on a shared machine.
"""
import io
import threading
import time

import pymupdf as fitz
import pytest
from PIL import Image

import ocr_synthetic as synth
from app.integrations.ocr import inspection as subject

TESSERACT = subject.find_tesseract()
needs_tesseract = pytest.mark.skipif(TESSERACT is None, reason="Tesseract is not installed")
WORKERS = 4


def assert_accounted_for(report, expected_total):
    c = report.completeness
    assert c["pages_total"] == expected_total == report.page_count == len(report.pages)
    assert [p.page_number for p in report.pages] == list(range(1, expected_total + 1))
    assert c["pages_read"] + c["pages_blank"] + c["pages_unread"] == c["pages_total"]
    assert c["pages_read"] == c["pages_native"] + c["pages_ocr"]
    assert c["pages_unread"] == len(c["unread"]) == len(report.unread_pages)
    assert all(item["reason"] for item in c["unread"]), "an unread page must say why"
    assert c["seconds"] >= 0 and c["chars"] == sum(len(p.text) for p in report.pages)
    assert report.to_dict()["completeness"]["pages_total"] == expected_total


# ---------------------------------------------------------------- native text is used, not OCR'd

def test_native_pages_never_start_tesseract(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A page with a complete text layer must not be OCR'd.")
    monkeypatch.setattr(subject, "_tesseract", forbidden)
    report = subject.inspect_document(synth.native_pdf(list(range(12))), page_workers=WORKERS)
    assert_accounted_for(report, 12)
    assert report.completeness["pages_native"] == 12 and report.completeness["pages_ocr"] == 0
    for index, page in enumerate(report.pages):
        assert page.method == "native_text"
        assert synth.recall(synth.page_lines(index), page.text) >= 0.99


def test_usable_native_rejects_stray_characters_and_fragments():
    page = "\n".join(synth.page_lines(3))
    assert subject._usable_native(page)
    assert not subject._usable_native("Page 3 of 12")
    assert not subject._usable_native("Scanned by DocuScan 12 pages total footer " * 1)
    # Enough characters but not enough words: a few long tokens.
    assert not subject._usable_native("A" * 120 + " " + "B" * 120)
    # Single-character fragments, symbols only, and damaged glyph mapping.
    assert not subject._usable_native(" ".join("a b c d e f g h i j k l m n o p".split() * 5))
    assert not subject._usable_native("-_=+*#~ " * 40)
    assert not subject._usable_native("�" * 100)


def test_text_layer_over_a_scan_is_trusted_but_a_header_over_a_scan_is_not(monkeypatch):
    # A searchable scan (full-page picture plus its own invisible-looking text layer).
    with fitz.open() as document:
        image = synth.render_lines(synth.page_lines(2))
        page = document.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
        page.insert_image(page.rect, stream=synth.jpeg_page(image))
        y = 60
        for line in synth.page_lines(2):
            if line:
                page.insert_text((60, y), line, fontsize=8.5)
            y += 12
        searchable = document.tobytes()
    monkeypatch.setattr(subject, "_tesseract", lambda *a, **k: pytest.fail("covered by its own text layer"))
    report = subject.inspect_document(searchable, page_workers=1)
    assert report.pages[0].method == "native_text"
    assert synth.recall(synth.page_lines(2), report.pages[0].text) >= 0.99


# ---------------------------------------------------------------- OCR correctness

@needs_tesseract
def test_clean_scans_keep_order_and_find_every_number_email_and_phone():
    report = subject.inspect_document(synth.scanned_pdf(list(range(10))), tesseract_cmd=TESSERACT, page_workers=WORKERS)
    assert_accounted_for(report, 10)
    assert report.completeness["pages_ocr"] == 10 and report.unread_pages == []
    for index, page in enumerate(report.pages):
        assert page.status == "read" and page.method == "tesseract"
        assert synth.recall(synth.page_lines(index), page.text) >= 0.97, f"page {index + 1}"
        n = index + 1
        # Order: each page carries its own unique identifiers, and not its neighbour's.
        assert f"candidate{n:03d}.sample@example.test" in page.text
        assert f"Z{7000000 + n * 137}" in page.text
        assert f"candidate{n + 1:03d}.sample@example.test" not in page.text
        assert f"Certificate {900000 + n * 13}" in page.text


@needs_tesseract
@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_rotated_scan_is_turned_upright_and_read_in_full(degrees):
    pdf = synth.scanned_pdf([0, 4], rotate={1: degrees})
    report = subject.inspect_document(pdf, tesseract_cmd=TESSERACT, page_workers=2)
    assert_accounted_for(report, 2)
    assert synth.recall(synth.page_lines(0), report.pages[0].text) >= 0.97
    assert synth.recall(synth.page_lines(4), report.pages[1].text) >= 0.97


@needs_tesseract
def test_small_print_is_read_at_higher_resolution():
    report = subject.inspect_document(synth.scanned_pdf([6], point_size=6.5), tesseract_cmd=TESSERACT, page_workers=1)
    assert_accounted_for(report, 1)
    assert synth.recall(synth.page_lines(6), report.pages[0].text) >= 0.95


@needs_tesseract
def test_noisy_jpeg_scan_is_still_read():
    with fitz.open() as document:
        image = synth.render_lines(synth.page_lines(8), noise=True)
        page = document.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
        page.insert_image(page.rect, stream=synth.jpeg_page(image, quality=45))
        data = document.tobytes()
    report = subject.inspect_document(data, tesseract_cmd=TESSERACT, page_workers=1)
    assert synth.recall(synth.page_lines(8), report.pages[0].text) >= 0.95


@needs_tesseract
def test_blank_pages_are_blank_and_text_pages_around_them_are_read():
    report = subject.inspect_document(synth.scanned_pdf([0, None, 1, None]), tesseract_cmd=TESSERACT, page_workers=WORKERS)
    assert_accounted_for(report, 4)
    assert [p.status for p in report.pages] == ["read", "blank", "read", "blank"]
    assert report.completeness["pages_blank"] == 2 and report.completeness["pages_unread"] == 0


def test_a_page_with_only_scanner_specks_is_blank_and_one_with_a_line_of_text_is_not():
    with Image.new("L", (1654, 2339), 255) as speckled:
        for x in range(0, 40, 4):
            speckled.putpixel((100 + x, 200), 120)
        assert subject._blank(speckled)
    assert not subject._blank(synth.render_lines(["Hello 12345"]))


@needs_tesseract
def test_partial_text_layer_does_not_hide_the_scan_content():
    report = subject.inspect_document(synth.partial_native_pdf(7), tesseract_cmd=TESSERACT, page_workers=1)
    page = report.pages[0]
    assert page.status == "read" and page.method == "tesseract"
    assert synth.recall(synth.page_lines(7), page.text) >= 0.97
    assert "DocuScan" in page.text, "nothing from the embedded layer may be dropped"


@needs_tesseract
def test_multiframe_tiff_reads_every_frame_in_order():
    report = subject.inspect_document(synth.multiframe_tiff([1, 2, 3]), tesseract_cmd=TESSERACT, page_workers=3)
    assert report.source_format == "tiff"
    assert_accounted_for(report, 3)
    for position, index in enumerate([1, 2, 3]):
        assert synth.recall(synth.page_lines(index), report.pages[position].text) >= 0.97


@needs_tesseract
def test_photo_formats_go_through_the_same_path():
    image = synth.render_lines(synth.page_lines(9), dpi=150)
    for fmt, extra in (("PNG", {}), ("JPEG", {"quality": 85})):
        out = io.BytesIO()
        image.save(out, format=fmt, **extra)
        report = subject.inspect_document(out.getvalue(), tesseract_cmd=TESSERACT, page_workers=1)
        assert report.source_format == fmt.lower()
        assert_accounted_for(report, 1)
        assert synth.recall(synth.page_lines(9), report.pages[0].text) >= 0.97, fmt


@needs_tesseract
def test_transparent_png_text_is_read_on_white_paper():
    base = synth.render_lines(synth.page_lines(5), dpi=150)
    rgba = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ink = Image.new("RGBA", base.size, (0, 0, 0, 255))
    rgba.paste(ink, mask=Image.eval(base, lambda v: 255 - v))
    out = io.BytesIO()
    rgba.save(out, format="PNG")
    report = subject.inspect_document(out.getvalue(), tesseract_cmd=TESSERACT, page_workers=1)
    assert synth.recall(synth.page_lines(5), report.pages[0].text) >= 0.97


def test_tsv_parser_keeps_quotes_and_odd_characters_instead_of_swallowing_rows():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t30\t91.5\t\"Quoted",
        "5\t1\t1\t1\t1\t2\t0\t0\t10\t30\t92\t+91-98765-43210",
        "5\t1\t1\t1\t2\t1\t0\t0\t10\t30\t90\ta@b.test",
        "5\t1\t1\t1\t2\t2\t0\t0\t10\t30\t-1\t",
        "5\t1\t1\t1\t3\t1\t0\t0\t10\t30\t88\t£ 1,250.00  x",
    ]
    read = subject._parse_tsv("\n".join([header, *rows]) + "\n")
    assert read.text.splitlines()[:2] == ["\"Quoted +91-98765-43210", "a@b.test"]
    assert "1,250.00" in read.text
    assert read.words == 4


@needs_tesseract
def test_requested_language_packs_that_are_missing_are_dropped_not_fatal():
    assert subject._usable_languages(TESSERACT, "eng+zzz") == "eng"
    assert subject._usable_languages(TESSERACT, "zzz") == "eng"
    assert subject._usable_languages(TESSERACT, "--oops") == "eng"
    report = subject.inspect_document(synth.scanned_pdf([0]), tesseract_cmd=TESSERACT, languages="eng+zzz", page_workers=1)
    assert report.pages[0].status == "read"


# ---------------------------------------------------------------- scale, parallelism, memory

@needs_tesseract
def test_hundred_page_mixed_document_is_complete_ordered_and_fast():
    kinds = []
    for i in range(100):
        slot = i % 5
        kinds.append(("native" if slot < 2 else "scan" if slot < 4 else "blank", None if slot == 4 else i))
    data = synth.mixed_pdf(kinds)
    subject._GAUGE.reset()
    started = time.perf_counter()
    report = subject.inspect_document(data, tesseract_cmd=TESSERACT, page_workers=WORKERS)
    elapsed = time.perf_counter() - started
    print(f"\n100-page mixed document, {WORKERS} workers, {elapsed:.1f}s (sequential baseline was ~98s)")
    assert_accounted_for(report, 100)
    assert report.completeness["pages_native"] == 40 and report.completeness["pages_ocr"] == 40
    assert report.completeness["pages_blank"] == 20 and report.completeness["pages_unread"] == 0
    for i, ((kind, index), page) in enumerate(zip(kinds, report.pages)):
        if kind == "blank":
            assert page.status == "blank"
        else:
            assert page.method == ("native_text" if kind == "native" else "tesseract")
            assert synth.recall(synth.page_lines(index), page.text) >= 0.97, f"page {i + 1}"
            assert f"candidate{index + 1:03d}.sample@example.test" in page.text
    assert elapsed < 60, f"100 mixed pages took {elapsed:.1f}s"
    assert subject._GAUGE.peak <= 2 * WORKERS


@needs_tesseract
def test_page_bitmaps_alive_at_once_are_bounded_by_the_pool_not_the_page_count():
    subject._GAUGE.reset()
    report = subject.inspect_document(synth.scanned_pdf(list(range(12))), tesseract_cmd=TESSERACT, page_workers=3)
    assert_accounted_for(report, 12)
    assert 1 <= subject._GAUGE.peak <= 2 * 3
    assert subject._GAUGE.alive == 0, "every bitmap must be released"


def test_pages_are_read_concurrently_but_reported_in_page_order(monkeypatch):
    lock, running, peak = threading.Lock(), [0], [0]

    def slow(self, index, image, dpi):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.03 * (12 - index))  # later pages finish first
        with lock:
            running[0] -= 1
        return subject.OCRRead(f"Marker {index + 1}. " + "word " * 30, 90)

    monkeypatch.setattr(subject._PageReader, "_ocr", slow)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake")
    monkeypatch.setattr(subject, "_usable_languages", lambda _c, languages: languages)
    report = subject.inspect_document(synth.scanned_pdf(list(range(12)), dpi=72), page_workers=4)
    assert peak[0] >= 3, "pages were not read in parallel"
    assert peak[0] <= 4, "the pool bound was exceeded"
    assert [p.text.split(".")[0] for p in report.pages] == [f"Marker {i}" for i in range(1, 13)]
    assert_accounted_for(report, 12)


# ---------------------------------------------------------------- failures are reported, never hidden

def test_one_failed_page_is_listed_as_unread_with_its_reason_and_one_retry_is_recorded(monkeypatch):
    def flaky(self, index, image, dpi):
        if index == 3:
            return subject.OCRRead("", 0, "Local OCR reached the page time limit.", retryable=True)
        return subject.OCRRead("Good page text " * 20, 90)

    monkeypatch.setattr(subject._PageReader, "_ocr", flaky)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake")
    monkeypatch.setattr(subject, "_usable_languages", lambda _c, languages: languages)
    report = subject.inspect_document(synth.scanned_pdf(list(range(6)), dpi=72), page_workers=3)
    assert_accounted_for(report, 6)
    assert report.unread_pages == [4]
    assert report.completeness["unread"] == [{"page": 4, "reason": "Local OCR reached the page time limit."}]
    assert report.completeness["pages_read"] == 5
    assert not report.complete
    with pytest.raises(subject.InspectionError, match="read or identified"):
        report.pdf_for_pages([1])


def test_a_transient_failure_is_retried_once_and_the_page_is_read(monkeypatch):
    calls = {}

    def tesseract(image, command, languages, timeout, **options):
        key = image.size
        calls[key] = calls.get(key, 0) + 1
        if calls[key] == 1:
            return subject.OCRRead("", 0, "Local OCR reached the page time limit.", retryable=True)
        return subject.OCRRead("Recovered page text " * 20, 95, words=60, good_words=60, text_height=30)

    monkeypatch.setattr(subject, "_tesseract", tesseract)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake")
    monkeypatch.setattr(subject, "_usable_languages", lambda _c, languages: languages)
    report = subject.inspect_document(synth.scanned_pdf([0], dpi=72), page_workers=1)
    assert report.pages[0].status == "read"
    assert report.completeness["pages_retried"] == [1]
    assert calls[next(iter(calls))] >= 2


def test_a_failure_that_repeats_is_reported_after_exactly_one_retry(monkeypatch):
    count = []

    def tesseract(image, command, languages, timeout, **options):
        count.append(1)
        return subject.OCRRead("", 0, "Local OCR reached the page time limit.", retryable=True)

    monkeypatch.setattr(subject, "_tesseract", tesseract)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake")
    monkeypatch.setattr(subject, "_usable_languages", lambda _c, languages: languages)
    report = subject.inspect_document(synth.scanned_pdf([0], dpi=72), page_workers=1)
    assert report.unread_pages == [1] and "time limit" in report.pages[0].reason
    assert len(count) == 2
    assert_accounted_for(report, 1)


@needs_tesseract
def test_real_page_timeout_marks_every_page_unread_with_a_reason():
    report = subject.inspect_document(synth.scanned_pdf([0, 1, 2]), tesseract_cmd=TESSERACT,
                                      page_workers=2, page_timeout=0.01)
    assert_accounted_for(report, 3)
    assert report.unread_pages == [1, 2, 3]
    assert all("time limit" in p.reason for p in report.pages)


def test_deadline_scales_with_the_number_of_pages_but_never_exceeds_the_callers_limit():
    one, hundred = subject._scaled_budget(1, 8, 20), subject._scaled_budget(100, 8, 20)
    assert one < hundred
    assert hundred > subject._scaled_budget(10, 8, 20)
    assert subject._scaled_budget(100, 1, 20) > subject._scaled_budget(100, 8, 20)


def test_pages_after_the_deadline_are_reported_not_dropped(monkeypatch):
    import itertools
    clock = itertools.chain([0, 0, 0, 0], itertools.repeat(10_000))
    monkeypatch.setattr(subject.time, "monotonic", lambda: next(clock))
    report = subject.inspect_document(synth.native_pdf([0, 1, 2, 3]), page_workers=1, document_timeout=5)
    assert_accounted_for(report, 4)
    assert report.completeness["pages_unread"] >= 1
    assert all("time limit" in item["reason"] for item in report.completeness["unread"])


def test_page_workers_must_be_a_positive_integer():
    with pytest.raises(subject.InspectionError, match="page workers"):
        subject.inspect_document(synth.native_pdf([0]), page_workers=0)
