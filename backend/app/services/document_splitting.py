"""One scan, several documents: file every page in the candidate document field it belongs to, whichever way it came in.

The engine is `bulk_candidate_import.split_by_content` (it reads each page locally, never loses a page, is bounded in cost and
falls back to the whole file on any failure). This module only wires it into the upload paths of a CANDIDATE's documents:

  * `files.upload` (the profile's Add files / Replace and every application-form slot upload) calls `plan_split` before it
    takes any lock and `store_pieces` inside its own transaction.
  * the Add-candidate intake calls `store_split_upload` when the saved upload is attached to the candidate.

A slot chosen by the person counts as the name of the file: a combined scan put in the Passport slot is analysed, and when it
turns out to be passport pages only it is stored exactly as it came, in that slot (`plan_split` returns None).
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Attachment, AuditEntry, Candidate, User
from app.services import bulk_candidate_import as bulk
from app.services.application_form import SLOTS

READ_SLOTS = bulk.SENT_TO_READER   # the slots sent to the reader (an upload to one of them can wait for "Extract details")
# Never the target of a piece cut from a scan: a photo is an image, an invoice is the admin's own, and the original upload is kept as received.
NEVER_FILED = ("photo", "invoice", "source_bundle")
# The slots that keep every file (front/back, several pages): an identical file already in one of them is not stored twice.
MULTI_SLOTS = ("passport", "aadhaar", "other")
# A file with one page cannot hold several documents.
MIN_PAGES = 2


@dataclass
class PlannedPiece:
    slots: list[str]          # the candidate document fields this piece belongs in (several when one page is two documents)
    data: bytes
    pages: list[int]          # numbered in the original scan
    facts: list | None = None  # what the reading of the scan found on each of these pages (duplicate_pages.PageRead), if known


@dataclass
class SplitPlan:
    pieces: list[PlannedPiece]
    note: str | None = None


@dataclass
class StoredPiece:
    slot: str
    label: str
    file_name: str
    pages: list[int]
    attachment: Attachment = field(repr=False)
    duplicate: bool = False   # the very same file, or the very same pages, were already filed in this slot: nothing new was stored

    def as_dict(self) -> dict:
        return {"slot": self.slot, "label": self.label, "fileName": self.file_name, "pages": self.pages,
                "id": self.attachment.id, "url": f"/api/files/{self.attachment.id}", "version": self.attachment.version,
                **({"alreadyFiled": True} if self.duplicate else {})}


def label_of(slot: str) -> str:
    return bulk.KIND_LABELS.get(slot, slot)


def _page_count(data: bytes) -> int:
    try:
        import pymupdf as fitz
        with fitz.open(stream=data, filetype="pdf") as document:
            return document.page_count
    except Exception:
        return 0


def eligible(slot: str | None, content_type: str, data: bytes) -> bool:
    """A PDF of two pages or more going into a document field that can hold a scan. Images and Word files are one document."""
    if content_type != "application/pdf" or (slot is not None and (slot not in SLOTS or slot in NEVER_FILED)):
        return False
    return _page_count(data) >= MIN_PAGES


# A normal front + back scan of an identity card: two pages filed in the passport / Aadhaar field are not worth a page-by-page read.
PAIR_SLOTS = ("passport", "aadhaar")


def worth_analysing(slot: str | None, filename: str, content_type: str, data: bytes) -> bool:
    """`eligible`, and cheap enough to be worth the (slow, local) page-by-page reading.

    A two-page PDF put in the Passport or Aadhaar field is an ordinary front + back scan: it is stored whole, as it came, unless
    its file name says it holds several kinds of document ("passport and aadhaar.pdf"). Three pages or more, a nameless file, or a
    file in any other field is always read. (A PDF with a text layer is read without OCR, so it needs no pre-check: the cost this
    guards against is the OCR of scanned pages.)"""
    if not eligible(slot, content_type, data):
        return False
    if slot in PAIR_SLOTS and _page_count(data) == MIN_PAGES and len(bulk.classify_path_kinds(filename or "")) < 2:
        return False
    return True


def costs_ocr(data: bytes) -> bool:
    """Whether sorting the pages of this PDF means running OCR on at least one of them (a scan, a photographed page, a text layer that
    is damaged): the slow part, minutes on a busy machine. A PDF whose every page carries a real text layer is read in seconds. Used to
    keep the OCR out of web requests (see `manual_extraction._plan_split_of_original`). True when it cannot be told."""
    try:
        import pymupdf as fitz
        from app.integrations.ocr.inspection import _usable_native
        with fitz.open(stream=data, filetype="pdf") as document:
            return any(not _usable_native(page.get_text("text", sort=True).strip()) for page in document)
    except Exception:
        return True


def _targets(kinds: list[str], claim: str | None, found: set[str]) -> list[str]:
    """The fields for one piece. Pages the reader could not place ("other") stay in the field the person chose when that field
    was not found anywhere else in the scan; otherwise they go to Other documents. Nothing is ever filed in a field that
    cannot take a PDF cut from a scan."""
    slots: list[str] = []
    for kind in kinds:
        slot = kind
        if kind == "other" and claim and claim not in found and claim not in NEVER_FILED and claim in SLOTS:
            slot = claim
        if slot not in SLOTS or slot in NEVER_FILED:
            slot = "other"
        if slot not in slots:
            slots.append(slot)
    return slots


def _plan_of(result, slot: str | None) -> SplitPlan | None:
    """The plan for what `split_by_content` found: the pieces to file in their own fields, or None when the scan is one document."""
    if not result.split:
        return None
    found = {kind for piece in result.pieces for kind in piece.kinds if kind != "other"}
    pieces = [PlannedPiece(_targets(piece.kinds, slot, found), piece.data, list(piece.pages), result.reads_for(piece.pages))
              for piece in result.pieces]
    if len(pieces) < 2 or any(not piece.pages for piece in pieces):
        return None   # a single piece is the whole scan: stored whole, byte for byte
    return SplitPlan(pieces, result.note)


def plan_split(slot: str | None, filename: str, data: bytes, content_type: str = "application/pdf") -> SplitPlan | None:
    """Analyse an upload. Returns the pieces to file in their own fields, or None when the file is to be stored whole in the
    field the person chose (one document throughout, not a PDF, too long, or the reading failed). Never raises; the cost is
    bounded by `split_by_content` (40 pages, 8 s a page, 120 s)."""
    if not worth_analysing(slot, filename, content_type, data):
        return None
    try:
        return _plan_of(bulk.split_by_content(data, content_type, [], filename), slot)
    except Exception:
        return None


def sort_pages(filename: str, data: bytes, *, document_timeout: float | None = None) -> tuple[SplitPlan | None, bool]:
    """`plan_split` for a file that is already stored, with the one thing a background re-sort needs to know besides the plan: whether
    the reading was COMPLETE. Returns (plan or None, complete). `complete` is False when the pages could not all be read in the time
    given (or the reading failed): the plan, or its absence, is then not final and nothing should be marked as sorted. Never raises.
    Always a fresh reading of the pages (sideways scans are turned upright by the reader): an old saved reading is never trusted here."""
    try:
        if document_timeout:
            result = bulk.split_by_content(data, "application/pdf", [], filename, document_timeout=document_timeout)
        else:
            result = bulk.split_by_content(data, "application/pdf", [], filename)
        complete = not (getattr(result, "cut_short", False) or getattr(result, "failed", False))
        return _plan_of(result, None), complete
    except Exception:
        return None, False


def _read_again(data: bytes, readings, report, saved_kinds):
    """The local reading of `data` without OCR: the one made in this run (`readings`, by SHA-256), else the saved inspection with the
    page kinds saved beside it (`intake_documents.remember_kinds`). None when neither is usable."""
    from app.services import intake_documents
    digest = hashlib.sha256(data).hexdigest()
    return intake_documents._reading_of(data, digest, (readings or {}).get(digest), report, saved_kinds)


def kinds_found(data: bytes, *, readings: dict | None = None, report: dict | None = None, saved_kinds: dict | None = None,
                name: str = "") -> dict[str, list[int]] | None:
    """Which pages of a read file are which document, for every kind the content classifier found: {field: [page numbers]},
    the fields in the order of their first page (a page that is two documents is listed under both). The fields are the ones the
    pages are filed in (a kind with no field of its own, or whose field cannot take a cut PDF, is "other"). Nothing is read: it is
    the reading the job already made (see `_read_again`). None when no reading is at hand; {} when nothing was recognised. Never raises."""
    try:
        reading = _read_again(data, readings, report, saved_kinds)
        if reading is None:
            return None
        result = bulk._split_inspected(reading, data, [], name)
        everything = list(range(1, reading.page_count + 1))
        found: dict[str, set[int]] = {}
        for piece in result.pieces:
            for slot in _targets(piece.kinds, None, set()):
                found.setdefault(slot, set()).update(piece.pages or everything)
        if set(found) <= {"other"}:
            return {}
        return {slot: sorted(pages) for slot, pages in sorted(found.items(), key=lambda item: min(item[1]))}
    except Exception:
        return None


def plan_split_saved(slot: str | None, filename: str, data: bytes, report: dict | None, saved_kinds: dict | None,
                     content_type: str = "application/pdf") -> tuple[bool, SplitPlan | None]:
    """`plan_split` from the reading a job already made (no OCR, nothing slow): (True, the plan or None) when that reading is at hand,
    (False, None) when it is not and the caller has to read the pages itself. Never raises."""
    if not worth_analysing(slot, filename, content_type, data):
        return False, None
    try:
        reading = _read_again(data, None, report, saved_kinds)
        if reading is None:
            return False, None
        result = bulk._split_inspected(reading, data, [], filename)
        result.page_reads = {}   # a restored reading holds no page text: the duplicate check reads the few pages it needs itself
        return True, _plan_of(result, slot)
    except Exception:
        return False, None


# ------------------------------------------------------------------ the other documents of a stored, read file

# The fields the reader fills (and `_attach_read_documents` files from what the reader was given) and "Other documents" (the pages
# the reader left, filed as one file by the reader's own cut): none of them is ever filed from a piece of the stored file's reading.
READER_FILED = ("resume", "passport", "aadhaar", "other")


def own_field_pages(kinds: dict[str, list[int]] | None) -> set[int]:
    """The pages of a read file that the content classifier gives a field of their own (a medical report, an experience certificate, a
    signed offer ...), from `kinds_found`: every field but the ones the reader files itself. Those pages are filed by
    `file_other_documents`, so the reader's "Other documents" file leaves them out. Empty when nothing is known."""
    return {number for slot, pages in (kinds or {}).items() if slot not in READER_FILED for number in pages}


