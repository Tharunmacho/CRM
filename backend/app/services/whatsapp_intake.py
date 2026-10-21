"""Durable WhatsApp media intake using the shared OCR and candidate identity rules."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.routers.candidates import lock_assignment_cursor, next_owner
from app.db.models import Attachment, AuditEntry, Candidate, CandidateStatus, Designation, Notification, Role, Source, Task, TaskKind, User, WhatsAppChannel, WhatsAppMessage
from app.integrations.ocr.inspection import restore_inspection
from app.integrations.ocr.mapping import parse_loose_date, resume_text_from
from app.integrations.whatsapp import download_media, WhatsAppError
from app.services.candidate_identity import enrich_empty, identity_matches, normalize_candidate_values
from app.services.document_extraction import extract_attachment, ExtractionPending, request_extraction_retry
from app.services.document_storage import put_bytes, read_bytes

log = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc)


class ChannelPaused(Exception):
    """Keep durable work queued until the Admin reconnects its source."""


def connected_channel(db, phone_id, settings, *, lock=False):
    statement = select(WhatsAppChannel).where(WhatsAppChannel.phone_number_id == phone_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    channel = db.scalar(statement)
    if not settings.whatsapp_enabled or phone_id != settings.whatsapp_phone_number_id or not channel or not channel.enabled:
        raise ChannelPaused()
    return channel


def receive_payload(db, payload, settings):
    """Authenticate before calling. Commit receipt before returning HTTP acknowledgement."""
    count = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata", {})
            phone_id = str(metadata.get("phone_number_id", ""))
            if phone_id != settings.whatsapp_phone_number_id:
                continue
            # Connection is an explicit Admin action. A provider delivery must
            # never silently reconnect a source that the operator disconnected.
            try:
                channel = connected_channel(db, phone_id, settings, lock=True)
            except ChannelPaused:
                continue
            if metadata.get("display_phone_number"):
                channel.display_number = str(metadata["display_phone_number"])[:80]
            contacts = {str(c.get("wa_id")): str(c.get("profile", {}).get("name", ""))[:200] for c in value.get("contacts", [])}
            for message in value.get("messages", []):
                provider_id, sender = str(message.get("id", "")), str(message.get("from", ""))
                if not provider_id or len(provider_id) > 255 or not sender or len(sender) > 40:
                    continue
                kind = str(message.get("type", "unknown"))[:30]
                media = message.get(kind, {}) if kind in {"document", "image"} else {}
                received = datetime.fromtimestamp(int(message.get("timestamp", utcnow().timestamp())), timezone.utc)
                has_media = bool(media.get("id"))
                state = "pending" if has_media and channel.auto_ingest and channel.enabled else "received" if has_media else "ignored"
                row_id = db.scalar(insert(WhatsAppMessage).values(
                    provider_message_id=provider_id, phone_number_id=phone_id, sender_number=sender, sender_name=contacts.get(sender),
                    message_type=kind, body=str(message.get("text", {}).get("body") or media.get("caption") or "")[:65000],
                    media_id=str(media["id"])[:100] if has_media else None,
                    file_name=str(media.get("filename") or ("image.jpg" if kind == "image" else "document"))[:255] if has_media else None,
                    content_type=str(media.get("mime_type") or "")[:120] if has_media else None,
                    received_at=received, status=state,
                ).on_conflict_do_nothing(index_elements=["provider_message_id"]).returning(WhatsAppMessage.id))
                count += bool(row_id)
            channel.last_received_at = utcnow()
    db.commit()
    return count


def attach_original(db, message, candidate):
    if not message.object_key:
        return
    links = [{"slot": "source_bundle", "object_key": message.object_key, "file_name": message.file_name or "WhatsApp document",
              "content_hash": message.content_hash, "content_type": message.content_type, "size_kb": message.processing_state.get("sizeKb")}]
    links.extend({"slot": kind, **document} for kind, document in message.processing_state.get("documents", {}).items())
    for link in links:
        if db.scalar(select(Attachment.id).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == link["slot"], Attachment.content_hash == link["content_hash"])):
            continue
        version = (db.scalar(select(func.max(Attachment.version)).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == link["slot"])) or 0) + 1
        db.add(Attachment(entity_type="candidate", entity_id=candidate.id, version=version, **link))
        db.flush()


def commit_candidate(db, message, output):
    from app.services.email_intake import combine_extractions, IntakeReviewError
    fields, raw, review = combine_extractions([output])
    pointer = lock_assignment_cursor(db)
    matches = identity_matches(db, fields)
    if len(matches) > 1:
        raise IntakeReviewError("The document matches multiple candidates. Review the original and choose the correct profile.")
    values = {key: fields[key] for key in ("name", "phone", "whatsapp", "email", "gender", "nationality", "current_city", "languages", "passport_no", "passport_place", "skills", "total_experience", "gulf_experience") if fields.get(key) is not None}
    for key in ("dob", "passport_issue", "passport_expiry"):
        if fields.get(key): values[key] = parse_loose_date(str(fields[key]))
    if fields.get("designation"): values["designations"] = [fields["designation"]]
    values = normalize_candidate_values(values)
    # The sender is a contact/provenance fallback, never a duplicate identity.
    # A document-provided contact and any existing human value take precedence.
    values["whatsapp"] = values.get("whatsapp") or message.sender_number
    review = review or not values.get("name") or not values.get("phone")
    created = not matches
    if created:
        owner = next_owner(db, pointer=pointer)
        candidate = Candidate(ref=f"CND-W-{message.id.replace('-', '')}", source=Source.whatsapp,
            source_detail=f"whatsapp_message:{message.id}", owner_id=owner.id if owner else None, intake_owner_id=owner.id if owner else None,
            name=values.pop("name", None) or "Name needs review", phone=values.pop("phone", None) or "",
            needs_review=review or owner is None, status=CandidateStatus.needs_review if review or owner is None else CandidateStatus.new,
            ocr_data=raw, **values)
        db.add(candidate); db.flush()
        if owner:
            now = utcnow()
            db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest", candidate_id=candidate.id, owner_id=owner.id,
                        created_at=now, sla_started_at=now, due_at=now + timedelta(hours=48)))
        recipients = set(db.scalars(select(User.id).where(User.active.is_(True), User.role.in_([Role.admin, Role.manager]))))
        if owner: recipients.add(owner.id)
        for recipient in recipients:
            db.add(Notification(user_id=recipient, kind="assignment", title="Candidate received by WhatsApp", detail=candidate.name, link_to=f"/candidates/{candidate.id}"))
    else:
        candidate = matches[0][0]
        if candidate.dob and values.get("dob") and candidate.dob != values["dob"]:
            raise IntakeReviewError("Date of birth conflicts with the existing candidate. Review the documents.")
        if candidate.gender and values.get("gender") and candidate.gender.casefold() != values["gender"].casefold():
            raise IntakeReviewError("Gender conflicts with the existing candidate. Review the documents.")
        enrich_empty(candidate, values, raw)
    if not candidate.resume_text:
        candidate.resume_text = resume_text_from(raw)
    attach_original(db, message, candidate)
    message.candidate_id, message.status, message.last_error = candidate.id, "completed", None
    db.add(AuditEntry(action="Created WhatsApp candidate" if created else "Added WhatsApp candidate evidence", entity_type="candidate", entity_id=candidate.id, target=candidate.name, meta={"messageId": message.id, "senderNumber": message.sender_number}))


def process_message(message_id, *, sessions, settings, fetch_media=download_media, extract=extract_attachment):
    with sessions() as db:
        message = db.get(WhatsAppMessage, message_id)
        if not message or message.status not in {"pending", "processing", "retry"}:
            return
        try:
            channel = connected_channel(db, message.phone_number_id, settings, lock=True)
        except ChannelPaused:
            return
        message.status = "processing"
        state, key, media_id = deepcopy(message.processing_state or {}), message.object_key, message.media_id
        phone_id = message.phone_number_id
        titles = list(db.scalars(select(Designation.title)))
        channel.worker_seen_at = utcnow()
        db.commit()
    def checkpoint(value):
        with sessions() as db:
            row = db.get(WhatsAppMessage, message_id)
            row.processing_state = deepcopy(value)
            db.commit()
            connected_channel(db, phone_id, settings)
    try:
        with sessions() as db:
            connected_channel(db, phone_id, settings)
        if key:
            data = read_bytes(settings, key)
            if hashlib.sha256(data).hexdigest() != state.get("originalHash"):
                raise WhatsAppError("Stored original checksum mismatch. Restore the original before retrying.")
        else:
            data, mime = fetch_media(settings, media_id)
            digest = hashlib.sha256(data).hexdigest()
            key = put_bytes(settings, f"whatsapp/{message_id}/{digest}.bin", data, mime)
            state.update({"originalHash": digest, "sizeKb": (len(data) + 1023) // 1024})
            with sessions() as db:
                row = db.get(WhatsAppMessage, message_id)
                row.object_key, row.content_hash, row.content_type, row.processing_state = key, digest, mime, deepcopy(state)
                db.commit()
                connected_channel(db, phone_id, settings)
        output = state.get("output")
        if output is None:
            # A second delivery retains its own original and receipt, while a
            # completed extraction for exactly these bytes avoids a paid rescan.
            with sessions() as db:
                current = db.get(WhatsAppMessage, message_id)
                previous = db.scalars(select(WhatsAppMessage).where(
                    WhatsAppMessage.phone_number_id == current.phone_number_id,
                    WhatsAppMessage.content_hash == state["originalHash"],
                    WhatsAppMessage.status == "completed", WhatsAppMessage.id != message_id,
                ).order_by(WhatsAppMessage.received_at.desc(), WhatsAppMessage.id))
                for receipt in previous:
                    cached = (receipt.processing_state or {}).get("output")
                    if not isinstance(cached, dict) or cached.get("status") == "needs_review" or not cached.get("extractions") or not receipt.object_key:
                        continue
                    try:
                        valid = hashlib.sha256(read_bytes(settings, receipt.object_key)).hexdigest() == state["originalHash"]
                    except (OSError, ValueError):
                        valid = False
                    if valid:
                        output = deepcopy(cached)
                        state.update(output=output, reusedReceiptId=receipt.id)
                        break
            if output is not None:
                checkpoint(state)
        if output is None:
            extraction_state = state.setdefault("extraction", {})
            def save_extraction(value):
                state["extraction"] = deepcopy(value)
                checkpoint(state)
            output = extract(data, state=extraction_state, save=save_extraction, settings=settings, designations=titles)
            state["output"] = output
            checkpoint(state)
        if output.get("status") == "needs_review":
            raise WhatsAppError("Some pages need review. Open the original document before resolving this message.")
        if not output.get("extractions"):
            with sessions() as db:
                connected_channel(db, phone_id, settings, lock=True)
                db.get(WhatsAppMessage, message_id).status = "ignored"
                db.commit()
            return
        if not state.get("documents"):
            inspected = restore_inspection(data, output["inspection"], document_timeout=settings.ocr_document_timeout_seconds)
            documents = {}
            for kind, part in output["extractions"].items():
                if kind not in {"resume", "passport", "aadhaar"} or part["selected_pages"] != getattr(inspected, f"{kind}_pages"):
                    raise WhatsAppError("Document page selections need review")
                content = inspected.pdf_for_pages(part["selected_pages"])
                digest = hashlib.sha256(content).hexdigest()
                documents[kind] = {"object_key": put_bytes(settings, f"whatsapp/{message_id}/{kind}-{digest}.pdf", content, "application/pdf"),
                                   "content_hash": digest, "file_name": f"{kind}.pdf", "content_type": "application/pdf", "size_kb": (len(content) + 1023) // 1024}
            state["documents"] = documents
            checkpoint(state)
        # A retained extraction checkpoint is useful only if every selected file
        # still has its recorded bytes. Never attach a missing or changed version.
        for document in state.get("documents", {}).values():
            content = read_bytes(settings, document["object_key"])
            if hashlib.sha256(content).hexdigest() != document["content_hash"]:
                raise WhatsAppError("Stored document checksum mismatch. Restore the original before retrying.")
        with sessions() as db:
            connected_channel(db, phone_id, settings, lock=True)
            commit_candidate(db, db.get(WhatsAppMessage, message_id), output)
            db.commit()
    except ChannelPaused:
        with sessions() as db:
            row = db.get(WhatsAppMessage, message_id)
            row.status, row.next_attempt_at = "retry", utcnow()
            row.last_error = "Channel disconnected. Saved work will resume after reconnecting."
            db.commit()
    except ExtractionPending:
        with sessions() as db:
            row = db.get(WhatsAppMessage, message_id)
            row.status, row.next_attempt_at, row.last_error = "retry", utcnow() + timedelta(seconds=30), "Waiting for the existing document extraction job"
            db.commit()
    except Exception as exc:
        from app.services.email_intake import IntakeReviewError
        with sessions() as db:
            row = db.get(WhatsAppMessage, message_id)
            row.attempts += 1
            review = isinstance(exc, IntakeReviewError) or isinstance(exc, WhatsAppError) and any(word in str(exc).lower() for word in ("review", "checksum", "format", "limit"))
            row.status = "needs_review" if review else "failed" if row.attempts >= 4 else "retry"
            if row.status == "retry":
                saved = deepcopy(row.processing_state or {})
                # Only a confirmed failed provider job may start a new paid
                # generation. Pending/uncertain accepted jobs keep their ID.
                request_extraction_retry(saved.setdefault("extraction", {}), automatic=True)
                row.processing_state = saved
            row.next_attempt_at = utcnow() + timedelta(seconds=90 * 2 ** min(row.attempts - 1, 4))
            row.last_error = str(exc)[:500] if isinstance(exc, (WhatsAppError, IntakeReviewError)) else "Document processing failed. Check worker configuration and retry."
            db.commit()
        log.warning("WhatsApp processing stopped (%s); receipt retained.", type(exc).__name__)
