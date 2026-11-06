"""Lead sheet extras on a disposable PostgreSQL: campaigns table, follow-up to-dos, late matching of candidates, WhatsApp templates.

Synthetic people only (tests/lead_sheet_fixtures.py). Needs TEST_DATABASE_URL like the other DB tests.
"""
from datetime import datetime, timedelta, timezone

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.api.routers import candidates, files, lead_campaigns, leads
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import lead_followups, lead_matching, lead_templates
from lead_sheet_fixtures import lead_row
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_lead_sheet import act_as, imported


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    """Same app and people as test_lead_sheet, plus the extras router (registered before the leads router, as in app/main.py)."""
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="manager", name="Manager", email="manager@example.test", role=m.Role.manager),
                    m.User(id="a", name="Asha", email="a@example.test", role=m.Role.staff, receives_auto_assign=True),
                    m.User(id="b", name="Ravi", email="b@example.test", role=m.Role.staff, receives_auto_assign=True)])
        db.commit()
        app = FastAPI()
        for router in (lead_campaigns.router, leads.router, files.router, candidates.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, engine=schema_database)

IST = timezone(timedelta(hours=5, minutes=30))


def lead_id_of(env, n: int) -> str:
    return env.db.scalar(select(m.Lead.id).where(m.Lead.external_id == str(1795000000000000 + n)))


def todos_of(env, lead_id: str):
    env.db.expire_all()
    return env.db.execute(text("SELECT owner_id, title, due_at, done, link_to FROM personal_todos WHERE link_label = :l AND link_to = :k ORDER BY created_at"),
                          {"l": lead_followups.LABEL, "k": lead_followups.LINK_PREFIX + lead_id}).all()


def open_todos(env, lead_id: str):
    return [row for row in todos_of(env, lead_id) if not row.done]


# ------------------------------------------------------------------ campaigns

def campaign_rows():
    rows = []
    for n in range(0, 10):
        rows.append(lead_row(n, campaign_name="Dubai-A", ad_name="Dubai-A", adset_name="Dubai-A", form_name="Form-A1"))
    for n in range(10, 16):
        rows.append(lead_row(n, campaign_name="Qatar-B", ad_name="Qatar-B", adset_name="Qatar-B", form_name="Form-B1"))
    for n in range(16, 18):
        rows.append(lead_row(n, campaign_name="Qatar-B", ad_name="Qatar-B", adset_name="Qatar-B", form_name="Form-B2"))
    for n in range(18, 20):
        rows.append(lead_row(n, campaign_name="Kuwait-C", ad_name="Kuwait-C", adset_name="Kuwait-C", form_name="Form-C1"))
    return rows


def seed_campaigns(env):
    imported(env, campaign_rows())
    db = env.db
    ids = {n: lead_id_of(env, n) for n in range(20)}
    # Dubai-A: six assigned to Asha; 2 contacted, 2 interested, 1 resume received, 1 converted; three arrived today, two 3 days ago, the rest 20 days ago.
    for n in range(0, 6):
        db.execute(text("UPDATE crm_leads SET owner_id = 'a' WHERE id = :i"), {"i": ids[n]})
    for n, status, calls in ((0, "contacted", 1), (1, "contacted", 2), (2, "interested", 1), (3, "interested", 1), (4, "resume_received", 2), (5, "converted", 3)):
        db.execute(text("UPDATE crm_leads SET status = :s, call_count = :c WHERE id = :i"), {"s": status, "c": calls, "i": ids[n]})
    db.execute(text("UPDATE crm_leads SET created_time = now() WHERE id = ANY(:ids)"), {"ids": [ids[n] for n in (0, 1, 2)]})
    db.execute(text("UPDATE crm_leads SET created_time = now() - interval '3 days' WHERE id = ANY(:ids)"), {"ids": [ids[n] for n in (3, 4)]})
    db.execute(text("UPDATE crm_leads SET created_time = now() - interval '20 days' WHERE id = ANY(:ids)"), {"ids": [ids[n] for n in range(5, 20)]})
    db.execute(text("UPDATE crm_leads SET platform = NULL WHERE id = :i"), {"i": ids[0]})  # one lead with no platform: "other"
    db.commit()
    return ids


