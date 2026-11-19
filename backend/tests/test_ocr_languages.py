"""OCR language packs: which are used, where they come from, and the second read of bilingual Aadhaar pages."""
import io
import logging
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.config import BACKEND_ROOT, Settings
from app.integrations.ocr import inspection as subject
from test_ocr_classification import AADHAAR, CV

TESSERACT = subject.find_tesseract()
WARNING = "Tamil text will be read poorly: install tam.traineddata"


@pytest.fixture(autouse=True)
def clean_language_state(monkeypatch):
    monkeypatch.setattr(subject, "_LANGUAGE_CACHE", {})
    monkeypatch.setattr(subject, "_TESSDATA_CHOICE", {})
    monkeypatch.setenv("OCR_TESSDATA_DIR", "")  # blank: the folder is off unless a test names one
    monkeypatch.delenv("OCR_AADHAAR_LANGUAGES", raising=False)
    monkeypatch.setenv("OCR_SCRIPT_LANGUAGES", "")  # the script pass has its own tests (test_ocr_script_pass.py)


def own_folder(root: Path, languages=("eng", "osd", "tam"), tsv=True) -> Path:
    folder = root / "tessdata"
    folder.mkdir()
    for language in languages:
        (folder / f"{language}.traineddata").write_bytes(b"x")
    if tsv:
        (folder / "configs").mkdir()
        (folder / "configs" / "tsv").write_text("tessedit_create_tsv 1\n")
    return folder


def listing(system, own=None):
    """A stand-in for ``tesseract --list-langs`` (``own`` is what it finds with --tessdata-dir)."""
    def fake(command, tessdata):
        return set(own) if tessdata is not None and own is not None else (None if tessdata is not None else set(system))
    return fake


# ---------------------------------------------------------------- settings

def test_settings_default_to_english_for_every_page_and_tamil_for_aadhaar(monkeypatch):
    for name in ("OCR_LANGUAGES", "OCR_AADHAAR_LANGUAGES", "OCR_TESSDATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.ocr_languages == "eng"
    assert settings.ocr_aadhaar_languages == "eng+tam"
    assert Path(settings.ocr_tessdata_dir) == BACKEND_ROOT / "data" / "tessdata"


def test_the_language_folder_is_git_ignored():
    ignore = (BACKEND_ROOT.parent / ".gitignore").read_text(encoding="utf-8", errors="replace")
    assert "backend/data/" in ignore


# ---------------------------------------------------------------- folder discovery

def test_tessdata_folder_comes_from_the_environment_and_must_exist(monkeypatch, tmp_path):
    folder = own_folder(tmp_path)
    monkeypatch.setenv("OCR_TESSDATA_DIR", str(folder))
    assert subject._configured_tessdata_dir() == folder
    monkeypatch.setenv("OCR_TESSDATA_DIR", str(tmp_path / "missing"))
    assert subject._configured_tessdata_dir() is None
    monkeypatch.setenv("OCR_TESSDATA_DIR", "  ")
    assert subject._configured_tessdata_dir() is None


def test_list_languages_names_the_folder_only_when_one_is_given(monkeypatch, tmp_path):
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, b'List of available languages (2):\neng\ntam\n', b"")

    monkeypatch.setattr(subject.subprocess, "run", run)
    assert subject._list_languages("tess", None) == {"eng", "tam"}
    assert subject._list_languages("tess", tmp_path) == {"eng", "tam"}
    assert seen[0] == ["tess", "--list-langs"]
    assert seen[1] == ["tess", "--list-langs", "--tessdata-dir", str(tmp_path)]
    monkeypatch.setattr(subject.subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 1, b"", b"boom"))
    assert subject._list_languages("tess", None) is None


# ---------------------------------------------------------------- language resolution

