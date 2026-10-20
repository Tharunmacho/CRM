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
    """Actionable input/inspection failure, safe to show without document data."""


@dataclass
class InspectedPage:
    page_number: int
    status: str
    document_type: str
    method: str
    text: str = field(repr=False)
    confidence: float = 0.0
    reason: str = ""


@dataclass
class Inspection:
    pages: list[InspectedPage]
    page_count: int
    source_format: str
    warnings: list[str]
    _pdf: bytes = field(repr=False)

    @property
    def resume_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.document_type == "resume" and p.status == "read"]

    @property
    def passport_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.document_type == "passport" and p.status == "read"]

    @property
    def aadhaar_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.document_type == "aadhaar" and p.status == "read"]

    @property
    def unread_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.status == "unread"]

    @property
    def unknown_pages(self) -> list[int]:
        return [p.page_number for p in self.pages if p.status == "read" and p.document_type == "unknown"]

    @property
    def complete(self) -> bool:
        return len(self.pages) == self.page_count and not self.unread_pages and not self.unknown_pages

    def to_dict(self, *, include_text: bool = False) -> dict:
        pages = [asdict(page) for page in self.pages]
        if not include_text:
            for page in pages:
                page.pop("text")
        return {
            "source_format": self.source_format, "page_count": self.page_count,
            "pages": pages, "resume_pages": self.resume_pages,
            "passport_pages": self.passport_pages, "aadhaar_pages": self.aadhaar_pages,
            "unread_pages": self.unread_pages,
            "unknown_pages": self.unknown_pages, "warnings": list(self.warnings),
            "complete": self.complete,
        }

    def pdf_for_pages(self, page_numbers: list[int]) -> bytes:
        """Only verified pages; positions in the report always name the original."""
        if not self.complete:
            raise InspectionError("Some pages are unread or unclassified. Review the inspection before sending this document.")
        if not page_numbers:
            raise InspectionError("No matching pages were identified in this document.")
        if any(type(n) is not int or n < 1 or n > self.page_count for n in page_numbers):
            raise InspectionError("Selected page numbers are outside this document.")
        wanted = sorted(set(page_numbers))
        if any(self.pages[n - 1].status != "read" for n in wanted):
            raise InspectionError("Only successfully read pages can be selected.")
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
        return OCRRead("", 0, "Tesseract could not read this page. Check the configured OCR language installation.")
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
        raise InspectionError("Word inspection requires LibreOffice. Install LibreOffice on the server or upload an exported PDF.")
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
            raise InspectionError("Word-to-PDF conversion failed or timed out. Export the document as PDF and retry.") from None
        output = root / "document.pdf"
        if result.returncode or not output.is_file():
            raise InspectionError("LibreOffice could not convert this Word document. Export it as PDF and retry.")
        return output.read_bytes()


def _normalize(data: bytes, deadline: float) -> tuple[bytes, str]:
    if not data:
        raise InspectionError("The uploaded document is empty.")
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
                raise InspectionError("Multiple-frame images are not supported. Convert every frame to a multipage PDF and retry.")
            if image.width * image.height > 40_000_000:
                raise InspectionError("The image exceeds the 40-million-pixel inspection limit. Resize it and retry.")
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
        raise InspectionError("Unsupported or damaged document. Upload a valid PDF, Word document or single-frame image.") from None


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
        raise InspectionError("The PDF cannot be opened. Export a valid, unlocked PDF and retry.") from None
    with source:
        if source.needs_pass:
            raise InspectionError("Password-protected PDFs must be unlocked before inspection.")
        if not source.page_count:
            raise InspectionError("The document has no pages.")
        for index in range(source.page_count):
            number = index + 1
            reason = ""
            if number > max_pages:
                reason = f"The document exceeds the {max_pages}-page inspection limit."
            elif time.monotonic() >= deadline:
                reason = "The document inspection time limit was reached."
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
                    raise InspectionError("This page is too large to render safely at 300 DPI. Export it at a standard page size.")
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
                    pages[-1].reason = "OCR did not produce enough reliable text. Review or replace this page."
            except Exception as exc:
                reason = str(exc) if isinstance(exc, InspectionError) else "This page could not be inspected. Export a new PDF and retry."
                pages.append(InspectedPage(number, "unread", "unknown", "failed", "", reason=reason))
    classified = classify_pages([p.text if p.status == "read" else "" for p in pages])
    for page, verdict in zip(pages, classified):
        if page.status == "read":
            page.document_type = verdict.document_type
            page.confidence = min(page.confidence, verdict.confidence)
            page.reason = verdict.reason
    if any(p.status == "unread" for p in pages):
        warnings.append("One or more pages could not be read. No pages will be sent to Veris until the document is resolved.")
    if any(p.status == "read" and p.document_type == "unknown" for p in pages):
        warnings.append("One or more pages need classification review. No pages will be sent to Veris until the document is resolved.")
    return Inspection(pages, len(pages), source_format, warnings, normalized)


def restore_inspection(data: bytes, report: dict, *, document_timeout: float = 600) -> Inspection:
    """Restore a trusted, complete checkpoint without repeating local OCR.

    The caller must verify source hash and inspection settings first. Reopening
    the original verifies physical page count; Word may need reconversion.
    """
    try:
        count = report["page_count"]
        entries = report["pages"]
        if (
            report.get("complete") is not True or type(count) is not int or count < 1
            or not isinstance(entries, list) or len(entries) != count
        ):
            raise ValueError()
        pages = []
        for number, entry in enumerate(entries, 1):
            if (
                type(entry["page_number"]) is not int or entry["page_number"] != number
                or entry["status"] not in {"read", "blank"}
                or entry["document_type"] not in {"resume", "passport", "aadhaar", "other"}
                or (entry["status"] == "blank" and entry["document_type"] != "other")
            ):
                raise ValueError()
            pages.append(InspectedPage(
                number, entry["status"], entry["document_type"], entry["method"], "",
                confidence=entry.get("confidence", 0.0), reason=entry.get("reason", ""),
            ))
    except (KeyError, TypeError, ValueError):
        raise InspectionError("Saved page inspection is incomplete or invalid.") from None
    normalized, source_format = _normalize(data, time.monotonic() + document_timeout)
    try:
        with fitz.open(stream=normalized, filetype="pdf") as source:
            if source.needs_pass or source.page_count != count or source_format != report["source_format"]:
                raise InspectionError("Saved inspection no longer matches the original document.")
    except InspectionError:
        raise
    except Exception:
        raise InspectionError("The original document could not be reopened.") from None
    return Inspection(pages, count, source_format, list(report.get("warnings", [])), normalized)
