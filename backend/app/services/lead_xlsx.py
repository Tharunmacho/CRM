"""A small styled .xlsx writer (stdlib only) for the Lead sheet's downloads: import reports and the lead export.

What `report_exports.xlsx_rows` does not give: a bold, filled header row, frozen header, auto-filter, column widths, real
Excel dates with a date format, and phone numbers / ids kept as TEXT (so Excel never shows 9.19E+11 or drops a leading + or 0).
Every string is written as an inline string cell, which Excel never evaluates, so a value like `=cmd|...` stays text.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo

from app.core.config import get_settings

# Cell styles (cellXfs indexes in styles.xml below).
DEFAULT, HEADER, DATE, DATETIME, TEXT, WRAP, BOLD = 0, 1, 2, 3, 4, 5, 6

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
STYLES = (
    f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{NS}">'
    '<numFmts count="2"><numFmt numFmtId="164" formatCode="dd\\ mmm\\ yyyy"/><numFmt numFmtId="165" formatCode="dd\\ mmm\\ yyyy\\ hh:mm"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F2"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left/><right/><top/><bottom style="thin"><color rgb="FF8EA3BF"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="7">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '</cellXfs></styleSheet>'
)

_EPOCH = datetime(1899, 12, 30)


def agency_zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().agency_timezone)
    except Exception:  # unknown zone name in settings
        return ZoneInfo("UTC")


def excel_serial(value: datetime | date) -> float:
    """A date or time as an Excel serial number; an aware time is shown in the agency's time zone."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(agency_zone()).replace(tzinfo=None)
        delta = value - _EPOCH
        return delta.days + delta.seconds / 86400
    return (datetime(value.year, value.month, value.day) - _EPOCH).days


def clean(text: str) -> str:
    """XML 1.0 excludes the low control characters even inside text."""
    return "".join(c for c in text if ord(c) >= 32 or c in "\t\n\r")


def column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def cell_xml(ref: str, value, style: int | None = None) -> str:
    """One cell. Text is an inline string (never a formula); numbers are numbers; dates are serials with a date format."""
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>' if style else ""
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, datetime):
        return f'<c r="{ref}" s="{DATETIME if style in (None, DEFAULT) else style}"><v>{excel_serial(value):.6f}</v></c>'
    if isinstance(value, date):
        return f'<c r="{ref}" s="{DATE if style in (None, DEFAULT) else style}"><v>{excel_serial(value)}</v></c>'
    if isinstance(value, (int, float, Decimal)):
        return f'<c r="{ref}"' + (f' s="{style}"' if style else "") + f'><v>{value}</v></c>'
    text = clean(str(value))
    if style in (None, DEFAULT) and "\n" in text:
        style = WRAP
    return f'<c r="{ref}" s="{style if style else TEXT}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def sheet_xml(rows: list[list], widths: list[float] | None, header: bool, freeze: bool, filtered: bool, bold_first: bool) -> str:
    width = max((len(r) for r in rows), default=1)
    out = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            style = HEADER if header and r == 1 else (BOLD if bold_first and c == 0 else None)
            cells.append(cell_xml(f"{column_name(c)}{r}", value, style))
        height = ' ht="30" customHeight="1"' if header and r == 1 else ""
        if not height:
            lines = max((len(value.splitlines()) for value in row if isinstance(value, str)), default=1)
            if lines > 1:
                height = f' ht="{min(409, 18 * lines + 8)}" customHeight="1"'
        out.append(f'<row r="{r}"{height}>' + "".join(cells) + "</row>")
    cols = ""
    if widths:
        cols = "<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)) + "</cols>"
    pane = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft"/></sheetView></sheetViews>') if freeze else '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
    auto = f'<autoFilter ref="A1:{column_name(width - 1)}{max(1, len(rows))}"/>' if filtered and rows else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}">{pane}{cols}<sheetData>{"".join(out)}</sheetData>{auto}</worksheet>'


def safe_sheet_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
    unique, n = base, 2
    while unique.lower() in taken:
        unique = f"{base[:28]} {n}"
        n += 1
    taken.add(unique.lower())
    return unique


def build_workbook(sheets: list[dict]) -> bytes:
    """`sheets`: dicts with `name`, `rows` (first row is the header when `header`), optional `widths`, `header`, `freeze`, `filter`, `bold_first`."""
    out = BytesIO()
    taken: set[str] = set()
    names = [safe_sheet_name(s["name"], taken) for s in sheets]
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                         '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                         + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                   for i in range(1, len(sheets) + 1)) + "</Types>")
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                         + "".join(f'<sheet name="{escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1)) + "</sheets></workbook>")
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
                         + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", STYLES)
        for i, sheet in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(sheet["rows"], sheet.get("widths"), sheet.get("header", True), sheet.get("freeze", True),
                                                                    sheet.get("filter", True), sheet.get("bold_first", False)))
    return out.getvalue()


def fit_widths(rows: list[list], minimum: float = 8, maximum: float = 48) -> list[float]:
    """Widths from the longest line in the first 200 rows (dates count as 14 characters)."""
    width = max((len(r) for r in rows), default=0)
    sizes = [minimum] * width
    for row in rows[:200]:
        for c, value in enumerate(row):
            length = (14 if isinstance(value, (datetime, date)) else
                      max((len(line) for line in str(value).splitlines()), default=0) if value is not None else 0)
            sizes[c] = max(sizes[c], min(maximum, length + 3))
    return sizes
