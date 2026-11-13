"""Lead sheet workflow: list/search/summary, the daily-work gate, call logging, assignment, conversion, export.

The import itself lives in lead_import.py and the file parsing in lead_parsing.py. Rules and JSON shapes are in
docs/workflows/lead-sheet-contract.md.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, UploadFile
from sqlalchemy import and_, case, cast, exists, func, or_, select, text, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased, undefer

from app.db.models import (Attachment, AuditEntry, Candidate, Lead, LeadActivity, LeadBatch, Notification, Role, User,
                           LEAD_STATUSES)
from app.services import lead_followups, lead_matching, lead_parsing as lp
from app.services.candidate_search import agency_midnight, literal_contains, search_terms

STATUS_LABELS = {
    "new": "New", "contacted": "Contacted", "interested": "Interested", "resume_requested": "Resume requested",
    "resume_received": "Resume received", "converted": "Converted", "not_interested": "Not interested",
    "invalid": "Invalid", "wrong_number": "Wrong number",
}
CLOSED = ("converted", "not_interested", "invalid", "wrong_number")
OUTCOMES = ("answered", "interested", "not_interested", "no_answer", "wrong_number")
PLATFORM_LABELS = {"ig": "Instagram", "fb": "Facebook"}
EXPORT_LIMIT = 50_000
SORTS = ("created", "name", "status", "owner", "campaign", "position", "follow_up", "last_contacted", "calls", "experience")


def fail(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **extra})


def iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


# ------------------------------------------------------------------ the daily-work gate

def gate_message(open_: int, postponed: int) -> str:
    left = f"{open_} daily {'task' if open_ == 1 else 'tasks'} left"
    if postponed:
        left += f" ({postponed} postponed)"
    return f"Finish today's tasks first. You have {left}. Then you can work the lead sheet."


def daily_work_gate(db: Session, user: User, now: datetime | None = None) -> dict:
    """Is this person locked out of the lead sheet by unfinished daily work? Only staff are ever locked.

    Reads workflow_daily_work_items (migrations 0020/0023): open items allocated today or earlier (postponed ones are
    the earlier days). Nothing allocated means unlocked. One statement; never cached.
    """
    unlocked = {"locked": False, "open": 0, "total": 0, "postponed": 0, "nextDueAt": None, "message": None}
    if user.role != Role.staff:
        return unlocked
    from app.services.daily_work import agency_today
    today = agency_today(now or datetime.now(timezone.utc))
    row = db.execute(text(
        "SELECT count(*) FILTER (WHERE status = 'open'), count(*) FILTER (WHERE status = 'open' AND work_date < :today), "
        "count(*) FILTER (WHERE status = 'open' OR (status = 'completed' AND work_date = :today)), "
        "min(due_at) FILTER (WHERE status = 'open') FROM workflow_daily_work_items "
        "WHERE owner_id = :u AND work_date <= :today AND status IN ('open', 'completed')"), {"u": user.id, "today": today}).one()
    open_, postponed, total, due = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), row[3]
    if not open_:
        return {**unlocked, "total": total}
    return {"locked": True, "open": open_, "total": total, "postponed": postponed,
            "nextDueAt": iso(due), "message": gate_message(open_, postponed)}


def lead_work_refusal(db: Session, lead: Lead, user: User, gate: dict | None = None) -> HTTPException | None:
    """Why `user` may not work this lead (call, edit, convert, link, upload a resume), or None.

    Admin and manager work any lead and are never gated. Staff work only the leads assigned to them, and only once their
    daily tasks are finished.
    """
    if user.role in (Role.admin, Role.manager):
        return None
    if lead.owner_id != user.id:
        if lead.owner_id is None:
            return fail(403, "lead_not_yours", "This lead has no owner yet. Only a manager or an admin can assign it.")
        name = db.scalar(select(User.name).where(User.id == lead.owner_id))
        return fail(403, "lead_not_yours", f"This lead is assigned to {name or 'another recruiter'}. Only they, a manager or an admin can work on it.")
    gate = gate or daily_work_gate(db, user)
    if gate["locked"]:
        return fail(403, "daily_work_pending", gate["message"], dailyWorkGate=gate)
    return None


def ensure_can_work_lead(db: Session, lead: Lead, user: User) -> None:
    refusal = lead_work_refusal(db, lead, user)
    if refusal:
        raise refusal


# ------------------------------------------------------------------ activities and output

def log_activity(db: Session, lead_id: str, user_id: str | None, kind: str, summary: str, meta: dict | None = None) -> None:
    db.add(LeadActivity(id=str(uuid.uuid4()), lead_id=lead_id, user_id=user_id, kind=kind, summary=summary[:2000], meta=meta))


def source_question(header: str, raw: dict | None = None) -> str:
    companion = lp.EXPERIENCE_QUESTION_COMPANIONS.get(lp.header_key(header))
    supplied = [value for key, value in (raw or {}).items() if lp.header_key(key) == companion and isinstance(value, str) and value.strip()] if companion else []
    return supplied[0] if len(supplied) == 1 else header


def readable_question(header: str) -> str | None:
    words = " ".join(lp.clean_cell(header).replace("_", " ").split())
    return words[:1].upper() + words[1:] if words else None


def experience_question(column_map: dict | None, field: str, *, answer=None, raw=None) -> str | None:
    """The exact mapped sheet question, made readable without guessing its meaning.

    Use the persisted import mapping, including explicit overrides. Never infer a
    question from a numeric value or from another answer in raw/extra. Older leads
    without mapping metadata simply have no source question.
    """
    if not isinstance(column_map, dict):
        return None
    headers = [header for header, mapped in column_map.items() if mapped == field and isinstance(header, str) and header.strip()]
    if len(headers) != 1:
        return None
    if answer is not None:
        # fill_empty may have copied an answer from a later sheet without moving
        # the lead's original batch/raw. Do not label it with an unrelated older
        # question when its source can no longer be established.
        original = raw.get(headers[0]) if isinstance(raw, dict) else None
        if original is None or lp.clean_cell(original)[:200] != answer:
            return None
    return readable_question(source_question(headers[0], raw))


def geographic_experience(source: dict, scope: str) -> dict:
    """One explicit source question or an unknown value, without inferred totals."""
    raw = source.get("raw") if isinstance(source.get("raw"), dict) else {}
    headers = [header for header in raw if lp.geographic_experience_scope(header) in (scope, "mixed")]
    # An exported answer can coexist with its original custom-question column.
    # Collapse only an explicitly identified, agreeing copy; never pick between
    # different questions or contradictory answers.
    canonical = [header for header in headers if lp.header_key(header) == "experience" + scope]
    if len(canonical) == 1:
        primary = canonical[0]
        question = source_question(primary, raw)
        if question != primary and lp.geographic_experience_scope(question) == scope:
            def agrees(other):
                first, second = lp.clean_cell(raw[primary]), lp.clean_cell(raw[other])
                return first == second or (re.fullmatch(r"\d+(?:[.,]\d+)?", first) and re.fullmatch(r"\d+(?:[.,]\d+)?", second)
                                          and Decimal(first.replace(",", ".")) == Decimal(second.replace(",", ".")))
            headers = [header for header in headers if header == primary or lp.header_key(header) != lp.header_key(question) or not agrees(header)]
    result = {"Years": None, "Text": None, "Question": None}
    if len(headers) != 1 or lp.geographic_experience_scope(headers[0]) != scope:
        return result
    header = headers[0]
    years, answer = lp.parse_experience(raw[header])
    if lp.header_key(answer or "") in {"na", "notapplicable", "notavailable", "unknown", "notknown", "notsupplied", "notprovided"}:
        years = None
    result.update(Years=years, Text=answer, Question=readable_question(source_question(header, raw)))
    return result


def lead_dict(lead: Lead, *, owner_name=None, candidate_ref=None, matched_ref=None, matched_name=None, has_resume=False,
              experience_source=None) -> dict:
    source = experience_source if isinstance(experience_source, dict) else {}
    geography = {f"experience{label}{suffix}": value
                 for label, scope in (("India", "india"), ("Abroad", "abroad"))
                 for suffix, value in geographic_experience(source, scope).items()}
    for label, years, written in (("India", lead.experience_india_years, lead.experience_india_text),
                                  ("Abroad", lead.experience_abroad_years, lead.experience_abroad_text)):
        if years is not None or written is not None:  # a recruiter's edit wins over the sheet's answer ('' = cleared)
            geography.update({f"experience{label}Years": iso(years), f"experience{label}Text": written or None, f"experience{label}Question": None})
    return {
        "id": lead.id, "externalId": lead.external_id, "batchId": lead.batch_id, "importRow": lead.import_row,
        "createdTime": iso(lead.created_time), "name": lead.name, "phone": lead.phone, "phoneCountryCode": lead.phone_country_code,
        "email": lead.email, "whatsapp": lead.whatsapp, "whatsappSame": lead.whatsapp_same, "city": lead.city, "state": lead.state,
        "currentLocation": lead.current_location, "positionApplied": lead.position_applied, "countryInterest": lead.country_interest,
        "experienceRestaurantYears": iso(lead.experience_restaurant_years), "experienceRestaurantText": lead.experience_restaurant_text,
        "experienceTotalYears": iso(lead.experience_total_years), "experienceTotalText": lead.experience_total_text,
        "experienceRestaurantQuestion": experience_question(source.get("mapping"), "experienceRestaurant",
                                                              answer=lead.experience_restaurant_text, raw=source.get("raw")),
        "experienceTotalQuestion": experience_question(source.get("mapping"), "experienceTotal",
                                                         answer=lead.experience_total_text, raw=source.get("raw")),
        **geography,
        "gender": lead.gender, "maritalStatus": lead.marital_status, "dob": iso(lead.dob), "educationLevel": lead.education_level,
        "militaryStatus": lead.military_status, "platform": lead.platform, "isOrganic": lead.is_organic,
        "leadStatusSource": lead.lead_status_source, "campaignId": lead.campaign_id, "campaignName": lead.campaign_name,
        "adsetId": lead.adset_id, "adsetName": lead.adset_name, "adId": lead.ad_id, "adName": lead.ad_name,
        "formId": lead.form_id, "formName": lead.form_name,
        "status": lead.status, "ownerId": lead.owner_id, "ownerName": owner_name, "assignedAt": iso(lead.assigned_at),
        "lastContactedAt": iso(lead.last_contacted_at), "nextFollowUpAt": iso(lead.next_follow_up_at), "callCount": lead.call_count,
        "notes": lead.notes, "candidateId": lead.candidate_id, "candidateRef": candidate_ref,
        "matchedCandidateId": lead.matched_candidate_id, "matchedCandidateRef": matched_ref, "matchedCandidateName": matched_name,
        "hasResume": bool(has_resume), "resumeReceivedAt": iso(lead.resume_received_at),
        "createdAt": iso(lead.created_at), "updatedAt": iso(lead.updated_at),
    }


def _base_query():
    cand, matched, owner = aliased(Candidate), aliased(Candidate), aliased(User)
    has_resume = exists().where(Attachment.entity_type == "lead", Attachment.entity_id == Lead.id, Attachment.slot == "resume")
    mapping = case((func.jsonb_typeof(LeadBatch.column_map) == "object", LeadBatch.column_map), else_=cast({}, JSONB))
    raw = case((func.jsonb_typeof(Lead.raw) == "object", Lead.raw), else_=cast({}, JSONB))
    extra = case((func.jsonb_typeof(Lead.extra) == "object", Lead.extra), else_=cast({}, JSONB))
    # Mapping keys retain blank questions; raw/extra also support old unbatched
    # leads. Raw wins when both contain the same original source header.
    keys = func.jsonb_each_text(mapping.op("||")(extra).op("||")(raw)).table_valued("key", "value")
    possible_geography = and_(keys.c.key.op("~*")("experience|exp|work|employed"),
                              keys.c.key.op("~*")("india|abroad|overseas|gulf|foreign"))
    question_companion = func.regexp_replace(func.lower(keys.c.key), "[^a-z0-9]", "", "g").in_(tuple(lp.EXPERIENCE_QUESTION_COMPANIONS.values()))
    # raw/extra remain deferred. Return only mapped or geographic experience
    # cells in this query, then apply strict header classification in Python.
    experience_source = (select(func.jsonb_build_object(
        "mapping", func.jsonb_object_agg(keys.c.key, mapping[keys.c.key]),
        "raw", func.jsonb_object_agg(keys.c.key, func.coalesce(raw[keys.c.key], extra[keys.c.key]))))
        .select_from(keys).where(or_(mapping[keys.c.key].astext.in_(("experienceRestaurant", "experienceTotal")), possible_geography, question_companion))
        .correlate(Lead, LeadBatch).scalar_subquery())
    query = (select(Lead, owner.name, cand.ref, matched.ref, matched.name, has_resume.label("has_resume"),
                    experience_source.label("experience_source"))
             .outerjoin(owner, owner.id == Lead.owner_id)
             .outerjoin(cand, cand.id == Lead.candidate_id)
             .outerjoin(matched, matched.id == Lead.matched_candidate_id)
             .outerjoin(LeadBatch, LeadBatch.id == Lead.batch_id))
    return query, owner, has_resume


def _row_out(row) -> dict:
    lead, owner_name, cand_ref, matched_ref, matched_name, has_resume, experience_source = row
    return lead_dict(lead, owner_name=owner_name, candidate_ref=cand_ref, matched_ref=matched_ref, matched_name=matched_name,
                     has_resume=has_resume, experience_source=experience_source)


# ------------------------------------------------------------------ search and filters

def _csv_param(value) -> list[str]:
    items: list[str] = []
    for part in (value if isinstance(value, (list, tuple)) else [value]):
        items += [p.strip() for p in str(part or "").split(",") if p.strip()]
    return items


def apply_filters(query, owner, has_resume, user: User, f: dict):
    """The list/export/assign filters (all optional, AND-ed). `f` holds the snake_case query parameters."""
    effective_time = func.coalesce(Lead.created_time, Lead.created_at)

    def term_matches(term: str):
        lowered = term.lower()
        fields = [Lead.name, Lead.phone, Lead.whatsapp, Lead.email, Lead.city, Lead.state, Lead.current_location,
                  Lead.position_applied, Lead.country_interest, Lead.campaign_name, Lead.adset_name, Lead.ad_name,
                  Lead.form_name, Lead.notes, owner.name, Lead.external_id]
        clauses = [literal_contains(field, term) for field in fields]
        squeezed = term.replace(" ", "")
        if len(squeezed) >= 3:
            clauses += [literal_contains(func.replace(field, " ", ""), squeezed) for field in (Lead.name, owner.name, Lead.position_applied)]
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 4 and re.fullmatch(r"[\d\s+\-()]+", term):
            clauses.append(literal_contains(func.regexp_replace(Lead.phone, r"\D", "", "g"), digits))
            clauses.append(literal_contains(func.regexp_replace(func.coalesce(Lead.whatsapp, ""), r"\D", "", "g"), digits))
        statuses = [key for key, label in STATUS_LABELS.items() if lowered in label.lower() or lowered in key.replace("_", " ")]
        if statuses:
            clauses.append(Lead.status.in_(statuses))
        platforms = [key for key, label in PLATFORM_LABELS.items() if lowered in label.lower() or lowered == key]
        if platforms:
            clauses.append(Lead.platform.in_(platforms))
        return or_(*clauses)

    for term in search_terms(f.get("q") or ""):
        query = query.where(term_matches(term))
    statuses = _csv_param(f.get("status"))
    if statuses:
        unknown = [s for s in statuses if s not in LEAD_STATUSES]
        if unknown:
            raise fail(422, "invalid_request", f"Unknown status: {', '.join(unknown)}.")
        query = query.where(Lead.status.in_(statuses))
    for key, column in (("campaign", Lead.campaign_name), ("form", Lead.form_name), ("position", Lead.position_applied),
                        ("country", Lead.country_interest), ("platform", Lead.platform)):
        values = _csv_param(f.get(key))
        if values:
            query = query.where(func.lower(column).in_([v.lower() for v in values]))
    who = (f.get("owner") or "").strip()
    if who in ("unassigned", "none"):  # the Lead sheet's "Unassigned" choice sends `none`
        query = query.where(Lead.owner_id.is_(None))
    elif who:
        query = query.where(Lead.owner_id == (user.id if who == "me" else who))
    if f.get("created_from"):
        query = query.where(effective_time >= agency_midnight(f["created_from"], "From date"))
    if f.get("created_to"):
        query = query.where(effective_time < agency_midnight(f["created_to"], "To date", 1))
    if f.get("has_resume") is not None:
        query = query.where(has_resume if f["has_resume"] else ~has_resume)
    if f.get("matched_candidate") is not None:
        query = query.where(Lead.matched_candidate_id.is_not(None) if f["matched_candidate"] else Lead.matched_candidate_id.is_(None))
    if f.get("follow_up") == "due":
        query = query.where(Lead.next_follow_up_at < follow_up_horizon(), Lead.status.not_in(CLOSED))
    elif f.get("follow_up") == "none":
        query = query.where(Lead.next_follow_up_at.is_(None))
    elif f.get("follow_up"):
        raise fail(422, "invalid_request", "follow_up must be due or none.")
    if f.get("batch_id"):
        query = query.where(Lead.batch_id == f["batch_id"])
    return query


def follow_up_horizon() -> datetime:
    """Midnight at the end of today in the agency time zone: a follow-up before it is due today or overdue."""
    from app.services.daily_work import agency_today
    return agency_midnight(agency_today(datetime.now(timezone.utc)).isoformat(), "Today", 1)


def _order(owner, key: str, direction: str):
    if key not in SORTS:
        raise fail(422, "invalid_request", f"Sort by one of: {', '.join(SORTS)}.")
    columns = {
        "created": func.coalesce(Lead.created_time, Lead.created_at), "name": func.lower(Lead.name), "status": Lead.status,
        "owner": func.lower(owner.name), "campaign": func.lower(Lead.campaign_name), "position": func.lower(Lead.position_applied),
        "follow_up": Lead.next_follow_up_at, "last_contacted": Lead.last_contacted_at, "calls": Lead.call_count,
        "experience": func.coalesce(Lead.experience_total_years, Lead.experience_restaurant_years),
    }
    column = columns[key]
    ordered = column.desc().nulls_last() if direction == "desc" else column.asc().nulls_last()
    return [ordered, Lead.id]


MAX_EXTRA_COLUMNS = 12


def pretty_header(key: str) -> str:
    """A sheet header as a column heading: `which_position_are_you_applying_for?` -> `Which position are you applying for`."""
    words = re.sub(r"[\s_]+", " ", str(key or "")).strip().rstrip("?:").strip()
    return (words[:1].upper() + words[1:])[:80] if words else ""


def extra_columns(db: Session, filters: dict | None = None) -> list[dict]:
    """The custom question columns found in the stored sheets: `{key, label, count}`, most answered first.

    Every ad form asks its own questions and the importer keeps the answers it has no lead field for in `extra`
    (header -> text), so the Lead sheet offers one optional column per distinct header. `filters` may narrow them to
    one campaign, form or upload (the questions of the form being looked at).
    """
    filters = filters or {}
    where, params = ["jsonb_typeof(l.extra) = 'object'"], {}
    for field, column in (("campaign", "campaign_name"), ("form", "form_name")):
        values = _csv_param(filters.get(field) or [])
        if values:
            where.append(f"l.{column} = ANY(CAST(:{field} AS text[]))")
            params[field] = values
    if filters.get("batch_id"):
        where.append("l.batch_id = :batch_id")
        params["batch_id"] = filters["batch_id"]
    rows = db.execute(text(
        "SELECT k.key, count(*) AS n FROM crm_leads l CROSS JOIN LATERAL jsonb_each_text(l.extra) AS k(key, value) "
        f"WHERE {' AND '.join(where)} AND btrim(k.value) <> '' GROUP BY k.key ORDER BY n DESC, k.key LIMIT 60"), params).all()
    return [{"key": key, "label": pretty_header(key) or key, "count": int(count)} for key, count in rows]


def search_leads(db: Session, user: User, filters: dict, page: int, page_size: int, sort: str = "created", direction: str = "desc",
                 extra_keys: list[str] | None = None) -> dict:
    query, owner, has_resume = _base_query()
    query = apply_filters(query, owner, has_resume, user, filters).order_by(*_order(owner, sort, direction))
    listing = query.add_columns(func.count().over().label("total"))
    found = db.execute(listing.offset(page * page_size).limit(page_size)).all()
    if found:
        total = found[0].total
    elif page == 0:
        total = 0
    else:
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        page = min(page, max(0, (total - 1) // page_size))
        found = db.execute(listing.offset(page * page_size).limit(page_size)).all()
    items = [_row_out(row[:-1]) for row in found]  # The final selected column is the total-count window.
    wanted = [k for k in dict.fromkeys(extra_keys or []) if k]
    if wanted:  # the custom question answers: `*` = every answer the lead has, else just the named ones
        keys = None if "*" in wanted else wanted[:MAX_EXTRA_COLUMNS]
        for item, row in zip(items, found):
            stored = row[0].extra if isinstance(row[0].extra, dict) else {}
            item["extra"] = {str(k): str(v)[:500] for k, v in stored.items() if v not in (None, "") and (keys is None or k in keys)}
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def get_lead_detail(db: Session, user: User, lead_id: str) -> dict:
    query, _, _ = _base_query()
    row = db.execute(query.where(Lead.id == lead_id).options()).first()
    if row is None:
        raise fail(404, "not_found", "This lead was not found.")
    lead = row[0]
    data = _row_out(row)
    data["extra"], data["raw"] = lead.extra or {}, lead.raw or {}
    activities = db.execute(select(LeadActivity, User.name).outerjoin(User, User.id == LeadActivity.user_id)
                            .where(LeadActivity.lead_id == lead_id).order_by(LeadActivity.created_at.desc(), LeadActivity.id).limit(100)).all()
    data["activities"] = [{"id": a.id, "kind": a.kind, "summary": a.summary, "userId": a.user_id, "userName": name,
                           "meta": a.meta, "createdAt": iso(a.created_at)} for a, name in activities]
    resume = db.scalar(select(Attachment).where(Attachment.entity_type == "lead", Attachment.entity_id == lead_id, Attachment.slot == "resume")
                       .order_by(Attachment.version.desc()).limit(1))
    data["resume"] = ({"id": resume.id, "name": resume.file_name, "sizeKb": resume.size_kb, "version": resume.version,
                       "url": f"/api/files/{resume.id}", "uploadedAt": iso(resume.uploaded_at)} if resume else None)
    data["dailyWorkGate"] = daily_work_gate(db, user)
    return data


def load_lead(db: Session, lead_id: str, *, lock: bool = False) -> Lead:
    statement = select(Lead).where(Lead.id == lead_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    lead = db.scalar(statement)
    if lead is None:
        raise fail(404, "not_found", "This lead was not found.")
    return lead


def lead_response(db: Session, lead_id: str) -> dict:
    query, _, _ = _base_query()
    row = db.execute(query.where(Lead.id == lead_id)).first()
    return _row_out(row)


# ------------------------------------------------------------------ summary and facets

def summary(db: Session, user: User, scope: str | None = None) -> dict:
    scope = scope or ("all" if user.role in (Role.admin, Role.manager) else "mine")
    if scope not in ("mine", "all"):
        raise fail(422, "invalid_request", "scope must be mine or all.")
    from app.services.daily_work import agency_today
    from app.services.candidate_search import agency_midnight as midnight
    today_start = midnight(agency_today(datetime.now(timezone.utc)).isoformat(), "Today")
    closed = "('converted','not_interested','invalid','wrong_number')"
    rows = db.execute(text(
        "SELECT status, campaign_name, count(*) AS n, count(*) FILTER (WHERE coalesce(created_time, created_at) >= :ts) AS new_today, "
        f"count(*) FILTER (WHERE next_follow_up_at < :horizon AND status NOT IN {closed}) AS due, "
        "count(*) FILTER (WHERE matched_candidate_id IS NOT NULL) AS matched, "
        "count(*) FILTER (WHERE resume_received_at IS NOT NULL) AS with_resume, "
        "count(*) FILTER (WHERE status = 'new' AND call_count = 0) AS uncontacted, NULL::timestamptz AS last_import "
        "FROM crm_leads WHERE (:everyone OR owner_id = :me) GROUP BY status, campaign_name "
        "UNION ALL SELECT '__meta__', NULL, "
        f"(SELECT count(*) FROM crm_leads WHERE owner_id IS NULL AND status NOT IN {closed}), 0, "
        f"(SELECT count(*) FROM crm_leads WHERE owner_id = :me AND next_follow_up_at < :horizon AND status NOT IN {closed}), 0, 0, 0, "
        "(SELECT max(created_at) FROM crm_lead_batches WHERE status = 'completed')"),
        {"ts": today_start, "everyone": scope == "all", "me": user.id, "horizon": today_start + timedelta(days=1)}).all()
    out = {"scope": scope, "total": 0, "newToday": 0, "unassigned": 0, "uncontacted": 0, "interested": 0, "resumeRequested": 0,
           "resumeReceived": 0, "converted": 0, "followUpsDue": 0, "followUpsDueMine": 0, "matchedCandidates": 0, "withResume": 0,
           "byStatus": {}, "byCampaign": [], "lastImportAt": None}
    campaigns: dict[str, dict] = {}
    for status, campaign, n, new_today, due, matched, with_resume, uncontacted, last_import in rows:
        if status == "__meta__":
            out["unassigned"], out["lastImportAt"], out["followUpsDueMine"] = int(n), iso(last_import), int(due)  # due: the viewer's own follow-ups due today or overdue
            continue
        out["total"] += n
        out["newToday"] += new_today
        out["followUpsDue"] += due
        out["matchedCandidates"] += matched
        out["withResume"] += with_resume
        out["uncontacted"] += uncontacted
        out["byStatus"][status] = out["byStatus"].get(status, 0) + n
        label = campaign or ""
        entry = campaigns.setdefault(label, {"campaign": campaign, "count": 0, "converted": 0})
        entry["count"] += n
        entry["converted"] += n if status == "converted" else 0
    out["interested"] = out["byStatus"].get("interested", 0)
    out["resumeRequested"] = out["byStatus"].get("resume_requested", 0)
    out["resumeReceived"] = out["byStatus"].get("resume_received", 0)
    out["converted"] = out["byStatus"].get("converted", 0)
    out["byCampaign"] = sorted(campaigns.values(), key=lambda c: -c["count"])[:50]
    out["dailyWorkGate"] = daily_work_gate(db, user)
    return out


def facets(db: Session) -> dict:
    rows = db.execute(text(
        "SELECT 'campaign' AS k, campaign_name AS v, NULL AS id, count(*) FROM crm_leads WHERE campaign_name IS NOT NULL GROUP BY campaign_name "
        "UNION ALL SELECT 'form', form_name, NULL, count(*) FROM crm_leads WHERE form_name IS NOT NULL GROUP BY form_name "
        "UNION ALL SELECT 'position', position_applied, NULL, count(*) FROM crm_leads WHERE position_applied IS NOT NULL GROUP BY position_applied "
        "UNION ALL SELECT 'country', country_interest, NULL, count(*) FROM crm_leads WHERE country_interest IS NOT NULL GROUP BY country_interest "
        "UNION ALL SELECT 'platform', platform, NULL, count(*) FROM crm_leads WHERE platform IS NOT NULL GROUP BY platform "
        "UNION ALL SELECT 'owner', u.name, l.owner_id, count(*) FROM crm_leads l JOIN auth_users u ON u.id = l.owner_id GROUP BY l.owner_id, u.name")).all()
    out = {"campaigns": [], "forms": [], "positions": [], "countries": [], "platforms": [], "owners": []}
    plural = {"campaign": "campaigns", "form": "forms", "position": "positions", "country": "countries", "platform": "platforms"}
    for kind, value, owner_id, count in sorted(rows, key=lambda r: (str(r[1]).lower(), r[0])):
        if kind == "owner":
            out["owners"].append({"id": owner_id, "name": value, "count": count})
        elif len(out[plural[kind]]) < 300:
            out[plural[kind]].append(value)
    return out


# ------------------------------------------------------------------ call, patch, link

def log_call(db: Session, user: User, lead: Lead, outcome: str, remarks: str | None, next_follow_up_at: datetime | None, set_follow_up: bool) -> None:
    if outcome not in OUTCOMES:
        raise fail(422, "invalid_request", f"outcome must be one of: {', '.join(OUTCOMES)}.")
    if lead.status == "converted":
        raise fail(409, "already_converted", "This lead is already converted to a candidate.")
    now = datetime.now(timezone.utc)
    before = lead.status
    lead.call_count = (lead.call_count or 0) + 1
    lead.last_contacted_at = now
    if outcome == "answered" and lead.status == "new":
        lead.status = "contacted"
    elif outcome == "interested" and lead.status not in ("resume_requested", "resume_received"):
        lead.status = "interested"
    elif outcome == "not_interested":
        lead.status = "not_interested"
    elif outcome == "wrong_number":
        lead.status = "wrong_number"
    if set_follow_up:
        lead.next_follow_up_at = next_follow_up_at
    text_ = (remarks or "").strip()
    log_activity(db, lead.id, user.id, "call", f"{outcome.replace('_', ' ').capitalize()}" + (f": {text_}" if text_ else ""),
                 {"outcome": outcome, "from": before, "to": lead.status, "callNo": lead.call_count})
    if set_follow_up or lead.status != before:  # the owner's follow-up to-do follows the date and closes with the lead
        lead_followups.sync(db, [lead.id])


EDITABLE = {
    "name": ("name", 200), "email": ("email", 255), "whatsapp": ("whatsapp", 40), "city": ("city", 120), "state": ("state", 120),
    "positionApplied": ("position_applied", 200), "countryInterest": ("country_interest", 120),
    "maritalStatus": ("marital_status", 120), "educationLevel": ("education_level", 200),
}


def patch_lead(db: Session, user: User, lead: Lead, body: dict) -> None:
    """Apply the fields present in `body` (camelCase keys, already type-checked). Writes one activity per kind of change."""
    changes: list[str] = []
    if "status" in body and body["status"] != lead.status:
        status = body["status"]
        if status not in LEAD_STATUSES:
            raise fail(422, "invalid_request", f"status must be one of: {', '.join(LEAD_STATUSES)}.")
        if status == "converted":
            raise fail(422, "invalid_request", "Use Convert to turn a lead into a candidate.")
        if lead.status == "converted":
            raise fail(409, "already_converted", "This lead is already converted to a candidate.")
        before, lead.status = lead.status, status
        log_activity(db, lead.id, user.id, "status", f"Status changed from {STATUS_LABELS[before]} to {STATUS_LABELS[status]}", {"from": before, "to": status})
    if "notes" in body and (body["notes"] or None) != lead.notes:
        lead.notes = (body["notes"] or "").strip() or None
        log_activity(db, lead.id, user.id, "note", (lead.notes or "Notes cleared")[:300])
    if "nextFollowUpAt" in body and body["nextFollowUpAt"] != lead.next_follow_up_at:
        lead.next_follow_up_at = body["nextFollowUpAt"]
        changes.append("follow-up date")
    for key, (column, limit) in EDITABLE.items():
        if key in body:
            value = " ".join(str(body[key]).split())[:limit] if body[key] is not None and str(body[key]).strip() else None
            if key == "email":
                value = lp.parse_email(value) if value else None
            if value != getattr(lead, column):
                setattr(lead, column, value)
                changes.append(key)
                if key == "email":
                    lead.email_key = value
    if "phone" in body:
        parsed = lp.parse_phone(body["phone"])
        if body["phone"] and not parsed["valid"]:
            raise fail(422, "invalid_request", "Enter a valid phone number (7 to 15 digits).")
        if parsed["phone"] != lead.phone:
            lead.phone, lead.phone_country_code, lead.phone_key = parsed["phone"], parsed["country_code"], parsed["key"]
            changes.append("phone")
    for key, column, cleaner in (("gender", "gender", lp.parse_gender), ("dob", "dob", lambda v: v)):
        if key in body:
            value = cleaner(body[key]) if body[key] is not None else None
            if value != getattr(lead, column):
                setattr(lead, column, value)
                changes.append(key)
    for key, column in (("experienceRestaurantYears", "experience_restaurant_years"), ("experienceTotalYears", "experience_total_years")):
        if key in body and body[key] != getattr(lead, column):
            setattr(lead, column, body[key])
            changes.append(key)
    # Free-text experience answers ("3", "2.5 yrs", "6 months", "Yes"): the years are read from the text, as at import.
    for key, prefix, label in (("experienceRestaurant", "experience_restaurant", "restaurant experience"),
                               ("experienceTotal", "experience_total", "total experience"),
                               ("experienceIndia", "experience_india", "India experience"),
                               ("experienceAbroad", "experience_abroad", "abroad experience")):
        if key not in body:
            continue
        years, written = lp.parse_experience(body[key]) if body[key] is not None else (None, None)
        if written is None and key in ("experienceIndia", "experienceAbroad"):
            written = ""  # cleared on purpose: do not fall back to the sheet's answer
        before = (getattr(lead, f"{prefix}_years"), getattr(lead, f"{prefix}_text"))
        before = (float(before[0]) if before[0] is not None else None, before[1])
        if (years, written) != before:
            setattr(lead, f"{prefix}_years", years)
            setattr(lead, f"{prefix}_text", written)
            changes.append(label)
    if changes:
        key_clash = None
        if {"phone", "email"} & set(changes):
            key_clash = db.execute(text("SELECT id FROM crm_leads WHERE id <> :id AND ((phone_key IS NOT NULL AND phone_key = :p) OR (email_key IS NOT NULL AND email_key = :e)) LIMIT 1"),
                                   {"id": lead.id, "p": lead.phone_key, "e": lead.email_key}).scalar()
        if key_clash:
            db.rollback()
            raise fail(409, "duplicate_lead", "Another lead already has this phone number or e-mail.", existingLeadId=key_clash)
        log_activity(db, lead.id, user.id, "edit", "Edited " + ", ".join(changes))
    lead.updated_at = datetime.now(timezone.utc)
    if {"status", "nextFollowUpAt", "name"} & set(body):  # the owner's follow-up to-do follows the date, the name and the status
        lead_followups.sync(db, [lead.id])


def note_resume_uploaded(db: Session, lead: Lead, user: User, attachment) -> None:
    """A resume was stored for the lead: it is received, and the history says so (called by the files router)."""
    lead.resume_received_at = datetime.now(timezone.utc)
    if lead.status in ("new", "contacted", "interested", "resume_requested"):
        lead.status = "resume_received"
    log_activity(db, lead.id, user.id, "resume", f"Resume uploaded: {attachment.file_name} (version {attachment.version})")


# ------------------------------------------------------------------ assignment

def assign_leads(db: Session, user: User, ids: list[str] | None, filters: dict | None, owner_id: str, only_unassigned: bool) -> dict:
    from app.services.lead_import import eligible_staff, open_lead_counts, spread_evenly
    if not ids and filters is None:
        raise fail(422, "invalid_request", "Choose the leads to assign (ids) or give a filter.")
    query = select(Lead.id, Lead.owner_id).where(Lead.status != "converted")
    if ids:
        if len(ids) > 20000:
            raise fail(422, "invalid_request", "Assign at most 20,000 leads at a time.")
        query = query.where(Lead.id.in_(ids))
    else:
        base, owner, has_resume = _base_query()
        filtered = apply_filters(base, owner, has_resume, user, filters or {})
        query = select(Lead.id, Lead.owner_id).where(Lead.id.in_(select(filtered.with_only_columns(Lead.id).subquery().c.id)), Lead.status != "converted")
    if only_unassigned:
        query = query.where(Lead.owner_id.is_(None))
    target = db.execute(query.order_by(Lead.created_time.desc().nulls_last(), Lead.id).limit(20001)).all()
    if len(target) > 20000:
        raise fail(422, "invalid_request", "That filter matches more than 20,000 leads. Narrow it down first.")
    if owner_id == "auto":
        staff = eligible_staff(db)
        if not staff:
            raise fail(422, "invalid_request", "No staff member is available for automatic assignment.")
        names = {u.id: u.name for u in staff}
        picks = spread_evenly(staff, open_lead_counts(db, [u.id for u in staff]), len(target))
    else:
        from app.api.routers.candidates import ensure_assignable
        try:
            person = ensure_assignable(db, owner_id)
        except HTTPException as exc:
            raise fail(422, "invalid_request", str(exc.detail)) from None
        names, picks = {person.id: person.name}, [person.id] * len(target)
    move: dict[str, list[str]] = {}
    skipped = 0
    for (lead_id, current), new_owner in zip(target, picks):
        if current == new_owner:
            skipped += 1
        else:
            move.setdefault(new_owner, []).append(lead_id)
    for new_owner, lead_ids in move.items():
        db.execute(text("UPDATE crm_leads SET owner_id = :o, assigned_at = now(), updated_at = now() WHERE id = ANY(CAST(:ids AS text[]))"),
                   {"o": new_owner, "ids": lead_ids})
        db.execute(text("INSERT INTO crm_lead_activities (id, lead_id, user_id, kind, summary) "
                        "SELECT md5(random()::text || clock_timestamp()::text || x)::uuid::text, x, :u, 'assign', :s FROM unnest(CAST(:ids AS text[])) AS x"),
                   {"u": user.id, "s": f"Assigned to {names[new_owner]}", "ids": lead_ids})
        db.add(Notification(user_id=new_owner, kind="lead_assignment", title="New leads assigned",
                            detail=f"{len(lead_ids)} {'lead' if len(lead_ids) == 1 else 'leads'} to call", link_to="/leads"))
    lead_followups.sync(db, [i for ids_ in move.values() for i in ids_])  # a reassigned lead's follow-up to-do moves to the new owner
    owners = [{"id": o, "name": names[o], "count": len(l)} for o, l in move.items()]
    return {"assigned": sum(len(l) for l in move.values()), "skipped": skipped, "owners": owners}


# ------------------------------------------------------------------ convert / link

MARITAL = {"single", "married", "divorced", "widowed", "separated"}


def candidate_body(lead: Lead, overrides: dict, user: User, assign_to: str | None) -> dict:
    """The CandidateCreate payload for a lead, with the person's corrections on top."""
    years = lead.experience_total_years if lead.experience_total_years is not None else lead.experience_restaurant_years
    body = {
        "name": lead.name or "", "phone": lead.phone or "", "email": lead.email, "whatsapp": lead.whatsapp, "dob": lead.dob,
        "gender": (lead.gender or "").lower() or None, "current_city": lead.city or lead.current_location,
        "designations": [lead.position_applied] if lead.position_applied else [],
        "total_experience": min(80, int(years or 0)), "country_preference": lead.country_interest,
        "marital_status": lead.marital_status if (lead.marital_status or "").lower() in MARITAL else None,
        "education": [{"level": lead.education_level}] if lead.education_level else [],
        # A campaign identifies where the lead came from, not a referral agency.
        # Keep the campaign on the linked lead and store an explicit provenance
        # pointer so candidate screens do not label advertising text as an agent.
        "source": "other", "source_detail": f"lead_sheet:{lead.id}",
    }
    camel = {"currentCity": "current_city", "totalExperience": "total_experience", "countryPreference": "country_preference",
             "sourceDetail": "source_detail"}
    for key, value in overrides.items():
        if key == "assignTo":
            continue
        body[camel.get(key, key)] = value
    body = {k: v for k, v in body.items() if v is not None}
    if assign_to:
        body["assign_to"] = assign_to
    return body


