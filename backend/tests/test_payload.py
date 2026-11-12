"""Local preparation of what is sent to the document reader: minimal rebuilt job files, upright pages, bounded pictures,
blank and duplicate pages, and the file the (fake) reader actually receives. Everything is synthetic.
"""

from copy import deepcopy
import io
import logging

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFilter

import ocr_hard_cases as hard
from app.integrations.ocr import payload
from app.integrations.ocr.payload import prepare_job, rotations_from, unsendable
from app.services import document_extraction as subject
from test_document_extraction import pdf
from test_ocr_classification import CV, INDIA_PASSPORT_DATA, PASSPORT_ADDRESS
from test_veris_handoff import world  # noqa: F401  (the fixture)

A4 = (595, 842)


def scan_image(width: int, height: int, seed: int = 1, mode: str = "RGB") -> Image.Image:
    """A synthetic scan: blurred noise (a heavy but compressible picture) with a red block in the top-left corner."""
    noise = Image.effect_noise((width, height), 40 + seed).filter(ImageFilter.GaussianBlur(1.2))
    image = noise.convert("RGB") if mode == "RGB" else noise
    ImageDraw.Draw(image).rectangle((0, 0, width // 8, height // 8), fill=(255, 0, 0) if mode == "RGB" else 255)
    return image


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=1)
    return out.getvalue()


def hide(doc, number: int, image_xref: int) -> None:
    """List a picture among the resources of page ``number`` (0-based) without drawing it."""
    page_xref = doc.page_xref(number)
    kind, value = doc.xref_get_key(page_xref, "Resources")
    holder, prefix = (int(value.split()[0]), "") if kind == "xref" else (page_xref, "Resources/")
    kind, value = doc.xref_get_key(holder, prefix + "XObject")
    if kind == "xref":
        doc.xref_set_key(int(value.split()[0]), "ImHidden", f"{image_xref} 0 R")
    elif kind == "dict":
        doc.xref_set_key(holder, prefix + "XObject/ImHidden", f"{image_xref} 0 R")
    else:
        doc.xref_set_key(holder, prefix + "XObject", f"<</ImHidden {image_xref} 0 R>>")


def scan_pdf(images: list[Image.Image], sizes: list[tuple[float, float]] | None = None, hidden: bool = False) -> bytes:
    """One scanned page per picture. With ``hidden`` every page also lists the first picture among its resources without
    drawing it (what a shared resource dictionary does in the wild)."""
    with fitz.open() as doc:
        for index, image in enumerate(images):
            width, height = (sizes or [A4] * len(images))[index]
            page = doc.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=png(image))
        if hidden:
            first = doc[0].get_images()[0][0]
            for number in range(1, doc.page_count):
                hide(doc, number, first)
        return doc.tobytes(deflate=True, no_new_id=True)


def page_images(content: bytes) -> list[list[tuple[int, int]]]:
    """(width, height) of every picture on every page."""
    with fitz.open(stream=content, filetype="pdf") as doc:
        return [[(info[2], info[3]) for info in page.get_images()] for page in doc]


def first_picture(content: bytes, index: int = 0) -> Image.Image:
    with fitz.open(stream=content, filetype="pdf") as doc:
        raw = doc.extract_image(doc[index].get_images()[0][0])["image"]
    return Image.open(io.BytesIO(raw)).convert("RGB")


def naive_subset(content: bytes, numbers: list[int]) -> bytes:
    """What was sent before: the pages cut out of the original as they are."""
    with fitz.open(stream=content, filetype="pdf") as source, fitz.open() as subset:
        for number in numbers:
            subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
        return subset.tobytes(garbage=4, deflate=True, no_new_id=True)


def is_red(pixel) -> bool:
    return pixel[0] > 200 and pixel[1] < 80 and pixel[2] < 80


# ------------------------------------------------------------------------------ the prepared file