def plan_other_documents(filename: str, data: bytes, report: dict | None, saved_kinds: dict | None) -> tuple[bool, SplitPlan | None]:
    """(known, plan) for a file that was already read: its saved inspection and page kinds are used, nothing is read again. `known`
    is False when that reading is missing or damaged (nothing can be said without reading the pages); the plan is None when the file
    holds no document for a field other than the reader's. Never raises."""
    known, plan = plan_split_saved(None, filename, data, report, saved_kinds)
    if not known or plan is None:
        return known, None
    pieces = [piece for piece in plan.pieces if any(slot not in READER_FILED for slot in piece.slots)]
    return True, (SplitPlan(pieces, plan.note) if pieces else None)


def inherited_pages(saved_kinds: dict | None, pages: set[int]) -> list[int]:
    """The pages (among `pages`) whose kind was only taken from the page before them: the person is told to check these."""
    saved = (saved_kinds or {}).get("pages") if isinstance(saved_kinds, dict) else None
    if not isinstance(saved, dict):
        return []
    return sorted(number for number in pages if isinstance(saved.get(str(number)), dict) and saved[str(number)].get("inherited"))


def _person(candidate: Candidate, original_name: str) -> str:
    """The name the pieces carry: the candidate's; a candidate without a name yet keeps the file's own name."""
    name = (candidate.name or "").strip()
    if name and name.casefold() not in {"unnamed", "unnamed candidate", "candidate"}:
        return name
    stem = (original_name or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    return stem or "Candidate"


def derived_file_name(candidate: Candidate | None, slot: str, taken: set[str], original_name: str = "") -> str:
    """The name of a file the reader derived from a scan (the pages of one kind): "<Candidate>-<Document>.pdf", like every other
    stored file, never the generic "resume.pdf". `taken` keeps the names of one filing apart."""
    from app.api.routers.files import _safe_name
    person = _person(candidate, original_name) if candidate is not None else "Candidate"
    return _safe_name(bulk.import_file_name(person, [slot], "derived.pdf", taken))


def _already_filed(piece: PlannedPiece, slot: str, versions: list[Attachment], settings) -> Attachment | None:
    """The file of this field that already shows every page of the piece (the same physical pages, even scanned again or cut
    differently), or None when any page is new: a piece that truly differs is stored as before. Fields that keep every file
    compare with all of them, the others with the current file. Never raises: a file that cannot be read takes no part."""
    from app.services import document_storage, duplicate_pages as dup
    held = versions if slot in MULTI_SLOTS else versions[:1]
    if not held or slot in ("photo", "invoice", "source_bundle"):
        return None
    try:
        count = dup.page_count(piece.data, "application/pdf")
        if not count:
            return None
        facts = piece.facts if piece.facts and len(piece.facts) == count else [None] * count
        new = dup.Doc("new piece", piece.data, "application/pdf", list(piece.pages) if len(piece.pages) == count else list(range(1, count + 1)),
                      facts, standalone=False)
        deadline = time.monotonic() + dup.READ_TIME_BUDGET
        if not dup.read_pages(new, settings=settings, deadline=deadline):
            return None
        files: list[tuple[Attachment, "dup.Doc"]] = []
        for row in held:
            try:
                data = document_storage.read_bytes(settings, row.object_key)
            except Exception:
                continue
            content_type = row.content_type or "application/pdf"
            pages = dup.page_count(data, content_type)
            if not pages:
                continue
            doc = dup.Doc(row.file_name, data, content_type, list(range(1, pages + 1)), [None] * pages, standalone=True)
            if dup.read_pages(doc, settings=settings, deadline=deadline):
                files.append((row, doc))
        found = dup.covered(new, [doc for _, doc in files], slot)
        return files[found[0].of_doc][0] if found else None
    except Exception:
        return None


def store_pieces(db: Session, user: User, candidate: Candidate, plan: SplitPlan, original_name: str, *, settings, written: list[str],
                 deferred: bool = False, skip_slots: frozenset[str] | set[str] = frozenset(), audit_extra: dict | None = None,
                 uploaded_at: datetime | None = None) -> list[StoredPiece]:
    """Write the pieces as new versions of their fields, in the caller's transaction (the caller holds the candidate's lock and
    commits, or rolls back and discards `written`). One file is written per piece even when it fills two fields. A piece
    identical to the field's current file is not stored again. Fields in `skip_slots` are left alone."""
    from app.api.routers.files import _safe_name
    now = uploaded_at or datetime.now(timezone.utc)
    taken: set[str] = set()   # names are only kept apart within this upload; a later version of a field keeps the standard name
    person = _person(candidate, original_name)
    original = (original_name or "").replace("\\", "/").rsplit("/", 1)[-1][:200]
    stored: list[StoredPiece] = []
    for piece in plan.pieces:
        slots = [slot for slot in piece.slots if slot not in skip_slots]
        if not slots:
            continue
        digest = hashlib.sha256(piece.data).hexdigest()
        file_name = _safe_name(bulk.import_file_name(person, slots, "piece.pdf", taken))
        object_key: str | None = None
        for slot in slots:
            versions = db.scalars(select(Attachment).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == slot)
                .order_by(Attachment.version.desc())).all()
            live = [row for row in versions if row.deleted_at is None]   # a removed file is not "already filed": a fresh upload is stored
            same = next((row for row in live if row.content_hash == digest and (slot in MULTI_SLOTS or row is live[0])), None)
            if same is None:
                same = _already_filed(piece, slot, live, settings)
            if same is not None:
                stored.append(StoredPiece(slot, label_of(slot), same.file_name, piece.pages, same, duplicate=True))
                continue
            if object_key is None:
                object_key, _ = bulk._store_bytes(settings, piece.data, file_name, "application/pdf", written)
            row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=slot,
                             version=(versions[0].version if versions else 0) + 1, file_name=file_name,
                             size_kb=max(1, round(len(piece.data) / 1024)), content_type="application/pdf", object_key=object_key,
                             content_hash=digest, uploaded_by=user.id, uploaded_at=now)
            db.add(row)
            db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=file_name, entity_type="candidate", entity_id=candidate.id,
                              meta={"attachment_id": row.id, "slot": slot, "version": row.version,
                                    "original_name": original, "split_pages": piece.pages, **(audit_extra or {}),
                                    **({"extraction_deferred": True} if deferred and slot in READ_SLOTS else {})}, at=now))
            db.flush()
            stored.append(StoredPiece(slot, label_of(slot), file_name, piece.pages, row))
    return stored


