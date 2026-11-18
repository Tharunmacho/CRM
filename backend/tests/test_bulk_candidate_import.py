"""Bulk candidate import: file classification, the stage sheet, the dry run and the real import (disposable PostgreSQL)."""
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, Candidate, CandidateStatus, Client, Company, Designation,
    DocumentExtractionJob, Order, OrderStatus, Role, Source, Task, TaskKind, User,
)
from app.services import bulk_candidate_import as bulk
from app.services.candidate_review import PENDING_NAME
from test_database_naming import _migrate, schema_database

SETTINGS = SimpleNamespace(database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
                           agency_timezone="Asia/Kolkata", instance_name="test")
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.api.routers import applications, candidate_import as subject, candidates as candidates_router, files as files_router

PDF = b"%PDF-1.7 synthetic document"
ALL_DOCUMENTS = ["Application form.pdf", "Resume.pdf", "Passport.pdf", "Undertaking letter.pdf", "Insurance.pdf", "Invoice.pdf", "Photo.jpg", "Education certificate.pdf", "Offer letter.pdf", "Medical.pdf", "PCC.pdf",
                 "Visa.pdf", "Google review.png", "Visa stamping.pdf", "Emigration.pdf", "Air ticket.pdf", "Advance receipt.pdf"]
FULL_ROW = {"phone": "9876543210", "order": "ORD-1", "interview_date": "10/01/2026", "advance_amount": "15,000",
            "advance_currency": "INR", "medical_date": "20/01/2026", "passport_collected_date": "25/01/2026", "advance_date": "28/01/2026",
            "visa_arrival_date": "15/02/2026", "stamping_date": "01/03/2026", "travel_date": "20/03/2026", "airline": "AI 934", "booked_by": "employer"}


# ------------------------------------------------------------------ pure parts

@pytest.mark.parametrize("path,kind", [
    ("Application form.pdf", "application_form"), ("Job application.pdf", "application_form"),
    ("Undertaking letter.pdf", "undertaking_letter"), ("Insurance policy.pdf", "insurance"), ("Invoice.pdf", "invoice"),
    ("Application.pdf", None), ("Policy.pdf", None),
    ("Resume.pdf", "resume"), ("CV rahul.docx", "resume"), ("Passport copy.jpg", "passport"), ("passport size photo.jpg", "photo"),
    ("Offer Letter.pdf", "signed_offer"), ("Medical fit.pdf", "medical"), ("Police Clearance.pdf", "pcc"), ("Visa.pdf", "visa"),
    ("Visa Stamping.pdf", "visa_stamping"), ("stamped visa.pdf", "visa_stamping"), ("AirTicket.pdf", "air_ticket"),
    ("Advance Receipt.pdf", "advance_receipt"), ("Emigration clearance.pdf", "emigration"), ("Google review.png", "google_review"),
    ("Education certificate.pdf", "education_certificate"), ("Experience letter.pdf", "experience_certificate"), ("aadhar.pdf", "aadhaar"),
    ("Visa/scan-001.pdf", "visa"), ("Stage 8/receipt.jpg", "advance_receipt"), ("scan001.pdf", None),
])
def test_files_are_recognised_by_name_and_folder(path, kind):
    assert bulk.classify_path(path) == kind


def test_dates_amounts_and_times_are_read_the_way_the_office_writes_them():
    assert bulk.parse_date("14/03/2026").isoformat() == "2026-03-14"
    assert bulk.parse_date("14 Mar 2026").isoformat() == "2026-03-14"
    assert bulk.parse_date("2026-03-14 09:30:00").isoformat() == "2026-03-14"
    assert bulk.parse_date("46095").isoformat() == "2026-03-14"      # an Excel serial
    assert bulk.parse_date("someday") is None
    assert bulk.parse_amount("Rs. 15,000/-")[0] == 15000
    assert bulk.parse_amount("INR 15000") == (15000, "INR")
    assert bulk.parse_amount("0")[0] is None
    assert bulk.parse_time("2.30 PM") == "14:30"


# ------------------------------------------------------------------ the database-backed screen

@pytest.fixture(autouse=True)
def import_only_stores(monkeypatch):
    """These tests describe what an import stores and what "Read documents" then queues: the automatic start of reading right after
    an import (see test_import_starts_reading.py) is switched off here so a person asking for the reading is still its own step."""
    from app.api.routers import candidate_import
    monkeypatch.setattr(candidate_import, "_start_reading", lambda db, user, result: None)


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("staff", Role.staff), ("other", Role.staff))])
        db.add(Client(id="client", name="Contact"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Employer"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add_all([Order(id="o1", ref="ORD-1", company_id="company", designation_id="designation", openings=2),
                    Order(id="o2", ref="ORD-2", company_id="company", designation_id="designation", openings=1),
                    Order(id="o3", ref="ORD-3", company_id="company", designation_id="designation", openings=1, status=OrderStatus.closed)])
        db.commit()
    monkeypatch.setattr(files_router, "STORAGE", tmp_path)
    settings = SimpleNamespace(**{**vars(SETTINGS), "storage_dir": str(tmp_path)})
    for target in (bulk, "app.core.config", "app.services.lead_xlsx"):
        monkeypatch.setattr(target if not isinstance(target, str) else __import__(target, fromlist=["x"]), "get_settings", lambda: settings)
    state = SimpleNamespace(sessions=sessions, storage=tmp_path, actor=SimpleNamespace(id="admin", role=Role.admin))
    app = FastAPI()
    app.include_router(subject.router)
    app.include_router(applications.router)
    app.include_router(candidates_router.router)

    def database():
        with sessions() as db:
            yield db

    for module in (subject, applications):
        app.dependency_overrides[module.get_db] = database
        app.dependency_overrides[module.current_user] = lambda: state.actor
    with TestClient(app) as client:
        state.client = client
        yield state


def send(env, folder, names, row=None, *, default_order=None, kinds=None, recruiter="staff", named=True):
    """Import one folder. `named` plays the office that wrote the candidate's name in the sheet; without it the folder name is only a
    grouping and the candidate is "Name not read yet" until a document names them."""
    row = {"name": bulk._clean_name(folder), **(row or {})} if named else (row or {})
    spec = {"folder": folder, "row": row, "files": [{"path": n, **({"kind": kinds[n]} if kinds and n in kinds else {})} for n in names],
            "defaultOrder": default_order, "defaultRecruiterId": recruiter}
    files = [("files", (n.rsplit("/", 1)[-1], (PDF if not n.endswith((".jpg", ".png")) else b"JPEGDATA") + n.encode() + folder.encode(), "application/pdf" if not n.endswith((".jpg", ".png")) else "image/jpeg"))
             for n in names]
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=files)
    assert response.status_code == 200, response.text
    return response.json()


def stored(env, model, *where):
    with env.sessions() as db:
        return list(db.scalars(select(model).where(*where)))


def _historical_import(env, folder, files, row, *, travel_confirmed=False):
    """What the travelled-candidates script does: the service in 'already travelled' mode, one candidate per transaction."""
    uploads = [bulk.Upload(path=f"{folder}/{name}", kinds=kinds, data=PDF + name.encode(), content_type="application/pdf") for name, kinds in files]
    with env.sessions() as db:
        admin = db.get(User, "admin")
        ctx = bulk.load_context(db, default_order=None, default_recruiter="admin", wanted_orders={row.get("order", "")}, me=admin)
        ctx.historical, ctx.travel_confirmed = True, travel_confirmed
        written: list[str] = []
        result = bulk.import_candidate(db, admin, ctx, folder=folder, row=row, uploads=uploads, written=written)
        db.commit()
    return result


def _stage_rows(env, candidate_id):
    with env.sessions() as db:
        application = db.scalar(select(Application).where(Application.candidate_id == candidate_id))
        stages = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == application.id).order_by(ApplicationStage.stage)))
        return application, stages


def test_someone_who_has_flown_is_recorded_as_travelled_even_without_the_offer_copy_receipt_and_stamped_visa(env):
    files = [("Resume.pdf", ["resume"]), ("Passport.pdf", ["passport"]), ("Medical.pdf", ["medical"]), ("Visa.pdf", ["visa"]),
             ("Air ticket.pdf", ["air_ticket"])]
    result = _historical_import(env, "Ravi", files, {"order": "ORD-2", "travel_date": "09/05/2026", "airline": "Air Arabia G9472", "pnr": "6ZA8JH"})
    assert (result["action"], result["completedThrough"]) == ("deploy", 11)
    assert result["careId"] == "CI-001"
    application, stages = _stage_rows(env, result["candidateId"])
    assert application.status == ApplicationStatus.deployed and [s.stage for s in stages] == list(range(1, 12))
    assert stages[10].data["travelDate"] == "2026-05-09" and stages[10].data["pnr"] == "6ZA8JH"
    assert "file" not in stages[4].data and "receipt" not in stages[7].data and "file" not in stages[9].data   # nothing invented for the missing papers
    assert "amount" not in stages[7].data
    assert '"id": "history"' not in json.dumps([s.data for s in stages])                                       # the stand-in is never stored
    with env.sessions() as db:   # travelled: the first Traveller ID of the continuous series, separate from the Care India ID
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.traveller_id == "CI-T-001" and candidate.care_id == "CI-001"


def test_someone_the_office_says_has_flown_but_has_no_ticket_is_travelled_with_the_date_left_blank(env):
    files = [("Resume.pdf", ["resume"]), ("Passport.pdf", ["passport"]), ("Medical.pdf", ["medical"]), ("Visa stamping.pdf", ["visa_stamping"])]
    ordinary = _historical_import(env, "Sita", files, {"order": "ORD-1"})
    assert ordinary["action"] == "in_progress" and ordinary["currentStage"] == 11     # without the office's word, no ticket means not yet flown
    with env.sessions() as db:
        assert db.get(Candidate, ordinary["candidateId"]).traveller_id is None        # not travelled: no Traveller ID
    flown = _historical_import(env, "Gopal", files, {"order": "ORD-2"}, travel_confirmed=True)
    assert (flown["action"], flown["completedThrough"]) == ("deploy", 11)
    application, stages = _stage_rows(env, flown["candidateId"])
    assert application.status == ApplicationStatus.deployed
    assert "travelDate" not in stages[10].data and "file" not in stages[10].data and '"id": "history"' not in json.dumps([s.data for s in stages])
    with env.sessions() as db:   # no travel date on file: still numbered, as the next traveller
        assert db.get(Candidate, flown["candidateId"]).traveller_id == "CI-T-001"


