"""Lead sheet status templates on a disposable PostgreSQL: the shared list (anybody adds, admin/MD manage), setting a lead to a built-in
or a template status (single and bulk), filters, facets, export, history, and how the new statuses count in the dashboard and reports.

Counting rule under test (services/lead_status_templates.py): Ringing / No incoming calls (warning) = contacted but not reached and not
interested; Not eligible (negative) = not interested, closed; a neutral template = contacted; a positive one = interested.
Synthetic people only (tests/lead_sheet_fixtures.py). Needs TEST_DATABASE_URL like the other DB tests.
"""
import csv
import io
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.routers import candidates, files, lead_bulk, lead_status_templates, leads
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import daily_work_report, lead_metrics, lead_sheet, lead_status_templates as service
from lead_sheet_fixtures import meta_export, meta_rows
from test_database_naming import _migrate, schema_database  # noqa: F401
from test_lead_sheet import act_as

TZ = "Asia/Kolkata"


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha", email="a@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="b", name="Ravi", email="b@example.test", role=m.Role.staff, receives_auto_assign=True)])
        db.commit()
        app = FastAPI()
        for router in (leads.router, lead_bulk.router, lead_status_templates.router, files.router, candidates.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, engine=schema_database)


URL = "/api/lead-status-templates"


def add(env, name, tone=None, as_user=None):
    if as_user:
        act_as(env, as_user)
    body = {"name": name, **({"tone": tone} if tone else {})}
    return env.client.post(URL, json=body)


def names(env, **params):
    return [item["name"] for item in env.client.get(URL, params=params).json()["items"]]


def six_leads(env, owner="a"):
    """Six synthetic leads assigned to `owner`; their ids in a stable order."""
    act_as(env, "admin")
    response = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export(meta_rows(0, 6)), "text/csv")},
                               data={"dry_run": "false", "assign_to": owner})
    assert response.status_code == 200, response.text
    env.db.rollback()
    return [row[0] for row in env.db.execute(text("SELECT id FROM crm_leads ORDER BY name")).all()]


def lead(env, lead_id):
    env.db.rollback()
    row = env.db.get(m.Lead, lead_id)
    env.db.refresh(row)
    return row


def patch(env, lead_id, status, as_user="a"):
    act_as(env, as_user)
    return env.client.patch(f"/api/leads/{lead_id}", json={"status": status})


# ------------------------------------------------------------------ the shared list

def test_the_three_new_statuses_are_there_for_everybody_in_order(env):
    for user in ("a", "manager", "admin"):
        act_as(env, user)
        items = env.client.get(URL).json()["items"]
        assert [i["name"] for i in items] == ["Ringing", "No incoming calls", "Not eligible"]
        assert [(i["tone"], i["builtin"], i["countsAs"]) for i in items] == [("warning", True, "contacted"), ("warning", True, "contacted"),
                                                                            ("negative", True, "not_interested")]


def test_anybody_signed_in_adds_a_template_and_every_user_then_sees_it(env):
    created = add(env, "  Busy   later ", as_user="a")
    assert created.status_code == 201
    row = created.json()
    assert (row["name"], row["tone"], row["active"], row["builtin"], row["countsAs"]) == ("Busy later", "neutral", True, False, "contacted")
    act_as(env, "b")
    assert names(env) == ["Ringing", "No incoming calls", "Not eligible", "Busy later"]
    assert add(env, "Visa ready", tone="positive", as_user="b").json()["countsAs"] == "interested"
    assert add(env, "Warm lead", tone="Warning", as_user="a").json()["countsAs"] == "contacted"
    assert add(env, "Not for us", tone="negative", as_user="a").json()["countsAs"] == "not_interested"
    # built-in ones first (their own order), then every other one A to Z
    assert names(env) == ["Ringing", "No incoming calls", "Not eligible", "Busy later", "Not for us", "Visa ready", "Warm lead"]
    assert env.db.scalar(select(m.LeadStatusTemplate.created_by).where(m.LeadStatusTemplate.name == "Visa ready")) == "b"
    audit = env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.entity_type == "lead_status_template")).all()
    assert {a.target for a in audit} == {"Busy later", "Visa ready", "Warm lead", "Not for us"}


