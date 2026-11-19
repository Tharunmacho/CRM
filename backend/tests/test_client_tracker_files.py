"""The Client tracker downloads as files: the .xlsx and .csv a client opens in Excel, built from fake rows (no database)."""
from __future__ import annotations

import csv
import re
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import current_user
from app.db.session import get_db
from app.services import client_tracker as tracker

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
                           agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.api.routers import reports as subject

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
LONG_CLIENT = "Al Muhaidib Industrial Contracting & Heavy Engineering Company Limited (Kuwait branch)"
TRICKY = 'Wait, retest "3rd DEC"; call back'
TAMIL_NAME = "முருகன் சந்திரன்"
MIXED_REMARK = "புதன்கிழமை / बुधवार / الأربعاء"


class Status:
    def __init__(self, value):
        self.value = value


def _rows() -> list[dict]:
    """Forty placements built through the real assembly: long names, blank advances, odd remarks, other scripts."""
    found, remarks, stages = [], [], []

    def add(client, name, job, agency, stage, deployed=False, status=None, amount=None, total=None, remark=None, travel=None, country="Kuwait"):
        n = len(found) + 1
        app_id, candidate = f"a{n:03d}", f"c{n:03d}"
        found.append((app_id, candidate, stage, Status("deployed" if deployed else "active"), name, agency, client, country, f"o{client}",
                      "REF", country, job))
        if status or amount or total:    # the placement's stage-8 record: advance status, amount, total service charge
            data = {k: v for k, v in (("advanceStatus", status), ("amount", amount), ("serviceChargeTotal", total)) if v is not None}
            stages.append((app_id, 8, 1, None, amount, "INR" if amount else None, data))
        if remark:
            remarks.append((app_id, candidate, remark))
        if travel:
            stages.append((app_id, 11, 1, None, None, None, {"travelDate": travel}))

    add("Aldhow", "Kabil", "Rigger", "Asian tech", 11, True, travel="2026-11-11", remark="Travelled")
    add("Aldhow", "Elumalai", "Helper", "Admire", 11, True, travel="2026-11-22")
    add("Aldhow", "Sanjay", "Elect engineer", "care", 11, True, travel="2026-11-01")
    add("Aldhow", "Sakthi", "Fitter", "Admire", 11, True, travel="2026-11-03")
    add("Aldhow", "Durgeshwaran", "Fitter", "Admire", 11, True)
    add(LONG_CLIENT, "Ahmed Al-Qahtani", "Senior Heavy Equipment Maintenance Supervisor (Diesel & Hydraulics)", "Shajahan", 3,
        remark=TRICKY, country="Saudi Arabia")
    add("Ammachi", "Tarun", "ITC Chef", "Shajahan", 10, status="collected", amount=20000, total=55000, remark='=HYPERLINK("http://x","y")+1', country="Sri lanka")
    add("Ammachi", "Rahim", "Dosa & parotta master", "Care", 5, remark="+91 98765 43210\nsecond line\r\nthird", country="Sri lanka")
    add("Ammachi", "Sagar", "Chef", "Jothi", 5, remark="@SUM(1+1) <b>x</b> & more", country="Sri lanka")
    add("Ammachi", "Mukesh", "Chef", "Jothi", 5, remark="-cmd|' /C calc'!A0", country="Sri lanka")
    add("VB World", TAMIL_NAME, "சமையல்காரர்", "Jothi", 3, remark=MIXED_REMARK, country="Dubai")
    add("VB World", "José Ñuñez 日本語", "Cook", "=Jothi", 2, remark="x" * 150, country="Dubai")
    add("VB World", "Bell\x07 name", "Cook", "", 1, remark="ring\x07", country="Dubai")
    add("Finesco", "KRISHNAMOORTHY THIRUVENGADAM", "ELECTRICAL FOREMAN", "JOTHI TRAVELS", 9, status="collected", amount=10000, total=40000)
    add("Finesco", "Pending Person", "Safety sup", "Care", 8, status="pending", amount=10000, total=45000)
    for i in range(40 - len(found)):
        add("Tawseel", f"Candidate {i}", "Driver", "Jothi", 6)
    rows = tracker._assemble(found, stages, remarks, "INR")
    return tracker.build(rows, tracker.Filters())["rows"]


