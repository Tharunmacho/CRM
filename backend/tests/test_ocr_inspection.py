"""Local inspection boundaries using synthetic documents only."""
import io
import itertools
import json
import subprocess
import time
import zipfile

import pymupdf as fitz
import pytest
from PIL import Image

from app.integrations.ocr import inspection as subject
from test_ocr_classification import AADHAAR, AADHAAR_BACK, CERTIFICATE, CONTINUATION, CV, PASSPORT


def pdf(texts):
    with fitz.open() as document:
        for text in texts:
            page = document.new_page()
            if text:
                page.insert_textbox(fitz.Rect(40, 40, 555, 800), text, fontsize=10)
        return document.tobytes()


def image_bytes(*, frames=1):
    with Image.new("RGB", (600, 800), "gray") as image:
        out = io.BytesIO()
        if frames > 1:
            image.save(out, format="TIFF", save_all=True, append_images=[image.copy() for _ in range(frames - 1)])
        else:
            image.save(out, format="PNG")
        return out.getvalue()


@pytest.fixture(autouse=True)
def no_external_tools(monkeypatch):
    monkeypatch.setattr(subject.shutil, "which", lambda _name: None)
    monkeypatch.setattr(subject, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(subject, "_find_libreoffice", lambda: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not invoke a real process or OCR engine.")
    monkeypatch.setattr(subject.subprocess, "run", forbidden)


def test_real_100_page_native_pdf_finds_deep_cv_and_preserves_original_positions(monkeypatch):
    text = [f"Supporting sheet {index + 1}\n{CERTIFICATE}" for index in range(100)]
    text[76], text[77], text[99] = CV, CONTINUATION, PASSPORT
    monkeypatch.setattr(subject, "_ocr_image", lambda *args, **kwargs: pytest.fail("Native text should not use OCR."))
    report = subject.inspect_document(pdf(text))
    assert report.page_count == 100
    assert report.complete
    assert report.resume_pages == [77, 78]
    assert report.passport_pages == [100]
    assert len(report.pages) == 100
    assert [p.page_number for p in report.pages] == list(range(1, 101))
    assert all(p.status == "read" for p in report.pages)
    with fitz.open(stream=report.resume_pdf(), filetype="pdf") as selected:
        assert selected.page_count == 2
        assert "CURRICULUM VITAE" in selected[0].get_text()
        assert "JOB RESPONSIBILITIES" in selected[1].get_text()
        assert "CERTIFICATE OF COMPLETION" not in "".join(p.get_text() for p in selected)
    with fitz.open(stream=report.pdf_for_pages([100]), filetype="pdf") as selected:
        assert selected.page_count == 1
        assert "PASSPORT" in selected[0].get_text()


def test_filename_cannot_influence_detection_or_routing():
    # The contract intentionally has no filename input, even for mislabeled bytes.
    report = subject.inspect_document(pdf([CERTIFICATE]))
    assert report.source_format == "pdf"
    assert report.complete
    assert report.resume_pages == []
    with pytest.raises(subject.InspectionError, match="No matching pages"):
        report.resume_pdf()


def test_subset_bytes_are_stable_across_repeated_inspections():
    original = pdf([CERTIFICATE, CV, PASSPORT])
    first = subject.inspect_document(original)
    second = subject.inspect_document(original)
    assert first.resume_pdf() == first.resume_pdf() == second.resume_pdf()
    assert first.pdf_for_pages([3]) == second.pdf_for_pages([3])


def test_aadhaar_front_and_back_keep_original_positions_and_do_not_enter_resume_payload():
    original = pdf([CV, CERTIFICATE, AADHAAR, AADHAAR_BACK, PASSPORT])
    report = subject.inspect_document(original)
    assert report.complete
    assert report.resume_pages == [1]
    assert report.aadhaar_pages == [3, 4]
    assert report.passport_pages == [5]
    assert report.to_dict()["aadhaar_pages"] == [3, 4]
    assert all("text" not in page for page in report.to_dict()["pages"])
    with fitz.open(stream=report.resume_pdf(), filetype="pdf") as selected:
        assert selected.page_count == 1
        assert "AADHAAR" not in selected[0].get_text()
    selected_bytes = report.pdf_for_pages(report.aadhaar_pages)
    assert selected_bytes == subject.inspect_document(original).pdf_for_pages([3, 4])
    with fitz.open(stream=selected_bytes, filetype="pdf") as selected:
        assert selected.page_count == 2
        assert "GOVERNMENT OF INDIA" in selected[0].get_text()
        assert "UNIQUE IDENTIFICATION AUTHORITY" in selected[1].get_text()


def test_mixed_aadhaar_and_resume_page_is_sent_to_both_providers():
    report = subject.inspect_document(pdf([CV + AADHAAR]))
    assert report.complete
    assert report.unknown_pages == []
    assert report.aadhaar_pages == report.resume_pages == [1]
    assert report.pdf_for_pages([1])


@pytest.mark.parametrize("numbers", [[0], [4], [True], ["2"]])
def test_subset_rejects_invalid_page_numbers(numbers):
    report = subject.inspect_document(pdf([CV, CERTIFICATE, PASSPORT]))
    with pytest.raises(subject.InspectionError, match="does not have"):
        report.pdf_for_pages(numbers)


def test_report_omits_extracted_text_and_pdf_bytes_by_default():
    report = subject.inspect_document(pdf([CV]))
    encoded = json.dumps(report.to_dict())
    assert "alex@example.test" not in encoded
    assert "text" not in report.to_dict()["pages"][0]
    assert "_pdf" not in encoded
    assert "alex@example.test" in report.to_dict(include_text=True)["pages"][0]["text"]
    assert "alex@example.test" not in repr(report)


def test_rendered_blank_is_distinct_from_failed_scan(monkeypatch):
    blank = subject.inspect_document(pdf([CV, ""]))
    assert blank.pages[1].status == "blank"
    assert blank.complete
    monkeypatch.setattr(subject, "_ocr_image", lambda *args, **kwargs: subject.OCRRead("", 0, "OCR timed out."))
    failed = subject.inspect_document(image_bytes())
    assert failed.pages[0].status == "unread"
    assert failed.pages[0].document_type == "unknown"
    assert failed.unread_pages == [1]
    assert not failed.complete
    with pytest.raises(subject.InspectionError, match="read or identified"):
        failed.pdf_for_pages([1])


def test_unknown_nonblank_page_blocks_even_known_resume_subset():
    text = "This is correspondence about travel arrangements and several unrelated meeting dates. " * 3
    report = subject.inspect_document(pdf([CV, text]))
    assert report.resume_pages == [1]
    assert report.unknown_pages == [2]
    assert report.unread_pages == []
    with pytest.raises(subject.InspectionError, match="read or identified"):
        report.resume_pdf()


def test_page_cap_reports_every_unprocessed_original_page():
    report = subject.inspect_document(pdf([CV, CERTIFICATE, PASSPORT]), max_pages=2)
    assert report.page_count == 3
    assert report.unread_pages == [3]
    assert report.pages[2].method == "not_processed"
    assert "more than 2 pages" in report.pages[2].reason
    assert not report.complete


def test_document_deadline_stops_processing_but_keeps_original_page_count(monkeypatch):
    clock = itertools.chain([0], itertools.repeat(2))
    monkeypatch.setattr(subject.time, "monotonic", lambda: next(clock))
    report = subject.inspect_document(pdf([CV, PASSPORT]), document_timeout=1)
    assert report.page_count == 2
    assert report.unread_pages == [1, 2]
    assert all("time limit" in p.reason for p in report.pages)


def test_single_image_is_normalized_and_read_locally(monkeypatch):
    monkeypatch.setattr(subject, "_ocr_image", lambda *args, **kwargs: subject.OCRRead(CV, 95))
    report = subject.inspect_document(image_bytes())
    assert report.source_format == "png"
    assert report.resume_pages == [1]
    assert report.pages[0].method == "tesseract"
    with fitz.open(stream=report.resume_pdf(), filetype="pdf") as selected:
        assert selected.page_count == 1
        assert selected[0].get_images()


def mixed_pdf():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(40, 20, 550, 100),
            "Attachment cover information. This page includes a scanned document below. "
            "Read the image content as well as this native-text header.",
            fontsize=10,
        )
        page.insert_image(fitz.Rect(40, 150, 550, 780), stream=image_bytes())
        return document.tobytes()


def test_large_embedded_scan_is_read_even_when_native_header_is_long(monkeypatch):
    calls = []
    def read(*args, **kwargs):
        calls.append(True)
        return subject.OCRRead(CV, 95)
    monkeypatch.setattr(subject, "_ocr_image", read)
    report = subject.inspect_document(mixed_pdf())
    assert calls == [True]
    assert report.resume_pages == [1]
    assert report.pages[0].method == "native_text+tesseract"
    assert "Attachment cover information" in report.pages[0].text
    assert "CURRICULUM VITAE" in report.pages[0].text


def test_failed_embedded_scan_cannot_be_hidden_by_readable_header(monkeypatch):
    monkeypatch.setattr(subject, "_ocr_image", lambda *args, **kwargs: subject.OCRRead("", 0, "OCR timed out."))
    report = subject.inspect_document(mixed_pdf())
    assert report.unread_pages == [1]
    assert not report.complete
    assert "Attachment cover information" in report.pages[0].text


def test_multiframe_tiff_becomes_one_page_per_frame_instead_of_reading_only_the_first(monkeypatch):
    monkeypatch.setattr(subject, "_ocr_image", lambda *a, **k: subject.OCRRead(CV, 95))
    report = subject.inspect_document(image_bytes(frames=3))
    assert report.page_count == 3
    assert [p.page_number for p in report.pages] == [1, 2, 3]
    assert report.completeness["pages_read"] + report.completeness["pages_blank"] + report.completeness["pages_unread"] == 3


def test_animated_gif_is_still_rejected_instead_of_silently_reading_first_frame():
    out = io.BytesIO()
    with Image.new("RGB", (60, 80), "white") as first:
        first.save(out, format="GIF", save_all=True, append_images=[Image.new("RGB", (60, 80), "black")])
    with pytest.raises(subject.InspectionError, match="every frame"):
        subject.inspect_document(out.getvalue())


def test_missing_tesseract_is_actionable_and_never_blank():
    report = subject.inspect_document(image_bytes())
    assert report.unread_pages == [1]
    assert "TESSERACT_CMD" in report.pages[0].reason


def test_low_confidence_scan_requires_review_even_if_it_mentions_resume(monkeypatch):
    monkeypatch.setattr(subject, "_ocr_image", lambda *args, **kwargs: subject.OCRRead(CV, 20))
    report = subject.inspect_document(image_bytes())
    assert report.unread_pages == [1]
    assert report.resume_pages == []


def test_word_bytes_require_converter_and_do_not_fall_back_to_partial_paragraphs():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    with pytest.raises(subject.InspectionError, match="Word documents"):
        subject.inspect_document(out.getvalue())


def test_word_conversion_uses_all_rendered_pages(monkeypatch):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
    monkeypatch.setattr(subject, "_word_pdf", lambda *args: pdf([CERTIFICATE, CV]))
    report = subject.inspect_document(out.getvalue())
    assert report.source_format == "docx"
    assert report.page_count == 2
    assert report.resume_pages == [2]


@pytest.mark.parametrize("data", [b"", b"not a document", b"%PDF-broken"])
def test_invalid_documents_have_actionable_errors(data):
    with pytest.raises(subject.InspectionError):
        subject.inspect_document(data)


def test_encrypted_pdf_requires_unlock():
    with fitz.open() as document:
        document.new_page()
        data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="test-owner", user_pw="test-user")
    with pytest.raises(subject.InspectionError, match="unlocked"):
        subject.inspect_document(data)


