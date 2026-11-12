"""Local preparation of what is sent to the document reader (Veris): check and fix it here, then send it.

Every job file is rebuilt fresh and minimal from the pages it is to carry:

* a scanned page (one picture that fills the page) becomes ONE embedded picture on a new page of its own, with none of the
  resources the original page inherited (shared dictionaries, unused pictures and fonts of other pages);
* a page with real content (a typed résumé) is copied with its contents cleaned, so unused resources are left behind;
* pages the local reading found sideways or upside-down are turned upright;
* a picture is never enlarged; one that is more than ~2000 px on its long side (never below ~150 dpi of the page) is shrunk,
  and when the whole file is still above ~3 MB the pictures are re-encoded as JPEG (quality 85) where that is smaller. A
  picture that is already small is embedded exactly as it was (no second lossy pass);
* the result is deflated and carries no generated id, so the same pages give the same bytes.

``unsendable`` finds, before any job is built, pages that carry nothing to send (a blank page) or that repeat an earlier page
exactly. Everything here is best effort: a fault in preparation sends the file as it was, never loses a page, and nothing
here logs or returns page content, only counts and sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import logging
import math
import re

import pymupdf as fitz
from PIL import Image

log = logging.getLogger("crm.reading")

MAX_SIDE = 2000          # px on the long side of a page picture
MAX_SIDE_CAP = 3200      # ... raised for a big paper size so that a page keeps at least MIN_DPI, never beyond this
MIN_DPI = 150
TARGET_BYTES = 3_000_000  # above this the pictures are re-encoded
QUALITY = 85
_COVER = 0.85            # a picture that covers this share of the page makes it a scanned page
_TURNS = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}  # counter-clockwise


@dataclass
class Prepared:
    content: bytes
    pages: int = 0
    original_bytes: int = 0
    rotated: int = 0               # pages turned upright
    recompressed: int = 0          # pictures shrunk or re-encoded
    images: list[int] = field(default_factory=list)  # pictures on each page of the result
    fallback: bool = False         # preparation failed and the file is as it was


def rotations_from(completeness: dict | None) -> dict[int, int]:
    """{page number: degrees counter-clockwise} for the pages the local reading only managed to read once turned."""
    found: dict[int, int] = {}
    for entry in (completeness or {}).get("pages_detail") or []:
        # Merged reads retain their winning orientation separately from their "merged" setting.
        rotation = entry.get("rotation")
        match = re.search(r"rotate(90|180|270)", str(entry.get("setting") or ""))
        if rotation not in (90, 180, 270):
            rotation = int(match.group(1)) if match else None
        if rotation is not None and isinstance(entry.get("page"), int):
            found[entry["page"]] = rotation
    return found


def _largest_image(page) -> tuple[dict | None, float]:
    best, best_area = None, 0.0
    for info in page.get_image_info(xrefs=True):
        x0, y0, x1, y1 = info["bbox"]
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area > best_area:
            best, best_area = info, area
    page_area = max(1.0, page.rect.width * page.rect.height)
    return best, best_area / page_area


def _stored_picture_bytes(src) -> int:
    """Bytes the scanned pages' pictures take in ``src`` (their stored streams)."""
    total = 0
    for page in src:
        info, cover = _largest_image(page)
        if info is not None and info.get("xref") and cover >= _COVER:
            total += len(src.xref_stream_raw(info["xref"]))
    return total


def _encode(image: Image.Image, quality: int) -> bytes:
    image = image if image.mode in ("L", "RGB") else image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def _axis_aligned(info: dict) -> bool:
    a, b, c, d = info["transform"][:4]
    return abs(b) < 1e-3 and abs(c) < 1e-3 and a > 0 and d > 0


