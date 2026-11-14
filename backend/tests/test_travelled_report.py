"""Travelled candidates report: filters, totals, agency, visibility, undated placements and the export (disposable PostgreSQL)."""
import csv
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, Candidate, Client, Company, Designation, Order, ReferenceAgency, Role, Source, User,
)
from test_database_naming import _migrate, schema_database
from app.services.application_form import required_for_deployment

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
                           agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.api.routers import travelled as subject

# (candidate, company, order country, travel date or None, source, agency, recruiter, status, extra stage-11 data)
PLACEMENTS = [
    ("Akash Murugesan", "cms", "UAE", "2026-05-09", Source.referral, "Star Agency", "staff", "deployed", {"airline": "Air Arabia G9472", "pnr": "6ZA8JH"}),
    ("Arun Kumar", "cms", "UAE", "2026-05-12", Source.referral, "Star Agency", "staff", "deployed", {}),
    ("Selvam Balraj", "gcc", "UAE", "2026-05-19", Source.other, "Bulk import", "other", "deployed", {}),
    ("Sivakumar K", "arb", "Kuwait", "2026-06-01", Source.referral, "Gulf Link", "staff", "deployed", {"pnr": "FGLZXE"}),
    ("Gladwin Gnanaraj", "wood", "Malaysia", "2025-12-30", Source.referral, "Star Agency", "other", "deployed", {}),
    ("Visakh Kumar", "palm", "Malaysia", None, Source.other, None, "staff", "deployed", {}),                 # deployed, no date on file
    ("Shijo", "palm", "Malaysia", None, Source.other, None, "staff", "active", None),                          # still in progress: not travelled
    ("=Formula Name", "cms", "UAE", "2026-05-12", Source.referral, "Gulf Link", "other", "deployed", {}),
]


