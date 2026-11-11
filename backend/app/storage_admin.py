"""Offline document inventory and verified, non-destructive local-to-S3 migration.

Run from backend: python -m app.storage_admin audit
Migration is a dry run unless --apply --writers-stopped are both provided.
An exact --allow-missing-manifest may retain already-missing local references;
such a migration is explicitly incomplete and never removes the originals.
"""
import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select, text

from app.db.models import (Application, ApplicationStage, Attachment, AuditEntry, DocumentExtractionJob, EmailIngestionJob,
                           MailAccount, MailAttachment, MailDraftAttachment, MailSendAttempt,
                           MultiPersonIntake, WhatsAppMessage, WhatsAppSendAttempt)
from app.domain.stages import FILE_FIELDS
from app.services.document_storage import put_bytes, read_bytes, storage_key
from app.services.instance_guard import instance_name
from app.services.mail_storage import storage_identity

DIRECT = ((MailDraftAttachment, "object_key"), (Attachment, "object_key"), (MailAttachment, "object_key"),
          (WhatsAppMessage, "object_key"), (DocumentExtractionJob, "object_key"), (WhatsAppSendAttempt, "object_key"))
NESTED = ((MailSendAttempt, "snapshot"), (EmailIngestionJob, "state"), (WhatsAppMessage, "processing_state"),
          (DocumentExtractionJob, "state"), (MultiPersonIntake, "sources"))


def legacy_application_documents(db):
    """Identify only owned, stage-specific bare keys supported by applications._normalized_data.

    Current ID/URL references need no migration. Do not replace arbitrary equal
    strings elsewhere in drafts, completed-stage history or provider payloads.
    """
    attachments = {}
    for attachment in db.scalars(select(Attachment)):
        attachments.setdefault(attachment.object_key, []).append(attachment)
    applications = {row.id: row for row in db.scalars(select(Application))}
    rows = [(row, row, "draft", row.current_stage) for row in applications.values()]
    rows.extend((applications[row.application_id], row, "data", row.stage)
                for row in db.scalars(select(ApplicationStage)))
    for application, row, attribute, stage in rows:
        payload = getattr(row, attribute) or {}
        if not isinstance(payload, dict):
            continue
        for field in FILE_FIELDS.get(stage, ()):
            raw = payload.get(field)
            if not isinstance(raw, str) or raw.startswith("/api/files/"):
                continue
            owned = [item for item in attachments.get(raw, ()) if (
                item.entity_type == "application" and item.entity_id == application.id
                and item.slot in {field, f"stage_{stage}_{field}"}
            ) or (stage == 7 and field == "copy" and item.entity_type == "candidate"
                  and item.entity_id == application.candidate_id and item.slot == "passport")]
            if len(owned) > 1:
                raise ValueError(f"Ambiguous legacy application document reference: {application.id}, stage {stage}, {field}; resolve it before migration")
            if owned:
                yield row, attribute, field, raw, owned[0]


def job_instance_report(db, settings, target):
    counts = {}
    for name in db.scalars(select(DocumentExtractionJob.instance).where(
            DocumentExtractionJob.status.in_(("uploaded", "pending", "processing", "retry")))):
        counts[name] = counts.get(name, 0) + 1
    return {"bindingsPreserved": True, "sourceInstance": instance_name(settings),
            "targetInstance": instance_name(target),
            "queuedJobs": [{"instance": name, "count": count} for name, count in
                           sorted(counts.items(), key=lambda item: item[0] or "")],
            "action": "Run workers with the preserved INSTANCE_NAME bindings, or perform a separately reviewed reassignment before retiring them."}


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
        known = {"key": key, "expectedHashes": sorted(expected)}
        try:
            data = read_bytes(settings, key)
            digest = hashlib.sha256(data).hexdigest()
            result.append({**known, "status": "ok" if not expected or expected == {digest} else "checksum_mismatch",
                           "bytes": len(data), "sha256": digest})
        except Exception as exc:
            # Do not print exception text: provider errors can contain signed URLs.
            result.append({**known, "status": "unavailable", "errorType": type(exc).__name__,
                           "missing": isinstance(exc, FileNotFoundError)})
    return {"objects": result, "ok": all(item["status"] == "ok" for item in result)}


