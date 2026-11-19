"""Pages written in another script (Arabic, Tamil, ...): the trigger, the probe, the choice and the cheap-clean-up rules."""
import time

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.integrations.ocr import inspection as subject
from app.integrations.ocr import page_enhance as enhance
from app.integrations.ocr import read_merge as merge
from app.integrations.ocr import script_pass as sp

ARABIC = "الجنسية"      # "nationality"
TAMIL = "சான்றிதழ்"
DEVANAGARI = "नाम"
TELUGU = "పేరు"
MALAYALAM = "പേര്"


def item(word, confidence=90.0):
    return (0.1, 0.1, 0.2, 0.12, word, confidence)


def read(words=40, good=30, confidence=80.0, text="some text", lines=None, items=None):
    return subject.OCRRead(text, confidence, words=words, good_words=good, lines=lines or [], items=items or [])


# ---------------------------------------------------------------- scripts

@pytest.mark.parametrize("char, pack", [(ARABIC[0], "ara"), (TAMIL[0], "tam"), (DEVANAGARI[0], "hin"),
                                        (TELUGU[0], "tel"), (MALAYALAM[0], "mal"), ("ಸ", "kan"),
                                        ("a", None), ("7", None), ("é", None)])
def test_each_character_belongs_to_the_pack_that_reads_it(char, pack):
    assert sp.pack_of(char) == pack


def test_only_confident_letters_are_counted_per_script():
    counts, latin = sp.count_scripts([item(TAMIL, 90), item(TAMIL, 30), item("Name", 80), item("1234", 90), item(ARABIC, 70)])
    assert counts == {"tam": len(TAMIL), "ara": len(ARABIC)}
    assert latin == 4   # digits and the low-confidence word count for nothing


def test_configured_packs_are_cleaned_and_unknown_codes_dropped():
    assert sp.enabled_packs("ara, tam+tel  hin xyz tam") == ["ara", "tam", "tel", "hin"]
    assert sp.enabled_packs("") == [] and sp.enabled_packs(None) == []


def test_a_script_is_named_only_when_it_clearly_dominates():
    allowed = ["ara", "tam"]
    assert sp.choose({"tam": 60, "ara": 3}, 5, allowed) == "tam"
    assert sp.choose({"tam": 5}, 3, allowed) == ""                     # too little to say
    assert sp.choose({"tam": 15}, 80, allowed) == ""                   # a Latin page with a Tamil stamp
    assert sp.choose({"hin": 90}, 0, allowed) == ""                    # a pack that is not enabled is never chosen
    assert sp.choose({}, 40, allowed) == ""


# ---------------------------------------------------------------- the trigger

def test_only_a_page_with_many_badly_read_words_is_poor():
    assert sp.poor_read(read(words=120, good=40, confidence=55))
    assert sp.poor_read(read(words=120, good=50, confidence=80))       # confident on average, few good words
    assert not sp.poor_read(read(words=120, good=100, confidence=85))  # reads fine: no extra pass
    assert not sp.poor_read(read(words=3, good=0, confidence=10))      # almost nothing recognised: a photo, not a script
    assert not sp.poor_read(None)


def test_adopting_the_script_read_needs_a_clear_gain():
    old = read(words=55, good=4, confidence=27)
    assert sp.adopt(read(words=56, good=32, confidence=64), old)
    assert not sp.adopt(read(words=60, good=6, confidence=30), old)    # invented glyphs on a stamp
    assert sp.adopt(read(words=90, good=40, confidence=80), read(words=90, good=40, confidence=60))
    assert not sp.adopt(read(words=90, good=40, confidence=62), read(words=90, good=40, confidence=60))
    assert not sp.adopt(read(text="  "), old)
    failed = read(words=50, good=40)
    failed.reason = "Local OCR reached the page time limit."
    assert not sp.adopt(failed, old)