def test_a_name_is_checked_before_it_is_stored(env):
    add(env, "Busy later")
    for name in ("BUSY LATER", " busy   later "):
        refused = add(env, name)
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert detail["code"] == "status_exists" and detail["existing"]["name"] == "Busy later"
    for name in ("ringing", "No Incoming Calls", "Contacted", "wrong number", "Not reachable", "Interested (resume in)", "new", "wrong_number"):
        assert add(env, name).status_code == 409, name          # the built-in templates and the standard statuses are taken
    for name in ("", "   ", "x" * 41, "Call back, later"):
        assert add(env, name).status_code == 422, name
    assert add(env, "x" * 40).status_code == 201
    assert add(env, "Odd", tone="loud").status_code == 422
    assert env.client.post(URL, json={"name": "Odd", "colour": "red"}).status_code == 422
    assert names(env).count("Busy later") == 1


def test_only_admin_and_md_rename_hide_or_delete_and_the_builtin_ones_are_never_deleted(env):
    mine = add(env, "Busy later", as_user="a").json()["id"]
    ringing = next(i["id"] for i in env.client.get(URL).json()["items"] if i["name"] == "Ringing")
    for user in ("a", "b"):
        act_as(env, user)
        assert env.client.patch(f"{URL}/{mine}", json={"active": False}).status_code == 403
        assert env.client.patch(f"{URL}/{mine}", json={"name": "Other"}).status_code == 403
        assert env.client.delete(f"{URL}/{mine}").status_code == 403
        assert env.client.get(URL, params={"all": 1}).status_code == 200
    act_as(env, "manager")
    renamed = env.client.patch(f"{URL}/{mine}", json={"name": "Busy, later"})
    assert renamed.status_code == 422                                                       # no commas, ever
    assert env.client.patch(f"{URL}/{mine}", json={"name": "ringing"}).status_code == 409   # taken by a built-in template
    assert env.client.patch(f"{URL}/{mine}", json={"name": "Call tomorrow", "tone": "negative"}).status_code == 422   # the tone is fixed
    assert env.client.patch(f"{URL}/{mine}", json={"name": "Call tomorrow"}).json()["name"] == "Call tomorrow"
    assert env.client.patch(f"{URL}/{mine}", json={"name": "CALL TOMORROW"}).json()["name"] == "CALL TOMORROW"   # its own name, other case
    # built-in: no rename, no delete, but it may be switched off (and on again)
    act_as(env, "admin")
    assert env.client.patch(f"{URL}/{ringing}", json={"name": "Phone ringing"}).status_code == 409
    assert env.client.delete(f"{URL}/{ringing}").status_code == 409
    assert env.client.patch(f"{URL}/{ringing}", json={"active": False}).json()["active"] is False
    assert "Ringing" not in names(env) and "Ringing" in names(env, all=1)
    act_as(env, "a")
    assert "Ringing" not in names(env, all=1)                      # hidden ones are for admin / MD only, whatever the flag says
    act_as(env, "admin")
    assert env.client.patch(f"{URL}/{ringing}", json={"active": True}).json()["active"] is True
    gone = env.client.delete(f"{URL}/{mine}")
    assert gone.status_code == 200 and gone.json()["deleted"] is True
    assert "CALL TOMORROW" not in names(env, all=1)
    assert env.client.delete(f"{URL}/{mine}").status_code == 404
    assert env.client.patch(f"{URL}/none", json={"active": False}).status_code == 404
    actions = [a.action for a in env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.entity_type == "lead_status_template").order_by(m.AuditEntry.at))]
    assert "Deleted lead status" in actions and "Updated lead status" in actions


# ------------------------------------------------------------------ a lead holds a status

def test_a_lead_takes_a_new_built_in_status_and_the_screen_text_is_the_label(env):
    first, second, third = six_leads(env)[:3]
    done = patch(env, first, "Ringing")
    assert done.status_code == 200
    body = done.json()
    assert (body["status"], body["statusLabel"], body["statusText"]) == ("contacted", "Ringing", "Ringing")
    row = lead(env, first)
    assert (row.status, row.status_label) == ("contacted", "Ringing")
    assert patch(env, second, "no incoming calls").json()["statusLabel"] == "No incoming calls"      # any letter case, stored as the template spells it
    not_eligible = patch(env, third, "Not eligible").json()
    assert (not_eligible["status"], not_eligible["statusLabel"]) == ("not_interested", "Not eligible")
    detail = env.client.get(f"/api/leads/{first}").json()
    activity = next(a for a in detail["activities"] if a["kind"] == "status")
    assert activity["summary"] == "Status changed from Not contacted to Ringing"
    assert activity["meta"] == {"from": "new", "to": "contacted", "fromLabel": None, "toLabel": "Ringing", "attempt": True}
    # the same choice again changes nothing and writes no second history line
    assert patch(env, first, "Ringing").status_code == 200
    assert len([a for a in env.client.get(f"/api/leads/{first}").json()["activities"] if a["kind"] == "status"]) == 1


