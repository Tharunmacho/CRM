"""A recruiter uploads ONE mixed packet straight into a candidate's profile, then reopens the profile and presses Extract again.

The real HTTP routes the screens call (`POST /api/files`, `POST /api/candidates/{id}/application-form/documents`, `GET .../application-form`,
`POST /api/candidates/{id}/read-pending`, `POST .../application-form/extract`) run on the disposable PostgreSQL; the worker is the real one
(`manual_extraction.process_job`) with the real local page sorting; only the paid online reader (Veris) is faked, and every submission it
receives is recorded (mode and the text of each page). Synthetic documents only; no network, no key, no real database.

* the packet is cut by what is on each page: resume + education pages go to the reader together in ONE resume-mode submission, the passport
  and the Aadhaar page each on their own; every document is also filed on its own in its field and the original packet stays downloadable;
* the education rows of the form come from the resume-mode reading;
* reopening the profile and pressing Extract again reads nothing: no submission, no job, no new file or version, the applied fields stay;
  the answer says plainly that everything was already read;
* one genuinely new page is read once (and only that page); a partial passport answer keeps every local passport page together and goes to review.
"""
import hashlib
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from app.api.routers import candidate_import, files as files_router
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob
from app.services.document_reading import pending_expression
from app.integrations.ocr import inspection
from app.services import document_extraction, manual_extraction
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_document_extraction import pdf
from test_document_splitting_uploads import desk, form_router, document_jobs  # noqa: F401  (desk is a fixture)
from test_kind_classifier import MEDICAL, SSLC
from test_ocr_classification import AADHAAR, CONTINUATION, CV, PASSPORT, PASSPORT_ADDRESS
from test_veris_handoff import FakeVeris

EDUCATION_ROW = {"degree": "S.S.L.C.", "institution": "Karnataka Secondary Education Examination Board", "end": "2014"}
SECOND_EDUCATION = SSLC.replace("20140246704", "20160999111").replace("APRIL-2014", "APRIL-2016").replace("S.S.L.C.", "P.U.C.")
CORRECTED_PASSPORT = PASSPORT.replace("P1234567", "P7654321")

PAGES = {"resume_1": CV, "resume_2": CONTINUATION, "education": SSLC, "passport": PASSPORT, "passport_2": PASSPORT_ADDRESS,
         "aadhaar": AADHAAR, "medical": MEDICAL}
MARKS = (("CURRICULUM VITAE", "resume_1"), ("JOB RESPONSIBILITIES", "resume_2"), ("Wafid", "medical"), ("AADHAAR", "aadhaar"),
         ("Name of Father", "passport_2"), ("PASSPORT", "passport"), ("Karnataka", "education"))


def label(text: str) -> str:
    return next((kind for mark, kind in MARKS if mark in text), "?")


def packet(*keys: str) -> bytes:
    return pdf(*[PAGES[key] for key in keys])


MIXED = ("resume_1", "resume_2", "education", "passport", "aadhaar", "medical")


@pytest.fixture
def world(desk, monkeypatch):
    """The profile's upload route, the application-form routes and read-pending, with the paid reader faked."""
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])
    for name, value in dict(veris_ocr_base_url="https://ocr.invalid", veris_ocr_api_key="synthetic-key", ocr_provider_wait_seconds=0.0,
                            veris_parallel_jobs=1, veris_chunk_pages=20, veris_chunk_overlap_pages=1).items():
        setattr(desk.settings, name, value)
    fake = FakeVeris()
    fake.build = lambda mode, number, texts: ({"education": [EDUCATION_ROW], "highest_qualification": "SSLC"} if mode == "resume" else {})

    class Provider:
        def __init__(self, **options):
            self.jobs = fake

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(document_extraction, "VerisOCR", Provider)
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    app = FastAPI()
    for router in (files_router.router, form_router.router, candidate_import.router, candidate_import.candidate_reading_router):
        app.include_router(router)

    def database():
        with desk.sessions() as db:
            yield db

    for module in (files_router, form_router, candidate_import):
        app.dependency_overrides[module.get_db] = database
        app.dependency_overrides[module.current_user] = lambda: desk.actor
    with TestClient(app) as client:
        desk.api, desk.veris = client, fake
        yield desk


