"""A page that is already filed is not filed again: the same physical page, scanned or photographed several times, is kept once.

One candidate's documents arrive in many shapes: the passport scan on its own, the same two pages inside a combined "picker" scan,
inside the signed-offer scan, inside two "merged" files; the Aadhaar card as a JPEG and as a PDF; the visa pages in three bundles.
Filing every one of them gives the slot five files that all show the same two pages. This module finds those repeats, page by
page, and says which copy to keep. It never decides alone what is lost: callers record every skipped page (which page, which
kept page it repeats, and why) and keep the original source file, so staff can always see and recover a page.

Evidence, strongest first (see `compare`; every rule errs towards keeping both copies):

  * the same valid ID number on the same kind of page: a passport's machine-readable zone that passes its check digits, an
    Aadhaar number that passes Verhoeff. A passport DATA page and its BACK (address) page carry no common number and are
    different pages of the same passport, so both are kept; an Aadhaar front and back are told apart by their side. Two
    different valid numbers are never the same page, whatever else looks alike.
  * the same picture: a perceptual hash (difference hash of the page content, tolerant of scale, crop of the margins, JPEG
    quality) at almost the same value, with no contradicting text. Pages that carry hardly any text (a photo, a stamp, a blank
    sheet) are only ever called the same when the picture is practically identical.
  * the same words: the page text, read once already by the splitting (no second reading), with a token similarity of 0.8 and
    at least 25 words on both sides, or 0.7 / 0.5 when the pictures are also fairly / very close; or fairly close pictures that
    print the same long number (a visa or slip number). Two copies are different documents when each prints a date the other
    lacks (a re-issued offer, a second medical) or when both print long numbers (file, PIN, slip, passport no.) and none is shared,
    unless the pictures are practically identical. The picture hash alone cannot tell two people's pages of one template apart,
    which is why those two checks exist.

Choosing the copy to keep (`priority`): a whole file that is that one document beats pages cut from a combined scan; then the
file with more pages (it keeps the whole document together); then the better reading (confidence, then how much text, then the
pixel size); then the name. The file kept first keeps all its pages; a later file keeps only the pages the others do not have,
and a file with nothing left is dropped. Nothing depends on the order the files were given in.

Never raises: a file that cannot be rendered or read simply takes no part. The reading of a file that was not read yet (a file
named for one document is stored without being read) is local, bounded by a time budget, and happens only when two files of the
same kind are there to compare.
"""
from __future__ import annotations

import hashlib
import io
import re
import time
from dataclasses import dataclass, field
from typing import Any

MIN_TOKENS = 25            # a page needs this many words before its text alone can say it is the same page
LOW_TEXT = 10              # below this a page is a picture (photo, stamp, blank sheet): only a practically identical picture counts
SAME_PICTURE = 0.04        # share of differing hash bits: the same image, copied or recompressed
NEAR_PICTURE = 0.10        # a rescan of the same page (needs the text to agree at least this much: NEAR_TEXT)
FAR_PICTURE = 0.30         # the same words laid out a little differently (a crop, a slight tilt): FAR_TEXT
NUMBER_PICTURE = 0.20      # the pictures are fairly close and both pages print the same long number (a visa, a slip, a file number)
LONG_NUMBER = 8            # digits that make a number worth trusting
IDENTICAL_PICTURE = 0.01   # for pages with almost no text
SAME_TEXT, NEAR_TEXT, FAR_TEXT, PICTURE_TEXT = 0.80, 0.50, 0.70, 0.25
ASPECT_TOLERANCE = 0.12
READ_TIME_BUDGET = 150.0   # seconds spent reading files that were not read yet, for one candidate
READ_MAX_PAGES = 12        # a file longer than this is not read just to compare it
HASH_BITS = 256

_WORD = re.compile(r"[a-z0-9]+")
_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DATE_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s*(?:st|nd|rd|th)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s*(\d{4})", re.I)


# ----------------------------------------------------------------------------------------------------------- the pages