def _picture(src, xref: int):
    """(PIL image or None, ready JPEG bytes or None, current size in bytes, width, height) of a page picture, read the fast way:
    a JPEG is taken as it is (no re-encoding), anything else as decoded pixels. ``None`` for a picture that has a mask,
    an alpha channel or a colour space other than grey or RGB (the caller then draws the page instead)."""
    if src.xref_get_key(xref, "SMask")[0] != "null" or src.xref_get_key(xref, "Mask")[0] != "null":
        return None
    current = len(src.xref_stream_raw(xref))
    if "DCTDecode" in src.xref_get_key(xref, "Filter")[1]:
        raw = src.extract_image(xref)  # the stored JPEG itself
        if raw.get("ext") in ("jpeg", "jpg") and raw.get("colorspace") in (1, 3) and not raw.get("smask"):
            return None, raw["image"], current, raw["width"], raw["height"]
        return None
    pixmap = fitz.Pixmap(src, xref)
    if pixmap.alpha or pixmap.colorspace is None or pixmap.colorspace.n not in (1, 3):
        return None
    mode = "L" if pixmap.colorspace.n == 1 else "RGB"
    if pixmap.stride != pixmap.width * pixmap.n:
        return None
    return Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples), None, current, pixmap.width, pixmap.height


def _scan_page(src, out, page, info: dict, turn: int, force: bool, stats: dict, *, max_side: int, quality: int, min_dpi: int) -> None:
    width, height = page.rect.width, page.rect.height
    if turn in (90, 270):
        width, height = height, width
    limit = max(max_side, min(MAX_SIDE_CAP, math.ceil(min_dpi * max(width, height) / 72)))
    found = _picture(src, info["xref"]) if page.rotation == 0 and _axis_aligned(info) else None
    stream, pixmap, recoded = None, None, False
    if found is not None:
        image, jpeg, current, pixels_w, pixels_h = found
        if not turn and not force and max(pixels_w, pixels_h) <= limit:
            # Already small: embedded exactly as it was (no second lossy pass, no enlarging).
            if jpeg is not None:
                stream = jpeg
            else:
                pixmap = fitz.Pixmap(src, info["xref"])
        else:
            if image is None:
                with Image.open(io.BytesIO(jpeg)) as opened:
                    image = opened.copy()
            if max(image.size) > limit:
                image.thumbnail((limit, limit), Image.Resampling.LANCZOS)  # never enlarges
            if turn:
                image = image.transpose(_TURNS[turn])
            encoded = _encode(image, quality)
            if not turn and max(pixels_w, pixels_h) <= limit and len(encoded) >= current:
                # Only re-encoded to save bytes, and it did not: keep the picture as it was.
                if jpeg is not None:
                    stream = jpeg
                else:
                    pixmap = fitz.Pixmap(src, info["xref"])
            else:
                stream, recoded = encoded, True
    else:  # turned, mirrored, masked or an unusual colour space in the original: draw the page as it is shown and keep that
        target = min(max(info["width"], info["height"]) or limit, limit)
        scale = target / max(page.rect.width, page.rect.height)
        rendered = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.samples)
        if turn:
            image = image.transpose(_TURNS[turn])
        stream, recoded = _encode(image, quality), True
    sheet = out.new_page(width=width, height=height)
    if pixmap is not None:
        sheet.insert_image(sheet.rect, pixmap=pixmap, keep_proportion=False)
    else:
        sheet.insert_image(sheet.rect, stream=stream, keep_proportion=False)
    stats["recompressed"] += int(recoded)


def _build(src, turns: list[int], force: bool, *, max_side: int, quality: int, min_dpi: int) -> tuple[bytes, dict]:
    stats = {"recompressed": 0, "rotated": 0, "scans": 0}
    with fitz.open() as out:
        for index in range(src.page_count):
            page, turn = src[index], turns[index] % 360
            info, cover = _largest_image(page)
            if info is not None and info.get("xref") and cover >= _COVER:
                _scan_page(src, out, page, info, turn, force, stats, max_side=max_side, quality=quality, min_dpi=min_dpi)
                stats["scans"] += 1
            else:
                out.insert_pdf(src, from_page=index, to_page=index)
                copy = out[out.page_count - 1]
                try:
                    copy.clean_contents(sanitize=True)  # drops resources the page does not use
                except Exception:
                    pass
                if turn:
                    copy.set_rotation((copy.rotation + 360 - turn) % 360)
            stats["rotated"] += int(bool(turn))
        data = out.tobytes(garbage=4, deflate=True, no_new_id=True)
        stats["images"] = [len(out[i].get_images()) for i in range(out.page_count)]
        stats["pages"] = out.page_count
    return data, stats


