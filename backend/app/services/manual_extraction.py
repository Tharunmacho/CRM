"""Durable manual-document extraction, independent of candidate creation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import random
import socket
import time
import traceback
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import undefer

from app.db.models import Attachment, AuditEntry, Candidate, Designation, DocumentExtractionJob, Role
from app.integrations.ocr import reader_failures
from app.integrations.ocr.inspection import restore_inspection
from app.integrations.ocr.mapping import OcrFields
from app.services.document_extraction import extract_attachment, ExtractionFailure, ExtractionPending, provider_codes, request_extraction_retry
from app.services.document_storage import put_bytes, read_bytes
from app.services.reading_notes import plain_notes
from app.services.worker_heartbeat import waiting_reason

log = logging.getLogger(__name__)


def _plain(value):
    """JSON for values the standard encoder refuses (numpy numbers from an image library, sets, dates)."""
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _where(exc: BaseException) -> str:
    """module:function:line of the innermost frame in this application (never any text from the document)."""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "app" + os.sep in f.filename or "app/" in f.filename]
    frame = (ours or frames or [None])[-1]
    if frame is None:
        return "unknown"
    name = frame.filename.replace(os.sep, "/").rsplit("/app/", 1)[-1].removesuffix(".py").replace("/", ".")
    return f"{name}:{frame.name}:{frame.lineno}"


def failure_record(exc: BaseException, *, attempt: int, worker: str | None) -> dict:
    """A short, safe account of why an attempt failed, kept in the job's state: no message, no document content."""
    from app.services.worker_heartbeat import code_version
    return {"type": type(exc).__name__, "where": _where(exc), "at": now().isoformat(), "attempt": attempt,
            "worker": worker, "version": code_version()}


def reader_failure_view(reason: dict, *, attempt: int | None = None, retry_in: float | None = None, fallback: dict | None = None) -> dict:
    """What the screen and the diagnostics say about a reader failure: the machine fields and the sentence. Nothing here is
    document content (see integrations/ocr/reader_failures.py): the class, the HTTP status, the attempt, a safe detail."""
    from app.integrations.ocr import reader_failures
    view = {key: reason[key] for key in ("errorClass", "httpStatus", "retriable", "code", "detail") if reason.get(key) not in (None, "")}
    view.setdefault("errorClass", "internal_error")
    view.setdefault("retriable", False)
    view.setdefault("detail", "")
    view.update(attempt=attempt, maxAttempts=MAX_ATTEMPTS, at=now().isoformat(),
                message=reader_failures.message(reason, attempt=attempt, max_attempts=MAX_ATTEMPTS, retry_in=retry_in))
    if retry_in:
        view["retryAt"] = (now() + timedelta(seconds=retry_in)).isoformat()
    if fallback:
        view["localFallback"] = fallback
        view["message"] += (" The " + ", ".join(fallback["fields"]) + " from the passport's coded lines (read on this computer, source: "
                            + fallback["source"] + ") " + ("are" if len(fallback["fields"]) > 1 else "is") + " on the form. Check them.")
    return view


def _local_passport_fallback(state: dict) -> dict | None:
    """The passport values the local reading verified by their own check digits and put on the form (names only, no values)."""
    local = (state.get("local_patterns") or {}).get("passport") or {}
    if not state.get("local_filled"):
        return None
    fields = [label for flag, label in (("number_ok", "passport number"), ("birth_ok", "date of birth"), ("expiry_ok", "expiry date")) if local.get(flag)]
    return {"source": "local passport zone", "fields": fields} if fields else None


def _flag_local_fallback(db, job, fallback: dict, failure: dict) -> None:
    """The reader gave up but the passport's own coded lines filled the form: leave a trace and flag it for a look (never silently)."""
    from types import SimpleNamespace
    from app.db.models import AuditEntry
    from app.services.candidate_review import record_review_flag
    if not job.candidate_id:
        return
    name = db.scalar(select(Candidate.name).where(Candidate.id == job.candidate_id))
    if name is None:
        return
    who = SimpleNamespace(id=job.candidate_id, name=name)
    record_review_flag(db, who, ocr_warning=True, attachment_id=job.attachment_id, document_job_id=job.id)
    db.add(AuditEntry(actor_id=None, action="Kept passport details from the local passport zone", target=name, entity_type="candidate",
                      entity_id=job.candidate_id, meta={"source": fallback["source"], "fields": fallback["fields"],
                                                         "reader_error_class": failure["errorClass"], "document_job_id": job.id}))


READY = {"pending", "processing", "retry"}
# Lease renewed by each checkpoint; the worker pool also renews it by heartbeat (settings.extraction_lease_seconds).
LEASE_SECONDS = 180
TERMINAL = {"completed", "needs_review", "failed", "attached", "dismissed"}
# A document is read this many times before it is marked failed.
MAX_ATTEMPTS = 4


