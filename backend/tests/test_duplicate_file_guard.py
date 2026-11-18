"""A file is never ingested twice: the manual upload (Add files / Upload / Replace) checks the bytes against everything already filed.

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL); storage is a temporary folder. Rules under test (see
services/file_ownership.py): the same bytes in the same field (any version) are not stored again; a personal document that is
another candidate's is refused with 409 and nothing is written; shared documents (group ticket ...) may belong to several candidates.
"""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pymupdf as fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Attachment, AuditEntry, Candidate, Role, Source, User
from app.integrations.ocr import inspection
from app.services import bulk_candidate_import as bulk, document_splitting as splitting, file_ownership
from ocr_synthetic import composite_pdf
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixtures)

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import application_form as form_router, document_jobs, files as files_router

REFUSED = ("This file is already filed for CI-139 · Asha Nair. A file cannot belong to two candidates. "
           "Open that candidate, or delete it first.")


def page(text):
    """One page of text: never analysed as a combined scan."""
    with fitz.open() as document:
        document.new_page().insert_text((40, 60), text)
        return document.tobytes(no_new_id=True)


@pytest.fixture
def desk(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("staff", Role.staff))])
        db.flush()
        db.add_all([
            Candidate(id="candidate", ref="CND-1", care_id="CI-140", name="Alex Example", phone="9000000001", source=Source.walk_in, owner_id="staff"),
            Candidate(id="other", ref="CND-2", care_id="CI-139", name="Asha Nair", phone="9000000002", source=Source.walk_in, owner_id="staff"),
        ])
        db.commit()
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None, ocr_languages="eng",
                               agency_timezone="Asia/Kolkata", ocr_max_pages=300, ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600)
    env = SimpleNamespace(sessions=sessions, settings=settings, storage=tmp_path, actor=SimpleNamespace(id="staff", name="Staff", role=Role.staff))
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    monkeypatch.setattr(files_router, "STORAGE", tmp_path)
    monkeypatch.setattr(files_router, "settings", settings)
    monkeypatch.setattr(bulk, "get_settings", lambda: settings)          # never the real .env
    monkeypatch.setattr(document_jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(form_router.service, "get_settings", lambda: settings)
    app = FastAPI()
    for router in (files_router.router, form_router.router, document_jobs.router):
        app.include_router(router)

    def database():
        with sessions() as db:
            yield db

    for module in (files_router, form_router, document_jobs):
        app.dependency_overrides[module.get_db] = database
        app.dependency_overrides[module.current_user] = lambda: env.actor
    with TestClient(app) as client:
        env.client = client
        yield env


def send(env, data, slot="resume", name="scan.pdf", candidate="candidate", defer="true"):
    return env.client.post("/api/files", data={"entity_type": "candidate", "entity_id": candidate, "slot": slot, "defer_extraction": defer},
                           files={"file": (name, data, "application/pdf")})


def form_send(env, data, slot="resume", candidate="candidate", name="scan.pdf"):
    return env.client.post(f"/api/candidates/{candidate}/application-form/documents", data={"slot": slot},
                           files={"file": (name, data, "application/pdf")})


def rows(env, candidate="candidate"):
    with env.sessions() as db:
        return list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate)
                               .order_by(Attachment.slot, Attachment.version)))


def file_for(env, candidate, slot, data, name="held.pdf"):
    """Another candidate's file, filed the way an earlier upload would have (a row and its bytes)."""
    with env.sessions() as db:
        db.add(Attachment(id=str(uuid4()), entity_type="candidate", entity_id=candidate, slot=slot, version=1, file_name=name, size_kb=1,
                          content_type="application/pdf", object_key=f"held/{uuid4().hex}.pdf", content_hash=hashlib.sha256(data).hexdigest(),
                          uploaded_by="staff"))
        db.commit()


def stored_files(env):
    return sorted(path.name for path in env.storage.rglob("*") if path.is_file())


def refusals(env):
    with env.sessions() as db:
        return list(db.scalars(select(AuditEntry).where(AuditEntry.action == "Refused duplicate file")))