def test_a_complete_folder_becomes_a_deployed_candidate_with_all_eleven_stages(env):
    result = send(env, "Rahul Kumar", ALL_DOCUMENTS, FULL_ROW)
    assert result["action"] == "deploy" and result["completedThrough"] == 11
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert (candidate.name, candidate.status, candidate.owner_id) == ("Rahul Kumar", CandidateStatus.deployed, "staff")
        assert candidate.traveller_id == "CI-T-001"   # numbering follows the order people become travellers, not the travel date
        assert candidate.designations == ["Welder"]
        application = db.scalar(select(Application).where(Application.candidate_id == candidate.id))
        assert application.status == ApplicationStatus.deployed and application.current_stage == 11
        stages = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == application.id).order_by(ApplicationStage.stage)))
        assert [s.stage for s in stages] == list(range(1, 12))
        assert [s.outcome for s in stages][:6] == ["Passed", "Forwarded", "Scheduled", "Selected", "Agreed", "Fit"]
        assert (float(stages[7].amount), stages[7].amount_currency) == (15000.0, "INR")
        assert stages[2].data["date"] == "2026-01-10"
        assert [s.completed_at for s in stages] == sorted(s.completed_at for s in stages)   # the history reads in order
        # the stage evidence points at the same files the profile shows
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "application", Attachment.entity_id == application.id))}
        assert slots == {"stage_5_file", "stage_8_receipt", "stage_9_file", "stage_10_file", "stage_11_file"}
        documents = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id))}
        assert documents == {"application_form", "resume", "passport", "photo", "education_certificate", "signed_offer", "medical", "pcc", "visa",
                             "google_review", "visa_stamping", "emigration", "air_ticket", "undertaking_letter", "insurance", "invoice"}
        # the import only stores: nothing is queued to be read until a person asks, and no stage task is left open
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate.id)) == 0
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate.id)) == 0


def test_missing_documents_leave_the_last_stage_open_instead_of_blocking(env):
    names = [n for n in ALL_DOCUMENTS if n not in ("PCC.pdf", "Emigration.pdf")]
    result = send(env, "Anil", names, {**FULL_ROW, "phone": "9876500001"})
    assert (result["action"], result["completedThrough"], result["currentStage"]) == ("in_progress", 10, 11)
    assert any("PCC" in issue["message"] for issue in result["issues"])
    with env.sessions() as db:
        application = db.scalar(select(Application).where(Application.candidate_id == result["candidateId"]))
        assert application.status == ApplicationStatus.active and application.current_stage == 11
        assert application.draft["file"]["name"] == "Anil-Air_ticket.pdf"      # stored as "<Name>-<Document>", not under the original file name
        task = db.scalar(select(Task).where(Task.application_id == application.id, Task.completed_at.is_(None)))
        assert task is not None and task.stage == 11


def test_only_what_the_folder_proves_is_recorded_and_the_rest_is_done_on_the_stage_screen(env):
    # A resume, a passport and an offer letter: the offer proves stages 1-5, nothing proves the medical.
    result = send(env, "Bala", ["Resume.pdf", "Passport.pdf", "Offer letter.pdf"], {"phone": "9876500002", "order": "ORD-1", "interview_date": "10/01/2026"})
    assert (result["action"], result["completedThrough"], result["currentStage"]) == ("in_progress", 5, 6)
    with env.sessions() as db:
        application = db.scalar(select(Application).where(Application.candidate_id == result["candidateId"]))
        assert application.current_stage == 6 and application.draft == {}
        assert db.scalar(select(func.count()).select_from(ApplicationStage).where(ApplicationStage.application_id == application.id)) == 5
        assert db.get(Candidate, result["candidateId"]).status == CandidateStatus.in_process
    # ... and the recruiter carries on exactly as for any other candidate.
    with env.sessions() as db:
        application_id = db.scalar(select(Application.id).where(Application.candidate_id == result["candidateId"]))
    done = env.client.post(f"/api/applications/{application_id}/complete", json={"lock_version": 0, "data": {"applied": "yes", "result": "fit", "feedback": "Medical done; fit."}})
    assert done.status_code == 200, done.text
    assert done.json()["currentStage"] == 7


def test_a_folder_with_no_proof_of_any_stage_starts_at_prescreening(env):
    result = send(env, "Chitra", ["Resume.pdf"], {"phone": "9876500003", "order": "ORD-1"})
    assert (result["action"], result["completedThrough"], result["currentStage"]) == ("in_progress", 0, 1)


def test_no_order_means_documents_and_an_initial_call_only(env):
    result = send(env, "Dev", ["Resume.pdf", "Passport.pdf"], {"phone": "9876500004"})
    assert result["action"] == "documents_only"
    with env.sessions() as db:
        assert db.get(Candidate, result["candidateId"]).care_id.startswith("CI-")
        assert db.scalar(select(func.count()).select_from(Application).where(Application.candidate_id == result["candidateId"])) == 0
        assert db.scalar(select(Task.kind).where(Task.candidate_id == result["candidateId"])) == TaskKind.initial_call


def test_a_closed_order_gets_no_half_finished_placement(env):
    result = send(env, "Esha", ["Resume.pdf", "Offer letter.pdf"], {"phone": "9876500005", "order": "ORD-3", "interview_date": "10/01/2026"})
    assert result["action"] == "documents_only" and any("not open" in i["message"] for i in result["issues"])


def test_the_last_opening_is_not_over_filled(env):
    first = send(env, "Farid", ALL_DOCUMENTS, {**FULL_ROW, "phone": "9876500006", "order": "ORD-2"})
    assert first["action"] == "deploy"
    second = send(env, "Gita", ALL_DOCUMENTS, {**FULL_ROW, "phone": "9876500007", "order": "ORD-2"})
    assert (second["action"], second["currentStage"]) == ("in_progress", 11)
    with env.sessions() as db:
        assert db.get(Order, "o2").status == OrderStatus.filled
        assert db.scalar(select(func.count()).select_from(Application).where(Application.order_id == "o2", Application.status == ApplicationStatus.deployed)) == 1


def test_a_candidate_already_in_the_crm_is_skipped_and_nothing_is_written(env):
    send(env, "Hari", ["Resume.pdf"], {"phone": "9876500008"})
    before = len(list(env.storage.rglob("*-*")))
    again = send(env, "Hari again", ["Resume.pdf"], {"phone": "+91 98765 00008"})
    assert again["action"] == "skip" and "Already in the CRM" in again["issues"][0]["message"]
    assert len(stored(env, Candidate)) == 1
    assert len(list(env.storage.rglob("*-*"))) == before


def test_a_bad_date_stops_that_candidate_with_a_clear_message(env):
    result = send(env, "Indu", ["Resume.pdf"], {"phone": "9876500009", "order": "ORD-1", "interview_date": "next week"})
    assert result["action"] == "skip" and "not a date" in result["issues"][0]["message"]
    assert stored(env, Candidate) == []


def test_a_staff_member_imports_for_themselves_whatever_the_sheet_says(env):
    env.actor = SimpleNamespace(id="other", role=Role.staff, name="Other")
    result = send(env, "Jay", ["Resume.pdf"], {"phone": "9876500010", "recruiter": "admin@example.test"})
    with env.sessions() as db:
        assert db.get(Candidate, result["candidateId"]).owner_id == "other"


def test_a_manager_imports_for_themselves_too_only_the_admin_chooses_the_recruiter(env):
    with env.sessions() as db:
        db.add(User(id="mgr", name="Mgr", email="mgr@example.test", role=Role.manager, active=True))
        db.commit()
    env.actor = SimpleNamespace(id="mgr", role=Role.manager, name="Mgr")
    result = send(env, "Jay", ["Resume.pdf"], {"phone": "9876500011", "recruiter": "staff@example.test"})
    with env.sessions() as db:
        assert db.get(Candidate, result["candidateId"]).owner_id == "mgr"


def test_check_matches_sheet_rows_to_folders_and_reports_unmatched_rows(env):
    body = {"folders": [{"folder": "Kiran", "files": [{"path": "Resume.pdf"}, {"path": "Visa Stamping.pdf"}, {"path": "notes.txt"}]}],
            "rows": [{"name": "Kiran", "phone": "9876500011", "order": "ORD-1", "rowNumber": 2}, {"name": "Nobody", "phone": "1234567890", "rowNumber": 3}],
            "defaultRecruiterId": "staff"}
    response = env.client.post("/api/candidate-import/check", json=body)
    assert response.status_code == 200, response.text
    plan = response.json()["plans"][0]
    assert plan["hasRow"] and plan["order"] == "ORD-1" and plan["recruiter"] == "Staff"
    assert {f["path"]: f["kind"] for f in plan["files"]} == {"Resume.pdf": "resume", "Visa Stamping.pdf": "visa_stamping", "notes.txt": None}
    assert [f["skipped"] for f in plan["files"]] == [False, False, True]
    assert [row["name"] for row in response.json()["unmatchedRows"]] == ["Nobody"]
    assert stored(env, Candidate) == []      # a dry run writes nothing


def test_the_downloadable_sheet_reads_back(env):
    sheet = env.client.post("/api/candidate-import/template", json={"folders": ["01 - Rahul_Kumar", "Anil"]})
    assert sheet.status_code == 200
    read = env.client.post("/api/candidate-import/manifest", files={"file": ("sheet.xlsx", BytesIO(sheet.content), "application/octet-stream")})
    assert read.status_code == 200, read.text
    rows = read.json()["rows"]
    assert [(r["folder"], r["name"]) for r in rows] == [("01 - Rahul_Kumar", "Rahul Kumar"), ("Anil", "Anil")]


def test_the_folder_alone_is_enough_no_sheet_no_phone(env):
    """Documents and a job order are all the screen asks for; the reading of the resume and passport fills in the rest."""
    # A travel date is the one thing a folder cannot give: the ticket alone no longer deploys (stage 11 needs the ticket and its date).
    without_date = send(env, "Mohan Das", ALL_DOCUMENTS, None, default_order="ORD-1", recruiter=None)
    assert (without_date["action"], without_date["completedThrough"], without_date["currentStage"]) == ("in_progress", 10, 11)
    assert any("Travel date" in i["message"] for i in without_date["issues"])
    result = send(env, "Mohan Raj", ALL_DOCUMENTS, {"travel_date": "20/03/2026"}, default_order="ORD-1", recruiter=None)
    assert (result["action"], result["completedThrough"]) == ("deploy", 11)
    assert any("interview date" in i["message"] for i in result["issues"])
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.phone == "" and candidate.needs_review and candidate.status == CandidateStatus.deployed
        assert candidate.owner_id == "admin"                      # nobody was named: the person importing owns it
        stages = {s.stage: s for s in db.scalars(select(ApplicationStage).join(Application).where(Application.candidate_id == candidate.id))}
        assert len(stages) == 11 and "date" not in stages[3].data and stages[8].amount is None
        assert stages[8].data["receipt"]["name"] == "Mohan_Raj-Advance_receipt_stage_8.pdf"
    # ... and the same person is not imported twice.
    again = send(env, "mohan raj", ["Resume.pdf"], None, default_order="ORD-1")
    assert again["action"] == "skip" and "Already in the CRM" in again["issues"][0]["message"]


# ------------------------------------------------------------------ files whose names say nothing