def error_hint(message):
    """The first sentence of a stored error, short and safe for a screen (those messages never carry document content)."""
    first = " ".join((message or "").split()).split(". ")[0].rstrip(".")
    return (first[:120] + ".") if first else None


def now():
    return datetime.now(timezone.utc)


class JobFinished(Exception):
    """An operator attached/dismissed the original while processing was in flight."""


def owned_job(db, identifier, owner_id, *, lock=False):
    statement = select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier, DocumentExtractionJob.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    job = db.scalar(statement)
    if job is None:
        raise HTTPException(404, "Document intake not found")
    return job


def job_out(job, *, detail=False, queue=None, reader=None):
    """``queue`` is ``extraction_queue.queue_positions(...)`` for the listed jobs: waiting jobs get their place in line.

    ``reader`` is ``worker_heartbeat.reader_status(db)`` (None when unknown): it is added as ``reader`` and decides
    ``waitingReason`` (``reader_not_running`` / ``queue`` / null) so the screen never waits silently on a dead reader.
    """
    place = (queue or {}).get(job.id) or {}
    result = {"id": job.id, "groupId": job.group_id, "kind": job.kind, "filename": job.filename,
              "sizeKb": job.size_kb, "status": job.status, "attempts": job.attempts,
              "lastError": job.last_error, "candidateId": job.candidate_id, "attachmentId": job.attachment_id,
              "createdAt": job.created_at, "updatedAt": job.updated_at,
              "queuedAt": job.queued_at or job.created_at, "startedAt": job.started_at, "finishedAt": job.finished_at,
              "queuePosition": place.get("queuePosition"), "aheadOfYou": place.get("aheadOfYou"),
              "originalUrl": "/api/ocr/jobs/" + job.id + "/original",
              "waitingReason": waiting_reason(job, reader),
              # For the upload screen: the attempt budget, a one-line reason, and when a waiting job is really due
              # (next_attempt_at holds a far-future value for stamped jobs, see migration 0032).
              "maxAttempts": MAX_ATTEMPTS, "lastErrorHint": error_hint(job.last_error),
              "readyAt": job.ready_at or (job.next_attempt_at if job.next_attempt_at and job.next_attempt_at.year < 2100 else None)}
    if reader is not None:
        result["reader"] = dict(reader)
    if detail:
        result.update(result=job.result or {}, draft=job.draft or {})
    return result


def _with_form_fields(kind, extraction):
    """Fields saved before the application form existed lack its values; read them from the stored raw result."""
    fields = dict(extraction.get("fields", {}))
    try:
        from app.services.form_extraction import values_for
        from app.integrations.ocr.mapping import _FORM_ONLY
        for name, value in values_for(kind, extraction.get("raw")).items():
            if name in _FORM_ONLY and name not in fields:
                fields[name] = value
    except Exception:  # the preview must never fail because of an optional extra
        pass
    return fields


def _scan_results(output):
    """What the person sees of a reading. The raw warnings stay in the job's state (diag_extraction reads them);
    here they are plain, and a warning about one kind appears on that kind's card only."""
    report = output.get("inspection", {})
    raw_warnings = output.get("warnings", [])
    shown_report = {**report, "warnings": plain_notes(report.get("warnings") or [])} if "warnings" in report else report
    scans = {}
    for kind in ("resume", "passport", "aadhaar"):
        extraction = output.get("extractions", {}).get(kind)
        selected = report.get(kind + "_pages", [])
        if not selected and kind != "resume":
            continue
        scans[kind] = {
            "fields": OcrFields.model_validate(_with_form_fields(kind, extraction)).model_dump(by_alias=True, exclude_none=True, mode="json") if extraction else {},
            "raw": extraction.get("raw", {}) if extraction else {},
            "source": extraction.get("source", {}) if extraction else {},
            "inspection": {**shown_report, "selected_pages": selected},
            "warnings": plain_notes(raw_warnings, kind=kind),
            "extraction_status": "complete" if extraction else "no_resume" if kind == "resume" and not selected and output["status"] != "needs_review" else "needs_review",
        }
    return {"scans": scans, "warnings": plain_notes(raw_warnings), "inspection": shown_report, "coverage": output.get("coverage")}


def _autocommit(db):
    """Run this session's statements outside a transaction: one round trip each, nothing to commit or roll back.
    Only for reads and for noise-only writes (checkpoints, leases), which announce nothing to the live feed."""
    db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})