def test_a_custom_template_counts_as_its_tone_says(env):
    ids = six_leads(env)
    add(env, "Busy later")
    add(env, "Visa ready", tone="positive")
    add(env, "Not for us", tone="negative")
    assert patch(env, ids[0], "Busy later").json()["status"] == "contacted"
    assert patch(env, ids[1], "Visa ready").json()["status"] == "interested"
    done = patch(env, ids[2], "Not for us").json()
    assert (done["status"], done["statusLabel"]) == ("not_interested", "Not for us")
    assert patch(env, ids[3], "Nope").status_code == 422                   # not a status at all
    assert patch(env, ids[3], "converted").status_code == 422
    assert patch(env, ids[3], "interested").json()["statusLabel"] is None


def test_a_built_in_status_ends_the_template_and_a_call_that_connects_ends_an_attempt(env):
    ids = six_leads(env)
    add(env, "Busy later")
    patch(env, ids[0], "Ringing")
    patch(env, ids[1], "Busy later")
    patch(env, ids[2], "Ringing")
    # back to the plain status of the same code: same counting code, the label goes
    changed = patch(env, ids[0], "contacted").json()
    assert (changed["status"], changed["statusLabel"], changed["statusText"]) == ("contacted", None, "Contacted")
    act_as(env, "a")
    # "Spoke to them": Ringing was an attempt, so it ends; a neutral template stays
    assert env.client.post(f"/api/leads/{ids[2]}/call", json={"outcome": "answered"}).json()["statusLabel"] is None
    assert env.client.post(f"/api/leads/{ids[1]}/call", json={"outcome": "answered"}).json()["statusLabel"] == "Busy later"
    # a call outcome that sets another status replaces the label
    patch(env, ids[3], "Ringing")
    after = env.client.post(f"/api/leads/{ids[3]}/call", json={"outcome": "interested"}).json()
    assert (after["status"], after["statusLabel"]) == ("interested", None)
    patch(env, ids[4], "Not eligible")
    wrong = env.client.post(f"/api/leads/{ids[4]}/call", json={"outcome": "wrong_number"}).json()
    assert (wrong["status"], wrong["statusLabel"]) == ("wrong_number", None)


def test_a_staff_member_cannot_set_a_status_on_a_lead_that_is_not_theirs(env):
    ids = six_leads(env, owner="b")
    assert patch(env, ids[0], "Ringing", as_user="a").status_code == 404
    assert patch(env, ids[0], "Ringing", as_user="b").status_code == 200


def test_a_hidden_or_deleted_template_keeps_the_leads_status_text_but_cannot_be_chosen(env):
    ids = six_leads(env)
    busy = add(env, "Busy later").json()["id"]
    patch(env, ids[0], "Busy later")
    patch(env, ids[1], "Busy later")
    act_as(env, "admin")
    env.client.patch(f"{URL}/{busy}", json={"active": False})
    assert lead(env, ids[0]).status_label == "Busy later"
    assert patch(env, ids[2], "Busy later").status_code == 422             # switched off: nobody can pick it any more
    assert patch(env, ids[0], "Busy later").status_code == 200              # the lead that holds it may keep it
    shown = env.client.get(f"/api/leads/{ids[0]}").json()
    assert (shown["statusLabel"], shown["statusText"], shown["status"]) == ("Busy later", "Busy later", "contacted")
    facets = {s["value"]: s for s in env.client.get("/api/leads/facets").json()["statuses"]}
    assert facets["Busy later"]["count"] == 2                               # still filterable while leads hold it
    env.client.delete(f"{URL}/{busy}")
    assert lead(env, ids[1]).status_label == "Busy later"
    assert env.client.get("/api/leads", params={"status": "Busy later"}).json()["total"] == 2
    facets = {s["value"]: s for s in env.client.get("/api/leads/facets").json()["statuses"]}
    assert facets["Busy later"]["count"] == 2 and facets["Busy later"]["kind"] == "template"


def test_renaming_a_template_renames_it_on_the_leads(env):
    ids = six_leads(env)
    busy = add(env, "Busy later").json()["id"]
    patch(env, ids[0], "Busy later")
    act_as(env, "manager")
    assert env.client.patch(f"{URL}/{busy}", json={"name": "Call tomorrow"}).status_code == 200
    assert lead(env, ids[0]).status_label == "Call tomorrow"
    assert env.client.get("/api/leads", params={"status": "call tomorrow"}).json()["total"] == 1


# ------------------------------------------------------------------ lists, filters, facets, export

