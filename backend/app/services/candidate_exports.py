"""Candidate inspection workbooks and complete, bounded document packages."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import ensure_can_work_candidate
from app.db.models import Application, ApplicationFeedback, ApplicationStage, Attachment, Candidate, Remark, Role, Task, User
from app.services import application_form, document_storage
from app.services.report_exports import stage_name, xlsx_rows

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CHUNK_BYTES = 64 * 1024
SPOOL_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_SOURCE_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_DOCUMENTS = 2000
MAX_HISTORY_ROWS = 10000
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_PRIVATE_KEYS = {"objectkey", "storagekey", "object_key", "storage_key"}


def safe_name(value: str, fallback: str = "document") -> str:
    name = _UNSAFE.sub("-", (value or "").replace("\\", "/").rsplit("/", 1)[-1]).strip("-.")[:140]
    return name or fallback


def plain(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def public_data(value):
    """Keep business stage answers, excluding storage implementation fields."""
    if isinstance(value, dict):
        return {key: public_data(item) for key, item in value.items() if key.lower() not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [public_data(item) for item in value]
    return plain(value)


def details(value, prefix=""):
    """Flatten without truncating long answers; Excel cells are limited to 32,767 characters."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from details(item, f"{prefix} / {key}" if prefix else key)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value, 1):
            yield from details(item, f"{prefix} / {index}")
        if not value:
            yield [prefix, ""]
    else:
        item = plain(value)
        if isinstance(item, str) and len(item) > 30000:
            for index in range(0, len(item), 30000):
                yield [f"{prefix} (part {index // 30000 + 1})", item[index:index + 30000]]
        else:
            yield [prefix, item]


@dataclass
class Inspection:
    candidate: Candidate
    form: dict
    applications: list
    stages: list
    attachments: list
    names: dict
    feedback: list
    tasks: list
    remarks: list


def load_inspection(db: Session, candidate_id: str, user: User, *, application_id=None, order_id=None) -> Inspection:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    # Full inspection exports include private stage answers/history, not only the
    # public form summary. Match the existing stage and application-file access.
    ensure_can_work_candidate(candidate, user, db)
    app_ids = select(Application.id).where(Application.candidate_id == candidate_id)
    attachments = list(db.scalars(select(Attachment).where(or_(
        (Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id),
        (Attachment.entity_type == "application") & Attachment.entity_id.in_(app_ids),
    )).order_by(Attachment.entity_type, Attachment.entity_id, Attachment.slot,
                Attachment.uploaded_at.desc(), Attachment.version.desc(), Attachment.id.desc()).limit(MAX_DOCUMENTS + 1)))
    if len(attachments) > MAX_DOCUMENTS:
        raise HTTPException(413, "This candidate has too many document versions for one export. Contact your administrator.")
    form = application_form.build_form(db, candidate_id, user, application_id=application_id, order_id=order_id)
    if application_id and order_id and form["application"]["orderId"] != order_id:
        raise HTTPException(404, "This candidate's selected application is not on that order.")
    applications = list(db.scalars(select(Application).where(Application.candidate_id == candidate_id)
                                  .order_by(Application.created_at.desc(), Application.id.desc())))
    stages = list(db.scalars(select(ApplicationStage).where(ApplicationStage.application_id.in_(app_ids))
                            .order_by(ApplicationStage.application_id, ApplicationStage.stage, ApplicationStage.attempt)
                            .limit(MAX_HISTORY_ROWS + 1)))
    feedback = list(db.scalars(select(ApplicationFeedback).where(ApplicationFeedback.candidate_id == candidate_id)
                              .order_by(ApplicationFeedback.created_at, ApplicationFeedback.id).limit(MAX_HISTORY_ROWS + 1)))
    task_scope = [Task.candidate_id == candidate_id]
    if user.role not in (Role.admin, Role.manager):
        task_scope.append(Task.owner_id == user.id)
    tasks = list(db.scalars(select(Task).where(*task_scope).order_by(Task.created_at, Task.id).limit(MAX_HISTORY_ROWS + 1)))
    remarks = list(db.scalars(select(Remark).where(Remark.task_id.in_(select(Task.id).where(*task_scope)))
                             .order_by(Remark.created_at, Remark.id).limit(MAX_HISTORY_ROWS + 1)))
    if any(len(rows) > MAX_HISTORY_ROWS for rows in (stages, feedback, tasks, remarks)):
        raise HTTPException(413, "This candidate has too much history for one export. Contact your administrator; no records were omitted.")
    person_ids = {candidate.owner_id, candidate.intake_owner_id, candidate.created_by}
    person_ids.update(row.uploaded_by for row in attachments)
    person_ids.update(row.completed_by for row in stages)
    person_ids.update(row.owner_id for row in applications)
    person_ids.update(row.draft_saved_by for row in applications)
    person_ids.update(row.author_id for row in feedback)
    person_ids.update(row.owner_id for row in tasks)
    person_ids.update(row.author_id for row in remarks)
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(person_ids - {None}))).all())
    return Inspection(candidate, form, applications, stages, attachments, names, feedback, tasks, remarks)


