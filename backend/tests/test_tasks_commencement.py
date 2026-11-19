"""Tasks page additions: the date of commencement (DOC), the remark templates and the Tasks Excel (disposable PostgreSQL).

Every name, client, agency and amount below is invented. Time is frozen at Tuesday 08:30 IST (2026-11-03 03:00 UTC).
"""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, Client, ClosureReason, Company, Designation, Order,
    RemarkTemplate, Remark, Role, Source, Task, TaskKind, User,
)
from app.services import daily_work, tasks_export
from test_database_naming import _migrate, schema_database

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import application_form as form_router
    from app.api.routers import daily_work as daily_router
    from app.api.routers import remark_templates as templates_router

TZ = "Asia/Kolkata"
TUESDAY = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)  # 08:30 IST
SEVEN = ["Visa in progress", "Visa stamping in progress", "Awaiting Gamca Medical", "Offer Accepted", "Offer Declined by Candidate",
         "PCC Pending", "Trial feedback pending"]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADER = ["S.No", "DOC", "Recruiter", "Job", "Client", "Country", "Candidate", "Care ID", "Agency", "Advance", "Stage", "Today", "Remark"]


def frozen(clock):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"] if tz is None else clock["now"].astimezone(tz)
    return FrozenDatetime


@pytest.fixture
def desk(schema_database, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    clock = {"now": TUESDAY}
    monkeypatch.setattr(daily_work, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    monkeypatch.setattr(daily_router, "datetime", frozen(clock))
    monkeypatch.setattr(form_router.service, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("asha", Role.staff), ("ravi", Role.staff))])
        db.add(Client(id="client", name="Mr Contact", country="UAE"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Gulf Builders", country="UAE"))
        db.add_all([Designation(id="welder", title="Welder"), Designation(id="cook", title="Cook")])
        db.flush()
        db.add_all([Order(id="order", ref="ORD-1", company_id="company", designation_id="welder", openings=40, country="Qatar"),
                    Order(id="order2", ref="ORD-2", company_id="company", designation_id="cook", openings=5, country="Oman")])
        # asha: c1 prescreening (daily), c2 stage 5 with an advance and a remark, c3 unmatched with a call made today, c5 moved a stage today
        # ravi: c4 stage 3
        people = [("c1", "asha", "Anil Sample", "CI-001", "Sigma Agency"), ("c2", "asha", "Bina Example", "CI-002", None),
                  ("c3", "asha", "Chandra Invented", "CI-003", None), ("c4", "ravi", "Deepak Test", "CI-004", None),
                  ("c5", "asha", "Esha Made-up", "CI-005", None)]
        for key, owner, name, care_id, agency in people:
            db.add(Candidate(id=key, ref=f"CND-{key}", name=name, phone=f"9000000{key[1]}00", source=Source.referral if agency else Source.walk_in,
                             source_detail=agency, owner_id=owner, care_id=care_id, designations=["Mason"], created_at=TUESDAY - timedelta(days=20)))
        db.flush()
        for key, owner, stage, order in (("c1", "asha", 1, "order"), ("c2", "asha", 5, "order"), ("c4", "ravi", 3, "order2"), ("c5", "asha", 4, "order")):
            db.add(Application(id=f"a-{key}", candidate_id=key, order_id=order, owner_id=owner, current_stage=stage,
                               created_at=TUESDAY - timedelta(days=10)))
        db.add(Candidate.__table__.update().where(Candidate.id == "c3").values(call_recorded_at=TUESDAY, call_called=True, call_interested="yes")) \
            if False else None
        db.flush()
        db.execute(text("UPDATE crm_candidates SET call_recorded_at = :at, call_called = true, call_interested = 'yes' WHERE id = 'c3'"), {"at": TUESDAY})
        db.add(ApplicationStage(application_id="a-c2", stage=8, attempt=1, completed_by="admin", amount=10000, amount_currency="INR",
                                completed_at=TUESDAY - timedelta(days=3),
                                data={"advanceStatus": "collected", "amount": 10000, "amountCurrency": "INR", "serviceChargeTotal": 25000}))
        db.add(ApplicationStage(application_id="a-c5", stage=3, attempt=1, completed_by="asha", completed_at=TUESDAY + timedelta(hours=1), data={}))
        db.add(Task(id="t-c2", kind=TaskKind.stage, title="Stage 5", candidate_id="c2", application_id="a-c2", stage=5, owner_id="asha"))
        db.flush()
        db.add(Remark(task_id="t-c2", body="=cmd|' /C calc'!A0", author_id="asha", created_at=TUESDAY - timedelta(days=2)))
        db.commit()
    actor = SimpleNamespace(id="asha", name="Asha", role=Role.staff)
    app = FastAPI()
    for module in (daily_router, form_router, templates_router):
        app.include_router(module.router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[daily_router.get_db] = database
    app.dependency_overrides[daily_router.current_user] = lambda: actor
    env = SimpleNamespace(sessions=sessions, clock=clock, actor=actor, engine=schema_database)
    with TestClient(app) as client:
        env.client = client
        yield env


def as_user(env, user_id):
    env.actor.id = user_id
    env.actor.name = user_id.title()
    env.actor.role = Role.staff if user_id in {"asha", "ravi"} else Role.admin if user_id == "admin" else Role.manager


def get(env, user_id, path):
    as_user(env, user_id)
    response = env.client.get(path)
    assert response.status_code == 200, response.text
    return response


def future(days):
    return (date.today() + timedelta(days=days)).isoformat()


# ------------------------------------------------------------------ the migration

def test_migration_adds_the_column_and_seeds_the_templates_idempotently(schema_database):
    engine = schema_database
    _migrate(engine, "0045_more_currencies")
    assert "commencement_date" not in {c["name"] for c in inspect(engine).get_columns("crm_applications")}
    assert "remark_templates" not in inspect(engine).get_table_names()
    _migrate(engine, "head")
    column = next(c for c in inspect(engine).get_columns("crm_applications") if c["name"] == "commencement_date")
    assert column["nullable"] is True and str(column["type"]) == "DATE"
    with engine.connect() as connection:
        assert [row[0] for row in connection.execute(text("SELECT text FROM remark_templates ORDER BY sort_order"))] == SEVEN
        assert connection.scalar(text("SELECT bool_and(active) FROM remark_templates")) is True
    with engine.begin() as connection:
        # An administrator renamed one, switched one off and added their own; then the revision runs again (lost version row).
        connection.execute(text("UPDATE remark_templates SET active = false WHERE text = 'PCC Pending'"))
        connection.execute(text("UPDATE remark_templates SET sort_order = 5 WHERE text = 'Offer Accepted'"))
        connection.execute(text("INSERT INTO remark_templates (id, text, sort_order) VALUES ('mine', 'Own note', 500)"))
        connection.execute(text("UPDATE alembic_version SET version_num = '0045_more_currencies'"))
    _migrate(engine, "head")
    with engine.connect() as connection:
        rows = {text_: (active, order) for text_, active, order in connection.execute(text("SELECT text, active, sort_order FROM remark_templates"))}
        assert len(rows) == 8 and rows["PCC Pending"][0] is False and rows["Offer Accepted"][1] == 5 and rows["Own note"] == (True, 500)
    # Text is unique ignoring case.
    with engine.begin() as connection, pytest.raises(Exception):
        connection.execute(text("INSERT INTO remark_templates (id, text) VALUES ('dup', 'visa IN progress')"))
    _migrate(engine, "0045_more_currencies", downgrade=True)
    assert "remark_templates" not in inspect(engine).get_table_names()
    assert "commencement_date" not in {c["name"] for c in inspect(engine).get_columns("crm_applications")}
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM remark_templates")) == 7


# ------------------------------------------------------------------ the date of commencement

def save(env, user_id, value, candidate="c1", query=""):
    as_user(env, user_id)
    return env.client.put(f"/api/candidates/{candidate}/application-form{query}", json={"application": {"commencementDate": value}})


def test_the_owner_saves_the_date_on_the_placement_and_it_comes_back_in_the_form(desk):
    day = future(30)
    response = save(desk, "asha", day)
    assert response.status_code == 200, response.text
    form = response.json()
    assert form["application"]["commencementDate"] == day and form["application"]["id"] == "a-c1"
    assert [a["commencementDate"] for a in form["applications"]] == [day]
    assert get(desk, "asha", "/api/candidates/c1/application-form").json()["application"]["commencementDate"] == day
    # The audit trail names the field, and nothing else of the form changed.
    with desk.sessions() as db:
        entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "Updated application form")).one()
        assert entry.meta["changed_fields"] == ["application.commencementDate"] and entry.meta["application_id"] == "a-c1"
    # Saving the same date again is no change and writes nothing; null clears it.
    assert save(desk, "asha", day).status_code == 200
    with desk.sessions() as db:
        assert len(db.scalars(select(AuditEntry).where(AuditEntry.action == "Updated application form")).all()) == 1
    cleared = save(desk, "asha", None)
    assert cleared.status_code == 200 and cleared.json()["application"]["commencementDate"] is None


def test_the_placement_is_chosen_like_everywhere_else_in_the_form(desk):
    with desk.sessions() as db:   # a second, closed placement of c1 on another order
        db.add(Application(id="a-old", candidate_id="c1", order_id="order2", owner_id="asha", current_stage=4, status=ApplicationStatus.closed,
                           closure_reason=ClosureReason.interview_rejected, created_at=TUESDAY - timedelta(days=60)))
        db.commit()
    assert save(desk, "asha", future(40), query="?applicationId=a-old").status_code == 200
    assert save(desk, "asha", future(50), query="?orderId=order").status_code == 200
    assert save(desk, "asha", future(60)).status_code == 200      # no selector: the active placement
    with desk.sessions() as db:
        dates = {a.id: a.commencement_date for a in db.scalars(select(Application).where(Application.candidate_id == "c1"))}
    assert dates == {"a-old": date.fromisoformat(future(40)), "a-c1": date.fromisoformat(future(60))}
    assert save(desk, "asha", future(40), query="?applicationId=a-c4").status_code == 404    # another candidate's placement
    assert save(desk, "asha", future(40), query="?orderId=order2&applicationId=nope").status_code == 404


def test_the_date_is_validated_and_needs_a_placement(desk):
    for bad in ("1999-12-31", future(5 * 366 + 10)):
        refused = save(desk, "asha", bad)
        assert refused.status_code == 422, bad
        assert "commencement" in refused.text.lower()
    assert save(desk, "asha", "not a date").status_code == 422
    assert save(desk, "asha", "2000-01-01").status_code == 200      # the first day allowed
    assert save(desk, "asha", future(1820)).status_code == 200      # about five years ahead is allowed
    refused = save(desk, "asha", future(10), candidate="c3")        # c3 is not mapped to any job
    assert refused.status_code == 422 and "job" in refused.text.lower()
    with desk.sessions() as db:
        assert db.scalar(select(Application.commencement_date).where(Application.id == "a-c1")) == date.fromisoformat(future(1820))


def test_only_the_owner_a_manager_or_an_admin_may_save_it(desk):
    refused = save(desk, "ravi", future(20))                         # another recruiter's candidate
    assert refused.status_code == 403
    with desk.sessions() as db:
        assert db.scalar(select(Application.commencement_date).where(Application.id == "a-c1")) is None
    assert save(desk, "manager", future(20)).status_code == 200
    assert save(desk, "admin", future(21)).status_code == 200
    assert save(desk, "asha", future(22)).status_code == 200
    # A save with no application block still works as before.
    as_user(desk, "asha")
    assert desk.client.put("/api/candidates/c1/application-form", json={"screening": {"nativePlace": "Kochi"}}).status_code == 200
    assert desk.client.put("/api/candidates/c1/application-form", json={"application": {"orderId": "x"}}).status_code == 422


def test_every_tasks_row_carries_the_date_and_null_when_there_is_no_placement(desk):
    assert save(desk, "asha", "2026-12-05", candidate="c1").status_code == 200
    assert save(desk, "asha", "2026-12-20", candidate="c2").status_code == 200
    daily = {i["candidateId"]: i for i in get(desk, "asha", "/api/daily-work").json()["items"]}
    assert daily["c1"]["commencementDate"] == "2026-12-05" and daily["c3"]["commencementDate"] is None
    follow = {i["candidateId"]: i for i in get(desk, "asha", "/api/follow-up").json()["items"]}
    assert follow["c2"]["commencementDate"] == "2026-12-20" and follow["c5"]["commencementDate"] is None


def test_the_date_adds_no_statement_to_either_list(desk):
    def statements(path):
        seen = []
        record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))  # noqa: E731
        event.listen(desk.engine, "before_cursor_execute", record)
        try:
            as_user(desk, "asha")
            assert desk.client.get(path).status_code == 200
        finally:
            event.remove(desk.engine, "before_cursor_execute", record)
        return seen

    for path, budget in (("/api/daily-work", 6), ("/api/follow-up", 5)):
        seen = statements(path)
        assert len(seen) <= budget, [sql[:70] for sql in seen]
        assert any("commencement_date" in sql for sql in seen)    # it rides in the placement statement already there


