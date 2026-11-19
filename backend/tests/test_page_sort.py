"""Page sorting that survives (services/page_sort.py): a long scan is read in saved units across worker passes, a crash resumes without
re-reading finished pages, two workers never read the same page twice, the progress and the plain per-page reasons reach the screen, and
a person's correction of one page is idempotent and recorded.

The OCR itself is faked (a reader that returns each unit's text layer and counts the pages it was asked for); the engine, the queue
statements and the job state are the real ones on a disposable PostgreSQL.
"""
import time
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import undefer

from app.db.models import Attachment, AuditEntry, DocumentExtractionJob
from app.integrations.ocr import inspection
from app.integrations.ocr.inspection import InspectedPage
from app.services import document_splitting, page_sort
from test_document_extraction import pdf
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_manual_extraction import intake, process, start, upload  # noqa: F401  (intake is a fixture)
from test_ocr_classification import CV, PASSPORT

LETTER = "Dear Sir, thank you for your letter of last week about the arrangements for the meeting. Regards, the office."
NOISE = "xq zv kk jj qq"


class Crash(BaseException):
    """A worker that dies (not an error the job could record)."""


def texts_of(unit_pdf):
    with fitz.open(stream=unit_pdf, filetype="pdf") as document:
        return [page.get_text("text").strip() for page in document]


class Reader:
    """A fake `page_sort.inspect_unit`: reads the unit's text layer; remembers every page it was asked for."""

    def __init__(self, delay=0.0, unread=(), timeout_once=(), crash_at=None):
        self.asked, self.delay, self.unread, self.timeout_once, self.crash_at = [], delay, set(unread), set(timeout_once), crash_at
        self.armed = True

    def __call__(self, unit_pdf, numbers, *, limits, page_timeout, document_timeout):
        if self.crash_at is not None and self.armed and self.crash_at in numbers:
            self.armed = False
            raise Crash()
        time.sleep(self.delay)
        out = []
        for number, text in zip(numbers, texts_of(unit_pdf)):
            self.asked.append(number)
            if number in self.timeout_once:
                self.timeout_once.discard(number)
                out.append((InspectedPage(number, "unread", "unknown", "not_processed", "", reason="Reading this document went over the time limit."), None))
            elif number in self.unread:
                out.append((InspectedPage(number, "unread", "unknown", "tesseract", "x", reason="No readable text on this page after 3 reading settings. The print may be too faint."), None))
            else:
                out.append((InspectedPage(number, "read", "unknown", "tesseract", text, 0.9), {"confidence": 0.9, "seconds": 0.5, "rotation": 90 if number == 2 else 0}))
        return out


def twelve():
    return pdf(CV, "Work Experience continues here with more duties and dates of employment.", PASSPORT, "Passport address page. Name of Father SURESH KUMAR. Address 12 EXAMPLE ROAD KOCHI PIN 682001",
               LETTER, LETTER + " Second.", NOISE, LETTER + " Third.", LETTER + " Fourth.", LETTER + " Fifth.", LETTER + " Sixth.", LETTER + " Seventh.")


def configure(env, monkeypatch, reader, **limits):
    for name, value in dict(sort_unit_pages=2, sort_page_workers=1, sort_slice_seconds=600.0, sort_total_seconds=900.0, sort_page_attempts=3,
                            sort_resumable_min_pages=2, sort_max_pages=120).items():
        setattr(env.settings, name, limits.get(name, value))
    monkeypatch.setattr(document_splitting, "costs_ocr", lambda data: True)
    monkeypatch.setattr(page_sort, "inspect_unit", reader)


def job_state(env, identifier):
    with env.sessions() as db:
        job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.result),
                                                              undefer(DocumentExtractionJob.draft)).where(DocumentExtractionJob.id == identifier))
        return job


def run_to_end(env, identifier, passes=40):
    seen = []
    for _ in range(passes):
        process(env, identifier)
        job = job_state(env, identifier)
        seen.append(job)
        if job.status not in ("pending", "processing", "retry"):
            return seen
    pytest.fail("the file never finished")