def manifest(inspection: Inspection) -> dict:
    form = inspection.form
    application_map = {row["id"]: row for row in form["applications"]}
    # The form includes rejected-file and selected-placement evidence; rebuilding
    # without reading metadata would incorrectly count a known wrong document.
    slots = form["documents"]
    rejected_ids = {file["attachmentId"] for entry in slots.values()
                    for file in [*entry["files"], *entry["history"], *([entry["current"]] if entry["current"] else [])]
                    if file.get("identityMismatch")}
    mandatory = dict(application_form.required_for_deployment())
    selected = set()
    latest = {}
    for row in inspection.attachments:
        canonical = application_form.LEGACY_SLOTS.get(row.slot, row.slot)
        key = (row.entity_type, row.entity_id, canonical)
        previous = latest.get(key)
        if previous is None or (row.uploaded_at, row.version, row.id) > (previous.uploaded_at, previous.version, previous.id):
            latest[key] = row
        if canonical in {"other", "invoice", "passport", "aadhaar"}:
            selected.add(row.id)
    for (entity_type, _entity_id, canonical), row in latest.items():
        # Legacy aliases can tie on timestamp/version. The checklist is the
        # authoritative current candidate document, including that tie-break.
        current = slots.get(canonical, {}).get("current") if entity_type == "candidate" else None
        selected.add(current["attachmentId"] if current else row.id)
    documents = []
    for index, row in enumerate(inspection.attachments, 1):
        canonical = application_form.LEGACY_SLOTS.get(row.slot, row.slot)
        application = application_map.get(row.entity_id) if row.entity_type == "application" else None
        directory = "candidate" if row.entity_type == "candidate" else f"applications/{safe_name(row.entity_id)}"
        entry = f"{directory}/{safe_name(canonical)}/{index:04d}-v{row.version}-{safe_name(row.file_name)}"
        documents.append({
            "attachmentId": row.id, "entityType": row.entity_type, "candidateId": inspection.candidate.id,
            "applicationId": row.entity_id if application else None, "orderId": application["orderId"] if application else None,
            "orderRef": application["orderRef"] if application else None, "slot": canonical, "storedSlot": row.slot,
            "fileName": row.file_name, "contentType": row.content_type, "sizeKb": row.size_kb, "version": row.version,
            "uploadedAt": plain(row.uploaded_at), "uploadedBy": inspection.names.get(row.uploaded_by, ""),
            "includedInZip": row.id in selected, "archivePath": entry if row.id in selected else None,
            "versionNote": "Wrong document: retained for review" if row.id in rejected_ids else "Included" if row.id in selected else "Older version: metadata only",
            "identityMismatch": row.id in rejected_ids,
        })
    checklist = [{"slot": slot, "label": entry["label"], "mandatoryBeforeDeployment": slot in mandatory,
                  "requiredNow": entry["required"], "requiredFromStage": entry["requiredFromStage"],
                  "status": "Wrong document — upload the correct file" if entry.get("identityMismatch") and not entry["uploaded"] else "Uploaded" if entry["fileUploaded"] else "Marked manually (no file attached)" if entry["uploaded"] else "Missing" if entry["required"] else "Optional" if entry["optional"] else "Needed later",
                  "uploaded": entry["uploaded"], "fileUploaded": entry["fileUploaded"], "manualStatus": entry["manualStatus"],
                  "current": entry["current"], "versions": entry["versions"]}
                 for slot, entry in slots.items()]
    return {
        "candidateId": inspection.candidate.id, "reference": inspection.candidate.ref, "candidateName": inspection.candidate.name,
        "generatedAt": datetime.now(timezone.utc).isoformat(), "selectedApplication": form.get("application"),
        "scope": "This candidate's documents and documents from every one of their applications.",
        "filePolicy": "Latest document per record and slot; all passport and Aadhaar files, Other documents and all invoice versions. Older normal-document version metadata is retained in this manifest.",
        "missingRequiredBeforeDeployment": application_form.deployment_gaps({slot for slot, entry in slots.items() if entry["uploaded"]}),
        "missingRequiredNow": [{"slot": item["slot"], "label": item["label"]} for item in checklist if item["requiredNow"] and not item["uploaded"]],
        "checklist": checklist, "documents": documents,
    }