# ------------------------------------------------------------------ the remark templates

def test_everyone_reads_the_active_templates_in_order(desk):
    body = get(desk, "asha", "/api/remark-templates").json()
    assert [i["text"] for i in body["items"]] == SEVEN
    assert [i["sortOrder"] for i in body["items"]] == sorted(i["sortOrder"] for i in body["items"])
    assert set(body["items"][0]) == {"id", "text", "active", "sortOrder"} and all(i["active"] is True for i in body["items"])
    assert get(desk, "manager", "/api/remark-templates").json() == body


def test_the_admin_adds_renames_reorders_switches_off_and_sees_all(desk):
    def recruiter_reads(path):
        try:
            return get(desk, "asha", path)
        finally:
            as_user(desk, "admin")

    def admin_reads(path):
        return get(desk, "admin", path)

    as_user(desk, "admin")
    added = desk.client.post("/api/remark-templates", json={"text": "  Passport   collected  "})
    assert added.status_code == 201, added.text
    new = added.json()
    assert new["text"] == "Passport collected" and new["active"] is True
    items = recruiter_reads("/api/remark-templates").json()["items"]
    assert items[-1] == new and len(items) == 8                       # appended last
    # idempotent ignoring case: the existing item comes back with 200 and nothing is added; 1-120 characters
    again = desk.client.post("/api/remark-templates", json={"text": "PASSPORT COLLECTED"})
    assert again.status_code == 200 and again.json() == new
    assert len(recruiter_reads("/api/remark-templates").json()["items"]) == 8
    assert desk.client.post("/api/remark-templates", json={"text": "   "}).status_code == 422
    assert desk.client.post("/api/remark-templates", json={"text": "x" * 121}).status_code == 422
    assert desk.client.post("/api/remark-templates", json={"text": "x" * 120}).status_code == 201
    # change
    changed = desk.client.patch(f"/api/remark-templates/{new['id']}", json={"text": "Passport received", "sortOrder": 5})
    assert changed.status_code == 200 and changed.json()["text"] == "Passport received" and changed.json()["sortOrder"] == 5
    assert recruiter_reads("/api/remark-templates").json()["items"][0]["text"] == "Passport received"
    assert desk.client.patch(f"/api/remark-templates/{new['id']}", json={"text": "visa in progress"}).status_code == 409
    assert desk.client.patch(f"/api/remark-templates/{new['id']}", json={"text": "PASSPORT RECEIVED"}).status_code == 200   # its own text, other case
    assert desk.client.patch("/api/remark-templates/nope", json={"active": False}).status_code == 404
    # switch off: the recruiters stop seeing it, the admin still does with all=1
    assert desk.client.patch(f"/api/remark-templates/{new['id']}", json={"active": False}).json()["active"] is False
    switched_on = desk.client.post("/api/remark-templates", json={"text": "passport received"})   # adding a switched-off text revives it
    assert switched_on.status_code == 200 and switched_on.json() == {**new, "text": "PASSPORT RECEIVED", "sortOrder": 5, "active": True}
    assert desk.client.patch(f"/api/remark-templates/{new['id']}", json={"active": False}).json()["active"] is False
    assert new["id"] not in [i["id"] for i in recruiter_reads("/api/remark-templates").json()["items"]]
    assert new["id"] not in [i["id"] for i in admin_reads("/api/remark-templates").json()["items"]]
    everything = admin_reads("/api/remark-templates?all=1").json()["items"]
    assert new["id"] in [i["id"] for i in everything] and next(i for i in everything if i["id"] == new["id"])["active"] is False
    # a recruiter asking for all gets the active ones only
    assert new["id"] not in [i["id"] for i in recruiter_reads("/api/remark-templates?all=1").json()["items"]]
    # delete = switch off, never a real delete
    seven = admin_reads("/api/remark-templates").json()["items"]
    gone = desk.client.delete(f"/api/remark-templates/{seven[0]['id']}")
    assert gone.status_code == 200 and gone.json()["active"] is False and gone.json()["text"] == "Visa in progress"
    assert "Visa in progress" not in [i["text"] for i in recruiter_reads("/api/remark-templates").json()["items"]]
    with desk.sessions() as db:
        assert db.scalar(select(RemarkTemplate.active).where(RemarkTemplate.id == seven[0]["id"])) is False
        actions = [a.action for a in db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "remark_template"))]
    assert actions.count("Added remark template") == 2 and "Updated remark template" in actions and "Switched off remark template" in actions and "Switched on remark template" in actions


