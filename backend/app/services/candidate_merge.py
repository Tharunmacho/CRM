"""Merge a duplicate candidate INTO the existing one, from the app (admin only).

The same person was imported twice: the new record (the "source", e.g. CI-167) holds documents, reading jobs, notes and tasks
that belong on the older record (the "target", e.g. CI-139). `merge_candidate` moves everything worth keeping onto the target
and then removes the source with the CRM's OWN delete (`app.api.routers.candidates.delete_candidate`, the routine the
`scripts/delete_candidate_permanently.py` script calls in-process), all in ONE transaction.

What moves (and how):
  * documents (Attachment rows of the candidate): a new version on the target in each slot; a file whose content hash is already
    in that slot of the target is skipped (everything that pointed at it follows to the target's copy). The file name is kept, but
    when it starts with the source's name stem it is renamed to the target's stem (`rename_imported_files`).
    The stored bytes of a moved document are never touched; only the files left on the source are removed, after the commit.
  * reading jobs (DocumentExtractionJob) and their saved results: re-pointed at the target (and at the target's attachments).
  * what was read (`ocr_data`) and the profile: the target's own values always win, empty target fields take the source's value,
    with the "extracted / manual" provenance of that value. Stored readings are merged with `merge_ocr_data` (nothing is lost).
  * notes (application-form notes, task remarks), tasks with their daily-work items, the audit trail (a merge entry on the
    target, a tombstone for the source), applications / stages / placements (when only one of the two is on an order), invoices
    that are still drafts, leads, mail and WhatsApp links, notifications and to-do links.

What is refused (HTTP 409, nothing changed): the same candidate twice; either one deployed, travelled or with invoice / payment
history; both on an order; different passport numbers (two different people) unless an admin confirms AND they share a phone or
e-mail; no sign they are the same person (no shared passport, phone or e-mail) unless an admin confirms.

After the commit the documents of the source that an identity check had stopped are judged again against the target
(`reconsider_identity_rejection`), so the passport that said "Already in the CRM as CI-139" now fills CI-139.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import Text, cast, exists, func, literal, or_, select, text, update
from sqlalchemy.orm import Session, undefer

from app.db.models import (
    Application, ApplicationFeedback, ApplicationStatus, Attachment, AttendanceCoverItem, AuditEntry, Candidate,
    CandidateInvoice, CandidateStatus, DailyWorkItem, DocumentExtractionJob, DocumentPersonGroup, EmailIngestionJob,
    InvoicePayment, Lead, MailMessage, Notification, Order, Remark, Role, Task, TaskEvent, TaskKind, TodoItem, User,
    WhatsAppConversation, WhatsAppMessage,
)
from app.services import agency_settings
from app.services import form_extraction as fx
from app.services.candidate_identity import merge_ocr_data, normalize_email, normalize_passport, normalize_phone
from app.services.candidate_review import UNNAMED
from app.core.roles import is_admin_level

logger = logging.getLogger(__name__)

TOMBSTONE_ACTION = "Duplicate candidate merged and removed"
_CARE_ID_LOCK_KEY = 7_340_001   # the key `care_india_id.allocate_care_id` takes: the Care India ID series stays consistent

# Plain profile columns outside fx.SPECS that an empty target takes from the source.
_FILL_PLAIN = ("expected_salary", "resume_text", "call_called", "call_interested", "call_recorded_at")


# ------------------------------------------------------------------------------------------------ small helpers

def _short(candidate) -> str:
    return candidate.care_id or candidate.ref


def _label(candidate) -> str:
    return f"{_short(candidate)} · {candidate.name}"


def _refusal(code: str, message: str, *, can_confirm: bool = False) -> dict:
    return {"code": code, "message": message, "canConfirm": can_confirm}


def _remap(value: Any, mapping: dict[str, str]) -> Any:
    """Replace every string that EQUALS a key (an id) anywhere in a JSON value."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _remap(item, mapping) for key, item in value.items()}
    return value


