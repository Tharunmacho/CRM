"""Explicit geographic source answers are projected without invented experience."""
import json
from datetime import datetime

import pytest
from sqlalchemy import event

from app.db.models import Lead
from app.services import lead_parsing as lp
from lead_sheet_fixtures import csv_bytes, xlsx_bytes
from test_database_naming import schema_database  # noqa: F401
from test_lead_sheet import env, upload  # noqa: F401


@pytest.mark.parametrize("header,scope", [
    ("India Experience", "india"), ("Indian work experience", "india"), ("experienceIndia", "india"),
    ("How many years of experience do you have in India?", "india"),
    ("Working abroad", "abroad"), ("Have you worked in India?", "india"),
    ("Overseas experience", "abroad"), ("Gulf Exp", "abroad"), ("experienceAbroad", "abroad"),
    ("Foreign experience", "abroad"), ("Do you have experience in foreign countries?", "abroad"),
    ("Years of experience outside India", "abroad"), ("Indian or overseas experience", "mixed"),
    ("Experience in India and outside India", "mixed"),
    ("Experience in India and abroad", "mixed"), ("Local experience", "unspecified"),
    ("Domestic helper experience", "unspecified"), ("Domestic experience", "unspecified"),
    ("Restaurant experience", None), ("South Indian cooking experience", "unspecified"),
    ("Indian restaurant experience", "unspecified"), ("Foreign language experience", "unspecified"),
    ("Do you have 5+ years of experience with a relevant qualification?", None),
    ("Country of interest: India", None), ("Date of birth", None),
    ("Do you have experience handling Indian customers?", "unspecified"),
    ("Are you an Indian citizen with work experience?", "unspecified"),
    ("Do you have experience handling overseas customers?", "unspecified"),
    ("Do you have Gulf client service experience?", "unspecified"),
    ("Experience handling overseas vendors", "unspecified"),
    ("Experience working with foreign suppliers", "unspecified"),
    ("Indian nationality and three years of experience?", "unspecified"),
    ("How long have you lived in India and what is your experience?", "unspecified"),
    ("Experience with Indian software?", "unspecified"),
    ("Experience in Indian companies?", "unspecified"),
])
def test_only_explicit_geographic_experience_headers_have_a_scope(header, scope):
    assert lp.geographic_experience_scope(header) == scope


@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_india_abroad_and_total_answers_roundtrip_independently(env, file_format):
    india, abroad = "Years_of_experience_in_India?", "Do_you_have_overseas_experience?"
    headers = ["Name", "Phone", india, abroad, "Total experience", "Restaurant experience", "Date of birth"]
    answers = [("2 years", "6 months", "4 years", 2, 0.5, 4),
               ("Yes", "No", "Yes", None, 0, None),
               ("", "", "", None, None, None),
               ("N/A", "Unknown", "Not supplied", None, None, None)]
    rows = [[f"Synthetic Geographic {index}", f"90000900{index:02d}", india_answer, abroad_answer, total, "1 year", "1990-02-01"]
            for index, (india_answer, abroad_answer, total, *_years) in enumerate(answers)]
    content = csv_bytes(rows, headers=headers) if file_format == "csv" else xlsx_bytes([headers, *rows])
    imported = upload(env, content, name=f"geography.{file_format}")
    assert imported.status_code == 200, imported.text
    mapped = {column["header"]: column["field"] for column in imported.json()["columns"]}
    assert mapped[india] is None and mapped[abroad] is None
    assert mapped["Total experience"] == "experienceTotal"
    items = env.client.get("/api/leads", params={"sort": "name", "direction": "asc"}).json()["items"]
    for item, (india_text, abroad_text, total_text, india_years, abroad_years, total_years) in zip(items, answers, strict=True):
        assert "raw" not in item and "extra" not in item
        assert item["experienceIndiaText"] == (india_text or None)
        assert item["experienceIndiaYears"] == india_years
        assert item["experienceIndiaQuestion"] == "Years of experience in India?"
        assert item["experienceAbroadText"] == (abroad_text or None)
        assert item["experienceAbroadYears"] == abroad_years
        assert item["experienceAbroadQuestion"] == "Do you have overseas experience?"
        assert item["experienceTotalText"] == (total_text or None)
        assert item["experienceTotalYears"] == total_years
        assert item["experienceTotalQuestion"] == "Total experience"
        assert item["experienceRestaurantYears"] == 1 and item["dob"] == "1990-02-01"
        detail = env.client.get("/api/leads/" + item["id"]).json()
        for region in ("India", "Abroad", "Total"):
            for suffix in ("Years", "Text", "Question"):
                assert detail[f"experience{region}{suffix}"] == item[f"experience{region}{suffix}"]
        if india_text:
            assert detail["extra"][india] == india_text and detail["raw"][india] == india_text


def test_source_without_geography_never_becomes_india_or_abroad(env):
    restaurant = "How many years of experience do you have in the restaurant industry?"
    total = "Do you have 5+ years of experience in a relevant company with a business qualification?"
    assert upload(env, csv_bytes([["Synthetic Unspecified", "9000090080", "10 years", "Yes"]],
                                 headers=["Name", "Phone", restaurant, total])).status_code == 200
    item = env.client.get("/api/leads").json()["items"][0]
    assert all(item[f"experience{region}{suffix}"] is None for region in ("India", "Abroad") for suffix in ("Years", "Text", "Question"))
    assert item["experienceRestaurantYears"] == 10
    assert item["experienceTotalYears"] is None and item["experienceTotalText"] == "Yes"
    assert item["experienceTotalQuestion"] == total