def store_source_bundle(db: Session, user: User, candidate: Candidate, data: bytes, content_type: str, original_name: str, *, settings,
                        written: list[str], uploaded_at: datetime | None = None) -> Attachment:
    """Keep the whole scan that was just cut into documents, as received, in the candidate's `source_bundle` field (the same field
    an email or manually attached scan is kept in), so nothing is lost and the pieces can be cut again. The same bytes already kept
    are not stored twice. In the caller's transaction, like `store_pieces`."""
    from app.api.routers.files import _safe_name
    now = uploaded_at or datetime.now(timezone.utc)
    digest = hashlib.sha256(data).hexdigest()
    versions = db.scalars(select(Attachment).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == "source_bundle")
        .order_by(Attachment.version.desc())).all()
    same = next((row for row in versions if row.content_hash == digest and row.deleted_at is None), None)
    if same is not None:
        return same
    name = _safe_name(original_name or "file")
    object_key, _ = bulk._store_bytes(settings, data, name, content_type, written)
    row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot="source_bundle",
                     version=(versions[0].version if versions else 0) + 1, file_name=name, size_kb=max(1, round(len(data) / 1024)),
                     content_type=content_type, object_key=object_key, content_hash=digest, uploaded_by=user.id, uploaded_at=now)
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=name, entity_type="candidate", entity_id=candidate.id,
                      meta={"attachment_id": row.id, "slot": "source_bundle", "version": row.version, "kept_original": True,
                            "original_name": (original_name or "").replace("\\", "/").rsplit("/", 1)[-1][:200]}, at=now))
    db.flush()
    return row