def _load_pair(db: Session, source_id: str, target_id: str, *, lock: bool) -> dict[str, Candidate]:
    """Both candidates with the columns the merge reads. Locked in id order, so two merges can never wait on each other."""
    statement = (select(Candidate).where(Candidate.id.in_(sorted({source_id, target_id}))).order_by(Candidate.id)
                 .options(undefer(Candidate.education), undefer(Candidate.work_history), undefer(Candidate.field_sources),
                          undefer(Candidate.source_claims), undefer(Candidate.ocr_data))
                 .execution_options(populate_existing=True))
    if lock:
        statement = statement.with_for_update()
    return {candidate.id: candidate for candidate in db.scalars(statement)}


def _passports_read_from_documents(db: Session, candidate: Candidate) -> set[str]:
    """Passport numbers the candidate's own passport documents carry: the stored readings and the saved reading jobs."""
    found: set[str] = set()

    def note(raw: Any) -> None:
        if isinstance(raw, dict):
            number = normalize_passport(fx.values_for("passport", raw).get("passport_no"))
            if number:
                found.add(number)

    for raw in fx.stored_documents(candidate.ocr_data, "passport"):
        note(raw)
    states = db.scalars(select(DocumentExtractionJob.state).where(DocumentExtractionJob.candidate_id == candidate.id))
    for state in states:
        output = state.get("output") if isinstance(state, dict) else None
        extractions = output.get("extractions") if isinstance(output, dict) else None
        passport = extractions.get("passport") if isinstance(extractions, dict) else None
        note(passport.get("raw") if isinstance(passport, dict) else None)
    return found


def _is_deployed(db: Session, candidate: Candidate) -> bool:
    deployed = db.scalar(select(exists().where(Application.candidate_id == candidate.id, Application.status == ApplicationStatus.deployed)))
    return bool(deployed or candidate.traveller_id or candidate.status is CandidateStatus.deployed)


def _blockers(db: Session, candidate: Candidate) -> list[str]:
    """Why this candidate may not be REMOVED by a merge. Only the duplicate (the source) is checked: a deployed or travelled candidate
    that is merged INTO (the target) is only added to, never changed in its stages."""
    reasons: list[str] = []
    if _is_deployed(db, candidate):
        reasons.append(f"{_label(candidate)} has been deployed or has travelled. That is kept on record, so it cannot be merged away. "
                       "Merge the other candidate into it instead.")
    financial = db.scalar(select(exists().where(
        CandidateInvoice.candidate_id == candidate.id,
        or_(CandidateInvoice.number.is_not(None), exists().where(InvoicePayment.invoice_id == CandidateInvoice.id)))))
    if financial:
        reasons.append(f"{_label(candidate)} has a reserved or issued invoice, or recorded payments. Financial history is kept, so it cannot be merged away.")
    return reasons


# ------------------------------------------------------------------------------------------------ the plan

def _identity_refusals(db: Session, source: Candidate, target: Candidate, confirm: bool) -> tuple[list[dict], dict]:
    home = agency_settings.home_digits(db)
    source_passport, target_passport = normalize_passport(source.passport_no), normalize_passport(target.passport_no)
    read_passports = _passports_read_from_documents(db, source)
    # The number on the profile is what the person's record says; only without one do the documents speak for the record.
    source_passports = {source_passport} if source_passport else read_passports
    phones = lambda c: {normalize_phone(value, home) for value in (c.phone, c.whatsapp)} - {""}   # noqa: E731
    shared_phone = bool(phones(source) & phones(target))
    shared_email = bool(normalize_email(source.email) and normalize_email(source.email) == normalize_email(target.email))
    shared_contact = shared_phone or shared_email
    same_passport = bool(target_passport and target_passport in source_passports)
    different = bool(target_passport and source_passports and target_passport not in source_passports)
    evidence = {"samePassport": same_passport, "sharedPhone": shared_phone, "sharedEmail": shared_email, "confirmed": bool(confirm)}
    refusals: list[dict] = []
    if different:
        if not (confirm and shared_contact):
            refusals.append(_refusal(
                "different_passports",
                f"{_label(source)} and {_label(target)} have different passport numbers, so they look like two different people. "
                + ("They share a phone number or e-mail, so an admin can confirm that they are the same person and merge anyway."
                   if shared_contact else "They share no phone number or e-mail either, so they cannot be merged."),
                can_confirm=shared_contact))
    elif not (same_passport or shared_contact or confirm):
        refusals.append(_refusal(
            "identity_unconfirmed",
            f"Nothing shows that {_label(source)} and {_label(target)} are the same person: no shared passport number, phone number or "
            "e-mail. If you are sure they are one person, confirm it and merge.", can_confirm=True))
    return refusals, evidence


