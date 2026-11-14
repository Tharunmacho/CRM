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


def send(env, folder, names, row=None, *, default_order=None, kinds=None, recruiter="staff"):
    spec = {"folder": folder, "row": row or {}, "files": [{"path": n, **({"kind": kinds[n]} if kinds and n in kinds else {})} for n in names],
            "defaultOrder": default_order, "defaultRecruiterId": recruiter}
    files = [("files", (n.rsplit("/", 1)[-1], (PDF if not n.endswith((".jpg", ".png")) else b"JPEGDATA") + n.encode(), "application/pdf" if not n.endswith((".jpg", ".png")) else "image/jpeg"))
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
    application, stages = _stage_rows(env, result["candidateId"])
    assert application.status == ApplicationStatus.deployed and [s.stage for s in stages] == list(range(1, 12))
    assert stages[10].data["travelDate"] == "2026-05-09" and stages[10].data["pnr"] == "6ZA8JH"
    assert "file" not in stages[4].data and "receipt" not in stages[7].data and "file" not in stages[9].data   # nothing invented for the missing papers
    assert "amount" not in stages[7].data
    assert '"id": "history"' not in json.dumps([s.data for s in stages])                                       # the stand-in is never stored


def test_someone_the_office_says_has_flown_but_has_no_ticket_is_travelled_with_the_date_left_blank(env):
    files = [("Resume.pdf", ["resume"]), ("Passport.pdf", ["passport"]), ("Medical.pdf", ["medical"]), ("Visa stamping.pdf", ["visa_stamping"])]
    ordinary = _historical_import(env, "Sita", files, {"order": "ORD-1"})
    assert ordinary["action"] == "in_progress" and ordinary["currentStage"] == 11     # without the office's word, no ticket means not yet flown
    flown = _historical_import(env, "Gopal", files, {"order": "ORD-2"}, travel_confirmed=True)
    assert (flown["action"], flown["completedThrough"]) == ("deploy", 11)
    application, stages = _stage_rows(env, flown["candidateId"])
    assert application.status == ApplicationStatus.deployed
    assert "travelDate" not in stages[10].data and "file" not in stages[10].data and '"id": "history"' not in json.dumps([s.data for s in stages])


def test_a_complete_folder_becomes_a_deployed_candidate_with_all_eleven_stages(env):
    result = send(env, "Rahul Kumar", ALL_DOCUMENTS, FULL_ROW)
    assert result["action"] == "deploy" and result["completedThrough"] == 11
    with env.sessions() as db:
        candidate = db.get(Candidate, result["candidateId"])
        assert (candidate.name, candidate.status, candidate.owner_id) == ("Rahul Kumar", CandidateStatus.deployed, "staff")
        assert candidate.designations == ["Welder"]
        application = db.scalar(select(Application).where(Application.candidate_id == candidate.id))
        assert application.status == ApplicationStatus.deployed and application.current_stage == 11
        stages = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == application.id).order_by(ApplicationStage.stage)))
        assert [s.stage for s in stages] == list(range(1, 12))
        assert [s.outcome for s in stages][:6] == ["Passed", "Forwarded", "Scheduled", "Selected", "Obtained", "Fit"]
        assert (float(stages[7].amount), stages[7].amount_currency) == (15000.0, "INR")
        assert stages[2].data["date"] == "2026-01-10"
        assert [s.completed_at for s in stages] == sorted(s.completed_at for s in stages)   # the history reads in order
        # the stage evidence points at the same files the profile shows
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "application", Attachment.entity_id == application.id))}
        assert slots == {"stage_5_file", "stage_8_receipt", "stage_9_file", "stage_10_file", "stage_11_file"}
        documents = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id))}
        assert documents == {"application_form", "resume", "passport", "photo", "education_certificate", "signed_offer", "medical", "pcc", "visa",
                             "google_review", "visa_stamping", "emigration", "air_ticket", "undertaking_letter", "insurance", "invoice"}
        # the resume and passport are queued to be read, and no stage task is left open
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate.id)) == 2
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate.id)) == 0