def test_only_admin_level_users_may_change_the_templates(desk):
    """A recruiter never may. (Admin-level is `require_role(Role.admin)`: the admin, and the MD stored as `manager` since core.deps counts one.)"""
    template = get(desk, "asha", "/api/remark-templates").json()["items"][0]
    for user in ("asha",):
        as_user(desk, user)
        assert desk.client.post("/api/remark-templates", json={"text": "Not allowed"}).status_code == 403
        assert desk.client.patch(f"/api/remark-templates/{template['id']}", json={"active": False}).status_code == 403
        assert desk.client.delete(f"/api/remark-templates/{template['id']}").status_code == 403
    assert [i["text"] for i in get(desk, "asha", "/api/remark-templates").json()["items"]] == SEVEN


# ------------------------------------------------------------------ the agency of every row

def test_every_row_carries_the_trackers_agency_text_from_the_rows_own_statement(desk):
    with desk.sessions() as db:
        db.add_all([
            Candidate(id="e1", ref="CND-e1", name="Email Agency", phone="9100000101", source=Source.email, source_detail="  Lotus Overseas ", owner_id="asha",
                      care_id="CI-101", created_at=TUESDAY - timedelta(days=20)),
            Candidate(id="e2", ref="CND-e2", name="Bulk Imported", phone="9100000102", source=Source.email, source_detail="Bulk import", owner_id="asha",
                      care_id="CI-102", created_at=TUESDAY - timedelta(days=20)),
            Candidate(id="e3", ref="CND-e3", name="Machine Source", phone="9100000103", source=Source.walk_in, source_detail=None, owner_id="asha",
                      care_id="CI-103", created_at=TUESDAY - timedelta(days=20))])
        db.execute(text("UPDATE crm_candidates SET source = 'email', source_detail = 'Gulf Link' WHERE id = 'c2'"))   # a placement in Follow-up
        db.commit()
    daily = {i["candidateId"]: i for i in get(desk, "asha", "/api/daily-work").json()["items"]}
    assert daily["c1"]["agency"] == "Sigma Agency"                  # a referral
    assert daily["e1"]["agency"] == "Lotus Overseas"                # arrived by email, then got an agency: the old row showed nothing
    assert daily["e2"]["agency"] is None and daily["e3"]["agency"] is None and daily["c3"]["agency"] is None   # "Bulk import" is provenance, not an agency
    follow = {i["candidateId"]: i for i in get(desk, "asha", "/api/follow-up").json()["items"]}
    assert follow["c2"]["agency"] == "Gulf Link" and follow["c5"]["agency"] is None
    rows = sheet(get(desk, "asha", "/api/daily-work/export?format=xlsx"))[1]
    assert {r[6]: r[8] for r in rows[1:]} == {"Bina Example": "Gulf Link", "Anil Sample": "Sigma Agency", "Email Agency": "Lotus Overseas",
                                            "Bulk Imported": "", "Machine Source": "", "Esha Made-up": "", "Chandra Invented": ""}