def _plan(db: Session, source: Candidate, target: Candidate, confirm: bool) -> SimpleNamespace:
    """Everything the merge would do and everything that stops it. Reads only."""
    plan = SimpleNamespace(refusals=[], evidence={}, moves=[], remap={}, skipped=[], tasks=[], skipped_tasks=[], counts={},
                           target_deployed=_is_deployed(db, target), notice=None, withdraw=False)
    plan.refusals += [_refusal("kept_on_record", reason) for reason in _blockers(db, source)]
    # A deployed target keeps its placement, stages, tasks, owner and invoices exactly as they are: only documents, reading results,
    # empty details, notes and links are added. The duplicate's own placements and work are then not carried over.
    plan.move_work = not plan.target_deployed
    if plan.target_deployed:
        plan.notice = f"{_short(target)} is deployed: documents and empty details are added, its stages are not touched."

    active = {candidate.id: db.execute(
        select(Application.id, Order.ref).join(Order, Order.id == Application.order_id)
        .where(Application.candidate_id == candidate.id, Application.status == ApplicationStatus.active)).all()
        for candidate in (source, target)}
    if active[source.id] and active[target.id]:
        plan.refusals.append(_refusal(
            "both_on_orders",
            f"Both candidates are on an order ({active[source.id][0].ref} for {_short(source)}, {active[target.id][0].ref} for {_short(target)}). "
            "Close or move one of the placements first, then merge."))
    identity_refusals, plan.evidence = _identity_refusals(db, source, target, confirm)
    plan.refusals += identity_refusals

    # Documents: a new version per slot on the target; the same file (hash) already in a slot is skipped.
    have: dict[tuple[str, str], str] = {}
    newest: dict[str, int] = {}
    present = select(Attachment.slot, Attachment.content_hash, Attachment.id, Attachment.version, Attachment.deleted_at).where(
        Attachment.entity_type == "candidate", Attachment.entity_id == target.id).order_by(Attachment.version.desc(), Attachment.id)
    for slot, content_hash, attachment_id, version, deleted_at in db.execute(present):
        if content_hash and deleted_at is None:
            have.setdefault((slot, content_hash), attachment_id)
        newest[slot] = max(newest.get(slot, 0), version)
    documents = db.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == source.id)
                           .order_by(Attachment.slot, Attachment.version, Attachment.uploaded_at, Attachment.id)).all()
    for document in documents:
        if document.deleted_at is None and document.content_hash and (document.slot, document.content_hash) in have:
            plan.remap[document.id] = have[(document.slot, document.content_hash)]
            plan.skipped.append({"kind": "document", "name": document.file_name, "slot": document.slot,
                                 "reason": f"The same file is already on {_short(target)}."})
            continue
        newest[document.slot] = newest.get(document.slot, 0) + 1
        plan.moves.append((document, newest[document.slot]))
        if document.deleted_at is None and document.content_hash:
            have[(document.slot, document.content_hash)] = document.id

    source_apps = db.execute(select(Application.status, Order.ref).join(Order, Order.id == Application.order_id)
                             .where(Application.candidate_id == source.id).order_by(Order.ref)).all()
    if not plan.move_work:
        # Nothing of the duplicate's work is put on a deployed candidate: applications, stages, tasks, daily work, invoices.
        for state, order_ref in source_apps:
            plan.skipped.append({"kind": "placement", "name": order_ref, "reason": f"{_short(target)} is deployed, so its stages are not touched: "
                                 f"the {state.value} placement on {order_ref} is not carried over and goes with {_short(source)}."})
        plan.withdraw = any(state is ApplicationStatus.active for state, _ in source_apps)
        skipped_tasks = db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == source.id)) or 0
        if skipped_tasks:
            plan.skipped.append({"kind": "task", "name": f"{skipped_tasks} task{'s' if skipped_tasks != 1 else ''}",
                                 "reason": f"{_short(target)} is deployed, so the duplicate's tasks are not added to it."})
    # Tasks: all move with their remarks and daily-work items, except a second open "initial call" (the target has its own).
    target_has_call = bool(db.scalar(select(exists().where(
        Task.candidate_id == target.id, Task.kind == TaskKind.initial_call, Task.completed_at.is_(None), Task.cancelled_at.is_(None)))))
    for task_id, kind, title, completed_at, cancelled_at in (db.execute(
            select(Task.id, Task.kind, Task.title, Task.completed_at, Task.cancelled_at).where(Task.candidate_id == source.id)) if plan.move_work else ()):
        if kind is TaskKind.initial_call and completed_at is None and cancelled_at is None and target_has_call:
            plan.skipped_tasks.append(task_id)
            plan.skipped.append({"kind": "task", "name": title, "reason": f"{_short(target)} already has an open first call."})
        else:
            plan.tasks.append(task_id)
    moved_tasks = plan.tasks
    counts = db.execute(select(
        select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == source.id).scalar_subquery(),
        select(func.count()).select_from(ApplicationFeedback).where(ApplicationFeedback.candidate_id == source.id, ApplicationFeedback.deleted_at.is_(None)).scalar_subquery(),
        select(func.count()).select_from(Remark).where(Remark.task_id.in_(moved_tasks)).scalar_subquery() if moved_tasks else literal(0),
        select(func.count()).select_from(Application).where(Application.candidate_id == source.id).scalar_subquery() if plan.move_work else literal(0),
        (select(func.count()).select_from(MailMessage).where(MailMessage.candidate_id == source.id).scalar_subquery()
         + select(func.count()).select_from(WhatsAppMessage).where(WhatsAppMessage.candidate_id == source.id).scalar_subquery()
         + select(func.count()).select_from(WhatsAppConversation).where(WhatsAppConversation.candidate_id == source.id).scalar_subquery()
         + select(func.count()).select_from(Lead).where(or_(Lead.candidate_id == source.id, Lead.matched_candidate_id == source.id)).scalar_subquery()),
    )).one()
    plan.counts = {"jobs": counts[0], "feedback": counts[1], "remarks": counts[2], "applications": counts[3], "links": counts[4]}
    return plan