def filing_message(pieces: list[StoredPiece]) -> str | None:
    """"This file held several documents: Passport (pages 2-3) and Resume (pages 1, 8) were filed in their own fields." """
    groups: dict[tuple[int, ...], list[str]] = {}
    already: list[str] = []
    for piece in pieces:
        if piece.duplicate and piece.pages:
            already.append(f"{piece.label} ({'pages' if len(piece.pages) > 1 else 'page'} {bulk._page_list(list(piece.pages))}) is already filed in "
                           f"{piece.file_name}: not added again")
            continue
        labels = groups.setdefault(tuple(piece.pages), [])
        if piece.label not in labels:
            labels.append(piece.label)
    if not groups and not already:
        return None
    tail = ("; ".join(already) + ".") if already else ""
    if not groups:
        return f"This file held pages that are already filed: {tail}"
    parts = [f"{' + '.join(labels)} ({'pages' if len(pages) > 1 else 'page'} {bulk._page_list(list(pages))})" for pages, labels in groups.items()]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    filed = f"This file held several documents: {joined} {'was' if len(parts) == 1 and len(next(iter(groups.values()))) == 1 else 'were'} filed in their own fields."
    return f"{filed} {tail}".strip()


def report(pieces: list[StoredPiece]) -> dict:
    """What the upload answers with when it split a file (the screens show `message`)."""
    return {"pieces": [piece.as_dict() for piece in pieces], "message": filing_message(pieces)}