def _sheet(payload: bytes):
    with ZipFile(BytesIO(payload)) as book:
        return (ElementTree.fromstring(book.read("xl/workbook.xml")), ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml")),
                ElementTree.fromstring(book.read("xl/styles.xml")))


def _cells(sheet):
    return [[(c.get("r"), c.get("t"), c.get("s"), "".join(c.itertext())) for c in row.findall("m:c", NS)] for row in sheet.findall(".//m:row", NS)]


def test_the_xlsx_is_a_real_sheet_like_the_clients():
    rows = _rows()
    assert len(rows) == 40
    workbook, sheet, styles = _sheet(tracker.xlsx_bytes(rows))
    assert [s.get("name") for s in workbook.findall(".//m:sheet", NS)] == ["Client tracker"]
    pane = sheet.find(".//m:pane", NS)
    assert pane.get("state") == "frozen" and pane.get("ySplit") == "1"
    assert sheet.find("m:autoFilter", NS).get("ref") == "A1:I41"
    widths = [float(c.get("width")) for c in sheet.findall(".//m:col", NS)]
    assert widths == [float(w) for w in tracker.WIDTHS] and len(widths) == 9
    table = _cells(sheet)
    assert [cell[3] for cell in table[0]] == list(tracker.COLUMNS)
    assert all(cell[2] == "1" for cell in table[0])                                    # the bold header style
    assert [row[0][1] for row in table[1:]] == [None] * 40                              # S.No is a number cell (no t="inlineStr")
    assert [int(row[0][3]) for row in table[1:]] == list(range(1, 41))
    assert all(len(row) == 9 for row in table)
    assert not sheet.findall(".//m:f", NS)                                              # no formula anywhere
    assert styles.findall(".//m:fonts/m:font", NS)[1].find("m:b", NS) is not None


def test_every_body_cell_wraps_and_is_boxed_and_no_row_height_is_forced():
    _, sheet, styles = _sheet(tracker.xlsx_bytes(_rows()))
    grid = styles.findall(".//m:cellXfs/m:xf", NS)[7]
    assert grid.find("m:alignment", NS).get("wrapText") == "1" and grid.get("borderId") == "2"
    assert all(cell[2] == "7" for row in _cells(sheet)[1:] for cell in row)             # also the blank cells, so the grid is complete
    rows = sheet.findall(".//m:row", NS)
    assert rows[0].get("customHeight") == "1"
    assert all(row.get("customHeight") is None for row in rows[1:])                      # Excel sizes the rows to the wrapped text


def test_the_widths_fit_the_names_on_the_owners_sheet():
    assert tracker.WIDTHS[0] >= 9 and tracker.WIDTHS[2] >= 13                           # room for the header and its filter button
    assert tracker.WIDTHS[3] >= len("KRISHNAMOORTHY THIRUVENGADAM") + 4 and tracker.WIDTHS[4] >= len("Dosa & parotta master") + 2
    assert tracker.WIDTHS[8] >= len("Offer declined by candidate/ Position regarding") + 2


def test_text_that_looks_like_a_formula_stays_text_in_the_xlsx_and_unicode_is_intact():
    _, sheet, _ = _sheet(tracker.xlsx_bytes(_rows()))
    text = {cell[3]: cell[1] for row in _cells(sheet) for cell in row}
    for danger in ('=HYPERLINK("http://x","y")+1', "@SUM(1+1) <b>x</b> & more", "-cmd|' /C calc'!A0", "=Jothi"):
        assert text[danger] == "inlineStr"
    for name in (TAMIL_NAME, "José Ñuñez 日本語", MIXED_REMARK, TRICKY, LONG_CLIENT):
        assert name in text                                                              # intact, nothing shortened
    assert "Bell name" in text and "ring" in text                                        # the control character is dropped, the XML stays valid
    assert text["Travelled on 11th NOV 2026"] == "inlineStr" and "Travelled on 22nd NOV 2026" in text and "Travelled on 3rd NOV 2026" in text
    assert "Travelled on 1st NOV 2026" in text and "Travelled" in text                   # no travel date on file: plain Travelled