def _summary(source: Candidate, target: Candidate, plan: SimpleNamespace) -> dict:
    return {
        "source": {"id": source.id, "careId": source.care_id, "name": source.name},
        "mergedInto": {"id": target.id, "careId": target.care_id, "name": target.name},
        "moved": {"documents": len(plan.moves), "jobs": plan.counts["jobs"],
                  "notes": plan.counts["feedback"] + plan.counts["remarks"], "tasks": len(plan.tasks),
                  "applications": plan.counts["applications"], "links": plan.counts["links"]},
        "skipped": plan.skipped,
        "targetDeployed": plan.target_deployed,
        "notice": plan.notice,
        "evidence": plan.evidence,
        "refusals": plan.refusals,
    }


# ------------------------------------------------------------------------------------------------ the move

def _fill_empty(source: Candidate, target: Candidate) -> list[str]:
    """Empty target fields take the source's value (with its extracted / manual provenance). A value the target already has,
    a person's or a document's, is never replaced."""
    target_sources, source_sources = fx.sources_of(target), fx.sources_of(source)
    filled: list[str] = []
    marks = {name: set(target_sources.get(name) or []) for name in ("_fromPassport", "_fromAadhaar")}
    for column, spec in fx.SPECS.items():
        incoming = getattr(source, column)
        if incoming in (None, "", [], {}, 0) or (spec.kind == "text" and not str(incoming).strip()):
            continue
        if column == "name" and incoming == UNNAMED:
            continue
        if not fx._is_empty(spec, getattr(target, column), target_sources.get(spec.key)):
            continue
        setattr(target, column, deepcopy(incoming))
        filled.append(spec.key)
        if spec.key in source_sources:
            target_sources[spec.key] = source_sources[spec.key]
        for name, kept in marks.items():
            if spec.key in (source_sources.get(name) or []):
                kept.add(spec.key)
    for column in _FILL_PLAIN:
        incoming = getattr(source, column)
        if incoming not in (None, "", [], {}) and getattr(target, column) in (None, "", [], {}):
            setattr(target, column, deepcopy(incoming))
            filled.append(column)
    if source.call_remarks and source.call_remarks.strip() and source.call_remarks.strip() != (target.call_remarks or "").strip():
        text_from_source = source.call_remarks.strip()
        target.call_remarks = f"{target.call_remarks.strip()}\n\nFrom duplicate {_short(source)}: {text_from_source}" if (target.call_remarks or "").strip() else text_from_source
        filled.append("call_remarks")
    if filled:
        for name, kept in marks.items():
            if kept:
                target_sources[name] = sorted(kept)
        target.field_sources = target_sources
    claims = {**(source.source_claims or {}), **(target.source_claims or {})}
    if claims != (target.source_claims or {}):
        target.source_claims = claims
    merged = merge_ocr_data(target.ocr_data, source.ocr_data) if source.ocr_data else None
    if merged is not None and merged != (target.ocr_data or {}):
        target.ocr_data = merged
        filled.append("ocr_data")
    return filled


