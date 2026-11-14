"""The order-wise job application form: payload, manual saves, tracking, completeness and document reading.

One form per candidate and order (application). The candidate's own details live on `crm_candidates` (the OCR-read
or typed values, with `field_sources` saying which); client, country, position, recruiter and the tracking table are
derived from the application, its order and its stage records, so clearing a stage updates the form without any
write here. See docs/workflows/application-form-contract.md for the wire format.
"""
from __future__ import annotations

from copy import deepcopy
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, undefer

from app.core.config import get_settings
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate, Client, Company,
    Designation, DocumentExtractionJob, Order, Priority, Role, Source, Task, TodoItem, User,
)
from app.domain.stages import stage_by_no
from app.services import form_assignment, form_feedback
from app.services import form_extraction as fx
from app.services.candidate_identity import identity_matches, normalize_candidate_values, normalize_passport, normalize_phone, normalized_labels
from app.services.parallel_reads import parallel_reads
from app.services.reading_notes import plain_issues, plain_notes
from app.services.document_status import ACTION as DOCUMENT_STATUS_ACTION, manual_document_statuses, read_manual_document_statuses
from app.services.document_identity import rejected_expression

# Document slots of the form, in the order shown (docs/workflows/application-form-v2-contract.md section 2):
# (slot, label, hint, required from this stage on (None: see `always`), always counted, read by OCR, several files allowed)
SLOT_SPECS = (
    ("application_form", "Application form", "Optional. A completed candidate application form, if available.", None, False, False, False),
    ("resume", "Resume", "The latest resume. It is read to fill the form.", None, True, True, False),
    ("passport", "Passport", "Add the passport front, back and any additional pages. Each file is kept and read to fill the form.", None, True, True, True),
    ("aadhaar", "Aadhaar", "Optional. Add the Aadhaar front, back and any additional pages, then choose Extract details.", None, False, True, True),
    ("medical", "Medical", "The medical fitness report.", 6, False, False, False),
    ("signed_offer", "Signed offer", "The offer letter signed by the candidate.", 5, False, False, False),
    ("pcc", "Police clearance certificate (PCC)", "The police clearance certificate.", 7, False, False, False),
    ("visa", "Visa", "The visa received for the candidate.", 9, False, False, False),
    ("air_ticket", "Air ticket", "The air ticket.", 11, False, False, False),
    ("undertaking_letter", "Undertaking letter", "The undertaking letter signed by the candidate, before deployment.", 11, False, False, False),
    ("emigration", "Emigration", "The emigration clearance, required after the visa stage.", 10, False, False, False),
    ("insurance", "Insurance", "The candidate's insurance policy, before deployment.", 11, False, False, False),
    ("invoice", "Invoice", "The candidate's invoice, before deployment.", 11, False, False, False),
    ("photo", "Photo", "Optional. A recent passport-size photo (an image).", None, False, False, False),
    ("education_certificate", "Education certificate", "Optional. The highest qualification certificate.", None, False, False, False),
    ("experience_certificate", "Experience certificate", "Optional. Certificates from previous employers.", None, False, False, False),
    ("google_review", "Google review", "Optional. Proof of the candidate's Google review.", None, False, False, False),
    ("visa_stamping", "Visa stamping", "Optional. The passport page with the visa stamp.", None, False, False, False),
    ("other", "Other documents", "Optional. Anything else, several files allowed.", None, False, False, True),
)
SLOTS = tuple(spec[0] for spec in SLOT_SPECS)
READ_SLOTS = tuple(spec[0] for spec in SLOT_SPECS if spec[5])
SLOT_LABELS = {spec[0]: spec[1] for spec in SLOT_SPECS}
# The slots that are not part of the candidate's older Documents list (data.DOCUMENT_SLOTS): stored only.
EXTRA_SLOTS = tuple(slot for slot in SLOTS if slot not in {"resume", "passport", "aadhaar", "photo"})
# Slot keys stored before the form had its own certificate slots; they are folded into the new ones when the payload is built.
LEGACY_SLOTS = {"education": "education_certificate", "experience": "experience_certificate"}
STORED_SLOTS = SLOTS + tuple(LEGACY_SLOTS)
PROCESSING = {"uploaded", "pending", "processing", "retry"}
EXPIRING_DAYS = 180

HEADER = {
    "company": "Careindia Overseas Pvt Ltd",
    "title": "JOB APPLICATION FORM",
    "address": "743/Dhanalakshmi Apartments, 1st Avenue, near reliance digital, Ashok Nagar, Chennai - 600083.",
    "phone": "044 - 24711234",
    "email": "info@careindiaoverseas.com",
}

# (key, docx label, stage or None, listed in the docx). Order is the order shown.
TRACKING = (
    ("prescreening", "Pre-screening", 1, True),
    ("forwarded", "Forwarded to Client", 2, True),
    ("interview_scheduling", "Interview scheduling", 3, False),
    ("interview_result", "Interview result / selection", 4, False),
    ("offer_letter", "Offer Letter", 5, True),
    ("pcc", "PCC", None, True),
    ("medical", "Medical", 6, True),
    ("passport_collection", "Passport collection", 7, False),
    ("advance_payment", "Advance payment", 8, False),
    ("visa", "Visa", 9, True),
    ("visa_stamping", "Visa Stamping", 10, True),
    ("ticket", "Ticket", 11, True),
    ("invoice_generated", "Invoice", None, True),
)
NOT_TRACKED_NOTE = {
    "pcc": "Police clearance is not a stage in the CRM, so it is not tracked here.",
}
FAILED_OUTCOMES = {1: {"Failed"}, 2: {"Not forwarded"}, 4: {"Rejected"}, 6: {"Unfit"}}
CLOSURE_LABELS = {
    "prescreen_failed": "Failed prescreening", "not_interested": "Candidate not interested",
    "resume_not_received": "Resume not received", "interview_rejected": "Rejected at interview",
    "medical_unfit": "Medically unfit", "unmapped": "Removed from the order", "changed_job": "Moved to another job",
}
FOLLOW_UP_LABEL = "Follow-up"
SAME_AS_PHONE = object()
SOURCE_LABELS = {"email": "Email", "whatsapp": "WhatsApp", "walk_in": "Walk-in", "referral": "Referral", "other": "Other"}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().agency_timezone)
    except Exception:  # an unknown zone name must not take the form down
        return ZoneInfo("UTC")


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# ------------------------------------------------------------------ permissions

def can_edit(user: User, candidate) -> bool:
    return user.role in (Role.admin, Role.manager) or (user.role == Role.staff and candidate.owner_id == user.id)


def ensure_can_edit(user: User, candidate) -> None:
    from app.core.deps import ensure_can_work_candidate
    ensure_can_work_candidate(candidate, user)


# ------------------------------------------------------------------ pure derivations (unit-tested without a database)

def expiry_info(expiry: date | None, today: date) -> dict:
    if expiry is None:
        return {"status": "unknown", "daysLeft": None}
    days = (expiry - today).days
    status = "expired" if days < 0 else "expiring_soon" if days <= EXPIRING_DAYS else "valid"
    return {"status": status, "daysLeft": days}


def tracking_payload(application, stage_rows: list, names: dict[str, str], tz: ZoneInfo | None = None,
                     invoice: dict | None = None) -> dict:
    """Rows of section 7 from the application's own stage records.

    `application` is an Application (or anything with status/current_stage/closure_reason) or None;
    `stage_rows` are ApplicationStage records of that application (any order).
    """
    tz = tz or _tz()
    active: dict[int, Any] = {}
    for row in stage_rows:
        if row.reopened_reason is not None:
            continue  # a reopened attempt no longer counts as done
        if row.stage not in active or row.attempt > active[row.stage].attempt:
            active[row.stage] = row
    open_app = application is not None and application.status == ApplicationStatus.active
    gone = application is not None and not open_app and application.status != ApplicationStatus.deployed
    rows = []
    for key, label, stage, listed in TRACKING:
        row = {
            "key": key, "label": label, "systemLabel": stage_by_no(stage).title if stage else None, "stage": stage,
            "extra": not listed, "tracked": stage is not None, "status": "not_started", "checked": False,
            "date": None, "completedAt": None, "completedBy": None, "outcome": None, "attempt": None, "note": NOT_TRACKED_NOTE.get(key),
        }
        if key == "invoice_generated":
            row.update(tracked=True, note="Invoice not uploaded.")
            if invoice is not None:
                uploaded_at = invoice.get("uploadedAt")
                row.update(status="completed", checked=True, completedAt=uploaded_at,
                           date=datetime.fromisoformat(uploaded_at).astimezone(tz).date().isoformat() if uploaded_at else None,
                           completedBy=invoice.get("uploadedBy"), outcome="Uploaded",
                           note="Invoice on file. Upload time is shown; this does not record payment.")
            rows.append(row)
            continue
        if stage is None:
            row["status"] = "not_applicable"
            rows.append(row)
            continue
        record = active.get(stage)
        current = open_app and application.current_stage == stage
        if record is not None:
            row.update(outcome=record.outcome, attempt=record.attempt, completedAt=_iso(record.completed_at),
                       date=record.completed_at.astimezone(tz).date().isoformat() if record.completed_at else None)
            if record.completed_by:
                row["completedBy"] = {"id": record.completed_by, "name": names.get(record.completed_by, "Former team member")}
        if current:
            # Being worked now: a first attempt, a reopened stage, or a repeat after "Unfit: reapplying".
            row["status"] = "in_progress"
            if record is not None:
                row["note"] = f"Previous attempt: {record.outcome}" if record.outcome else None
        elif record is not None:
            failed = record.outcome in FAILED_OUTCOMES.get(stage, ())
            row["status"] = "failed" if failed else "completed"
            row["checked"] = not failed
        elif gone:
            row["status"] = "not_applicable"
            row["note"] = "The application is closed."
        rows.append(row)
    # Invoice evidence is visible but is not a twelfth workflow stage.
    tracked = [r for r in rows if r["stage"] is not None]
    done = sum(1 for r in tracked if r["status"] == "completed")
    summary = {
        "completed": done, "tracked": len(tracked), "percent": round(100 * done / len(tracked)),
        "currentStage": application.current_stage if application is not None else None,
        "currentStageLabel": stage_by_no(application.current_stage).title if application is not None and 1 <= application.current_stage <= 11 else None,
        "applicationStatus": application.status.value if application is not None else "none",
        "closureLabel": CLOSURE_LABELS.get(application.closure_reason.value) if application is not None and application.closure_reason else None,
    }
    return {"summary": summary, "rows": rows}