def text_pdf(*lines: str) -> bytes:
    import pymupdf
    document = pymupdf.open()
    page = document.new_page()
    for number, line in enumerate(lines):
        page.insert_text((72, 72 + number * 18), line)
    return document.tobytes()


def test_only_a_resume_passport_or_aadhaar_is_ever_read_from_the_content(env):
    cv = text_pdf("CURRICULUM VITAE", "Career Objective", "To work as a steel fixer", "Work Experience", "Steel fixer, Al Ahmadi Co, Jan 2018 - Mar 2022",
                  "Skills", "Education", "Declaration", "Phone 9876543210 email a@b.com")
    assert bulk.classify_content(cv) == ["resume"]
    # an offer letter or a ticket is sorted by name only: its content is not read to be sorted
    assert bulk.classify_content(text_pdf("E-TICKET", "PNR: XY12AB", "Itinerary Chennai to Kuwait")) == []
    assert bulk.classify_content(text_pdf("We are pleased to offer you the position of Steel Fixer")) == []
    assert bulk.classify_content(text_pdf("Some unrelated page")) == []
    assert bulk.classify_content(b"not a document") == []


def test_an_unnamed_cv_is_filed_as_the_resume_on_import(env):
    spec = {"folder": "Nirmal", "row": {}, "files": [{"path": "scan0001.pdf"}], "defaultOrder": None, "defaultRecruiterId": None}
    cv = text_pdf("CURRICULUM VITAE", "Career Objective", "To work as a welder", "Work Experience", "Welder, Al Ahmadi Co, Jan 2018 - Mar 2022",
                  "Skills", "Education", "Declaration", "Phone 9876543210 email a@b.com")
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=[("files", ("scan0001.pdf", cv, "application/pdf"))])
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == response.json()["candidateId"]))}
    assert slots == {"resume"}


MIXED = ["31MAY-3-PAX-CHENNAI-KUWAIT.pdf", "K.Sivakumar-signed-offer.jpeg", "K.Sivakumar-Steel-fixer.pdf", "M.Poomalai-Steel-fixer.pdf",
         "Poomalai-visa-medical-pcc.pdf", "Subramaniyan-Steel-fixer.pdf", "Subramanian-Medical_1.pdf", "Subramanian-visa.jpg", "scan1.pdf"]


def test_a_folder_that_holds_several_people_is_separated_by_the_names_on_the_files():
    split = bulk.split_people(MIXED)
    assert [p["name"] for p in split["people"]] == ["K. Sivakumar", "M. Poomalai", "Subramanian"]
    assert split["people"][0]["paths"] == ["K.Sivakumar-signed-offer.jpeg", "K.Sivakumar-Steel-fixer.pdf", "31MAY-3-PAX-CHENNAI-KUWAIT.pdf"]
    assert split["shared"] == ["31MAY-3-PAX-CHENNAI-KUWAIT.pdf"] and split["left_out"] == ["scan1.pdf"]   # the group ticket goes to all
    assert bulk.split_people(["Resume.pdf", "Passport.pdf", "Offer letter.pdf"]) is None
    assert bulk.split_people(["Rahul-resume.pdf", "Rahul-passport.pdf"]) is None
    assert bulk.classify_path("31MAY-3-PAX-CHENNAI-KUWAIT.pdf") == "air_ticket"


def test_check_and_import_treat_each_person_of_a_mixed_folder_as_a_candidate(env):
    body = {"folders": [{"folder": "sivakumar", "files": [{"path": n} for n in MIXED]}], "rows": [], "defaultRecruiterId": None}
    plans = env.client.post("/api/candidate-import/check", json=body).json()["plans"]
    assert [p["folder"] for p in plans] == ["sivakumar / K. Sivakumar", "sivakumar / M. Poomalai", "sivakumar / Subramanian"]
    assert all(p["parent"] == "sivakumar" and p["name"] == PENDING_NAME and p["nameSource"] == "pending" for p in plans)   # file names group, they never name
    assert {f["path"] for f in plans[0]["files"]} == {"K.Sivakumar-signed-offer.jpeg", "K.Sivakumar-Steel-fixer.pdf", "31MAY-3-PAX-CHENNAI-KUWAIT.pdf"}
    assert any("scan1.pdf" in i["message"] for i in plans[0]["issues"])
    person = plans[2]
    spec = {"folder": person["folder"], "name": person["name"], "row": {}, "files": [{"path": f["path"]} for f in person["files"]]}
    files = [("files", (f["path"], PDF if f["path"].endswith(".pdf") else b"JPEGDATA", "application/pdf" if f["path"].endswith(".pdf") else "image/jpeg")) for f in person["files"]]
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=files)
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        candidate = db.get(Candidate, response.json()["candidateId"])
        assert candidate.name == PENDING_NAME and "subramanian" not in json.dumps(candidate.field_sources).lower()
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id))}
    assert {"medical", "visa", "air_ticket"} <= slots


def test_one_scan_of_several_documents_counts_for_each_and_copies_are_kept_once(env):
    assert bulk.classify_path_kinds("K.Sivakumar-signed offer,pp,aadhar.pdf") == ["signed_offer", "passport", "aadhaar"]
    assert bulk.classify_path_kinds("Poomalai-visa medical pcc.pdf") == ["pcc", "medical", "visa"]
    assert bulk.classify_path_kinds("Poomalai-visa stamping.jpg") == ["visa_stamping"]
    names = ["Rahul-signed offer,pp,aadhar.pdf", "Rahul-visa medical pcc.pdf", "Rahul-Medical.pdf", "Rahul-Medical_1.pdf"]
    spec = {"folder": "Rahul", "row": {"name": "Rahul"}, "files": [{"path": n} for n in names], "defaultOrder": None, "defaultRecruiterId": None}
    contents = {names[0]: PDF + b" a", names[1]: PDF + b" b", names[2]: PDF + b" c", names[3]: PDF + b" c"}   # the last two are identical
    files = [("files", (n, contents[n], "application/pdf")) for n in names]
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=files)
    assert response.status_code == 200, response.text
    assert any("copy of" in i["message"] for i in response.json()["issues"])
    with env.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == response.json()["candidateId"])))
    by_slot = {}
    for row in rows:
        by_slot.setdefault(row.slot, []).append(row)
    assert set(by_slot) == {"signed_offer", "passport", "aadhaar", "pcc", "medical", "visa"}
    assert {r.version for r in by_slot["medical"]} == {1, 2}                       # the combined scan, then Medical.pdf (the copy is not added again)
    scan = [r for r in rows if r.slot in ("pcc", "medical", "visa") and r.file_name == "Rahul-Police_clearance_certificate_PCC_Medical_Visa.pdf"]   # the scan carries all three names
    assert {r.slot for r in scan} == {"pcc", "medical", "visa"} and len({r.object_key for r in scan}) == 1   # one stored file behind pcc, medical and visa


def test_a_cv_saved_under_its_trade_is_recognised_at_once():
    assert bulk.classify_path_kinds("K.Sivakumar-Steel fixer.pdf") == ["resume"]
    assert bulk.classify_path_kinds("Welder offer letter.pdf") == ["signed_offer"]      # a trade only counts when nothing else matches
    assert bulk.classify_path_kinds("scan1.pdf") == []


def _jobs(env, candidate_id):
    return stored(env, DocumentExtractionJob, DocumentExtractionJob.candidate_id == candidate_id)


def _read_again(env, ids):
    response = env.client.post("/api/candidate-import/read-again", json={"candidateIds": ids})
    assert response.status_code == 200, response.text
    return response.json()["queued"]


def test_the_aadhaar_card_is_its_own_document_and_is_read_only_when_a_person_asks(env):
    assert bulk.classify_path_kinds("K.Sivakumar-signed offer,pp,aadhar.pdf") == ["signed_offer", "passport", "aadhaar"]
    result = send(env, "Ravi", ["Ravi-signed offer,pp,aadhar.pdf", "Ravi-Steel fixer.pdf", "Ravi-visa medical pcc.pdf"], {"phone": "9876500020"})
    with env.sessions() as db:
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"]))}
    assert {"signed_offer", "passport", "aadhaar", "resume", "pcc", "medical", "visa"} <= slots
    assert _jobs(env, result["candidateId"]) == []      # the import only stores the files
    assert _read_again(env, [result["candidateId"]]) == 2
    read = sorted(j.kind for j in _jobs(env, result["candidateId"]))
    assert read == ["passport", "resume"]       # nothing else is read, and the scan holding a passport and an Aadhaar card is read once


def test_failed_readings_can_be_put_back_in_the_queue(env):
    result = send(env, "Sita", ["Sita-Steel fixer.pdf", "Sita-signed offer,pp,aadhar.pdf"], {"phone": "9876500030"})
    assert _read_again(env, [result["candidateId"]]) == 2
    with env.sessions() as db:
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == result["candidateId"])):
            job.status = "failed"
        db.commit()
    assert _read_again(env, [result["candidateId"]]) == 2
    assert len(_jobs(env, result["candidateId"])) == 2           # the failed jobs were put back, not duplicated
    assert {j.status for j in _jobs(env, result["candidateId"])} == {"pending"}


def test_an_import_stores_every_file_in_its_slot_under_standard_names_and_creates_no_reading_job(env):
    names = ["Gunavelan-signed offer,pp,aadhar.pdf", "Gunavelan-Steel fixer.pdf", "Photo.jpg", "Visa.pdf", "Medical.pdf"]
    result = send(env, "Gunavelan-alnoor", names, {"phone": "9876500110"})
    with env.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"])))
        by_slot = {row.slot: row for row in rows}
        assert set(by_slot) == {"signed_offer", "passport", "aadhaar", "resume", "photo", "visa", "medical"}
        assert all(row.file_name.startswith("Gunavelan_alnoor-") and row.version == 1 for row in rows)
        assert by_slot["resume"].file_name == "Gunavelan_alnoor-Resume.pdf" and by_slot["photo"].file_name == "Gunavelan_alnoor-Photo.jpg"
        # the scan of three documents is ONE stored file behind its three slots
        assert len({by_slot[slot].object_key for slot in ("signed_offer", "passport", "aadhaar")}) == 1
        candidate = db.get(Candidate, result["candidateId"])      # nothing was read; the sheet gave the name
        assert candidate.name == "Gunavelan-alnoor" and candidate.field_sources == {}
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 0
    assert _jobs(env, result["candidateId"]) == []


