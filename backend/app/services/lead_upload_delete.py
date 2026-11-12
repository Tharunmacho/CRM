"""Admin-confirmed, atomic removal of one upload and only its first-owned leads."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import logging

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models import AuditEntry, Role
from app.services.lead_sheet import fail

ACTION = "Permanently deleted lead upload"
LOCK_PREFIX = "crm-lead-resume:"
ACTIVE = {"pending", "processing", "retry"}
TERMINAL = {"uploaded", "completed", "needs_review", "failed", "dismissed"}
log = logging.getLogger(__name__)


def _admin(user):
    if user.role != Role.admin:
        raise fail(403, "admin_required", "Only an administrator can permanently delete an upload.")


@contextmanager
def resume_check_guard(db: Session, lead_id: str):
    """A dedicated physical connection keeps this lock across intake's commits."""
    connection = db.get_bind().engine.connect()
    acquired = False
    try:
        acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": LOCK_PREFIX + lead_id}))
        if not acquired:
            raise fail(409, "upload_busy", "This lead is being changed. Wait a moment and try again.")
        yield
    finally:
        try:
            connection.rollback()
            if acquired:
                released = connection.scalar(text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": LOCK_PREFIX + lead_id})
                if not released:
                    connection.invalidate()
                else:
                    connection.commit()
        except Exception:
            connection.invalidate()
            raise
        finally:
            connection.close()


def _rows(db, sql, **params):
    return [dict(row) for row in db.execute(text(sql), params).mappings()]


def _snapshot(db: Session, batch_id: str):
    batches = _rows(db, "SELECT id,file_name,rows_duplicate,rows_updated,status,md5(to_jsonb(b)::text) AS fingerprint FROM crm_lead_batches b WHERE id=:batch", batch=batch_id)
    if not batches:
        return None
    batch = batches[0]
    # Import duplicates include repeated rows inside this same file. Only real
    # surviving leads from another upload are "preserved existing leads".
    duplicates = db.scalar(text("SELECT CASE WHEN jsonb_typeof(report->'duplicates')='array' THEN report->'duplicates' ELSE '[]'::jsonb END FROM crm_lead_batches WHERE id=:batch"), {"batch": batch_id})
    external_ids = sorted({item.get("existingLeadId") for item in duplicates if isinstance(item,dict) and item.get("scope")=="database" and isinstance(item.get("existingLeadId"),str)})
    count = db.scalar(text("SELECT count(*) FROM crm_leads WHERE id=ANY(CAST(:ids AS text[])) AND batch_id IS DISTINCT FROM :batch"), {"ids": external_ids, "batch": batch_id})
    batch["preserved_existing_lead_count"] = count if len(duplicates)==batch["rows_duplicate"] else None
    leads = _rows(db, "SELECT id,status,candidate_id,md5(to_jsonb(l)::text) AS fingerprint FROM crm_leads l WHERE batch_id=:batch ORDER BY id", batch=batch_id)
    ids = [row["id"] for row in leads]
    activities = _rows(db, "SELECT id,lead_id,md5(to_jsonb(a)::text) AS fingerprint FROM crm_lead_activities a WHERE lead_id=ANY(CAST(:ids AS text[])) ORDER BY id", ids=ids)
    attachments = _rows(db, "SELECT id,entity_id,object_key,md5(to_jsonb(a)::text) AS fingerprint FROM document_attachments a WHERE entity_type='lead' AND entity_id=ANY(CAST(:ids AS text[])) ORDER BY id", ids=ids)
    attachment_ids = [row["id"] for row in attachments]
    jobs = _rows(db, """SELECT id,status,candidate_id,attachment_id,group_id,object_key,
        draft->'leadCheck'->>'leadId' AS lead_id,
        draft->'leadCheck'->>'sourceAttachmentId' AS source_attachment_id,
        claimed_by,lease_until,state->'documents' AS documents,
        md5(to_jsonb(j)::text) AS fingerprint FROM document_extraction_jobs j
        WHERE draft->'leadCheck'->>'leadId'=ANY(CAST(:ids AS text[]))
           OR attachment_id=ANY(CAST(:attachments AS text[]))
           OR draft->'leadCheck'->>'sourceAttachmentId'=ANY(CAST(:attachments AS text[])) ORDER BY id""", ids=ids, attachments=attachment_ids)
    groups = [row["group_id"] for row in jobs]
    intakes = _rows(db, "SELECT id,source_id,md5(to_jsonb(i)::text) AS fingerprint FROM document_person_intakes i WHERE source_type='manual' AND source_id=ANY(CAST(:groups AS text[])) ORDER BY id", groups=groups)
    links = ["/leads?lead=" + identifier for identifier in ids]
    todos = _rows(db, "SELECT id,md5(to_jsonb(t)::text) AS fingerprint FROM personal_todos t WHERE link_label='Lead follow-up' AND link_to=ANY(CAST(:links AS text[])) ORDER BY id", links=links)
    return {"batch": batch, "leads": leads, "activities": activities, "attachments": attachments, "jobs": jobs, "intakes": intakes, "todos": todos}


def _preview(snapshot, user):
    batch = snapshot["batch"]
    ids = {row["id"] for row in snapshot["leads"]}
    attachment_leads = {row["id"]: row["entity_id"] for row in snapshot["attachments"]}
    protected = {row["id"] for row in snapshot["leads"] if row["candidate_id"] or row["status"] == "converted"}
    reasons = []
    if protected:
        reasons.append({"code": "candidate_linked", "message": "This upload contains leads converted or linked to Data Bank candidates. Keep those candidate records; nothing in this upload can be deleted here.", "count": len(protected)})
    active, shared = set(), set()
    reviewed_groups = {row["source_id"] for row in snapshot["intakes"]}
    for job in snapshot["jobs"]:
        owners = {job["lead_id"], attachment_leads.get(job["attachment_id"]), attachment_leads.get(job["source_attachment_id"])} & ids
        if job["status"] in ACTIVE or job["claimed_by"] or job["lease_until"]:
            active |= owners
        if (job["candidate_id"] or job["status"] not in TERMINAL | ACTIVE
                or job["attachment_id"] and job["attachment_id"] not in attachment_leads
                or job["source_attachment_id"] and job["source_attachment_id"] not in attachment_leads
                or job["lead_id"] and job["lead_id"] not in ids
                or job["group_id"] in reviewed_groups):
            shared |= owners
    if active:
        reasons.append({"code": "extraction_active", "message": "Resume reading is queued or running for this upload. Wait for reading to finish, then refresh the preview.", "count": len(active)})
    if shared:
        reasons.append({"code": "document_shared", "message": "A resume is attached to another record or belongs to a document-person review. Resolve that review before deleting this upload.", "count": len(shared)})
    protected |= active | shared
    token = sha256(json.dumps({"actor": user.id, "snapshot": snapshot}, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return {"batchId": batch["id"], "fileName": batch["file_name"], "leadCount": len(ids), "protectedCount": len(protected),
            "duplicateRows": batch["rows_duplicate"], "updatedExistingRows": batch["rows_updated"],
            "preservedExistingLeadCount": batch["preserved_existing_lead_count"],
            "canDelete": not reasons, "reasons": reasons, "previewToken": token}


def preview(db: Session, user, batch_id: str):
    _admin(user)
    snapshot = _snapshot(db, batch_id)
    if snapshot is None:
        raise fail(404, "not_found", "This upload was not found.")
    return _preview(snapshot, user)


def _lock(db):
    # NOWAIT avoids waiting behind an upload, importer, worker or recruiter edit.
    # Row locks below also catch a writer that has selected a lead but not written.
    try:
        db.execute(text("LOCK TABLE crm_lead_batches,crm_leads,crm_lead_activities,document_attachments,document_extraction_jobs,document_person_intakes,personal_todos IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise fail(409, "upload_busy", "Lead records are being changed. Wait a moment, refresh the preview and try again.") from None
        raise


def _receipt(db, user, batch_id, token, count):
    row = db.execute(text("SELECT meta FROM system_audit_log WHERE action=:action AND entity_type='lead_batch' AND entity_id=:batch AND actor_id=:actor AND meta->>'previewToken'=:token ORDER BY at DESC LIMIT 1"), {"action": ACTION, "batch": batch_id, "actor": user.id, "token": token}).scalar()
    result = (row or {}).get("receipt")
    return result if result and result.get("deletedLeads") == count else None


def remove(db: Session, user, batch_id: str, token: str, expected_count: int, confirm: str):
    _admin(user)
    if confirm != "DELETE":
        raise fail(422, "confirmation_required", "Type DELETE to permanently remove this upload.")
    _lock(db)
    snapshot = _snapshot(db, batch_id)
    if snapshot is None:
        receipt = _receipt(db, user, batch_id, token, expected_count)
        if receipt:
            return receipt, set()
        raise fail(404, "not_found", "This upload was not found.")
    ids = [row["id"] for row in snapshot["leads"]]
    try:
        db.execute(text("SELECT id FROM crm_lead_batches WHERE id=:batch FOR UPDATE NOWAIT"), {"batch": batch_id}).all()
        db.execute(text("SELECT id FROM crm_leads WHERE id=ANY(CAST(:ids AS text[])) ORDER BY id FOR UPDATE NOWAIT"), {"ids": ids}).all()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise fail(409, "upload_busy", "A lead in this upload is being changed. Refresh the preview and try again.") from None
        raise
    locks = db.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:prefix || id,0)) FROM unnest(CAST(:ids AS text[])) AS id ORDER BY id"), {"ids": ids, "prefix": LOCK_PREFIX}).scalars().all()
    if not all(locks):
        raise fail(409, "upload_busy", "A resume check is starting for this upload. Wait for it to finish and refresh the preview.")
    outcome = _preview(snapshot, user)
    if not outcome["canDelete"]:
        raise fail(409, "upload_protected", outcome["reasons"][0]["message"], reasons=outcome["reasons"])
    if token != outcome["previewToken"] or expected_count != outcome["leadCount"]:
        raise fail(409, "preview_changed", "This upload changed. Refresh the deletion preview and confirm again.")
    keys = {row["object_key"] for row in snapshot["attachments"] + snapshot["jobs"]}
    for job in snapshot["jobs"]:
        documents = job["documents"]
        if isinstance(documents, dict):
            keys.update(value["object_key"] for value in documents.values() if isinstance(value, dict) and isinstance(value.get("object_key"), str))
    params = {"ids": ids, "jobs": [row["id"] for row in snapshot["jobs"]], "attachments": [row["id"] for row in snapshot["attachments"]],
              "todos": [row["id"] for row in snapshot["todos"]], "batch": batch_id}
    db.execute(text("DELETE FROM document_extraction_jobs WHERE id=ANY(CAST(:jobs AS text[]))"), params)
    db.execute(text("DELETE FROM document_attachments WHERE id=ANY(CAST(:attachments AS text[]))"), params)
    db.execute(text("DELETE FROM personal_todos WHERE id=ANY(CAST(:todos AS text[]))"), params)
    db.execute(text("DELETE FROM crm_leads WHERE id=ANY(CAST(:ids AS text[]))"), params)  # activities/raw/extra cascade with their owner
    db.execute(text("DELETE FROM crm_lead_batches WHERE id=:batch"), params)
    result = {"deleted": True, "batchId": batch_id, "deletedLeads": len(ids), "deletedActivities": len(snapshot["activities"]),
              "deletedFollowUps": len(snapshot["todos"]), "deletedAttachments": len(snapshot["attachments"]),
              "deletedExtractionJobs": len(snapshot["jobs"]), "duplicateRows": outcome["duplicateRows"],
              "preservedExistingLeadCount": outcome["preservedExistingLeadCount"]}
    db.add(AuditEntry(actor_id=user.id, action=ACTION, target=outcome["fileName"], entity_type="lead_batch", entity_id=batch_id,
                      meta={"previewToken": token, "receipt": result, "updatedExistingRowsPreserved": outcome["updatedExistingRows"]}))
    db.flush()
    return result, keys


def cleanup_files(engine, keys):
    """After commit only; never remove bytes still referenced by a file or job."""
    from app.core.config import get_settings
    from app.services import document_storage
    with Session(engine) as db:
        for key in sorted(keys):
            try:
                shared = db.scalar(text("""SELECT EXISTS(SELECT 1 FROM document_attachments WHERE object_key=:key)
                    OR EXISTS(SELECT 1 FROM document_extraction_jobs j WHERE object_key=:key
                        OR jsonb_path_exists(j.state,'$.documents.*.object_key ? (@ == $key)',jsonb_build_object('key',CAST(:key AS text))))
                    OR EXISTS(SELECT 1 FROM document_person_intakes i WHERE jsonb_path_exists(to_jsonb(i),'$.** ? (@ == $key)',jsonb_build_object('key',CAST(:key AS text))))"""), {"key": key})
                if shared:
                    continue
                document_storage.delete_bytes(get_settings(), key)
            except Exception:
                db.rollback()
                log.warning("Could not remove an unused file after permanent lead-upload deletion.")