def store_split_upload(db: Session, user: User, candidate: Candidate, slot: str, filename: str, data: bytes, content_type: str, *,
                       settings, written: list[str], deferred: bool = False) -> list[StoredPiece]:
    """The one-call form for callers that store a file themselves (no endpoint around it): analyse `data`, file its pieces in
    their own fields, or, when it is one document throughout or cannot be analysed, store it whole in `slot` as it came."""
    from app.api.routers.files import _safe_name
    plan = plan_split(slot, filename, data, content_type)
    if plan is not None:
        return store_pieces(db, user, candidate, plan, filename, settings=settings, written=written, deferred=deferred)
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(data).hexdigest()
    previous = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                                                  Attachment.slot == slot).order_by(Attachment.version.desc()).limit(1))
    name = _safe_name(filename or "file")
    object_key, _ = bulk._store_bytes(settings, data, name, content_type, written)
    row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate.id, slot=slot, version=(previous.version if previous else 0) + 1,
                     file_name=name, size_kb=max(1, round(len(data) / 1024)), content_type=content_type, object_key=object_key,
                     content_hash=digest, uploaded_by=user.id, uploaded_at=now)
    db.add(row)
    db.add(AuditEntry(actor_id=user.id, action="Uploaded document", target=name, entity_type="candidate", entity_id=candidate.id,
                      meta={"attachment_id": row.id, "slot": slot, "version": row.version}, at=now))
    db.flush()
    return [StoredPiece(slot, label_of(slot), name, [], row)]
