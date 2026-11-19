"""Reading pages written in another script (Arabic, Tamil, Telugu, Hindi, Malayalam) with the matching language pack.

The first read of every page is English only: it is the fastest, and an Indic or Arabic pack makes Tesseract invent glyphs on
photos and stamps. A page that is mostly in another script reads as noise in English, so the pass here looks at *only the
pages that read badly*:

1. ``poor_read``: the English read has low confidence or mostly low-confidence words (free, from the first read).
2. ``detect``: the script is found by reading a few of the garbled lines (cropped out and stacked in one small image) with ALL
   the installed packs at once and counting which script the confident characters belong to. A handful of lines costs about
   as much as one ordinary page read; reading the whole page with every pack would cost several times that.
3. The caller reads the whole page once with ``<pack>+eng`` and keeps it only when ``better`` says it is a clear improvement
   (``adopt``), so a page that was only dim or tilted, or a photo, is never made worse.

Nothing here runs Tesseract itself: the caller passes ``read(image, languages, psm, timeout)``, which keeps the logic testable
without Tesseract and the process budget in one place (``inspection._tesseract``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image

# Unicode blocks of the scripts the CRM reads, with the Tesseract pack that reads each.
_BLOCKS = (
    ("ara", ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))),
    ("hin", ((0x0900, 0x097F),)),
    ("tam", ((0x0B80, 0x0BFF),)),
    ("tel", ((0x0C00, 0x0C7F),)),
    ("kan", ((0x0C80, 0x0CFF),)),
    ("mal", ((0x0D00, 0x0D7F),)),
)
PACKS = tuple(pack for pack, _ in _BLOCKS)
DEFAULT_PACKS = "ara tam tel hin mal"

# A read is "poor" below this confidence, or when fewer than this share of its words were recognised with confidence.
POOR_CONFIDENCE = 62.0
POOR_GOOD_SHARE = 0.55
MIN_WORDS = 6
# A script read at least this confident is final for the page: the other reading settings are skipped.
STOP_CONFIDENCE = 60.0
STOP_MIN_WORDS = 25   # a short read (a card with a few lines) may still gain from the small-print settings
# Garbled lines sampled for the script probe, and the least text the probe must find to name a script.
SAMPLE_LINES = 7
MIN_SCRIPT_CHARS = 12
MIN_SCRIPT_SHARE = 0.30
# The packs that read a script well enough to be chosen are only trusted when the probe's characters are confident.
MIN_WORD_CONFIDENCE = 55.0


def pack_of(char: str) -> str | None:
    """The language pack of the script ``char`` is written in, or None for Latin letters, digits and marks."""
    code = ord(char)
    if code < 0x0600:
        return None
    for pack, ranges in _BLOCKS:
        if any(low <= code <= high for low, high in ranges):
            return pack
    return None


def count_scripts(items, *, min_confidence: float = MIN_WORD_CONFIDENCE) -> tuple[dict[str, int], int]:
    """({pack: letters of that script in words read with at least ``min_confidence``}, Latin letters in such words).

    ``items`` are the (left, top, right, bottom, word, confidence) tuples of an ``OCRRead``. Digits and punctuation count for
    nothing; combining marks of a script count for that script (they belong to its letters)."""
    counts: dict[str, int] = {}
    latin = 0
    for item in items:
        word, confidence = item[4], item[5]
        if confidence < min_confidence:
            continue
        for char in word:
            pack = pack_of(char)
            if pack:
                counts[pack] = counts.get(pack, 0) + 1
            elif char.isalpha():
                latin += 1
    return counts, latin


def enabled_packs(configured: str | None) -> list[str]:
    """The packs the configuration allows (``OCR_SCRIPT_LANGUAGES``: codes separated by spaces, commas or +), known ones only."""
    wanted = [part.strip().lower() for part in (configured or "").replace(",", " ").replace("+", " ").split()]
    return [pack for pack in dict.fromkeys(wanted) if pack in PACKS]


def poor_read(read) -> bool:
    """Whether an English read looks like text in a script it cannot read: many words, few of them recognised with confidence.

    A read with almost no words is not judged here (a photo, a blank or a sideways page says nothing about the script)."""
    if read is None or read.words < MIN_WORDS:
        return False
    return read.confidence < POOR_CONFIDENCE or read.good_words < POOR_GOOD_SHARE * read.words


def garbled_lines(read, size: tuple[int, int], limit: int = SAMPLE_LINES) -> list[tuple[int, int, int, int]]:
    """Pixel boxes (left, top, right, bottom) of the lines of ``read`` that read worst, the best places to look for another
    script: wide enough to hold a few words, ordered top to bottom."""
    width, height = size
    scored = []
    for line in read.lines:
        left, top, right, bottom = line.box
        box_w, box_h = (right - left) * width, (bottom - top) * height
        if box_w < 0.06 * width or box_h < 6 or box_h > 0.12 * height:
            continue
        scored.append((line.conf, -box_w, (left, top, right, bottom)))
    scored.sort(key=lambda row: (row[0], row[1]))
    chosen = [box for _, _, box in scored[:limit]]
    chosen.sort(key=lambda box: box[1])
    pad = 0.004
    return [(max(0, int((l - pad) * width)), max(0, int((t - pad) * height)),
             min(width, int((r + pad) * width)), min(height, int((b + pad) * height))) for l, t, r, b in chosen]


def stack_lines(image: Image.Image, boxes: list[tuple[int, int, int, int]], gap: int = 24) -> Image.Image | None:
    """The cropped lines one under the other on white, as one image (None when there is nothing to stack)."""
    crops = [image.crop(box) for box in boxes if box[2] - box[0] >= 20 and box[3] - box[1] >= 6]
    if not crops:
        return None
    width = max(crop.width for crop in crops) + 2 * gap
    height = sum(crop.height for crop in crops) + gap * (len(crops) + 1)
    sheet = Image.new("L", (width, height), 255)
    top = gap
    for crop in crops:
        sheet.paste(crop if crop.mode == "L" else crop.convert("L"), (gap, top))
        top += crop.height + gap
        crop.close()
    return sheet


@dataclass
class Detection:
    pack: str = ""
    counts: dict | None = None
    latin: int = 0
    seconds: float = 0.0


def choose(counts: dict[str, int], latin: int, allowed: list[str]) -> str:
    """The pack of the script that dominates ``counts`` (confident letters per script), or "" when none clearly does."""
    candidates = {pack: n for pack, n in counts.items() if pack in allowed}
    if not candidates:
        return ""
    pack, count = max(candidates.items(), key=lambda row: row[1])
    total = sum(counts.values()) + latin
    if count < MIN_SCRIPT_CHARS or count < MIN_SCRIPT_SHARE * total:
        return ""
    return pack


_LATIN_WORD = re.compile(r"[A-Za-z]{4,}")
KEEP_ENGLISH_SHARE = 0.6


def confident_english(read, confidence: float = 70.0) -> set[str]:
    """The English words (4+ letters) the English read was sure of."""
    return {item[4].lower() for item in read.items if item[5] >= confidence and _LATIN_WORD.fullmatch(item[4])}


def adopt(new, old) -> bool:
    """Whether the read with the script pack replaces the English read: clearly more recognised words, or about as many with
    clearly higher confidence, and the English the first read was sure of is still there (a pack that turns the dotted lines of
    a form into script letters and loses its English captions is no gain). A pack that only invents glyphs on a stamp or
    texture does none of this."""
    if new is None or not new.text.strip() or new.reason:
        return False
    if old is None or not old.text.strip():
        return new.good_words >= 3
    kept = confident_english(old)
    if len(kept) >= 3:
        lowered = new.text.lower()
        if sum(word in lowered for word in kept) < KEEP_ENGLISH_SHARE * len(kept):
            return False
    if new.good_words >= 1.25 * old.good_words + 2:
        return True
    return new.good_words >= 0.9 * old.good_words and new.confidence >= old.confidence + 8