def _present(value) -> bool:
    return value not in (None, "", [], {})


def completeness_payload(form: dict) -> dict:
    """The counted items of the form: 16 fields (personal, passport, education, experience) plus every document that is `required`."""
    personal, passport, experience = form["personal"], form["passport"], form["experience"]
    sources = form["fieldSources"]
    items = [
        ("personal.candidateName", "Candidate name", "personal", personal["candidateName"], "Candidate name is missing"),
        ("personal.fatherName", "Father's name", "personal", personal["fatherName"], "Father's name is missing"),
        ("personal.dateOfBirth", "Date of birth", "personal", personal["dateOfBirth"], "Date of birth is missing"),
        ("personal.maritalStatus", "Marital status", "personal", personal["maritalStatus"], "Marital status is missing"),
        ("personal.gender", "Gender", "personal", personal["gender"], "Gender is missing"),
        ("personal.nationality", "Nationality", "personal", personal["nationality"], "Nationality is missing"),
        ("personal.languagesKnown", "Languages known", "personal", personal["languagesKnown"], "Languages known are missing"),
        ("personal.mobileNumber", "Mobile number", "personal", personal["mobileNumber"], "Mobile number is missing"),
        ("personal.currentAddress", "Current address", "personal", personal["currentAddress"], "Current address is missing"),
        ("passport.passportNumber", "Passport number", "passport", passport["passportNumber"], "Passport number is missing"),
        ("passport.placeOfIssue", "Passport place of issue", "passport", passport["placeOfIssue"], "Passport place of issue is missing"),
        ("passport.issueDate", "Passport issue date", "passport", passport["issueDate"], "Passport issue date is missing"),
        ("passport.expiryDate", "Passport expiry date", "passport", passport["expiryDate"], "Passport expiry date is missing"),
        ("education", "Educational details", "education", form["education"], "No educational details"),
        ("experience.totalExperience", "Total experience", "experience",
         experience["totalExperience"] > 0 or "experience.totalExperience" in sources or bool(experience["history"]), "Total experience is missing"),
        ("experience.history", "Previous experience", "experience", experience["history"], "No previous experience listed"),
    ]
    missing = [{"key": k, "label": label, "section": section, "kind": "field", "message": message}
               for k, label, section, value, message in items if not _present(value)]
    filled = len(items) - len(missing)
    counted = [entry for entry in form["documents"].values() if entry["required"]]
    total = len(items) + len(counted)
    for entry in counted:
        if entry["uploaded"]:
            filled += 1
        else:
            missing.append({"key": f"documents.{entry['slot']}", "label": entry["label"], "section": "documents", "kind": "document",
                            "message": f"{entry['label']} not uploaded"})
    optional_documents = [{"slot": e["slot"], "label": e["label"], "uploaded": e["uploaded"]}
                          for e in form["documents"].values() if e["optional"]]
    screening = form["screening"]
    optional = [
        ("screening.nativePlace", "Native place", screening["nativePlace"]),
        ("screening.familyMemberNumber", "Family member's number", screening["familyMemberNumber"]),
        ("screening.jobPreference", "Job preference", screening["jobPreference"]),
        ("screening.countryPreference", "Country preference", screening["countryPreference"]),
        ("screening.jobDurationPreference", "Job duration preference", screening["jobDurationPreference"]),
        ("screening.bestTimeToContact", "Best time to contact", screening["bestTimeToContact"]),
        ("screening.interestLevel", "Interest level", screening["interestLevel"]),
        ("screening.serviceChargesStatus", "Service charges", None if screening["serviceChargesStatus"] == "not_discussed" else screening["serviceChargesStatus"]),
    ]
    if screening["interestLevel"] in ("more_interested", "interested", "somewhat_interested"):
        optional.append(("screening.nextFollowUpDate", "Next follow-up date", screening["nextFollowUpDate"]))
    optional_missing = [{"key": k, "label": label, "section": "screening", "kind": "field", "message": f"{label} is not recorded"}
                        for k, label, value in optional if not _present(value)]
    return {"percent": round(100 * filled / total), "filled": filled, "total": total, "missing": missing,
            "optionalDocuments": optional_documents,
            # Screening details of the telemarketer's form: listed, but not part of the percentage above.
            "optional": {"filled": len(optional) - len(optional_missing), "total": len(optional), "missing": optional_missing}}


_INDIAN_PASSPORT = re.compile(r"^(?:[A-Z][0-9]{7}|[A-Z]{2}[0-9]{6})$")


def warnings_for(form: dict, today: date) -> list[str]:
    out = []
    personal, passport = form["personal"], form["passport"]
    number = passport["passportNumber"]
    nationality = (personal["nationality"] or "").strip().casefold()
    if number and nationality in ("", "indian", "india") and not _INDIAN_PASSPORT.match(number.upper()):
        out.append("Check the Indian passport number against the uploaded passport (one letter and seven digits, or two letters and six digits).")
    if passport["expiry"]["status"] == "expired":
        out.append("The passport has expired.")
    elif passport["expiry"]["status"] == "expiring_soon":
        out.append(f"The passport expires in {passport['expiry']['daysLeft']} days.")
    mobile = personal["mobileNumber"]
    if mobile and not normalize_phone(mobile):
        out.append("The mobile number does not look like a valid number.")
    screening = form["screening"]
    call = screening["callOutcome"]
    if call and call["interested"] == "no" and screening["interestLevel"] in ("more_interested", "interested", "somewhat_interested"):
        out.append("The last call recorded the candidate as not interested, but the interest level says interested.")
    if call and call["interested"] == "yes" and screening["interestLevel"] == "not_interested":
        out.append("The last call recorded the candidate as interested, but the interest level says not interested.")
    family = screening["familyMemberNumber"]
    if family and not normalize_phone(family):
        out.append("The family member's number does not look like a valid number.")
    if screening["nextFollowUpDate"] and screening["nextFollowUpDate"] < today.isoformat():
        out.append("The next follow-up date is in the past.")
    return out


# ------------------------------------------------------------------ documents

def extraction_view(job) -> dict | None:
    """How far reading a file has got, from its document job row (needs status, attempts, last_error, draft...)."""
    if job is None:
        return None
    form = (job.draft or {}).get("applicationForm") or {}
    saved = form.get("result") or {}
    base = {"jobId": job.id, "updatedAt": _iso(job.updated_at), "kindsFound": saved.get("kindsFound") or [],
            "filled": saved.get("filled") or [], "kept": saved.get("kept") or [],
            # Details the document reader returned that have no field on the form (path, values), and what was and was not sent.
            "unmapped": saved.get("unmapped") or [], "coverage": saved.get("coverage"),
            "identityMismatch": bool(saved.get("identityMismatch"))}
    if job.status in PROCESSING:
        retry_note = job.last_error if job.status == "retry" else None
        return {**base, "status": "processing", "message": retry_note}
    if job.status == "failed":
        return {**base, "status": "failed", "message": job.last_error or "We could not read this file. Try again, or enter the details by hand."}
    if saved.get("status"):
        return {**base, "status": saved["status"], "message": saved.get("message")}
    if job.status == "needs_review":
        return {**base, "status": "needs_review", "message": job.last_error}
    return {**base, "status": "done", "message": None}


def _document(row, names: dict[str, str]) -> dict:
    return {"attachmentId": row.id, "fileName": row.file_name, "sizeKb": row.size_kb, "version": row.version,
            "url": f"/api/files/{row.id}", "contentType": row.content_type, "uploadedAt": _iso(row.uploaded_at),
            "uploadedBy": {"id": row.uploaded_by, "name": names.get(row.uploaded_by, "Former team member")} if row.uploaded_by else None,
            "identityMismatch": bool(getattr(row, "identity_mismatch", False))}


DEPLOY_STAGE = 11  # completing this stage (Air ticket) deploys the candidate


def required_for_deployment() -> list[tuple[str, str]]:
    """Final-stage inspection checklist in form order; documents never gate manual deployment."""
    return [(slot, label) for slot, label, _hint, from_stage, always, _read, _multiple in SLOT_SPECS
            if always or (from_stage is not None and from_stage <= DEPLOY_STAGE)]


def deployment_gaps(uploaded: set[str]) -> list[dict]:
    """Missing final-stage checklist evidence; older stored keys count for their new slot."""
    have = {LEGACY_SLOTS.get(slot, slot) for slot in uploaded}
    return [{"slot": slot, "label": label} for slot, label in required_for_deployment() if slot not in have]


def missing_required_documents(db: Session, candidate_id: str) -> list[dict]:
    """Candidate-wide inspection gaps; these never gate manual stage movement."""
    records = db.execute(select(Attachment.slot, rejected_expression()).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id, Attachment.slot.in_(STORED_SLOTS)).distinct()).all()
    uploaded = {slot for slot, rejected in records if not rejected}
    wrong = {slot for slot, rejected in records if rejected}
    uploaded.update(slot for slot, saved in manual_document_statuses(db, candidate_id).items() if saved["uploaded"] and slot not in wrong)
    return deployment_gaps(uploaded)


