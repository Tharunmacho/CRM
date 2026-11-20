"""The same physical page is filed once: repeated pages across the files of one candidate are skipped, recorded and never lost.

The documents are the synthetic people of tests/people_fixtures.py (text layer, so nothing needs OCR). The first block is pure
(no database); the second drives the real import endpoint on the disposable PostgreSQL.
"""
import json
import random
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest
from sqlalchemy import select

import people_fixtures as pf
from app.db.models import Attachment, AuditEntry
from app.integrations.ocr import inspection

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", tesseract_cmd=None,
                           ocr_languages="eng", agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.services import bulk_candidate_import as bulk, duplicate_pages as dup


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(bulk, "get_settings", lambda: SETTINGS)   # never the real .env


def pdf(person: str, *tokens: str, variant: int = 0) -> bytes:
    return pf.build_pdf([pf.page(person, token, variant) for token in tokens])


def _uploads(files: dict[str, bytes], content_types: dict[str, str] | None = None) -> list:
    """What the import endpoint makes of a folder before the duplicate check: each file named, read or cut like `import_one` does."""
    uploads = []
    for name, data in files.items():
        content_type = (content_types or {}).get(name) or ("application/pdf" if name.lower().endswith(".pdf") else "image/jpeg")
        kinds = bulk.classify_path_kinds(name)
        result = None
        if bulk.wants_split(kinds, content_type):
            result = bulk.split_by_content(data, content_type, kinds, name)
            if not result.split:
                kinds = result.pieces[0].kinds
        elif not kinds:
            kinds = ["other"]
        if result is not None and result.split:
            for piece in result.pieces:
                uploads.append(bulk.Upload(path=name.rsplit(".", 1)[0] + ".pdf", kinds=piece.kinds, data=piece.data, content_type=content_type,
                                           pages=piece.pages, original_name=name, facts=result.reads_for(piece.pages), source=data))
        else:
            whole = result.reads_for(list(range(1, len(result.page_reads) + 1))) if result is not None else None
            uploads.append(bulk.Upload(path=name, kinds=kinds, data=data, content_type=content_type, facts=whole, source=data))
    return uploads


def _filed(uploads: list, kind: str) -> list:
    return [upload for upload in uploads if kind in upload.kinds]


def _text(data: bytes) -> str:
    with fitz.open(stream=data, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)


def _count(data: bytes) -> int:
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count


def _dedupe(uploads: list):
    return dup.dedupe_uploads(uploads, settings=SETTINGS)


def _every_page_accounted_for(before: list, after: list, report) -> None:
    """The invariant: every (file, page, kind) that went in is filed in a field, or listed as a repeat of a named kept page."""
    filed = {(upload.original_name or upload.path, number, kind) for upload in after if upload.kinds != [bulk.SOURCE_BUNDLE]
             for number in (upload.pages or range(1, dup.page_count(upload.data, upload.content_type) + 1)) for kind in upload.kinds}
    listed = {(item.file, number, item.kind) for item in report.skipped for number in item.pages}
    wanted = set()
    for upload in before:
        count = dup.page_count(upload.data, upload.content_type)
        for number in (upload.pages or range(1, count + 1)):
            wanted |= {(upload.original_name or upload.path, number, kind) for kind in upload.kinds}
    assert wanted <= filed | listed, sorted(wanted - filed - listed)
    for item in report.skipped:      # a skipped page always points at a kept page that really is filed
        assert item.keeper is not None and item.keeper in after and item.kind in item.keeper.kinds
        assert set(item.same_as_pages) <= set(item.keeper.pages or range(1, dup.page_count(item.keeper.data, item.keeper.content_type) + 1))
    sources = {upload.original_name or upload.path for upload in after if upload.kinds == [bulk.SOURCE_BUNDLE]}
    assert {item.file for item in report.skipped} <= sources           # the scan a skipped page is in is kept whole


# ------------------------------------------------------------------------------------------------------ the owner's case

RASHID = "tharun"


