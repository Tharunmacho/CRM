"""Client tracker: the running sheet of placements in progress and travelled, grouped by client (disposable PostgreSQL).

Every name, client, agency and amount below is invented.
"""
import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Candidate, Client, ClosureReason, Company, Designation, Order, Remark, Role, Source, Task, TaskKind, User,
)
from test_database_naming import _migrate, schema_database

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
                           agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.api.routers import reports as subject
from app.services import client_tracker as tracker

COLUMNS = ["S.No", "Client", "Country", "Name", "Position", "Ref", "Advance", "Stage", "Remarks"]

# key, candidate, company key, job, source, agency, owner, status, current stage, closure reason
PLACEMENTS = [
    ("benny", "Benny Test", "tawseel", "Driver", Source.referral, "Sigma", "staff", "active", 12, None),
    ("mahmood", "Mahmood Sample", "finesco", "Electrician", Source.referral, "Airport Agency", "staff", "active", 10, None),
    ("rama", "Rama Example", "gurair", "Rigger", Source.other, None, "other", "active", 5, None),
    ("vikram", "Vikram Invented", "thinnai", "Waiter", Source.referral, "Care", "staff", "deployed", 13, None),
    ("closed", "Closed Person", "tawseel", "Driver", Source.referral, "Sigma", "staff", "closed", 4, ClosureReason.interview_rejected),
    ("call", "Call Stage Person", "tawseel", "Cook", Source.referral, "Sigma", "staff", "active", 1, None),
    ("dollar", "Dollar Person", "finesco", "Welder", Source.referral, "Sigma", "other", "active", 6, None),
    ("voided", "Voided Receipt Person", "gurair", "Fitter", Source.other, None, "other", "active", 7, None),
    ("plain", "Plain Person", "gurair", "Helper", Source.other, None, "other", "active", 3, None),
    ("late", "Late Traveller", "thinnai", "Cleaner", Source.referral, "Care", "other", "deployed", 13, None),
]
KEYS = [p[0] for p in PLACEMENTS]


