"""Local, bounded inspection before any document is sent to an OCR provider."""
from __future__ import annotations

import contextlib
import csv
import dataclasses
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageOps, ImageStat

from . import page_enhance as enhance
from . import second_opinion
from . import read_merge as merge
from . import validators
from .classification import classify_page, classify_pages, identity_hint, needs_more_reading
from .classification import _evidence as classification_evidence


class InspectionError(ValueError):
    """Actionable input/inspection failure, safe to show without document data.

    ``retryable`` marks a failure of this server rather than of the document (a missing
    converter, a timeout): trying again later can succeed, so it is never a reason to
    set the document aside.
    """

    def __init__(self, message: str = "", *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class InspectedPage:
    page_number: int
    status: str
    document_type: str
    method: str
    text: str = field(repr=False)
    confidence: float = 0.0
    reason: str = ""
    # Further kinds a page also belongs to (a passport and Aadhaar copy on one sheet).
    also: list[str] = field(default_factory=list)
    # True when the kind was taken from the adjoining document, not from the page itself.
    inherited: bool = False


_IMAGE_FORMATS = {"jpeg", "jpg", "png", "gif", "bmp", "tiff", "webp", "mpo", "heif", "heic"}


@dataclass
class Inspection:
    pages: list[InspectedPage]
    page_count: int
    source_format: str
    warnings: list[str]
    _pdf: bytes = field(repr=False)
    # Set by a caller that extracts only the confidently classified pages of a file
    # that also has unread/unclassified ones. Those pages are never selected.
    allow_partial: bool = field(default=False, repr=False)
    # What the local read covered: every page is counted as read, blank or unread.
    completeness: dict = field(default_factory=dict, repr=False)
    # Identity patterns found by the local read (values stay in memory; see ``validators.LocalPatterns``).
    patterns: "validators.LocalPatterns | None" = field(default=None, repr=False)
    # Set on the partial inspection handed out after the first look: the page reader's lock, so that cutting pages out of
    # the PDF never runs at the same moment as the reader rendering a page (PyMuPDF is not thread safe).
    _lock: object = field(default=None, repr=False)

    def _pages_of(self, kind: str) -> list[int]:
        return [p.page_number for p in self.pages
                if p.status == "read" and (p.document_type == kind or kind in p.also)]

    @property
    def resume_pages(self) -> list[int]:
        return self._pages_of("resume")

    @property
    def passport_pages(self) -> list[int]:
        return self._pages_of("passport")

    @property
    def aadhaar_pages(self) -> list[int]:
        return self._pages_of("aadhaar")

    @property
    def inherited_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.status == "read" and p.inherited]

    @property
    def decorative_image(self) -> bool:
        """A single picture with no document text at all: a logo, signature or photo."""
        if self.source_format not in _IMAGE_FORMATS or len(self.pages) != 1:
            return False
        page = self.pages[0]
        if page.status == "blank":
            return True
        return page.status == "unread" and sum(c.isalnum() for c in page.text) < 15

    @property
    def unread_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.status == "unread"]

    @property
    def unknown_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.status == "read" and p.document_type == "unknown"]

    @property
    def complete(self) -> bool:
        return len(self.pages) == self.page_count and not self.unread_pages and not self.unknown_pages

    @property
    def has_classified_pages(self) -> bool:
        return bool(self.resume_pages or self.passport_pages or self.aadhaar_pages)

    def to_dict(self, *, include_text: bool = False) -> dict:
        pages = [asdict(page) for page in self.pages]
        if not include_text:
            for page in pages:
                page.pop("text")
        return {
            "source_format": self.source_format, "page_count": self.page_count,
            "pages": pages, "resume_pages": self.resume_pages,
            "passport_pages": self.passport_pages, "aadhaar_pages": self.aadhaar_pages,
            "unread_pages": self.unread_pages, "inherited_pages": self.inherited_pages,
            "unknown_pages": self.unknown_pages, "warnings": list(self.warnings),
            "complete": self.complete, "completeness": dict(self.completeness),
        }

    def pdf_for_pages(self, page_numbers: list[int], *, allow_unresolved: bool = False) -> bytes:
        """Only verified pages; positions in the report always name the original.

        ``allow_unresolved`` also lets through pages that could not be read or identified: the caller sends them to the
        document reader as candidates (the reader decides whether they are a passport or an Aadhaar card)."""
        if not (self.complete or self.allow_partial or allow_unresolved):
            raise InspectionError("Some pages could not be read or identified. Check the page results before sending this document.")
        if not page_numbers:
            raise InspectionError("No matching pages were identified in this document.")
        if any(type(n) is not int or n < 1 or n > self.page_count for n in page_numbers):
            raise InspectionError("You chose page numbers that this document does not have.")
        wanted = sorted(set(page_numbers))
        if not allow_unresolved and any(self.pages[n - 1].status != "read" or self.pages[n - 1].document_type == "unknown" for n in wanted):
            raise InspectionError("Only pages that were read and identified can be selected.")
        with (self._lock or contextlib.nullcontext()):
            with fitz.open(stream=self._pdf, filetype="pdf") as source, fitz.open() as subset:
                for number in wanted:
                    subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
                return subset.tobytes(garbage=4, deflate=True, no_new_id=True)

    def resume_pdf(self) -> bytes:
        return self.pdf_for_pages(self.resume_pages)


@dataclass
class OCRRead:
    text: str
    confidence: float
    reason: str = ""
    # A failure of this attempt (time limit, crashed process) that one more attempt can fix.
    retryable: bool = False
    words: int = 0
    # Words recognised with real confidence: the measure used to choose between attempts.
    good_words: int = 0
    # Median height in pixels of a recognised word; small values mean small print.
    text_height: float = 0.0
    # Lines and words with boxes (0..1 of the image), kept for merging reads of one page.
    lines: list = field(default_factory=list, repr=False)
    items: list = field(default_factory=list, repr=False)
    # How the text was obtained: the best setting, every setting tried, and measures (never text).
    setting: str = ""
    variants: list = field(default_factory=list)
    raw_confidence: float = 0.0
    notes: dict = field(default_factory=dict)
    # True when the page is a picture with almost no text (a photo page, a card photo).
    photo: bool = False


# Pixels per inch of the first pass. 200 reads normal print as well as 300 and is faster.
OCR_BASE_DPI = 200
# Second look at small print or a first pass with low confidence.
OCR_HIGH_DPI = 300
_ACCEPT_CONFIDENCE = 72.0
_SMALL_TEXT_PX = 20
_MAX_RENDER_PIXELS = 40_000_000
_MAX_IMAGE_FRAMES = 500
_DEFAULT_PAGE_WORKERS = max(1, min(8, os.cpu_count() or 2))
_LANGUAGE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*")

try:  # HEIC/HEIF photos are read when the optional decoder is installed.
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass


