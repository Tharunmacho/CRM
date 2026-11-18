"""Per-candidate report on how each stored document was read, for a person to paste to the developer.

Everything here is an allow-list of safe facts: file names as stored, page numbers, statuses, attempt counts, error classes and
HTTP statuses, field names. No extracted value (name, number, date, address), no page text and no reader payload ever enters the
report, whatever the saved state holds.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app.db.models import Attachment, Candidate, DocumentExtractionJob, User
from app.integrations.ocr.reader_failures import safe_text
from app.services import application_form as form

_FAILURE_KEYS = ("errorClass", "httpStatus", "retriable", "code", "detail", "attempt", "maxAttempts", "at", "retryAt")


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _ints(values) -> list[int]:
    return [item for item in (values or []) if isinstance(item, int) and not isinstance(item, bool)]


def _safe_failure(failure) -> dict | None:
    if not isinstance(failure, dict):
        return None
    out = {key: failure[key] for key in _FAILURE_KEYS if key in failure}
    if "detail" in out:
        out["detail"] = safe_text(out["detail"], 200)
    fallback = failure.get("localFallback")
    if isinstance(fallback, dict):
        out["localFallback"] = {"source": safe_text(fallback.get("source"), 40),
                                "fields": [safe_text(name, 30) for name in fallback.get("fields") or []][:6]}
    return out


def _reader_jobs(state: dict) -> list[dict]:
    """One row per reader job (or chunk of a long document) in the saved state, plus the earlier attempts that were replaced."""
    rows = []

    def describe(kind: str, record: dict, part: str, earlier: bool, merged: bool = False) -> dict:
        failure = _safe_failure(record.get("reader_failure")) or {}
        provider = record.get("provider_error") if isinstance(record.get("provider_error"), dict) else {}
        row = {"kind": safe_text(kind, 20), "part": part, "earlierAttempt": earlier, "status": safe_text(record.get("status"), 20),
               "pagesSent": _ints(record.get("selected_pages")), "hasResult": "result" in record or merged,
               "readerJobId": safe_text(record.get("job_id"), 60) or None, "duplicateAtReader": bool(record.get("duplicate")),
               "validation": safe_text(record.get("validation_status"), 20) or None,
               "coverage": safe_text(record.get("coverage_status"), 20) or None,
               "retryGeneration": record.get("retry_generation") if isinstance(record.get("retry_generation"), int) else None}
        if provider:
            row["readerCode"] = safe_text(provider.get("code"), 60) or None
            row["httpStatus"] = provider.get("http_status") if isinstance(provider.get("http_status"), int) else None
        row.update({key: failure[key] for key in ("errorClass", "detail", "retriable") if key in failure})
        return row

    for kind, record in (state.get("provider_jobs") or {}).items():
        if not isinstance(record, dict):
            continue
        chunks = record.get("chunks")
        if chunks:
            # Once the parts are merged the parts' own copies are dropped and the merged answer sits on the kind: that is a result.
            merged = "result" in record
            rows.extend(describe(kind, chunk, f"{index} of {len(chunks)}", False, merged) for index, chunk in enumerate(chunks, 1))
        else:
            rows.append(describe(kind, record, "1 of 1", False))
    for kind, history in (state.get("provider_job_history") or {}).items():
        rows.extend(describe(kind, old, "earlier", True) for old in history or [] if isinstance(old, dict))
    return rows


_NUMBER_KEYS = ("waitSeconds", "downloadSeconds", "localReadSeconds", "saveSeconds", "storeSeconds", "totalSeconds")
_READER_KINDS = ("resume", "passport", "aadhaar")


def _seconds(value):
    return round(float(value), 1) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 10 ** 7 else None


def _timings(state: dict) -> dict | None:
    """Where the time of each attempt went, from the job's saved state: numbers, ISO times, the three reader kinds and a short
    outcome word. Anything else that might be in the state is dropped (an allow-list, like everything in this report)."""
    saved = state.get("timings")
    if not isinstance(saved, dict):
        return None
    runs = []
    for item in (saved.get("runs") or [])[-6:]:
        if not isinstance(item, dict):
            continue
        run = {key: _seconds(item.get(key)) for key in _NUMBER_KEYS if _seconds(item.get(key)) is not None}
        readers = item.get("readerSeconds") if isinstance(item.get("readerSeconds"), dict) else {}
        run["readerSeconds"] = {kind: _seconds(readers[kind]) for kind in _READER_KINDS if _seconds(readers.get(kind)) is not None}
        run.update(saves=item["saves"] if isinstance(item.get("saves"), int) and not isinstance(item.get("saves"), bool) else None,
                   afterEarlierRun=bool(item.get("afterEarlierRun")), outcome=safe_text(item.get("outcome"), 30) or None,
                   claimedAt=safe_text(item.get("claimedAt"), 40) or None, endedAt=safe_text(item.get("endedAt"), 40) or None)
        runs.append(run)
    return {"queuedAt": safe_text(saved.get("queuedAt"), 40) or None, "firstClaimedAt": safe_text(saved.get("firstClaimedAt"), 40) or None,
            "runs": runs} if runs else None


def _span(value) -> str:
    return "?" if value is None else (f"{value:.1f} s" if value < 10 else f"{value:.0f} s")


def timing_lines(timings: dict | None) -> list[str]:
    """One plain sentence per attempt: 'waited 3 s in the queue, read pages locally 12 s, reader 41 s (resume 38 s, passport 22 s),
    saved 2 s (6 saves), took 60 s in all'. No names or numbers of any document."""
    out = []
    runs = (timings or {}).get("runs") or []
    for number, run in enumerate(runs, 1):
        parts = [f"waited {_span(run.get('waitSeconds'))} " + ("before this attempt, after the previous one ended" if run.get("afterEarlierRun") else "in the queue")]
        if "downloadSeconds" in run:
            parts.append(f"fetched the file {_span(run['downloadSeconds'])}")
        if "localReadSeconds" in run:
            parts.append(f"read pages locally {_span(run['localReadSeconds'])}")
        readers = run.get("readerSeconds") or {}
        if readers:
            parts.append(f"reader {_span(max(readers.values()))} (" + ", ".join(f"{kind} {_span(seconds)}" for kind, seconds in readers.items()) + ")")
        if "saveSeconds" in run:
            parts.append(f"saved {_span(run['saveSeconds'])}" + (f" ({run['saves']} saves)" if run.get("saves") else ""))
        if "storeSeconds" in run:
            parts.append(f"stored the cut documents {_span(run['storeSeconds'])}")
        if "totalSeconds" in run:
            parts.append(f"took {_span(run['totalSeconds'])} in all" + (f" - {run['outcome']}" if run.get("outcome") else ""))
        out.append(f"timing, attempt {number} of {len(runs)}: " + ", ".join(parts))
    return out


