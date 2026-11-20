"""Lead sheet bulk actions and delete (POST /api/leads/bulk, DELETE /api/leads/{id}) on a disposable PostgreSQL.

Synthetic people only (tests/lead_sheet_fixtures.py). Needs TEST_DATABASE_URL like the other DB tests.
"""
from types import SimpleNamespace
import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.api.routers import candidates, files, lead_bulk, leads
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import change_feed
from app.services import lead_bulk as service
from lead_sheet_fixtures import lead_row, meta_export, meta_rows
from test_database_naming import _migrate, schema_database
from test_lead_sheet import PDF, act_as, add_candidate, daily_item


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
        for router in (leads.router, lead_bulk.router, files.router, candidates.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, engine=schema_database)


def imported(env, rows, **fields):
    form = {"dry_run": "false", **{k: str(v) for k, v in fields.items()}}
    response = env.client.post("/api/leads/import", files={"file": ("leads.csv", meta_export(rows), "text/csv")}, data=form)
    assert response.status_code == 200, response.text
    return response.json()


def all_ids(env, **params):
    env.db.rollback()
    return [i["id"] for i in env.client.get("/api/leads", params={"page_size": 1000, **params}).json()["items"]]


def by_status(env):
    env.db.rollback()
    return dict(env.db.execute(text("SELECT status, count(*) FROM crm_leads GROUP BY status")).all())


def bulk(env, **body):
    return env.client.post("/api/leads/bulk", json=body)


def writes(env, call):
    statements = []
    def watch(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(env.engine, "before_cursor_execute", watch)
    try:
        response = call()
    finally:
        event.remove(env.engine, "before_cursor_execute", watch)
    return response, statements


# ------------------------------------------------------------------ assign

def test_assign_five_thousand_leads_is_a_handful_of_set_based_statements(env):
    imported(env, meta_rows(0, 5000))
    response, statements = writes(env, lambda: bulk(env, action="assign", filter={"owner": "unassigned"}, ownerId="auto", expectedCount=5000))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] == 5000 and body["skipped"] == 0 and {o["id"] for o in body["owners"]} == {"a", "b"}
    assert sorted(o["count"] for o in body["owners"]) == [2500, 2500]
    assert len(statements) <= 24, f"{len(statements)} statements for 5,000 leads"
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(m.LeadActivity).where(m.LeadActivity.kind == "assign")) == 5000
    assert env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.owner_id.is_(None))) == 0
    assert env.db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.kind == "lead_assignment")) == 2
    # nothing left to move: everything is skipped as already owned, no error
    again = bulk(env, action="assign", filter={}, ownerId="a").json()
    assert again["updated"] == 2500 and again["skipped"] == 2500
    assert again["skippedReasons"] == [{"code": "already_owner", "count": 2500, "message": "already assigned to that person"}]


def test_assign_to_a_person_validates_and_skips_converted(env):
    imported(env, meta_rows(0, 6))
    ids = all_ids(env)
    existing = add_candidate(env, "9555555555")
    assert env.client.post(f"/api/leads/{ids[0]}/link-candidate", json={"candidateId": existing.id}).status_code == 200
    assert bulk(env, action="assign", ids=ids, ownerId="away").status_code == 422
    assert bulk(env, action="assign", ids=ids).status_code == 422
    done = bulk(env, action="assign", ids=ids + ["no-such-lead"], ownerId="b").json()
    assert done["updated"] == 5 and done["skipped"] == 2
    assert {r["code"]: r["count"] for r in done["skippedReasons"]} == {"gone": 1, "converted": 1}
    env.db.rollback()
    assert env.db.scalar(select(m.Lead.owner_id).where(m.Lead.id == ids[0])) is None, "a converted lead is never moved"
    only = bulk(env, action="assign", ids=ids, ownerId="a", onlyUnassigned=True).json()
    assert only["updated"] == 0 and {r["code"] for r in only["skippedReasons"]} == {"converted", "already_owner"}


# ------------------------------------------------------------------ filter-based selection is the list's result set