def test_filters_search_facets_and_export_include_the_new_statuses(env):
    ids = six_leads(env)
    add(env, "Visa ready", tone="positive")
    for lead_id, status in zip(ids, ("Ringing", "Ringing", "Not eligible", "Visa ready", "interested", "contacted")):
        assert patch(env, lead_id, status).status_code == 200
    act_as(env, "admin")

    def total(**params):
        return env.client.get("/api/leads", params={"page_size": 100, **params}).json()["total"]

    assert total(status="Ringing") == 2 and total(status="ringing") == 2
    assert total(status="Not eligible") == 1 and total(status="Visa ready") == 1
    assert total(status="Ringing,Not eligible") == 3
    assert total(status="Ringing,interested") == 4                          # a name and a code in one filter
    assert total(status="contacted") == 3                                   # counts as contacted: Ringing x2 and the plain one
    assert total(status="interested") == 2                                  # Visa ready counts as interested
    assert total(status="not_interested") == 1
    assert total() == 6
    assert env.client.get("/api/leads", params={"status": "bogus"}).status_code == 422
    assert env.client.get("/api/leads", params={"status": "Ringing,bogus"}).status_code == 422
    assert total(q="ringing") == 2 and total(q="eligible") == 1             # the search box finds a status by its text
    items = env.client.get("/api/leads", params={"status": "Ringing"}).json()["items"]
    assert {(i["status"], i["statusLabel"]) for i in items} == {("contacted", "Ringing")}

    statuses = env.client.get("/api/leads/facets").json()["statuses"]
    assert [s["value"] for s in statuses] == ["new", "contacted", "interested", "resume_requested", "resume_received", "converted", "not_interested",
                                              "invalid", "wrong_number", "Ringing", "No incoming calls", "Not eligible", "Visa ready"]
    assert {s["value"]: s["count"] for s in statuses} == {"new": 0, "contacted": 3, "interested": 2, "resume_requested": 0, "resume_received": 0,
                                                         "converted": 0, "not_interested": 1, "invalid": 0, "wrong_number": 0, "Ringing": 2,
                                                         "No incoming calls": 0, "Not eligible": 1, "Visa ready": 1}
    assert [s["kind"] for s in statuses][:9] == ["builtin"] * 9 and statuses[9]["builtin"] is True and statuses[-1]["tone"] == "positive"
    act_as(env, "b")                                                        # another recruiter has none of these leads: counts are scoped
    assert {s["value"]: s["count"] for s in env.client.get("/api/leads/facets").json()["statuses"]}["Ringing"] == 0
    act_as(env, "a")
    assert {s["value"]: s["count"] for s in env.client.get("/api/leads/facets").json()["statuses"]}["Ringing"] == 2

    act_as(env, "admin")
    for url in ("/api/leads/export.csv?status=Ringing,Not%20eligible", "/api/leads/export.csv"):
        exported = env.client.get(url)
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert {r["Status"] for r in rows} <= {"Ringing", "Not eligible", "Visa ready", "Interested", "Contacted"}
    only = list(csv.DictReader(io.StringIO(env.client.get("/api/leads/export.csv?status=Ringing,Not%20eligible").content.decode("utf-8-sig"))))
    assert sorted(r["Status"] for r in only) == ["Not eligible", "Ringing", "Ringing"]
    selected = env.client.post("/api/leads/bulk", json={"action": "export", "ids": ids[:3]})
    assert selected.status_code == 200 and selected.headers["X-Lead-Count"] == "3"


def test_bulk_status_change_takes_a_template_name_too(env):
    ids = six_leads(env)
    add(env, "Busy later")
    act_as(env, "admin")
    done = env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids[:4], "status": "Ringing"})
    assert done.status_code == 200 and done.json()["updated"] == 4 and done.json()["status"] == "Ringing"
    assert {(r.status, r.status_label) for r in env.db.execute(select(m.Lead)).scalars()} == {("contacted", "Ringing"), ("new", None)}
    again = env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids[:4], "status": "ringing"}).json()
    assert again["updated"] == 0 and again["skippedReasons"][0]["code"] == "already_set"
    env.db.rollback()
    activity = env.db.scalars(select(m.LeadActivity).where(m.LeadActivity.lead_id == ids[0], m.LeadActivity.kind == "status")).one()
    assert activity.summary == "Status changed from Not contacted to Ringing"
    assert activity.meta == {"from": "new", "to": "contacted", "fromLabel": None, "toLabel": "Ringing", "attempt": True, "bulk": True}
    # a plain built-in status replaces the label; a bulk pick of the counting code alone also clears it
    assert env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids[:2], "status": "contacted"}).json()["updated"] == 2
    env.db.rollback()
    assert [r.status_label for r in env.db.scalars(select(m.Lead).where(m.Lead.id.in_(ids[:4])).order_by(m.Lead.name))] == [None, None, "Ringing", "Ringing"]
    assert env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids, "status": "Busy later"}).json()["updated"] == 6
    assert env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids, "status": "Nope"}).status_code == 422
    assert env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids, "status": "converted"}).status_code == 422
    # "not interested" in one go keeps working and drops the label
    assert env.client.post("/api/leads/bulk", json={"action": "not_interested", "ids": ids[:1]}).json()["updated"] == 1
    env.db.rollback()
    env.db.expire_all()
    assert (env.db.get(m.Lead, ids[0]).status, env.db.get(m.Lead, ids[0]).status_label) == ("not_interested", None)
    # a selection by filter ("every lead holding this name") acts on exactly those leads
    env.client.post("/api/leads/bulk", json={"action": "status", "ids": ids[1:3], "status": "Ringing"})
    moved = env.client.post("/api/leads/bulk", json={"action": "status", "filter": {"status": "Ringing"}, "status": "interested"})
    assert moved.status_code == 200 and moved.json()["updated"] == 2


