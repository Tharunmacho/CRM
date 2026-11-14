"""Lead sheet bulk actions: assign, change status, mark not interested, export selected, delete.

Contract: docs/workflows/lead-sheet-contract.md ("Bulk actions and delete"). Every write is set-based (a handful of
statements whatever the size: resolve + lock, one activity insert, one update per owner) and every write statement starts
with INSERT / UPDATE / DELETE so the live change feed (services/change_feed.py) announces the `leads` topic.

The selection is either `ids` or a `filter` (the GET /api/leads query parameters, snake_case) which is resolved with the very
same `lead_sheet.apply_filters` as the list, so "select all N matching" acts on exactly the rows the list shows.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import ARRAY, String, any_, literal, select, text
from sqlalchemy.orm import Session, undefer

from app.core.config import get_settings
from app.db.models import AuditEntry, Lead, LEAD_STATUSES, Notification, Role, User
from app.services import lead_followups, lead_sheet
from app.services.lead_sheet import STATUS_LABELS, fail

log = logging.getLogger("uvicorn.error")

BULK_LIMIT = 10_000          # leads per call, hard cap
TYPED_CONFIRM_OVER = 20      # deleting more than this many leads needs the word DELETE
ACTIONS = ("assign", "status", "not_interested", "export", "delete")
FILTER_KEYS = ("q", "status", "campaign", "form", "position", "country", "platform", "owner", "created_from", "created_to",
               "has_resume", "matched_candidate", "follow_up", "batch_id")
EXPORT_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

REASONS = {
    "converted": "converted or linked to a candidate",
    "already_set": "already had that status",
    "already_owner": "already assigned to that person",
    "already_unassigned": "already not assigned to anyone",
    "not_yours": "not assigned to you",
    "gone": "not found (deleted meanwhile)",
    "changed": "changed while the action ran",
}


def clean_filter(raw: dict | None) -> dict:
    """The body's `filter` as the dict `apply_filters` reads (same keys and meaning as the list's query parameters)."""
    raw = raw or {}
    unknown = sorted(set(raw) - set(FILTER_KEYS))
    if unknown:
        raise fail(422, "invalid_request", f"Unknown filter: {', '.join(unknown)}.")
    out: dict = {}
    for key in FILTER_KEYS:
        value = raw.get(key)
        if key in ("has_resume", "matched_candidate") and isinstance(value, str):
            value = {"true": True, "false": False}.get(value.lower())
        out[key] = value
    out["q"] = str(out["q"] or "")[:500]
    return out


def _order_resolving():
    return [Lead.created_time.desc().nulls_last(), Lead.id]


def resolve(db: Session, user: User, ids: list[str] | None, filt: dict | None, *, lock: bool) -> tuple[list[tuple], bool]:
    """The selected leads as (id, status, owner_id, candidate_id), newest first, and whether they came from a filter.

    `lock=True` takes row locks (FOR UPDATE) so the classification below is the truth until the transaction ends: a lead
    that changed concurrently is read after the other writer committed, never half-way.
    """
    if ids:
        ids = list(dict.fromkeys(ids))
        if len(ids) > BULK_LIMIT:
            raise too_many(len(ids))
        statement = (select(Lead.id, Lead.status, Lead.owner_id, Lead.candidate_id)
                     .where(Lead.id == any_(literal(ids, ARRAY(String))), lead_sheet.visible_leads(user)).order_by(*_order_resolving()))
        if lock:
            statement = statement.with_for_update()
        return [tuple(r) for r in db.execute(statement).all()], False
    if filt is None:
        raise fail(422, "invalid_request", "Choose the leads (ids) or give a filter.")
    filt = clean_filter(filt)
    if not lead_sheet.sees_all_leads(user):
        filt["owner"] = "me"  # a manager's or recruiter's selection never reaches beyond their own leads
    base, owner, has_resume = lead_sheet._base_query()
    statement = (lead_sheet.apply_filters(base, owner, has_resume, user, filt)
                 .with_only_columns(Lead.id, Lead.status, Lead.owner_id, Lead.candidate_id)
                 .order_by(*_order_resolving()).limit(BULK_LIMIT + 1))
    if lock:
        statement = statement.with_for_update(of=Lead)
    rows = [tuple(r) for r in db.execute(statement).all()]
    if len(rows) > BULK_LIMIT:
        raise too_many(None)
    return rows, True


def too_many(found: int | None) -> HTTPException:
    size = f"{found:,} leads" if found else f"more than {BULK_LIMIT:,} leads"
    return fail(422, "too_many_leads", f"That selection is {size}. Bulk actions handle up to {BULK_LIMIT:,} leads at a time: narrow the filters or do it in parts.",
                limit=BULK_LIMIT)


def is_linked(row: tuple) -> bool:
    return row[1] == "converted" or row[3] is not None


def reasons_out(counts: dict[str, int]) -> list[dict]:
    return [{"code": code, "count": n, "message": REASONS[code]} for code, n in counts.items() if n]


def _plural(n: int) -> str:
    return "lead" if n == 1 else "leads"


def _summary_audit(db: Session, user: User, action: str, count: int, meta: dict) -> None:
    db.add(AuditEntry(actor_id=user.id, action=action, target=f"{count} {_plural(count)}", entity_type="lead_bulk", entity_id=None, meta=meta))


# ------------------------------------------------------------------ entry point

def run(db: Session, user: User, body: dict) -> dict | tuple[bytes, int]:
    """Apply `body` (action, ids | filter, ownerId, status, confirm, expectedCount, sort, direction).

    Returns the result dict, or (xlsx bytes, lead count) for `export`. The caller commits.
    """
    action = body["action"]
    supervisor = user.role in (Role.admin, Role.manager)
    if action == "delete" and user.role != Role.admin:
        raise fail(403, "forbidden_role", "Only an admin can delete leads.")
    if action == "assign" and user.role != Role.admin:
        raise fail(403, "forbidden_role", "Only the admin can assign leads.")
    if action == "export" and not supervisor:
        raise fail(403, "forbidden_role", "Only a manager or an admin can export leads.")

    target_status = "not_interested" if action == "not_interested" else body.get("status")
    owner_id = body.get("ownerId")
    if action == "status":
        if target_status not in LEAD_STATUSES:
            raise fail(422, "invalid_request", f"status must be one of: {', '.join(LEAD_STATUSES)}.")
        if target_status == "converted":
            raise fail(422, "invalid_request", "Use Convert to turn a lead into a candidate.")
    if action == "assign" and not owner_id:
        raise fail(422, "invalid_request", "Choose who the leads go to (ownerId, or auto).")

    if action == "export":
        return export_selected(db, user, body)

    rows, from_filter = resolve(db, user, body.get("ids"), body.get("filter"), lock=True)
    expected = body.get("expectedCount")
    if from_filter and expected is not None and expected != len(rows):
        raise fail(409, "selection_changed", f"The matching leads changed while you were confirming: {len(rows):,} now match, not {expected:,}. Review the list and try again.",
                   matching=len(rows))
    if not rows:
        raise fail(422, "invalid_request", "No leads match that selection.")
    if action == "delete" and len(rows) > TYPED_CONFIRM_OVER and (body.get("confirm") or "").strip() != "DELETE":
        raise fail(422, "confirmation_required", f"Deleting {len(rows):,} leads needs the word DELETE typed to confirm.", count=len(rows))

    requested = len(set(body["ids"])) if body.get("ids") else len(rows)
    missing = max(0, requested - len(rows)) if body.get("ids") else 0
    # Managers and staff never see leads that are not theirs: a selected id that did not resolve is "not yours" to them.
    skipped = {"gone": missing} if lead_sheet.sees_all_leads(user) else {"not_yours": missing}
    if action in ("status", "not_interested"):
        result = _change_status(db, user, rows, target_status, skipped)
    elif action == "assign":
        result = _assign(db, user, rows, owner_id, body.get("onlyUnassigned", False), skipped)
    else:
        result = _delete(db, user, rows, skipped, bulk=True)
    result.update(action=action, requested=requested, skipped=sum(skipped.values()), skippedReasons=reasons_out(skipped))
    return result


# ------------------------------------------------------------------ status

def _change_status(db: Session, user: User, rows: list[tuple], target: str, skipped: dict) -> dict:
    todo: list[tuple[str, str]] = []
    for row in rows:
        lead_id, status, owner, _ = row
        if not lead_sheet.sees_all_leads(user) and owner != user.id:
            skipped["not_yours"] = skipped.get("not_yours", 0) + 1
        elif is_linked(row):
            skipped["converted"] = skipped.get("converted", 0) + 1
        elif status == target:
            skipped["already_set"] = skipped.get("already_set", 0) + 1
        else:
            todo.append((lead_id, status))
    if todo:
        ids, olds = [t[0] for t in todo], [t[1] for t in todo]
        db.execute(text(
            "INSERT INTO crm_lead_activities (id, lead_id, user_id, kind, summary, meta) "
            "SELECT gen_random_uuid()::text, x.id, :u, 'status', "
            "'Status changed from ' || coalesce(CAST(:labels AS jsonb) ->> x.old, x.old) || ' to ' || CAST(:to_label AS text), "
            "jsonb_build_object('from', x.old, 'to', CAST(:to AS text), 'bulk', true) "
            "FROM unnest(CAST(:ids AS text[]), CAST(:olds AS text[])) AS x(id, old)"),
            {"u": user.id, "labels": _labels_json(), "to_label": STATUS_LABELS[target], "to": target, "ids": ids, "olds": olds})
        db.execute(text("UPDATE crm_leads SET status = :s, updated_at = now() WHERE id = ANY(CAST(:ids AS text[]))"), {"s": target, "ids": ids})
        lead_followups.sync(db, ids)  # a lead that is closed no longer has a follow-up to-do; a reopened one gets it back
        _summary_audit(db, user, "Bulk status change", len(ids), {"to": target, "count": len(ids)})
    return {"updated": len(todo), "status": target}


def _labels_json() -> str:
    import json
    return json.dumps(STATUS_LABELS)


# ------------------------------------------------------------------ assign

def _assign(db: Session, user: User, rows: list[tuple], owner_id: str, only_unassigned: bool, skipped: dict) -> dict:
    """Assign, reassign or (ownerId "none") take the selected leads back from their recruiter."""
    from app.services.lead_import import eligible_staff, open_lead_counts, spread_evenly
    unassign = owner_id in lead_sheet.UNASSIGN
    candidates: list[tuple[str, str | None]] = []
    for row in rows:
        if is_linked(row):
            skipped["converted"] = skipped.get("converted", 0) + 1
        elif only_unassigned and row[2] is not None:
            skipped["already_owner"] = skipped.get("already_owner", 0) + 1
        elif unassign and row[2] is None:
            skipped["already_unassigned"] = skipped.get("already_unassigned", 0) + 1
        else:
            candidates.append((row[0], row[2]))
    if unassign:
        names, picks = {}, [None] * len(candidates)
    elif owner_id == "auto":
        staff = eligible_staff(db)
        if not staff:
            raise fail(422, "invalid_request", "No staff member is available for automatic assignment.")
        names = {u.id: u.name for u in staff}
        picks = spread_evenly(staff, open_lead_counts(db, [u.id for u in staff]), len(candidates))
    else:
        from app.api.routers.candidates import ensure_assignable
        try:
            person = ensure_assignable(db, owner_id)
        except HTTPException as exc:
            raise fail(422, "invalid_request", str(exc.detail)) from None
        names, picks = {person.id: person.name}, [person.id] * len(candidates)
    move: dict[str | None, list[str]] = {}
    for (lead_id, current), new_owner in zip(candidates, picks):
        if current == new_owner:
            skipped["already_owner"] = skipped.get("already_owner", 0) + 1
        else:
            move.setdefault(new_owner, []).append(lead_id)
    lead_sheet.apply_owner_changes(db, user, move, names, bulk=True)  # follow-up to-dos move to the new owner (or go when nobody owns the lead)
    freed = len(move.get(None, []))
    updated = sum(len(ids) for ids in move.values())
    if freed:
        _summary_audit(db, user, "Bulk lead unassign", freed, {"count": freed})
    if updated - freed:
        _summary_audit(db, user, "Bulk lead assignment", updated - freed, {"owners": {names[o]: len(ids) for o, ids in move.items() if o is not None}})
    return {"updated": updated, "assigned": updated - freed, "unassigned": freed,
            "owners": [{"id": o, "name": names[o], "count": len(ids)} for o, ids in move.items() if o is not None]}


# ------------------------------------------------------------------ delete

def linked_refusal(db: Session, lead_id: str) -> HTTPException:
    ref = db.scalar(text("SELECT c.ref FROM crm_leads l JOIN crm_candidates c ON c.id = l.candidate_id WHERE l.id = :i"), {"i": lead_id})
    where = f" ({ref})" if ref else ""
    return fail(409, "lead_linked", f"This lead was converted to a candidate{where}, so it cannot be deleted. Manage the person from the Data Bank instead.")


def delete_one(db: Session, user: User, lead_id: str) -> dict:
    """DELETE /api/leads/{id}: refuses a converted / linked lead; otherwise the same set-based path as the bulk delete."""
    row = db.execute(select(Lead.id, Lead.status, Lead.owner_id, Lead.candidate_id).where(Lead.id == lead_id).with_for_update()).first()
    if row is None:
        raise fail(404, "not_found", "This lead was not found.")
    if is_linked(tuple(row)):
        raise linked_refusal(db, lead_id)
    result = _delete(db, user, [tuple(row)], {}, bulk=False)
    return {"deleted": result["updated"], "id": lead_id}


def _delete(db: Session, user: User, rows: list[tuple], skipped: dict, *, bulk: bool) -> dict:
    doomed: list[str] = []
    for row in rows:
        if is_linked(row):
            skipped["converted"] = skipped.get("converted", 0) + 1
        else:
            doomed.append(row[0])
    if not doomed:
        return {"updated": 0, "deleted": 0}
    ids = {"ids": doomed}
    db.execute(text(
        "INSERT INTO system_audit_log (id, actor_id, action, target, entity_type, entity_id, meta) "
        "SELECT gen_random_uuid()::text, :u, 'Deleted lead', left(coalesce(name, phone, email, 'Lead'), 300), 'lead', id, "
        "jsonb_build_object('externalId', external_id, 'status', status, 'campaign', campaign_name, 'bulk', CAST(:bulk AS boolean)) "
        "FROM crm_leads WHERE id = ANY(CAST(:ids AS text[]))"), {"u": user.id, "bulk": bulk, **ids})
    keys = [r[0] for r in db.execute(text(
        "DELETE FROM document_attachments a WHERE a.entity_type = 'lead' AND a.entity_id = ANY(CAST(:ids AS text[])) "
        "AND NOT EXISTS (SELECT 1 FROM document_extraction_jobs j WHERE j.attachment_id = a.id) RETURNING a.object_key"), ids).all()]
    gone = db.execute(text("DELETE FROM crm_leads WHERE id = ANY(CAST(:ids AS text[])) RETURNING id"), ids).all()  # activities cascade
    lead_followups.sync(db, doomed)  # the deleted leads' follow-up to-dos go with them
    if bulk:
        _summary_audit(db, user, "Bulk lead delete", len(gone), {"count": len(gone)})
    db.info.setdefault("lead_files_to_remove", set()).update(keys)
    return {"updated": len(gone), "deleted": len(gone)}


def remove_stored_files(db: Session) -> None:
    """After the commit: drop the bytes of deleted leads' resumes that no remaining attachment still points at. Best effort."""
    keys = db.info.pop("lead_files_to_remove", None)
    if not keys:
        return
    from app.services import document_storage
    settings = get_settings()
    shared = {r[0] for r in db.execute(text("SELECT DISTINCT object_key FROM document_attachments WHERE object_key = ANY(CAST(:k AS text[]))"), {"k": sorted(keys)})}
    for key in keys - shared:
        try:
            document_storage.delete_bytes(settings, key)
        except Exception:  # a missing file or an unreachable bucket must not turn a finished delete into an error
            log.warning("Could not remove a deleted lead's stored file.")