def test_filter_selection_equals_the_list_result_set(env):
    imported(env, meta_rows(0, 120), assign_to="a")
    imported(env, meta_rows(120, 200))
    filters = [{"q": "Testville 3"}, {"status": ["new"], "owner": "unassigned"}, {"owner": "a", "q": "kerala"},
               {"platform": ["ig"], "created_from": "2026-11-01", "created_to": "2026-11-30", "date_field": "created"}, {"position": ["Kitchen Helper"]}]
    for filt in filters:
        params = {k: v for k, v in filt.items()}
        env.db.execute(text("UPDATE crm_leads SET status = 'new'")); env.db.commit()
        expected = set(all_ids(env, **params))
        assert expected, filt
        response = bulk(env, action="status", status="contacted", filter=filt, expectedCount=len(expected))
        assert response.status_code == 200, (filt, response.text)
        env.db.rollback()
        changed = set(env.db.scalars(select(m.Lead.id).where(m.Lead.status == "contacted")))
        assert changed == expected, filt
        assert response.json()["updated"] == len(expected)
    # a stale confirmation count is refused and changes nothing
    env.db.execute(text("UPDATE crm_leads SET status = 'new'")); env.db.commit()
    stale = bulk(env, action="status", status="contacted", filter={"q": "Testville 3"}, expectedCount=3)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "selection_changed"
    assert by_status(env) == {"new": 200}
    assert bulk(env, action="status", status="contacted", filter={"nonsense": 1}).status_code == 422
    assert bulk(env, action="status", status="contacted", filter={"status": ["bogus"]}).status_code == 422
    assert bulk(env, action="status", status="contacted").status_code == 422


# ------------------------------------------------------------------ status

def test_status_change_logs_activity_skips_converted_and_handles_stale_rows(env):
    imported(env, meta_rows(0, 8))
    ids = all_ids(env)
    existing = add_candidate(env, "9666666666")
    assert env.client.post(f"/api/leads/{ids[0]}/link-candidate", json={"candidateId": existing.id}).status_code == 200
    assert env.client.patch(f"/api/leads/{ids[1]}", json={"status": "not_interested"}).status_code == 200  # changed after the list was loaded
    response = bulk(env, action="not_interested", ids=ids)
    body = response.json()
    assert response.status_code == 200 and body["updated"] == 6 and body["skipped"] == 2 and body["status"] == "not_interested"
    assert {r["code"]: r["count"] for r in body["skippedReasons"]} == {"converted": 1, "already_set": 1}
    assert by_status(env) == {"converted": 1, "not_interested": 7}
    env.db.rollback()
    rows = env.db.scalars(select(m.LeadActivity).where(m.LeadActivity.kind == "status", m.LeadActivity.lead_id == ids[2])).all()
    assert [(r.summary, r.meta["from"], r.meta["to"]) for r in rows] == [("Status changed from Not contacted to Not interested", "new", "not_interested")]
    assert bulk(env, action="status", status="converted", ids=ids).status_code == 422
    assert bulk(env, action="status", status="bogus", ids=ids).status_code == 422
    assert bulk(env, action="status", ids=ids).status_code == 422
    assert bulk(env, action="status", status="interested", ids=[]).status_code == 422
    audit = env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == "Bulk status change")).all()
    assert len(audit) == 1 and audit[0].meta == {"to": "not_interested", "count": 6}


def test_undo_is_a_patch_with_the_previous_status_and_logs_both(env):
    imported(env, meta_rows(0, 1))
    lead_id = all_ids(env)[0]
    assert env.client.patch(f"/api/leads/{lead_id}", json={"status": "not_interested"}).status_code == 200
    assert env.client.patch(f"/api/leads/{lead_id}", json={"status": "new"}).json()["status"] == "new"
    env.db.rollback()
    summaries = [a.summary for a in env.db.scalars(select(m.LeadActivity).where(m.LeadActivity.lead_id == lead_id, m.LeadActivity.kind == "status").order_by(m.LeadActivity.created_at))]
    assert summaries == ["Status changed from Not contacted to Not interested", "Status changed from Not interested to Not contacted"]