def test_languages_come_from_the_own_folder_when_it_holds_more_of_them(monkeypatch, tmp_path):
    folder = own_folder(tmp_path)
    monkeypatch.setenv("OCR_TESSDATA_DIR", str(folder))
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd"}, own={"eng", "osd", "tam"}))
    assert subject._usable_languages("tess", "eng+tam") == "eng+tam"
    assert subject._TESSDATA_CHOICE[("tess", "eng+tam")] == str(folder)


def test_the_install_is_kept_when_it_has_everything_or_the_request_is_english_only(monkeypatch, tmp_path, caplog):
    folder = own_folder(tmp_path)
    monkeypatch.setenv("OCR_TESSDATA_DIR", str(folder))
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd", "tam"}, own={"eng", "osd", "tam"}))
    with caplog.at_level(logging.WARNING, logger="crm.reading"):
        assert subject._usable_languages("tess", "eng+tam") == "eng+tam"
        assert subject._usable_languages("tess", "eng") == "eng"
    assert subject._TESSDATA_CHOICE[("tess", "eng+tam")] is None
    assert subject._TESSDATA_CHOICE[("tess", "eng")] is None
    assert not caplog.records


@pytest.mark.parametrize("languages,tsv", [(("eng", "tam"), True), (("eng", "osd", "tam"), False)])
def test_an_incomplete_folder_is_ignored_with_a_warning(monkeypatch, tmp_path, caplog, languages, tsv):
    # It must hold eng and osd (Tesseract reads one folder only) and the tsv setting (else the output is plain text).
    folder = own_folder(tmp_path, languages=languages, tsv=tsv)
    monkeypatch.setenv("OCR_TESSDATA_DIR", str(folder))
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd"}, own=set(languages)))
    with caplog.at_level(logging.WARNING, logger="crm.reading"):
        assert subject._usable_languages("tess", "eng+tam") == "eng"
    assert subject._TESSDATA_CHOICE[("tess", "eng")] is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("is ignored" in m for m in messages)
    assert any(WARNING in m for m in messages)


def test_a_missing_pack_falls_back_to_english_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd"}))
    with caplog.at_level(logging.WARNING, logger="crm.reading"):
        assert subject._usable_languages("tess", "eng+tam") == "eng"
        assert subject._usable_languages("tess", "eng+tam") == "eng"
        assert subject._usable_languages("tess", "eng+tam") == "eng"
    assert [r.getMessage().split(" (")[0] for r in caplog.records] == [WARNING]


def test_other_missing_packs_are_named(monkeypatch, caplog):
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd", "tam"}))
    with caplog.at_level(logging.WARNING, logger="crm.reading"):
        assert subject._usable_languages("tess", "eng+tam+hin+mal") == "eng+tam"
    assert len(caplog.records) == 1
    assert "hin, mal" in caplog.records[0].getMessage()
    assert "Tamil" not in caplog.records[0].getMessage()


def test_unusable_requests_and_unreadable_listings(monkeypatch):
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd"}))
    assert subject._usable_languages("tess", "--oops") == "eng"
    assert subject._usable_languages("tess", "zzz") == "eng"
    monkeypatch.setattr(subject, "_list_languages", lambda command, tessdata: None)
    assert subject._usable_languages("other", "eng+tam") == "eng+tam"  # cannot tell: asked for as given
    assert subject._TESSDATA_CHOICE[("other", "eng+tam")] is None


# ---------------------------------------------------------------- the Tesseract command

