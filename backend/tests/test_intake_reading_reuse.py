"""The pages of an emailed / WhatsApp attachment are read by the local OCR once: the reading made for the document reader is what
the split into the other documents (offer, visa, medical ...) is cut from.

The OCR itself is faked (`_PageReader._ocr` returns the text of the page, counting each call), so the counts are exact and no
Tesseract runs: a page is "read" once for every call. Before this change the split read every page a second time.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import threading
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from app.core.cpus import default_page_workers
from app.db.models import EmailIngestionJob, WhatsAppMessage
from app.integrations.ocr import inspection
from app.integrations.ocr.inspection import OCRRead
from app.services import bulk_candidate_import as bulk
from app.services import intake_documents as docs
import people_fixtures as pf
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_email_intake_split import (  # noqa: F401  (splitting is a fixture)
    EVERYTHING, FILED_BY_SPLIT, NAME, REAL_BUILD, everything_output, intake, receipt, run, slots, splitting, subject,
)
from test_email_intake import row_count  # noqa: F401
from test_whatsapp_intake import receipt as whatsapp_receipt, setup  # noqa: F401  (setup is a fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture)
from auto_assign import round_robin_on  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("round_robin_on")


@lru_cache(maxsize=None)
def _scan_of(tokens, person):
    """(a scanned, image-only PDF of the pages, the text each page reads as)."""
    specs = [pf.page(person, token) for token in tokens]
    with fitz.open(stream=pf.build_pdf(specs, "text"), filetype="pdf") as document:
        texts = [page.get_text("text", sort=True).strip() for page in document]
    return pf.build_pdf(specs, "scan"), tuple(texts)


def scan_of(tokens=EVERYTHING, person="tharun"):
    scan, texts = _scan_of(tuple(tokens), person)
    return scan, list(texts)


class FakeOCR:
    """Replaces the page reader's OCR step: returns the known text of the page and counts the calls (the OCR passes)."""

    def __init__(self, monkeypatch, texts):
        self.calls, self.texts, self._lock = 0, texts, threading.Lock()
        monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
        monkeypatch.setattr(bulk, "get_settings", lambda: SimpleNamespace(tesseract_cmd=None, ocr_languages="eng"))
        fake = self

        def ocr(reader, index, image, dpi):
            with fake._lock:
                fake.calls += 1
            text = fake.texts[index]
            return OCRRead(text, 92.0, words=len(text.split()), good_words=len(text.split()))
        monkeypatch.setattr(inspection._PageReader, "_ocr", ocr)


def store(index, content):
    return f"piece-{index}-{hashlib.sha256(content).hexdigest()[:8]}"


def shape(split):
    return [(piece["slots"], piece["pages"], piece["content_hash"]) for piece in split["pieces"]]


def read_in_scope(data):
    with docs.reading_scope() as scope:
        inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
    return scope


# ------------------------------------------------------------------------------------------------------------ the engine level

def test_the_split_reuses_the_reading_made_for_the_reader_and_runs_no_second_ocr_pass(monkeypatch):
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    scope = read_in_scope(data)
    first_read = ocr.calls
    assert first_read >= len(texts) and len(scope) == 1

    reused = docs.build_split(data, store, covered=[1, 2, 3, 4, 5, 6], extracted=True, readings=scope)

    assert ocr.calls == first_read                       # the split read nothing
    # The old way (nothing to reuse: the pages are read again) gives exactly the same pieces, at the price of a second reading.
    again = docs.build_split(data, store, covered=[1, 2, 3, 4, 5, 6], extracted=True)
    assert ocr.calls >= 2 * first_read - 1 and ocr.calls > first_read
    assert shape(reused) == shape(again) and reused["pieces"]
    assert {slot for piece in reused["pieces"] for slot in piece["slots"]} >= set(FILED_BY_SPLIT)


def test_a_retry_in_another_process_cuts_from_the_saved_page_kinds_without_reading_or_keeping_text(monkeypatch):
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    scope = read_in_scope(data)
    digest = hashlib.sha256(data).hexdigest()
    expected = docs.build_split(data, store, covered=[1, 2, 3, 4, 5, 6], extracted=True, readings=scope)
    state = {}
    assert docs.remember_kinds(state, digest, scope)
    saved = json.loads(json.dumps(state))["page_kinds"]       # what the job state keeps survives JSON ...
    dumped = json.dumps(saved)
    assert all(fragment not in dumped for text in texts for fragment in (text[:30], NAME))   # ... and holds no page text
    report = scope[digest].to_dict()                          # the saved inspection: statuses and kinds, no text
    assert all("text" not in page for page in report["pages"])
    before = ocr.calls

    # A new process: no reading in memory, only what the job state saved.
    restored = docs.build_split(data, store, covered=[1, 2, 3, 4, 5, 6], extracted=True, readings={}, report=report, page_kinds=saved)

    assert ocr.calls == before
    assert shape(restored) == shape(expected)
    # Kinds saved for other bytes, or no kinds at all: never trusted, the pages are read as they always were.
    docs.build_split(data, store, covered=[1, 2], extracted=True, report=report, page_kinds={**saved, "sha256": "0" * 64})
    assert ocr.calls > before
    after_wrong = ocr.calls
    docs.build_split(data, store, covered=[1, 2], extracted=True, report=report, page_kinds=None)
    assert ocr.calls > after_wrong