def test_staff_may_only_change_the_status_of_their_own_leads_and_an_md_reaches_every_lead(env):
    imported(env, meta_rows(0, 4), assign_to="a")
    imported(env, meta_rows(4, 6), assign_to="b")
    imported(env, meta_rows(6, 7))
    ids = all_ids(env)
    mine = all_ids(env, owner="a")
    assert len(mine) == 4 and len(ids) == 7
    act_as(env, "a")
    for action, extra in (("assign", {"ownerId": "a"}), ("delete", {})):
        assert bulk(env, action=action, ids=mine, **extra).status_code == 403, action
    # A recruiter may export, and only the leads allocated to them come out (the other three of the seven never resolve).
    own_export = bulk(env, action="export", ids=ids)
    assert own_export.status_code == 200 and own_export.headers["x-lead-count"] == "4"
    mixed = bulk(env, action="status", status="contacted", ids=ids)
    assert mixed.status_code == 200 and mixed.json()["updated"] == 4 and mixed.json()["skippedReasons"] == [{"code": "not_yours", "count": 3, "message": "not assigned to you"}]
    by_filter = bulk(env, action="status", status="interested", filter={"owner": "unassigned"}, expectedCount=4)
    assert by_filter.status_code == 200 and by_filter.json()["updated"] == 4, "a recruiter's filter never reaches leads that are not theirs"
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.owner_id.is_(None), m.Lead.status == "new")) == 1
    assert env.db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.status == "interested", m.Lead.owner_id == "a")) == 4
    # open daily tasks never lock the lead sheet: a recruiter's bulk status change on their own leads goes through
    daily_item(env, "a", key="bulk")
    daily_item(env, "a", days_ago=1, key="bulk-late")
    unlocked = bulk(env, action="status", status="contacted", ids=mine)
    assert unlocked.status_code == 200 and unlocked.json()["updated"] == 4, unlocked.text
    # an MD has everything an admin has: every lead, by ids or by filter, with nothing skipped as "not yours"
    act_as(env, "manager")
    everything = bulk(env, action="status", status="interested", ids=ids)
    assert everything.status_code == 200 and everything.json()["updated"] == 7 and everything.json()["skippedReasons"] == []
    assert bulk(env, action="status", status="not_interested", filter={}, expectedCount=7).json()["updated"] == 7
    act_as(env, "admin")  # the admin reaches every lead too
    assert bulk(env, action="status", status="resume_requested", ids=ids).json()["updated"] == 7


# ------------------------------------------------------------------ caps

def test_hard_cap_per_call(env, monkeypatch):
    imported(env, meta_rows(0, 9))
    too_many_ids = [f"id-{n}" for n in range(service.BULK_LIMIT + 1)]
    response = bulk(env, action="status", status="contacted", ids=too_many_ids)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "too_many_leads" and response.json()["detail"]["limit"] == 10000
    assert service.BULK_LIMIT == 10_000
    monkeypatch.setattr(service, "BULK_LIMIT", 5)
    for call in (dict(action="status", status="contacted"), dict(action="export"), dict(action="assign", ownerId="a")):
        over = bulk(env, filter={}, **call)
        assert over.status_code == 422 and over.json()["detail"]["code"] == "too_many_leads", call
    assert bulk(env, action="status", status="contacted", filter={"q": "00001"}).json()["updated"] == 1
    assert by_status(env).get("contacted") == 1


# ------------------------------------------------------------------ export selected