def _move_rows(db: Session, actor: User, source: Candidate, target: Candidate, plan: SimpleNamespace) -> dict:
    mapping = {source.id: target.id, **plan.remap}
    # Documents: a new version on the target, same id, same stored file.
    for document, version in plan.moves:
        document.entity_id, document.version = target.id, version
    db.flush()
    # Reading jobs: the target owns them now; the file they read is the target's copy when the source's was a duplicate.
    jobs = db.scalars(select(DocumentExtractionJob).options(undefer(DocumentExtractionJob.draft))
                      .where(DocumentExtractionJob.candidate_id == source.id).with_for_update()
                      .execution_options(populate_existing=True)).all()
    for job in jobs:
        job.candidate_id = target.id
        if job.attachment_id in plan.remap:
            job.attachment_id = plan.remap[job.attachment_id]
        draft = _remap(job.draft or {}, mapping)
        if draft != (job.draft or {}):
            job.draft = draft
    db.flush()
    # Applications (placements), their stages and stage documents travel with the application id.
    if plan.move_work:
        db.execute(update(Application).where(Application.candidate_id == source.id).values(candidate_id=target.id))
    if plan.tasks:
        db.execute(update(Task).where(Task.id.in_(plan.tasks)).values(candidate_id=target.id))
        db.execute(update(DailyWorkItem).where(DailyWorkItem.task_id.in_(plan.tasks), DailyWorkItem.candidate_id == source.id)
                   .values(candidate_id=target.id))
    if plan.move_work:
        db.execute(update(TaskEvent).where(TaskEvent.candidate_id == source.id).values(candidate_id=target.id))
    db.execute(update(ApplicationFeedback).where(ApplicationFeedback.candidate_id == source.id).values(candidate_id=target.id))
    for model in (MailMessage, WhatsAppMessage, WhatsAppConversation, EmailIngestionJob, DocumentPersonGroup):
        db.execute(update(model).where(model.candidate_id == source.id).values(candidate_id=target.id))
    db.execute(update(Lead).where(Lead.candidate_id == source.id).values(candidate_id=target.id))
    db.execute(update(Lead).where(Lead.matched_candidate_id == source.id).values(matched_candidate_id=target.id))
    # Leave-cover rows: one per request and candidate; where the target already has one the source's goes with the source.
    if plan.move_work:
        db.execute(update(AttendanceCoverItem).where(
            AttendanceCoverItem.candidate_id == source.id,
            AttendanceCoverItem.request_id.not_in(select(AttendanceCoverItem.request_id).where(AttendanceCoverItem.candidate_id == target.id)))
            .values(candidate_id=target.id))
    # Invoices that are still drafts (an issued or paid one refused the merge): move where the target has none for that placement.
    if plan.move_work:
        db.execute(update(CandidateInvoice).where(
            CandidateInvoice.candidate_id == source.id,
            CandidateInvoice.scope_key.not_in(select(CandidateInvoice.scope_key).where(CandidateInvoice.candidate_id == target.id)))
            .values(candidate_id=target.id))
    # Links in notices and to-dos: /candidates/<source>[/...] now opens the target.
    old, new = f"/candidates/{source.id}", f"/candidates/{target.id}"
    for model in (Notification, TodoItem):
        column = model.link_to
        db.execute(update(model).where(or_(column == old, column.startswith(old + "/", autoescape=True)))
                   .values(link_to=func.concat(new, func.substr(column, len(old) + 1))))
    # The audit trail follows (attachment ids that were skipped now name the target's copy).
    # (The bulk-import entries stay with the removed record: they decide how the CRM treats a candidate on delete, and the target was not imported.)
    db.execute(update(AuditEntry).where(AuditEntry.entity_type == "candidate", AuditEntry.entity_id == source.id,
                                        ~AuditEntry.action.like("Imported candidate with documents%"))
               .values(entity_id=target.id))
    if plan.remap:
        for old_id, new_id in plan.remap.items():
            for entry in db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "candidate", AuditEntry.entity_id == target.id,
                                                             cast(AuditEntry.meta, Text).contains(old_id))):
                entry.meta = _remap(entry.meta, {old_id: new_id})
    db.flush()
    return mapping