# ------------------------------------------------------------------ same candidate, same field

def test_the_same_file_twice_in_one_field_is_stored_once_and_the_second_answer_says_so(desk):
    data = page("resume of Alex")
    first = send(desk, data, "resume")
    assert first.status_code == 201 and "alreadyUploaded" not in first.json()
    second = send(desk, data, "resume", name="renamed copy.pdf")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["alreadyUploaded"] is True and body["message"] == "This file is already uploaded (version 1)."
    assert body["id"] == first.json()["id"] == body["existing"]["id"] and body["version"] == 1
    assert [(row.slot, row.version) for row in rows(desk)] == [("resume", 1)]
    assert len(stored_files(desk)) == 1                                   # the second copy was never written


def test_an_older_version_of_the_field_counts_and_replace_with_identical_bytes_adds_no_version(desk):
    old, new = page("first scan"), page("corrected scan")
    assert send(desk, old, "medical").status_code == 201
    assert send(desk, new, "medical").json()["version"] == 2
    again = send(desk, new, "medical")                                     # Replace with the file that is already current
    assert again.status_code == 200 and again.json()["alreadyUploaded"] and again.json()["version"] == 2
    revert = send(desk, old, "medical")                                    # the first scan again: it is still on file as version 1
    assert revert.status_code == 200 and revert.json()["alreadyUploaded"] is True and revert.json()["version"] == 1
    assert [row.version for row in rows(desk)] == [1, 2] and len(stored_files(desk)) == 2


def test_passport_and_aadhaar_keep_every_page_but_never_the_same_page_twice(desk):
    front, back = page("passport front"), page("passport back")
    assert send(desk, front, "passport").status_code == 201 and send(desk, back, "passport").status_code == 201
    assert send(desk, front, "passport").json()["alreadyUploaded"] is True
    assert [row.version for row in rows(desk) if row.slot == "passport"] == [1, 2]


def test_the_form_upload_answers_already_uploaded_with_the_form_and_stores_nothing(desk):
    data = page("resume for the form")
    assert form_send(desk, data).status_code == 202
    second = form_send(desk, data)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["alreadyUploaded"] is True and body["message"] == "This file is already uploaded (version 1)."
    assert body["form"]["documents"]["resume"]["uploaded"] and "document" in body
    assert len(rows(desk)) == 1 and len(stored_files(desk)) == 1


# ------------------------------------------------------------------ another candidate

def test_a_personal_document_that_is_another_candidates_is_refused_and_nothing_is_stored(desk):
    data = page("Asha passport")
    file_for(desk, "other", "passport", data)
    before = stored_files(desk)
    refused = send(desk, data, "passport", name="whatever.pdf")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == REFUSED
    assert not any(character.isdigit() for character in refused.json()["detail"].replace("CI-139", ""))   # no internal id numbers
    assert rows(desk) == [] and stored_files(desk) == before               # no attachment row, no storage object
    entries = refusals(desk)
    assert len(entries) == 1 and entries[0].entity_id == "candidate" and entries[0].meta["slot"] == "passport"
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Uploaded document")) == 0
    # the same file in ANOTHER personal field of this candidate is refused too: it is Asha's document, whatever the field
    assert send(desk, data, "resume").status_code == 409
    # and through the form's own upload, with the same sentence
    via_form = form_send(desk, data, "passport")
    assert via_form.status_code == 409 and via_form.json()["detail"] == REFUSED
    assert rows(desk) == [] and stored_files(desk) == before


@pytest.mark.parametrize("slot", ["resume", "aadhaar", "medical", "pcc", "photo", "education_certificate", "experience_certificate",
                                  "application_form", "visa_stamping", "other"])