def rashid_folder() -> dict[str, bytes]:
    return {
        "Rashid khan - PP.pdf": pdf(RASHID, "passport_data", "passport_back"),
        "Rashid Khan-picker.pdf": pdf(RASHID, "resume_1", "passport_data", "passport_back", "degree", "resume_2"),
        "Rashid khan-signed offer,aadhar,pp.pdf": pdf(RASHID, "offer", "passport_data", "passport_back", "aadhaar_front"),
        "rashidkhan (3 files merged) 9.pdf": pdf(RASHID, "passport_data"),
        "rashidkhan (3 files merged) 10.pdf": pdf(RASHID, "passport_back"),
    }


def test_the_same_passport_pages_in_five_files_are_filed_once_as_the_standalone_scan():
    files = rashid_folder()
    before = _uploads(files)
    assert len(_filed(before, "passport")) == 5                                   # what the owner saw: five passport files
    after, report = _dedupe(before)
    passport = _filed(after, "passport")
    assert len(passport) == 1 and passport[0].data is files["Rashid khan - PP.pdf"] and not passport[0].pages    # the standalone, untouched bytes
    assert _count(passport[0].data) == 2
    assert {item.file for item in report.skipped if item.kind == "passport"} == {
        "Rashid Khan-picker.pdf", "Rashid khan-signed offer,aadhar,pp.pdf", "rashidkhan (3 files merged) 9.pdf", "rashidkhan (3 files merged) 10.pdf"}
    # the other documents are still filed: the offer pages, the Aadhaar page, the resume
    assert len(_filed(after, "aadhaar")) == 1 and len(_filed(after, "signed_offer")) == 1 and len(_filed(after, "resume")) >= 1
    assert "employment offer" in _text(_filed(after, "signed_offer")[0].data).lower()
    # nothing vanishes: every skipped page names the page it repeats, and its scan is kept whole as source evidence
    _every_page_accounted_for(before, after, report)
    bundles = {upload.original_name for upload in after if upload.kinds == [bulk.SOURCE_BUNDLE]}
    assert bundles == {"Rashid Khan-picker.pdf", "Rashid khan-signed offer,aadhar,pp.pdf", "rashidkhan (3 files merged) 9.pdf", "rashidkhan (3 files merged) 10.pdf"}
    for upload in after:
        if upload.kinds == [bulk.SOURCE_BUNDLE]:
            assert upload.data is files[upload.original_name]                       # the scan as received
    message = next(item.message() for item in report.skipped if item.file == "Rashid Khan-picker.pdf")
    assert "Passport pages 2-3 of Rashid Khan-picker.pdf" in message and "pages 1-2 of Rashid khan - PP.pdf" in message and "not filed again" in message


def test_the_result_does_not_depend_on_the_order_of_the_files():
    files = rashid_folder()
    files["Rashid khan-visa stamping.pdf"] = pdf(RASHID, "visa_stamping")
    files["Rashid khan-visa,medical,pcc.pdf"] = pdf(RASHID, "evisa", "medical", "pcc")
    files["Rashid khan-Medical.pdf"] = pdf(RASHID, "medical")
    expected = None
    names = list(files)
    for seed in range(5):
        random.Random(seed).shuffle(names)
        after, report = _dedupe(_uploads({name: files[name] for name in names}))
        shape = sorted((tuple(upload.kinds), upload.original_name or upload.path, tuple(upload.pages or ())) for upload in after)
        skipped = sorted((item.kind, item.file, tuple(item.pages), item.same_as_file) for item in report.skipped)
        if expected is None:
            expected = (shape, skipped)
        assert (shape, skipped) == expected


def test_a_page_only_a_bundle_has_is_still_filed_and_pages_the_standalone_lacks_are_kept():
    files = {
        "Rashid-passport.pdf": pdf(RASHID, "passport_data"),                                  # only the photo page
        "Rashid-picker.pdf": pdf(RASHID, "resume_1", "passport_data", "passport_back"),       # the same photo page, AND the back
    }
    after, report = _dedupe(_uploads(files))
    passport = _filed(after, "passport")
    texts = " ".join(_text(upload.data).lower() for upload in passport)
    assert "republic of india" in texts and "legal guardian" in texts                    # both pages are filed, each once
    assert sum(_count(upload.data) for upload in passport) == 2
    assert [(item.file, item.pages) for item in report.skipped if item.kind == "passport"] == [("Rashid-picker.pdf", [2])]
    _every_page_accounted_for(_uploads(files), after, report)