def convert_lead(db: Session, user: User, lead_id: str, overrides: dict, resume: UploadFile | None, *, resume_check_id: str | None = None) -> dict:
    from pydantic import ValidationError
    from app.api.routers import candidates as cand_router
    cand_router.lock_assignment_cursor(db)
    lead = load_lead(db, lead_id, lock=True)
    ensure_can_work_lead(db, lead, user)
    if lead.status == "converted":
        raise fail(409, "already_converted", "This lead is already converted to a candidate.")
    from app.services import lead_resume_check
    verified_job_id = lead_resume_check.require_verified(db, user, lead, resume, resume_check_id, overrides)
    assign_to = None
    if user.role in (Role.admin, Role.manager):
        wanted = overrides.get("assignTo") or lead.owner_id
        if wanted:
            try:
                cand_router.ensure_assignable(db, wanted)
                assign_to = wanted
            except HTTPException:
                assign_to = None
    lead_id_, lead_status_before = lead.id, lead.status
    try:
        body = cand_router.CandidateCreate(**candidate_body(lead, overrides, user, assign_to))
    except ValidationError as exc:
        first = exc.errors()[0]
        raise fail(422, "invalid_request", str(first.get("msg", "Check the lead's details")).replace("Value error, ", "")) from None
    try:
        created = cand_router.create_candidate_record(body, db, user, commit=False)
    except HTTPException as exc:
        detail = exc.detail
        if exc.status_code == 409 and isinstance(detail, dict) and "candidate" in detail:
            raise fail(409, "candidate_exists", detail.get("message", "A candidate with this phone or e-mail already exists."), candidate=detail["candidate"]) from None
        if exc.status_code == 409:
            raise fail(409, "candidate_exists", str(detail)) from None
        raise
    candidate = db.get(Candidate, created.id)
    resume_attached, resume_error = False, None
    if verified_job_id:
        try:
            lead_resume_check.attach_verified(db, user, candidate, verified_job_id, lead)
            resume_attached = True
        except Exception:
            db.rollback()
            raise fail(409, "resume_verification_required", "The checked resume could not be attached. No candidate was created. Check it again and retry.") from None
    lead = load_lead(db, lead_id_, lock=True)
    lead.status, lead.candidate_id = "converted", candidate.id
    lead.updated_at = datetime.now(timezone.utc)
    provenance = {"leadId": lead.id, "campaignId": lead.campaign_id, "campaignName": lead.campaign_name,
                  "formName": lead.form_name, "platform": lead.platform}
    log_activity(db, lead.id, user.id, "convert", f"Converted to candidate {candidate.ref}",
                 {**provenance, "candidateId": candidate.id, "candidateRef": candidate.ref,
                  "from": lead_status_before, "resumeAttached": resume_attached, "initialTaskId": created.task_id})
    db.add(AuditEntry(actor_id=user.id, action="Converted lead to candidate", target=candidate.name,
                      entity_type="candidate", entity_id=candidate.id, meta=provenance))
    lead_matching.clear_self_match(lead)
    lead_followups.sync(db, [lead.id])
    db.commit()
    return {"lead": lead_response(db, lead.id),
            "candidate": {"id": created.id, "ref": created.ref, "name": created.name, "ownerId": created.owner_id,
                          "ownerName": created.owner_name, "taskId": created.task_id},
            "resumeAttached": resume_attached, "resumeError": resume_error}