def test_read_again_queues_one_job_per_distinct_stored_file_and_a_second_call_queues_nothing(env):
    # the one scan sits in the passport and Aadhaar slots (and a signed offer); the resume is its own file
    names = ["Ravi-signed offer,pp,aadhar.pdf", "Ravi-Steel fixer.pdf", "Ravi-visa medical pcc.pdf", "Photo.jpg"]
    result = send(env, "Ravi", names, {"phone": "9876500111"})
    candidate_id = result["candidateId"]
    assert _jobs(env, candidate_id) == []
    assert _read_again(env, [candidate_id]) == 2
    jobs = _jobs(env, candidate_id)
    assert sorted(job.kind for job in jobs) == ["passport", "resume"] and {job.status for job in jobs} == {"pending"}
    assert len({job.object_key for job in jobs}) == 2                       # one job per distinct stored file
    assert _read_again(env, [candidate_id]) == 0                             # nothing new to read
    assert len(_jobs(env, candidate_id)) == 2
    with env.sessions() as db:                                               # a job that failed is queued again, and counted
        failed = next(job for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)) if job.kind == "resume")
        failed.status = "failed"
        db.commit()
    assert _read_again(env, [candidate_id]) == 1
    jobs = _jobs(env, candidate_id)
    assert len(jobs) == 2 and {job.status for job in jobs} == {"pending"}
    assert _read_again(env, [candidate_id]) == 0


def test_one_scan_in_the_passport_aadhaar_and_resume_slots_is_one_job(env):
    names = ["Latha-resume,pp,aadhar.pdf"]
    assert sorted(bulk.classify_path_kinds(names[0])) == ["aadhaar", "passport", "resume"]
    result = send(env, "Latha", names, {"phone": "9876500112"})
    with env.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"])))
    assert {row.slot for row in rows} == {"resume", "passport", "aadhaar"} and len({row.object_key for row in rows}) == 1
    assert _jobs(env, result["candidateId"]) == []
    assert _read_again(env, [result["candidateId"]]) == 1
    assert len(_jobs(env, result["candidateId"])) == 1
    assert _read_again(env, [result["candidateId"]]) == 0
    assert len(_jobs(env, result["candidateId"])) == 1


def test_every_path_that_queues_a_reading_agrees_on_what_is_sent_to_the_reader():
    from app.services import application_form as form, document_reading, document_splitting
    assert bulk.READ_SLOTS == ("resume", "passport", "aadhaar")              # the identity kinds, told apart by content (split_by_content)
    assert bulk.SENT_TO_READER == ("resume", "passport", "aadhaar", "education_certificate")
    assert set(form.READ_SLOTS) == set(bulk.SENT_TO_READER) == set(document_reading.READABLE_SLOTS) == set(document_splitting.READ_SLOTS)
    assert {spec[0] for spec in form.SLOT_SPECS if spec[5]} == set(bulk.SENT_TO_READER)
    assert next(spec for spec in form.SLOT_SPECS if spec[0] == "education_certificate")[6] is False      # still one file
    # the job kind column is 20 characters: no slot sent to the reader may be stored under its own name unless it fits
    from app.db.models import DocumentExtractionJob as Job
    assert all(len(form.JOB_KIND.get(slot, slot)) <= Job.__table__.c.kind.type.length for slot in bulk.SENT_TO_READER)
    assert len("education_certificate") > Job.__table__.c.kind.type.length and form.JOB_KIND["education_certificate"] == "resume"


EDUCATION_FOLDER = ["Asha-Passport.pdf", "Asha-Resume.pdf", "Asha-Education certificate.pdf", "Asha-Medical.pdf"]


def _education_read_output(name):
    raw = {"name": name, "education": [{"degree": "B.Sc Physics", "institution": "Madras University", "year": "2015"}]}
    from app.services import form_extraction as fx
    return {"status": "complete", "coverage": {"complete": True}, "inspection": {"resume_pages": [1]},
            "extractions": {"resume": {"fields": fx.resume_values(raw), "raw": raw, "selected_pages": [1]}}}


def test_an_education_certificate_is_read_again_once_requeued_when_failed_and_its_details_land_on_the_profile(env, monkeypatch):
    result = send(env, "Asha", EDUCATION_FOLDER, {"phone": "9876500120"})
    candidate_id = result["candidateId"]
    assert _jobs(env, candidate_id) == []                                    # an import only stores
    assert _read_again(env, [candidate_id]) == 3                             # passport, resume and the education certificate; not the medical
    jobs = _jobs(env, candidate_id)
    with env.sessions() as db:
        education = db.scalar(select(Attachment).where(Attachment.entity_id == candidate_id, Attachment.slot == "education_certificate"))
    job = next(job for job in jobs if job.attachment_id == education.id)
    assert job.kind == "resume" and len(job.kind) <= 20 and job.status == "pending"       # the short kind: 'education_certificate' would not fit
    with env.sessions() as db:
        assert db.get(DocumentExtractionJob, job.id).draft["applicationForm"]["slot"] == "education_certificate"
    assert job.object_key == education.object_key
    assert sorted(j.kind for j in jobs) == ["passport", "resume", "resume"] and len({j.group_id for j in jobs}) == 3     # no uniqueness collision
    assert _read_again(env, [candidate_id]) == 0                             # a second call queues nothing
    assert len(_jobs(env, candidate_id)) == 3

    with env.sessions() as db:                                               # a failed education reading is queued again, not duplicated
        db.get(DocumentExtractionJob, job.id).status = "failed"
        db.commit()
    assert _read_again(env, [candidate_id]) == 1
    assert len(_jobs(env, candidate_id)) == 3 and {j.status for j in _jobs(env, candidate_id)} == {"pending"}

    # the reader finishes the education file: the education details land through the same apply step, and the certificate's pages are
    # never filed as a Resume
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    with env.sessions() as db:
        read = db.get(DocumentExtractionJob, job.id)
        read.state = {"documents": {"resume": {"object_key": "manual/x/resume-abc.pdf", "content_hash": "abc", "file_name": "resume.pdf",
                                               "content_type": "application/pdf", "size_kb": 1, "selected_pages": [1]}}}
        before = db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == candidate_id, Attachment.slot == "resume"))
        application_form.apply_job_output(db, read, _education_read_output("Asha Kumar"))
        read.status = "completed"
        db.commit()
    with env.sessions() as db:
        candidate = db.get(Candidate, candidate_id)
        assert "B.Sc Physics" in json.dumps(candidate.education)
        assert not candidate.resume_text                                     # an education page is not the candidate's resume
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == candidate_id, Attachment.slot == "resume")) == before
        form = db.get(DocumentExtractionJob, job.id).draft["applicationForm"]
        assert form["result"]["status"] == "done" and form["attachmentId"] == education.id and form["attachedDocuments"] == {}
    assert _read_again(env, [candidate_id]) == 0


def test_read_again_skips_candidates_the_caller_may_not_work_on_and_unknown_ids(env):
    mine = send(env, "Gunavelan-alnoor", ["Resume.pdf", "Passport.pdf"], {"phone": "9876500113"}, recruiter="staff")
    ids = [mine["candidateId"], "no-such-candidate"]
    env.actor = SimpleNamespace(id="other", role=Role.staff)                 # another staff member: nothing is queued
    assert _read_again(env, ids) == 0
    assert _jobs(env, mine["candidateId"]) == []
    env.actor = SimpleNamespace(id="staff", role=Role.staff)                 # the owner: both files, unknown id ignored
    assert _read_again(env, ids) == 2
    assert len(_jobs(env, mine["candidateId"])) == 2
    assert _read_again(env, []) == 0


def test_read_again_takes_at_most_two_hundred_ids_like_the_status_call(env):
    assert _read_again(env, [f"id-{n}" for n in range(200)]) == 0
    assert env.client.post("/api/candidate-import/read-again", json={"candidateIds": [f"id-{n}" for n in range(201)]}).status_code == 422


def test_what_the_local_reading_verifies_fills_the_form_at_once(env):
    from app.services.application_form import apply_local_patterns
    result = send(env, "Mohan Lal", ["Mohan Lal-Steel fixer.pdf"], None)
    patterns = {"passport": {"passport_number": "M1234567", "date_of_birth": "1990-05-17", "date_of_expiry": "2031-01-01", "surname": "LAL",
                             "given_names": "MOHAN", "nationality": "IND", "mrz_checksums_valid": True, "number_ok": True, "birth_ok": True, "expiry_ok": True},
                "resume": {"email": "mohan@example.com", "mobile": "9840012345"}}
    with env.sessions() as db:
        filled = apply_local_patterns(db, result["candidateId"], patterns)
    assert {"passport.passportNumber", "personal.dateOfBirth", "passport.expiryDate"} <= set(filled)
    # contacts printed on a resume cannot identify their owner, so the local reading never files them (the online reader
    # does, after its identity check)
    assert not {"personal.mobileNumber", "personal.email"} & set(filled)
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert (candidate.passport_no, str(candidate.dob), str(candidate.passport_expiry)) == ("M1234567", "1990-05-17", "2031-01-01")
        assert not candidate.phone and not candidate.email and candidate.name == "Mohan Lal"
        assert candidate.needs_review                          # the phone is still the gap: a resume's own contacts are not used here
        assert (candidate.field_sources or {}).get("passport.passportNumber") == "extracted"   # the online reader may still improve on it
    # a check digit that fails is not trusted
    with env.sessions() as db:
        assert apply_local_patterns(db, result["candidateId"], {"passport": {"passport_number": "X9999999", "number_ok": False}}) == []


def test_an_imported_candidate_can_be_deleted_even_when_on_a_job_and_frees_the_opening(env):
    done = send(env, "Farid", ALL_DOCUMENTS, {**FULL_ROW, "phone": "9876500040", "order": "ORD-2"})
    part = send(env, "Gita", ["Gita-Steel fixer.pdf", "Gita-signed offer.jpg"], {"phone": "9876500041", "order": "ORD-1", "interview_date": "10/01/2026"})
    assert (done["action"], part["action"]) == ("deploy", "in_progress")
    with env.sessions() as db:
        assert db.get(Order, "o2").status == OrderStatus.filled
    for result in (done, part):
        response = env.client.delete(f"/api/candidates/{result['candidateId']}")
        assert response.status_code == 200, response.text
    with env.sessions() as db:
        assert db.get(Order, "o2").status == OrderStatus.open        # the deployment that filled it is gone
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0
        assert db.scalar(select(func.count()).select_from(Application)) == 0
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0


def test_an_import_with_a_referral_agency_is_stored_as_a_referral_and_can_still_be_deleted(env):
    done = send(env, "Hari", ALL_DOCUMENTS, {**FULL_ROW, "phone": "9876500050", "order": "ORD-2", "agency": "  Star   Agency "})
    assert done["action"] == "deploy"
    with env.sessions() as db:
        candidate = db.get(Candidate, done["candidateId"])
        assert (candidate.source, candidate.source_detail) == (Source.referral, "Star Agency")   # what the Travelled report reads as the agency
    response = env.client.delete(f"/api/candidates/{done['candidateId']}")      # "Bulk import" is gone from source_detail, the audit trail says it was imported
    assert response.status_code == 200, response.text