def test_ambiguous_multiple_or_combined_questions_are_unknown_without_summing(env):
    headers = ["Name", "Phone", "India experience", "India Exp", "Overseas experience", "Gulf experience", "Total experience"]
    response = upload(env, csv_bytes([["Synthetic Ambiguous", "9000090081", "2", "3", "4", "5", "12"]], headers=headers))
    assert response.status_code == 200
    combined = upload(env, csv_bytes([["Synthetic Combined", "9000090082", "7 years"]],
                                    headers=["Name", "Phone", "Experience in India and abroad"]))
    assert combined.status_code == 200
    for item in env.client.get("/api/leads").json()["items"]:
        assert all(item[f"experience{region}{suffix}"] is None for region in ("India", "Abroad") for suffix in ("Years", "Text", "Question"))
        if item["name"] == "Synthetic Ambiguous":
            assert item["experienceTotalYears"] == 12
        else:
            assert item["experienceTotalYears"] is None


def test_domestic_local_and_geographic_questions_cannot_steal_total_mapping():
    headers = ["Domestic helper experience", "Local experience", "India experience", "Foreign experience", "Total experience"]
    mapped = lp.map_headers(headers).as_map()
    assert mapped == {**{header: None for header in headers[:-1]}, "Total experience": "experienceTotal"}
    assert lp.map_headers(["India experience"]).as_map() == {"India experience": None}
    assert lp.map_headers(["India experience"], {"India experience": "experienceTotal"}).as_map() == {"India experience": "experienceTotal"}


def test_customer_and_nationality_answers_never_become_work_location_experience(env):
    questions = ["Do you have experience handling Indian customers?", "Are you an Indian citizen with work experience?",
                 "Do you have experience handling overseas customers?", "Experience with Indian software?",
                 "Experience in Indian companies?"]
    answers = ["Yes", "Yes", "4 years", "3 years", "No"]
    headers = ["Name", "Phone", *questions, "Total experience"]
    response = upload(env, csv_bytes([["Synthetic Geography Context", "9000090084", *answers, "6 years"]], headers=headers))
    assert response.status_code == 200
    assert set(response.json()["unmappedColumns"]) == set(questions)
    item = env.client.get("/api/leads").json()["items"][0]
    assert all(item[f"experience{region}{suffix}"] is None for region in ("India", "Abroad") for suffix in ("Years", "Text", "Question"))
    assert item["experienceTotalYears"] == 6 and item["experienceTotalQuestion"] == "Total experience"
    detail = env.client.get("/api/leads/" + item["id"]).json()
    assert detail["extra"] == dict(zip(questions, answers, strict=True))


def test_explicit_work_location_without_experience_word_is_projected(env):
    response = upload(env, csv_bytes([["Synthetic Work Locations", "9000090085", "Yes", "3 years"]],
                                    headers=["Name", "Phone", "Working abroad", "Have you worked in India?"]))
    assert response.status_code == 200
    item = env.client.get("/api/leads").json()["items"][0]
    assert item["experienceAbroadText"] == "Yes" and item["experienceAbroadQuestion"] == "Working abroad"
    assert item["experienceIndiaYears"] == 3 and item["experienceIndiaQuestion"] == "Have you worked in India?"
    assert item["experienceTotalYears"] is None


def test_legacy_raw_and_extra_are_read_without_loading_full_rows_or_writing(env):
    leads = [Lead(name=f"Synthetic Legacy Geo {index}", phone=f"900008{index:04d}",
                  raw={"India experience": "2 years", "Private unrelated source": "not projected"},
                  extra={"India experience": "3 years", "Foreign experience": "Yes"}) for index in range(40)]
    env.db.add_all(leads)
    env.db.commit()
    before = {row.id: row.updated_at for row in leads}
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(env.engine, "before_cursor_execute", record)
    try:
        result = env.client.get("/api/leads", params={"page_size": 100})
    finally:
        event.remove(env.engine, "before_cursor_execute", record)
    assert result.status_code == 200
    assert len(statements) <= 3 and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    for item in result.json()["items"]:
        assert "raw" not in item and "extra" not in item
        assert item["experienceIndiaYears"] == 2 and item["experienceAbroadText"] == "Yes"
        assert item["experienceTotalYears"] is None  # never sum or copy a geographic answer into Total
        assert datetime.fromisoformat(item["updatedAt"]) == before[item["id"]]


def test_explicit_total_mapping_is_preserved_with_original_question(env):
    header = "India experience"
    imported = upload(env, csv_bytes([["Synthetic Explicit Map", "9000090083", "5 years"]], headers=["Name", "Phone", header]),
                      mapping=json.dumps({header: "experienceTotal"}))
    assert imported.status_code == 200
    item = env.client.get("/api/leads").json()["items"][0]
    assert item["experienceTotalYears"] == 5 and item["experienceTotalQuestion"] == header
    assert item["experienceIndiaYears"] == 5 and item["experienceAbroadYears"] is None
