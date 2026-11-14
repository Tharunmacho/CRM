"""Fixes from the review of the lead-sheet / candidate-ownership changes: who sees which lead, importer assignment, phone parsing.

Synthetic people only. Needs TEST_DATABASE_URL like the other DB tests.
"""
import pytest
from sqlalchemy import select, text

from app.db import models as m
from app.services import lead_parsing as lp
from lead_resume_fixtures import convert_with_resume
from lead_sheet_fixtures import lead_row, meta_export
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_lead_bulk import bulk, env, imported, writes  # noqa: F401  (fixture)
from test_lead_sheet import act_as, first_lead


def own_one(env, lead_id, owner="manager"):
    env.db.execute(text("UPDATE crm_leads SET owner_id = :o WHERE id = :i"), {"o": owner, "i": lead_id})
    env.db.commit()


def ids_of(env):
    env.db.rollback()
    return [row[0] for row in env.db.execute(text("SELECT id FROM crm_leads ORDER BY import_row"))]


# ------------------------------------------------------------------ 1. export by ids

def test_a_manager_exports_only_their_own_leads_even_when_the_ids_name_others(env):
    imported(env, [lead_row(n) for n in range(1, 4)], assign_to="a")
    ids = ids_of(env)
    own_one(env, ids[0])
    act_as(env, "manager")
    mixed = bulk(env, action="export", ids=ids)
    assert mixed.status_code == 200 and mixed.headers["X-Lead-Count"] == "1", "the other two leads never reach a manager's workbook"
    others = bulk(env, action="export", ids=ids[1:])
    assert others.status_code == 422, "nothing of theirs matched: the same answer as an id that does not exist"
    assert bulk(env, action="export", ids=ids[1:] + ["no-such-lead"]).status_code == 422
    act_as(env, "admin")
    assert bulk(env, action="export", ids=ids).headers["X-Lead-Count"] == "3"


# ------------------------------------------------------------------ 2. the importer is not a way to assign

def test_a_manager_cannot_assign_through_the_importer_but_may_import(env):
    data = meta_export([lead_row(1), lead_row(2)])
    act_as(env, "manager")
    for assign in ("a", "auto"):
        refused = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "false", "assign_to": assign})
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "forbidden_role", assign
    env.db.rollback()
    assert env.db.scalar(text("SELECT count(*) FROM crm_leads")) == 0
    done = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "false"})
    assert done.status_code == 200 and done.json()["rowsNew"] == 2


def test_import_responses_do_not_tell_a_manager_which_lead_or_batch_matched(env):
    first = imported(env, [lead_row(1), lead_row(2)], assign_to="a")  # the admin's upload
    data = meta_export([lead_row(1), lead_row(3)])
    act_as(env, "manager")
    other = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "true"}).json()
    assert other["rowsDuplicate"] == 1 and [d["scope"] for d in other["duplicates"]] == ["database"]
    assert other["duplicates"][0]["existingLeadId"] is None
    same = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export([lead_row(1), lead_row(2)]), "text/csv")}, data={"dry_run": "true"}).json()
    assert same["sameFileBefore"] == {"batchId": None, "uploadedAt": None, "rowsNew": None}, "a match is reported, not whose or when"
    assert first["batchId"] not in str(same)
    act_as(env, "admin")
    mine = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "true"}).json()
    assert mine["duplicates"][0]["existingLeadId"]
    admin_same = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export([lead_row(1), lead_row(2)]), "text/csv")}, data={"dry_run": "true"}).json()
    assert admin_same["sameFileBefore"]["batchId"] == first["batchId"]


# ------------------------------------------------------------------ 3. a lead that is not yours does not exist for you

def test_a_lead_that_is_not_yours_is_not_found_and_names_nobody(env):
    imported(env, [lead_row(1), lead_row(2)], assign_to="b")
    ids = ids_of(env)
    env.db.execute(text("UPDATE crm_leads SET owner_id = NULL WHERE id = :i"), {"i": ids[1]})
    env.db.commit()
    for who in ("a", "manager"):
        act_as(env, who)
        for lead_id in ids:
            for response in (env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}),
                             env.client.patch(f"/api/leads/{lead_id}", json={"notes": "x"}),
                             env.client.get(f"/api/leads/{lead_id}")):
                assert response.status_code == 404 and response.json()["detail"]["code"] == "not_found", (who, response.text)
                assert "Ravi" not in response.text and "no owner" not in response.text