def test_a_candidate_added_by_hand_on_a_job_still_cannot_be_deleted(env):
    with env.sessions() as db:
        db.add(Candidate(id="hand", ref="CND-H", name="By Hand", phone="9000000099", source=Source.walk_in, owner_id="staff"))
        db.commit()
    mapped = env.client.post("/api/applications", json={"candidate_id": "hand", "order_id": "o1"})
    assert mapped.status_code == 201, mapped.text
    refused = env.client.delete("/api/candidates/hand")
    assert refused.status_code == 409


def test_a_job_can_be_chosen_for_one_candidate_in_the_list(env):
    body = {"folders": [{"folder": "Kiran", "files": [{"path": "Kiran-Steel fixer.pdf"}]}, {"folder": "Lata", "files": [{"path": "Lata-Steel fixer.pdf"}]}],
            "rows": [], "defaultOrder": "ORD-1", "defaultRecruiterId": None, "jobs": {"Lata": "ORD-2"}}
    plans = env.client.post("/api/candidate-import/check", json=body).json()["plans"]
    assert [(p["folder"], p["order"]) for p in plans] == [("Kiran", "ORD-1"), ("Lata", "ORD-2")]
    assert plans[1]["row"]["order"] == "ORD-2"          # the import call carries it, so the candidate lands on that job
    spec = {"folder": "Lata", "name": "Lata", "row": plans[1]["row"], "files": [{"path": "Lata-Steel fixer.pdf"}], "defaultOrder": "ORD-1"}
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=[("files", ("Lata-Steel fixer.pdf", PDF + b" lata", "application/pdf"))])
    assert response.status_code == 200 and response.json()["order"] == "ORD-2"


# ------------------------------------------------------------------ one folder, one or several candidates; the folder name is only a stand-in

RASHID_FOLDER = "rashid khan-alnoor"
RASHID_FILES = ["EVisa-123456789.pdf", "Itinerary AB1CD2 (1).pdf", "RASHIDKHAN-SIGNED OFFER.jpeg", "RASHIDKHAND-WhatsApp Image.jpeg",
               "Rashid Khan Nawazkhan-picker.pdf", "Rashid khan - PP.pdf", "Rashiid khan-visa,medical,pcc s.pdf", "rashidkhan (3 files merged) 10.pdf"]
TWO_PEOPLE = ["Ramesh-resume.pdf", "Ramesh-passport.pdf", "Suresh-resume.pdf", "Suresh-visa.pdf"]


def _check(env, folder, names, **extra):
    body = {"folders": [{"folder": folder, "files": [{"path": n} for n in names]}], "rows": [], "defaultRecruiterId": None, **extra}
    response = env.client.post("/api/candidate-import/check", json=body)
    assert response.status_code == 200, response.text
    return response.json()["plans"]


def _paths(plan):
    return {f["path"] for f in plan["files"] if not f["skipped"]}


def test_one_persons_folder_with_document_words_and_spelling_slips_is_one_candidate(env):
    plans = _check(env, RASHID_FOLDER, RASHID_FILES)
    assert len(plans) == 1
    assert plans[0]["folder"] == RASHID_FOLDER and {f["path"] for f in plans[0]["files"]} == set(RASHID_FILES)
    assert stored(env, Candidate) == []      # a dry run writes nothing


def test_a_folder_of_two_real_people_splits_and_can_be_forced_into_one_candidate(env):
    plans = _check(env, "pair", TWO_PEOPLE)
    assert [p["folder"] for p in plans] == ["pair / Ramesh", "pair / Suresh"] and {p["name"] for p in plans} == {PENDING_NAME}
    assert _paths(plans[0]) == {"Ramesh-resume.pdf", "Ramesh-passport.pdf"} and _paths(plans[1]) == {"Suresh-resume.pdf", "Suresh-visa.pdf"}
    # the whole screen's choice, then the choice for just that picked folder
    for extra in ({"groupMode": "one"}, {"grouping": {"pair": "one"}}):
        merged = _check(env, "pair", TWO_PEOPLE, **extra)
        assert len(merged) == 1 and merged[0]["folder"] == "pair" and {f["path"] for f in merged[0]["files"]} == set(TWO_PEOPLE), extra
    # a per-folder 'auto' beats the screen-wide 'one'
    assert len(_check(env, "pair", TWO_PEOPLE, groupMode="one", grouping={"pair": "auto"})) == 2
    # another folder's choice does not touch this one
    assert len(_check(env, "pair", TWO_PEOPLE, grouping={"elsewhere": "one"})) == 2
    assert env.client.post("/api/candidate-import/check", json={"folders": [], "rows": [], "groupMode": "several"}).status_code == 422


def test_a_person_can_move_a_file_to_the_other_candidate_or_leave_it_out(env):
    first, second = (p["folder"] for p in _check(env, "pair", TWO_PEOPLE))
    moved = _check(env, "pair", TWO_PEOPLE, assign={"pair/Ramesh-passport.pdf": second})
    assert [_paths(p) for p in moved] == [{"Ramesh-resume.pdf"}, {"Ramesh-passport.pdf", "Suresh-resume.pdf", "Suresh-visa.pdf"}]
    left_out = _check(env, "pair", TWO_PEOPLE, assign={"pair/Suresh-visa.pdf": ""})
    assert [_paths(p) for p in left_out] == [{"Ramesh-resume.pdf", "Ramesh-passport.pdf"}, {"Suresh-resume.pdf"}]
    both = _check(env, "pair", TWO_PEOPLE, assign={"pair/Suresh-resume.pdf": first, "pair/Ramesh-resume.pdf": ""})
    assert [_paths(p) for p in both] == [{"Ramesh-passport.pdf", "Suresh-resume.pdf"}, {"Suresh-visa.pdf"}]
    # a choice for a file of another picked folder, or naming a candidate that does not exist, changes nothing
    assert [_paths(p) for p in _check(env, "pair", TWO_PEOPLE, assign={"other/Ramesh-passport.pdf": second})] == [_paths(p) for p in _check(env, "pair", TWO_PEOPLE)]
    assert [len(_paths(p)) for p in _check(env, "pair", TWO_PEOPLE, assign={"pair/Ramesh-passport.pdf": "nobody"})] == [1, 2]
    assert stored(env, Candidate) == []


def _resume_output(name):
    raw = {"name": name, "personal_info": {"father_name": "Ganesan"}, "education": [{"degree": "ITI", "year": "2004"}],
           "experience": [{"company": "Example Engineering", "title": "Fitter", "duration_months": 60}]}
    from app.services import form_extraction as fx
    return {"status": "complete", "coverage": {"complete": True}, "extractions": {"resume": {"fields": fx.resume_values(raw), "raw": raw}}}


def _apply_read_resume(env, candidate_id, name, monkeypatch, job_id="read-1"):
    """What the reading worker does when it finishes a file: save the read resume onto the candidate (the reader itself is faked)."""
    from app.services import application_form
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args, **kwargs: {})
    _read_again(env, [candidate_id])      # a person asks for the reading: this queues the real jobs (none exist before)
    real = next(j for j in _jobs(env, candidate_id) if j.kind == "resume")
    job = SimpleNamespace(id=job_id, candidate_id=candidate_id, owner_id="admin", status="completed", last_error=None,
                          draft={"applicationForm": {"slot": "resume", "attachmentId": real.attachment_id, "candidateId": candidate_id}})
    with env.sessions() as db:
        application_form.apply_job_output(db, job, _resume_output(name))
        db.commit()
    return job


def test_the_folder_name_is_replaced_by_the_name_read_from_the_resume(env, monkeypatch):
    result = send(env, "Gunavelan-alnoor", ["Resume.pdf"], {"phone": "9876500060"}, named=False)
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == PENDING_NAME and candidate.field_sources == {"_provisionalName": "pending"}
    job = _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    assert job.status == "completed"
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == "Gunavelan R"
        assert "_provisionalName" not in (candidate.field_sources or {}) and candidate.field_sources["personal.candidateName"] == "extracted"
        assert candidate.father_name == "Ganesan"


def test_a_second_clearly_different_name_after_the_folder_name_was_replaced_is_rejected_as_another_person(env, monkeypatch):
    from app.services.document_identity import IDENTITY_REJECTED_ACTION, IDENTITY_REVIEW_MESSAGE
    from app.db.models import AuditEntry
    result = send(env, "Gunavelan-alnoor", ["Resume.pdf"], {"phone": "9876500061"}, named=False)
    _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    wrong = _apply_read_resume(env, result["candidateId"], "Mohammed Arif Basha", monkeypatch, job_id="read-2")
    assert wrong.status == "needs_review" and wrong.last_error == IDENTITY_REVIEW_MESSAGE
    assert wrong.draft["applicationForm"]["result"]["identityMismatch"] is True and wrong.draft["applicationForm"]["result"]["filled"] == []
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == "Gunavelan R" and candidate.needs_review
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == IDENTITY_REJECTED_ACTION, AuditEntry.entity_id == candidate.id)) == 1


def test_a_name_a_person_typed_is_never_replaced_by_a_document(env, monkeypatch):
    from app.services import form_extraction as fx
    result = send(env, "Gunavelan-alnoor", ["Resume.pdf"], {"phone": "9876500062"})
    with env.sessions() as db:       # the recruiter corrects the stand-in name by hand
        candidate = db.get(Candidate, result["candidateId"])
        candidate.name = "Gunavelan Ganesan"
        fx.mark_manual(candidate, ["name"])
        db.commit()
        assert not fx.provisional_name(candidate)
    # a later document with the same person's other spelling does not touch the typed name ...
    _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == "Gunavelan Ganesan" and candidate.field_sources["personal.candidateName"] == "manual"
    # ... and one with a different person's name is turned away, as for any named candidate
    wrong = _apply_read_resume(env, result["candidateId"], "Mohammed Arif Basha", monkeypatch, job_id="read-2")
    assert wrong.status == "needs_review"
    with env.sessions() as db:
        assert db.get(Candidate, result["candidateId"]).name == "Gunavelan Ganesan"


def test_a_name_from_the_sheet_is_not_provisional(env):
    result = send(env, "Gunavelan-alnoor", ["Resume.pdf"], {"phone": "9876500063", "name": "Gunavelan R"})
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == "Gunavelan R" and "_provisionalName" not in (candidate.field_sources or {})


# ------------------------------------------------------------------ stored file names: "<Name>-<Document>.<ext>"

STANDARD_FOLDER = ["Gunavelan-signed offer,pp,aadhar.pdf", "Passport.pdf", "Passport copy.pdf", "Aadhaar.pdf", "Gunavelan-Steel fixer.pdf", "Photo.jpg",
                   "Offer letter.pdf", "Visa.pdf", "Air ticket.pdf", "Advance receipt.pdf"]
OLD_PREFIX, NEW_PREFIX = "CI_001-", "Gunavelan_R-"      # the files of a candidate with no name read yet carry the Care India ID


