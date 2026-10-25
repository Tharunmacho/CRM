"""Browser previews for Word, Excel and similar mail attachments.

PDFs and images already open in the browser. Other office files are turned into
something the browser can show:

1. When LibreOffice is installed (the same converter the document reader uses for
   Word files), the file becomes a PDF. The PDF is cached by content hash so the
   conversion runs once per distinct file.
2. Without LibreOffice, .docx and .xlsx fall back to a small read-only HTML view
   built with the standard library (paragraphs and tables of a Word file, the cells
   of the first Excel sheet). Everything is HTML-escaped and size-capped, and the
   page is served with a sandboxing Content-Security-Policy.
3. Anything else cannot be shown and the caller gets a plain-English reason.
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
import time
import zipfile
from dataclasses import dataclass
from html import escape
from xml.etree import ElementTree

from app.integrations.ocr import inspection
from app.services.document_storage import put_bytes, read_bytes

MAX_SOURCE_BYTES = 15 * 1024 * 1024        # larger files are downloaded, not previewed
MAX_UNZIPPED_BYTES = 40 * 1024 * 1024      # zip-bomb guard for the HTML fallback
MAX_XML_BYTES = 12 * 1024 * 1024
CONVERT_SECONDS = 60
MAX_PARAGRAPHS = 3000
MAX_TABLE_ROWS = 400
MAX_COLUMNS = 40
MAX_HTML_CHARS = 600_000
LIBREOFFICE_TYPES = {"doc", "docx", "odt", "rtf", "xls", "xlsx", "ods"}
HTML_HEADERS = {
    # No scripts, no network, no same-origin access, even if the markup were hostile.
    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:",
    "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
}
PDF_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
               "Content-Disposition": "inline; filename=\"preview.pdf\""}
_slots = threading.BoundedSemaphore(2)  # at most two office conversions at the same time


class PreviewUnavailable(Exception):
    """The file cannot be previewed. ``status`` is the HTTP status; ``message`` is for the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Preview:
    content: bytes
    media_type: str
    headers: dict


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _looks_right(ext: str, data: bytes) -> bool:
    if ext in {"docx", "xlsx", "odt", "ods"}:
        return data.startswith(b"PK")
    if ext in {"doc", "xls"}:
        return data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext == "rtf":
        return data.lstrip().startswith(b"{\\rtf")
    return False


def build_preview(settings, file_name: str, object_key: str, content_hash: str | None = None) -> Preview:
    ext = extension(file_name)
    if ext not in LIBREOFFICE_TYPES:
        raise PreviewUnavailable(415, "We cannot show this type of file here. Download it to open it on your computer.")
    data = read_bytes(settings, object_key)
    if len(data) > MAX_SOURCE_BYTES:
        raise PreviewUnavailable(413, "This file is too large to show here. Download it to open it on your computer.")
    if not _looks_right(ext, data):
        raise PreviewUnavailable(422, "This file does not look like a real " + ext.upper() + " document, so we cannot show it. Download it to check it.")
    digest = hashlib.sha256(data).hexdigest()
    # Never trust a stored hash for the cache name: a wrong value would show another file.
    if content_hash and content_hash != digest:
        content_hash = None
    cache_key = f"mail-previews/{digest}.pdf"
    cached = _cached(settings, cache_key)
    if cached is not None:
        return Preview(cached, "application/pdf", PDF_HEADERS)
    converted = False
    if inspection._find_libreoffice():
        pdf = _convert(data, ext)
        if pdf is not None:
            _store(settings, cache_key, pdf)
            return Preview(pdf, "application/pdf", PDF_HEADERS)
        converted = True
    if ext in {"docx", "xlsx"}:
        try:
            page = docx_html(data) if ext == "docx" else xlsx_html(data)
        except PreviewUnavailable:
            raise
        except Exception:
            raise PreviewUnavailable(422, "We could not read this file to show it. Download it to open it on your computer.") from None
        return Preview(page.encode("utf-8"), "text/html; charset=utf-8", HTML_HEADERS)
    if converted:
        raise PreviewUnavailable(422, "We could not convert this file to show it. Download it to open it on your computer.")
    raise PreviewUnavailable(415, "Previews of this type of file are not set up on this server. Download it to open it on your computer.")