def set_document_status(db: Session, user: User, candidate_id: str, slot: str, uploaded: bool, note: str = "", *,
                        application_id: str | None = None, order_id: str | None = None) -> None:
    """Confirm a checklist item without manufacturing file evidence or running OCR/invoicing."""
    if slot not in SLOTS:
        raise HTTPException(422, "Choose a document from the candidate checklist.")
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update().execution_options(populate_existing=True))
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    ensure_can_edit(user, candidate)
    applications = list(db.scalars(select(Application).where(Application.candidate_id == candidate_id)
                                   .order_by(Application.created_at.desc(), Application.id.desc())))
    if application_id:
        chosen = next((app for app in applications if app.id == application_id), None)
        if chosen is None or (order_id and chosen.order_id != order_id):
            raise HTTPException(404, "This candidate has no such application.")
    elif order_id:
        chosen = next((app for app in applications if app.order_id == order_id), None)
        if chosen is None:
            raise HTTPException(404, "This candidate is not on that order.")
    else:
        chosen = next((app for app in applications if app.status == ApplicationStatus.active), applications[0] if applications else None)
    keys = [slot, *[old for old, new in LEGACY_SLOTS.items() if new == slot]]
    predicate = (Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id) & Attachment.slot.in_(keys)
    if slot == "invoice" and chosen:
        predicate = or_(predicate, (Attachment.entity_type == "application") & (Attachment.entity_id == chosen.id) & (Attachment.slot == "invoice"))
    actual = db.scalar(select(Attachment.id).where(predicate).limit(1))
    if actual:
        if db.scalar(select(Attachment.id).where(predicate, rejected_expression()).limit(1)):
            raise HTTPException(409, "A document belongs to another person. Upload the correct document; it cannot be accepted manually.")
        if not uploaded:
            raise HTTPException(409, "A file is attached to this document. Its checklist status cannot be changed to pending.")
        return
    note = note.strip()
    previous = manual_document_statuses(db, candidate_id).get(slot)
    if previous and previous["uploaded"] == uploaded and previous["note"] == note:
        return
    db.add(AuditEntry(actor_id=user.id, action=DOCUMENT_STATUS_ACTION, target=candidate.name,
        entity_type="candidate", entity_id=candidate_id, at=func.clock_timestamp(),
        meta={"slot": slot, "uploaded": uploaded, "note": note, "application_id": chosen.id if chosen else None}))
    candidate.updated_at = datetime.now(timezone.utc)
    from app.services.change_feed import announce_document_status
    announce_document_status(db, candidate_id)


def _file_reading(row, jobs):
    job = next((job for job in jobs if job.attachment_id == row.id or row.id in
                (((job.draft or {}).get("applicationForm") or {}).get("attachedDocuments") or {}).values()), None)
    return extraction_view(job)


def _combined_reading(files):
    """Keep a failed earlier identity page visible when a newer page finishes reading."""
    reads = [file["extraction"] for file in files if file.get("extraction")]
    if not reads:
        return None
    if len(reads) == 1:
        return reads[0]
    priority = {"processing": 0, "failed": 1, "unreadable": 2, "needs_review": 3, "done": 4}
    primary = min(reads, key=lambda read: priority.get(read["status"], 4))
    result = {**primary}
    if any(read["status"] == "processing" for read in reads):
        result["message"] = "Reading the uploaded files. Each file keeps its own reading result."
    elif any(read["status"] != "done" for read in reads):
        count = sum(read["status"] != "done" for read in reads)
        result["status"] = "needs_review"
        result["message"] = f"{count} file{'s' if count != 1 else ''} need checking. See the reading result beside each file."
    else:
        result["message"] = f"{len(reads)} files read."
    for key in ("filled", "kept", "kindsFound"):
        result[key] = list(dict.fromkeys(value for read in reads for value in read.get(key, [])))
    return result


def document_slots(attachments: list, jobs: list, names: dict[str, str], stage: int | None = None,
                   manual_records: dict | None = None) -> dict:
    """All document slots in order. `stage` is the shown application's current stage (None: no application, only the always-counted ones are required)."""
    by_slot: dict[str, list] = {slot: [] for slot in SLOTS}
    for row in attachments:
        slot = LEGACY_SLOTS.get(row.slot, row.slot)
        if slot in by_slot:
            by_slot[slot].append(row)
    slots = {}
    for slot, label, hint, from_stage, always, readable, multiple in SLOT_SPECS:
        # Newest first. Versions count per stored slot key, so a folded legacy slot is ordered by upload time instead.
        rows = sorted(by_slot[slot], key=lambda r: (r.uploaded_at, r.version), reverse=True)
        current = rows[0] if rows else None
        files = [{**_document(row, names), "extraction": _file_reading(row, jobs) if readable else None} for row in rows] if multiple else []
        for file in files:
            file["identityMismatch"] = file["identityMismatch"] or bool((file["extraction"] or {}).get("identityMismatch"))
            file["extractionPending"] = readable and (file["extraction"] is None or file["extraction"]["status"] == "failed")
            if file["identityMismatch"]:
                file["extractionPending"] = False
        reading = _combined_reading(files) if multiple and readable else _file_reading(current, jobs) if current is not None and readable else None
        current_read = _file_reading(current, jobs) if current is not None and readable else None
        current_pending = readable and (current_read is None or current_read["status"] == "failed")
        current_view = {**_document(current, names), "extractionPending": current_pending} if current else None
        if current_view:
            current_view["identityMismatch"] = current_view["identityMismatch"] or bool((current_read or {}).get("identityMismatch"))
            if current_view["identityMismatch"]:
                current_view["extractionPending"] = False
        rejected = [row for row in rows if getattr(row, "identity_mismatch", False) or bool((_file_reading(row, jobs) or {}).get("identityMismatch"))]
        accepted = len(rows) > len(rejected)
        required = always or (from_stage is not None and stage is not None and stage >= from_stage)
        manual = (manual_records or {}).get(slot)
        slots[slot] = {"slot": slot, "label": label, "hint": hint, "required": required, "requiredFromStage": from_stage,
                       # optional: never counted as missing (experience certificate, other documents)
                       "optional": not always and from_stage is None,
                       "readable": readable, "multiple": multiple, "uploaded": accepted or bool(not rejected and manual and manual["uploaded"]),
                       "fileUploaded": current is not None, "manualStatus": manual,
                       "identityMismatch": bool(rejected),
                       "current": current_view, "versions": len(rows),
                       "history": [_document(r, names) for r in rows[1:]],
                       "files": files,
                       "extraction": reading,
                       "extractionPending": bool(readable and rows and (any(file["extractionPending"] for file in files) if multiple else current_view["extractionPending"]))}
    return slots


def selected_document_attachments(attachments: list, application_id: str | None) -> list:
    """A generated invoice is evidence for its own placement, never another job.

    Older manually uploaded candidate invoices remain a fallback. A placement's
    own invoice takes precedence when both exist; all versions stay in exports.
    """
    invoices = [row for row in attachments if row.entity_type == "application"
                and row.entity_id == application_id and row.slot == "invoice"] if application_id else []
    candidate_files = [row for row in attachments if row.entity_type == "candidate"
                       and not (invoices and row.slot == "invoice")]
    return candidate_files + invoices


# ------------------------------------------------------------------ building the form

def _applications(rows: list) -> list[dict]:
    return [{"id": a.id, "orderId": o.id, "orderRef": o.ref, "clientName": c.name, "position": d.title,
             "country": o.country or c.country, "status": a.status.value, "currentStage": a.current_stage,
             "createdAt": _iso(a.created_at), "selected": False}
            for a, o, c, d, _client in rows]


def _pick(rows: list, application_id: str | None, order_id: str | None):
    if application_id:
        found = next((r for r in rows if r[0].id == application_id), None)
        if found is None:
            raise HTTPException(404, "This candidate has no such application.")
        return found
    if order_id:
        found = next((r for r in rows if r[1].id == order_id), None)
        if found is None:
            raise HTTPException(404, "This candidate is not on that order.")
        return found
    ordered = sorted(rows, key=lambda r: r[0].created_at, reverse=True)
    return next((r for r in ordered if r[0].status == ApplicationStatus.active), ordered[0] if ordered else None)


