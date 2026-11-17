"""Mail and WhatsApp intake: file every OTHER document of an attachment in its own candidate field.

The online reader gets the resume, passport, Aadhaar and education pages, and the candidate is created from what it returns. A
combined PDF also holds the offer, visa, medical, police clearance, air ticket ... : those pages must not stay only inside the stored
original. This module cuts them out with the Import screen's engine (`bulk_candidate_import.split_by_content`: local reading, bounded
by 40 pages / 8 s a page / 120 s, any failure leaves the file whole) in two steps that mirror the intake worker:

  * `build_split` runs in the worker, outside any database transaction, once per attachment: it cuts the pieces, stores their bytes
    and returns a small JSON-able state (slots, pages, object key, hash) that the worker checkpoints with the rest of the job state.
  * `file_pieces` runs inside the candidate commit: it turns the checkpointed pieces into `Attachment` versions of the candidate,
    named "<Candidate>-<Document>.pdf", with an audit entry carrying the original name and the pages. It writes nothing for a piece
    whose bytes the field already holds, so a retried commit never adds duplicate versions.

Identity pieces (resume, passport, Aadhaar) are not filed here: the reader-derived documents are. A page the reader did not take and
no other piece holds goes to "Other documents", so every page of an attachment is in the original `source_bundle` AND in at least one
field. Nothing in here raises for a document that cannot be split: the result is then no pieces and an info note.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Attachment, AuditEntry, Candidate
from app.integrations.mail.mime import document_mime
from app.services import bulk_candidate_import as bulk
from app.services.application_form import SLOTS
from app.services.document_splitting import NEVER_FILED

IDENTITY = tuple(bulk.READ_SLOTS)   # resume, passport, aadhaar: filed from what the reader was given, never from a piece
_IMAGE_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tiff", "image/webp": ".webp", "image/gif": ".gif",
                     "image/bmp": ".bmp"}


def slots_for(kinds: Iterable[str]) -> list[str]:
    """The candidate fields for the non-identity kinds of a piece. A kind that has no field, or whose field cannot take a file cut
    from a scan (photo, invoice, the original bundle), goes to "Other documents"."""
    slots: list[str] = []
    for kind in kinds:
        if kind in IDENTITY:
            continue
        slot = kind if kind in SLOTS and kind not in NEVER_FILED else "other"
        if slot not in slots:
            slots.append(slot)
    return slots


def _page_count(data: bytes) -> int:
    import pymupdf as fitz
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.page_count


def _short(name: str) -> str:
    return (name or "The file").replace("\\", "/").rsplit("/", 1)[-1] or "The file"


def reading_scope():
    """Open around the first reading of the attachments (`extract_attachment`): the local reading of each file is kept in memory,
    by the SHA-256 of its bytes, so that `build_split` can cut the file by content without running OCR on its pages a second time.
    Yields the dict the readings are collected in. It holds page text, so it is never saved and ends with the job run."""
    from app.integrations.ocr.inspection import capture_inspections
    return capture_inspections()


_KEEP_READINGS = 8
_KEEP_READING_BYTES = 96 * 1024 * 1024


def keep_readings(kept: dict, fresh: dict) -> None:
    """Add the readings of `fresh` to `kept` (the run's readings) while their files stay within a bounded amount of memory. A reading
    left out costs nothing but speed: `build_split` then uses the saved page kinds (see `remember_kinds`)."""
    held = sum(len(getattr(item, "_pdf", b"")) for item in kept.values())
    for digest, item in fresh.items():
        size = len(getattr(item, "_pdf", b""))
        if digest not in kept and len(kept) < _KEEP_READINGS and held + size <= _KEEP_READING_BYTES:
            kept[digest] = item
            held += size


def remember_kinds(document_state: dict, digest: str, readings: dict) -> bool:
    """Save, in the attachment's job state, what the content classifier made of each page of the local reading in `readings`
    (page number -> kind, confidence, flags): no page text. A retry in another process, which holds no reading in memory, cuts the
    file from this and from the saved inspection instead of reading the pages again. False when nothing was read this run."""
    reading = readings.get(digest)
    if reading is None:
        return False
    try:
        guesses = bulk._content_kinds(reading)
    except Exception:
        return False
    document_state["page_kinds"] = {"sha256": digest, "page_count": reading.page_count, "pages": {
        str(number): {"kind": guess.kind, "confidence": guess.confidence, "reason": guess.reason, "inherited": guess.inherited,
                      "overrides_identity": guess.overrides_identity, "alongside_identity": guess.alongside_identity}
        for number, guess in guesses.items()}}
    return True


def _saved_kinds(saved, digest: str) -> dict | None:
    """The kinds `remember_kinds` saved for these bytes, as {page number: KindGuess}; None when absent, for other bytes or damaged."""
    from app.integrations.ocr.kind_classifier import KindGuess
    try:
        if not isinstance(saved, dict) or saved.get("sha256") != digest:
            return None
        return {int(number): KindGuess(**{**entry, "confidence": float(entry["confidence"])}) for number, entry in saved["pages"].items()}
    except Exception:
        return None


def _reading_of(data: bytes, digest: str, reading, report, saved_kinds):
    """The local reading to cut `data` from without OCR: the one made in this run, else the saved inspection (statuses and kinds,
    no text) restored with the saved page kinds; None when neither is usable (the pages are then read, as before)."""
    if reading is not None and getattr(reading, "page_count", 0) >= 1:
        return reading
    kinds = _saved_kinds(saved_kinds, digest)
    if kinds is None or not isinstance(report, dict):
        return None
    try:
        from app.integrations.ocr.inspection import restore_inspection
        restored = restore_inspection(data, report, allow_partial=True)
        if restored.page_count != saved_kinds.get("page_count"):
            return None
    except Exception:
        return None
    restored.content_kinds = kinds   # read by bulk._content_kinds instead of classifying the (absent) page text
    return restored


def _plan_pdf(data: bytes, covered: set[int], extracted: bool, original_name: str, reading=None):
    """(planned pieces, note). A planned piece is (slots, pages, bytes). `reading` is a local reading of these bytes (see
    `_reading_of`): when it cannot be used the engine reads the pages itself."""
    count = _page_count(data)
    if count < 2:
        return [], None
    result = None
    if reading is not None:
        try:
            result = bulk._split_inspected(reading, data, [], original_name)
        except Exception:
            result = None
    if result is None:
        result = bulk.split_by_content(data, "application/pdf", [], original_name)
    everything = list(range(1, count + 1))
    if not result.split:
        if result.note:   # could not be read in time (or at all): the whole file stays in the original bundle, said on the job
            return [], result.note
        # One document throughout: the whole file, in the field its content says.
        pieces = [(piece.kinds, everything, data) for piece in result.pieces]
    else:
        pieces = [(piece.kinds, list(piece.pages), piece.data) for piece in result.pieces]
    planned = []
    for kinds, pages, content in pieces:
        slots = slots_for(kinds)
        # Nothing was recognised in a file the reader has already taken pages of: those pages are the reader's (below).
        if slots == ["other"] and not result.split and extracted:
            continue
        if slots and pages:
            planned.append((slots, pages, content))
    # Page accounting: a page that is in no field piece and that the reader did not take (an unread or unclassified page, a duplicate
    # passport page the reader left out) is filed in Other documents, so no page of the attachment is only in the original.
    filed = set(covered).union(*(set(pages) for _, pages, _ in planned)) if planned else set(covered)
    remaining = [number for number in everything if number not in filed]
    if remaining:
        # One "Other documents" file: the engine's own leftover pages and the ones the reader did not take, in page order.
        pages = sorted(set(remaining).union(*(set(p) for s, p, _ in planned if s == ["other"])))
        planned = [item for item in planned if item[0] != ["other"]]
        planned.append((["other"], pages, data if pages == everything else bulk.cut_pdf_pages(data, pages)))
    return planned, None


def _plan_image(data: bytes, mime: str, reading=None):
    """An image is one page: kept whole, in the field its content says (nothing is filed when the content is not recognised).
    `reading` is a local reading of these bytes (see `_reading_of`); without one the page is read here."""
    from app.integrations.ocr.kind_classifier import classify_kinds
    kind = None
    if reading is not None and reading.page_count == 1:
        content = getattr(reading, "content_kinds", None)   # a restored reading: the kind saved, the text is not kept
        if isinstance(content, dict):
            kind = content[1].kind if 1 in content else None
        else:
            page = reading.pages[0]
            kind = classify_kinds([page.text if page.status == "read" else ""])[0].kind
    else:
        from app.integrations.ocr.inspection import inspect_document
        settings = bulk.get_settings()
        inspected = inspect_document(
            data, tesseract_cmd=getattr(settings, "tesseract_cmd", None), languages=getattr(settings, "ocr_languages", "eng"),
            max_pages=1, page_timeout=8, document_timeout=30)
        page = inspected.pages[0]
        kind = classify_kinds([page.text if page.status == "read" else ""])[0].kind
    slots = slots_for([kind]) if kind else []
    return ([(slots, [1], data)] if slots else []), None


def build_split(data: bytes, store: Callable[[int, bytes], str], *, covered: Iterable[int] = (), extracted: bool = False,
                original_name: str = "", readings: dict | None = None, report: dict | None = None,
                page_kinds: dict | None = None) -> dict:
    """Cut one attachment into the documents it holds and store the bytes of the ones that are not identity documents.

    `covered` are the pages the reader was given (the resume / passport / Aadhaar pages already filed from the reader's own
    documents); `extracted` says the reader took something from this attachment; `store(index, bytes)` writes one piece and returns
    its object key. Never raises for a document that cannot be read: it returns no pieces and a note. A storage failure does raise
    (the intake retries, as for the reader's own documents).

    The pages are not read again when the first reading is at hand: `readings` (what `reading_scope` collected in this run), or
    else `report` (the saved inspection) with `page_kinds` (`remember_kinds`). With neither, the pages are read here, as before."""
    digest = hashlib.sha256(data).hexdigest()
    mime = document_mime(data)
    try:
        reading = _reading_of(data, digest, (readings or {}).get(digest), report, page_kinds) \
            if mime == "application/pdf" or mime in _IMAGE_EXTENSIONS else None
        if mime == "application/pdf":
            planned, note = _plan_pdf(data, {n for n in covered if type(n) is int}, extracted, original_name, reading)
        elif mime in _IMAGE_EXTENSIONS:
            planned, note = _plan_image(data, mime, reading)
        else:
            planned, note = [], None   # Word and anything else: one document, kept whole in the original bundle
    except Exception:
        planned, note = [], f"{_short(original_name)} could not be split into its documents; it was stored whole."
    pieces = []
    for index, (slots, pages, content) in enumerate(planned):
        pieces.append({
            "slots": slots, "pages": pages, "object_key": store(index, content), "content_hash": hashlib.sha256(content).hexdigest(),
            "size_kb": (len(content) + 1023) // 1024, "content_type": mime if mime in _IMAGE_EXTENSIONS else "application/pdf",
            "extension": _IMAGE_EXTENSIONS.get(mime, ".pdf"),
        })
    return {"source_sha256": digest, "original_name": _short(original_name), "pieces": pieces, "note": note}


def usable(state, digest: str, read: Callable[[str], bytes]) -> bool:
    """A checkpointed split can be reused for these exact bytes when every stored piece still reads back unchanged."""
    if not isinstance(state, dict) or state.get("source_sha256") != digest or not isinstance(state.get("pieces"), list):
        return False
    try:
        return all(hashlib.sha256(read(piece["object_key"])).hexdigest() == piece["content_hash"] for piece in state["pieces"])
    except Exception:
        return False


def _person(candidate: Candidate) -> str:
    name = (candidate.name or "").strip()
    return name if name and name.casefold() not in {"unnamed", "unnamed candidate", "candidate"} else "Candidate"


def file_pieces(db: Session, candidate: Candidate, splits: list[dict], *, origin: dict) -> list[dict]:
    """Attach the checkpointed pieces to the candidate, each as the next version of its field. In the caller's transaction.

    A piece whose bytes the field already holds (any version) is skipped, so a retried commit or the same file attached twice adds
    nothing. `origin` goes into every audit entry (for example {"email_message_id": ...}). Returns what was added."""
    from app.api.routers.files import _safe_name
    person, taken, added = _person(candidate), set(), []
    for split in splits:
        for piece in split.get("pieces", []):
            file_name = None
            for slot in piece["slots"]:
                if slot not in SLOTS or slot in NEVER_FILED:
                    slot = "other"
                if db.scalar(select(Attachment.id).where(
                    Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                    Attachment.slot == slot, Attachment.content_hash == piece["content_hash"],
                ).limit(1)):
                    continue
                if file_name is None:
                    file_name = _safe_name(bulk.import_file_name(person, piece["slots"], f"piece{piece.get('extension', '.pdf')}", taken))
                version = max(db.scalars(select(Attachment.version).where(
                    Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == slot)), default=0) + 1
                row = Attachment(entity_type="candidate", entity_id=candidate.id, slot=slot, version=version, file_name=file_name,
                                 size_kb=piece.get("size_kb"), content_type=piece.get("content_type", "application/pdf"),
                                 object_key=piece["object_key"], content_hash=piece["content_hash"])
                db.add(row)
                db.flush()
                db.add(AuditEntry(action="Uploaded document", target=file_name, entity_type="candidate", entity_id=candidate.id,
                                  meta={"attachment_id": row.id, "slot": slot, "version": version, "original_name": split.get("original_name"),
                                        "split_pages": piece["pages"], **origin}))
                added.append({"slot": slot, "pages": piece["pages"], "file_name": file_name})
    return added