def test_a_reading_that_fails_falls_back_to_reading_the_pages(monkeypatch):
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    scope = read_in_scope(data)
    expected = docs.build_split(data, store, covered=[1, 2], extracted=True, readings=scope)
    scope[hashlib.sha256(data).hexdigest()]._pdf = b"damaged"   # damaged in memory: cutting from it raises
    before = ocr.calls
    assert shape(docs.build_split(data, store, covered=[1, 2], extracted=True, readings=scope)) == shape(expected)
    assert ocr.calls > before


def test_a_photographed_document_is_not_read_twice(monkeypatch):
    spec = pf.page("tharun", "medical")
    texts = [spec_text for spec_text in scan_of(["medical"])[1]]
    ocr = FakeOCR(monkeypatch, texts)
    image = pf.build_jpeg(spec)
    scope = read_in_scope(image)
    first = ocr.calls
    reused = docs.build_split(image, store, readings=scope)
    assert ocr.calls == first
    assert [piece["slots"] for piece in reused["pieces"]] == [["medical"]]
    assert [piece["slots"] for piece in docs.build_split(image, store)["pieces"]] == [["medical"]] and ocr.calls > first
    # And from the saved kind alone (another process).
    state = {}
    assert docs.remember_kinds(state, hashlib.sha256(image).hexdigest(), scope)
    before = ocr.calls
    restored = docs.build_split(image, store, report=scope[hashlib.sha256(image).hexdigest()].to_dict(), page_kinds=state["page_kinds"])
    assert ocr.calls == before and [piece["slots"] for piece in restored["pieces"]] == [["medical"]]


def test_the_capture_is_bounded_and_scoped(monkeypatch):
    data, texts = scan_of(["offer", "evisa"])
    FakeOCR(monkeypatch, texts)
    inspection.inspect_document(data)                          # outside any scope nothing is kept
    assert inspection._CAPTURE.get() is None
    with inspection.capture_inspections() as outer:
        inspection.inspect_document(data)
        inspection.inspect_document(data)                      # the same bytes again: the first reading stays
        assert len(outer) == 1
        monkeypatch.setattr(inspection, "_CAPTURE_LIMIT", 1)
        inspection.inspect_document(scan_of(["medical", "pcc"])[0])
        assert len(outer) == 1
    assert inspection._CAPTURE.get() is None
    kept = {}
    docs.keep_readings(kept, {str(n): SimpleNamespace(_pdf=b"x" * 10) for n in range(20)})
    assert len(kept) == docs._KEEP_READINGS
    monkeypatch.setattr(docs, "_KEEP_READING_BYTES", 25)
    kept = {}
    docs.keep_readings(kept, {str(n): SimpleNamespace(_pdf=b"x" * 10) for n in range(5)})
    assert len(kept) == 2


def test_the_capture_follows_a_thread_started_inside_the_scope(monkeypatch):
    """The reader may run the local reading on a thread of its own (it sends settled pages early); it runs in a copy of the context."""
    import contextvars
    data, texts = scan_of(["offer", "evisa"])
    FakeOCR(monkeypatch, texts)
    with inspection.capture_inspections() as found:
        worker = threading.Thread(target=contextvars.copy_context().run, args=(lambda: inspection.inspect_document(data),))
        worker.start()
        worker.join()
    assert list(found) == [hashlib.sha256(data).hexdigest()]


# ------------------------------------------------------------------------------------------------------------ the email job

def reading_extract(monkeypatch, outputs, *, pending_first=False):
    """A stand-in for `extract_attachment` that does the local reading for real (so it is the reading the split reuses)."""
    calls = {"n": 0}

    def extract(data, *, state, save, **kwargs):
        calls["n"] += 1
        output = deepcopy(outputs[data])
        if pending_first and calls["n"] == 1:
            inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
            state.update(source_sha256=hashlib.sha256(data).hexdigest(), inspection=deepcopy(output["inspection"]), person_detection_version=1)
            save(state)
            raise subject.ExtractionPending("synthetic-accepted", "resume")
        if not state.get("inspection"):          # a resumed job restores its saved inspection: nothing is read
            inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
        state.update(source_sha256=hashlib.sha256(data).hexdigest(), inspection=deepcopy(output["inspection"]), person_detection_version=1)
        save(state)
        return output
    monkeypatch.setattr(subject, "extract_attachment", extract)
    return calls