def _file_names(env, candidate_id):
    """{(owner, slot, version): stored file name} for the candidate's own documents and its application's stage copies."""
    with env.sessions() as db:
        application_ids = list(db.scalars(select(Application.id).where(Application.candidate_id == candidate_id)))
        rows = db.scalars(select(Attachment).where(
            (Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id)
            | (Attachment.entity_type == "application") & Attachment.entity_id.in_(application_ids)))
        return {(row.entity_type, row.slot, row.version): row.file_name for row in rows}


def _import_standard(env, phone="9876500070"):
    """The folder "Gunavelan-alnoor" with a job order, so the stage evidence (the application's own copies) exists too."""
    return send(env, "Gunavelan-alnoor", STANDARD_FOLDER, {"phone": phone}, default_order="ORD-1", named=False)


def _add_attachment(db, id, entity_type, entity_id, slot, name):
    db.add(Attachment(id=id, entity_type=entity_type, entity_id=entity_id, slot=f"{slot}-{id}", version=1, file_name=name, size_kb=1,
                      content_type="application/pdf", object_key=f"extra/{id}", content_hash=id, uploaded_by="admin"))


def test_imported_documents_are_stored_as_the_folder_name_and_the_document_not_the_original_file_name(env):
    result = _import_standard(env)
    names = _file_names(env, result["candidateId"])
    scan = OLD_PREFIX + "Signed_offer_Passport_Aadhaar_card.pdf"        # one scan of three documents carries all three names
    assert names[("candidate", "signed_offer", 1)] == names[("candidate", "passport", 1)] == names[("candidate", "aadhaar", 1)] == scan
    assert names[("candidate", "passport", 2)] == OLD_PREFIX + "Passport.pdf"            # "Passport copy.pdf" (files are taken in path order)
    assert names[("candidate", "passport", 3)] == OLD_PREFIX + "Passport-2.pdf"          # "Passport.pdf": a second file of the same kind gets "-2"
    assert names[("candidate", "aadhaar", 2)] == OLD_PREFIX + "Aadhaar_card.pdf"
    assert names[("candidate", "signed_offer", 2)] == OLD_PREFIX + "Signed_offer.pdf"
    assert names[("candidate", "resume", 1)] == OLD_PREFIX + "Resume.pdf"                # "Steel fixer" is a CV saved under its trade
    assert names[("candidate", "photo", 1)] == OLD_PREFIX + "Photo.jpg"                  # the extension is kept
    assert names[("candidate", "visa", 1)] == OLD_PREFIX + "Visa.pdf"
    assert names[("candidate", "air_ticket", 1)] == OLD_PREFIX + "Air_ticket.pdf"
    # every stored name is "<folder-name slug>-<document label>.<ext>": none keeps an original name
    assert all(name.startswith(OLD_PREFIX) and name.rsplit(".", 1)[-1] in ("pdf", "jpg") for name in names.values())
    assert not any(word in name.lower() for name in names.values() for word in ("copy", "steel", "fixer", "pp", "aadhar.", "offer letter"))
    # the application's stage copies are named the same way
    assert {slot: name for (owner, slot, _), name in names.items() if owner == "application"} == {
        "stage_5_file": OLD_PREFIX + "Signed_offer.pdf", "stage_9_file": OLD_PREFIX + "Visa.pdf",
        "stage_11_file": OLD_PREFIX + "Air_ticket.pdf", "stage_8_receipt": OLD_PREFIX + "Advance_receipt_stage_8.pdf"}


def test_the_original_file_name_is_kept_in_the_upload_audit_entry(env):
    from app.db.models import AuditEntry
    result = _import_standard(env)
    with env.sessions() as db:
        entries = list(db.scalars(select(AuditEntry).where(AuditEntry.action == "Uploaded document", AuditEntry.entity_id == result["candidateId"])))
    originals = {entry.target: entry.meta["original_name"] for entry in entries}
    assert originals[OLD_PREFIX + "Passport.pdf"] == "Passport copy.pdf" and originals[OLD_PREFIX + "Passport-2.pdf"] == "Passport.pdf"   # taken in path order
    assert originals[OLD_PREFIX + "Resume.pdf"] == "Gunavelan-Steel fixer.pdf"
    assert originals[OLD_PREFIX + "Signed_offer_Passport_Aadhaar_card.pdf"] == "Gunavelan-signed offer,pp,aadhar.pdf"


def test_every_document_takes_the_name_read_from_the_resume_and_nothing_else_is_renamed(env, monkeypatch):
    from app.services.application_form import rename_imported_files
    result = _import_standard(env)
    candidate_id = result["candidateId"]
    other = send(env, "Ravi Kumar", ["Passport.pdf", "Resume.pdf"], {"phone": "9876500071"}, default_order="ORD-1")
    before, others_before = _file_names(env, candidate_id), _file_names(env, other["candidateId"])
    assert any(owner == "application" for owner, _, _ in before) and others_before
    assert all(name.startswith("Ravi_Kumar-") for name in others_before.values())
    extras = {"extra-1": "resume.pdf", "extra-2": "Original scan.pdf", "extra-3": "Gunavelan_alnoorX-Letter.pdf"}   # derived, hand-uploaded, a look-alike prefix
    with env.sessions() as db:
        application_id = db.scalar(select(Application.id).where(Application.candidate_id == candidate_id))
        for id, name in extras.items():
            _add_attachment(db, id, "candidate", candidate_id, "other", name)
        _add_attachment(db, "extra-4", "application", application_id, "stage_2_file", "Interview note.pdf")
        extras["extra-4"] = "Interview note.pdf"
        db.commit()

    _apply_read_resume(env, candidate_id, "Gunavelan R", monkeypatch)
    after = {key: name for key, name in _file_names(env, candidate_id).items() if key in before}      # (the extras are checked below)
    assert set(after) == set(before)
    for key, name in before.items():          # the candidate's documents AND the stage copies: only the name part changes
        assert after[key] == NEW_PREFIX + name[len(OLD_PREFIX):], key
    assert {k: v for k, v in after.items() if k[0] == "application"} and not any(n.startswith(OLD_PREFIX) for n in after.values())
    with env.sessions() as db:
        assert {a.id: a.file_name for a in db.scalars(select(Attachment).where(Attachment.id.like("extra-%")))} == extras      # untouched
    assert _file_names(env, other["candidateId"]) == others_before                                                  # another candidate is untouched

    # a second read that changes nothing renames nothing: a file that carries the old prefix again is left alone
    with env.sessions() as db:
        _add_attachment(db, "extra-late", "candidate", candidate_id, "other", OLD_PREFIX + "Late.pdf")
        db.commit()
    _apply_read_resume(env, candidate_id, "Gunavelan R", monkeypatch, job_id="read-2")
    with env.sessions() as db:
        assert db.get(Attachment, "extra-late").file_name == OLD_PREFIX + "Late.pdf"
        assert {a.file_name for a in db.scalars(select(Attachment).where(Attachment.entity_id == candidate_id, Attachment.id.notlike("extra-%")))} \
            == {name for (owner, _, _), name in after.items() if owner == "candidate"}
        candidate = db.get(Candidate, candidate_id)
        assert rename_imported_files(db, candidate, "Gunavelan R", "Gunavelan R") == 0
        assert rename_imported_files(db, candidate, "Gunavelan_R", "Gunavelan R") == 0      # the same slug is the same prefix
        db.rollback()


def _file_references(env, candidate_id):
    """Every {"id", "name", ...} file reference kept in the application's stages and draft: {(where, path in the JSON): reference}."""
    found = {}

    def walk(where, path, value):
        if isinstance(value, dict):
            if "id" in value and "name" in value:
                found[(where, path)] = value
            for key, item in value.items():
                walk(where, f"{path}.{key}", item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(where, f"{path}[{index}]", item)

    with env.sessions() as db:
        for application in db.scalars(select(Application).where(Application.candidate_id == candidate_id)):
            walk("draft", "", application.draft)
            for stage in db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == application.id)):
                walk(f"stage {stage.stage}", "", stage.data)
    return found


def test_the_file_references_kept_in_the_stages_follow_the_renamed_files(env, monkeypatch):
    result = _import_standard(env, phone="9876500074")
    candidate_id = result["candidateId"]
    before = _file_references(env, candidate_id)
    wheres = {where for where, _ in before}
    assert {"stage 5", "stage 8", "stage 9", "draft"} <= wheres, wheres            # finished stages that hold a file, and the open stage's draft
    assert all(ref["name"].startswith(OLD_PREFIX) for ref in before.values())
    with env.sessions() as db:
        stored_names = {a.id: a.file_name for a in db.scalars(select(Attachment).where(Attachment.id.in_({r["id"] for r in before.values()})))}
    assert set(stored_names) == {ref["id"] for ref in before.values()}              # every reference points at a real attachment
    assert all(stored_names[ref["id"]] == ref["name"] for ref in before.values())

    _apply_read_resume(env, candidate_id, "Gunavelan R", monkeypatch)
    after = _file_references(env, candidate_id)
    assert set(after) == set(before)                                                # nothing added or lost, only the name follows
    with env.sessions() as db:
        attachments = {a.id: a.file_name for a in db.scalars(select(Attachment).where(Attachment.id.in_({r["id"] for r in after.values()})))}
    for key, ref in after.items():
        assert ref["id"] == before[key]["id"]
        assert ref["name"] == NEW_PREFIX + before[key]["name"][len(OLD_PREFIX):], key
        assert ref["name"] == attachments[ref["id"]], key                           # the copy equals the Attachment's own name
        assert {k: v for k, v in ref.items() if k != "name"} == {k: v for k, v in before[key].items() if k != "name"}


def test_a_name_typed_by_hand_does_not_rename_the_documents(env, monkeypatch):
    from app.services import form_extraction as fx
    result = _import_standard(env, phone="9876500072")
    before = _file_names(env, result["candidateId"])
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        candidate.name = "Gunavelan Ganesan"
        fx.mark_manual(candidate, ["name"])
        db.commit()
    _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    assert _file_names(env, result["candidateId"]) == before
    assert all(name.startswith(OLD_PREFIX) for name in before.values())


def test_a_name_from_the_sheet_names_the_files_and_a_read_never_renames_them(env, monkeypatch):
    result = send(env, "Gunavelan-alnoor", ["Passport.pdf", "Resume.pdf"], {"phone": "9876500073", "name": "Gunavelan Ganesan"}, default_order="ORD-1")
    names = _file_names(env, result["candidateId"])
    assert {n for (owner, _, _), n in names.items() if owner == "candidate"} == {"Gunavelan_Ganesan-Passport.pdf", "Gunavelan_Ganesan-Resume.pdf"}
    _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    assert _file_names(env, result["candidateId"]) == names


# ------------------------------------------------------------------ what the Import screen really sends