def workbook(inspection: Inspection, listing: dict | None = None) -> bytes:
    form, candidate = inspection.form, inspection.candidate
    listing = listing or manifest(inspection)
    names = inspection.names
    source = {**form["recruitment"], "agency": form["assignment"]["agency"],
              "intakeRecruiter": names.get(candidate.intake_owner_id, ""), "enteredBy": names.get(candidate.created_by, "")}
    detail = source.get("sourceDetail") or ""
    for prefix, label in (("lead_sheet:", "Lead sheet"), ("email_message:", "Email intake"),
                          ("whatsapp_message:", "WhatsApp intake"), ("person_review:", "Document intake")):
        if detail.startswith(prefix):
            source["sourceDetail"] = label
    sections = [
        ("About", [["Field", "Value"], ["Candidate", candidate.name], ["Reference", candidate.ref],
                   ["Generated at (UTC)", listing["generatedAt"]], ["Scope", listing["scope"]], ["Document policy", listing["filePolicy"]],
                   ["Document status", "Attached files and explicit manual confirmations are identified separately. A manual confirmation does not create a downloadable file. The ZIP verifies every included file can be read."],
                   ["Task visibility", "Task history includes tasks visible to the exporting user: all candidate tasks for managers and administrators, or the recruiter's own tasks."],
                   ["Missing before deployment", ", ".join(item["label"] for item in listing["missingRequiredBeforeDeployment"])]]),
        ("Personal", [["Field", "Value"], *details({**{key: value for key, value in form["candidate"].items() if key != "canEdit"},
                    **form["personal"], "createdAt": plain(candidate.created_at)})]),
        ("Contact", [["Field", "Value"], *details({key: form["personal"].get(key) for key in
                  ("mobileNumber", "whatsapp", "email", "currentAddress", "currentCity")}),
                  ["Family contact", candidate.family_member_number]]),
        ("Source and recruiter", [["Field", "Value"], *details(source)]),
        ("Passport", [["Field", "Value"], *details(form["passport"]), *details(form.get("aadhaar"), "Aadhaar")]),
        ("Education", [["Field", "Value"], *details(form["education"])]),
        ("Work history", [["Field", "Value"], *details(form["experience"])]),
        ("Screening", [["Field", "Value"], *details({**form["screening"], "expectedSalary": candidate.expected_salary,
                      "callCalled": candidate.call_called, "callInterested": candidate.call_interested, "callRemarks": candidate.call_remarks,
                      "callRecordedAt": plain(candidate.call_recorded_at)})]),
        ("Placements", [["Application ID", "Order", "Company", "Job", "Country", "Status", "Stage", "Recruiter", "Created", "Selected context"]]
         + [[row["id"], row["orderRef"], row["clientName"], row["position"], row["country"], row["status"],
             stage_name(row["currentStage"]), names.get(next((app.owner_id for app in inspection.applications if app.id == row["id"]), None), ""),
             row["createdAt"], row["selected"]] for row in form["applications"]]),
    ]
    stage_rows = [["Application ID", "Stage", "Attempt", "Field", "Value"]]
    for stage in inspection.stages:
        data = {"outcome": stage.outcome, "completedAt": plain(stage.completed_at), "completedBy": names.get(stage.completed_by, ""),
                "reopenedReason": stage.reopened_reason, "amount": stage.amount, "currency": stage.amount_currency,
                "answers": public_data(stage.data)}
        stage_rows.extend([stage.application_id, stage_name(stage.stage), stage.attempt, key, value] for key, value in details(data))
    draft_rows = [["Application ID", "Current stage", "Saved at", "Saved by", "Field", "Value"]]
    for application in inspection.applications:
        if application.draft or application.draft_saved_at:
            saved_fields = list(details(public_data(application.draft))) or [["Saved draft", ""]]
            draft_rows.extend([application.id, stage_name(application.current_stage), plain(application.draft_saved_at),
                               names.get(application.draft_saved_by, ""), key, value]
                              for key, value in saved_fields)
    feedback_rows = [["Note ID", "Application ID", "Stage", "Kind", "Reason", "Note", "Author", "Created at", "Updated at"]]
    feedback_rows.extend([row.id, row.application_id, stage_name(row.stage), row.kind, row.reason, row.body,
                          names.get(row.author_id, "Former team member"), plain(row.created_at), plain(row.updated_at)]
                         for row in inspection.feedback)
    task_rows = [["Task ID", "Application ID", "Stage", "Kind", "Title", "Owner", "Created at", "Due at", "Completed at", "Cancelled at", "Escalated", "Escalated at"]]
    task_rows.extend([row.id, row.application_id, stage_name(row.stage), plain(row.kind), row.title,
                      names.get(row.owner_id, ""), plain(row.created_at), plain(row.due_at), plain(row.completed_at),
                      plain(row.cancelled_at), row.escalated, plain(row.escalated_at)] for row in inspection.tasks)
    remark_rows = [["Note ID", "Task ID", "Note", "Author", "Created at"]]
    remark_rows.extend([row.id, row.task_id, row.body, names.get(row.author_id, ""), plain(row.created_at)]
                       for row in inspection.remarks)
    checklist_rows = [["Document", "Mandatory before deployment", "Required now", "Required from stage", "Status", "Current file", "Version", "Uploaded at", "Uploaded by",
                       "File attached", "Manual status", "Marked by", "Marked at", "Manual note"]]
    for item in listing["checklist"]:
        current = item["current"] or {}
        manual = item["manualStatus"]
        checklist_rows.append([item["label"], item["mandatoryBeforeDeployment"], item["requiredNow"], item["requiredFromStage"], item["status"],
                               current.get("fileName", ""), current.get("version", ""), current.get("uploadedAt", ""), (current.get("uploadedBy") or {}).get("name", ""),
                               item["fileUploaded"], ("Uploaded" if manual["uploaded"] else "Pending") if manual else "",
                               manual["by"]["name"] if manual else "", manual["at"] if manual else "", manual["note"] if manual else ""])
    columns = ["attachmentId", "entityType", "applicationId", "orderId", "orderRef", "slot", "fileName", "contentType", "sizeKb", "version", "uploadedAt", "uploadedBy", "includedInZip", "archivePath", "versionNote", "identityMismatch"]
    sections.extend([
        ("Stage history", stage_rows), ("Saved stage drafts", draft_rows), ("Document checklist", checklist_rows),
        ("Document manifest", [columns] + [[item[key] for key in columns] for item in listing["documents"]]),
        ("Recruiter notes", feedback_rows), ("Tasks", task_rows), ("Task notes", remark_rows),
        ("Resume text", [["Field", "Value"], *details(candidate.resume_text or "", "Extracted resume text")]),
    ])
    return xlsx_rows(sections)