def test_an_emailed_scan_is_read_once_and_the_other_documents_are_still_filed(splitting, monkeypatch):
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    reading_extract(monkeypatch, {data: everything_output()})
    inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
    one_reading = ocr.calls                                     # what ONE reading of this file costs
    ocr.calls = 0
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)

    assert ocr.calls == one_reading                             # reader + split together: one reading, not two
    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        assert set(slots(db, job.candidate_id)) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}
        kinds = job.state[next(key for key in job.state if isinstance(job.state[key], dict) and "page_kinds" in job.state[key])]["page_kinds"]
        assert kinds["sha256"] == hashlib.sha256(data).hexdigest() and "text" not in kinds and NAME not in json.dumps(kinds)


def test_a_job_that_waits_for_the_reader_and_resumes_reads_the_pages_once(splitting, monkeypatch):
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
    one_reading = ocr.calls
    ocr.calls = 0
    reading_extract(monkeypatch, {data: everything_output()}, pending_first=True)
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)                                     # reads the pages, the reader's job is accepted and pending
    with splitting.sessions() as db:
        assert db.get(EmailIngestionJob, job_id).status == "retry"
    assert ocr.calls == one_reading
    # Another process, a while later: the saved inspection is restored, nothing is in memory.
    monkeypatch.setattr(bulk, "split_by_content", lambda *a, **kw: pytest.fail("the pages must not be read again"))
    run(splitting, job_id)

    assert ocr.calls == one_reading
    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed", job.last_error
        assert set(slots(db, job.candidate_id)) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}


def test_a_job_saved_before_the_kinds_were_kept_still_reads_the_pages_as_before(splitting, monkeypatch):
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    outputs = {data: everything_output()}

    def extract(data, *, state, save, **kwargs):               # no local reading in this run, no kinds saved (an old job state)
        state.update(source_sha256=hashlib.sha256(data).hexdigest(), inspection=deepcopy(outputs[data]["inspection"]), person_detection_version=1)
        save(state)
        return deepcopy(outputs[data])
    monkeypatch.setattr(subject, "extract_attachment", extract)
    job_id, _ = receipt(splitting, data=data)

    run(splitting, job_id)

    assert ocr.calls > 0                                        # the engine read the pages itself: today's behaviour is the fallback
    with splitting.sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        assert job.status == "completed" and set(slots(db, job.candidate_id)) >= set(FILED_BY_SPLIT)


# ------------------------------------------------------------------------------------------------------------ WhatsApp

def test_a_whatsapp_scan_is_read_once_too(setup, monkeypatch):
    from app.services.whatsapp_intake import process_message
    monkeypatch.setattr(subject.intake_documents, "build_split", REAL_BUILD)
    sessions, settings = setup
    data, texts = scan_of()
    ocr = FakeOCR(monkeypatch, texts)
    inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
    one_reading = ocr.calls
    ocr.calls = 0
    output = everything_output()

    def extract(data, *, state, save, **kwargs):
        inspection.inspect_document(data, max_pages=300, page_timeout=20, document_timeout=600)
        state.update(source_sha256=hashlib.sha256(data).hexdigest(), inspection=deepcopy(output["inspection"]), person_detection_version=1)
        save(state)
        return deepcopy(output)

    message_id = whatsapp_receipt(sessions, settings)
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (data, "application/pdf"), extract=extract)

    assert ocr.calls == one_reading
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        assert set(slots(db, row.candidate_id)) == {"source_bundle", "resume", "passport", "aadhaar", *FILED_BY_SPLIT}
        assert row.processing_state["page_kinds"]["sha256"] == hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------------------------------------ the worker default

@pytest.mark.parametrize("cpus, expected", [(1, 1), (2, 1), (3, 1), (4, 2), (6, 3), (8, 4), (12, 4), (64, 4)])
def test_the_default_page_workers_are_half_the_cpus_between_one_and_four(cpus, expected):
    assert default_page_workers(cpus) == expected


def test_the_setting_defaults_to_that_and_a_configured_value_wins(monkeypatch):
    from app.core.config import Settings
    monkeypatch.delenv("OCR_PAGE_WORKERS", raising=False)
    assert Settings(_env_file=None).ocr_page_workers == default_page_workers()
    monkeypatch.setenv("OCR_PAGE_WORKERS", "7")
    assert Settings(_env_file=None).ocr_page_workers == 7
    assert 1 <= inspection._DEFAULT_PAGE_WORKERS <= 4
