"""
Veris OCR, through its job queue.

Submit selected document pages as a job and poll for a bounded time. Retries
use a stable idempotency key; a slow job never falls through to a second,
synchronous extraction endpoint.

A long document is not one huge job: its pages are cut into chunks (``plan_chunks``) that Veris reads side by
side, and the results are put back together by ``merge.merge_results``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Protocol

from veriis import FileDescriptor, VerisOCR
from veriis.errors import VerisOCRConnectionError, VerisOCRRateLimitError, VerisOCRServerError, VerisOCRTimeoutError
from veriis.models import AadhaarResponse, JobResponse, PassportResponse, ResumeResponse

DEADLINE_SECONDS = 150.0
POLL_SECONDS = 1.5
# Fast jobs finish in a second or two: look early, then back off to the steady rate.
POLL_SCHEDULE = (0.3, 0.6, 1.0, POLL_SECONDS)
# A job is given 60 s plus 3 s a page before it is called timed out (never more than ten minutes).
DEADLINE_BASE_SECONDS = 60.0
DEADLINE_PER_PAGE_SECONDS = 3.0
DEADLINE_CAP_SECONDS = 600.0
# 429 and 5xx are answered by trying the same request (same idempotency key) again after a pause.
RETRY_ATTEMPTS = 4
RETRY_BASE_SECONDS = 0.5
RETRY_CAP_SECONDS = 8.0

# A status request is harmless to repeat, so a dropped connection or a slow answer is also tried again.
READ_ERRORS = (VerisOCRConnectionError, VerisOCRTimeoutError)

# Replaceable in tests so timing can be checked without waiting.
_sleep: Callable[[float], None] = time.sleep
_clock: Callable[[], float] = time.monotonic


class ScanFailed(Exception):
    """Veris ran the job and could not read the file."""


class ScanTimedOut(Exception):
    """The job was still queued or running at the deadline."""


class _Jobs(Protocol):
    def get(self, job_id: str) -> JobResponse: ...


def poll_intervals(schedule: tuple[float, ...] = POLL_SCHEDULE) -> Iterator[float]:
    yield from schedule
    while True:
        yield schedule[-1]


def provider_deadline(pages: int) -> float:
    """Seconds a job of this many pages may stay queued or running before it counts as timed out."""
    return min(DEADLINE_CAP_SECONDS, DEADLINE_BASE_SECONDS + DEADLINE_PER_PAGE_SECONDS * max(0, pages))


def retrying(call: Callable[[], object], *, attempts: int = RETRY_ATTEMPTS, extra: tuple[type[Exception], ...] = ()):
    """``call()``, tried again with a growing pause after a 429 or 5xx (``extra`` adds error types, for harmless reads)."""
    transient = (VerisOCRRateLimitError, VerisOCRServerError, *extra)
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except transient as exc:
            if attempt == attempts:
                raise
            hinted = getattr(exc, "retry_after", None)
            pause = hinted if isinstance(hinted, (int, float)) and hinted >= 0 else RETRY_BASE_SECONDS * 2 ** (attempt - 1)
            _sleep(min(pause, RETRY_CAP_SECONDS))
    raise AssertionError("unreachable")


def settle(
    get: Callable[[], JobResponse], *, budget: float,
    sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None,
) -> JobResponse:
    """Poll now, then at 0.3, 0.6, 1.0, 1.5 ... seconds, until the job is no longer queued or running or ``budget``
    seconds have passed. The last status seen is returned either way."""
    sleep, clock = sleep or _sleep, clock or _clock
    give_up_at = clock() + max(0.0, budget)
    job = get()
    intervals = poll_intervals()
    while job.status in {"queued", "running"}:
        remaining = give_up_at - clock()
        if remaining <= 0:
            break
        sleep(min(next(intervals), remaining))
        job = get()
    return job


def wait_for_job(
    jobs: _Jobs,
    job_id: str,
    *,
    deadline: float = DEADLINE_SECONDS,
    interval: float | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> JobResponse:
    """Poll until the job settles. ``interval`` fixes the pause; left out, polling starts fast and eases off."""
    sleep, clock = sleep or _sleep, clock or _clock
    give_up_at = clock() + deadline
    intervals = poll_intervals() if interval is None else None
    while True:
        job = retrying(lambda: jobs.get(job_id), extra=READ_ERRORS)
        if job.status == "succeeded":
            return job
        if job.status == "failed":
            raise ScanFailed(job.error.message if job.error else "The document reader could not read this file.")
        remaining = give_up_at - clock()
        if remaining <= 0:
            raise ScanTimedOut(job_id)
        sleep(min(next(intervals) if intervals else interval, remaining))


def plan_chunks(pages: list[int], size: int, overlap: int = 0) -> list[list[int]]:
    """Cut ``pages`` into the fewest jobs of about ``size`` pages, as even as possible (100 pages at 20 give five
    jobs of 20, not four of 20 and one of 20 plus a stray page). With ``overlap`` the last pages of one chunk also
    open the next, so something that runs over a page break is whole in at least one chunk; a chunk is then at
    most ``size + overlap`` pages. Every page is in at least one chunk and chunks keep the page order."""
    count = len(pages)
    if size < 2 or count <= size:
        return [list(pages)]
    overlap = max(0, min(overlap, size - 1))
    parts = -(-count // size)
    base, extra = divmod(count, parts)
    chunks, start = [], 0
    for index in range(parts):
        end = start + base + (1 if index < extra else 0)
        chunks.append(list(pages[max(0, start - overlap):end]))
        start = end
    return chunks


_MODELS = {"resume": ResumeResponse, "passport": PassportResponse, "aadhaar": AadhaarResponse}


def _pdf_chunks(doc: FileDescriptor, size: int, overlap: int) -> list[bytes] | None:
    """The pages of a PDF cut into chunk PDFs, or None when it is one job's worth (or not a PDF)."""
    data = doc.data
    if not isinstance(data, (bytes, bytearray)) or not bytes(data[:5]) == b"%PDF-":
        return None
    try:
        import pymupdf as fitz
        with fitz.open(stream=bytes(data), filetype="pdf") as source:
            count = source.page_count
            plan = plan_chunks(list(range(count)), size, overlap)
            if len(plan) == 1:
                return None
            out = []
            for pages in plan:
                with fitz.open() as part:
                    for number in pages:
                        part.insert_pdf(source, from_page=number, to_page=number)
                    out.append(part.tobytes(garbage=4, deflate=True, no_new_id=True))
            return out
    except Exception:
        return None