def test_sideways_page_is_retried_with_orientation_aware_layout_and_shares_the_page_deadline(monkeypatch):
    calls = []
    def read(image, command, languages, timeout, **options):
        calls.append((options["psm"], timeout))
        return subject.OCRRead(CV, 90, words=40, good_words=40) if options["psm"] == 1 else subject.OCRRead("x y", 20, words=2)
    monkeypatch.setattr(subject, "_tesseract", read)
    with Image.new("L", (40, 60)) as image:
        result = subject._ocr_image(image, command="test-tesseract", languages="eng", deadline=time.monotonic() + 30)
    assert result.text == CV
    assert [psm for psm, _ in calls] == [3, 1]
    assert all(0 < timeout <= 30 for _, timeout in calls)


@pytest.mark.parametrize("first_words,confidence", [(0, 0), (8, 85)])
def test_unclassified_sideways_scan_turns_before_enhancement_even_without_words(monkeypatch, first_words, confidence):
    calls = []
    fragments = "vx mn rqt lmp hq vb yt zw" if first_words else ""

    def read(image, command, languages, timeout, **options):
        calls.append((image.size, options["psm"], timeout))
        if image.size == (60, 40):
            lines = [subject.merge.Line(line, 94, len(line.split()), (0, n / 15, 1, (n + 1) / 15))
                     for n, line in enumerate(CV.splitlines()) if line.strip()]
            return subject.OCRRead(CV, 94, words=40, good_words=40, lines=lines)
        lines = [subject.merge.Line(fragments, confidence, first_words, (0, 0, 1, .1))] if fragments else []
        return subject.OCRRead(fragments, confidence, words=first_words, good_words=first_words, lines=lines)

    monkeypatch.setattr(subject, "_tesseract", read)
    monkeypatch.setattr(subject.enhance, "looks_like_photo", lambda _: False)
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    with Image.new("L", (40, 60), 255) as image:
        result = subject._ocr_image(image, command="test-tesseract", languages="eng",
                                    deadline=time.monotonic() + 30, need_more=subject.needs_more_reading)
    assert subject.classify_page(result.text).document_type == "resume"
    assert result.notes["rotation"] == 90
    assert "rotate90 psm3" in result.variants
    assert not any("sparse" in variant or "contrast" in variant for variant in result.variants)
    assert all(0 < timeout <= 30 for _, _, timeout in calls)
    if first_words:
        assert result.setting == "merged"  # the orientation survives combining readable fragments