def link_candidate(db: Session, user: User, lead: Lead, candidate_id: str) -> None:
    if lead.status == "converted":
        raise fail(409, "already_converted", "This lead is already converted to a candidate.")
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise fail(404, "not_found", "That candidate was not found.")
    before = lead.status
    lead.status, lead.candidate_id = "converted", candidate.id
    lead.updated_at = datetime.now(timezone.utc)
    log_activity(db, lead.id, user.id, "convert", f"Linked to existing candidate {candidate.ref}",
                 {"candidateId": candidate.id, "candidateRef": candidate.ref, "from": before, "linked": True})
    lead_matching.clear_self_match(lead)
    lead_followups.sync(db, [lead.id])


# ------------------------------------------------------------------ batches

def batch_dict(batch: LeadBatch, uploader: str | None, remaining: int | None = None) -> dict:
    return {
        "id": batch.id, "fileName": batch.file_name, "fileSize": batch.file_size, "fileSha256": batch.file_sha256, "source": batch.source,
        "format": batch.file_format, "encoding": batch.encoding, "delimiter": batch.delimiter, "uploadedBy": batch.uploaded_by,
        "uploadedByName": uploader, "createdAt": iso(batch.created_at), "status": batch.status, "campaignLabel": batch.campaign_label,
        "assignedTo": batch.assigned_to, "fillEmpty": batch.fill_empty, "rowsTotal": batch.rows_total, "rowsNew": batch.rows_new,
        "rowsDuplicate": batch.rows_duplicate, "rowsInvalid": batch.rows_invalid, "rowsMatchedCandidates": batch.rows_matched_candidates,
        "rowsUpdated": batch.rows_updated, "columnMap": batch.column_map or {}, "unmappedColumns": [h for h, f in (batch.column_map or {}).items() if not f],
        "notes": batch.notes, "leadsRemaining": remaining,
    }