def test_campaigns_aggregate_is_correct_and_sorted(env):
    seed_campaigns(env)
    act_as(env, "admin")
    body = env.client.get("/api/leads/campaigns")
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["scope"] == "all" and data["truncated"] is False
    assert [(r["campaign"], r["leads"]) for r in data["items"]] == [("Dubai-A", 10), ("Qatar-B", 8), ("Kuwait-C", 2)]
    dubai = data["items"][0]
    assert (dubai["newToday"], dubai["last7Days"]) == (3, 5)
    assert (dubai["assignedPct"], dubai["contactedPct"]) == (60.0, 60.0)
    assert (dubai["interested"], dubai["resumeReceived"], dubai["converted"], dubai["conversionRate"]) == (4, 1, 1, 10.0)
    assert dubai["platform"] == {"fb": 5, "ig": 4, "other": 1}
    assert dubai["topPositions"] == [{"name": "South Indian Chef (CDP)", "count": 4}, {"name": "Dosa & Parotta Master", "count": 3}, {"name": "Kitchen Helper", "count": 3}]
    assert dubai["topCountries"] == [{"name": "Dubai", "count": 10}]
    assert dubai["lastUploadAt"] and dubai["forms"][0]["form"] == "Form-A1" and dubai["forms"][0]["leads"] == 10
    qatar = data["items"][1]
    assert [(f["form"], f["leads"]) for f in qatar["forms"]] == [("Form-B1", 6), ("Form-B2", 2)]
    assert qatar["assignedPct"] == 0.0 and qatar["conversionRate"] == 0.0 and qatar["topCountries"] == [{"name": "Qatar", "count": 8}]
    total = data["total"]
    assert (total["leads"], total["converted"], total["newToday"]) == (20, 1, 3) and total["conversionRate"] == 5.0
    assert total["platform"]["fb"] + total["platform"]["ig"] + total["platform"]["other"] == 20


def test_campaigns_staff_sees_only_their_own_leads(env):
    seed_campaigns(env)
    act_as(env, "a")
    data = env.client.get("/api/leads/campaigns").json()
    assert data["scope"] == "mine"
    assert [(r["campaign"], r["leads"]) for r in data["items"]] == [("Dubai-A", 6)]
    assert data["items"][0]["assignedPct"] == 100.0 and data["total"]["leads"] == 6
    act_as(env, "b")
    assert env.client.get("/api/leads/campaigns").json()["items"] == []
    assert env.client.get("/api/leads/campaigns").json()["total"]["leads"] == 0


def test_campaigns_is_one_statement_for_any_number_of_campaigns(env):
    seed_campaigns(env)
    act_as(env, "admin")
    env.client.get("/api/leads/campaigns")  # warm: the signed-in user is read once
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(env.engine, "before_cursor_execute", record)
    try:
        assert env.client.get("/api/leads/campaigns").status_code == 200
    finally:
        event.remove(env.engine, "before_cursor_execute", record)
    assert len([s for s in seen if "crm_leads" in s]) == 1, seen


def test_campaigns_route_is_not_taken_for_a_lead_id(env):
    imported(env, [lead_row(1)])
    act_as(env, "admin")
    assert env.client.get("/api/leads/campaigns").json()["items"][0]["leads"] == 1
    assert env.client.get("/api/leads/not-a-lead").status_code == 404


# ------------------------------------------------------------------ follow-up to-dos

