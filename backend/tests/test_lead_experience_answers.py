"""Imported experience answers remain available to the ordinary list and detail.

Uses synthetic people and the existing disposable PostgreSQL fixture only. The
additive question metadata requires no schema, parser or live-record changes.
"""
import json
import csv
import io
import zipfile
from datetime import date
from xml.etree import ElementTree

import pytest
from sqlalchemy import event, func, select

from app.db.models import Lead
from app.services import lead_parsing, lead_sheet, lead_xlsx
from lead_sheet_fixtures import csv_bytes, xlsx_bytes
from test_database_naming import schema_database  # noqa: F401
from test_lead_sheet import env, upload  # noqa: F401


def excel_rows(content):
    """Use actual XLSX cell references, including sparse rows with blank cells."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    result = []
    width = 0
    for row in root.findall("s:sheetData/s:row", ns):
        cells = {}
        for cell in row.findall("s:c", ns):
            index = 0
            for char in cell.attrib["r"]:
                if char.isalpha():
                    index = index * 26 + ord(char.upper()) - 64
            value = cell.find("s:is/s:t", ns) if cell.attrib.get("t") == "inlineStr" else cell.find("s:v", ns)
            cells[index - 1] = (value.text if value is not None else "", cell.attrib.get("t", "n"), int(cell.attrib.get("s", 0)))
            width = max(width, index)
        result.append(cells)
    return [[row.get(index, ("", "n", 0)) for index in range(width)] for row in result]


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_experience_answers_survive_import_storage_list_detail_and_repeat(env, format):
    restaurant = "Do you have experience in the restaurant industry?"
    total = "Do you have work experience?"
    headers = ["Name", "Phone", restaurant, total]
    cases = [
        ("Yes", "Yes", None, None),
        ("No", "No", 0, 0),
        ("", "", None, None),
        ("3.5 years", "6 months", 3.5, 0.5),
        ("1-3 years", "10+ years", 1, 10),
        ("Unknown", "Not supplied", None, None),
    ]
    rows = [[f"Synthetic Experience {index}", f"90000500{index:02d}", rest, work]
            for index, (rest, work, _, _) in enumerate(cases)]
    content = csv_bytes(rows, headers=headers) if format == "csv" else xlsx_bytes([headers, *rows])
    imported = upload(env, content, name=f"experience.{format}")
    assert imported.status_code == 200, imported.text
    assert imported.json()["rowsNew"] == len(cases) and imported.json()["rowsInvalid"] == 0
    mapping = {column["header"]: column["field"] for column in imported.json()["columns"]}
    assert mapping[restaurant] == "experienceRestaurant" and mapping[total] == "experienceTotal"

    # No extra=* or detail-only expansion is required to receive these answers.
    listed = env.client.get("/api/leads", params={"sort": "name", "direction": "asc"})
    assert listed.status_code == 200
    assert listed.json()["total"] == len(cases)
    for item, (rest, work, rest_years, work_years) in zip(listed.json()["items"], cases, strict=True):
        assert "raw" not in item and "extra" not in item
        assert item["experienceRestaurantText"] == (rest or None)
        assert item["experienceTotalText"] == (work or None)
        assert item["experienceRestaurantYears"] == rest_years
        assert item["experienceTotalYears"] == work_years
        assert item["experienceRestaurantQuestion"] == restaurant
        assert item["experienceTotalQuestion"] == total
        stored = env.db.get(Lead, item["id"])
        assert stored.experience_restaurant_text == (rest or None)
        assert stored.experience_total_text == (work or None)
        detail = env.client.get("/api/leads/" + item["id"]).json()
        for field in ("experienceRestaurantText", "experienceTotalText", "experienceRestaurantYears", "experienceTotalYears",
                      "experienceRestaurantQuestion", "experienceTotalQuestion"):
            assert detail[field] == item[field]
        assert detail["raw"].get(restaurant) == (rest or None)
        assert detail["raw"].get(total) == (work or None)

    repeated = upload(env, content, name=f"experience.{format}")
    assert repeated.status_code == 200
    assert repeated.json()["rowsNew"] == 0 and repeated.json()["rowsDuplicate"] == len(cases)
    assert len(env.db.scalars(select(Lead)).all()) == len(cases)


def test_numeric_years_and_separate_yes_no_question_are_both_retained(env):
    question = "Do you have relevant work experience?"
    headers = ["Name", "Phone", "Total experience", question]
    response = upload(env, csv_bytes([["Synthetic Mixed Answer", "9000050099", "4 years", "Yes"]], headers=headers))
    assert response.status_code == 200
    assert question in response.json()["unmappedColumns"]
    listed = env.client.get("/api/leads", params={"extra": "*"}).json()["items"][0]
    assert listed["experienceTotalYears"] == 4
    assert listed["experienceTotalText"] == "4 years"
    assert listed["experienceTotalQuestion"] == "Total experience"
    assert listed["extra"] == {question: "Yes"}
    detail = env.client.get("/api/leads/" + listed["id"]).json()
    assert detail["raw"][question] == "Yes" and detail["raw"]["Total experience"] == "4 years"


@pytest.mark.parametrize("answer,legacy_years", [("Yes", None), ("No", 0)])
def test_compound_threshold_question_keeps_answer_without_inventing_years(answer, legacy_years):
    threshold = "Do you have 5+ years of experience in a relevant company with a business qualification?"
    operations = "Do you have experience in customer handling, office administration and coordination?"
    headers = ["Name", "Phone", threshold, operations]
    values = lead_parsing.normalise_row(headers, ["Synthetic Compound Answer", "9000050088", answer, "Yes"],
                                        lead_parsing.map_headers(headers))
    assert values["experience_total_text"] == answer
    assert values["experience_total_years"] == legacy_years
    assert values["experience_total_years"] != 5
    assert values["extra"] == {operations: "Yes"}
    assert values["raw"][threshold] == answer and values["raw"][operations] == "Yes"


def test_explicit_import_mapping_controls_question_context_in_list_detail_and_edit(env):
    source_question = "do_you_have_5+_years_of_experience_in_a_relevant_company_with_an_mba_or_equivalent_business_qualification?"
    numeric_question = "what_is_your_total_years_of_experience?"
    headers = ["Name", "Phone", source_question, numeric_question]
    # Explicit override intentionally differs from the fuzzy field guess.
    mapping = {source_question: "experienceRestaurant", numeric_question: "experienceTotal"}
    response = upload(env, csv_bytes([["Synthetic Mapped Answers", "9000050077", "No", "6 years"]], headers=headers),
                      mapping=json.dumps(mapping))
    assert response.status_code == 200
    item = env.client.get("/api/leads").json()["items"][0]
    question = "Do you have 5+ years of experience in a relevant company with an mba or equivalent business qualification?"
    assert len(question) > 80 and item["experienceRestaurantQuestion"] == question
    assert item["experienceTotalQuestion"] == "What is your total years of experience?"
    assert item["experienceRestaurantText"] == "No" and item["experienceTotalYears"] == 6
    assert "raw" not in item and "columnMap" not in item and "importColumnMap" not in item
    edited = env.client.patch("/api/leads/" + item["id"], json={"notes": "Called the synthetic lead"})
    assert edited.status_code == 200 and edited.json()["experienceRestaurantQuestion"] == question
    detail = env.client.get("/api/leads/" + item["id"]).json()
    assert detail["experienceRestaurantQuestion"] == question


def test_legacy_record_without_mapping_has_no_invented_question(env):
    row = Lead(name="Synthetic Legacy Answer", phone="9000050066", experience_total_text="Yes",
               raw={"do_you_have_10_years_experience?": "Yes"}, extra={})
    env.db.add(row)
    env.db.commit()
    result = env.client.get("/api/leads").json()["items"][0]
    assert result["experienceTotalQuestion"] is None and result["experienceRestaurantQuestion"] is None
    assert result["experienceTotalText"] == "Yes" and result["experienceTotalYears"] is None


def test_question_join_does_not_issue_a_lookup_for_every_lead(env):
    rows = [[f"Synthetic Query Count {i}", f"900006{i:04d}", "Yes"] for i in range(40)]
    assert upload(env, csv_bytes(rows, headers=["Name", "Phone", "Do you have work experience?"])).status_code == 200
    statements = []
    def count_select(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(env.engine, "before_cursor_execute", count_select)
    try:
        result = env.client.get("/api/leads", params={"page_size": 100}).json()
    finally:
        event.remove(env.engine, "before_cursor_execute", count_select)
    assert len(result["items"]) == 40
    assert all(item["experienceTotalQuestion"] == "Do you have work experience?" for item in result["items"])
    assert len(statements) <= 3
    assert sum("crm_lead_batches" in statement for statement in statements) == 1


def test_missing_disabled_or_ambiguous_question_mapping_is_not_guessed():
    assert lead_sheet.experience_question(None, "experienceTotal") is None
    assert lead_sheet.experience_question({"Do you have experience?": None}, "experienceTotal") is None
    assert lead_sheet.experience_question({"Question one": "experienceTotal", "Question two": "experienceTotal"}, "experienceTotal") is None


def test_answer_filled_from_later_sheet_is_not_mislabeled_with_original_question(env):
    first_question = "How many years of experience do you have?"
    later_question = "Do you have office administration experience?"
    person = ["Synthetic Later Answer", "9000050055"]
    first = upload(env, csv_bytes([[*person, ""]], headers=["Name", "Phone", first_question]))
    assert first.status_code == 200
    later = upload(env, csv_bytes([[*person, "Yes"]], headers=["Name", "Phone", later_question]), fill_empty="true")
    assert later.status_code == 200 and later.json()["rowsUpdated"] == 1
    result = env.client.get("/api/leads").json()["items"][0]
    assert result["batchId"] == first.json()["batchId"]
    assert result["experienceTotalText"] == "Yes"
    assert result["experienceTotalQuestion"] is None


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_export_exact_batch_preserves_21_yes_and_9_no_with_original_question(env, file_format):
    question = "do_you_have_5+_years_of_experience_in_a_relevant_company_with_an_mba_or_equivalent_business_qualification?"
    headers = ["Name", "Phone", question]
    assert upload(env, csv_bytes([["Synthetic Other Batch", "9000070099", "Yes"]], headers=headers)).status_code == 200
    rows = [[f"Synthetic Export {index:02d}", f"90000700{index:02d}", "Yes" if index < 21 else "No"] for index in range(30)]
    imported = upload(env, csv_bytes(rows, headers=headers))
    assert imported.status_code == 200 and imported.json()["rowsNew"] == 30
    response = env.client.get(f"/api/leads/export.{file_format}", params={"batch_id": imported.json()["batchId"]})
    assert response.status_code == 200
    exported = (list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")))) if file_format == "csv"
                else [[cell[0] for cell in row] for row in excel_rows(response.content)])
    header, body = exported[0], exported[1:]
    assert header[:7] == ["Name / Age / Contact", "Job applied", "Education", "Experience (India)", "Experience (Abroad)", "Experience (total)", "Location"]
    assert len(body) == 30
    answer_column = header.index("Experience (total)")
    question_column = header.index("Total experience question")
    assert [row[answer_column] for row in body].count("Yes") == 21
    assert [row[answer_column] for row in body].count("No") == 9
    expected_question = "Do you have 5+ years of experience in a relevant company with an mba or equivalent business qualification?"
    assert len(expected_question) > 80 and all(row[question_column] == expected_question for row in body)
    assert not any("years)" in name.lower() for name in header if "Experience" in name)
    assert all(row[header.index("Experience (India)")] == "" and row[header.index("Experience (Abroad)")] == "" for row in body)
    assert "Synthetic Other Batch" not in {row[header.index("Name")] for row in body}
    assert all("Age:" not in row[0] and row[header.index("Phone")] in row[0] for row in body)


def test_excel_experience_preserves_ranges_months_and_true_numeric_answers(env, monkeypatch):
    from app.services import daily_work
    monkeypatch.setattr(daily_work, "agency_today", lambda now: date(2026, 11, 12))
    headers = ["Name", "Phone", "India experience", "Overseas experience", "Total experience", "Restaurant experience",
               "Date of birth", "Education", "Position", "City", "State", "Email"]
    assert upload(env, csv_bytes([["Synthetic Export Durations", "9000070088", "2", "6 months", "1-3 years", "10+ years",
                                  "1990-11-13", "Diploma", "Welder", "Chennai", "TN", "synthetic@example.test"]], headers=headers)).status_code == 200
    response = env.client.get("/api/leads/export.xlsx")
    assert response.status_code == 200
    sheet = excel_rows(response.content)
    header = [cell[0] for cell in sheet[0]]
    cells = sheet[1]
    assert "\nAge: 35\n" in cells[0][0] and cells[header.index("Phone")][0] in cells[0][0]
    assert cells[0][1:] == ("inlineStr", 5), "the combined identity is wrapped inline text, never a formula"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"s": lead_xlsx.NS}
    row = xml.find("s:sheetData/s:row[@r='2']", ns)
    assert len(cells[0][0].splitlines()) == 4
    assert row.attrib["customHeight"] == "1" and float(row.attrib["ht"]) >= 80
    assert float(xml.find("s:cols/s:col[@min='1']", ns).attrib["width"]) == max(map(len, cells[0][0].splitlines())) + 3
    assert cells[header.index("Age")][0] == "35" and cells[header.index("Age")][1] == "n"
    assert cells[header.index("Phone")][1] == "inlineStr"
    assert cells[header.index("Job applied")][0] == "Welder" and cells[header.index("Education")][0] == "Diploma"
    assert cells[header.index("Location")][0] == "Chennai, Tamil Nadu"
    assert cells[header.index("Imported")][1:] == ("n", 3)
    assert float(cells[header.index("Experience (India)")][0]) == 2 and cells[header.index("Experience (India)")][1] == "n"
    for name, answer in (("Experience (Abroad)", "6 months"), ("Experience (total)", "1-3 years"), ("Experience (restaurant)", "10+ years")):
        assert cells[header.index(name)][0] == answer and cells[header.index(name)][1] == "inlineStr"


def test_export_age_uses_birthday_and_does_not_invent_unknown_age():
    today = date(2026, 11, 12)
    assert lead_sheet.export_age(date(1990, 11, 12), today) == 36
    assert lead_sheet.export_age(date(1990, 11, 13), today) == 35
    assert lead_sheet.export_age(None, today) is None
    assert lead_sheet.export_age(date(2027, 1, 1), today) is None


def test_excel_multiline_height_is_bounded_and_ordinary_rows_keep_default_height():
    rows = [["Contact"], ["Single line"], ["Name\nAge: 25\n+919000000001\nsynthetic@example.test"], ["line\n" * 100]]
    xml = ElementTree.fromstring(lead_xlsx.sheet_xml(rows, lead_xlsx.fit_widths(rows), True, True, True, False))
    body = xml.findall(f"{{{lead_xlsx.NS}}}sheetData/{{{lead_xlsx.NS}}}row")
    assert body[0].attrib["ht"] == "30"
    assert "ht" not in body[1].attrib and "customHeight" not in body[1].attrib
    assert body[2].attrib["ht"] == "80" and body[3].attrib["ht"] == "409"
    assert lead_xlsx.fit_widths(rows) == [len("synthetic@example.test") + 3]


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_export_import_preview_preserves_identity_geography_answers_and_source_date(env, file_format):
    india = "How many years of experience do you have in India?"
    abroad = "Do you have overseas experience?"
    total = "Do you have 5+ years of experience with a business qualification?"
    headers = ["Name", "Phone", india, abroad, total]
    source_rows = [["Synthetic Roundtrip A", "9000070061", "2 years", "Yes", "Yes"],
                   ["Synthetic Roundtrip B", "9000070062", "6 months", "No", "No"]]
    imported = upload(env, csv_bytes(source_rows, headers=headers))
    assert imported.status_code == 200
    exported = env.client.get(f"/api/leads/export.{file_format}", params={"batch_id": imported.json()["batchId"], "sort": "name", "direction": "asc"})
    assert exported.status_code == 200
    preview = upload(env, exported.content, name=f"roundtrip.{file_format}", dry_run=True)
    assert preview.status_code == 200, preview.text
    assert preview.json()["rowsDuplicate"] == 2 and preview.json()["rowsInvalid"] == 0
    mapping = {column["header"]: column["field"] for column in preview.json()["columns"]}
    assert mapping["Name"] == "name" and mapping["Phone"] == "phone"
    assert mapping["Name / Age / Contact"] is None and mapping["Age"] is None
    assert mapping["Created"] == "createdTime" and mapping["Imported"] is None
    assert all(mapping[header] is None for header in ("India experience question", "Abroad experience question", "Total experience question", "Restaurant experience question"))
    assert all(row["createdTime"] is None for row in preview.json()["sample"])
    assert [row["experienceTotalText"] for row in preview.json()["sample"]] == ["Yes", "No"]
    assert all("experienceRestaurantText" in row for row in preview.json()["sample"])
    table = lead_parsing.read_table(exported.content, f"roundtrip.{file_format}")
    columns = lead_parsing.map_headers(table.headers)
    for (_, cells), original in zip(table.rows, source_rows, strict=True):
        values = lead_parsing.normalise_row(table.headers, cells, columns)
        assert values["name"] == original[0] and values["phone"].endswith(original[1])
        assert values["created_time"] is None and values["raw"]["Imported"]
        assert values["experience_total_text"] == original[4]
        source = {"raw": values["raw"], "mapping": columns.as_map()}
        assert lead_sheet.geographic_experience(source, "india")["Text"] == original[2]
        assert lead_sheet.geographic_experience(source, "india")["Question"] == india
        assert lead_sheet.geographic_experience(source, "abroad")["Text"] == original[3]
        assert lead_sheet.geographic_experience(source, "abroad")["Question"] == abroad
        assert lead_sheet.experience_question(columns.as_map(), "experienceTotal", answer=original[4], raw=values["raw"]) == total
    assert env.db.scalar(select(func.count()).select_from(Lead)) == 2, "preview never writes or duplicates business rows"


def test_roundtrip_question_metadata_does_not_mask_conflicting_geographic_answers():
    source = {"raw": {"Experience (India)": "2 years", "India experience question": "India experience", "India experience": "3 years"}}
    assert lead_sheet.geographic_experience(source, "india") == {"Years": None, "Text": None, "Question": None}
    assert lead_parsing.map_headers(["Total experience question"]).as_map() == {"Total experience question": None}
