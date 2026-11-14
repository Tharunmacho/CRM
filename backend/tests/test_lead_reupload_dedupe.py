"""Re-uploading a lead sheet that still holds the OLD leads adds only the NEW people.

Pure parsing checks need no database; the import checks run on the disposable PostgreSQL (TEST_DATABASE_URL) like
tests/test_lead_sheet.py. Every person is synthetic.
"""
import threading

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import models as m
from app.services import lead_import, lead_parsing as lp
from lead_sheet_fixtures import csv_bytes, lead_row, meta_rows, xlsx_bytes
from test_database_naming import schema_database  # noqa: F401  (fixture used by env)
from test_lead_sheet import act_as, count, env, imported, upload  # noqa: F401  (env is the shared fixture)


def csv_import(env, rows, **fields):
    response = upload(env, csv_bytes(rows), name="more-leads.csv", **fields)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ 1. phone spellings

SAME_PERSON = ["+91 90000 00123", "919000000123", "9000000123", "090000-00123", "p:+919000000123", "(+91) 90000-00123",
               "+91-(90000)00123", "0091 90000 00123", "0919000000123", "tel:+919000000123", " 90000  00123 ", "91 090000 00123"]
EXCEL_SPELLINGS = ["9000000123.0", "9000000123.00", "9.000000123E9", "9.000000123e+09", "9.000000123E+09", "+919000000123.0",
                   "919000000123.0", "9.19000000123E11", "p:9000000123.0"]


@pytest.mark.parametrize("written", SAME_PERSON + EXCEL_SPELLINGS)
def test_every_spelling_of_one_indian_number_gets_the_same_key(written):
    parsed = lp.parse_phone(written)
    assert parsed["valid"] and parsed["key"] == "9000000123", written


def test_different_numbers_keep_different_keys_and_junk_has_no_key():
    keys = {lp.parse_phone(v)["key"] for v in ["9000000123", "9000000124", "8000000123", "+971 50 123 4567", "+44 7911 123456"]}
    assert len(keys) == 5
    for junk in ["", "p:", "12", "99940", "9.99", "0000000000", "00000000000", "9999999999", "+91 99999 99999",
                 "123456789012345678", "n/a", "abc"]:
        parsed = lp.parse_phone(junk)
        assert parsed["key"] is None and not parsed["valid"], junk


def test_dots_between_digit_groups_are_separators_not_decimals():
    assert not lp.parse_phone("9.99")["valid"]
    assert lp.parse_phone("90000.00123")["key"] == "9000000123"
    assert lp.parse_phone("+91.90000.00123")["key"] == "9000000123"


def test_native_digits_match_ascii_digits():
    assert lp.parse_phone("٩٠٠٠٠٠٠١٢٣")["key"] == "9000000123"


# ------------------------------------------------------------------ 2. e-mail, 3. names, rounded ids

def test_email_compare_is_exact_lowercase_and_placeholders_never_match():
    assert lp.parse_email("  Asha.K@Example.Test ") == "asha.k@example.test"
    assert lp.parse_email("<mailto:Asha@Example.test>") == "asha@example.test"
    assert lp.parse_email("a.sha+jobs@gmail.com") == "a.sha+jobs@gmail.com"      # Gmail dots / plus tags are different addresses
    assert lp.parse_email("asha@gmail.com") != lp.parse_email("a.sha@gmail.com")
    for placeholder in ["", "  ", "na", "NA", "N/A", "none", "None", "null", "-", "na@gmail.com", "NONE@none.com", "n.a@x.test", "no_email@x.test", "nil@x.test"]:
        assert lp.parse_email(placeholder) is None, placeholder
    assert lp.parse_email("na123@x.test") == "na123@x.test"                       # only the whole filler is dropped


def test_rounded_excel_ids_are_not_ids():
    assert lp.parse_id("l:1795000000000001") == "1795000000000001"
    assert lp.parse_id("1795000000000001.0") == "1795000000000001"
    assert lp.parse_id("1.795E+15") is None


def test_the_same_phone_in_every_spelling_is_one_person_across_files(env):
    first = csv_import(env, [lead_row(1, phone_number="+91 90000 00123", email="")])
    assert first["rowsNew"] == 1
    for spelling in ["919000000123", "090000-00123", "p:+919000000123", "9000000123.0", "9.000000123E9", "(+91) 90000-00123"]:
        again = csv_import(env, [lead_row(2, id="", phone_number=spelling, email="", full_name="Other Spelling")])
        assert (again["rowsNew"], again["rowsDuplicate"]) == (0, 1), spelling
        assert again["duplicates"][0]["reason"] == "phone"
    assert count(env) == 1


def test_an_excel_workbook_with_float_and_scientific_numbers_matches_stored_leads(env):
    csv_import(env, [lead_row(1, phone_number="9000000123", email=""), lead_row(2, phone_number="9000000124", email="")])
    sheet = [["full_name", "phone_number"], ["Float Person", 9000000123.0], ["Scientific Person", "9.000000124E9"], ["Brand New", 9000000199]]
    result = upload(env, xlsx_bytes(sheet), name="leads.xlsx").json()
    assert (result["rowsNew"], result["rowsDuplicate"]) == (1, 2)
    assert count(env) == 3