# ------------------------------------------------------------------------------------------------------ what is NOT a repeat

def test_two_different_peoples_passports_are_never_merged():
    files = {"Rashid-passport.pdf": pdf("tharun", "passport_data", "passport_back"), "Rashid-picker.pdf": pdf("meera", "resume_1", "passport_data", "passport_back")}
    after, report = _dedupe(_uploads(files))
    assert len(_filed(after, "passport")) == 2 and not [item for item in report.skipped if item.kind == "passport"]


def test_a_passports_data_page_and_its_back_page_are_both_kept_and_so_are_an_aadhaar_front_and_back():
    files = {"Rashid-passport.pdf": pdf(RASHID, "passport_data"), "Rashid-passport back.pdf": pdf(RASHID, "passport_back"),
             "Rashid-aadhaar front.pdf": pdf(RASHID, "aadhaar_front"), "Rashid-aadhaar back.pdf": pdf(RASHID, "aadhaar_back")}
    after, report = _dedupe(_uploads(files))
    assert len(_filed(after, "passport")) == 2 and len(_filed(after, "aadhaar")) == 2 and not report.skipped


def test_the_same_aadhaar_card_as_a_picture_and_a_pdf_is_one_file_with_front_and_back():
    card = pf.build_pdf([pf.page(RASHID, "aadhaar_front"), pf.page(RASHID, "aadhaar_back")])
    front = pf.build_jpeg(pf.page(RASHID, "aadhaar_front"))
    files = {"Rashid-aadhaar.pdf": card, "Rashid-aadhaar card.jpeg": front}
    with patch.object(dup, "read_pages", wraps=dup.read_pages) as reading:
        after, report = _dedupe(_uploads(files))
    aadhaar = _filed(after, "aadhaar")
    assert len(aadhaar) == 1 and aadhaar[0].data is card
    assert [(item.file, item.pages, item.same_as_file) for item in report.skipped] == [("Rashid-aadhaar card.jpeg", [1], "Rashid-aadhaar.pdf")]
    assert reading.called


def test_a_re_issued_document_with_other_dates_is_not_a_repeat():
    files = {"Rashid-Medical.pdf": pdf(RASHID, "medical", variant=0), "Rashid-Medical (retest).pdf": pdf(RASHID, "medical", variant=3)}
    after, report = _dedupe(_uploads(files))
    assert len(_filed(after, "medical")) == 2 and not report.skipped
    offers = {"Rashid-offer.pdf": pdf(RASHID, "offer", variant=0), "Rashid-offer (new).pdf": pdf(RASHID, "offer", variant=5)}
    assert len(_filed(_dedupe(_uploads(offers))[0], "signed_offer")) == 2


def test_photos_and_blank_pages_are_not_judged_by_a_picture_hash():
    blank = pf.build_pdf([pf.page(RASHID, "blank")])
    files = {"Rashid-other.pdf": blank, "Rashid-other (2).pdf": blank + b"\n%x", "Rashid-document.pdf": pf.build_pdf([pf.page(RASHID, "blank"), pf.page(RASHID, "photo")])}
    uploads = _uploads(files)
    for upload in uploads:
        upload.kinds = ["other"]
    after, report = _dedupe(uploads)
    assert len(after) == len(uploads) and not report.skipped


def test_files_that_cannot_be_read_are_left_alone():
    uploads = [bulk.Upload(path="a-passport.pdf", kinds=["passport"], data=b"%PDF-1.7 synthetic a", content_type="application/pdf"),
               bulk.Upload(path="b-passport.pdf", kinds=["passport"], data=b"%PDF-1.7 synthetic b", content_type="application/pdf")]
    after, report = _dedupe(uploads)
    assert after == uploads and not report.skipped


# ------------------------------------------------------------------------------------------------------ copies that look different

def _scaled_and_cropped(spec, *, crop=0.0, scale=1.0) -> bytes:
    """The page as a second copy: a smaller sheet with the content scaled down, and the outer margin trimmed away (a crop)."""
    source = fitz.open(stream=pf.build_pdf([spec]), filetype="pdf")
    page = source[0]
    if crop:
        rect = page.rect
        page.set_cropbox(fitz.Rect(rect.x0 + rect.width * crop, rect.y0 + rect.height * crop * 0.5, rect.x1 - rect.width * crop, rect.y1 - rect.height * crop * 0.5))
    out = fitz.open()
    target = out.new_page(width=page.rect.width * scale, height=page.rect.height * scale)
    target.show_pdf_page(target.rect, source, 0)
    return out.tobytes()


