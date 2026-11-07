"""Combining several OCR reads of one page, and recognising identity-document patterns in text.

A page that the plain pass reads badly is read again under other settings (see ``inspection``).
Each attempt finds some lines the others miss, so the reads are merged region by region: a line
that two attempts both found keeps the better one, a line only one attempt found is added. Noise
(random symbols from photos, stamps and textured backgrounds) is dropped by a measurable rule and
never counted in the confidence. Nothing in this module reads files, images or the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
_MRZ_CHARS = re.compile(r"[A-Z0-9<]+")
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class Line:
    text: str
    conf: float
    words: int
    box: tuple[float, float, float, float]  # normalised 0..1 within the frame it was read in
    frame: str = "page"
    alnum: int = field(default=0)
    engine: str = "tesseract"

    def __post_init__(self) -> None:
        if not self.alnum:
            self.alnum = sum(c.isalnum() for c in self.text)


@dataclass
class Candidate:
    name: str
    lines: list[Line]
    score: float = 0.0


# ------------------------------------------------------------------------------ patterns

def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def mrz_lines(text: str) -> list[str]:
    """Lines that look like a machine-readable zone line: 28+ characters of A-Z, 0-9 and '<'."""
    found = []
    for raw in text.splitlines():
        line = compact(raw).upper()
        if len(line) >= 28 and _MRZ_CHARS.fullmatch(line) and line.count("<") >= 3:
            found.append(line)
    return found


def has_mrz(text: str) -> bool:
    return bool(mrz_lines(text))


def has_aadhaar_number(text: str) -> bool:
    return bool(_AADHAAR.search(text))


def has_email(text: str) -> bool:
    return bool(_EMAIL.search(text))


def has_phone(text: str) -> bool:
    return any(len(re.sub(r"\D", "", m.group())) >= 10 for m in _PHONE.finditer(text))


def pattern_bonus(text: str) -> float:
    return (30.0 if has_mrz(text) else 0.0) + (15.0 if has_aadhaar_number(text) else 0.0) \
        + (10.0 if has_email(text) else 0.0) + (10.0 if has_phone(text) else 0.0)


def read_score(confidence: float, words: int, text: str = "") -> float:
    """Mean confidence (0-100) times word count, plus bonuses for identity patterns."""
    return confidence * words / 100.0 + pattern_bonus(text)


_D = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
      (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
      (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
      (9, 8, 7, 6, 5, 4, 3, 2, 1, 0))
_P = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
      (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
      (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8))


def verhoeff_valid(digits: str) -> bool:
    """The check-digit test every Aadhaar number passes (a public algorithm, not a lookup)."""
    if not digits.isdigit() or not digits:
        return False
    check = 0
    for position, char in enumerate(reversed(digits)):
        check = _D[check][_P[position % 8][int(char)]]
    return check == 0


def normalise_mrz_line(raw: str) -> str:
    """Undo the usual misreadings of the filler character '<' (K, L, C runs) in a zone line."""
    line = compact(raw).upper()
    line = re.sub(r"(?<=<)[KLC]{2,}(?=<|$)|[KLC]{3,}$|[KLC]{4,}", lambda m: "<" * len(m.group()), line)
    line = re.sub(r"^P[KCL]", "P<", line)
    return line


# ------------------------------------------------------------------------------ line rules

def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def _is_pattern(line: Line) -> bool:
    flat = compact(line.text)
    return (flat.count("<") >= 3 and len(flat) >= 20) or has_aadhaar_number(line.text) or has_email(line.text) \
        or has_phone(line.text)


def line_is_useful(line: Line) -> bool:
    """Whether a line is text rather than noise. Identity patterns always count."""
    flat = compact(line.text)
    if not flat:
        return False
    if _is_pattern(line):
        return True
    ratio = line.alnum / len(flat)
    real = sum(1 for t in tokens(line.text) if len(t) >= 3)
    if line.conf >= 55 and line.alnum >= 2 and ratio >= 0.4:
        return True
    return line.conf >= 25 and line.alnum >= 4 and ratio >= 0.7 and real >= 1


def regroup_words(words: list[tuple[float, float, float, float, str, float]], frame: str = "page") -> list[Line]:
    """Lines from loose words (sparse-text mode returns one word per line): by height band, then left to right."""
    if not words:
        return []
    heights = sorted(w[3] - w[1] for w in words)
    typical = max(1.0, heights[len(heights) // 2])
    ordered = sorted(words, key=lambda w: (w[1] + w[3]) / 2)
    bands: list[list] = []
    for word in ordered:
        centre = (word[1] + word[3]) / 2
        if bands and abs(centre - bands[-1][0]) <= 0.6 * typical:
            bands[-1][1].append(word)
            bands[-1][0] = sum((w[1] + w[3]) / 2 for w in bands[-1][1]) / len(bands[-1][1])
        else:
            bands.append([centre, [word]])
    lines: list[Line] = []
    for _, members in bands:
        members.sort(key=lambda w: w[0])
        group = [members[0]]
        for word in members[1:]:
            if word[0] - group[-1][2] > 4 * typical:  # a separate field on the same row
                lines.append(_line_of(group, frame))
                group = []
            group.append(word)
        lines.append(_line_of(group, frame))
    return lines


def _line_of(group: list, frame: str) -> Line:
    text = " ".join(w[4] for w in group)
    conf = sum(w[5] for w in group) / len(group)
    box = (min(w[0] for w in group), min(w[1] for w in group), max(w[2] for w in group), max(w[3] for w in group))
    return Line(text, conf, len(group), box, frame)


def _overlap(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / smaller if smaller > 0 else 0.0


def _value(line: Line) -> float:
    return line.conf * line.alnum


def merge_candidates(candidates: list[Candidate]) -> tuple[list[Line], int]:
    """Merge the lines of several reads. Returns ``(useful lines in reading order, dropped noise lines)``.

    The best-scoring read supplies the order. Another read of the same geometry replaces a line
    only when it read that region clearly better, and adds regions the first one missed. Reads
    of a different geometry (a cropped card, a turned page) add lines whose words are not
    already on the page.
    """
    usable = [c for c in candidates if c.lines]
    if not usable:
        return [], 0
    usable.sort(key=lambda c: c.score, reverse=True)
    merged = [replace(l) for l in usable[0].lines]
    for candidate in usable[1:]:
        for line in candidate.lines:
            if line.frame == "page" and usable[0].lines and usable[0].lines[0].frame == "page":
                _merge_geometric(merged, line)
            else:
                _merge_by_words(merged, line)
    useful = [l for l in merged if line_is_useful(l)]
    return useful, len(merged) - len(useful)


def _merge_geometric(merged: list[Line], line: Line) -> None:
    hits = [i for i, m in enumerate(merged) if m.frame == "page" and _overlap(m.box, line.box) >= 0.5]
    if not hits:
        merged.append(line)
        return
    existing = sum(_value(merged[i]) for i in hits)
    # Replace only when the new read is clearly better and does not lose text that was really read.
    kept_text = sum(merged[i].alnum for i in hits if merged[i].conf >= 35)
    if _value(line) > 1.15 * existing and line.conf >= 0.8 * (sum(merged[i].conf for i in hits) / len(hits))             and line.alnum >= 0.8 * kept_text:
        merged[hits[0]] = line
        for i in reversed(hits[1:]):
            del merged[i]


def _merge_by_words(merged: list[Line], line: Line) -> None:
    if not line_is_useful(line):
        return
    have = {t for m in merged for t in tokens(m.text)}
    mine = tokens(line.text)
    if not mine:
        return
    known = sum(t in have for t in mine)
    if len(mine) == 1:
        if mine[0] in have and len(mine[0]) >= 3:
            return
    elif known / len(mine) >= 0.6:
        return
    merged.append(line)


def summarise(lines: list[Line]) -> tuple[str, float, int, int]:
    """``(text, mean confidence 0-100 weighted by words, words, confident words)``."""
    text = "\n".join(l.text for l in lines)
    words = sum(l.words for l in lines)
    if not words:
        return text, 0.0, 0, 0
    confidence = sum(l.conf * l.words for l in lines) / words
    good = sum(l.words for l in lines if l.conf >= 60)
    return text, confidence, words, good
