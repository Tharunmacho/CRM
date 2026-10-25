"""Local, bounded inspection before any document is sent to an OCR provider."""
from __future__ import annotations

import csv
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageOps

from .classification import classify_page, classify_pages


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


_IMAGE_FORMATS = {"jpeg", "jpg", "png", "gif", "bmp", "tiff", "webp", "mpo"}


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
            "complete": self.complete,
        }

    def pdf_for_pages(self, page_numbers: list[int]) -> bytes:
        """Only verified pages; positions in the report always name the original."""
        if not (self.complete or self.allow_partial):
            raise InspectionError("Some pages could not be read or identified. Check the page results before sending this document.")
        if not page_numbers:
            raise InspectionError("No matching pages were identified in this document.")
        if any(type(n) is not int or n < 1 or n > self.page_count for n in page_numbers):
            raise InspectionError("You chose page numbers that this document does not have.")
        wanted = sorted(set(page_numbers))
        if any(self.pages[n - 1].status != "read" or self.pages[n - 1].document_type == "unknown" for n in wanted):
            raise InspectionError("Only pages that were read and identified can be selected.")
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


def _usable_native(text: str) -> bool:
    visible = [c for c in text if not c.isspace()]
    damaged = sum(c == "\ufffd" or unicodedata.category(c) in {"Cc", "Cs", "Co", "Cn"} for c in visible)
    spaced = re.search(r"\b[A-Za-z]\s[A-Za-z]\s[A-Za-z]\s[A-Za-z]\b", text)
    return len(visible) >= 80 and damaged / max(1, len(visible)) < 0.02 and not spaced


def _blank(image: Image.Image) -> bool:
    # Empty OCR is never evidence of a blank page. Require a near-white render.
    return image.convert("L").getextrema()[0] >= 245