def _identity(draft: dict) -> tuple[list[dict], dict | None]:
    """The identity reasons as field names and kinds only (the names and numbers compared are left out), and who owns a duplicate."""
    saved = ((draft or {}).get("applicationForm") or {}).get("result") or {}
    reasons = [{"field": safe_text(item.get("field"), 30), "documentKind": safe_text(item.get("documentKind"), 20),
                **({"matchedOn": [safe_text(label, 20) for label in item["matchedOn"]][:3]} if item.get("matchedOn") else {})}
               for item in saved.get("identityReasons") or [] if isinstance(item, dict)]
    other = saved.get("duplicateOf")
    duplicate = None
    if isinstance(other, dict):
        duplicate = {"candidateId": safe_text(other.get("candidateId"), 40) or None, "careId": safe_text(other.get("careId"), 40) or None}
    return reasons, duplicate


def _local_pages(state: dict) -> list[dict]:
    report = state.get("inspection") or (state.get("output") or {}).get("inspection") or {}
    pages = []
    for page in report.get("pages") or []:
        if isinstance(page, dict) and isinstance(page.get("page_number"), int):
            pages.append({"page": page["page_number"], "status": safe_text(page.get("status"), 20),
                          "kind": safe_text(page.get("document_type"), 20)})
    return pages


