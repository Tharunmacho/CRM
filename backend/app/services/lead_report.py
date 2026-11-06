"""The import report of one lead upload: always something to read, never just a header row.

Built from what is stored, with no per-row outcome table: the people who were added are the batch's own leads (`import_row`
says which file row each came from), the ignored rows and invalid rows are in the batch's `report` JSON (capped), and the
counts are on the batch. Delivered as an Excel workbook (Summary, All rows, Ignored & invalid) or a CSV of the same rows.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Candidate, Lead, LeadBatch, User
from app.services import lead_xlsx
from app.services.lead_sheet import fail, safe_cell

OLDER_NOTE = "Row details were not stored for this older upload."
DELIMITER_NAMES = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}
DUPLICATE_REASON = {"lead_id": "same lead id", "phone": "same phone number", "email": "same e-mail address"}
INVALID_REASON = {"no_contact": "no usable phone number or e-mail address", "bad_phone": "the phone number is not valid", "bad_email": "the e-mail address is not valid"}
ALL_COLUMNS = ["Row", "Outcome", "Reason", "Name", "Phone", "E-mail", "City", "Position", "Campaign",
               "Matched stored lead: name", "Matched stored lead: phone", "Matched stored lead: added on", "Matched candidate"]
ISSUE_COLUMNS = ALL_COLUMNS


def duplicate_reason(entry: dict) -> str:
    why = DUPLICATE_REASON.get(entry.get("reason"), str(entry.get("reason") or "duplicate"))
    if entry.get("scope") == "file":
        return f"Ignored: {why}; the person is already in row {entry.get('existingRow')} of this file"
    return f"Ignored: {why}; the person is already stored"


def invalid_reason(entry: dict) -> str:
    return entry.get("message") or INVALID_REASON.get(entry.get("reason"), str(entry.get("reason") or "invalid row"))


def summary_line(batch: LeadBatch) -> str:
    total, new, dup, bad = batch.rows_total, batch.rows_new, batch.rows_duplicate, batch.rows_invalid
    if dup == 0 and bad == 0:
        return f"No duplicates or invalid rows: all {total:,} rows were added." if new == total else f"No duplicates or invalid rows. {new:,} of {total:,} rows were added."
    parts = [f"{new:,} of {total:,} rows were added"]
    if dup:
        parts.append(f"{dup:,} {'duplicate was' if dup == 1 else 'duplicates were'} ignored (the person was already stored or appears twice in the file)")
    if bad:
        parts.append(f"{bad:,} {'row was' if bad == 1 else 'rows were'} invalid (no usable phone number or e-mail address)")
    return "; ".join(parts) + "."


def load_batch(db: Session, batch_id: str) -> LeadBatch:
    batch = db.get(LeadBatch, batch_id)
    if batch is None:
        raise fail(404, "not_found", "This upload was not found.")
    return batch


def collect(db: Session, batch: LeadBatch) -> dict:
    """Everything the report shows: one dict per file row, plus the notes about what could not be listed."""
    report = batch.report or {}
    duplicates, invalid = list(report.get("duplicates") or []), list(report.get("invalid") or [])
    notes: list[str] = []
    if not report and (batch.rows_duplicate or batch.rows_invalid):
        notes.append(OLDER_NOTE)
    else:
        if len(duplicates) < batch.rows_duplicate:
            notes.append(f"Only the first {len(duplicates):,} of {batch.rows_duplicate:,} ignored rows were stored.")
        if len(invalid) < batch.rows_invalid:
            notes.append(f"Only the first {len(invalid):,} of {batch.rows_invalid:,} invalid rows were stored.")
    added = db.execute(
        select(Lead.import_row, Lead.name, Lead.phone, Lead.email, Lead.city, Lead.position_applied, Lead.campaign_name, Candidate.ref)
        .outerjoin(Candidate, Candidate.id == Lead.matched_candidate_id).where(Lead.batch_id == batch.id).order_by(Lead.import_row)).all()
    # stored leads the ignored rows matched (one statement)
    ids = sorted({d["existingLeadId"] for d in duplicates if d.get("existingLeadId")})
    stored = {}
    if ids:
        for lead_id, name, phone, created, ref in db.execute(
                select(Lead.id, Lead.name, Lead.phone, Lead.created_at, Candidate.ref).outerjoin(Candidate, Candidate.id == Lead.matched_candidate_id)
                .where(Lead.id.in_(ids))).all():
            stored[lead_id] = (name, phone, created, ref)
    rows: dict[int, dict] = {}
    for number, name, phone, email, city, position, campaign, ref in added:
        rows[number or 0] = {"row": number, "outcome": "Added", "reason": "New lead", "name": name, "phone": phone, "email": email, "city": city,
                             "position": position, "campaign": campaign, "match": None, "candidate": ref}
    for d in duplicates:
        match = stored.get(d.get("existingLeadId"))
        rows[d["row"]] = {"row": d["row"], "outcome": "Ignored: duplicate", "reason": duplicate_reason(d), "name": d.get("name"), "phone": d.get("phone"),
                          "email": d.get("email"), "city": d.get("city"), "position": d.get("position"), "campaign": d.get("campaign"),
                          "match": match, "candidate": match[3] if match else None}
    for d in invalid:
        rows[d["row"]] = {"row": d["row"], "outcome": "Invalid", "reason": invalid_reason(d), "name": d.get("name"), "phone": d.get("phone"),
                          "email": d.get("email"), "city": d.get("city"), "position": d.get("position"), "campaign": d.get("campaign"), "match": None, "candidate": None}
    ordered = [rows[key] for key in sorted(rows)]
    return {"rows": ordered, "notes": notes}


def as_line(row: dict) -> list:
    match = row.get("match")
    return [row["row"], row["outcome"], row["reason"], row.get("name"), row.get("phone"), row.get("email"), row.get("city"), row.get("position"),
            row.get("campaign"), match[0] if match else None, match[1] if match else None, match[2] if match else None, row.get("candidate")]


def format_text(batch: LeadBatch) -> str:
    parts = [(batch.file_format or "file").upper()]
    if batch.delimiter:
        parts.append(f"{DELIMITER_NAMES.get(batch.delimiter, batch.delimiter)} separated")
    if batch.encoding:
        parts.append(batch.encoding)
    return ", ".join(parts)


def report_workbook(db: Session, batch_id: str) -> bytes:
    batch = load_batch(db, batch_id)
    data = collect(db, batch)
    uploader = db.get(User, batch.uploaded_by) if batch.uploaded_by else None
    summary = [
        ["Import report", None],
        ["File name", batch.file_name],
        ["Uploaded by", uploader.name if uploader else "(unknown)"],
        ["Uploaded on", batch.created_at],
        ["Detected format", format_text(batch)],
        ["Rows in file", batch.rows_total],
        ["New leads added", batch.rows_new],
        ["Duplicates ignored", batch.rows_duplicate],
        ["Invalid rows", batch.rows_invalid],
        ["Already in the Data Bank", batch.rows_matched_candidates],
        ["Result", summary_line(batch)],
    ] + [["Note", note] for note in data["notes"]]
    lines = [as_line(row) for row in data["rows"]]
    issues = [as_line(row) for row in data["rows"] if row["outcome"] != "Added"]
    if not issues:
        issues = [[None, None, "Nothing was ignored and no row was invalid. Every row in the file was added.", None, None, None, None, None, None, None, None, None, None]]
    all_rows = [ALL_COLUMNS] + (lines or [[None, None, OLDER_NOTE if data["notes"] else "No rows are stored for this upload."] + [None] * 10])
    return lead_xlsx.build_workbook([
        {"name": "Summary", "rows": summary, "widths": [28, 90], "header": False, "freeze": False, "filter": False, "bold_first": True},
        {"name": "All rows", "rows": all_rows, "widths": lead_xlsx.fit_widths(all_rows)},
        {"name": "Ignored & invalid", "rows": [ISSUE_COLUMNS] + issues, "widths": lead_xlsx.fit_widths([ISSUE_COLUMNS] + issues)},
    ])


def report_csv(db: Session, batch_id: str) -> bytes:
    batch = load_batch(db, batch_id)
    data = collect(db, batch)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(ALL_COLUMNS)
    for row in data["rows"]:
        line = as_line(row)
        writer.writerow([safe_cell(v.isoformat() if isinstance(v, datetime) else v) if isinstance(v, str) or isinstance(v, datetime) else ("" if v is None else v) for v in line])
    for note in data["notes"]:
        writer.writerow(["", "Note", note])
    if not data["rows"] and not data["notes"]:
        writer.writerow(["", "Note", "No rows are stored for this upload."])
    return ("﻿" + out.getvalue()).encode("utf-8")


def download_name(db: Session, batch_id: str, extension: str) -> str:
    batch = load_batch(db, batch_id)
    stamp = batch.created_at.astimezone(lead_xlsx.agency_zone()).date().isoformat() if batch.created_at else datetime.now(timezone.utc).date().isoformat()
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", batch.file_name or "leads")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")[:60] or "leads"
    return f"lead-import-{stamp}-{stem}.{extension}"