def _lost(job, claimed_by):
    """True when a job no longer belongs to this claim (finished, released, or leased to another worker)."""
    return job is None or job.status not in READY or (claimed_by is not None and job.claimed_by != claimed_by)


def _hand_back(identifier, claimed_by, sessions, reason, delay=300):
    """Give a job back without counting an attempt (its file is not in this server's storage): another server takes it, or
    this one looks again later. It is never marked failed for this."""
    stamp = now()
    with sessions() as db:
        db.execute(text(
            "UPDATE document_extraction_jobs SET status = 'retry', next_attempt_at = :due, last_error = :reason, updated_at = :stamp, "
            "claimed_by = NULL, lease_until = NULL WHERE id = :id AND status = 'processing' "
            "AND (CAST(:fence AS TEXT) IS NULL OR claimed_by = :fence)"),
            {"due": stamp + timedelta(seconds=delay), "reason": reason, "stamp": stamp, "id": identifier, "fence": claimed_by})
        db.commit()
    log.warning("Document %s is not in this server's storage; handed back without an attempt.", identifier)


# The reading kind a form slot stands for (an education certificate is read as a resume, as application_form.JOB_KIND does).
_SLOT_KIND = {"resume": "resume", "passport": "passport", "aadhaar": "aadhaar", "education_certificate": "resume"}


def _expected_kind(draft) -> str | None:
    """The kind of document the person said they uploaded (the form slot of the file), or None for any other upload.
    It only lets the reader look at a scan the local check could not identify; it never decides what a page is."""
    form = draft.get("applicationForm") if isinstance(draft, dict) else None
    slot = form.get("slot") if isinstance(form, dict) else None
    return _SLOT_KIND.get(slot) if isinstance(slot, str) else None


# ------------------------------------------------------------------ timings of a reading, a dropped connection, parallel uploads

TIMING_RUNS = 6                  # attempts whose timings stay in a job's state
DB_RETRY_PAUSES = (1.0, 2.0, 4.0)
_pause = time.sleep              # replaced in tests


def _when(value):
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def begin_run(state: dict, queued_at, claimed_at: datetime) -> dict:
    """Open this attempt's record in ``state["timings"]`` and return it.

    Numbers and times only, never anything from a document. ``waitSeconds`` is how long the job waited before this attempt:
    since it was queued for the first attempt, since the previous attempt ended for a later one (that includes the pause the
    reader asked for). Only the last ``TIMING_RUNS`` attempts are kept."""
    before = state.get("timings") if isinstance(state.get("timings"), dict) else {}
    runs = [dict(item) for item in (before.get("runs") or []) if isinstance(item, dict)][-(TIMING_RUNS - 1):]
    since = _when(runs[-1].get("endedAt")) if runs else queued_at
    try:
        waited = (claimed_at - since).total_seconds() if isinstance(since, datetime) else None
    except TypeError:    # one side without a time zone: say nothing rather than guess
        waited = None
    run = {"claimedAt": claimed_at.isoformat(), "waitSeconds": round(max(0.0, waited), 1) if waited is not None else None,
           "afterEarlierRun": bool(runs)}
    state["timings"] = {"queuedAt": before.get("queuedAt") or (queued_at.isoformat() if isinstance(queued_at, datetime) else None),
                        "firstClaimedAt": before.get("firstClaimedAt") or run["claimedAt"], "runs": [*runs, run]}
    return run


def end_run(run: dict, seconds: float, outcome: str) -> None:
    run.update(totalSeconds=round(seconds, 1), outcome=outcome, endedAt=now().isoformat())


def record_read(record) -> bool:
    """Has this reader job (or every chunk of a long document) its result?"""
    if not isinstance(record, dict):
        return False
    chunks = record.get("chunks")
    return "result" in record or bool(chunks) and all(isinstance(chunk, dict) and "result" in chunk for chunk in chunks)


def noted_readers(run: dict, provider_jobs, done: set) -> None:
    """Note, once per kind, how long the reader took (from the job being sent to the save that holds its result)."""
    for kind, record in (provider_jobs or {}).items():
        if kind in done or not record_read(record):
            continue
        done.add(kind)
        sent = [part.get("submitted_at") for part in [record, *(record.get("chunks") or [])] if isinstance(part.get("submitted_at"), (int, float))]
        if sent:
            run.setdefault("readerSeconds", {})[str(kind)[:20]] = round(max(0.0, time.time() - min(sent)), 1)


def transient_database_error(exc: BaseException) -> bool:
    """A connection that dropped or could not be opened (a tunnel to the hosted database): worth trying again in a moment."""
    return isinstance(exc, OperationalError) or (isinstance(exc, DBAPIError) and bool(exc.connection_invalidated))