@pytest.fixture
def env(schema_database):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("staff", Role.staff), ("other", Role.staff))])
        db.add(Client(id="client", name="Contact"))
        db.flush()
        companies = {"cms": "Century Mechanical Systems Factory LLC", "gcc": "Gulf Center Cosmetics", "arb": "Arabian Corrosions",
                     "wood": "Woodcraft Studio Sdn Bhd", "palm": "Syarikat Perusahaan Kelapa Sawit"}
        db.add_all([Company(id=key, client_id="client", name=name) for key, name in companies.items()])
        db.add(Designation(id="job", title="Machine Operator"))
        db.flush()
        for number, (name, company, country, when, source, agency, recruiter, status, extra) in enumerate(PLACEMENTS, 1):
            order = Order(id=f"o{number}", ref=f"ORD-{number}", company_id=company, designation_id="job", openings=5, country=country)
            candidate = Candidate(id=f"c{number}", ref=f"CND-{number:05d}", name=name, phone=f"90000000{number:02d}", source=source,
                                  source_detail=agency, owner_id=recruiter, passport_no="P7654321" if number == 1 else None,
                                  care_id=f"CI-26-27-{number:03d}", traveller_id="CI-T-26-27-001" if number == 1 else None)
            db.add_all([order, candidate])
            db.flush()
            application = Application(id=f"a{number}", candidate_id=candidate.id, order_id=order.id, owner_id=recruiter, current_stage=11,
                                      status=ApplicationStatus(status))
            db.add(application)
            db.flush()
            if extra is not None:
                data = {**({"travelDate": when} if when else {}), **extra}
                # an earlier attempt of the ticket stage must not be read: only the latest attempt counts
                db.add(ApplicationStage(application_id=application.id, stage=11, attempt=1, data={"travelDate": "2001-01-01"}, completed_by=recruiter))
                db.add(ApplicationStage(application_id=application.id, stage=11, attempt=2, data=data, completed_by=recruiter))
        # Akash has all eleven checklist documents. Arun has four required documents and retained optional evidence;
        # an application-stage file does not fill a candidate slot.
        for slot, _label in required_for_deployment():
            db.add(Attachment(entity_type="candidate", entity_id="c1", slot=slot, file_name=f"{slot}.pdf", object_key=f"k/c1-{slot}"))
        for slot in ("resume", "passport", "photo", "education", "medical", "visa", "experience_certificate"):
            db.add(Attachment(entity_type="candidate", entity_id="c2", slot=slot, file_name=f"{slot}.pdf", object_key=f"k/c2-{slot}"))
        db.add(Attachment(entity_type="application", entity_id="a2", slot="signed_offer", file_name="offer.pdf", object_key="k/a2-offer"))
        db.add(Attachment(entity_type="candidate", entity_id="c2", slot="resume", version=2, file_name="resume2.pdf", object_key="k/c2-resume-2"))  # a second version is still one document
        db.commit()
    state = SimpleNamespace(sessions=sessions, actor=SimpleNamespace(id="admin", role=Role.admin))
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
    response = env.client.get("/api/travelled", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(body):
    return [row["name"] for row in body["rows"]]


def test_every_travelled_placement_is_listed_newest_first_with_its_client_and_agency(env):
    body = get(env)
    # in-progress Shijo (no date, not deployed) is not travelled; the deployed one without a date is listed, last
    assert names(body) == ["Sivakumar K", "Selvam Balraj", "=Formula Name", "Arun Kumar", "Akash Murugesan", "Gladwin Gnanaraj", "Visakh Kumar"]
    assert body["total"] == 7
    akash = next(row for row in body["rows"] if row["name"] == "Akash Murugesan")
    assert akash == {
        "applicationId": "a1", "candidateId": "c1", "ref": "CND-00001", "travellerId": "CI-T-26-27-001", "careId": "CI-26-27-001", "name": "Akash Murugesan", "client": "Century Mechanical Systems Factory LLC",
        "clientId": "client", "country": "UAE", "job": "Machine Operator", "orderRef": "ORD-1", "orderId": "o1", "passportNumber": "P7654321", "agency": "Star Agency",
        "travelDate": "2026-05-09", "airline": "Air Arabia G9472", "pnr": "6ZA8JH", "recruiter": "Staff", "status": "deployed",
        "documents": {"have": 11, "required": 11, "missing": []}}
    by_name = {row["name"]: row for row in body["rows"]}
    assert by_name["Selvam Balraj"]["agency"] is None      # "Bulk import" is provenance, not an agency
    assert by_name["Visakh Kumar"]["travelDate"] is None and by_name["Visakh Kumar"]["agency"] is None
    assert all(row["status"] == "deployed" for row in body["rows"])


def test_agency_matches_form_assignment_for_real_names_and_intake_provenance(env):
    from app.services.form_assignment import build_block, read_agencies

    values = {
        "c1": (Source.email, "  Actual Referrer  ", "Actual Referrer"),
        "c2": (Source.other, "\tBuLK  import\n", None),
        "c3": (Source.email, "email_message:receipt", None),
        "c4": (Source.whatsapp, "whatsapp_message:receipt", None),
        "c5": (Source.referral, "Bulk import", "Bulk import"),
        "c6": (Source.other, "person_review:group", None),
        "c8": (Source.other, "lead_sheet:original-lead", None),
    }
    with env.sessions() as db:
        for identifier, (source, detail, _expected) in values.items():
            candidate = db.get(Candidate, identifier)
            candidate.source, candidate.source_detail = source, detail
        db.commit()

    rows = {row["candidateId"]: row for row in get(env)["rows"]}
    with env.sessions() as db:
        agencies = read_agencies(db)
        for identifier, (_source, _detail, expected) in values.items():
            assignment = build_block(env.actor, db.get(Candidate, identifier), None, None, [], agencies, [])
            form_agency = assignment["agency"]["name"] if assignment["agency"] else None
            assert rows[identifier]["agency"] == form_agency == expected
    assert names(get(env, agency="Actual Referrer")) == ["Akash Murugesan"]
    assert "Actual Referrer" in get(env)["options"]["agencies"]
    exported = list(csv.reader(StringIO(env.client.get("/api/travelled/export", params={"format": "csv", "agency": "Actual Referrer"}).content.decode("utf-8-sig"))))
    assert len(exported) == 2 and exported[1][exported[0].index("Referral agency")] == "Actual Referrer"


def test_registered_agency_named_bulk_import_is_preserved_without_changing_source(env):
    from app.services.form_assignment import build_block, read_agencies

    with env.sessions() as db:
        candidate = db.get(Candidate, "c3")
        candidate.source, candidate.source_detail = Source.email, "  Bulk import  "
        db.add(ReferenceAgency(id="literal-import-agency", name="Contact", agency_name="  bULK IMPORT  "))
        db.commit()
    body = get(env, agency="Bulk import")
    assert names(body) == ["Selvam Balraj"]
    with env.sessions() as db:
        candidate = db.get(Candidate, "c3")
        assignment = build_block(env.actor, candidate, None, None, [], read_agencies(db), [])
        assert body["rows"][0]["agency"] == assignment["agency"]["name"] == "Bulk import"
        assert candidate.source == Source.email


def test_only_the_latest_ticket_attempt_is_read(env):
    assert "2001-01-01" not in {row["travelDate"] for row in get(env)["rows"]}


def test_a_reopened_ticket_is_no_longer_travelled(env):
    """Reopening the ticket stage takes the candidate back out of the report (an active candidate whose ticket was reopened has not flown)."""
    before = names(get(env))
    assert "=Formula Name" in before
    with env.sessions() as db:
        application_id = db.scalar(select(Application.id).join(Candidate, Candidate.id == Application.candidate_id).where(Candidate.name == "=Formula Name"))
        db.get(Application, application_id).status = ApplicationStatus.active
        for stage in db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == application_id, ApplicationStage.stage == 11)):
            stage.reopened_reason = "Ticket cancelled"
        db.commit()
    after = names(get(env))
    assert "=Formula Name" not in after and len(after) == len(before) - 1