# ----------------------------------------------------------------------------------------------------------- what the screens do

def profile_upload(env, data, slot="resume", name="Alex_Example_packet.pdf"):
    """The profile's Documents tab: `api.upload('candidate', id, slot, file, {deferExtraction: true})` for the four readable fields."""
    response = env.api.post("/api/files", data={"entity_type": "candidate", "entity_id": "candidate", "slot": slot, "defer_extraction": "true"},
                            files={"file": (name, data, "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def form_upload(env, data, slot="resume", name="Alex_Example_packet.pdf"):
    """The application form's 'Add documents': nothing deferred, the reading is queued at once."""
    response = env.api.post("/api/candidates/candidate/application-form/documents", data={"slot": slot},
                            files={"file": (name, data, "application/pdf")})
    assert response.status_code in (200, 202), response.text
    return response.json()


def open_form(env):
    response = env.api.get("/api/candidates/candidate/application-form")
    assert response.status_code == 200, response.text
    return response.json()


def read_pending(env):
    return env.api.post("/api/candidates/candidate/read-pending").json()


def extract(env, ids):
    response = env.api.post("/api/candidates/candidate/application-form/extract", json={"attachmentIds": ids})
    assert response.status_code == 202, response.text
    return response.json()


def run_worker(env):
    with env.sessions() as db:
        ids = list(db.scalars(select(DocumentExtractionJob.id).where(DocumentExtractionJob.status.in_(manual_extraction.READY))
                              .order_by(DocumentExtractionJob.created_at)))
    for identifier in ids:
        manual_extraction.process_job(identifier, sessions=env.sessions, settings=env.settings)
    return ids


def pending_ids(env) -> list[str]:
    """The files the profile still offers "Extract details" for (the same flag the Documents tab and the form read)."""
    with env.sessions() as db:
        rows = db.execute(select(Attachment.id, pending_expression()).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == "candidate", Attachment.deleted_at.is_(None))).all()
    return [identifier for identifier, pending in rows if pending]


def upload_and_read(env, route, data, slot="resume"):
    """Upload as the screen does, read it, run the worker. The profile stores with the reading deferred and the recruiter presses
    "Extract details" for the files it offers; the form's upload is read at once. Then the screen refreshes (read-pending on a visit)."""
    (profile_upload if route == "profile" else form_upload)(env, data, slot)
    if route == "profile":
        waiting = pending_ids(env)
        assert waiting                                          # the Extract button is offered for what was stored unread
        assert extract(env, waiting)["queued"] >= 1
    run_worker(env)
    read_pending(env)       # the first visit after the reading records, once per stored file, that no sorting is needed
    assert pending_ids(env) == []                               # nothing is offered for reading any more
    assert not unread_in_form(env)


def unread_in_form(env) -> list[str]:
    form = open_form(env)
    return [name for name, slot in form["documents"].items() if slot["extractionPending"]]


# ----------------------------------------------------------------------------------------------------------- what is stored

def live(env):
    with env.sessions() as db:
        return list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == "candidate",
                                                        Attachment.deleted_at.is_(None)).order_by(Attachment.slot, Attachment.version)))


def pages_of(env, row) -> list[str]:
    with fitz.open(env.storage / row.object_key) as document:
        return [label(page.get_text()) for page in document]


def filed(env) -> dict[str, list[list[str]]]:
    found: dict[str, list[list[str]]] = {}
    for row in live(env):
        found.setdefault(row.slot, []).append(pages_of(env, row))
    return found


def sent(env) -> list[tuple[str, tuple[str, ...]]]:
    return sorted((item["mode"], tuple(sorted(label(text) for text in item["texts"]))) for item in env.veris.submissions)


def counts(env) -> dict:
    with env.sessions() as db:
        return {"jobs": db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.kind != "resort")),
                "markers": db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.kind == "resort")),
                "files": [(row.slot, row.version, row.content_hash) for row in live(env)],
                "audit": db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Uploaded document")),
                "submissions": len(env.veris.submissions)}