@dataclass
class PageRead:
    """What the local reader found on one page: kept from the splitting so nothing is read twice."""
    text: str = ""
    confidence: float = 0.0
    status: str = "read"          # read | blank | unread


def _tokens(text: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall((text or "").lower()) if len(word) >= 2)


def _dates(text: str) -> frozenset[tuple[int, int, int]]:
    found: set[tuple[int, int, int]] = set()

    def year(raw: str) -> int:
        return int(raw) if len(raw) == 4 else 2000 + int(raw)

    for day, month, raw in _DATE_NUMERIC.findall(text):
        if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
            found.add((year(raw), int(month), int(day)))
    for raw, month, day in _DATE_ISO.findall(text):
        if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
            found.add((int(raw), int(month), int(day)))
    for day, month, raw in _DATE_WORDS.findall(text):
        if 1 <= int(day) <= 31:
            found.add((int(raw), _MONTHS[month.lower()[:3]], int(day)))
    return frozenset(found)


def _has(text: str, *phrases: str) -> int:
    lowered = text.lower()
    return sum(phrase in lowered for phrase in phrases)


def _passport_role(text: str, has_mrz: bool) -> str | None:
    """"data" for the photo page (name, birth, MRZ), "back" for the family / address page, None when the page does not say."""
    data = _has(text, "surname", "given name", "date of birth", "place of birth", "nationality", "republic of india", "date of expiry")
    back = _has(text, "father", "mother", "legal guardian", "spouse", "file no", "old passport", "address")
    if has_mrz or data >= 2 and data > back:
        return "data"
    if back >= 2 and back > data:
        return "back"
    return None


def _aadhaar_role(text: str) -> str | None:
    """"front" (name, birth date or gender) or "back" (address); None for a sheet that shows both or neither."""
    lowered = text.lower()
    front = bool(re.search(r"\b(dob|date of birth|year of birth|yob|male|female)\b", lowered))
    back = bool(re.search(r"\b(address|c/o|s/o|d/o|w/o|vtc|pin code)\b", lowered)) or "address:" in lowered
    if front and not back:
        return "front"
    if back and not front:
        return "back"
    return None


class Page:
    """One page with the signals `compare` looks at, all computed once."""

    def __init__(self, read: PageRead | None, image: "Any | None", pixels: int, kind: str) -> None:
        text = (read.text if read is not None and read.status != "blank" else "") or ""
        self.read = read
        self.tokens = _tokens(text)
        self.digits = frozenset(token for token in self.tokens if len(token) >= 3 and any(c.isdigit() for c in token))
        self.dates = _dates(text)
        # numbers with six digits or more (a file number, a PIN, a slip or passport number): the page's own fingerprint
        self.longs = frozenset(token for token in self.digits if sum(c.isdigit() for c in token) >= 6)
        self.pixels = pixels
        self.confidence = read.confidence if read is not None else 0.0
        self.number: str | None = None
        self.role: str | None = None
        if kind == "passport":
            from app.integrations.ocr.validators import find_mrz
            mrz = find_mrz(text) if text else None
            if mrz is not None and mrz.valid and mrz.document_number:
                self.number = mrz.document_number
            self.role = _passport_role(text, mrz is not None and mrz.valid)
        elif kind == "aadhaar":
            from app.integrations.ocr.validators import find_aadhaar_numbers
            valid = next((item for item in find_aadhaar_numbers(text) if item.valid), None) if text else None
            self.number = valid.number if valid else None
            self.role = _aadhaar_role(text)
        self.hash, self.aspect = picture_signature(image) if image is not None else (None, None)


