"""Lead sheet downloads: the import report workbook (always readable, even when nothing was ignored), the lead export with
the custom question columns, the extra-columns endpoint, `extra=*` on the list and the larger page sizes.

All people are synthetic (tests/lead_sheet_fixtures.py). Needs TEST_DATABASE_URL like the other DB tests.
"""
import csv
import io
import re
import zipfile

from sqlalchemy import text

from lead_sheet_fixtures import META_HEADERS, csv_bytes, lead_row, meta_rows
from test_lead_sheet import act_as, env, imported  # noqa: F401  (env is the shared fixture)
from test_database_naming import schema_database  # noqa: F401

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def read_book(content: bytes) -> dict:
    """Sheet name -> {rows: [[(text, style, type)]], xml}, read straight from the workbook XML; `_styles` is styles.xml."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = re.findall(r'<sheet name="([^"]*)"', z.read("xl/workbook.xml").decode())
        out = {}
        for i, name in enumerate(names, 1):
            xml = z.read(f"xl/worksheets/sheet{i}.xml").decode()
            rows = []
            for row in re.findall(r"<row [^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, body in re.findall(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                    value = re.search(r"<t[^>]*>(.*?)</t>", body or "", re.S) or re.search(r"<v>(.*?)</v>", body or "")
                    style = re.search(r's="(\d+)"', attrs)
                    cells.append((value.group(1) if value else "", int(style.group(1)) if style else 0, "t" if "inlineStr" in attrs else "n"))
                rows.append(cells)
            out[name] = {"rows": rows, "xml": xml}
        out["_styles"] = z.read("xl/styles.xml").decode()
        return out


def texts(sheet) -> list:
    return [[c[0] for c in row] for row in sheet["rows"]]


def sheets_of(book) -> list:
    return [name for name in book if name != "_styles"]


def test_report_of_a_clean_upload_has_a_summary_and_every_row(env):
    batch = imported(env, meta_rows(1, 31))
    act_as(env, "manager")
    response = env.client.get(f"/api/leads/batches/{batch['batchId']}/report.xlsx")
    assert response.status_code == 200 and response.headers["content-type"].startswith(XLSX)
    disposition = response.headers["content-disposition"]
    assert re.fullmatch(r'attachment; filename="lead-import-\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+\.xlsx"', disposition), disposition
    book = read_book(response.content)
    assert sheets_of(book) == ["Summary", "All rows", "Ignored &amp; invalid"]
    summary = {r[0]: r[1] for r in texts(book["Summary"]) if len(r) > 1}
    assert summary["Rows in file"] == "30" and summary["New leads added"] == "30" and summary["Duplicates ignored"] == "0" and summary["Invalid rows"] == "0"
    assert summary["Result"] == "No duplicates or invalid rows: all 30 rows were added."
    assert "UTF-16" in summary["Detected format"].upper() and "TAB" in summary["Detected format"].upper()
    allrows = texts(book["All rows"])
    assert allrows[0][:4] == ["Row", "Outcome", "Reason", "Name"] and len(allrows) == 31
    assert {r[1] for r in allrows[1:]} == {"Added"} and [int(r[0]) for r in allrows[1:]] == list(range(2, 32))
    issues = texts(book["Ignored &amp; invalid"])
    assert len(issues) == 2 and "Nothing was ignored" in " ".join(issues[1])


def test_report_keeps_phones_as_text_and_dates_as_dates_with_a_styled_header(env):
    batch = imported(env, meta_rows(1, 4))
    book = read_book(env.client.get(f"/api/leads/batches/{batch['batchId']}/report.xlsx").content)
    rows = book["All rows"]["rows"]
    assert {c[1] for c in rows[0]} == {1}, "every header cell uses the bold, filled style"
    phone_cells = [c for c in rows[1] if c[0].startswith("+91")]
    assert phone_cells and all(c[2] == "t" and c[1] == 4 for c in phone_cells), "phone numbers are text cells"
    xml = book["All rows"]["xml"]
    assert 'state="frozen"' in xml and '<autoFilter ref="A1:M4"' in xml and "<cols>" in xml
    summary_cells = [c for row in book["Summary"]["rows"] for c in row]
    assert any(c[1] == 3 and c[2] == "n" for c in summary_cells), "the upload time is a real date-time cell"
    assert "dd\\ mmm\\ yyyy\\ hh:mm" in book["_styles"] and "<b/>" in book["_styles"]


def test_report_lists_ignored_and_invalid_rows_and_never_runs_a_formula(env):
    imported(env, meta_rows(0, 5))
    rows = [lead_row(1), lead_row(6, full_name='=HYPERLINK("http://x")', email="=cmd@example.test"), lead_row(7, phone_number="", email=""),
            lead_row(8, phone_number="p:+919000000003", id="l:1")]
    batch = imported(env, rows)
    book = read_book(env.client.get(f"/api/leads/batches/{batch['batchId']}/report.xlsx").content)
    allrows = texts(book["All rows"])
    assert [r[1] for r in allrows[1:]] == ["Ignored: duplicate", "Added", "Invalid", "Ignored: duplicate"]
    first = allrows[1]
    assert "same lead id" in first[2] and first[9], "the matched stored lead is named"
    assert "No phone number" in allrows[3][2]
    issues = texts(book["Ignored &amp; invalid"])
    assert len(issues) == 4 and {r[1] for r in issues[1:]} == {"Ignored: duplicate", "Invalid"}
    summary = {r[0]: r[1] for r in texts(book["Summary"]) if len(r) > 1}
    assert summary["Result"].startswith("1 of 4 rows were added; 2 duplicates were ignored") and "1 row was invalid" in summary["Result"]
    assert all("<f>" not in book[name]["xml"] for name in sheets_of(book))
    formula_cells = [c for c in book["All rows"]["rows"][2] if c[0].startswith("=")]
    assert formula_cells and all(c[2] == "t" for c in formula_cells), "a value that starts with = stays text"


def test_old_upload_without_stored_rows_says_so(env):
    batch = imported(env, meta_rows(0, 3))
    env.db.execute(text("UPDATE crm_lead_batches SET report = '{}'::jsonb, rows_duplicate = 2 WHERE id = :b"), {"b": batch["batchId"]})
    env.db.commit()
    book = read_book(env.client.get(f"/api/leads/batches/{batch['batchId']}/report.xlsx").content)
    assert any(r[0] == "Note" and "older upload" in r[1] for r in texts(book["Summary"]))
    assert [r[1] for r in texts(book["All rows"])[1:]] == ["Added"] * 3


def test_csv_report_always_has_rows_and_a_bom_and_is_supervisor_only(env):
    batch = imported(env, meta_rows(1, 4))
    response = env.client.get(f"/api/leads/batches/{batch['batchId']}/report.csv")
    assert response.content.startswith(b"\xef\xbb\xbf") and 'filename="lead-import-' in response.headers["content-disposition"]
    parsed = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert parsed[0][:3] == ["Row", "Outcome", "Reason"] and [r[1] for r in parsed[1:]] == ["Added"] * 3
    act_as(env, "a")
    assert env.client.get(f"/api/leads/batches/{batch['batchId']}/report.xlsx").status_code == 403
    act_as(env, "admin")
    assert env.client.get("/api/leads/batches/nope/report.xlsx").status_code == 404


def upload_with_questions(env, rows):
    headers = [*META_HEADERS, "do_you_have_a_passport?", "preferred_shift"]
    return env.client.post("/api/leads/import", files={"file": ("sheet.csv", csv_bytes(rows, headers=headers), "text/csv")})


def test_export_has_text_phones_real_dates_and_the_custom_question_columns(env):
    rows = [[*lead_row(n), "yes" if n % 2 else "", "night" if n == 1 else ""] for n in range(1, 4)]
    assert upload_with_questions(env, rows).status_code == 200
    keys = {c["key"]: c for c in env.client.get("/api/leads/columns").json()["columns"]}
    assert keys["do_you_have_a_passport?"]["label"] == "Do you have a passport" and keys["do_you_have_a_passport?"]["count"] == 2
    assert keys["preferred_shift"]["count"] == 1
    assert env.client.get("/api/leads/columns", params={"campaign": "No such campaign"}).json()["columns"] == []
    book = read_book(env.client.get("/api/leads/export.xlsx", params={"q": "Test Lead"}).content)
    sheet = book["Lead sheet"]
    head = texts(sheet)[0]
    assert head[0] == "Created" and "Do you have a passport" in head and "Preferred shift" in head
    first = sheet["rows"][1]
    assert first[0][1] == 3 and first[0][2] == "n", "Created is a real date-time"
    phone = head.index("Phone")
    assert first[phone][2] == "t" and first[phone][0].startswith("+91")
    assert 'state="frozen"' in sheet["xml"] and "<autoFilter" in sheet["xml"]
    assert "Do you have a passport" in env.client.get("/api/leads/export.csv").content.decode("utf-8-sig").splitlines()[0]


def test_list_can_carry_every_custom_answer_and_all_page_sizes(env):
    rows = [[*lead_row(n), "", "night" if n == 1 else ""] for n in range(1, 4)]
    assert upload_with_questions(env, rows).status_code == 200
    plain = env.client.get("/api/leads", params={"page_size": 10}).json()
    assert all("extra" not in item for item in plain["items"])
    carried = env.client.get("/api/leads", params={"page_size": 1000, "extra": "*", "sort": "name", "direction": "asc"}).json()
    assert carried["total"] == 3 and carried["items"][0]["extra"] == {"preferred_shift": "night"} and carried["items"][1]["extra"] == {}
    only = env.client.get("/api/leads", params={"extra": "nothing"}).json()
    assert all(item["extra"] == {} for item in only["items"])
    assert env.client.get("/api/leads", params={"page_size": 1001}).status_code == 422