class _BitmapGauge:
    """Counts page bitmaps alive at once, so memory stays provably bounded."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.alive = 0
        self.peak = 0

    def reset(self) -> None:
        with self._lock:
            self.alive = self.peak = 0

    def hold(self) -> "_BitmapGauge._Hold":
        return _BitmapGauge._Hold(self)

    class _Hold:
        def __init__(self, gauge: "_BitmapGauge") -> None:
            self._gauge = gauge

        def __enter__(self) -> None:
            with self._gauge._lock:
                self._gauge.alive += 1
                self._gauge.peak = max(self._gauge.peak, self._gauge.alive)

        def __exit__(self, *exc) -> None:
            with self._gauge._lock:
                self._gauge.alive -= 1


_GAUGE = _BitmapGauge()


def find_tesseract(command: str | None = None) -> str | None:
    if command:
        return shutil.which(command) or (str(Path(command)) if Path(command).is_file() else None)
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        fallback = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if fallback.is_file():
            return str(fallback)
    return None


def _find_libreoffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    if os.name == "nt":
        fallback = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
        if fallback.is_file():
            return str(fallback)
    return None


_LANGUAGE_CACHE: dict[tuple[str, str], str] = {}


def _usable_languages(command: str, languages: str) -> str:
    """The requested language packs that are installed (``eng`` when none are).

    A missing pack makes Tesseract fail every page, so it is dropped up front rather than
    discovered page by page.
    """
    key = (command, languages)
    if key in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[key]
    wanted = languages if _LANGUAGE.fullmatch(languages or "") else "eng"
    chosen = wanted
    try:
        result = subprocess.run([command, "--list-langs"], capture_output=True, timeout=15, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            installed = {line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines()[1:]}
            kept = [lang for lang in wanted.split("+") if lang in installed]
            chosen = "+".join(kept) if kept else ("eng" if "eng" in installed else wanted)
    except (OSError, subprocess.SubprocessError):
        pass
    _LANGUAGE_CACHE[key] = chosen
    return chosen


def _usable_native(text: str) -> bool:
    """Whether a page's embedded text is a real, complete text layer and not stray characters.

    Besides damaged glyph mappings and letter-spaced text, a layer must carry enough words, be
    mostly letters and digits, and not be made of single-character fragments.
    """
    visible = [c for c in text if not c.isspace()]
    if len(visible) < 80:
        return False
    damaged = sum(c == "\ufffd" or unicodedata.category(c) in {"Cc", "Cs", "Co", "Cn"} for c in visible)
    if damaged / len(visible) >= 0.02 or re.search(r"\b[A-Za-z]\s[A-Za-z]\s[A-Za-z]\s[A-Za-z]\b", text):
        return False
    words = [token for token in text.split() if any(c.isalnum() for c in token)]
    if len(words) < 12:
        return False
    if sum(c.isalnum() for c in visible) / len(visible) < 0.5:
        return False
    fragments = sum(sum(c.isalnum() for c in token) == 1 for token in words)
    return fragments / len(words) <= 0.35


def _blank(image: Image.Image) -> bool:
    # Empty OCR is never evidence of a blank page. Require a render that is white apart
    # from a few scanner specks.
    grey = image if image.mode == "L" else image.convert("L")
    if grey.getextrema()[0] >= 245:
        return True
    histogram = grey.histogram()
    # Ink is whatever is clearly darker than the paper: a faint fax or a pale photocopy has no pixel below 160
    # yet is far from blank, so the cut follows the paper tone.
    total, running, paper = sum(histogram), 0, 255
    for level, count in enumerate(histogram):
        running += count
        if running >= total / 2:
            paper = level
            break
    return sum(histogram[:max(160, min(200, paper - 30))]) <= 20


def _parse_tsv(raw: str, size: tuple[int, int] | None = None) -> OCRRead:
    lines: dict[tuple[str, ...], list[tuple]] = {}
    scores: list[float] = []
    heights: list[int] = []
    seen_header = False
    for row in raw.split("\n"):
        row = row.rstrip("\r")
        if not row:
            continue
        columns = row.split("\t", 11)
        if columns[0] == "level":
            seen_header = True
            continue
        if len(columns) < 12:
            continue
        try:
            level, confidence = int(columns[0]), float(columns[10])
        except ValueError:
            continue
        word = columns[11].strip()
        if level != 5 or not word or confidence < 0:
            continue
        try:
            left, top, width, height = (int(columns[i]) for i in (6, 7, 8, 9))
        except ValueError:
            left = top = width = height = 0
        lines.setdefault(tuple(columns[1:5]), []).append((left, top, left + width, top + height, word, confidence))
        scores.append(confidence)
        if sum(c.isalnum() for c in word) >= 2:
            heights.append(height)
    if raw.strip() and not seen_header and not scores:
        return OCRRead("", 0, "Tesseract returned an unreadable response.")
    heights.sort()
    scale_x, scale_y = (1.0 / size[0], 1.0 / size[1]) if size and size[0] and size[1] else (1.0, 1.0)
    items = [(w[0] * scale_x, w[1] * scale_y, w[2] * scale_x, w[3] * scale_y, w[4], w[5]) for ws in lines.values() for w in ws]
    built = [merge.Line(" ".join(w[4] for w in ws), sum(w[5] for w in ws) / len(ws), len(ws),
                        (min(w[0] for w in ws) * scale_x, min(w[1] for w in ws) * scale_y,
                         max(w[2] for w in ws) * scale_x, max(w[3] for w in ws) * scale_y))
             for ws in lines.values()]
    return OCRRead(
        "\n".join(" ".join(w[4] for w in words) for words in lines.values()),
        sum(scores) / len(scores) if scores else 0,
        words=len(scores), good_words=sum(score >= 60 for score in scores),
        text_height=float(heights[len(heights) // 2]) if heights else 0.0,
        lines=built, items=items,
    )


class _ProcessBudget:
    """At most ``limit`` Tesseract processes at once in this process, however many threads ask.

    The extraction queue runs several documents, each with several page workers; without one
    shared budget their product would swamp the machine. A caller that cannot get a slot in time
    gets a retryable time-limit read, exactly like a slow page.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._slots = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self.running = 0
        self.peak = 0

    def acquire(self, timeout: float) -> bool:
        if not self._slots.acquire(timeout=max(0.0, timeout)):
            return False
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        return True

    def release(self) -> None:
        with self._lock:
            self.running -= 1
        self._slots.release()


def _default_process_limit() -> int:
    try:
        configured = int(os.environ.get("OCR_MAX_TESSERACT_PROCESSES", "0"))
    except ValueError:
        configured = 0
    return configured if configured > 0 else max(2, 2 * (os.cpu_count() or 2))


_PROCESSES = _ProcessBudget(_default_process_limit())


def set_process_limit(limit: int) -> _ProcessBudget:
    """Replace the shared Tesseract process budget (used by tests and by deployments with other needs)."""
    global _PROCESSES
    _PROCESSES = _ProcessBudget(limit)
    return _PROCESSES