def run_recorder(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n", b"")

    monkeypatch.setattr(subject.subprocess, "run", run)
    return calls


def test_command_names_the_folder_only_for_languages_found_there(monkeypatch, tmp_path):
    calls = run_recorder(monkeypatch)
    monkeypatch.setitem(subject._TESSDATA_CHOICE, ("tess", "eng+tam"), str(tmp_path))
    monkeypatch.setitem(subject._TESSDATA_CHOICE, ("tess", "eng"), None)
    with Image.new("L", (40, 60), 255) as image:
        subject._tesseract(image, "tess", "eng+tam", 5)
        subject._tesseract(image, "tess", "eng", 5)
        subject._tesseract(image, "tess", "fra", 5)  # never resolved: no folder
    with_folder, english, unknown = calls
    assert with_folder[with_folder.index("-l") + 1] == "eng+tam"
    assert with_folder[with_folder.index("--tessdata-dir") + 1] == str(tmp_path)
    assert "--tessdata-dir" not in english
    assert "--tessdata-dir" not in unknown


# ---------------------------------------------------------------- the second read of Aadhaar pages

AADHAAR_ENGLISH_JUNK = AADHAAR.replace("Alex Example", "Aaharapef Dio Tdliadledf")
AADHAAR_BILINGUAL = "\u0b87\u0ba8\u0bcd\u0ba4\u0bbf\u0baf \u0b85\u0bb0\u0b9a\u0bbe\u0b99\u0bcd\u0b95\u0bae\u0bcd\n" + AADHAAR


def png() -> bytes:
    with Image.new("RGB", (600, 800), "gray") as image:
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


@pytest.fixture
def scripted(monkeypatch):
    """A fake Tesseract that answers per language set; records every call."""
    calls: list[str] = []
    answers = {"eng": (AADHAAR_ENGLISH_JUNK, 60), "eng+tam": (AADHAAR_BILINGUAL, 72)}

    def read(image, command, languages, timeout, **options):
        calls.append(languages)
        text, confidence = answers[languages]
        lines = [subject.merge.Line(line, confidence, len(line.split()), (0, n / 12, 1, (n + 1) / 12))
                 for n, line in enumerate(text.splitlines()) if line.strip()]
        return subject.OCRRead(text, confidence, words=40, good_words=40, lines=lines)

    monkeypatch.setattr(subject, "_tesseract", read)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake")
    monkeypatch.setattr(subject, "_usable_languages", lambda _c, languages: languages)
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    return calls, answers


def test_an_aadhaar_page_is_read_again_with_the_extra_languages_and_the_better_read_kept(scripted):
    calls, _ = scripted
    report = subject.inspect_document(png(), languages="eng", aadhaar_languages="eng+tam", page_workers=1)
    page = report.pages[0]
    assert page.document_type == "aadhaar"
    assert calls == ["eng", "eng+tam"]
    assert "Alex Example" in page.text and "Aaharapef" not in page.text
    assert "\u0b87\u0ba8\u0bcd\u0ba4\u0bbf\u0baf" in page.text  # the Tamil line is read as Tamil, not as Latin junk
    assert page.confidence == pytest.approx(0.72)


def test_the_extra_read_is_dropped_when_it_is_worse(scripted):
    calls, answers = scripted
    answers["eng+tam"] = ("scattered words only", 30)
    page = subject.inspect_document(png(), languages="eng", aadhaar_languages="eng+tam", page_workers=1).pages[0]
    assert calls == ["eng", "eng+tam"]
    assert "Aaharapef" in page.text  # the first read stands
    assert page.document_type == "aadhaar"


def test_the_extra_read_must_keep_the_aadhaar_number(scripted, monkeypatch):
    class Number:
        def __init__(self, number, valid=True):
            self.number, self.valid = number, valid

    calls, _ = scripted
    monkeypatch.setattr(subject.validators, "find_aadhaar_numbers",
                        lambda text: [Number("234123412346")] if "Aaharapef" in text else [Number("999911112222")])
    page = subject.inspect_document(png(), languages="eng", aadhaar_languages="eng+tam", page_workers=1).pages[0]
    assert "Aaharapef" in page.text  # a read that finds another number is not trusted


def test_other_pages_are_never_read_with_the_extra_languages(scripted):
    calls, answers = scripted
    answers["eng"] = (CV, 85)
    page = subject.inspect_document(png(), languages="eng", aadhaar_languages="eng+tam", page_workers=1).pages[0]
    assert page.document_type == "resume"
    assert calls == ["eng"]


def test_no_second_read_when_it_is_switched_off_or_would_use_the_same_packs(scripted, monkeypatch):
    calls, _ = scripted
    subject.inspect_document(png(), languages="eng", aadhaar_languages="", page_workers=1)
    subject.inspect_document(png(), languages="eng", aadhaar_languages="eng", page_workers=1)
    assert calls == ["eng", "eng"]
    monkeypatch.setenv("OCR_AADHAAR_LANGUAGES", "")
    subject.inspect_document(png(), languages="eng", page_workers=1)
    assert calls == ["eng", "eng", "eng"]


def test_the_environment_chooses_the_aadhaar_languages(scripted, monkeypatch):
    calls, _ = scripted
    monkeypatch.setenv("OCR_AADHAAR_LANGUAGES", "eng+tam")
    subject.inspect_document(png(), languages="eng", page_workers=1)
    assert calls == ["eng", "eng+tam"]


def test_a_machine_without_tamil_reads_english_and_warns_once(monkeypatch, caplog):
    calls = []

    def read(image, command, languages, timeout, **options):
        calls.append(languages)
        return subject.OCRRead(AADHAAR_ENGLISH_JUNK, 60, words=40, good_words=40)

    monkeypatch.setattr(subject, "_tesseract", read)
    monkeypatch.setattr(subject, "find_tesseract", lambda _c=None: "fake")
    monkeypatch.setattr(subject, "_list_languages", listing({"eng", "osd"}))
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    with caplog.at_level(logging.WARNING, logger="crm.reading"):
        for _ in range(2):
            report = subject.inspect_document(png(), languages="eng", aadhaar_languages="eng+tam", page_workers=1)
            assert report.pages[0].document_type == "aadhaar"
    assert calls == ["eng", "eng"]
    assert sum(WARNING in r.getMessage() for r in caplog.records) == 1


# ---------------------------------------------------------------- real Tesseract with the Tamil pack

def tamil_font():
    for name in (r"C:\Windows\Fonts\Nirmala.ttc", r"C:\Windows\Fonts\Nirmala.ttf","/usr/share/fonts/truetype/lohit-tamil/Lohit-Tamil.ttf",
                 "/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf"):
        if Path(name).is_file():
            return name
    return None


def tamil_installed(monkeypatch):
    if TESSERACT is None:
        return False
    monkeypatch.setenv("OCR_TESSDATA_DIR", str(BACKEND_ROOT / "data" / "tessdata"))
    return "tam" in subject._usable_languages(TESSERACT, "eng+tam").split("+")


def test_english_words_survive_next_to_tamil_text(monkeypatch):
    if not tamil_installed(monkeypatch):
        pytest.skip("tam.traineddata is not installed (run backend/scripts/install_ocr_languages.py)")
    font_file = tamil_font()
    if font_file is None:
        pytest.skip("no font with Tamil letters is available to draw the test line")
    font = ImageFont.truetype(font_file, 56)
    tamil = "\u0b95\u0bc1\u0ba3\u0b9a\u0bc0\u0bb2\u0ba9\u0bcd \u0b95\u0bcb\u0baa\u0bbe\u0bb2\u0b95\u0bbf\u0bb0\u0bc1\u0bb7\u0bcd\u0ba3\u0ba9\u0bcd"
    with Image.new("L", (1500, 330), 255) as image:
        draw = ImageDraw.Draw(image)
        draw.text((30, 30), tamil, font=font, fill=0)
        draw.text((30, 130), "Gunavelan Ramakrishnan", font=font, fill=0)
        draw.text((30, 230), "Date of Birth: 11/09/2000  MALE", font=font, fill=0)
        languages = subject._usable_languages(TESSERACT, "eng+tam")
        both = subject._tesseract(image, TESSERACT, languages, 60, psm=6, dpi=300)
    assert languages == "eng+tam"
    assert not both.reason
    lowered = both.text.lower()
    assert "gunavelan" in lowered and "ramakrishnan" in lowered and "male" in lowered
    assert any("\u0b80" <= ch <= "\u0bff" for ch in both.text)  # Tamil is read as Tamil letters