def test_an_active_booking_does_not_count_as_confirmed_travel(env):
    with env.sessions() as db:
        db.add(ApplicationStage(application_id="a7", stage=11, attempt=1, data={"travelDate": "2026-05-01"}, completed_by="staff"))
        db.commit()
    assert "Shijo" not in names(get(env))


def test_deployed_without_a_ticket_or_any_documents_remains_travelled(env):
    with env.sessions() as db:
        db.get(Application, "a7").status = ApplicationStatus.deployed
        db.commit()
    row = next(row for row in get(env, undated="true")["rows"] if row["candidateId"] == "c7")
    assert row["status"] == "deployed" and row["travelDate"] is None
    assert row["documents"]["have"] == 0 and len(row["documents"]["missing"]) == 11


def test_invalid_or_reopened_latest_ticket_never_invents_a_travel_date(env):
    with env.sessions() as db:
        latest = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == "a1", ApplicationStage.attempt == 2))
        latest.data = {"travelDate": "2026-02-31"}
        latest2 = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == "a2", ApplicationStage.attempt == 2))
        latest2.reopened_reason = "Date to be confirmed"
        db.commit()
    rows = {row["candidateId"]: row for row in get(env, undated="true")["rows"]}
    assert rows["c1"]["travelDate"] is None and rows["c2"]["travelDate"] is None


def test_each_row_says_which_mandatory_documents_are_missing(env):
    rows = {row["name"]: row["documents"] for row in get(env)["rows"]}
    assert rows["Akash Murugesan"] == {"have": 11, "required": 11, "missing": []}
    # Arun: resume (two versions = one document), passport, photo, medical, visa and the education certificate under its older key
    assert rows["Arun Kumar"]["have"] == 4 and rows["Arun Kumar"]["required"] == 11
    assert rows["Arun Kumar"]["missing"] == ["Signed offer", "Police clearance certificate (PCC)", "Air ticket", "Undertaking letter", "Emigration", "Insurance", "Invoice"]
    assert rows["Gladwin Gnanaraj"]["have"] == 0 and len(rows["Gladwin Gnanaraj"]["missing"]) == 11   # nothing uploaded at all