def candidate_row(env):
    with env.sessions() as db:
        row = db.scalars(select(Candidate).options(undefer(Candidate.education), undefer(Candidate.field_sources))
                         .where(Candidate.id == "candidate")).one()
        return row.education, dict(row.field_sources or {}), row.passport_no, row.review_status if hasattr(row, "review_status") else None


def readable_ids(env):
    return [row.id for row in live(env) if row.slot in ("resume", "passport", "aadhaar", "education_certificate")]


# ----------------------------------------------------------------------------------------------------------------- (1) to (3)

@pytest.mark.parametrize("route", ["profile", "form"])
def test_mixed_packet_is_read_once_then_extract_again_reads_nothing_and_a_new_page_is_read_alone(world, route):
    original = packet(*MIXED)
    upload_and_read(world, route, original)

    # (1) every document in its own slot; the education rows are applied; the packet is kept byte for byte
    held = filed(world)
    assert held["resume"] == [["resume_1", "resume_2"]]
    assert held["education_certificate"] == [["education"]]
    assert held["passport"] == [["passport"]] and held["aadhaar"] == [["aadhaar"]] and held["medical"] == [["medical"]]
    [bundle] = [row for row in live(world) if row.slot == "source_bundle"]
    assert (world.storage / bundle.object_key).read_bytes() == original
    download = world.api.get(f"/api/files/{bundle.id}")
    assert download.status_code == 200 and download.content == original
    # exactly one resume-mode submission (resume + education pages), one passport, one Aadhaar
    assert sent(world) == [("aadhaar", ("aadhaar",)), ("passport", ("passport",)), ("resume", ("education", "resume_1", "resume_2"))]
    education, sources, passport_no, _ = candidate_row(world)
    assert [row["level"] for row in education] == ["S.S.L.C."] and "Karnataka" in education[0]["institute"]
    assert sources.get("education") == "extracted"
    form = open_form(world)
    for slot in ("resume", "passport", "aadhaar", "education_certificate"):    # every piece shows the packet's reading, none waits to be read
        read = form["documents"][slot]["extraction"]
        assert read["status"] == "done" and not form["documents"][slot]["extractionPending"], slot
    assert [row["level"] for row in form["education"]] == ["S.S.L.C."]
    assert passport_no == "P0000000"

    # (2) reopen the profile, press Extract again: nothing is read, nothing changes
    before, applied = counts(world), candidate_row(world)
    with world.sessions() as db:
        known_jobs = set(db.scalars(select(DocumentExtractionJob.id)))
    assert open_form(world)["education"] == form["education"]
    assert counts(world) == before, "open form"
    assert read_pending(world)["queued"] == 0
    assert counts(world) == before, "read-pending"
    answer = extract(world, readable_ids(world))
    assert answer["queued"] == 0 and answer["existing"] >= 1 and set(answer["jobIds"]) <= set(known_jobs)   # only jobs that already exist
    assert "already" in answer["message"].lower() and "read" in answer["message"].lower()
    assert run_worker(world) == []
    assert counts(world) == before and candidate_row(world) == applied
    assert [row["level"] for row in extract(world, readable_ids(world))["form"]["education"]] == ["S.S.L.C."]   # not blanked, not duplicated
    assert counts(world) == before
    # the same packet uploaded again is not filed or read again either
    again = (profile_upload if route == "profile" else form_upload)(world, original)
    assert run_worker(world) == [] and counts(world) == before and again

    # (3) one genuinely new page: a second education certificate. Exactly one new submission, for that page only.
    upload_and_read(world, route, pdf(SECOND_EDUCATION), "education_certificate")
    assert len(world.veris.submissions) == 4
    assert sorted(item["mode"] for item in world.veris.submissions) == ["aadhaar", "passport", "resume", "resume"]
    assert "P.U.C." in world.veris.submissions[-1]["texts"][0] and len(world.veris.submissions[-1]["texts"]) == 1
    assert {slot: len(rows) for slot, rows in filed(world).items()} == {
        "resume": 1, "education_certificate": 2, "passport": 1, "aadhaar": 1, "medical": 1, "source_bundle": 1}
    levels = sorted(row["level"] for row in candidate_row(world)[0])
    assert levels.count("S.S.L.C.") == 1                      # the earlier row is still there once
    settled = counts(world)
    assert extract(world, readable_ids(world))["queued"] == 0 and run_worker(world) == [] and counts(world) == settled