def list_batches(db: Session, page: int, page_size: int) -> dict:
    remaining = (select(func.count()).select_from(Lead).where(Lead.batch_id == LeadBatch.id).correlate(LeadBatch).scalar_subquery())
    rows = db.execute(select(LeadBatch, User.name, remaining, func.count().over()).outerjoin(User, User.id == LeadBatch.uploaded_by)
                      .order_by(LeadBatch.created_at.desc(), LeadBatch.id).offset(page * page_size).limit(page_size)).all()
    total = rows[0][3] if rows else (db.scalar(select(func.count()).select_from(LeadBatch)) or 0)
    return {"items": [batch_dict(b, name, left) for b, name, left, _ in rows], "total": total, "page": page, "pageSize": page_size}


def get_batch(db: Session, batch_id: str) -> dict:
    row = db.execute(select(LeadBatch, User.name).outerjoin(User, User.id == LeadBatch.uploaded_by).where(LeadBatch.id == batch_id)).first()
    if row is None:
        raise fail(404, "not_found", "This upload was not found.")
    batch = row[0]
    left = db.scalar(select(func.count()).select_from(Lead).where(Lead.batch_id == batch.id))
    data = batch_dict(batch, row[1], left)
    report = batch.report or {}
    dups, bad = report.get("duplicates", []), report.get("invalid", [])
    data.update(duplicates=dups[:200], duplicatesTruncated=len(dups) > 200 or batch.rows_duplicate > len(dups),
                invalid=bad[:200], invalidTruncated=len(bad) > 200 or batch.rows_invalid > len(bad))
    return data