def _cached(settings, key: str) -> bytes | None:
    try:
        data = read_bytes(settings, key)
    except Exception:
        return None
    return data if data.startswith(b"%PDF-") else None


def _store(settings, key: str, pdf: bytes) -> None:
    try:
        put_bytes(settings, key, pdf, "application/pdf")
    except Exception:
        pass  # A cache that cannot be written only means the next open converts again.


def _convert(data: bytes, ext: str) -> bytes | None:
    if not _slots.acquire(timeout=20):
        raise PreviewUnavailable(503, "Several previews are being prepared right now. Try again in a moment.")
    try:
        pdf = inspection._word_pdf(data, ext, time.monotonic() + CONVERT_SECONDS)
    except inspection.InspectionError:
        return None
    finally:
        _slots.release()
    return pdf if pdf.startswith(b"%PDF-") else None


# ------------------------------------------------------------ HTML fallbacks

STYLE = ("body{font:14px/1.5 system-ui,Segoe UI,Arial,sans-serif;margin:16px;color:#1b2430;background:#fff}"
         "p{margin:0 0 .7em;white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;margin:0 0 1em;max-width:100%}"
         "td,th{border:1px solid #c8ced6;padding:3px 7px;vertical-align:top;overflow-wrap:anywhere}"
         "th{background:#f1f3f6;font-weight:600;text-align:left}.note{color:#6b7280;font-size:12px;margin-top:1em}")


def _page(title: str, body: str, note: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>" + escape(title) + "</title>"
            "<style>" + STYLE + "</style></head><body>" + body
            + "<p class=\"note\">" + escape(note) + "</p></body></html>")


def _open_zip(data: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(data))
    total = sum(item.file_size for item in archive.infolist())
    if total > MAX_UNZIPPED_BYTES or len(archive.infolist()) > 5000:
        raise PreviewUnavailable(413, "This file is too large to show here. Download it to open it on your computer.")
    return archive


def _xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise PreviewUnavailable(413, "This file is too large to show here. Download it to open it on your computer.")
    raw = archive.read(info)
    # Office files never need a DTD; refusing it rules out entity-expansion tricks.
    if re.search(rb"<!(DOCTYPE|ENTITY)", raw[:8192], re.I):
        raise PreviewUnavailable(422, "We could not read this file to show it. Download it to open it on your computer.")
    return ElementTree.fromstring(raw)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts)


