"""A page whose box is huge (a phone app wrote a photo's pixel size as points) is read at a lower resolution, not refused."""
import io
import math

import pymupdf as fitz
import pytest
from PIL import Image

from app.integrations.ocr import inspection as subject
from test_ocr_classification import PASSPORT


@pytest.fixture(autouse=True)
def no_external_tools(monkeypatch):
    monkeypatch.setattr(subject.shutil, "which", lambda _name: None)
    monkeypatch.setattr(subject, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(subject, "_find_libreoffice", lambda: None)


def scanned(width, height):
    """A one-page PDF with a picture and no text layer, so the page has to be rendered and read."""
    with fitz.open() as document:
        page = document.new_page(width=width, height=height)
        with Image.new("L", (60, 80), 30) as picture:
            out = io.BytesIO()
            picture.save(out, format="PNG")
        page.insert_image(page.rect, stream=out.getvalue())
        return document.tobytes()


@pytest.fixture
def renders(monkeypatch):
    """Record the resolution every render of the page asks for; the bitmaps themselves are tiny stand-ins."""
    asked = []

    def stand_in(mode):
        def render(self, index, dpi):
            asked.append(dpi)
            return Image.new(mode, (80, 100), 40)
        return render

    monkeypatch.setattr(subject._PageReader, "_render", stand_in("L"))
    monkeypatch.setattr(subject._PageReader, "_render_color", stand_in("RGB"))
    return asked


def reading(monkeypatch, seen):
    def read(image, **kwargs):
        seen.append(kwargs["dpi"])
        kwargs["render"](subject.OCR_HIGH_DPI)  # the ladder's higher-resolution pass
        kwargs["render_color"](subject.OCR_HIGH_DPI)
        return subject.OCRRead(PASSPORT, 85)
    monkeypatch.setattr(subject, "_ocr_image", read)


def test_a_normal_page_keeps_the_base_resolution(monkeypatch, renders):
    seen = []
    reading(monkeypatch, seen)
    report = subject.inspect_document(scanned(595, 842))
    assert seen == [subject.OCR_BASE_DPI]
    assert renders == [subject.OCR_BASE_DPI, subject.OCR_HIGH_DPI, subject.OCR_HIGH_DPI]
    assert report.pages[0].status == "read"


@pytest.mark.parametrize("size", [(1700, 2400), (2400, 3200), (2600, 3700), (4000, 5000)])
def test_an_oversized_page_box_is_read_at_a_lower_resolution_inside_the_pixel_budget(monkeypatch, renders, size):
    seen = []
    reading(monkeypatch, seen)
    report = subject.inspect_document(scanned(*size))
    page = report.pages[0]
    assert (page.status, page.document_type) == ("read", "passport")
    assert report.complete
    width, height = size
    assert renders and all(dpi >= 40 for dpi in renders)
    for dpi in renders + seen:
        assert width * height * (dpi / 72) ** 2 <= subject._MAX_RENDER_PIXELS
    # Only the oversized boxes are lowered; every higher-resolution request on one is capped to the same budget.
    if width * height * (subject.OCR_BASE_DPI / 72) ** 2 > subject._MAX_RENDER_PIXELS:
        assert seen[0] < subject.OCR_BASE_DPI and max(renders) < subject.OCR_HIGH_DPI
    else:
        assert seen == [subject.OCR_BASE_DPI]


def test_the_clamp_is_the_largest_resolution_that_fits_the_budget():
    rect = fitz.Rect(0, 0, 2600, 3700)
    dpi = subject._PageReader._fit_dpi(rect, 200)
    assert 2600 * 3700 * (dpi / 72) ** 2 <= subject._MAX_RENDER_PIXELS < 2600 * 3700 * ((dpi + 1) / 72) ** 2
    assert dpi == math.floor(72 * math.sqrt(subject._MAX_RENDER_PIXELS / (2600 * 3700)))
    assert subject._PageReader._fit_dpi(fitz.Rect(0, 0, 595, 842), 200) == 200
    assert subject._PageReader._fit_dpi(fitz.Rect(0, 0, 2400, 3200), 72) == 72  # a request already inside the budget is never raised


def test_an_absurd_page_box_is_still_refused(monkeypatch, renders):
    seen = []
    reading(monkeypatch, seen)
    report = subject.inspect_document(scanned(14400, 14400))
    page = report.pages[0]
    assert (page.status, page.method) == ("unread", "failed")
    assert "too large to read" in page.reason
    assert renders == [] and seen == []