def test_junk_phone_numbers_and_placeholder_emails_never_merge_different_people(env):
    rows = [lead_row(1, phone_number="0000000000", email="one@example.test", id=""),
            lead_row(2, phone_number="9999999999", email="two@example.test", id=""),
            lead_row(3, phone_number="+91 77777 77777", email="three@example.test", id=""),
            lead_row(4, phone_number="9000000004", email="NA", id=""),
            lead_row(5, phone_number="9000000005", email="none@gmail.com", id=""),
            lead_row(6, phone_number="9000000006", email=" na ", id="")]
    result = csv_import(env, rows)
    assert (result["rowsNew"], result["rowsDuplicate"], result["rowsInvalid"]) == (6, 0, 0)
    again = csv_import(env, rows)                                                  # the same file again adds nobody
    assert (again["rowsNew"], again["rowsDuplicate"]) == (0, 6)
    assert {d["reason"] for d in again["duplicates"]} == {"email", "phone"}
    act_as(env, "admin")
    assert env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.phone_key.is_(None))) == 3
    assert env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.email_key.is_(None))) == 3


def test_a_row_with_only_junk_contacts_is_invalid_not_a_match(env):
    rows = [lead_row(1, phone_number="0000000000", email="NA", id=""), lead_row(2, phone_number="", email="none", id="")]
    result = csv_import(env, rows)
    assert (result["rowsNew"], result["rowsInvalid"]) == (0, 2) and count(env) == 0


def test_names_alone_are_never_a_duplicate_key(env):
    csv_import(env, [lead_row(1, full_name="Same Name")])
    result = csv_import(env, [lead_row(2, full_name="Same Name"), lead_row(3, full_name="SAME NAME"), lead_row(4, full_name="same name")])
    assert (result["rowsNew"], result["rowsDuplicate"]) == (3, 0)
    # a row with a name and nothing else cannot be stored at all
    nameless = csv_import(env, [lead_row(5, full_name="Same Name", phone_number="", email="", id="")])
    assert (nameless["rowsNew"], nameless["rowsInvalid"]) == (0, 1)
    assert count(env) == 4


def test_email_matches_ignoring_case_and_spaces_but_not_gmail_aliases(env):
    csv_import(env, [lead_row(1, email="Asha.K@Gmail.com", phone_number="9000000001", id="")])
    rows = [lead_row(2, email="  asha.k@gmail.COM ", phone_number="9000000002", id=""),     # same address
            lead_row(3, email="ashak@gmail.com", phone_number="9000000003", id=""),          # dots differ: another person
            lead_row(4, email="asha.k+jobs@gmail.com", phone_number="9000000004", id="")]    # plus tag differs: another person
    result = csv_import(env, rows)
    assert (result["rowsNew"], result["rowsDuplicate"]) == (2, 1)
    assert [(d["row"], d["reason"]) for d in result["duplicates"]] == [(2, "email")]


def test_rounded_excel_ids_do_not_merge_different_people(env):
    rows = [lead_row(1, id="1.795E+15"), lead_row(2, id="1.795E+15"), lead_row(3, id="1.795E+15")]
    result = csv_import(env, rows)
    assert (result["rowsNew"], result["rowsDuplicate"]) == (3, 0)
    assert env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.external_id.is_not(None))) == 0


# ------------------------------------------------------------------ 4. old leads are never touched

def snapshot(lead):
    return (lead.status, lead.owner_id, lead.notes, lead.call_count, lead.candidate_id, lead.name, lead.phone, lead.city, lead.batch_id)


def test_reuploading_old_leads_never_resets_worked_leads_and_adds_only_new_people(env):
    imported(env, meta_rows(0, 6))
    env.db.add(m.Candidate(id="cand-conv", ref="CND-CONV1", name="Converted Person", phone="9000000001", source=m.Source.other, owner_id="a"))
    env.db.flush()
    by_phone = {l.phone_key: l for l in env.db.scalars(select(m.Lead))}
    by_phone["9000000001"].status, by_phone["9000000001"].candidate_id, by_phone["9000000001"].notes = "converted", "cand-conv", "Joined the Data Bank"
    by_phone["9000000002"].status, by_phone["9000000002"].notes = "not_interested", "Said no twice"
    by_phone["9000000003"].status, by_phone["9000000003"].owner_id, by_phone["9000000003"].call_count = "interested", "a", 3
    by_phone["9000000004"].status = "wrong_number"
    env.db.commit()
    before = {key: snapshot(lead) for key, lead in by_phone.items()}
    prefixes = ["", "p:+91", "0", "91 ", "+91 ", "p:"]
    # the new export: every old person (typed differently, with another name and city, no lead id) plus two new people
    rows = [lead_row(n, id="", full_name=f"Renamed {n}", city="Elsewhere", phone_number=prefixes[n] + str(9000000000 + n)) for n in range(6)]
    rows += [lead_row(100), lead_row(101)]
    for fill_empty in ("false", "true"):
        result = csv_import(env, rows, fill_empty=fill_empty)
        assert result["rowsTotal"] == 8 and result["rowsNew"] == (2 if fill_empty == "false" else 0) and result["rowsDuplicate"] == (6 if fill_empty == "false" else 8)
    env.db.rollback()
    env.db.expire_all()
    after = {l.phone_key: l for l in env.db.scalars(select(m.Lead)) if l.phone_key in before}
    assert {key: snapshot(lead) for key, lead in after.items()} == before            # status, owner, notes, calls, link, name, city: all untouched
    assert count(env) == 8                                                         # 6 old + 2 new, never more