def sheet_xml(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read("xl/worksheets/sheet1.xml").decode()


def test_export_selected_uses_the_styled_workbook_with_custom_columns(env):
    imported(env, [lead_row(n) for n in range(1, 6)])
    ids = all_ids(env)
    response = bulk(env, action="export", ids=ids[:2])
    assert response.status_code == 200 and response.content[:2] == b"PK" and response.headers["x-lead-count"] == "2"
    assert "spreadsheetml" in response.headers["content-type"] and "attachment" in response.headers["content-disposition"]
    xml = sheet_xml(response.content)
    assert xml.count("<row ") == 3 and "Name" in xml and "Test Lead" in xml
    everyone = bulk(env, action="export", filter={"q": "Testville"}, expectedCount=5, sort="name", direction="asc")
    assert everyone.headers["x-lead-count"] == "5" and sheet_xml(everyone.content).count("<row ") == 6
    assert bulk(env, action="export", filter={"q": "zzzz-nothing"}).status_code == 422
    assert bulk(env, action="export", filter={}, expectedCount=1).status_code == 409
    env.db.rollback()
    assert by_status(env) == {"new": 5}


def test_selected_export_keeps_literal_experience_and_question_context(env):
    from lead_sheet_fixtures import csv_bytes
    from test_lead_experience_answers import excel_rows
    question = "Do you have 5+ years of experience with a relevant qualification?"
    response = env.client.post("/api/leads/import", files={"file": ("answers.csv", csv_bytes([
        ["Synthetic Answer A", "9000070071", "Yes"], ["Synthetic Answer B", "9000070072", "No"],
        ["Synthetic Unselected", "9000070073", "Yes"],
    ], headers=["Name", "Phone", question]), "text/csv")})
    assert response.status_code == 200
    ids = all_ids(env, sort="name", direction="asc")
    exported = bulk(env, action="export", ids=ids[:2])
    assert exported.status_code == 200
    rows = excel_rows(exported.content)
    header = [cell[0] for cell in rows[0]]
    assert len(rows) == 3
    assert {row[header.index("Experience (total)")][0] for row in rows[1:]} == {"Yes", "No"}
    assert all(row[header.index("Total experience question")][0] == question for row in rows[1:])
    env.db.rollback()
    assert by_status(env) == {"new": 3}


# ------------------------------------------------------------------ delete

def test_delete_one_is_admin_only_refuses_linked_leads_and_audits(env):
    imported(env, meta_rows(0, 3), assign_to="a")
    first, second, third = all_ids(env)
    existing = add_candidate(env, "9777777777")
    assert env.client.post(f"/api/leads/{second}/link-candidate", json={"candidateId": existing.id}).status_code == 200
    env.client.patch(f"/api/leads/{first}", json={"notes": "called twice"})
    assert env.client.post("/api/files", data={"entity_type": "lead", "entity_id": first, "slot": "resume"}, files={"file": ("cv.pdf", PDF, "application/pdf")}).status_code == 201
    name = env.db.scalar(select(m.Lead.name).where(m.Lead.id == first))
    act_as(env, "a")
    assert env.client.delete(f"/api/leads/{first}").status_code == 403
    act_as(env, "manager")   # an MD may delete a lead like an admin (the linked lead is refused for the same reason as for an admin)
    assert env.client.delete(f"/api/leads/{second}").status_code == 409
    act_as(env, "admin")
    linked = env.client.delete(f"/api/leads/{second}")
    assert linked.status_code == 409 and linked.json()["detail"]["code"] == "lead_linked"
    assert existing.ref in linked.json()["detail"]["message"] and "cannot be deleted" in linked.json()["detail"]["message"]
    assert env.client.delete("/api/leads/no-such-lead").status_code == 404
    response, statements = writes(env, lambda: env.client.delete(f"/api/leads/{first}"))
    assert response.status_code == 200 and response.json() == {"deleted": 1, "id": first}
    assert len(statements) <= 12
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(m.Lead)) == 2
    assert env.db.scalar(select(func.count()).select_from(m.LeadActivity).where(m.LeadActivity.lead_id == first)) == 0, "activities go with the lead"
    assert env.db.scalar(select(func.count()).select_from(m.Attachment).where(m.Attachment.entity_type == "lead", m.Attachment.entity_id == first)) == 0
    entry = env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == "Deleted lead")).one()
    assert (entry.actor_id, entry.entity_type, entry.entity_id) == ("admin", "lead", first) and entry.target == name
    assert entry.meta["status"] == "resume_received" and entry.meta["bulk"] is False
    assert env.client.get(f"/api/leads/{first}").status_code == 404
    # the leads list and the batch's remaining count agree
    batch = env.client.get("/api/leads/batches").json()["items"][0]
    assert batch["leadsRemaining"] == 2 and batch["rowsNew"] == 3