def _tesseract(image: Image.Image, command: str, languages: str, timeout: float, *,
               psm: int = 3, dpi: int = OCR_BASE_DPI, config: tuple[str, ...] = ()) -> OCRRead:
    started = time.monotonic()
    budget = _PROCESSES
    if not budget.acquire(timeout):
        return OCRRead("", 0, "Local OCR reached the page time limit.", retryable=True)
    try:
        timeout = max(0.01, timeout - (time.monotonic() - started))
        with tempfile.TemporaryDirectory(prefix="crm-page-ocr-") as directory:
            image_path = Path(directory) / "page.png"
            image.save(image_path, format="PNG", compress_level=1)
            env = {**os.environ, "OMP_THREAD_LIMIT": os.environ.get("OMP_THREAD_LIMIT", "1")}
            # LSTM engine only; keep the space between words. Some trained-data sets are
            # legacy-only, so a rejected --oem is retried once without it.
            for use_oem in (True, False):
                args = [command, str(image_path), "stdout", "-l", languages, "--dpi", str(int(dpi)), "--psm", str(psm)]
                if use_oem:
                    args += ["--oem", "1"]
                args += ["-c", "preserve_interword_spaces=1"]
                for option in config:
                    args += ["-c", option]
                args += ["tsv"]
                try:
                    result = subprocess.run(
                        args, capture_output=True, timeout=max(0.01, timeout), check=False, env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except subprocess.TimeoutExpired:
                    return OCRRead("", 0, "Local OCR reached the page time limit.", retryable=True)
                except OSError:
                    return OCRRead("", 0, "Tesseract could not start. Install it or configure TESSERACT_CMD.")
                if result.returncode and use_oem and b"oem" in result.stderr.lower():
                    continue
                break
    finally:
        budget.release()
    if result.returncode:
        return OCRRead("", 0, "We could not read this page. Ask your system administrator to check the document reader's language settings.",
                       retryable=True)
    return _parse_tsv(result.stdout.decode("utf-8", errors="replace"), image.size)


def _skew_angle(image: Image.Image) -> float:
    """Degrees to turn a scan so its lines run level (0 when it is straight or has no lines).

    Looks for the turn that makes the rows of ink most uneven (text lines against white gaps),
    on a small copy, so it costs a few milliseconds per page.
    """
    if min(image.size) < 300:
        return 0.0
    small = image if image.mode == "L" else image.convert("L")
    small = small.copy()
    small.thumbnail((600, 600))
    ink = ImageOps.invert(small).point(lambda v: 255 if v > 100 else 0)

    def sharpness(angle: float) -> float:
        turned = ink.rotate(angle, resample=Image.BILINEAR, fillcolor=0) if angle else ink
        return ImageStat.Stat(turned.resize((1, turned.height), Image.BOX)).var[0]

    base = sharpness(0)
    if base <= 0 and sharpness(2) <= 0:
        return 0.0
    best, best_score = 0.0, base
    for step, span in ((2.0, 10.0), (0.5, 1.5)):
        centre, angle = best, best - span
        while angle <= centre + span + 1e-9:
            if abs(angle) <= 12.0:
                score = sharpness(angle)
                if score > best_score:
                    best, best_score = angle, score
            angle += step
    return best if abs(best) >= 1.0 and best_score > base * 1.08 else 0.0


def _better(candidate: OCRRead, current: OCRRead) -> bool:
    if not candidate.text.strip():
        return False
    if not current.text.strip():
        return True
    return (candidate.good_words, candidate.confidence) > (current.good_words, current.confidence)


def _accepted(read: OCRRead) -> bool:
    return read.confidence >= _ACCEPT_CONFIDENCE and read.words >= 1


# Seconds a hard page may spend on extra reading settings (not counting the first pass), and on the
# targeted passport/Aadhaar passes. Clean pages never reach either.
LADDER_SECONDS = 4.5
SPECIAL_SECONDS = 2.5
# Share of a page's ink that must lie under recognised words for the first pass to be trusted.
_MIN_COVERAGE = 0.5
# Extra reading settings, in order: (name, image preparation, Tesseract page-segmentation mode).
_LADDER = (
    ("sparse", "grey", 11),
    ("flat", "flat", 6),
    ("contrast", "clahe", 3),
    ("binary", "adapt", 6),
    ("sharp", "sharp", 3),
    ("red", "redflat", 3),
)
_MRZ_CONFIG = ("tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<", "load_system_dawg=0", "load_freq_dawg=0")
_DIGIT_CONFIG = ("tessedit_char_whitelist=0123456789", "load_system_dawg=0", "load_freq_dawg=0")
_PASSPORT_HINT = re.compile(r"passport|republic of|\bp<[a-z]{3}|nationality|place of (?:birth|issue)|surname|given name", re.I)
_AADHAAR_HINT = re.compile(r"aadhaar|aadhar|uidai|unique identification|government of india|\bvid\b|enrol|year of birth|\bdob\b|\bmale\b|\bfemale\b", re.I)


def _second_deadline(window_end: float) -> float:
    """When to stop waiting for the second engine: most of the page window, but never less than its own
    run allowance, so a busy machine slows a hard page down instead of costing it the better read."""
    now = time.monotonic()
    if window_end - now < second_opinion.MIN_SECONDS_LEFT:
        return now  # the page window is (nearly) used up: no second engine, the time limit stands
    return now + max((window_end - now) * 0.6, second_opinion.RUN_SECONDS)


def _prepare(kind: str, hi: Image.Image, color) -> Image.Image | None:
    if kind == "grey":
        return hi
    cv = enhance.has_cv()
    if kind == "flat":
        return enhance.cv_flatten(hi) if cv else enhance.flatten_background(hi)
    if kind == "clahe":
        return enhance.cv_clahe(hi) if cv else enhance.local_contrast(hi)
    if kind == "adapt":
        return enhance.cv_adaptive(hi) if cv else enhance.local_threshold(hi)
    if kind == "sharp":
        return enhance.sharpen(enhance.cv_denoise(hi) if cv else enhance.denoise(hi))
    if kind == "redflat":
        rgb = color()
        if rgb is None:
            return None
        red = enhance.red_channel(rgb)
        return enhance.cv_flatten(red) if cv else enhance.flatten_background(red)
    return None


def _ocr_image(image: Image.Image, *, command: str | None, languages: str, deadline: float,
               render=None, dpi: int = OCR_BASE_DPI, render_color=None, hard_deadline: float | None = None,
               need_more=None, fast: bool = False) -> OCRRead:
    """Read one page image, escalating only as far as the page needs.

    One fast pass is enough for a clean, well covered page. Otherwise the page is read again under
    other settings (orientation-aware layout, 300 dpi sparse text, sharpened, background-flattened,
    contrast-stretched, binarised, red channel, a cut-out photographed card), each attempt adding
    what the others missed; the reads are merged and noise dropped. Targeted passes then read a
    passport's machine-readable zone and an Aadhaar number. ``render(dpi)`` and
    ``render_color(dpi)`` return a fresh image of the same page at another resolution.

    ``need_more(text)`` (optional) says whether the text read so far could still change what the page is taken to be;
    once it answers False the general ladder (second engine, card cut-out, extra settings, deskew, rotation) stops. The
    targeted passport/Aadhaar passes keep their own conditions. Without it, only the read quality decides.
    """
    if not command:
        return OCRRead("", 0, "Tesseract is unavailable. Install it or configure TESSERACT_CMD, then retry.")
    best = OCRRead("", 0, "No readable text was found; the page is not confirmed blank.")
    best_name = ""
    problem: OCRRead | None = None
    timed_out = False
    opened: list[Image.Image] = []
    tried: list[str] = []
    candidates: list[merge.Candidate] = []

    def attempt(name: str, source: Image.Image, psm: int, resolution: int, *, frame: str = "page",
                config: tuple[str, ...] = (), sparse: bool = False) -> OCRRead | None:
        nonlocal best, best_name, problem, timed_out
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            return None
        options = {"config": config} if config else {}
        read = _tesseract(source, command, languages, remaining, psm=psm, dpi=resolution, **options)
        tried.append(name)
        if not read.text.strip() and read.reason:
            problem = problem or read
            timed_out = timed_out or read.retryable and "time limit" in read.reason
        if _better(read, best):
            best, best_name = read, name
        lines = merge.regroup_words(read.items, frame) if sparse and read.items else \
            [dataclasses.replace(l, frame=frame) for l in read.lines]
        if lines:
            candidates.append(merge.Candidate(name, lines, merge.read_score(read.confidence, read.words, read.text)))
        return read

    started = time.monotonic()
    first = attempt(f"base psm3 {dpi}dpi", image, 3, dpi)
    if fast and first is not None:
        # The quick first look of a two-pass inspection: one Tesseract call, whatever it read. Pages that turn out
        # unclassified are read again with the full ladder.
        first.setting, first.variants, first.raw_confidence = tried[0], list(tried), first.confidence
        first.notes = {"engine": "tesseract", "stopped": "fast"}
        return first
    small = bool(first and first.words >= 10 and 0 < first.text_height < _SMALL_TEXT_PX)
    covered = True
    if first and first.lines and first.text.strip():
        try:
            coverage = enhance.text_coverage(image, [l.box for l in first.lines])
        except Exception:  # a measure only; never a reason to fail the page
            coverage = None
        covered = coverage is None or coverage >= _MIN_COVERAGE
    if first and need_more is not None and first.text.strip() and not timed_out:
        try:
            decided = len(first.text.strip()) >= 40 and not need_more(first.text)
        except Exception:
            decided = False
        if decided and not (_accepted(first) and not small and covered):
            # Classified (or full of readable, plainly ordinary text) already: no other settings are tried.
            first.setting, first.variants, first.raw_confidence = tried[0], list(tried), first.confidence
            first.notes = {"engine": "tesseract", "stopped": "classified"}
            return first
    if first and _accepted(first) and not small and covered:
        first.setting, first.variants, first.raw_confidence = tried[0], list(tried), first.confidence
        first.notes = {"engine": "tesseract"}
        return first
    if problem is not None and not best.text.strip() and not timed_out:
        return problem

    ladder_start = time.monotonic()
    extra_lines: list[merge.Line] = []
    notes: dict = {}
    engines_tried: list[str] = ["tesseract"]
    photo_like = False

    def can(limit: float = LADDER_SECONDS) -> bool:
        return not timed_out and deadline - time.monotonic() > 0.3 and time.monotonic() - ladder_start < limit

    def current() -> tuple[list, int, str, float, int, int]:
        if not candidates:
            return [], 0, "", 0.0, 0, 0
        lines, dropped = merge.merge_candidates(candidates)
        text, confidence, words, good = merge.summarise(lines)
        return lines, dropped, text, confidence, words, good

    def classified() -> bool:
        if need_more is None:
            return False
        text_so_far = current()[2] if candidates else best.text
        try:
            return len(text_so_far.strip()) >= 40 and not need_more(text_so_far)
        except Exception:
            return False

    def settled() -> bool:
        if classified():
            return True
        if not candidates:
            return _accepted(best)
        lines, _, _, confidence, words, _ = current()
        if confidence < _ACCEPT_CONFIDENCE or words < 3:
            return False
        try:
            coverage = enhance.text_coverage(image, [l.box for l in lines if l.frame == "page"])
        except Exception:
            coverage = None
        return coverage is None or coverage >= 0.4

    try:
        # Nearly nothing recognised and a big dark picture: look once for small print, then call it a photo.
        if first and first.words <= 3:
            try:
                photo_like = enhance.looks_like_photo(enhance.photo_metrics(image))
            except Exception:
                photo_like = False
        hi: Image.Image | None = None
        hi_dpi = OCR_HIGH_DPI

        def get_hi() -> Image.Image | None:
            nonlocal hi, hi_dpi
            if hi is None:
                if dpi >= OCR_HIGH_DPI or render is None:
                    hi, hi_dpi = image, dpi
                else:
                    hi = render(OCR_HIGH_DPI)
                    opened.append(hi)
            return hi

        color_cache: list[Image.Image | None] = []

        def color() -> Image.Image | None:
            if not color_cache:
                rgb = render_color(OCR_HIGH_DPI) if render_color is not None else None
                if rgb is not None:
                    opened.append(rgb)
                color_cache.append(rgb)
            return color_cache[0]

        if first is not None and (first.confidence < 45 or first.words < 3) and not photo_like and can():
            attempt("orientation psm1", image, 1, dpi)

        # The second local engine reads what Tesseract could not (photos of cards, textured backgrounds).
        if not timed_out and not settled() and second_opinion.available():
            opinion = second_opinion.read_image(image, deadline=_second_deadline(deadline))
            engines_tried.append("rapidocr")
            # The second engine's run is not charged to the page window that Tesseract's later steps share.
            deadline = min(hard_deadline or float("inf"), deadline + opinion.seconds)
            if opinion.lines:
                tried.append("rapidocr")
                candidates.append(merge.Candidate("rapidocr", opinion.lines, merge.read_score(
                    opinion.confidence, opinion.words, "\n".join(l.text for l in opinion.lines))))
            elif opinion.error:
                notes["second_engine"] = opinion.error[:80]
        ladder_start = time.monotonic()

        if small and render is not None and can():
            base = get_hi()
            if base is not None:
                attempt("small-print psm3", base, 3, hi_dpi)

        # A photographed card on a darker surface: cut it out and straighten it.
        if not photo_like and can() and not settled():
            try:
                quad = enhance.cv_document_quad(image) if enhance.has_cv() else enhance.find_document_quad(image)
            except Exception:
                quad = None
            base = get_hi() if quad else None
            if quad and base is not None:
                factor = base.width / image.width
                corners = [(x * factor, y * factor) for x, y in quad[0]]
                crop = enhance.cv_warp(base, corners) if enhance.has_cv() else enhance.crop_to_document(base, corners)
                opened.append(crop)
                if crop.width < 1800:
                    crop = enhance.upscale(crop, min(2.5, 1800 / crop.width))
                    opened.append(crop)
                notes["card_found"] = True
                if second_opinion.available() and not timed_out:
                    opinion = second_opinion.read_image(crop, deadline=_second_deadline(deadline), frame="card")
                    deadline = min(hard_deadline or float("inf"), deadline + opinion.seconds)
                    if opinion.lines:
                        tried.append("card-crop rapidocr")
                        candidates.append(merge.Candidate("card-crop rapidocr", opinion.lines, merge.read_score(
                            opinion.confidence, opinion.words, "\n".join(l.text for l in opinion.lines))))
                flat = enhance.cv_flatten(crop) if enhance.has_cv() else enhance.flatten_background(crop)
                opened.append(flat)
                attempt("card-crop psm11", flat, 11, hi_dpi, frame="card", sparse=True)
                if can() and not settled():
                    attempt("card-crop psm6", flat, 6, hi_dpi, frame="card")

        for name, kind, psm in (_LADDER[:1] if photo_like else _LADDER):
            if not can() or settled():
                break
            base = get_hi()
            if base is None:
                break
            if kind == "redflat" and color() is None:
                continue
            prepared = _prepare(kind, base, color)
            if prepared is None:
                continue
            if prepared is not base:
                opened.append(prepared)
            attempt(f"{name} psm{psm}", prepared, psm, hi_dpi, sparse=psm in (11, 12))

        # A tilted page that the plain pass could not level by itself.
        if not photo_like and can() and not settled():
            skew = enhance.cv_deskew_angle(image) if enhance.has_cv() else _skew_angle(image)
            base = get_hi() if skew else None
            if skew and base is not None:
                level = base.rotate(skew, resample=Image.BICUBIC, expand=True, fillcolor=255)
                opened.append(level)
                attempt("deskew psm3", level, 3, hi_dpi, frame="deskew")

        # Sideways or upside-down text that never read well: turn the page and look again.
        _, _, text, confidence, _, confident_words = current()
        # Upright text always yields some confident words; only a page that yields none is worth turning.
        if not photo_like and not settled() and confident_words < 6 and (confidence < 45 or len(text.strip()) < 60) and not timed_out \
                and best.words >= 3 and time.monotonic() - ladder_start < LADDER_SECONDS + 3:
            base = get_hi() or image
            for angle in (90, 270, 180):
                if timed_out or deadline - time.monotonic() <= 0.3:
                    break
                turned = base.rotate(angle, expand=True, fillcolor=255)
                try:
                    attempt(f"rotate{angle} psm3", turned, 3, hi_dpi, frame=f"rot{angle}")
                finally:
                    turned.close()
                _, _, _, c2, w2, _ = current()
                if c2 >= _ACCEPT_CONFIDENCE and w2 >= 8:
                    break

        # Targeted passes: a passport's machine-readable zone, and an Aadhaar number.
        special_start = time.monotonic()
        lines, _, text, _, _, _ = current()
        mrz_state = validators.find_mrz(text) if text else None
        if text and not timed_out and (mrz_state is None or not mrz_state.valid) \
                and (_PASSPORT_HINT.search(text) or text.count("<") >= 3):
            base = get_hi()
            if base is not None:
                band = None
                for line in lines:
                    if line.frame == "page" and line.text.count("<") >= 3:
                        band = (max(0.0, line.box[1] - 0.08), min(1.0, line.box[3] + 0.12))
                        break
                top, bottom = band or (0.6, 1.0)
                crop = base.crop((0, int(top * base.height), base.width, int(bottom * base.height)))
                opened.append(crop)
                best_zone, best_checks = None, sum(mrz_state.checks.values()) if mrz_state is not None else -1
                # Looks at the zone, best first: the second engine on the cropped strip (it reads these lines at full
                # size), then Tesseract restricted to the MRZ alphabet, background-flattened and plain.
                for label, psm, prep in (("rapid", 0, None),
                                         ("flat", 6, enhance.cv_flatten if enhance.has_cv() else enhance.flatten_background),
                                         ("plain", 11, None)):
                    if timed_out or (label != "rapid" and time.monotonic() - special_start > SPECIAL_SECONDS):
                        break
                    if label == "rapid":
                        if not second_opinion.available():
                            continue
                        opinion = second_opinion.read_image(crop, deadline=_second_deadline(deadline), frame="mrz")
                        deadline = min(hard_deadline or float("inf"), deadline + opinion.seconds)
                        tried.append("mrz rapidocr")
                        zone_lines = opinion.lines
                    else:
                        prepared = prep(crop) if prep else crop
                        if prepared is not crop:
                            opened.append(prepared)
                        read = attempt(f"mrz {label} psm{psm}", prepared, psm, hi_dpi, frame="mrz", config=_MRZ_CONFIG)
                        if read is None or not read.lines:
                            continue
                        zone_lines = read.lines
                    zone = [merge.Line(merge.normalise_mrz_line(l.text), l.conf, l.words, l.box, "mrz", engine=l.engine)
                            for l in zone_lines if len(merge.compact(l.text)) >= 28]
                    fresh = validators.find_mrz(chr(10).join(l.text for l in zone))
                    if fresh is not None and sum(fresh.checks.values()) > best_checks:
                        best_zone, best_checks = zone, sum(fresh.checks.values())
                    if fresh is not None and fresh.valid:
                        break
                if best_zone:
                    extra_lines.extend(best_zone)
                    notes["mrz_pass"] = True
        if text and not timed_out and not any(r.valid for r in validators.find_aadhaar_numbers(text)) \
                and (_AADHAAR_HINT.search(text) or len(re.findall(r"(?<!\d)\d{4}(?!\d)", text)) >= 2) \
                and time.monotonic() - special_start < SPECIAL_SECONDS + 1.0:
            base = get_hi()
            if base is not None:
                prepared = enhance.flatten_background(base)
                opened.append(prepared)
                read = attempt("aadhaar-digits psm11", prepared, 11, hi_dpi, frame="digits", config=_DIGIT_CONFIG, sparse=True)
                if read is not None and read.items:
                    regrouped = merge.regroup_words(read.items, "digits")
                    found = validators.find_aadhaar_numbers("\n".join(l.text for l in regrouped))
                    good_numbers = [n for n in found if n.valid]
                    if good_numbers and not any(n.number in re.sub(r"\D", "", text) for n in good_numbers):
                        for n in good_numbers[:2]:
                            extra_lines.append(merge.Line(f"{n.number[:4]} {n.number[4:8]} {n.number[8:]}", 90.0, 3,
                                                          (0.0, 0.0, 0.0, 0.0), "digits"))
                        notes["aadhaar_pass"] = True
    finally:
        for extra in opened:
            if extra is not image:
                extra.close()

    lines, dropped, text, confidence, words, good = current()
    if extra_lines:
        if any(l.frame == "mrz" for l in extra_lines):
            lines = [l for l in lines if l.text.count("<") < 3] + [l for l in extra_lines if l.frame == "mrz"]
        lines = lines + [l for l in extra_lines if l.frame == "digits"]
        text, confidence, words, good = merge.summarise(lines)
    kept_engines = {l.engine for l in lines}
    notes.update({"noise_lines_dropped": dropped, "seconds": round(time.monotonic() - started, 2),
                  "engine": "both" if len(kept_engines) > 1 else (next(iter(kept_engines)) if kept_engines else "tesseract")})

    # The merged read replaces the single best read unless it lost real text.
    use_merged = bool(lines) and len(text.strip()) >= min(40, len(best.text.strip()))
    if use_merged:
        result = OCRRead(text, confidence, words=words, good_words=good, text_height=best.text_height, lines=lines)
    else:
        result = best
    result.setting = best_name if not use_merged or len(candidates) <= 1 else "merged"
    result.variants = list(tried)
    result.raw_confidence = best.confidence
    result.notes = notes
    length = len(result.text.strip())
    if timed_out and not (length >= 40 and (result.confidence >= 45 or (length >= 150 and result.confidence >= 25))):
        return OCRRead(result.text, 0, "Local OCR reached the page or document time limit.", retryable=True,
                       variants=list(tried))
    if not result.text.strip() and problem is not None:
        return problem
    if sum(c.isalnum() for c in result.text) < 15 and first is not None and first.words <= 3:
        try:
            result.photo = photo_like or enhance.looks_like_photo(enhance.photo_metrics(image))
        except Exception:
            result.photo = False
    return result


def _merge_native(native: str, ocr: str, usable: bool) -> str:
    """Page text from both sources. Nothing the embedded layer says is lost."""
    if usable:
        return native + "\n" + ocr if native not in ocr else ocr
    squashed = re.sub(r"\s+", " ", ocr).casefold()
    extra: list[str] = []
    for line in native.splitlines():
        line = line.strip()
        words = line.split()
        if sum(c.isalnum() for c in line) < 4 or any(c == "\ufffd" or unicodedata.category(c) in {"Cc", "Cs", "Co", "Cn"} for c in line):
            continue
        if words and sum(len(word) == 1 for word in words) / len(words) > 0.5:
            continue
        if re.sub(r"\s+", " ", line).casefold() not in squashed:
            extra.append(line)
    return ocr + ("\n" + "\n".join(extra) if extra else "")


def _word_pdf(data: bytes, suffix: str, deadline: float) -> bytes:
    command = _find_libreoffice()
    if not command:
        raise InspectionError("We cannot open Word documents on this server right now. Save the document as a PDF and upload that instead.", retryable=True)
    with tempfile.TemporaryDirectory(prefix="crm-word-inspection-") as directory:
        root = Path(directory)
        source = root / f"document.{suffix}"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                [command, f"-env:UserInstallation={(root / 'profile').as_uri()}", "--headless",
                 "--norestore", "--convert-to", "pdf", "--outdir", str(root), str(source)],
                capture_output=True, timeout=max(0.01, min(120, deadline - time.monotonic())),
                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise InspectionError("We could not convert this Word document. Save it as a PDF and upload that instead.", retryable=True) from None
        output = root / "document.pdf"
        if result.returncode or not output.is_file():
            raise InspectionError("We could not convert this Word document. Save it as a PDF and upload that instead.")
        return output.read_bytes()


def _flatten(frame: Image.Image) -> Image.Image:
    """An upright RGB/greyscale copy of one image frame; transparency becomes white paper."""
    upright = ImageOps.exif_transpose(frame)
    if upright.mode in ("RGBA", "LA") or "transparency" in upright.info:
        rgba = upright.convert("RGBA")
        paper = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(paper, rgba).convert("RGB")
    return upright.convert("L" if upright.mode in ("1", "L") else "RGB")


def _normalize(data: bytes, deadline: float) -> tuple[bytes, str]:
    if not data:
        raise InspectionError("The file you uploaded is empty.")
    if b"%PDF-" in data[:1024]:
        return data, "pdf"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return _word_pdf(data, "docx", deadline), "docx"
        except zipfile.BadZipFile:
            pass
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _word_pdf(data, "doc", deadline), "doc"
    try:
        with Image.open(io.BytesIO(data)) as image:
            source_format = (image.format or "image").lower()
            frames = getattr(image, "n_frames", 1)
            # Every frame of a multi-page TIFF is a page. Other multi-frame images
            # (animations, burst photos) are not documents.
            if frames != 1 and source_format != "tiff":
                raise InspectionError("This image has several frames, which we cannot read. Save every frame in one multi-page PDF and upload that.")
            if frames > _MAX_IMAGE_FRAMES:
                raise InspectionError(f"This image has more than {_MAX_IMAGE_FRAMES} frames, which is too many to read.")
            with fitz.open() as picture:
                for index in range(frames):
                    image.seek(index)
                    if image.width * image.height > 40_000_000:
                        raise InspectionError("This image is too large to read. Make it smaller and upload it again.")
                    image.load()
                    with _flatten(image) as upright:
                        width, height = upright.size
                        normalized = io.BytesIO()
                        upright.save(normalized, format="PNG")
                    page = picture.new_page(width=width * 72 / 300, height=height * 72 / 300)
                    page.insert_image(page.rect, stream=normalized.getvalue())
                return picture.tobytes(deflate=True, no_new_id=True), source_format
    except InspectionError:
        raise
    except Exception:
        raise InspectionError("We cannot open this file. Upload a PDF, a Word document or a normal photo.") from None


def _default_workers() -> int:
    try:
        from app.core.config import get_settings

        return max(1, int(get_settings().ocr_page_workers))
    except Exception:
        return _DEFAULT_PAGE_WORKERS


class _PageReader:
    """Reads the pages of one PDF, any number at a time.

    PyMuPDF is not thread safe, so every use of the document is serialised by one lock;
    that is only text extraction and rendering (milliseconds). The slow part, Tesseract,
    runs in a separate process per page outside the lock. A page's bitmaps exist only
    inside the worker that reads it, so at most ``2 * workers`` are alive at once.
    """

    def __init__(self, document, *, command, languages, page_timeout, deadline, image_source, need_more=None):
        self.need_more = need_more
        self.fast = False
        self.document = document
        self.command = command
        self.languages = languages
        self.page_timeout = page_timeout
        self.deadline = deadline
        self.image_source = image_source
        self.lock = threading.Lock()
        self.retried: set[int] = set()
        # Per OCR'd page: how it was read (settings, confidence, flags). Numbers and booleans only.
        self.details: dict[int, dict] = {}

    def _skipped(self, number: int, reason: str) -> InspectedPage:
        return InspectedPage(number, "unread", "unknown", "not_processed", "", reason=reason)

    def _render(self, index: int, dpi: float) -> Image.Image:
        with self.lock:
            pixmap = self.document[index].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
            return Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)

    def _render_color(self, index: int, dpi: float) -> Image.Image:
        with self.lock:
            pixmap = self.document[index].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    def _page_dpi(self, rect) -> int:
        if self.image_source:
            # Photos and scans uploaded as images: keep their own resolution, but never
            # read a small picture below 1800 px or a huge one above 3300 px on the long side.
            long_inches = max(rect.width, rect.height) / 72
            return max(72, round(max(1800, min(long_inches * 300, 3300)) / long_inches))
        return OCR_BASE_DPI

    def read(self, index: int) -> InspectedPage:
        number = index + 1
        if time.monotonic() >= self.deadline:
            return self._skipped(number, "Reading this document went over the time limit.")
        try:
            return self._read(index, number)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, InspectionError) else "We could not read this page. Save the document as a new PDF and upload it again."
            return InspectedPage(number, "unread", "unknown", "failed", "", reason=reason)

    def _read(self, index: int, number: int) -> InspectedPage:
        with self.lock:
            page = self.document[index]
            text = page.get_text("text", sort=True).strip()
            rect = page.rect
            area = rect.get_area()
            image_rects = [fitz.Rect(info["bbox"]) & rect for info in page.get_image_info()]
            image_area = sum(max(0.0, r.get_area()) for r in image_rects)
            native_usable = _usable_native(text)
            mixed_page = image_area > 0 and image_area >= area * 0.2
            if mixed_page and native_usable:
                # A scan that already carries its own text layer is covered by that layer;
                # a header over a picture is not.
                inside = sum(
                    any(r.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)) for r in image_rects)
                    for w in page.get_text("words")
                )
                if inside / (image_area / 10_000) >= 1.5:
                    mixed_page = False
        if native_usable and not mixed_page:
            return InspectedPage(number, "read", "unknown", "native_text", text, 1.0)
        dpi = self._page_dpi(rect)
        if rect.width * rect.height * (dpi / 72) ** 2 > _MAX_RENDER_PIXELS:
            raise InspectionError("This page is too large to read. Save it at a standard page size and upload it again.")
        with _GAUGE.hold():
            image = self._render(index, dpi)
            try:
                if _blank(image):
                    return InspectedPage(number, "blank", "other", "rendered_blank", "", 1.0,
                                         "The rendered page is uniformly near-white.")
                page_started = time.monotonic()
                read = self._ocr(index, image, dpi)
            finally:
                image.close()
        self.details[number] = {
            "confidence": round(read.confidence / 100, 3), "raw_confidence": round(read.raw_confidence / 100, 3),
            "setting": read.setting, "variants": list(read.variants), "words": read.words,
            "seconds": round(time.monotonic() - page_started, 2), **read.notes,
        }
        if read.photo and not read.retryable and len(read.text.strip()) < 40:
            # A picture with next to no text (a passport photo page, the photo side of a card): it carries nothing to
            # read, so it is accounted for as such and never holds the document back.
            return InspectedPage(number, "blank", "other", "photo_no_text", "", 1.0,
                                 "Photo / no text: this page is a picture with almost no text to read.")
        # A poor scan or a photo of a page reads at low average confidence yet still yields real text. A page with a
        # substantial amount of it is kept (its confidence stays low) and classified on the evidence it holds: the
        # classifier needs structure (a machine-readable zone, issuer and number ...), so noise cannot pass for a document.
        text_length = len(read.text.strip())
        usable = text_length >= 40 and (read.confidence >= 45 or (text_length >= 150 and read.confidence >= 25))
        combined = _merge_native(text, read.text, native_usable) if text else read.text
        result = InspectedPage(
            number, "read" if usable else "unread", "unknown",
            "native_text+tesseract" if native_usable else "tesseract",
            combined, read.confidence / 100, read.reason if not usable else "",
        )
        if not usable and not result.reason:
            tried = len(read.variants)
            result.reason = (f"No readable text on this page after {tried} reading settings"
                             if tried > 1 else "We could not read enough text on this page")
            result.reason += ". The print may be too faint, blurred or small, or in a script this reader does not have. Check it, or upload a clearer copy."
        return result

    def _ocr(self, index: int, image: Image.Image, dpi: int) -> OCRRead:
        extra_holds: list[_BitmapGauge._Hold] = []

        def render(resolution: int) -> Image.Image:
            hold = _GAUGE.hold()
            hold.__enter__()
            extra_holds.append(hold)
            return self._render(index, resolution)

        def render_color(resolution: int) -> Image.Image:
            hold = _GAUGE.hold()
            hold.__enter__()
            extra_holds.append(hold)
            return self._render_color(index, resolution)

        read = OCRRead("", 0)
        try:
            for attempt in (1, 2):
                window = min(self.deadline, time.monotonic() + self.page_timeout)
                read = _ocr_image(image, command=self.command, languages=self.languages, deadline=window,
                                  render=render, dpi=dpi, render_color=render_color, hard_deadline=self.deadline,
                                  need_more=(lambda text: self.need_more(text, index)) if self.need_more else None,
                                  fast=self.fast)
                if not read.retryable or time.monotonic() >= self.deadline:
                    break
                if attempt == 1:
                    self.retried.add(index + 1)
        finally:
            for hold in extra_holds:
                hold.__exit__()
        return read