def test_a_lead_whose_batch_was_reverted_is_added_again_but_a_worked_one_stays_a_duplicate(env):
    first = imported(env, meta_rows(0, 4))
    worked = env.db.scalars(select(m.Lead).where(m.Lead.external_id.like("%1"))).one()
    worked.status, worked.owner_id = "contacted", "a"
    env.db.commit()
    reverted = env.client.delete(f"/api/leads/batches/{first['batchId']}")
    assert reverted.status_code == 200 and reverted.json() == {"deleted": 3, "kept": 1}
    again = imported(env, meta_rows(0, 4))
    assert (again["rowsNew"], again["rowsDuplicate"]) == (3, 1)                   # the untouched three are gone, so they are new again
    assert again["duplicates"][0]["reason"] == "lead_id" and count(env) == 4


# ------------------------------------------------------------------ 5. the report says what happened

def test_the_result_and_the_preview_count_duplicates_by_id_phone_and_email(env):
    imported(env, [lead_row(1), lead_row(2), lead_row(3)])
    rows = [lead_row(1),                                                              # same id
            lead_row(12, id="", phone_number="+91 90000 00002"),                      # same phone
            lead_row(13, id="", phone_number="9111111113", email="LEAD3@example.test"),  # same e-mail
            lead_row(14, id="", phone_number="9111111114", email="new14@example.test"),   # new
            lead_row(15, id="", phone_number="", email=""),                           # invalid
            lead_row(16, id="", phone_number="9111111114", email="again@example.test")]  # in-file phone duplicate
    expected = {"lead_id": 1, "phone": 2, "email": 1}
    preview = upload(env, csv_bytes(rows), name="x.csv", dry_run=True).json()
    assert (preview["rowsNew"], preview["rowsDuplicate"], preview["rowsInvalid"]) == (1, 4, 1)
    assert preview["duplicatesByReason"] == expected
    done = csv_import(env, rows)
    assert (done["rowsNew"], done["rowsDuplicate"], done["rowsInvalid"]) == (1, 4, 1)
    assert done["duplicatesByReason"] == expected
    assert sum(done["duplicatesByReason"].values()) == done["rowsDuplicate"]
    batch = env.client.get(f"/api/leads/batches/{done['batchId']}").json()
    assert (batch["rowsNew"], batch["rowsDuplicate"], batch["rowsInvalid"]) == (1, 4, 1)


def test_uploading_a_file_of_only_old_leads_adds_nothing_and_says_so(env):
    imported(env, meta_rows(0, 30))
    csv_rows = [lead_row(n, id="") for n in range(30)]
    result = csv_import(env, csv_rows)                                                 # a different file (no ids), the same people
    assert (result["rowsNew"], result["rowsDuplicate"], result["rowsInvalid"]) == (0, 30, 0)
    assert result["duplicatesByReason"] == {"lead_id": 0, "phone": 30, "email": 0}
    assert count(env) == 30


# ------------------------------------------------------------------ 6. two uploads at once

def test_two_overlapping_uploads_at_the_same_time_never_store_a_person_twice(env):
    """Different files, different (or no) lead ids, same people: only the advisory lock stands between them and duplicates."""
    file_a = csv_bytes([lead_row(n) for n in range(0, 120)])
    file_b = csv_bytes([lead_row(n, id=f"l:{2000000000000000 + n}", phone_number=f"+91 {9000000000 + n}") for n in range(60, 180)])
    outcomes, errors = [], []
    barrier = threading.Barrier(2)

    def run(data, name):
        try:
            with Session(env.engine) as db:
                user = db.get(m.User, "admin")
                barrier.wait(timeout=10)
                outcomes.append(lead_import.run_import(db, user, data, name))
                db.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(file_a, "a.csv")), threading.Thread(target=run, args=(file_b, "b.csv"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors, errors
    assert sorted(o["rowsNew"] + o["rowsDuplicate"] for o in outcomes) == [120, 120]
    assert sum(o["rowsNew"] for o in outcomes) == 180
    env.db.rollback()
    assert env.db.scalar(text("SELECT count(*) FROM crm_leads")) == 180
    assert env.db.scalar(text("SELECT count(DISTINCT phone_key) FROM crm_leads")) == 180
    assert env.db.scalar(text("SELECT count(DISTINCT email_key) FROM crm_leads")) == 180
