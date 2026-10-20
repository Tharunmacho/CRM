"""Durable mail receipt, document checkpoints and atomic candidate ingestion.

The worker is the only caller of processing. Provider and OCR work runs between
short database transactions; a PostgreSQL session advisory lock selects one worker.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.routers.candidates import lock_assignment_cursor, next_owner
from app.db.models import (
    Attachment, AuditEntry, Candidate, CandidateStatus, Designation, EmailIngestionJob,
    MailAccount, MailAttachment, MailMessage, Notification, Role, Source, Task, TaskKind, User,
)
from app.integrations.mail.imap import IMAPMailbox, MailMessageError, require_cursor_source
from app.integrations.ocr.mapping import parse_loose_date
from app.integrations.ocr.inspection import restore_inspection
from app.services.document_extraction import (
    extract_attachment, ExtractionPending, request_extraction_retry,
)
from app.services.mail_config import configured_mailboxes

log = logging.getLogger(__name__)


class IntakeReviewError(ValueError):
    """An actionable, deliberately sanitized reason to hold a receipt for review."""


def now():
    return datetime.now(timezone.utc)


def store_original(settings, account_id: str, message_key: str, ordinal: int, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    receipt = hashlib.sha256(message_key.encode()).hexdigest()
    key = f"email/{account_id}/{receipt}/{ordinal}-{digest}.bin"
    target = Path(settings.storage_dir) / key
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return key


def receive_account(account_id, *, sessions, settings):
    """Commit originals/receipts before advancing the IMAP cursor; never mark read."""
    configs = configured_mailboxes(settings)
    with sessions() as db:
        account = db.get(MailAccount, account_id)
        if not account:
            return
        address, cursor = account.address, dict(account.sync_state)
        auto = account.auto_ingest
    if address not in configs:
        raise ValueError("Mailbox credentials are missing from the server environment.")
    require_cursor_source(cursor, configs[address])
    with IMAPMailbox(configs[address]) as mailbox:
        uids = mailbox.list_uids(after_uid=cursor["last_uid"],
                                 uid_validity=cursor["uid_validity"], limit=25)
        for uid in uids:
            try:
                incoming = mailbox.fetch(uid, uid_validity=cursor["uid_validity"])
            except MailMessageError as exc:
                # A partial download or temporary provider outage must be fetched
                # again from the same cursor, never converted to a skipped receipt.
                if exc.retryable:
                    raise
                # Oversized/damaged mail gets a visible durable failure, not a silent skip.
                with sessions() as db:
                    message = db.scalar(select(MailMessage).where(
                        MailMessage.account_id == account_id, MailMessage.provider_message_id == exc.provider_id))
                    if not message:
                        message = MailMessage(account_id=account_id, provider_message_id=exc.provider_id,
                                              subject="Message could not be imported", received_at=now())
                        db.add(message)
                        db.flush()
                        db.add(EmailIngestionJob(message_id=message.id, status="failed",
                                                last_error=str(exc), state={"receive_error": True}))
                    account = db.get(MailAccount, account_id)
                    account.sync_state = {**cursor, "last_uid": uid}
                    db.commit()
                cursor["last_uid"] = uid
                continue
            stored = [
                (part, store_original(settings, account_id, incoming.provider_id, index, part.data))
                for index, part in enumerate(incoming.attachments)
            ]
            with sessions() as db:
                message = db.scalar(select(MailMessage).where(
                    MailMessage.account_id == account_id, MailMessage.provider_message_id == incoming.provider_id))
                if message is None:
                    message = MailMessage(
                        account_id=account_id, provider_message_id=incoming.provider_id,
                        from_name=incoming.from_name[:200], from_address=incoming.from_address[:255],
                        subject=incoming.subject, body=incoming.body, received_at=incoming.received_at,
                        unread=incoming.unread,
                    )
                    db.add(message)
                    db.flush()
                    for part, key in stored:
                        db.add(MailAttachment(
                            message_id=message.id, file_name=part.filename[:255],
                            size_kb=(len(part.data) + 1023) // 1024,
                            object_key=key, content_hash=part.content_hash,
                        ))
                    if auto and stored:
                        db.add(EmailIngestionJob(message_id=message.id, state={}))
                account = db.get(MailAccount, account_id)
                account.sync_state = {**cursor, "last_uid": uid}
                account.worker_seen_at = now()
                db.commit()
            cursor["last_uid"] = uid
    with sessions() as db:
        account = db.get(MailAccount, account_id)
        account.last_sync_at, account.last_error, account.sync_requested = now(), None, False
        account.worker_seen_at = now()
        db.commit()


def _person_key(value):
    return re.sub(r"\W", "", (value or "").casefold())


def combine_extractions(results: list[dict]) -> tuple[dict, dict, bool]:
    """Merge only evidence for one person. Sender and filenames are never identity."""
    fields, raw = {}, {}
    names = set()
    needs_review = False
    identity = {}
    for result in results:
        needs_review = needs_review or bool(result.get("warnings"))
        for kind, extraction in result.get("extractions", {}).items():
            current = extraction["fields"]
            if current.get("name"):
                names.add(_person_key(current["name"]))
            for key in ("phone", "email", "passport_no", "dob", "gender"):
                value = current.get(key)
                normalized = (
                    re.sub(r"\D", "", value)[-10:] if key == "phone" and value
                    else str(parse_loose_date(str(value)) or "") if key == "dob" and value
                    else str(value or "").strip().casefold()
                )
                if normalized and key in identity and identity[key] != normalized:
                    raise IntakeReviewError("The attachments contain conflicting identities. Review them before creating a candidate.")
                if normalized:
                    identity[key] = normalized
            if kind in raw:
                # Distinct documents can be retained without silently overwriting evidence.
                raw.setdefault("additional_documents", []).append({kind: extraction["raw"]})
            else:
                raw[kind] = extraction["raw"]
            for key, value in current.items():
                if value not in (None, "", [], {}) and fields.get(key) in (None, "", [], {}):
                    fields[key] = value
            needs_review = needs_review or bool(extraction.get("warnings"))
    if len(names) > 1:
        raise IntakeReviewError("The documents name different people. Review and separate their files before retrying.")
    return fields, raw, needs_review


def _duplicates(db, fields):
    clauses = []
    digits = re.sub(r"\D", "", fields.get("phone") or "")
    if len(digits) >= 10:
        clauses.append(func.right(func.regexp_replace(Candidate.phone, r"\D", "", "g"), 10) == digits[-10:])
    if fields.get("email"):
        clauses.append(func.lower(func.trim(Candidate.email)) == fields["email"].strip().lower())
    if fields.get("passport_no"):
        clauses.append(func.upper(func.trim(Candidate.passport_no)) == fields["passport_no"].strip().upper())
    return list(db.scalars(select(Candidate).where(or_(*clauses)).with_for_update())) if clauses else []


def commit_candidate(db: Session, job: EmailIngestionJob, results: list[dict]):
    """Candidate, immutable file links, assignment, first task and receipt commit together."""
    fields, raw, review = combine_extractions(results)
    message = db.get(MailMessage, job.message_id)
    # Serialize identity lookup and assignment together. Checking for duplicates
    # before acquiring this lock allows two concurrent receipts to create one
    # person twice. The singleton is also initialized safely on an empty database.
    pointer = lock_assignment_cursor(db)
    matches = _duplicates(db, fields)
    if len(matches) > 1:
        raise IntakeReviewError("Phone, email or passport match different existing candidates. Review the conflict.")
    created = not matches
    values = {}
    for name in ("name", "phone", "email", "whatsapp", "gender", "nationality", "current_city",
                 "languages", "passport_no", "passport_place", "skills", "total_experience", "gulf_experience"):
        if fields.get(name) is not None:
            values[name] = fields[name]
    for name in ("dob", "passport_issue", "passport_expiry"):
        if fields.get(name):
            values[name] = parse_loose_date(str(fields[name]))
    if fields.get("designation"):
        values["designations"] = [fields["designation"]]
    review = review or not values.get("name") or not values.get("phone")
    if created:
        owner = next_owner(db, pointer=pointer)
        # A source-derived reference is stable across retries and cannot collide with
        # the legacy manual count-based CND-1001 references.
        candidate = Candidate(
            ref=f"CND-E-{job.id.replace('-', '')}", name=values.pop("name", None) or "Name needs review",
            phone=values.pop("phone", None) or "", source=Source.email,
            source_detail=f"email_message:{message.id}", owner_id=owner.id if owner else None,
            needs_review=review or owner is None,
            status=CandidateStatus.needs_review if review or owner is None else CandidateStatus.new,
            ocr_data=raw, **values,
        )
        db.add(candidate)
        db.flush()
        if owner:
            db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest",
                        candidate_id=candidate.id, owner_id=owner.id, created_at=now()))
        recipients = set(db.scalars(select(User.id).where(User.role == Role.admin, User.active.is_(True))))
        if owner:
            recipients.add(owner.id)
        for recipient in recipients:
            db.add(Notification(user_id=recipient, kind="candidate_assignment",
                                title="Candidate received by email", detail=candidate.name,
                                link_to=f"/candidates/{candidate.id}"))
    else:
        candidate = matches[0]
        if candidate.dob and values.get("dob") and candidate.dob != values["dob"]:
            raise IntakeReviewError("The date of birth conflicts with the existing candidate. Review the source documents.")
        if candidate.gender and values.get("gender") and candidate.gender.casefold() != values["gender"].casefold():
            raise IntakeReviewError("The gender conflicts with the existing candidate. Review the source documents.")
        for name, value in values.items():
            if getattr(candidate, name) in (None, "", [], 0):
                setattr(candidate, name, value)
        previous = copy.deepcopy(candidate.ocr_data or {})
        for kind, value in raw.items():
            if kind not in previous:
                previous[kind] = value
            else:
                previous.setdefault("additional_documents", []).append({kind: value})
        candidate.ocr_data = previous
        # Existing ownership, status and human-corrected populated fields are preserved.
    text = "\n".join(page.get("text", "") for result in results
                     for kind, part in result.get("extractions", {}).items() if kind == "resume"
                     for page in part["raw"].get("pages", []))
    if text and not candidate.resume_text:
        candidate.resume_text = text
    attachments = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id == message.id)))
    for attachment in attachments:
        document = job.state.get(attachment.id, {})
        links = [{
            "slot": "source_bundle", "file_name": attachment.file_name,
            "size_kb": attachment.size_kb, "object_key": attachment.object_key,
            "content_hash": attachment.content_hash, "content_type": "application/octet-stream",
        }]
        links.extend({"slot": kind, **item} for kind, item in document.get("documents", {}).items())
        for link in links:
            if db.scalar(select(Attachment.id).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                Attachment.slot == link["slot"], Attachment.content_hash == link["content_hash"],
            )):
                continue
            version = (db.scalar(select(func.max(Attachment.version)).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                Attachment.slot == link["slot"])) or 0) + 1
            db.add(Attachment(entity_type="candidate", entity_id=candidate.id, version=version, **link))
            db.flush()
    message.candidate_id = candidate.id
    job.candidate_id, job.status, job.last_error = candidate.id, "completed", None
    db.add(AuditEntry(action="Ingested email candidate" if created else "Attached email candidate evidence",
                      target=candidate.name, entity_type="candidate", entity_id=candidate.id))


def _inspection_summary(state, attachments):
    """Only counts and page selections may enter the non-deferred inbox summary."""
    summaries = []
    for attachment in attachments:
        document = state.get(attachment.id, {})
        output = document.get("output") or {}
        report = document.get("inspection") or output.get("inspection")
        if not isinstance(report, dict):
            continue
        summary = {
            "attachmentId": attachment.id, "fileName": attachment.file_name,
            "pageCount": report.get("page_count", 0),
            "status": output.get("status") or document.get("extraction_status") or "processing",
        }
        for wire, key in (
            ("resumePages", "resume_pages"), ("passportPages", "passport_pages"),
            ("aadhaarPages", "aadhaar_pages"), ("unreadPages", "unread_pages"),
            ("unknownPages", "unknown_pages"),
        ):
            summary[wire] = [number for number in report.get(key, []) if type(number) is int]
        summaries.append(summary)
    return summaries


def _cached_document(db, attachment):
    """Reuse a successfully committed extraction for identical original bytes."""
    candidates = db.execute(
        select(MailAttachment.id, EmailIngestionJob.state)
        .join(EmailIngestionJob, EmailIngestionJob.message_id == MailAttachment.message_id)
        .where(
            MailAttachment.content_hash == attachment.content_hash,
            MailAttachment.id != attachment.id,
            EmailIngestionJob.status == "completed",
        )
        .order_by(EmailIngestionJob.updated_at.desc())
    )
    for prior_id, state in candidates:
        document = state.get(prior_id, {})
        output = document.get("output") or {}
        if document.get("source_sha256") == attachment.content_hash and output.get("status") == "complete":
            saved = copy.deepcopy(document)
            saved["reused_from_attachment_id"] = prior_id
            # Derived files belong to the new receipt's storage namespace.
            # Rebuild those from the verified original without repeating OCR.
            saved.pop("documents", None)
            return saved
    return None


def _save_selected_documents(settings, account_id, attachment, data, result):
    """Build downloadable per-kind PDFs outside candidate/database transactions."""
    inspected = restore_inspection(
        data, result["inspection"],
        document_timeout=settings.ocr_document_timeout_seconds,
    )
    documents = {}
    for kind, extraction in result["extractions"].items():
        if kind not in {"resume", "passport", "aadhaar"}:
            raise IntakeReviewError("An unrecognized document type needs review.")
        selected = extraction["selected_pages"]
        if selected != getattr(inspected, f"{kind}_pages"):
            raise IntakeReviewError("The extracted document pages do not match the saved inspection.")
        content = inspected.pdf_for_pages(selected)
        key = store_original(
            settings, account_id, f"{attachment.message_id}:{attachment.id}:{kind}", 0, content,
        )
        documents[kind] = {
            "object_key": key, "content_hash": hashlib.sha256(content).hexdigest(),
            "size_kb": (len(content) + 1023) // 1024, "file_name": f"{kind}.pdf",
            "content_type": "application/pdf",
        }
    return documents


def process_job(job_id, *, sessions, settings):
    with sessions() as db:
        job = db.get(EmailIngestionJob, job_id)
        if not job or job.status not in {"pending", "processing", "retry"}:
            return
        job.status = "processing"
        state = copy.deepcopy(job.state or {})
        attachments = list(db.scalars(select(MailAttachment).where(MailAttachment.message_id == job.message_id).order_by(MailAttachment.id)))
        titles = list(db.scalars(select(Designation.title)))
        account_id = db.get(MailMessage, job.message_id).account_id
        db.commit()

    def checkpoint(attachment_id, document_state):
        state[attachment_id] = copy.deepcopy(document_state)
        with sessions() as db:
            job = db.get(EmailIngestionJob, job_id)
            job.state = copy.deepcopy(state)
            job.inspection = _inspection_summary(state, attachments)
            job.updated_at = now()
            db.get(MailAccount, account_id).worker_seen_at = now()
            db.commit()

    try:
        results = []
        for attachment in attachments:
            document_state = state.get(attachment.id, {})
            result = document_state.get("output")
            data = (Path(settings.storage_dir) / attachment.object_key).read_bytes()
            if hashlib.sha256(data).hexdigest() != attachment.content_hash:
                raise IntakeReviewError("Stored attachment checksum mismatch. Restore the original file before retrying.")
            if result is None:
                # An explicit re-extraction may select different pages. Derived
                # documents are valid only alongside the output that created them.
                document_state.pop("documents", None)
                # A provider job already in progress belongs to this receipt.
                # Resume it; only untouched documents can borrow a completed result.
                cached = None
                if not document_state.get("provider_jobs"):
                    for prior_id, prior in state.items():
                        if (
                            prior_id != attachment.id
                            and isinstance(prior, dict)
                            and prior.get("source_sha256") == attachment.content_hash
                            and (prior.get("output") or {}).get("status") == "complete"
                        ):
                            cached = copy.deepcopy(prior)
                            cached["reused_from_attachment_id"] = prior_id
                            cached.pop("documents", None)
                            break
                    if cached is None:
                        with sessions() as db:
                            cached = _cached_document(db, attachment)
                if cached is not None:
                    document_state = cached
                    result = copy.deepcopy(cached["output"])
                else:
                    result = extract_attachment(
                        data, state=document_state,
                        save=lambda value, aid=attachment.id: checkpoint(aid, value),
                        settings=settings, designations=titles,
                    )
                document_state["output"] = result
                checkpoint(attachment.id, document_state)
            if result["status"] == "complete" and result.get("extractions"):
                documents = document_state.get("documents")
                if not documents:
                    document_state["documents"] = _save_selected_documents(
                        settings, account_id, attachment, data, result,
                    )
                    checkpoint(attachment.id, document_state)
                else:
                    for document in documents.values():
                        content = (Path(settings.storage_dir) / document["object_key"]).read_bytes()
                        if hashlib.sha256(content).hexdigest() != document["content_hash"]:
                            raise IntakeReviewError("A selected document checksum does not match. Restore it before retrying.")
            results.append(result)
        with sessions() as db:
            job = db.get(EmailIngestionJob, job_id)
            if any(result["status"] == "needs_review" for result in results):
                job.status, job.last_error = "needs_review", "One or more document pages need review. No candidate was changed."
            elif not any(result.get("extractions") for result in results):
                job.status, job.last_error = "ignored", "No resume, passport or Aadhaar pages identified."
            else:
                commit_candidate(db, job, results)
            db.commit()
    except ExtractionPending:
        with sessions() as db:
            job = db.get(EmailIngestionJob, job_id)
            job.status, job.next_attempt_at = "retry", now() + timedelta(seconds=30)
            job.last_error = "Waiting for the existing Veris job."
            db.commit()
    except ValueError as exc:
        with sessions() as db:
            job = db.get(EmailIngestionJob, job_id)
            job.status = "needs_review"
            job.last_error = (
                str(exc)[:500] if isinstance(exc, IntakeReviewError)
                else "Document values could not be applied. Review the saved extraction before retrying."
            )
            db.commit()
    except Exception as exc:
        log.error("Email ingestion job %s failed (%s)", job_id, type(exc).__name__)
        with sessions() as db:
            job = db.get(EmailIngestionJob, job_id)
            job.attempts += 1
            # The first failed attempt is followed by three retry attempts.
            job.status = "failed" if job.attempts >= 4 else "retry"
            if job.status == "retry":
                saved = copy.deepcopy(job.state or {})
                for document in saved.values():
                    if isinstance(document, dict):
                        request_extraction_retry(document, automatic=True)
                job.state = saved
            job.next_attempt_at = now() + timedelta(seconds=90 * 2 ** (job.attempts - 1))
            job.last_error = "Document processing failed. Check worker logs and OCR configuration; retry after correcting the cause."
            db.commit()