def test_a_cropped_or_rescaled_copy_of_a_page_is_the_same_page():
    data_page, back_page = pf.page(RASHID, "passport_data"), pf.page(RASHID, "passport_back")
    files = {"Rashid-passport.pdf": pf.build_pdf([data_page, back_page]),
             "Rashid-passport cropped.pdf": _scaled_and_cropped(data_page, crop=0.06, scale=0.6),
             "Rashid-passport small.pdf": _scaled_and_cropped(back_page, scale=0.5)}
    after, report = _dedupe(_uploads(files))
    assert len(_filed(after, "passport")) == 1 and _count(_filed(after, "passport")[0].data) == 2
    assert {item.file for item in report.skipped} == {"Rashid-passport cropped.pdf", "Rashid-passport small.pdf"}


@pytest.mark.skipif(inspection.find_tesseract() is None, reason="needs Tesseract")
def test_a_scanned_copy_at_another_resolution_and_crop_is_found_by_the_picture_and_its_words():
    spec = pf.page(RASHID, "offer")
    sharp = pf.render_scan(spec, random.Random("a"))
    softer = pf.render_scan(spec, random.Random("b"))
    width, height = softer.size
    softer = softer.crop((int(width * 0.04), int(height * 0.03), int(width * 0.97), int(height * 0.98))).resize((int(width * 0.7), int(height * 0.7)))

    def as_pdf(image) -> bytes:
        with fitz.open() as document:
            page = document.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
            page.insert_image(page.rect, stream=pf._jpeg(image, 70))
            return document.tobytes()

    files = {"Rashid-offer.pdf": as_pdf(sharp), "Rashid-offer (rescanned).pdf": as_pdf(softer)}
    with patch.object(bulk, "get_settings", lambda: SimpleNamespace(**{**vars(SETTINGS), "tesseract_cmd": None})):
        after, report = dup.dedupe_uploads(_uploads(files), settings=SETTINGS)
    assert len(_filed(after, "signed_offer")) == 1 and [item.file for item in report.skipped] == ["Rashid-offer (rescanned).pdf"]


# ------------------------------------------------------------------------------------------------------ units

def test_signals_page_by_page():
    def page_of(person, token, kind):
        spec = pf.page(person, token)
        read = dup.PageRead(spec.text)
        return dup.Page(read, None, 0, kind)

    a, b, c = page_of("tharun", "passport_data", "passport"), page_of("tharun", "passport_data", "passport"), page_of("meera", "passport_data", "passport")
    assert a.number and a.number == b.number and a.number != c.number and a.role == "data"
    assert dup.compare(a, b) and dup.compare(a, c) is None
    back = page_of("tharun", "passport_back", "passport")
    assert back.role == "back" and dup.compare(a, back) is None
    front, rear = page_of("tharun", "aadhaar_front", "aadhaar"), page_of("tharun", "aadhaar_back", "aadhaar")
    assert front.role == "front" and rear.role == "back" and front.number == rear.number and dup.compare(front, rear) is None
    assert dup.page_runs([1, 2, 3, 7]) == "1-3, 7" and dup.pages_word([4]) == "page 4"


# ------------------------------------------------------------------------------------------------------ the real import

from test_bulk_candidate_import import env  # noqa: E402,F401  (fixture)
from test_database_naming import schema_database  # noqa: E402,F401  (fixture)


def _import(env, files: dict[str, bytes], folder="Rashid Khan", content_types=None):
    spec = {"folder": folder, "row": {"phone": "9876500123"}, "files": [{"path": name} for name in files], "defaultOrder": "ORD-1",
            "defaultRecruiterId": "staff"}
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)},
                               files=[("files", (name, data, (content_types or {}).get(name, "application/pdf"))) for name, data in files.items()])
    assert response.status_code == 200, response.text
    return response.json()