# ------------------------------------------------------------------ the Excel

def sheet(response):
    """(sheet name, rows of cell text, the raw sheet xml) of the downloaded workbook."""
    with ZipFile(BytesIO(response.content)) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in ElementTree.fromstring(xml).findall(".//m:row", ns):
        cells = {}
        for cell in row.findall("m:c", ns):
            letters = "".join(ch for ch in cell.get("r") if ch.isalpha())
            text_ = "".join(t.text or "" for t in cell.findall(".//m:t", ns))
            value = cell.find("m:v", ns)
            cells[letters] = text_ if cell.get("t") == "inlineStr" else (value.text if value is not None else "")
        rows.append([cells.get(chr(65 + i), "") for i in range(13)])
    return workbook, rows, xml


def test_the_excel_has_the_screens_columns_and_rows_in_the_screens_order(desk):
    desk.clock["now"] = TUESDAY
    assert save(desk, "asha", "2026-12-05", candidate="c1").status_code == 200
    assert save(desk, "asha", "2026-12-20", candidate="c2").status_code == 200
    response = get(desk, "asha", "/api/daily-work/export?format=xlsx&staff=all")
    assert response.headers["content-type"].startswith(XLSX)
    assert response.headers["content-disposition"] == 'attachment; filename="tasks-2026-11-03.xlsx"'
    assert response.headers["cache-control"] == "private, no-store"
    workbook, rows, xml = sheet(response)
    assert 'name="Tasks"' in workbook
    assert rows[0] == HEADER
    assert "<pane" in xml and 'state="frozen"' in xml and "<autoFilter" in xml
    # untouched first (the furthest stage first), then what was done today: Bina (5), Anil (1), then Esha (moved a stage), Chandra (called)
    assert [r[6] for r in rows[1:]] == ["Bina Example", "Anil Sample", "Esha Made-up", "Chandra Invented"]
    assert [r[0] for r in rows[1:]] == ["1", "2", "3", "4"]                    # S.No
    bina, anil, esha, chandra = rows[1:]
    assert bina[1:] == ["20-Dec-2026", "Asha", "Welder", "Gulf Builders", "Qatar", "Bina Example", "CI-002", "", "10k collected/25k",
                        tasks_export.stage_text(5), "", "=cmd|' /C calc'!A0"]
    assert anil[1:] == ["05-Dec-2026", "Asha", "Welder", "Gulf Builders", "Qatar", "Anil Sample", "CI-001", "Sigma Agency", "",
                        tasks_export.stage_text(1), "", ""]
    assert esha[11] == "Stage moved 09:30" and esha[1] == "" and esha[10].startswith("Stage 4 - ")
    assert chandra[10] == "Initial call" and chandra[11] == "Called 08:30" and chandra[4] == "" and chandra[1] == ""
    # no "Remark added" wording anywhere; the remark text is stored as text, never as a formula
    assert "Remark added" not in xml and "<f>" not in xml
    assert 'r="M2" s="7" t="inlineStr"' in xml