def test_follow_up_todo_is_created_moved_and_cleared(env):
    imported(env, [lead_row(1)], assign_to="a")
    lead_id = lead_id_of(env, 1)
    act_as(env, "a")
    assert env.client.patch(f"/api/leads/{lead_id}", json={"nextFollowUpAt": "2030-03-05T15:30:00+05:30"}).status_code == 200
    todo = open_todos(env, lead_id)
    assert len(todo) == 1
    assert todo[0].owner_id == "a" and todo[0].title == "Follow up lead: Test Lead 00001"
    assert todo[0].due_at.astimezone(IST) == datetime(2030, 3, 5, 9, 0, tzinfo=IST)
    # the same date again, and an unrelated edit: still exactly one
    env.client.patch(f"/api/leads/{lead_id}", json={"nextFollowUpAt": "2030-03-05T15:30:00+05:30"})
    env.client.patch(f"/api/leads/{lead_id}", json={"notes": "called twice"})
    assert len(todos_of(env, lead_id)) == 1
    # moved
    env.client.patch(f"/api/leads/{lead_id}", json={"nextFollowUpAt": "2030-03-09T10:00:00+05:30"})
    moved = open_todos(env, lead_id)
    assert len(moved) == 1 and moved[0].due_at.astimezone(IST) == datetime(2030, 3, 9, 9, 0, tzinfo=IST)
    # renamed: the title follows
    env.client.patch(f"/api/leads/{lead_id}", json={"name": "Renamed Person"})
    assert open_todos(env, lead_id)[0].title == "Follow up lead: Renamed Person"
    # cleared
    env.client.patch(f"/api/leads/{lead_id}", json={"nextFollowUpAt": None})
    assert todos_of(env, lead_id) == []


def test_follow_up_todo_from_a_call_and_closed_with_the_lead(env):
    imported(env, [lead_row(1), lead_row(2), lead_row(3)], assign_to="a")
    one, two, three = (lead_id_of(env, n) for n in (1, 2, 3))
    act_as(env, "a")
    env.client.post(f"/api/leads/{one}/call", json={"outcome": "answered", "nextFollowUpAt": "2030-04-01T11:00:00+05:30"})
    env.client.post(f"/api/leads/{two}/call", json={"outcome": "interested", "nextFollowUpAt": "2030-04-02T11:00:00+05:30"})
    env.client.post(f"/api/leads/{three}/call", json={"outcome": "no_answer", "nextFollowUpAt": "2030-04-03T11:00:00+05:30"})
    assert all(len(open_todos(env, i)) == 1 for i in (one, two, three))
    # a call that ends the lead closes the to-do; a new outcome without a date keeps it
    env.client.post(f"/api/leads/{one}/call", json={"outcome": "not_interested"})
    assert open_todos(env, one) == []
    env.client.post(f"/api/leads/{two}/call", json={"outcome": "answered"})
    assert len(open_todos(env, two)) == 1
    # invalid via PATCH closes it too; going back to an open status brings it back
    env.client.patch(f"/api/leads/{three}", json={"status": "invalid"})
    assert open_todos(env, three) == []
    env.client.patch(f"/api/leads/{three}", json={"status": "contacted"})
    assert len(open_todos(env, three)) == 1


def test_follow_up_todo_moves_with_the_owner_and_dies_with_the_candidate_conversion(env):
    imported(env, [lead_row(1), lead_row(2)], assign_to="a")
    one, two = lead_id_of(env, 1), lead_id_of(env, 2)
    act_as(env, "admin")
    for lead_id in (one, two):
        assert env.client.patch(f"/api/leads/{lead_id}", json={"nextFollowUpAt": "2030-05-01T09:30:00+05:30"}).status_code == 200
    assert open_todos(env, one)[0].owner_id == "a"
    reassigned = env.client.post("/api/leads/assign", json={"ids": [one], "ownerId": "b"})
    assert reassigned.json()["assigned"] == 1
    moved = open_todos(env, one)
    assert len(moved) == 1 and moved[0].owner_id == "b"
    assert open_todos(env, two)[0].owner_id == "a"
    # converting the lead closes its reminder
    done = env.client.post(f"/api/leads/{two}/convert")
    assert done.status_code == 200, done.text
    assert open_todos(env, two) == []