def test_a_corrected_passport_is_read_alone_and_the_old_one_is_not_read_again(world):
    upload_and_read(world, "form", packet(*MIXED))
    assert len(world.veris.submissions) == 3
    upload_and_read(world, "form", pdf(CORRECTED_PASSPORT), "passport")
    assert [item["mode"] for item in world.veris.submissions] == [item["mode"] for item in world.veris.submissions[:3]] + ["passport"]
    assert "P7654321" in world.veris.submissions[-1]["texts"][0]
    assert len(world.veris.submissions) == 4
    assert extract(world, readable_ids(world))["queued"] == 0 and run_worker(world) == [] and len(world.veris.submissions) == 4


# ----------------------------------------------------------------------------------------------------------------------- (4)

@pytest.mark.parametrize("together", [True, False], ids=["three_pages_in_one_job", "one_job_per_page"])
def test_a_partial_passport_answer_keeps_every_passport_page_together_flags_review_and_is_not_read_again(world, together):
    """The reader gives details for ONE of the three passport pages (the others come back without any text)."""
    world.settings.veris_passport_together_pages = 3 if together else 2
    third = PASSPORT_ADDRESS.replace("File No. COH/1234/2020", "File No. COH/1234/2021 continuation of the address page")
    data = pdf(CV, CONTINUATION, PASSPORT, PASSPORT_ADDRESS, third, AADHAAR, MEDICAL)
    page = {"language": "eng", "width": 595, "height": 842, "average_confidence": 0.95, "processing_time_ms": 1}

    def partial(mode, number, texts):
        if mode == "passport" and together:       # one answer for a three-page job that holds one page
            return {"pages": [{"page_number": 1, "text": texts[0], **page}]}
        if mode == "passport" and number:         # the later single-page jobs come back empty
            return {"pages": [{"page_number": 1, "text": "", **page}], "mrz": {}}
        return {"education": [EDUCATION_ROW]} if mode == "resume" else {}

    world.veris.build = partial
    upload_and_read(world, "profile", data)

    held = filed(world)
    submitted = [item for item in world.veris.submissions if item["mode"] == "passport"]
    assert sorted(label(text) for item in submitted for text in item["texts"]) == ["passport", "passport_2", "passport_2"]   # each page once
    assert [len(item["texts"]) for item in submitted] == ([3] if together else [1, 1, 1])
    assert len(held["passport"]) == 1 and held["passport"][0] == ["passport", "passport_2", "passport_2"]   # filed together, in one file
    assert held["aadhaar"] == [["aadhaar"]] and held["medical"] == [["medical"]]
    [bundle] = [row for row in live(world) if row.slot == "source_bundle"]
    assert (world.storage / bundle.object_key).read_bytes() == data
    form = open_form(world)
    with world.sessions() as db:
        assert db.get(Candidate, "candidate").passport_no == "P0000000"        # what the reader did give is kept
    flagged = form["documents"]["passport"]["files"][0]["extraction"]           # a person is asked to check the passport
    assert flagged["status"] == "needs_review" and "only part of the passport was read" in flagged["message"]
    assert not form["documents"]["passport"]["extractionPending"]

    before = counts(world)
    answer = extract(world, readable_ids(world))
    assert answer["queued"] == 0 and run_worker(world) == [] and read_pending(world)["queued"] == 0
    assert counts(world) == before and len(world.veris.submissions) == before["submissions"]
    assert open_form(world)["documents"]["passport"]["files"][0]["extraction"]["status"] == "needs_review"   # still flagged, never re-read
