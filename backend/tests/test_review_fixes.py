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

def test_an_md_exports_every_lead_like_an_admin_and_a_recruiter_cannot_export(env):
    imported(env, [lead_row(n) for n in range(1, 4)], assign_to="a")
    ids = ids_of(env)
    own_one(env, ids[0])
    act_as(env, "manager")
    everything = bulk(env, action="export", ids=ids)
    assert everything.status_code == 200 and everything.headers["X-Lead-Count"] == "3", "an MD's workbook holds every lead"
    assert bulk(env, action="export", ids=ids[1:]).headers["X-Lead-Count"] == "2"
    act_as(env, "a")
    assert bulk(env, action="export", ids=ids).status_code == 403


# ------------------------------------------------------------------ 2. the importer is not a way to assign

def test_a_recruiter_cannot_assign_through_the_importer_but_an_md_can(env):
    data = meta_export([lead_row(1), lead_row(2)])
    act_as(env, "b")
    for assign in ("a", "auto"):
        refused = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "false", "assign_to": assign})
        assert refused.status_code == 403, assign   # a recruiter cannot upload at all, let alone assign
    env.db.rollback()
    assert env.db.scalar(text("SELECT count(*) FROM crm_leads")) == 0
    act_as(env, "manager")   # an MD hands leads out exactly as an admin does
    done = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "false", "assign_to": "a"})
    assert done.status_code == 200 and done.json()["rowsNew"] == 2 and done.json()["assignedTo"] == "a"


def test_import_responses_tell_an_md_which_lead_and_batch_matched_like_an_admin(env):
    first = imported(env, [lead_row(1), lead_row(2)], assign_to="a")  # the admin's upload
    data = meta_export([lead_row(1), lead_row(3)])
    act_as(env, "manager")
    other = env.client.post("/api/leads/import", files={"file": ("leads.csv", data, "text/csv")}, data={"dry_run": "true"}).json()
    assert other["rowsDuplicate"] == 1 and other["duplicates"][0]["existingLeadId"]
    same = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export([lead_row(1), lead_row(2)]), "text/csv")}, data={"dry_run": "true"}).json()
    assert same["sameFileBefore"]["batchId"] == first["batchId"]
    act_as(env, "admin")
    admin_same = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export([lead_row(1), lead_row(2)]), "text/csv")}, data={"dry_run": "true"}).json()
    assert admin_same["sameFileBefore"]["batchId"] == first["batchId"]


# ------------------------------------------------------------------ 3. a lead that is not yours does not exist for you

def test_a_lead_that_is_not_yours_is_not_found_and_names_nobody(env):
    imported(env, [lead_row(1), lead_row(2)], assign_to="b")
    ids = ids_of(env)
    env.db.execute(text("UPDATE crm_leads SET owner_id = NULL WHERE id = :i"), {"i": ids[1]})
    env.db.commit()
    for who in ("a",):   # an MD works and sees every lead, like an admin
        act_as(env, who)
        for lead_id in ids:
            for response in (env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}),
                             env.client.patch(f"/api/leads/{lead_id}", json={"notes": "x"}),
                             env.client.get(f"/api/leads/{lead_id}")):
                assert response.status_code == 404 and response.json()["detail"]["code"] == "not_found", (who, response.text)
                assert "Ravi" not in response.text and "no owner" not in response.text


# ------------------------------------------------------------------ candidates follow the lead rule: only the admin hands them out

def test_an_md_converting_a_lead_picks_the_recruiter_like_an_admin(env):
    imported(env, [lead_row(1)], assign_to="a")
    lead_id = ids_of(env)[0]
    own_one(env, lead_id)
    act_as(env, "manager")
    done = convert_with_resume(env, lead_id, overrides={"assignTo": "b"})
    assert done.status_code == 200, done.text
    assert done.json()["candidate"]["ownerId"] == "b"


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