def _review_reasons(draft: dict) -> list[str]:
    """The plain reasons the reading went to review, as saved on the form result (what the screen lists). Wording only: the
    sentences name checks and fields, never a value."""
    saved = ((draft or {}).get("applicationForm") or {}).get("result") or {}
    if saved.get("status") != "needs_review" or saved.get("identityMismatch"):
        return []
    issues = (saved.get("coverage") or {}).get("issues") or [saved.get("message")]
    return [safe_text(item, 240) for item in issues if isinstance(item, str) and item.strip()][:8]


def _reading(job, state: dict) -> dict:
    draft = job.draft or {}
    reasons, duplicate = _identity(draft)
    failure = _safe_failure(draft.get("readerFailure"))
    recorded = state.get("failure") if isinstance(state.get("failure"), dict) else {}
    return {
        "jobId": job.id, "kind": job.kind, "status": job.status, "attempts": job.attempts, "maxAttempts": 4,
        "queuedAt": _iso(job.queued_at), "startedAt": _iso(job.started_at), "finishedAt": _iso(job.finished_at),
        "updatedAt": _iso(job.updated_at),
        "nextAttemptAt": _iso(job.ready_at or job.next_attempt_at) if job.status == "retry" else None,   # next_attempt_at holds 2100 on stamped rows
        "message": safe_text(job.last_error, 400) or None, "failure": failure,
        "lastFault": {"type": safe_text(recorded.get("type"), 60), "where": safe_text(recorded.get("where"), 80),
                      "at": safe_text(recorded.get("at"), 40), "attempt": recorded.get("attempt")} if recorded else None,
        "readerJobs": _reader_jobs(state), "localPages": _local_pages(state), "localFallback": (failure or {}).get("localFallback"),
        "identityReasons": reasons, "duplicateOf": duplicate, "reviewReasons": _review_reasons(draft),
        "readerVersion": safe_text((state.get("reader") or {}).get("version"), 20) or None,
        "timings": _timings(state),
    }


def build(db: Session, user: User, candidate_id: str) -> dict:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    form.ensure_can_edit(user, candidate)
    from app.services.document_identity import rejected_expression
    files = db.execute(select(Attachment.id, Attachment.slot, Attachment.version, Attachment.file_name, Attachment.size_kb,
                              Attachment.content_type, Attachment.uploaded_at, rejected_expression())
                       .where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id,
                              Attachment.slot.in_([slot for slot in form.STORED_SLOTS if slot != form.INVOICE_SLOT]))
                       .order_by(Attachment.slot, Attachment.uploaded_at)).all()
    splits = form.split_sources(db.scalars(form.split_sources_statement(candidate_id)))
    jobs = list(db.scalars(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.draft))
                           .where(DocumentExtractionJob.candidate_id == candidate_id).order_by(DocumentExtractionJob.created_at.desc())))
    used: set[str] = set()
    documents = []
    for row in files:
        job = next((item for item in jobs if item.attachment_id == row.id or row.id in
                    (((item.draft or {}).get("applicationForm") or {}).get("attachedDocuments") or {}).values()), None)
        if job is not None:
            used.add(job.id)
        split = splits.get(row.id)
        documents.append({
            "slot": row.slot, "attachmentId": row.id, "storedName": safe_text(row.file_name, 150), "version": row.version,
            "sizeKb": row.size_kb, "contentType": safe_text(row.content_type, 80), "uploadedAt": _iso(row.uploaded_at),
            "cutFrom": {"fileName": safe_text(split["fileName"], 150), "pages": _ints(split.get("pages"))} if split else None,
            "rejectedAsAnotherPerson": bool(row[-1]),
            "reading": _reading(job, dict(job.state or {})) if job is not None else None,
        })
    other = [_reading(job, dict(job.state or {})) for job in jobs if job.id not in used and job.status != "dismissed"]
    out = {"candidateId": candidate_id, "generatedAt": datetime.now(timezone.utc).isoformat(), "documents": documents, "otherReadings": other}
    out["report"] = as_text(out)
    return out


