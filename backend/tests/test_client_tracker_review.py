"""Client tracker, review pass: edge cases the first test file does not pin (disposable PostgreSQL). Every name and amount is invented."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Candidate, CandidateStatus, Client, ClosureReason, Company,
    Designation, Order, Remark, Role, Source, Task, TaskKind, User,
)
from test_database_naming import _migrate, schema_database  # noqa: F401  (the fixture)

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
                           agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.api.routers import reports as subject
from app.services import client_tracker as tracker


class World:
    """A builder over a migrated scratch database and a test client that answers as `actor`."""

    def __init__(self, engine):
        self.engine = engine
        self.sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        self.db = self.sessions()
        self.actor = SimpleNamespace(id="admin", role=Role.admin)
        self.number = 0
        self.db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                         for key, role in (("admin", Role.admin), ("manager", Role.manager), ("staff", Role.staff), ("other", Role.staff))])
        self.db.add(Client(id="client", name="Contact"))
        self.db.flush()
        self.companies, self.jobs, self.orders = {}, {}, {}

    def company(self, name, country="Kuwait"):
        if name not in self.companies:
            self.companies[name] = Company(id=f"co{len(self.companies)}", client_id="client", name=name, country=country)
            self.db.add(self.companies[name])
            self.db.flush()
        return self.companies[name]

    def order(self, company, ref, job="Driver", country=None):
        key = (company, ref)
        if key not in self.orders:
            if job not in self.jobs:
                self.jobs[job] = Designation(id=f"d{len(self.jobs)}", title=job)
                self.db.add(self.jobs[job])
                self.db.flush()
            self.orders[key] = Order(id=f"o{len(self.orders)}", ref=ref, company_id=self.company(company).id, designation_id=self.jobs[job].id,
                                     openings=50, country=country)
            self.db.add(self.orders[key])
            self.db.flush()
        return self.orders[key]

    def place(self, key, name, company="Alpha", ref="ORD-1", stage=3, status="active", owner="staff", cand_status=CandidateStatus.in_process,
              agency=None, candidate=None, job="Driver", country=None, reason=None, candidate_owner=None):
        self.number += 1
        order = self.order(company, ref, job, country)
        if candidate is None:
            candidate = Candidate(id=f"c-{key}", ref=f"CND-{self.number:05d}", name=name, phone=f"9100000{self.number:03d}", source=Source.referral,
                                  source_detail=agency, owner_id=candidate_owner or owner, status=cand_status)
            self.db.add(candidate)
            self.db.flush()
        self.db.add(Application(id=f"a-{key}", candidate_id=candidate.id, order_id=order.id, owner_id=owner, current_stage=stage,
                                status=ApplicationStatus(status), closure_reason=reason))
        self.db.flush()
        return candidate

    def stage8(self, key, amount=None, attempt=1, currency="INR", reopened=None, status="collected", total=None, **extra):
        """A stage-9 record the way the form saves it; `status=None` leaves advanceStatus out (a record from before it existed)."""
        data = {k: v for k, v in (("advanceStatus", status), ("amount", None if amount is None else float(amount)),
                                  ("serviceChargeTotal", total)) if v is not None}
        self.db.add(ApplicationStage(application_id=f"a-{key}", stage=9, attempt=attempt, completed_by="admin", amount=amount,
                                     amount_currency=currency if amount else None, reopened_reason=reopened, data={**data, **extra}))

    def remark(self, key, body, when, kind=TaskKind.stage, application=True, task=None):
        task_id = task or f"t-{key}-{when.day}"
        if not self.db.get(Task, task_id):
            self.db.add(Task(id=task_id, kind=kind, title="T", candidate_id=f"c-{key}", application_id=f"a-{key}" if application else None, stage=3,
                             owner_id="staff"))
            self.db.flush()
        self.db.add(Remark(task_id=task_id, body=body, author_id="staff", created_at=when))

    def travel(self, key, travel_date, attempt=1, reopened=None):
        self.db.add(ApplicationStage(application_id=f"a-{key}", stage=12, attempt=attempt, completed_by="admin", reopened_reason=reopened,
                                     data={"travelDate": travel_date}))

    def commit(self):
        self.db.commit()

    def get(self, **params):
        response = self.client.get("/api/reports/client-tracker", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def rows(self, **params):
        return {row["name"]: row for row in self.get(**params)["rows"]}

    def as_(self, who):
        self.actor = SimpleNamespace(id=who, role=Role(who) if who in ("admin", "manager") else Role.staff)


@pytest.fixture
def world(schema_database):  # noqa: F811
    _migrate(schema_database, "head")
    state = World(schema_database)
    app = FastAPI()
    app.include_router(subject.router)

    def database():
        with state.sessions() as db:
            yield db

    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: state.actor
    with TestClient(app) as client:
        state.client = client
        yield state
    state.db.close()


# ------------------------------------------------------------------------------------------------ (1) rows

def test_only_the_right_placements_one_row_each_in_sheet_order(world):
    w = world
    w.place("zed", "Zed Last", company="alpha corp", ref="ORD-10")
    w.place("adam", "adam first", company="alpha corp", ref="ORD-10")                        # same order: by name, case-blind
    w.place("nine", "Nine Person", company="alpha corp", ref="ORD-9")                         # ORD-10 < ORD-9 as text: documented sort
    w.place("beta", "Beta Person", company="Beta Corp", ref="ORD-21")
    w.place("uni", "Ünï Person", company="Ünï Corp", ref="ORD-22")
    w.place("notint", "Not Interested", company="Alpha", cand_status=CandidateStatus.not_interested)         # excluded
    w.place("declined", "Declined Person", company="Alpha", status="closed", reason=ClosureReason.stage_declined)
    w.place("rejected", "Rejected Person", company="Alpha", status="closed", reason=ClosureReason.stage_rejected)
    w.place("notrav", "Not Travelling", company="Alpha", status="closed", reason=ClosureReason.not_travelling, stage=12)
    w.place("dep", "Deployed Person", company="Alpha", status="deployed", stage=12, cand_status=CandidateStatus.deployed)
    w.place("nullowner", "Ownerless", company="Alpha", owner=None)
    # a candidate with an old closed placement and a current one: one row for the current only
    old = w.place("hist-new", "History Person", company="Alpha", ref="ORD-5", stage=6)
    w.db.add(Application(id="a-hist-old", candidate_id=old.id, order_id=w.order("Alpha", "ORD-6").id, owner_id="staff", current_stage=4,
                         status=ApplicationStatus.closed, closure_reason=ClosureReason.interview_rejected))
    w.commit()
    body = w.get()
    names = [(r["client"], r["orderRef"], r["name"]) for r in body["rows"]]
    assert names == [("Alpha", "ORD-1", "Deployed Person"), ("Alpha", "ORD-1", "Ownerless"), ("Alpha", "ORD-5", "History Person"),
                     ("alpha corp", "ORD-10", "adam first"), ("alpha corp", "ORD-10", "Zed Last"), ("alpha corp", "ORD-9", "Nine Person"),
                     ("Beta Corp", "ORD-21", "Beta Person"), ("Ünï Corp", "ORD-22", "Ünï Person")]
    shown = [r["name"] for r in body["rows"]]
    for gone in ("Not Interested", "Declined Person", "Rejected Person", "Not Travelling"):
        assert gone not in shown
    assert shown.count("History Person") == 1 and "Deployed Person" in shown and "Ownerless" in shown
    assert [r["sNo"] for r in body["rows"]] == list(range(1, len(shown) + 1))
    # the clients sort case-blind: "Alpha" and "alpha corp" before "Beta Corp", the unicode one after
    clients = [r["client"] for r in body["rows"]]
    assert clients == sorted(clients, key=str.lower)


def test_manager_sees_all_and_a_recruiter_only_what_they_own(world):
    w = world
    w.place("mine", "Mine", owner="staff")
    w.place("theirs", "Theirs", owner="other", candidate_owner="staff")      # the candidate is staff's but the placement is other's
    w.place("nobody", "Nobody", owner=None)
    w.commit()
    assert sorted(w.rows()) == ["Mine", "Nobody", "Theirs"]
    w.as_("manager")
    assert sorted(w.rows()) == ["Mine", "Nobody", "Theirs"]
    w.as_("staff")
    assert sorted(w.rows()) == ["Mine"]
    w.as_("other")
    assert sorted(w.rows()) == ["Theirs"]


# ------------------------------------------------------------------------------------------------ (2) advance

def test_advance_cells_through_the_database(world):
    w = world
    amounts = {"a999": 999, "a1000": 1000, "a1050": 1050, "a12500": 12500, "a12345": Decimal("12345.50"), "a1000_5": Decimal("1000.50")}
    for key, amount in amounts.items():
        w.place(key, key, ref=f"ORD-{key}")
        w.stage8(key, amount)
    w.place("zero", "zero")                      # collected with no figure
    w.stage8("zero", None)
    w.place("zerototal", "zerototal")            # a recorded 0 is no advance, the total still shows
    w.stage8("zerototal", 0, total=5000)
    w.place("both", "both")
    w.stage8("both", 10000, total=50000)
    w.place("pendtotal", "pendtotal")            # pending: the total service charge wins over the expected advance
    w.stage8("pendtotal", 10000, status="pending", total=45000)
    w.place("pendonly", "pendonly")              # pending, the advance expected only
    w.stage8("pendonly", 10000, status="pending")
    w.place("pendnone", "pendnone")
    w.stage8("pendnone", None, status="pending")
    w.place("oldamount", "oldamount")            # a record from before the status existed: the amount is the advance collected
    w.stage8("oldamount", 60000, status=None, collected="yes")
    w.place("oldno", "oldno")                    # ... and 'collected: no' with no amount reads as pending
    w.stage8("oldno", None, status=None, collected="no")
    w.place("stopped", "stopped")                # a stopping exit is no advance
    w.stage8("stopped", None, status=None, stop="not_paid")
    w.place("usd", "usd")
    w.stage8("usd", 250, currency="USD")
    w.place("usdboth", "usdboth")
    w.stage8("usdboth", 600, currency="usd", total=1500)
    w.place("reopened", "reopened")              # attempt 1 reopened, attempt 2 is the live one
    w.stage8("reopened", 10000, attempt=1, reopened="redo")
    w.stage8("reopened", 20000, attempt=2, total=40000)
    w.place("reopened_only", "reopened_only")    # the only record was reopened: nothing recorded
    w.stage8("reopened_only", 10000, attempt=1, reopened="redo")
    w.place("norecord", "norecord")              # no stage-9 record at all
    w.place("invoiced", "invoiced")              # an invoice or receipt does not count: only stage 9 speaks
    w.db.execute(text("INSERT INTO crm_candidate_invoices (id, candidate_id, application_id, scope_key, service_fee, status, lock_version, created_at, updated_at) "
                      "VALUES ('i1', 'c-invoiced', 'a-invoiced', 'a-invoiced', 20000, 'pending_fee', 0, now(), now())"))
    w.db.execute(text("INSERT INTO crm_invoice_payments (id, invoice_id, amount, paid_on, method, idempotency_key, reference, note, created_at) "
                      "VALUES ('p1', 'i1', 7000, '2026-10-01', 'cash', 'k', '', '', now())"))
    w.commit()
    rows = w.rows()
    assert {k: rows[k]["advance"] for k in rows} == {
        "a999": "999 Collected", "a1000": "1k Collected", "a1050": "1050 Collected", "a12500": "12.5k Collected",
        "a12345": "12345.5 Collected", "a1000_5": "1000.5 Collected", "zero": "Collected", "zerototal": "Collected/5k",
        "both": "10k collected/50k", "pendtotal": "Pending/45k", "pendonly": "Pending/10k", "pendnone": "Pending",
        "oldamount": "60k Collected", "oldno": "Pending", "stopped": "", "usd": "250 Collected USD",
        "usdboth": "600 collected/1.5k USD", "reopened": "20k collected/40k", "reopened_only": "", "norecord": "", "invoiced": ""}


def test_the_advance_cell_is_the_same_for_every_role(world):
    w = world
    w.place("p", "Pay Person")
    w.stage8("p", 7000, total=20000)
    w.place("q", "Wait Person", ref="ORD-2")
    w.stage8("q", 5000, status="pending", total=30000)
    w.commit()
    for who in ("admin", "manager", "staff"):
        w.as_(who)
        rows = w.rows()
        assert rows["Pay Person"]["advance"] == "7k collected/20k" and rows["Wait Person"]["advance"] == "Pending/30k", who
        csv_text = w.client.get("/api/reports/client-tracker", params={"format": "csv"}).content.decode("utf-8-sig")
        assert "7k collected/20k" in csv_text and "Pending/30k" in csv_text


def test_the_invoice_tables_are_not_read(world):
    from sqlalchemy import event
    seen = []
    w = world
    w.place("p", "Pay Person")
    w.commit()
    event.listen(w.engine, "before_cursor_execute", lambda c, cur, statement, *a: seen.append(statement))
    w.get()
    assert seen and not any("crm_candidate_invoices" in s or "crm_invoice_payments" in s for s in seen)


# ------------------------------------------------------------------------------------------------ (3) wording and exports

def test_travelled_wording_for_every_ordinal(world):
    days = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th", 13: "13th", 21: "21st", 22: "22nd", 23: "23rd", 30: "30th", 31: "31st"}
    for day in days:
        w = world
        w.place(f"d{day}", f"Day {day:02d}", status="deployed", stage=12, ref=f"ORD-{day:02d}")
        w.travel(f"d{day}", f"2026-12-{day:02d}")
    world.commit()
    rows = world.rows()
    for day, word in days.items():
        assert rows[f"Day {day:02d}"]["stage"] == f"Travelled on {word} DEC 2026"


def test_travel_date_rules_in_the_database(world):
    w = world
    w.place("none", "No Stage 12", status="deployed", stage=12)                          # deployed with no stage-12 record at all
    w.place("bad", "Bad Date", status="deployed", stage=12, ref="ORD-2")
    w.travel("bad", "2026-02-30")                                                         # not a real day
    w.place("garbage", "Garbage Date", status="deployed", stage=12, ref="ORD-3")
    w.travel("garbage", "11/11/2026")
    w.place("redo", "Reopened Travel", status="deployed", stage=12, ref="ORD-4")
    w.travel("redo", "2026-01-01", attempt=1, reopened="redo")
    w.travel("redo", "2026-03-04", attempt=2)
    w.place("number", "Number Date", status="deployed", stage=12, ref="ORD-5")
    w.db.add(ApplicationStage(application_id="a-number", stage=12, attempt=1, completed_by="admin", data={"travelDate": 20261111}))
    w.commit()
    rows = w.rows()
    assert rows["No Stage 12"]["stage"] == "Travelled" and rows["Bad Date"]["stage"] == "Travelled"
    assert rows["Garbage Date"]["stage"] == "Travelled" and rows["Number Date"]["stage"] == "Travelled"
    assert rows["Reopened Travel"]["stage"] == "Travelled on 4th MAR 2026"
    assert rows["No Stage 12"]["remarks"] == "Travelled"


def test_remarks_one_line_newest_wins_and_unicode(world):
    w = world
    w.place("r", "Tamil Name தமிழ்", agency="Ünï Agency")
    w.remark("r", "older", datetime(2026, 11, 1, tzinfo=timezone.utc))
    w.remark("r", "  line one\r\nline two,\t\"quoted\"  \n\n  =SUM(A1)  வணக்கம்  ", datetime(2026, 11, 9, tzinfo=timezone.utc))
    w.place("dep", "Deployed Remark", status="deployed", stage=12, ref="ORD-2")
    w.travel("dep", "2026-11-03")
    w.remark("dep", "Landed safely", datetime(2026, 11, 8, tzinfo=timezone.utc))
    w.place("first", "First Stage", stage=1, ref="ORD-3")
    w.remark("first", "Call went well", datetime(2026, 11, 2, tzinfo=timezone.utc), kind=TaskKind.initial_call, application=False)
    w.place("later", "Later Stage", stage=4, ref="ORD-4")
    w.remark("later", "Initial call note", datetime(2026, 11, 2, tzinfo=timezone.utc), kind=TaskKind.initial_call, application=False)   # not shown past stage 1
    w.commit()
    rows = w.rows()
    assert rows["Tamil Name தமிழ்"]["remarks"] == 'line one line two, "quoted" =SUM(A1) வணக்கம்'
    assert rows["Tamil Name தமிழ்"]["referral"] == "Ünï Agency"
    assert rows["Deployed Remark"]["remarks"] == "Landed safely"
    assert rows["First Stage"]["remarks"] == "Call went well" and rows["Later Stage"]["remarks"] == ""


def test_csv_and_xlsx_with_hostile_text(world):
    w = world
    w.place("h", "=HYPERLINK(\"http://x\")", company="+Client, \"Inc\"", agency="@agency", job="-Driver")
    w.remark("h", "=1+1\nsecond, line \"q\"", datetime(2026, 11, 9, tzinfo=timezone.utc))
    w.place("u", "Zoë Ñandú 李雷", ref="ORD-2")
    w.remark("u", "plain\x01control\x0bchars", datetime(2026, 11, 9, tzinfo=timezone.utc))
    w.commit()
    raw = w.client.get("/api/reports/client-tracker", params={"format": "csv"})
    table = list(csv.reader(StringIO(raw.content.decode("utf-8-sig"))))
    hostile = next(r for r in table if "HYPERLINK" in r[3])
    assert hostile[1:] == ["'+Client, \"Inc\"", "Kuwait", "'=HYPERLINK(\"http://x\")", "'-Driver", "'@agency", "", "Interview to be scheduled", "'=1+1 second, line \"q\""]
    assert any(r[3] == "Zoë Ñandú 李雷" for r in table)
    book = w.client.get("/api/reports/client-tracker", params={"format": "xlsx"})
    assert book.status_code == 200
    with ZipFile(BytesIO(book.content)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    import xml.dom.minidom
    xml.dom.minidom.parseString(sheet)                      # well-formed XML, control characters included
    assert "<f>" not in sheet and 't="inlineStr"' in sheet  # no cell is a formula
    assert "Zoë Ñandú 李雷" in sheet and "=HYPERLINK" in sheet


# ------------------------------------------------------------------------------------------------ (4) filters

def test_filters_in_the_database(world):
    w = world
    w.place("a", "Aa", company="Alpha", agency="Sigma", stage=3, country="UAE")
    w.place("b", "Bb", company="Alpha Two", agency="sigma ", stage=3, ref="ORD-2")
    w.place("c", "Cc", company="Beta", agency="Sigma Group", stage=12, ref="ORD-3")
    w.place("d", "Dd", company="Beta", status="deployed", stage=12, ref="ORD-3", agency="Sigma")
    w.commit()
    assert sorted(w.rows(client="ALPHA")) == ["Aa"]                       # exact, not a prefix
    assert sorted(w.rows(client="  alpha  ")) == ["Aa"]
    assert sorted(w.rows(agency="SIGMA")) == ["Aa", "Bb", "Dd"]
    assert sorted(w.rows(country="uae")) == ["Aa"]
    assert sorted(w.rows(country="Kuwait")) == ["Bb", "Cc", "Dd"]
    assert sorted(w.rows(stage=12)) == ["Cc"]                              # in progress at the ticket stage, not the travelled one
    assert sorted(w.rows(stage=3)) == ["Aa", "Bb"]
    assert sorted(w.rows(include_travelled="false")) == ["Aa", "Bb", "Cc"]
    assert [r["sNo"] for r in w.get(stage=3)["rows"]] == [1, 2]
    for bad in (0, 13, -1, 99):
        assert w.client.get("/api/reports/client-tracker", params={"stage": bad}).status_code == 422, bad
    assert w.client.get("/api/reports/client-tracker", params={"stage": "x"}).status_code == 422
    assert w.client.get("/api/reports/client-tracker", params={"format": "pdf"}).status_code == 422
    for number in range(1, 13):
        assert w.client.get("/api/reports/client-tracker", params={"stage": number}).status_code == 200
    # the pickers do not shrink with the filter
    assert w.get(client="Alpha")["options"]["clients"] == ["Alpha", "Alpha Two", "Beta"]
    # an empty filtered download is still a valid file with its header
    empty = w.client.get("/api/reports/client-tracker", params={"format": "csv", "client": "Nobody"})
    assert list(csv.reader(StringIO(empty.content.decode("utf-8-sig")))) == [list(tracker.COLUMNS)]
    book = w.client.get("/api/reports/client-tracker", params={"format": "xlsx", "client": "Nobody"})
    assert book.status_code == 200 and ZipFile(BytesIO(book.content)).testzip() is None


# ------------------------------------------------------------------------------------------------ (5) the kept answer

@pytest.fixture
def kept(monkeypatch):
    from app.services import response_cache
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    response_cache.clear()
    yield response_cache
    response_cache.clear()


def test_kept_answers_never_cross_roles_and_every_source_drops_them(world, kept):
    w = world
    w.place("p", "Kept Person", stage=3)
    w.place("q", "Other Owner", stage=3, owner="other", ref="ORD-2")
    w.stage8("p", 5000, status="pending", total=12500)
    w.commit()
    admin_first = w.get()
    assert admin_first["rows"][0]["advance"] == "Pending/12.5k"
    w.as_("manager")
    assert [r["name"] for r in w.get()["rows"]] == ["Kept Person", "Other Owner"]
    w.as_("staff")
    assert [r["name"] for r in w.get()["rows"]] == ["Kept Person"]
    w.as_("other")
    assert [r["name"] for r in w.get()["rows"]] == ["Other Owner"]          # not staff's kept answer
    w.as_("admin")

    def changed(sql, **params):
        with w.engine.begin() as connection:
            connection.execute(text(sql), params)
        return w.rows()

    assert changed("UPDATE crm_applications SET current_stage = 6 WHERE id = 'a-p'")["Kept Person"]["stage"] == "Medical in progress"
    # a stage-9 record
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE crm_application_stages SET data = '{\"advanceStatus\": \"collected\", \"amount\": 3000, \"serviceChargeTotal\": 12500}', amount = 3000 "
                                "WHERE application_id = 'a-p' AND stage = 9"))
    assert w.rows()["Kept Person"]["advance"] == "3k collected/12.5k"
    # a remark
    with w.engine.begin() as connection:
        connection.execute(text("INSERT INTO workflow_tasks (id, kind, title, candidate_id, application_id, stage, owner_id, escalated) "
                                "VALUES ('tt', 'stage', 'T', 'c-p', 'a-p', 6, 'staff', false)"))
        connection.execute(text("INSERT INTO workflow_task_remarks (id, task_id, body, author_id, created_at) VALUES ('rr', 'tt', 'fresh note', 'staff', now())"))
    assert w.rows()["Kept Person"]["remarks"] == "fresh note"
    # a stage-9 record, a company rename, the candidate's agency
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE crm_employer_companies SET name = 'Renamed' WHERE id = 'co0'"))
    assert w.rows()["Kept Person"]["client"] == "Renamed"
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE crm_candidates SET source_detail = 'New Agency' WHERE id = 'c-p'"))
    assert w.rows()["Kept Person"]["referral"] == "New Agency"
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'c-p'"))
    assert "Kept Person" not in w.rows()