def with_db_retry(call, pauses=None):
    """``call()``, tried again after 1, 2 and 4 seconds when the database connection dropped. For idempotent statements only:
    a lost connection must cost a few seconds, not an attempt of the document (which waits 30 s or more before the next)."""
    for pause in (*(DB_RETRY_PAUSES if pauses is None else pauses), None):
        try:
            return call()
        except DBAPIError as exc:
            if pause is None or not transient_database_error(exc):
                raise
            log.warning("The database connection dropped (%s); trying again in %.0fs.", type(exc).__name__, pause)
            _pause(pause)


def object_missing(exc: BaseException) -> bool:
    """The object store says there is no such file (as opposed to being unreachable)."""
    code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")
    return code in {"NoSuchKey", "404", "NotFound"}


def put_all(settings, items: list[tuple[str, bytes]]) -> list[str]:
    """Store ``[(key, bytes)]`` as PDFs and return the object keys in the same order; on a remote store side by side
    (every upload is a trip over the internet). Each put is idempotent, so a failed one can simply be run again."""
    def one(item):
        return put_bytes(settings, item[0], item[1], "application/pdf")
    if str(getattr(settings, "storage_backend", "local")) == "s3" and len(items) > 1:
        with ThreadPoolExecutor(min(4, len(items)), thread_name_prefix="put") as pool:
            return list(pool.map(one, items))
    return [one(item) for item in items]