def test_missing_documents_leave_the_last_stage_open_instead_of_blocking(env):
    names = [n for n in ALL_DOCUMENTS if n not in ("PCC.pdf", "Emigration.pdf")]
    result = send(env, "Anil", names, {**FULL_ROW, "phone": "9876500001"})
    assert (result["action"], result["completedThrough"], result["currentStage"]) == ("in_progress", 10, 11)
    assert any("PCC" in issue["message"] for issue in result["issues"])
    with env.sessions() as db:
        application = db.scalar(select(Application).where(Application.candidate_id == result["candidateId"]))
        assert application.status == ApplicationStatus.active and application.current_stage == 11
        assert application.draft["file"]["name"].startswith("Air")
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
        assert stages[8].data["receipt"]["name"].startswith("Advance")
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
    assert [p["name"] for p in plans] == ["K. Sivakumar", "M. Poomalai", "Subramanian"]
    assert all(p["parent"] == "sivakumar" for p in plans)
    assert {f["path"] for f in plans[0]["files"]} == {"K.Sivakumar-signed-offer.jpeg", "K.Sivakumar-Steel-fixer.pdf", "31MAY-3-PAX-CHENNAI-KUWAIT.pdf"}
    assert any("scan1.pdf" in i["message"] for i in plans[0]["issues"])
    person = plans[2]
    spec = {"folder": person["folder"], "name": person["name"], "row": {}, "files": [{"path": f["path"]} for f in person["files"]]}
    files = [("files", (f["path"], PDF if f["path"].endswith(".pdf") else b"JPEGDATA", "application/pdf" if f["path"].endswith(".pdf") else "image/jpeg")) for f in person["files"]]
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=files)
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        candidate = db.get(Candidate, response.json()["candidateId"])
        assert candidate.name == "Subramanian"
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id))}
    assert {"medical", "visa", "air_ticket"} <= slots


def test_one_scan_of_several_documents_counts_for_each_and_copies_are_kept_once(env):
    assert bulk.classify_path_kinds("K.Sivakumar-signed offer,pp,aadhar.pdf") == ["signed_offer", "passport", "aadhaar"]
    assert bulk.classify_path_kinds("Poomalai-visa medical pcc.pdf") == ["pcc", "medical", "visa"]
    assert bulk.classify_path_kinds("Poomalai-visa stamping.jpg") == ["visa_stamping"]
    names = ["Rahul-signed offer,pp,aadhar.pdf", "Rahul-visa medical pcc.pdf", "Rahul-Medical.pdf", "Rahul-Medical_1.pdf"]
    spec = {"folder": "Rahul", "row": {}, "files": [{"path": n} for n in names], "defaultOrder": None, "defaultRecruiterId": None}
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
    assert len({r.object_key for r in rows if r.file_name.startswith("Rahul-visa")}) == 1   # one stored file behind pcc, medical and visa


def test_a_cv_saved_under_its_trade_is_recognised_at_once():
    assert bulk.classify_path_kinds("K.Sivakumar-Steel fixer.pdf") == ["resume"]
    assert bulk.classify_path_kinds("Welder offer letter.pdf") == ["signed_offer"]      # a trade only counts when nothing else matches
    assert bulk.classify_path_kinds("scan1.pdf") == []


def test_the_aadhaar_card_is_its_own_document_and_is_queued_to_be_read(env):
    assert bulk.classify_path_kinds("K.Sivakumar-signed offer,pp,aadhar.pdf") == ["signed_offer", "passport", "aadhaar"]
    result = send(env, "Ravi", ["Ravi-signed offer,pp,aadhar.pdf", "Ravi-Steel fixer.pdf", "Ravi-visa medical pcc.pdf"], {"phone": "9876500020"})
    with env.sessions() as db:
        slots = {a.slot for a in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"]))}
        read = sorted(j.kind for j in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == result["candidateId"])))
    assert {"signed_offer", "passport", "aadhaar", "resume", "pcc", "medical", "visa"} <= slots
    assert read == ["passport", "resume"]       # nothing else is read, and the scan holding a passport and an Aadhaar card is read once


def test_failed_readings_can_be_put_back_in_the_queue(env):
    result = send(env, "Sita", ["Sita-Steel fixer.pdf", "Sita-signed offer,pp,aadhar.pdf"], {"phone": "9876500030"})
    with env.sessions() as db:
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == result["candidateId"])):
            job.status = "failed"
        db.commit()
    response = env.client.post("/api/candidate-import/read-again", json={"candidateIds": [result["candidateId"]]})
    assert response.status_code == 200 and response.json() == {"queued": 2}
    with env.sessions() as db:
        assert {j.status for j in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == result["candidateId"]))} == {"pending"}


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
    assert [(p["name"], p["order"]) for p in plans] == [("Kiran", "ORD-1"), ("Lata", "ORD-2")]
    assert plans[1]["row"]["order"] == "ORD-2"          # the import call carries it, so the candidate lands on that job
    spec = {"folder": "Lata", "name": "Lata", "row": plans[1]["row"], "files": [{"path": "Lata-Steel fixer.pdf"}], "defaultOrder": "ORD-1"}
    response = env.client.post("/api/candidate-import/candidate", data={"spec": json.dumps(spec)}, files=[("files", ("Lata-Steel fixer.pdf", PDF + b" lata", "application/pdf"))])
    assert response.status_code == 200 and response.json()["order"] == "ORD-2"