def build_form(db: Session, candidate_id: str, user: User, *, application_id: str | None = None, order_id: str | None = None) -> dict:
    """Everything the form shows, in two parallel rounds of reads."""
    def read_candidate(session):
        return session.execute(
            select(Candidate, User.name).outerjoin(User, User.id == Candidate.owner_id)
            .options(undefer(Candidate.education), undefer(Candidate.work_history), undefer(Candidate.field_sources))
            .where(Candidate.id == candidate_id)).one_or_none()

    def read_applications(session):
        return list(session.execute(
            select(Application, Order, Company, Designation, Client)
            .join(Order, Order.id == Application.order_id).join(Company, Company.id == Order.company_id)
            .join(Designation, Designation.id == Order.designation_id).join(Client, Client.id == Company.client_id)
            .where(Application.candidate_id == candidate_id)).all())

    def read_attachments(session):
        application_ids = select(Application.id).where(Application.candidate_id == candidate_id)
        return list(session.execute(
            select(Attachment.id, Attachment.slot, Attachment.version, Attachment.file_name, Attachment.size_kb,
                   Attachment.content_type, Attachment.uploaded_at, Attachment.uploaded_by, Attachment.entity_type, Attachment.entity_id, rejected_expression())
            .where(or_(
                (Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id) & Attachment.slot.in_(STORED_SLOTS),
                (Attachment.entity_type == "application") & Attachment.entity_id.in_(application_ids) & (Attachment.slot == "invoice"),
            ))).all())

    def read_jobs(session):
        return list(session.execute(
            select(DocumentExtractionJob.id, DocumentExtractionJob.kind, DocumentExtractionJob.status,
                   DocumentExtractionJob.attempts, DocumentExtractionJob.last_error, DocumentExtractionJob.attachment_id,
                   DocumentExtractionJob.updated_at, DocumentExtractionJob.draft)
            .where(DocumentExtractionJob.candidate_id == candidate_id, DocumentExtractionJob.kind.in_(READ_SLOTS))
            .order_by(DocumentExtractionJob.created_at.desc())).all())

    def read_stages(session):
        return list(session.scalars(
            select(ApplicationStage).join(Application, Application.id == ApplicationStage.application_id)
            .where(Application.candidate_id == candidate_id)))

    def read_names(session):
        # Who uploaded a file or completed a stage, resolved inside the same round as everything else (a superset of the
        # selected application's people, which is harmless): a second round trip just for names is avoided.
        uploaders = select(Attachment.uploaded_by).where(
            or_((Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate_id) & Attachment.slot.in_(STORED_SLOTS),
                (Attachment.entity_type == "application") & (Attachment.slot == "invoice")
                & Attachment.entity_id.in_(select(Application.id).where(Application.candidate_id == candidate_id))))
        completers = select(ApplicationStage.completed_by).join(Application, Application.id == ApplicationStage.application_id).where(
            Application.candidate_id == candidate_id)
        return dict(session.execute(select(User.id, User.name).where(or_(User.id.in_(uploaders), User.id.in_(completers)))).all())

    def read_notes(session):
        # Recruiter notes, and the open stage tasks whose 48-hour SLA window is the stage clock.
        tasks = list(session.execute(
            select(Task.application_id, Task.stage, Task.created_at, Task.sla_started_at, Task.due_at)
            .where(Task.candidate_id == candidate_id, Task.application_id.is_not(None),
                   Task.completed_at.is_(None), Task.cancelled_at.is_(None))).all())
        return form_feedback.read_items(session, candidate_id), tasks

    def read_lists(session):
        # Reference agencies and recruiters for the assignment pickers.
        return form_assignment.read_agencies(session), form_assignment.read_recruiters(session)

    found, application_rows, attachments, jobs, stages, names, (feedback_items, tasks), job_rows, (agencies, recruiters), manual_records = parallel_reads(
        db, read_candidate, read_applications, read_attachments, read_jobs, read_stages, read_names, read_notes,
        lambda session: form_assignment.read_job_options(session, candidate_id), read_lists,
        lambda session: manual_document_statuses(session, candidate_id))
    if found is None:
        raise HTTPException(404, "Candidate not found")
    candidate, owner_name = found
    chosen = _pick(application_rows, application_id, order_id)
    application = chosen[0] if chosen else None
    selected_stages = [s for s in stages if application is not None and s.application_id == application.id]

    tz = _tz()
    today = datetime.now(tz).date()

    sources = fx.sources_of(candidate)
    slots = document_slots(selected_document_attachments(attachments, application.id if application else None),
                           jobs, names, application.current_stage if application is not None else None, manual_records)
    listing = _applications(application_rows)
    for item in listing:
        item["selected"] = application is not None and item["id"] == application.id
    listing.sort(key=lambda item: item["createdAt"] or "", reverse=True)

    order = chosen[1] if chosen else None
    company = chosen[2] if chosen else None
    designation = chosen[3] if chosen else None
    client = chosen[4] if chosen else None

    form = {
        "header": dict(HEADER),
        "capabilities": {"deferredDocumentReading": True},
        "candidate": {"id": candidate.id, "ref": candidate.ref, "name": candidate.name if candidate.name != fx.UNNAMED else "",
                      "status": candidate.status.value, "needsReview": bool(candidate.needs_review), "ownerId": candidate.owner_id,
                      "ownerName": owner_name, "updatedAt": _iso(candidate.updated_at), "canEdit": can_edit(user, candidate)},
        "application": ({"id": application.id, "orderId": application.order_id, "orderRef": order.ref,
                         "status": application.status.value,
                         "closureReason": application.closure_reason.value if application.closure_reason else None,
                         "currentStage": application.current_stage, "createdAt": _iso(application.created_at)}
                        if application is not None else None),
        "applications": listing,
        "photo": slots["photo"]["current"],
        "personal": {
            "candidateName": candidate.name if candidate.name != fx.UNNAMED else "",
            "fatherName": candidate.father_name, "dateOfBirth": _iso(candidate.dob), "maritalStatus": candidate.marital_status,
            "gender": candidate.gender, "nationality": candidate.nationality, "languagesKnown": list(candidate.languages or []),
            "mobileNumber": candidate.phone or "", "currentAddress": candidate.current_address,
            "whatsapp": candidate.whatsapp, "email": candidate.email, "currentCity": candidate.current_city,
            # derived: true when the WhatsApp number is the mobile number (sending whatsappSameAsPhone: true in a save copies it)
            "whatsappSameAsPhone": bool(candidate.whatsapp and candidate.phone and normalize_phone(candidate.whatsapp) == normalize_phone(candidate.phone)),
        },
        "screening": {
            "nativePlace": candidate.native_place, "familyMemberNumber": candidate.family_member_number,
            "jobPreference": list(candidate.designations or []), "countryPreference": candidate.country_preference,
            "jobDurationPreference": candidate.job_duration_preference, "bestTimeToContact": candidate.best_time_to_contact,
            "serviceChargesStatus": candidate.service_charges_status, "interestLevel": candidate.interest_level,
            "nextFollowUpDate": _iso(candidate.next_follow_up_date), "skills": list(candidate.skills or []),
            # read only, from the initial call (POST /api/candidates/{id}/call): the telemarketer's notes and outcome
            "callOutcome": ({"called": bool(candidate.call_called), "interested": candidate.call_interested,
                             "remarks": candidate.call_remarks, "recordedAt": _iso(candidate.call_recorded_at)}
                            if candidate.call_interested else None),
        },
        "passport": {
            "passportNumber": candidate.passport_no, "placeOfIssue": candidate.passport_place,
            "issueDate": _iso(candidate.passport_issue), "expiryDate": _iso(candidate.passport_expiry),
            "expiry": expiry_info(candidate.passport_expiry, today),
            # derived: a passport number is on file or a passport scan was uploaded
            "available": bool(candidate.passport_no) or slots["passport"]["uploaded"],
        },
        "education": [_education_out(r) for r in (candidate.education or []) if isinstance(r, dict)],
        "experience": {
            "totalExperience": candidate.total_experience or 0, "experienceIndia": candidate.experience_india or 0,
            "experienceAbroad": candidate.gulf_experience or 0,
            "history": [_history_out(r) for r in (candidate.work_history or []) if isinstance(r, dict)],
        },
        "documents": slots,
        # Only the last four digits of an Aadhaar card read for this candidate ("XXXX XXXX 1234"); the number itself is never in this payload.
        "aadhaar": {"maskedNumber": fx.aadhaar_of(candidate)} if fx.aadhaar_of(candidate) else None,
        "recruitment": {
            "client": company.name if company else None, "clientContact": client.name if client else None,
            "country": (order.country or company.country) if order else None,
            "position": designation.title if designation else None, "orderRef": order.ref if order else None,
            "source": SOURCE_LABELS.get(candidate.source.value, candidate.source.value), "sourceCode": candidate.source.value,
            "sourceDetail": candidate.source_detail,
            "recruiter": {"id": candidate.owner_id, "name": owner_name or "Former team member"} if candidate.owner_id else None,
        },
        "tracking": tracking_payload(application, selected_stages, names, tz, invoice=slots["invoice"]["current"]),
        "fieldSources": fx.public_sources(sources),
        "documentOrder": list(SLOTS),
        "deployment": _deployment(slots),
        "assignment": form_assignment.build_block(user, candidate, owner_name, chosen, job_rows, agencies, recruiters),
        "feedback": form_feedback.build_block(user, candidate, application, feedback_items, tasks, selected_stages,
                                              can_edit(user, candidate)),
    }
    form["completeness"] = completeness_payload(form)
    form["warnings"] = warnings_for(form, today)
    form["processing"] = any((slot["extraction"] or {}).get("status") == "processing" for slot in slots.values())
    return form


def _deployment(slots: dict) -> dict:
    missing = deployment_gaps({slot for slot, entry in slots.items() if entry["uploaded"]})
    return {"ready": not missing, "missing": missing}


def _education_out(row: dict) -> dict:
    year = row.get("passedYear")
    return {"level": row.get("level"), "passedYear": year if isinstance(year, int) and not isinstance(year, bool) else None,
            "institute": row.get("institute")}


def _history_out(row: dict) -> dict:
    return {key: row.get(key) for key in ("company", "designation", "country", "period")}


# ------------------------------------------------------------------ manual save

SECTION_COLUMNS = {
    "personal": {"candidateName": "name", "fatherName": "father_name", "dateOfBirth": "dob", "maritalStatus": "marital_status",
                 "gender": "gender", "nationality": "nationality", "languagesKnown": "languages", "mobileNumber": "phone",
                 "currentAddress": "current_address", "whatsapp": "whatsapp", "email": "email", "currentCity": "current_city"},
    "passport": {"passportNumber": "passport_no", "placeOfIssue": "passport_place", "issueDate": "passport_issue",
                 "expiryDate": "passport_expiry"},
    "screening": {"nativePlace": "native_place", "familyMemberNumber": "family_member_number",
                  "jobDurationPreference": "job_duration_preference", "countryPreference": "country_preference",
                  "bestTimeToContact": "best_time_to_contact", "serviceChargesStatus": "service_charges_status",
                  "interestLevel": "interest_level", "nextFollowUpDate": "next_follow_up_date", "skills": "skills",
                  "jobPreference": "designations"},
    "experience": {"totalExperience": "total_experience", "experienceIndia": "experience_india", "experienceAbroad": "gulf_experience",
                   "history": "work_history"},
}
TEXT_LIMITS = {"native_place": 120, "family_member_number": 40, "job_duration_preference": 60, "country_preference": 200,
               "best_time_to_contact": 60, "service_charges_status": 20, "interest_level": 20, "name": 200, "father_name": 200, "marital_status": 30, "gender": 20, "nationality": 80, "phone": 40,
               "current_address": 500, "whatsapp": 40, "email": 255, "current_city": 120, "passport_no": 40, "passport_place": 120}


def _rows_from_body(column: str, rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        if column == "education":
            item = {"level": _clip(row.get("level"), 200), "passedYear": row.get("passedYear"), "institute": _clip(row.get("institute"), 300)}
        else:
            item = {"company": _clip(row.get("company"), 200), "designation": _clip(row.get("designation"), 200),
                    "country": _clip(row.get("country"), 80), "period": _clip(row.get("period"), 120)}
        if any(value is not None for value in item.values()):
            out.append(item)
    return out


def _clip(value, limit: int) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:limit] or None