def test_bulk_delete_needs_the_typed_word_above_twenty_and_skips_linked(env):
    imported(env, meta_rows(0, 30))
    ids = all_ids(env)
    existing = add_candidate(env, "9888888888")
    assert env.client.post(f"/api/leads/{ids[0]}/link-candidate", json={"candidateId": existing.id}).status_code == 200
    few = bulk(env, action="delete", ids=ids[1:6])
    assert few.status_code == 200 and few.json()["updated"] == 5 and few.json()["skipped"] == 0
    refused = bulk(env, action="delete", ids=ids[6:])
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "confirmation_required" and refused.json()["detail"]["count"] == 24
    assert bulk(env, action="delete", ids=ids[6:], confirm="delete").status_code == 422
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(m.Lead)) == 25
    done = bulk(env, action="delete", ids=ids, confirm="DELETE", filter=None)
    assert done.status_code == 200
    body = done.json()
    assert body["updated"] == 24 and body["skipped"] == 6 and {r["code"]: r["count"] for r in body["skippedReasons"]} == {"converted": 1, "gone": 5}
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(m.Lead)) == 1 and env.db.scalar(select(m.Lead.status)) == "converted"
    assert env.db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == "Deleted lead")) == 29
    summary = env.db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == "Bulk lead delete")).all()
    assert sorted(s.meta["count"] for s in summary) == [5, 24]
    # a re-upload of the same sheet brings the deleted people back (documented behaviour: no tombstone)
    again = imported(env, meta_rows(0, 30))
    assert again["rowsNew"] == 29 and again["rowsDuplicate"] == 1


def test_delete_by_filter_matches_the_list(env):
    imported(env, meta_rows(0, 40))
    expected = set(all_ids(env, q="Testville 2"))
    assert 0 < len(expected) <= 20
    response = bulk(env, action="delete", filter={"q": "Testville 2"}, expectedCount=len(expected))
    assert response.status_code == 200 and response.json()["updated"] == len(expected)
    assert set(all_ids(env)).isdisjoint(expected) and len(all_ids(env)) == 40 - len(expected)


# ------------------------------------------------------------------ live updates

def test_every_write_statement_is_classified_for_the_leads_topic(env):
    imported(env, meta_rows(0, 40))
    ids = all_ids(env)
    existing = add_candidate(env, "9999999999")
    assert env.client.post(f"/api/leads/{ids[-1]}/link-candidate", json={"candidateId": existing.id}).status_code == 200
    seen = []
    for body in (dict(action="assign", ids=ids, ownerId="auto"), dict(action="status", status="interested", ids=ids),
                 dict(action="delete", ids=ids[:25], confirm="DELETE")):
        response, statements = writes(env, lambda: bulk(env, **body))
        assert response.status_code == 200, response.text
        seen += statements
    classified = {change_feed.classify_statement(s) for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "WITH"))}
    assert ("UPDATE", "crm_leads") in classified and ("DELETE", "crm_leads") in classified and ("INSERT", "crm_lead_activities") in classified
    assert not [s for s in seen if s.lstrip().upper().startswith("WITH") and "crm_leads" in s], "a statement starting with WITH would not be announced"
    pending = change_feed.Pending()
    for verb, table in (c for c in classified if c):
        pending.note_table(verb, table)
    assert "leads" in pending.topics


# ------------------------------------------------------------------ a lead changing while the action runs

def test_a_lead_converted_by_someone_else_mid_action_is_skipped_not_an_error(env):
    import threading
    imported(env, meta_rows(0, 6))
    ids = all_ids(env)
    existing = add_candidate(env, "9123456789")
    other = env.engine.connect()
    other.execute(text("UPDATE crm_leads SET status = 'converted', candidate_id = :c WHERE id = :i"), {"c": existing.id, "i": ids[2]})
    timer = threading.Timer(1.0, other.commit)  # the other writer commits while the bulk request waits on the row lock
    timer.start()
    try:
        response = bulk(env, action="not_interested", ids=ids)
    finally:
        timer.join()
        other.close()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] == 5 and {r["code"]: r["count"] for r in body["skippedReasons"]} == {"converted": 1}
    assert by_status(env) == {"converted": 1, "not_interested": 5}


# ------------------------------------------------------------------ several recruiters share the leads equally

def add_recruiters(env, *ids):
    env.db.add_all([m.User(id=i, name=i.title(), email=f"{i}@example.test", role=m.Role.staff, receives_auto_assign=True) for i in ids])
    env.db.commit()


def owner_counts(env):
    env.db.rollback()
    return dict(env.db.execute(text("SELECT owner_id, count(*) FROM crm_leads WHERE owner_id IS NOT NULL GROUP BY owner_id")).all())


def owners_in_response(body):
    return {o["id"]: o["count"] for o in body["owners"]}