def picture_signature(gray) -> tuple[int | None, float | None]:
    """(difference hash, width / height of the content) of a grey page image (a 2-D uint8 array), or (None, None) for a blank page.

    The page is cropped to where the ink is first (so a scan with wide margins and the same page cropped tightly agree), then
    reduced to 17 x 16 and each pixel compared with its right neighbour: 256 bits that survive scaling and JPEG quality."""
    import numpy as np
    from PIL import Image
    try:
        picture = np.asarray(gray, dtype=np.uint8)
        if picture.ndim != 2 or min(picture.shape) < 8:
            return None, None
        background = float(np.percentile(picture, 60))
        ink = picture < background - 45
        if ink.mean() < 0.002:
            return None, None                                   # nothing printed: a blank sheet
        height, width = picture.shape
        if background >= 100:                                   # paper: crop to the printed area, ignoring specks
            rows = np.flatnonzero(ink.sum(axis=1) >= max(2.0, 0.006 * width))
            columns = np.flatnonzero(ink.sum(axis=0) >= max(2.0, 0.006 * height))
            if rows.size and columns.size:
                picture = picture[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1]
        crop_height, crop_width = picture.shape
        if min(crop_height, crop_width) < 8:
            return None, None
        small = np.asarray(Image.fromarray(picture).resize((17, 16), Image.BILINEAR), dtype=np.int16)
        bits = (small[:, :-1] > small[:, 1:]).flatten()
        return int("".join("1" if bit else "0" for bit in bits), 2), crop_width / crop_height
    except Exception:
        return None, None


def picture_distance(left: Page, right: Page) -> float | None:
    """Share of differing hash bits, or None when either page has no picture or the shapes disagree."""
    if left.hash is None or right.hash is None or not left.aspect or not right.aspect:
        return None
    if abs(left.aspect / right.aspect - 1) > ASPECT_TOLERANCE:
        return None
    return bin(left.hash ^ right.hash).count("1") / HASH_BITS


def _similarity(left: frozenset, right: frozenset) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def compare(left: Page, right: Page) -> str | None:
    """Why two pages of the SAME kind are the same physical page, or None (keep both). Symmetric."""
    if left.number and right.number and left.number != right.number:
        return None                                              # two different people's passports / cards
    if left.role and right.role and left.role != right.role:
        return None                                              # a passport's data page and its back page, a card's front and back
    if left.number and left.number == right.number and (left.role == right.role or "data" in (left.role, right.role)):
        return "same ID number"
    distance = picture_distance(left, right)
    if left.longs and right.longs and not (left.longs & right.longs) and not (distance is not None and distance <= IDENTICAL_PICTURE):
        return None                                              # printed numbers (file, pin, slip, passport no.) all differ: another document
    if len(left.tokens) < LOW_TEXT or len(right.tokens) < LOW_TEXT:
        # A photo, a stamp, a blank sheet: nothing to read, so only a practically identical picture counts.
        if distance is not None and distance <= IDENTICAL_PICTURE and left.pixels and left.pixels == right.pixels:
            return "identical picture"
        return None
    similarity = (_similarity(left.tokens, right.tokens)
                  if len(left.tokens) >= MIN_TOKENS and len(right.tokens) >= MIN_TOKENS else None)
    same_picture = distance is not None and distance <= SAME_PICTURE
    if left.dates and right.dates and not (left.dates <= right.dates or right.dates <= left.dates):
        return None                                              # each prints a date the other lacks: a different (re-issued) document
    if distance is not None:
        if same_picture and (similarity is None or similarity >= PICTURE_TEXT):
            return "same picture"
        if distance <= NEAR_PICTURE and similarity is not None and similarity >= NEAR_TEXT:
            return "same page, scanned again"
        if distance <= FAR_PICTURE and similarity is not None and similarity >= FAR_TEXT:
            return "same words and layout"
        if distance <= NUMBER_PICTURE and any(len(token) >= LONG_NUMBER and token.isdigit() for token in left.digits & right.digits):
            return "same document number and layout"
    if similarity is not None and similarity >= SAME_TEXT:
        return "same text"
    return None


# ----------------------------------------------------------------------------------------------------------- the files

@dataclass
class Doc:
    """One file (or one cut of a scan) that holds pages of one candidate document kind."""
    label: str                          # what staff see: the original file name
    data: bytes
    content_type: str
    numbers: list[int]                  # page number to show for each page of `data` (numbered in the scan the page came from)
    reads: list[PageRead | None]        # what was read on each page, None when it was not read yet
    standalone: bool                    # the whole file is this one document (not pages cut from a combined scan)
    droppable: bool = True              # False when the pages would be lost: the original scan is not kept anywhere
    ref: Any = None                     # the caller's own object for this file
    pages: list[Page] = field(default_factory=list)
    prepared: str = ""                  # the kind the signals were computed for


