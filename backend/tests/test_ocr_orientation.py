"""A page lying sideways or upside down is read upright and its turn is recorded for the sending step."""
import io
import time

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.integrations.ocr import inspection as subject
from app.integrations.ocr.payload import rotations_from

TEXT = [
    "THIS IS TO CERTIFY THAT MR VIVEK KUMAR HAS WORKED WITH US",
    "AS A WAITER FROM JANUARY 2021 TO MARCH 2022 AND THAT HE IS",
    "HONEST AND HARDWORKING. WE WISH HIM ALL THE BEST FOR THE FUTURE",
    "AND CERTIFY THE ABOVE IS TRUE TO THE BEST OF OUR KNOWLEDGE.",
    "SIGNED FOR THE COMPANY BY THE MANAGER OF THE HOTEL AND RESTAURANT",
    "DATE OF ISSUE 24 JUNE 2022 PLACE OF ISSUE VISAKHAPATNAM INDIA",
] * 2

COMMAND = subject.find_tesseract(None)
needs_tesseract = pytest.mark.skipif(COMMAND is None, reason="Tesseract is not installed")


def _font():
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, 34)
        except OSError:
            continue
    pytest.skip("no TrueType font to draw the page with")


def _page() -> Image.Image:
    image = Image.new("L", (1700, 2200), 255)
    draw, font = ImageDraw.Draw(image), _font()
    for row, line in enumerate(TEXT):
        draw.text((60, 80 + row * 60), line, font=font, fill=0)
    return image


def _words(text: str) -> int:
    return sum(word in text.upper() for word in ("CERTIFY", "WAITER", "MANAGER", "ISSUE", "HOTEL", "VISAKHAPATNAM"))


def _pdf(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PDF", resolution=200)
    return buffer.getvalue()


@needs_tesseract
@pytest.mark.parametrize("sideways", [90, 270, 180])
def test_turned_page_is_read_upright_and_the_turn_recorded(sideways):
    # The scan is the upright page turned `sideways` degrees; undoing it takes (360 - sideways) counter-clockwise.
    upright_turn = (360 - sideways) % 360
    scan = _page().rotate(sideways, expand=True, fillcolor=255)
    read = subject._ocr_image(scan, command=COMMAND, languages="eng", deadline=time.monotonic() + 60, dpi=200)
    assert _words(read.text) >= 4, read.text[:200]
    assert read.notes.get("rotation") == upright_turn


@needs_tesseract
def test_upright_page_pays_for_no_turned_read():
    read = subject._ocr_image(_page(), command=COMMAND, languages="eng", deadline=time.monotonic() + 60, dpi=200)
    assert _words(read.text) >= 4
    assert "rotation" not in read.notes
    assert not any(name.startswith(("probe", "rotate", "upright")) for name in read.variants)


@needs_tesseract
def test_sideways_pdf_page_reports_its_rotation_to_the_sending_step():
    scan = _page().rotate(90, expand=True, fillcolor=255)
    inspected = subject.inspect_document(_pdf(scan), tesseract_cmd=COMMAND, languages="eng", page_workers=1)
    assert _words(inspected.pages[0].text) >= 4
    assert rotations_from(inspected.completeness) == {1: 270}


def test_orientation_plan_from_box_shapes():
    def read(boxes, confidence=60.0):
        items = [(0.1, 0.1, 0.1 + w, 0.1 + h, "word", confidence) for w, h in boxes]
        return subject.OCRRead("word " * len(items), confidence, words=len(items), good_words=len(items), items=items)

    size = (1000, 1000)
    assert subject._orientation_plan(read([(0.05, 0.2)] * 10), size) == ((90, 270), False)
    assert subject._orientation_plan(read([(0.2, 0.02)] * 30), size) == ((), True)
    assert subject._orientation_plan(read([(0.2, 0.02)] * 30, confidence=25.0), size) == ((180,), False)
    assert subject._orientation_plan(read([(0.2, 0.02)] * 3), size) == ((), False)


def test_split_keeps_read_pages_and_notes_unread_ones():
    from app.integrations.ocr.inspection import Inspection, InspectedPage
    from app.services import bulk_candidate_import as bulk
    import pymupdf

    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    data = doc.tobytes()
    pages = [
        InspectedPage(1, "read", "resume", "tesseract", "resume text", 0.9),
        InspectedPage(2, "read", "resume", "tesseract", "more resume", 0.9),
        InspectedPage(3, "unread", "unknown", "not_processed", "", reason="over the time limit"),
    ]
    result = bulk._split_inspected(Inspection(pages, 3, "pdf", [], data), data, [], "scan.pdf")
    assert result.split and [(p.kinds, p.pages) for p in result.pieces] == [(["resume"], [1, 2]), (["other"], [3])]
    assert "page 3 could not be read in time" in result.note
