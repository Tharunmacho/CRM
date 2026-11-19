"""Sort the pages of a long scan in page-sized units that survive: a crash, a restart, a busy night or a 40-page phone scan never costs
more than the unit being read, and nothing ends silently as "not split and not read".

Why. The local reading (`integrations/ocr/inspection.inspect_document`) reads a WHOLE file inside one time window and keeps its result in
memory: a 27-page scan that needed more than the window had its last pages dropped to "Other documents", and a crash after 9 minutes lost
the 9 minutes. This module reads the same file as a series of small units and saves every finished page in the job at once:

  * A UNIT is up to `sort_unit_pages` consecutive pages. It is cut out of the file and read by `inspect_document` (so every OCR
    improvement applies unchanged); what each page gave (status, text, method, confidence, reason, turn, seconds, how many tries) is
    saved as the key `sp:<page>` of the job's state, in ONE statement that also renews the lease and publishes the progress.
  * A PASS (one claim of the job by a worker) reads units, `sort_page_workers` at a time, until the file is done, the pass has used
    `sort_slice_seconds`, or the file's own cap (`sort_total_seconds` of reading in all its passes) is used up. A pass that is not
    finished hands the file back to the queue (`SortContinues`, no attempt is counted): another file, or another worker, takes the next
    turn, and the file continues from the pages already saved. Pages are never read twice: before a unit is read the job claims it
    with a fenced statement that fails when a page of it already has its result or the lease belongs to someone else.
  * A page that ran out of time or crashed is read again (up to `sort_page_attempts` times, with more time each time); a page that is
    simply unreadable is final at once, with a plain reason ("no text found", "too dark", ...).
  * When every page is accounted for the reading is assembled into the same `Inspection` the whole-file reading gives (the pages are
    classified together, so a continuation page still takes the kind of the page before it), and the page texts are dropped from the
    job (only kinds, reasons and times stay, see `finish`).

The cap is a decision, not a silent loss: the pages not read by then are marked "ran out of time" and the file is `cut_short` as before
(the reading says needs-review, the background re-sort tries again later with a fresh budget).
"""
from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import hashlib
import json
import logging
import math
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

log = logging.getLogger(__name__)

META_KEY = "sort"        # state["sort"]: what the pages of this file need (see `Ledger`) and, once finished, the summary
PAGE_PREFIX = "sp:"      # state["sp:7"]: the saved reading of page 7
UNIT_PREFIX = "su:"      # state["su:5"]: the unit starting at page 5 was claimed (removed when its result is saved)
RECORD_VERSION = 1
TEXT_LIMIT = 20_000      # characters of one page's text kept while the file is being sorted
RETRYABLE_METHODS = ("not_processed", "failed")      # a page stopped by a time limit or an error: read again

# What a page that could not be placed says, in plain words (the screen shows `reason`; `code` is for code and tests).
REASONS = {
    "retry": "time ran out - retrying",
    "timeout": "time ran out",
    "failed": "could not be opened",
    "dark": "too dark",
    "faint": "too faint or blurred",
    "no_text": "no text found",
    "photo": "a photo with no text",
    "blank": "blank page",
    "too_many": "over the page limit",
    "unknown": "no wording of a known document",
}


class SortContinues(Exception):
    """This pass is over but the file is not: it goes back to the queue (no attempt counted) and continues from the saved pages.

    Raised as an `ExtractionPending` (so the reading job's existing hand-back path takes it), see `continues`."""


def continues(message: str):
    """`SortContinues` that is also an `ExtractionPending`, so `manual_extraction.process_job` hands the job back without an attempt."""
    from app.services.document_extraction import ExtractionPending

    class _Continues(SortContinues, ExtractionPending):
        retry_seconds = 1.0

        def __init__(self, text: str):
            ExtractionPending.__init__(self, "", "pages")
            self.args = (text,)

    return _Continues(message)


class JobLost(Exception):
    """The job no longer belongs to this claim (finished, released, or leased to another worker): stop writing."""


# ------------------------------------------------------------------ limits

@dataclass(frozen=True)
class Limits:
    unit_pages: int = 4
    workers: int = 1
    slice_seconds: float = 120.0
    total_seconds: float = 900.0
    page_timeout: float = 20.0
    page_attempts: int = 3
    max_pages: int = 120
    min_pages: int = 10
    tesseract_cmd: str | None = None
    languages: str = "eng"