@pytest.fixture
def env(schema_database):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("staff", Role.staff), ("other", Role.staff))])
        db.add(Client(id="client", name="Contact Person"))
        db.flush()
        companies = {"tawseel": ("Tawseel Test", "Kuwait", None), "finesco": ("Finesco Test", "Kuwait", None),
                     "gurair": ("Gurair Test", "UAE", None), "thinnai": ("Thinnai Test", "Malaysia", None)}
        db.add_all([Company(id=key, client_id="client", name=name, country=country) for key, (name, country, _x) in companies.items()])
        db.add(Designation(id="job", title="Anything"))
        db.flush()
        titles = {}
        for number, (key, name, company, job, source, agency, owner, status, stage, reason) in enumerate(PLACEMENTS, 1):
            if job not in titles:
                titles[job] = Designation(id=f"d-{job}", title=job)
                db.add(titles[job])
                db.flush()
            order = Order(id=f"o-{key}", ref=f"ORD-{number:02d}", company_id=company, designation_id=titles[job].id, openings=2,
                          country=None if key == "voided" else companies[company][1])    # the company's country stands in for the order's
            candidate = Candidate(id=f"c-{key}", ref=f"CND-{number:05d}", name=name, phone=f"90000000{number:02d}", source=source,
                                  source_detail=agency, owner_id=owner, care_id=f"CI-{number:03d}")
            db.add_all([order, candidate])
            db.flush()
            db.add(Application(id=f"a-{key}", candidate_id=candidate.id, order_id=order.id, owner_id=owner, current_stage=stage,
                               status=ApplicationStatus(status), closure_reason=reason))
        db.flush()

        def advance(key, amount=None, currency="INR", status="collected", total=None, **extra):
            data = {k: v for k, v in (("advanceStatus", status), ("amount", amount), ("amountCurrency", currency if amount else None),
                                      ("serviceChargeTotal", total)) if v is not None}
            db.add(ApplicationStage(application_id=f"a-{key}", stage=6, attempt=1, completed_by="admin", amount=amount,
                                    amount_currency=currency if amount else None, data={**data, **extra}))

        advance("benny", 10000, total=25000)                                          # collected and the total service charge
        advance("mahmood", 7500, total=20000)
        advance("vikram", 60000)                                                       # collected only
        for attempt, when in ((1, "2001-01-01"), (2, "2026-11-11")):                 # the latest attempt's date is the travel date
            db.add(ApplicationStage(application_id="a-vikram", stage=13, attempt=attempt, data={"travelDate": when}, completed_by="admin"))
        db.add(ApplicationStage(application_id="a-late", stage=13, attempt=1, data={}, completed_by="admin"))   # travelled, no date on file
        advance("call", None, status="pending", total=15000)                          # pending, the total service charge only
        advance("dollar", 500, "USD")                                                 # a collected amount in another currency
        advance("voided", 12500, status="pending")                                    # pending, the advance expected only
        advance("closed", 99000)                                                      # a closed placement never appears
        # remarks: the newest one of the placement's tasks; the first stage falls back on the initial call's
        stage = Task(id="t-rama", kind=TaskKind.stage, title="Offer", candidate_id="c-rama", application_id="a-rama", stage=5, owner_id="other")
        call = Task(id="t-call", kind=TaskKind.initial_call, title="Call", candidate_id="c-call", owner_id="staff")
        later = Task(id="t-rama-2", kind=TaskKind.stage, title="Offer 2", candidate_id="c-rama", application_id="a-rama", stage=5, owner_id="other")
        db.add_all([stage, call, later])
        db.flush()
        db.add_all([
            Remark(task_id="t-rama", body="Old note", author_id="other", created_at=datetime(2026, 11, 1, tzinfo=timezone.utc)),
            Remark(task_id="t-rama-2", body="Offer declined by\ncandidate  / position regarding", author_id="other", created_at=datetime(2026, 11, 5, tzinfo=timezone.utc)),
            Remark(task_id="t-call", body="=Call back after Diwali", author_id="staff", created_at=datetime(2026, 11, 2, tzinfo=timezone.utc)),
        ])
        db.commit()
    state = SimpleNamespace(sessions=sessions, engine=schema_database, actor=SimpleNamespace(id="admin", role=Role.admin))
    app = FastAPI()
    app.include_router(subject.router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[subject.get_db] = database
    app.dependency_overrides[subject.current_user] = lambda: state.actor
    with TestClient(app) as client:
        state.client = client
        yield state


def get(env, **params):
    response = env.client.get("/api/reports/client-tracker", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_name(body):
    return {row["name"]: row for row in body["rows"]}


def test_the_sheet_lists_what_is_in_progress_and_travelled_by_client_with_running_numbers(env):
    body = get(env)
    assert body["columns"] == COLUMNS
    assert [(row["sNo"], row["client"], row["name"]) for row in body["rows"]] == [
        (1, "Finesco Test", "Mahmood Sample"), (2, "Finesco Test", "Dollar Person"),          # by client, then by order (ORD-02, ORD-07)
        (3, "Gurair Test", "Rama Example"), (4, "Gurair Test", "Voided Receipt Person"), (5, "Gurair Test", "Plain Person"),
        (6, "Tawseel Test", "Benny Test"), (7, "Tawseel Test", "Call Stage Person"),
        (8, "Thinnai Test", "Vikram Invented"), (9, "Thinnai Test", "Late Traveller")]
    assert body["total"] == 9    # the closed (rejected) placement is not there
    assert "Closed Person" not in by_name(body)


def test_each_column_comes_from_the_placement_for_the_admin(env):
    rows = by_name(get(env))
    assert {k: rows["Benny Test"][k] for k in ("client", "country", "name", "position", "referral", "advance", "stage", "remarks")} == {
        "client": "Tawseel Test", "country": "Kuwait", "name": "Benny Test", "position": "Driver", "referral": "Sigma",
        "advance": "10k collected/25k", "stage": "Waiting for stamp", "remarks": ""}
    assert rows["Mahmood Sample"]["advance"] == "7.5k collected/20k"            # collected and the total service charge, from stage 6
    assert rows["Mahmood Sample"]["stage"] == "Waiting for visa" and rows["Mahmood Sample"]["referral"] == "Airport Agency"
    assert rows["Call Stage Person"]["advance"] == "Pending/15k"               # pending, the total service charge only
    assert rows["Call Stage Person"]["stage"] == "Prescreening"
    assert rows["Call Stage Person"]["remarks"] == "=Call back after Diwali"   # the initial call's remark, for a placement with none of its own
    assert rows["Dollar Person"]["advance"] == "500 Collected USD"             # not the agency's default currency: the code follows
    assert rows["Voided Receipt Person"]["advance"] == "Pending/12.5k"         # pending, the advance expected
    assert rows["Voided Receipt Person"]["country"] == "UAE"                   # the company's country when the order has none
    assert rows["Plain Person"]["advance"] == "" and rows["Plain Person"]["referral"] == ""   # nothing known: blank, never invented
    assert rows["Plain Person"]["stage"] == "Interview to be scheduled" and rows["Plain Person"]["remarks"] == ""
    assert rows["Rama Example"]["stage"] == "Waiting for signed offer"
    assert rows["Rama Example"]["remarks"] == "Offer declined by candidate / position regarding"   # newest remark, on one line
    assert rows["Vikram Invented"]["advance"] == "60k Collected"
    assert rows["Vikram Invented"]["stage"] == "Travelled on 11th NOV 2026" and rows["Vikram Invented"]["remarks"] == "Travelled"
    assert rows["Vikram Invented"]["travelled"] is True and rows["Vikram Invented"]["travelDate"] == "2026-11-11"
    assert rows["Late Traveller"]["stage"] == "Travelled" and rows["Late Traveller"]["travelDate"] is None   # no date on file: no date written


def test_advance_is_filled_for_six_of_the_nine_rows_in_the_fixture(env):
    rows = get(env)["rows"]
    assert sorted(row["name"] for row in rows if row["advance"]) == [
        "Benny Test", "Call Stage Person", "Dollar Person", "Mahmood Sample", "Vikram Invented", "Voided Receipt Person"]
    assert sum(1 for row in rows if row["advance"]) == 6 and sum(1 for row in rows if not row["advance"]) == 3


@pytest.mark.parametrize("who", ["admin", "manager", "staff"])
def test_every_role_sees_the_same_advance_cell(env, who):
    env.actor = SimpleNamespace(id=who, role=Role(who))
    rows = by_name(get(env))
    mine = {"admin": rows.keys(), "manager": rows.keys(), "staff": {"Benny Test", "Mahmood Sample", "Call Stage Person", "Vikram Invented"}}[who]
    expected = {"Benny Test": "10k collected/25k", "Mahmood Sample": "7.5k collected/20k", "Call Stage Person": "Pending/15k",
                "Vikram Invented": "60k Collected", "Dollar Person": "500 Collected USD", "Voided Receipt Person": "Pending/12.5k"}
    for name in mine:
        assert rows[name]["advance"] == expected.get(name, ""), (who, name)


def test_a_recruiter_sees_only_the_placements_they_own(env):
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    body = get(env)
    assert sorted(row["name"] for row in body["rows"]) == ["Benny Test", "Call Stage Person", "Mahmood Sample", "Vikram Invented"]
    assert [row["sNo"] for row in body["rows"]] == [1, 2, 3, 4]
    assert body["options"]["clients"] == ["Finesco Test", "Tawseel Test", "Thinnai Test"]    # the pickers offer only what they may see
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert "Benny Test" not in by_name(get(env))


def test_filters_narrow_the_sheet_and_the_numbers_start_again_at_one(env):
    assert [r["name"] for r in get(env, client="finesco test")["rows"]] == ["Mahmood Sample", "Dollar Person"]
    kuwait = get(env, country="Kuwait")
    assert [(r["sNo"], r["name"]) for r in kuwait["rows"]] == [(1, "Mahmood Sample"), (2, "Dollar Person"), (3, "Benny Test"), (4, "Call Stage Person")]
    assert sorted(r["name"] for r in get(env, agency="Sigma")["rows"]) == ["Benny Test", "Call Stage Person", "Dollar Person"]
    assert [r["name"] for r in get(env, stage=5)["rows"]] == ["Rama Example"]
    no_travel = get(env, include_travelled="false")
    assert no_travel["total"] == 7 and not any(r["travelled"] for r in no_travel["rows"])
    assert [r["sNo"] for r in no_travel["rows"]] == list(range(1, 8))
    assert get(env, stage=13)["rows"] == []                                    # travelled ones are not "in progress at stage 13"
    assert [r["name"] for r in get(env, stage=12)["rows"]] == ["Benny Test"]   # Waiting for stamp
    assert get(env, client="Nobody")["rows"] == []
    assert env.client.get("/api/reports/client-tracker", params={"stage": 14}).status_code == 422


def test_the_pickers_list_what_exists(env):
    options = get(env)["options"]
    assert options["clients"] == ["Finesco Test", "Gurair Test", "Tawseel Test", "Thinnai Test"]
    assert options["countries"] == ["Kuwait", "Malaysia", "UAE"]
    assert options["agencies"] == ["Airport Agency", "Care", "Sigma"]
    assert [s["no"] for s in options["stages"]] == list(range(1, 14))


def test_the_excel_download_is_the_clients_sheet(env):
    response = env.client.get("/api/reports/client-tracker", params={"format": "xlsx", "country": "Kuwait"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert response.headers["content-disposition"].startswith('attachment; filename="client-tracker-') and response.headers["content-disposition"].endswith('.xlsx"')
    assert response.headers["cache-control"] == "private, no-store"
    with ZipFile(BytesIO(response.content)) as book:
        workbook = book.read("xl/workbook.xml").decode()
        sheet = book.read("xl/worksheets/sheet1.xml").decode()
        styles = book.read("xl/styles.xml").decode()
    assert 'name="Client tracker"' in workbook
    assert 'state="frozen"' in sheet and 'ySplit="1"' in sheet                       # frozen header
    assert "<b/>" in styles and sheet.count(' s="1"') == 9                           # the nine header cells use the bold style
    for column, width in enumerate(tracker.WIDTHS, 1):
        assert f'<col min="{column}" max="{column}" width="{width}" customWidth="1"/>' in sheet
    header = sheet.split("</row>")[0]
    assert [part.split("</t>")[0] for part in header.split("<t xml:space=\"preserve\">")[1:]] == COLUMNS
    first_data_row = sheet.split("</row>")[1]
    assert "<v>1</v>" in first_data_row and "Mahmood Sample" in first_data_row and "7.5k collected/20k" in first_data_row   # S.No is a number
    assert sheet.count("<row ") == 5                                                  # header + four Kuwait placements


def test_the_csv_download_has_the_same_columns_and_guards_formulas(env):
    response = env.client.get("/api/reports/client-tracker", params={"format": "csv"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    table = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
    assert table[0] == COLUMNS and len(table) == 10
    call = next(row for row in table if row[3] == "Call Stage Person")
    assert call == ["7", "Tawseel Test", "Kuwait", "Call Stage Person", "Cook", "Sigma", "Pending/15k", "Prescreening", "'=Call back after Diwali"]


def test_the_advance_wording_comes_from_the_stage_6_rules():
    from app.services.advance_text import advance_cell
    facts = {"advance": (10000.0, "INR"), "status": "collected", "total": 50000.0, "expected": None, "currency": "INR", "review": False}
    assert advance_cell(facts, "INR") == "10k collected/50k" and advance_cell(None, "INR") is None
    assert advance_cell({**facts, "advance": None, "status": "pending", "total": None, "expected": 10000.0}, "INR") == "Pending/10k"
    assert advance_cell({**facts, "advance": None, "status": "pending", "total": None, "expected": None}, "INR") == "Pending"
    assert advance_cell({**facts, "advance": (500.0, "USD"), "total": None, "currency": "USD"}, "INR") == "500 Collected USD"
    assert [tracker.ordinal(d) for d in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 30, 31)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "30th", "31st"]
    assert tracker.travel_words("2026-10-15") == "Travelled on 15th OCT 2026" and tracker.travel_words(None) == "Travelled"
    assert sorted(tracker.STAGE_WORDS) == list(range(1, 14))
    assert tracker.STAGE_WORDS[6] == "Waiting for advance" and tracker.STAGE_WORDS[7] == "Waiting for passport" and tracker.STAGE_WORDS[8] == "Medical in progress"
    assert tracker.STAGE_WORDS[9] == "Waiting for PCC" and tracker.STAGE_WORDS[11] == "Waiting for Google review" and tracker.STAGE_WORDS[13] == "Waiting for air ticket"


def test_the_sheet_costs_a_fixed_number_of_statements(env):
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(env.engine, "before_cursor_execute", record)
    try:
        get(env)
    finally:
        event.remove(env.engine, "before_cursor_execute", record)
    data = [s for s in seen if "crm_applications" in s or "workflow_task_remarks" in s or "agency_settings" in s]
    assert len(data) <= 4, data      # placements, stage records, remarks, default currency: never one per row
    assert not any("crm_candidate_invoices" in s or "crm_invoice_payments" in s for s in seen)    # the invoices are not read at all