def safe_cell(value) -> str:
    """Spreadsheet-formula safe text: a cell that starts with = + - @ (or a tab/CR) is prefixed with an apostrophe."""
    text_ = "" if value is None else str(value)
    if re.fullmatch(r"[+-]?[\d\s().-]+", text_):  # a phone number or a negative number is not a formula
        return text_
    return "'" + text_ if text_[:1] in ("=", "+", "-", "@", "\t", "\r") else text_


def batch_report_csv(db: Session, batch_id: str) -> bytes:
    from app.services import lead_report
    return lead_report.report_csv(db, batch_id)


def revert_batch(db: Session, user: User, batch_id: str) -> dict:
    batch = db.get(LeadBatch, batch_id)
    if batch is None:
        raise fail(404, "not_found", "This upload was not found.")
    deleted = db.execute(text(
        "DELETE FROM crm_leads l WHERE l.batch_id = :b AND l.status = 'new' AND l.owner_id IS NULL AND l.call_count = 0 AND l.candidate_id IS NULL "
        "AND l.notes IS NULL AND l.resume_received_at IS NULL AND NOT EXISTS (SELECT 1 FROM crm_lead_activities a WHERE a.lead_id = l.id AND a.kind <> 'import') "
        "RETURNING l.id"), {"b": batch_id}).all()
    kept = db.scalar(select(func.count()).select_from(Lead).where(Lead.batch_id == batch_id)) or 0
    if not kept:
        batch.status = "reverted"
    db.add(AuditEntry(actor_id=user.id, action="Reverted lead upload", target=batch.file_name, entity_type="lead_batch", entity_id=batch.id,
                      meta={"deleted": len(deleted), "kept": kept}))
    return {"deleted": len(deleted), "kept": kept}