def _already_merged(db: Session, source_id: str, target_id: str) -> dict | None:
    """The tombstone left by an earlier successful merge of this pair (the retry of a request that already worked)."""
    entry = db.scalar(select(AuditEntry).where(
        AuditEntry.action == TOMBSTONE_ACTION, AuditEntry.entity_type == "candidate", AuditEntry.entity_id == source_id,
        AuditEntry.meta["mergedIntoId"].astext == target_id).order_by(AuditEntry.at.desc()).limit(1))
    return entry.meta if entry is not None else None


def _recheck_documents(db: Session, actor: User, target_id: str, attachment_ids: list[str]) -> dict:
    """Judge the moved documents that an identity check had stopped against the target. Own transaction: a problem here never
    undoes the merge. Nothing is sent to the reader again unless a saved reading is incomplete (then the file is queued)."""
    outcome = {"accepted": 0, "stillNeedReview": 0, "requeued": 0}
    if not attachment_ids:
        return outcome
    from app.services.application_form import reconsider_identity_rejection
    from app.services.document_identity import rejected_expression
    try:
        waiting = [attachment_id for attachment_id, stopped in db.execute(
            select(Attachment.id, rejected_expression()).where(Attachment.id.in_(attachment_ids))) if stopped]
        for attachment_id in waiting:
            try:
                with db.begin_nested():
                    result = reconsider_identity_rejection(db, actor, target_id, attachment_id)
            except HTTPException:
                continue   # no longer waiting for an identity check
            if result.get("requeued"):
                outcome["requeued"] += 1
            elif result.get("resolved"):
                outcome["accepted"] += 1
            else:
                outcome["stillNeedReview"] += 1
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not judge the documents of merged candidate %s again", target_id)
        db.rollback()
        outcome["error"] = True
    return outcome


# ------------------------------------------------------------------------------------------------ entry point