def limits_from(settings) -> Limits:
    def pick(name, default):
        value = getattr(settings, name, None)
        return default if value is None else value
    workers = pick("sort_page_workers", None) or pick("ocr_page_workers", 1)
    return Limits(
        unit_pages=max(1, int(pick("sort_unit_pages", 4))), workers=max(1, int(workers)),
        slice_seconds=float(pick("sort_slice_seconds", 120.0)), total_seconds=float(pick("sort_total_seconds", 900.0)),
        page_timeout=float(pick("ocr_page_timeout_seconds", 20.0)), page_attempts=max(1, int(pick("sort_page_attempts", 3))),
        max_pages=max(2, int(pick("sort_max_pages", 120))), min_pages=max(2, int(pick("sort_resumable_min_pages", 10))),
        tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng") or "eng")


# ------------------------------------------------------------------ plain reasons

def explain(record: dict, *, attempts_left: bool = False) -> tuple[str, str]:
    """(code, plain reason) for a page: "" for a page that was read normally. Never any text from the page."""
    status, method = record.get("status"), record.get("method")
    reason = str(record.get("reason") or "")
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    if status == "blank":
        code = "photo" if method == "photo_no_text" else "blank"
        return code, REASONS[code]
    if status == "read":
        return "", ""
    if method in RETRYABLE_METHODS:
        if "more than" in reason and "pages" in reason:
            return "too_many", REASONS["too_many"]
        code = ("retry" if attempts_left else "timeout") if method == "not_processed" else ("retry" if attempts_left else "failed")
        return code, REASONS[code]
    lowered = (reason + " " + " ".join(str(value) for value in detail.values() if isinstance(value, str))).lower()
    if "dark" in lowered or detail.get("photo") is True:
        return "dark", REASONS["dark"]
    text = str(record.get("text") or "")
    if sum(c.isalnum() for c in text) >= 15 or any(word in lowered for word in ("faint", "blurred")):
        return "faint", REASONS["faint"]
    return "no_text", REASONS["no_text"]


# ------------------------------------------------------------------ the saved reading of a page

def page_key(number: int) -> str:
    return f"{PAGE_PREFIX}{number}"


def unit_key(first: int) -> str:
    return f"{UNIT_PREFIX}{first}"


def _plain(value):
    return json.loads(json.dumps(value, default=str))


def record_of(page, detail: dict | None, *, seconds: float, attempts: int, worker: str | None = None) -> dict:
    """What is saved of one page reading: everything `assemble` needs to rebuild the page without reading it again."""
    detail = dict(detail or {})
    confidence = detail.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 1.0 if page.method == "native_text" else page.confidence
    record = {"v": RECORD_VERSION, "page": page.page_number, "status": page.status, "method": page.method,
              "confidence": round(float(confidence or 0.0), 3), "reason": (page.reason or "")[:400],
              "text": (page.text or "")[:TEXT_LIMIT], "seconds": round(float(seconds), 2), "attempts": int(attempts),
              "turned": detail.get("rotation") if detail.get("rotation") in (90, 180, 270) else None,
              "detail": _plain({key: value for key, value in detail.items() if key != "page"}), "at": round(time.time(), 1)}
    if worker:
        record["by"] = worker
    return record


def page_from_record(record: dict):
    from app.integrations.ocr.inspection import InspectedPage
    return InspectedPage(int(record["page"]), record["status"], "unknown" if record["status"] != "blank" else "other",
                         record.get("method") or "", record.get("text") or "", float(record.get("confidence") or 0.0), record.get("reason") or "")


def _valid(record) -> bool:
    return (isinstance(record, dict) and record.get("v") == RECORD_VERSION and isinstance(record.get("page"), int)
            and record.get("status") in ("read", "blank", "unread"))


def is_final(record: dict, limits: Limits) -> bool:
    """A page that needs no further reading: read, blank, unreadable for good, or out of attempts."""
    if record.get("status") in ("read", "blank"):
        return True
    if record.get("capped"):
        return True
    if record.get("method") in RETRYABLE_METHODS:
        return int(record.get("attempts") or 0) >= limits.page_attempts
    return True


@dataclass
class Ledger:
    """The saved readings of one file's pages, rebuilt from the job's state at the start of a pass."""
    sha256: str
    count: int
    records: dict[int, dict] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, state: dict, digest: str, count: int) -> "Ledger":
        meta = state.get(META_KEY) if isinstance(state.get(META_KEY), dict) else {}
        if meta.get("sha256") != digest or meta.get("pages") != count:
            meta = {}          # another file (or none): nothing saved applies
        ledger = cls(digest, count, {}, dict(meta))
        if meta:
            for number in range(1, count + 1):
                record = state.get(page_key(number))
                if _valid(record) and record["page"] == number:
                    ledger.records[number] = record
        ledger.meta.setdefault("sha256", digest)
        ledger.meta.setdefault("pages", count)
        ledger.meta.setdefault("spent", 0.0)
        ledger.meta.setdefault("passes", 0)
        ledger.meta.setdefault("startedAt", datetime.now(timezone.utc).isoformat())
        return ledger

    def pending(self, limits: Limits) -> list[int]:
        return [number for number in range(1, self.count + 1) if number not in self.records or not is_final(self.records[number], limits)]

    def units(self, limits: Limits) -> list[list[int]]:
        """The pending pages in units: the file's fixed windows of `unit_pages` pages, each holding only its pending pages."""
        pending = set(self.pending(limits))
        out: list[list[int]] = []
        for first in range(1, self.count + 1, limits.unit_pages):
            window = [number for number in range(first, min(self.count, first + limits.unit_pages - 1) + 1) if number in pending]
            if window:
                out.append(window)
        return out

    def reopen_capped(self) -> int:
        """A new reading of a file whose last reading hit its cap: the pages left unread by the cap get a fresh budget."""
        reopened = [number for number, record in self.records.items() if record.get("capped")]
        for number in reopened:
            del self.records[number]
        self.meta.update(spent=0.0, capHit=False)
        return len(reopened)