# ------------------------------------------------------------------ export

def experience_export_value(value: dict, field: str):
    """Keep the actual answer; only unqualified numeric answers become numbers."""
    answer = value.get(field + "Text")
    years = value.get(field + "Years")
    if isinstance(answer, str) and answer.strip():
        answer = answer.strip()
        if years is not None and re.fullmatch(r"\d+(?:[.,]\d+)?", answer):
            return years
        return answer
    return years


def export_age(dob: date | None, today: date) -> int | None:
    if not isinstance(dob, date) or dob > today:
        return None
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if 0 <= years <= 120 else None


EXPORT_COLUMNS = [
    ("Name / Age / Contact", lambda l, o, e: e["_exportIdentity"]),
    ("Job applied", lambda l, o, e: l.position_applied),
    ("Education", lambda l, o, e: l.education_level),
    ("Experience (India)", lambda l, o, e: experience_export_value(e, "experienceIndia")),
    ("Experience (Abroad)", lambda l, o, e: experience_export_value(e, "experienceAbroad")),
    ("Experience (total)", lambda l, o, e: experience_export_value(e, "experienceTotal")),
    ("Location", lambda l, o, e: e["_exportLocation"]),
    ("Created", lambda l, o, e: l.created_time), ("Imported", lambda l, o, e: l.created_at),
    ("Campaign", lambda l, o, e: l.campaign_name), ("Form", lambda l, o, e: l.form_name), ("Status", lambda l, o, e: STATUS_LABELS.get(l.status, l.status)),
    ("Owner", lambda l, o, e: o), ("Calls", lambda l, o, e: l.call_count), ("Last contacted", lambda l, o, e: l.last_contacted_at),
    ("Next follow-up", lambda l, o, e: l.next_follow_up_at), ("Notes", lambda l, o, e: l.notes),
    ("Name", lambda l, o, e: l.name), ("Age", lambda l, o, e: e["_exportAge"]),
    ("Phone", lambda l, o, e: l.phone), ("E-mail", lambda l, o, e: l.email), ("WhatsApp", lambda l, o, e: l.whatsapp),
    ("City", lambda l, o, e: l.city), ("State", lambda l, o, e: l.state), ("Current location", lambda l, o, e: l.current_location),
    ("Country interest", lambda l, o, e: l.country_interest), ("Gender", lambda l, o, e: l.gender),
    ("Date of birth", lambda l, o, e: l.dob),
    ("India experience question", lambda l, o, e: e["experienceIndiaQuestion"]),
    ("Abroad experience question", lambda l, o, e: e["experienceAbroadQuestion"]),
    ("Total experience question", lambda l, o, e: e["experienceTotalQuestion"]),
    ("Experience (restaurant)", lambda l, o, e: experience_export_value(e, "experienceRestaurant")),
    ("Restaurant experience question", lambda l, o, e: e["experienceRestaurantQuestion"]),
    ("Platform", lambda l, o, e: l.platform), ("Lead id", lambda l, o, e: l.external_id),
]