def test_the_worst_lines_are_sampled_top_to_bottom_and_stacked_on_white():
    lines = [merge.Line(f"line {i}", conf, 4, (0.1, 0.05 + i * 0.05, 0.6, 0.08 + i * 0.05)) for i, conf in
             enumerate([90, 20, 85, 10, 95, 30, 15, 88, 25, 12])]
    lines.append(merge.Line("tiny", 1, 1, (0.1, 0.9, 0.12, 0.92)))     # too narrow to hold words
    boxes = sp.garbled_lines(read(lines=lines), (1000, 1400))
    assert len(boxes) == sp.SAMPLE_LINES
    assert [b[1] for b in boxes] == sorted(b[1] for b in boxes)
    assert all(b[2] - b[0] >= 400 for b in boxes)
    page = Image.new("L", (1000, 1400), 0)
    sheet = sp.stack_lines(page, boxes)
    assert sheet is not None and sheet.getpixel((2, 2)) == 255 and sheet.height > sum(b[3] - b[1] for b in boxes)
    assert sp.stack_lines(page, []) is None


# ---------------------------------------------------------------- the pass inside the page reader

class FakeTesseract:
    """Stands in for ``_tesseract``: the English read is poor, the probe sees Tamil, the Tamil read is good."""

    def __init__(self, english, probe, tamil):
        self.calls = []
        self.english, self.probe, self.tamil = english, probe, tamil

    def __call__(self, image, command, languages, timeout, *, psm=3, dpi=200, config=()):
        self.calls.append((languages, psm))
        if languages == "eng":
            return self.english
        if psm == 6 and "ara" in languages:
            return self.probe
        return self.tamil


def poor_english():
    lines = [merge.Line(f"xq zz {i}", 20.0, 3, (0.1, 0.05 + i * 0.06, 0.7, 0.09 + i * 0.06)) for i in range(10)]
    result = read(words=60, good=5, confidence=25.0, text="xq zz qx vv " * 20, lines=lines)
    result.items = [(0.1, 0.1, 0.3, 0.13, "xq", 20.0)] * 60
    return result


def run_page(monkeypatch, fake, *, need_more=None, packs=("ara", "tam")):
    monkeypatch.setattr(subject, "_tesseract", fake)
    monkeypatch.setattr(subject, "_usable_languages", lambda command, languages, warn=True: languages)
    monkeypatch.setattr(subject.second_opinion, "available", lambda: False)
    page = Image.new("L", (1700, 2200), 255)
    return subject._ocr_image(page, command="tess", languages="eng", deadline=time.monotonic() + 60, dpi=200,
                              need_more=need_more, script_packs=packs, script_probe="eng+ara+tam")


def test_a_poor_unclassified_page_is_read_again_in_its_script(monkeypatch):
    probe = read(items=[item(TAMIL, 85)] * 6, text=TAMIL)
    good = read(words=58, good=45, confidence=85.0, text="Certificate " + TAMIL + " Name Anna " * 20)
    fake = FakeTesseract(poor_english(), probe, good)
    result = run_page(monkeypatch, fake, need_more=lambda text: True)
    assert ("tam+eng", 3) in fake.calls
    assert result.notes.get("script") == "tam"
    assert "Certificate" in result.text
    assert any(name.startswith("script tam") for name in result.variants)
    # read with confidence in its own script: the other reading settings are skipped, and none uses English only again
    assert result.notes.get("stopped") == "script"
    assert all(languages != "eng" for languages, _ in fake.calls[1:])


def test_a_script_read_below_the_stop_confidence_is_judged_like_any_first_read(monkeypatch):
    # 52 is under the script pass's own stop, so the ordinary rules decide: a clean-enough unclassified read ends the page there.
    probe = read(items=[item(TAMIL, 85)] * 6, text=TAMIL)
    middling = read(words=58, good=40, confidence=52.0, text="Certificate " + TAMIL + " Name Anna " * 20)
    fake = FakeTesseract(poor_english(), probe, middling)
    result = run_page(monkeypatch, fake, need_more=lambda text: True)
    assert result.notes.get("script") == "tam" and result.notes.get("stopped") != "script"
    assert all(languages != "eng" for languages, _ in fake.calls[1:])


def test_the_script_read_must_keep_the_english_the_first_read_was_sure_of():
    old = read(words=60, good=30, confidence=55.0, items=[item(w, 90) for w in ("Religion", "Christian", "Father", "Birth")])
    lost = read(words=80, good=70, confidence=70.0, text="dots dots " + TAMIL * 5)
    kept = read(words=80, good=70, confidence=70.0, text="Religion Christian Father Birth " + TAMIL)
    assert not sp.adopt(lost, old)
    assert sp.adopt(kept, old)