def test_follow_up_todo_not_recreated_after_it_was_ticked_off_and_unowned_leads_have_none(env):
    imported(env, [lead_row(1), lead_row(2)], assign_to="a")
    # unowned lead (uploaded without an owner) with a date: no reminder because nobody owns it
    act_as(env, "admin")
    imported(env, [lead_row(5)])
    free = lead_id_of(env, 5)
    env.client.patch(f"/api/leads/{free}", json={"nextFollowUpAt": "2030-06-01T09:00:00+05:30"})
    assert todos_of(env, free) == []
    # the owner ticks the to-do off: an unrelated edit does not create it again, a new date does
    one = lead_id_of(env, 1)
    env.client.patch(f"/api/leads/{one}", json={"nextFollowUpAt": "2030-06-01T09:00:00+05:30"})
    env.db.execute(text("UPDATE personal_todos SET done = true WHERE link_to = :k"), {"k": lead_followups.LINK_PREFIX + one})
    env.db.commit()
    env.client.patch(f"/api/leads/{one}", json={"notes": "x"})
    env.client.patch(f"/api/leads/{one}", json={"nextFollowUpAt": "2030-06-01T09:00:00+05:30"})
    assert len(todos_of(env, one)) == 1 and open_todos(env, one) == []
    env.client.patch(f"/api/leads/{one}", json={"nextFollowUpAt": "2030-06-02T09:00:00+05:30"})
    assert len(open_todos(env, one)) == 1 and len(todos_of(env, one)) == 2
    # assigning the free lead now creates its reminder
    env.client.post("/api/leads/assign", json={"ids": [free], "ownerId": "b"})
    assert open_todos(env, free)[0].owner_id == "b"


def test_follow_up_sync_is_set_based_and_summary_counts_the_day(env):
    imported(env, [lead_row(n) for n in range(1, 41)], assign_to="a")
    ids = [lead_id_of(env, n) for n in range(1, 41)]
    env.db.execute(text("UPDATE crm_leads SET next_follow_up_at = now() WHERE id = ANY(:ids)"), {"ids": ids[:30]})
    env.db.commit()
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(env.engine, "before_cursor_execute", record)
    try:
        result = lead_followups.sync(env.db, ids)
        env.db.commit()
    finally:
        event.remove(env.engine, "before_cursor_execute", record)
    assert result["created"] == 30 and result["removed"] == 0 and len([s for s in seen if "personal_todos" in s]) == 3
    assert lead_followups.sync(env.db, ids) == {"created": 0, "moved": 0, "removed": 0}  # idempotent
    act_as(env, "a")
    # a follow-up three days away is not due
    env.db.execute(text("UPDATE crm_leads SET next_follow_up_at = now() + interval '3 days' WHERE id = ANY(:ids)"), {"ids": ids[:5]})
    env.db.commit()
    summary = env.client.get("/api/leads/summary").json()
    assert summary["followUpsDue"] == 25
    assert env.client.get("/api/leads", params={"follow_up": "due", "page_size": 100}).json()["total"] == 25
    assert lead_followups.sync_all(env.db)["removed"] == 0


# ------------------------------------------------------------------ late matching

def flagged(env, lead_id):
    env.db.expire_all()
    return env.db.scalar(select(m.Lead.matched_candidate_id).where(m.Lead.id == lead_id))


