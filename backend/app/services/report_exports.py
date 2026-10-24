"""Actual OOXML workbooks and paginated PDFs from the same authorized report data."""
from __future__ import annotations

from html import escape
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from decimal import Decimal


REPORTS = {
    "funnel": ("Pipeline funnel", [("Pipeline", "pipeline", ["stage", "label", "reached", "active", "progressed", "closed", "dropOffPercent", "lostToNext", "lostToNextPercent"])]),
    "staff": ("Staff performance", [("Staff", "staff", ["name", "assigned", "completedTasks", "averageStageHours", "durationSamples", "escalations", "missedUpdates"]),
                                    ("Stage durations", "stageHours", ["stage", "label", "completed", "averageHours"])]),
    "source": ("Candidate sources", [("Sources", "sources", ["label", "count"]), ("Email accounts", "accounts", ["account", "count"])]),
    "orders": ("Order status", [("Orders", "orders", ["ref", "client", "company", "designation", "status", "openings", "matched", "selected", "deployed"])]),
    "rejections": ("Rejections and closures", [("Closures", "rejections", ["stage", "label", "count"])]),
    "advance": ("Advance collections", [("Collections", "advances", ["candidate", "order", "date", "amount", "currency"]),
                                         ("Currency totals", "advanceTotals", ["currency", "amount"]),
                                         ("Monthly totals", "advanceMonths", ["month", "currency", "amount"])])
}
LABELS = {"label": "Description", "ref": "Order", "name": "Staff", "assigned": "Initial assignments",
          "completedTasks": "Tasks completed", "averageStageHours": "Average stage hours", "durationSamples": "Stage samples",
          "missedUpdates": "Missed daily updates", "averageHours": "Average hours", "dropOffPercent": "Closure rate (%)",
          "lostToNext": "Lost before next stage", "lostToNextPercent": "Lost before next stage (%)"}


def heading(key):
    return LABELS.get(key, key[:1].upper() + key[1:])


def sheets(report, data):
    title, definitions = REPORTS[report]
    metadata = [["Report", title], ["Generated at", data["generatedAt"]], ["Timezone", data["timezone"]]]
    metadata.extend([key, val or "All"] for key, val in data["filters"].items())
    metadata.extend(["Definition", note] for note in data["notes"])
    return [("Scope and definitions", metadata)] + [
        (name, [[heading(key) for key in keys]] + [[Decimal(row[key]) if key == "amount" and row.get(key) is not None else row.get(key) for key in keys] for row in data[field]])
        for name, field, keys in definitions
    ]


def xlsx(report, data):
    """Inline strings prevent formula injection and preserve names/leading zeroes."""
    return xlsx_rows(sheets(report, data))


def xlsx_rows(workbook_sheets):
    """Reusable workbook writer for authorized exports of tabular CRM data."""
    out = BytesIO()
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                         + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                   for i in range(1, len(workbook_sheets) + 1)) + '</Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                         + ''.join(f'<sheet name="{escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(workbook_sheets, 1)) + '</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(workbook_sheets) + 1)) + '</Relationships>')
        for i, (_, rows) in enumerate(workbook_sheets, 1):
            xml_rows = []
            for row_no, row in enumerate(rows, 1):
                cells = []
                for item in row:
                    if isinstance(item, (int, float, Decimal)) and not isinstance(item, bool):
                        cells.append(f'<c t="n"><v>{item}</v></c>')
                    else:
                        # XML 1.0 excludes low control characters even within text.
                        text = ''.join(c for c in str(item if item is not None else "") if ord(c) >= 32 or c in '\t\n\r')
                        cells.append(f'<c t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
                xml_rows.append(f'<row r="{row_no}">' + ''.join(cells) + '</row>')
            archive.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{ns}"><sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>')
    return out.getvalue()


def pdf(report, data):
    import pymupdf
    title = REPORTS[report][0]
    sections = []
    for name, rows in sheets(report, data):
        rendered_rows = []
        for i, row in enumerate(rows):
            tag = "th" if i == 0 and name != "Scope and definitions" else "td"
            rendered_rows.append('<tr>' + ''.join(f'<{tag}>{escape(str(cell if cell is not None else "—"))}</{tag}>' for cell in row) + '</tr>')
        sections.append(f'<h2>{escape(name)}</h2><table>' + ''.join(rendered_rows) + '</table>')
    html = f'<h1>{escape(title)}</h1>' + ''.join(sections)
    css = "body{font-family:sans-serif;font-size:9pt;color:#17253b}h1{font-size:20pt}h2{font-size:12pt;margin-top:18pt}table{border-collapse:collapse;width:100%;margin-bottom:10pt}td,th{border:0.5pt solid #d7deea;padding:5pt;text-align:left;word-break:break-word}th{background:#edf3fb}"
    stream = BytesIO()
    story = pymupdf.Story(html=html, user_css=css)
    writer = pymupdf.DocumentWriter(stream)
    page = pymupdf.Rect(0, 0, 842, 595)
    content = page + (32, 32, -32, -32)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()
    return stream.getvalue()