def collect_changes(body: dict) -> dict[str, Any]:
    """Flatten a validated PUT body ({section: {key: value}}, education list) into {column: normalised value}."""
    changes: dict[str, Any] = {}
    for section, keys in SECTION_COLUMNS.items():
        for key, column in keys.items():
            data = body.get(section) or {}
            if key not in data:
                continue
            value = data[key]
            if column in ("dob", "passport_issue", "passport_expiry", "next_follow_up_date"):
                changes[column] = value
            elif column in ("languages", "skills", "designations"):
                changes[column] = normalized_labels(value or [])
            elif column == "work_history":
                changes[column] = _rows_from_body(column, value or [])
            elif column in ("total_experience", "experience_india", "gulf_experience"):
                changes[column] = int(value or 0)
            else:
                text = " ".join(str(value or "").split()) if column != "current_address" else _address_text(value)
                if column == "marital_status" and text:
                    text = fx.normalize_marital(text) or text
                if column == "gender" and text:
                    text = text.lower()
                changes[column] = text[: TEXT_LIMITS.get(column, 255)]
    if (body.get("personal") or {}).get("whatsappSameAsPhone") is True and "whatsapp" not in changes:
        changes["whatsapp"] = SAME_AS_PHONE  # resolved against the mobile number when saving
    if "education" in body and body["education"] is not None:
        changes["education"] = _rows_from_body("education", body["education"])
    return changes


def _address_text(value) -> str:
    parts = [" ".join(p.split()) for p in re.split(r"[\r\n]+", str(value or ""))]
    return ", ".join(p for p in parts if p)


def _validate(candidate, values: dict[str, Any], db: Session | None = None) -> None:
    def final(column):
        return values[column] if column in values else getattr(candidate, column)
    today = datetime.now(_tz()).date()
    if final("dob") and final("dob") > today:
        raise HTTPException(422, "Date of birth cannot be in the future.")
    if final("passport_issue") and final("passport_expiry") and final("passport_expiry") < final("passport_issue"):
        raise HTTPException(422, "Passport expiry cannot precede its issue date.")
    if (final("gulf_experience") or 0) > (final("total_experience") or 0):
        raise HTTPException(422, "Experience abroad cannot exceed total experience.")
    for column in ("total_experience", "experience_india", "gulf_experience"):
        if column in values and not 0 <= values[column] <= 80:
            raise HTTPException(422, "Experience must be between 0 and 80 years.")
    if "education" in values:
        if len(values["education"]) > fx.MAX_EDUCATION:
            raise HTTPException(422, f"List at most {fx.MAX_EDUCATION} educational qualifications.")
        for row in values["education"]:
            year = row["passedYear"]
            if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 1950 <= year <= today.year + 6):
                raise HTTPException(422, f"Passed year must be between 1950 and {today.year + 6}.")
    if "work_history" in values and len(values["work_history"]) > fx.MAX_HISTORY:
        raise HTTPException(422, f"List at most {fx.MAX_HISTORY} previous jobs.")
    if db is not None:
        # The admin-managed lists (crm_form_options); a value the candidate already has stays valid.
        from app.services.form_options import ensure_allowed
        for column in ("service_charges_status", "interest_level"):
            if column in values:
                ensure_allowed(db, column, values[column], current=getattr(candidate, column),
                               default="not_discussed" if column == "service_charges_status" else None)
    follow_up = values.get("next_follow_up_date")
    if follow_up and not today - timedelta(days=3650) <= follow_up <= today + timedelta(days=1830):
        raise HTTPException(422, "Choose a follow-up date within the next five years.")
    if "skills" in values and (len(values["skills"]) > 200 or any(len(x) > 200 for x in values["skills"])):
        raise HTTPException(422, "List at most 200 skills of at most 200 characters each.")
    if "designations" in values and (len(values["designations"]) > 50 or any(len(x) > 200 for x in values["designations"])):
        raise HTTPException(422, "List at most 50 job preferences of at most 200 characters each.")
    if "languages" in values and (len(values["languages"]) > 30 or any(len(x) > 80 for x in values["languages"])):
        raise HTTPException(422, "List at most 30 languages of at most 80 characters each.")
    if "phone" in values and values["phone"] and not normalize_phone(values["phone"]):
        raise HTTPException(422, "Enter a valid phone number with at least seven digits.")
    if "name" in values and not values["name"] and candidate.name not in ("", fx.UNNAMED):
        raise HTTPException(422, "The candidate's name cannot be removed. Replace it instead.")
    if "phone" in values and not values["phone"] and candidate.phone:
        raise HTTPException(422, "The candidate's phone number cannot be removed. Replace it instead.")


def save_form(db: Session, user: User, candidate_id: str, body: dict) -> dict[str, Any]:
    """Apply a partial manual save under the candidate row lock. Returns the changed form keys (caller rebuilds the form)."""
    from app.api.routers.candidates import lock_assignment_cursor
    lock_assignment_cursor(db)
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id)
                          .options(undefer(Candidate.education), undefer(Candidate.work_history), undefer(Candidate.field_sources))
                          .with_for_update().execution_options(populate_existing=True))
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    ensure_can_edit(user, candidate)
    changes = collect_changes(body)
    if changes.get("whatsapp") is SAME_AS_PHONE:
        changes["whatsapp"] = (changes.get("phone") or candidate.phone or "").strip() or None
    changes = {k: (v if k not in ("email", "passport_no") else normalize_candidate_values({k: v})[k] or None) for k, v in changes.items()}
    for column in ("father_name", "marital_status", "gender", "nationality", "current_address", "whatsapp", "email", "current_city",
                   "passport_no", "passport_place", "native_place", "family_member_number", "job_duration_preference",
                   "country_preference", "best_time_to_contact", "interest_level"):
        if column in changes and changes[column] == "":
            changes[column] = None
    if changes.get("service_charges_status") == "":
        changes["service_charges_status"] = "not_discussed"
    _validate(candidate, changes, db)
    identity = {c: changes[c] for c in ("phone", "email", "passport_no") if c in changes and changes[c] and changes[c] != getattr(candidate, c)}
    if identity and identity_matches(db, identity, exclude_id=candidate.id):
        raise HTTPException(409, "The corrected phone, email or passport already belongs to another candidate.")

    sources = fx.sources_of(candidate)
    from_passport = set(sources.get("_fromPassport") or [])
    changed: list[str] = []
    for column, value in changes.items():
        spec = fx.SPECS[column]
        current = getattr(candidate, column)
        entered = spec.kind == "int" or value not in (None, "", [], {})  # an explicit count, even 0, is an entry
        if current == value or (current in (None, "", [], {}) and value in (None, "", [], {})):
            # Unchanged: an extracted value stays "extracted"; anything else a person sent counts as confirmed.
            if entered and sources.get(spec.key) is None:
                sources[spec.key] = "manual"
            continue
        setattr(candidate, column, value)
        changed.append(spec.key)
        if entered:
            sources[spec.key] = "manual"
        else:
            sources.pop(spec.key, None)
        from_passport.discard(spec.key)
    if from_passport:
        sources["_fromPassport"] = sorted(from_passport)
    else:
        sources.pop("_fromPassport", None)
    if sources != fx.sources_of(candidate):
        candidate.field_sources = sources
    if "screening.nextFollowUpDate" in changed:
        sync_follow_up_todo(db, candidate)
    if changed:
        db.add(AuditEntry(actor_id=user.id, action="Updated application form", target=candidate.name, entity_type="candidate",
                          entity_id=candidate.id, meta={"changed_fields": changed}))
    db.commit()
    return {"changed": changed}


def sync_follow_up_todo(db: Session, candidate) -> None:
    """Keep one reminder on the recruiter's to-do list for the candidate's next follow-up date.

    The to-do list and its reminder worker already exist, so the date only has to be mirrored there: set, moved or
    cleared with the date (a to-do the recruiter has already ticked off is left alone and a new one is made for a new date).
    """
    link = f"/candidates/{candidate.id}"
    todo = db.scalar(select(TodoItem).where(TodoItem.link_to == link, TodoItem.link_label == FOLLOW_UP_LABEL,
                                            TodoItem.done.is_(False)).order_by(TodoItem.created_at.desc()).limit(1))
    day = candidate.next_follow_up_date
    if day is None or not candidate.owner_id:
        if todo is not None:
            db.delete(todo)
        return
    due = datetime.combine(day, time(9, 0), tzinfo=_tz())
    title = f"Follow up: {candidate.name}"[:300]
    if todo is None:
        db.add(TodoItem(owner_id=candidate.owner_id, title=title, notes="Next follow-up date set on the application form.",
                        due_at=due, priority=Priority.normal, link_to=link, link_label=FOLLOW_UP_LABEL))
    elif todo.due_at != due or todo.owner_id != candidate.owner_id or todo.title != title:
        todo.due_at, todo.owner_id, todo.title, todo.reminded_at = due, candidate.owner_id, title, None


def refresh_from_stored(db: Session, user: User, candidate_id: str) -> list[str]:
    """Replay the stored OCR output over the form under the usual fill rule. Returns the fields filled."""
    from app.api.routers.candidates import lock_assignment_cursor
    lock_assignment_cursor(db)
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id)
                          .options(undefer(Candidate.education), undefer(Candidate.work_history), undefer(Candidate.field_sources), undefer(Candidate.ocr_data))
                          .with_for_update().execution_options(populate_existing=True))
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    ensure_can_edit(user, candidate)
    result = fx.apply_stored_ocr(candidate, db, force=True)
    if result.filled:
        db.add(AuditEntry(actor_id=user.id, action="Filled application form from stored documents", target=candidate.name,
                          entity_type="candidate", entity_id=candidate.id, meta={"filled_fields": result.filled}))
    db.commit()
    return result.filled


# ------------------------------------------------------------------ reading uploaded documents