# ------------------------------------------------------------------ candidates follow the lead rule: only the admin hands them out

def test_a_manager_converting_a_lead_cannot_pick_the_recruiter(env):
    imported(env, [lead_row(1)], assign_to="a")
    lead_id = ids_of(env)[0]
    own_one(env, lead_id)
    act_as(env, "manager")
    done = convert_with_resume(env, lead_id, overrides={"assignTo": "b"})
    assert done.status_code == 200, done.text
    assert done.json()["candidate"]["ownerId"] == "manager"


# ------------------------------------------------------------------ 4, 5, 6. phones

@pytest.mark.parametrize("text_value", ["9.99E+09", "9.9E9", "9990000000.0", "9.99401E9", "8.0E9", "1E10", "9876500000.0"])
def test_a_phone_rounded_by_a_spreadsheet_is_not_a_phone(text_value):
    parsed = lp.parse_phone(text_value)
    assert parsed["valid"] is False and parsed["key"] is None and parsed["phone"] is None and parsed["given"] is True


@pytest.mark.parametrize("text_value", ["9000000123.0", "9.000000123E9", "9.000000123e+09", "+919000000123.0", "9000000123"])
def test_a_whole_number_written_the_spreadsheet_way_is_still_a_phone(text_value):
    parsed = lp.parse_phone(text_value)
    assert parsed["valid"] is True and parsed["key"] == "9000000123"


@pytest.mark.parametrize("text_value", ["0000000000", "9999999999", "+91 99999 99999", "919999999999", "1111111", "00 0000 0000"])
def test_a_placeholder_made_of_one_repeated_digit_is_rejected(text_value):
    assert lp.parse_phone(text_value)["valid"] is False


@pytest.mark.parametrize("text_value,key", [("+1 555 555 5555", "5555555555"), ("+44 7700 900000", "7700900000"), ("+91 99999 99998", "9999999998")])
def test_a_genuine_number_with_a_repeated_tail_is_kept(text_value, key):
    parsed = lp.parse_phone(text_value)
    assert parsed["valid"] is True and parsed["key"] == key


HEADERS = ["full_name", "phone_number", "whatsapp_number", "email"]


def normalised(*cells):
    columns = lp.map_headers(HEADERS, None)
    return lp.normalise_row(HEADERS, list(cells), columns)


def test_a_placeholder_phone_next_to_a_real_whatsapp_number_keeps_the_lead():
    row = normalised("Test Person", "0000000000", "+91 90000 00123", "")
    assert row["_invalid"] is None
    assert row["phone"] == "+919000000123" and row["phone_key"] == "9000000123" and row["whatsapp"] == "+919000000123"
    only_bad = normalised("Test Person", "0000000000", "", "")
    assert only_bad["_invalid"]["code"] == "bad_phone"
    both_bad = normalised("Test Person", "0000000000", "9999999999", "")
    assert both_bad["_invalid"]["code"] == "bad_phone"
    with_email = normalised("Test Person", "0000000000", "9000000123", "p@example.test")
    assert with_email["_invalid"] is None and with_email["phone_key"] == "9000000123"


# ------------------------------------------------------------------ 8. a double click does not write the history twice

def test_lead_assign_takes_row_locks_like_the_bulk_path(env):
    imported(env, [lead_row(1), lead_row(2)], assign_to="a")
    ids = ids_of(env)
    for body in ({"ids": ids, "ownerId": "none"}, {"filter": {}, "ownerId": "none"}):
        env.db.execute(text("UPDATE crm_leads SET owner_id = 'a'")); env.db.commit()
        response, statements = writes(env, lambda: env.client.post("/api/leads/assign", json=body))
        assert response.status_code == 200 and response.json()["unassigned"] == 2, response.text
        assert any("FOR UPDATE" in s.upper() and "crm_leads" in s for s in statements)
        again = env.client.post("/api/leads/assign", json=body).json()
        assert again["unassigned"] == 0 and again["skipped"] == 2
    env.db.rollback()
    notes = env.db.scalars(select(m.LeadActivity.summary).where(m.LeadActivity.kind == "assign", m.LeadActivity.summary.like("Unassigned%"))).all()
    assert len(notes) == 4, "one entry per real unassignment (2 leads x 2 rounds), none for the repeated clicks"
