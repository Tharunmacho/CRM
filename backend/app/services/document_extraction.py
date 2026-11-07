"""Resumable document extraction with caller-owned durable checkpoints.

A provider job is polled for at most ocr_provider_wait_seconds (60 s) per invocation. A worker can
release its slot on ExtractionPending and revisit the same provider job later.

Order: the kinds of one document go to Veris one at a time (résumé, then passport, then Aadhaar; the late part of a
kind right after it; the chunks of a long document one after another) because veris_parallel_jobs defaults to 1;
a higher value sends that many side by side. A job that is still running holds the later ones back, and a job that
failed does not: the later kinds are still sent. Every step is logged (kind, pages, size, job id, outcome, time; never
page text). The state is saved at checkpoints only: the keys before any paid work, the job ids once accepted, and the
results as they settle. Nothing is saved on a poll.

Payload: before a job is sent its file is prepared locally (integrations/ocr/payload.py: rebuilt minimal, upright, bounded
pictures; blank and exactly repeated pages are left out and named in the coverage), and one logged line says what was sent. A
passport of several pages goes one page per job, the strongest first; once a page gave the number, birth date and name, a
further scan of the data page is not paid for. A job of several pages that is above the byte limit is split before it is sent,
and one the reader refuses as too heavy (a resource, limit or size code that cannot be retried) is sent again page by page
in the same pass; a job of one page that fails like that is terminal.

Degrading: when some kinds were read and another kind ended in a terminal failure (the reader said it cannot be
retried, the last automatic attempt was used, or its answer was unusable), the result carries what was read, is marked
needs_review and says which kind could not be read; the failed kind stays in the saved state so an explicit retry
reads only that kind again. When nothing usable was read the failure is raised as before.

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
import logging
import queue
import re
import threading
import time

from pydantic import ValidationError
from veriis import FileDescriptor, VerisOCR, VerisOCRError
from veriis.models import AadhaarResponse, PassportResponse, ResumeResponse

from app.integrations.ocr import client as ocr_client
from app.integrations.ocr.field_coverage import field_report, row_report
from app.integrations.ocr.classification import identity_hint, passport_plan
from app.integrations.ocr.inspection import InspectionError, inspect_document, restore_inspection
from app.integrations.ocr.mapping import aadhaar_fields, passport_fields, resume_fields
from app.integrations.ocr.merge import merge_results
from app.integrations.ocr.payload import prepare_job, rotations_from, unsendable
from app.integrations.ocr.person_split import page_identity, incompatible


log = logging.getLogger(__name__)
PIPELINE_VERSION = "local-pages-v2"  # v2: jobs are sent as prepared files, so v1 keys (other bytes) must not be reused
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
        if kind not in state["provider_jobs"]:
            continue  # its late part was cleared together with the kind
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
        late = state["provider_jobs"].pop(kind + "+", None) if not kind.endswith("+") else None
        if late is not None:
            # The page plan of this kind is about to change, so its late part would be left behind with nothing to merge into.
            state["provider_job_history"].setdefault(kind + "+", []).append(deepcopy(late))
        if kind == "passport" and record.get("status") == "failed":
            # Which pages the reader failed on (numbers only): the next attempt reads fewer of them (see _narrowed).
            tried = sorted({*record["selected_pages"], *((late or {}).get("selected_pages") or [])})
            state.setdefault("passport_plan", {}).setdefault("tried", []).append(tried)
        restarted.append(kind)
    return restarted


def _narrowed(plan: dict) -> list[int] | None:
    """The single passport page the next attempt is narrowed to after failed ones: the strongest page not yet read alone
    (valid machine-readable zone first, then the best remaining candidate, a duplicate scan last). None when nothing
    failed, or no page is left to narrow to (the whole selection is then sent again under a new key)."""
    ranked = plan.get("ranked") or []
    tried = plan.get("tried") or []
    if not tried or len(ranked) < 2:
        return None
    alone = {pages[0] for pages in tried if len(pages) == 1}
    return next(([page] for page in ranked if page not in alone), None)


def _plan_passport(state: dict, inspected, fresh: bool) -> tuple[list[int], bool]:
    """(pages of the passport job, narrowed after a failure). Empty pages: no page has passport evidence of its own, so
    the caller keeps every passport page. The plan is saved in the state (page numbers only) so that a restored
    inspection, which holds no text, and the failed attempts stay the same on every visit."""
    stored = state.get("passport_plan") or {}
    if fresh or "ranked" not in stored:
        numbers = set(inspected.passport_pages)
        rows = [(p.page_number, p.text, p.inherited, p.confidence) for p in inspected.pages if p.page_number in numbers]
        found = passport_plan(rows)
        gone = {item["page"] for item in state.get("passport_skipped") or []}  # pages already found not needed stay out
        stored = {"pages": [page for page in found.pages if page not in gone] if found else [],
                  "ranked": found.ranked if found else [],
                  "duplicates": found.duplicates if found else [], "tried": list(stored.get("tried") or []),
                  # The pages that are a data page (a second scan of one need not be paid for once the first was read).
                  "data_pages": [number for number, text, _inherited, _confidence in rows if _is_data_page(text)]}
        state["passport_plan"] = stored
    narrowed = _narrowed(stored)
    return (narrowed or list(stored["pages"])), narrowed is not None


def _is_data_page(text: str | None) -> bool:
    """Whether a passport page is the personal-details page (machine-readable zone, or the printed surname and date of
    birth), as opposed to the address or observations page. Evidence only; the text itself is never kept."""
    if not text:
        return False
    try:
        from app.integrations.ocr import validators
        if validators.find_mrz(text):
            return True
    except Exception:
        pass
    low = text.lower()
    return "date of birth" in low and ("surname" in low or "given name" in low)


def _passport_data_pages(state: dict, inspected) -> set[int]:
    plan = state.get("passport_plan") or {}
    if "data_pages" in plan:
        return set(plan["data_pages"])
    pages = getattr(_for("passport", inspected), "pages", None) or []  # a restored or chosen inspection holds no text
    return {page.page_number for page in pages if _is_data_page(getattr(page, "text", ""))}


def _usable_passport(payload) -> bool:
    """A passport result that already holds the number, the birth date and a name."""
    mrz = payload.get("mrz") if isinstance(payload, dict) and isinstance(payload.get("mrz"), dict) else {}
    return bool(mrz.get("passport_number") and mrz.get("date_of_birth") and (mrz.get("surname") or mrz.get("given_names")))


def _usable_passport_page(state: dict) -> int | None:
    """The page whose job already gave the number, birth date and name (the first such page), if any."""
    for kind, record in (state.get("provider_jobs") or {}).items():
        if _mode(kind) != "passport":
            continue
        first = lambda rec: (rec.get("selected_pages") or [0])[0]
        found = [(first(rec), rec.get("result")) for rec in record.get("chunks") or []]
        if "result" in record:  # the chunks were merged: the first page of the part stands for them
            found.append((first(record), record["result"]))
        for page, payload in found:
            if payload is not None and _usable_passport(payload):
                return page
    return None


def _skip_passport_page(state: dict, unit: _Unit, inspected) -> bool:
    """Do not pay for a further scan of the passport data page once another page gave the number, birth date and name.
    Only a page not yet sent is dropped: it leaves its job (and the kind's pages) and is recorded as not needed."""
    if _mode(unit.kind) != "passport" or len(unit.pages) != 1 or unit.record.get("job_id"):
        return False
    record = state["provider_jobs"].get(unit.kind) or {}
    chunks = record.get("chunks") or []
    page = unit.pages[0]
    if len(chunks) < 2 or not any(chunk is unit.record for chunk in chunks) or page not in _passport_data_pages(state, inspected):
        return False
    source = _usable_passport_page(state)
    if source is None or source == page:
        return False
    chunks[:] = [chunk for chunk in chunks if chunk is not unit.record]
    record["selected_pages"] = sorted(number for chunk in chunks for number in chunk["selected_pages"])
    place = {number: index for index, number in enumerate(record["selected_pages"], 1)}
    for index, chunk in enumerate(chunks):
        chunk["index"], chunk["positions"] = index, [place[number] for number in chunk["selected_pages"]]
    state.setdefault("passport_skipped", []).append({"page": page, "of": source})
    plan = state.get("passport_plan")
    if plan:
        plan["pages"] = [number for number in plan.get("pages") or [] if number != page]
    log.info("%s: page %d not needed, the details were already read from page %d", _step(unit), page, source)
    return True


def _without_skipped(state: dict, *selections: dict) -> None:
    """Take the passport pages found not needed out of the page lists (kind -> pages) that were planned before."""
    gone = {item["page"] for item in state.get("passport_skipped") or []}
    if not gone:
        return
    for selection in selections:
        for kind in list(selection):
            if _mode(kind) == "passport":
                selection[kind] = [page for page in selection[kind] if page not in gone]


def _resource_limited(rec: dict) -> bool:
    """The reader refused the job as too heavy (a resource or size limit) and said that trying it again cannot help."""
    reason = rec.get("provider_error") or {}
    code = str(reason.get("code") or "").lower()
    return reason.get("retryable", True) is False and any(word in code for word in ("resource", "limit", "too_large"))


def _split_unit(state: dict, unit: _Unit, source_hash) -> None:
    """Replace a job of several pages by one job per page (each page its own key; the results merge per kind as the chunks of
    a long document do, and a page already read is kept)."""
    record = state["provider_jobs"][unit.kind]
    digest = _for(_mode(unit.kind), source_hash)
    state.setdefault("provider_job_history", {}).setdefault(unit.kind, []).append(deepcopy(unit.record))
    positions = unit.record.get("positions") or list(range(1, len(unit.pages) + 1))
    singles = [{
        "index": 0, "selected_pages": [page], "positions": [position],
        "idempotency_key": _request_key(digest, unit.kind, [page], 0), "retry_generation": 0, "status": "submitting",
    } for page, position in zip(unit.pages, positions)]
    if record is unit.record:
        keep = {name: record[name] for name in ("idempotency_key", "selected_pages", "retry_generation") if name in record}
        record.clear()
        record.update(keep, status="submitting", chunks=singles)
    else:  # one chunk of a long document
        chunks = record["chunks"]
        where = next(index for index, chunk in enumerate(chunks) if chunk is unit.record)
        chunks[where:where + 1] = singles
        for name in ("result", "merge", "coverage_status", "validation_status", "error"):
            record.pop(name, None)
        record["status"] = "submitting"
    for index, chunk in enumerate(record["chunks"]):
        chunk["index"] = index


def _split_heavy(state: dict, units: list[_Unit], source_hash) -> bool:
    """A job of several pages that the reader refused as too heavy is sent again page by page. A job of one page cannot be
    made smaller: its failure stays terminal. Called once per pass; a split job cannot split again."""
    redone = False
    for unit in units:
        if not (unit.outcome == "failed" and len(unit.pages) > 1 and _resource_limited(unit.record)):
            continue
        log.warning("%s: the reader refused %d pages as too heavy (code=%s); sending them one page at a time",
                    _step(unit), len(unit.pages), _short((unit.record.get("provider_error") or {}).get("code")) or "none")
        _split_unit(state, unit, source_hash)
        redone = True
    return redone


def _split_oversize(state: dict, units: list[_Unit], source_hash, cap: int) -> bool:
    """Preflight: a prepared job of several pages above the byte limit is split BEFORE it is sent, not after a failure."""
    redone = False
    for unit in units:
        if unit.document is not None and len(unit.pages) > 1 and unit.size > cap:
            log.warning("%s: %d pages are %d KB, above the %d KB limit of one job; sending them one page at a time",
                        _step(unit), len(unit.pages), unit.size // 1024, cap // 1024)
            _split_unit(state, unit, source_hash)
            redone = True
    return redone


def _prepared_job(unit: _Unit, content: bytes, rotations: dict, *, settings) -> bytes:
    """The file of one job as sent (see integrations/ocr/payload.py), with the one log line of safe numbers."""
    pages = sorted(set(unit.pages))
    turns = [int(rotations.get(str(page), rotations.get(page, 0)) or 0) for page in pages]
    prepared = prepare_job(content, turns, max_bytes=int(_setting(settings, "veris_prepare_target_bytes", 3_000_000)))
    if prepared.fallback:
        return content
    log.info("%s: prepared %d page%s, %d KB (was %d KB), rotation fixed on %d page%s",
             _step(unit), prepared.pages, "" if prepared.pages == 1 else "s", max(1, (len(prepared.content) + 1023) // 1024),
             max(1, (prepared.original_bytes + 1023) // 1024), prepared.rotated, "" if prepared.rotated == 1 else "s")
    return prepared.content


def _payload_drops(state: dict, inspected, selected_by_kind: dict, fresh: bool) -> dict[int, str]:
    """Pages that carry nothing to send (blank) or repeat an earlier page exactly: decided once from the original (and
    kept in the state so every visit plans the same pages), left out of the page lists, and named in the coverage."""
    stored = state.get("payload_drops")
    if stored is None or fresh:
        roles: dict[int, list[str]] = {}
        for kind, pages in selected_by_kind.items():
            for page in pages:
                roles.setdefault(page, []).append(kind)
        source = getattr(inspected, "_pdf", None)
        pages = getattr(inspected, "pages", None)
        found: dict[int, str] = {}
        if source and pages and roles:
            unread = {page.page_number for page in pages if page.status == "unread" and page.page_number in roles}
            found = unsendable(source, {page: tuple(kinds) for page, kinds in roles.items()}, unread, getattr(inspected, "_lock", None))
        stored = {str(page): reason for page, reason in found.items()}
        state["payload_drops"] = stored
    drops = {int(page): reason for page, reason in stored.items()}
    for kind in list(selected_by_kind):
        selected_by_kind[kind] = [page for page in selected_by_kind[kind] if page not in drops]
    return drops


def _resubmit_duplicates(state: dict, units: list[_Unit]) -> bool:
    """A job the reader reported as a duplicate of an earlier one (same key) that is already failed would only fail the
    same way again: restart it under a new key (next retry generation). Called once per pass, so it never loops."""
    redone = False
    for unit in units:
        if not ((unit.outcome == "failed" and unit.record.get("duplicate"))
                or (unit.outcome == "error" and unit.record.get("key_conflict"))):
            continue
        record = state["provider_jobs"][unit.kind]
        state.setdefault("provider_job_history", {}).setdefault(unit.kind, []).append(deepcopy(unit.record))
        log.warning("%s: the reader already had this key (%s); sending it again under a new key", _step(unit),
                    "the job had failed" if unit.outcome == "failed" else "with other file contents")
        if record is unit.record:
            generations = state.setdefault("provider_generations", {})
            generations[unit.kind] = generations.get(unit.kind, 0) + 1
            del state["provider_jobs"][unit.kind]
        else:  # one chunk of a long document
            keep = {name: unit.record[name] for name in ("index", "selected_pages", "positions") if name in unit.record}
            generation = unit.record.get("retry_generation", 0) + 1
            unit.record.clear()
            unit.record.update(keep, retry_generation=generation, status="submitting")
            for name in ("result", "merge", "coverage_status", "validation_status", "error"):
                record.pop(name, None)
            record["status"] = "submitting"
        redone = True
    return redone


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


def _new_record(kind: str, selected: list[int], source_hash: str, generation: int, settings,
                order: list[int] | None = None) -> dict:
    """The durable intent for one kind: one provider job, or one per chunk of a long document.

    A passport of several pages is one job per page, the strongest page first (``order``: the plan's ranking), because
    the reader's passport mode fails on heavy multi-page PDFs and because a second scan of the data page may not be needed."""
    overlap = _setting(settings, "veris_chunk_overlap_pages", 1) if _mode(kind) == "resume" else 0
    if _mode(kind) == "passport" and len(selected) > 1 and _setting(settings, "veris_passport_one_page", True):
        ranked = [page for page in order or [] if page in selected]
        plan = [[page] for page in [*ranked, *(page for page in selected if page not in ranked)]]
    else:
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
    size: int = 0          # bytes of the document sent (for the log)


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
    reason = unit.record.get("provider_error") or {}
    code = f" (reader code: {reason['code']})" if reason.get("code") else ""
    wait = "Try again in a minute" if reason.get("retryable") else "Try again later"
    return f"We could not read the {_mode(unit.kind)} pages{unit.label}{code}. {wait}, or enter the details by hand."


_ORDER = ("resume", "passport", "aadhaar")


def _order(kind: str) -> tuple[int, bool]:
    """Send order: résumé, passport, Aadhaar; a kind's late part ("passport+") right after it."""
    mode = _mode(kind)
    return (_ORDER.index(mode) if mode in _ORDER else len(_ORDER), kind.endswith("+"))


def _step(unit: _Unit) -> str:
    return f"Veris {_mode(unit.kind)}{' (later pages)' if unit.kind.endswith('+') else ''}{unit.label}"


def _short(value) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(value or ""))[:40]


def provider_codes(state: dict) -> str:
    """The reader's own short failure codes in a saved state ("passport=code"), for a log line. Never any message or text."""
    found = []
    for kind, record in (state.get("provider_jobs") or {}).items():
        for rec in record.get("chunks") or [record]:
            code = _short((rec.get("provider_error") or {}).get("code"))
            if code and rec.get("status") == "failed":
                found.append(f"{kind}={code}")
    return ", ".join(found) or "none"


def _terminal(rec: dict, status: str, final: bool) -> bool:
    """A failure that trying the same job again will not fix: the reader said it cannot be retried, or the last
    automatic attempt has been used."""
    return final or (status == "failed" and (rec.get("provider_error") or {}).get("retryable", True) is False)


def _failed_notice(kind: str) -> str:
    name = _KIND_NAMES.get(_mode(kind), _mode(kind))
    return f"The {name} pages could not be read. Enter the {name} details by hand, or ask for the document to be read again."


def _check_cached(kind: str, selected: list[int], record: dict, source_hash: str, generation: int,
                  failures: dict | None = None, final: bool = False) -> list[_Unit]:
    """The units of a kind that already has a record; raises for a record that cannot be continued
    (with ``failures``, a terminal failure is recorded there instead and the kind has no work)."""
    if record.get("selected_pages") != selected:
        raise ExtractionFailure("The pages chosen for this document have changed. Check the saved results before trying again.")
    expected = _request_key(source_hash, kind, selected, generation)
    if not record.get("chunks") and record.get("idempotency_key") != expected:
        if record.get("job_id") or "result" in record:
            raise ExtractionFailure("The pages chosen for this document have changed. Check the saved results before trying again.")
        record["idempotency_key"] = expected  # never accepted by the reader (an older key format): nothing to conflict with
        record.pop("key_conflict", None)
    units = _units_of(kind, record, source_hash)
    if "result" in record:
        return []  # the whole kind (merged, for chunks) is already paid for
    for unit in units:
        rec = unit.record
        if rec.get("status") in {"failed", "timed_out"}:
            message = _failure_text(unit) if rec["status"] == "failed" else (
                f"Reading the {_mode(unit.kind)} pages{unit.label} took too long. It can be tried again.")
            if failures is not None and _terminal(rec, rec["status"], final):
                failures[kind] = message
                return []
            raise ExtractionFailure(message)
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
    except (VerisOCRError, ValidationError) as exc:
        events.put(("error", unit, exc, None))
    except BaseException as exc:  # an unexpected fault must reach the caller, not die in a thread
        events.put(("crash", unit, exc, None))


def _apply_job(unit: _Unit, job) -> bool:
    """Record what a polled job reported. Returns True when the saved state changed."""
    rec = unit.record
    before = (rec.get("status"), rec.get("error"), rec.get("validation_status"), "result" in rec, rec.get("provider_error"))
    rec["status"] = job.status
    if job.status in _PENDING:
        unit.outcome = "pending"
        age = _wall() - rec.setdefault("submitted_at", _wall())
        if age > ocr_client.provider_deadline(len(unit.pages)):
            rec["status"], rec["error"] = "timed_out", "The document reader did not finish in time."
            unit.outcome = "timed_out"
    elif job.status == "failed":
        rec["error"] = "The document reader could not read this file."
        # The reader's own reason (a short code and sentence it wrote, never page content) is kept so a failure can be named.
        reason = getattr(job, "error", None)
        if reason is not None:
            rec["provider_error"] = {
                "code": str(getattr(reason, "code", ""))[:80],
                "message": " ".join(str(getattr(reason, "message", "")).split())[:200],
                "retryable": bool(getattr(reason, "retryable", False)),
            }
        unit.outcome = "failed"
    elif job.status == "succeeded" and isinstance(job.result, dict):
        rec["result"] = deepcopy(job.result)
        unit.outcome = "ok"
    else:
        if job.status == "succeeded":
            rec["validation_status"] = "failed"
        unit.outcome = "unusable"
    return before != (rec.get("status"), rec.get("error"), rec.get("validation_status"), "result" in rec, rec.get("provider_error"))


def _log_outcome(unit: _Unit) -> None:
    rec, name = unit.record, _step(unit)
    took = max(0.0, _wall() - rec.get("submitted_at", _wall()))
    if unit.outcome == "ok":
        log.info("%s: succeeded in %.1fs", name, took)
    elif unit.outcome == "failed":
        reason = rec.get("provider_error") or {}
        log.warning("%s: failed in %.1fs code=%s retryable=%s", name, took, _short(reason.get("code")) or "none", bool(reason.get("retryable")))
    elif unit.outcome == "pending":
        log.info("%s: still running after %.1fs; it is checked again later", name, took)
    elif unit.outcome == "timed_out":
        log.warning("%s: did not finish in time (%.1fs)", name, took)
    else:
        log.warning("%s: the answer could not be used", name)


def _run_jobs(units: list[_Unit], *, state: dict, save, settings, on_kind_done=None, skip=None) -> bool:
    """Drive every unit that still needs the provider, at most veris_parallel_jobs at once. The job ids are saved once
    all submissions in flight are accepted; the caller saves the results (the returned flag says they changed).
    ``on_kind_done(kind)`` is called as soon as every job of a kind has its result, while other kinds are still being
    read; when it reports a change and other jobs are still running, that kind's result is saved at once, so a
    partial result is visible as each kind completes.
    ``skip(unit)`` may call a job off before it is sent (a passport page that is not needed any more). The pages of a
    passport go one at a time even when several jobs run side by side, so that a page can be found not needed."""
    if not units:
        return False
    left: dict[str, int] = {}
    for unit in units:
        left[unit.kind] = left.get(unit.kind, 0) + 1
    parallel = max(1, int(_setting(settings, "veris_parallel_jobs", 1)))
    events: queue.Queue = queue.Queue()
    abort = threading.Event()
    waiting: list[threading.Event] = []
    presubmit: set[int] = set()
    queued, active = deque(units), {}
    crash: BaseException | None = None
    dirty = False
    finished: list[str] = []
    # One at a time, a job still running holds the later ones back (they follow when it is done); a failed one does not.
    held = False
    held_passport = False  # a passport page is still running: the later pages wait for it

    def may_start(unit: _Unit) -> bool:
        return not (_mode(unit.kind) == "passport" and (held_passport or any(_mode(a.kind) == "passport" for a in active.values())))

    def start() -> None:
        nonlocal dirty
        while queued and not held and len(active) < parallel:
            unit = next((item for item in queued if may_start(item)), None)
            if unit is None:
                break
            queued.remove(unit)
            if skip is not None and skip(unit):
                unit.outcome, dirty = "skipped", True
                left[unit.kind] -= 1
                if left[unit.kind] == 0:
                    finished.append(unit.kind)
                continue
            active[id(unit)] = unit
            if not unit.record.get("job_id"):
                presubmit.add(id(unit))
                log.info("%s: sending pages %s (%d KB)", _step(unit), _ranges(unit.pages), max(1, (unit.size + 1023) // 1024))
            else:
                log.info("%s: checking saved job %s", _step(unit), _short(unit.record["job_id"])[:8])
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
                    duplicate = bool(getattr(payload, "duplicate", False))  # the reader already had a job under this key
                    unit.record.update(job_id=payload.job_id, status=payload.status, submitted_at=_wall(), duplicate=duplicate)
                    log.info("%s: job accepted %s duplicate=%s", _step(unit), _short(payload.job_id)[:8], duplicate)
                    waiting.append(ack)
                    dirty = True
                    continue
                presubmit.discard(id(unit))
                active.pop(id(unit), None)
                if name == "job":
                    dirty = _apply_job(unit, payload) or dirty
                    _log_outcome(unit)
                    held = held or (unit.outcome == "pending" and parallel == 1)
                    held_passport = held_passport or (unit.outcome == "pending" and _mode(unit.kind) == "passport")
                    if unit.outcome == "ok":
                        left[unit.kind] -= 1
                        if left[unit.kind] == 0:
                            finished.append(unit.kind)
                elif name == "error":
                    unit.outcome = "error"
                    status = getattr(payload, "status", None)
                    if status == 409:  # the same upload key was used before with other file contents
                        unit.record["key_conflict"] = True
                    log.warning("%s: the reader could not be reached or sent an answer we could not use (http=%s code=%s)",
                                _step(unit), status, str(getattr(payload, "code", None) or "")[:60] or "-")
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
    for unit in queued:
        log.info("%s: not sent yet, waiting for the earlier reading to finish", _step(unit))
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
    unsaved: bool = False, ready=None, failures: dict | None = None, final: bool = False,
) -> dict[str, dict | None]:
    """Provider result of every kind, with the kinds (and the chunks of a long document) sent side by side.

    ``inspected``/``source_hash`` may be per kind (``_Each``). A kind whose chunks did not cover their pages comes back
    as None. Raises ExtractionFailure for a failed job and ExtractionPending when a job is still running.

    With ``failures`` (a dict the caller owns) a kind whose job ended in a terminal failure (see _terminal; ``final`` is
    the last automatic attempt) is left out of the result and named there with its message instead of raising; a
    failure that a new attempt may still fix raises as before."""
    saved = not unsaved

    def checkpoint() -> None:
        nonlocal saved
        _persist(state, save)
        saved = True

    try:
        return _fetch(selected_by_kind, inspected, state=state, save=save, settings=settings,
                      source_hash=source_hash, checkpoint=checkpoint, ready=ready, failures=failures, final=final)
    finally:
        if not saved:
            try:
                checkpoint()  # the local inspection must outlive a failure that happens before the first save
            except ExtractionFailure:
                pass


def _fetch(selected_by_kind, inspected, *, state, save, settings, source_hash, checkpoint, ready=None,
           failures: dict | None = None, final: bool = False, resubmitted: bool = False) -> dict:
    jobs = state.setdefault("provider_jobs", {})
    generations = state.get("provider_generations", {})
    kinds = sorted((kind for kind, selected in selected_by_kind.items() if selected), key=_order)
    units: dict[str, list[_Unit]] = {}
    work: list[_Unit] = []
    fresh: dict[str, dict] = {}
    for kind in kinds:
        selected, digest = selected_by_kind[kind], _for(_mode(kind), source_hash)
        generation = generations.get(kind, 0)
        if kind in jobs:
            work.extend(_check_cached(kind, selected, jobs[kind], digest, generation, failures, final))
        else:
            fresh[kind] = _new_record(kind, selected, digest, generation, settings,
                                      order=(state.get("passport_plan") or {}).get("ranked"))
            work.extend(_units_of(kind, fresh[kind], digest))
    if work and not (settings.veris_ocr_base_url and settings.veris_ocr_api_key):
        raise ExtractionFailure("Document reading is not set up on this server yet. Ask your system administrator to finish the setup.")
    kinds = [kind for kind in kinds if kind not in (failures or {})]  # an earlier terminal failure is not read again here
    if fresh:
        # Persist the keys before any paid work. Lost acceptance responses can
        # safely retry the same payload/key rather than create another job.
        jobs.update(fresh)
        checkpoint()
    for kind in kinds:
        digest = _for(_mode(kind), source_hash)
        units[kind] = _units_of(kind, jobs[kind], digest)
    work = [unit for kind in kinds for unit in units[kind] if "result" not in unit.record and "result" not in jobs[kind]]
    rotations = state.get("page_rotations") or {}
    for unit in work:
        if not unit.record.get("job_id"):
            content = _for(_mode(unit.kind), inspected).pdf_for_pages(unit.pages, allow_unresolved=True)
            content = _prepared_job(unit, content, rotations, settings=settings)
            unit.document = FileDescriptor(content, filename=f"{_mode(unit.kind)}.pdf", content_type="application/pdf")
            unit.size = len(content)
    if _split_oversize(state, work, source_hash, int(_setting(settings, "veris_job_max_bytes", 8_000_000))):
        checkpoint()
        return _fetch(selected_by_kind, inspected, state=state, save=save, settings=settings, source_hash=source_hash,
                      checkpoint=checkpoint, ready=ready, failures=failures, final=final,
                      resubmitted=resubmitted)
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
    dirty = _run_jobs(work, state=state, save=save, settings=settings, on_kind_done=kind_done,
                      skip=lambda unit: _skip_passport_page(state, unit, inspected)) or bool(early_done)
    if _split_heavy(state, work, source_hash):
        # A job of several pages was refused as too heavy: its pages go again one by one in this same pass, before any
        # degrading. The paid results of this pass are kept; a page that fails alone is terminal as before.
        checkpoint()
        return _fetch(selected_by_kind, inspected, state=state, save=save, settings=settings, source_hash=source_hash,
                      checkpoint=checkpoint, ready=ready, failures=failures, final=final, resubmitted=resubmitted)
    if not resubmitted and _resubmit_duplicates(state, work):
        checkpoint()  # the paid results of this pass are kept; the restarted jobs are met again with their new keys
        return _fetch(selected_by_kind, inspected, state=state, save=save, settings=settings, source_hash=source_hash,
                      checkpoint=checkpoint, ready=ready, failures=failures, final=final, resubmitted=True)

    failure: str | None = None
    pending: tuple[str, str] | None = None
    for kind in kinds:
        for unit in units[kind]:
            message = {
                "failed": lambda: _failure_text(unit),
                "timed_out": lambda: f"Reading the {_mode(unit.kind)} pages{unit.label} took too long. It can be tried again.",
                "error": lambda: "The document reader sent back an answer we could not use. Try again.",
                "unusable": lambda: "The document reader did not find anything we could use. Check the document, then try again.",
            }.get(unit.outcome)
            if message is not None:
                if failures is not None and (unit.outcome == "unusable" or _terminal(unit.record, unit.outcome, final)):
                    failures.setdefault(kind, message())
                elif failure is None:
                    failure = message()
            elif unit.outcome == "pending" and pending is None:
                pending = (unit.record["job_id"], kind)
    if failure or pending:
        if dirty:
            checkpoint()  # the paid responses of the jobs that did finish are kept
        raise ExtractionFailure(failure) if failure else ExtractionPending(*pending)
    results: dict[str, dict | None] = {}
    for kind in kinds:
        if kind in (failures or {}):
            continue
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


def _page_notes(report: dict, sent: set[int], reasons: dict[int, str] | None = None) -> list[dict]:
    """The pages of the file that no job was sent, with the reason (``reasons``: a given reason for a page that was
    identified as a document page but deliberately held back)."""
    out = []
    for page in report.get("pages") or []:
        number = page.get("page_number")
        if number in sent:
            continue
        status, kind = page.get("status"), page.get("document_type")
        if (reasons or {}).get(number):
            reason, blocking = reasons[number], False  # deliberately held back (not needed, blank, duplicate)
        elif status == "unread":
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
              probes: dict | None = None, review: list[str] | None = None,
              page_reasons: dict[int, str] | None = None) -> tuple[dict, list[str], list[str]]:
    """The account of what was sent and what became of every Veris field, and the reasons (if any) to review."""
    sent = sorted({page for pages in selected_by_kind.values() for page in pages})
    not_sent = [] if chosen else _page_notes(report, set(sent), page_reasons)
    issues: list[str] = list(review or [])  # a kind that could not be read is a reason to review
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
        "kinds": kinds, "unmapped": unmapped, "issues": issues, "notes": notes, "failed": list(review or []),
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
               unsaved: bool = False, finalize: bool = True, classified: dict | None = None,
               degrade: bool = False, final_attempt: bool = False):
    """Send each kind's pages to Veris (resuming checkpointed jobs), validate coverage and map
    the fields. Returns (extractions, warnings, every_kind_covered, reasons_to_review).

    With ``degrade``, a kind that ended in a terminal provider failure does not fail the file while another kind was
    read: it is named in the reasons to review (and the warnings) and the others are mapped. Nothing read at all
    still raises. ``final_attempt`` says no automatic attempt is left, so any failure is terminal.

    With ``finalize`` the state is saved once at the end; a caller that saves right after (``_finish``) passes False."""
    extractions: dict = {}
    warnings: list[str] = []
    review: list[str] = []
    covered = True
    # Pages found after a kind was first sent (the reading went on while its first pages were already with Veris) are
    # sent as a late part ("resume+") and merged with the first part; pages already sent stay sent.
    _without_skipped(state, selected_by_kind, classified or {})
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

    failures: dict[str, str] | None = {} if degrade else None
    payloads = _fetch_all(
        parts, inspected, state=state, save=save, settings=settings,
        source_hash=source_hash, unsaved=unsaved, ready=ready, failures=failures, final=final_attempt,
    )
    # A passport page found not needed during this pass (see _skip_passport_page) is no longer a page of the kind.
    _without_skipped(state, selected_by_kind, final, parts, classified or {})
    lost = {_mode(key) for key in failures or {}}
    if lost:
        if not any(payload is not None for key, payload in payloads.items() if _mode(key) not in lost):
            raise ExtractionFailure(next(iter(failures.values())))  # nothing usable was read: the failure is not hidden
        for kind in sorted(lost, key=_order):
            review.append(_failed_notice(kind))
        warnings.extend(review)
        covered = False
        log.warning("Reading finished without: %s (reader codes: %s); the rest is kept for review.",
                    ", ".join(sorted(lost, key=_order)), provider_codes(state))
    late_merge: dict[str, dict] = {}
    for kind in kinds:
        if kind + "+" not in parts or kind in lost:
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
        if kind in lost:
            continue
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


def _early_selection(partial, provisional, again: list[int], plans: dict | None = None) -> dict[str, list[int]]:
    """After the first quick look: the pages already settled for each kind (not waiting for a second look)."""
    waiting = set(again)
    out: dict[str, list[int]] = {}
    for kind in ("resume", "passport", "aadhaar"):
        rows = [
            (page, verdict) for page, verdict in zip(partial.pages, provisional)
            if page.status == "read" and page.page_number not in waiting and (verdict.document_type == kind or kind in verdict.also)
        ]
        pages = [page.page_number for page, _ in rows]
        if kind == "passport" and pages:
            # The same rule as the final plan (see _plan_passport): neighbours with no passport details of their own are not sent.
            plan = passport_plan([(page.page_number, page.text, verdict.inherited, page.confidence) for page, verdict in rows])
            pages = plan.pages if plan else pages
            if plan and plans is not None:  # the ranking and data pages, so that the strongest page is sent first
                plans["passport"] = {"pages": list(plan.pages), "ranked": list(plan.ranked), "duplicates": list(plan.duplicates),
                                     "data_pages": [page.page_number for page, _ in rows if _is_data_page(page.text)]}
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
        plans: dict = {}
        events.put(("early", (partial, _early_selection(partial, provisional, again, plans), plans)))

    def work() -> None:
        try:
            events.put(("done", inspect_document(data, **options, on_first_pass=first_pass)))
        except BaseException as exc:
            events.put(("error", exc))

    threading.Thread(target=work, daemon=True).start()
    while True:
        name, payload = events.get()
        if name == "early":
            partial, selection, plans = payload
            if (state.get("passport_plan") or {}).get("tried"):
                selection.pop("passport", None)  # the passport reading failed before: the normal pass narrows it
            elif "passport" in selection and "ranked" not in (state.get("passport_plan") or {}) and plans.get("passport"):
                state["passport_plan"] = {**plans["passport"], "tried": []}  # replaced by the final plan after the full read
            if any(selection.values()):
                try:
                    _fetch_all(selection, partial, state=state, save=save, settings=settings, source_hash=source_hash)
                except (ExtractionPending, ExtractionFailure):
                    pass
        elif name == "done":
            return payload
        else:
            raise payload


def _passport_reasons(state: dict, inspected, sent: set[int]) -> dict[int, str]:
    """Why a page taken for a passport was not sent (shown in the coverage)."""
    plan = state.get("passport_plan") or {}
    reasons = {}
    skipped = {item["page"]: item["of"] for item in state.get("passport_skipped") or []}
    for page in inspected.passport_pages:
        if page in sent:
            continue
        if page in skipped:
            reasons[page] = f"Not needed: details already read from page {skipped[page]}."
            continue
        twin = next((d["of"] for d in plan.get("duplicates") or [] if d["page"] == page), None)
        reasons[page] = (
            f"Duplicate of page {twin}; the copy with the better reading was sent." if twin else
            "Held back after the reader failed on these pages; the strongest page is read alone first." if page in (plan.get("pages") or []) else
            "Next to a passport page but with no passport details of its own; not sent.")
    return reasons


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
    final_attempt: bool = False,
) -> dict:
    """Inspect every page and resume recorded résumé/passport/Aadhaar jobs.

    Every page is classified as résumé, passport, Aadhaar or other whatever the upload was called, and each kind found is
    sent on its own (a combined file is the normal case). ``partial`` (the default) extracts the confidently classified
    pages of a file that also has unread or unclassified ones (reported as needs_review, never dropped); with
    ``partial=False`` any such page holds the whole file back. ``ignore_decorative`` sets aside a single
    picture holding no document text (a logo or photo) instead of asking for review.

    A kind the reader terminally failed on does not fail the file while another kind was read: the result is
    needs_review with a notice naming the kind (``final_attempt``: the caller has no automatic attempt left).
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
        # Pages the local reading could only read once turned: sent upright (see integrations/ocr/payload.py).
        state["page_rotations"] = {str(page): angle for page, angle in rotations_from(inspected.completeness).items()}
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
    passport_pages, narrowed = _plan_passport(state, inspected, fresh_inspection)
    if passport_pages:
        classified["passport"] = passport_pages
    if narrowed:
        probes = {kind: pages for kind, pages in probes.items() if kind != "passport"}  # nothing is added to a narrowed attempt
    selected_by_kind = {kind: sorted({*pages, *probes.get(kind, [])}) for kind, pages in classified.items()}
    payload_drops = _payload_drops(state, inspected, selected_by_kind, fresh_inspection)
    for kind in classified:
        classified[kind] = [page for page in classified[kind] if page not in payload_drops]
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
    extractions, notices, covered, review = _run_kinds(
        selected_by_kind, inspected, report, source_hash, state=state, save=save,
        settings=settings, designations=designations, unsaved=fresh_inspection, finalize=False, classified=classified,
        degrade=True, final_attempt=final_attempt,
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
    page_reasons = {page: f"Not sent: {reason}." for page, reason in payload_drops.items()}
    page_reasons.update(_passport_reasons(state, inspected, set(selected_by_kind["passport"])))
    coverage, issues, shown = _coverage(report, selected_by_kind, extractions, settings=settings, probes=probes, review=review,
                                        page_reasons=page_reasons)
    warnings.extend([*issues, *shown])
    if issues:
        status = "needs_review"
    return _finish(state, save, report, extractions, status, warnings, coverage=coverage)