def queue_reading(db: Session, user: User, candidate: Candidate, attachment: Attachment, content_type: str) -> DocumentExtractionJob:
    """Queue the stored file for the extraction worker. Same transaction as the attachment row."""
    from app.core.config import get_settings
    from app.services.instance_guard import instance_name
    job = DocumentExtractionJob(
        instance=instance_name(get_settings()),
        id=str(uuid.uuid4()), owner_id=user.id, group_id=str(uuid.uuid4()), kind=attachment.slot, filename=attachment.file_name[:150],
        content_type=content_type, object_key=attachment.object_key, content_hash=attachment.content_hash or "",
        size_kb=attachment.size_kb or 1, status="pending", candidate_id=candidate.id, attachment_id=attachment.id,
        draft={"applicationForm": {"candidateId": candidate.id, "slot": attachment.slot, "attachmentId": attachment.id}},
        queued_at=datetime.now(timezone.utc),
    )
    db.add(job)
    from app.services.extraction_queue import notify_work
    notify_work(db, "manual")  # a sleeping worker starts reading at once (delivered when the caller commits)
    return job


def requeue_failed(db: Session, candidate_id: str, attachment_id: str) -> bool:
    """A failed or reviewed job for the very same file is put back in the queue (the same bytes were sent again)."""
    job = db.scalar(select(DocumentExtractionJob).where(
        DocumentExtractionJob.candidate_id == candidate_id, DocumentExtractionJob.attachment_id == attachment_id)
        .order_by(DocumentExtractionJob.created_at.desc()).limit(1).with_for_update())
    if job is None or job.status not in ("failed",):
        return False
    from copy import deepcopy
    from app.services.document_extraction import request_extraction_retry
    saved = deepcopy(job.state or {})
    request_extraction_retry(saved)
    saved.pop("output", None)
    job.state, job.result = saved, {}
    job.status, job.attempts, job.last_error = "pending", 0, None
    job.next_attempt_at = datetime.now(timezone.utc)
    job.queued_at, job.started_at, job.finished_at, job.claimed_by, job.lease_until = job.next_attempt_at, None, None, None, None
    job.updated_at = datetime.now(timezone.utc)
    from app.services.extraction_queue import notify_work
    notify_work(db, "manual")
    return True


def _attach_read_documents(db: Session, job: DocumentExtractionJob, output: dict, meta: dict, *,
                           derive_source_slot: bool = False) -> dict[str, str]:
    """Link the checked PDFs from a combined upload to their actual document slots.

    The original remains in version history. A newer upload in another slot wins
    over an older queued scan, and a retry reuses the same derived attachments.
    The caller already holds the candidate lock used by ordinary uploads.
    """
    source = db.get(Attachment, meta.get("attachmentId") or job.attachment_id)
    if source is None or source.entity_type != "candidate" or source.entity_id != job.candidate_id:
        return {}
    inspection = output.get("inspection") or {}
    mixed = sum(bool(inspection.get(kind + "_pages")) for kind in fx.SLOT_KINDS) > 1
    documents = (job.state or {}).get("documents") or {}
    attached = dict(meta.get("attachedDocuments") or {})
    for kind in output.get("extractions") or {}:
        document = documents.get(kind)
        if kind not in fx.SLOT_KINDS or not document or (not mixed and kind == source.slot and not derive_source_slot):
            continue
        previous = db.scalar(select(Attachment).where(
            Attachment.entity_type == "candidate", Attachment.entity_id == job.candidate_id, Attachment.slot == kind)
            .order_by(Attachment.uploaded_at.desc(), Attachment.version.desc()).limit(1))
        if previous and previous.content_hash == document["content_hash"]:
            attached[kind] = previous.id
            continue
        # In a bulk folder a later dedicated passport should remain current even
        # if the older combined resume finishes reading after it.
        if previous and previous.object_key != source.object_key and previous.uploaded_at >= source.uploaded_at:
            continue
        row = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=job.candidate_id,
            slot=kind, version=(previous.version if previous else 0) + 1,
            object_key=document["object_key"], content_hash=document["content_hash"],
            file_name=document["file_name"], content_type=document["content_type"], size_kb=document["size_kb"],
            uploaded_by=job.owner_id, uploaded_at=source.uploaded_at)
        db.add(row)
        attached[kind] = row.id
        db.add(AuditEntry(actor_id=job.owner_id, action="Separated a document from a combined upload",
            target=row.file_name, entity_type="candidate", entity_id=job.candidate_id,
            meta={"attachment_id": row.id, "source_attachment_id": source.id, "document_job_id": job.id, "slot": kind}))
    recognized = any(kind in fx.SLOT_KINDS and kind in documents for kind in output.get("extractions") or {})
    if recognized and source.slot in fx.SLOT_KINDS and source.slot not in (output.get("extractions") or {}) \
            and not inspection.get(source.slot + "_pages"):
        # A passport uploaded in the resume control must not count as an uploaded
        # resume. Retain the original by the same ID as a source bundle instead.
        previous_bundle = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate",
            Attachment.entity_id == job.candidate_id, Attachment.slot == "source_bundle")
            .order_by(Attachment.version.desc()).limit(1))
        source.slot, source.version = "source_bundle", (previous_bundle.version if previous_bundle else 0) + 1
    return attached


def _page_numbers(value) -> set[int]:
    return {number for number in (value or []) if type(number) is int and number > 0}


def _mixed_resume_passport_ignores(candidate: Candidate, output: dict, extractions: dict) -> frozenset[str]:
    """Passport fields from a resume are not evidence when a packet has separate passport pages.

    A scanned packet can contain a resume followed by a passport page.  The
    provider occasionally reads one character differently in the resume's
    copied ``passport_details`` section than it reads from the passport page.
    Keep the normal identity guard for the actual passport extraction, while
    allowing the resume's personal, education and work details to be applied.
    This is intentionally limited to packets with *separate* classified pages
    and a passport number extracted from the dedicated passport pages.
    """
    resume = extractions.get("resume") if isinstance(extractions, dict) else None
    passport = extractions.get("passport") if isinstance(extractions, dict) else None
    if not isinstance(resume, dict) or not isinstance(passport, dict):
        return frozenset()
    inspection = output.get("inspection") if isinstance(output, dict) else {}
    inspection = inspection if isinstance(inspection, dict) else {}
    resume_pages = _page_numbers(inspection.get("resume_pages")) or _page_numbers(resume.get("selected_pages"))
    passport_pages = _page_numbers(inspection.get("passport_pages")) or _page_numbers(passport.get("selected_pages"))
    # A page sent to both readers is not a separate source.  Do not weaken its
    # normal identity check merely because it has two classifications.
    if not (resume_pages - passport_pages and passport_pages - resume_pages):
        return frozenset()
    passport_fields = passport.get("fields") if isinstance(passport.get("fields"), dict) else {}
    passport_values = fx.values_for("passport", passport.get("raw") or {})
    if not (passport_values.get("passport_no") or passport_fields.get("passport_no")):
        return frozenset()
    return fx.PASSPORT_DOCUMENT_COLUMNS


def _without_ignored_fields(values, ignored: frozenset[str]) -> dict:
    return {key: value for key, value in (values or {}).items() if key not in ignored}


def _partial_resume_replay_ignores(extraction: object, candidate: Candidate | None = None) -> frozenset[str]:
    """Withhold copied passport fields, placeholder text, and established profile values.

    Without ``candidate`` this is the identity-check set: only fields which
    cannot be trusted from the mixed source itself are omitted. With a
    candidate, every value already present on that profile is also retained in
    the ignored sidecar, so a later force-refresh cannot replace a verified or
    manual value with the partial resume's older OCR.
    """
    extraction = extraction if isinstance(extraction, dict) else {}
    raw = extraction.get("raw") if isinstance(extraction.get("raw"), dict) else {}
    fields = extraction.get("fields") if isinstance(extraction.get("fields"), dict) else {}
    ignored = set(fx.PASSPORT_DOCUMENT_COLUMNS)
    values = fx.values_for("resume", raw)
    address = values.get("current_address") or fields.get("current_address")
    if isinstance(address, str) and re.search(
            r"\[(?:illegible|unreadable|not readable|not legible)\]|\b(?:illegible|unreadable|not readable|not legible)\b",
            address, re.I):
        ignored.add("current_address")
    if candidate is not None:
        sources = fx.sources_of(candidate)
        for column in values:
            spec = fx.SPECS.get(column)
            if spec and not fx._is_empty(spec, getattr(candidate, column), sources.get(spec.key)):
                ignored.add(column)
    return frozenset(ignored)