# ------------------------------------------------------------------ progress (what the screen shows while the file is sorted)

def _kinds_so_far(records: dict[int, dict], count: int) -> dict[int, str]:
    """What each read page is so far, by the same classifiers the split uses (pages not yet read have none). Never raises."""
    try:
        from app.integrations.ocr.classification import classify_pages
        from app.integrations.ocr.kind_classifier import classify_kinds
        texts = [(records[n].get("text") or "") if n in records and records[n].get("status") == "read" else "" for n in range(1, count + 1)]
        identity = classify_pages(texts)
        content = classify_kinds(texts)
        found = {}
        for number, (verdict, guess) in enumerate(zip(identity, content), 1):
            if number not in records or records[number].get("status") != "read":
                continue
            kind = guess.kind if guess.kind and (guess.overrides_identity or verdict.document_type in ("unknown", "other")) else (
                verdict.document_type if verdict.document_type not in ("unknown", "other") else guess.kind)
            if kind:
                found[number] = kind
        return found
    except Exception:
        return {}


def _text_hash(text: str) -> str:
    return hashlib.sha256(" ".join((text or "").lower().split()).encode()).hexdigest()[:24]


def progress_of(ledger: Ledger, limits: Limits, *, step: str, reading: list[int] | None = None, seconds: float | None = None,
                hashes: bool = False) -> dict:
    """The small progress document published with every save (`result["sorting"]` of the job): honest counts and one line per page."""
    kinds = _kinds_so_far(ledger.records, ledger.count)
    pages = []
    for number in range(1, ledger.count + 1):
        record = ledger.records.get(number)
        if record is None:
            pages.append({"page": number, "state": "reading" if reading and number in reading else "waiting"})
            continue
        left = not is_final(record, limits)
        code, reason = explain(record, attempts_left=left)
        entry = {"page": number, "state": "retry" if left else record["status"], "seconds": record.get("seconds")}
        if kinds.get(number):
            entry["kind"] = kinds[number]
        if record.get("turned"):
            entry["turned"] = record["turned"]
        if hashes and record.get("status") == "read":
            entry["textHash"] = _text_hash(record.get("text") or "")
        if reason:
            entry.update(reason=reason, code=code)
        pages.append(entry)
    read = sum(1 for page in pages if page["state"] in ("read", "blank", "unread"))
    return {"pageCount": ledger.count, "pagesRead": read, "pagesSorted": sum(1 for page in pages if page.get("kind")),
            "step": step, "seconds": round(ledger.meta.get("spent", 0.0) if seconds is None else seconds, 1),
            "startedAt": ledger.meta.get("startedAt"), "passes": ledger.meta.get("passes", 0),
            **({"reading": list(reading)} if reading else {}), "pages": pages}


def final_summary(progress: dict, report: dict | None, kinds: dict | None) -> dict:
    """The finished progress: each page's field (from the reading's own result) and the pages that could not be placed, each with a
    plain reason. `report` is the inspection report (`Inspection.to_dict()`), `kinds` the pages of every other kind found
    (`document_splitting.kinds_found`)."""
    placed: dict[int, str] = {}
    for kind in ("resume", "passport", "aadhaar"):
        for number in (report or {}).get(kind + "_pages") or []:
            placed.setdefault(number, kind)
    for kind, numbers in sorted((kinds or {}).items(), key=lambda item: min(item[1]) if item[1] else 0):
        if kind == "other":
            continue                       # the pages nobody could place are filed there, but they are not "placed"
        for number in numbers:
            placed.setdefault(number, kind)
    pages, unplaced = [], []
    for page in progress.get("pages", []):
        entry = dict(page)
        number = entry["page"]
        if number in placed:
            entry["kind"] = placed[number]
            entry.pop("reason", None), entry.pop("code", None)
        else:
            entry.pop("kind", None)
            if entry.get("state") == "blank":
                pass                       # a blank or photo page stays beside the document it follows
            else:
                if not entry.get("reason"):
                    entry.update(reason=REASONS["unknown"], code="unknown")
                unplaced.append({"page": number, "reason": entry["reason"], "code": entry["code"]})
        pages.append(entry)
    return {**{key: value for key, value in progress.items() if key not in ("pages", "reading")}, "step": "done", "pages": pages,
            "pagesSorted": sum(1 for page in pages if page.get("kind")), "unplaced": unplaced}


