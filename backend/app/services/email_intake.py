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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers.candidates import lock_assignment_cursor, next_owner
from app.db.models import (
    Attachment, AuditEntry, Candidate, CandidateStatus, Designation, EmailIngestionJob,
    MailAccount, MailAttachment, MailMessage, Notification, Role, Source, Task, TaskKind, User,
)
from app.integrations.mail.imap import IMAPMailbox, MailMessageError, require_cursor_source
from app.integrations.ocr.mapping import parse_loose_date
from app.integrations.ocr.inspection import restore_inspection
from app.integrations.mail.mime import document_mime
from app.services.document_extraction import (
    extract_attachment, ExtractionPending, request_extraction_retry, UnreadableDocument,
)
from app.services.mail_config import configured_mailboxes
from app.services.mail_providers import account_config
from app.services.candidate_identity import name_relation, identity_matches, enrich_empty, merge_ocr_data, normalize_candidate_values, normalize_phone, normalize_email, normalize_passport
from app.services.document_storage import put_bytes, read_bytes

log = logging.getLogger(__name__)


class IntakeReviewError(ValueError):
    """An actionable, deliberately sanitized reason to hold a receipt for review."""


def now():
    return datetime.now(timezone.utc)


def store_original(settings, account_id: str, message_key: str, ordinal: int, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    receipt = hashlib.sha256(message_key.encode()).hexdigest()
    key = f"email/{account_id}/{receipt}/{ordinal}-{digest}.bin"
    return put_bytes(settings, key, data, "application/octet-stream")


def receive_account(account_id, *, sessions, settings):
    """Commit originals/receipts before advancing the IMAP cursor; never mark read."""
    with sessions() as db:
        account = db.get(MailAccount, account_id)
        if not account or account.disconnected_at:
            return
        address, cursor = account.address, dict(account.sync_state)
        auto = account.auto_ingest
        config = account_config(db, settings, account)
        if config is None:
            raise ValueError("The sign-in details for this mailbox are missing. Ask an administrator to reconnect it.")
    require_cursor_source(cursor, config)
    with IMAPMailbox(config) as mailbox:
        uids = mailbox.list_uids(after_uid=cursor["last_uid"],
                                 uid_validity=cursor["uid_validity"], limit=25)
        for uid in uids:
            with sessions() as db:
                current_account = db.get(MailAccount, account_id)
                if not current_account or current_account.disconnected_at:
                    return
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
                    account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
                    account.sync_state = {**account.sync_state, "last_uid": uid}
                    db.commit()
                cursor["last_uid"] = uid
                continue
            stored = [
                (part, store_original(settings, account_id, incoming.provider_id, index, part.data))
                for index, part in enumerate(incoming.attachments)
            ]
            with sessions() as db:
                db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
                message = db.scalar(select(MailMessage).where(
                    MailMessage.account_id == account_id, MailMessage.provider_message_id == incoming.provider_id))
                if message is None:
                    message = MailMessage(
                        account_id=account_id, provider_message_id=incoming.provider_id,
                        from_name=incoming.from_name[:200], from_address=incoming.from_address[:255],
                        subject=incoming.subject, body=incoming.body, received_at=incoming.received_at,
                        unread=incoming.unread,
                        message_id_header=incoming.message_id_header,
                        automation_headers=incoming.automation_headers,
                        references_header=incoming.references_header, reply_to=incoming.reply_to,
                        folder=incoming.folder or "INBOX", remote_uid=incoming.uid, uid_validity=incoming.uid_validity,
                        to_addresses=list(incoming.to_addresses), cc_addresses=list(incoming.cc_addresses),
                        bcc_addresses=list(incoming.bcc_addresses), starred=incoming.starred,
                    )
                    db.add(message)
                    db.flush()
                    for part, key in stored:
                        db.add(MailAttachment(
                            message_id=message.id, file_name=part.filename[:255],
                            size_kb=(len(part.data) + 1023) // 1024,
                            object_key=key, content_hash=part.content_hash,
                        ))
                if auto and stored and not db.scalar(select(EmailIngestionJob.id).where(EmailIngestionJob.message_id == message.id)):
                    _receive_job(db, message, stored)
                account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
                account.sync_state = {**account.sync_state, "last_uid": uid}
                account.worker_seen_at = now()
                db.commit()
            cursor["last_uid"] = uid
    with sessions() as db:
        account = db.get(MailAccount, account_id)
        account.last_sync_at, account.last_error, account.sync_requested = now(), None, False
        account.worker_seen_at = now()
        db.commit()


def _receive_job(db: Session, message: MailMessage, stored) -> None:
    """Queue the message for OCR, unless every attachment was already processed.

    PRD: "Skip a file whose hash was already processed." When all attachments are byte-identical
    to files that were committed before, the receipt is kept for the audit trail (sender, message
    id, time) as ``ignored`` with the reason, and nothing is read, created or acknowledged.
    A message that also carries any new file is processed as usual.
    """
    from app.services.duplicate_files import duplicate_reason, processed_hashes
    hashes = {part.content_hash for part, _ in stored}
    known = processed_hashes(db, hashes, exclude_mail_message_id=message.id)
    if hashes and set(known) == hashes:
        db.add(EmailIngestionJob(message_id=message.id, status="ignored", state={}, last_error=duplicate_reason([known[h] for h in sorted(known)])))
        db.add(AuditEntry(action="Ignored duplicate email file", target=(message.subject or "")[:300],
                          entity_type="email_message", entity_id=message.id,
                          meta={"channel": "email", "fromAddress": message.from_address, "receivedAt": message.received_at.isoformat(),
                                "providerMessageId": message.provider_message_id, "contentHashes": sorted(hashes),
                                "matched": {h: known[h] for h in sorted(known)}}))
        return
    db.add(EmailIngestionJob(message_id=message.id, state={}))


_PASSPORT_KEYS = ("passport_no", "passport_issue", "passport_expiry", "passport_place")


def combine_extractions(results: list[dict]) -> tuple[dict, dict, bool]:
    """Merge only evidence for one person. Sender and filenames are never identity."""
    fields, raw = {}, {}
    names: list[str] = []
    needs_review = False
    identity = {}
    for result in results:
        # A file that was set aside (a logo, a vCard) is not a reason to doubt the candidate.
        needs_review = needs_review or (bool(result.get("warnings")) and result.get("status") != "ignored")
        for kind, extraction in result.get("extractions", {}).items():
            current = extraction["fields"]
            if current.get("name"):
                names.append(current["name"])
            for key in ("phone", "email", "passport_no", "dob", "gender"):
                value = current.get(key)
                normalized = (
                    normalize_phone(value) if key == "phone" and value
                    else normalize_email(value) if key == "email" and value
                    else normalize_passport(value) if key == "passport_no" and value
                    else str(parse_loose_date(str(value)) or "") if key == "dob" and value
                    else str(value or "").strip().casefold()
                )
                if normalized and key in identity and identity[key] != normalized:
                    raise IntakeReviewError("The attachments seem to belong to different people. Check them before you create a candidate.")
                if normalized:
                    identity[key] = normalized
            document = extraction["raw"]
            # The first document of a kind is primary; every other distinct one is kept
            # under additional_documents, never overwritten or dropped. ``raw`` is the
            # verbatim Veris result, so our provenance goes beside it in ``_sources``.
            if "source" in extraction:
                source = {**({"source_file": {**result["source"], "selected_pages": extraction.get("selected_pages")}} if result.get("source") else {}),
                          **extraction["source"]}
                raw = merge_ocr_data(raw, {kind: document, "_sources": {kind: source}})
            else:
                # A checkpoint from before provenance moved out of the payload.
                if result.get("source"):
                    document = {**document, "source_file": {**result["source"], "selected_pages": extraction.get("selected_pages")}}
                raw = merge_ocr_data(raw, {kind: document})
            for key, value in current.items():
                if value not in (None, "", [], {}) and fields.get(key) in (None, "", [], {}):
                    fields[key] = value
            needs_review = needs_review or bool(extraction.get("warnings"))
    # The passport itself is the authority for passport details; a resume's own
    # passport section only fills what the passport did not give.
    for result in results:
        passport = result.get("extractions", {}).get("passport")
        if passport:
            for key in _PASSPORT_KEYS:
                value = passport["fields"].get(key)
                if value not in (None, "", [], {}):
                    fields[key] = value
    # Names are compared as word sets: order, a missing middle name and initials differ
    # between a passport (given names + surname), a resume and an Aadhaar card.
    relations = {name_relation(a, b) for index, a in enumerate(names) for b in names[index + 1:]}
    if "different" in relations:
        raise IntakeReviewError("The documents name different people. Check them and split the files by person before trying again.")
    if "partial" in relations:
        needs_review = True
    return fields, raw, needs_review


def _duplicates(db, fields):
    return [candidate for candidate, _ in identity_matches(db, fields)]


def candidate_values(fields: dict, raw: dict) -> dict:
    """Candidate columns from the merged OCR fields; the resume's own designation wording
    is kept when no master title matched."""
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
    else:
        # No master title matched: keep the resume's own wording in the profile's free-text
        # designations so it is visible and searchable.
        stated = ((raw.get("resume") or {}).get("designation") or "").strip()
        if stated:
            values["designations"] = [stated[:200]]
    return normalize_candidate_values(values)


def commit_candidate(db: Session, job: EmailIngestionJob, results: list[dict], notes: list[str] | None = None):
    """Candidate, immutable file links, assignment, first task and receipt commit together.

    ``notes`` name attachments that could not be read; the candidate is saved from the
    readable ones, flagged for review, and the notes stay on the job.
    """
    fields, raw, review = combine_extractions(results)
    review = review or bool(notes)
    message = db.get(MailMessage, job.message_id)
    # Serialize identity lookup and assignment together. Checking for duplicates
    # before acquiring this lock allows two concurrent receipts to create one
    # person twice. The singleton is also initialized safely on an empty database.
    pointer = lock_assignment_cursor(db)
    from app.services.document_person_split import ensure_single_person_source
    ensure_single_person_source(db, "email", job.message_id)
    db.refresh(job)
    if job.status == "completed":
        return
    matches = _duplicates(db, fields)
    if len(matches) > 1:
        raise IntakeReviewError("The phone, email or passport match different candidates already in the system. Check which one is right.")
    created = not matches
    values = candidate_values(fields, raw)
    review = review or not values.get("name") or not values.get("phone")
    if created:
        owner = next_owner(db, pointer=pointer)
        # A source-derived reference is stable across retries and cannot collide with
        # the legacy manual count-based CND-1001 references.
        candidate = Candidate(
            ref=f"CND-E-{job.id.replace('-', '')}", name=values.pop("name", None) or "Name needs review",
            phone=values.pop("phone", None) or "", source=Source.email,
            source_detail=f"email_message:{message.id}", owner_id=owner.id if owner else None,
            intake_owner_id=owner.id if owner else None,
            needs_review=review or owner is None,
            status=CandidateStatus.needs_review if review or owner is None else CandidateStatus.new,
            ocr_data=raw, **values,
        )
        db.add(candidate)
        db.flush()
        if owner:
            started = now()
            db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest",
                        candidate_id=candidate.id, owner_id=owner.id, created_at=started,
                        sla_started_at=started, due_at=started + timedelta(hours=48)))
        from app.services.candidate_review import notify_candidate_received
        notify_candidate_received(db, candidate, owner, channel="email", kind="candidate_assignment", results=results)
    else:
        candidate = matches[0]
        if candidate.dob and values.get("dob") and candidate.dob != values["dob"]:
            raise IntakeReviewError("The date of birth does not match the candidate already in the system. Check the documents.")
        if candidate.gender and values.get("gender") and candidate.gender.casefold() != values["gender"].casefold():
            raise IntakeReviewError("The gender does not match the candidate already in the system. Check the documents.")
        enrich_empty(candidate, values, raw)
        # Existing ownership, status and human-corrected populated fields are preserved.
    text = "\n".join(page.get("text", "") for result in results
                     for kind, part in result.get("extractions", {}).items() if kind == "resume"
                     for page in sorted(part["raw"].get("pages", []), key=lambda page: page.get("page_number") if type(page.get("page_number")) is int else 0))
    new_resume = False
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
            new_resume = new_resume or link["slot"] == "resume"
    # A different resume file is the latest resume, so Data Bank search must find its
    # text (the database trigger refreshes the search index when resume_text changes).
    if text and (new_resume or not candidate.resume_text):
        candidate.resume_text = text
    message.candidate_id = candidate.id
    job.candidate_id, job.status = candidate.id, "completed"
    job.last_error = ("Saved from the readable documents. " + " ".join(notes))[:500] if notes else None
    db.flush()
    from app.services.intake_acknowledgements import queue_intake_acknowledgement
    queue_intake_acknowledgement(db, "email", message.id)
    db.add(AuditEntry(action="Created candidate from email" if created else "Added email documents to a candidate",
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
        if output.get("reason"):
            summary["reason"] = str(output["reason"])[:300]
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


_DECORATIVE_SIDE, _DECORATIVE_AREA = 350, 150_000


def _set_aside_reason(data: bytes) -> str | None:
    """Why an attachment is not a candidate document at all, decided before any OCR.

    Signatures, logos and vCards ride along with real resumes; none of them may stop
    the resume becoming a candidate. Unreadable *documents* are not set aside here.
    """
    if not data:
        return "The attachment is empty."
    mime = document_mime(data)
    if mime is None:
        return "Not a PDF, Word document or image, so it was not read."
    if mime.startswith("image/"):
        try:
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
        except Exception:
            return None
        if max(width, height) < _DECORATIVE_SIDE or width * height < _DECORATIVE_AREA:
            return "A small image (logo, signature or icon), not a document page."
    return None


def _synthetic_output(status: str, reason: str, file_name: str) -> dict:
    """A recorded outcome for an attachment that never produced an inspection."""
    report = {"source_format": "", "page_count": 0, "pages": [], "resume_pages": [], "passport_pages": [],
              "aadhaar_pages": [], "unread_pages": [], "unknown_pages": [], "warnings": [], "complete": False}
    return {"inspection": report, "extractions": {}, "status": status, "reason": reason, "synthetic": True,
            "warnings": [] if status == "ignored" else [f"{file_name}: {reason}"], "splitSuggested": False}


def _save_selected_documents(settings, account_id, attachment, data, result):
    """Build downloadable per-kind PDFs outside candidate/database transactions."""
    inspected = restore_inspection(
        data, result["inspection"],
        document_timeout=settings.ocr_document_timeout_seconds, allow_partial=True,
    )
    documents = {}
    for kind, extraction in result["extractions"].items():
        if kind not in {"resume", "passport", "aadhaar"}:
            raise IntakeReviewError("We could not tell what kind of document this is. Please check it.")
        selected = extraction["selected_pages"]
        if selected != getattr(inspected, f"{kind}_pages"):
            raise IntakeReviewError("The pages we read do not match the saved check. Please check the document.")
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
        job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
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
            job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
            if not job:
                raise IntakeReviewError("This email is being checked or is already saved.")
            paused = job.status not in {"pending", "processing", "retry"}
            job.state = copy.deepcopy(state)
            job.inspection = _inspection_summary(state, attachments)
            job.updated_at = now()
            db.get(MailAccount, account_id).extraction_seen_at = now()
            db.commit()
            if paused:
                raise IntakeReviewError("This email is being checked or is already saved.")

    try:
        results = []
        for attachment in attachments:
            document_state = state.get(attachment.id, {})
            result = document_state.get("output")
            data = read_bytes(settings, attachment.object_key)
            if hashlib.sha256(data).hexdigest() != attachment.content_hash:
                raise IntakeReviewError("The saved file looks damaged. Restore the original file, then try again.")
            if result is None:
                reason = _set_aside_reason(data)
                if reason:
                    result = _synthetic_output("ignored", reason, attachment.file_name)
                    document_state["output"] = result
                    document_state["extraction_status"] = "ignored"
                    checkpoint(attachment.id, document_state)
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
                    try:
                        result = extract_attachment(
                            data, state=document_state,
                            save=lambda value, aid=attachment.id: checkpoint(aid, value),
                            settings=settings, designations=titles,
                            partial=True, ignore_decorative=True,
                        )
                    except UnreadableDocument as exc:
                        # A damaged or protected document: report this attachment for review
                        # and carry on with the others instead of failing the whole receipt.
                        result = _synthetic_output("needs_review", str(exc)[:300], attachment.file_name)
                document_state["output"] = result
                checkpoint(attachment.id, document_state)
            if not result.get("synthetic"):
                from app.services.document_extraction import checked_person_output
                try:
                    result = checked_person_output(data, result, document_state, settings)
                except UnreadableDocument as exc:
                    result = _synthetic_output("needs_review", str(exc)[:300], attachment.file_name)
                document_state["output"] = result
                checkpoint(attachment.id, document_state)
            if result["status"] in {"complete", "needs_review"} and result.get("extractions"):
                documents = document_state.get("documents")
                if not documents:
                    document_state["documents"] = _save_selected_documents(
                        settings, account_id, attachment, data, result,
                    )
                    checkpoint(attachment.id, document_state)
                else:
                    for document in documents.values():
                        content = read_bytes(settings, document["object_key"])
                        if hashlib.sha256(content).hexdigest() != document["content_hash"]:
                            raise IntakeReviewError("A selected document looks different from the saved copy. Restore the original, then try again.")
            results.append({**result, "source": {
                "channel": "email", "message_id": attachment.message_id,
                "attachment_id": attachment.id, "file_name": attachment.file_name,
            }})
        with sessions() as db:
            lock_assignment_cursor(db)
            job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
            if not job or job.status not in {"pending", "processing", "retry"}:
                return
            readable = [result for result in results if result.get("extractions")]
            unresolved = [result for result in results if result["status"] == "needs_review"]
            if any(result.get("splitSuggested") for result in results):
                job.status, job.last_error = "needs_review", "One or more document pages need review. No candidate was changed."
            elif readable:
                # Unreadable or partly unclassified attachments are named on the job while the
                # readable documents still become the candidate (flagged for review).
                notes = [
                    f'{result["source"]["file_name"]}: ' + (result.get("reason") or "; ".join(result.get("warnings", [])[:2]) or "needs review") + "."
                    for result in unresolved if not result.get("extractions")
                ]
                commit_candidate(db, job, results, notes=notes)
            elif unresolved:
                job.status, job.last_error = "needs_review", "One or more document pages need review. No candidate was changed."
            else:
                job.status, job.last_error = "ignored", "No resume, passport or Aadhaar pages identified."
            db.commit()
    except ExtractionPending:
        with sessions() as db:
            job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
            if not job or job.status not in {"pending", "processing", "retry"}:
                return
            job.status, job.next_attempt_at = "retry", now() + timedelta(seconds=30)
            job.last_error = "Waiting for the document reader to finish."
            db.commit()
    except ValueError as exc:
        with sessions() as db:
            job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
            if not job or job.status not in {"pending", "processing", "retry"}:
                return
            job.status = "needs_review"
            job.last_error = (
                str(exc)[:500] if isinstance(exc, IntakeReviewError)
                else "We could not fill in the details from the documents. Check what was read from them, then try again."
            )
            db.commit()
    except Exception as exc:
        log.error("Email ingestion job %s failed (%s)", job_id, type(exc).__name__)
        with sessions() as db:
            job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.id == job_id).with_for_update())
            if not job or job.status not in {"pending", "processing", "retry"}:
                return
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
            job.last_error = "We could not read the documents in this email. Try again, and contact your administrator if it keeps happening."
            db.commit()