def _one_job(kind, doc, *, base_url, api_key, idempotency_key) -> dict:
    # The SDK otherwise permits 120 seconds per request plus automatic retries,
    # which could exceed the polling budget by several minutes.
    with VerisOCR(base_url=base_url, api_key=api_key, timeout=30.0, max_retries=0) as client:
        accepted = retrying(lambda: client.jobs.submit(doc, mode=kind, idempotency_key=idempotency_key))
        job = wait_for_job(client.jobs, accepted.job_id)
    return job.result or {}


def scan(
    kind: Literal["resume", "passport", "aadhaar"],
    doc: FileDescriptor,
    *,
    base_url: str,
    api_key: str,
    idempotency_key: str | None = None,
    chunk_pages: int = 20,
    overlap: int = 1,
    parallel: int = 4,
) -> dict:
    """The job's ``result`` exactly as Veris returned it, checked against the SDK model
    (a malformed payload raises ValidationError) but never rebuilt from it.

    A PDF of more than ``chunk_pages`` pages is read as several jobs side by side and merged; the merged result
    numbers the pages 1..n of the whole file."""
    model = _MODELS[kind]
    parts = _pdf_chunks(doc, chunk_pages, overlap if kind == "resume" else 0)
    if parts is None:
        payload = _one_job(kind, doc, base_url=base_url, api_key=api_key, idempotency_key=idempotency_key)
        model.model_validate(payload)
        return payload

    from app.integrations.ocr.merge import merge_results
    import pymupdf as fitz
    with fitz.open(stream=bytes(doc.data), filetype="pdf") as source:
        plan = plan_chunks(list(range(1, source.page_count + 1)), chunk_pages, overlap if kind == "resume" else 0)

    def run(index: int) -> dict:
        key = f"{idempotency_key}:chunk{index}" if idempotency_key else None
        piece = FileDescriptor(parts[index], filename=f"{kind}-{index + 1}.pdf", content_type="application/pdf")
        return _one_job(kind, piece, base_url=base_url, api_key=api_key, idempotency_key=key)

    with ThreadPoolExecutor(max_workers=max(1, min(parallel, len(parts)))) as pool:
        results = list(pool.map(run, range(len(parts))))
    for result in results:
        model.model_validate(result)
    merged, _conflicts, _notes = merge_results(kind, [(plan[i], results[i]) for i in range(len(parts))])
    model.model_validate(merged)
    return merged