def merge_candidate(db: Session, actor: User, source_id: str, target_id: str, *, confirm_identity: bool = False,
                    dry_run: bool = False, background: BackgroundTasks | None = None) -> dict:
    """Merge candidate `source_id` (the duplicate) into `target_id` (the existing one). Admin only.

    `dry_run=True` reads and reports (what moves, what is skipped, what would refuse) and changes nothing. Otherwise the merge is one
    transaction (move, then the CRM's own delete of the source); a refusal raises HTTPException(409) with nothing changed. A repeat of a
    merge that already worked answers `alreadyMerged`. `background` receives the removal of the source's leftover stored files (run after
    the response); without one they are removed here, after the commit.
    """
    if not is_admin_level(actor):
        raise HTTPException(403, "Your role does not allow this action")
    if source_id == target_id:
        refusals = [_refusal("same_candidate", "Choose two different candidates: a candidate cannot be merged into itself.")]
        if dry_run:
            return {"dryRun": True, "allowed": False, "refusals": refusals, "moved": {}, "skipped": []}
        raise HTTPException(409, refusals[0]["message"])

    if dry_run:
        pair = _load_pair(db, source_id, target_id, lock=False)
        source, target = pair.get(source_id), pair.get(target_id)
        if target is None:
            raise HTTPException(404, "The candidate to merge into was not found.")
        if source is None:
            done = _already_merged(db, source_id, target_id)
            if done:
                return {"dryRun": True, "allowed": False, "alreadyMerged": True, "mergedInto": done.get("mergedInto"), "moved": {}, "skipped": [],
                        "refusals": [], "removedSource": True}
            raise HTTPException(404, "The duplicate candidate was not found.")
        plan = _plan(db, source, target, confirm_identity)
        summary = _summary(source, target, plan)
        db.rollback()   # a read: nothing to keep, and no lock is held afterwards
        return {"dryRun": True, "allowed": not plan.refusals, **summary}

    from app.api.routers import candidates as candidate_routes
    candidate_routes.lock_assignment_cursor(db)          # same first lock as every create, save and delete
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CARE_ID_LOCK_KEY})
    pair = _load_pair(db, source_id, target_id, lock=True)
    source, target = pair.get(source_id), pair.get(target_id)
    if target is None:
        db.rollback()
        raise HTTPException(404, "The candidate to merge into was not found.")
    if source is None:
        done = _already_merged(db, source_id, target_id)
        db.rollback()
        if done:
            return {"mergedInto": done.get("mergedInto"), "moved": {"documents": 0, "jobs": 0, "notes": 0, "tasks": 0}, "skipped": [],
                    "removedSource": True, "alreadyMerged": True}
        raise HTTPException(404, "The duplicate candidate was not found.")

    plan = _plan(db, source, target, confirm_identity)
    if plan.refusals:
        db.rollback()
        raise HTTPException(409, " ".join(refusal["message"] for refusal in plan.refusals))

    source_name, source_label, target_label = source.name, _label(source), _label(target)
    source_snapshot = {"id": source.id, "ref": source.ref, "careId": source.care_id, "name": source.name}
    summary = _summary(source, target, plan)
    moved_attachment_ids = [document.id for document, _ in plan.moves] + list(plan.remap.values())
    try:
        _move_rows(db, actor, source, target, plan)
        filled = _fill_empty(source, target)
        db.flush()
        from app.services.application_form import rename_imported_files
        renamed = rename_imported_files(db, target, source_name, target.name)
        if source.care_id:
            # A duplicate whose name was never read carries its files under the care-id stem ("CI_167-Passport.pdf").
            renamed += rename_imported_files(db, target, source.care_id, target.name)
        db.add(AuditEntry(actor_id=actor.id, action=f"Merged duplicate {_short(source)} into this candidate"[:200], target=target.name,
                          entity_type="candidate", entity_id=target.id,
                          meta={"mergedFrom": source_snapshot, "moved": summary["moved"], "skipped": plan.skipped, "filledFields": filled,
                                "renamedFiles": renamed, "identityEvidence": plan.evidence, "mergedBy": actor.name}))
        db.add(AuditEntry(actor_id=actor.id, action=TOMBSTONE_ACTION, target=f"{source_name} ({_short(source)})", entity_type="candidate",
                          entity_id=source.id, meta={"mergedIntoId": target.id, "mergedInto": summary["mergedInto"], "mergedFrom": source_snapshot,
                                                      "moved": summary["moved"], "mergedBy": actor.name}))
        db.flush()
        # The CRM's own delete, in this same transaction: its cascades, audit entry and change notices, and it commits.
        # Everything that moved no longer points at the source, so only what is left on it goes (and its stored files).
        tasks = background if background is not None else BackgroundTasks()
        candidate_routes.delete_candidate(candidate_id=source.id, background=tasks, withdraw_from_orders=plan.withdraw, db=db, user=actor)
    except Exception:
        db.rollback()   # all or nothing: the merge is one transaction
        raise
    if background is None:
        for task in tasks.tasks:
            try:
                task.func(*task.args, **task.kwargs)
            except Exception:  # noqa: BLE001
                logger.exception("Could not remove the leftover stored files of merged candidate %s", source_id)

    summary["identityCheck"] = _recheck_documents(db, actor, target_id, moved_attachment_ids)
    summary.pop("refusals", None)
    summary.pop("evidence", None)
    summary.pop("source", None)
    summary.update({"removedSource": True, "removedLabel": source_label, "mergedIntoLabel": target_label})
    return summary
