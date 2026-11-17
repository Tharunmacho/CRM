"""Clean up the repeated pages an earlier import left in a candidate's document slots (for example CI-167's five Passport files).

    python scripts/dedupe_candidate_documents.py --candidate CI-167            # DRY RUN: shows what it would unlink, changes nothing
    python scripts/dedupe_candidate_documents.py --candidate CI-167 --apply    # does it
    python scripts/dedupe_candidate_documents.py --all                         # every candidate (dry run unless --apply)

The import now keeps ONE copy of every page (app/services/duplicate_pages.py). Candidates imported before that hold the same
physical pages several times in one slot: the passport scan on its own, and the same pages again inside the picker scan, the signed
offer scan, the merged files. This script applies the same page comparison to what is already stored and, per slot, UNLINKS the
document versions that repeat pages of a version it keeps: the standalone scan first, then the one with more pages, then the better
reading, then the name (so the standalone passport stays and the cut copies go).

What it never does:
  * it never deletes a stored FILE: only the slot's `document_attachments` row is removed (the version stops being listed), the file
    stays in storage, and an audit entry "Unlinked duplicate document" records the version, the file name, the storage key, the file's
    hash, the original scan it was cut from and the version that was kept, so it can be put back;
  * it never touches a version that holds a page no other kept version has (a file is unlinked only when EVERY page of it repeats a
    page of a kept file); photos, invoices and the "Source document evidence" scans are never compared;
  * a version a reading (OCR) job points at is kept too: the details read from it are tied to it;
  * it only looks at files the import or the page-splitting filed by itself (their upload audit entry says `bulk_import` or has
    `split_pages`); a file a person uploaded or replaced by hand is never unlinked;
  * stage evidence copies (application rows) are left alone.

The database is whatever the app settings say (backend/.env, or CRM_ENV_FILE) unless --database-url is given. The same guard as
scripts/delete_candidate_permanently.py applies, also to the dry run (it connects and reads): backend/.env is the SSH tunnel to
the SHARED database, so unless CRM_ENV_FILE names another file, or the database is on this computer and not on the tunnel's port,
the script stops before it connects, unless `--allow-remote` is given on purpose.
Exit code 0 = done (or dry run), 1 = candidate not found, 2 = refused by the guard / bad arguments, 3 = failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, or_, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob, Role, User  # noqa: E402
from app.services import document_storage, duplicate_pages as dup  # noqa: E402

TUNNEL_FILE = Path(__file__).resolve().parents[1] / ".db-tunnel.json"
SKIPPED_SLOTS = ("photo", "invoice", "source_bundle")


def out(text: str = "") -> None:
    print(text, flush=True)


def shared_reason(url) -> str | None:
    """Why this database must be treated as the shared one (None for a plain local database)."""
    host = url.host or ""
    if host not in {"127.0.0.1", "localhost", "::1", ""}:
        return f"the database is on '{host}', not on this computer."
    try:
        port = int(json.loads(TUNNEL_FILE.read_text(encoding="utf-8")).get("local_port") or 0) if TUNNEL_FILE.exists() else 0
    except (OSError, ValueError, TypeError, AttributeError):
        port = 0
    if port and url.port == port:
        return f"the database is on port {port}, the SSH tunnel to the shared database ({TUNNEL_FILE.name})."
    return None


def storage_settings():
    return get_settings()


def filing_meta(db: Session, candidate_id: str) -> dict[str, dict]:
    """attachment id -> the meta of its upload audit entry (original scan, pages, whether the import filed it)."""
    found: dict[str, dict] = {}
    for entry in db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate_id,
                                                     AuditEntry.action == "Uploaded document").order_by(AuditEntry.at)):
        meta = entry.meta or {}
        if meta.get("attachment_id"):
            found[str(meta["attachment_id"])] = meta
    return found


def machine_filed(meta: dict | None) -> bool:
    return bool(meta) and bool(meta.get("bulk_import") or meta.get("split_pages"))


def label_of(row: Attachment, meta: dict | None) -> str:
    original = (meta or {}).get("original_name")
    pages = (meta or {}).get("split_pages")
    if original and pages:
        return f"{original}"
    return original or row.file_name


def plan_slot(db: Session, candidate: Candidate, slot: str, rows: list[Attachment], metas: dict[str, dict], settings, deadline: float):
    """(documents, repeats to unlink): `rows` are the versions of one slot, newest first. Nothing is written."""
    docs: list[dup.Doc] = []
    kept_rows: list[Attachment] = []
    for row in rows:
        meta = metas.get(row.id)
        if not machine_filed(meta):
            continue
        try:
            data = document_storage.read_bytes(settings, row.object_key)
        except Exception:
            continue
        content_type = row.content_type or "application/pdf"
        count = dup.page_count(data, content_type)
        if not count:
            continue
        split = (meta or {}).get("split_pages")
        numbers = list(split) if split and len(split) == count else list(range(1, count + 1))
        doc = dup.Doc(label=label_of(row, meta), data=data, content_type=content_type, numbers=numbers, reads=[None] * count,
                      standalone=not split, ref=row)
        dup.read_pages(doc, settings=settings, deadline=deadline)
        docs.append(doc)
        kept_rows.append(row)
    if len(docs) < 2:
        return docs, []
    keep, repeats = dup.plan(docs, slot, whole_only=True)
    return docs, [(docs[index], rows_of(docs, repeats, index), keep[index]) for index in range(len(docs)) if not keep[index] and docs[index].pages]


def rows_of(docs: list[dup.Doc], repeats: list[dup.Repeat], index: int) -> list[dup.Repeat]:
    return [item for item in repeats if item.doc == index]


def describe(doc: dup.Doc, repeats: list[dup.Repeat], docs: list[dup.Doc]) -> str:
    parts = []
    for group in dup.grouped(repeats):
        other = docs[group[0].of_doc]
        parts.append(f"{dup.pages_word([doc.numbers[item.page] for item in group])} repeat {dup.pages_word([other.numbers[item.of_page] for item in group])}"
                     f" of {other.ref.file_name} v{other.ref.version} ({', '.join(dict.fromkeys(item.why for item in group))})")
    return "; ".join(parts)


def run(engine, args) -> int:
    settings = storage_settings()
    unlinked = 0
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        query = select(Candidate)
        if args.candidate:
            wanted = args.candidate.strip()
            query = query.where(or_(func.lower(Candidate.care_id) == wanted.lower(), Candidate.ref == wanted, Candidate.id == wanted))
        found = list(db.scalars(query.order_by(Candidate.care_id, Candidate.ref)))
        if args.candidate and len(found) != 1:
            print(f"Stopped: {len(found)} candidates match '{args.candidate}' (exactly one is required).", file=sys.stderr)
            return 1
        actor = db.scalar(select(User).where(User.role == Role.admin, User.active.is_(True)).order_by(func.lower(User.email), User.id).limit(1))
        for candidate in found:
            metas = filing_meta(db, candidate.id)
            rows = list(db.scalars(select(Attachment).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot.not_in(SKIPPED_SLOTS))
                .order_by(Attachment.slot, Attachment.version.desc())))
            by_slot: dict[str, list[Attachment]] = {}
            for row in rows:
                by_slot.setdefault(row.slot, []).append(row)
            deadline = time.monotonic() + dup.READ_TIME_BUDGET
            # A version a reading job points at stays: the job's result (the details on the profile) is tied to it.
            read_already = set(db.scalars(select(DocumentExtractionJob.attachment_id).where(
                DocumentExtractionJob.candidate_id == candidate.id, DocumentExtractionJob.attachment_id.is_not(None))))
            heading = False
            for slot, versions in by_slot.items():
                if len(versions) < 2:
                    continue
                docs, drops = plan_slot(db, candidate, slot, versions, metas, settings, deadline)
                for doc, repeats, _ in drops:
                    if doc.ref.id in read_already:
                        continue
                    if not heading:
                        out(f"{candidate.care_id or candidate.ref}  {candidate.name}")
                        heading = True
                    row = doc.ref
                    first = repeats[0]
                    keeper = docs[first.of_doc].ref
                    out(f"  {slot}: {'UNLINK' if args.apply else 'would unlink'} v{row.version} {row.file_name} "
                        f"[{dup.pages_word(doc.numbers)} of {doc.label}] - {describe(doc, repeats, docs)}; the stored file stays")
                    unlinked += 1
                    if args.apply:
                        db.add(AuditEntry(
                            actor_id=actor.id if actor else None, action="Unlinked duplicate document", target=row.file_name,
                            entity_type="candidate", entity_id=candidate.id,
                            meta={"slot": slot, "attachment_id": row.id, "version": row.version, "file_name": row.file_name, "object_key": row.object_key,
                                  "content_hash": row.content_hash, "size_kb": row.size_kb, "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None,
                                  "original_name": (metas.get(row.id) or {}).get("original_name"), "split_pages": (metas.get(row.id) or {}).get("split_pages"),
                                  "same_as_attachment_id": keeper.id, "same_as_file": keeper.file_name, "same_as_version": keeper.version,
                                  "reason": describe(doc, repeats, docs), "file_kept_in_storage": True}))
                        db.delete(row)
            if args.apply:
                db.commit()
            else:
                db.rollback()
    out("")
    if not found:
        out("No candidate matched.")
    elif args.apply:
        out(f"Done: {unlinked} duplicate version(s) unlinked. No stored file was deleted; each unlink has an audit entry 'Unlinked duplicate document'.")
    else:
        out(f"DRY RUN: {unlinked} duplicate version(s) would be unlinked. Nothing was changed. Run again with --apply to do it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", help="the Care India ID (CI-167), the CRM reference (CND-...) or the id of ONE candidate")
    parser.add_argument("--all", action="store_true", help="every candidate")
    parser.add_argument("--apply", action="store_true", help="really unlink (without it: dry run, nothing is changed)")
    parser.add_argument("--database-url", help="override the database from the app settings (backend/.env)")
    parser.add_argument("--allow-remote", action="store_true", help="allow the shared database (backend/.env is the tunnel to it); also for the dry run")
    args = parser.parse_args(argv)
    if bool(args.candidate) == bool(args.all):
        print("Refused: give exactly one of --candidate <id> or --all.", file=sys.stderr)
        return 2
    url = make_url(args.database_url) if args.database_url else None
    if url is None and not os.environ.get("CRM_ENV_FILE"):
        why = "no CRM_ENV_FILE was set, so this would use backend/.env (the shared database)."
    else:
        why = shared_reason(url or make_url(get_settings().database_url))
    if why and not args.allow_remote:
        print(f"Refused: {why} Add --allow-remote on purpose to use it.", file=sys.stderr)
        return 2
    url = url or make_url(get_settings().database_url)
    out(f"Database: {url.host or 'local socket'}:{url.port or 5432}/{url.database}")
    out("Mode    : APPLY" if args.apply else "Mode    : dry run (nothing will be changed)")
    engine = create_engine(url, connect_args={"connect_timeout": 10}, future=True)
    try:
        return run(engine, args)
    except Exception as error:  # noqa: BLE001
        print(f"FAILED: {type(error).__name__}: {error}\nEach candidate is committed on its own; one that did not print 'Done' was not changed.", file=sys.stderr)
        return 3
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