def test_a_page_that_reads_fine_never_pays_for_the_pass(monkeypatch):
    clean = read(words=120, good=105, confidence=88.0, text="This is to certify that Mr Vivek Kumar worked as waiter. " * 6)
    fake = FakeTesseract(clean, read(), read())
    result = run_page(monkeypatch, fake, need_more=lambda text: True)
    assert [languages for languages, _ in fake.calls] == ["eng"]
    assert "script" not in result.notes


def test_a_poor_page_that_already_classified_in_english_is_left_alone(monkeypatch):
    # An Aadhaar front is poor in English (the state language is noise) yet classifies; the Aadhaar re-read handles it.
    text = "Government of India\nUnique Identification Authority of India\nAadhaar\n2345 6789 0123\nDOB 01/01/1990 Male\n" * 2
    poor = poor_english()
    poor.text = text
    fake = FakeTesseract(poor, read(items=[item(TAMIL, 85)] * 6), read(words=58, good=45, confidence=85.0, text=text))
    result = run_page(monkeypatch, fake, need_more=lambda text_: False)
    assert [languages for languages, _ in fake.calls] == ["eng"]
    assert "script" not in result.notes


def test_a_probe_that_finds_no_script_keeps_the_english_read(monkeypatch):
    fake = FakeTesseract(poor_english(), read(items=[item("Name", 85)] * 6, text="Name"), read())
    result = run_page(monkeypatch, fake, need_more=lambda text: True)
    assert not any(languages == "tam+eng" for languages, _ in fake.calls)
    assert "script" not in result.notes


def test_the_script_read_is_dropped_when_it_is_no_better(monkeypatch):
    probe = read(items=[item(TAMIL, 85)] * 6, text=TAMIL)
    worse = read(words=60, good=5, confidence=26.0, text="glyphs " * 30)
    fake = FakeTesseract(poor_english(), probe, worse)
    result = run_page(monkeypatch, fake, need_more=lambda text: True)
    assert ("tam+eng", 3) in fake.calls
    assert "script" not in result.notes


def test_no_packs_means_no_probe(monkeypatch):
    fake = FakeTesseract(poor_english(), read(items=[item(TAMIL, 85)] * 6), read())
    run_page(monkeypatch, fake, need_more=lambda text: True, packs=())
    assert [languages for languages, _ in fake.calls if languages != "eng"] == []


# ---------------------------------------------------------------- configuration