def test_only_candidates_with_missing_documents_can_be_asked_for(env):
    assert names(get(env, incomplete="true")) == [n for n in names(get(env)) if n != "Akash Murugesan"]
    assert get(env, incomplete="false")["total"] == get(env)["total"]
    assert names(get(env, incomplete="true", year=2026, month=5, day=12)) == ["=Formula Name", "Arun Kumar"]
    assert get(env, incomplete="true", agency="Star Agency")["total"] == 2          # Arun and Gladwin; Akash is complete
    assert get(env, incomplete="true")["options"] == get(env)["options"]           # the pickers never shrink


def test_the_travelled_without_a_date_can_be_asked_for_and_counted(env):
    body = get(env, undated="true")
    assert names(body) == ["Visakh Kumar"] and body["total"] == 1
    assert body["rows"][0]["travelDate"] is None and body["summary"]["byYear"] == []
    assert body["options"]["undated"] == 1
    assert get(env)["options"]["undated"] == 1 and get(env, year=2026)["options"]["undated"] == 1   # never shrinks with the filters
    assert names(get(env, undated="true", agency="Not recorded")) == ["Visakh Kumar"]
    assert names(get(env, undated="true", client="Gladwin")) == []                                   # still combines with the other filters
    assert get(env, undated="false")["total"] == get(env)["total"]
    for clash in ({"year": 2026}, {"year": 2026, "month": 5}, {"from": "2026-01-01"}, {"to": "2026-12-31"}):
        refused = env.client.get("/api/travelled", params={"undated": "true", **clash})
        assert refused.status_code == 422, clash
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert get(env)["options"]["undated"] == 0                                                         # only what this person may see


def test_totals_by_year_month_day_client_agency_and_country(env):
    summary = get(env)["summary"]
    assert summary["byYear"] == [{"year": 2026, "count": 5}, {"year": 2025, "count": 1}]
    assert summary["byMonth"] == [{"month": "2026-06", "count": 1}, {"month": "2026-05", "count": 4}, {"month": "2025-12", "count": 1}]
    assert {"date": "2026-05-12", "count": 2} in summary["byDay"] and summary["byDay"][0] == {"date": "2026-06-01", "count": 1}
    assert summary["byClient"][0] == {"client": "Century Mechanical Systems Factory LLC", "count": 3}
    assert summary["byAgency"] == [{"agency": "Star Agency", "count": 3}, {"agency": "Gulf Link", "count": 2}, {"agency": "Not recorded", "count": 2}]
    assert summary["byCountry"][0] == {"country": "UAE", "count": 4}


def test_filter_by_year_month_and_day(env):
    assert names(get(env, year=2025)) == ["Gladwin Gnanaraj"]
    assert set(names(get(env, year=2026, month=5))) == {"Selvam Balraj", "=Formula Name", "Arun Kumar", "Akash Murugesan"}
    assert names(get(env, year=2026, month=5, day=12)) == ["=Formula Name", "Arun Kumar"]
    assert names(get(env, year=2026, month=6, day=1)) == ["Sivakumar K"]


def test_a_month_needs_a_year_and_a_day_needs_a_month(env):
    assert env.client.get("/api/travelled", params={"month": 5}).status_code == 422
    assert env.client.get("/api/travelled", params={"year": 2026, "day": 12}).status_code == 422
    assert env.client.get("/api/travelled", params={"year": 2026, "month": 2, "day": 30}).status_code == 422
    assert env.client.get("/api/travelled", params={"year": 2026, "month": 13}).status_code == 422
    assert env.client.get("/api/travelled", params={"from": "2026-06-01", "to": "2026-05-01"}).status_code == 422
    assert env.client.get("/api/travelled", params={"from": "yesterday"}).status_code == 422
    assert env.client.get("/api/travelled", params={"from": "20260501"}).status_code == 422
    assert env.client.get("/api/travelled", params={"year": 0}).status_code == 422


def test_from_and_to_are_inclusive(env):
    assert names(get(env, **{"from": "2026-05-12", "to": "2026-05-19"})) == ["Selvam Balraj", "=Formula Name", "Arun Kumar"]
    assert names(get(env, **{"from": "2026-06-01"})) == ["Sivakumar K"]
    assert names(get(env, to="2025-12-30")) == ["Gladwin Gnanaraj"]