def test_a_heavy_page_set_is_rebuilt_smaller_with_one_picture_per_page_and_no_hidden_resources():
    original = scan_pdf([scan_image(2200, 3000, 1), scan_image(2200, 3000, 2), scan_image(2200, 3000, 3)], hidden=True)
    sent_before = naive_subset(original, [2, 3])
    assert [len(images) for images in page_images(sent_before)] == [2, 2]  # the hidden resource travels with the page

    prepared = prepare_job(sent_before)

    assert not prepared.fallback and prepared.pages == 2
    assert [len(images) for images in page_images(prepared.content)] == [1, 1] == prepared.images
    assert len(prepared.content) < 0.5 * len(sent_before)
    assert all(max(width, height) <= 2000 for page in page_images(prepared.content) for width, height in page)
    assert len(prepared.content) <= payload.TARGET_BYTES and prepared.recompressed == 2
    print(f"heavy set: {len(sent_before)} -> {len(prepared.content)} bytes")


def test_preparing_is_deterministic_and_never_larger_for_a_small_scan_that_is_embedded_as_it_was():
    small = scan_image(900, 1200, 4)
    original = scan_pdf([small])
    first, second = prepare_job(original), prepare_job(original)
    assert first.content == second.content  # the same pages give the same bytes
    assert first.recompressed == 0 and first.rotated == 0
    assert len(first.content) <= len(original) + 2048
    # Embedded exactly: the picture is pixel for pixel the one that was there (no second lossy pass, no enlarging).
    assert list(first_picture(first.content).getdata()) == list(first_picture(original).getdata())
    assert page_images(first.content) == [[(900, 1200)]]
    print(f"small scan: {len(original)} -> {len(first.content)} bytes")


def test_an_oversized_page_is_shrunk_never_enlarged_and_keeps_its_page_size():
    original = scan_pdf([scan_image(3000, 4200, 5, "L")])
    prepared = prepare_job(original)
    ((width, height),) = page_images(prepared.content)[0]
    assert max(width, height) <= 2000 and abs(width / height - 3000 / 4200) < 0.01
    with fitz.open(stream=prepared.content, filetype="pdf") as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == A4
    assert len(prepared.content) < len(original) / 2 and prepared.recompressed == 1
    print(f"oversized page: {len(original)} -> {len(prepared.content)} bytes")


def test_a_big_paper_size_keeps_at_least_150_dpi():
    big = (1000, 1400)  # 13.9 x 19.4 in: 2000 px would be 103 dpi
    prepared = prepare_job(scan_pdf([scan_image(3300, 4620, 6, "L")], sizes=[big]))
    ((width, height),) = page_images(prepared.content)[0]
    assert 150 * 1400 / 72 - 2 <= height <= payload.MAX_SIDE_CAP


def test_a_sideways_page_is_turned_upright_and_the_page_swaps_its_shape():
    upright = scan_image(1000, 1400, 7)
    sideways = upright.transpose(Image.Transpose.ROTATE_270)  # the sheet was scanned turned a quarter clockwise
    original = scan_pdf([sideways], sizes=[(842, 595)])
    before = first_picture(original)
    assert before.width > before.height and not is_red(before.getpixel((20, 20)))

    prepared = prepare_job(original, [90])  # the local reading read it after turning it 90 degrees counter-clockwise

    after = first_picture(prepared.content)
    assert prepared.rotated == 1 and after.height > after.width and is_red(after.getpixel((20, 20)))
    with fitz.open(stream=prepared.content, filetype="pdf") as doc:
        assert doc[0].rect.height > doc[0].rect.width
    # A page that needs no turning is left alone.
    assert prepare_job(scan_pdf([upright]), [0]).rotated == 0


def test_upside_down_and_a_typed_page_turned_by_the_page_rotation():
    upright = scan_image(800, 1100, 8)
    turned = upright.transpose(Image.Transpose.ROTATE_180)
    again = first_picture(prepare_job(scan_pdf([turned]), [180]).content)
    assert is_red(again.getpixel((20, 20)))
    text = pdf(CV)
    prepared = prepare_job(text, [90])
    with fitz.open(stream=prepared.content, filetype="pdf") as doc:
        assert doc[0].rotation == 270 and doc[0].get_text().strip()  # still typed text, shown upright