# ------------------------------------------------------------------ the store: where the pages are saved (the job's state)

class PageStore(Protocol):
    name: str

    def claim(self, pages: list[int], attempts: dict[int, int], progress: dict) -> str:
        """"ok" (these pages are now being read by this pass), "done" (a page of them was read since: its saved number of tries is not
        `attempts`, so another pass did it) or "lost" (the job is no longer this claim's)."""

    def save(self, records: list[dict], meta: dict, progress: dict, *, claimed: list[int]) -> bool:
        """Save finished pages, the file's meta and the progress; False when the job is no longer this claim's."""

    def publish(self, progress: dict) -> bool: ...


class MemoryStore:
    """A store that keeps everything in a dict (tests, and a sort that is not tied to a job)."""

    def __init__(self, state: dict | None = None, name: str = "memory"):
        self.state = state if state is not None else {}
        self.name = name
        self.progress: dict = {}
        self.lost = False
        self.lock = threading.Lock()

    def claim(self, pages, attempts, progress):
        with self.lock:
            if self.lost:
                return "lost"
            if any(int((self.state.get(page_key(number)) or {}).get("attempts") or 0) != attempts.get(number, 0) for number in pages):
                return "done"
            self.state[unit_key(pages[0])] = {"by": self.name, "at": time.time()}
            self.progress = progress
            return "ok"

    def save(self, records, meta, progress, *, claimed):
        with self.lock:
            if self.lost:
                return False
            for record in records:
                self.state[page_key(record["page"])] = record
            self.state[META_KEY] = meta
            for number in claimed:
                self.state.pop(unit_key(number), None)
            self.progress = progress
            return True

    def publish(self, progress):
        self.progress = progress
        return not self.lost


class JobPageStore:
    """The job's own state, written with fenced statements (`claimed_by = fence`): a worker whose lease moved to another one writes
    nothing. One statement per unit claimed and one per unit finished; each also renews the lease and publishes the progress."""

    def __init__(self, sessions, job_id: str, fence: str | None, settings=None, *, name: str | None = None):
        self.sessions, self.job_id, self.fence, self.settings = sessions, job_id, fence, settings
        self.name = name or fence or "direct"

    def _lease(self, stamp):
        return stamp + timedelta(seconds=getattr(self.settings, "extraction_lease_seconds", 180) or 180)

    def _run(self, statement: str, params: dict):
        from sqlalchemy import text
        from app.services.manual_extraction import _autocommit, with_db_retry

        def go():
            from app.services.manual_extraction import now
            stamp = now()
            with self.sessions() as db:
                _autocommit(db)
                return db.execute(text(statement), {"id": self.job_id, "fence": self.fence, "stamp": stamp, "lease": self._lease(stamp), **params}).first()
        return with_db_retry(go)

    _WHERE = ("WHERE id = :id AND (CAST(:fence AS TEXT) IS NULL OR claimed_by = :fence) AND status = 'processing'")
    _SET = ("updated_at = :stamp, lease_until = CASE WHEN claimed_by IS NOT NULL THEN :lease ELSE lease_until END, "
            "result = (CASE WHEN jsonb_typeof(result) = 'object' THEN result ELSE CAST('{}' AS JSONB) END) "
            "|| jsonb_build_object('sorting', CAST(:progress AS JSONB))")

    def claim(self, pages, attempts, progress):
        keys = [page_key(number) for number in pages]
        same = " AND ".join(f"COALESCE(CAST(state -> :k{i} ->> 'attempts' AS INTEGER), 0) = :a{i}" for i in range(len(pages)))
        marks = {**{f"k{i}": key for i, key in enumerate(keys)}, **{f"a{i}": int(attempts.get(number, 0)) for i, number in enumerate(pages)}}
        row = self._run(
            f"UPDATE document_extraction_jobs SET state = state || CAST(:claim AS JSONB), {self._SET} {self._WHERE} AND {same} RETURNING 1",
            {"claim": json.dumps({unit_key(pages[0]): {"by": self.name, "at": round(time.time(), 1)}}), **marks,
             "progress": json.dumps(progress, default=str)})
        if row is not None:
            return "ok"
        from sqlalchemy import text
        from app.services.manual_extraction import _autocommit, with_db_retry

        def look():
            with self.sessions() as db:
                _autocommit(db)
                return db.execute(text("SELECT status, claimed_by FROM document_extraction_jobs WHERE id = :id"), {"id": self.job_id}).first()
        found = with_db_retry(look)
        if found is None or found.status != "processing" or (self.fence is not None and found.claimed_by != self.fence):
            return "lost"
        return "done"

    def save(self, records, meta, progress, *, claimed):
        value = {page_key(record["page"]): record for record in records}
        value[META_KEY] = meta
        row = self._run(
            f"UPDATE document_extraction_jobs SET state = (state || CAST(:value AS JSONB)) - CAST(:claims AS TEXT[]), {self._SET} {self._WHERE} RETURNING 1",
            {"value": json.dumps(value, default=str), "claims": [unit_key(number) for number in claimed],
             "progress": json.dumps(progress, default=str)})
        return row is not None

    def publish(self, progress):
        row = self._run(f"UPDATE document_extraction_jobs SET {self._SET} {self._WHERE} RETURNING 1", {"progress": json.dumps(progress, default=str)})
        return row is not None