def test_low_confidence_ordinary_fragments_do_not_stop_a_mixed_orientation_passport(monkeypatch):
    calls = []
    fragments = "\n".join(["the and of in to at for with from is are was example text" for _ in range(8)])
    assert not subject.needs_more_reading(fragments)  # content alone mistakes this for ordinary prose

    def read(image, command, languages, timeout, **options):
        calls.append((image.size, options["psm"]))
        if image.size == (60, 40) and options["psm"] == 6:
            return subject.OCRRead(PASSPORT, 40, words=30, good_words=12)
        return subject.OCRRead(fragments, 38, words=107, good_words=30)

    monkeypatch.setattr(subject, "_tesseract", read)
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    with Image.new("L", (40, 60), 255) as image:
        result = subject._ocr_image(image, command="test-tesseract", languages="eng",
                                    deadline=time.monotonic() + 30, need_more=subject.needs_more_reading)
    assert subject.classify_page(result.text).document_type == "passport"
    assert result.notes["rotation"] == 90
    assert calls[:4] == [((40, 60), 3), ((40, 60), 1), ((60, 40), 3), ((60, 40), 6)]


def test_merging_noise_cannot_discard_an_identified_passport(monkeypatch):
    noise = "vx mn rqt lmp hq vb yt zw " * 4
    noise_line = subject.merge.Line(noise, 40, 32, (0, 0, 1, 1))

    def read(image, command, languages, timeout, **options):
        text = PASSPORT if image.size == (60, 40) else noise
        return subject.OCRRead(text, 40, words=32, good_words=12,
                               lines=[subject.merge.Line(text, 40, 32, (0, 0, 1, 1))])

    monkeypatch.setattr(subject, "_tesseract", read)
    monkeypatch.setattr(subject.merge, "merge_candidates", lambda _: ([noise_line], 0))
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    with Image.new("L", (40, 60), 255) as image:
        result = subject._ocr_image(image, command="test-tesseract", languages="eng",
                                    deadline=time.monotonic() + 30, need_more=subject.needs_more_reading)
    assert result.text == PASSPORT
    assert result.setting == "rotate90 psm3"
    assert result.notes["rotation"] == 90