def _scaled_budget(page_count: int, workers: int, page_timeout: float) -> float:
    """Seconds a document of this size is allowed: grows with the number of page rounds."""
    return 30.0 + 2.0 * page_timeout * math.ceil(page_count / max(1, workers))


def inspect_document(
    data: bytes, *, tesseract_cmd: str | None = None, languages: str = "eng",
    max_pages: int = 300, page_timeout: float = 20, document_timeout: float = 600,
    page_workers: int | None = None, need_more=needs_more_reading, two_pass: bool = True,
    on_first_pass=None,
) -> Inspection:
    """Inspect all pages without network, database, filename or nationality rules.

    ``need_more(page_text, page_index) -> bool`` decides, between reading steps, whether a page must still be read
    under other settings; by default the page classifier decides (a classified page, valid identity evidence or a plain
    full page of text stops the escalation). ``lambda text, index: True`` keeps the full ladder for every page.

    With ``two_pass`` (the default) every page first gets one quick Tesseract look; the pages are classified (with
    their neighbours) and only the pages that are still unclassified, unread, or inherited with a sign of an identity
    document are read again with the full ladder. ``two_pass=False`` reads every page completely in one go.

    ``on_first_pass(partial_inspection, provisional_classification, pages_to_read_again)`` is called once, from the
    calling thread, after the first look and before the second: the caller can already start sending the pages that are
    settled to the document reader while the rest are still being read. The partial inspection is only good for
    ``pdf_for_pages(..., allow_unresolved=True)``.

    Pages are read concurrently (``page_workers``, default from ``OCR_PAGE_WORKERS``) and
    always reported in page order. A page with a usable embedded text layer is not OCR'd;
    every other page is rendered lazily and read by Tesseract. ``document_timeout`` is the
    most a document may take; the effective limit shrinks for small documents.
    """
    if type(max_pages) is not int or max_pages < 1:
        raise InspectionError("The inspection page limit must be a positive integer.")
    if not all(math.isfinite(value) and value > 0 for value in (page_timeout, document_timeout)):
        raise InspectionError("Inspection time limits must be finite positive numbers.")
    if page_workers is not None and (type(page_workers) is not int or page_workers < 1):
        raise InspectionError("The number of page workers must be a positive integer.")
    started = time.monotonic()
    deadline = started + document_timeout
    normalized, source_format = _normalize(data, deadline)
    command = find_tesseract(tesseract_cmd)
    if command:
        languages = _usable_languages(command, languages)
    warnings: list[str] = []
    try:
        source = fitz.open(stream=normalized, filetype="pdf")
    except Exception:
        raise InspectionError("We cannot open this PDF. Save it again as a PDF without a password and try again.") from None
    with source:
        if source.needs_pass:
            raise InspectionError("Password-protected PDFs must be unlocked before you upload them.")
        count = source.page_count
        if not count:
            raise InspectionError("This document has no pages.")
        workers = max(1, min(page_workers or _default_workers(), 32, count))
        deadline = min(deadline, time.monotonic() + _scaled_budget(min(count, max_pages), workers, page_timeout))
        reader = _PageReader(source, command=command, languages=languages, page_timeout=page_timeout,
                             deadline=deadline, image_source=source_format in _IMAGE_FORMATS, need_more=need_more)
        limit = min(count, max_pages)

        def read_all(indexes: list[int]) -> list[InspectedPage]:
            if workers == 1 or len(indexes) == 1:
                return [reader.read(index) for index in indexes]
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="crm-ocr") as pool:
                return [future.result() for future in [pool.submit(reader.read, index) for index in indexes]]

        two = two_pass and limit > 1 and need_more is not None
        reader.fast = two
        pages = read_all(list(range(limit)))
        if two:
            reader.fast = False
            provisional = classify_pages([p.text if p.status == "read" else "" for p in pages])
            # A kind whose checksum-valid evidence (MRZ, Verhoeff-valid Aadhaar number) has been found needs no deeper
            # look at the pages assigned to it by adjacency; without such evidence, those pages are read again.
            proven = {kind for page, verdict in zip(pages, provisional) if page.status == "read"
                      for kind, found in classification_evidence(page.text).items() if found}
            again = [
                index for index, (page, verdict) in enumerate(zip(pages, provisional))
                if page.method != "native_text" and page.status != "blank" and (
                    (page.status == "unread" and page.method not in ("not_processed", "failed"))
                    or (page.status == "read" and verdict.document_type == "unknown")
                    or (page.status == "read" and verdict.inherited and identity_hint(page.text)
                        and verdict.document_type in ("passport", "aadhaar") and verdict.document_type not in proven)
                )
            ]
            if on_first_pass is not None:
                partial_pages = list(pages) + [
                    InspectedPage(n, "unread", "unknown", "not_processed", "") for n in range(len(pages) + 1, count + 1)]
                partial = Inspection(partial_pages, count, source_format, [], normalized, allow_partial=True)
                partial._lock = reader.lock
                try:
                    on_first_pass(partial, provisional, [index + 1 for index in again])
                except Exception:  # an early start is a convenience; the full read goes on regardless
                    pass
            for index, page in zip(again, read_all(again)):
                pages[index] = page
        for number in range(limit + 1, count + 1):
            pages.append(InspectedPage(
                number, "unread", "unknown", "not_processed", "",
                reason=f"This document has more than {max_pages} pages, which is too many to read."))
    classified = classify_pages([p.text if p.status == "read" else "" for p in pages])
    for page, verdict in zip(pages, classified):
        if page.status == "read":
            page.document_type = verdict.document_type
            page.also = list(verdict.also)
            page.inherited = verdict.inherited
            page.confidence = min(page.confidence, verdict.confidence)
            page.reason = verdict.reason
    unread = [p.page_number for p in pages if p.status == "unread"]
    unknown = [p.page_number for p in pages if p.status == "read" and p.document_type == "unknown"]
    inherited = [p.page_number for p in pages if p.status == "read" and p.inherited]
    if unread:
        warnings.append(f"Pages {_numbers(unread)} could not be read. Review them in the stored original.")
    if unknown:
        warnings.append(f"We could not tell what kind of pages {_numbers(unknown)} are. Check them in the saved original.")
    if inherited:
        warnings.append(
            f"Pages {_numbers(inherited)} have no identifying content and were assigned to the adjoining "
            "document (for example the back of a card). Check them."
        )
    native = sum(p.status == "read" and p.method == "native_text" for p in pages)
    ocr = sum(p.status == "read" and p.method != "native_text" for p in pages)
    blank = sum(p.status == "blank" for p in pages)
    photo = [p.page_number for p in pages if p.method == "photo_no_text"]
    completeness = {
        "pages_total": count, "pages_read": native + ocr, "pages_native": native, "pages_ocr": ocr,
        "pages_blank": blank, "pages_unread": len(unread),
        "unread": [{"page": p.page_number, "reason": p.reason} for p in pages if p.status == "unread"],
        "unread_detail": [{"page": p.page_number, "reason": p.reason,
                           "variants": reader.details.get(p.page_number, {}).get("variants", [])}
                          for p in pages if p.status == "unread"],
        "pages_retried": sorted(reader.retried),
        "chars": sum(len(p.text) for p in pages), "workers": workers,
        "seconds": round(time.monotonic() - started, 3),
        # Photo pages are counted inside ``pages_blank`` (they carry no text and never block a document).
        "pages_photo": len(photo), "photo": photo,
        "pages_detail": [{"page": n, **reader.details[n]} for n in sorted(reader.details)],
    }
    try:
        patterns = validators.scan_pages({p.page_number: p.text for p in pages if p.status == "read"})
    except Exception:  # a cross-check aid only; never a reason to lose a read
        patterns = None
    if patterns is not None:
        completeness["patterns"] = patterns.flags()
    # Every original page is accounted for exactly once: read, blank or unread.
    if len(pages) != count or [p.page_number for p in pages] != list(range(1, count + 1)) \
            or completeness["pages_read"] + blank + len(unread) != count:
        raise InspectionError("The page check lost track of a page. Please upload the document again.", retryable=True)
    return Inspection(pages, len(pages), source_format, warnings, normalized, completeness=completeness,
                      patterns=patterns)