def _screen_import(env, folder, names, sheet_row=None):
    """/check, then the import with the spec shaped exactly as the screen builds it: spec.name = plan.name, spec.row = plan.row."""
    body = {"folders": [{"folder": folder, "files": [{"path": n} for n in names]}], "rows": [sheet_row] if sheet_row else [],
            "defaultOrder": "ORD-1", "defaultRecruiterId": "staff"}
    response = env.client.post("/api/candidate-import/check", json=body)
    assert response.status_code == 200, response.text
    plan = response.json()["plans"][0]
    spec = {"folder": folder, "name": plan["name"], "nameFromFiles": plan["nameSource"] == "documents", "row": plan["row"], "files": [{"path": n} for n in names],
            "defaultOrder": "ORD-1", "defaultRecruiterId": "staff"}
    files = [("files", (n, PDF + n.encode(), "application/pdf")) for n in names]
    imported = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=files)
    assert imported.status_code == 200, imported.text
    return plan, imported.json()


def test_the_screens_folder_name_stand_in_is_replaced_by_the_resume_and_the_files_are_renamed(env, monkeypatch):
    plan, result = _screen_import(env, "Gunavelan-alnoor", ["Resume.pdf", "Passport.pdf", "Offer letter.pdf", "Visa.pdf"])   # with a job: stage copies too
    assert plan["name"] == PENDING_NAME and plan["nameSource"] == "pending" and plan["row"] == {}      # no sheet: the folder is not a name
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == PENDING_NAME and candidate.field_sources == {"_provisionalName": "pending"}
    before = _file_names(env, result["candidateId"])
    assert before and all(name.startswith(OLD_PREFIX) for name in before.values())
    _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == "Gunavelan R" and "_provisionalName" not in (candidate.field_sources or {})
    after = _file_names(env, result["candidateId"])
    assert set(after) == set(before) and any(owner == "application" for owner, _, _ in after)
    assert after == {key: NEW_PREFIX + name[len(OLD_PREFIX):] for key, name in before.items()}


def test_a_name_the_sheet_gives_through_the_screen_is_never_provisional_or_renamed(env, monkeypatch):
    plan, result = _screen_import(env, "Gunavelan-alnoor", ["Resume.pdf", "Passport.pdf"], {"name": "Typed Name", "folder": "Gunavelan-alnoor", "phone": "9876500080"})
    assert plan["name"] == "Typed Name" and plan["row"]["name"] == "Typed Name"
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert candidate.name == "Typed Name" and candidate.field_sources == {}
    before = _file_names(env, result["candidateId"])
    assert before and all(name.startswith("Typed_Name-") for name in before.values())
    _apply_read_resume(env, result["candidateId"], "Gunavelan R", monkeypatch)
    assert _file_names(env, result["candidateId"]) == before
    with env.sessions() as db:
        assert db.get(Candidate, result["candidateId"]).name == "Typed Name"


def test_check_returns_only_what_the_sheet_said_for_each_person_of_a_split_folder(env):
    plans = _check(env, "pair", TWO_PEOPLE)
    assert len(plans) == 2 and [plan["row"] for plan in plans] == [{}, {}]       # no sheet: the names taken from the files are not sheet entries
    assert all(plan["name"] for plan in plans)


# ------------------------------------------------------------------ POST /api/candidate-import/status (what the screen polls)

def _status(env, ids, expect=200):
    response = env.client.post("/api/candidate-import/status", json={"candidateIds": ids})
    assert response.status_code == expect, response.text
    return response.json()["items"] if expect == 200 else response


def _one_status(env, candidate_id):
    items = _status(env, [candidate_id])
    assert len(items) == 1
    return items[0]


def _resume_output_with_history(name):
    raw = {"name": name, "personal_info": {"father_name": "Ganesan"}, "education": [{"degree": "ITI", "year": "2004"}],
           "experience": [{"company": "Example Engineering", "title": "Fitter", "period": "Jan 2018 to Mar 2020", "country": "India"}]}
    from app.services import form_extraction as fx
    return {"status": "complete", "coverage": {"complete": True}, "extractions": {"resume": {"fields": fx.resume_values(raw), "raw": raw}}}


def test_status_follows_a_screen_import_from_the_folder_name_to_the_name_read_from_the_resume(env, monkeypatch):
    from app.services import application_form
    names = ["Resume.pdf", "Passport.pdf", "Offer letter.pdf", "Visa.pdf"]
    plan, result = _screen_import(env, "Gunavelan-alnoor", names)
    candidate_id = result["candidateId"]
    assert _jobs(env, candidate_id) == []          # the import only stores the files: nothing is queued to be read

    # (1) right after the import: the folder name, every filed document under the standard name, nothing read yet
    item = _one_status(env, candidate_id)
    assert item["candidateId"] == candidate_id and item["careId"] and item["ref"]
    assert item["nameSource"] == "pending" and item["name"] == PENDING_NAME
    with env.sessions() as db:
        filed = {row.slot: (row.file_name, row.version) for row in db.scalars(
            select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id))}
    assert len(filed) == len(names) and {d["slot"] for d in item["documents"]} == set(filed) and len(item["documents"]) == len(names)
    for document in item["documents"]:
        assert document["fileName"] == filed[document["slot"]][0] and document["versions"] == filed[document["slot"]][1] == 1
        assert document["fileName"].startswith("CI_001-") and document["label"]
    assert {d["slot"] for d in item["documents"]} == {"resume", "passport", "signed_offer", "visa"}
    assert item["reading"] == {"total": 0, "open": 0, "done": 0, "review": 0, "failed": 0}
    assert item["needsReview"] and not item["detailsFound"]
    assert not item["experience"]["total"] and not item["experience"]["india"] and not item["experience"]["abroad"]

    # (2) after the resume is read: the name from the documents, the files renamed, details and experience filled in
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args, **kwargs: {})
    assert _read_again(env, [candidate_id]) == 2             # a person asks: the resume and the passport are queued
    jobs = _jobs(env, candidate_id)
    assert len(jobs) == 2 and _one_status(env, candidate_id)["reading"] == {"total": 2, "open": 2, "done": 0, "review": 0, "failed": 0}
    with env.sessions() as db:
        resume_id = next(job.id for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id))
                         if (job.draft or {}).get("applicationForm", {}).get("slot") == "resume")
    fake = SimpleNamespace(id=resume_id, candidate_id=candidate_id, owner_id="admin", status="completed", last_error=None,
                           draft={"applicationForm": {"slot": "resume", "attachmentId": f"file-{resume_id}", "candidateId": candidate_id}})
    with env.sessions() as db:
        application_form.apply_job_output(db, fake, _resume_output_with_history("Gunavelan R"))
        db.get(DocumentExtractionJob, resume_id).status = "completed"      # the worker marks the queued job finished
        db.commit()
    after = _one_status(env, candidate_id)
    assert after["nameSource"] == "documents" and after["name"] == "Gunavelan R"
    assert len(after["documents"]) == len(names) and all(d["fileName"].startswith("Gunavelan_R-") for d in after["documents"])
    assert after["reading"]["total"] == len(jobs) and after["reading"]["done"] >= 1
    assert after["reading"]["open"] == len(jobs) - after["reading"]["done"]
    assert after["detailsFound"] > 0
    assert after["experience"]["total"] == 2 and after["experience"]["india"] == 2 and not after["experience"]["abroad"]


def test_status_reports_a_sheet_name_and_a_name_typed_by_hand(env):
    from app.services import form_extraction as fx
    sheet = send(env, "Gunavelan-alnoor", ["Resume.pdf"], {"phone": "9876500090", "name": "Gunavelan R"})
    assert _one_status(env, sheet["candidateId"])["nameSource"] == "sheet"
    assert _one_status(env, sheet["candidateId"])["name"] == "Gunavelan R"
    folder = send(env, "Ramesh-kumar", ["Resume.pdf"], {"phone": "9876500091"}, named=False)
    assert _one_status(env, folder["candidateId"])["nameSource"] == "pending"
    with env.sessions() as db:
        candidate = db.get(Candidate, folder["candidateId"])
        candidate.name = "Ramesh Kumar"
        fx.mark_manual(candidate, ["name"])
        db.commit()
    typed = _one_status(env, folder["candidateId"])
    assert typed["nameSource"] == "typed" and typed["name"] == "Ramesh Kumar"
    both = {item["candidateId"]: item["nameSource"] for item in _status(env, [sheet["candidateId"], folder["candidateId"]])}
    assert both == {sheet["candidateId"]: "sheet", folder["candidateId"]: "typed"}


def test_status_counts_readings_by_how_they_ended(env):
    result = send(env, "Gunavelan-alnoor", ["Resume.pdf", "Passport.pdf", "Aadhaar.pdf"], {"phone": "9876500092"})
    assert _one_status(env, result["candidateId"])["reading"]["total"] == 0          # nothing is read by the import itself
    assert _read_again(env, [result["candidateId"]]) == 3
    jobs = _jobs(env, result["candidateId"])
    assert len(jobs) == 3
    with env.sessions() as db:
        for job, status in zip(jobs, ("attached", "needs_review", "failed")):
            db.get(DocumentExtractionJob, job.id).status = status
        db.commit()
    reading = _one_status(env, result["candidateId"])["reading"]
    assert reading["total"] == len(jobs) and reading["done"] == 1 and reading["review"] == 1 and reading["failed"] == 1
    assert reading["open"] == len(jobs) - 3


def test_status_leaves_out_candidates_the_caller_may_not_work_on_and_unknown_ids(env):
    mine = send(env, "Gunavelan-alnoor", ["Resume.pdf"], {"phone": "9876500093"}, recruiter="staff")
    ids = [mine["candidateId"], "no-such-candidate"]
    assert [item["candidateId"] for item in _status(env, ids)] == [mine["candidateId"]]       # the admin sees it; unknown ids are ignored
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    assert [item["candidateId"] for item in _status(env, ids)] == [mine["candidateId"]]       # the owner sees it
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert _status(env, ids) == []                                                            # another staff member gets nothing
    assert _status(env, []) == []


def test_status_refuses_more_than_two_hundred_ids(env):
    assert _status(env, [f"id-{n}" for n in range(200)]) == []
    assert _status(env, [f"id-{n}" for n in range(201)], expect=422).status_code == 422


# ------------------------------------------------------------------ a scan of several documents is split at upload

def _send_files(env, folder, files, *, default_order="ORD-1", row=None):
    """The import request with real file contents: files = {path: (bytes, content type)}."""
    spec = {"folder": folder, "row": {"name": bulk._clean_name(folder), **(row or {"phone": "9876500123"})}, "files": [{"path": n} for n in files],
            "defaultOrder": default_order, "defaultRecruiterId": "staff"}
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)},
                               files=[("files", (n.rsplit("/", 1)[-1], data, kind)) for n, (data, kind) in files.items()])
    assert response.status_code == 200, response.text
    return response.json()