def test_the_excel_follows_the_scoping_of_the_list(desk):
    def names(user, query):
        return [r[6] for r in sheet(get(desk, user, "/api/daily-work/export?format=xlsx" + query))[1][1:]]

    assert names("asha", "") == ["Bina Example", "Anil Sample", "Esha Made-up", "Chandra Invented"]
    assert names("asha", "&staff=asha") == names("asha", "&staff=all") == names("asha", "")
    as_user(desk, "asha")
    assert desk.client.get("/api/daily-work/export?format=xlsx&staff=ravi").status_code == 403
    assert names("ravi", "") == ["Deepak Test"]
    assert names("admin", "&staff=ravi") == ["Deepak Test"]
    assert names("manager", "&staff=asha") == ["Bina Example", "Anil Sample", "Esha Made-up", "Chandra Invented"]
    everyone = names("admin", "&staff=all")
    assert set(everyone) == {"Bina Example", "Anil Sample", "Esha Made-up", "Chandra Invented", "Deepak Test"} and names("admin", "") == everyone
    # the same order as the screen's list: furthest stage first among the untouched
    assert everyone[:3] == ["Bina Example", "Deepak Test", "Anil Sample"]
    rows = sheet(get(desk, "admin", "/api/daily-work/export?staff=ravi"))[1]
    assert rows[1][2] == "Ravi"
    as_user(desk, "admin")
    assert desk.client.get("/api/daily-work/export?format=xlsx&staff=nobody").status_code == 404
    assert desk.client.get("/api/daily-work/export?format=csv").status_code == 422


