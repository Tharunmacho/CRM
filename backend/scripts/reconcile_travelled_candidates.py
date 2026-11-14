"""Reviewed, repeat-safe reconciliation of already-travelled people with existing CRM IDs.

Planning is read-only. Applying requires an unchanged saved plan, explicit shared-DB
authorization and stopped writers. Original files and historical rows are retained.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import UniqueConstraint, create_engine, delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, undefer
from app.db.models import (Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Base,
    Candidate, CandidateStatus, Client, Company, DailyWorkItem, Designation, DocumentExtractionJob,
    Order, OrderStatus, Role, Task, User)
from app.domain.stages import stage_by_no, validate_payload
from app.services.candidate_identity import merge_ocr_data, name_relation, normalize_email, normalize_passport, normalize_phone
from app.services.document_storage import put_bytes, read_bytes
from scripts.import_travelled_candidates import kinds_from_name

ACTION = "Reconciled confirmed historical travel"
MERGE_ACTION = "Merged verified duplicate candidate"
CURRENCY = {"UAE": "AED", "Kuwait": "KWD", "Malaysia": "MYR"}
STAGE_FILES = {"signed_offer": "stage_5_file", "passport": "stage_7_copy", "visa": "stage_9_file",
               "visa_stamping": "stage_10_file", "air_ticket": "stage_11_file"}


class ReconciliationError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def normalized(value):
    return " ".join((value or "").casefold().split())


def source_documents(folder, inventory, reviewed_pages=None, prepared_dir=None, *, retained_originals=None, original_root=None):
    """Verify all declared bytes; derive only human-reviewed page selections."""
    folder = Path(folder).resolve()
    if hashlib.sha256((folder / "_placements.json").read_bytes()).hexdigest() != inventory["summary"]["placementsSha256"]:
        raise ReconciliationError("The placement source changed; prepare a fresh inventory")
    placements = json.loads((folder / "_placements.json").read_text(encoding="utf8"))
    documents = []
    for item in inventory["files"]:
        path = (folder / item["path"]).resolve()
        if not path.is_relative_to(folder) or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ReconciliationError("A source file changed or escaped the source folder")
        person = item["person"]
        if not 1 <= person <= len(placements) or Path(item["path"]).parts[0] != placements[person - 1]["folder"]:
            raise ReconciliationError("A source file does not belong to its declared person")
        documents.append({**item, "localPath": str(path), "kinds": kinds_from_name(placements[person - 1]["folder"], path.name),
                          "contentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "derived": False})
    if reviewed_pages:
        import pymupdf
        prepared = Path(prepared_dir).resolve()
        prepared.mkdir(parents=True, exist_ok=True)
        for record in reviewed_pages["files"]:
            original = next((item for item in documents if not item["derived"] and item["person"] == record["person"]
                             and item["path"] == record["path"] and item["sha256"] == record["sha256"]), None)
            if original is None:
                raise ReconciliationError("Reviewed pages do not match an inventoried original")
            invalid = record.get("invalidOriginalSlots") or []
            if invalid:
                original["invalidOriginalSlots"] = sorted(set(invalid))
                original["kinds"] = [kind for kind in original["kinds"] if kind not in invalid] or ["source_bundle"]
            with pymupdf.open(original["localPath"]) as pdf:
                used = set()
                for kind, pages in record.get("visuallyVerifiedPages", {}).items():
                    if not pages or any(type(page) is not int or page < 1 or page > len(pdf) or page in used for page in pages) or len(set(pages)) != len(pages):
                        raise ReconciliationError("Reviewed document pages are invalid or overlap")
                    used.update(pages)
                    if kind == "photo":
                        if len(pages) != 1:
                            raise ReconciliationError("A reviewed photograph must be one page")
                        data = pdf[pages[0] - 1].get_pixmap(dpi=200).tobytes("png")
                        extension, media = "png", "image/png"
                    else:
                        selected = pymupdf.open()
                        for page in pages:
                            selected.insert_pdf(pdf, from_page=page - 1, to_page=page - 1)
                        data = selected.tobytes(garbage=4, deflate=True, no_new_id=True)
                        selected.close()
                        extension, media = "pdf", "application/pdf"
                    sha = hashlib.sha256(data).hexdigest()
                    destination = prepared / f"{record['person']:02d}-{kind}-{sha[:16]}.{extension}"
                    if destination.exists() and destination.read_bytes() != data:
                        raise ReconciliationError("A prepared document differs from its reviewed content")
                    destination.write_bytes(data)
                    documents.append({"person": record["person"], "path": destination.name, "localPath": str(destination),
                                      "sha256": sha, "bytes": len(data), "kinds": [kind], "contentType": media,
                                      "derived": True, "sourceSha256": record["sha256"], "pages": pages})
    if retained_originals:
        root = Path(original_root).resolve()
        for record in retained_originals["files"]:
            path = (root / record["path"]).resolve()
            people = record.get("people", [])
            if not path.is_relative_to(root) or not people or any(type(person) is not int or not 1 <= person <= len(placements) for person in people):
                raise ReconciliationError("A retained original has an invalid path or owner")
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != record["sha256"] or not record.get("reviewReason"):
                raise ReconciliationError("A retained original changed or lacks a review reason")
            for person in sorted(set(people)):
                documents.append({"person": person, "path": str(Path("retained-originals") / record["path"]), "localPath": str(path),
                    "sha256": record["sha256"], "bytes": len(payload), "kinds": ["source_bundle"],
                    "contentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "derived": False,
                    "retainedOriginal": True, "reviewReason": record["reviewReason"]})
    return placements, documents


def application_signature(db, application):
    stages = [(row.id, row.stage, row.attempt, row.data, row.reopened_reason) for row in db.scalars(
        select(ApplicationStage).where(ApplicationStage.application_id == application.id).order_by(ApplicationStage.stage, ApplicationStage.attempt))]
    return digest([application.id, application.order_id, application.status.value, application.current_stage, application.lock_version, stages])


def untouched_bulk_application(db, application):
    audits = list(db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "application", AuditEntry.entity_id == application.id)))
    return (application.status == ApplicationStatus.active and application.lock_version == 0 and len(audits) == 1
            and audits[0].action == "Imported candidate with documents and stage history" and (audits[0].meta or {}).get("bulk_import") is True
            and not any(row.reopened_reason for row in db.scalars(select(ApplicationStage).where(ApplicationStage.application_id == application.id))))


def exactly_one(rows, label):
    if len(rows) > 1:
        raise ReconciliationError(f"Ambiguous existing {label}; resolve it before importing")
    return rows[0] if rows else None


def master_plan(db, placements):
    companies = list(db.scalars(select(Company)))
    clients = list(db.scalars(select(Client)))
    designations = list(db.scalars(select(Designation)))
    orders = list(db.scalars(select(Order)))
    result = []
    groups = Counter((row["client"], row["job"], row["country"]) for row in placements)
    for (employer, job, country), count in sorted(groups.items()):
        company = exactly_one([row for row in companies if normalized(row.name) == normalized(employer)], "employer company")
        client = db.get(Client, company.client_id) if company else exactly_one([row for row in clients if normalized(row.name) == normalized(employer)], "client")
        designation = exactly_one([row for row in designations if normalized(row.title) == normalized(job)], "designation")
        order = exactly_one([row for row in orders if company and designation and row.company_id == company.id
                             and row.designation_id == designation.id and normalized(row.country) == normalized(country)], "job order")
        result.append({"employer": employer, "job": job, "country": country, "people": count,
                       "clientId": client.id if client else None, "companyId": company.id if company else None,
                       "designationId": designation.id if designation else None, "orderId": order.id if order else None})
    return result


def reviewed_name_correction(canonical, aliases, placement, choice, identity_hashes):
    correction = choice.get("nameCorrection")
    if not correction:
        return None
    verified = (correction.get("verifiedName") or "").strip()
    evidence = set(correction.get("sourceHashes") or [])
    if (not verified or not correction.get("reason") or len(evidence) < 2 or not evidence <= identity_hashes
            or canonical.name not in (correction.get("expectedCanonicalName"), verified)):
        raise ReconciliationError("Reviewed name correction lacks exact unchanged identity evidence")
    if (not normalize_passport(correction.get("verifiedPassport"))
            or normalize_passport(canonical.passport_no) != normalize_passport(correction["verifiedPassport"])
            or not canonical.dob or canonical.dob.isoformat() != correction.get("verifiedDob")):
        raise ReconciliationError("Reviewed name correction does not match the stored passport and date of birth")
    if name_relation(verified, placement["folder"]) == "different" or any(name_relation(verified, alias.name) == "different" for alias in aliases):
        raise ReconciliationError("Reviewed name correction conflicts with source or duplicate names")
    return {**correction, "verifiedName": verified, "actualPreviousName": canonical.name}


def merge_guard(db, canonical, alias, *, reviewed_name=None):
    from app.services import form_extraction as fx
    for column, normalizer in (("phone", normalize_phone), ("email", normalize_email), ("passport_no", normalize_passport)):
        left, right = normalizer(getattr(canonical, column)), normalizer(getattr(alias, column))
        if left and right and left != right:
            raise ReconciliationError(f"Duplicate candidates disagree on {column}")
    if canonical.dob and alias.dob and canonical.dob != alias.dob:
        raise ReconciliationError("Duplicate candidates disagree on date of birth")
    if name_relation(reviewed_name or canonical.name, alias.name) == "different":
        raise ReconciliationError("Duplicate candidate names conflict")
    for column, spec in fx.SPECS.items():
        if (alias.field_sources or {}).get(spec.key) != "manual":
            continue
        left, right = getattr(canonical, column), getattr(alias, column)
        if ((canonical.field_sources or {}).get(spec.key) != "extracted"
                and left not in (None, "", [], {}) and right not in (None, "", [], {}) and left != right):
            raise ReconciliationError(f"Duplicate candidates contain conflicting manual {column}")
    if db.scalar(select(func.count()).select_from(Application).where(Application.candidate_id == alias.id)):
        raise ReconciliationError("The duplicate gained application history; manual review is required")
    if db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(
            DocumentExtractionJob.candidate_id.in_((canonical.id, alias.id)),
            (DocumentExtractionJob.status == "processing") | DocumentExtractionJob.claimed_by.is_not(None))):
        raise ReconciliationError("A duplicate's document is still being processed; stop and drain its worker first")
    for table in Base.metadata.sorted_tables:
        for foreign in table.foreign_keys:
            if foreign.column.table.name != Candidate.__tablename__:
                continue
            for constraint in table.constraints:
                if not isinstance(constraint, UniqueConstraint) or foreign.parent.name not in constraint.columns:
                    continue
                others = [column for column in constraint.columns if column is not foreign.parent]
                for row in db.execute(select(*others).where(foreign.parent == alias.id)):
                    if all(value is not None for value in row) and db.scalar(select(func.count()).select_from(table).where(
                            foreign.parent == canonical.id, *(column == value for column, value in zip(others, row)))):
                        raise ReconciliationError(f"Duplicate relationship conflict in {table.name}; preserve both records for review")


def build_plan(db, placements, documents, decisions):
    candidates = {row.id: row for row in db.scalars(select(Candidate).options(undefer(Candidate.field_sources)))}
    applications = list(db.scalars(select(Application)))
    attachments = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate")))
    by_hash = defaultdict(set)
    source_people = defaultdict(set)
    for item in documents:
        if not item["derived"]:
            source_people[item["sha256"]].add(item["person"])
    for row in attachments:
        if row.content_hash in source_people:
            by_hash[row.content_hash].add(row.entity_id)
    masters = master_plan(db, placements)
    people = []
    retained = set()
    for index, placement in enumerate(placements, 1):
        day = placement.get("travel_date") or None
        if day:
            day = date.fromisoformat(day).isoformat()
            if day > date.today().isoformat():
                raise ReconciliationError("A confirmed historical travel date is in the future")
        elif not placement.get("travelled"):
            raise ReconciliationError("An undated placement lacks explicit confirmation of travel")
        hashes = {item["sha256"] for item in documents if item["person"] == index and not item["derived"] and len(source_people[item["sha256"]]) == 1}
        evidence = Counter(identifier for sha in hashes for identifier in by_hash.get(sha, ()))
        choice = decisions.get("people", {}).get(str(index), {})
        selected = choice.get("candidateId") or (next(iter(evidence)) if len(evidence) == 1 else None)
        if selected not in evidence or selected not in candidates or evidence[selected] < 2:
            raise ReconciliationError(f"Person {index} needs an explicit candidate selection backed by at least two unique source hashes")
        if selected in retained:
            raise ReconciliationError("Two source people resolve to the same candidate")
        retained.add(selected)
        canonical = candidates[selected]
        aliases = sorted(identifier for identifier in choice.get("mergeCandidateIds", []) if identifier in candidates)
        if set(evidence) != {selected, *aliases}:
            raise ReconciliationError(f"Person {index} has an unreviewed duplicate match")
        correction = reviewed_name_correction(canonical, [candidates[identifier] for identifier in aliases], placement, choice, hashes)
        effective_name = correction["verifiedName"] if correction else canonical.name
        if name_relation(effective_name, placement["folder"]) == "different":
            raise ReconciliationError(f"Person {index} has conflicting name and document evidence")
        for identifier in aliases:
            merge_guard(db, canonical, candidates[identifier], reviewed_name=effective_name)
        owned_apps = [row for row in applications if row.candidate_id == selected]
        application = exactly_one(owned_apps, "candidate placement")
        master = next(row for row in masters if (row["employer"], row["job"], row["country"]) ==
                      (placement["client"], placement["job"], placement["country"]))
        if application and application.status != ApplicationStatus.deployed:
            if application.id not in decisions.get("correctBulkApplicationIds", []) or not untouched_bulk_application(db, application):
                raise ReconciliationError(f"Person {index} has unreviewed or changed application history")
        if application and application.status == ApplicationStatus.deployed:
            if application.order_id != master["orderId"]:
                raise ReconciliationError("An existing deployed placement belongs to a different job")
            stage = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == application.id,
                ApplicationStage.stage == 11).order_by(ApplicationStage.attempt.desc()).limit(1))
            if stage is None or (stage.data or {}).get("travelDate") != day:
                raise ReconciliationError("An existing deployed placement has different travel evidence")
        people.append({"index": index, "candidateId": selected, "mergeCandidateIds": aliases,
                       "nameCorrection": correction,
                       "identityHashes": sorted(hashes), "matchedHashes": dict(sorted(evidence.items())),
                       "applicationId": application.id if application else None,
                       "applicationSignature": application_signature(db, application) if application else None,
                       "travelDate": day, "dateUnknown": day is None,
                       "action": "already_deployed" if application and application.status == ApplicationStatus.deployed else "reconcile"})
    plan = {"version": 1, "sourceDigest": digest([placements, [{key: value for key, value in row.items() if key != "localPath"} for row in documents]]),
            "masters": masters, "people": people,
            "summary": {"people": len(people), "knownDates": sum(bool(row["travelDate"]) for row in people),
                        "unknownDates": sum(row["dateUnknown"] for row in people), "merges": sum(len(row["mergeCandidateIds"]) for row in people),
                        "clientGroups": len({row["employer"] for row in masters}), "orders": len(masters), "sourceDocuments": len(documents)}}
    plan["approvalHash"] = digest(plan)
    return plan


def merge_duplicate(db, user, canonical, alias, source_hash):
    """Move rows, IDs and evidence intact; uniqueness conflicts roll back the whole import."""
    merge_guard(db, canonical, alias)
    from app.services import form_extraction as fx
    original_id = alias.id
    snapshot = {column.name: getattr(alias, column.name) for column in Candidate.__table__.columns if column.name != "resume_tsv"}
    ignore = {"id", "ref", "created_at", "updated_at", "resume_tsv", "owner_id", "intake_owner_id", "created_by", "status", "source", "source_detail", "field_sources", "ocr_data", "needs_review"}
    sources = deepcopy(canonical.field_sources or {})
    for column in Candidate.__table__.columns:
        name = column.name
        if name in ignore:
            continue
        value, old = getattr(alias, name), getattr(canonical, name)
        spec = fx.SPECS.get(name)
        if spec and (alias.field_sources or {}).get(spec.key) == "manual" and value not in (None, "", [], {}):
            setattr(canonical, name, deepcopy(value))
            sources[spec.key] = "manual"
            continue
        if old in (None, "", [], {}) and value not in (None, "", [], {}):
            setattr(canonical, name, deepcopy(value))
            if spec and spec.key in (alias.field_sources or {}):
                sources[spec.key] = alias.field_sources[spec.key]
        elif isinstance(old, list) and isinstance(value, list):
            setattr(canonical, name, old + [item for item in value if item not in old])
        elif name == "call_remarks" and value and value != old:
            canonical.call_remarks = (old or "") + "\n\nPreserved duplicate entry note:\n" + value
    canonical.field_sources = sources
    fx.mark_stored_applied(canonical)
    for kind in fx.SLOT_KINDS:
        for raw in fx.stored_documents(alias.ocr_data, kind):
            fx.apply_document(canonical, kind, raw, db)
    canonical.ocr_data = merge_ocr_data(canonical.ocr_data, alias.ocr_data)
    versions = dict(db.execute(select(Attachment.slot, func.max(Attachment.version)).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == canonical.id).group_by(Attachment.slot)).all())
    attachment_ids = []
    for attachment in db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == original_id)
                                 .order_by(Attachment.uploaded_at, Attachment.version)):
        versions[attachment.slot] = versions.get(attachment.slot, 0) + 1
        attachment.entity_id, attachment.version = canonical.id, versions[attachment.slot]
        attachment_ids.append(attachment.id)
    for job in db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == original_id)):
        draft = deepcopy(job.draft or {})
        if draft.get("savedCandidateId") == original_id:
            draft["savedCandidateId"] = canonical.id
        if (draft.get("applicationForm") or {}).get("candidateId") == original_id:
            draft["applicationForm"]["candidateId"] = canonical.id
        job.draft = draft
    db.flush()
    moved = {}
    for table in Base.metadata.sorted_tables:
        for foreign in table.foreign_keys:
            if foreign.column.table.name == Candidate.__tablename__:
                count = db.execute(update(table).where(foreign.parent == original_id).values({foreign.parent.name: canonical.id})).rowcount
                if count:
                    moved[f"{table.name}.{foreign.parent.name}"] = count
    for audit in db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "candidate", AuditEntry.entity_id == original_id)):
        audit.entity_id = canonical.id
        audit.meta = {**(audit.meta or {}), "originalCandidateId": original_id}
    prefix = f"/candidates/{original_id}"
    for table in Base.metadata.sorted_tables:
        if "link_to" in table.c:
            for row in db.execute(select(table.c.id, table.c.link_to).where(table.c.link_to.startswith(prefix))):
                suffix = row.link_to[len(prefix):]
                if not suffix or suffix[0] in "/?#":
                    db.execute(update(table).where(table.c.id == row.id).values(link_to=f"/candidates/{canonical.id}{suffix}"))
    db.flush()
    db.execute(delete(Candidate).where(Candidate.id == original_id))
    db.add(AuditEntry(actor_id=user.id, action=MERGE_ACTION, entity_type="candidate", entity_id=canonical.id,
                     meta={"sourceCandidateId": original_id, "sourceRef": alias.ref, "profileSnapshotHash": digest(snapshot),
                           "sourceInventory": source_hash, "attachmentIdsPreserved": attachment_ids, "relationshipsMoved": moved}))


def ensure_masters(db, plan):
    result, contacts, companies, titles = {}, {}, {}, {}
    for item in plan["masters"]:
        label, job, country = item["employer"], item["job"], item["country"]
        client = db.get(Client, item["clientId"]) if item["clientId"] else contacts.get(label)
        if client is None:
            client = Client(name=label, country=country, status="active", notes="Employer label from confirmed historical travel. Contact person, phone and email are not recorded.")
            db.add(client); db.flush(); contacts[label] = client
        company = db.get(Company, item["companyId"]) if item["companyId"] else companies.get(label)
        if company is None:
            company = Company(client_id=client.id, name=label, country=country, status="active")
            db.add(company); db.flush(); companies[label] = company
        title = db.get(Designation, item["designationId"]) if item["designationId"] else titles.get(job)
        if title is None:
            title = Designation(title=job, required_skills=[], min_experience=0)
            db.add(title); db.flush(); titles[job] = title
        order = db.get(Order, item["orderId"]) if item["orderId"] else None
        if order is None:
            number = db.scalar(select(func.nextval("crm_order_reference_seq")))
            order = Order(ref=f"ORD-{datetime.now(timezone.utc).year}-{number:05d}", company_id=company.id,
                          designation_id=title.id, openings=item["people"], country=country,
                          currency=CURRENCY.get(country, "USD"), benefits=[], status=OrderStatus.open)
            db.add(order); db.flush()
        result[(label, job, country)] = order
    return result


def reconcile(db, user, settings, placements, documents, decisions, approved_plan, *, writers_stopped=False):
    if not writers_stopped or user.role != Role.admin:
        raise ReconciliationError("An active administrator and stopped writers are required")
    fresh = build_plan(db, placements, documents, decisions)
    if fresh != approved_plan:
        raise ReconciliationError("The reviewed plan is stale; review a fresh plan before applying")
    selected = {row["candidateId"] for row in fresh["people"]} | {identifier for row in fresh["people"] for identifier in row["mergeCandidateIds"]}
    candidates = {row.id: row for row in db.scalars(select(Candidate).where(Candidate.id.in_(selected)).order_by(Candidate.id).with_for_update())}
    merge_ids = {identifier for row in fresh["people"] if row["mergeCandidateIds"] for identifier in [row["candidateId"], *row["mergeCandidateIds"]]}
    if merge_ids:
        for row in db.scalars(select(Candidate).options(undefer("*")).where(Candidate.id.in_(merge_ids))):
            candidates[row.id] = row
    jobs = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id.in_(selected)).with_for_update()))
    if any(job.status == "processing" or job.claimed_by for job in jobs):
        raise ReconciliationError("Selected document jobs still hold processing claims; drain stopped workers before applying")
    orders = ensure_masters(db, fresh)
    now = datetime.now(timezone.utc)
    outcome = {"candidatesReconciled": 0, "duplicatesMerged": 0, "documentsAdded": 0, "documentSlotsCorrected": 0, "alreadyDeployed": 0}
    verified_storage = {}

    def verify_storage(key, expected_hash):
        actual_hash = verified_storage.get(key)
        if actual_hash is None:
            actual_hash = hashlib.sha256(read_bytes(settings, key)).hexdigest()
            verified_storage[key] = actual_hash
        if actual_hash != expected_hash:
            raise ReconciliationError("A stored document did not pass checksum verification")

    for person in fresh["people"]:
        index = person["index"]
        placement, candidate = placements[index - 1], candidates[person["candidateId"]]
        correction = person.get("nameCorrection")
        if correction and candidate.name != correction["verifiedName"]:
            candidate.name = correction["verifiedName"]
            db.add(AuditEntry(actor_id=user.id, action="Corrected historical identity from reviewed documents", entity_type="candidate", entity_id=candidate.id,
                             meta={"field": "name", "before": correction["actualPreviousName"], "after": correction["verifiedName"],
                                   "sourceHashes": correction["sourceHashes"], "reason": correction["reason"], "sourceInventory": fresh["sourceDigest"]}))
        for identifier in person["mergeCandidateIds"]:
            merge_duplicate(db, user, candidate, candidates[identifier], fresh["sourceDigest"])
            outcome["duplicatesMerged"] += 1
        db.flush()
        owned = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id)))
        for item in sorted((row for row in documents if row["person"] == index), key=lambda row: row["derived"]):
            matching = [row for row in owned if row.content_hash == item["sha256"]]
            for attachment in matching:
                verify_storage(attachment.object_key, item["sha256"])
                if attachment.slot in item.get("invalidOriginalSlots", []):
                    previous_slot = attachment.slot
                    attachment.version = max((row.version for row in owned if row.slot == "source_bundle"), default=0) + 1
                    attachment.slot = "source_bundle"
                    outcome["documentSlotsCorrected"] += 1
                    db.add(AuditEntry(actor_id=user.id, action="Corrected a reviewed historical document type", entity_type="candidate", entity_id=candidate.id,
                        meta={"attachmentId": attachment.id, "previousSlot": previous_slot, "sourceSha256": item["sha256"], "sourceInventory": fresh["sourceDigest"]}))
            if matching and (not item["derived"] or any(row.slot in item["kinds"] for row in matching)):
                continue
            data = Path(item["localPath"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ReconciliationError("Prepared source bytes changed after review")
            key = matching[0].object_key if matching else put_bytes(settings,
                f"travel-history/{item['sha256'][:2]}/{item['sha256']}{Path(item['path']).suffix.lower()}", data, item["contentType"])
            verify_storage(key, item["sha256"])
            for kind in item["kinds"]:
                version = max((row.version for row in owned if row.slot == kind), default=0) + 1
                attachment = Attachment(entity_type="candidate", entity_id=candidate.id, slot=kind, version=version,
                    file_name=Path(item["path"]).name, object_key=key, content_hash=item["sha256"], content_type=item["contentType"],
                    size_kb=(len(data) + 1023) // 1024, uploaded_by=user.id, uploaded_at=now)
                db.add(attachment); db.flush(); owned.append(attachment); outcome["documentsAdded"] += 1
                if kind in {"resume", "passport", "aadhaar"}:
                    from app.services.application_form import queue_reading
                    queue_reading(db, user, candidate, attachment, item["contentType"])
                db.add(AuditEntry(actor_id=user.id, action="Attached verified historical document", entity_type="candidate", entity_id=candidate.id,
                    meta={"attachmentId": attachment.id, "sourceInventory": fresh["sourceDigest"], "sourceSha256": item.get("sourceSha256", item["sha256"]), "selectedPages": item.get("pages")}))
        order = orders[(placement["client"], placement["job"], placement["country"])]
        if person["action"] == "already_deployed":
            candidate.status = CandidateStatus.deployed
            outcome["alreadyDeployed"] += 1
            continue
        application = db.get(Application, person["applicationId"]) if person["applicationId"] else None
        before = {"orderId": application.order_id, "currentStage": application.current_stage} if application else None
        if application is None:
            application = Application(candidate_id=candidate.id, order_id=order.id, owner_id=candidate.owner_id or user.id, draft={})
            db.add(application); db.flush()
        application.order_id, application.status, application.current_stage = order.id, ApplicationStatus.deployed, 11
        application.closure_reason, application.lock_version = None, application.lock_version + 1
        data = {key: placement[source] for source, key in (("travel_date", "travelDate"), ("airline", "airline"), ("pnr", "pnr")) if placement.get(source)}
        for kind, slot in STAGE_FILES.items():
            source = max((row for row in owned if row.slot == kind), key=lambda row: (row.uploaded_at, row.version), default=None)
            if source is None:
                continue
            evidence = Attachment(entity_type="application", entity_id=application.id, slot=slot,
                version=(db.scalar(select(func.max(Attachment.version)).where(Attachment.entity_type == "application", Attachment.entity_id == application.id, Attachment.slot == slot)) or 0) + 1,
                file_name=source.file_name, object_key=source.object_key, content_hash=source.content_hash, content_type=source.content_type,
                size_kb=source.size_kb, uploaded_by=user.id, uploaded_at=now)
            db.add(evidence); db.flush()
            if kind == "air_ticket":
                data["file"] = {"id": evidence.id, "name": evidence.file_name, "url": f"/api/files/{evidence.id}", "sizeKb": evidence.size_kb, "version": evidence.version}
        # Completion is the office's confirmed historical fact. No fictitious
        # intermediate payment, interview or visa milestone is manufactured.
        outcome_label = stage_by_no(11).evaluate({"file": {"id": "confirmed-history"}}).outcome
        stage = ApplicationStage(application_id=application.id, stage=11, attempt=1, data=validate_payload(11, data),
                                 outcome=outcome_label, completed_by=user.id, owner_id=application.owner_id, completed_at=now)
        db.add(stage)
        candidate.status = CandidateStatus.deployed
        from app.services.traveller_id import assign_traveller_id
        assign_traveller_id(db, candidate, data.get("travelDate"), now)   # numbered by the financial year of the travel date
        for task in db.scalars(select(Task).where(Task.candidate_id == candidate.id, Task.completed_at.is_(None), Task.cancelled_at.is_(None))):
            task.cancelled_at = now
        for allocation in db.scalars(select(DailyWorkItem).where(DailyWorkItem.candidate_id == candidate.id, DailyWorkItem.status == "open")):
            allocation.status, allocation.released_at, allocation.release_reason = "released", now, "task_cancelled"
        db.add(AuditEntry(actor_id=user.id, action=ACTION, entity_type="application", entity_id=application.id,
            meta={"sourceInventory": fresh["sourceDigest"], "personIndex": index, "officeConfirmedTravel": True,
                  "unknownTravelDate": person["dateUnknown"], "earlierPlacement": before, "missingMilestonesNotInvented": True}))
        outcome["candidatesReconciled"] += 1
    db.flush()
    for order in orders.values():
        deployed = db.scalar(select(func.count()).select_from(Application).where(Application.order_id == order.id, Application.status == ApplicationStatus.deployed))
        order.openings = max(order.openings, deployed)
        if deployed >= order.openings:
            order.status = OrderStatus.filled
    db.flush()
    proof = []
    for person in fresh["people"]:
        candidate = candidates[person["candidateId"]]
        application = exactly_one(list(db.scalars(select(Application).where(Application.candidate_id == candidate.id))), "reconciled placement")
        stage = db.scalar(select(ApplicationStage).where(ApplicationStage.application_id == application.id, ApplicationStage.stage == 11)
                          .order_by(ApplicationStage.attempt.desc()).limit(1)) if application else None
        placement = placements[person["index"] - 1]
        order = orders[(placement["client"], placement["job"], placement["country"])]
        if (candidate.status != CandidateStatus.deployed or application is None or application.status != ApplicationStatus.deployed
                or application.order_id != order.id or stage is None or (stage.data or {}).get("travelDate") != person["travelDate"]):
            raise ReconciliationError("Historical travel postconditions failed; rolling back the import")
        if person["applicationId"] and application.id != person["applicationId"]:
            raise ReconciliationError("An existing placement ID changed; rolling back the import")
        if any(db.get(Candidate, identifier) is not None for identifier in person["mergeCandidateIds"]):
            raise ReconciliationError("A duplicate remains after reconciliation")
        company = db.get(Company, order.company_id)
        owned_documents = list(db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id)))
        proof.append({"index": person["index"], "candidateId": candidate.id, "applicationId": application.id,
                      "orderId": order.id, "companyId": company.id, "clientId": company.client_id,
                      "client": placement["client"], "job": placement["job"], "country": placement["country"],
                      "reusedApplication": person["applicationId"] is not None,
                      "travelDate": person["travelDate"], "dateUnknown": person["dateUnknown"],
                      "documents": [{"id": row.id, "slot": row.slot, "sha256": row.content_hash} for row in owned_documents]})
    outcome["verifiedPlacements"] = proof
    outcome["verifiedCounts"] = {"candidatesDeployed": len(proof), "applicationsDeployed": len(proof),
        "knownTravelDates": sum(bool(row["travelDate"]) for row in proof), "unknownTravelDates": sum(row["dateUnknown"] for row in proof),
        "clients": len({row["clientId"] for row in proof}), "orders": len({row["orderId"] for row in proof}),
        "reusedApplications": sum(row["reusedApplication"] for row in proof)}
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--reviewed-pages", type=Path)
    parser.add_argument("--retained-originals", type=Path)
    parser.add_argument("--original-root", type=Path)
    parser.add_argument("--prepared-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--approved-plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--use-tunnel", action="store_true")
    parser.add_argument("--user-id")
    args = parser.parse_args()
    from app.core.config import get_settings
    settings = get_settings()
    url = make_url(settings.database_url)
    if args.use_tunnel:
        port = json.loads((Path(__file__).resolve().parents[1] / ".db-tunnel.json").read_text())["local_port"]
        url = url.set(host="127.0.0.1", port=int(port))
    if args.apply and not args.allow_remote:
        raise ReconciliationError("Applying requires --allow-remote even for a loopback SSH tunnel")
    inventory, decisions = (json.loads(path.read_text(encoding="utf8")) for path in (args.inventory, args.decisions))
    pages = json.loads(args.reviewed_pages.read_text(encoding="utf8")) if args.reviewed_pages else None
    retained = json.loads(args.retained_originals.read_text(encoding="utf8")) if args.retained_originals else None
    if retained and not args.original_root:
        raise ReconciliationError("Retained originals require --original-root")
    placements, documents = source_documents(args.folder, inventory, pages, args.prepared_dir,
                                            retained_originals=retained, original_root=args.original_root)
    engine = create_engine(url, connect_args={"connect_timeout": 10}, hide_parameters=True)
    with engine.begin() as connection:
        if not args.apply:
            connection.execute(text("SET TRANSACTION READ ONLY"))
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
            if args.apply:
                if not args.approved_plan:
                    raise ReconciliationError("Apply requires the reviewed --approved-plan")
                approved = json.loads(args.approved_plan.read_text(encoding="utf8"))
                users = list(db.scalars(select(User).where(User.role == Role.admin, User.active.is_(True))))
                user = next((row for row in users if row.id == args.user_id), None) if args.user_id else exactly_one(users, "active administrator")
                if user is None:
                    raise ReconciliationError("Choose an active administrator")
                result = reconcile(db, user, settings, placements, documents, decisions, approved, writers_stopped=args.writers_stopped)
                db.flush()
                payload = {"applied": True, "planHash": approved["approvalHash"], "result": result}
            else:
                payload = build_plan(db, placements, documents, decisions)
    args.report.write_text(json.dumps(payload, indent=2, default=str), encoding="utf8")
    print(json.dumps({key: value for key, value in payload["result"].items() if key != "verifiedPlacements"} if "result" in payload else payload["summary"]))


if __name__ == "__main__":
    main()