def _numbers(values: list[int]) -> str:
    shown = ", ".join(str(value) for value in values[:20])
    return shown + (f" and {len(values) - 20} more" if len(values) > 20 else "")


def restore_inspection(
    data: bytes, report: dict, *, document_timeout: float = 600, allow_partial: bool = False,
) -> Inspection:
    """Restore a trusted checkpoint without repeating local OCR.

    The caller must verify source hash and inspection settings first. Reopening
    the original verifies physical page count; Word may need reconversion. A
    checkpoint with unread/unclassified pages is restored only for a caller that
    extracts the classified pages alone (``allow_partial``); those pages stay
    unselectable.
    """
    try:
        count = report["page_count"]
        entries = report["pages"]
        if (
            (report.get("complete") is not True and not allow_partial) or type(count) is not int or count < 1
            or not isinstance(entries, list) or len(entries) != count
        ):
            raise ValueError()
        statuses = {"read", "blank", "unread"} if allow_partial else {"read", "blank"}
        types = ({"resume", "passport", "aadhaar", "other", "unknown"} if allow_partial
                 else {"resume", "passport", "aadhaar", "other"})
        pages = []
        for number, entry in enumerate(entries, 1):
            also = entry.get("also", [])
            if (
                type(entry["page_number"]) is not int or entry["page_number"] != number
                or entry["status"] not in statuses
                or entry["document_type"] not in types
                or (entry["status"] == "blank" and entry["document_type"] != "other")
                or not isinstance(also, list) or any(kind not in {"resume", "passport", "aadhaar"} for kind in also)
                or (entry["status"] == "unread" and entry["document_type"] != "unknown")
            ):
                raise ValueError()
            pages.append(InspectedPage(
                number, entry["status"], entry["document_type"], entry["method"], "",
                confidence=entry.get("confidence", 0.0), reason=entry.get("reason", ""),
                also=list(also), inherited=bool(entry.get("inherited", False)),
            ))
    except (KeyError, TypeError, ValueError):
        raise InspectionError("The saved page check is incomplete. Please upload the document again.") from None
    normalized, source_format = _normalize(data, time.monotonic() + document_timeout)
    try:
        with fitz.open(stream=normalized, filetype="pdf") as source:
            if source.needs_pass or source.page_count != count or source_format != report["source_format"]:
                raise InspectionError("The saved check no longer matches the file. Please upload the document again.")
    except InspectionError:
        raise
    except Exception:
        raise InspectionError("We could not open the original file again.") from None
    return Inspection(pages, count, source_format, list(report.get("warnings", [])), normalized,
                      allow_partial=allow_partial)