def test_an_empty_task_list_is_still_a_sheet_with_its_header(desk):
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET owner_id = 'asha' WHERE id = 'c4'"))
        db.execute(text("UPDATE crm_candidates SET owner_id = NULL WHERE owner_id = 'ravi'"))
        db.commit()
    _, rows, _ = sheet(get(desk, "ravi", "/api/daily-work/export?format=xlsx"))
    assert rows == [HEADER]


def test_the_excel_costs_a_few_statements_whatever_the_number_of_rows(desk):
    def statements():
        seen = []
        record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))  # noqa: E731
        event.listen(desk.engine, "before_cursor_execute", record)
        try:
            as_user(desk, "admin")
            assert desk.client.get("/api/daily-work/export?format=xlsx&staff=all").status_code == 200
        finally:
            event.remove(desk.engine, "before_cursor_execute", record)
        return seen

    few = statements()
    with desk.sessions() as db:
        for n in range(40):
            db.add(Candidate(id=f"x{n:02}", ref=f"CND-X{n:02}", name=f"Extra {n}", phone=f"91{n:08}", source=Source.walk_in, owner_id="asha" if n % 2 else "ravi",
                             designations=["Mason"], care_id=f"CI-9{n:02}", created_at=TUESDAY - timedelta(days=5)))
        db.commit()
    many = statements()
    assert len(many) <= len(few) <= 16, [sql[:70] for sql in many]
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in many)