def process_job(identifier, *, sessions, settings, claimed_by=None):
    """Read one document. ``claimed_by`` is the worker that leased it (see services/extraction_queue.py); a lease that
    has moved to another worker makes this run stop writing. Without it (direct calls) the job is taken here."""
    titles = None
    with sessions() as db:
        if claimed_by is None:
            job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.draft))
                            .where(DocumentExtractionJob.id == identifier).with_for_update())
            if job is None or job.status not in READY:
                return
            job.status = "processing"
            job.updated_at = job.started_at = now()
            expected_kind = _expected_kind(job.draft)
            state = deepcopy(job.state or {})
            key, digest = job.object_key, job.content_hash
            owner, group_id, form_candidate = job.owner_id, job.group_id, job.candidate_id
            attempts = job.attempts
            queued_at = job.queued_at or job.created_at
            if not state.get("output"):
                titles = list(db.scalars(select(Designation.title)))
            db.commit()
        else:
            # The claim already moved the job to processing: one read gets everything this run needs.
            _autocommit(db)
            row = db.execute(select(DocumentExtractionJob.status, DocumentExtractionJob.claimed_by, DocumentExtractionJob.state,
                DocumentExtractionJob.object_key, DocumentExtractionJob.content_hash, DocumentExtractionJob.owner_id,
                DocumentExtractionJob.group_id, DocumentExtractionJob.candidate_id, DocumentExtractionJob.attempts,
                DocumentExtractionJob.draft, DocumentExtractionJob.queued_at, DocumentExtractionJob.created_at,
                select(func.array_agg(Designation.title)).scalar_subquery().label("titles"))
                .where(DocumentExtractionJob.id == identifier)).one_or_none()
            if row is None or row.status not in READY or row.claimed_by != claimed_by:
                return
            queued_at = row.queued_at or row.created_at
            state = deepcopy(row.state or {})
            key, digest, owner, group_id, form_candidate = row.object_key, row.content_hash, row.owner_id, row.group_id, row.candidate_id
            attempts = row.attempts
            titles = list(row.titles or [])
            expected_kind = _expected_kind(row.draft)

    # Which reader ran this attempt (a job that has no "reader" in its state was handled by older code).
    from app.services.worker_heartbeat import code_version
    from app.services.instance_guard import instance_name
    state["reader"] = {"version": code_version(), "worker": claimed_by, "host": socket.gethostname()[:40], "pid": os.getpid(),
                       "instance": instance_name(settings)}

    # What the row already holds, key by key: a checkpoint sends only the keys that changed (and nothing when none did),
    # so a 300-page inspection report is not uploaded again with every small step.
    saved_keys = {name: json.dumps(item, sort_keys=True, default=_plain) for name, item in state.items()}
    saved_keys.pop("reader", None)  # always written by the first checkpoint of this attempt
    saved_keys.pop("timings", None)  # the timings ride along with a write that happens anyway; they never cause one

    # Where this attempt's time goes (numbers and times only, see document_diagnostics): kept in the job's own state.
    run_began = time.monotonic()
    run = begin_run(state, queued_at, now())
    reader_done = {kind for kind, record in (state.get("provider_jobs") or {}).items() if record_read(record)}

    def checkpoint(value):
        # One statement: merge the changed keys into the saved state and renew the lease (a checkpoint proves the worker is alive).
        changed = {name: json.dumps(item, sort_keys=True, default=_plain) for name, item in value.items() if name != "timings"}
        changed = {name: text_ for name, text_ in changed.items() if saved_keys.get(name) != text_}
        if not changed:
            return
        run.setdefault("localReadSeconds", round(time.monotonic() - run_began - run.get("downloadSeconds", 0.0), 1))   # up to the first save
        noted_readers(run, value.get("provider_jobs"), reader_done)
        stamp = now()
        began = time.monotonic()
        payload = {**changed, "timings": json.dumps(state.get("timings"), sort_keys=True, default=_plain)}

        def write():
            with sessions() as db:
                _autocommit(db)
                return db.execute(text(
                    "UPDATE document_extraction_jobs SET state = state || CAST(:value AS JSONB), updated_at = :stamp, "
                    "lease_until = CASE WHEN claimed_by IS NOT NULL THEN :lease ELSE lease_until END "
                    "WHERE id = :id AND (CAST(:fence AS TEXT) IS NULL OR claimed_by = :fence) RETURNING status"),
                    {"value": "{" + ",".join(json.dumps(name) + ":" + body for name, body in payload.items()) + "}", "stamp": stamp, "lease": stamp + timedelta(seconds=getattr(settings, "extraction_lease_seconds", LEASE_SECONDS)), "id": identifier, "fence": claimed_by}).first()
        # A dropped connection costs a short pause and the same idempotent statement again, not an attempt of the document.
        row = with_db_retry(write)
        run["saveSeconds"] = round(run.get("saveSeconds", 0.0) + time.monotonic() - began, 2)
        run["saves"] = run.get("saves", 0) + 1
        # A reviewer can reserve the source while a provider acceptance is in flight. The returned ID/result
        # is kept, but the job is never reopened. A job leased to someone else is not ours to write.
        if row is None or row.status not in READY:
            raise JobFinished()
        saved_keys.update(changed)

    def save_and_fill(value):
        """A checkpoint; and the first time the local reading has verified something, the form is filled from it at once."""
        checkpoint(value)
        if form_candidate and value.get("local_patterns") and not value.get("local_filled"):
            try:
                from app.services.application_form import apply_local_patterns
                with sessions() as fill_db:
                    apply_local_patterns(fill_db, form_candidate, value["local_patterns"])
                value["local_filled"] = True
            except Exception:
                log.warning("Filling the form from the local reading of job %s failed; the reading goes on.", identifier, exc_info=True)
            checkpoint(value)

    from app.services.instance_guard import NOT_HERE, stored_here
    # A key in the shared object store is readable from every server: the existence check (one more trip over the internet)
    # is only for local files, which may live on another computer. A missing shared object shows when it is read, below.
    if not str(key).startswith("s3://") and not stored_here(settings, key):
        _hand_back(identifier, claimed_by, sessions, NOT_HERE)
        return
    try:
        try:
            fetched = time.monotonic()
            data = read_bytes(settings, key)
            run["downloadSeconds"] = round(time.monotonic() - fetched, 2)
        except FileNotFoundError:
            _hand_back(identifier, claimed_by, sessions, NOT_HERE)
            return
        except Exception as missing:
            if not object_missing(missing):
                raise
            _hand_back(identifier, claimed_by, sessions, NOT_HERE)
            return
        if hashlib.sha256(data).hexdigest() != digest:
            raise ExtractionFailure("The saved copy of this document looks damaged. Upload the original again, then try again.")
        output = state.get("output")
        if output is None:
            from app.services.extraction_scheduling import handoff_settings
            output = extract_attachment(data, state=state, save=save_and_fill, settings=handoff_settings(settings), designations=titles or [],
                                        final_attempt=attempts + 1 >= MAX_ATTEMPTS, expected_kind=expected_kind)
            state["output"] = deepcopy(output)
        from app.services.document_extraction import checked_person_output
        output = checked_person_output(data, output, state, settings)
        state["output"] = deepcopy(output)
        # The derived output is saved together with the stored documents below (one statement, not one per step): the paid
        # reader results themselves were already saved by the reading as they arrived, so a crash in between loses only the
        # cheap mapping, never a reader job.
        # Retain selected PDFs beside the original bundle; later attachment is a
        # short transaction and never needs to resubmit paid extraction work.
        documents = state.setdefault("documents", {})
        # A previous attempt may already have stored a typed PDF before later
        # review changed its classification or page set. Keep the old object and
        # metadata as history, but never attach it as the current document.
        current_extractions = output.get("extractions") or {}
        for kind, document in list(documents.items()):
            selected = (current_extractions.get(kind) or {}).get("selected_pages")
            if not selected or document.get("selected_pages") != selected:
                state.setdefault("document_history", {}).setdefault(kind, []).append(deepcopy(document))
                del documents[kind]
        if current_extractions:
            inspected = restore_inspection(data, output["inspection"], document_timeout=settings.ocr_document_timeout_seconds, allow_partial=True)
            cut = []
            for kind, extraction in output["extractions"].items():
                if kind in documents:
                    continue
                selected = extraction["selected_pages"]
                # The pages sent are the checked pages or fewer: a passport plan sends only the pages with passport details of their own.
                if not selected or not set(selected) <= set(getattr(inspected, kind + "_pages")):
                    raise ExtractionFailure("The selected pages no longer match what was checked earlier. Check the pages and try again.")
                content = inspected.pdf_for_pages(selected)
                cut.append((kind, selected, content, hashlib.sha256(content).hexdigest()))
            if form_candidate and "other" not in documents:
                # The pages of the file that are none of the three kinds (read but not recognised, or unreadable) are kept as ONE file
                # for the candidate's Other documents; blank pages are only listed. The whole file stays as the source evidence.
                from app.services.bulk_candidate_import import cut_pdf_pages
                from app.services.document_extraction import other_pages
                rest = other_pages(output["inspection"], current_extractions)
                if rest:
                    content = cut_pdf_pages(data, rest)
                    cut.append(("other", rest, content, hashlib.sha256(content).hexdigest()))
            # Classification can add pages to a previously read document. Storage is
            # immutable: retain its earlier PDF and publish the expanded bytes at a
            # distinct key. Repeating the same bytes reuses that key safely. The documents of one file go up side by side.
            stored_began = time.monotonic()
            keys = put_all(settings, [("manual/" + owner + "/" + identifier + "/" + kind + "-" + digest + ".pdf", content)
                                      for kind, _selected, content, digest in cut])
            for (kind, selected, content, digest), document_key in zip(cut, keys):
                documents[kind] = {"object_key": document_key, "file_name": kind + ".pdf", "content_type": "application/pdf",
                                   "size_kb": max(1, (len(content) + 1023) // 1024), "content_hash": digest, "selected_pages": list(selected)}
            if cut:
                run["storeSeconds"] = round(time.monotonic() - stored_began, 2)
        checkpoint(state)    # the output and every stored document in ONE statement (the final write below keeps them too)
        with sessions() as db:
            # A file read for a candidate's application form fills that candidate's form in this same transaction.
            # Candidate before job, the order every other writer uses.
            if form_candidate:
                db.scalar(select(Candidate.id).where(Candidate.id == form_candidate).with_for_update())
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if _lost(job, claimed_by):
                return
            end_run(run, time.monotonic() - run_began, "read")
            job.state = json.loads(json.dumps(state, default=_plain))
            job.result = json.loads(json.dumps(_scan_results(output), default=_plain))
            job.status = "completed" if output["status"] == "complete" else "needs_review"
            # A kind the reader could not read is named for the person (the upload row shows this line).
            from app.services.reading_notes import review_reasons
            job.last_error = None if job.status == "completed" else " ".join(review_reasons(output))[:500]   # never a bare "review"
            job.updated_at = job.finished_at = now()
            job.claimed_by = job.lease_until = None
            if (job.draft or {}).get("readerFailure"):   # an earlier attempt's failure no longer applies
                job.draft = {key: item for key, item in dict(job.draft).items() if key != "readerFailure"}
            lost_reason = next((part["reader_failure"] for record in (state.get("provider_jobs") or {}).values()
                                for part in [record, *(record.get("chunks") or [])]
                                if part.get("reader_failure") and "result" not in part), None)
            if lost_reason and job.status == "needs_review":   # some kind could not be read: say precisely why
                job.draft = {**dict(job.draft or {}), "readerFailure": reader_failure_view(lost_reason, attempt=attempts + 1)}
            if form_candidate:
                try:
                    from app.services.application_form import apply_job_output
                    with db.begin_nested():
                        apply_job_output(db, job, output)
                except Exception:
                    log.warning("Filling the application form from job %s failed; its saved result will be retried.", identifier, exc_info=True)
                    raise ExtractionFailure("The document was read, but its details could not be saved. The saved result will be tried again.") from None
            db.commit()
    except JobFinished:
        return
    except ExtractionPending as exc:
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier).with_for_update())
            if not _lost(job, claimed_by):
                job.status, job.next_attempt_at = "retry", now() + timedelta(seconds=getattr(exc, "retry_seconds", 2))
                end_run(run, time.monotonic() - run_began, "waiting for the reader")
                # The run's timings join the saved state inside this very UPDATE (a jsonb merge: nothing is read first).
                # (a Python dict, bound once as jsonb: a string would be stored as a jsonb string and turn the state into an array)
                job.state = DocumentExtractionJob.state.op("||")(cast(json.loads(json.dumps({"timings": state.get("timings")}, default=_plain)), JSONB))
                job.last_error = str(exc) if hasattr(exc, "retry_seconds") else "The document is still being read. Its progress is saved."
                if getattr(exc, "reason", None):   # a busy or slow reader is named on the document line, with a Details disclosure
                    job.draft = {**dict(job.draft or {}), "readerFailure": reader_failure_view(
                        exc.reason, attempt=job.attempts, retry_in=getattr(exc, "retry_seconds", None))}
                elif (job.draft or {}).get("readerFailure"):
                    job.draft = {key: item for key, item in dict(job.draft).items() if key != "readerFailure"}
                job.updated_at = now()
                job.claimed_by = job.lease_until = None
                db.commit()
    except Exception as exc:
        # The traceback stays in the server log (never in the job row or the response) so a file that keeps failing can be diagnosed.
        log.warning("Manual extraction %s failed (%s at %s; reader codes: %s); original and checkpoints retained.",
                    identifier, type(exc).__name__, _where(exc), provider_codes(state), exc_info=True)
        with sessions() as db:
            job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.draft))
                            .where(DocumentExtractionJob.id == identifier).with_for_update())
            if not _lost(job, claimed_by):
                job.attempts += 1
                # A reader failure that trying again cannot fix (the file was refused, our key is wrong) is not tried again.
                gives_up = job.attempts >= MAX_ATTEMPTS or bool(getattr(exc, "permanent", False))
                saved = deepcopy(job.state or {})
                if not gives_up:
                    request_extraction_retry(saved, automatic=True)
                # Why, for whoever has to look: the exception type and where it was raised, never the document.
                saved["failure"] = failure_record(exc, attempt=job.attempts, worker=claimed_by)
                saved["reader"] = state.get("reader")
                end_run(run, time.monotonic() - run_began, "failed")
                saved["timings"] = state.get("timings")
                job.status = "failed" if gives_up else "retry"
                # Exponential backoff (30 s, 1 min, 2 min) with jitter, so many files that failed together do not return together.
                delay = 30 * 2 ** (job.attempts - 1) * random.uniform(0.85, 1.25)
                reason = getattr(exc, "reason", None)
                fallback = _local_passport_fallback(saved)
                if reason:
                    failure = reader_failure_view(reason, attempt=job.attempts, retry_in=None if gives_up else delay, fallback=fallback)
                    saved["failure"].update(errorClass=failure["errorClass"], httpStatus=failure.get("httpStatus"),
                                            retriable=failure["retriable"], detail=failure["detail"])
                    job.draft = {**dict(job.draft or {}), "readerFailure": failure}
                    job.last_error = failure["message"][:500]
                    if gives_up and fallback:
                        _flag_local_fallback(db, job, fallback, failure)
                else:
                    job.last_error = str(exc)[:500] if isinstance(exc, ExtractionFailure) else (
                        f"Reading failed ({type(exc).__name__}). The technical details are in the server log. "
                        "The original file is safe. Try again, and contact your administrator if it keeps happening.")
                    if not isinstance(exc, ExtractionFailure):
                        failure = reader_failure_view(reader_failures.custom("internal_error", type(exc).__name__),
                                                      attempt=job.attempts, retry_in=None if gives_up else delay, fallback=fallback)
                        job.draft = {**dict(job.draft or {}), "readerFailure": failure}
                job.state = saved
                job.next_attempt_at = now() + timedelta(seconds=delay)
                job.updated_at = now()
                job.claimed_by = job.lease_until = None
                if job.status == "failed":
                    job.finished_at = job.updated_at
                db.commit()