def test_a_date_filter_hides_the_deployed_candidate_with_no_travel_date(env):
    assert "Visakh Kumar" in names(get(env))
    assert "Visakh Kumar" not in names(get(env, year=2026))
    assert "Visakh Kumar" in names(get(env, country="Malaysia"))   # other filters keep it


def test_filter_by_client_agency_country_and_search(env):
    assert set(names(get(env, client="century mechanical systems factory llc"))) == {"Akash Murugesan", "Arun Kumar", "=Formula Name"}
    assert set(names(get(env, agency="Gulf Link"))) == {"Sivakumar K", "=Formula Name"}
    assert set(names(get(env, agency="Not recorded"))) == {"Selvam Balraj", "Visakh Kumar"}
    assert set(names(get(env, country="Kuwait"))) == {"Sivakumar K"}
    assert names(get(env, q="cnd-00002")) == ["Arun Kumar"]
    assert names(get(env, q="fglzxe")) == ["Sivakumar K"]
    assert names(get(env, country="UAE", agency="Star Agency", year=2026)) == ["Arun Kumar", "Akash Murugesan"]
    assert get(env, client="Nobody")["total"] == 0


def test_job_order_and_passport_search_follow_actual_relationships(env):
    assert names(get(env, order="o1")) == ["Akash Murugesan"]
    assert get(env, order="o1", client="Arabian Corrosions")["total"] == 0
    assert names(get(env, q="p7654321")) == ["Akash Murugesan"]
    assert names(get(env, q="woodcraft")) == ["Gladwin Gnanaraj"]
    assert get(env, q="machine operator")["total"] == 7
    assert get(env, q="Star Agency")["total"] == 3
    choice = next(order for order in get(env)["options"]["orders"] if order["id"] == "o1")
    assert choice == {"id": "o1", "ref": "ORD-1", "job": "Machine Operator", "client": "Century Mechanical Systems Factory LLC"}


def test_totals_follow_the_filter_but_the_options_never_shrink(env):
    body = get(env, country="Kuwait")
    assert body["summary"]["byClient"] == [{"client": "Arabian Corrosions", "count": 1}]
    assert body["options"] == get(env)["options"]
    assert body["options"]["years"] == [2026, 2025]
    # the "Travel month" picker: every month somebody travelled in, newest first, with its count, whatever is filtered
    assert body["options"]["months"] == [{"month": "2026-06", "count": 1}, {"month": "2026-05", "count": 4}, {"month": "2025-12", "count": 1}]
    assert get(env, year=2026, month=5)["options"]["months"] == body["options"]["months"]
    assert body["options"]["countries"] == ["Kuwait", "Malaysia", "UAE"]
    assert body["options"]["agencies"] == ["Gulf Link", "Star Agency", "Not recorded"]
    assert "Woodcraft Studio Sdn Bhd" in body["options"]["clients"]


def test_staff_see_only_their_own_placements_and_managers_see_all(env):
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert set(names(get(env))) == {"Akash Murugesan", "Arun Kumar", "Sivakumar K", "Visakh Kumar"}
    assert set(row["recruiter"] for row in get(env)["rows"]) == {"Staff"}
    assert get(env)["summary"]["byYear"] == [{"year": 2026, "count": 3}]
    assert "Gladwin Gnanaraj" not in get(env)["options"]["clients"] and get(env)["options"]["years"] == [2026]
    assert get(env)["options"]["months"] == [{"month": "2026-06", "count": 1}, {"month": "2026-05", "count": 2}]   # only what this person may see
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert set(names(get(env))) == {"Selvam Balraj", "Gladwin Gnanaraj", "=Formula Name"}
    env.actor = SimpleNamespace(id="manager", role=Role.manager)
    assert get(env)["total"] == 7