@contextmanager
def document_stream(settings, key):
    if key.startswith("s3://"):
        bucket, path = document_storage._parts(key)
        body = document_storage._s3(settings).get_object(Bucket=bucket, Key=path)["Body"]
        try:
            yield body
        finally:
            body.close()
    else:
        with document_storage._local(settings, key).open("rb") as stream:
            yield stream


def document_package(db: Session, inspection: Inspection, user: User):
    """Finish the ZIP before responding, so a missing object never produces a misleading partial download."""
    if any(row.entity_type == "application" for row in inspection.attachments):
        ensure_can_work_candidate(inspection.candidate, user, db)
    listing = manifest(inspection)
    by_id = {row.id: row for row in inspection.attachments}
    settings = get_settings()
    spool = SpooledTemporaryFile(max_size=SPOOL_BYTES, mode="w+b")
    total = 0
    try:
        with ZipFile(spool, "w", ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("inspection.xlsx", workbook(inspection, listing))
            for entry in listing["documents"]:
                if not entry["includedInZip"]:
                    continue
                row = by_id[entry["attachmentId"]]
                maximum = MAX_SOURCE_BYTES if row.slot == "source_bundle" else MAX_FILE_BYTES
                size = 0
                try:
                    with document_stream(settings, row.object_key) as source, archive.open(entry["archivePath"], "w") as target:
                        while block := source.read(CHUNK_BYTES):
                            size += len(block)
                            total += len(block)
                            if size > maximum or total > MAX_ARCHIVE_BYTES:
                                raise HTTPException(413, "These documents are too large for one download. Download individual files instead.")
                            target.write(block)
                except (OSError, ValueError, BotoCoreError, ClientError) as exc:
                    raise HTTPException(503, f'The package could not be completed because "{safe_name(row.file_name)}" is unavailable. Please retry or ask an administrator to restore that file.') from exc
                entry["downloadedBytes"] = size
            archive.writestr("manifest.json", json.dumps(listing, ensure_ascii=False, indent=2, default=str))
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise
