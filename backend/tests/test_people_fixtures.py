"""The synthetic people fixtures are themselves valid: the checks the real reader applies would pass on them, the ground truth matches
the PDFs, and the same seed gives the same bytes. (No database, no network; the scan-mode proof is opt-in: CRM_ACCEPTANCE_SCAN=1.)"""
import io
import os
import re

import pymupdf as fitz
import pytest
from PIL import Image

import people_fixtures as pf

FOLDER = pf.mixed_folder(7)          # the owner's scenario, text mode: 10 files for Tharun, 5 for Meera
SCAN = os.environ.get("CRM_ACCEPTANCE_SCAN") == "1"


def _pdf_text(data: bytes) -> list[str]:
    with fitz.open(stream=data, filetype="pdf") as document:
        return [page.get_text() for page in document]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


# ---- independent re-implementations of the two public check algorithms (so the fixtures are not graded by their own code) ----

_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
      [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
      [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
      [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff(number: str) -> bool:
    check = 0
    for i, ch in enumerate(reversed(number)):
        check = _D[check][_P[i % 8][int(ch)]]
    return check == 0


def icao(field: str) -> int:
    total = 0
    for i, ch in enumerate(field):
        value = 0 if ch == "<" else int(ch) if ch.isdigit() else ord(ch) - 55
        total += value * (7, 3, 1)[i % 3]
    return total % 10


def check_td3(line1: str, line2: str) -> None:
    assert len(line1) == 44 and len(line2) == 44 and line1.startswith("P<IND")
    assert re.fullmatch(r"[A-Z0-9<]{44}", line1) and re.fullmatch(r"[A-Z0-9<]{44}", line2)
    assert icao(line2[0:9]) == int(line2[9])                       # passport number
    assert icao(line2[13:19]) == int(line2[19])                    # date of birth
    assert icao(line2[21:27]) == int(line2[27])                    # date of expiry
    assert icao(line2[28:42]) == int(line2[42])                    # personal number
    assert icao(line2[0:10] + line2[13:20] + line2[21:43]) == int(line2[43])   # composite


# ---- the people ---------------------------------------------------------------------------------------------------------

def test_the_invented_people_are_distinct_and_their_numbers_are_valid():
    people = [pf.person(key) for key in ("tharun", "meera", "arjun", "divya", "ravi")]
    assert pf.person("tharun").display == "Tharun Pk Kumar" and pf.person("meera").display == "Meera Sundaram"
    for field in ("passport_no", "aadhaar", "phone", "email", "file_no", "pnr", "ticket_no", "visa_no", "slip_no", "reg_no", "vid"):
        values = [getattr(p, field) for p in people]
        assert len(set(values)) == len(values), f"{field} must differ between people"
    for p in people:
        assert verhoeff(p.aadhaar) and re.fullmatch(r"[2-9]\d{11}", p.aadhaar)
        assert re.fullmatch(r"[A-Z]\d{7}", p.passport_no) and re.fullmatch(r"[6-9]\d{9}", p.phone)
        assert p.passport_expiry > pf.ANCHOR > p.passport_issue          # a current passport
        assert p.email.endswith("@example.test")
    assert pf.person("tharun") == pf.person("THARUN")                    # stable for the same key


def test_every_passport_mrz_has_valid_check_digits_and_names_the_person():
    for key in ("tharun", "meera", "arjun", "divya", "ravi"):
        p = pf.person(key)
        line1, line2 = pf.mrz_lines(p)
        check_td3(line1, line2)
        assert line1[5:].split("<<")[0] == p.surname.upper() and line2[:8] == p.passport_no
        assert line2[20] == p.sex and line2[13:19] == p.dob.strftime("%y%m%d")
        from app.integrations.ocr.validators import parse_mrz_td3     # and the real parser agrees
        parsed = parse_mrz_td3(line1, line2)
        assert parsed is not None and parsed.valid and all(parsed.checks.values())


def test_the_mrz_printed_on_a_rendered_passport_page_is_the_valid_one():
    for key in ("tharun", "meera"):
        p = pf.person(key)
        (text,) = _pdf_text(pf.build_pdf([pf.page(key, "passport_data")]))
        zone = [line.strip() for line in text.splitlines() if "<" in line and len(line.strip()) == 44]
        assert len(zone) == 2
        check_td3(*zone)
        assert p.passport_no in text and p.surname.upper() in text and p.given.upper() in text


def test_the_aadhaar_number_on_every_aadhaar_page_is_valid_and_belongs_to_the_person():
    for key in ("tharun", "meera"):
        p = pf.person(key)
        for token in ("aadhaar_front", "aadhaar_back"):
            (text,) = _pdf_text(pf.build_pdf([pf.page(key, token)]))
            groups = re.findall(r"(?<!\d)(\d{4}) (\d{4}) (\d{4})(?!\d)", text)
            assert groups and "".join(groups[0]) == p.aadhaar and verhoeff(p.aadhaar)
        assert "uidai" in text.lower() or "unique identification" in text.lower()


# ---- every document builder --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["tharun", "meera"])
def test_each_page_carries_exactly_the_evidence_it_claims_and_nothing_of_anybody_else(key):
    p, other = pf.person(key), pf.person("meera" if key == "tharun" else "tharun")
    for token in pf.TOKENS:
        spec = pf.page(key, token)
        text = _norm(spec.text)
        assert spec.person == key and spec.kind in pf.KINDS and spec.evidence in ("direct", "indirect", "none")
        assert other.surname.lower() not in text and other.passport_no.lower() not in text and other.aadhaar not in text.replace(" ", "")
        if spec.evidence == "direct":
            assert p.surname.lower() in text and p.given.lower().split()[0] in text, token
        elif spec.evidence == "indirect":
            assert p.surname.lower() + " " not in text.replace(p.father.lower(), "") or token == "passport_back", token
            assert p.file_no.lower() in text or p.aadhaar in text.replace(" ", ""), token       # linked only through an identifier
        else:
            assert not text and spec.layout in ("photo", "blank")
        if spec.kind in ("passport", "visa", "medical", "pcc", "signed_offer", "visa_stamping") and spec.evidence == "direct":
            assert p.passport_no.lower() in text, token


def test_kinds_and_evidence_are_what_the_owners_list_says():
    wanted = {"resume_1": "resume", "resume_2": "resume", "passport_data": "passport", "passport_back": "passport",
              "aadhaar_front": "aadhaar", "aadhaar_back": "aadhaar", "degree": "education_certificate", "offer": "signed_offer",
              "evisa": "visa", "medical": "medical", "pcc": "pcc", "air_ticket": "air_ticket", "visa_stamping": "visa_stamping",
              "photo": "photo", "blank": "blank"}
    assert {token: pf.page("tharun", token).kind for token in pf.TOKENS} == wanted
    assert {pf.page("tharun", token).evidence for token in ("passport_back", "aadhaar_back")} == {"indirect"}
    assert {pf.page("tharun", token).evidence for token in ("photo", "blank")} == {"none"}
    # the same passport/file number ties the passport back page to the police clearance
    assert pf.person("tharun").file_no in pf.page("tharun", "passport_back").text and pf.person("tharun").file_no in pf.page("tharun", "pcc").text


def test_the_existing_page_classifier_recognises_the_identity_pages_it_should():
    """Realism check against the CRM's own (unchanged) identity classifier: resume, passport and Aadhaar pages are what they say."""
    from app.integrations.ocr.classification import classify_page
    for key in ("tharun", "meera"):
        for token, kind in (("resume_1", "resume"), ("resume_2", "resume"), ("passport_data", "passport"), ("passport_back", "passport"),
                            ("aadhaar_front", "aadhaar"), ("aadhaar_back", "aadhaar")):
            guess = classify_page(pf.page(key, token).text)
            assert guess.document_type == kind, f"{key} {token}: {guess}"
        # (the e-visa is left out on purpose: its machine-readable zone looks like a passport's to this classifier, the known trap
        # the content classifier has to overrule; it is a ground-truth visa page all the same)
        for token in ("offer", "medical", "pcc", "air_ticket", "visa_stamping"):
            assert classify_page(pf.page(key, token).text).document_type not in ("passport", "aadhaar"), token


# ---- the scenario -----------------------------------------------------------------------------------------------------------

def test_the_scenario_has_ten_files_for_tharun_and_five_for_meera_with_one_shared():
    assert len(FOLDER.files_of("tharun")) == 10 and len(FOLDER.files_of("meera")) == 5
    assert len(FOLDER.files) == 14 and len(set(name for name, _ in FOLDER.files)) == 14
    assert set(FOLDER.files_of("tharun")) & set(FOLDER.files_of("meera")) == {FOLDER.mixed_file}
    rows = FOLDER.truth[FOLDER.mixed_file]
    assert [(who, kind) for _, who, kind in rows] == [("tharun", "passport"), ("meera", "passport")]
    assert FOLDER.total_pages == 25 == sum(len(rows) for rows in FOLDER.truth.values())
    # a spread of file shapes: one-page and multi-page files, a mixed-person PDF, multi-document scans of one person
    shapes = sorted(len(rows) for rows in FOLDER.truth.values())
    assert shapes[0] == 1 and shapes[-1] == 4
    multi = [rows for rows in FOLDER.truth.values() if len({kind for _, _, kind in rows}) > 1 and len({who for _, who, _ in rows}) == 1]
    assert len(multi) >= 3
    kinds = {kind for rows in FOLDER.truth.values() for _, _, kind in rows}
    assert kinds == set(pf.KINDS)                                       # every kind the owner listed is in the folder
    for key in ("tharun", "meera"):
        mine = {kind for _, _, kind in FOLDER.pages_of(key)}
        assert {"resume", "passport", "aadhaar", "signed_offer", "visa", "medical", "pcc", "air_ticket"} <= mine


def test_the_file_names_are_generic_and_the_pdfs_have_the_pages_the_truth_says():
    for name, data in FOLDER.files:
        lowered = name.lower()
        assert not any(word in lowered for word in ("tharun", "meera", "kumar", "sundaram", "passport", "aadhaar", "aadhar", "resume",
                                                    "visa", "medical", "pcc", "offer", "ticket", "cv", "photo")), name
        assert re.fullmatch(r"(scan\d+|document \(\d+\)|Scan_\d+|DOC\d+|New Doc \d+|CamScanner [\d-]+)\.pdf|IMG[_-].*\.jpg", name), name
        with fitz.open(stream=data, filetype="pdf") as document:
            assert document.page_count == len(FOLDER.truth[name]) == len(FOLDER.evidence[name])
    assert [number for number, _, _ in FOLDER.truth[FOLDER.mixed_file]] == [1, 2]


def test_text_mode_pages_have_a_text_layer_except_photo_and_blank_pages():
    for name, data in FOLDER.files:
        for (number, who, kind), text in zip(FOLDER.truth[name], _pdf_text(data)):
            if kind in pf.ANONYMOUS_KINDS:
                assert not text.strip(), (name, number)
            else:
                assert len(text.strip()) > 80 and pf.person(who).surname.lower() in _norm(text) or FOLDER.evidence[name][number - 1] == "indirect"


def test_the_same_seed_gives_the_same_bytes_and_another_seed_a_different_order():
    again = pf.mixed_folder(7)
    assert again.files == FOLDER.files and again.truth == FOLDER.truth and again.evidence == FOLDER.evidence
    other = pf.mixed_folder(8)
    assert [kinds for kinds in other.truth.values()] != [kinds for kinds in FOLDER.truth.values()]     # shuffled differently
    assert len(other.files_of("tharun")) == 10 and len(other.files_of("meera")) == 5
    assert pf.build_pdf([pf.page("tharun", "resume_1")]) == pf.build_pdf([pf.page("tharun", "resume_1")])


def test_other_counts_and_people_are_honoured():
    small = pf.mixed_folder(3, counts=(3, 2))
    assert len(small.files_of("tharun")) == 3 and len(small.files_of("meera")) == 2 and len(small.files) == 4
    big = pf.mixed_folder(3, counts=(12, 7))                       # more files than the recipe: further copies with other dates
    assert len(big.files_of("tharun")) == 12 and len(big.files_of("meera")) == 7
    assert len({data for _, data in big.files}) == len(big.files)    # no two files are identical bytes (no accidental duplicates)
    three = pf.mixed_folder(5, people=("tharun", "meera", "arjun"), counts=(4, 3, 3))
    assert [len(three.files_of(key)) for key in three.people] == [4, 3, 3]
    separate = pf.mixed_folder(5, counts=(4, 3), with_mixed=False)
    assert separate.mixed_file is None and not set(separate.files_of("tharun")) & set(separate.files_of("meera"))
    named = pf.mixed_folder(5, generic_names=False)
    assert any("tharun" in name for name in named.truth)


def test_images_are_pictures_only_when_asked_and_text_mode_keeps_them_as_pdfs():
    assert all(name.endswith(".pdf") for name, _ in FOLDER.files)
    with_images = pf.mixed_folder(7, images=True)
    jpgs = [(name, data) for name, data in with_images.files if name.lower().endswith(".jpg")]
    assert len(jpgs) == 2 and all(len(with_images.truth[name]) == 1 for name, _ in jpgs)
    for name, data in jpgs:
        assert Image.open(io.BytesIO(data)).format == "JPEG" and with_images.content_type(name) == "image/jpeg"
    assert pf.mixed_folder(7, images=True).files == with_images.files        # deterministic with pictures too


def test_the_folder_can_be_written_to_disk_for_a_manual_run(tmp_path):
    where = pf.write_folder(FOLDER, tmp_path)
    assert sorted(path.name for path in where.iterdir()) == sorted(name for name, _ in FOLDER.files)
    assert (tmp_path / "truth.json").is_file()


# ---- scan mode: pixels only (cheap structure check always; one real OCR read when CRM_ACCEPTANCE_SCAN=1) ---------------------

def test_scan_mode_gives_pixels_only_with_noise_and_a_tilt_and_is_deterministic():
    specs = [pf.page("meera", "aadhaar_front"), pf.page("meera", "photo"), pf.page("meera", "blank")]
    first = pf.build_pdf(specs, "scan", "x")
    assert first == pf.build_pdf(specs, "scan", "x") and first != pf.build_pdf(specs, "scan", "y")
    with fitz.open(stream=first, filetype="pdf") as document:
        assert document.page_count == 3
        assert all(not page.get_text().strip() and len(page.get_images()) == 1 for page in document)   # no text layer: a picture
        blank = document[2].get_pixmap(dpi=50)
        assert 200 < sum(blank.samples) / len(blank.samples) <= 255          # a mostly white sheet with grain, not a black one


@pytest.mark.skipif(not SCAN, reason="set CRM_ACCEPTANCE_SCAN=1 to run the one real OCR read (needs Tesseract)")
def test_a_scan_mode_passport_page_is_read_by_the_local_ocr_and_its_mrz_is_valid():
    from app.integrations.ocr.inspection import find_tesseract, inspect_document
    from app.integrations.ocr.validators import find_mrz
    if find_tesseract(None) is None:
        pytest.skip("Tesseract is not installed")
    p = pf.person("tharun")
    scan = pf.build_pdf([pf.page("tharun", "passport_data")], "scan", "ocr")
    inspected = inspect_document(scan, tesseract_cmd=None, languages="eng", max_pages=2, page_timeout=60, document_timeout=120, page_workers=1)
    (read,) = inspected.pages
    assert read.status == "read" and read.method != "native_text" and read.document_type == "passport"
    mrz = find_mrz(read.text)
    assert mrz is not None and mrz.valid and mrz.document_number == p.passport_no
    assert p.surname.lower() in read.text.lower()