def as_text(report: dict) -> str:
    """The report as plain text, for the Copy report button (no values from any document)."""
    lines = [f"Document reading report - candidate {report['candidateId']} - {report['generatedAt']}"]
    for number, doc in enumerate(report["documents"], 1):
        lines.append("")
        cut = doc.get("cutFrom")
        lines.append(f"[{number}] {doc['slot']} - {doc['storedName']} (v{doc['version']}, {doc['sizeKb']} KB, {doc['contentType']})"
                     + (f" - cut from '{cut['fileName']}' pages {cut['pages']}" if cut else "")
                     + (" - REJECTED: belongs to another person" if doc["rejectedAsAnotherPerson"] else ""))
        reading = doc.get("reading")
        if not reading:
            lines.append("    no reading job")
            continue
        lines.extend("    " + text for text in _reading_lines(reading))
    for reading in report["otherReadings"]:
        lines.append("")
        lines.append("[unmatched reading job]")
        lines.extend("    " + text for text in _reading_lines(reading))
    return "\n".join(lines)


def _reading_lines(reading: dict) -> list[str]:
    out = [f"job {reading['jobId']} ({reading['kind']}): {reading['status']}, attempt {reading['attempts']} of {reading['maxAttempts']}"
           f", queued {reading['queuedAt']}, started {reading['startedAt']}, finished {reading['finishedAt']}"]
    if reading.get("nextAttemptAt"):
        out.append(f"next automatic attempt: {reading['nextAttemptAt']}")
    out.extend(timing_lines(reading.get("timings")))
    if reading.get("message"):
        out.append(f"shown to the user: {reading['message']}")
    failure = reading.get("failure")
    if failure:
        out.append("reader failure: " + ", ".join(f"{key}={failure[key]}" for key in ("errorClass", "httpStatus", "retriable", "code", "attempt", "detail") if key in failure))
    if reading.get("lastFault"):
        fault = reading["lastFault"]
        out.append(f"last fault: {fault['type']} at {fault['where']} (attempt {fault['attempt']}, {fault['at']})")
    for job in reading["readerJobs"]:
        out.append(f"reader {job['kind']} part {job['part']}{' (earlier attempt)' if job['earlierAttempt'] else ''}: status={job['status']}, "
                   f"pages={job['pagesSent']}, result={'yes' if job['hasResult'] else 'no'}"
                   + "".join(f", {key}={job[key]}" for key in ("errorClass", "httpStatus", "readerCode", "validation", "coverage", "detail") if job.get(key)))
    pages = reading.get("localPages") or []
    if pages:
        out.append("local page reading: " + "; ".join(f"p{page['page']}={page['kind'] or '?'}/{page['status']}" for page in pages[:80]))
    for reason in reading.get("reviewReasons") or []:
        out.append(f"needs review because: {reason}")
    if reading.get("identityReasons"):
        out.append("identity: " + "; ".join(f"{item['field']} ({item['documentKind']})" for item in reading["identityReasons"]))
    if reading.get("duplicateOf"):
        out.append(f"duplicate of: {reading['duplicateOf']}")
    if reading.get("localFallback"):
        out.append(f"kept from the local passport zone: {', '.join(reading['localFallback']['fields'])}")
    if reading.get("readerVersion"):
        out.append(f"read by version {reading['readerVersion']}")
    return out