_STORE: contextvars.ContextVar["PageStore | None"] = contextvars.ContextVar("page_sort_store", default=None)


@contextlib.contextmanager
def use_store(store: "PageStore | None"):
    """Open around a reading job's run: the page sorting inside it saves its pages in this store (and the job is handed back between
    passes). Outside it (`current_store()` is None) the whole-file reading is used as before."""
    token = _STORE.set(store)
    try:
        yield store
    finally:
        _STORE.reset(token)


def current_store() -> "PageStore | None":
    return _STORE.get()


# ------------------------------------------------------------------ reading a unit

def _page_count(data: bytes) -> int:
    import pymupdf as fitz
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            return document.page_count
    except Exception:
        return 0


def is_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def applicable(data: bytes, limits: Limits) -> bool:
    """A PDF with enough pages, at least one of which needs OCR (a file with a text layer on every page is read in seconds, whole)."""
    if not is_pdf(data):
        return False
    count = _page_count(data)
    if count < limits.min_pages:
        return False
    from app.services.document_splitting import costs_ocr
    return costs_ocr(data)


class _Cutter:
    """Cuts units out of one file (PyMuPDF is not thread safe: one lock, opened once per pass)."""

    def __init__(self, data: bytes):
        import pymupdf as fitz
        self.lock = threading.Lock()
        self.source = fitz.open(stream=data, filetype="pdf")

    def cut(self, numbers: list[int]) -> bytes:
        import pymupdf as fitz
        with self.lock:
            with fitz.open() as subset:
                for number in numbers:
                    subset.insert_pdf(self.source, from_page=number - 1, to_page=number - 1)
                try:
                    return subset.tobytes(garbage=4, deflate=True, clean=True, no_new_id=True)
                except Exception:
                    return subset.tobytes(deflate=True)

    def close(self) -> None:
        with self.lock:
            self.source.close()


Reader = Callable[..., "list[tuple]"]


def inspect_unit(unit_pdf: bytes, numbers: list[int], *, limits: Limits, page_timeout: float, document_timeout: float) -> list[tuple]:
    """The default reader: `inspect_document` on the unit's own PDF. Returns [(InspectedPage numbered as in the file, its detail)]."""
    from app.integrations.ocr import inspection as ins
    token = None
    try:        # the unit's snapshot is not the file's: only the assembled reading is offered to `capture_inspections`
        token = ins._CAPTURE.set(None)
    except Exception:
        token = None
    try:
        inspected = ins.inspect_document(unit_pdf, tesseract_cmd=limits.tesseract_cmd, languages=limits.languages, max_pages=len(numbers),
                                         page_timeout=page_timeout, document_timeout=document_timeout, page_workers=1)
    finally:
        if token is not None:
            ins._CAPTURE.reset(token)
    details = {entry.get("page"): entry for entry in (inspected.completeness or {}).get("pages_detail") or [] if isinstance(entry, dict)}
    return [(dataclasses.replace(page, page_number=numbers[local - 1]), details.get(local)) for local, page in enumerate(inspected.pages, 1)]