def export_table(found) -> tuple[list[str], list[list]]:
    """Shared table for filtered CSV/XLSX and selected-lead Excel downloads."""
    from app.services.daily_work import agency_today
    today = agency_today(datetime.now(timezone.utc))
    counts: dict[str, int] = {}
    for row in found:
        for key, value in (row[0].extra or {}).items():
            if value not in (None, ""):
                counts[key] = counts.get(key, 0) + 1
    extra_keys = [key for key, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:60]]
    header = [name for name, _ in EXPORT_COLUMNS] + [pretty_header(key) or key for key in extra_keys]
    rows = []
    for row in found:
        experience = _row_out(row)
        lead = row[0]
        age = export_age(lead.dob, today)
        experience["_exportAge"] = age
        experience["_exportIdentity"] = "\n".join(str(value) for value in (
            lead.name, f"Age: {age}" if age is not None else None, lead.phone, lead.email) if value)
        experience["_exportLocation"] = ", ".join(value for value in (lead.city, lead.state) if value) or lead.current_location
        rows.append([fn(row[0], row[1], experience) for _, fn in EXPORT_COLUMNS] + [(row[0].extra or {}).get(key) for key in extra_keys])
    return header, rows


def export_leads(db: Session, user: User, filters: dict, fmt: str, sort: str = "created", direction: str = "desc") -> tuple[bytes, str, str]:
    """The filtered, sorted list as Excel or CSV: every core column, then one column per custom question the exported leads answered
    (headed with the sheet's own wording). Excel keeps phone numbers and ids as text and dates as real dates."""
    from app.services import lead_xlsx
    query, owner, has_resume = _base_query()
    query = apply_filters(query, owner, has_resume, user, filters).options(undefer(Lead.extra)).order_by(*_order(owner, sort, direction)).limit(EXPORT_LIMIT)
    header, rows = export_table(db.execute(query).all())
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([safe_cell(iso(v)) if v is not None else "" for v in row])
        return ("﻿" + out.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "lead-sheet.csv"
    body = [header] + [[(float(v) if isinstance(v, Decimal) else v) for v in row] for row in rows]
    sheets = [{"name": "Lead sheet", "rows": body, "widths": lead_xlsx.fit_widths(body)}]
    return lead_xlsx.build_workbook(sheets), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "lead-sheet.xlsx"
