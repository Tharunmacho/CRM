"""Import a lead sheet: parse, de-duplicate in the file and against every stored lead, store only the new people.

Set-based on purpose (the database may be a network hop away; ~300 ms per statement): a real import is a handful of
statements whatever the file size -
  1 advisory lock, 1 same-file lookup, 1 lookup of stored leads per 5,000 rows, 1 lookup of Data Bank candidates,
  1 INSERT ... SELECT FROM jsonb_to_recordset per 1,000 new leads, 1 activity INSERT ... SELECT, optional fill-empty
  UPDATE, 1 notification insert per owner, 1 batch row.
Never one query per row.

Duplicate rules (docs/workflows/lead-sheet-contract.md): same lead id, else same phone key (last 10 digits), else same
e-mail; an earlier row of the same file or a stored lead. The first match decides the reason. Ignored rows are counted and
listed; stored leads are never overwritten (only `fill_empty` copies still-empty descriptive fields).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Lead, LeadBatch, Notification, Role, User
from app.services import lead_parsing as lp

LOOKUP_CHUNK = 5000
INSERT_CHUNK = 1000
REPORT_STORED = 5000        # ignored rows kept per kind in the batch report
REPORT_RETURNED = 200       # ignored rows returned in the API response
SAMPLE_ROWS = 20
CLOSED_STATUSES = ("converted", "not_interested", "invalid", "wrong_number")

# Columns written for a new lead, with the SQL type jsonb_to_recordset reads them as.
INSERT_COLUMNS = [
    ("id", "text"), ("batch_id", "text"), ("import_row", "integer"), ("external_id", "text"), ("phone_key", "text"),
    ("email_key", "text"), ("created_time", "timestamptz"), ("name", "text"), ("phone", "text"), ("phone_country_code", "text"),
    ("email", "text"), ("whatsapp", "text"), ("whatsapp_same", "boolean"), ("city", "text"), ("state", "text"),
    ("current_location", "text"), ("position_applied", "text"), ("country_interest", "text"),
    ("experience_restaurant_years", "numeric"), ("experience_restaurant_text", "text"), ("experience_total_years", "numeric"),
    ("experience_total_text", "text"), ("gender", "text"), ("marital_status", "text"), ("dob", "date"),
    ("education_level", "text"), ("military_status", "text"), ("platform", "text"), ("is_organic", "boolean"),
    ("lead_status_source", "text"), ("campaign_id", "text"), ("campaign_name", "text"), ("adset_id", "text"),
    ("adset_name", "text"), ("ad_id", "text"), ("ad_name", "text"), ("form_id", "text"), ("form_name", "text"),
    ("extra", "jsonb"), ("raw", "jsonb"), ("owner_id", "text"), ("assigned_at", "timestamptz"), ("matched_candidate_id", "text"),
]
INSERT_SQL = text(
    "INSERT INTO crm_leads (" + ", ".join(c for c, _ in INSERT_COLUMNS) + ") SELECT " + ", ".join(c for c, _ in INSERT_COLUMNS)
    + " FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(" + ", ".join(f"{c} {t}" for c, t in INSERT_COLUMNS)
    + ") ON CONFLICT DO NOTHING RETURNING id")

# Descriptive columns a duplicate may fill when `fill_empty` is on. Never contact fields, status, owner, notes, follow-up.
FILL_COLUMNS = [
    ("created_time", "timestamptz"), ("city", "text"), ("state", "text"), ("current_location", "text"),
    ("position_applied", "text"), ("country_interest", "text"), ("experience_restaurant_years", "numeric"),
    ("experience_restaurant_text", "text"), ("experience_total_years", "numeric"), ("experience_total_text", "text"),
    ("gender", "text"), ("marital_status", "text"), ("dob", "date"), ("education_level", "text"), ("military_status", "text"),
    ("platform", "text"), ("campaign_name", "text"), ("adset_name", "text"), ("ad_name", "text"), ("form_name", "text"),
]
FILL_SQL = text(
    "UPDATE crm_leads l SET " + ", ".join(f"{c} = COALESCE(l.{c}, x.{c})" for c, _ in FILL_COLUMNS) + ", updated_at = now() "
    "FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(id text, " + ", ".join(f"{c} {t}" for c, t in FILL_COLUMNS) + ") "
    "WHERE l.id = x.id AND (" + " OR ".join(f"(l.{c} IS NULL AND x.{c} IS NOT NULL)" for c, _ in FILL_COLUMNS) + ") RETURNING l.id")

# Public (camelCase) names of the columns echoed in the dry-run sample.
SAMPLE_FIELDS = {
    "externalId": "external_id", "createdTime": "created_time", "name": "name", "phone": "phone", "email": "email",
    "city": "city", "state": "state", "currentLocation": "current_location", "positionApplied": "position_applied",
    "countryInterest": "country_interest", "experienceRestaurantYears": "experience_restaurant_years",
    "experienceTotalYears": "experience_total_years", "gender": "gender", "maritalStatus": "marital_status", "dob": "dob",
    "educationLevel": "education_level", "militaryStatus": "military_status", "platform": "platform",
    "campaignName": "campaign_name", "adName": "ad_name", "formName": "form_name",
}


class ImportError_(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value))


def _public(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def eligible_staff(db: Session) -> list[User]:
    """Active staff who take automatic assignments and are not on leave today (same rules as candidate assignment)."""
    from app.services.attendance_availability import unavailable_staff_ids
    absent = set(unavailable_staff_ids(db))
    staff = db.scalars(select(User).where(User.role == Role.staff, User.active.is_(True), User.receives_auto_assign.is_(True),
                                          User.on_leave.is_(False)).order_by(User.created_at, User.id)).all()
    return [u for u in staff if u.id not in absent]


def open_lead_counts(db: Session, user_ids: list[str]) -> dict[str, int]:
    if not user_ids:
        return {}
    rows = db.execute(text("SELECT owner_id, count(*) FROM crm_leads WHERE owner_id = ANY(CAST(:ids AS text[])) "
                           "AND status NOT IN ('converted','not_interested','invalid','wrong_number') GROUP BY owner_id"),
                      {"ids": user_ids}).all()
    return {owner: count for owner, count in rows}


def spread_evenly(staff: list[User], loads: dict[str, int], count: int) -> list[str]:
    """Owner id for each of `count` leads: always the person with the fewest open leads (ties: staff order)."""
    import heapq
    heap = [(loads.get(u.id, 0), index, u.id) for index, u in enumerate(staff)]
    heapq.heapify(heap)
    owners = []
    for _ in range(count):
        load, index, uid = heapq.heappop(heap)
        owners.append(uid)
        heapq.heappush(heap, (load + 1, index, uid))
    return owners


def _find_stored(db: Session, ids: list[str], phones: list[str], emails: list[str]) -> tuple[dict, dict, dict]:
    by_id, by_phone, by_email = {}, {}, {}
    for start in range(0, max(len(ids), len(phones), len(emails)), LOOKUP_CHUNK):
        found = db.execute(text(
            "SELECT id, external_id, phone_key, email_key FROM crm_leads WHERE external_id = ANY(CAST(:i AS text[])) "
            "OR phone_key = ANY(CAST(:p AS text[])) OR email_key = ANY(CAST(:e AS text[])) ORDER BY created_at, id"),
            {"i": ids[start:start + LOOKUP_CHUNK], "p": phones[start:start + LOOKUP_CHUNK], "e": emails[start:start + LOOKUP_CHUNK]}).all()
        for lead_id, external, phone, email in found:
            if external:
                by_id.setdefault(external, lead_id)
            if phone:
                by_phone.setdefault(phone, lead_id)
            if email:
                by_email.setdefault(email, lead_id)
    return by_id, by_phone, by_email


def _find_candidates(db: Session, phones: list[str], emails: list[str]) -> tuple[dict, dict]:
    if not phones and not emails:
        return {}, {}
    rows = db.execute(text(
        "SELECT id, right(regexp_replace(phone, '\\D', '', 'g'), 10) AS pk, "
        "right(regexp_replace(coalesce(whatsapp, ''), '\\D', '', 'g'), 10) AS wk, lower(email) AS ek FROM crm_candidates "
        "WHERE right(regexp_replace(phone, '\\D', '', 'g'), 10) = ANY(CAST(:p AS text[])) "
        "OR right(regexp_replace(coalesce(whatsapp, ''), '\\D', '', 'g'), 10) = ANY(CAST(:p AS text[])) "
        "OR lower(email) = ANY(CAST(:e AS text[])) ORDER BY created_at, id"), {"p": phones, "e": emails}).all()
    by_phone, by_email = {}, {}
    for cid, pk, wk, ek in rows:
        if pk:
            by_phone.setdefault(pk, cid)
        if wk:
            by_phone.setdefault(wk, cid)
        if ek:
            by_email.setdefault(ek, cid)
    return by_phone, by_email


def preview_summary(table, columns) -> dict:
    return {"format": table.format, "encoding": table.encoding, "delimiter": table.delimiter,
            "columns": columns.columns, "unmappedColumns": columns.unmapped}


def run_import(db: Session, user: User, data: bytes, file_name: str, *, dry_run: bool = False, mapping: dict | None = None,
               assign_to: str | None = None, campaign_label: str | None = None, fill_empty: bool = False,
               source: str = "meta_ads") -> dict:
    """Parse and (unless dry_run) store the file. Returns the response described in the contract. Caller does not commit."""
    try:
        table = lp.read_table(data, file_name)
    except lp.LeadFileError as exc:
        raise ImportError_(exc.code, exc.message, exc.status) from None
    columns = lp.map_headers(table.headers, mapping)
    if not any(f in columns.by_field for f in ("phone", "email", "externalId", "name", "firstName")):
        raise ImportError_("no_usable_columns", "We could not find a phone, e-mail, lead id or name column in this file. "
                                                "Check the header row, or choose the columns by hand.")
    settings = get_settings()
    file_hash = hashlib.sha256(data).hexdigest()
    campaign_label = (campaign_label or "").strip()[:255] or None

    # 1) normalise
    rows = []  # (row number, normalised dict)
    for number, cells in table.rows:
        rows.append((number, lp.normalise_row(table.headers, cells, columns, campaign_label=campaign_label, tz=settings.agency_timezone)))

    # serialize imports (a second upload of the same people must see the first one's rows)
    if not dry_run:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('crm_lead_import'))"))

    same_file = db.execute(text("SELECT id, created_at, rows_new FROM crm_lead_batches WHERE file_sha256 = :h AND status = 'completed' "
                                "ORDER BY created_at DESC LIMIT 1"), {"h": file_hash}).first()

    # 2) in-file duplicates
    outcome: dict[int, dict] = {}
    seen_id, seen_phone, seen_email = {}, {}, {}
    candidates_for_db = []
    duplicates, invalid = [], []

    def public_name(values):
        return {"name": values["name"], "phone": values["phone"] or None, "email": values.get("email") or None, "city": values.get("city") or None,
                "position": values.get("position_applied") or None, "campaign": values.get("campaign_name") or None}

    for number, values in rows:
        problem = values["_invalid"]
        if problem:
            invalid.append({"row": number, "reason": problem["code"], "message": problem["message"], **public_name(values)})
            outcome[number] = {"outcome": "invalid"}
            continue
        match = None
        if values["external_id"] and values["external_id"] in seen_id:
            match = ("lead_id", seen_id[values["external_id"]])
        elif values["phone_key"] and values["phone_key"] in seen_phone:
            match = ("phone", seen_phone[values["phone_key"]])
        elif values["email_key"] and values["email_key"] in seen_email:
            match = ("email", seen_email[values["email_key"]])
        if match:
            duplicates.append({"row": number, "reason": match[0], "scope": "file", "existingLeadId": None, "existingRow": match[1], **public_name(values)})
            outcome[number] = {"outcome": "duplicate"}
            continue
        if values["external_id"]:
            seen_id[values["external_id"]] = number
        if values["phone_key"]:
            seen_phone[values["phone_key"]] = number
        if values["email_key"]:
            seen_email[values["email_key"]] = number
        candidates_for_db.append((number, values))

    # 3) duplicates of stored leads (one statement per 5,000 rows)
    by_id, by_phone, by_email = _find_stored(
        db, [v["external_id"] for _, v in candidates_for_db if v["external_id"]],
        [v["phone_key"] for _, v in candidates_for_db if v["phone_key"]],
        [v["email_key"] for _, v in candidates_for_db if v["email_key"]])
    fresh, fill_rows = [], []
    for number, values in candidates_for_db:
        match = None
        if values["external_id"] and values["external_id"] in by_id:
            match = ("lead_id", by_id[values["external_id"]])
        elif values["phone_key"] and values["phone_key"] in by_phone:
            match = ("phone", by_phone[values["phone_key"]])
        elif values["email_key"] and values["email_key"] in by_email:
            match = ("email", by_email[values["email_key"]])
        if match:
            duplicates.append({"row": number, "reason": match[0], "scope": "database", "existingLeadId": match[1], "existingRow": None, **public_name(values)})
            outcome[number] = {"outcome": "duplicate"}
            if fill_empty:
                fill_rows.append({"id": match[1], **{c: values[c] for c, _ in FILL_COLUMNS}})
        else:
            fresh.append((number, values))
            outcome[number] = {"outcome": "new"}
    duplicates.sort(key=lambda d: d["row"])
    invalid.sort(key=lambda d: d["row"])

    # 4) already in the Data Bank? (flag only)
    cand_phone, cand_email = _find_candidates(db, [v["phone_key"] for _, v in fresh if v["phone_key"]],
                                              [v["email_key"] for _, v in fresh if v["email_key"]])
    matched = 0
    for _, values in fresh:
        cid = cand_phone.get(values["phone_key"]) or cand_email.get(values["email_key"])
        values["matched_candidate_id"] = cid
        matched += bool(cid)

    # 5) owners
    owner_ids: list[str | None] = [None] * len(fresh)
    assigned_to = None
    if assign_to and fresh:
        if assign_to == "auto":
            staff = eligible_staff(db)
            if not staff:
                raise ImportError_("invalid_request", "No staff member is available for automatic assignment. Upload without assigning, or choose a person.")
            owner_ids = spread_evenly(staff, open_lead_counts(db, [u.id for u in staff]), len(fresh))
            assigned_to = "auto"
        else:
            from app.api.routers.candidates import ensure_assignable
            try:
                target = ensure_assignable(db, assign_to)
            except Exception as exc:  # HTTPException 422
                raise ImportError_("invalid_request", getattr(exc, "detail", "Choose an active staff member who is not on leave")) from None
            owner_ids = [target.id] * len(fresh)
            assigned_to = target.id

    result = {
        "dryRun": dry_run, "batchId": None, "fileName": file_name, "fileSize": len(data), "fileSha256": file_hash,
        "sameFileBefore": ({"batchId": same_file[0], "uploadedAt": same_file[1].isoformat(), "rowsNew": same_file[2]} if same_file else None),
        "format": table.format, "encoding": table.encoding, "delimiter": table.delimiter, "source": source,
        "rowsTotal": len(rows), "rowsNew": len(fresh), "rowsDuplicate": len(duplicates), "rowsInvalid": len(invalid),
        "rowsMatchedCandidates": matched, "rowsUpdated": 0,
        "columns": columns.columns, "unmappedColumns": columns.unmapped,
        "duplicates": duplicates[:REPORT_RETURNED], "duplicatesTruncated": len(duplicates) > REPORT_RETURNED,
        "invalid": invalid[:REPORT_RETURNED], "invalidTruncated": len(invalid) > REPORT_RETURNED,
        "assignedTo": assigned_to, "assignedCount": len(fresh) if assigned_to else 0,
    }
    if dry_run:
        by_number = dict(rows)
        result["sample"] = [
            {"row": number, **{public: _public(by_number[number][column]) for public, column in SAMPLE_FIELDS.items()}, **outcome[number]}
            for number, _ in rows[:SAMPLE_ROWS]]
        return result

    # 6) store
    batch = LeadBatch(
        id=str(uuid.uuid4()), file_name=file_name[:255] or "leads", file_size=len(data), file_sha256=file_hash, source=source,
        file_format=table.format, encoding=table.encoding, delimiter=table.delimiter, uploaded_by=user.id,
        assigned_to=assigned_to if assigned_to and assigned_to != "auto" else None, campaign_label=campaign_label,
        fill_empty=fill_empty, rows_total=len(rows), rows_new=0, rows_duplicate=len(duplicates), rows_invalid=len(invalid),
        rows_matched_candidates=matched, column_map=columns.as_map(),
        report={"duplicates": duplicates[:REPORT_STORED], "invalid": invalid[:REPORT_STORED],
                "unmappedColumns": columns.unmapped, "truncated": len(duplicates) > REPORT_STORED or len(invalid) > REPORT_STORED})
    db.add(batch)
    db.flush()
    stored = 0
    stamp = db.scalar(text("SELECT now()"))
    for start in range(0, len(fresh), INSERT_CHUNK):
        chunk = []
        for offset, (number, values) in enumerate(fresh[start:start + INSERT_CHUNK]):
            owner = owner_ids[start + offset]
            row = {c: values.get(c) for c, _ in INSERT_COLUMNS if c not in ("id", "batch_id", "import_row", "owner_id", "assigned_at")}
            row.update(id=str(uuid.uuid4()), batch_id=batch.id, import_row=number, owner_id=owner, assigned_at=stamp if owner else None)
            chunk.append(row)
        returned = db.execute(INSERT_SQL, {"rows": json.dumps(chunk, default=_json_default, ensure_ascii=False)}).all()
        stored += len(returned)
    updated = 0
    for start in range(0, len(fill_rows), INSERT_CHUNK):
        updated += len(db.execute(FILL_SQL, {"rows": json.dumps(fill_rows[start:start + INSERT_CHUNK], default=_json_default, ensure_ascii=False)}).all())
    # a stored count below the plan means another import won a race on the unique lead id; report what really happened
    batch.rows_new = stored
    batch.rows_duplicate = len(duplicates) + (len(fresh) - stored)
    batch.rows_updated = updated
    result.update(batchId=batch.id, rowsNew=stored, rowsDuplicate=batch.rows_duplicate, rowsUpdated=updated)
    if stored:
        who = "automatically" if assigned_to == "auto" else ("to a staff member" if assigned_to else "unassigned")
        db.execute(text("INSERT INTO crm_lead_activities (id, lead_id, user_id, kind, summary) "
                        "SELECT md5(random()::text || clock_timestamp()::text || id)::uuid::text, id, :u, 'import', :s FROM crm_leads WHERE batch_id = :b"),
                   {"u": user.id, "s": f"Imported from {batch.file_name} ({who})", "b": batch.id})
        if assigned_to:
            counts: dict[str, int] = {}
            for owner in owner_ids[:len(fresh)]:
                if owner:
                    counts[owner] = counts.get(owner, 0) + 1
            for owner, count in counts.items():
                db.add(Notification(user_id=owner, kind="lead_assignment", title="New leads assigned",
                                    detail=f"{count} new {'lead' if count == 1 else 'leads'} to call", link_to="/leads"))
    return result