def _read_unit(cutter: _Cutter, numbers: list[int], attempts: dict[int, int], limits: Limits, reader: Reader, worker: str | None,
               budget: float) -> list[dict]:
    """Read one unit; returns one record per page. Never raises: a unit that failed gives its pages the failure, to be tried again."""
    began = time.monotonic()
    tries = max(attempts.get(number, 0) for number in numbers)
    page_timeout = min(120.0, limits.page_timeout * (1 + 0.5 * tries))
    document_timeout = max(20.0, min(budget, 30.0 + 2.0 * page_timeout * len(numbers)))
    try:
        results = reader(cutter.cut(numbers), numbers, limits=limits, page_timeout=page_timeout, document_timeout=document_timeout)
        seconds = time.monotonic() - began
        records = []
        for page, detail in results:
            each = float((detail or {}).get("seconds") or seconds / max(1, len(numbers)))
            records.append(record_of(page, detail, seconds=each, attempts=attempts.get(page.page_number, 0) + 1, worker=worker))
        if sorted(record["page"] for record in records) != sorted(numbers):
            raise ValueError("a page was lost")
        return records
    except Exception as exc:
        log.warning("Reading pages %s failed (%s); they will be read again.", numbers, type(exc).__name__, exc_info=log.isEnabledFor(logging.DEBUG))
        from app.integrations.ocr.inspection import InspectedPage
        each = (time.monotonic() - began) / max(1, len(numbers))
        return [record_of(InspectedPage(number, "unread", "unknown", "failed", "", reason="We could not read this page."), None,
                          seconds=each, attempts=attempts.get(number, 0) + 1, worker=worker) for number in numbers]


# ------------------------------------------------------------------ putting the pages back together

def _ranges(values: list[int]) -> str:
    shown = ", ".join(str(value) for value in values[:20])
    return shown + (f" and {len(values) - 20} more" if len(values) > 20 else "")


def assemble(data: bytes, ledger: Ledger, limits: Limits, *, workers: int, source_format: str = "pdf"):
    """The `Inspection` of the whole file from its saved pages, as `inspect_document` ends it: the pages are classified together (a
    continuation page takes the kind of the page before it), warnings and the completeness report are made, and every page is
    accounted for exactly once. Pages not read (the cap, the page limit) are `unread` / `not_processed`, so the split says "cut short"."""
    from app.integrations.ocr import inspection as ins
    from app.integrations.ocr import validators
    from app.integrations.ocr.classification import classify_pages
    count = ledger.count
    pages = []
    for number in range(1, count + 1):
        record = ledger.records.get(number)
        if record is None:
            pages.append(ins.InspectedPage(number, "unread", "unknown", "not_processed", "", reason="Reading this document went over the time limit."))
        else:
            pages.append(page_from_record(record))
    classified = classify_pages([page.text if page.status == "read" else "" for page in pages])
    for page, verdict in zip(pages, classified):
        if page.status == "read":
            page.document_type = verdict.document_type
            page.also = list(verdict.also)
            page.inherited = verdict.inherited
            page.confidence = min(page.confidence, verdict.confidence)
            page.reason = verdict.reason
    unread = [page.page_number for page in pages if page.status == "unread"]
    unknown = [page.page_number for page in pages if page.status == "read" and page.document_type == "unknown"]
    inherited = [page.page_number for page in pages if page.status == "read" and page.inherited]
    warnings: list[str] = []
    if unread:
        warnings.append(f"Pages {_ranges(unread)} could not be read. Review them in the stored original.")
    if unknown:
        warnings.append(f"We could not tell what kind of pages {_ranges(unknown)} are. Check them in the saved original.")
    details = {number: record.get("detail") or {} for number, record in ledger.records.items() if record.get("detail")}
    deferred = [number for number, detail in details.items() if detail.get("second_engine_deferred")]
    if deferred:
        warnings.append(f"The additional OCR check was busy on pages {_ranges(sorted(deferred))}. "
                        "The available reading was kept; compare those pages with the original.")
    if inherited:
        warnings.append(f"Pages {_ranges(inherited)} have no identifying content and were assigned to the adjoining "
                        "document (for example the back of a card). Check them.")
    native = sum(page.status == "read" and page.method == "native_text" for page in pages)
    ocr = sum(page.status == "read" and page.method != "native_text" for page in pages)
    blank = sum(page.status == "blank" for page in pages)
    photo = [page.page_number for page in pages if page.method == "photo_no_text"]
    completeness = {
        "pages_total": count, "pages_read": native + ocr, "pages_native": native, "pages_ocr": ocr, "pages_blank": blank,
        "pages_unread": len(unread),
        "unread": [{"page": page.page_number, "reason": page.reason} for page in pages if page.status == "unread"],
        "unread_detail": [{"page": page.page_number, "reason": page.reason, "variants": (details.get(page.page_number) or {}).get("variants", [])}
                          for page in pages if page.status == "unread"],
        "pages_retried": sorted(number for number, record in ledger.records.items() if int(record.get("attempts") or 0) > 1),
        "chars": sum(len(page.text) for page in pages), "workers": workers, "seconds": round(float(ledger.meta.get("spent") or 0.0), 3),
        "pages_photo": len(photo), "photo": photo,
        "pages_detail": [{"page": number, **details[number]} for number in sorted(details)],
        "sorted_in_units": {"unitPages": limits.unit_pages, "passes": ledger.meta.get("passes", 0)},
    }
    try:
        patterns = validators.scan_pages({page.page_number: page.text for page in pages if page.status == "read"})
    except Exception:
        patterns = None
    if patterns is not None:
        completeness["patterns"] = patterns.flags()
    if len(pages) != count or completeness["pages_read"] + blank + len(unread) != count:
        raise ins.InspectionError("The page check lost track of a page. Please upload the document again.", retryable=True)
    result = ins.Inspection(pages, len(pages), source_format, warnings, data, completeness=completeness, patterns=patterns)
    try:
        ins._offer_to_capture(data, result)
    except Exception:
        pass
    return result


