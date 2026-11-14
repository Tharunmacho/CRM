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
               {"platform": ["ig"], "created_from": "2026-11-01", "created_to": "2026-11-30"}, {"position": ["Kitchen Helper"]}]
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
    assert [(r.summary, r.meta["from"], r.meta["to"]) for r in rows] == [("Status changed from New to Not interested", "new", "not_interested")]
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
    assert summaries == ["Status changed from New to Not interested", "Status changed from Not interested to New"]


def test_staff_and_managers_may_only_change_the_status_of_their_own_leads(env):
    imported(env, meta_rows(0, 4), assign_to="a")
    imported(env, meta_rows(4, 6), assign_to="b")
    imported(env, meta_rows(6, 7))
    ids = all_ids(env)
    mine = all_ids(env, owner="a")
    assert len(mine) == 4 and len(ids) == 7
    act_as(env, "a")
    for action, extra in (("assign", {"ownerId": "a"}), ("delete", {}), ("export", {})):
        assert bulk(env, action=action, ids=mine, **extra).status_code == 403, action
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
    # a manager, like staff, changes only the leads assigned to them, by ids or by filter
    act_as(env, "manager")
    none_mine = bulk(env, action="status", status="interested", ids=ids)
    assert none_mine.status_code == 422 and none_mine.json()["detail"]["code"] == "invalid_request", "none of these leads exist for the manager"
    env.db.execute(text("UPDATE crm_leads SET owner_id = 'manager' WHERE id = :i"), {"i": mine[0]}); env.db.commit()
    one = bulk(env, action="status", status="interested", ids=ids).json()
    assert one["updated"] == 1 and {r["code"]: r["count"] for r in one["skippedReasons"]} == {"not_yours": 6}
    assert bulk(env, action="status", status="not_interested", filter={}, expectedCount=1).json()["updated"] == 1
    assert bulk(env, action="delete", ids=ids).status_code == 403, "delete is admin only"
    act_as(env, "admin")  # the admin reaches every lead
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
    for who in ("manager", "a"):
        act_as(env, who)
        assert env.client.delete(f"/api/leads/{first}").status_code == 403, who
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