def rewritten(value, mapping):
    if isinstance(value, dict):
        return {key: mapping.get(child, child) if key == "object_key" and isinstance(child, str)
                else rewritten(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [rewritten(child, mapping) for child in value]
    return value


def approved_missing(report, manifest):
    """An exact reviewed manifest can retain missing local references, never hide other failures."""
    if manifest is None:
        return []
    if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
            or manifest["version"] != 1 or not isinstance(manifest.get("objects"), list)):
        raise ValueError("The missing-file manifest must have version 1 and an objects list")
    current = {item["key"]: item for item in report["objects"]}
    allowed = []
    seen = set()
    for entry in manifest["objects"]:
        if not isinstance(entry, dict) or set(entry) != {"key", "expectedHashes"}:
            raise ValueError("Each missing-file manifest entry must contain only key and expectedHashes")
        key, hashes = entry["key"], entry["expectedHashes"]
        if not isinstance(key, str) or key in seen or not isinstance(hashes, list) or any(not isinstance(value, str) for value in hashes):
            raise ValueError("The missing-file manifest contains an invalid or duplicate entry")
        seen.add(key)
        item = current.get(key)
        if (not item or key.startswith("s3://") or item["status"] != "unavailable" or not item.get("missing")
                or hashes != item["expectedHashes"]):
            raise ValueError("The missing-file manifest is stale or does not match a missing local file and its known hashes")
        allowed.append(item)
    return sorted(allowed, key=lambda item: item["key"])


def migrate_s3(db, settings, *, apply=False, writers_stopped=False, missing_manifest=None):
    if apply and not writers_stopped:
        raise ValueError("Stop the API and all workers, back up database and documents, then pass --writers-stopped")
    if not settings.s3_bucket:
        raise ValueError("Configure the destination S3 bucket and credentials before migration")
    target = settings.model_copy(update={"storage_backend": "s3"})
    if apply:
        # Defensive DB write exclusion; slow provider calls are acceptable only in
        # this explicit offline maintenance command, never in request handlers.
        names = sorted({model.__tablename__ for model, _ in (*DIRECT, *NESTED)} |
                       {MailAccount.__tablename__, Application.__tablename__, ApplicationStage.__tablename__})
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("LOCK TABLE " + ", ".join(names) + " IN SHARE ROW EXCLUSIVE MODE"))
    report = audit(db, settings)
    prefix = storage_key(target, "migration-sentinel").removesuffix("migration-sentinel")
    if any(item["key"].startswith("s3://") and not item["key"].startswith(prefix) for item in report["objects"]):
        raise ValueError("Existing S3 objects belong to another namespace. Use a separately reviewed cross-bucket migration")
    retained_missing = approved_missing(report, missing_manifest)
    retained_keys = {item["key"] for item in retained_missing}
    if any(item["status"] != "ok" and item["key"] not in retained_keys for item in report["objects"]):
        raise ValueError("Source audit failed. Restore unavailable/corrupt objects before migration")
    mapping = {item["key"]: storage_key(target, item["key"]) for item in report["objects"]
               if item["status"] == "ok" and not item["key"].startswith("s3://")}
    if any(len(key) > 500 for key in mapping.values()):
        raise ValueError("A destination object key exceeds the database limit")
    legacy_documents = list(legacy_application_documents(db))
    result = {"dryRun": not apply, "complete": not retained_missing,
              "copies": [{"from": key, "to": value} for key, value in sorted(mapping.items())],
              "retainedMissingObjects": retained_missing,
              "counts": {"inventoried": len(report["objects"]), "copied": len(mapping),
                         "retainedMissing": len(retained_missing),
                         "alreadyS3": sum(item["key"].startswith("s3://") for item in report["objects"])},
              "legacyApplicationReferences": sum(key in mapping for _, _, _, key, _ in legacy_documents),
              "workerInstances": job_instance_report(db, settings, target)}
    if not apply:
        return result
    audited_hashes = {item["key"]: item["sha256"] for item in report["objects"] if item["status"] == "ok"}
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
    for row, attribute, field, key, attachment in legacy_documents:
        if key in mapping:
            # Resolve by ID once, before shared/copy object keys become ambiguous
            # in the request handler. Keep attachment identity and version intact.
            reference = {"id": attachment.id, "name": attachment.file_name,
                         "url": f"/api/files/{attachment.id}", "sizeKb": attachment.size_kb,
                         "version": attachment.version}
            setattr(row, attribute, {**getattr(row, attribute), field: reference})
    # Binding changes only after every copied original and derivative has passed
    # verification. Explicitly retained missing keys remain local and reported.
    # Future receipts use this same S3 namespace identity.
    for account in db.scalars(select(MailAccount)):
        account.sync_state = {**(account.sync_state or {}), "storage_id": storage_identity(settings.storage_dir, target)}
    db.add(AuditEntry(action="Moved documents to cloud storage", entity_type="storage", target=settings.s3_bucket,
                      meta={"objectsCopied": len(mapping), "originalsRetained": True,
                            "complete": not retained_missing, "retainedMissingObjects": retained_missing,
                            "legacyApplicationReferences": result["legacyApplicationReferences"],
                            "workerInstances": result["workerInstances"]}))
    db.commit()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "migrate-s3"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--allow-missing-manifest", type=Path,
                        help="Reviewed version-1 JSON allowlist of exact missing local keys and expectedHashes; retained references stay missing")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        manifest = json.loads(args.allow_missing_manifest.read_text(encoding="utf-8")) if args.allow_missing_manifest else None
        if args.command == "audit" and manifest is not None:
            parser.error("--allow-missing-manifest is only valid with migrate-s3; audit always reports every missing file")
        result = audit(db, get_settings()) if args.command == "audit" else migrate_s3(
            db, get_settings(), apply=args.apply, writers_stopped=args.writers_stopped, missing_manifest=manifest)
    content = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    if args.command == "audit" and not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