# ------------------------------------------------------------------ the pass

def _cap(ledger: Ledger, limits: Limits, numbers: list[int], reason: str) -> list[dict]:
    """Records for pages the file's budget did not reach: they are accounted for, as `not_processed`, and say why."""
    from app.integrations.ocr.inspection import InspectedPage
    out = []
    for number in numbers:
        previous = ledger.records.get(number)
        page = InspectedPage(number, "unread", "unknown", "not_processed", (previous or {}).get("text", ""), reason=reason)
        record = record_of(page, None, seconds=0.0, attempts=int((previous or {}).get("attempts") or 0))
        record["capped"] = True
        out.append(record)
    return out


def sort_file(data: bytes, state: dict, store: "PageStore", limits: Limits, *, reader: Reader | None = None, worker: str | None = None,
              clock: Callable[[], float] = time.monotonic):
    """Run one pass over the file. Returns the assembled `Inspection` when every page is accounted for; raises `SortContinues` when the
    pass is over but pages are left (the job goes back to the queue), `JobLost` when the claim moved to someone else.

    `state` is the job's state as it was loaded (the saved pages are read from it); `store` saves what this pass reads."""
    reader = reader or inspect_unit
    digest = hashlib.sha256(data).hexdigest()
    count = _page_count(data)
    if count < 1:
        from app.integrations.ocr.inspection import InspectionError
        raise InspectionError("We cannot open this PDF. Save it again as a PDF without a password and try again.")
    ledger = Ledger.load(state, digest, count)
    if ledger.meta.get("capHit"):
        ledger.reopen_capped()
    ledger.meta["passes"] = int(ledger.meta.get("passes", 0)) + 1
    spent_before = float(ledger.meta.get("spent") or 0.0)
    began = clock()
    lock = threading.Lock()

    def spent() -> float:
        return spent_before + (clock() - began)

    def commit(records: list[dict], claimed: list[int], step: str, reading: list[int] | None = None) -> None:
        with lock:
            for record in records:
                ledger.records[record["page"]] = record
            ledger.meta["spent"] = round(spent(), 1)
            progress = progress_of(ledger, limits, step=step, reading=reading)
            if not store.save(records, dict(ledger.meta), progress, claimed=claimed):
                raise JobLost()

    # Pages beyond the page limit are accounted for at once.
    beyond = [number for number in range(limits.max_pages + 1, count + 1) if number not in ledger.records]
    if beyond:
        reason = f"This document has more than {limits.max_pages} pages, which is too many to read."
        from app.integrations.ocr.inspection import InspectedPage
        records = []
        for number in beyond:
            record = record_of(InspectedPage(number, "unread", "unknown", "not_processed", "", reason=reason), None, seconds=0.0, attempts=0)
            record["capped"] = True
            records.append(record)
        commit(records, [], "sorting")
    cutter = _Cutter(data)
    try:
        with ThreadPoolExecutor(limits.workers, thread_name_prefix="sort") as pool:
            running: dict = {}
            skipped_units: set[int] = set()
            while True:
                capped = spent() >= limits.total_seconds
                over_slice = (clock() - began) >= limits.slice_seconds
                if not capped and not over_slice:
                    for unit in ledger.units(limits):
                        if len(running) >= limits.workers:
                            break
                        if unit[0] in skipped_units or any(unit[0] in claimed for claimed in running.values()):
                            continue
                        attempts = {n: int((ledger.records.get(n) or {}).get("attempts") or 0) for n in unit}
                        claim = store.claim(unit, attempts, progress_of(ledger, limits, step="sorting", reading=unit))
                        if claim == "lost":
                            raise JobLost()
                        if claim == "done":            # another pass saved a page of it: never read twice; the next pass sees it
                            skipped_units.add(unit[0])
                            continue
                        budget = max(20.0, limits.total_seconds - spent())
                        future = pool.submit(contextvars.copy_context().run, _read_unit, cutter, unit, attempts, limits, reader, worker, budget)
                        running[future] = unit
                if not running:
                    break
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in done:
                    unit = running.pop(future)
                    commit(future.result(), [unit[0]], "sorting", reading=[n for other in running.values() for n in other] or None)
    finally:
        cutter.close()
    left = ledger.pending(limits)
    if left and spent() >= limits.total_seconds:
        reason = f"Reading this page ran out of time ({int(limits.total_seconds // 60)} minutes for the file)."
        ledger.meta["capHit"] = True
        commit(_cap(ledger, limits, left, reason), [], "sorting")
        left = []
    if left:
        with lock:
            ledger.meta["spent"] = round(spent(), 1)
            store.publish(progress_of(ledger, limits, step="waiting"))
        raise continues(f"Sorting pages: {count - len(left)} of {count} saved; the rest continue in the next turn.")
    ledger.meta["spent"] = round(spent(), 1)
    inspection = assemble(data, ledger, limits, workers=limits.workers)
    ledger.meta["summary"] = progress_of(ledger, limits, step="assembling", hashes=True)
    state[META_KEY] = dict(ledger.meta)
    for number in range(1, count + 1):          # the pages' texts live in the job only while the file is being sorted
        state.pop(page_key(number), None)
        state.pop(unit_key(number), None)
    return inspection