def test_split_rule_is_equal_remainder_to_the_fewest_open_leads_and_deterministic():
    from app.services.lead_import import split_equally
    people = [SimpleNamespace(id=i) for i in ("a", "b", "c")]

    def sizes(loads, count, order=people):
        picks = split_equally(order, loads, count)
        assert picks == split_equally(order, loads, count), "same input, same answer"
        return {p.id: picks.count(p.id) for p in people}
    assert sizes({}, 10) == {"a": 4, "b": 3, "c": 3}, "no workload difference: the first recruiter in the stable order gets the extra lead"
    assert sizes({"a": 5, "b": 0, "c": 2}, 10) == {"a": 3, "b": 4, "c": 3}, "the extra lead goes to the recruiter with the fewest open leads"
    assert sizes({"a": 5, "b": 0, "c": 2}, 11) == {"a": 3, "b": 4, "c": 4}, "two left over: the two with the fewest open leads"
    assert sizes({}, 2) == {"a": 1, "b": 1, "c": 0}
    assert sizes({}, 0) == {"a": 0, "b": 0, "c": 0}
    assert sizes({}, 9) == {"a": 3, "b": 3, "c": 3}
    assert split_equally(people[:2], {}, 7).count("a") == 4
    assert split_equally([], {}, 5) == []
    # leads are dealt round-robin, so newer and older leads are mixed between everyone
    assert split_equally(people, {}, 6) == ["a", "b", "c", "a", "b", "c"]
    for count in range(0, 40):
        got = [split_equally(people, {"a": 3, "b": 1, "c": 2}, count).count(p.id) for p in people]
        assert max(got) - min(got) <= (1 if count else 0) and sum(got) == count


def test_ten_leads_over_three_recruiters_is_four_three_three(env):
    add_recruiters(env, "c")
    imported(env, meta_rows(500, 502), assign_to="a")   # Asha already has 2 open leads
    imported(env, meta_rows(600, 601), assign_to="c")   # C has 1
    imported(env, meta_rows(0, 10))
    ids = all_ids(env, owner="unassigned")
    assert len(ids) == 10
    response = bulk(env, action="assign", ids=ids, assigneeIds=["c", "a", "b"])  # the order they were ticked in does not matter
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] == 10 and body["skipped"] == 0 and body["assigned"] == 10
    assert owners_in_response(body) == {"b": 4, "a": 3, "c": 3}, "Ravi has the fewest open leads, so he gets the extra one"
    assert all(o["name"] for o in body["owners"])
    assert owner_counts(env) == {"a": 2 + 3, "b": 4, "c": 1 + 3}
    env.db.rollback()
    per_lead = dict(env.db.execute(text("SELECT lead_id, count(*) FROM crm_lead_activities WHERE kind = 'assign' AND lead_id = ANY(CAST(:i AS text[])) GROUP BY lead_id"), {"i": ids}).all())
    assert set(per_lead) == set(ids) and set(per_lead.values()) == {1}, "one history entry per lead"
    notes = {n.user_id: n for n in env.db.scalars(select(m.Notification).where(m.Notification.kind == "lead_assignment", m.Notification.user_id.in_(["a", "b", "c"]),
                                                                                  m.Notification.title.like("%assigned to you")))}
    assert notes["b"].title == "4 leads assigned to you" and notes["b"].detail.startswith("Admin assigned you 4 leads")
    assert notes["a"].title == "3 leads assigned to you" and notes["c"].title == "3 leads assigned to you"


def test_same_selection_gives_the_same_split_every_time(env):
    add_recruiters(env, "c")
    imported(env, meta_rows(0, 10))
    ids = all_ids(env)
    first = bulk(env, action="assign", ids=ids, assigneeIds=["a", "b", "c"]).json()
    env.db.rollback()
    owners_first = dict(env.db.execute(text("SELECT id, owner_id FROM crm_leads")).all())
    env.db.execute(text("UPDATE crm_leads SET owner_id = NULL, assigned_at = NULL"))  # back to the starting point: same leads, same workloads
    env.db.commit()
    second = bulk(env, action="assign", ids=ids, assigneeIds=["c", "b", "a"]).json()  # ticked in another order
    env.db.rollback()
    assert dict(env.db.execute(text("SELECT id, owner_id FROM crm_leads")).all()) == owners_first, "every lead goes to the same recruiter as the first time"
    assert owners_in_response(first) == owners_in_response(second) == {"a": 4, "b": 3, "c": 3}


