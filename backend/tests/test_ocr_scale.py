"""Scalability: bounded memory and time for long documents and for many documents at once, and a process-wide
ceiling on Tesseract processes (about twice the CPU count) however many threads ask. No Tesseract is needed:
the reading itself is faked, the scheduling and accounting are real."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import ocr_synthetic as synth
from app.integrations.ocr import inspection as subject
from app.integrations.ocr import second_opinion

TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def many_pages(count: int) -> bytes:
    """``count`` image-only pages made from one rendered page (cheap to build; every page is still read)."""
    import pymupdf as fitz

    image = synth.jpeg_page(synth.render_lines(synth.page_lines(0), dpi=72))
    with fitz.open() as document:
        for _ in range(count):
            document.new_page(width=595, height=842).insert_image(fitz.Rect(0, 0, 595, 842), stream=image)
        return document.tobytes(deflate=True, garbage=3)


def tsv(words=40):
    rows = [TSV_HEADER]
    for k in range(words):
        rows.append(f"5\t1\t1\t1\t{k // 8 + 1}\t{k % 8 + 1}\t{10 + k * 5}\t{20 + (k // 8) * 30}\t40\t25\t95\tword{k}")
    return ("\n".join(rows) + "\n").encode()


@pytest.fixture
def fake_tesseract(monkeypatch):
    """Replaces the Tesseract process: records how many run at once, takes a little time, returns clean text."""
    state = {"running": 0, "peak": 0, "calls": 0}
    lock = threading.Lock()

    def run(args, **kwargs):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
            state["calls"] += 1
        try:
            time.sleep(0.02)
            return type("Result", (), {"returncode": 0, "stdout": tsv(), "stderr": b""})()
        finally:
            with lock:
                state["running"] -= 1

    monkeypatch.setattr(subject.subprocess, "run", run)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake-tesseract")
    monkeypatch.setattr(subject, "_usable_languages", lambda _c, languages: languages)
    monkeypatch.setenv("OCR_SECOND_ENGINE", "off")
    # The fake text carries no real boxes; coverage is the image measure that would send every page down the ladder.
    monkeypatch.setattr(subject.enhance, "text_coverage", lambda *a, **k: 1.0)
    previous = subject._PROCESSES
    yield state
    subject._PROCESSES = previous


def test_tesseract_processes_never_exceed_the_shared_budget_across_documents(fake_tesseract):
    subject.set_process_limit(3)
    pdf = many_pages(8)
    with ThreadPoolExecutor(6) as pool:  # six documents at once, each with four page workers
        reports = list(pool.map(lambda _: subject.inspect_document(pdf, page_workers=4), range(6)))
    assert all(r.completeness["pages_read"] == 8 and r.completeness["pages_unread"] == 0 for r in reports)
    assert fake_tesseract["calls"] >= 48
    assert fake_tesseract["peak"] <= 3, fake_tesseract
    assert subject._PROCESSES.peak <= 3 and subject._PROCESSES.running == 0


def test_the_default_budget_is_about_twice_the_cpu_count():
    assert subject._default_process_limit() == max(2, 2 * (os.cpu_count() or 2)) or os.environ.get("OCR_MAX_TESSERACT_PROCESSES")


def test_a_caller_that_cannot_get_a_slot_in_time_gets_a_retryable_time_limit(monkeypatch):
    budget = subject.set_process_limit(1)
    assert budget.acquire(1)  # the only slot is taken
    try:
        from PIL import Image

        with Image.new("L", (40, 60)) as image:
            result = subject._tesseract(image, "fake", "eng", 0.05)
        assert result.retryable and "time limit" in result.reason
    finally:
        budget.release()


def test_two_hundred_pages_stay_within_the_bitmap_and_time_bounds(fake_tesseract):
    pdf = many_pages(200)
    subject._GAUGE.reset()
    started = time.monotonic()
    report = subject.inspect_document(pdf, page_workers=4)
    elapsed = time.monotonic() - started
    assert report.completeness["pages_total"] == 200 and report.completeness["pages_read"] == 200
    assert subject._GAUGE.peak <= 2 * 4 and subject._GAUGE.alive == 0
    assert report.completeness["pages_read"] + report.completeness["pages_blank"] + report.completeness["pages_unread"] == 200
    assert elapsed < 120, elapsed


def test_twenty_documents_together_keep_bitmaps_and_processes_bounded(fake_tesseract):
    subject.set_process_limit(4)
    pdf = many_pages(10)
    subject._GAUGE.reset()
    with ThreadPoolExecutor(5) as pool:  # an extraction queue running five documents, four page workers each
        reports = list(pool.map(lambda _: subject.inspect_document(pdf, page_workers=4), range(20)))
    assert all(r.completeness["pages_read"] == 10 for r in reports)
    assert fake_tesseract["peak"] <= 4
    assert subject._GAUGE.peak <= 2 * 4 * 5 and subject._GAUGE.alive == 0


@pytest.mark.skipif(not second_opinion.available(), reason="the second reading engine is not installed")
def test_second_engine_runs_are_bounded_by_its_slot_limit(monkeypatch):
    from PIL import Image

    state = {"running": 0, "peak": 0}
    lock = threading.Lock()

    def fake(rgb):
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.05)
        with lock:
            state["running"] -= 1
        return []

    monkeypatch.setattr(second_opinion, "_run", fake)
    second_opinion.set_slots(2)
    with Image.new("L", (200, 200), 255) as image, ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: second_opinion.read_image(image, deadline=time.monotonic() + 30), range(10)))
    assert state["peak"] <= 2 and all(not r.error for r in results)
    second_opinion.set_slots(max(1, (os.cpu_count() or 2) // 2))