# ------------------------------------------------------------------ export

def export_selected(db: Session, user: User, body: dict) -> tuple[bytes, int]:
    """The selected leads as the styled xlsx of the Lead sheet export (core columns + every custom question answered)."""
    from app.services import lead_xlsx
    sort, direction = body.get("sort") or "created", body.get("direction") or "desc"
    query, owner, has_resume = lead_sheet._base_query()
    if body.get("ids"):
        ids = list(dict.fromkeys(body["ids"]))
        if len(ids) > BULK_LIMIT:
            raise too_many(len(ids))
        # ids that are not the caller's own never resolve (a manager's export holds only their leads; X-Lead-Count says how many)
        query = query.where(Lead.id == any_(literal(ids, ARRAY(String))), lead_sheet.visible_leads(user))
    elif body.get("filter") is not None:
        filt = clean_filter(body["filter"])
        if user.role == Role.staff:
            filt["owner"] = "me"
        query = lead_sheet.apply_filters(query, owner, has_resume, user, filt)
    else:
        raise fail(422, "invalid_request", "Choose the leads (ids) or give a filter.")
    found = db.execute(query.options(undefer(Lead.extra)).order_by(*lead_sheet._order(owner, sort, direction)).limit(BULK_LIMIT + 1)).all()
    if len(found) > BULK_LIMIT:
        raise too_many(None)
    if not found:
        raise fail(422, "invalid_request", "No leads match that selection.")
    expected = body.get("expectedCount")
    if not body.get("ids") and expected is not None and expected != len(found):
        raise fail(409, "selection_changed", f"The matching leads changed: {len(found):,} now match, not {expected:,}. Review the list and try again.", matching=len(found))
    header, rows = lead_sheet.export_table(found)
    sheet = [header] + [[(float(v) if isinstance(v, Decimal) else v) for v in row] for row in rows]
    return lead_xlsx.build_workbook([{"name": "Lead sheet", "rows": sheet, "widths": lead_xlsx.fit_widths(sheet)}]), len(found)
