"""Resumable document extraction with caller-owned durable checkpoints.

A provider job is polled for at most ocr_provider_wait_seconds per invocation. A worker can
release its slot on ExtractionPending and revisit the same provider job later.

Speed: the kinds of one document (résumé, passport, Aadhaar) are sent to Veris side by side, a long document is cut
into chunks of veris_chunk_pages pages that are read side by side too (at most veris_parallel_jobs at a time), and
the state is saved at checkpoints only: the keys before any paid work, the job ids once all are accepted, and the
results once all have settled. Nothing is saved on a poll.

Completeness: every chunk result is kept under its own idempotency key until all chunks are in, so one failed
chunk is retried alone; chunk results are merged without dropping or overwriting anything (integrations/ocr/merge.py);
and a coverage record states which pages were sent, which were not and why, and which Veris fields reached the
candidate and the form and which did not.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import queue
import threading
import time

from pydantic import ValidationError
from veriis import FileDescriptor, VerisOCR, VerisOCRError
from veriis.models import AadhaarResponse, PassportResponse, ResumeResponse

from app.integrations.ocr import client as ocr_client
from app.integrations.ocr.field_coverage import field_report, row_report
from app.integrations.ocr.classification import identity_hint
from app.integrations.ocr.inspection import InspectionError, inspect_document, restore_inspection
from app.integrations.ocr.mapping import aadhaar_fields, passport_fields, resume_fields
from app.integrations.ocr.merge import merge_results
from app.integrations.ocr.person_split import page_identity, incompatible


PIPELINE_VERSION = "local-pages-v1"
_MODELS = {"resume": ResumeResponse, "passport": PassportResponse, "aadhaar": AadhaarResponse}
_PENDING = {"queued", "running"}
# The wall clock a job's age is measured with (stored, so it survives a worker restart); replaceable in tests.
_wall: Callable[[], float] = time.time
# Local page text kept for pages Veris never sees, per page, so nothing a person might need is thrown away.
_HELD_TEXT_LIMIT = 50_000


class ExtractionPending(Exception):
    def __init__(self, job_id: str, kind: str):
        self.job_id = job_id
        self.kind = kind
        super().__init__(f"The {kind} extraction is still processing.")


class ExtractionFailure(Exception):
    """An actionable failure without provider URLs, credentials or document text."""


class UnreadableDocument(ExtractionFailure):
    """This file itself cannot be read (damaged, protected, unsupported): retrying the
    same bytes cannot help, unlike a provider outage or a missing server tool."""


def _unreadable(exc: InspectionError) -> ExtractionFailure:
    return ExtractionFailure(str(exc)) if exc.retryable else UnreadableDocument(str(exc))


def checked_person_output(data, output, state, settings):
    """Recheck legacy/cached results locally without discarding paid checkpoints."""
    if state.get("person_detection_version") != 1:
        try:
            inspected = inspect_document(data, tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng"),
                max_pages=getattr(settings, "ocr_max_pages", 300), page_timeout=getattr(settings, "ocr_page_timeout_seconds", 20),
                document_timeout=getattr(settings, "ocr_document_timeout_seconds", 600))
        except InspectionError as exc:
            raise _unreadable(exc) from None
        evidence = [page_identity(page.text) for page in inspected.pages if page.status == "read"]
        state["person_detection_version"] = 1
        state["person_identities"] = evidence
        state["splitSuggested"] = any(item["mixed"] for item in evidence) or any(
            incompatible(left, right) for index, left in enumerate(evidence) for right in evidence[index + 1:])
    if state.get("splitSuggested"):
        return {**output, "status": "needs_review", "splitSuggested": True, "extractions": {},
            "warnings": list(dict.fromkeys([*output.get("warnings", []), "Different candidate identities were found. Open Review people and pages before saving."]))}
    return output


def _persist(state: dict, save: Callable[[dict], None]) -> None:
    try:
        save(deepcopy(state))
    except Exception:
        raise ExtractionFailure("We could not save the progress of reading this document. Try again in a few minutes.") from None


def _request_key(source_hash: str, kind: str, selected: list[int], generation: int = 0) -> str:
    value = f"{PIPELINE_VERSION}:{kind}:{source_hash}:{','.join(map(str, selected))}"
    if generation:
        value += f":retry:{generation}"
    return "crm-" + hashlib.sha256(value.encode()).hexdigest()


def request_extraction_retry(state: dict, *, automatic: bool = False) -> list[str]:
    """Retain terminal failures before another budgeted or explicit attempt.

    The worker may call with ``automatic=True`` only when its durable retry
    budget has attempts remaining. A paid but incomplete/invalid result needs
    an explicit operator retry; pending or unknown jobs always retain their ID.
    A job that outlived its deadline (``timed_out``) is retried like a failed one.
    A document read as several chunks restarts only the chunks that failed: the others keep their paid results.
    """
    restarted = []
    for kind, record in list(state.get("provider_jobs", {}).items()):
        if record.get("chunks"):
            if _restart_chunks(state, kind, record, automatic):
                restarted.append(kind)
            continue
        failed = record.get("status") in {"failed", "timed_out"}
        invalid = record.get("status") == "succeeded" and (
            record.get("coverage_status") == "needs_review" or record.get("validation_status") == "failed"
        )
        if not (failed or (invalid and not automatic)):
            continue
        state.setdefault("provider_job_history", {}).setdefault(kind, []).append(deepcopy(record))
        generations = state.setdefault("provider_generations", {})
        generations[kind] = generations.get(kind, 0) + 1
        del state["provider_jobs"][kind]
        restarted.append(kind)
    return restarted


def _chunk_bad(chunk: dict, automatic: bool) -> bool:
    if chunk.get("status") in {"failed", "timed_out"}:
        return True
    return not automatic and (chunk.get("coverage_status") == "needs_review" or chunk.get("validation_status") == "failed")


def _restart_chunks(state: dict, kind: str, record: dict, automatic: bool) -> bool:
    chunks = record["chunks"]
    bad = [chunk for chunk in chunks if _chunk_bad(chunk, automatic)]
    merged_invalid = (
        record.get("status") == "succeeded" and not automatic
        and (record.get("coverage_status") == "needs_review" or record.get("validation_status") == "failed")
    )
    if not bad and merged_invalid:
        bad = list(chunks)  # a merged result that cannot be used: every part is read again
    if not bad:
        return False
    history = state.setdefault("provider_job_history", {}).setdefault(kind, [])
    for chunk in bad:
        history.append(deepcopy(chunk))
        generation = chunk.get("retry_generation", 0) + 1
        keep = {name: chunk[name] for name in ("index", "selected_pages", "positions") if name in chunk}
        chunk.clear()
        chunk.update(keep, retry_generation=generation, status="submitting")
    for name in ("result", "merge", "coverage_status", "validation_status", "error"):
        record.pop(name, None)
    record["status"] = "submitting"
    return True


# ---------------------------------------------------------------------------- provider jobs


def _mode(kind: str) -> str:
    """The Veris mode of a job key: "resume" for both "resume" and "resume+" (pages found after the first dispatch)."""
    return kind.rstrip("+")


def _setting(settings, name: str, default):
    value = getattr(settings, name, default)
    return default if value is None else value


def _new_record(kind: str, selected: list[int], source_hash: str, generation: int, settings) -> dict:
    """The durable intent for one kind: one provider job, or one per chunk of a long document."""
    overlap = _setting(settings, "veris_chunk_overlap_pages", 1) if _mode(kind) == "resume" else 0
    plan = ocr_client.plan_chunks(selected, _setting(settings, "veris_chunk_pages", 20), overlap)
    record = {
        "idempotency_key": _request_key(source_hash, kind, selected, generation), "selected_pages": list(selected),
        "status": "submitting", "retry_generation": generation,
    }
    if len(plan) > 1:
        position = {page: index for index, page in enumerate(selected, 1)}
        record["chunks"] = [{
            "index": index, "selected_pages": list(pages), "positions": [position[p] for p in pages],
            "idempotency_key": _request_key(source_hash, kind, pages, 0), "retry_generation": 0, "status": "submitting",
        } for index, pages in enumerate(plan)]
    return record


@dataclass(eq=False)
class _Unit:
    """One provider job: a whole kind, or one chunk of it."""
    kind: str
    record: dict           # the dict in `state` that holds this job's fields
    pages: list[int]
    key: str
    document: FileDescriptor | None = None
    outcome: str = ""      # "", "ok", "pending", "failed", "timed_out", "unusable", "error"
    label: str = ""


def _units_of(kind: str, record: dict, source_hash: str) -> list[_Unit]:
    chunks = record.get("chunks")
    if not chunks:
        return [_Unit(kind, record, record["selected_pages"], record["idempotency_key"])]
    units = []
    for chunk in chunks:
        if not chunk.get("idempotency_key"):
            chunk["idempotency_key"] = _request_key(source_hash, kind, chunk["selected_pages"], chunk.get("retry_generation", 0))
        units.append(_Unit(kind, chunk, chunk["selected_pages"], chunk["idempotency_key"],
                           label=f" (part {chunk['index'] + 1} of {len(chunks)})"))
    return units


def _failure_text(unit: _Unit) -> str:
    return f"We could not read the {_mode(unit.kind)} pages{unit.label}. Try again later, or enter the details by hand."


def _check_cached(kind: str, selected: list[int], record: dict, source_hash: str, generation: int) -> list[_Unit]:
    """The units of a kind that already has a record; raises for a record that cannot be continued."""
    if record.get("selected_pages") != selected:
        raise ExtractionFailure("The pages chosen for this document have changed. Check the saved results before trying again.")
    if not record.get("chunks") and record.get("idempotency_key") != _request_key(source_hash, kind, selected, generation):
        raise ExtractionFailure("The pages chosen for this document have changed. Check the saved results before trying again.")
    units = _units_of(kind, record, source_hash)
    if "result" in record:
        return []  # the whole kind (merged, for chunks) is already paid for
    for unit in units:
        rec = unit.record
        if rec.get("status") == "failed":
            raise ExtractionFailure(_failure_text(unit))
        if rec.get("status") == "timed_out":
            raise ExtractionFailure(
                f"Reading the {_mode(unit.kind)} pages{unit.label} took too long. It can be tried again.")
        if rec.get("status") == "succeeded" and "result" not in rec:
            raise ExtractionFailure("The saved result for this document has gone missing. Check the document, then try again.")
    return [unit for unit in units if "result" not in unit.record]


def _drive(unit: _Unit, settings, events: queue.Queue, abort: threading.Event) -> None:
    """Runs in a pool thread and only talks to Veris: submit (same key on every retry), then poll with the adaptive
    schedule. Everything that touches the saved state is done by the coordinating thread."""
    try:
        with VerisOCR(
            base_url=settings.veris_ocr_base_url, api_key=settings.veris_ocr_api_key,
            timeout=30.0, max_retries=0,
        ) as provider:
            job_id = unit.record.get("job_id")
            if not job_id:
                accepted = ocr_client.retrying(lambda: provider.jobs.submit(
                    unit.document, mode=_mode(unit.kind), idempotency_key=unit.key))
                ack = threading.Event()
                events.put(("submitted", unit, accepted, ack))
                ack.wait()
                if abort.is_set():
                    return
                job_id = accepted.job_id
            # Most files finish within seconds. Waiting here avoids a full release, a retry delay
            # and a worker wake-up for every status check; a slower job is still handed back.
            job = ocr_client.settle(
                lambda: ocr_client.retrying(lambda: provider.jobs.get(job_id), extra=ocr_client.READ_ERRORS),
                budget=max(0.0, _setting(settings, "ocr_provider_wait_seconds", 0.0)),
            )
        events.put(("job", unit, job, None))
    except (VerisOCRError, ValidationError):
        events.put(("error", unit, None, None))
    except BaseException as exc:  # an unexpected fault must reach the caller, not die in a thread
        events.put(("crash", unit, exc, None))


def _apply_job(unit: _Unit, job) -> bool:
    """Record what a polled job reported. Returns True when the saved state changed."""
    rec = unit.record
    before = (rec.get("status"), rec.get("error"), rec.get("validation_status"), "result" in rec)
    rec["status"] = job.status
    if job.status in _PENDING:
        unit.outcome = "pending"
        age = _wall() - rec.setdefault("submitted_at", _wall())
        if age > ocr_client.provider_deadline(len(unit.pages)):
            rec["status"], rec["error"] = "timed_out", "The document reader did not finish in time."
            unit.outcome = "timed_out"
    elif job.status == "failed":
        rec["error"] = "The document reader could not read this file."
        unit.outcome = "failed"
    elif job.status == "succeeded" and isinstance(job.result, dict):
        rec["result"] = deepcopy(job.result)
        unit.outcome = "ok"
    else:
        if job.status == "succeeded":
            rec["validation_status"] = "failed"
        unit.outcome = "unusable"
    return before != (rec.get("status"), rec.get("error"), rec.get("validation_status"), "result" in rec)


def _run_jobs(units: list[_Unit], *, state: dict, save, settings, on_kind_done=None) -> bool:
    """Drive every unit that still needs the provider, at most veris_parallel_jobs at once. The job ids are saved once
    all submissions in flight are accepted; the caller saves the results (the returned flag says they changed).
    ``on_kind_done(kind)`` is called as soon as every job of a kind has its result, while other kinds are still being
    read; when it reports a change and other jobs are still running, that kind's result is saved at once, so a
    partial result is visible as each kind completes."""
    if not units:
        return False
    left: dict[str, int] = {}
    for unit in units:
        left[unit.kind] = left.get(unit.kind, 0) + 1
    parallel = max(1, int(_setting(settings, "veris_parallel_jobs", 4)))
    events: queue.Queue = queue.Queue()
    abort = threading.Event()
    waiting: list[threading.Event] = []
    presubmit: set[int] = set()
    queued, active = deque(units), set()
    crash: BaseException | None = None
    dirty = False
    finished: list[str] = []

    def start() -> None:
        while queued and len(active) < parallel:
            unit = queued.popleft()
            active.add(id(unit))
            if not unit.record.get("job_id"):
                presubmit.add(id(unit))
            threading.Thread(target=_drive, args=(unit, settings, events, abort), daemon=True).start()

    try:
        start()
        while active:
            batch = [events.get()]
            while True:
                try:
                    batch.append(events.get_nowait())
                except queue.Empty:
                    break
            for name, unit, payload, ack in batch:
                if name == "submitted":
                    presubmit.discard(id(unit))
                    unit.record.update(job_id=payload.job_id, status=payload.status, submitted_at=_wall())
                    waiting.append(ack)
                    dirty = True
                    continue
                presubmit.discard(id(unit))
                active.discard(id(unit))
                if name == "job":
                    dirty = _apply_job(unit, payload) or dirty
                    if unit.outcome == "ok":
                        left[unit.kind] -= 1
                        if left[unit.kind] == 0:
                            finished.append(unit.kind)
                elif name == "error":
                    unit.outcome = "error"
                else:
                    unit.outcome, crash = "error", crash or payload
            if waiting and not presubmit:
                # The remote jobs are now recoverable even if the worker exits before its first status request.
                _persist(state, save)
                dirty = False
                for ack in waiting:
                    ack.set()
                waiting.clear()
            while finished:
                if on_kind_done is not None and on_kind_done(finished.pop(0)):
                    dirty = True
                    if active or queued:
                        _persist(state, save)
                        dirty = False
            start()
    except BaseException:
        abort.set()
        for ack in waiting:
            ack.set()
        raise
    if crash is not None:
        raise crash
    return dirty


def _chunk_covered(response, expected: int) -> bool:
    pages = response.pages
    return not (
        len(pages) != expected
        or {page.page_number for page in pages} != set(range(1, expected + 1))
        or any(not page.text.strip() for page in pages)
    )


def _merge_kind(kind: str, record: dict, state: dict, save) -> dict | None:
    """Merge a chunked kind once every chunk has its result. None when a chunk did not cover its pages."""
    chunks = record["chunks"]
    parts: list[tuple[list[int], dict]] = []
    covered = True
    for chunk in chunks:
        payload = chunk["result"]
        try:
            response = _MODELS[_mode(kind)].model_validate(payload)
        except ValidationError:
            chunk["validation_status"] = "failed"
            record["status"] = "succeeded"
            _persist(state, save)
            raise ExtractionFailure(
                f"The saved {kind} result could not be used. Check it before trying to read the document again."
            ) from None
        if _chunk_covered(response, len(chunk["selected_pages"])):
            chunk["coverage_status"] = "complete"
        else:
            chunk["coverage_status"] = "needs_review"
            covered = False
        parts.append((chunk["positions"], payload))
    record["status"] = "succeeded"
    if not covered:
        record["coverage_status"] = "needs_review"
        return None
    merged, conflicts, notes = merge_results(_mode(kind), parts)
    record["result"] = merged
    record["merge"] = {
        "chunks": len(chunks), "chunk_pages": [c["selected_pages"] for c in chunks],
        "request_ids": [p.get("request_id") for _, p in parts if p.get("request_id")],
        "conflicts": conflicts, "notes": notes,
    }
    for chunk in chunks:
        chunk.pop("result", None)  # the merged result holds it all; the chunk keeps only its job details
    return merged


_UNSET = object()


def _fetch_all(
    selected_by_kind: dict[str, list[int]], inspected, *, state: dict, save, settings, source_hash,
    unsaved: bool = False, ready=None,
) -> dict[str, dict | None]:
    """Provider result of every kind, with the kinds (and the chunks of a long document) sent side by side.

    ``inspected``/``source_hash`` may be per kind (``_Each``). A kind whose chunks did not cover their pages comes back
    as None. Raises ExtractionFailure for a failed job and ExtractionPending when a job is still running."""
    saved = not unsaved

    def checkpoint() -> None:
        nonlocal saved
        _persist(state, save)
        saved = True

    try:
        return _fetch(selected_by_kind, inspected, state=state, save=save, settings=settings,
                      source_hash=source_hash, checkpoint=checkpoint, ready=ready)
    finally:
        if not saved:
            try:
                checkpoint()  # the local inspection must outlive a failure that happens before the first save
            except ExtractionFailure:
                pass


def _fetch(selected_by_kind, inspected, *, state, save, settings, source_hash, checkpoint, ready=None) -> dict:
    jobs = state.setdefault("provider_jobs", {})
    generations = state.get("provider_generations", {})
    kinds = [kind for kind, selected in selected_by_kind.items() if selected]
    units: dict[str, list[_Unit]] = {}
    work: list[_Unit] = []
    fresh: dict[str, dict] = {}
    for kind in kinds:
        selected, digest = selected_by_kind[kind], _for(_mode(kind), source_hash)
        generation = generations.get(kind, 0)
        if kind in jobs:
            work.extend(_check_cached(kind, selected, jobs[kind], digest, generation))
        else:
            fresh[kind] = _new_record(kind, selected, digest, generation, settings)
            work.extend(_units_of(kind, fresh[kind], digest))
    if work and not (settings.veris_ocr_base_url and settings.veris_ocr_api_key):
        raise ExtractionFailure("Document reading is not set up on this server yet. Ask your system administrator to finish the setup.")
    if fresh:
        # Persist the keys before any paid work. Lost acceptance responses can
        # safely retry the same payload/key rather than create another job.
        jobs.update(fresh)
        checkpoint()
    for kind in kinds:
        digest = _for(_mode(kind), source_hash)
        units[kind] = _units_of(kind, jobs[kind], digest)
    work = [unit for kind in kinds for unit in units[kind] if "result" not in unit.record and "result" not in jobs[kind]]
    for unit in work:
        if not unit.record.get("job_id"):
            unit.document = FileDescriptor(
                _for(_mode(unit.kind), inspected).pdf_for_pages(unit.pages, allow_unresolved=True),
                filename=f"{_mode(unit.kind)}.pdf", content_type="application/pdf",
            )
    def kind_done(kind: str) -> bool:
        record = jobs[kind]
        if ready is None:
            return False
        if record.get("chunks") and "result" not in record:
            if not all("result" in chunk for chunk in record["chunks"]):
                return False
            try:
                payload = _merge_kind(kind, record, state, save)
            except ExtractionFailure:
                return False
        else:
            payload = record.get("result")
        return payload is not None and bool(ready(kind, deepcopy(payload)))

    # A kind whose jobs were all finished on an earlier pass (or earlier in this one) is shown at once too.
    in_work = {unit.kind for unit in work}
    early_done = [kind for kind in kinds if kind not in in_work and kind_done(kind)]
    dirty = _run_jobs(work, state=state, save=save, settings=settings, on_kind_done=kind_done) or bool(early_done)

    failure: str | None = None
    pending: tuple[str, str] | None = None
    for kind in kinds:
        for unit in units[kind]:
            if unit.outcome in {"failed", "timed_out", "error"} and failure is None:
                failure = {
                    "failed": _failure_text(unit),
                    "timed_out": f"Reading the {_mode(unit.kind)} pages{unit.label} took too long. It can be tried again.",
                    "error": "The document reader sent back an answer we could not use. Try again.",
                }[unit.outcome]
            elif unit.outcome == "unusable" and failure is None:
                failure = "The document reader did not find anything we could use. Check the document, then try again."
            elif unit.outcome == "pending" and pending is None:
                pending = (unit.record["job_id"], kind)
    if failure or pending:
        if dirty:
            checkpoint()  # the paid responses of the jobs that did finish are kept
        raise ExtractionFailure(failure) if failure else ExtractionPending(*pending)
    results: dict[str, dict | None] = {}
    for kind in kinds:
        record = jobs[kind]
        if record.get("chunks") and "result" not in record:
            results[kind] = _merge_kind(kind, record, state, save)
            dirty = True
        else:
            results[kind] = deepcopy(record["result"]) if "result" in record else None
    if dirty:
        # The paid responses (merged, for a long document) are saved before any mapping or candidate write.
        checkpoint()
    return results


class _Each(dict):
    """A value per kind (the pages a reviewer chose are separate files with their own hash and report)."""


def _for(kind: str, value):
    return value[_mode(kind)] if isinstance(value, _Each) else value


def _provider_result(
    kind: str, selected: list[int], inspected, *, state: dict,
    save: Callable[[dict], None], settings, source_hash: str,
) -> dict | None:
    """The provider result of one kind (see _fetch_all)."""
    return _fetch_all({kind: selected}, inspected, state=state, save=save, settings=settings, source_hash=source_hash)[kind]


# ---------------------------------------------------------------------------- coverage


def _page_notes(report: dict, sent: set[int]) -> list[dict]:
    """The pages of the file that no job was sent, with the reason."""
    out = []
    for page in report.get("pages") or []:
        number = page.get("page_number")
        if number in sent:
            continue
        status, kind = page.get("status"), page.get("document_type")
        if status == "unread":
            reason, blocking = page.get("reason") or "The page could not be read.", True
        elif status == "read" and kind == "unknown":
            reason, blocking = "The page was read but could not be identified as a résumé, passport or Aadhaar page.", True
        elif status == "blank":
            reason, blocking = "The page is blank.", False
        else:
            reason, blocking = "Not a résumé, passport or Aadhaar page; it stays in the stored original.", False
        out.append({"page": number, "reason": reason, "blocking": blocking})
    return out


def _ranges(pages: list[int]) -> str:
    """[1, 2, 3, 5] -> "1-3, 5"."""
    out, pages = [], sorted(set(pages))
    index = 0
    while index < len(pages):
        end = index
        while end + 1 < len(pages) and pages[end + 1] == pages[end] + 1:
            end += 1
        out.append(str(pages[index]) if end == index else f"{pages[index]}-{pages[end]}")
        index = end + 1
    return ", ".join(out)


_KIND_NAMES = {"resume": "resume", "passport": "passport", "aadhaar": "Aadhaar"}


def _local_patterns(kind: str, text: str) -> dict:
    """What the local reading of a kind's pages says (identifiers only, never the text), for cross-checking Veris.
    Provided by integrations/ocr/validators.py when it is installed; without it nothing is cross-checked."""
    try:
        from app.integrations.ocr import validators
        reader = getattr(validators, "local_patterns", None)
        return dict(reader(kind, text) or {}) if reader else {}
    except Exception:
        return {}


def _cross_check(kind: str, patterns: dict, payload: dict) -> dict:
    """{"verified": [field names the two readings agree on], "conflicts": [field names they disagree on]}.
    Values are never returned, so they cannot reach a log or a message."""
    if not patterns:
        return {}
    try:
        from app.integrations.ocr import validators
        found = validators.cross_check(patterns, payload)
    except Exception:
        return {}
    found = found if isinstance(found, dict) else {}
    names = lambda key: [str(item if not isinstance(item, dict) else item.get("field", "")) for item in found.get(key) or found.get(key.rstrip("s")) or []]
    return {"verified": names("agreements"), "conflicts": names("conflicts")}


def _coverage(report: dict, selected_by_kind: dict, extractions: dict, *, settings, chosen: bool = False,
              probes: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """The account of what was sent and what became of every Veris field, and the reasons (if any) to review."""
    sent = sorted({page for pages in selected_by_kind.values() for page in pages})
    not_sent = [] if chosen else _page_notes(report, set(sent))
    issues: list[str] = []
    notes: list[str] = []  # recorded with the coverage; they do not hold the extraction back
    shown: list[str] = []  # the notes worth a line in the result's warnings
    unmapped: list[dict] = []
    kinds: dict[str, dict] = {}
    unknown = 0
    for kind, extraction in extractions.items():
        part = extraction["coverage"]
        kinds[kind] = {"pages_sent": part["pages_sent"], "chunks": part["chunks"], "provider_pages": part["provider_pages"],
                       "fields": {name: part["fields"][name] for name in ("received", "mapped", "technical", "unknown")},
                       "left_out": part["rows"], "conflicts": len(part["conflicts"])}
        unmapped.extend({"kind": kind, **group} for group in part["fields"]["unmapped"])
        unknown += part["fields"]["unknown"]
        check = part.get("crossCheck") or {}
        if check:
            kinds[kind]["crossCheck"] = check
        if check.get("conflicts"):
            issues.append(
                f"The {_KIND_NAMES.get(kind, kind)} read locally and by the document reader disagree on "
                f"{', '.join(check['conflicts'])}. Compare them with the original before relying on either."
            )
        if part["conflicts"]:
            names = ", ".join(dict.fromkeys(c["field"] for c in part["conflicts"]))
            issues.append(f"Different parts of the {kind} gave different values for {names}. The first was kept; check them against the original.")
        for row in part["rows"]:
            issues.append(f"{row['left_out']} of {row['received']} {row['what']} in the {kind} do not fit the form. They are saved with the document; read them there.")
    strict = bool(_setting(settings, "veris_unmapped_needs_review", False))
    if unknown:
        text = ("The document reader returned details this app has not seen before. They are saved with the document "
                "and listed under Not on the form.")
        (issues if strict else notes).append(text)
        if not strict:
            shown.append(text)
    elif unmapped and strict:
        issues.append(f"{sum(g['count'] for g in unmapped)} details the document reader found have no place on the form. They are listed under Not on the form.")
    elif unmapped:
        notes.append("Some details the document reader found have no place on the form. They are listed under Not on the form.")
    probed = {kind: list(pages) for kind, pages in (probes or {}).items() if pages}
    ignored = [p["page"] for p in not_sent if not p["blocking"]]
    unresolved = [p["page"] for p in not_sent if p["blocking"]]
    read = [f"{_KIND_NAMES.get(kind, kind)} page{'s' if len(pages) > 1 else ''} {_ranges(pages)}"
            for kind, pages in selected_by_kind.items() if pages and kind in extractions]
    summary = "Read: " + (", ".join(read) if read else "nothing")
    if ignored:
        summary += f"; {len(ignored)} page{'s' if len(ignored) > 1 else ''} ignored ({_ranges(ignored)})"
    if unresolved:
        summary += f"; {len(unresolved)} page{'s' if len(unresolved) > 1 else ''} could not be identified ({_ranges(unresolved)})"
    summary += "."
    record = {
        "summary": summary,
        "pages": {"total": report.get("page_count"), "sent": sent, "not_sent": not_sent,
                  "ignored": ignored, "unresolved": unresolved,
                  # Pages the local check could not identify, sent to a mode as candidates; the reader decides.
                  "probed": probed},
        "kinds": kinds, "unmapped": unmapped, "issues": issues, "notes": notes,
        "complete": not issues and not any(p["blocking"] for p in not_sent),
    }
    return record, issues, shown


# ---------------------------------------------------------------------------- run


def _finish(state: dict, save: Callable[[dict], None], report: dict, extractions: dict,
            status: str, warnings: list[str], reason: str | None = None, coverage: dict | None = None) -> dict:
    result = {
        "inspection": report, "extractions": extractions,
        "status": status, "warnings": list(dict.fromkeys(warnings)),
        "splitSuggested": bool(state.get("splitSuggested")),
    }
    if reason:
        result["reason"] = reason
    if coverage is not None:
        result["coverage"] = coverage
        state["coverage"] = deepcopy(coverage)
    state.update(
        extractions=deepcopy(extractions), extraction_status=status,
        warnings=list(result["warnings"]),
    )
    _persist(state, save)
    return result


def _run_kinds(selected_by_kind, inspected, report, source_hash, *, state, save, settings, designations,
               unsaved: bool = False, finalize: bool = True, classified: dict | None = None):
    """Send each kind's pages to Veris (resuming checkpointed jobs), validate coverage and map
    the fields. Returns (extractions, warnings, every_kind_covered, reasons_to_review).

    With ``finalize`` the state is saved once at the end; a caller that saves right after (``_finish``) passes False."""
    extractions: dict = {}
    warnings: list[str] = []
    review: list[str] = []
    covered = True
    # Pages found after a kind was first sent (the reading went on while its first pages were already with Veris) are
    # sent as a late part ("resume+") and merged with the first part; pages already sent stay sent.
    parts, final = _split_parts(selected_by_kind, state)
    selected_by_kind.update(final)
    kinds = [kind for kind, selected in final.items() if selected]

    def ready(kind, payload) -> bool:
        """A kind whose jobs are all in: its fields are put in the saved state now, before the other kinds finish."""
        if kind.endswith("+") or kind + "+" in parts:
            return False
        try:
            response = _MODELS[kind].model_validate(payload)
        except ValidationError:
            return False
        if not _chunk_covered(response, len(selected_by_kind[kind])):
            return False
        try:
            fields, _notices = _map_fields(kind, response, designations)
        except Exception:
            return False  # the final pass maps it again, after the paid result is saved, and reports the fault
        state.setdefault("partial_extractions", {})[kind] = {
            "selected_pages": list((classified or {}).get(kind, selected_by_kind[kind])),
            "fields": fields.model_dump(by_alias=False, exclude_none=True, mode="json"),
        }
        return True

    payloads = _fetch_all(
        parts, inspected, state=state, save=save, settings=settings,
        source_hash=source_hash, unsaved=unsaved, ready=ready,
    )
    late_merge: dict[str, dict] = {}
    for kind in kinds:
        if kind + "+" not in parts:
            continue
        first, late = payloads.get(kind), payloads.get(kind + "+")
        if first is None or late is None:
            payloads[kind] = None
            continue
        place = {page: index for index, page in enumerate(final[kind], 1)}
        merged, conflicts, notes = merge_results(kind, [
            ([place[p] for p in parts[kind]], first), ([place[p] for p in parts[kind + "+"]], late)])
        payloads[kind] = merged
        late_merge[kind] = {"conflicts": conflicts, "notes": notes, "pages": parts[kind + "+"],
                            "request_ids": [p.get("request_id") for p in (first, late) if p.get("request_id")]}
    for kind in kinds:
        selected = selected_by_kind[kind]
        record = state["provider_jobs"][kind]
        payload = payloads[kind]
        if payload is None:
            covered = False
            warnings.append(f"The {kind} result did not cover every selected page. No fields from it were applied.")
            continue
        try:
            response = _MODELS[kind].model_validate(payload)
        except ValidationError:
            record["validation_status"] = "failed"
            _persist(state, save)
            raise ExtractionFailure(
                f"The saved {kind} result could not be used. Check it before trying to read the document again."
            ) from None
        if not _chunk_covered(response, len(selected)):
            record["coverage_status"] = "needs_review"
            covered = False
            warnings.append(f"The {kind} result did not cover every selected page. No fields from it were applied.")
            continue
        record["coverage_status"] = "complete"
        fields, notices = _map_fields(kind, response, designations)
        warnings.extend(notices)
        merge = deepcopy(record.get("merge")) if record.get("merge") else None
        if kind in late_merge:
            extra = late_merge[kind]
            merge = merge or {"chunks": 1, "chunk_pages": [parts[kind]], "request_ids": [], "conflicts": [], "notes": []}
            merge["chunks"] += 1
            merge["chunk_pages"] = [*merge["chunk_pages"], extra["pages"]]
            merge["request_ids"] = [*merge["request_ids"], *extra["request_ids"]]
            merge["conflicts"] = [*merge["conflicts"], *extra["conflicts"]]
            merge["notes"] = [*merge["notes"], *extra["notes"]]
        if merge:
            warnings.extend(merge["notes"])
        # The stored document is exactly what Veris returned: the checkpointed job
        # result (merged, for a long document), never the validated model. Provenance travels beside it, and
        # selected_pages[page_number - 1] maps a provider page to the original file.
        source_report = _for(kind, report)
        extractions[kind] = {
            "fields": fields.model_dump(by_alias=False, exclude_none=True, mode="json"),
            "raw": deepcopy(payload),
            "source": {
                "local_inspection": {**source_report, "selected_pages": list(selected)},
                "source_sha256": _for(kind, source_hash), "selected_pages": list(selected),
            },
            # The pages the local check identified; ``source`` names every page sent (candidates included).
            "selected_pages": list((classified or {}).get(kind, selected)),
            "coverage": {
                "pages_sent": list(selected), "provider_pages": len(response.pages),
                "chunks": (merge["chunks"] if merge else 1),
                "fields": field_report(kind, payload), "rows": row_report(kind, payload),
                "conflicts": list(merge["conflicts"]) if merge else [],
            },
        }
        if merge:
            extractions[kind]["merge"] = {k: merge[k] for k in ("chunks", "chunk_pages", "request_ids", "notes")}
    state.pop("partial_extractions", None)
    state["extractions"] = deepcopy(extractions)
    if finalize:
        _persist(state, save)
    return extractions, warnings, covered, review


def _split_parts(selected_by_kind: dict, state: dict) -> tuple[dict, dict]:
    """(jobs to run, pages per kind). A kind already sent with fewer pages gets a late part for the rest, and one
    already sent with pages the new plan does not list keeps them (a page once sent is never taken back)."""
    jobs = state.get("provider_jobs", {})
    parts: dict[str, list[int]] = {}
    final: dict[str, list[int]] = {}
    for kind, selected in selected_by_kind.items():
        if not selected:
            continue
        record = jobs.get(kind)
        sent = (record or {}).get("selected_pages")
        if sent and sent != selected:
            selected = sorted({*sent, *selected})
            parts[kind] = list(sent)
            late = [page for page in selected if page not in set(sent)]
            if late:
                parts[kind + "+"] = late
        else:
            parts[kind] = list(selected)
        final[kind] = list(selected)
    return parts, final


def _map_fields(kind: str, response, designations):
    if kind == "resume":
        return resume_fields(response, designations=designations or [])
    if kind == "passport":
        return passport_fields(response)
    return aadhaar_fields(response)


class _Chosen:
    """The pages a person already chose, as one PDF; a chunk of them is cut out of it."""

    def __init__(self, content: bytes):
        self.content = content

    def pdf_for_pages(self, selected, *, allow_unresolved=False):
        import pymupdf as fitz
        with fitz.open(stream=self.content, filetype="pdf") as source:
            if list(selected) == list(range(1, source.page_count + 1)):
                return self.content
            with fitz.open() as part:
                for number in sorted(set(selected)):
                    part.insert_pdf(source, from_page=number - 1, to_page=number - 1)
                return part.tobytes(garbage=4, deflate=True, no_new_id=True)


def extract_selected_pages(
    page_pdfs: dict[str, bytes], *, state: dict, save: Callable[[dict], None], settings,
    designations: list[str] | None = None,
) -> dict:
    """Extract pages a person already chose (kind -> PDF of exactly those pages), with the
    same provider jobs, idempotency keys, checkpoints and field mapping as a whole file.
    The kinds are sent side by side.
    """
    import pymupdf as fitz

    selected_by_kind: dict[str, list[int]] = {}
    sources, reports, hashes = _Each(), _Each(), _Each()
    for kind, content in page_pdfs.items():
        with fitz.open(stream=content, filetype="pdf") as document:
            count = document.page_count
        selected_by_kind[kind] = list(range(1, count + 1))
        sources[kind], hashes[kind] = _Chosen(content), hashlib.sha256(content).hexdigest()
        reports[kind] = {"source_format": "pdf", "page_count": count, "chosen_by_reviewer": True}
    extractions, warnings, covered, review = _run_kinds(
        selected_by_kind, sources, reports, hashes, state=state, save=save,
        settings=settings, designations=designations,
    )
    status = "complete" if covered else "needs_review"
    coverage = None
    if extractions:
        coverage, issues, shown = _coverage({"page_count": None, "pages": []}, selected_by_kind, extractions, settings=settings, chosen=True)
        warnings.extend([*issues, *shown])
        if issues:
            status = "needs_review"
    output = {"extractions": extractions, "status": status, "warnings": list(dict.fromkeys(warnings))}
    if coverage is not None:
        output["coverage"] = coverage
    return output


_PROBE_LIMIT = 40


def _early_selection(partial, provisional, again: list[int]) -> dict[str, list[int]]:
    """After the first quick look: the pages already settled for each kind (not waiting for a second look)."""
    waiting = set(again)
    out: dict[str, list[int]] = {}
    for kind in ("resume", "passport", "aadhaar"):
        pages = [
            page.page_number for page, verdict in zip(partial.pages, provisional)
            if page.status == "read" and page.page_number not in waiting and (verdict.document_type == kind or kind in verdict.also)
        ]
        if pages:
            out[kind] = pages
    return out


def _inspect_and_send_early(data: bytes, options: dict, *, state: dict, save, settings, source_hash: str, early: bool = True):
    """Inspect the document and, as soon as the quick first look has settled some pages, send those to Veris while the
    rest are still being read. Best effort: an early job that is slow or fails is met again, with the same keys, by the
    normal pass that follows the inspection (the idempotency keys are stored, so nothing is submitted twice)."""
    if not (getattr(settings, "veris_ocr_base_url", None) and getattr(settings, "veris_ocr_api_key", None)) \
            or not early or not _setting(settings, "veris_early_dispatch", True):
        return inspect_document(data, **options)
    events: queue.Queue = queue.Queue()

    def first_pass(partial, provisional, again) -> None:
        events.put(("early", (partial, _early_selection(partial, provisional, again))))

    def work() -> None:
        try:
            events.put(("done", inspect_document(data, **options, on_first_pass=first_pass)))
        except BaseException as exc:
            events.put(("error", exc))

    threading.Thread(target=work, daemon=True).start()
    while True:
        name, payload = events.get()
        if name == "early":
            partial, selection = payload
            if any(selection.values()):
                try:
                    _fetch_all(selection, partial, state=state, save=save, settings=settings, source_hash=source_hash)
                except (ExtractionPending, ExtractionFailure):
                    pass
        elif name == "done":
            return payload
        else:
            raise payload


def _probe_pages(inspected) -> dict[str, list[int]]:
    """Pages the local check could not read or identify that may still be a passport or an Aadhaar card.

    Veris is the authority on what a page is and answers with nothing for a page that is not one, so these are sent
    as candidates: to a kind that has no page yet, all of them; to a kind that has pages, the unresolved pages
    sitting right beside them (the rest of a passport or card that is read badly). Only a file with at least one
    identified page is probed, so a file of unrelated pages costs nothing."""
    if not inspected.has_classified_pages:
        return {}
    # A page left unread because of the page limit or the time limit was never looked at and is not sent; a page that
    # was read but is plainly something else (a cover letter) is not either.
    unresolved = sorted(
        page.page_number for page in inspected.pages
        if (page.status == "unread" and page.method != "not_processed")
        or (page.status == "read" and page.document_type == "unknown" and identity_hint(page.text))
    )
    if not unresolved:
        return {}
    runs, current = [], [unresolved[0]]
    for page in unresolved[1:]:
        if page == current[-1] + 1:
            current.append(page)
        else:
            runs.append(current)
            current = [page]
    runs.append(current)
    out: dict[str, list[int]] = {}
    for kind, known in (("passport", inspected.passport_pages), ("aadhaar", inspected.aadhaar_pages)):
        if not known:
            pages = list(unresolved)
        else:
            pages = [p for run in runs if (run[0] - 1 in known or run[-1] + 1 in known) for p in run]
        if pages:
            out[kind] = pages[:_PROBE_LIMIT]
    return out


def _found(kind: str, payload: dict) -> bool:
    """Whether a Veris result for candidate pages holds anything of the kind."""
    if kind == "passport":
        mrz = payload.get("mrz") if isinstance(payload.get("mrz"), dict) else {}
        return bool(mrz.get("passport_number") or mrz.get("surname") or mrz.get("given_names") or payload.get("fields"))
    identity = payload.get("aadhaar") if isinstance(payload.get("aadhaar"), dict) else {}
    return any(identity.get(name) for name in ("name", "aadhaar_number", "masked_aadhaar_number", "date_of_birth", "address", "vid"))


def _settle_probes(inspected, probes: dict, classified: dict, extractions: dict, warnings: list[str]) -> list[str]:
    """Decide what the candidate pages turned out to be. Returns the kinds whose candidate pages were promoted.

    A kind with no identified page and nothing found is dropped (the reader saw no passport / card there). A kind with
    nothing identified before and something found takes the candidate pages as its own. Where the kind already had pages,
    its candidates stay candidates that were read along with them."""
    promoted = []
    for kind, pages in probes.items():
        extraction = extractions.get(kind)
        if extraction is None or classified.get(kind):
            if extraction is not None and pages:
                warnings.append(f"Pages {', '.join(map(str, pages))} could not be identified and were read along with the {kind}. Check them in the stored original.")
            continue
        if not _found(kind, extraction["raw"]):
            del extractions[kind]
            warnings.append(f"Pages {', '.join(map(str, pages))} were checked as a possible {kind} and none was found.")
            continue
        for number in pages:
            page = inspected.pages[number - 1]
            page.status, page.document_type, page.inherited, page.confidence = "read", kind, True, 0.5
            page.reason = f"Confirmed as a {kind} by the document reader."
        extraction["selected_pages"] = list(pages)
        promoted.append(kind)
        warnings.append(f"Pages {', '.join(map(str, pages))} were identified as a {kind} by the document reader, not by the local check. Compare the details with the original.")
    return promoted


def extract_attachment(
    data: bytes, *, state: dict, save: Callable[[dict], None], settings,
    designations: list[str] | None = None, partial: bool = True, ignore_decorative: bool = False,
) -> dict:
    """Inspect every page and resume recorded résumé/passport/Aadhaar jobs.

    Every page is classified as résumé, passport, Aadhaar or other whatever the upload was called, and each kind found is
    sent on its own (a combined file is the normal case). ``partial`` (the default) extracts the confidently classified
    pages of a file that also has unread or unclassified ones (reported as needs_review, never dropped); with
    ``partial=False`` any such page holds the whole file back. ``ignore_decorative`` sets aside a single
    picture holding no document text (a logo or photo) instead of asking for review.
    """
    source_hash = hashlib.sha256(data).hexdigest()
    if state.get("source_sha256") not in {None, source_hash}:
        raise ExtractionFailure("The attachment changed. Start a separate intake for the replacement document.")
    same_source = state.get("source_sha256") == source_hash
    state["source_sha256"] = source_hash
    options = {
        "tesseract_cmd": settings.tesseract_cmd, "languages": settings.ocr_languages,
        "max_pages": settings.ocr_max_pages, "page_timeout": settings.ocr_page_timeout_seconds,
        "document_timeout": settings.ocr_document_timeout_seconds,
    }
    signature = hashlib.sha256(json.dumps(
        {"version": PIPELINE_VERSION, **options}, sort_keys=True,
    ).encode()).hexdigest()
    inspected = None
    if same_source and state.get("person_detection_version") == 1 and state.get("inspection_signature") == signature and isinstance(state.get("inspection"), dict):
        try:
            # Unread pages may be a time limit or a hiccup, so only a report whose gaps
            # are classification gaps is trusted in place of reading the file again.
            inspected = restore_inspection(
                data, state["inspection"], document_timeout=settings.ocr_document_timeout_seconds,
                allow_partial=partial and not state["inspection"].get("unread_pages"),
            )
        except InspectionError:
            # An incomplete or incompatible checkpoint is never authority to
            # skip pages. Inspect the original again before selecting anything.
            pass
    previous_text = (state.get("inspection") or {}).get("unsent_page_text") if same_source and isinstance(state.get("inspection"), dict) else None
    fresh_inspection = inspected is None
    try:
        if inspected is None:
            inspected = _inspect_and_send_early(
                data, options, state=state, save=save, settings=settings, source_hash=source_hash, early=partial)
            evidence = [page_identity(page.text) for page in inspected.pages if page.status == "read"]
            state["person_detection_version"] = 1
            state["person_identities"] = evidence
            state["splitSuggested"] = any(item["mixed"] for item in evidence) or any(
                incompatible(left, right) for index, left in enumerate(evidence) for right in evidence[index + 1:])
    except InspectionError as exc:
        raise _unreadable(exc) from None
    report = inspected.to_dict()
    # What the local reader saw on pages that Veris will not be sent (unreadable by Veris, or not identified) is kept
    # with the saved inspection, never thrown away, so a person can still read it. It is not part of the report
    # returned to callers.
    held = dict(previous_text or {})
    held.update({
        str(page.page_number): page.text[:_HELD_TEXT_LIMIT] for page in inspected.pages
        if (page.status == "unread" or (page.status == "read" and page.document_type == "unknown")) and (page.text or "").strip()
    })
    state["inspection"] = {**report, "unsent_page_text": held} if held else report
    state["inspection_signature"] = signature
    warnings = list(inspected.warnings)
    if ignore_decorative and inspected.decorative_image and not state.get("splitSuggested"):
        note = "The image holds no readable document text (a logo, signature or photo) and was set aside."
        return _finish(state, save, report, {}, "ignored", [], reason=note)
    inspected.allow_partial = partial
    if fresh_inspection:
        # Identifiers read locally from each kind's pages, kept (not the text) to cross-check what Veris returns later,
        # even on a visit that restores the inspection without its text.
        patterns = {
            kind: _local_patterns(kind, chr(10).join(inspected.pages[n - 1].text for n in pages))
            for kind, pages in (("resume", inspected.resume_pages), ("passport", inspected.passport_pages), ("aadhaar", inspected.aadhaar_pages))
            if pages
        }
        state["local_patterns"] = {kind: value for kind, value in patterns.items() if value}
    if state.get("splitSuggested"):
        warnings.append("This document contains different candidate identities. Open Review people and pages before saving any candidate.")
        return _finish(state, save, report, {}, "needs_review", warnings)
    if not inspected.complete and not (partial and inspected.has_classified_pages):
        warnings.append("Some pages are unread or unclassified. Review the document before extraction.")
        return _finish(state, save, report, {}, "needs_review", warnings)

    classified = {
        "resume": inspected.resume_pages, "passport": inspected.passport_pages,
        "aadhaar": inspected.aadhaar_pages,
    }
    if not partial:
        probes = {}
    elif fresh_inspection or "probe_plan" not in state:
        probes = _probe_pages(inspected)
        state["probe_plan"] = probes  # a restored inspection holds no text, so the plan is kept to stay the same on every visit
    else:
        probes = {kind: list(pages) for kind, pages in state["probe_plan"].items()}
    selected_by_kind = {kind: sorted({*pages, *probes.get(kind, [])}) for kind, pages in classified.items()}
    if not any(selected_by_kind.values()):
        warnings.append("No résumé, passport or Aadhaar pages were identified.")
        return _finish(state, save, report, {}, "ignored", warnings)
    status = "complete"
    if not inspected.complete:
        # Only the pages identified with confidence are sent; the rest stay in the stored original.
        gaps = sorted({*inspected.unread_pages, *inspected.unknown_pages} - {p for pages in probes.values() for p in pages})
        if gaps:
            warnings.append(
                f"Pages {', '.join(map(str, gaps[:20]))} could not be read or classified and were not extracted. "
                "Review them in the stored original."
            )
        status = "needs_review"
    # The first save of this run (the keys, before any paid work) also carries the inspection.
    extractions, notices, covered, _review = _run_kinds(
        selected_by_kind, inspected, report, source_hash, state=state, save=save,
        settings=settings, designations=designations, unsaved=fresh_inspection, finalize=False, classified=classified,
    )
    warnings.extend(notices)
    if not covered:
        status = "needs_review"
    for kind, extraction in extractions.items():
        check = _cross_check(kind, (state.get("local_patterns") or {}).get(kind) or {}, extraction["raw"])
        if check:
            extraction["coverage"]["crossCheck"] = check
    promoted = _settle_probes(inspected, probes, classified, extractions, warnings)
    if promoted:
        # Pages the document reader confirmed are now identified pages of the file.
        report = inspected.to_dict()
        state["inspection"] = {**report, "unsent_page_text": held} if held else report
        for kind in promoted:
            extractions[kind]["source"]["local_inspection"] = {**report, "selected_pages": list(selected_by_kind[kind])}
    coverage, issues, shown = _coverage(report, selected_by_kind, extractions, settings=settings, probes=probes)
    warnings.extend([*issues, *shown])
    if issues:
        status = "needs_review"
    return _finish(state, save, report, extractions, status, warnings, coverage=coverage)
