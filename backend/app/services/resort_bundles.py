"""Sort the combined files that were stored by older code, in the background, once.

Candidates saved before pages were sorted by content (an import that stored one 9-page PDF holding the resume, the passport, the
experience certificates and two ID cards in the Resume field) show one document and empty fields. This module finds such stored files
and queues ONE `resort` job per file; the reading worker (document_extraction_worker -> manual_extraction.process_job) runs it, where
the long page-by-page reading is fine, and files the pieces in their own fields exactly like an upload-time split
(`document_splitting.store_pieces`): never a page lost, nothing filed twice, the whole file kept as the source evidence
(`source_bundle`), pages nobody could place in Other documents with a note. The new resume / passport / Aadhaar / education pieces are
then queued for reading, so the form fills by itself.

Nothing here runs in a request thread except the cheap queuing (`enqueue`): no file is opened and no page is read there.

Markers. A resort job is the marker: one row per stored file (candidate + SHA-256) for ever, in ANY state, so a file is never queued
twice and a second run finds nothing to do. The row has no `candidate_id` (so the candidate's reading jobs, their screens and the
read-pending bookkeeping never see it); the candidate is its `group_id` (indexed) and the file is named in its draft:

  group_id        the candidate's id
  kind            "resort"
  draft["resort"] {"candidateId", "attachmentId", "slot"}
  state["resort"] {"outcome", ...}      what happened (set when the job ends)

A file that needs no sorting at all (already filed by the reader, a piece of a larger scan, too big, left to the reader because it was
never read) gets its marker already `completed`, with the reason as the outcome. A sort that was CUT SHORT (time budget, a page that
could not be read in time, a failure) is never marked as sorted: it is tried again later, up to `RESORT_MAX_ATTEMPTS` times, and then
given up on (status `dismissed`, nothing changed) rather than looping.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select, update
from sqlalchemy.orm import undefer

from app.core.config import get_settings
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob
from app.services import document_splitting as splitting
from app.services.document_storage import read_bytes

log = logging.getLogger(__name__)

RESORT_KIND = "resort"
IDENTITY_SLOTS = ("resume", "passport", "aadhaar")
RESORT_SLOTS = (*IDENTITY_SLOTS, "other", "source_bundle")       # where a combined file can be sitting
SOURCE_SLOT = "source_bundle"
RESORT_MAX_KB = 25 * 1024            # the upload cap: a bigger stored file is never opened
RESORT_MAX_PAGES = 40                # the page sorter's own cap (`bulk.SPLIT_MAX_PAGES`): more pages cannot be sorted in full
RESORT_DOCUMENT_TIMEOUT = 240.0      # seconds of page reading one attempt may take (an upload's own sort gets 120)
RESORT_MAX_ATTEMPTS = 3              # cut-short or failed attempts before the file is given up on
RESORT_RETRY_SECONDS = 600           # a cut-short attempt is tried again after this
RESORT_FILES_PER_CANDIDATE = 8       # jobs one call queues for one candidate (the rest wait for the next call)
RESORT_SWEEP_CANDIDATES = 25         # candidates one sweep call handles

# What a marker says when it was created complete (no sorting to do) and what a finished job says.
OUTCOMES = {
    "sorted": "the pieces were filed in their own fields",
    "one_document": "the file is one kind of document throughout",
    "not_a_bundle": "a single page or a front and back scan",
    "too_large": "over the size cap, never opened",
    "too_long": "more pages than the sorter reads",
    "unreadable": "the file could not be opened",
    "file_changed": "the stored file is gone or was replaced",
    "sorted_by_reading": "its reading already filed its other documents",
    "already_a_piece": "cut from a larger scan",
    "pieces_already_filed": "its pieces were filed when it was stored",
    "left_to_reading": "never read: the reading of the file sorts it",
    "superseded": "an older version, replaced by a newer file",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ queuing (cheap: statements only, no file is opened)

@dataclass
class Queued:
    jobs: int = 0        # sort jobs put in the queue
    marked: int = 0      # files that needed no sorting (their marker was written complete)
    more: bool = False   # the per-candidate cap left files for the next call

    def __iadd__(self, other: "Queued") -> "Queued":
        self.jobs += other.jobs
        self.marked += other.marked
        self.more = self.more or other.more
        return self


def _new_job(db, user, candidate_id: str, attachment: Attachment, status: str, outcome: str | None, settings) -> DocumentExtractionJob:
    from app.services.instance_guard import instance_name
    stamp = now()
    job = DocumentExtractionJob(
        id=str(uuid.uuid4()), instance=instance_name(settings), owner_id=user.id, group_id=candidate_id, kind=RESORT_KIND,
        filename=(attachment.file_name or "file")[:150], content_type=attachment.content_type or "application/pdf",
        object_key=attachment.object_key, content_hash=attachment.content_hash, size_kb=attachment.size_kb or 1,
        status=status, candidate_id=None, attachment_id=None, queued_at=stamp,
        draft={RESORT_KIND: {"candidateId": candidate_id, "attachmentId": attachment.id, "slot": attachment.slot}},
        state={RESORT_KIND: {"outcome": outcome}} if outcome else {})
    if status == "completed":
        job.finished_at = stamp
        job.result = {"outcome": outcome, "reason": OUTCOMES.get(outcome or "", "")}
    db.add(job)
    return job


def _unmarked_files(db, candidate_ids: list[str]) -> list[Attachment]:
    """The stored PDFs of these candidates, in the fields a combined file can sit in, that have no resort job yet (one statement)."""
    marked = select(DocumentExtractionJob.id).where(
        DocumentExtractionJob.kind == RESORT_KIND, DocumentExtractionJob.group_id == Attachment.entity_id,
        DocumentExtractionJob.content_hash == Attachment.content_hash).exists()
    return list(db.scalars(select(Attachment).where(
        Attachment.entity_type == "candidate", Attachment.entity_id.in_(candidate_ids), Attachment.slot.in_(RESORT_SLOTS),
        Attachment.content_type == "application/pdf", Attachment.content_hash.is_not(None), Attachment.content_hash != "", ~marked)
        .order_by(Attachment.entity_id, Attachment.version.desc())))


def _current_files(db, candidate_ids: list[str]) -> dict[tuple[str, str], tuple[int, datetime | None]]:
    """(candidate, field) -> (newest version, when it was uploaded), for the identity fields."""
    newest: dict[tuple[str, str], tuple[int, datetime | None]] = {}
    for entity, slot, version, uploaded in db.execute(select(Attachment.entity_id, Attachment.slot, Attachment.version, Attachment.uploaded_at).where(
            Attachment.entity_type == "candidate", Attachment.entity_id.in_(candidate_ids), Attachment.slot.in_(IDENTITY_SLOTS))):
        if (entity, slot) not in newest or version > newest[(entity, slot)][0]:
            newest[(entity, slot)] = (version, uploaded)
    return newest


def _filing_history(db, candidate_ids: list[str]) -> tuple[set[str], set[str]]:
    """From the audit trail: (the stored files that are themselves a piece cut from a larger scan, the stored files whose pieces were
    already filed). Pieces of the Resume / Passport / Aadhaar fields are never cut again, and a scan whose pieces were filed when it was
    stored has nothing left to sort. (A piece in Other documents is different: the old sorter left the pages it could not place there,
    so `enqueue` sorts it anyway; and a scan kept as received with NO piece cut from it was never sorted at all.)"""
    pieces: set[str] = set()
    sources: set[str] = set()
    kept: dict[str, str] = {}              # a scan kept as received -> its original name
    cut_from: set[str] = set()             # original names of the scans some piece was cut from
    for meta in db.scalars(select(AuditEntry.meta).where(
            AuditEntry.entity_type == "candidate", AuditEntry.entity_id.in_(candidate_ids),
            AuditEntry.action.in_(("Uploaded document", "Separated a document from a combined upload", "Added a saved document to a candidate")))):
        if not isinstance(meta, dict) or not meta.get("attachment_id"):
            continue
        if meta.get("split_pages") or meta.get("source_attachment_id"):
            pieces.add(meta["attachment_id"])
            if meta.get("original_name"):
                cut_from.add(meta["original_name"])
        if meta.get("source_attachment_id"):
            sources.add(meta["source_attachment_id"])
        if meta.get("kept_original"):
            kept[meta["attachment_id"]] = meta.get("original_name") or ""
        elif meta.get("slot") == SOURCE_SLOT and meta.get("original_only") is False:
            sources.add(meta["attachment_id"])     # the scan kept as received beside the pieces cut from it
    sources.update(identifier for identifier, name in kept.items() if name in cut_from)
    return pieces, sources


def _reading_state(db, candidate_ids: list[str], keys: set[str]) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    """For the stored files among `keys`: the (candidate, file) pairs that have a reading job, those whose reading already filed the other
    documents, and those read by today's code (page kinds saved) whose other documents are about to be filed from that reading (the
    cheap, in-request `application_form.file_other_documents_of_read_files`): no sorting job is needed for those."""
    from app.services.application_form import OTHERS_FILED
    read: set[tuple[str, str]] = set()
    filed: set[tuple[str, str]] = set()
    due: set[tuple[str, str]] = set()
    if not keys:
        return read, filed, due
    for candidate_id, object_key, marker, kinds, job_status in db.execute(
            select(DocumentExtractionJob.candidate_id, DocumentExtractionJob.object_key, DocumentExtractionJob.draft[OTHERS_FILED].astext,
                   DocumentExtractionJob.state["page_kinds"].is_not(None), DocumentExtractionJob.status)
            .where(DocumentExtractionJob.candidate_id.in_(candidate_ids), DocumentExtractionJob.kind != RESORT_KIND,
                   DocumentExtractionJob.object_key.in_(keys))):
        pair = (candidate_id, object_key)
        read.add(pair)
        if marker:
            filed.add(pair)
        elif kinds and job_status in ("completed", "needs_review"):
            due.add(pair)
    return read, filed, due


def enqueue(db, user, candidates: list, *, per_candidate: int = RESORT_FILES_PER_CANDIDATE) -> Queued:
    """Queue the sorting of every stored combined file of these (already permitted) candidates that was never looked at, once per file.

    Call it BEFORE `candidate_import.queue_unread` in the same request: a file of the Resume / Passport / Aadhaar field that no reading
    job has seen yet is left to its reading (the reading sorts it), and only a file that already HAS a reading job (read by older code
    that did not file its pages), or sits in Other documents / the source evidence, is queued here. A file that needs no sorting gets a
    marker that is already complete. The caller commits. Statements only: nothing is opened or read."""
    out = Queued()
    ids = [candidate.id for candidate in candidates]
    if not ids:
        return out
    files = _unmarked_files(db, ids)
    if not files:
        return out
    settings = get_settings()
    newest = _current_files(db, ids)
    pieces, sources = _filing_history(db, ids)
    read, filed, due = _reading_state(db, ids, {row.object_key for row in files if row.slot in IDENTITY_SLOTS})
    order = {slot: index for index, slot in enumerate(RESORT_SLOTS)}
    taken: dict[str, set[str]] = {}          # candidate -> hashes handled in this call (one stored file may sit in several fields)
    queued_for: dict[str, int] = {}
    for row in sorted(files, key=lambda item: (item.entity_id, order[item.slot], -item.version)):
        done = taken.setdefault(row.entity_id, set())
        if row.content_hash in done:
            continue
        outcome: str | None
        if row.slot in IDENTITY_SLOTS and (row.entity_id, row.slot) in newest and (
                row.version != newest[(row.entity_id, row.slot)][0] and row.uploaded_at != newest[(row.entity_id, row.slot)][1]):
            outcome = "superseded"          # an older version of a field: history, never cut
        elif (row.size_kb or 0) > RESORT_MAX_KB:
            outcome = "too_large"
        elif row.slot != "other" and row.id in pieces:
            outcome = "already_a_piece"
        elif row.slot != "other" and row.id in sources:
            outcome = "pieces_already_filed"
        elif row.slot in IDENTITY_SLOTS and (row.entity_id, row.object_key) in filed:
            outcome = "sorted_by_reading"
        elif row.slot in IDENTITY_SLOTS and (row.entity_id, row.object_key) in due:
            continue                        # no marker: the next call finds its other documents filed from the saved reading
        elif row.slot in IDENTITY_SLOTS and (row.entity_id, row.object_key) not in read:
            outcome = "left_to_reading"
        else:
            outcome = None
        if outcome is None and queued_for.get(row.entity_id, 0) >= per_candidate:
            out.more = True                 # no marker: the next call looks at this file again
            continue
        done.add(row.content_hash)
        if outcome is None:
            _new_job(db, user, row.entity_id, row, "pending", None, settings)
            queued_for[row.entity_id] = queued_for.get(row.entity_id, 0) + 1
            out.jobs += 1
        else:
            _new_job(db, user, row.entity_id, row, "completed", outcome, settings)
            out.marked += 1
    if out.jobs:
        from app.services.extraction_queue import notify_work
        notify_work(db, "manual")           # a sleeping worker starts at once (delivered when the caller commits)
    return out


def next_candidates(db, limit: int = RESORT_SWEEP_CANDIDATES) -> tuple[list[str], bool]:
    """The ids of up to `limit` candidates that still have a stored file nobody looked at (see `enqueue`), and whether there are more.
    One statement; the order is stable, so repeated calls walk through the Data Bank."""
    marked = select(DocumentExtractionJob.id).where(
        DocumentExtractionJob.kind == RESORT_KIND, DocumentExtractionJob.group_id == Attachment.entity_id,
        DocumentExtractionJob.content_hash == Attachment.content_hash).exists()
    ids = list(db.scalars(
        select(Attachment.entity_id).join(Candidate, Candidate.id == Attachment.entity_id).where(
            Attachment.entity_type == "candidate", Attachment.slot.in_(RESORT_SLOTS), Attachment.content_type == "application/pdf",
            Attachment.content_hash.is_not(None), Attachment.content_hash != "", ~marked)
        .group_by(Attachment.entity_id).order_by(Attachment.entity_id).limit(limit + 1)))
    return ids[:limit], len(ids) > limit


# ------------------------------------------------------------------ the job (runs in the reading worker)

def _lost(job, claimed_by) -> bool:
    from app.services.manual_extraction import READY
    return job is None or job.status not in READY or (claimed_by is not None and job.claimed_by != claimed_by)


def _finish(job, outcome: str, result: dict | None = None) -> None:
    """Mark a locked job done (a file that needs no more sorting, or one that was sorted)."""
    stamp = now()
    job.state = {**dict(job.state or {}), RESORT_KIND: {"outcome": outcome, "at": stamp.isoformat()}}
    job.result = {"outcome": outcome, "reason": OUTCOMES.get(outcome, ""), **(result or {})}
    job.status, job.last_error = "completed", None
    job.updated_at = job.finished_at = stamp
    job.claimed_by = job.lease_until = None


def _complete(sessions, identifier: str, claimed_by, outcome: str) -> None:
    with sessions() as db:
        job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state)).where(DocumentExtractionJob.id == identifier).with_for_update())
        if _lost(job, claimed_by):
            return
        _finish(job, outcome)
        db.commit()


def _postpone(sessions, identifier: str, claimed_by, why: str) -> None:
    """The sort was cut short or failed: never marked as sorted. Tried again later; after `RESORT_MAX_ATTEMPTS` it is given up on."""
    with sessions() as db:
        job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state)).where(DocumentExtractionJob.id == identifier).with_for_update())
        if _lost(job, claimed_by):
            return
        stamp = now()
        job.attempts += 1
        job.last_error = why[:500]
        job.state = {**dict(job.state or {}), RESORT_KIND: {"outcome": None, "attempt": job.attempts, "why": why[:200], "at": stamp.isoformat()}}
        if job.attempts >= RESORT_MAX_ATTEMPTS:
            job.status = "dismissed"      # given up: nothing was changed; a person can still read and file the pages by hand
            job.finished_at = stamp
        else:
            job.status, job.next_attempt_at = "retry", stamp + timedelta(seconds=RESORT_RETRY_SECONDS)
        job.updated_at = stamp
        job.claimed_by = job.lease_until = None
        attempt = job.attempts
        db.commit()
    log.warning("Re-sort %s did not finish (%s); attempt %s of %s.", identifier, why, attempt, RESORT_MAX_ATTEMPTS)


def _pages_text(pages) -> str:
    return ("page " if len(pages) == 1 else "pages ") + ", ".join(str(page) for page in sorted(pages))


def _file_pieces(db, owner, candidate: Candidate, source: Attachment, plan, job, settings, written: list[str]) -> list:
    """The filing itself, in the caller's transaction and savepoint (it holds the candidate's lock)."""
    from app.services.application_form import queue_reading, requeue_failed
    original_slot = source.slot
    if source.slot != SOURCE_SLOT:
        # The whole file leaves its field first: left there, it would "already hold" every page of every piece (the duplicate check), so
        # nothing would be filed. It becomes the source evidence, the same file under the same id: nothing is copied or lost.
        last = db.scalar(select(func.max(Attachment.version)).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == SOURCE_SLOT))
        source.slot, source.version = SOURCE_SLOT, (last or 0) + 1
        db.flush()
    if original_slot != "resume":
        # A resume pulled out of a passport / Aadhaar / Other file never replaces a resume the candidate already has: its pages go to
        # Other documents instead (they are still on the record, and the whole file is kept).
        held = db.scalars(select(Attachment).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == "resume")
            .order_by(Attachment.version.desc())).all()
        if held:
            for piece in plan.pieces:
                # ... unless the candidate's resume already shows these very pages (cut from the same scan earlier): then nothing is
                # filed at all, and `store_pieces` says so
                if "resume" in piece.slots and splitting._already_filed(piece, "resume", held, settings) is None:
                    piece.slots = [slot for slot in dict.fromkeys("other" if slot == "resume" else slot for slot in piece.slots)]
    stored = splitting.store_pieces(db, owner, candidate, plan, source.file_name, settings=settings, written=written,
                                    audit_extra={"document_job_id": job.id, "source_attachment_id": source.id, "resorted": True},
                                    uploaded_at=source.uploaded_at)
    # The pieces are read like any upload to their field (once per stored file); the whole file's own waiting reading is no longer needed.
    queued: set[str] = set()
    for piece in stored:
        if piece.slot not in splitting.READ_SLOTS:
            continue
        if piece.duplicate:
            requeue_failed(db, candidate.id, piece.attachment.id)
        elif piece.attachment.object_key not in queued:
            queued.add(piece.attachment.object_key)
            queue_reading(db, owner, candidate, piece.attachment, "application/pdf")
    db.execute(update(DocumentExtractionJob).where(
        DocumentExtractionJob.attachment_id == source.id, DocumentExtractionJob.kind != RESORT_KIND,
        DocumentExtractionJob.status.in_(("pending", "retry"))).values(status="dismissed", updated_at=now()))
    return stored


def _apply(sessions, settings, identifier: str, claimed_by, candidate_id: str, attachment_id: str, digest: str, plan) -> None:
    from app.services.bulk_candidate_import import discard_written
    with sessions() as db:
        candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())     # candidate before job
        job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.draft))
                        .where(DocumentExtractionJob.id == identifier).with_for_update())
        if _lost(job, claimed_by):
            return
        source = db.get(Attachment, attachment_id)
        if candidate is None or source is None or source.entity_id != candidate_id or source.content_hash != digest:
            _finish(job, "file_changed")      # the file was replaced or removed while its pages were being read
            db.commit()
            return
        owner = SimpleNamespace(id=job.owner_id)
        written: list[str] = []
        from_slot = source.slot
        try:
            with db.begin_nested():
                stored = _file_pieces(db, owner, candidate, source, plan, job, settings, written)
        except Exception:
            discard_written(settings, written)
            raise
        fresh = [piece for piece in stored if not piece.duplicate]
        unplaced = sorted({number for piece in fresh if piece.slot == "other" for number in piece.pages})
        notes = [note for note in (plan.note,) if note]
        if unplaced:
            notes.append(f"{_pages_text(unplaced).capitalize()} could not be placed with certainty and {'was' if len(unplaced) == 1 else 'were'} "
                         "filed under Other documents: check them.")
        pieces = [{"slot": piece.slot, "pages": list(piece.pages), "fileName": piece.file_name, "alreadyFiled": piece.duplicate} for piece in stored]
        db.add(AuditEntry(actor_id=job.owner_id, action="Sorted a combined file in the background", target=source.file_name,
                          entity_type="candidate", entity_id=candidate.id,
                          meta={"attachment_id": source.id, "document_job_id": job.id, "from_slot": from_slot, "pieces": pieces,
                                **({"note": " ".join(notes)} if notes else {})}))
        _finish(job, "sorted", {"message": splitting.filing_message(stored), "pieces": pieces, **({"note": " ".join(notes)} if notes else {})})
        db.commit()


def process_resort(identifier: str, *, sessions, settings, claimed_by=None) -> None:
    """Run one re-sort job (called by `manual_extraction.process_job`, from the reading worker). Never raises for a file's own trouble."""
    from app.services.manual_extraction import READY, _hand_back, now as claim_time
    with sessions() as db:
        job = db.scalar(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.draft)).where(DocumentExtractionJob.id == identifier).with_for_update())
        if job is None or job.kind != RESORT_KIND or job.status not in READY or (claimed_by is not None and job.claimed_by != claimed_by):
            return
        if claimed_by is None:
            job.status = "processing"
            job.updated_at = job.started_at = claim_time()
        meta = dict((job.draft or {}).get(RESORT_KIND) or {})
        key, digest = job.object_key, job.content_hash
        db.commit()
    candidate_id, attachment_id = meta.get("candidateId"), meta.get("attachmentId")
    try:
        with sessions() as db:
            row = db.get(Attachment, attachment_id) if attachment_id else None
            candidate = db.get(Candidate, candidate_id) if candidate_id else None
            if row is None or candidate is None or row.entity_id != candidate_id or row.content_hash != digest:
                found = None
            else:
                found = SimpleNamespace(slot=row.slot, name=row.file_name or "file", key=row.object_key, kb=row.size_kb or 0)
        if found is None:
            return _complete(sessions, identifier, claimed_by, "file_changed")
        if found.kb > RESORT_MAX_KB:
            return _complete(sessions, identifier, claimed_by, "too_large")
        from app.services.instance_guard import NOT_HERE, stored_here
        if not str(found.key).startswith("s3://") and not stored_here(settings, found.key):
            return _hand_back(identifier, claimed_by, sessions, NOT_HERE)
        try:
            data = read_bytes(settings, found.key)
        except FileNotFoundError:
            return _hand_back(identifier, claimed_by, sessions, NOT_HERE)
        if hashlib.sha256(data).hexdigest() != digest:
            return _complete(sessions, identifier, claimed_by, "file_changed")
        pages = splitting._page_count(data)
        if not pages:
            return _complete(sessions, identifier, claimed_by, "unreadable")
        if pages > RESORT_MAX_PAGES:
            return _complete(sessions, identifier, claimed_by, "too_long")
        if not splitting.worth_analysing(found.slot if found.slot in IDENTITY_SLOTS else None, found.name, "application/pdf", data):
            return _complete(sessions, identifier, claimed_by, "not_a_bundle")
        plan, complete = splitting.sort_pages(found.name, data, document_timeout=RESORT_DOCUMENT_TIMEOUT)
        if not complete:
            return _postpone(sessions, identifier, claimed_by, "The pages could not all be read in time.")
        if plan is None:
            return _complete(sessions, identifier, claimed_by, "one_document")
        _apply(sessions, settings, identifier, claimed_by, candidate_id, attachment_id, digest, plan)
    except Exception as exc:
        log.warning("Re-sort %s failed (%s).", identifier, type(exc).__name__, exc_info=True)
        _postpone(sessions, identifier, claimed_by, f"The file could not be sorted ({type(exc).__name__}).")
