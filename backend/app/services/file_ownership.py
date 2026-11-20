"""A file is never ingested twice: the same bytes are not filed again, and a personal document never belongs to two candidates.

The one question every upload path asks is "where are these exact bytes (sha256) already filed?". It is answered with ONE indexed
query on `document_attachments.content_hash` (the column has its own index), never by scanning a candidate's files. The answer
depends on the document field:

  * same candidate, same field (any earlier version)  -> nothing is stored; the screen says "This file is already uploaded (version N)".
  * same candidate, another field                      -> allowed (a combined scan is kept in its own fields); a quiet note says so.
  * another candidate, PERSONAL field                  -> refused (HTTP 409) with the other candidate's Care India ID and name; nothing
                                                          is stored and the only trace is one audit entry "Refused duplicate file".
  * another candidate, SHARED field (group ticket ...) -> allowed, with a quiet note.

Personal fields hold a document that belongs to one person (resume, passport, Aadhaar, medical, PCC, certificates, photo, application
form, visa stamping, other, and the kept original scan). Shared fields are the ones an agency files ONE document for a whole group in:
the group air ticket, group visa and offer letters, the undertaking letter, emigration, insurance, the invoice and the Google review.
(The folder import follows the same idea: one shared group ticket is never enough to call a folder a duplicate.)

Two uploads of the same bytes at the same moment cannot both be stored for different candidates: personal uploads take a transaction
advisory lock keyed on the file's hash before they look (one cheap statement; it is released at commit or rollback).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Attachment, AuditEntry, Candidate, User

SHARED_SLOTS = frozenset({"signed_offer", "visa", "air_ticket", "undertaking_letter", "emigration", "insurance", "invoice", "google_review"})
PERSONAL_SLOTS = frozenset({
    "application_form", "resume", "passport", "aadhaar", "medical", "pcc", "photo", "education_certificate", "experience_certificate",
    "visa_stamping", "other", "source_bundle", "education", "experience",       # the last two: slot names stored before the form had its own
})


def is_personal(slot: str | None) -> bool:
    return slot in PERSONAL_SLOTS


def candidate_label(candidate: Candidate) -> str:
    """"CI-139 · Asha Nair": the Care India ID (the reference while it has none) and the name. Never an internal id."""
    code = candidate.care_id or candidate.ref or "a candidate"
    name = (candidate.name or "").strip()
    return f"{code} · {name}" if name else code


def refusal_message(candidate: Candidate) -> str:
    return f"This file is already filed for {candidate_label(candidate)}. A file cannot belong to two candidates. Open that candidate, or delete it first."


def _slot_label(slot: str) -> str:
    from app.services.application_form import SLOT_LABELS
    return {**SLOT_LABELS, "source_bundle": "the original upload"}.get(slot, slot)


@dataclass
class Findings:
    same_slot: Attachment | None = None                       # the newest earlier version of this field holding these bytes
    elsewhere: Candidate | None = None                        # another candidate that holds these bytes in a PERSONAL field
    elsewhere_row: Attachment | None = None
    notes: list[str] = field(default_factory=list)           # quiet information for the screen; never blocks


def holders(db: Session, digests) -> dict[str, list[tuple[Attachment, Candidate]]]:
    """Every candidate file with one of these hashes, as (file, its candidate): ONE query on the hash index. A deleted candidate has
    no row to join, so its files never count."""
    wanted = {digest for digest in digests if digest}
    found: dict[str, list[tuple[Attachment, Candidate]]] = {}
    if not wanted:
        return found
    for row, candidate in db.execute(
        select(Attachment, Candidate).join(Candidate, Candidate.id == Attachment.entity_id)
        .where(Attachment.content_hash.in_(wanted), Attachment.entity_type == "candidate", Attachment.deleted_at.is_(None))
        .order_by(Attachment.uploaded_at, Attachment.id)
    ).all():
        found.setdefault(row.content_hash, []).append((row, candidate))
    return found


def lock_hash(db: Session, digest: str) -> None:
    """Serialize uploads of the same bytes: held until the caller's transaction ends."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:digest, 0))"), {"digest": digest})