def prepare_job(content: bytes, rotations: list[int] | None = None, *, max_side: int = MAX_SIDE, max_bytes: int = TARGET_BYTES,
                quality: int = QUALITY, min_dpi: int = MIN_DPI) -> Prepared:
    """The job file for the pages of ``content`` (a PDF), rebuilt as described in the module note.
    ``rotations`` is, per page of ``content``, the degrees counter-clockwise that make it upright."""
    try:
        with fitz.open(stream=content, filetype="pdf") as src:
            count = src.page_count
            turns = [*(rotations or []), *([0] * count)][:count]
            # The stored size of the pictures says up front whether the file will be above the limit: then it is built compressed
            # at once instead of being built twice.
            heavy = _stored_picture_bytes(src) > max_bytes
            data, stats = _build(src, turns, heavy, max_side=max_side, quality=quality, min_dpi=min_dpi)
            if len(data) > max_bytes and stats["scans"] and not heavy:
                data, stats = _build(src, turns, True, max_side=max_side, quality=quality, min_dpi=min_dpi)
        if stats["pages"] != count:
            raise ValueError("page count changed")
        return Prepared(data, count, len(content), stats["rotated"], stats["recompressed"], stats["images"])
    except Exception as exc:  # never a reason to lose a page: the file goes as it was
        log.warning("Reading: could not tidy the pages before sending (%s); sending them as they are", type(exc).__name__)
        try:
            with fitz.open(stream=content, filetype="pdf") as src:
                count = src.page_count
        except Exception:
            count = 0
        return Prepared(content, count, len(content), fallback=True)


def unsendable(source: bytes, candidates: dict[int, tuple], unread: set[int] | None = None, lock=None) -> dict[int, str]:
    """{page: reason} for pages of the original PDF that are not worth a job: ``"blank"`` (a page the local reading could not
    read whose picture has no ink) or ``"duplicate of page N"`` (the very same picture, with the same role, as an earlier page).
    ``candidates`` maps each page about to be sent to its role (the kinds it is sent as); a page the local reading read is
    never called blank. Best effort: on a fault nothing is dropped."""
    import contextlib
    from .inspection import _blank

    dropped: dict[int, str] = {}
    seen: dict[tuple, int] = {}
    try:
        with (lock or contextlib.nullcontext()):
            with fitz.open(stream=source, filetype="pdf") as src:
                for number in sorted(candidates):
                    page = src[number - 1]
                    info, cover = _largest_image(page)
                    scanned = info is not None and bool(info.get("xref")) and cover >= _COVER
                    if unread and number in unread:
                        scale = 400 / max(page.rect.width, page.rect.height)
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
                        with Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples) as thumb:
                            if _blank(thumb):
                                dropped[number] = "blank"
                                continue
                    if scanned:
                        # The stored bytes of the picture (and its size): identical scans give identical bytes, and nothing is decoded.
                        digest = hashlib.sha256(src.xref_stream_raw(info["xref"])).hexdigest() + f":{info['width']}x{info['height']}"
                        key = (tuple(candidates[number]), page.rotation, digest)
                        if key in seen:
                            dropped[number] = f"duplicate of page {seen[key]}"
                        else:
                            seen[key] = number
    except Exception as exc:
        log.warning("Reading: could not check for blank or repeated pages (%s); sending every page", type(exc).__name__)
        return {}
    return dropped