def test_the_import_files_the_passport_once_records_the_skipped_pages_and_keeps_the_scans(env):
    files = rashid_folder()
    result = _import(env, files)
    with env.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"])))
        by_slot: dict[str, list] = {}
        for row in rows:
            by_slot.setdefault(row.slot, []).append(row)
        assert len(by_slot["passport"]) == 1 and by_slot["passport"][0].file_name.endswith("-Passport.pdf")
        passport_bytes = (env.storage / by_slot["passport"][0].object_key).read_bytes() if (env.storage / by_slot["passport"][0].object_key).exists() \
            else next(env.storage.glob(f"*/*{by_slot['passport'][0].file_name}")).read_bytes()
        assert passport_bytes == files["Rashid khan - PP.pdf"]
        # the scans the repeated pages are in are kept as received
        from app.api.routers.files import _safe_name
        kept = {row.file_name for row in by_slot["source_bundle"]}
        # (the offer scan's passport pages are byte for byte the picker's: the import already kept that piece once, "is a copy of")
        assert kept == {_safe_name(name) for name in ("Rashid Khan-picker.pdf", "Rashid khan-signed offer,aadhar,pp.pdf", "rashidkhan (3 files merged) 9.pdf", "rashidkhan (3 files merged) 10.pdf")}
        notes = [entry.meta for entry in db.scalars(select(AuditEntry).where(AuditEntry.action == "Skipped duplicate pages", AuditEntry.entity_id == result["candidateId"]))]
        assert {note["slot"] for note in notes} >= {"passport"} and all("not filed again" in note["message"] for note in notes)
    # the answer says what was left out, per kept file and overall
    passport_doc = next(doc for doc in result["documents"] if "passport" in doc["kinds"])
    assert passport_doc["pages"] == [] and len(passport_doc["duplicates"]) == 3
    assert all(item["keptAs"] == passport_doc["fileName"] for item in passport_doc["duplicates"])
    assert result["duplicates"] and any("not filed again" in issue["message"] for issue in result["issues"])
    assert all(doc["kinds"] != ["source_bundle"] for doc in result["documents"])


def test_an_import_without_repeats_files_everything_as_before_and_adds_no_source_files(env):
    result = _import(env, {"Rashid-passport.pdf": pdf(RASHID, "passport_data", "passport_back"), "Rashid-Medical.pdf": pdf(RASHID, "medical")})
    assert result["duplicates"] == [] and len(result["documents"]) == 2 and result["files"] == 2
    with env.sessions() as db:
        slots = {row.slot for row in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"]))}
    assert slots == {"passport", "medical"}


# ------------------------------------------------------------------------------------------------------ the profile upload path

from test_document_splitting_uploads import desk, send as send_upload, rows as stored_rows  # noqa: E402,F401  (fixtures)


def test_a_scan_whose_passport_pages_are_already_filed_says_so_instead_of_adding_a_version(desk):
    first = send_upload(desk, pdf(RASHID, "passport_data", "passport_back"), slot="passport", name="Rashid passport.pdf")
    assert first.status_code == 201, first.text
    again = send_upload(desk, pdf(RASHID, "resume_1", "passport_data", "passport_back", "degree", "resume_2"), slot="passport", name="Rashid picker.pdf")
    assert again.status_code == 201, again.text
    body = again.json()["split"]
    passport = next(piece for piece in body["pieces"] if piece["slot"] == "passport")
    assert passport["alreadyFiled"] is True and passport["pages"] == [2, 3]
    assert "already filed" in body["message"] and "not added again" in body["message"]
    held = [row for row in stored_rows(desk) if row.slot == "passport"]
    assert len(held) == 1 and held[0].version == 1                              # no second passport file
    assert {row.slot for row in stored_rows(desk)} >= {"resume", "source_bundle"}  # the other documents of the scan were filed, and the scan kept


def test_a_scan_whose_passport_pages_differ_is_still_added_as_before(desk):
    assert send_upload(desk, pdf("tharun", "passport_data", "passport_back"), slot="passport", name="First passport.pdf").status_code == 201
    other = send_upload(desk, pdf("meera", "resume_1", "passport_data", "passport_back", "degree"), slot="passport", name="Second.pdf")
    assert other.status_code == 201, other.text
    assert len([row for row in stored_rows(desk) if row.slot == "passport"]) == 2
    assert not any(piece.get("alreadyFiled") for piece in other.json()["split"]["pieces"])
