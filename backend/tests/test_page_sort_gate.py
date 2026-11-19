"""The page-sorting pipeline, end to end, on invented bundles (no real person, no database).

This is the small, always-on part of the page-sorting accuracy gate: it builds multi-document scans out of made-up text, runs the
real `split_by_content` (OCR included, so Tesseract is needed: the tests are skipped without it) and asserts every page lands in
the piece of its own kind. It guards the WIRING (reader -> classifiers -> splitter); the accuracy on real bundles is measured by
`scripts/score_page_sorting.py` (see docs/sorting-gate.md). Every name, number and machine-readable line below is invented.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest

import ocr_synthetic as synth
from app.integrations.ocr import inspection

TESSERACT = inspection.find_tesseract()
needs_tesseract = pytest.mark.skipif(TESSERACT is None, reason="Tesseract is not installed")

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", tesseract_cmd=None,
                           ocr_languages="eng", agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.services import bulk_candidate_import as bulk


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(bulk, "get_settings", lambda: SETTINGS)   # never the real .env


# Invented page texts. Tester Sample / Northwind / Example University do not exist.
RESUME_1 = synth.page_lines(3)
RESUME_2 = ["CURRICULUM VITAE (continued)", "Name: Tester Sample", "Languages: English, Hindi, Tamil",
            "Skills: arc welding, TIG welding, blueprint reading, safety audits", "Declaration: the details above are true.",
            "Place: Examplepur    Signature: Tester Sample"]
PASSPORT = synth.passport_lines(3)
AADHAAR = synth.aadhaar_lines(3)
EXPERIENCE_LETTER = ["NORTHWIND FABRICATION LLC", "EXPERIENCE CERTIFICATE", "Date: 05/02/2026", "To whom it may concern",
                     "This is to certify that Mr. Tester Sample worked with our company as a Welding Supervisor",
                     "from 13 February 2019 to 12 February 2026. His conduct and performance were satisfactory.",
                     "We wish him success in his future career.", "Authorised signatory", "Human Resources Manager"]
DEGREE = ["EXAMPLE UNIVERSITY", "DEGREE CERTIFICATE", "This is to certify that Tester Sample has passed the examination",
          "for the degree of BACHELOR OF COMMERCE held in April 2012 and is awarded the degree.",
          "Register Number 123456    Class: First Class", "Controller of Examinations", "Vice Chancellor"]


def scan(pages: list[list[str]]) -> bytes:
    """One image-only PDF, a page per line list: the text exists only as pixels, as in a phone scan."""
    with fitz.open() as document:
        for lines in pages:
            image = synth.render_lines(lines)
            page = document.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
            page.insert_image(page.rect, stream=synth.jpeg_page(image))
        return document.tobytes(deflate=True)


def filed(result) -> dict[int, set[str]]:
    """{page: every kind of every piece that holds it}, as the gate script reads a result."""
    out: dict[int, set[str]] = {}
    for piece in result.pieces:
        for page in piece.pages:
            out.setdefault(page, set()).update(piece.kinds)
    return out


def pieces_of(result, kind: str):
    return [piece for piece in result.pieces if kind in piece.kinds]


def split(pages):
    return bulk.split_by_content(scan(pages), "application/pdf", [], "bundle.pdf")


@needs_tesseract
def test_resume_passport_and_certificates_are_each_filed_in_their_own_piece():
    result = split([RESUME_1, RESUME_2, PASSPORT, EXPERIENCE_LETTER, DEGREE])
    assert result.split and not getattr(result, "failed", False) and not result.cut_short
    kinds = filed(result)
    assert "resume" in kinds[1] and "resume" in kinds[2]
    assert "passport" in kinds[3] and "resume" not in kinds[3]
    assert "experience_certificate" in kinds[4]
    assert "education_certificate" in kinds[5]
    assert {number for piece in result.pieces for number in piece.pages} == {1, 2, 3, 4, 5}   # nothing lost


@needs_tesseract
def test_an_id_card_and_a_passport_are_told_apart_and_the_resume_stays_whole():
    result = split([RESUME_1, AADHAAR, PASSPORT, RESUME_2])
    kinds = filed(result)
    assert "aadhaar" in kinds[2] and "passport" not in kinds[2]
    assert "passport" in kinds[3] and "aadhaar" not in kinds[3]
    assert "resume" in kinds[1] and "passport" not in kinds[1] and "aadhaar" not in kinds[1]
    # the second resume page is not mistaken for an identity document
    assert not (kinds[4] & {"passport", "aadhaar"})


@needs_tesseract
def test_a_page_of_something_else_is_not_filed_as_an_identity_document():
    result = split([PASSPORT, list(synth._UNRELATED), EXPERIENCE_LETTER])
    kinds = filed(result)
    assert "passport" in kinds[1]
    assert not (kinds[2] & {"passport", "aadhaar", "resume", "education_certificate", "experience_certificate"})
    assert "experience_certificate" in kinds[3]


@needs_tesseract
def test_the_gate_script_scores_these_pages_the_same_way(tmp_path):
    """The script's own scoring and truth-file reader work on a real split result, so the two cannot drift apart."""
    import importlib.util
    import sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("score_page_sorting", Path(__file__).resolve().parents[1] / "scripts" / "score_page_sorting.py")
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate          # a dataclass in the script looks its module up by name
    try:
        spec.loader.exec_module(gate)
    finally:
        sys.modules.pop(spec.name, None)
    truth = gate.parse_truth("pages 1-2: resume\npage 3: passport\npage 4: experience_certificate\npage 5: education_certificate\n")
    result = split([RESUME_1, RESUME_2, PASSPORT, EXPERIENCE_LETTER, DEGREE])
    rows = gate.score_pages(truth, [(list(p.pages), list(p.kinds)) for p in result.pieces])
    assert [r["page"] for r in rows] == [1, 2, 3, 4, 5]
    assert all(r["ok"] for r in rows), rows