def test_configuration_names_the_script_packs_and_a_blank_value_turns_the_pass_off(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("OCR_SCRIPT_LANGUAGES", raising=False)
    assert Settings(_env_file=None).ocr_script_languages.split() == ["ara", "tam", "tel", "hin", "mal"]
    monkeypatch.setenv("OCR_SCRIPT_LANGUAGES", "")
    assert subject._script_languages_setting() == ""
    monkeypatch.setenv("OCR_SCRIPT_LANGUAGES", "tam hin")
    assert subject._script_languages_setting() == "tam hin"


def test_only_installed_packs_are_used(monkeypatch):
    monkeypatch.setattr(subject, "_usable_languages", lambda command, languages, warn=True: "eng+tam")   # ara is not installed
    assert subject._installed_script_packs("tess", "ara tam") == (("tam",), "eng+tam")
    monkeypatch.setattr(subject, "_usable_languages", lambda command, languages, warn=True: "eng")
    assert subject._installed_script_packs("tess", "ara tam") == ((), "")
    assert subject._installed_script_packs("tess", "") == ((), "")


# ---------------------------------------------------------------- with real Tesseract and the packs

COMMAND = subject.find_tesseract(None)


def _installed():
    if COMMAND is None:
        return set()
    own = subject._configured_tessdata_dir()
    return (subject._list_languages(COMMAND, own) if own else None) or subject._list_languages(COMMAND, None) or set()


def _font(names):
    for name in names:
        try:
            return ImageFont.truetype(name, 46, layout_engine=ImageFont.Layout.RAQM)
        except (OSError, KeyError, AttributeError):
            continue
    return None


# Arabic and Tamil letters join and reorder, which Pillow only does with the Raqm layout engine; without it the rendered text is
# not real text, so these two tests are skipped on such a machine rather than made to pass on junk.
CASES = {
    "ara": (("arial.ttf", "tahoma.ttf", "DejaVuSans.ttf"),
            ["وزارة الداخلية",
             "المملكة العربية السعودية",
             "رقم الهوية والإقامة"]),
    "tam": (("Nirmala.ttc", "Nirmala.ttf", "NotoSansTamil-Regular.ttf"),
            ["அரசுத் தேர்வுகள் துறை",
             "சான்றிதழ் வரிசை எண்",
             "இந்திய அரசு சென்னை"]),
}


@pytest.mark.parametrize("pack", ["ara", "tam"])
def test_the_probe_names_the_script_of_a_rendered_page(pack):
    from PIL import features

    installed = _installed()
    if COMMAND is None or not {"eng", pack} <= installed or not features.check("raqm"):
        pytest.skip("Tesseract, the language pack or the Raqm text layout is not available")
    fonts, lines = CASES[pack]
    font = _font(fonts)
    if font is None:
        pytest.skip("no font for this script")
    page = Image.new("L", (1700, 1200), 255)
    draw = ImageDraw.Draw(page)
    for row, line in enumerate(lines * 3):
        draw.text((80, 60 + row * 100), line, font=font, fill=0)
    languages = subject._usable_languages(COMMAND, f"eng+{pack}")
    assert pack in languages.split("+")
    probe = subject._tesseract(page, COMMAND, languages, 60, psm=6, dpi=200)
    counts, latin = sp.count_scripts(probe.items)
    assert sp.choose(counts, latin, [pack]) == pack, (counts, latin)


# ---------------------------------------------------------------- the clean-up of a shadowed page

def _shadowed_page():
    """Text bars on pale paper under a shadow that deepens toward the bottom, like a phone photo taken at an angle."""
    page = Image.new("L", (900, 1200), 235)
    draw = ImageDraw.Draw(page)
    for row in range(30):
        draw.rectangle((60, 60 + row * 36, 840, 74 + row * 36), fill=60)
    shade = Image.linear_gradient("L").resize((900, 1200)).point(lambda v: 255 - int(v * 0.5))   # 255 at the top, 128 at the bottom
    return ImageChops.multiply(page, shade)


def test_flattening_a_shadow_evens_the_paper_and_keeps_the_ink():
    shaded = _shadowed_page()
    flat = enhance.flatten_background(shaded)
    gap_before = abs(shaded.getpixel((30, 30)) - shaded.getpixel((30, 1170)))
    gap_after = abs(flat.getpixel((30, 30)) - flat.getpixel((30, 1170)))
    assert gap_before > 60 and gap_after <= 15, (gap_before, gap_after)
    assert flat.getpixel((300, 67)) < 150 and flat.getpixel((300, 1160)) > 200


def test_cv_flatten_gives_the_same_result_for_a_shadow():
    if not enhance.has_cv():
        pytest.skip("OpenCV is not installed")
    flat = enhance.cv_flatten(_shadowed_page())
    assert abs(flat.getpixel((30, 30)) - flat.getpixel((30, 1170))) <= 15
    assert flat.getpixel((300, 67)) < 150


def test_a_tilted_page_is_measured_and_a_straight_one_is_not():
    page = Image.new("L", (1400, 1900), 255)
    draw = ImageDraw.Draw(page)
    for row in range(40):
        draw.rectangle((100, 100 + row * 40, 1300, 114 + row * 40), fill=0)
    straight = subject._skew_angle(page)
    tilted = page.rotate(3.0, resample=Image.BICUBIC, fillcolor=255)
    assert straight == 0.0
    assert 2.0 <= abs(subject._skew_angle(tilted)) <= 4.0
    if enhance.has_cv():
        assert enhance.cv_deskew_angle(page) == 0.0
        assert 2.0 <= abs(enhance.cv_deskew_angle(tilted)) <= 4.0