def _plan_split_of_original(db, user, identifier, candidate_id, settings):
    """Before any lock is taken: does the saved scan hold documents beyond the ones the reader already cut out of it?

    Reading the pages is slow, so it never happens while the candidate (or the assignment cursor) is locked. Any refusal or
    failure here only means "no analysis": the attach below then raises whatever it always raised, or stores the files as before.
    """
    from app.core.deps import ensure_can_work_candidate
    from app.services import document_splitting
    job = candidate = None
    try:
        job = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == identifier, DocumentExtractionJob.owner_id == user.id))
        candidate = db.get(Candidate, candidate_id)
        if (job is None or candidate is None or job.candidate_id or job.status == "dismissed" or job.content_type != "application/pdf"
                or job.kind not in document_splitting.READ_SLOTS):
            return None
        ensure_can_work_candidate(candidate, user, db)
        data = read_bytes(settings, job.object_key)
        if hashlib.sha256(data).hexdigest() != job.content_hash:
            return None
        return document_splitting.plan_split(None, job.filename, data)
    except Exception:
        return None
    finally:
        for row in (candidate, job):   # the locked reads that follow must see the rows as they are then, not as they were before
            if row is not None:
                db.expire(row)


def attach_original(db, user, identifier, candidate_id, settings, *, original_only=False):
    from app.api.routers.candidates import lock_assignment_cursor
    from app.services.document_person_split import ensure_single_person_source
    from app.services import document_splitting
    split_plan = None if original_only else _plan_split_of_original(db, user, identifier, candidate_id, settings)
    lock_assignment_cursor(db)
    source_job = owned_job(db, identifier, user.id)
    ensure_single_person_source(db, "manual", source_job.group_id)
    # Candidate locks are shared with normal file uploads, guaranteeing one next
    # version even when another recruiter uploads to this candidate concurrently.
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    job = owned_job(db, identifier, user.id, lock=True)
    # Adding files is work on the candidate: the assigned recruiter, a manager or an admin. Having entered the
    # candidate, or an earlier merge, no longer counts once an admin has given it to someone else.
    from app.core.deps import ensure_can_work_candidate
    ensure_can_work_candidate(candidate, user, db)
    if job.candidate_id:
        if job.candidate_id != candidate.id:
            raise HTTPException(409, "These documents are already attached to another candidate")
        return job_out(job, detail=True)
    if job.status == "dismissed":
        raise HTTPException(409, "This upload was dismissed. Restore it before you attach it.")
    original = read_bytes(settings, job.object_key)
    if hashlib.sha256(original).hexdigest() != job.content_hash:
        raise HTTPException(409, "The saved file looks damaged. Upload it again.")
    documents = {"source_bundle": {"object_key": job.object_key, "file_name": job.filename,
                  "content_type": job.content_type, "size_kb": job.size_kb, "content_hash": job.content_hash},
                 **({} if original_only else deepcopy((job.state or {}).get("documents", {})))}
    if not original_only and job.kind in {"photo", "education", "experience"}:
        documents[job.kind] = deepcopy(documents["source_bundle"])
    attached = {}
    named: set[str] = set()
    for slot, document in documents.items():
        data = original if slot == "source_bundle" else read_bytes(settings, document["object_key"])
        if hashlib.sha256(data).hexdigest() != document["content_hash"]:
            raise HTTPException(409, "A selected file looks damaged. Upload it again.")
        previous = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate",
            Attachment.entity_id == candidate.id, Attachment.slot == slot).order_by(Attachment.version.desc()).limit(1))
        row = Attachment(id=str(uuid4()), entity_type="candidate", entity_id=candidate.id, slot=slot,
            version=(previous.version if previous else 0) + 1,
            # A file the reader derived is named like every other stored file ("<Candidate>-Resume.pdf"), not "resume.pdf".
            file_name=(document_splitting.derived_file_name(candidate, slot, named, job.filename)
                       if slot != "source_bundle" and document["file_name"] == slot + ".pdf" else document["file_name"]),
            content_type=document["content_type"], object_key=document["object_key"], content_hash=document["content_hash"], size_kb=document["size_kb"],
            uploaded_by=user.id, uploaded_at=now())
        db.add(row)
        attached[slot] = row.id
        db.add(AuditEntry(actor_id=user.id, action="Added a saved document to a candidate", target=row.file_name,
            entity_type="candidate", entity_id=candidate.id, meta={"attachment_id": row.id, "slot": slot,
            "version": row.version, "document_job_id": job.id, "original_only": original_only}))
    db.flush()
    # A scan that also holds other documents (a signed offer, a medical report ...) has those pages filed in their own fields
    # too; the fields the reader already filled from this scan are left as they are.
    filed = []
    if split_plan is not None:
        from app.services import document_splitting
        written: list[str] = []
        try:
            with db.begin_nested():
                filed = document_splitting.store_pieces(db, user, candidate, split_plan, job.filename, settings=settings, written=written,
                                                        skip_slots=set(documents), audit_extra={"document_job_id": job.id})
        except Exception:
            from app.services.bulk_candidate_import import discard_written
            discard_written(settings, written)
            filed = []
    job.attachment_id = attached["source_bundle"]
    job.candidate_id = candidate.id
    job.status = "attached"
    job.updated_at = now()
    job.state = {**deepcopy(job.state or {}), "attached_documents": attached, "attached_original_only": original_only}
    db.commit()
    answer = job_out(job, detail=True)
    if filed:
        from app.services import document_splitting
        answer["split"] = document_splitting.report(filed)   # the screen tells the person which documents went to which field
    return answer