def test_the_xlsx_opens_in_openpyxl_when_it_is_installed():
    try:
        import openpyxl
    except ImportError:
        return
    sheet = openpyxl.load_workbook(BytesIO(tracker.xlsx_bytes(_rows()))).active
    assert sheet.title == "Client tracker" and sheet.freeze_panes == "A2" and sheet.auto_filter.ref == "A1:I41"
    assert sheet["A1"].font.b and [sheet.cell(r, 1).value for r in range(2, 42)] == list(range(1, 41))
    assert all(sheet.cell(r, c).data_type != "f" for r in range(1, 42) for c in range(1, 10))
    assert sheet.cell(2, 2).alignment.wrap_text and sheet.cell(2, 2).border.left.style == "thin"


def test_the_csv_has_a_bom_nine_columns_and_guards_formulas_but_keeps_other_text():
    payload = tracker.csv_bytes(_rows())
    assert payload[:3] == b"\xef\xbb\xbf" and b"\r\n" in payload
    table = list(csv.reader(StringIO(payload.decode("utf-8-sig"), newline="")))
    assert table[0] == list(tracker.COLUMNS) and len(table) == 41 and {len(row) for row in table} == {9}
    assert [int(row[0]) for row in table[1:]] == list(range(1, 41))
    by_name = {row[3]: row for row in table[1:]}
    assert by_name["Tarun"][8] == "'=HYPERLINK(\"http://x\",\"y\")+1" and by_name["Tarun"][6] == "20k collected/55k"
    assert by_name["Sagar"][8].startswith("'@") and by_name["Mukesh"][8].startswith("'-")
    assert by_name["José Ñuñez 日本語"][5] == "'=Jothi" and by_name[TAMIL_NAME][1] == "VB World" and by_name[TAMIL_NAME][8] == MIXED_REMARK
    assert by_name["Ahmed Al-Qahtani"][8] == TRICKY and by_name["Ahmed Al-Qahtani"][1] == LONG_CLIENT   # commas and quotes survive the quoting
    assert by_name["Rahim"][8] == "'+91 98765 43210 second line third"                                  # a remark is one line
    assert by_name["Bell name"][8] == "ring"
    assert by_name["Durgeshwaran"][7] == "Travelled" and by_name["Kabil"][7] == "Travelled on 11th NOV 2026"
    assert by_name["Pending Person"][6] == "Pending/45k" and by_name["Candidate 0"][6] == ""


def test_the_download_names_the_file_by_date_and_sets_the_right_headers():
    app = FastAPI()
    app.include_router(subject.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id="admin", role=SimpleNamespace(value="admin"))
    app.dependency_overrides[get_db] = lambda: None
    client = TestClient(app)
    plain = [{k: v for k, v in r.items() if k != "sNo"} for r in _rows()]
    with patch.object(subject.client_tracker, "read_rows", return_value=plain):
        xlsx = client.get("/api/reports/client-tracker", params={"format": "xlsx"})
        csv_response = client.get("/api/reports/client-tracker", params={"format": "csv"})
    assert xlsx.status_code == 200 and csv_response.status_code == 200
    assert re.fullmatch(r'attachment; filename="client-tracker-\d{4}-\d{2}-\d{2}\.xlsx"', xlsx.headers["content-disposition"])
    assert re.fullmatch(r'attachment; filename="client-tracker-\d{4}-\d{2}-\d{2}\.csv"', csv_response.headers["content-disposition"])
    assert xlsx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert csv_response.headers["content-type"] == "text/csv; charset=utf-8" and csv_response.content[:3] == b"\xef\xbb\xbf"
    assert xlsx.content[:2] == b"PK" and xlsx.headers["cache-control"] == "private, no-store"