# ------------------------------------------------------------------ resumable progress

def test_a_file_that_needs_several_passes_finishes_with_every_page_accounted_for_and_nothing_read_twice(intake, monkeypatch):
    reader = Reader(delay=0.06)
    configure(intake, monkeypatch, reader, sort_slice_seconds=0.03)          # every pass reads one unit, then hands the file back
    job = upload(intake, twelve())
    start(intake, job["id"])
    process(intake, job["id"])
    first = job_state(intake, job["id"])
    assert first.status == "retry" and first.attempts == 0 and first.last_error is None          # handed back, no attempt counted
    sorting = first.result["sorting"]
    assert sorting["pageCount"] == 12 and sorting["pagesRead"] == 2 and sorting["step"] == "waiting"
    assert [page["state"] for page in sorting["pages"][:3]] == ["read", "read", "waiting"]
    live = intake.client.get("/api/ocr/jobs/groups/" + job["groupId"]).json()[0]       # the screen's own poll sees it
    assert live["result"]["sorting"]["pagesRead"] == 2

    passes = run_to_end(intake, job["id"])
    final = passes[-1]
    assert final.status in ("completed", "needs_review")
    assert sorted(reader.asked) == list(range(1, 13))                                    # each page read exactly once, none lost
    done = final.result["sorting"]
    assert done["step"] == "done" and done["pageCount"] == 12 and done["pagesRead"] == 12 and len(done["pages"]) == 12
    assert done["passes"] >= 6
    assert not [key for key in final.state if key.startswith((page_sort.PAGE_PREFIX, page_sort.UNIT_PREFIX))]      # no page text is kept
    assert done["pages"][1]["turned"] == 90                                              # a sideways page says it was turned
    kinds = {page["page"]: page.get("kind") for page in done["pages"]}
    assert kinds[1] == "resume" and kinds[3] == "passport"
    assert {item["page"] for item in done["unplaced"]} >= {7}
    assert all(item["reason"] for item in done["unplaced"])


def test_a_crash_in_the_middle_of_a_file_resumes_without_reading_the_finished_pages_again(intake, monkeypatch):
    reader = Reader(crash_at=7)
    configure(intake, monkeypatch, reader)
    job = upload(intake, twelve())
    start(intake, job["id"])
    with pytest.raises(Crash):
        process(intake, job["id"])
    crashed = job_state(intake, job["id"])
    saved = sorted(int(key[3:]) for key in crashed.state if key.startswith(page_sort.PAGE_PREFIX))
    assert saved == [1, 2, 3, 4, 5, 6]                                                   # the finished units survived the crash
    before = sorted(reader.asked)
    run_to_end(intake, job["id"])
    assert sorted(reader.asked) == list(range(1, 13)) and sorted(reader.asked[len(before):]) == list(range(7, 13))


def test_a_page_that_ran_out_of_time_is_read_again_and_a_dead_unit_never_stops_the_others(intake, monkeypatch):
    reader = Reader(timeout_once={5})
    configure(intake, monkeypatch, reader)
    job = upload(intake, twelve())
    start(intake, job["id"])
    final = run_to_end(intake, job["id"])[-1]
    assert reader.asked.count(5) == 2 and all(reader.asked.count(n) == 1 for n in range(1, 13) if n != 5)
    assert final.result["sorting"]["pagesRead"] == 12


def test_a_page_that_cannot_be_placed_gets_a_plain_reason_and_is_offered_for_a_choice(intake, monkeypatch):
    configure(intake, monkeypatch, Reader())
    job = upload(intake, twelve())
    start(intake, job["id"])
    final = run_to_end(intake, job["id"])[-1]
    unplaced = {item["page"]: item for item in final.result["sorting"]["unplaced"]}
    assert unplaced[7] == {"page": 7, "code": "unknown", "reason": page_sort.REASONS["unknown"]}
    assert 1 not in unplaced and 3 not in unplaced