def test_two_recruiters_split_seven_leads_four_and_three(env):
    imported(env, meta_rows(0, 7))
    body = bulk(env, action="assign", ids=all_ids(env), assigneeIds=["a", "b"]).json()
    assert sorted(owners_in_response(body).values()) == [3, 4] and body["updated"] == 7
    assert owners_in_response(body) == {"a": 4, "b": 3}, "equal workload: the first recruiter in the stable order has the extra lead"
    assert owner_counts(env) == {"a": 4, "b": 3}


def test_one_chosen_recruiter_works_like_before_and_auto_still_works(env):
    imported(env, meta_rows(0, 5))
    ids = all_ids(env)
    one = bulk(env, action="assign", ids=ids, assigneeIds=["b"]).json()
    assert owners_in_response(one) == {"b": 5}
    again = bulk(env, action="assign", ids=ids, assigneeIds=["b"]).json()
    assert again["updated"] == 0 and again["skippedReasons"] == [{"code": "already_owner", "count": 5, "message": "already assigned to that person"}]
    auto = bulk(env, action="assign", ids=ids, ownerId="auto").json()
    assert set(owners_in_response(auto)) <= {"a", "b"} and auto["updated"] + auto["skipped"] == 5


def test_converted_leads_are_never_moved_by_a_shared_assignment(env):
    add_recruiters(env, "c")
    imported(env, meta_rows(0, 7))
    ids = all_ids(env)
    existing = add_candidate(env, "9555555500")
    assert env.client.post(f"/api/leads/{ids[0]}/link-candidate", json={"candidateId": existing.id}).status_code == 200
    body = bulk(env, action="assign", ids=ids, assigneeIds=["a", "b", "c"]).json()
    assert body["updated"] == 6 and sorted(owners_in_response(body).values()) == [2, 2, 2], "only the six movable leads are shared"
    assert {r["code"]: r["count"] for r in body["skippedReasons"]} == {"converted": 1}
    env.db.rollback()
    assert env.db.scalar(select(m.Lead.owner_id).where(m.Lead.id == ids[0])) is None


def test_a_bad_choice_of_recruiters_is_refused_in_plain_english_and_changes_nothing(env):
    add_recruiters(env, "c")
    imported(env, meta_rows(0, 6))
    ids = all_ids(env)
    cases = {
        "on leave": (["a", "away"], "Away is on leave"),
        "unknown id": (["a", "no-such-user"], "not found"),
        "twice": (["a", "b", "a"], "chosen twice"),
        "manager": (["a", "manager"], "not an active recruiter"),
        "admin": (["admin"], "not an active recruiter"),
        "empty": ([], "Choose at least one recruiter"),
    }
    for label, (chosen, words) in cases.items():
        response = bulk(env, action="assign", ids=ids, assigneeIds=chosen)
        assert response.status_code == 422, label
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_request" and words in detail["message"], (label, detail)
    both = bulk(env, action="assign", ids=ids, ownerId="a", assigneeIds=["b"])
    assert both.status_code == 422 and "one recruiter, several recruiters" in both.json()["detail"]["message"]
    assert bulk(env, action="assign", ids=ids, ownerId="none", assigneeIds=["b"]).status_code == 422
    env.db.execute(text("UPDATE auth_users SET active = false WHERE id = 'c'"))
    env.db.commit()
    assert bulk(env, action="assign", ids=ids, assigneeIds=["a", "c"]).status_code == 422
    assert owner_counts(env) == {}
    assert env.db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.kind == "lead_assignment")) == 0


def test_only_admin_level_roles_may_share_leads_between_recruiters(env):
    add_recruiters(env, "c")
    imported(env, meta_rows(0, 4))
    ids = all_ids(env)
    act_as(env, "a")
    assert bulk(env, action="assign", ids=ids, assigneeIds=["a", "b"]).status_code == 403
    assert env.client.post("/api/leads/assign", json={"ids": ids, "assigneeIds": ["a", "b"]}).status_code == 403
    assert owner_counts(env) == {}
    act_as(env, "manager")   # an MD shares leads exactly as an admin does
    assert bulk(env, action="assign", ids=ids, assigneeIds=["a", "b"]).status_code == 200
    assert owner_counts(env) == {"a": 2, "b": 2}
    act_as(env, "admin")


