"""Order-wise application form: migration, payload, manual saves, document reading, tracking and live feed.

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL); the document reader is a fake provider.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate, Client, Company, Designation,
    DocumentExtractionJob, Order, Role, Source, User,
)
from app.integrations.ocr import inspection
from app.services import document_extraction, form_extraction as fx, manual_extraction
from test_application_workflow import complete, mapped, payload
from test_database_naming import _migrate, schema_database
from test_document_extraction import pdf, result
from test_ocr_classification import CV, PASSPORT

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import application_form as subject, applications, files as files_router

FORM_COLUMNS = {"father_name", "marital_status", "current_address", "experience_india", "education", "work_history", "field_sources"}


# ------------------------------------------------------------------ migration

def test_migration_is_additive_idempotent_and_reversible(schema_database):
    engine = schema_database
    _migrate(engine, "0020_daily_work_quota")
    assert not FORM_COLUMNS & {c["name"] for c in inspect(engine).get_columns("crm_candidates")}
    before_tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO crm_candidates (id,ref,name,phone,source,languages,designations,skills,total_experience,gulf_experience,status,needs_review) "
                                "VALUES ('old','CND-OLD','Old Candidate','9000000001','walk_in','[]','[]','[]',4,1,'new',false)"))
    _migrate(engine, "head")
    columns = {c["name"]: c for c in inspect(engine).get_columns("crm_candidates")}
    assert FORM_COLUMNS <= set(columns)
    assert set(inspect(engine).get_table_names()) == before_tables | {"crm_form_options"}  # 0021 adds no table; 0022 adds the option lists
    with engine.connect() as connection:
        row = connection.execute(text("SELECT name, total_experience, father_name, experience_india, education, work_history, field_sources "
                                      "FROM crm_candidates WHERE id='old'")).one()
        assert row == ("Old Candidate", 4, None, 0, [], [], {})
        assert connection.scalar(text("SELECT col_description('crm_candidates'::regclass, attnum) FROM pg_attribute "
                                      "WHERE attrelid='crm_candidates'::regclass AND attname='father_name'"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE crm_candidates SET father_name='Kept', education='[{\"level\":\"BE\"}]'"))
        connection.execute(text("UPDATE alembic_version SET version_num = '0020_daily_work_quota'"))
    _migrate(engine, "head")  # a repeat of 0021 over its own columns is harmless and keeps the data
    with engine.connect() as connection:
        assert connection.execute(text("SELECT father_name, education FROM crm_candidates")).one() == ("Kept", [{"level": "BE"}])
    _migrate(engine, "0020_daily_work_quota", downgrade=True)
    assert "crm_form_options" not in inspect(engine).get_table_names()
    assert not FORM_COLUMNS & {c["name"] for c in inspect(engine).get_columns("crm_candidates")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT total_experience FROM crm_candidates WHERE id='old'")) == 4
    _migrate(engine, "head")
    assert FORM_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("crm_candidates")}


# ------------------------------------------------------------------ fixture: API + fake reader

RESUME_RESULT = {
    "name": "ALEX EXAMPLE",
    "contact": {"phones": ["+91 90000 11111"], "address": "12 Example Road\nKochi, Kerala 682001"},
    "personal_info": {"father_name": "Suresh Example", "marital_status": "Married", "date_of_birth": "01/01/1990",
                      "languages_known": ["English", "Hindi"], "nationality": "Indian", "gender": "Male"},
    "education": [{"degree": "Diploma Mechanical", "institution": "Govt Polytechnic", "end": "2010"}],
    "experience": [{"title": "Technician", "company": "ACME", "country": "UAE", "is_overseas": True, "start": "2018", "end": "2025",
                    "duration_months": 84, "duration_human": "7 years"}],
    "total_experience_months": 96, "indian_experience_months": 12, "overseas_experience_months": 84,
}


@pytest.fixture
def desk(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("staff", Role.staff), ("other", Role.staff), ("manager", Role.manager))])
        db.add(Client(id="client", name="Mr Contact"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Gulf Builders LLC", country="UAE"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add_all([Order(id=key, ref=ref, company_id="company", designation_id="designation", openings=2, country="Qatar")
                    for key, ref in (("order", "ORD-1"), ("other-order", "ORD-2"))])
        db.add_all([Candidate(id=key, ref=key, name=key.title(), phone=f"90000000{n:02}", source=Source.walk_in, owner_id="staff")
                    for n, key in enumerate(("candidate", "second-candidate"))])
        db.commit()
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None, ocr_languages="eng",
        ocr_max_pages=300, ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600, agency_timezone="Asia/Kolkata",
        veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key")
    env = SimpleNamespace(sessions=sessions, settings=settings, storage=tmp_path, actor=SimpleNamespace(id="staff", role=Role.staff),
                          results={}, statuses={})

    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            with fitz.open(stream=document.data, filetype="pdf") as sent:
                base = result(mode, sent.page_count)
            env.results.setdefault(mode, {**base, **env.extra.get(mode, {})})
            return SimpleNamespace(job_id="job-" + mode, status="queued")

        def get(self, job_id):
            mode = job_id.removeprefix("job-")
            return SimpleNamespace(status=env.statuses.get(mode, "succeeded"), result=deepcopy(env.results[mode]))

    class Provider:
        def __init__(self, **options): self.jobs = Jobs()
        def __enter__(self): return self
        def __exit__(self, *args): pass

    env.extra = {}
    monkeypatch.setattr(document_extraction, "VerisOCR", Provider)
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(inspection.subprocess, "run", lambda *args, **kwargs: pytest.fail("No external programs"))
    monkeypatch.setattr(files_router, "STORAGE", tmp_path)
    monkeypatch.setattr(files_router, "settings", settings)
    monkeypatch.setattr(subject.service, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(subject.router)
    app.include_router(applications.router)
    app.include_router(files_router.router)

    def database():
        with sessions() as db:
            yield db

    for module in (subject, applications, files_router):
        app.dependency_overrides[module.get_db] = database
        app.dependency_overrides[module.current_user] = lambda: env.actor
    monkeypatch.setattr(applications, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path)))
    with TestClient(app) as client:
        env.client = client
        yield env


def url(candidate="candidate"):
    return f"/api/candidates/{candidate}/application-form"


def form(env, candidate="candidate", **params):
    response = env.client.get(url(candidate), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def as_user(env, user_id, role):
    env.actor.id, env.actor.role = user_id, role


# ------------------------------------------------------------------ reading the form

def test_blank_candidate_form_has_every_docx_section_and_lists_what_is_missing(desk):
    data = form(desk)
    assert data["header"]["company"] == "Careindia Overseas Pvt Ltd" and data["header"]["title"] == "JOB APPLICATION FORM"
    assert set(data["personal"]) == {"candidateName", "fatherName", "dateOfBirth", "maritalStatus", "gender", "nationality", "whatsappSameAsPhone",
                                     "languagesKnown", "mobileNumber", "currentAddress", "whatsapp", "email", "currentCity"}
    assert set(data["passport"]) == {"passportNumber", "placeOfIssue", "issueDate", "expiryDate", "expiry", "available"}
    assert data["education"] == [] and data["experience"]["history"] == []
    assert set(data["experience"]) == {"totalExperience", "experienceIndia", "experienceAbroad", "history"}
    assert set(data["documents"]) == {"resume", "passport", "photo"} and not data["documents"]["passport"]["uploaded"]
    assert data["application"] is None and data["applications"] == []
    assert data["recruitment"]["client"] is None and data["recruitment"]["source"] == "Walk-in"
    assert data["recruitment"]["recruiter"] == {"id": "staff", "name": "Staff"}
    assert [row["key"] for row in data["tracking"]["rows"]] == [
        "prescreening", "forwarded", "interview_scheduling", "interview_result", "offer_letter", "pcc", "medical",
        "passport_collection", "advance_payment", "visa", "visa_stamping", "ticket", "invoice_generated"]
    assert {row["status"] for row in data["tracking"]["rows"]} == {"not_started", "not_applicable"}
    missing = {item["key"]: item for item in data["completeness"]["missing"]}
    assert missing["documents.passport"]["message"] == "Passport not uploaded"
    assert missing["personal.fatherName"]["message"] == "Father's name is missing"
    assert data["completeness"]["total"] == 19 and "personal.candidateName" not in missing
    assert data["completeness"]["percent"] == round(100 * data["completeness"]["filled"] / 19)
    assert data["candidate"]["canEdit"] is True and data["processing"] is False and data["fieldSources"] == {}


def test_unknown_candidate_and_order_are_404_and_anyone_signed_in_can_read(desk):
    assert desk.client.get(url("nobody")).status_code == 404
    assert desk.client.get(url(), params={"orderId": "order"}).status_code == 404
    as_user(desk, "other", Role.staff)
    data = form(desk)
    assert data["candidate"]["canEdit"] is False


# ------------------------------------------------------------------ manual save

def test_manual_save_marks_manual_audits_and_survives_a_later_reading(desk):
    body = {"personal": {"fatherName": "Suresh Example", "dateOfBirth": "1990-01-01", "maritalStatus": "married", "languagesKnown": ["English", "english", "Hindi"]},
            "passport": {"passportNumber": "p1234567", "issueDate": "2020-01-06", "expiryDate": "2030-01-05", "placeOfIssue": "Cochin"},
            "education": [{"level": "Diploma", "passedYear": 2010, "institute": "Govt Polytechnic"}, {"level": "", "institute": None}],
            "experience": {"totalExperience": 8, "experienceAbroad": 7, "history": [{"company": "ACME", "designation": "Technician", "country": "UAE", "period": "2018 - 2025"}]}}
    saved = desk.client.put(url(), json=body)
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["personal"]["maritalStatus"] == "Married" and data["personal"]["languagesKnown"] == ["English", "Hindi"]
    assert data["passport"]["passportNumber"] == "P1234567"
    assert data["education"] == [{"level": "Diploma", "passedYear": 2010, "institute": "Govt Polytechnic"}]  # the blank row is dropped
    assert data["experience"]["experienceAbroad"] == 7 and data["experience"]["history"][0]["company"] == "ACME"
    assert set(data["fieldSources"].values()) == {"manual"}
    assert {"personal.fatherName", "passport.passportNumber", "education", "experience.history", "experience.totalExperience"} <= set(data["fieldSources"])
    assert data["completeness"]["filled"] >= 12
    with desk.sessions() as db:
        entry = db.scalar(select(AuditEntry).where(AuditEntry.action == "Updated application form"))
        assert entry.actor_id == "staff" and "personal.fatherName" in entry.meta["changed_fields"]
        # A document read (stored OCR output replayed) leaves every typed value alone.
        candidate = db.get(Candidate, "candidate")
        candidate.ocr_data = {"resume": {**RESUME_RESULT, "personal_info": {**RESUME_RESULT["personal_info"], "father_name": "Someone Else"}}}
        db.commit()
    again = desk.client.post(url() + "/refresh")
    assert again.status_code == 200
    kept = again.json()
    assert kept["personal"]["fatherName"] == "Suresh Example" and kept["education"][0]["level"] == "Diploma"
    # ...but a field still empty does get filled, and is marked as read from a document.
    assert kept["personal"]["currentAddress"] == "12 Example Road, Kochi, Kerala 682001"
    assert kept["fieldSources"]["personal.currentAddress"] == "extracted"
    # A person clearing a field makes it empty again (and fillable).
    cleared = desk.client.put(url(), json={"personal": {"currentAddress": None}}).json()
    assert cleared["personal"]["currentAddress"] is None and "personal.currentAddress" not in cleared["fieldSources"]


def test_unchanged_extracted_value_stays_extracted_and_typing_over_it_makes_it_manual(desk):
    with desk.sessions() as db:
        db.get(Candidate, "candidate").ocr_data = {"resume": RESUME_RESULT}
        db.commit()
    first = desk.client.post(url() + "/refresh").json()
    assert first["fieldSources"]["personal.fatherName"] == "extracted"
    same = desk.client.put(url(), json={"personal": {"fatherName": "Suresh Example"}}).json()
    assert same["fieldSources"]["personal.fatherName"] == "extracted"
    typed = desk.client.put(url(), json={"personal": {"fatherName": "Suresh E."}}).json()
    assert typed["fieldSources"]["personal.fatherName"] == "manual"


@pytest.mark.parametrize("body, expected", [
    ({"personal": {"dateOfBirth": "2999-01-01"}}, 422),
    ({"personal": {"dateOfBirth": "not-a-date"}}, 422),
    ({"passport": {"issueDate": "2020-01-01", "expiryDate": "2019-01-01"}}, 422),
    ({"education": [{"level": "BE", "passedYear": 1800}]}, 422),
    ({"education": [{"level": "BE"}] * 21}, 422),
    ({"experience": {"history": [{"company": "x"}] * 31}}, 422),
    ({"experience": {"totalExperience": 2, "experienceAbroad": 5}}, 422),
    ({"experience": {"totalExperience": -1}}, 422),
    ({"personal": {"candidateName": ""}}, 422),
    ({"personal": {"mobileNumber": ""}}, 422),
    ({"personal": {"mobileNumber": "12"}}, 422),
    ({"personal": {"unknownField": "x"}}, 422),
    ({"surprise": 1}, 422),
])
def test_invalid_saves_are_rejected_and_change_nothing(desk, body, expected):
    assert desk.client.put(url(), json=body).status_code == expected
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").field_sources in (None, {})


def test_passport_format_and_dates_only_warn(desk):
    expired = (date.today() - timedelta(days=10)).isoformat()
    data = desk.client.put(url(), json={"passport": {"passportNumber": "12345", "issueDate": "2010-01-01", "expiryDate": expired}}).json()
    assert data["passport"]["expiry"]["status"] == "expired"
    assert any("Indian passport number" in w for w in data["warnings"]) and "The passport has expired." in data["warnings"]
    soon = (date.today() + timedelta(days=60)).isoformat()
    assert desk.client.put(url(), json={"passport": {"expiryDate": soon}}).json()["passport"]["expiry"]["status"] == "expiring_soon"


def test_identity_conflicts_and_permissions(desk):
    assert desk.client.put(url(), json={"personal": {"mobileNumber": "9000000001"}}).status_code == 409
    as_user(desk, "other", Role.staff)
    assert desk.client.put(url(), json={"personal": {"fatherName": "x"}}).status_code == 403
    assert desk.client.post(url() + "/refresh").status_code == 403
    assert desk.client.post(url() + "/documents", data={"slot": "passport"}, files={"file": ("p.pdf", pdf(PASSPORT), "application/pdf")}).status_code == 403
    as_user(desk, "manager", Role.manager)
    assert desk.client.put(url(), json={"personal": {"fatherName": "By manager"}}).status_code == 200


# ------------------------------------------------------------------ uploading and reading documents

def process_pending(env):
    with env.sessions() as db:
        ids = list(db.scalars(select(DocumentExtractionJob.id).where(DocumentExtractionJob.status.in_(manual_extraction.READY))))
    for identifier in ids:
        manual_extraction.process_job(identifier, sessions=env.sessions, settings=env.settings)
    return ids


def upload(env, slot, data, name="doc.pdf", media="application/pdf", candidate="candidate"):
    return env.client.post(url(candidate) + "/documents", data={"slot": slot}, files={"file": (name, data, media)})


def test_passport_then_resume_fill_the_form_live_and_keep_manual_values(desk):
    desk.extra = {"passport": {"mrz": {"given_names": "ALEX", "surname": "EXAMPLE", "passport_number": "P1234567", "nationality": "IND",
                                       "date_of_birth": "1990-01-01", "sex": "M", "expiry_date": "2030-01-01", "date_of_issue": "2020-01-02",
                                       "all_check_digits_valid": True}},
                  "resume": RESUME_RESULT}
    # The recruiter has already typed the marital status.
    assert desk.client.put(url(), json={"personal": {"maritalStatus": "Single"}}).status_code == 200

    response = upload(desk, "passport", pdf(PASSPORT))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["extraction"]["status"] == "processing" and body["form"]["processing"] is True
    assert body["document"]["uploaded"] and body["document"]["current"]["url"].startswith("/api/files/")
    assert desk.client.get(body["document"]["current"]["url"]).content == pdf(PASSPORT)
    assert upload(desk, "passport", pdf(PASSPORT)).json()["document"]["versions"] == 1  # same bytes again: no new version

    process_pending(desk)
    after_passport = form(desk)
    assert after_passport["documents"]["passport"]["extraction"]["status"] == "done"
    assert after_passport["passport"]["passportNumber"] == "P1234567" and after_passport["passport"]["expiryDate"] == "2030-01-01"
    assert after_passport["personal"]["dateOfBirth"] == "1990-01-01"
    assert after_passport["personal"]["candidateName"] == "Candidate"  # a name already on file is never replaced by a read
    assert after_passport["fieldSources"]["passport.passportNumber"] == "extracted"
    assert "passport.passportNumber" in after_passport["documents"]["passport"]["extraction"]["filled"]
    assert after_passport["processing"] is False

    assert upload(desk, "resume", pdf(CV)).status_code == 202
    process_pending(desk)
    done = form(desk)
    assert done["documents"]["resume"]["extraction"]["status"] == "done"
    assert done["personal"]["fatherName"] == "Suresh Example" and done["personal"]["currentAddress"] == "12 Example Road, Kochi, Kerala 682001"
    assert done["personal"]["maritalStatus"] == "Single"  # typed value kept over the resume's "Married"
    assert "personal.maritalStatus" in done["documents"]["resume"]["extraction"]["kept"]
    assert done["education"][0] == {"level": "Diploma Mechanical", "passedYear": 2010, "institute": "Govt Polytechnic"}
    assert done["experience"]["history"][0]["country"] == "UAE"
    assert (done["experience"]["totalExperience"], done["experience"]["experienceIndia"], done["experience"]["experienceAbroad"]) == (8, 1, 7)
    assert done["personal"]["languagesKnown"] == ["English", "Hindi"]
    assert done["passport"]["passportNumber"] == "P1234567"  # the resume has no passport section; nothing replaced it
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.ocr_data["passport"] and candidate.ocr_data["resume"]  # stored like every other intake
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == "candidate")) == 2


def test_better_passport_reupload_refreshes_extracted_but_not_typed_values(desk):
    first = {"mrz": {"given_names": "ALEX", "surname": "EXAMPLE", "passport_number": "P0000001", "nationality": "IND", "date_of_birth": "1990-01-01",
                     "expiry_date": "2029-01-01", "all_check_digits_valid": True}}
    desk.extra = {"passport": first}
    upload(desk, "passport", pdf(PASSPORT))
    process_pending(desk)
    assert desk.client.put(url(), json={"passport": {"placeOfIssue": "Typed Place"}}).status_code == 200
    desk.results.clear()
    desk.extra = {"passport": {"mrz": {**first["mrz"], "passport_number": "P0000002", "expiry_date": "2031-01-01"},
                               "fields": [{"label": "Place of Issue", "value": "Scanned Place", "category": "x", "page": 1, "source": "ocr", "confidence": 0.9}]}}
    upload(desk, "passport", pdf(PASSPORT, PASSPORT))
    process_pending(desk)
    data = form(desk)
    assert data["passport"]["passportNumber"] == "P0000002" and data["passport"]["expiryDate"] == "2031-01-01"
    assert data["passport"]["placeOfIssue"] == "Typed Place"
    assert data["documents"]["passport"]["versions"] == 2


def test_unreadable_file_and_wrong_slot_are_reported_and_photo_is_just_stored(desk):
    blank = upload(desk, "resume", pdf("lorem ipsum nothing to see here"))
    assert blank.status_code == 202
    process_pending(desk)
    extraction = form(desk)["documents"]["resume"]["extraction"]
    assert extraction["status"] == "unreadable" and extraction["message"]
    # a photo must be an image; an image is stored without reading
    assert upload(desk, "photo", pdf(CV)).status_code == 415
    assert upload(desk, "badslot", b"x").status_code == 422
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
    shot = upload(desk, "photo", png, "me.png", "image/png")
    assert shot.status_code == 202 and shot.json()["extraction"]["status"] == "done"
    assert form(desk)["photo"]["fileName"].endswith("me.png")
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.kind == "photo")) == 0


def test_failed_reading_is_reported_and_same_file_requeues_it(desk):
    upload(desk, "resume", pdf(CV))
    with desk.sessions() as db:
        job = db.scalar(select(DocumentExtractionJob))
        job.status, job.attempts, job.last_error = "failed", 4, "We could not read the resume pages."
        db.commit()
    extraction = form(desk)["documents"]["resume"]["extraction"]
    assert extraction["status"] == "failed" and "could not read" in extraction["message"]
    again = upload(desk, "resume", pdf(CV))
    assert again.json()["extraction"]["status"] == "processing"
    assert again.json()["document"]["versions"] == 1


def test_plain_file_upload_to_a_candidate_resume_or_passport_slot_is_read_too(desk):
    # The profile's upload button posts to /api/files; the hook lives in that endpoint, called here directly.
    class Upload:
        filename, content_type = "p.pdf", "application/pdf"
        file = __import__("io").BytesIO(pdf(PASSPORT))
    with desk.sessions() as db:
        files_router.upload(entity_type="candidate", entity_id="candidate", slot="passport", file=Upload, db=db,
                            user=db.get(User, "staff"))
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        assert len(jobs) == 1 and jobs[0].status == "pending" and jobs[0].candidate_id == "candidate" and jobs[0].attachment_id
    assert form(desk)["documents"]["passport"]["extraction"]["status"] == "processing"


# ------------------------------------------------------------------ automatic filling from stored OCR output

def test_saving_ocr_data_by_any_path_fills_the_new_columns_but_not_the_older_ones(desk):
    with desk.sessions() as db:
        db.add(Candidate(id="intake", ref="CND-I", name="Typed Name", phone="9111111111", source=Source.email, ocr_data={"resume": RESUME_RESULT}))
        db.commit()
    with desk.sessions() as db:
        c = db.execute(select(Candidate).where(Candidate.id == "intake")).scalar_one()
        assert c.father_name == "Suresh Example" and c.current_address and c.education and c.experience_india == 1
        assert c.name == "Typed Name" and c.dob is None and c.languages in (None, [])  # older columns: the intake code's business
    # a later save that changes ocr_data fills only what is still empty
    with desk.sessions() as db:
        c = db.get(Candidate, "intake")
        c.father_name = "Edited by hand"
        fx.mark_manual(c, ["father_name"])
        c.ocr_data = {"resume": {**RESUME_RESULT, "name": "Different"}}
        db.commit()
    with desk.sessions() as db:
        assert db.get(Candidate, "intake").father_name == "Edited by hand"


def test_backfill_service_fills_existing_candidates_idempotently(desk):
    from app.services.application_form import backfill
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET ocr_data = CAST(:d AS jsonb) WHERE id='candidate'"), {"d": __import__("json").dumps({"resume": RESUME_RESULT})})
        db.commit()
    with desk.sessions() as db:
        assert backfill(db, dry_run=True)["updated"] == 1
        assert db.get(Candidate, "candidate").father_name is None
    with desk.sessions() as db:
        first = backfill(db)
        assert first["updated"] == 1 and first["fieldsFilled"] >= 5
    with desk.sessions() as db:
        c = db.get(Candidate, "candidate")
        assert c.father_name == "Suresh Example" and c.work_history[0]["company"] == "ACME" and c.name == "Candidate"
        assert backfill(db)["updated"] == 0


# ------------------------------------------------------------------ tracking

def test_tracking_follows_the_stage_records_live(desk):
    app_id = mapped(desk)
    data = form(desk)
    assert data["application"]["id"] == app_id and data["application"]["orderRef"] == "ORD-1"
    assert data["recruitment"]["client"] == "Gulf Builders LLC" and data["recruitment"]["country"] == "Qatar"
    assert data["recruitment"]["position"] == "Welder" and data["recruitment"]["clientContact"] == "Mr Contact"
    rows = {r["key"]: r for r in data["tracking"]["rows"]}
    assert rows["prescreening"]["status"] == "in_progress" and rows["forwarded"]["status"] == "not_started"
    assert rows["pcc"]["status"] == rows["invoice_generated"]["status"] == "not_applicable" and rows["pcc"]["stage"] is None

    complete(desk, app_id, payload(desk, app_id, 1))
    rows = {r["key"]: r for r in form(desk)["tracking"]["rows"]}
    assert rows["prescreening"]["status"] == "completed" and rows["prescreening"]["checked"] is True
    assert rows["prescreening"]["completedBy"] == {"id": "staff", "name": "Staff"} and rows["prescreening"]["outcome"] == "Passed"
    assert rows["prescreening"]["date"] == datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date().isoformat()
    assert rows["forwarded"]["status"] == "in_progress"

    for stage in (2, 3, 4, 5, 6):
        complete(desk, app_id, payload(desk, app_id, stage))
    tracking = form(desk)["tracking"]
    assert tracking["summary"]["completed"] == 6 and tracking["summary"]["currentStage"] == 7
    assert {r["key"]: r["status"] for r in tracking["rows"]}["medical"] == "completed"
    assert {r["key"]: r["status"] for r in tracking["rows"]}["passport_collection"] == "in_progress"

    as_user(desk, "admin", Role.admin)
    reopen = desk.client.post(f"/api/applications/{app_id}/reopen", json={"lock_version": desk.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"],
                                                                            "stage": 5, "reason": "Wrong letter"})
    assert reopen.status_code == 200, reopen.text
    rows = {r["key"]: r["status"] for r in form(desk)["tracking"]["rows"]}
    assert rows["offer_letter"] == "in_progress" and rows["forwarded"] == "completed"
    assert rows["medical"] == "not_started"  # reopening a stage reopens the ones after it too


def test_failed_prescreen_closes_the_application_and_picks_between_orders(desk):
    app_id = mapped(desk)
    complete(desk, app_id, {"result": "failed"})
    data = form(desk)
    rows = {r["key"]: r["status"] for r in data["tracking"]["rows"]}
    assert rows["prescreening"] == "failed" and rows["forwarded"] == "not_applicable"
    assert data["tracking"]["summary"]["applicationStatus"] == "closed" and data["tracking"]["summary"]["closureLabel"] == "Failed prescreening"
    second = mapped(desk, order="other-order")
    both = form(desk)
    assert both["application"]["id"] == second  # the newest active one is shown
    assert {a["id"] for a in both["applications"]} == {app_id, second}
    assert form(desk, applicationId=app_id)["application"]["id"] == app_id
    assert form(desk, orderId="other-order")["application"]["id"] == second
    assert form(desk, applicationId=app_id)["tracking"]["rows"][0]["status"] == "failed"


def test_form_changes_announce_the_candidate_on_the_live_feed(desk):
    from app.services import change_feed
    assert change_feed.TOPIC_TABLES["crm_candidates"] == ("candidates",)
    # the new columns are plain updates of crm_candidates, so the existing statement hook publishes them
    assert change_feed.classify_statement('UPDATE crm_candidates SET father_name=%(f)s, education=%(e)s, field_sources=%(s)s WHERE crm_candidates.id = %(id)s') == ("UPDATE", "crm_candidates")
    assert change_feed.classify_statement('UPDATE document_extraction_jobs SET status=%(s)s, draft=%(d)s WHERE id = %(id)s') == ("UPDATE", "document_extraction_jobs")
    assert "documents" in change_feed.TOPIC_TABLES["document_extraction_jobs"]


# ------------------------------------------------------------------ screening details (telemarketer's form)

def test_screening_details_save_with_provenance_and_mirror_the_follow_up_date_to_the_todo_list(desk):
    from app.db.models import TodoItem
    tomorrow = date.today() + timedelta(days=1)
    body = {"personal": {"whatsappSameAsPhone": True},
            "screening": {"nativePlace": "Thrissur", "familyMemberNumber": "9447000000", "jobPreference": ["Welder", "welder", "Fitter"],
                          "countryPreference": "Qatar, UAE", "jobDurationPreference": "2 years", "bestTimeToContact": "Evenings",
                          "serviceChargesStatus": "agreed", "interestLevel": "interested", "nextFollowUpDate": tomorrow.isoformat(),
                          "skills": ["Welding", "welding"]}}
    data = desk.client.put(url(), json=body)
    assert data.status_code == 200, data.text
    data = data.json()
    assert data["personal"]["whatsapp"] == data["personal"]["mobileNumber"] and data["personal"]["whatsappSameAsPhone"] is True
    screening = data["screening"]
    assert screening["jobPreference"] == ["Welder", "Fitter"] and screening["skills"] == ["Welding"]
    assert (screening["nativePlace"], screening["serviceChargesStatus"], screening["interestLevel"]) == ("Thrissur", "agreed", "interested")
    assert screening["nextFollowUpDate"] == tomorrow.isoformat() and screening["callOutcome"] is None
    assert {"screening.nativePlace", "screening.interestLevel", "screening.nextFollowUpDate", "screening.jobPreference", "screening.skills"} <= set(data["fieldSources"])
    assert data["completeness"]["optional"]["missing"] == [] and data["completeness"]["optional"]["total"] == 9
    assert data["completeness"]["total"] == 19  # the counted core did not change
    with desk.sessions() as db:
        todo = db.scalar(select(TodoItem))
        assert (todo.owner_id, todo.link_to, todo.link_label, todo.done) == ("staff", "/candidates/candidate", "Follow-up", False)
        assert todo.due_at.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date() == tomorrow
        todo.reminded_at = datetime.now(timezone.utc)
        db.commit()
    later = (tomorrow + timedelta(days=3)).isoformat()
    desk.client.put(url(), json={"screening": {"nextFollowUpDate": later}})
    with desk.sessions() as db:
        todos = list(db.scalars(select(TodoItem)))
        assert len(todos) == 1 and todos[0].reminded_at is None and todos[0].due_at.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date().isoformat() == later
    cleared = desk.client.put(url(), json={"screening": {"nextFollowUpDate": None, "interestLevel": None}}).json()
    assert cleared["screening"]["nextFollowUpDate"] is None and cleared["screening"]["interestLevel"] is None
    assert "screening.interestLevel" in {item["key"] for item in cleared["completeness"]["optional"]["missing"]}
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(TodoItem)) == 0


@pytest.mark.parametrize("screening", [
    {"serviceChargesStatus": "maybe"}, {"interestLevel": "very"}, {"nextFollowUpDate": "2099-01-01"},
    {"jobPreference": ["x" * 201]}, {"unknown": 1},
])
def test_screening_validation(desk, screening):
    assert desk.client.put(url(), json={"screening": screening}).status_code == 422


def test_call_outcome_is_read_only_and_a_conflicting_interest_level_warns(desk):
    with desk.sessions() as db:
        c = db.get(Candidate, "candidate")
        c.call_called, c.call_interested, c.call_remarks, c.call_recorded_at = True, "no", "Said he is going abroad elsewhere", datetime.now(timezone.utc)
        db.commit()
    data = desk.client.put(url(), json={"screening": {"interestLevel": "more_interested"}}).json()
    assert data["screening"]["callOutcome"]["remarks"] == "Said he is going abroad elsewhere"
    assert any("not interested" in w for w in data["warnings"])
    assert desk.client.put(url(), json={"screening": {"callOutcome": {}}}).status_code == 422


def test_passport_available_is_derived_from_number_or_scan(desk):
    assert form(desk)["passport"]["available"] is False
    assert desk.client.put(url(), json={"passport": {"passportNumber": "K1234567"}}).json()["passport"]["available"] is True
    assert form(desk, candidate="second-candidate")["passport"]["available"] is False
    assert upload(desk, "passport", pdf(PASSPORT), candidate="candidate").status_code == 202