def _is_pdf(doc: Doc) -> bool:
    return doc.content_type == "application/pdf" or doc.data[:5] == b"%PDF-"


def page_count(data: bytes, content_type: str) -> int:
    """Pages of a PDF, 1 for a picture, 0 for anything else or a file that cannot be opened."""
    try:
        if content_type == "application/pdf" or data[:5] == b"%PDF-":
            import pymupdf as fitz
            with fitz.open(stream=data, filetype="pdf") as document:
                return document.page_count
        return 1 if content_type.startswith("image/") and content_type not in ("image/heic", "image/heif") else 0
    except Exception:
        return 0


def _render(doc: Doc) -> tuple[list[Any], list[int]]:
    """A small grey picture and a pixel size per page; (None, 0) for a page that cannot be drawn."""
    import numpy as np
    images: list[Any] = []
    sizes: list[int] = []
    try:
        if _is_pdf(doc):
            import pymupdf as fitz
            with fitz.open(stream=doc.data, filetype="pdf") as document:
                for page in document:
                    try:
                        scale = 320.0 / max(page.rect.width, page.rect.height, 1.0)
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
                        grey = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.stride)[:, :pixmap.width]
                        images.append(grey.copy())
                        info = page.get_image_info()
                        sizes.append(max((int(item.get("width", 0)) * int(item.get("height", 0)) for item in info), default=0)
                                     or int(page.rect.width * page.rect.height))
                    except Exception:
                        images.append(None)
                        sizes.append(0)
        else:
            from PIL import Image, ImageOps
            with Image.open(io.BytesIO(doc.data)) as picture:
                width, height = picture.size
                picture.draft("L", (640, 640))
                grey = ImageOps.exif_transpose(picture).convert("L")
                grey.thumbnail((320, 320))
                images.append(np.asarray(grey, dtype=np.uint8))
                sizes.append(width * height)
    except Exception:
        return [None] * len(doc.numbers), [0] * len(doc.numbers)
    return images, sizes


def prepare(doc: Doc, kind: str) -> None:
    """Build the page signals for `kind` (the passport rules differ from the Aadhaar rules). Safe to call again."""
    if doc.prepared == kind and doc.pages:
        return
    images, sizes = _render(doc)
    count = len(doc.numbers)
    images = (images + [None] * count)[:count]
    sizes = (sizes + [0] * count)[:count]
    reads = (doc.reads + [None] * count)[:count]
    doc.pages = [Page(read, image, size, kind) for read, image, size in zip(reads, images, sizes)]
    doc.prepared = kind


def read_pages(doc: Doc, *, settings: Any = None, deadline: float | None = None) -> bool:
    """Read a file that was not read yet (locally; images and PDFs alike). True when every page now has a reading."""
    if doc.reads and all(item is not None for item in doc.reads):
        return True
    if deadline is not None and time.monotonic() > deadline:
        return False
    try:
        from app.integrations.ocr.inspection import inspect_document
        if settings is None:
            from app.core.config import get_settings
            settings = get_settings()
        inspected = inspect_document(
            doc.data, tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng"),
            max_pages=READ_MAX_PAGES, page_timeout=8, document_timeout=60)
        if len(inspected.pages) != len(doc.numbers):
            return False
        doc.reads = [PageRead(page.text or "", float(page.confidence or 0.0), page.status) for page in inspected.pages]
        doc.prepared = ""
        return True
    except Exception:
        return False


def priority(doc: Doc) -> tuple:
    """Smaller sorts first = kept first. Total and independent of the order the files came in."""
    pages = doc.pages
    confidence = sum(page.confidence for page in pages) / len(pages) if pages else 0.0
    return (0 if doc.standalone else 1, -len(pages), -round(confidence, 1), -sum(len(page.tokens) for page in pages),
            -sum(page.pixels for page in pages), doc.label.lower(), hashlib.sha256(doc.data).hexdigest(), tuple(doc.numbers))


