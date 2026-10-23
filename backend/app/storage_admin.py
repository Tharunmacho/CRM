"""Offline document inventory and verified, non-destructive local-to-S3 migration.

Run from backend: python -m app.storage_admin audit
Migration is a dry run unless --apply --writers-stopped are both provided.
"""
import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select, text

from app.db.models import (Attachment, AuditEntry, DocumentExtractionJob, EmailIngestionJob,
                           MailAccount, MailAttachment, MailDraftAttachment, MailSendAttempt,
                           MultiPersonIntake, WhatsAppMessage, WhatsAppSendAttempt)
from app.services.document_storage import put_bytes, read_bytes, storage_key
from app.services.mail_storage import storage_identity

DIRECT = ((MailDraftAttachment, "object_key"), (Attachment, "object_key"), (MailAttachment, "object_key"),
          (WhatsAppMessage, "object_key"), (DocumentExtractionJob, "object_key"), (WhatsAppSendAttempt, "object_key"))
NESTED = ((MailSendAttempt, "snapshot"), (EmailIngestionJob, "state"), (WhatsAppMessage, "processing_state"),
          (DocumentExtractionJob, "state"), (MultiPersonIntake, "sources"))


def embedded_keys(value):
    if isinstance(value, dict):
        key = value.get("object_key")
        if isinstance(key, str) and key:
            # Multi-person source snapshots retain their verified digest as
            # `hash`; document and message snapshots use `content_hash`.
            yield key, value.get("content_hash") or value.get("hash")
        for child in value.values():
            yield from embedded_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from embedded_keys(child)


def inventory(db):
    objects = {}
    for model, attribute in DIRECT:
        for row in db.scalars(select(model)):
            key = getattr(row, attribute)
            if key:
                objects.setdefault(key, set()).update([row.content_hash] if getattr(row, "content_hash", None) else [])
    for model, attribute in NESTED:
        for row in db.scalars(select(model)):
            for key, digest in embedded_keys(getattr(row, attribute)):
                objects.setdefault(key, set()).update([digest] if digest else [])
    return objects


def audit(db, settings):
    result = []
    for key, expected in sorted(inventory(db).items()):
        try:
            data = read_bytes(settings, key)
            digest = hashlib.sha256(data).hexdigest()
            result.append({"key": key, "status": "ok" if not expected or expected == {digest} else "checksum_mismatch",
                           "bytes": len(data), "sha256": digest})
        except Exception as exc:
            # Do not print exception text: provider errors can contain signed URLs.
            result.append({"key": key, "status": "unavailable", "errorType": type(exc).__name__})
    return {"objects": result, "ok": all(item["status"] == "ok" for item in result)}


def rewritten(value, mapping):
    if isinstance(value, dict):
        return {key: mapping.get(child, child) if key == "object_key" and isinstance(child, str)
                else rewritten(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [rewritten(child, mapping) for child in value]
    return value


def migrate_s3(db, settings, *, apply=False, writers_stopped=False):
    if apply and not writers_stopped:
        raise ValueError("Stop the API and all workers, back up database and documents, then pass --writers-stopped")
    if not settings.s3_bucket:
        raise ValueError("Configure the destination S3 bucket and credentials before migration")
    target = settings.model_copy(update={"storage_backend": "s3"})
    if apply:
        # Defensive DB write exclusion; slow provider calls are acceptable only in
        # this explicit offline maintenance command, never in request handlers.
        names = sorted({model.__tablename__ for model, _ in (*DIRECT, *NESTED)} | {MailAccount.__tablename__})
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("LOCK TABLE " + ", ".join(names) + " IN SHARE ROW EXCLUSIVE MODE"))
    report = audit(db, settings)
    prefix = storage_key(target, "migration-sentinel").removesuffix("migration-sentinel")
    if any(item["key"].startswith("s3://") and not item["key"].startswith(prefix) for item in report["objects"]):
        raise ValueError("Existing S3 objects belong to another namespace. Use a separately reviewed cross-bucket migration")
    if not report["ok"]:
        raise ValueError("Source audit failed. Restore unavailable/corrupt objects before migration")
    mapping = {item["key"]: storage_key(target, item["key"]) for item in report["objects"] if not item["key"].startswith("s3://")}
    if any(len(key) > 500 for key in mapping.values()):
        raise ValueError("A destination object key exceeds the database limit")
    result = {"dryRun": not apply, "copies": [{"from": key, "to": value} for key, value in sorted(mapping.items())]}
    if not apply:
        return result
    audited_hashes = {item["key"]: item["sha256"] for item in report["objects"]}
    for source, destination in mapping.items():
        data = read_bytes(settings, source)
        if hashlib.sha256(data).hexdigest() != audited_hashes[source]:
            raise ValueError("Source checksum changed during migration; no database references were committed")
        put_bytes(target, destination, data)
        if hashlib.sha256(read_bytes(target, destination)).digest() != hashlib.sha256(data).digest():
            raise ValueError("Destination checksum verification failed; no database references were committed")
    for model, attribute in DIRECT:
        for row in db.scalars(select(model)):
            old = getattr(row, attribute)
            if old in mapping:
                setattr(row, attribute, mapping[old])
    for model, attribute in NESTED:
        for row in db.scalars(select(model)):
            old = getattr(row, attribute)
            value = rewritten(old, mapping)
            if old != value:
                setattr(row, attribute, value)
    # Binding changes only after every referenced original and derivative has
    # passed verification. Future receipts use this same S3 namespace identity.
    for account in db.scalars(select(MailAccount)):
        account.sync_state = {**(account.sync_state or {}), "storage_id": storage_identity(settings.storage_dir, target)}
    db.add(AuditEntry(action="Migrated documents to S3", entity_type="storage", target=settings.s3_bucket,
                      meta={"objectsCopied": len(mapping), "originalsRetained": True}))
    db.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "migrate-s3"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        result = audit(db, get_settings()) if args.command == "audit" else migrate_s3(db, get_settings(), apply=args.apply, writers_stopped=args.writers_stopped)
    content = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    if args.command == "audit" and not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