def test_late_match_by_phone_email_and_phone_change_in_the_same_transaction(env):
    imported(env, [lead_row(n) for n in range(1, 6)])
    ids = {n: lead_id_of(env, n) for n in range(1, 6)}
    assert all(flagged(env, i) is None for i in ids.values())
    # a candidate with the lead's number, written differently, is flagged before the commit
    cand = m.Candidate(id="c-late-1", ref="CND-L0001", name="Late One", phone="090000 00002", source=m.Source.other)
    env.db.add(cand)
    env.db.flush()
    assert env.db.scalar(select(m.Lead.matched_candidate_id).where(m.Lead.id == ids[2])) == "c-late-1"
    env.db.commit()
    # e-mail match is case-insensitive
    env.db.add(m.Candidate(id="c-late-2", ref="CND-L0002", name="Late Two", phone="9888888888", email="LEAD3@Example.test", source=m.Source.other))
    env.db.commit()
    assert flagged(env, ids[3]) == "c-late-2"
    # a changed phone on an existing candidate flags the lead of the new number; an unrelated edit does nothing
    other = env.db.get(m.Candidate, "c-late-2")
    other.phone = "+91 90000 00004"
    env.db.commit()
    assert flagged(env, ids[4]) == "c-late-2"
    # a flag is never overwritten by a later candidate with the same number
    env.db.add(m.Candidate(id="c-late-3", ref="CND-L0003", name="Late Three", phone="9000000002", source=m.Source.other))
    env.db.commit()
    assert flagged(env, ids[2]) == "c-late-1" and flagged(env, ids[1]) is None and flagged(env, ids[5]) is None
    # the lead drawer / list shows the match, and the candidate page can find its lead
    act_as(env, "admin")
    row = env.client.get(f"/api/leads/{ids[2]}").json()
    assert row["matchedCandidateId"] == "c-late-1" and row["matchedCandidateRef"] == "CND-L0001"
    back = env.client.get("/api/leads/for-candidate/c-late-1").json()["leads"]
    assert [(b["id"], b["campaignName"], b["converted"]) for b in back] == [(ids[2], "Dubai-Chef-03-11", False)]


def test_late_match_is_one_statement_for_many_candidates(env):
    imported(env, [lead_row(n) for n in range(1, 21)])
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(env.engine, "before_cursor_execute", record)
    try:
        env.db.add_all([m.Candidate(id=f"c-bulk-{n}", ref=f"CND-B{n:04d}", name=f"Bulk {n}", phone=f"90000000{n:02d}", source=m.Source.other) for n in range(1, 21)])
        env.db.commit()
    finally:
        event.remove(env.engine, "before_cursor_execute", record)
    updates = [s for s in seen if s.startswith("UPDATE crm_leads")]
    assert len(updates) == 1, updates
    assert env.db.scalar(text("SELECT count(*) FROM crm_leads WHERE matched_candidate_id IS NOT NULL")) == 20


def test_backfill_catches_up_core_writes_and_is_repeatable(env):
    imported(env, [lead_row(n) for n in range(1, 4)])
    env.db.execute(text("INSERT INTO crm_candidates (id, ref, name, phone, email, source) VALUES ('c-core-1', 'CND-C0001', 'Core One', '+91 9000000001', NULL, 'other'), "
                        "('c-core-2', 'CND-C0002', 'Core Two', '9777777777', 'lead2@example.test', 'other')"))
    env.db.commit()
    assert env.db.scalar(text("SELECT count(*) FROM crm_leads WHERE matched_candidate_id IS NOT NULL")) == 0  # Core writes are not seen by the hook
    assert lead_matching.backfill(env.db) == 2
    env.db.commit()
    assert lead_matching.backfill(env.db) == 0
    assert flagged(env, lead_id_of(env, 1)) == "c-core-1" and flagged(env, lead_id_of(env, 2)) == "c-core-2" and flagged(env, lead_id_of(env, 3)) is None


def test_converting_a_lead_leaves_no_self_match(env):
    imported(env, [lead_row(1)], assign_to="a")
    lead_id = lead_id_of(env, 1)
    act_as(env, "a")
    done = env.client.post(f"/api/leads/{lead_id}/convert")
    assert done.status_code == 200, done.text
    body = env.client.get(f"/api/leads/{lead_id}").json()
    assert body["candidateId"] and body["matchedCandidateId"] is None
    back = env.client.get(f"/api/leads/for-candidate/{body['candidateId']}").json()["leads"]
    assert [(b["id"], b["converted"]) for b in back] == [(lead_id, True)]