def test_a_typed_page_keeps_its_text_and_loses_resources_it_does_not_draw():
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Typed synthetic page for the test")
        other = doc.new_page()
        other.insert_image(other.rect, stream=png(scan_image(600, 800, 9)))
        hide(doc, 0, doc[1].get_images()[0][0])
        content = doc.tobytes(deflate=True, no_new_id=True)
    assert [len(images) for images in page_images(naive_subset(content, [1]))] == [1]
    prepared = prepare_job(naive_subset(content, [1]))
    assert prepared.images == [0] and "Typed synthetic page" in fitz.open(stream=prepared.content, filetype="pdf")[0].get_text()
    assert len(prepared.content) < len(naive_subset(content, [1]))


def test_a_file_that_cannot_be_prepared_is_sent_as_it_was():
    broken = b"%PDF-1.4 not really a pdf"
    prepared = prepare_job(broken)
    assert prepared.fallback and prepared.content == broken


def test_rotations_come_from_the_winning_reading_setting_only():
    completeness = {"pages_detail": [{"page": 2, "setting": "rotate90 psm3"}, {"page": 3, "setting": "deskew psm3"},
                                      {"page": 4, "setting": "rotate270 psm3"}, {"page": 5}]}
    assert rotations_from(completeness) == {2: 90, 4: 270} and rotations_from(None) == {}


def test_merged_read_keeps_its_winning_orientation_for_provider_preparation():
    completeness = {"pages_detail": [{"page": 1, "setting": "merged", "rotation": 90},
                                      {"page": 2, "setting": "merged", "rotation": 45},
                                      {"page": 3, "setting": "merged", "variants": ["rotate270 psm3"]}]}
    assert rotations_from(completeness) == {1: 90}


# ------------------------------------------------------------------------------ blank and duplicate pages


def test_a_blank_unread_page_and_an_exact_duplicate_are_found_and_a_read_page_is_never_called_blank():
    blank = Image.new("RGB", (800, 1100), "white")
    card = scan_image(800, 1100, 10)
    source = scan_pdf([card, blank, card, scan_image(800, 1100, 11)])
    found = unsendable(source, {1: ("passport",), 2: ("passport",), 3: ("passport",), 4: ("passport",)}, unread={2})
    assert found == {2: "blank", 3: "duplicate of page 1"}
    # The local reading read page 2: even a pale page is never dropped as blank.
    assert unsendable(source, {1: ("passport",), 2: ("passport",)}, unread=set()) == {}
    # The same picture with another role is not a duplicate.
    assert unsendable(source, {1: ("passport",), 3: ("aadhaar",)}, unread=set()) == {}


# ------------------------------------------------------------------------------ what the reader receives


def test_the_reader_receives_the_prepared_single_page_passport_job(world, caplog):
    caplog.set_level(logging.DEBUG, logger=subject.log.name)
    sent = []
    real = subject.prepare_job

    def spy(content, rotations=None, **options):
        prepared = real(content, rotations, **options)
        sent.append((len(content), prepared))
        return prepared

    world.monkeypatch.setattr(subject, "prepare_job", spy)
    original = world.veris.submit
    received = []

    def recording(document, *, mode, idempotency_key):
        received.append((mode, document.data))
        return original(document, mode=mode, idempotency_key=idempotency_key)

    world.veris.submit = recording
    mrz = {"given_names": "TESTER", "surname": "SAMPLE1", "passport_number": hard.valid_mrz(1)[2], "all_check_digits_valid": True}
    world.veris.build = lambda mode, index, texts: {"mrz": mrz} if mode == "passport" else {}

    line1, line2, _ = hard.valid_mrz(1)
    output = world.extract(pdf(CV, INDIA_PASSPORT_DATA + f"\n{line1}\n{line2}", PASSPORT_ADDRESS))

    assert output["status"] == "complete"
    passport = [data for mode, data in received if mode == "passport"]
    assert len(passport) == 2  # one page per job
    prepared = [item for _size, item in sent if item.pages == 1]
    assert {item.content for item in prepared} >= set(passport)
    for data in passport:
        with fitz.open(stream=data, filetype="pdf") as doc:
            assert doc.page_count == 1 and len(doc[0].get_images()) == 0
    text = " | ".join(r.getMessage() for r in caplog.records)
    assert "passport (part 1 of 2): prepared 1 page, " in text and "rotation fixed on 0 pages" in text
    assert "resume: prepared 1 page, " in text