def apply_job_output(db: Session, job: DocumentExtractionJob, output: dict, *,
                     _partial_verified_resume_replay: bool = False) -> None:
    """Called by the extraction worker in the transaction that completes a form-uploaded job.

    Stores the read documents on the candidate (`ocr_data`, like every other intake) and fills the form under the
    fill rule, then records the outcome on the job so the form can say what happened.
    """
    meta = (job.draft or {}).get("applicationForm")
    if not meta or not job.candidate_id:
        return
    from app.services.candidate_identity import merge_ocr_data
    from app.integrations.ocr.mapping import resume_text_from
    candidate = db.scalar(select(Candidate).where(Candidate.id == job.candidate_id)
                          .options(undefer(Candidate.education), undefer(Candidate.work_history), undefer(Candidate.field_sources), undefer(Candidate.ocr_data))
                          .with_for_update().execution_options(populate_existing=True))
    extractions = (output or {}).get("extractions") or {}
    from app.services.document_identity import identity_conflicts, IDENTITY_REVIEW_MESSAGE, IDENTITY_REJECTED_ACTION, IDENTITY_RESOLVED_ACTION
    identity_resume_ignores = (_partial_resume_replay_ignores(extractions.get("resume")) if _partial_verified_resume_replay else
                               _mixed_resume_passport_ignores(candidate, output or {}, extractions)) if candidate else frozenset()
    resume_ignores = (_partial_resume_replay_ignores(extractions.get("resume"), candidate) if _partial_verified_resume_replay else
                      identity_resume_ignores) if candidate else frozenset()
    identity_problems = sorted({field for kind, extraction in extractions.items()
                                for field in identity_conflicts(candidate, _without_ignored_fields(
                                    extraction.get("fields") or {}, identity_resume_ignores if kind == "resume" else frozenset()), db)}) if candidate else []
    if candidate and not identity_problems:
        identity_problems = sorted({field for kind, extraction in extractions.items()
                                    for field in identity_conflicts(candidate, _without_ignored_fields(
                                        fx.values_for(kind, extraction.get("raw") or {}), identity_resume_ignores if kind == "resume" else frozenset()), db)})
    if identity_problems:
        # Store diagnostics on the job only. Nothing from this document may enter profile OCR/history/derived slots.
        from app.services.candidate_review import record_review_flag
        candidate.needs_review = True
        record_review_flag(db, candidate, ocr_warning=True, attachment_id=meta.get("attachmentId"), document_job_id=job.id)
        job.status, job.last_error = "needs_review", IDENTITY_REVIEW_MESSAGE
        form = {**dict(meta), "result": {"status": "needs_review", "message": IDENTITY_REVIEW_MESSAGE,
                "kindsFound": list(extractions), "filled": [], "kept": [], "identityMismatch": True,
                "coverage": {**((output or {}).get("coverage") or {}), "complete": False,
                             "issues": [IDENTITY_REVIEW_MESSAGE]}}}
        job.draft = {**dict(job.draft or {}), "applicationForm": form}
        db.add(AuditEntry(actor_id=job.owner_id, action=IDENTITY_REJECTED_ACTION,
                         target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                         meta={"attachment_id": meta.get("attachmentId"), "document_job_id": job.id,
                               "conflicting_fields": identity_problems}))
        return
    # A saved, otherwise-complete output can be replayed after a narrowly
    # corrected identity rule. Resolution is appended only after the same
    # source has passed and its successful result is stored below.
    resolving_identity_rejection = (not _partial_verified_resume_replay and job.status == "needs_review"
                                    and job.last_error == IDENTITY_REVIEW_MESSAGE
                                    and (output or {}).get("status") == "complete")
    # Raw warnings stay in the job state (diagnostics); the form shows them in plain words.
    warnings = plain_notes([w for w in (output or {}).get("warnings") or [] if isinstance(w, str)])
    result = fx.ApplyResult()
    found: list[str] = []
    if candidate is not None:
        fx.mark_stored_applied(candidate)
        for kind in sorted(extractions, key=lambda k: {"resume": 0, "aadhaar": 1, "passport": 2}.get(k, 3)):
            extraction = extractions[kind]
            raw = extraction.get("raw")
            if not isinstance(raw, dict) or not raw:
                continue
            found.append(kind)
            incoming = {kind: raw}
            if extraction.get("source"):
                incoming["_sources"] = {kind: extraction["source"]}
            candidate.ocr_data = merge_ocr_data(candidate.ocr_data, incoming)
            ignored = resume_ignores if kind == "resume" else frozenset()
            if ignored:
                fx.remember_ignored_fields(candidate, kind, raw, ignored)
            if kind in fx.SLOT_KINDS:
                result.merge(fx.apply_document(
                    candidate, kind, raw, db, ignore_fields=ignored,
                    only_fill_empty=_partial_verified_resume_replay and kind == "resume"))
            if kind == "resume" and not candidate.resume_text:
                candidate.resume_text = resume_text_from({"resume": raw})
        if result.filled:
            db.add(AuditEntry(actor_id=job.owner_id, action="Filled application form from an uploaded document", target=candidate.name,
                              entity_type="candidate", entity_id=candidate.id,
                              meta={"filled_fields": result.filled, "attachment_id": meta.get("attachmentId")}))
    slot = meta.get("slot")
    if not found:
        status = "unreadable"
        message = warnings[0] if warnings else f"We could not find a {slot} in this file. Check the file and upload it again, or enter the details by hand."
    elif slot in fx.SLOT_KINDS and slot not in found:
        status = "needs_review"
        message = f"This file holds a {' and '.join(found)}, not a {slot}. It was read for what it contains."
    elif job.status == "needs_review" and not resolving_identity_rejection:
        status = "needs_review"
        message = warnings[0] if warnings else "Some pages could not be read. Check the details against the original."
    else:
        status = "done"
        message = None
    form = dict(meta)
    if candidate is not None:
        if _partial_verified_resume_replay:
            form["attachedDocuments"] = _attach_read_documents(
                db, job, output, meta, derive_source_slot=True)
        else:
            form["attachedDocuments"] = _attach_read_documents(db, job, output, meta)
    coverage = (output or {}).get("coverage") or {}
    form["result"] = {"status": status, "message": message, "kindsFound": found, "filled": result.filled, "kept": result.kept + result.conflicts,
                      "unmapped": coverage.get("unmapped") or [],
                      "coverage": {"summary": coverage.get("summary"), "pages": coverage.get("pages"),
                                   "issues": plain_issues(coverage.get("issues") or []), "notes": plain_notes(coverage.get("notes") or []),
                                   "kinds": coverage.get("kinds"), "complete": coverage.get("complete")} if coverage else None}
    job.draft = {**dict(job.draft or {}), "applicationForm": form}
    if resolving_identity_rejection and status == "done" and candidate is not None:
        job.status, job.last_error = "completed", None
        attachment_id = meta.get("attachmentId")
        db.add(AuditEntry(actor_id=job.owner_id, action=IDENTITY_RESOLVED_ACTION,
                          target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                          meta={"attachment_id": attachment_id, "document_job_id": job.id,
                                "resolves": IDENTITY_REJECTED_ACTION}))
        from app.services.candidate_review import clear_resolved_document_review, resolve_document_review_flag
        if resolve_document_review_flag(db, candidate, actor_id=job.owner_id, attachment_id=attachment_id,
                                        document_job_id=job.id):
            clear_resolved_document_review(db, candidate)


_PARTIAL_RESUME_REPLAY_MESSAGE = (
    "Some pages in this file remain unclassified. Verified resume pages were applied; "
    "passport and other pages remain available for review."
)


class _PartialResumeReplayRejected(RuntimeError):
    """A saved mixed packet does not meet the narrow, evidence-backed replay rule."""


def _same_application_form_source(meta: object, *, candidate_id: str, attachment_id: str, slot: str) -> bool:
    return isinstance(meta, dict) and meta.get("candidateId") == candidate_id \
        and meta.get("attachmentId") == attachment_id and meta.get("slot") == slot


def _has_unresolved_identity_rejection(db: Session, *, candidate_id: str, attachment_id: str, job_id: str) -> bool:
    """Require the exact saved rejection, not a candidate-wide or attachment-only warning."""
    from app.services.document_identity import IDENTITY_REJECTED_ACTION, IDENTITY_RESOLVED_ACTION
    rows = db.execute(select(AuditEntry.action, AuditEntry.meta).where(
        AuditEntry.entity_type == "candidate", AuditEntry.entity_id == candidate_id,
        AuditEntry.action.in_((IDENTITY_REJECTED_ACTION, IDENTITY_RESOLVED_ACTION)),
    )).all()
    rejected = resolved = False
    for action, entry_meta in rows:
        if not isinstance(entry_meta, dict) or entry_meta.get("attachment_id") != attachment_id \
                or entry_meta.get("document_job_id") != job_id:
            continue
        if action == IDENTITY_REJECTED_ACTION:
            rejected = True
        elif action == IDENTITY_RESOLVED_ACTION:
            resolved = True
    return rejected and not resolved


def _has_local_passport_anchor(candidate: Candidate, state: dict) -> bool:
    """The local MRZ is evidence only when its number and checksums both passed."""
    patterns = state.get("local_patterns") if isinstance(state, dict) else None
    passport = patterns.get("passport") if isinstance(patterns, dict) else None
    target = normalize_passport(getattr(candidate, "passport_no", None))
    return bool(target and isinstance(passport, dict) and passport.get("number_ok")
                and passport.get("mrz_checksums_valid")
                and normalize_passport(passport.get("passport_number")) == target)


def _has_separate_passport_anchor(db: Session, candidate: Candidate, source: Attachment) -> bool:
    """A completed, candidate-owned passport job can anchor a different mixed resume packet."""
    target = normalize_passport(getattr(candidate, "passport_no", None))
    if not target:
        return False
    attachments = db.scalars(select(Attachment).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
        Attachment.slot == "passport", Attachment.id != source.id,
    )).all()
    for attachment in attachments:
        jobs = db.scalars(select(DocumentExtractionJob).where(
            DocumentExtractionJob.candidate_id == candidate.id,
            DocumentExtractionJob.attachment_id == attachment.id,
            DocumentExtractionJob.kind == "passport",
            DocumentExtractionJob.status == "completed",
        )).all()
        for passport_job in jobs:
            meta = (passport_job.draft or {}).get("applicationForm") if isinstance(passport_job.draft, dict) else None
            state = passport_job.state if isinstance(passport_job.state, dict) else {}
            output = state.get("output") if isinstance(state.get("output"), dict) else None
            extractions = output.get("extractions") if isinstance(output, dict) else None
            extraction = extractions.get("passport") if isinstance(extractions, dict) else None
            raw = extraction.get("raw") if isinstance(extraction, dict) else None
            if not (_same_application_form_source(meta, candidate_id=candidate.id, attachment_id=attachment.id, slot="passport")
                    and isinstance(output, dict) and output.get("status") == "complete" and isinstance(raw, dict)):
                continue
            if normalize_passport(fx.values_for("passport", raw).get("passport_no")) == target:
                return True
    return False


def _resume_replay_identity_is_safe(db: Session, candidate: Candidate, resume: dict) -> bool:
    """The resume itself must still pass normal identity validation after passport fields are withheld."""
    from app.services.document_identity import identity_conflicts
    fields = resume.get("fields") if isinstance(resume.get("fields"), dict) else {}
    raw = resume.get("raw") if isinstance(resume.get("raw"), dict) else {}
    ignored = _partial_resume_replay_ignores(resume)
    checked_fields = _without_ignored_fields(fields, ignored)
    checked_raw = _without_ignored_fields(fx.values_for("resume", raw), ignored)
    return not identity_conflicts(candidate, checked_fields, db) and not identity_conflicts(candidate, checked_raw, db)