def _stored_pdf_text(env, name):
    import pymupdf
    with env.sessions() as db:
        key = db.scalar(select(Attachment.object_key).where(Attachment.file_name == name, Attachment.entity_type == "candidate"))
    path = next(env.storage.glob(f"*/{key.rsplit('/', 1)[-1]}"))
    with pymupdf.open(stream=path.read_bytes(), filetype="pdf") as document:
        return document.page_count, " ".join(page.get_text() for page in document).lower()


def test_a_scan_of_several_documents_is_split_so_each_slot_holds_only_its_own_pages(env):
    from ocr_synthetic import composite_pdf
    from app.db.models import AuditEntry
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    result = _send_files(env, "Rashid Khan", {
        "Rashid-signed offer,aadhar,pp.pdf": (scan, "application/pdf"),
        "Rashid-signed offer,aadhar,pp (copy).pdf": (scan, "application/pdf"),       # the same bytes again: kept once
        "Rashid-Medical.pdf": (PDF + b" medical", "application/pdf"),                  # named for one document: stored as it is
        "Rashid-photo.jpg": (b"JPEGDATA", "image/jpeg")})
    assert any("copy of" in issue["message"] for issue in result["issues"])
    assert any("split by what is on each page" in issue["message"] for issue in result["issues"])
    with env.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"])))
        by_slot = {row.slot: row for row in rows}
        assert set(by_slot) == {"passport", "aadhaar", "signed_offer", "medical", "photo"}
        assert all(row.version == 1 for row in rows)                                 # copy not added again: one file per slot
        assert by_slot["passport"].file_name == "Rashid_Khan-Passport.pdf"
        assert by_slot["aadhaar"].file_name == "Rashid_Khan-Aadhaar_card.pdf"
        assert by_slot["signed_offer"].file_name == "Rashid_Khan-Signed_offer.pdf"
        assert len({row.object_key for row in rows}) == 5                            # three different files cut from the one scan
        assert by_slot["medical"].file_name == "Rashid_Khan-Medical.pdf" and by_slot["photo"].file_name == "Rashid_Khan-Photo.jpg"
        # the Passport slot holds the passport page only, the Aadhaar slot the Aadhaar page only, the offer slot the rest
        passport_pages, passport_text = _stored_pdf_text(env, "Rashid_Khan-Passport.pdf")
        aadhaar_pages, aadhaar_text = _stored_pdf_text(env, "Rashid_Khan-Aadhaar_card.pdf")
        offer_pages, offer_text = _stored_pdf_text(env, "Rashid_Khan-Signed_offer.pdf")
        assert (passport_pages, aadhaar_pages, offer_pages) == (1, 1, 1)
        assert "passport" in passport_text and "aadhaar" not in passport_text and "passport" not in aadhaar_text and "uidai" not in passport_text
        assert "meeting" in offer_text or "apolog" in offer_text
        assert "passport" not in offer_text and "aadhaar" not in offer_text
        # the audit entries keep the original name and the pages each file came from
        entries = {entry.target: entry.meta for entry in db.scalars(select(AuditEntry).where(
            AuditEntry.action == "Uploaded document", AuditEntry.entity_id == result["candidateId"]))}
        for name, pages in (("Rashid_Khan-Passport.pdf", [2]), ("Rashid_Khan-Aadhaar_card.pdf", [3]), ("Rashid_Khan-Signed_offer.pdf", [1])):
            assert entries[name]["original_name"] == "Rashid-signed offer,aadhar,pp.pdf" and entries[name]["split_pages"] == pages
        assert "split_pages" not in entries["Rashid_Khan-Medical.pdf"]
        # the application's signed offer (stage 5) is the offer remainder, not the whole scan
        application = db.scalar(select(Application).where(Application.candidate_id == result["candidateId"]))
        copies = {row.slot: row for row in db.scalars(select(Attachment).where(Attachment.entity_type == "application", Attachment.entity_id == application.id))}
        assert copies["stage_5_file"].file_name == "Rashid_Khan-Signed_offer.pdf"
        assert copies["stage_5_file"].object_key == by_slot["signed_offer"].object_key


def test_a_scan_that_cannot_be_read_is_stored_whole_and_the_import_still_succeeds(env):
    from app.integrations.ocr import inspection
    with patch.object(inspection, "inspect_document", side_effect=inspection.InspectionError("timed out", retryable=True)):
        result = _send_files(env, "Rashid Khan", {"Rashid-signed offer,aadhar,pp.pdf": (PDF + b" scan", "application/pdf")})
    assert any("stored whole" in issue["message"] for issue in result["issues"])
    with env.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"])))
    assert {row.slot for row in rows} == {"passport", "aadhaar", "signed_offer"} and len({row.object_key for row in rows}) == 1
    assert {row.file_name for row in rows} == {"Rashid_Khan-Signed_offer_Passport_Aadhaar_card.pdf"}


def test_the_dry_run_never_reads_the_documents(env):
    from app.integrations.ocr import inspection
    body = {"folders": [{"folder": "Rashid", "files": [{"path": "Rashid-signed offer,pp,aadhar.pdf"}, {"path": "scan0001.pdf"}]}], "rows": [],
            "defaultOrder": "ORD-1", "defaultRecruiterId": "staff"}
    with patch.object(inspection, "inspect_document", side_effect=AssertionError("the check must stay name-only")):
        response = env.client.post("/api/candidate-import/check", json=body)
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ the candidate's page shows which pages of the scan a file holds

def _statements_while(env, action):
    from sqlalchemy import event
    seen = []
    engine = env.sessions.kw["bind"]

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()) + " " + str(parameters))   # the key name travels as a parameter

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = action()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, seen


def _form_documents(env, candidate_id):
    from app.services.application_form import build_form
    with env.sessions() as db:
        return build_form(db, candidate_id, db.get(User, "admin"))["documents"]


def _profile_documents(env, candidate_id):
    response = env.client.get(f"/api/candidates/{candidate_id}")
    assert response.status_code == 200, response.text
    return {slot["key"]: slot["versions"] for slot in response.json()["documents"]}


def test_the_form_and_the_profile_say_which_pages_of_the_scan_each_split_file_holds(env):
    from ocr_synthetic import composite_pdf
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    split = _send_files(env, "Rashid Khan", {"Rashid-signed offer,aadhar,pp.pdf": (scan, "application/pdf"),
                                            "Rashid-Medical.pdf": (PDF + b" medical", "application/pdf")})["candidateId"]
    plain = _send_files(env, "Rahul Kumar", {"Rahul-Medical.pdf": (PDF + b" other medical", "application/pdf")}, row={"phone": "9876500124"})["candidateId"]

    documents = _form_documents(env, split)
    for slot, pages in (("passport", [2]), ("aadhaar", [3]), ("signed_offer", [1])):
        assert documents[slot]["current"]["splitFrom"] == {"fileName": "Rashid-signed offer,aadhar,pp.pdf", "pages": pages}, slot
        if documents[slot]["files"]:
            assert documents[slot]["files"][0]["splitFrom"] == {"fileName": "Rashid-signed offer,aadhar,pp.pdf", "pages": pages}
    assert "splitFrom" not in documents["medical"]["current"]                        # named for one document: stored as it is
    assert all("splitFrom" not in slot["current"] for slot in _form_documents(env, plain).values() if slot["current"])

    profile = _profile_documents(env, split)
    for slot, pages in (("passport", [2]), ("aadhaar", [3]), ("signed_offer", [1])):
        assert [file.get("splitFrom") for file in profile[slot]] == [{"fileName": "Rashid-signed offer,aadhar,pp.pdf", "pages": pages}], slot
    assert all("splitFrom" not in file for file in profile["medical"])
    assert all("splitFrom" not in file for files in _profile_documents(env, plain).values() for file in files)


def test_showing_where_a_split_file_came_from_costs_at_most_one_more_statement_whatever_the_number_of_files(env):
    from ocr_synthetic import composite_pdf
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    split = _send_files(env, "Rashid Khan", {"Rashid-signed offer,aadhar,pp.pdf": (scan, "application/pdf"),
                                            "Rashid-Medical.pdf": (PDF + b" medical", "application/pdf")})["candidateId"]
    plain = _send_files(env, "Rahul Kumar", {"Rahul-Medical.pdf": (PDF + b" other medical", "application/pdf")}, row={"phone": "9876500124"})["candidateId"]

    for read in (_form_documents, _profile_documents):
        _, with_splits = _statements_while(env, lambda: read(env, split))
        _, without = _statements_while(env, lambda: read(env, plain))
        assert len(with_splits) == len(without), read.__name__                       # three split files, no statement per file
        assert sum("split_pages" in statement for statement in with_splits) <= 1
    # the form's one extra read is the upload entries that carry split_pages; the profile folds it into its single statement
    _, form_statements = _statements_while(env, lambda: _form_documents(env, split))
    assert sum("split_pages" in statement for statement in form_statements) == 1
    _, profile_statements = _statements_while(env, lambda: _profile_documents(env, split))
    assert len(profile_statements) == 1 and "split_pages" in profile_statements[0]


GENERIC_NAMES = ["CamScanner 04-10-2026.pdf", "CamScanner 10-10-2026.pdf", "New Doc 6.pdf", "New Doc 18.pdf", "Scanned Document 3.pdf",
                 "Scans 2026.pdf", "Docs-1.pdf", "WhatsApp Image 2026-10-14 at 10.12.33.jpeg", "Screenshot 2026-10-14.png", "Screen Shot.png",
                 "Untitled.pdf", "Untitled document.pdf", "Adobe Scan 12 Oct 2026.pdf", "Office Lens 20261012.jpg", "File 3.pdf", "Files (2).pdf",
                 "Pic 5.jpg", "Picture 4.jpg", "Photos 2.jpg", "Pages 7.pdf", "PDF 2.pdf", "Copy of scan.pdf", "noname.pdf",
                 "DOC00014.pdf", "IMG_0042.jpg", "IMG-20261014-WA0003.jpg", "DSC01234.jpg", "DSCN0042.jpg", "PXL_20261014_101233.jpg", "scan07.pdf"]


def test_the_generic_names_a_scanner_or_phone_gives_a_file_are_never_a_person():
    for name in GENERIC_NAMES:
        assert bulk._name_in(name) is None, name
    assert bulk.split_people(GENERIC_NAMES) is None
    # a real name still is one, also next to generic names
    assert bulk._name_in("Rashid Khan scan.pdf") == ("", ["Rashid", "Khan"])
    assert bulk._name_in("K.Sivakumar-offer.jpg") == ("K.", ["Sivakumar"])
    assert bulk.split_people(GENERIC_NAMES + ["Rashid-passport.pdf", "Ravi Kumar-resume.pdf"])["left_out"] == GENERIC_NAMES


def test_the_files_of_an_unrenamed_mixed_folder_name_nobody():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from people_fixtures import mixed_folder
    for seed in (7, 11, 3):
        names = [name for name, _ in mixed_folder(seed=seed).files]
        assert len(names) == 14
        assert bulk.split_people(names) is None, names
        assert all(bulk._name_in(name) is None for name in names), names