@dataclass
class Repeat:
    """One page of `docs[doc]` that is the same page as one page of `docs[of_doc]`."""
    doc: int
    page: int
    of_doc: int
    of_page: int
    why: str


def plan(docs: list[Doc], kind: str, *, whole_only: bool = False) -> tuple[dict[int, list[int]], list[Repeat]]:
    """Which pages of which files to keep for one document kind: ({file index: kept page indexes}, the repeats).

    `whole_only` (for files already stored, which cannot be cut) keeps a file complete unless EVERY page of it repeats a page
    of a file kept before it; a page of a file with `droppable` False is never dropped."""
    for doc in docs:
        prepare(doc, kind)
    order = sorted(range(len(docs)), key=lambda index: priority(docs[index]))
    kept: list[tuple[int, int]] = []
    keep: dict[int, list[int]] = {}
    repeats: list[Repeat] = []

    def find(index: int, page: int) -> tuple[int, int, str] | None:
        for other, other_page in kept:
            if other == index:
                continue                                         # pages of the same file are never compared with each other
            why = compare(docs[index].pages[page], docs[other].pages[other_page])
            if why:
                return other, other_page, why
        return None

    for index in order:
        doc = docs[index]
        hits = [find(index, page) for page in range(len(doc.pages))]
        if not doc.pages:
            keep[index] = []
            continue
        if whole_only:
            if doc.droppable and all(hit is not None for hit in hits):
                repeats += [Repeat(index, page, hit[0], hit[1], hit[2]) for page, hit in enumerate(hits)]
                keep[index] = []
            else:
                keep[index] = list(range(len(doc.pages)))
                kept += [(index, page) for page in range(len(doc.pages))]
            continue
        keep[index] = []
        for page, hit in enumerate(hits):
            if hit is not None and doc.droppable:
                repeats.append(Repeat(index, page, hit[0], hit[1], hit[2]))
            else:
                keep[index].append(page)
                kept.append((index, page))
    return keep, repeats