# ------------------------------------------------------------------ dashboard and reports

def test_dashboard_and_staff_reports_count_the_new_statuses_by_the_rule(env):
    ids = six_leads(env)
    add(env, "Busy later")                                       # neutral: contacted, spoke
    add(env, "Visa ready", tone="positive")
    # Ringing, No incoming calls, Not eligible, Busy later, Visa ready; the sixth lead is left Not contacted
    for lead_id, status in zip(ids, ("Ringing", "No incoming calls", "Not eligible", "Busy later", "Visa ready")):
        assert patch(env, lead_id, status).status_code == 200
    now = datetime.now(timezone.utc)
    admin = SimpleNamespace(id="admin", name="Admin", role=m.Role.admin)

    cards = lead_metrics.dashboard_leads(env.db, admin, None, TZ, now)
    assert cards["total"] == 6
    assert cards["notContacted"] == 1                            # only the lead nobody touched: Ringing is a contact attempt, not "not contacted"
    assert cards["open"] == 5                                    # Not eligible is closed like Not interested; the other five stay open
    summary = lead_sheet.summary(env.db, admin)
    assert summary["byStatus"] == {"new": 1, "contacted": 3, "interested": 1, "not_interested": 1}
    assert summary["uncontacted"] == 1 and summary["interested"] == 1

    day = now.astimezone(ZoneInfo(TZ)).date()
    body = daily_work_report.build_report(env.db, admin, day, day, None, None, TZ, now)
    feedback = body["leadFeedback"]
    assert feedback["contacted"] == 5                            # every lead the recruiter set a status on counts as contacted ...
    asha = next(p for p in body["staff"] if p["id"] == "a")["leadFeedback"]
    assert asha["contacted"] == 5
    assert asha["noAnswer"] == 2                                 # ... Ringing and No incoming calls are attempts: not reached
    assert asha["interested"] == 1 and asha["notInterested"] == 1
    assert asha["other"] == 1                                    # the neutral one: spoke, no decision yet
    assert asha["noAnswer"] + asha["interested"] + asha["notInterested"] + asha["wrongNumber"] + asha["callback"] + asha["other"] == asha["contacted"]
    table = {row["key"]: row["count"] for row in body["leadSheet"]["byStatus"]}
    assert table["contacted"] == 3 and table["not_interested"] == 1 and table["interested"] == 1 and table["new"] == 1

    # the weekly report credits "interested" only to a real interested status, never to Ringing
    start, end = datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc)
    weekly = lead_metrics.weekly_lead_numbers(env.db, start, end, now, None)["a"]
    assert weekly["interested"] == 1 and weekly["open"] == 5


def test_clean_helpers_and_the_reserved_names_cover_every_standard_label():
    assert service.clean_name("  a   b ") == "a b"
    assert service.counts_as("warning") == "contacted" and service.counts_as("negative") == "not_interested"
    assert service.counts_as("positive") == "interested" and service.counts_as("neutral") == "contacted"
    for label in lead_sheet.STATUS_LABELS.values():
        assert label.lower() in service.BUILTIN_NAMES
    assert set(service.COUNTS_AS) == set(m.LEAD_STATUS_TONES)
    assert set(service.COUNTS_AS.values()) <= set(m.LEAD_STATUSES) - {"converted"}
    assert lead_sheet.status_text("contacted", "Ringing") == "Ringing" and lead_sheet.status_text("wrong_number") == "Wrong number"