def docx_html(data: bytes) -> str:
    """Paragraphs and tables of a .docx as escaped HTML. Layout, images and comments are not shown."""
    with _open_zip(data) as archive:
        root = _xml(archive, "word/document.xml")
    body = root.find(W + "body") if root is not None else None
    if body is None:
        raise PreviewUnavailable(422, "We could not read this file to show it. Download it to open it on your computer.")
    out: list[str] = []
    state = {"paragraphs": 0, "chars": 0, "cut": False}

    def add_paragraph(element, sink):
        if state["paragraphs"] >= MAX_PARAGRAPHS or state["chars"] >= MAX_HTML_CHARS:
            state["cut"] = True
            return
        text = _paragraph_text(element)
        state["paragraphs"] += 1
        state["chars"] += len(text)
        if text.strip():
            sink.append("<p>" + escape(text) + "</p>")

    def add_table(table, sink):
        rows = []
        for count, row in enumerate(table.findall(W + "tr")):
            if count >= MAX_TABLE_ROWS or state["chars"] >= MAX_HTML_CHARS:
                state["cut"] = True
                break
            cells = []
            for cell in row.findall(W + "tc")[:MAX_COLUMNS]:
                inner: list[str] = []
                for child in cell:
                    if child.tag == W + "p":
                        add_paragraph(child, inner)
                    elif child.tag == W + "tbl":
                        add_table(child, inner)
                cells.append("<td>" + "".join(inner) + "</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        sink.append("<table>" + "".join(rows) + "</table>")

    for child in body:
        if child.tag == W + "p":
            add_paragraph(child, out)
        elif child.tag == W + "tbl":
            add_table(child, out)
    if not out:
        out.append("<p>This document has no text to show.</p>")
    note = "Simplified preview: text and tables only. Download the file to see the full layout."
    if state["cut"]:
        note = "This is the start of a long document. " + note
    return _page("Document preview", "".join(out), note)


S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _shared_strings(archive) -> list[str]:
    root = _xml(archive, "xl/sharedStrings.xml")
    if root is None:
        return []
    values = []
    for item in root.findall(S + "si")[:200_000]:
        # Plain text is <t>; formatted text is runs <r><t>. Phonetic guides (rPh) are skipped.
        values.append("".join((node.text or "") for node in item.findall(S + "t"))
                      + "".join((node.text or "") for run in item.findall(S + "r") for node in run.findall(S + "t")))
    return values


def _first_sheet(archive) -> tuple[str, str]:
    """Return (sheet name, part path) of the first sheet in workbook order."""
    workbook = _xml(archive, "xl/workbook.xml")
    sheets = workbook.find(S + "sheets") if workbook is not None else None
    first = sheets.find(S + "sheet") if sheets is not None else None
    name, target = (first.get("name") or "Sheet 1") if first is not None else "Sheet 1", "worksheets/sheet1.xml"
    relationship = first.get(R + "id") if first is not None else None
    rels = _xml(archive, "xl/_rels/workbook.xml.rels")
    if relationship and rels is not None:
        for item in rels.findall(PKG_REL + "Relationship"):
            if item.get("Id") == relationship and item.get("Target"):
                target = item.get("Target")
                break
    target = target.lstrip("/")
    return name, target if target.startswith("xl/") else "xl/" + target


def _column(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference or "")
    number = 0
    for char in (letters.group(0).upper() if letters else "A"):
        number = number * 26 + ord(char) - 64
    return number - 1


def xlsx_html(data: bytes) -> str:
    """The cells of the first sheet of an .xlsx as an escaped HTML table. Formulas show their last saved result."""
    with _open_zip(data) as archive:
        strings = _shared_strings(archive)
        title, part = _first_sheet(archive)
        sheet = _xml(archive, part)
    if sheet is None:
        raise PreviewUnavailable(422, "We could not read this file to show it. Download it to open it on your computer.")
    rows: list[list[str]] = []
    widest, cut = 0, False
    data_node = sheet.find(S + "sheetData")
    for row in (data_node.findall(S + "row") if data_node is not None else []):
        if len(rows) >= MAX_TABLE_ROWS:
            cut = True
            break
        cells: dict[int, str] = {}
        for position, cell in enumerate(row.findall(S + "c")):
            index = _column(cell.get("r") or "") if cell.get("r") else position
            if index >= MAX_COLUMNS:
                cut = True
                continue
            kind, value = cell.get("t"), cell.find(S + "v")
            if kind == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(S + "t"))
            elif value is None or value.text is None:
                text = ""
            elif kind == "s":
                try: text = strings[int(value.text)]
                except (ValueError, IndexError): text = ""
            elif kind == "b":
                text = "TRUE" if value.text.strip() == "1" else "FALSE"
            else:
                text = value.text
            if text:
                cells[index] = text
        if cells:
            widest = max(widest, max(cells) + 1)
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    if not any(rows):
        body = "<p>The first sheet is empty.</p>"
    else:
        body = "<h3>" + escape(title) + "</h3><table>" + "".join(
            "<tr>" + "".join("<td>" + escape(row[i] if i < len(row) else "") + "</td>" for i in range(widest)) + "</tr>"
            for row in rows) + "</table>"
    note = "Simplified preview: the first sheet only. Dates may show as numbers and formatting is not shown. Download the file for everything."
    if cut:
        note = "This is the top left part of a large sheet. " + note
    return _page("Spreadsheet preview", body, note)