def test_every_reason_a_page_can_have_is_said_in_plain_words():
    def record(status, method, reason="", text="", detail=None):
        return {"status": status, "method": method, "reason": reason, "text": text, "detail": detail or {}}
    assert page_sort.explain(record("unread", "not_processed"), attempts_left=True) == ("retry", "time ran out - retrying")
    assert page_sort.explain(record("unread", "not_processed")) == ("timeout", "time ran out")
    assert page_sort.explain(record("unread", "failed")) == ("failed", "could not be opened")
    assert page_sort.explain(record("unread", "tesseract", "The scan is too dark to read")) == ("dark", "too dark")
    assert page_sort.explain(record("unread", "tesseract", "No readable text on this page")) == ("no_text", "no text found")
    assert page_sort.explain(record("unread", "tesseract", "faint", text="some words here that were read")) == ("faint", "too faint or blurred")
    assert page_sort.explain(record("blank", "photo_no_text"))[0] == "photo" and page_sort.explain(record("read", "tesseract")) == ("", "")


def test_two_workers_never_read_the_same_page_twice(intake):
    upload_job = upload(intake, twelve())
    start(intake, upload_job["id"])
    with intake.sessions() as db:
        db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.id == upload_job["id"]).values(status="processing", claimed_by="worker-a"))
        db.commit()
    a = page_sort.JobPageStore(intake.sessions, upload_job["id"], "worker-a", intake.settings)
    b = page_sort.JobPageStore(intake.sessions, upload_job["id"], "worker-b", intake.settings)
    assert b.claim([1, 2], {}, {"step": "sorting"}) == "lost"                         # not the lease holder: it may not even start a unit
    assert a.claim([1, 2], {}, {"step": "sorting"}) == "ok"
    record = page_sort.record_of(InspectedPage(1, "read", "unknown", "tesseract", "text", 0.9), None, seconds=1, attempts=1)
    record2 = {**record, "page": 2}
    assert a.save([record, record2], {"sha256": "x", "pages": 12}, {"step": "sorting"}, claimed=[1]) is True
    assert a.claim([1, 2], {}, {"step": "sorting"}) == "done"                          # the same unit again: its pages are saved, never read twice
    assert a.claim([1, 2], {1: 1, 2: 1}, {"step": "sorting"}) == "ok"                  # ... unless a page is being read again after a time-out
    with intake.sessions() as db:                                                      # the lease moves to worker b
        db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.id == upload_job["id"]).values(claimed_by="worker-b"))
        db.commit()
    assert a.save([record], {"sha256": "x", "pages": 12}, {"step": "sorting"}, claimed=[]) is False      # the old worker's late write is refused
    assert a.claim([3, 4], {}, {"step": "sorting"}) == "lost"
    assert b.claim([3, 4], {}, {"step": "sorting"}) == "ok"


def test_the_whole_file_cap_marks_the_unread_pages_instead_of_dropping_them():
    state = {}
    store = page_sort.MemoryStore(state)
    ticks = iter(range(0, 10_000, 100))
    limits = page_sort.limits_from(SimpleNamespace(sort_unit_pages=2, sort_page_workers=1, sort_total_seconds=250.0, sort_slice_seconds=10_000.0,
                                                   sort_resumable_min_pages=2))
    reader = Reader()
    data = twelve()
    inspected = page_sort.sort_file(data, state, store, limits, reader=reader, clock=lambda: next(ticks))
    capped = [page.page_number for page in inspected.pages if page.status == "unread" and page.method == "not_processed"]
    assert capped and capped == list(range(13 - len(capped), 13))                         # the last pages, said so
    assert len(inspected.pages) == 12 and inspected.completeness["pages_unread"] == len(capped)
    assert all("ran out of time" in page.reason for page in inspected.pages if page.page_number in capped)
    from app.services import bulk_candidate_import as bulk
    assert bulk._split_inspected(inspected, data, [], "x.pdf").cut_short is True             # the split knows it is not final