# ------------------------------------------------------------------ WhatsApp templates

def test_default_templates_edit_reset_and_permissions(env):
    act_as(env, "a")
    body = env.client.get("/api/leads/wa-templates").json()
    assert [t["key"] for t in body["templates"]] == ["first_contact", "request_resume", "follow_up"]
    assert all(t["isDefault"] and "{name}" in t["body"] and "{position}" in t["body"] and "{company_name}" in t["body"] for t in body["templates"])
    assert body["placeholders"] == ["{name}", "{position}", "{company_name}"] and body["companyName"]
    assert env.client.put("/api/leads/wa-templates", json={"templates": [{"key": "follow_up", "title": "X", "body": "Hi {name}"}]}).status_code == 403
    act_as(env, "admin")
    saved = env.client.put("/api/leads/wa-templates", json={"templates": [{"key": "follow_up", "title": "Nudge", "body": "Hi {name}, still keen on {position}?"}]})
    assert saved.status_code == 200, saved.text
    nudge = next(t for t in saved.json()["templates"] if t["key"] == "follow_up")
    assert nudge["title"] == "Nudge" and nudge["body"] == "Hi {name}, still keen on {position}?" and nudge["isDefault"] is False
    act_as(env, "a")
    assert next(t for t in env.client.get("/api/leads/wa-templates").json()["templates"] if t["key"] == "follow_up")["title"] == "Nudge"
    act_as(env, "admin")
    assert env.client.put("/api/leads/wa-templates", json={"templates": [{"key": "follow_up", "title": "Z", "body": "Hi {nme}"}]}).status_code == 422
    assert env.client.put("/api/leads/wa-templates", json={"templates": [{"key": "bogus", "title": "Z", "body": "Hi"}]}).status_code == 422
    assert env.client.put("/api/leads/wa-templates", json={"templates": [{"key": "follow_up", "title": "Z", "body": "x" * 300}]}).status_code == 422
    reset = env.client.put("/api/leads/wa-templates", json={"templates": [{"key": "follow_up", "reset": True}]}).json()
    assert next(t for t in reset["templates"] if t["key"] == "follow_up")["isDefault"] is True
    assert lead_templates.stored(env.db) == {}
    # saving the default wording is the same as the default (no row is kept)
    default = lead_templates.DEFAULTS[0]
    env.client.put("/api/leads/wa-templates", json={"templates": [{"key": default["key"], "title": default["title"], "body": default["body"]}]})
    assert lead_templates.stored(env.db) == {}
    # the candidate form's option lists never show the templates
    from app.services import form_options
    assert lead_templates.CATEGORY not in {c["key"] for c in form_options.all_options(env.db)["categories"]}


def test_whatsapp_open_is_logged_in_the_history_for_people_who_can_work_the_lead(env):
    imported(env, [lead_row(1)], assign_to="a")
    lead_id = lead_id_of(env, 1)
    act_as(env, "b")
    assert env.client.post(f"/api/leads/{lead_id}/whatsapp", json={"template": "first_contact", "message": "Hello"}).status_code == 403
    act_as(env, "a")
    assert env.client.post(f"/api/leads/{lead_id}/whatsapp", json={"template": "first_contact", "message": "Hello   Test\nLead"}).json() == {"ok": True}
    detail = env.client.get(f"/api/leads/{lead_id}").json()
    entry = next(a for a in detail["activities"] if a["meta"] and a["meta"].get("kind") == "whatsapp")
    assert entry["kind"] == "note" and entry["summary"] == "WhatsApp message opened: Hello Test Lead" and entry["meta"]["template"] == "first_contact"
    assert env.client.post("/api/leads/missing/whatsapp", json={"message": "x"}).status_code == 404