def test_assign_endpoint_shares_leads_equally_and_by_filter(env):
    add_recruiters(env, "c")
    imported(env, meta_rows(0, 8))
    ids = all_ids(env)
    first = env.client.post("/api/leads/assign", json={"ids": ids[:4], "assigneeIds": ["a", "b"]})
    assert first.status_code == 200, first.text
    assert owners_in_response(first.json()) == {"a": 2, "b": 2} and first.json()["assigned"] == 4
    rest = env.client.post("/api/leads/assign", json={"filter": {"owner": "unassigned"}, "assigneeIds": ["a", "b", "c"], "onlyUnassigned": True})
    assert rest.status_code == 200, rest.text
    assert sum(owners_in_response(rest.json()).values()) == 4 and max(owners_in_response(rest.json()).values()) <= 2
    bad = env.client.post("/api/leads/assign", json={"ids": ids, "assigneeIds": ["away"]})
    assert bad.status_code == 422 and "Away is on leave" in bad.json()["detail"]["message"]
    neither = env.client.post("/api/leads/assign", json={"ids": ids})
    assert neither.status_code == 422
    old = env.client.post("/api/leads/assign", json={"ids": ids, "ownerId": "b"})  # an older open tab still sends one id
    assert old.status_code == 200 and old.json()["assigned"] + old.json()["skipped"] == 8


def test_upload_import_shares_new_leads_between_several_recruiters(env):
    import json as jsonlib
    add_recruiters(env, "c")
    result = imported(env, meta_rows(0, 7), assignee_ids=jsonlib.dumps(["c", "a", "b"]))
    assert result["rowsNew"] == 7 and result["assignedCount"] == 7 and result["assignedTo"] == "multiple"
    assert {o["id"]: o["count"] for o in result["assignedOwners"]} == {"a": 3, "b": 2, "c": 2}, "equal workload: the first in the stable order has the extra lead"
    assert owner_counts(env) == {"a": 3, "b": 2, "c": 2}
    env.db.rollback()
    assert env.db.scalar(select(m.LeadBatch.assigned_to)) is None
    titles = {n.user_id: n.title for n in env.db.scalars(select(m.Notification).where(m.Notification.kind == "lead_assignment"))}
    assert titles == {"a": "3 new leads assigned to you", "b": "2 new leads assigned to you", "c": "2 new leads assigned to you"}
    summary = env.db.scalar(select(m.LeadActivity.summary).where(m.LeadActivity.kind == "import").limit(1))
    assert "evenly between 3 recruiters" in summary


def test_upload_import_with_several_recruiters_refuses_a_bad_choice_and_stores_nothing(env):
    import json as jsonlib
    from test_lead_sheet import upload
    add_recruiters(env, "c")

    def attempt(rows, **fields):
        return upload(env, meta_export(rows), **fields)
    for label, chosen, words in (("leave", ["a", "away"], "Away is on leave"), ("unknown", ["a", "ghost"], "not found"), ("twice", ["a", "a"], "chosen twice"),
                                 ("none", [], "at least one recruiter")):
        response = attempt(meta_rows(0, 3), assignee_ids=jsonlib.dumps(chosen))
        assert response.status_code == 422 and words in response.json()["detail"]["message"], label
    assert attempt(meta_rows(0, 3), assignee_ids="not json").status_code == 422
    assert attempt(meta_rows(0, 3), assignee_ids=jsonlib.dumps(["a", "b"]), assign_to="c").status_code == 422
    env.db.rollback()
    assert env.db.scalar(select(func.count()).select_from(m.Lead)) == 0
    act_as(env, "a")
    assert attempt(meta_rows(0, 3), assignee_ids=jsonlib.dumps(["a", "b"])).status_code == 403
    act_as(env, "admin")
    single = imported(env, meta_rows(0, 3), assignee_ids=jsonlib.dumps(["b"]))
    assert single["assignedTo"] == "b" and owner_counts(env) == {"b": 3}
    check = attempt(meta_rows(10, 14), dry_run=True, assignee_ids=jsonlib.dumps(["a", "b"]))
    assert check.status_code == 200 and check.json()["assignedCount"] == 4 and check.json()["dryRun"] is True
    assert owner_counts(env) == {"b": 3}, "a dry run saves nothing"