def test_the_assembled_reading_is_the_one_the_whole_file_reading_gives():
    data = twelve()
    whole = inspection.inspect_document(data, page_workers=1)
    state = {}
    limits = page_sort.limits_from(SimpleNamespace(sort_unit_pages=3, sort_page_workers=2, sort_slice_seconds=10_000.0, sort_resumable_min_pages=2))
    pieces = page_sort.sort_file(data, state, page_sort.MemoryStore(state), limits, reader=page_sort.inspect_unit)
    a, b = whole.to_dict(), pieces.to_dict()
    for key in ("page_count", "resume_pages", "passport_pages", "aadhaar_pages", "unread_pages", "unknown_pages", "inherited_pages", "complete", "warnings"):
        assert a[key] == b[key], key
    assert [(p["status"], p["document_type"], p["also"], p["inherited"]) for p in a["pages"]] == [(p["status"], p["document_type"], p["also"], p["inherited"]) for p in b["pages"]]


# ------------------------------------------------------------------ the person's correction of one page

def finished_job(env, monkeypatch, **reader_options):
    configure(env, monkeypatch, Reader(**reader_options))
    job = upload(env, twelve())
    start(env, job["id"])
    run_to_end(env, job["id"])
    return job


def correct(env, identifier, page, kind):
    return env.client.post(f"/api/ocr/jobs/{identifier}/pages/{page}/kind", json={"kind": kind})


def audit(env, identifier):
    with env.sessions() as db:
        return [entry.meta for entry in db.scalars(select(AuditEntry).where(AuditEntry.action == "Corrected the field of a page"))
                if entry.meta.get("document_job_id") == identifier]


def test_a_correction_of_a_page_not_yet_on_a_candidate_is_kept_once_and_recorded_for_training(intake, monkeypatch):
    job = finished_job(intake, monkeypatch)
    first = correct(intake, job["id"], 7, "medical")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["correction"]["kind"] == "medical"
    sorting = body["result"]["sorting"]
    assert all(item["page"] != 7 for item in sorting["unplaced"]) and sorting["pages"][6]["kind"] == "medical"
    again = correct(intake, job["id"], 7, "medical")
    assert again.status_code == 200 and again.json()["correction"]["alreadyDone"] is True
    [entry] = audit(intake, job["id"])
    assert entry["page"] == 7 and entry["kind"] == "medical" and len(entry["page_text_sha256"]) == 24 and "text" not in entry


def test_a_correction_is_refused_for_strangers_bad_pages_and_unknown_fields(intake, monkeypatch):
    job = finished_job(intake, monkeypatch)
    assert correct(intake, job["id"], 99, "medical").status_code == 404
    assert correct(intake, job["id"], 7, "photo").status_code == 422 and correct(intake, job["id"], 7, "nonsense").status_code == 422
    intake.actor.id = "other"
    assert correct(intake, job["id"], 7, "medical").status_code == 404                  # someone else's upload
    intake.actor.id = "owner"
    assert audit(intake, job["id"]) == []


def test_a_correction_on_a_saved_candidates_file_files_the_page_and_queues_the_reading(intake, monkeypatch):
    job = finished_job(intake, monkeypatch)
    assert intake.client.post(f"/api/ocr/jobs/{job['id']}/attach", json={"candidateId": "candidate"}).status_code == 200
    answer = correct(intake, job["id"], 7, "passport")
    assert answer.status_code == 200, answer.text
    assert answer.json()["correction"]["filed"] is True and answer.json()["correction"]["readingQueued"] is True
    with intake.sessions() as db:
        rows = list(db.scalars(select(Attachment).where(Attachment.entity_id == "candidate", Attachment.slot == "passport")))
        assert any("corrected_page" in (entry.meta or {}) for entry in db.scalars(select(AuditEntry).where(AuditEntry.action == "Uploaded document")))
        queued = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == "candidate", DocumentExtractionJob.status == "pending")))
        assert rows and rows[-1].version == len(rows) and rows[-1].file_name.endswith("Passport.pdf")
    assert queued
    count = len(rows)
    repeated = correct(intake, job["id"], 7, "passport")
    assert repeated.json()["correction"]["alreadyDone"] is True
    with intake.sessions() as db:
        assert len(list(db.scalars(select(Attachment).where(Attachment.entity_id == "candidate", Attachment.slot == "passport")))) == count