def test_csv_export_follows_the_filters_and_is_safe_for_spreadsheets(env):
    response = env.client.get("/api/travelled/export", params={"format": "csv", "year": 2026, "month": 5, "day": 12})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="travelled-candidates.csv"'
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(StringIO(text)))
    assert rows[0] == ["Traveller ID", "Travel date", "Candidate", "Ref", "Client", "Country", "Job", "Order", "Referral agency", "Airline and flight",
                       "PNR", "Recruiter", "Status", "Missing documents", "Passport"]
    col = rows[0].index
    assert [r[col("Candidate")] for r in rows[1:]] == ["'=Formula Name", "Arun Kumar"]       # a name starting with "=" is not run as a formula
    assert rows[2][col("Travel date"):col("Travel date") + 5] == ["2026-05-12", "Arun Kumar", "CND-00002", "Century Mechanical Systems Factory LLC", "UAE"]
    assert rows[2][col("Referral agency")] == "Star Agency"
    assert rows[2][col("Missing documents")] == "Signed offer, Police clearance certificate (PCC), Air ticket, Undertaking letter, Emigration, Insurance, Invoice"
    assert rows[1][col("Missing documents")].startswith("Resume, Passport, Medical, Signed offer, Police clearance")   # nothing on file: all eleven


def test_rows_carry_the_traveller_id_and_it_can_be_searched_and_exported(env):
    rows = {row["name"]: row for row in get(env)["rows"]}
    assert rows["Akash Murugesan"]["travellerId"] == "CI-T-26-27-001"
    assert all(row["travellerId"] is None for name, row in rows.items() if name != "Akash Murugesan")   # not numbered yet: null, never missing
    assert names(get(env, q="ci-t-26-27-001")) == ["Akash Murugesan"]
    exported = list(csv.reader(StringIO(env.client.get("/api/travelled/export", params={"format": "csv", "q": "CI-T-26-27-001"}).content.decode("utf-8-sig"))))
    assert exported[0][0] == "Traveller ID" and len(exported) == 2 and exported[1][0] == "CI-T-26-27-001"


def test_rows_carry_the_care_india_id(env):
    rows = get(env)["rows"]
    assert rows and all("careId" in row for row in rows)
    by_ref = {row["ref"]: row["careId"] for row in rows}
    assert by_ref["CND-00001"] == "CI-26-27-001"
    assert all(care_id == f"CI-26-27-{int(ref[-5:]):03d}" for ref, care_id in by_ref.items())


def test_xlsx_export_holds_every_row_and_respects_staff_visibility(env):
    response = env.client.get("/api/travelled/export", params={"format": "xlsx"})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="travelled-candidates.xlsx"'
    with ZipFile(BytesIO(response.content)) as archive:
        sheet = " ".join(archive.read(n).decode("utf-8") for n in archive.namelist() if n.startswith("xl/"))
    for expected in ("Akash Murugesan", "Gladwin Gnanaraj", "Visakh Kumar", "Star Agency", "FGLZXE", "Referral agency"):
        assert expected in sheet
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    response = env.client.get("/api/travelled/export", params={"format": "xlsx"})
    with ZipFile(BytesIO(response.content)) as archive:
        sheet = " ".join(archive.read(n).decode("utf-8") for n in archive.namelist() if n.startswith("xl/"))
    assert "Gladwin Gnanaraj" in sheet and "Akash Murugesan" not in sheet


def test_export_rejects_an_unknown_format_and_a_bad_filter(env):
    assert env.client.get("/api/travelled/export", params={"format": "pdf"}).status_code == 422
    assert env.client.get("/api/travelled/export", params={"format": "csv", "month": 5}).status_code == 422


def test_relationships_and_document_counts_do_not_query_per_candidate(env):
    statements = []
    engine = env.sessions.kw["bind"]
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert get(env)["total"] == 7
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 2


def test_the_invoice_is_not_a_required_document_for_recruiters_and_managers(env):
    admin_rows = get(env)["rows"]
    assert all(row["documents"]["required"] == 11 for row in admin_rows)
    for actor in (SimpleNamespace(id="manager", role=Role.manager), SimpleNamespace(id="staff", role=Role.staff)):
        env.actor = actor
        rows = get(env)["rows"]
        assert rows and all(row["documents"]["required"] == 10 and "Invoice" not in row["documents"]["missing"] for row in rows)
        text = env.client.get("/api/travelled/export", params={"format": "csv"}).content.decode("utf-8-sig")
        assert "Invoice" not in text