def replay_saved_partial_resume(db: Session, *, candidate_id: str, job_id: str) -> bool:
    """Safely apply only verified resume pages from a saved mixed packet.

    This is a maintenance repair for a very narrow historical failure: a resume
    packet was rejected because unrelated passport/unknown pages were included
    in the same source file. The original packet and every unresolved page
    remain in the saved job. Nothing is re-read, re-uploaded, or sent to a
    provider. ``False`` means the evidence was incomplete and no row changed.
    """
    from app.services.document_identity import IDENTITY_REJECTED_ACTION, IDENTITY_REVIEW_MESSAGE, IDENTITY_RESOLVED_ACTION

    # Session factories used by maintenance commands deliberately disable
    # autoflush. A flush makes the exact audit check below see committed-or-
    # pending evidence without weakening its scope.
    db.flush()
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id)
                          .options(undefer(Candidate.education), undefer(Candidate.work_history),
                                   undefer(Candidate.field_sources), undefer(Candidate.ocr_data))
                          .with_for_update().execution_options(populate_existing=True))
    job = db.scalar(select(DocumentExtractionJob).where(
        DocumentExtractionJob.id == job_id, DocumentExtractionJob.candidate_id == candidate_id,
        DocumentExtractionJob.kind == "resume",
    ).with_for_update().execution_options(populate_existing=True))
    if candidate is None or job is None or job.status != "needs_review" or job.last_error != IDENTITY_REVIEW_MESSAGE:
        return False
    meta = (job.draft or {}).get("applicationForm") if isinstance(job.draft, dict) else None
    attachment_id = job.attachment_id
    if not isinstance(attachment_id, str) or not _same_application_form_source(
            meta, candidate_id=candidate.id, attachment_id=attachment_id, slot="resume"):
        return False
    source = db.scalar(select(Attachment).where(Attachment.id == attachment_id).with_for_update())
    if source is None or source.entity_type != "candidate" or source.entity_id != candidate.id \
            or source.slot != "resume" or source.object_key != job.object_key \
            or source.content_hash != job.content_hash:
        return False
    state = job.state if isinstance(job.state, dict) else {}
    saved = state.get("output") if isinstance(state.get("output"), dict) else None
    extractions = saved.get("extractions") if isinstance(saved, dict) else None
    resume = extractions.get("resume") if isinstance(extractions, dict) else None
    passport = extractions.get("passport") if isinstance(extractions, dict) else None
    inspection = saved.get("inspection") if isinstance(saved, dict) else None
    documents = state.get("documents") if isinstance(state.get("documents"), dict) else None
    derived_resume = documents.get("resume") if isinstance(documents, dict) else None
    derived_passport = documents.get("passport") if isinstance(documents, dict) else None
    if not (isinstance(saved, dict) and saved.get("status") == "needs_review" and isinstance(resume, dict)
            and isinstance(resume.get("raw"), dict) and isinstance(passport, dict)
            and isinstance(inspection, dict) and isinstance(derived_resume, dict)):
        return False
    resume_pages = _page_numbers(inspection.get("resume_pages"))
    passport_pages = _page_numbers(inspection.get("passport_pages"))
    selected_resume_pages = _page_numbers(resume.get("selected_pages"))
    selected_passport_pages = _page_numbers(passport.get("selected_pages"))
    document_pages = _page_numbers(derived_resume.get("selected_pages"))
    if not resume_pages or not passport_pages or resume_pages.intersection(passport_pages) \
            or selected_resume_pages != resume_pages or selected_passport_pages != passport_pages \
            or document_pages != resume_pages \
            or (isinstance(derived_passport, dict)
                and _page_numbers(derived_passport.get("selected_pages")) != passport_pages):
        return False
    if not all(isinstance(derived_resume.get(key), str) and derived_resume[key] for key in
               ("object_key", "content_hash", "file_name", "content_type")):
        return False
    if not _has_unresolved_identity_rejection(
            db, candidate_id=candidate.id, attachment_id=source.id, job_id=job.id):
        return False
    if not (_has_local_passport_anchor(candidate, state) or _has_separate_passport_anchor(db, candidate, source)):
        return False
    if not _resume_replay_identity_is_safe(db, candidate, resume):
        return False

    # Copy only the accepted resume reader result. Its inspection is similarly
    # narrowed, so _attach_read_documents cannot derive the weak embedded
    # passport section. The full output remains unchanged in ``job.state``.
    partial_output = deepcopy(saved)
    partial_output["extractions"] = {"resume": deepcopy(resume)}
    partial_output["inspection"] = {"resume_pages": sorted(resume_pages)}
    try:
        with db.begin_nested():
            apply_job_output(db, job, partial_output, _partial_verified_resume_replay=True)
            form = (job.draft or {}).get("applicationForm") if isinstance(job.draft, dict) else None
            result = form.get("result") if isinstance(form, dict) else None
            attached = result is not None and form.get("attachedDocuments")
            derived_id = attached.get("resume") if isinstance(attached, dict) else None
            db.flush()
            derived = db.get(Attachment, derived_id) if isinstance(derived_id, str) else None
            if not (isinstance(result, dict) and result.get("status") == "needs_review" and derived is not None
                    and derived.id != source.id and derived.entity_type == "candidate"
                    and derived.entity_id == candidate.id and derived.slot == "resume"
                    and derived.content_hash == derived_resume["content_hash"]):
                raise _PartialResumeReplayRejected()

            # The uploaded bundle is historical evidence, not a Resume. The
            # newly derived Resume above contains only its checked resume pages.
            previous_bundle = db.scalar(select(Attachment).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                Attachment.slot == "source_bundle",
            ).order_by(Attachment.version.desc()).limit(1))
            source.slot = "source_bundle"
            source.version = (previous_bundle.version if previous_bundle else 0) + 1
            db.add(AuditEntry(actor_id=job.owner_id, action="Retained mixed upload as source bundle",
                              target=source.file_name, entity_type="candidate", entity_id=candidate.id,
                              meta={"attachment_id": source.id, "document_job_id": job.id,
                                    "derived_resume_attachment_id": derived.id,
                                    "resume_pages": sorted(resume_pages)}))

            result["status"] = "needs_review"
            result["message"] = _PARTIAL_RESUME_REPLAY_MESSAGE
            job.status, job.last_error = "needs_review", _PARTIAL_RESUME_REPLAY_MESSAGE
            # Resolve only the exact old false-positive rejection. We
            # intentionally do not resolve the candidate-wide OCR review flag:
            # unknown pages remain visible for an administrator to inspect.
            db.add(AuditEntry(actor_id=job.owner_id, action=IDENTITY_RESOLVED_ACTION,
                              target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                              meta={"attachment_id": source.id, "document_job_id": job.id,
                                    "resolves": IDENTITY_REJECTED_ACTION}))
    except _PartialResumeReplayRejected:
        return False
    return True


def apply_local_patterns(db: Session, candidate_id: str, patterns: dict) -> list[str]:
    """Fill the form at once from what the LOCAL reading of the pages verified, before (or without) the online reader.

    Only values with their own check digit are used: the passport number, date of birth and expiry from the machine-readable
    zone (plus the names and nationality when the whole zone checks out), and the email and mobile number printed on a
    resume. They are stamped "extracted", so the online reader's answer, when it arrives, replaces them under the same fill
    rule, and anything a person typed is never touched. Returns the form fields that were filled.
    """
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id)
                          .options(undefer(Candidate.education), undefer(Candidate.work_history), undefer(Candidate.field_sources))
                          .with_for_update().execution_options(populate_existing=True))
    if candidate is None:
        return []
    filled: list[str] = []
    passport = patterns.get("passport") or {}
    mrz: dict = {}
    if passport.get("number_ok"):
        mrz["passport_number"] = passport.get("passport_number")
    if passport.get("birth_ok"):
        mrz["date_of_birth"] = passport.get("date_of_birth")
    if passport.get("expiry_ok"):
        mrz["expiry_date"] = passport.get("date_of_expiry")
    if passport.get("mrz_checksums_valid"):
        mrz.update(given_names=passport.get("given_names"), surname=passport.get("surname"), nationality=passport.get("nationality"))
    if mrz:
        from app.services.document_identity import identity_conflicts
        values = fx.passport_values({"mrz": mrz})
        # Early local filling requires an identity anchor. Resume contacts alone cannot identify their owner.
        anchored = bool(values.get("name") or (candidate.passport_no and values.get("passport_no") == candidate.passport_no))
        if anchored and not identity_conflicts(candidate, values, db):
            filled += fx.apply_values(candidate, "passport", values, db).filled
    if filled:
        from app.services.candidate_review import profile_gaps, release_ownership_review
        release_ownership_review(candidate, profile_gaps(candidate))   # a phone found lifts "needs review" when that was the only gap
        db.add(AuditEntry(actor_id=None, action="Filled application form from the local reading", target=candidate.name,
                          entity_type="candidate", entity_id=candidate.id, meta={"filled_fields": filled}))
    db.commit()
    return filled


# ------------------------------------------------------------------ backfill

def backfill(db: Session, *, candidate_id: str | None = None, dry_run: bool = False, batch: int = 100) -> dict:
    """Fill the new form columns of existing candidates from their stored OCR output. Idempotent."""
    statement = select(Candidate.id).where(Candidate.ocr_data.is_not(None)).order_by(Candidate.created_at, Candidate.id)
    if candidate_id:
        statement = statement.where(Candidate.id == candidate_id)
    ids = list(db.scalars(statement))
    touched = filled = 0
    for start in range(0, len(ids), batch):
        for identifier in ids[start:start + batch]:
            candidate = db.scalar(select(Candidate).where(Candidate.id == identifier)
                                  .options(undefer(Candidate.education), undefer(Candidate.work_history),
                                           undefer(Candidate.field_sources), undefer(Candidate.ocr_data))
                                  .with_for_update().execution_options(populate_existing=True))
            if candidate is None:
                continue
            result = fx.apply_stored_ocr(candidate, db, force=True, fill_all=False)
            if result.filled:
                touched += 1
                filled += len(result.filled)
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return {"candidates": len(ids), "updated": touched, "fieldsFilled": filled, "dryRun": dry_run}