def inspect_resumable(data: bytes, settings, *, state: dict, worker: str | None = None, reader: Reader | None = None):
    """The reading job's local inspection of a long scan, page by page and resumable. None when this file is read as before (no job
    store in scope, not a PDF, short, or text only). The job's store comes from `use_store`."""
    store = current_store()
    if store is None:
        return None
    limits = limits_from(settings)
    if not applicable(data, limits):
        return None
    return sort_file(data, state, store, limits, reader=reader, worker=worker or getattr(store, "name", None))


def sort_plan(name: str, data: bytes, settings, *, state: dict, store: "PageStore", reader: Reader | None = None,
              document_timeout: float | None = None):
    """`document_splitting.sort_pages` for a stored file, resumable: (plan or None, complete). Raises `SortContinues` / `JobLost` like
    `sort_file`. A file with a text layer on every page (and not longer than the whole-file reader's page cap) is sorted whole, in
    seconds, with nothing saved."""
    from app.services import bulk_candidate_import as bulk
    from app.services import document_splitting as splitting
    limits = limits_from(settings)
    if reader is None and _page_count(data) <= bulk.SPLIT_MAX_PAGES and not splitting.costs_ocr(data):
        return splitting.sort_pages(name, data, document_timeout=document_timeout)
    inspected = sort_file(data, state, store, limits, reader=reader, worker=getattr(store, "name", None))
    result = bulk._split_inspected(inspected, data, [], name)
    complete = not (result.cut_short or result.failed)
    return splitting._plan_of(result, None), complete


def summary_from_inspection(inspection, settings=None, *, seconds: float = 0.0) -> dict | None:
    """The page-by-page summary (see `progress_of`) of a file that was read whole (`inspect_document`), so every file shows the same
    honest per-page reasons. `inspection` is the live `Inspection` (with page text, for the corrections' text hashes). Never raises."""
    try:
        limits = limits_from(settings)
        details = {entry.get("page"): entry for entry in (inspection.completeness or {}).get("pages_detail") or [] if isinstance(entry, dict)}
        ledger = Ledger("", inspection.page_count, {}, {"spent": seconds, "passes": 1})
        for page in inspection.pages:
            detail = details.get(page.page_number)
            record = record_of(page, detail, seconds=float((detail or {}).get("seconds") or 0.0), attempts=limits.page_attempts)
            ledger.records[page.page_number] = record
        return progress_of(ledger, limits, step="assembling", hashes=True)
    except Exception:
        return None


def strip(state: dict) -> dict:
    """The job's state without the saved pages (their texts): what is kept when the file's sorting is over."""
    for key in [key for key in state if key.startswith((PAGE_PREFIX, UNIT_PREFIX))]:
        state.pop(key, None)
    return state


def summary_of(state: dict) -> dict | None:
    meta = state.get(META_KEY) if isinstance(state, dict) else None
    summary = meta.get("summary") if isinstance(meta, dict) else None
    return summary if isinstance(summary, dict) else None


def text_hash_of(text: str) -> str:
    return _text_hash(text)
