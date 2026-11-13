"""Lead sheet on a disposable PostgreSQL: import (dedupe, continue-where-it-stopped, set-based), list/search, workflow,
assignment, conversion, batches, export, the daily-work gate, live topics and the 0024 migration.

All people are synthetic (tests/lead_sheet_fixtures.py). Needs TEST_DATABASE_URL like the other DB tests.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import csv
import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routers import candidates, files, leads
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import change_feed, lead_import
from app.services.daily_work import agency_today
from lead_sheet_fixtures import META_HEADERS, csv_bytes, lead_row, meta_export, meta_rows, xlsx_bytes
from test_database_naming import _migrate, schema_database

PDF = b"%PDF-1.7 synthetic resume"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha", email="a@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="b", name="Ravi", email="b@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="away", name="Away", email="away@example.test", role=m.Role.staff, receives_auto_assign=True, on_leave=True)])
        db.commit()
        app = FastAPI()
        for router in (leads.router, files.router, candidates.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, engine=schema_database)


def act_as(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()


def upload(env, data, name="leads.csv", dry_run=False, **fields):
    form = {"dry_run": "true" if dry_run else "false", **{k: str(v) for k, v in fields.items()}}
    return env.client.post("/api/leads/import", files={"file": (name, data, "text/csv")}, data=form)


def imported(env, rows, **kw):
    response = upload(env, meta_export(rows), **kw)
    assert response.status_code == 200, response.text
    return response.json()


def count(env, table="crm_leads"):
    env.db.rollback()
    return env.db.scalar(text(f"SELECT count(*) FROM {table}"))


def first_lead(env, **where):
    act_as(env, env.who["id"])
    listing = env.client.get("/api/leads", params={"page_size": 200, **where}).json()
    return listing["items"][0]


def add_candidate(env, phone="9111111111", email=None, owner="a"):
    candidate = m.Candidate(id=f"cand-{phone}", ref=f"CND-{phone[-5:]}", name="Existing Person", phone=phone, email=email,
                            source=m.Source.other, owner_id=owner)
    env.db.add(candidate)
    env.db.commit()
    return candidate


def daily_item(env, owner, *, status="open", days_ago=0, key=None):
    today = agency_today(datetime.now(timezone.utc))
    cand = env.db.get(m.Candidate, f"daily-{key or owner}-{status}-{days_ago}")
    if cand is None:
        cand = m.Candidate(id=f"daily-{key or owner}-{status}-{days_ago}", ref=f"CND-D{abs(hash((owner, status, days_ago, key))) % 10**6:06d}",
                           name="Daily Person", phone=f"8{abs(hash((owner, status, days_ago, key))) % 10**9:09d}", source=m.Source.other, owner_id=owner)
        env.db.add(cand)
        env.db.flush()
    item = m.DailyWorkItem(owner_id=owner, work_date=today - timedelta(days=days_ago), kind="call", candidate_id=cand.id, slot_no=1,
                           session="morning", due_at=datetime.now(timezone.utc) + timedelta(hours=2), status=status)
    env.db.add(item)
    env.db.commit()
    return item


# ------------------------------------------------------------------ import

def test_meta_export_is_stored_by_columns(env):
    result = imported(env, meta_rows(0, 30))
    assert (result["format"], result["encoding"], result["delimiter"]) == ("tsv", "utf-16-le", "\t")
    assert (result["rowsTotal"], result["rowsNew"], result["rowsDuplicate"], result["rowsInvalid"]) == (30, 30, 0, 0)
    assert result["batchId"] and not result["unmappedColumns"] and not result["sameFileBefore"]
    lead = env.db.scalars(select(m.Lead).where(m.Lead.external_id == "1795000000000003")).one()
    assert (lead.name, lead.phone, lead.phone_country_code, lead.phone_key) == ("Test Lead 00003", "+919000000003", "91", "9000000003")
    assert lead.email_key == "lead3@example.test" and lead.campaign_name == "Dubai-Chef-03-11" and lead.country_interest == "Dubai"
    assert lead.position_applied == "South Indian Chef (CDP)"
    assert lead.state in ("Kerala", "Tamil Nadu") and lead.status == "new" and lead.owner_id is None and lead.import_row == 5
    assert lead.created_time.utcoffset() is not None and lead.is_organic is False and lead.platform == "fb"
    assert lead.raw["full_name"] == "Test Lead 00003" and lead.extra == {}
    activity = env.db.scalars(select(m.LeadActivity).where(m.LeadActivity.lead_id == lead.id)).one()
    assert activity.kind == "import"
    batch = env.db.get(m.LeadBatch, result["batchId"])
    assert batch.rows_new == 30 and batch.column_map["full_name"] == "name" and batch.file_format == "tsv"


def test_other_ad_form_columns_are_kept_in_extra(env):
    headers = ["Full Name", "Mobile", "Do you hold a licence?", "Preferred shift"]
    data = csv_bytes([["Test Person", "9000000011", "Yes", "Night"]], headers=headers)
    result = upload(env, data).json()
    assert result["rowsNew"] == 1 and result["unmappedColumns"] == ["Do you hold a licence?", "Preferred shift"]
    lead = env.db.scalars(select(m.Lead)).one()
    assert lead.extra == {"Do you hold a licence?": "Yes", "Preferred shift": "Night"} and lead.external_id is None


def test_xlsx_and_csv_uploads_store_the_same_people(env):
    rows = [["Name", "Phone", "Email"], ["Xlsx Person", 9000000021, "x1@example.test"], ["Second Person", "9000000022", None]]
    response = upload(env, xlsx_bytes(rows), name="leads.xlsx")
    assert response.status_code == 200 and response.json()["format"] == "xlsx" and response.json()["rowsNew"] == 2
    again = upload(env, csv_bytes([[str(c or "") for c in r] for r in rows[1:]], headers=rows[0]), name="leads.csv").json()
    assert (again["rowsNew"], again["rowsDuplicate"]) == (0, 2) and {d["reason"] for d in again["duplicates"]} == {"phone"}


def test_reuploading_the_same_file_adds_nothing_and_a_longer_file_continues(env):
    first = imported(env, meta_rows(0, 1000))
    assert first["rowsNew"] == 1000 and count(env) == 1000
    same = imported(env, meta_rows(0, 1000))
    assert (same["rowsNew"], same["rowsDuplicate"]) == (0, 1000) and count(env) == 1000
    assert same["sameFileBefore"]["batchId"] == first["batchId"]
    assert {d["reason"] for d in same["duplicates"]} == {"lead_id"} and len(same["duplicates"]) == 200 and same["duplicatesTruncated"]
    # the next export holds the first 1000 again plus 500 new people: exactly the 500 are added
    longer = imported(env, meta_rows(0, 1500))
    assert (longer["rowsTotal"], longer["rowsNew"], longer["rowsDuplicate"]) == (1500, 500, 1000) and count(env) == 1500
    assert env.db.scalar(select(func.count()).select_from(m.LeadActivity)) == 1500


def test_dry_run_changes_nothing_and_previews(env):
    result = upload(env, meta_export(meta_rows(0, 40)), dry_run=True).json()
    assert result["dryRun"] and result["batchId"] is None and result["rowsNew"] == 40
    assert len(result["sample"]) == 20 and result["sample"][0]["outcome"] == "new" and result["sample"][0]["name"] == "Test Lead 00000"
    assert result["columns"][0] == {"header": "id", "field": "externalId", "confidence": "alias"}
    assert count(env) == 0 and count(env, "crm_lead_batches") == 0 and count(env, "crm_lead_activities") == 0
    imported(env, meta_rows(0, 10))
    again = upload(env, meta_export(meta_rows(0, 40)), dry_run=True).json()
    assert (again["rowsNew"], again["rowsDuplicate"]) == (30, 10) and count(env) == 10


def test_duplicates_inside_the_file_by_id_then_phone_then_email(env):
    rows = [lead_row(1), lead_row(2),
            lead_row(3, id="l:1795000000000001"),                                  # same lead id as row 2
            lead_row(4, phone_number="p:+919000000002"),                           # same phone as the second person
            lead_row(5, email="LEAD1@example.test", phone_number="p:+919000000099", id="l:1795000000000555"),  # same e-mail, new phone and id
            lead_row(6, id="l:1795000000000006", phone_number="p:+919000000006", email="lead6@example.test")]
    result = imported(env, rows)
    assert (result["rowsNew"], result["rowsDuplicate"]) == (3, 3)
    reasons = {d["row"]: (d["reason"], d["scope"], d["existingRow"]) for d in result["duplicates"]}
    assert reasons == {4: ("lead_id", "file", 2), 5: ("phone", "file", 3), 6: ("email", "file", 2)}


def test_duplicates_against_stored_leads_by_id_phone_and_email_with_priority(env):
    imported(env, [lead_row(1), lead_row(2), lead_row(3)])
    ids = {r.external_id: r.id for r in env.db.scalars(select(m.Lead))}
    rows = [lead_row(11, id="l:1795000000000001"),                                     # id match
            lead_row(12, phone_number="+91 90000 00002"),                              # phone typed differently
            lead_row(13, email="Lead3@Example.test"),                                  # e-mail match
            lead_row(14, id="l:1795000000000003", phone_number="p:+919000000001"),     # id wins over phone
            lead_row(15)]
    result = imported(env, rows)
    assert (result["rowsNew"], result["rowsDuplicate"]) == (1, 4)
    by_row = {d["row"]: d for d in result["duplicates"]}
    assert [by_row[r]["reason"] for r in (2, 3, 4, 5)] == ["lead_id", "phone", "email", "lead_id"]
    assert by_row[2]["existingLeadId"] == ids["1795000000000001"] and by_row[3]["existingLeadId"] == ids["1795000000000002"]
    assert by_row[5]["existingLeadId"] == ids["1795000000000003"] and all(d["scope"] == "database" for d in by_row.values())


def test_a_duplicate_never_overwrites_crm_data_and_fill_empty_only_fills_blanks(env):
    imported(env, [lead_row(1, city="")])
    lead = env.db.scalars(select(m.Lead)).one()
    lead.status, lead.notes, lead.owner_id, lead.call_count = "interested", "Called twice", "a", 2
    env.db.commit()
    plain = imported(env, [lead_row(1, city="New City", full_name="Changed Name")])
    assert plain["rowsDuplicate"] == 1 and plain["rowsUpdated"] == 0
    env.db.refresh(lead)
    assert lead.city is None and lead.name == "Test Lead 00001"
    filled = imported(env, [lead_row(1, city="New City", full_name="Changed Name", gender="female")], fill_empty="true")
    assert filled["rowsUpdated"] == 1
    env.db.refresh(lead)
    assert lead.city == "New City" and lead.name == "Test Lead 00001" and lead.gender == "Male"  # blank filled, stored values kept
    assert (lead.status, lead.notes, lead.owner_id, lead.call_count) == ("interested", "Called twice", "a", 2)


def test_invalid_rows_are_counted_and_listed(env):
    rows = [lead_row(1), lead_row(2, phone_number="p:", email=""), lead_row(3, phone_number="p:+12", email=""), lead_row(4, phone_number="", email="x@example.test")]
    result = imported(env, rows)
    assert (result["rowsNew"], result["rowsInvalid"]) == (2, 2)
    assert [(i["row"], i["reason"]) for i in result["invalid"]] == [(3, "no_contact"), (4, "bad_phone")]


def test_existing_candidates_are_flagged_not_blocked(env):
    add_candidate(env, "9000000002")
    add_candidate(env, "9222222222", email="lead4@example.test")
    result = imported(env, meta_rows(0, 5))
    assert result["rowsNew"] == 5 and result["rowsMatchedCandidates"] == 2
    flagged = {l.name: l.matched_candidate_id for l in env.db.scalars(select(m.Lead)) if l.matched_candidate_id}
    assert flagged == {"Test Lead 00002": "cand-9000000002", "Test Lead 00004": "cand-9222222222"}
    listing = env.client.get("/api/leads", params={"matched_candidate": "true"}).json()
    assert listing["total"] == 2 and listing["items"][0]["matchedCandidateRef"].startswith("CND-")


def test_import_is_a_handful_of_statements_whatever_the_size(env):
    statements = []
    def counter(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(env.engine, "before_cursor_execute", counter)
    try:
        result = imported(env, meta_rows(0, 2000))
        small = len(statements)
        statements.clear()
        again = imported(env, meta_rows(0, 2000))
    finally:
        event.remove(env.engine, "before_cursor_execute", counter)
    assert result["rowsNew"] == 2000 and again["rowsDuplicate"] == 2000
    assert small <= 22, f"{small} statements for 2,000 rows"
    assert len(statements) <= 14, "a re-upload of 2,000 known people is a handful of reads"
    assert sum(1 for s in statements if s.lstrip().upper().startswith("INSERT INTO CRM_LEADS ")) == 0


def test_assignment_at_import_and_notifications(env):
    result = imported(env, meta_rows(0, 7), assign_to="auto")
    owners = [row[0] for row in env.db.execute(text("SELECT owner_id FROM crm_leads ORDER BY owner_id"))]
    assert sorted(set(owners)) == ["a", "b"] and {owners.count("a"), owners.count("b")} == {3, 4} and result["assignedCount"] == 7  # on-leave staff skipped
    assert env.db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.kind == "lead_assignment")) == 2
    before_b = owners.count("b")
    one = imported(env, meta_rows(10, 12), assign_to="b")
    assert one["assignedTo"] == "b" and env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.owner_id == "b")) == before_b + 2
    bad = upload(env, meta_export(meta_rows(20, 22)), assign_to="away")
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_request" and count(env) == 9


def test_import_rejections_and_permissions(env):
    assert upload(env, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 50, name="old.xls").json()["detail"]["code"] == "unsupported_file"
    assert upload(env, b"", name="e.csv").status_code == 422
    assert upload(env, b"foo,bar\n1,2\n", name="odd.csv").json()["detail"]["code"] == "no_usable_columns"
    bad = env.client.post("/api/leads/import", files={"file": ("x.csv", meta_export(meta_rows(0, 1)), "text/csv")}, data={"mapping": "[1]"})
    assert bad.status_code == 422
    mapped = upload(env, csv_bytes([["Test Person", "9000000031"]], headers=["colA", "colB"]), mapping=json.dumps({"colA": "name", "colB": "phone"}))
    assert mapped.status_code == 200 and mapped.json()["rowsNew"] == 1
    assert count(env) == 1
    for who in ("a", "away"):
        act_as(env, who)
        denied = upload(env, meta_export(meta_rows(0, 3)))
        assert denied.status_code == 403
    act_as(env, "manager")
    assert upload(env, meta_export(meta_rows(0, 3))).status_code == 200


# ------------------------------------------------------------------ batches

def test_batch_history_detail_report_and_revert(env):
    imported(env, meta_rows(0, 5))
    rows = [lead_row(1), lead_row(6, full_name="=HYPERLINK(\"http://x\")"), lead_row(7, phone_number="", email="")]
    second = imported(env, rows)
    history = env.client.get("/api/leads/batches").json()
    assert history["total"] == 2 and history["items"][0]["id"] == second["batchId"]
    assert history["items"][0]["uploadedByName"] == "Admin" and history["items"][0]["leadsRemaining"] == 1
    detail = env.client.get(f"/api/leads/batches/{second['batchId']}").json()
    assert detail["rowsDuplicate"] == 1 and detail["duplicates"][0]["reason"] == "lead_id" and detail["invalid"][0]["reason"] == "no_contact"
    assert env.client.get("/api/leads/batches/nope").status_code == 404
    report = env.client.get(f"/api/leads/batches/{second['batchId']}/report.csv")
    assert report.status_code == 200 and report.content.startswith(b"\xef\xbb\xbf")
    parsed = list(csv.reader(io.StringIO(report.content.decode("utf-8-sig"))))
    assert parsed[0][:3] == ["Row", "Outcome", "Reason"] and [r[1] for r in parsed[1:]] == ["Ignored: duplicate", "Added", "Invalid"]
    # revert: an untouched upload goes away, a worked lead stays
    lead = env.db.scalars(select(m.Lead).where(m.Lead.import_row == 3)).first()
    first_batch = history["items"][1]["id"]
    act_as(env, "admin")
    env.db.execute(text("UPDATE crm_leads SET status = 'contacted' WHERE batch_id = :b AND import_row = 3"), {"b": first_batch})
    env.db.commit()
    reverted = env.client.delete(f"/api/leads/batches/{first_batch}").json()
    assert reverted == {"deleted": 4, "kept": 1}
    act_as(env, "manager")
    assert env.client.delete(f"/api/leads/batches/{second['batchId']}").status_code == 403
    assert env.client.get(f"/api/leads/batches/{second['batchId']}/report.csv").status_code == 200
    act_as(env, "a")
    assert env.client.get(f"/api/leads/batches/{second['batchId']}/report.csv").status_code == 403
    assert env.client.get("/api/leads/batches").status_code == 200


# ------------------------------------------------------------------ list and search

def test_list_search_filters_sort_and_paging(env):
    imported(env, meta_rows(0, 40))
    listing = env.client.get("/api/leads", params={"page_size": 10}).json()
    assert listing["total"] == 40 and len(listing["items"]) == 10 and listing["page"] == 0
    assert env.client.get("/api/leads", params={"page": 99, "page_size": 10}).json()["page"] == 3
    lead = listing["items"][0]
    assert {"id", "name", "phone", "status", "campaignName", "hasResume", "ownerName", "matchedCandidateId"} <= set(lead)
    search = lambda q, **kw: env.client.get("/api/leads", params={"q": q, **kw}).json()["total"]
    assert search("test lead 00007") == 1 and search("TEST LEAD 00007") == 1 and search("lead 0000") == 10
    assert search("9000000012") == 1 and search("+91 90000 00012") == 1 and search("lead12@EXAMPLE") == 1
    assert search("dubai, kerala") == search("kerala") > 0 and search("dubai, instagram") == 20 and search("dubai, nonexistent") == 0
    assert search("kerala, instagram") < search("kerala") and search("instagram, facebook") == 0
    assert search("Kitchen Helper") > 0 and search("kitchen helper, dosa") == 0
    assert search("new") == 40 and search("resume received") == 0 and search("instagram") == 20
    assert search("", platform="ig") == 20 and search("", position="kitchen_helper") == 0
    assert search("", position="Kitchen Helper") == search("kitchen helper")
    assert search("", status="new,contacted") == 40 and search("", status="interested") == 0
    assert env.client.get("/api/leads", params={"status": "bogus"}).status_code == 422
    assert search("", created_from="2026-11-04", created_to="2026-11-04") == 40 and search("", created_from="2026-11-05") == 0
    ordered = env.client.get("/api/leads", params={"sort": "name", "direction": "asc", "page_size": 3}).json()["items"]
    assert [l["name"] for l in ordered] == ["Test Lead 00000", "Test Lead 00001", "Test Lead 00002"]
    assert env.client.get("/api/leads", params={"sort": "bogus"}).status_code == 422
    assert search("", owner="unassigned") == 40 and search("", owner="me") == 0
    facets = env.client.get("/api/leads/facets").json()
    assert facets["campaigns"] == ["Dubai-Chef-03-11"] and set(facets["platforms"]) == {"fb", "ig"} and "Kitchen Helper" in facets["positions"]


def test_search_covers_owner_notes_and_ad_names(env):
    imported(env, meta_rows(0, 3), assign_to="a")
    lead = first_lead(env)
    env.client.patch(f"/api/leads/{lead['id']}", json={"notes": "Wants Qatar offers"})
    search = lambda q: env.client.get("/api/leads", params={"q": q}).json()["total"]
    assert search("asha") == 3 and search("qatar offers") == 1 and search("dubai-chef-03-11-co") == 3 and search("asha, qatar") == 1


# ------------------------------------------------------------------ working a lead

def test_staff_work_only_their_own_leads_admin_and_manager_any(env):
    imported(env, meta_rows(0, 4))
    leads_ = env.db.scalars(select(m.Lead).order_by(m.Lead.import_row)).all()
    env.db.execute(text("UPDATE crm_leads SET owner_id = 'a' WHERE id = :i"), {"i": leads_[0].id})
    env.db.execute(text("UPDATE crm_leads SET owner_id = 'b' WHERE id = :i"), {"i": leads_[1].id})
    env.db.commit()
    mine, theirs, nobody = leads_[0].id, leads_[1].id, leads_[2].id
    act_as(env, "a")
    ok = env.client.post(f"/api/leads/{mine}/call", json={"outcome": "interested", "remarks": "Keen on Dubai"})
    assert ok.status_code == 200 and ok.json()["status"] == "interested" and ok.json()["callCount"] == 1 and ok.json()["lastContactedAt"]
    for lead_id, who in ((theirs, "Ravi"), (nobody, None)):
        for response in (env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}),
                         env.client.patch(f"/api/leads/{lead_id}", json={"notes": "x"}),
                         env.client.post(f"/api/leads/{lead_id}/convert"),
                         env.client.post(f"/api/leads/{lead_id}/link-candidate", json={"candidateId": "x"})):
            assert response.status_code == 403 and response.json()["detail"]["code"] == "lead_not_yours"
        assert env.client.get(f"/api/leads/{lead_id}").status_code == 200, "reading stays open to every signed-in user"
    assert "Ravi" in env.client.post(f"/api/leads/{theirs}/call", json={"outcome": "answered"}).json()["detail"]["message"]
    assert env.client.post(f"/api/leads/{mine}/call", json={"outcome": "bogus"}).status_code == 422
    for who in ("admin", "manager"):
        act_as(env, who)
        assert env.client.post(f"/api/leads/{theirs}/call", json={"outcome": "no_answer"}).status_code == 200


def test_call_outcomes_edit_log_and_validation(env):
    imported(env, meta_rows(0, 3))
    lead_id = first_lead(env)["id"]
    call = lambda outcome, **kw: env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": outcome, **kw}).json()
    assert call("no_answer")["status"] == "new" and call("answered")["status"] == "contacted"
    assert call("interested", nextFollowUpAt="2026-11-10T10:00:00+05:30")["nextFollowUpAt"].startswith("2026-11-10")
    env.client.patch(f"/api/leads/{lead_id}", json={"status": "resume_requested"})
    assert call("interested")["status"] == "resume_requested"
    done = call("not_interested", remarks="Not now")
    assert done["status"] == "not_interested" and done["callCount"] == 5
    assert call("wrong_number")["status"] == "wrong_number"
    edited = env.client.patch(f"/api/leads/{lead_id}", json={"positionApplied": "Pastry Chef", "countryInterest": "Qatar", "gender": "female",
                                                         "phone": "+91 98765 43211", "dob": "1990-01-02", "notes": "Call after 6", "status": "new"})
    body = edited.json()
    assert edited.status_code == 200 and (body["positionApplied"], body["countryInterest"], body["gender"], body["phone"]) == ("Pastry Chef", "Qatar", "Female", "+919876543211")
    assert body["dob"] == "1990-01-02" and body["status"] == "new" and body["notes"] == "Call after 6"
    detail = env.client.get(f"/api/leads/{lead_id}").json()
    assert {a["kind"] for a in detail["activities"]} >= {"call", "status", "edit", "note", "import"} and detail["activities"][0]["userName"] == "Admin"
    assert detail["raw"]["full_name"] and detail["extra"] == {} and detail["resume"] is None
    assert env.client.patch(f"/api/leads/{lead_id}", json={"status": "converted"}).status_code == 422
    assert env.client.patch(f"/api/leads/{lead_id}", json={"status": "nonsense"}).status_code == 422
    assert env.client.patch(f"/api/leads/{lead_id}", json={"phone": "123"}).status_code == 422
    other = env.db.scalars(select(m.Lead).where(m.Lead.id != lead_id)).first()
    clash = env.client.patch(f"/api/leads/{other.id}", json={"phone": "9876543211"})
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "duplicate_lead"
    assert env.client.patch(f"/api/leads/{lead_id}", json={"unknown": 1}).status_code == 422
    assert env.client.get("/api/leads/nope").status_code == 404


def test_row_edit_of_every_table_field_and_remarks(env):
    imported(env, meta_rows(0, 2))
    lead_id = first_lead(env)["id"]
    body = {"name": "Edited Name", "dob": "1995-05-06", "phone": "+91 98765 40000", "positionApplied": "Tandoor Chef",
            "educationLevel": "Diploma", "experienceIndia": "4 years", "experienceAbroad": "6 months", "experienceTotal": "4.5",
            "city": "Charummoodu", "state": "Kerala", "status": "contacted", "notes": "Prefers a call after 6 pm"}
    edited = env.client.patch(f"/api/leads/{lead_id}", json=body)
    assert edited.status_code == 200, edited.text
    out = edited.json()
    assert (out["name"], out["dob"], out["phone"], out["positionApplied"], out["educationLevel"]) == ("Edited Name", "1995-05-06", "+919876540000", "Tandoor Chef", "Diploma")
    assert (out["experienceIndiaYears"], out["experienceIndiaText"], out["experienceIndiaQuestion"]) == (4.0, "4 years", None)
    assert (out["experienceAbroadYears"], out["experienceAbroadText"]) == (0.5, "6 months")
    assert (out["experienceTotalYears"], out["experienceTotalText"]) == (4.5, "4.5")
    assert (out["city"], out["state"], out["status"], out["notes"]) == ("Charummoodu", "Kerala", "contacted", "Prefers a call after 6 pm")
    listed = next(item for item in env.client.get("/api/leads").json()["items"] if item["id"] == lead_id)
    assert listed["experienceIndiaText"] == "4 years" and listed["notes"] == "Prefers a call after 6 pm"
    cleared = env.client.patch(f"/api/leads/{lead_id}", json={"experienceIndia": "", "experienceTotal": None, "notes": ""}).json()
    assert cleared["experienceIndiaYears"] is None and cleared["experienceIndiaText"] is None and cleared["notes"] is None
    assert cleared["experienceTotalYears"] is None and cleared["experienceTotalText"] is None
    assert env.client.patch(f"/api/leads/{lead_id}", json={"experienceIndia": "x" * 201}).status_code == 422


def test_assign_ids_auto_filter_and_permissions(env):
    imported(env, meta_rows(0, 12))
    ids = [row[0] for row in env.db.execute(text("SELECT id FROM crm_leads ORDER BY import_row"))]
    act_as(env, "a")
    assert env.client.post("/api/leads/assign", json={"ids": ids[:2], "ownerId": "a"}).status_code == 403
    act_as(env, "manager")
    one = env.client.post("/api/leads/assign", json={"ids": ids[:2], "ownerId": "a"}).json()
    assert one["assigned"] == 2 and one["owners"] == [{"id": "a", "name": "Asha", "count": 2}]
    again = env.client.post("/api/leads/assign", json={"ids": ids[:2], "ownerId": "a"}).json()
    assert (again["assigned"], again["skipped"]) == (0, 2)
    spread = env.client.post("/api/leads/assign", json={"filter": {"owner": "unassigned"}, "ownerId": "auto"}).json()
    counts = {o["id"]: o["count"] for o in spread["owners"]}
    assert spread["assigned"] == 10 and counts == {"a": 4, "b": 6} and "away" not in counts  # Asha already holds two: Ravi gets more
    assert env.client.post("/api/leads/assign", json={"ids": ids[:1], "ownerId": "away"}).status_code == 422
    assert env.client.post("/api/leads/assign", json={"ownerId": "a"}).status_code == 422
    assert env.client.post("/api/leads/assign", json={"ids": ids[:3], "ownerId": "b", "onlyUnassigned": True}).json()["assigned"] == 0
    assert env.db.scalar(select(func.count()).select_from(m.LeadActivity).where(m.LeadActivity.kind == "assign")) == 12
    act_as(env, "a")
    mine = env.client.get("/api/leads", params={"owner": "me", "page_size": 50}).json()
    assert mine["total"] == 6 and all(l["ownerName"] == "Asha" for l in mine["items"])
    summary = env.client.get("/api/leads/summary").json()
    assert summary["scope"] == "mine" and summary["total"] == 6 and summary["unassigned"] == 0


def test_summary_counts_by_scope(env):
    imported(env, meta_rows(0, 10), assign_to="a")
    imported(env, meta_rows(10, 14))
    env.db.execute(text("UPDATE crm_leads SET created_time = now()")); env.db.commit()
    lead_id = env.db.scalars(select(m.Lead).where(m.Lead.owner_id == "a")).first().id
    env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "interested", "nextFollowUpAt": "2020-01-01T10:00:00+00:00"})
    everything = env.client.get("/api/leads/summary").json()
    assert (everything["scope"], everything["total"], everything["unassigned"], everything["interested"], everything["uncontacted"]) == ("all", 14, 4, 1, 13)
    assert everything["newToday"] == 14 and everything["followUpsDue"] == 1 and everything["byStatus"] == {"new": 13, "interested": 1}
    assert everything["byCampaign"] == [{"campaign": "Dubai-Chef-03-11", "count": 14, "converted": 0}] and everything["lastImportAt"]
    assert everything["dailyWorkGate"]["locked"] is False
    act_as(env, "a")
    mine = env.client.get("/api/leads/summary").json()
    assert (mine["scope"], mine["total"], mine["unassigned"]) == ("mine", 10, 4)
    assert env.client.get("/api/leads/summary", params={"scope": "all"}).json()["total"] == 14
    act_as(env, "b")
    assert env.client.get("/api/leads/summary").json()["total"] == 0


# ------------------------------------------------------------------ resume, convert, link

def test_resume_upload_to_a_lead_sets_status_and_follows_permissions(env):
    imported(env, meta_rows(0, 2), assign_to="a")
    lead_id = first_lead(env)["id"]
    send = lambda who, name="resume.pdf", slot="resume": (act_as(env, who), env.client.post(
        "/api/files", data={"entity_type": "lead", "entity_id": lead_id, "slot": slot}, files={"file": (name, PDF, "application/pdf")}))[1]
    assert send("b").status_code == 403
    assert send("a", slot="passport").status_code == 422
    sent = send("a")
    assert sent.status_code == 201 and sent.json()["version"] == 1
    assert send("manager").json()["version"] == 2
    act_as(env, "a")
    detail = env.client.get(f"/api/leads/{lead_id}").json()
    assert detail["status"] == "resume_received" and detail["hasResume"] and detail["resumeReceivedAt"]
    assert detail["resume"]["version"] == 2 and detail["resume"]["url"].startswith("/api/files/")
    assert env.client.get(detail["resume"]["url"]).content == PDF
    assert any(a["kind"] == "resume" for a in detail["activities"])
    assert env.client.get("/api/leads", params={"has_resume": "true"}).json()["total"] == 1


def test_convert_creates_a_candidate_with_the_leads_data_and_resume(env):
    from lead_resume_fixtures import checked_resume
    imported(env, [lead_row(1)], assign_to="a")
    lead_id = first_lead(env)["id"]
    act_as(env, "a")
    env.client.post("/api/files", data={"entity_type": "lead", "entity_id": lead_id, "slot": "resume"}, files={"file": ("cv.pdf", PDF, "application/pdf")})
    checked = checked_resume(env, lead_id, PDF, original=True)
    done = env.client.post(f"/api/leads/{lead_id}/convert", data={"resume_check_id": checked.id})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["lead"]["status"] == "converted" and body["lead"]["candidateId"] == body["candidate"]["id"] and body["lead"]["candidateRef"].startswith("CND-")
    assert body["resumeAttached"] is True and body["candidate"]["ownerId"] == "a"
    candidate = env.db.get(m.Candidate, body["candidate"]["id"])
    assert (candidate.name, candidate.phone, candidate.source, candidate.source_detail) == ("Test Lead 00001", "+919000000001", m.Source.other, f"lead_sheet:{lead_id}")
    assert candidate.designations == ["Kitchen Helper"] and candidate.country_preference == "Dubai" and candidate.gender == "male" and candidate.owner_id == "a"
    resume = env.db.scalars(select(m.Attachment).where(m.Attachment.entity_type == "candidate", m.Attachment.entity_id == candidate.id, m.Attachment.slot == "resume")).one()
    assert resume.file_name == "cv.pdf"
    assert env.db.scalar(select(func.count()).select_from(m.DocumentExtractionJob).where(m.DocumentExtractionJob.candidate_id == candidate.id)) == 1
    detail = env.client.get(f"/api/leads/{lead_id}").json()
    assert detail["status"] == "converted" and any(a["kind"] == "convert" for a in detail["activities"])
    assert detail["campaignName"] == "Dubai-Chef-03-11"
    activity = env.db.scalars(select(m.LeadActivity).where(m.LeadActivity.lead_id == lead_id, m.LeadActivity.kind == "convert")).one()
    assert activity.meta["leadId"] == lead_id and activity.meta["campaignName"] == detail["campaignName"]
    audit = env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.entity_id == candidate.id, m.AuditEntry.action == "Converted lead to candidate")).one()
    assert audit.meta["leadId"] == lead_id and audit.meta["campaignName"] == detail["campaignName"]
    assert env.client.post(f"/api/leads/{lead_id}/convert").json()["detail"]["code"] == "already_converted"
    assert env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}).status_code == 409


def test_convert_with_resume_in_the_request_and_overrides(env):
    from lead_resume_fixtures import checked_resume
    imported(env, [lead_row(2, email="")], assign_to="a")
    lead_id = first_lead(env)["id"]
    act_as(env, "a")
    checked = checked_resume(env, lead_id, PDF, name="Corrected Name", filename="new-cv.pdf", overrides={"name": "Corrected Name"})
    done = env.client.post(f"/api/leads/{lead_id}/convert", files={"resume": ("new-cv.pdf", PDF, "application/pdf")},
                           data={"resume_check_id": checked.id, "overrides": json.dumps({"name": "Corrected Name", "totalExperience": 7, "sourceDetail": "Known Referrer"})})
    assert done.status_code == 200 and done.json()["resumeAttached"] is True and done.json()["candidate"]["name"] == "Corrected Name"
    candidate = env.db.get(m.Candidate, done.json()["candidate"]["id"])
    assert candidate.total_experience == 7
    assert candidate.source_detail == "Known Referrer"  # Explicitly supplied referral details are preserved.
    assert env.db.get(m.Lead, lead_id).campaign_name == "Dubai-Chef-03-11"
    assert env.db.scalars(select(m.Attachment).where(m.Attachment.entity_id == candidate.id, m.Attachment.slot == "resume")).one().file_name == "new-cv.pdf"
    bad = env.client.post(f"/api/leads/{lead_id}/convert", data={"overrides": "[1]"})
    assert bad.status_code == 422


def test_convert_refuses_an_existing_candidate_and_link_connects_it(env):
    existing = add_candidate(env, "9000000003", owner="b")
    existing.source, existing.source_detail = m.Source.referral, "Existing Referral Agency"
    env.db.commit()
    imported(env, [lead_row(3, email="")], assign_to="a")
    lead_id = first_lead(env)["id"]
    act_as(env, "a")
    clash = env.client.post(f"/api/leads/{lead_id}/convert")
    assert clash.status_code == 409
    detail = clash.json()["detail"]
    assert detail["code"] == "candidate_exists" and detail["candidate"]["id"] == existing.id and detail["candidate"]["ref"] == existing.ref
    assert detail["candidate"]["ownerName"] == "Ravi"
    assert env.db.scalar(select(func.count()).select_from(m.Candidate)) == 1 and env.client.get(f"/api/leads/{lead_id}").json()["status"] == "new"
    linked = env.client.post(f"/api/leads/{lead_id}/link-candidate", json={"candidateId": existing.id})
    assert linked.status_code == 200 and linked.json()["status"] == "converted" and linked.json()["candidateId"] == existing.id
    env.db.refresh(existing)
    assert (existing.name, existing.owner_id, existing.source, existing.source_detail) == ("Existing Person", "b", m.Source.referral, "Existing Referral Agency")
    assert env.client.post(f"/api/leads/{lead_id}/link-candidate", json={"candidateId": existing.id}).status_code == 409
    act_as(env, "admin")
    imported(env, [lead_row(9)], assign_to="a")
    other = env.db.scalars(select(m.Lead).where(m.Lead.external_id == "1795000000000009")).one().id
    assert env.client.post(f"/api/leads/{other}/link-candidate", json={"candidateId": "nope"}).status_code == 404


def test_convert_needs_a_phone_and_admin_gets_the_leads_owner(env):
    imported(env, [lead_row(4, phone_number="")], assign_to="a")
    lead_id = first_lead(env)["id"]
    act_as(env, "manager")
    needs = env.client.post(f"/api/leads/{lead_id}/convert")
    assert needs.status_code == 422 and "phone" in needs.json()["detail"]["message"].lower()
    done = env.client.post(f"/api/leads/{lead_id}/convert", data={"overrides": json.dumps({"phone": "9000000044"})})
    assert done.status_code == 200 and done.json()["candidate"]["ownerId"] == "a"


# ------------------------------------------------------------------ the daily-work gate

def test_gated_staff_cannot_work_leads_until_their_daily_tasks_are_done(env):
    imported(env, meta_rows(0, 2), assign_to="a")
    lead_id = first_lead(env)["id"]
    existing = add_candidate(env, "9333333333")
    item = daily_item(env, "a", key="x")
    daily_item(env, "a", days_ago=2, key="postponed")
    act_as(env, "a")
    attempts = lambda: (
        env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}),
        env.client.patch(f"/api/leads/{lead_id}", json={"notes": "x"}),
        env.client.post(f"/api/leads/{lead_id}/convert"),
        env.client.post(f"/api/leads/{lead_id}/link-candidate", json={"candidateId": existing.id}),
        env.client.post("/api/files", data={"entity_type": "lead", "entity_id": lead_id, "slot": "resume"}, files={"file": ("cv.pdf", PDF, "application/pdf")}),
    )
    for response in attempts():
        assert response.status_code == 403, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "daily_work_pending"
        assert detail["message"] == "Finish today's tasks first. You have 2 daily tasks left (1 postponed). Then you can work the lead sheet."
        assert detail["dailyWorkGate"]["locked"] and detail["dailyWorkGate"]["open"] == 2 and detail["dailyWorkGate"]["postponed"] == 1
    # reading is never gated, and the gate rides along
    assert env.client.get("/api/leads").status_code == 200 and env.client.get(f"/api/leads/{lead_id}").status_code == 200
    gate = env.client.get("/api/leads/summary").json()["dailyWorkGate"]
    assert gate["locked"] is True and (gate["open"], gate["total"], gate["postponed"]) == (2, 2, 1) and gate["nextDueAt"] and gate["message"].startswith("Finish today's tasks")
    assert env.client.get(f"/api/leads/{lead_id}").json()["dailyWorkGate"]["locked"] is True
    # finishing today's item is not enough while the postponed one is open
    env.db.execute(text("UPDATE workflow_daily_work_items SET status = 'completed', completed_at = now() WHERE id = :i"), {"i": item.id}); env.db.commit()
    assert all(r.status_code == 403 for r in attempts())
    assert env.client.get("/api/leads/summary").json()["dailyWorkGate"] | {} and env.client.get("/api/leads/summary").json()["dailyWorkGate"]["open"] == 1
    # a released item no longer counts: the last open one is released, the lead sheet opens
    env.db.execute(text("UPDATE workflow_daily_work_items SET status = 'released', released_at = now() WHERE status = 'open'")); env.db.commit()
    gate = env.client.get("/api/leads/summary").json()["dailyWorkGate"]
    assert gate == {"locked": False, "open": 0, "total": 1, "postponed": 0, "nextDueAt": None, "message": None}
    assert env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}).status_code == 200
    assert env.client.patch(f"/api/leads/{lead_id}", json={"notes": "x"}).status_code == 200
    assert env.client.post("/api/files", data={"entity_type": "lead", "entity_id": lead_id, "slot": "resume"}, files={"file": ("cv.pdf", PDF, "application/pdf")}).status_code == 201
    assert env.client.post(f"/api/leads/{lead_id}/link-candidate", json={"candidateId": existing.id}).status_code == 200


def test_gate_is_open_with_nothing_allocated_and_never_applies_to_supervisors(env):
    imported(env, meta_rows(0, 2), assign_to="a")
    lead_id = first_lead(env)["id"]
    act_as(env, "a")
    assert env.client.get("/api/leads/summary").json()["dailyWorkGate"]["locked"] is False
    assert env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "answered"}).status_code == 200
    # an item allocated for tomorrow, or already done, does not lock
    daily_item(env, "a", status="completed", key="done")
    assert env.client.get("/api/leads/summary").json()["dailyWorkGate"]["locked"] is False
    # admin and manager hold open items of their own and are never gated
    for who in ("admin", "manager"):
        daily_item(env, who, key=who)
        act_as(env, who)
        gate = env.client.get("/api/leads/summary").json()["dailyWorkGate"]
        assert gate["locked"] is False and gate["message"] is None
        assert env.client.post(f"/api/leads/{lead_id}/call", json={"outcome": "no_answer"}).status_code == 200
    # someone else's open items do not lock staff b, who owns none
    daily_item(env, "a", key="again")
    act_as(env, "b")
    assert env.client.get("/api/leads/summary").json()["dailyWorkGate"]["locked"] is False
    # the gate is not applied before ownership: another person's lead says so
    other = env.db.scalars(select(m.Lead).where(m.Lead.id != lead_id)).first()
    env.db.execute(text("UPDATE crm_leads SET owner_id = 'b' WHERE id = :i"), {"i": other.id}); env.db.commit()
    act_as(env, "a")
    assert env.client.post(f"/api/leads/{other.id}/call", json={"outcome": "answered"}).json()["detail"]["code"] == "lead_not_yours"


# ------------------------------------------------------------------ export

def test_export_is_supervisor_only_filtered_and_formula_safe(env):
    imported(env, [lead_row(1), lead_row(2, full_name="=cmd|' /C calc'!A0"), lead_row(3, full_name="+SUM(1)")])
    act_as(env, "a")
    assert env.client.get("/api/leads/export.csv").status_code == 403 and env.client.get("/api/leads/export.xlsx").status_code == 403
    act_as(env, "manager")
    exported = env.client.get("/api/leads/export.csv", params={"q": "@example.test"})
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Name / Age / Contact", "Job applied", "Education"] and len(rows) == 4
    names = {r[rows[0].index("Name")] for r in rows[1:]}
    assert any(r[rows[0].index("Phone")] == "+919000000001" for r in rows[1:]), "a phone number is not mangled into text"
    assert "'=cmd|' /C calc'!A0" in names and "'+sum(1)" in names and "Test Lead 00001" in names
    assert len(list(csv.reader(io.StringIO(env.client.get("/api/leads/export.csv", params={"q": "00001"}).content.decode("utf-8-sig"))))) == 2
    book = env.client.get("/api/leads/export.xlsx")
    assert book.status_code == 200 and book.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(book.content)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet and "t=\"inlineStr\"" in sheet and "Test Lead 00001" in sheet


# ------------------------------------------------------------------ live updates and migration

def test_lead_tables_publish_the_leads_topic():
    for table in ("crm_leads", "crm_lead_batches", "crm_lead_activities"):
        assert change_feed.TOPIC_TABLES[table] == ("leads",)
    assert "leads" in change_feed.CLIENT_TOPICS
    assert change_feed.classify_statement("INSERT INTO crm_leads (id) SELECT id FROM jsonb_to_recordset(CAST(%(rows)s AS jsonb)) AS x(id text) ON CONFLICT DO NOTHING RETURNING id") == ("INSERT", "crm_leads")
    assert change_feed.classify_statement("UPDATE crm_leads SET owner_id = %(o)s WHERE id = ANY(%(ids)s)") == ("UPDATE", "crm_leads")
    # finishing the last daily item is an UPDATE of workflow_daily_work_items: the 'tasks' topic opens every lead screen's gate again
    assert change_feed.classify_statement("UPDATE workflow_daily_work_items SET status = %(s)s, completed_at = now() WHERE id = %(i)s") == ("UPDATE", "workflow_daily_work_items")
    assert change_feed.TOPIC_TABLES["workflow_daily_work_items"] == ("tasks",)
    pending = change_feed.Pending()
    pending.note_table("INSERT", "crm_leads")
    assert json.loads(pending.payload(1))["t"] == ["leads"]
    event_ = change_feed.visible_to({"t": ["leads"], "ids": {"leads": ["x"]}, "at": 5}, "staff", "u1")
    assert event_ == {"topics": ["leads"], "at": 5}, "staff are told the topic only"
    assert change_feed.visible_to({"t": ["leads"], "ids": {"leads": ["x"]}, "at": 5}, "admin", "u1")["ids"] == {"leads": ["x"]}


def test_migration_0024_is_additive_idempotent_and_reversible(schema_database):
    engine = schema_database
    _migrate(engine, "0023_daily_work_call_items")
    before = set(inspect(engine).get_table_names())
    _migrate(engine, "head")
    created = set(inspect(engine).get_table_names()) - before
    assert {"crm_lead_batches", "crm_leads", "crm_lead_activities"} <= created
    indexes = {i["name"] for i in inspect(engine).get_indexes("crm_leads")}
    assert {"uq_crm_leads_external_id", "ix_crm_leads_phone_key", "ix_crm_leads_email_key", "ix_crm_leads_owner_status", "ix_crm_leads_campaign", "ix_crm_leads_created_time"} <= indexes
    spec = importlib.util.spec_from_file_location("lead_sheet_migration", BACKEND / "alembic" / "versions" / "0024_lead_sheet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as c:  # running the upgrade statements again changes nothing and raises nothing
        with Operations.context(MigrationContext.configure(c)):
            module.upgrade()
    with pytest.raises(IntegrityError):  # the unique lead id index is real
        with engine.begin() as c:
            c.execute(text("INSERT INTO crm_leads (id, name, external_id) VALUES ('l1', 'Synthetic', '1')"))
            c.execute(text("INSERT INTO crm_leads (id, name, external_id) VALUES ('l2', 'Twin', '1')"))
    with pytest.raises(IntegrityError):  # status is checked
        with engine.begin() as c:
            c.execute(text("INSERT INTO crm_leads (id, name, status) VALUES ('l4', 'Synthetic', 'bogus')"))
    with engine.begin() as c:
        c.execute(text("INSERT INTO crm_leads (id, name, external_id) VALUES ('l3', 'Synthetic', '3')"))
    with pytest.raises(Exception, match="Cannot downgrade 0024"):
        _migrate(engine, "0023_daily_work_call_items", downgrade=True)
    assert "crm_leads" in inspect(engine).get_table_names()
    with engine.begin() as c:
        c.execute(text("DELETE FROM crm_leads"))
    _migrate(engine, "0023_daily_work_call_items", downgrade=True)
    assert not ({"crm_lead_batches", "crm_leads", "crm_lead_activities"} & set(inspect(engine).get_table_names()))
    assert set(inspect(engine).get_table_names()) == before
    _migrate(engine, "head")
    assert {"crm_lead_batches", "crm_leads", "crm_lead_activities"} <= set(inspect(engine).get_table_names())
