"""Durable, reviewed person/page separation shared by all intake channels."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

from fastapi import HTTPException
import pymupdf as fitz
from pydantic import ValidationError
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONPATH

from app.db.models import (Attachment, AuditEntry, Candidate, CandidateStatus, Designation, DocumentExtractionJob,
    DocumentPersonGroup, EmailIngestionJob, MailAttachment, MailMessage, MultiPersonIntake,
    Notification, Role, Source, Task, TaskKind, User, WhatsAppMessage)
from app.integrations.ocr.inspection import inspect_document
from app.integrations.ocr.person_split import page_identity, propose_groups
from app.services.candidate_identity import enrich_empty, identity_matches, merge_ocr_data, normalize_candidate_values, normalize_phone, normalize_email, normalize_passport
from app.services.document_storage import put_bytes, read_bytes

MAX_PAGES = 120
KINDS = {"resume", "passport", "aadhaar", "photo", "education", "experience", "source_bundle"}
FIELD_KEYS = {"name", "phone", "email", "whatsapp", "dob", "gender", "nationality", "current_city", "languages",
              "passport_no", "passport_issue", "passport_expiry", "passport_place", "designations", "skills",
              "total_experience", "gulf_experience", "expected_salary", "source"}


def now():
    return datetime.now(timezone.utc)


def ensure_single_person_source(db, source_type, source_id, *, check_evidence=True):
    bundle = db.scalar(select(MultiPersonIntake.id).where(MultiPersonIntake.source_type == source_type,
                                                         MultiPersonIntake.source_id == str(source_id)))
    if bundle:
        raise HTTPException(409, {"message": "This file is being split person by person. Open its saved review instead of attaching the whole file.", "bundleId": bundle})
    if not check_evidence:
        return
    if source_type == "manual":
        states = list(db.scalars(select(DocumentExtractionJob.state).where(DocumentExtractionJob.group_id == str(source_id), DocumentExtractionJob.status != "dismissed")))
    elif source_type == "email":
        state = db.scalar(select(EmailIngestionJob.state).where(EmailIngestionJob.message_id == str(source_id))) or {}
        states = [item for item in state.values() if isinstance(item, dict)]
    else:
        state = db.scalar(select(WhatsAppMessage.processing_state).where(WhatsAppMessage.id == str(source_id))) or {}
        states = [state, state.get("extraction", {})]
    from app.integrations.ocr.person_split import incompatible
    identities = [item for state in states for item in (state or {}).get("person_identities", [])]
    known_mixed = any((state or {}).get("splitSuggested") or ((state or {}).get("output") or {}).get("splitSuggested") for state in states)
    if known_mixed or any(incompatible(left, right) for index, left in enumerate(identities) for right in identities[index + 1:]):
        raise HTTPException(409, {"message": "This file seems to hold more than one person. Open Review people and pages instead of attaching the whole file to one person.", "splitSuggested": True})


def get_bundle(db, identifier, user, *, lock=False):
    statement = select(MultiPersonIntake).where(MultiPersonIntake.id == identifier)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    bundle = db.scalar(statement)
    if not bundle or (bundle.source_type == "manual" and bundle.owner_id != user.id):
        raise HTTPException(404, "Document review not found")
    return bundle


def groups_for(db, bundle):
    return list(db.scalars(select(DocumentPersonGroup).where(DocumentPersonGroup.bundle_id == bundle.id).order_by(DocumentPersonGroup.ordinal)))


def original_url(source_type, source_id):
    if source_type == "manual":
        return f"/api/ocr/jobs/{source_id}/original"
    if source_type == "email":
        return f"/api/inbox/attachments/{source_id}"
    return f"/api/whatsapp/messages/{source_id}/file"


def candidate_manual_sources(db, candidate_id, user):
    if db.scalar(select(Candidate.id).where(Candidate.id == candidate_id)) is None:
        raise HTTPException(404, "Candidate not found")
    # Project filenames/IDs inside PostgreSQL: source JSON also contains local OCR
    # evidence and must not be downloaded just to render these history links.
    rows = db.execute(select(MultiPersonIntake.id, MultiPersonIntake.source_id, MultiPersonIntake.updated_at,
        func.jsonb_path_query_array(MultiPersonIntake.sources, cast('$[*].id', JSONPATH)).label("source_ids"),
        func.jsonb_path_query_array(MultiPersonIntake.sources, cast('$[*].name', JSONPATH)).label("source_names"))
        .where(MultiPersonIntake.source_type == "manual", MultiPersonIntake.owner_id == user.id,
               MultiPersonIntake.status == "completed", select(DocumentPersonGroup.id).where(
                   DocumentPersonGroup.bundle_id == MultiPersonIntake.id, DocumentPersonGroup.status == "committed",
                   DocumentPersonGroup.candidate_id == candidate_id).exists())
        .order_by(MultiPersonIntake.updated_at.desc(), MultiPersonIntake.id.desc()).limit(101)).all()
    return {"reviews": [{"bundleId": row.id, "sourceId": row.source_id, "completedAt": row.updated_at,
        "originals": [{"id": identifier, "name": name, "url": original_url("manual", identifier)}
            for identifier, name in zip(row.source_ids, row.source_names)]} for row in rows[:100]], "hasMore": len(rows) > 100}


def bundle_out(db, bundle):
    groups = groups_for(db, bundle)
    return {"id": bundle.id, "sourceType": bundle.source_type, "sourceId": bundle.source_id,
            "status": bundle.status, "revision": bundle.revision, "lastError": bundle.last_error,
            "sources": [{"id": source["id"], "name": source["name"], "pageCount": source.get("page_count"),
                "originalUrl": original_url(bundle.source_type, source["id"])} for source in bundle.sources],
            "pages": [{key: value for key, value in page.items() if key != "text"} | {"snippet": page.get("text", "")[:800],
                "previewUrl": f'/api/document-bundles/{bundle.id}/sources/{page["sourceId"]}/pages/{page["pageNumber"]}'} for page in bundle.pages],
            "ignoredPages": bundle.ignored_pages,
            "groups": [{"id": group.id, "label": group.label, "fields": group.fields, "pages": group.pages,
                        "status": group.status, "candidateId": group.candidate_id} for group in groups],
            "candidateIds": list(dict.fromkeys(group.candidate_id for group in groups if group.candidate_id))}


def _source_records(db, source_type, source_id, user):
    """Called under the assignment cursor, before freezing a source for review."""
    if source_type == "manual":
        jobs = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.group_id == source_id,
            DocumentExtractionJob.owner_id == user.id).order_by(DocumentExtractionJob.id).with_for_update()))
        if not jobs:
            raise HTTPException(404, "Saved document intake not found")
        if any(job.candidate_id or (job.draft or {}).get("savedCandidateId") or job.status == "attached" for job in jobs):
            raise HTTPException(409, "This intake already belongs to a candidate. Start a separate upload to review multiple people.")
        sources = [{"id": job.id, "name": job.filename, "object_key": job.object_key, "hash": job.content_hash,
                    "content_type": job.content_type, "manual_source": (job.draft or {}).get("form", {}).get("source", "walk_in")} for job in jobs if job.status != "dismissed"]
        for job in jobs:
            if job.status != "dismissed":
                job.status, job.last_error = "needs_review", "Review and confirm the people and pages in this intake."
        return sources
    if source_type == "email":
        message = db.scalar(select(MailMessage).where(MailMessage.id == source_id).with_for_update())
        job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == source_id).with_for_update())
        if not message:
            raise HTTPException(404, "Email intake not found")
        if job is None:
            # A message nobody ingested yet (automatic intake off, or not started). The review
            # reserves it the way an ingest request would, on a live mailbox with attachments.
            from app.api.routers.inbox import require_intake_account
            require_intake_account(db, message.account_id)
            if not db.scalar(select(MailAttachment.id).where(MailAttachment.message_id == source_id).limit(1)):
                raise HTTPException(409, "This message has no imported attachments to review.")
            job = EmailIngestionJob(message_id=source_id, state={})
            db.add(job)
            db.flush()
        if message.candidate_id or job.candidate_id or job.status == "completed":
            raise HTTPException(409, "This email was already saved to a candidate. Its original history cannot be reassigned by splitting.")
        job.status, job.last_error = "needs_review", "Review and confirm the people and pages in this email."
        return [{"id": item.id, "name": item.file_name, "object_key": item.object_key, "hash": item.content_hash,
                 "content_type": "application/octet-stream"} for item in db.scalars(select(MailAttachment).where(MailAttachment.message_id == source_id).order_by(MailAttachment.id))]
    message = db.scalar(select(WhatsAppMessage).where(WhatsAppMessage.id == source_id).with_for_update())
    if not message:
        raise HTTPException(404, "WhatsApp intake not found")
    if message.candidate_id or message.status == "completed":
        raise HTTPException(409, "This WhatsApp document was already saved to a candidate.")
    message.status, message.last_error = "needs_review", "Review and confirm the people and pages in this document."
    return [{"id": message.id, "name": message.file_name or "WhatsApp document", "object_key": message.object_key,
             "hash": message.content_hash, "content_type": message.content_type}]


def prepare_bundle(db, source_type, source_id, user):
    if source_type != "manual" and user.role != Role.admin:
        raise HTTPException(403, "Only Admin can resolve a shared email or WhatsApp intake.")
    from app.api.routers.candidates import lock_assignment_cursor
    # Matches candidate create/merge and automatic intake. No candidate can commit
    # between checking the source and durably reserving it for reviewed splitting.
    lock_assignment_cursor(db)
    bundle = db.scalar(select(MultiPersonIntake).where(MultiPersonIntake.source_type == source_type,
        MultiPersonIntake.source_id == source_id).with_for_update())
    if bundle:
        bundle = get_bundle(db, bundle.id, user)
        if bundle.status == "pending":
            bundle.last_error = None
        db.commit()
        return bundle_out(db, bundle)
    sources = _source_records(db, source_type, source_id, user)
    if not sources or len(sources) > 20:
        raise HTTPException(422, "Use between one and twenty saved documents for one review.")
    if any(not source["object_key"] or not source["hash"] for source in sources):
        raise HTTPException(409, "Some files are still being received. Wait until they finish before you split them by person.")
    bundle = MultiPersonIntake(source_type=source_type, source_id=source_id,
        owner_id=user.id if source_type == "manual" else None, created_by=user.id,
        status="pending", revision=0, sources=sources, pages=[], ignored_pages=[])
    db.add(bundle); db.flush()
    db.add(AuditEntry(actor_id=user.id, action="Started checking documents for several people", entity_type="document_person_intake", entity_id=bundle.id,
        meta={"sourceType": source_type, "sourceId": source_id}))
    db.commit()
    return bundle_out(db, bundle)


def process_bundle(identifier, *, sessions, settings):
    """Worker preparation checkpoints each original; crashes need no paid replay."""
    try:
        with sessions() as db:
            bundle = db.get(MultiPersonIntake, identifier)
            if not bundle or bundle.status != "pending" or bundle.last_error:
                return
            sources = deepcopy(bundle.sources)
        pages = []
        for source in sources:
            original = read_bytes(settings, source["object_key"])
            if hashlib.sha256(original).hexdigest() != source["hash"]:
                raise ValueError("A saved file looks damaged. Restore the original file, then try again.")
            if source.get("pdf_key") and source.get("page_evidence"):
                normalized = read_bytes(settings, source["pdf_key"])
                if hashlib.sha256(normalized).hexdigest() != source["pdf_hash"]:
                    raise ValueError("A saved page preview looks damaged. Try again, and contact your administrator if it keeps happening.")
            else:
                inspected = inspect_document(original, tesseract_cmd=settings.tesseract_cmd,
                    languages=settings.ocr_languages, max_pages=min(settings.ocr_max_pages, MAX_PAGES),
                    page_timeout=min(settings.ocr_page_timeout_seconds, 10), document_timeout=min(settings.ocr_document_timeout_seconds, 90))
                if inspected.page_count + len(pages) > MAX_PAGES:
                    raise ValueError(f"Separate this source into smaller documents: a review supports at most {MAX_PAGES} pages.")
                normalized = inspected._pdf
                source["page_count"] = inspected.page_count
                source["pdf_hash"] = hashlib.sha256(normalized).hexdigest()
                preview_key = hashlib.sha256(f"{identifier}:{source['id']}:{source['pdf_hash']}".encode()).hexdigest()
                source["pdf_key"] = put_bytes(settings, f"person-previews/{preview_key}.pdf", normalized, "application/pdf")
                by_number = {page.page_number: page for page in inspected.pages}
                evidence = []
                for number in range(1, inspected.page_count + 1):
                    page = by_number.get(number)
                    text = (page.text if page else "")[:30000]
                    identity = page_identity(text)
                    evidence.append({"sourceId": source["id"], "pageNumber": number, "kind": page.document_type if page else "unknown",
                        "status": page.status if page else "unread", "text": text, "identity": identity,
                        "blocked": bool(identity["mixed"]), "reason": "Multiple candidate names occur on this page. Exclude it and upload separate pages." if identity["mixed"] else (page.reason if page else "Page was not inspected. Review its preview manually.")})
                source["page_evidence"] = evidence
                with sessions() as db:
                    current = db.scalar(select(MultiPersonIntake).where(MultiPersonIntake.id == identifier).with_for_update())
                    if current.status != "pending":
                        return
                    current.sources = deepcopy(sources); db.commit()
            pages.extend(source["page_evidence"])
        if len(pages) > MAX_PAGES:
            raise ValueError(f"A review supports at most {MAX_PAGES} pages.")
        proposals = propose_groups(pages)
        with sessions() as db:
            bundle = db.scalar(select(MultiPersonIntake).where(MultiPersonIntake.id == identifier).with_for_update())
            if bundle.status != "pending":
                return
            bundle.sources, bundle.pages, bundle.status, bundle.last_error = sources, pages, "review", None
            for ordinal, group in enumerate(proposals):
                if bundle.source_type == "manual":
                    group["fields"]["source"] = sources[0].get("manual_source", "walk_in")
                db.add(DocumentPersonGroup(bundle_id=bundle.id, ordinal=ordinal, label=group["label"], fields=group["fields"], pages=group["pages"], status="draft"))
            bundle.revision += 1
            db.commit()
    except Exception as exc:
        with sessions() as db:
            bundle = db.scalar(select(MultiPersonIntake).where(MultiPersonIntake.id == identifier).with_for_update())
            if bundle and bundle.status == "pending":
                bundle.last_error = str(exc)[:400] if isinstance(exc, ValueError) else "We could not get the pages ready. Your original files are safe. Try again, and contact your administrator if it keeps happening."
                db.commit()


def _page_key(page):
    return (page.get("sourceId"), page.get("pageNumber"))


def validate_selections(bundle, groups, ignored, *, complete=False):
    available = {_page_key(page): page for page in bundle.pages}
    seen = set()
    for group in groups:
        if complete and not group["pages"]:
            raise HTTPException(422, "Every person needs at least one page. Remove any person who has no pages before you confirm.")
        for page in group["pages"]:
            key = _page_key(page)
            if key not in available or key in seen:
                raise HTTPException(422, "Every selected page must belong to this source and appear in only one person or exclusion.")
            if available[key].get("blocked"):
                raise HTTPException(422, "A page containing multiple candidate identities cannot be attached. Exclude it and upload separate pages.")
            if page.get("kind") not in KINDS:
                raise HTTPException(422, "Choose a supported document type for each assigned page.")
            seen.add(key)
    for page in ignored:
        key = _page_key(page)
        if key not in available or key in seen:
            raise HTTPException(422, "Every page must be assigned once or excluded once, without overlaps.")
        if not str(page.get("reason", "")).strip():
            raise HTTPException(422, "Record a reason for each excluded page.")
        seen.add(key)
    if complete and seen != set(available):
        raise HTTPException(422, "Assign or explicitly exclude every original page before confirming.")


def save_review(db, identifier, user, revision, groups, ignored):
    bundle = get_bundle(db, identifier, user, lock=True)
    if bundle.source_type != "manual" and user.role != Role.admin:
        raise HTTPException(403, "Only Admin can resolve a shared email or WhatsApp intake.")
    if bundle.status != "review" or bundle.revision != revision:
        raise HTTPException(409, "This review was changed or is already finished. Reload it before you make changes.")
    validate_selections(bundle, groups, ignored)
    existing = {group.id: group for group in groups_for(db, bundle)}
    identifiers = [group.get("id") for group in groups if group.get("id")]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) - set(existing):
        raise HTTPException(422, "A person identifier does not belong to this review.")
    for group in existing.values():
        # Move old ordinals out of the way before reordering under the unique key.
        group.ordinal += 10000
    db.flush()
    for ordinal, entry in enumerate(groups):
        if set(entry["fields"]) - FIELD_KEYS:
            raise HTTPException(422, "The person details include unsupported fields.")
        group = existing.pop(entry.get("id"), None)
        if group is None:
            group = DocumentPersonGroup(bundle_id=bundle.id, status="draft")
            db.add(group)
        group.ordinal, group.label = ordinal, entry["label"].strip() or f"Person {ordinal + 1}"
        group.fields, group.pages = deepcopy(entry["fields"]), deepcopy(entry["pages"])
    for group in existing.values():
        db.delete(group)
    bundle.ignored_pages = deepcopy(ignored)
    bundle.revision += 1
    db.add(AuditEntry(actor_id=user.id, action="Saved the document check", entity_type="document_person_intake", entity_id=bundle.id,
        meta={"revision": bundle.revision, "people": len(groups), "excludedPages": len(ignored)}))
    db.commit()
    return bundle_out(db, bundle)


def _pdf_subset(settings, source, numbers):
    data = read_bytes(settings, source["pdf_key"])
    if hashlib.sha256(data).hexdigest() != source["pdf_hash"]:
        raise HTTPException(409, "A saved page looks damaged. No candidates were changed.")
    with fitz.open(stream=data, filetype="pdf") as original, fitz.open() as selected:
        for number in sorted(set(numbers)):
            selected.insert_pdf(original, from_page=number - 1, to_page=number - 1)
        return selected.tobytes(garbage=4, deflate=True, no_new_id=True)


def page_pdf(db, identifier, source_id, number, user, settings):
    bundle = get_bundle(db, identifier, user)
    source = next((item for item in bundle.sources if item["id"] == source_id), None)
    if not source or not source.get("pdf_key") or number < 1 or number > source.get("page_count", 0):
        raise HTTPException(404, "Page preview not found")
    return _pdf_subset(settings, source, [number])


def _attach_group(db, bundle, group, candidate, user, settings):
    sources = {source["id"]: source for source in bundle.sources}
    for source_id in sorted({page["sourceId"] for page in group.pages}):
        selections = [page for page in group.pages if page["sourceId"] == source_id]
        kinds = {"source_bundle": [page["pageNumber"] for page in selections]}
        for page in selections:
            if page["kind"] != "source_bundle":
                kinds.setdefault(page["kind"], []).append(page["pageNumber"])
        for kind, numbers in kinds.items():
            payload = _pdf_subset(settings, sources[source_id], numbers)
            digest = hashlib.sha256(payload).hexdigest()
            # A short opaque key also works on Windows storage with long roots;
            # exact person/source/page provenance is retained in the audit record.
            file_key = hashlib.sha256(f"{bundle.id}:{group.id}:{source_id}:{kind}:{digest}".encode()).hexdigest()
            key = put_bytes(settings, f"person-pages/{file_key}.pdf", payload, "application/pdf")
            version = (db.scalar(select(func.max(Attachment.version)).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id, Attachment.slot == kind)) or 0) + 1
            attachment = Attachment(entity_type="candidate", entity_id=candidate.id, slot=kind, version=version,
                file_name=f"{group.label[:80]}-{kind}.pdf", object_key=key, content_hash=digest, content_type="application/pdf",
                size_kb=max(1, (len(payload) + 1023) // 1024), uploaded_by=user.id)
            db.add(attachment); db.flush()
            db.add(AuditEntry(actor_id=user.id, action="Added checked pages to a candidate", target=candidate.name,
                entity_type="candidate", entity_id=candidate.id, meta={"bundleId": bundle.id, "groupId": group.id,
                "sourceId": source_id, "sourceHash": sources[source_id]["hash"], "pages": sorted(numbers), "slot": kind, "attachmentId": attachment.id}))


EXTRACTABLE = ("resume", "passport", "aadhaar")


def _group_signature(pages):
    return hashlib.sha256(repr(sorted((p["sourceId"], p["pageNumber"], p["kind"]) for p in pages if p["kind"] in EXTRACTABLE)).encode()).hexdigest()


def _group_pdfs(settings, bundle, pages):
    """One PDF per document kind holding exactly the pages the reviewer assigned to it."""
    sources = {source["id"]: source for source in bundle.sources}
    result = {}
    for kind in EXTRACTABLE:
        chosen = [p for p in pages if p["kind"] == kind]
        if not chosen:
            continue
        with fitz.open() as merged:
            for source_id in dict.fromkeys(p["sourceId"] for p in chosen):
                numbers = [p["pageNumber"] for p in chosen if p["sourceId"] == source_id]
                with fitz.open(stream=_pdf_subset(settings, sources[source_id], numbers), filetype="pdf") as part:
                    merged.insert_pdf(part)
            result[kind] = merged.tobytes(garbage=4, deflate=True, no_new_id=True)
    return result


def _saved_extractions(bundle):
    return (bundle.sources[0].get("group_extractions") or {}) if bundle.sources else {}


def extract_groups(db, identifier, user, revision, settings):
    """Read each reviewed person's pages with the same extraction used for whole files.

    Runs before the confirming transaction and never inside it: provider polling can take
    minutes. Progress is checkpointed on the review, so repeating the confirmation resumes
    the same provider jobs. A person whose pages cannot be read still gets saved by
    ``commit_review`` from the reviewer's own details, with the reason kept visible.
    """
    from app.services.document_extraction import ExtractionPending, extract_selected_pages
    bundle = get_bundle(db, identifier, user)
    if bundle.source_type != "manual" and user.role != Role.admin:
        raise HTTPException(403, "Only Admin can resolve a shared email or WhatsApp intake.")
    if bundle.status != "review" or bundle.revision != revision or not bundle.sources:
        return
    groups = [(group.id, deepcopy(group.pages)) for group in groups_for(db, bundle)]
    titles = list(db.scalars(select(Designation.title)))
    saved = deepcopy(_saved_extractions(bundle))
    work = []
    for group_id, pages in groups:
        record = saved.get(group_id) or {}
        signature = _group_signature(pages)
        if record.get("signature") == signature and record.get("finished"):
            continue
        work.append((group_id, pages, signature, record if record.get("signature") == signature else {}))
    if not work:
        return
    pdfs = {group_id: _group_pdfs(settings, bundle, pages) for group_id, pages, _, _ in work}
    db.rollback()
    waiting = False

    def persist(group_id, record):
        current = get_bundle(db, identifier, user, lock=True)
        if current.status != "review" or current.revision != revision:
            raise HTTPException(409, "This review was changed. Reload it before you confirm.")
        sources = deepcopy(current.sources)
        sources[0].setdefault("group_extractions", {})[group_id] = deepcopy(record)
        current.sources = sources
        db.commit()

    for group_id, pages, signature, record in work:
        record = {"signature": signature, "state": record.get("state", {}), "finished": False}
        if not pdfs[group_id]:
            record.update(finished=True, output=None)
            persist(group_id, record)
            continue

        def save(state, group_id=group_id, record=record):
            record["state"] = deepcopy(state)
            persist(group_id, record)

        try:
            output = extract_selected_pages(pdfs[group_id], state=record["state"], save=save, settings=settings, designations=titles)
            record.update(finished=True, output=output)
        except ExtractionPending:
            waiting = True
            continue
        except HTTPException:
            raise
        except Exception as exc:
            from app.services.document_extraction import ExtractionFailure
            record.update(finished=True, output=None, error=str(exc)[:300] if isinstance(exc, ExtractionFailure)
                          else "The document could not be read automatically.")
        persist(group_id, record)
    if waiting:
        raise HTTPException(409, {"message": "The files are still being read. Nothing is lost. Try again in a minute.", "code": "extraction_pending"})


def _ocr_for_group(bundle, group):
    """Merged OCR fields/raw/warnings for a confirmed group, or (None, None, [warning])."""
    record = _saved_extractions(bundle).get(group.id)
    if not record or record.get("signature") != _group_signature(group.pages):
        return None, None, []
    output = record.get("output")
    if not output:
        if record.get("error"):
            return None, None, [f"Automatic reading did not finish ({record['error']}) Only the reviewed details were saved."]
        return None, None, []
    from app.services.email_intake import IntakeReviewError, combine_extractions
    try:
        fields, raw, review = combine_extractions([{**output, "source": {
            "channel": bundle.source_type, "bundle_id": bundle.id, "group_id": group.id}}])
    except IntakeReviewError as exc:
        return None, None, [f"The documents of this person disagree: {exc} Only the reviewed details were saved."]
    warnings = list(output.get("warnings", []))
    return fields, raw, warnings


def commit_review(db, identifier, user, revision, decisions, settings):
    from app.api.routers.candidates import CandidateIn, lock_assignment_cursor, next_owner, PROFILE_CHECK_FIELDS, _validate_profile_relationships
    pointer = lock_assignment_cursor(db)
    bundle = get_bundle(db, identifier, user, lock=True)
    if bundle.source_type != "manual" and user.role != Role.admin:
        raise HTTPException(403, "Only Admin can resolve a shared email or WhatsApp intake.")
    if bundle.status == "completed":
        return bundle_out(db, bundle)
    if bundle.status != "review" or bundle.revision != revision:
        raise HTTPException(409, "This review was changed. Reload it before you confirm.")
    groups = groups_for(db, bundle)
    if not groups:
        raise HTTPException(422, "Add at least one person and assign their pages before confirming.")
    validate_selections(bundle, [{"pages": group.pages} for group in groups], bundle.ignored_pages, complete=True)
    if set(decisions) - {group.id for group in groups}:
        raise HTTPException(422, "A duplicate decision does not belong to this review.")
    prepared, duplicates = [], []
    extraction = {}
    within = {"phone": {}, "email": {}, "passport_no": {}}
    for group in groups:
        try:
            origin = "email" if bundle.source_type == "email" else "whatsapp" if bundle.source_type == "whatsapp" else group.fields.get("source", "walk_in")
            person = CandidateIn.model_validate({**group.fields, "source": origin})
        except ValidationError as exc:
            raise HTTPException(422, {"message": f"Check {group.label}: " + "; ".join(f'{".".join(map(str, issue["loc"]))}: {issue["msg"]}' for issue in exc.errors(include_input=False))}) from None
        values = normalize_candidate_values(person.model_dump(exclude={"intake_group_id", "ocr_data", "resume_text", "source_detail", "source"}))
        ocr_fields, ocr_raw, notes = _ocr_for_group(bundle, group)
        if ocr_fields is not None:
            from app.services.email_intake import candidate_values
            read = candidate_values(ocr_fields, ocr_raw)
            merged = dict(values)
            for key, value in read.items():
                # What the reviewer entered stays; the document only fills what is empty.
                if merged.get(key) in (None, "", [], {}, 0) and value not in (None, "", [], {}):
                    merged[key] = value
            try:
                _validate_profile_relationships({key: merged.get(key) for key in PROFILE_CHECK_FIELDS})
                values = merged
            except ValueError:
                notes = [*notes, "Values read from the documents conflict with the reviewed details and were not applied."]
        extraction[group.id] = (ocr_raw, notes)
        for key, normalize in (("phone", normalize_phone), ("email", normalize_email), ("passport_no", normalize_passport)):
            identity = normalize(values.get(key))
            if identity and identity in within[key]:
                raise HTTPException(409, f"{group.label} and {within[key][identity]} share a {key.replace('_', ' ')}. Review and combine duplicate person groups before saving.")
            if identity:
                within[key][identity] = group.label
        matches = identity_matches(db, values)
        if len(matches) > 1:
            raise HTTPException(409, f"{group.label} matches different existing people through phone, email or passport. Resolve those identity conflicts before confirming.")
        target = matches[0][0] if matches else None
        if target and decisions.get(group.id) != target.id:
            duplicates.append({"groupId": group.id, "label": group.label, "candidateId": target.id, "name": target.name, "ref": target.ref,
                               "phone": target.phone, "matchedOn": matches[0][1], "canMerge": True})
        elif decisions.get(group.id) and (not target or decisions[group.id] != target.id):
            raise HTTPException(409, "An existing candidate changed. Check duplicate matches again.")
        if target:
            for field in ("dob", "passport_no"):
                if getattr(target, field) and values.get(field) and getattr(target, field) != values[field]:
                    raise HTTPException(409, f"{group.label}: {field.replace('_', ' ')} conflicts with the existing candidate. Review the identity before merging.")
            retained = {key: getattr(target, key) for key in PROFILE_CHECK_FIELDS}
            for key in retained:
                if retained[key] in (None, "") and values.get(key) not in (None, ""):
                    retained[key] = values[key]
            try:
                _validate_profile_relationships(retained)
            except ValueError as exc:
                raise HTTPException(409, f"{group.label}: these details conflict with the retained profile: {exc}") from None
        prepared.append((group, values, target))
    if duplicates:
        raise HTTPException(409, {"message": "Some of these people are already in the system. Check each duplicate and choose whether to merge it.", "code": "duplicate_candidates", "duplicates": duplicates})
    admins = set(db.scalars(select(User.id).where(User.active.is_(True), User.role == Role.admin)))
    supervisors = set(db.scalars(select(User.id).where(User.active.is_(True), User.role.in_([Role.admin, Role.manager]))))
    for group, values, candidate in prepared:
        ocr_raw, notes = extraction.get(group.id, (None, []))
        provenance = {"bundleId": bundle.id, "groupId": group.id, "sourceType": bundle.source_type, "sourceId": bundle.source_id, "pages": group.pages}
        if notes:
            provenance["extractionWarnings"] = notes
        stored_ocr = merge_ocr_data(ocr_raw or {}, {"person_split": provenance})
        selected_keys = {_page_key(page) for page in group.pages if page["kind"] == "resume"}
        resume_text = "\n".join(page.get("text", "") for page in bundle.pages if _page_key(page) in selected_keys)
        if candidate:
            enrich_empty(candidate, values, stored_ocr)
            if resume_text:  # the newly confirmed resume is the latest one; Data Bank search follows it
                candidate.resume_text = resume_text
        else:
            owner = next_owner(db, pointer=pointer)
            candidate = Candidate(ref=f"CND-{db.scalar(select(func.nextval('crm_candidate_reference_seq'))):05d}", **values,
                source=Source.email if bundle.source_type == "email" else Source.whatsapp if bundle.source_type == "whatsapp" else Source(group.fields.get("source", "walk_in")),
                source_detail=f"person_review:{bundle.id}", created_by=user.id,
                owner_id=owner.id if owner else None, intake_owner_id=owner.id if owner else None,
                status=CandidateStatus.new if owner and not notes else CandidateStatus.needs_review, needs_review=owner is None or bool(notes),
                resume_text=resume_text or None, ocr_data=stored_ocr)
            db.add(candidate); db.flush()
            if owner:
                started = now()
                db.add(Task(kind=TaskKind.initial_call, title="Initial call: confirm interest", candidate_id=candidate.id,
                    owner_id=owner.id, created_at=started, sla_started_at=started, due_at=started + timedelta(hours=48)))
            # PRD: the owner and the admin are told about an assignment; a waiting candidate alerts admins and managers.
            for recipient in (admins | {owner.id}) if owner else supervisors:
                db.add(Notification(user_id=recipient, kind="candidate_assignment", title="Candidate assigned" if owner else "Candidate needs assignment", detail=candidate.name, link_to=f"/candidates/{candidate.id}"))
        _attach_group(db, bundle, group, candidate, user, settings)
        group.candidate_id, group.status = candidate.id, "committed"
        db.add(AuditEntry(actor_id=user.id, action="Confirmed a person from a document check", target=candidate.name, entity_type="candidate", entity_id=candidate.id, meta=provenance))
    bundle.status, bundle.last_error = "completed", None
    bundle.revision += 1
    identifiers = list(dict.fromkeys(group.candidate_id for group in groups))
    single = identifiers[0] if len(identifiers) == 1 else None
    if bundle.source_type == "manual":
        for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.group_id == bundle.source_id, DocumentExtractionJob.owner_id == bundle.owner_id, DocumentExtractionJob.status != "dismissed").with_for_update()):
            job.status, job.last_error = "attached", None
            job.draft = {**deepcopy(job.draft or {}), "splitBundleId": bundle.id, "splitCandidateIds": identifiers}
    elif bundle.source_type == "email":
        message = db.get(MailMessage, bundle.source_id)
        job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == bundle.source_id).with_for_update())
        message.candidate_id = single
        job.candidate_id, job.status, job.last_error = single, "completed", None
    else:
        message = db.get(WhatsAppMessage, bundle.source_id)
        message.candidate_id, message.status, message.last_error = single, "completed", None
    db.flush()
    if bundle.source_type in {"email", "whatsapp"}:
        from app.services.intake_acknowledgements import queue_intake_acknowledgement
        queue_intake_acknowledgement(db, bundle.source_type, bundle.source_id)
    db.add(AuditEntry(actor_id=user.id, action="Finished checking documents for several people", entity_type="document_person_intake", entity_id=bundle.id,
        meta={"candidateIds": identifiers, "excludedPages": bundle.ignored_pages}))
    db.commit()
    return bundle_out(db, bundle)