def _tesseract(image: Image.Image, command: str, languages: str, timeout: float) -> OCRRead:
    with tempfile.TemporaryDirectory(prefix="crm-page-ocr-") as directory:
        image_path = Path(directory) / "page.png"
        image.save(image_path, format="PNG")
        try:
            result = subprocess.run(
                [command, str(image_path), "stdout", "-l", languages, "--dpi", "300", "--psm", "3", "tsv"],
                capture_output=True, timeout=max(0.01, timeout), check=False,
                env={**os.environ, "OMP_THREAD_LIMIT": os.environ.get("OMP_THREAD_LIMIT", "1")},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return OCRRead("", 0, "Local OCR reached the page time limit.")
        except OSError:
            return OCRRead("", 0, "Tesseract could not start. Install it or configure TESSERACT_CMD.")
    if result.returncode:
        return OCRRead("", 0, "We could not read this page. Ask your system administrator to check the document reader's language settings.")
    lines: dict[tuple[str, ...], list[str]] = {}
    scores: list[float] = []
    try:
        for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
            word = (row.get("text") or "").strip()
            confidence = float(row.get("conf", "-1"))
            if word and confidence >= 0:
                key = tuple(row.get(k, "") for k in ("page_num", "block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(word)
                scores.append(confidence)
    except (TypeError, ValueError):
        return OCRRead("", 0, "Tesseract returned an unreadable response.")
    return OCRRead("\n".join(" ".join(words) for words in lines.values()), sum(scores) / len(scores) if scores else 0)


def _ocr_image(image: Image.Image, *, command: str | None, languages: str, deadline: float) -> OCRRead:
    if not command:
        return OCRRead("", 0, "Tesseract is unavailable. Install it or configure TESSERACT_CMD, then retry.")
    best = OCRRead("", 0, "No readable text was found; the page is not confirmed blank.")
    for angle in (0, 90, 180, 270):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return OCRRead(best.text, 0, "Local OCR reached the page or document time limit.")
        rotated = image if not angle else image.rotate(angle, expand=True)
        read = _tesseract(rotated, command, languages, remaining)
        if angle:
            rotated.close()
        if len(read.text.strip()) >= 40 and read.confidence > best.confidence:
            best = read
        elif not best.text and read.reason:
            best = read
        if read.confidence >= 65 and classify_page(read.text).document_type != "unknown":
            return read
    return best


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
            if getattr(image, "n_frames", 1) != 1:
                raise InspectionError("This image has several frames, which we cannot read. Save every frame in one multi-page PDF and upload that.")
            if image.width * image.height > 40_000_000:
                raise InspectionError("This image is too large to read. Make it smaller and upload it again.")
            source_format = (image.format or "image").lower()
            image.load()
            normalized = io.BytesIO()
            upright = ImageOps.exif_transpose(image).convert("RGB")
            width, height = upright.size
            upright.save(normalized, format="PNG")
            upright.close()
        with fitz.open() as picture:
            page = picture.new_page(width=width * 72 / 300, height=height * 72 / 300)
            page.insert_image(page.rect, stream=normalized.getvalue())
            return picture.tobytes(deflate=True, no_new_id=True), source_format
    except InspectionError:
        raise
    except Exception:
        raise InspectionError("We cannot open this file. Upload a PDF, a Word document or a normal photo.") from None


def inspect_document(
    data: bytes, *, tesseract_cmd: str | None = None, languages: str = "eng",
    max_pages: int = 300, page_timeout: float = 20, document_timeout: float = 600,
) -> Inspection:
    """Inspect all pages without network, database, filename or nationality rules."""
    if type(max_pages) is not int or max_pages < 1:
        raise InspectionError("The inspection page limit must be a positive integer.")
    if not all(math.isfinite(value) and value > 0 for value in (page_timeout, document_timeout)):
        raise InspectionError("Inspection time limits must be finite positive numbers.")
    deadline = time.monotonic() + document_timeout
    normalized, source_format = _normalize(data, deadline)
    command = find_tesseract(tesseract_cmd)
    pages: list[InspectedPage] = []
    warnings: list[str] = []
    try:
        source = fitz.open(stream=normalized, filetype="pdf")
    except Exception:
        raise InspectionError("We cannot open this PDF. Save it again as a PDF without a password and try again.") from None
    with source:
        if source.needs_pass:
            raise InspectionError("Password-protected PDFs must be unlocked before you upload them.")
        if not source.page_count:
            raise InspectionError("This document has no pages.")
        for index in range(source.page_count):
            number = index + 1
            reason = ""
            if number > max_pages:
                reason = f"This document has more than {max_pages} pages, which is too many to read."
            elif time.monotonic() >= deadline:
                reason = "Reading this document went over the time limit."
            if reason:
                pages.append(InspectedPage(number, "unread", "unknown", "not_processed", "", reason=reason))
                continue
            try:
                page = source[index]
                text = page.get_text("text", sort=True).strip()
                native_usable = _usable_native(text)
                image_area = sum(
                    max(0.0, (fitz.Rect(info["bbox"]) & page.rect).get_area())
                    for info in page.get_image_info()
                )
                mixed_page = image_area >= page.rect.get_area() * 0.2
                if native_usable and not mixed_page:
                    pages.append(InspectedPage(number, "read", "unknown", "native_text", text, 1.0))
                    continue
                # Bound memory for pathological page dimensions at the intended DPI.
                if page.rect.width * page.rect.height * (300 / 72) ** 2 > 40_000_000:
                    raise InspectionError("This page is too large to read. Save it at a standard page size and upload it again.")
                pixmap = page.get_pixmap(dpi=300, colorspace=fitz.csRGB, alpha=False)
                with Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples) as image:
                    if _blank(image):
                        pages.append(InspectedPage(number, "blank", "other", "rendered_blank", "", 1.0,
                                                   "The rendered page is uniformly near-white."))
                        continue
                    read = _ocr_image(image, command=command, languages=languages,
                                      deadline=min(deadline, time.monotonic() + page_timeout))
                usable = len(read.text.strip()) >= 40 and read.confidence >= 45
                combined = read.text
                if native_usable and text not in combined:
                    combined = text + "\n" + combined
                pages.append(InspectedPage(
                    number, "read" if usable else "unread", "unknown",
                    "native_text+tesseract" if native_usable else "tesseract",
                    combined, read.confidence / 100,
                    read.reason if not usable else "",
                ))
                if not usable and not pages[-1].reason:
                    pages[-1].reason = "We could not read enough text on this page. Check it, or upload a clearer copy."
            except Exception as exc:
                reason = str(exc) if isinstance(exc, InspectionError) else "We could not read this page. Save the document as a new PDF and upload it again."
                pages.append(InspectedPage(number, "unread", "unknown", "failed", "", reason=reason))
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
    return Inspection(pages, len(pages), source_format, warnings, normalized)


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
