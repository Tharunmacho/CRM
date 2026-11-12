"""Optional job requirements round-trip without changing shared title rules."""
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text

from app.db.models import Designation, Order
from test_database_naming import _migrate, schema_database  # noqa: F401
from test_master_data import desk, post, setup_order, subject  # noqa: F401


FIELDS = {
    "ageRequirement": "21-35 years; experienced applicants considered",
    "genderRequirement": "Any gender",
    "educationRequirement": "Diploma or equivalent practical training\nTrade certificate preferred",
}


def test_requirements_create_edit_and_workspace_preserve_salary_dates_and_title_scope(desk):
    _, _, title, first, payload = setup_order(desk)
    title_values = {"title": title["title"], "description": "Read drawings and complete welding tasks.",
                    "requiredSkills": ["TIG", "MIG"], "minExperience": 3}
    assert desk.client.put("/api/designations/" + title["id"], json=title_values).status_code == 200
    created = post(desk, "orders", {**payload, **FIELDS})
    assert {key: created[key] for key in FIELDS} == FIELDS
    assert created["salary"] == 1200.5 and created["currency"] == "AED"
    assert created["interviewDate"] == "2026-11-02"

    response = desk.client.put("/api/orders/" + created["id"], json={**payload, **FIELDS, "ageRequirement": "25-40 years"})
    assert response.status_code == 200
    assert response.json()["ageRequirement"] == "25-40 years"
    history = desk.client.get("/api/master-data/order/" + created["id"] + "/audit").json()
    assert history[0]["meta"]["changes"]["age_requirement"] == {"from": FIELDS["ageRequirement"], "to": "25-40 years"}
    with desk.sessions() as db:
        saved = db.get(Order, created["id"])
        assert saved.education_requirement == FIELDS["educationRequirement"]
        assert saved.interview_date == date(2026, 11, 2) and float(saved.salary) == 1200.5
        # Job-specific edits never impose requirements on other jobs with that title.
        assert db.get(Order, first["id"]).age_requirement is None
        designation = db.get(Designation, title["id"])
        assert designation.description == title_values["description"]
        assert designation.required_skills == ["TIG", "MIG"] and designation.min_experience == 3
        snapshot_order = next(item for item in subject.read_snapshot(db, "staff")["orders"] if item["id"] == created["id"])
        listed = subject.OrderOut.model_validate(snapshot_order).model_dump(by_alias=True, mode="json")
        assert listed["ageRequirement"] == "25-40 years"
        assert listed["educationRequirement"] == FIELDS["educationRequirement"]
        assert listed["genderRequirement"] == FIELDS["genderRequirement"]
        assert listed["salary"] == 1200.5 and listed["interviewDate"] == "2026-11-02"


def test_older_put_preserves_requirements_but_explicit_blanks_and_null_clear(desk):
    _, _, _, _, payload = setup_order(desk)
    created = post(desk, "orders", {**payload, **FIELDS})
    path = "/api/orders/" + created["id"]
    old_client = desk.client.put(path, json={**payload, "salary": 1400})
    assert old_client.status_code == 200
    assert {key: old_client.json()[key] for key in FIELDS} == FIELDS
    assert old_client.json()["salary"] == 1400
    cleared = desk.client.put(path, json={**payload, "ageRequirement": "  ", "genderRequirement": None, "educationRequirement": "\n "})
    assert cleared.status_code == 200 and all(cleared.json()[key] is None for key in FIELDS)
    with desk.sessions() as db:
        saved = db.get(Order, created["id"])
        assert saved.age_requirement is None and saved.gender_requirement is None and saved.education_requirement is None


def test_all_new_requirements_are_optional_and_trimmed_without_invented_defaults():
    body = {"companyId": "company", "designationId": "title"}
    default = subject.OrderIn(**body)
    assert all(getattr(default, key) is None for key in FIELDS)
    supplied = subject.OrderIn(**body, ageRequirement="  21-35 years  ", genderRequirement=" Any ", educationRequirement=" Trade training ")
    assert supplied.ageRequirement == "21-35 years"
    assert supplied.genderRequirement == "Any" and supplied.educationRequirement == "Trade training"
    assert supplied.interviewDate is None


@pytest.mark.parametrize("field,limit", [("ageRequirement", 120), ("genderRequirement", 120), ("educationRequirement", 2000)])
def test_requirement_bounds_and_types_are_validated(field, limit):
    body = {"companyId": "company", "designationId": "title"}
    assert getattr(subject.OrderIn(**body, **{field: "x" * limit}), field) == "x" * limit
    for invalid in ("x" * (limit + 1), ["not text"], 42):
        with pytest.raises(ValidationError):
            subject.OrderIn(**body, **{field: invalid})


def test_migration_keeps_existing_jobs_and_repeated_upgrade_preserves_requirements(schema_database):
    _migrate(schema_database, "0036_reference_agency_place")
    with schema_database.begin() as connection:
        connection.execute(text("INSERT INTO crm_client_contacts (id,name) VALUES ('contact','Synthetic contact')"))
        connection.execute(text("INSERT INTO crm_employer_companies (id,client_id,name) VALUES ('company','contact','Synthetic employer')"))
        connection.execute(text("INSERT INTO crm_job_designations (id,title,description,required_skills,min_experience) "
                                "VALUES ('title','Welder','Shared responsibilities','[\"TIG\"]'::jsonb,3)"))
        connection.execute(text("INSERT INTO crm_job_orders (id,ref,company_id,designation_id,openings,salary,currency,interview_date) "
                                "VALUES ('job','ORDER-TEST','company','title',2,1200.50,'AED','2026-11-02')"))
        before = dict(connection.execute(text("SELECT * FROM crm_job_orders WHERE id='job'")).mappings().one())
    _migrate(schema_database, "head")
    with schema_database.begin() as connection:
        after = dict(connection.execute(text("SELECT * FROM crm_job_orders WHERE id='job'")).mappings().one())
        assert {key: after[key] for key in before} == before
        assert all(after[key] is None for key in ("age_requirement", "gender_requirement", "education_requirement"))
        connection.execute(text("UPDATE crm_job_orders SET age_requirement='21-35',gender_requirement='Any',education_requirement='Diploma' WHERE id='job'"))
        connection.execute(text("UPDATE alembic_version SET version_num='0036_reference_agency_place'"))
    _migrate(schema_database, "head")
    with schema_database.connect() as connection:
        assert connection.execute(text("SELECT age_requirement,gender_requirement,education_requirement FROM crm_job_orders WHERE id='job'")).one() == ("21-35", "Any", "Diploma")
        assert connection.scalar(text("SELECT count(*) FROM crm_job_orders")) == 1
    columns = {column["name"]: column for column in inspect(schema_database).get_columns("crm_job_orders")}
    for field, length in (("age_requirement", 120), ("gender_requirement", 120), ("education_requirement", 2000)):
        assert columns[field]["nullable"] and columns[field]["type"].length == length
    _migrate(schema_database, "0036_reference_agency_place", downgrade=True)
    assert "age_requirement" not in {column["name"] for column in inspect(schema_database).get_columns("crm_job_orders")}
    with schema_database.connect() as connection:
        assert connection.scalar(text("SELECT salary FROM crm_job_orders WHERE id='job'")) == before["salary"]