def judge(held: list[tuple[Attachment, Candidate]], candidate_id: str, slot: str) -> Findings:
    """Apply the rules to the files that hold these bytes (see the module text)."""
    found = Findings()
    seen_notes: set[str] = set()
    for row, owner in held:
        if owner.id == candidate_id:
            if row.slot == slot:
                if found.same_slot is None or row.version > found.same_slot.version:
                    found.same_slot = row
            elif row.slot != "source_bundle":
                note = f"This file is also filed under {_slot_label(row.slot)}."
                if note not in seen_notes:
                    seen_notes.add(note)
                    found.notes.append(note)
            continue
        if is_personal(slot) and is_personal(row.slot):
            if found.elsewhere is None:
                found.elsewhere, found.elsewhere_row = owner, row
            continue
        note = f"This file is also filed for {candidate_label(owner)}. Shared documents such as a group ticket can belong to several candidates."
        if note not in seen_notes:
            seen_notes.add(note)
            found.notes.append(note)
    return found


def refuse(db: Session, user: User, candidate: Candidate, slot: str, file_name: str, findings: Findings) -> None:
    """Keep the one audit entry, commit it, and answer 409. Nothing else was written, so there is nothing to undo."""
    other = findings.elsewhere
    db.add(AuditEntry(actor_id=user.id, action="Refused duplicate file", target=(file_name or "file")[:300], entity_type="candidate",
                      entity_id=candidate.id, meta={"slot": slot, "filedForCandidateId": other.id, "filedForCareId": other.care_id or other.ref,
                                                    "attachmentId": findings.elsewhere_row.id if findings.elsewhere_row is not None else None}))
    db.commit()
    raise HTTPException(409, refusal_message(other))


def check_file(db: Session, user: User, candidate: Candidate, slot: str, digest: str, file_name: str, *, lock: bool = True) -> Findings:
    """One whole file about to be stored in `slot` of `candidate`. Raises 409 for a personal file that is another candidate's."""
    if lock and is_personal(slot):
        lock_hash(db, digest)
    findings = judge(holders(db, [digest]).get(digest, []), candidate.id, slot)
    if findings.elsewhere is not None and findings.same_slot is None:     # already filed here: say so, whatever an older mix-up left elsewhere
        refuse(db, user, candidate, slot, file_name, findings)
    return findings


def check_pieces(db: Session, user: User, candidate: Candidate, pieces: list[tuple[str, str]], file_name: str) -> None:
    """The parts cut from a scan, as (hash, field): a part that is a personal document of another candidate refuses the whole upload.
    (Parts already filed for THIS candidate are the page-level `already filed` logic of the splitter, not handled here.)"""
    held = holders(db, [digest for digest, _ in pieces])
    for digest, slot in pieces:
        findings = judge(held.get(digest, []), candidate.id, slot)
        if findings.elsewhere is not None:
            refuse(db, user, candidate, slot, file_name, findings)


def hashes_held_elsewhere(db: Session, digests, *, own_candidate_id: str | None = None, own_object_keys=()) -> dict[str, tuple[str, str]]:
    """For the intake paths: hash -> (candidate id, "CI-139 · Name") for each of these hashes that sits in a PERSONAL field of some
    candidate. Files stored from the very same receipt (same stored original) and the candidate the receipt already belongs to are
    not counted: a retry never refuses itself."""
    answer: dict[str, tuple[str, str]] = {}
    skip = {key for key in own_object_keys if key}
    for digest, pairs in holders(db, digests).items():
        for row, owner in pairs:
            if not is_personal(row.slot) or row.object_key in skip or owner.id == own_candidate_id:
                continue
            answer.setdefault(digest, (owner.id, candidate_label(owner)))
    return answer