def test_expired_orientation_window_never_starts_another_read(monkeypatch):
    calls = []
    now = [10.0]

    def read(*args, **kwargs):
        calls.append(kwargs["psm"])
        now[0] = 12.0
        return subject.OCRRead("", 0)

    monkeypatch.setattr(subject.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(subject, "_tesseract", read)
    monkeypatch.setattr(subject.enhance, "looks_like_photo", lambda _: False)
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    with Image.new("L", (40, 60), 255) as image:
        subject._ocr_image(image, command="test-tesseract", languages="eng", deadline=11.0,
                           need_more=subject.needs_more_reading)
    assert calls == [3]


def test_clean_page_costs_one_tesseract_call(monkeypatch):
    calls = []
    def read(image, command, languages, timeout, **options):
        calls.append(options)
        return subject.OCRRead(CV, 95, words=60, good_words=60, text_height=30)
    monkeypatch.setattr(subject, "_tesseract", read)
    with Image.new("L", (40, 60)) as image:
        subject._ocr_image(image, command="test-tesseract", languages="eng", deadline=time.monotonic() + 30)
    assert len(calls) == 1 and calls[0]["psm"] == 3


def test_tesseract_timeout_is_bounded_and_does_not_include_document_data(monkeypatch):
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 1.5
        assert command[0] == "configured-tesseract"
        raise subprocess.TimeoutExpired(command, 1.5)
    monkeypatch.setattr(subject.subprocess, "run", timeout)
    with Image.new("RGB", (40, 60)) as image:
        read = subject._tesseract(image, "configured-tesseract", "eng", 1.5)
    assert not read.text
    assert "time limit" in read.reason


def test_bad_native_font_mapping_and_spaced_characters_require_ocr():
    assert subject._usable_native(CV)
    assert not subject._usable_native("\ufffd" * 100)
    assert not subject._usable_native("R E S U M E " * 30)


def test_unclassified_page_is_left_out_of_the_selection_only_for_a_partial_caller():
    text = "This is correspondence about travel arrangements and several unrelated meeting dates. " * 3
    report = subject.inspect_document(pdf([CV, text]))
    assert report.unknown_pages == [2] and report.resume_pages == [1] and report.has_classified_pages
    assert any("kind of pages 2" in warning for warning in report.warnings)
    report.allow_partial = True
    with fitz.open(stream=report.resume_pdf(), filetype="pdf") as selected:
        assert selected.page_count == 1
    with pytest.raises(subject.InspectionError, match="does not have"):
        report.pdf_for_pages([3])
    with pytest.raises(subject.InspectionError, match="read and identified"):
        report.pdf_for_pages([2])


def test_partial_report_restores_only_for_a_partial_caller_and_unread_pages_stay_unselectable():
    text = "This is correspondence about travel arrangements and several unrelated meeting dates. " * 3
    original = pdf([CV, text, PASSPORT + AADHAAR])
    report = subject.inspect_document(original)
    saved = report.to_dict()
    assert saved["complete"] is False
    with pytest.raises(subject.InspectionError):
        subject.restore_inspection(original, saved)
    restored = subject.restore_inspection(original, saved, allow_partial=True)
    assert restored.resume_pages == [1] and restored.passport_pages == [3] and restored.aadhaar_pages == [3]
    assert restored.unknown_pages == [2]
    assert 2 not in restored.resume_pages + restored.passport_pages + restored.aadhaar_pages


def test_back_side_inherits_the_adjoining_card_and_is_reported():
    back = "Some printed text with address details and a barcode. " * 3
    report = subject.inspect_document(pdf([AADHAAR, back]))
    assert report.aadhaar_pages == [1, 2] and report.inherited_pages == [2]
    assert any("adjoining" in warning for warning in report.warnings)
    assert report.to_dict()["inherited_pages"] == [2]


def test_single_picture_without_text_is_a_decorative_image(monkeypatch):
    monkeypatch.setattr(subject, "_ocr_image", lambda *a, **k: subject.OCRRead("", 0, "no text"))
    assert subject.inspect_document(image_bytes()).decorative_image
    monkeypatch.setattr(subject, "_ocr_image", lambda *a, **k: subject.OCRRead("A few words only here", 30, "weak"))
    weak = subject.inspect_document(image_bytes())
    assert weak.unread_pages == [1] and not weak.decorative_image