def page_runs(numbers: list[int]) -> str:
    """[1, 2, 3, 7] -> "1-3, 7" (the pages as staff read them)."""
    runs: list[str] = []
    ordered = sorted(set(numbers))
    start = previous = ordered[0]
    for number in ordered[1:] + [None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        runs.append(str(start) if start == previous else f"{start}-{previous}")
        if number is not None:
            start = previous = number
    return ", ".join(runs)


def pages_word(numbers: list[int]) -> str:
    return f"{'pages' if len(set(numbers)) > 1 else 'page'} {page_runs(numbers)}"


def grouped(repeats: list[Repeat]) -> list[list[Repeat]]:
    """The repeats of one file against one kept file, in page order: each group is one sentence for staff."""
    groups: list[list[Repeat]] = []
    for repeat in sorted(repeats, key=lambda item: (item.doc, item.of_doc, item.page)):
        if groups and groups[-1][0].doc == repeat.doc and groups[-1][0].of_doc == repeat.of_doc:
            groups[-1].append(repeat)
        else:
            groups.append([repeat])
    return groups


def covered(new: Doc, existing: list[Doc], kind: str) -> list[Repeat] | None:
    """The repeats of EVERY page of `new` against pages of the `existing` files (indexes into `existing`; `of_doc` only), or None when
    at least one page of `new` is not there yet. Used when a file is added to a slot that already holds files."""
    if not existing:
        return None
    prepare(new, kind)
    if not new.pages:
        return None
    for doc in existing:
        prepare(doc, kind)
    found: list[Repeat] = []
    for number, page in enumerate(new.pages):
        hit = next(((index, other, why) for index, doc in enumerate(existing) for other, candidate in enumerate(doc.pages)
                    if (why := compare(page, candidate)) is not None), None)
        if hit is None:
            return None
        found.append(Repeat(0, number, hit[0], hit[1], hit[2]))
    return found


# ----------------------------------------------------------------------------------------------------------- an import's files

SOURCE_BUNDLE = "source_bundle"
NEVER_COMPARED = ("photo", "advance_receipt", SOURCE_BUNDLE)


@dataclass
class Skipped:
    """One run of pages that were not filed again, with where they are still to be seen."""
    kind: str
    label: str                    # the document, as staff know it ("Passport")
    file: str                     # the file the repeated pages are in
    pages: list[int]              # their numbers in that file
    same_as_file: str             # the file that was kept
    same_as_pages: list[int]
    why: str
    keeper: Any = None            # the Upload the kept pages went into
    original_kept: bool = True    # the file they are in is stored whole, as received (Source document evidence)

    def message(self) -> str:
        many = len(set(self.pages)) > 1
        return (f"{self.label} {pages_word(self.pages)} of {self.file} {'are' if many else 'is'} the same as {pages_word(self.same_as_pages)} "
                f"of {self.same_as_file} ({self.why}): not filed again. {'They stay' if many else 'It stays'} in the original file"
                f"{', which is kept under Source document evidence' if self.original_kept else ''}.")

    def as_dict(self, file_names: dict[int, str] | None = None) -> dict:
        kept_as = (file_names or {}).get(id(self.keeper)) if self.keeper is not None else None
        return {"kind": self.kind, "label": self.label, "file": self.file, "pages": list(self.pages), "sameAsFile": self.same_as_file,
                "sameAsPages": list(self.same_as_pages), "reason": self.why, "keptAs": kept_as, "originalKept": self.original_kept,
                "message": self.message()}


@dataclass
class DedupeReport:
    skipped: list[Skipped] = field(default_factory=list)

    def messages(self) -> list[str]:
        return [item.message() for item in self.skipped]


def dedupe_uploads(uploads: list, *, settings: Any = None, budget: float = READ_TIME_BUDGET) -> tuple[list, DedupeReport]:
    """The uploads of ONE candidate without the pages that are already filed in another file of the same document kind.

    A file whose pages are all kept comes back unchanged (the very same object, the original bytes). A file that loses pages is
    cut again to the pages that remain (one file per set of kinds, original page order), a file with nothing left is dropped, and
    the whole source it came from is added as a `source_bundle` upload so a skipped page can always be found. Pages that are not
    the same as any other are never touched. Never raises: any failure leaves the uploads exactly as they were."""
    try:
        return _dedupe(list(uploads), settings, budget)
    except Exception:
        return list(uploads), DedupeReport()


def _short(name: str) -> str:
    return (name or "file").replace("\\", "/").rsplit("/", 1)[-1]


def _dedupe(uploads: list, settings: Any, budget: float) -> tuple[list, DedupeReport]:
    from app.services import bulk_candidate_import as bulk
    eligible = {kind for kind in bulk.KIND_LABELS if kind not in NEVER_COMPARED}
    docs: dict[int, Doc] = {}
    for position, upload in enumerate(uploads):
        if not any(kind in eligible for kind in upload.kinds):
            continue
        count = page_count(upload.data, upload.content_type or "")
        if not count:
            continue
        numbers = list(upload.pages) if upload.pages and len(upload.pages) == count else list(range(1, count + 1))
        facts = list(upload.facts) if getattr(upload, "facts", None) and len(upload.facts) == count else [None] * count
        docs[position] = Doc(label=_short(upload.original_name or upload.path), data=upload.data, content_type=upload.content_type or "",
                             numbers=numbers, reads=facts, standalone=not upload.pages,
                             droppable=not upload.pages or getattr(upload, "source", None) is not None, ref=position)
    kinds = [kind for kind in dict.fromkeys(kind for upload in uploads for kind in upload.kinds) if kind in eligible]
    deadline = time.monotonic() + budget
    keeps: dict[str, dict[int, set[int]]] = {}
    repeats: dict[str, list[tuple[int, int, int, int, str]]] = {}
    for kind in kinds:
        members = [position for position, upload in enumerate(uploads) if kind in upload.kinds and position in docs]
        if len(members) < 2:
            continue
        for position in members:
            read_pages(docs[position], settings=settings, deadline=deadline)
            if docs[position].reads and all(item is not None for item in docs[position].reads):
                uploads[position].facts = list(docs[position].reads)      # what was read stays with the file (the name reading reuses it)
        group = [docs[position] for position in members]
        keep, found = plan(group, kind)
        keeps[kind] = {members[index]: set(pages) for index, pages in keep.items()}
        repeats[kind] = [(members[item.doc], item.page, members[item.of_doc], item.of_page, item.why) for item in found]
    if not any(repeats.values()):
        return list(uploads), DedupeReport()

    # Rebuild each file that lost pages: one file per set of kinds that keep the same pages.
    placed: dict[tuple[int, int, str], Any] = {}
    result: list = []
    failed: set[int] = set()
    rebuilt: dict[int, list] = {}
    for position, upload in enumerate(uploads):
        doc = docs.get(position)
        if doc is None or not any(position in keep for keep in keeps.values()):
            continue
        pages_of: dict[str, list[int]] = {}
        for kind in upload.kinds:
            keep = keeps.get(kind, {}).get(position)
            pages_of[kind] = list(range(len(doc.numbers))) if keep is None else sorted(keep)
        everything = list(range(len(doc.numbers)))
        if all(pages == everything for pages in pages_of.values()):
            continue
        groups: dict[tuple[int, ...], list[str]] = {}
        for kind, pages in pages_of.items():
            if pages:
                groups.setdefault(tuple(pages), []).append(kind)
        pieces = []
        try:
            for pages, group_kinds in groups.items():
                data = upload.data if list(pages) == everything else bulk.cut_pdf_pages(upload.data, [index + 1 for index in pages])
                pieces.append(bulk.Upload(
                    path=upload.path, kinds=group_kinds, data=data, content_type=upload.content_type,
                    pages=[doc.numbers[index] for index in pages], original_name=upload.original_name or upload.path,
                    facts=[doc.reads[index] for index in pages] if all(item is not None for item in doc.reads) else None,
                    source=getattr(upload, "source", None) or upload.data))
        except Exception:                                        # cannot cut this file: it keeps every page (nothing is lost)
            failed.add(position)
            continue
        rebuilt[position] = pieces
        for piece in pieces:
            for kind in piece.kinds:
                for number in piece.pages:
                    placed[(position, doc.numbers.index(number), kind)] = piece
    for position, upload in enumerate(uploads):
        if position in rebuilt:
            result += rebuilt[position]
        else:
            result.append(upload)
            doc = docs.get(position)
            if doc is not None:
                for kind in upload.kinds:
                    for index in range(len(doc.numbers)):
                        placed[(position, index, kind)] = upload

    # Every page that is no longer filed is listed against the page it repeats, and its source file is kept whole.
    skipped: list[Skipped] = []
    bundles: dict[str, Any] = {}
    for kind, found in repeats.items():
        found = [item for item in found if item[0] not in failed]
        runs: dict[tuple[int, int], list[tuple[int, int, str]]] = {}
        for position, page, other, other_page, why in found:
            runs.setdefault((position, other), []).append((page, other_page, why))
        for (position, other), items in sorted(runs.items()):
            items.sort()
            upload, doc, other_doc = uploads[position], docs[position], docs[other]
            source = getattr(upload, "source", None) or upload.data
            digest = hashlib.sha256(source).hexdigest()
            if digest not in bundles:
                bundles[digest] = bulk.Upload(path=upload.original_name or upload.path, kinds=[SOURCE_BUNDLE], data=source,
                                              content_type=upload.content_type, original_name=upload.original_name or upload.path)
            why = ", ".join(dict.fromkeys(item[2] for item in items))
            skipped.append(Skipped(
                kind=kind, label=bulk.KIND_LABELS.get(kind, kind), file=doc.label, pages=[doc.numbers[item[0]] for item in items],
                same_as_file=other_doc.label, same_as_pages=list(dict.fromkeys(other_doc.numbers[item[1]] for item in items)), why=why,
                keeper=placed.get((other, items[0][1], kind))))
    return result + list(bundles.values()), DedupeReport(skipped)