def test_every_personal_field_refuses_another_candidates_file(desk, slot):
    assert all(file_ownership.is_personal(name) for name in (slot, "source_bundle"))
    data = page(f"personal {slot}")
    file_for(desk, "other", slot, data)
    kind = "image/png" if slot == "photo" else "application/pdf"      # (a photo must be an image; the bytes are not decoded)
    response = desk.client.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": slot},
                                files={"file": ("f.png" if slot == "photo" else "f.pdf", data, kind)})
    assert response.status_code == 409, response.text
    assert rows(desk) == []


def test_a_group_document_in_a_shared_field_may_belong_to_several_candidates_with_a_note(desk):
    ticket = page("group air ticket")
    with patch("app.services.candidate_invoices.on_ticket_uploaded"):
        assert send(desk, ticket, "air_ticket", candidate="other").status_code == 201
        second = send(desk, ticket, "air_ticket", candidate="candidate")
    assert second.status_code == 201, second.text
    assert second.json()["notes"] == ["This file is also filed for CI-139 · Asha Nair. Shared documents such as a group ticket can belong to several candidates."]
    assert [(row.slot, row.version) for row in rows(desk)] == [("air_ticket", 1)]
    assert refusals(desk) == []
    for slot in ("undertaking_letter", "insurance", "google_review", "emigration", "visa", "signed_offer"):
        assert not file_ownership.is_personal(slot)


def test_the_same_file_in_another_field_of_the_same_candidate_is_allowed_with_a_note(desk):
    data = page("one scan, two fields")
    assert send(desk, data, "passport").status_code == 201
    second = send(desk, data, "medical")
    assert second.status_code == 201 and second.json()["notes"] == ["This file is also filed under Passport."]
    assert {row.slot for row in rows(desk)} == {"passport", "medical"}


def test_a_personal_file_held_in_a_shared_field_of_another_candidate_is_only_a_note(desk):
    data = page("filed as a group visa")
    file_for(desk, "other", "visa", data)
    response = send(desk, data, "resume")
    assert response.status_code == 201 and "CI-139 · Asha Nair" in response.json()["notes"][0]


def test_a_deleted_candidates_files_no_longer_block_the_same_file(desk):
    data = page("belonged to somebody removed")
    file_for(desk, "other", "passport", data)
    assert send(desk, data, "passport").status_code == 409
    with desk.sessions() as db:
        for row in db.scalars(select(Attachment).where(Attachment.entity_id == "other")):
            db.delete(row)
        db.delete(db.get(Candidate, "other"))
        db.commit()
    assert send(desk, data, "passport").status_code == 201


# ------------------------------------------------------------------ before any slow reading, and split scans

def test_a_refused_combined_scan_is_answered_before_it_is_read(desk):
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    assert send(desk, scan, "passport", candidate="other").status_code == 201        # filed (cut) for Asha: the whole scan is kept as her original
    before = stored_files(desk)
    with patch.object(splitting, "plan_split", side_effect=AssertionError("must not be read")):
        refused = send(desk, scan, "passport", name="again.pdf")
    assert refused.status_code == 409 and refused.json()["detail"] == REFUSED
    assert rows(desk) == [] and stored_files(desk) == before


def test_the_same_combined_scan_again_for_the_same_candidate_is_answered_before_it_is_read(desk):
    scan = composite_pdf(["unrelated", "passport", "aadhaar"])
    assert send(desk, scan, "passport").status_code == 201
    stored = [(row.slot, row.version) for row in rows(desk)]
    # the whole scan sits in the candidate's original-upload field; the pieces are already filed, so nothing new is stored
    again = send(desk, scan, "passport")
    assert again.status_code in (200, 201), again.text
    assert [(row.slot, row.version) for row in rows(desk)] == stored


def test_two_uploads_of_the_same_file_at_the_same_moment_never_both_store_it(desk):
    data = page("raced passport")
    gate = threading.Barrier(2)

    def go(candidate):
        gate.wait()
        return send(desk, data, "passport", candidate=candidate).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(go, ["candidate", "other"]))
    assert codes == [201, 409], codes
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment).where(Attachment.content_hash == hashlib.sha256(data).hexdigest())) == 1
    assert len(stored_files(desk)) == 1 and len(refusals(desk)) == 1
