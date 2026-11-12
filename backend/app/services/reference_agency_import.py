"""Agency-sheet preview and conservative, repeat-safe confirmation.

Parsing never writes. Confirmation reparses the exact previewed bytes/options, then
serialises directory writes for one short transaction. It only creates agencies or
appends distinct team contacts; existing values are never replaced by an import.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import PurePath
from uuid import uuid4
from xml.etree import ElementTree

from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas import ReferenceAgencyOut
from app.db.models import AuditEntry, ReferenceAgency
from app.services import agency_settings, lead_parsing as sheets
from app.services.candidate_identity import normalize_phone

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000
MAX_COLUMNS = 50
FIELDS = ("name", "agencyName", "phone", "email", "place", "address", "state", "notes")
STATUSES = ("create", "add_contact", "duplicate", "conflict", "invalid", "section")
ALIASES = {
    "name": ("name", "contact", "contact name", "contact person", "person", "person name", "agent name", "reference name"),
    "agencyName": ("agency", "agency name", "company", "company name", "consultancy", "consultancy name"),
    "phone": ("phone", "phone number", "mobile", "mobile number", "mobile no", "phone no", "contact number", "contact no", "whatsapp"),
    "email": ("email", "email address", "email id", "e mail"),
    "place": ("place", "city", "place city", "location", "town", "district"),
    "address": ("address", "postal address", "full address"),
    "state": ("state", "province"),
    "notes": ("notes", "note", "remarks", "remark"),
}
SECTION_PLACES = {"trichy": "Trichy", "madurai": "Madurai", "otherplaces": "Other Places"}


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", sheets.clean_cell(value)).strip()


def _key(value) -> str:
    return clean_text(value).casefold()


def _header_key(value) -> str:
    return re.sub(r"[^a-z0-9]", "", _key(value))


HEADER_KEYS = {_header_key(alias): field for field, aliases in ALIASES.items() for alias in aliases}


def _phone(value: str) -> str:
    # Excel's numeric cells are already converted exactly by the workbook reader.
    # CSV exports sometimes retain a terminal .0 or exponent; do not round decimals.
    raw = clean_text(value)
    if re.fullmatch(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", raw) and ("." in raw or "e" in raw.lower()):
        try:
            number = Decimal(raw)
            if number.is_finite() and number == number.to_integral_value() and 0 <= number < Decimal("1e20"):
                raw = str(int(number))
        except InvalidOperation:
            pass
    return raw


def _phone_key(value, home: str) -> str:
    if _looks_phone(value):
        return normalize_phone(value, home=home)
    # Preserve separators in lists of numbers rather than merging their digits.
    return re.sub(r"[\s()+.\-]", "", _key(value))


def _looks_phone(value) -> bool:
    return bool(re.fullmatch(r"\+?[\d\s().-]{7,30}", _phone(value)))


def _read(data: bytes, filename: str, selected: str | None):
    suffix = PurePath(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise HTTPException(422, "Upload an .xlsx or .csv agency sheet. Save old .xls files as .xlsx first.")
    try:
        if suffix == ".xlsx":
            names = sheets.workbook_sheets(data)
            if not names:
                raise HTTPException(422, "The workbook has no worksheet.")
            selected = selected or names[0]
            rows = sheets.read_xlsx(data, sheet_name=selected, max_rows=MAX_ROWS + 20,
                                   max_columns=MAX_COLUMNS, preserve_row_numbers=True)
        else:
            decoded, _ = sheets.decode_bytes(data)
            if "\ufffd" in decoded or "\x00" in decoded:
                raise HTTPException(422, "The CSV text could not be read. Save it as UTF-8 CSV.")
            try:
                delimiter = csv.Sniffer().sniff(decoded[:20000], delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = sheets.detect_delimiter(decoded)
            rows = []
            for row in csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter):
                if len(rows) >= MAX_ROWS + 21:
                    raise HTTPException(422, "Import at most 2,000 agency rows at a time.")
                rows.append([sheets.clean_cell(cell) for cell in row])
            selected, names = "CSV", ["CSV"]
        if not any(any(cell for cell in row) for row in rows):
            raise HTTPException(422, "The sheet is empty.")
        if max(map(len, rows), default=0) > MAX_COLUMNS:
            raise HTTPException(422, "The sheet must have at most 50 columns.")
        return rows, selected, names
    except sheets.LeadFileError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except (ElementTree.ParseError, ValueError, KeyError, RuntimeError, csv.Error) as exc:
        raise HTTPException(422, "The sheet could not be read. Save it again as .xlsx or .csv.") from exc


def _mapping(headers: list[str], rows: list[list[str]]) -> dict:
    mapping = {}
    for index, label in enumerate(headers):
        field = HEADER_KEYS.get(_header_key(label))
        if field and field not in mapping:
            mapping[field] = index
    # An unnamed column of phone-like cells is visible in the editable preview;
    # it never bypasses the required preview/confirm step.
    if "phone" not in mapping:
        possible = []
        for index, label in enumerate(headers):
            if clean_text(label) or index in mapping.values():
                continue
            sample = [row[index] for row in rows[:30] if len(row) > index and clean_text(row[index])]
            if sample and all(_looks_phone(cell) for cell in sample):
                possible.append(index)
        if len(possible) == 1:
            mapping["phone"] = possible[0]
    return mapping


def parse_upload(file: UploadFile, *, mapping: str | None, header_row: int | None, sheet_name: str | None) -> dict:
    data = file.file.read(MAX_BYTES + 1)
    if not data:
        raise HTTPException(422, "The sheet is empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "The agency sheet must be under 5 MB.")
    table, selected, names = _read(data, file.filename or "", sheet_name)
    if header_row is None:
        scored = [(sum(_header_key(cell) in HEADER_KEYS for cell in row), index + 1)
                  for index, row in enumerate(table[:20])]
        best = max(scored, key=lambda item: item[0])
        header_row = best[1] if best[0] else 0
    if header_row < 0 or header_row > min(20, len(table)):
        raise HTTPException(422, "Choose the header row from 1 to 20, or 0 for no header.")
    width = max(map(len, table), default=0)
    headers = list(table[header_row - 1]) if header_row else []
    headers += [""] * (width - len(headers))
    if mapping is None:
        mapped = _mapping(headers, table[header_row:])
    else:
        try:
            mapped = json.loads(mapping)
        except (ValueError, TypeError):
            raise HTTPException(422, "The column mapping must be a JSON object.") from None
        if not isinstance(mapped, dict) or any(key not in FIELDS or type(value) is not int or value < 0 or value >= width
                                               for key, value in mapped.items()):
            raise HTTPException(422, "Map each agency field to an existing column number.")
        if len(set(mapped.values())) != len(mapped):
            raise HTTPException(422, "Each sheet column may be mapped to only one agency field.")
    columns = [{"index": index, "label": label or f"Column {index + 1} (blank)",
                "sample": [row[index] for row in table[header_row:header_row + 8] if len(row) > index and row[index]][:3]}
               for index, label in enumerate(headers)]
    parsed, place = [], None
    # Sections preceding the header are also retained (a city title above a table).
    for index, cells in enumerate(table):
        nonempty = [clean_text(cell) for cell in cells if clean_text(cell)]
        if not nonempty:
            continue
        section = SECTION_PLACES.get(_header_key(nonempty[0])) if len(nonempty) == 1 else None
        if len(nonempty) == 1 and re.match(r"^(place|city)\s*:", nonempty[0], re.I):
            section = nonempty[0].split(":", 1)[1].strip()
        if section:
            place = None if section == "Other Places" else section
            message = f"Place for following rows: {place}." if place else "Other Places section: leave place blank unless the row supplies one."
            parsed.append({"rowNumber": index + 1, "status": "section", "message": message,
                           "values": {"place": place}})
            continue
        if index < header_row:
            continue
        values = {field: clean_text(cells[column]) if column < len(cells) else "" for field, column in mapped.items()}
        if values.get("phone"):
            values["phone"] = _phone(values["phone"])
        if not values.get("place") and place:
            values["place"] = place
        parsed.append({"rowNumber": index + 1, "values": values})
    if sum(row.get("status") != "section" for row in parsed) > MAX_ROWS:
        raise HTTPException(422, "Import at most 2,000 agency rows at a time.")
    options = json.dumps({"mapping": mapped, "headerRow": header_row, "sheetName": selected}, sort_keys=True).encode()
    digest = hashlib.sha256(b"reference-agency-import-v1\0" + data + b"\0" + options).hexdigest()
    return {"fileName": (file.filename or "agency-sheet").replace("\\", "/").rsplit("/", 1)[-1],
            "sheetName": selected, "sheets": names, "headerRow": header_row, "columns": columns,
            "mapping": mapped, "previewHash": digest, "rows": parsed}


def _stored(row: ReferenceAgency) -> dict:
    return {**ReferenceAgencyOut.model_validate(row).model_dump(by_alias=True, mode="json"), "_row": row}


def _contacts(row: dict) -> list[dict]:
    return [{field: row.get(field) for field in ("name", "phone", "email")}] + list(row.get("staffContacts") or [])


def _same(field, left, right, home: str) -> bool:
    return (_phone_key(left, home) == _phone_key(right, home)) if field == "phone" else (_key(left) == _key(right))


def _differences(values: dict, saved: dict, fields, home: str) -> list[str]:
    # Missing incoming values never erase stored data. A new nonempty value is
    # deliberately reviewed rather than silently filling an existing contact.
    return [field for field in fields if values.get(field) and not _same(field, values[field], saved.get(field), home)]


def plan(parsed: dict, existing: list[dict], *, home: str) -> list[dict]:
    from app.api.routers.data import MAX_STAFF_CONTACTS
    from app.api.routers.reference_agencies import ReferenceAgencyIn

    rows, directory = [], [copy.deepcopy({k: v for k, v in item.items() if k != "_row"}) for item in existing]
    for source in parsed["rows"]:
        row = copy.deepcopy(source)
        if row.get("status") == "section":
            rows.append(row)
            continue
        try:
            model = ReferenceAgencyIn.model_validate(row["values"])
            row["values"] = {field: getattr(model, field) for field in FIELDS}
        except ValidationError as exc:
            row.update(status="invalid", message="; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors()))
            rows.append(row)
            continue
        values = row["values"]
        targets = [item for item in directory if _key(item["agencyName"]) == _key(values["agencyName"])]

        def overlaps_contact(item):
            return any(_same("name", values["name"], contact.get("name"), home)
                       or (values.get("phone") and contact.get("phone") and _same("phone", values["phone"], contact["phone"], home))
                       or (values.get("email") and contact.get("email") and _same("email", values["email"], contact["email"], home))
                       for contact in _contacts(item))

        # A distinct contact with a known place must not disappear into an
        # unknown-location agency (nor vice versa). Same-contact missing-location
        # matches remain visible so an added location receives manual review.
        targets = [item for item in targets if _same("place", values.get("place"), item.get("place"), home)
                   or ((not values.get("place") or not item.get("place")) and overlaps_contact(item))]
        details = ("place", "address", "state", "notes")
        duplicate = next((item for item in targets if not _differences(values, item, details, home)
                          and any(not _differences(values, contact, ("name", "phone", "email"), home) for contact in _contacts(item))), None)
        if duplicate:
            row.update(status="duplicate", message="Already saved or repeated in this sheet; skipped.", agencyId=duplicate["id"])
        elif not targets:
            row.update(status="create", message="Create agency and its contact.")
            directory.append({**values, "id": f"new:{row['rowNumber']}", "staffContacts": []})
        elif len(targets) > 1:
            row.update(status="conflict", message="More than one existing agency matches. Review this row manually; nothing will be replaced.")
        else:
            target = targets[0]
            differences = _differences(values, target, details, home)
            contact_conflict = overlaps_contact(target)
            if differences:
                row.update(status="conflict", message="Different or additional " + ", ".join(differences) + "; review manually. Saved details will be kept.")
            elif contact_conflict:
                row.update(status="conflict", message="This contact already exists with different or missing details. Review manually; saved details will be kept.")
            elif len(target.get("staffContacts") or []) >= MAX_STAFF_CONTACTS or len(values["name"]) > 160:
                row.update(status="conflict", message="The agency team limit or contact-name length would be exceeded. Review manually.")
            else:
                row.update(status="add_contact", message="Add this person to the existing agency team; keep its primary contact and details.", agencyId=target["id"])
                target.setdefault("staffContacts", []).append({"name": values["name"], "phone": values.get("phone"),
                                                               "email": values.get("email"), "designation": None})
        rows.append(row)
    return rows


def preview(db, parsed: dict) -> dict:
    existing = [_stored(row) for row in db.scalars(select(ReferenceAgency).order_by(ReferenceAgency.id)).all()]
    rows = plan(parsed, existing, home=agency_settings.home_digits(db))
    counts = Counter(row["status"] for row in rows)
    return {**parsed, "rows": rows, "counts": {status: counts[status] for status in STATUSES},
            "canConfirm": bool(counts["create"] + counts["add_contact"])}


def confirm(db, user, parsed: dict) -> dict:
    try:
        # All manual CRUD also acquires RowExclusiveLock implicitly. This table
        # lock therefore covers imports racing ordinary saves without a new index
        # that would reject legitimate existing duplicate agency names.
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("LOCK TABLE crm_reference_agencies IN SHARE ROW EXCLUSIVE MODE"))
        existing = db.scalars(select(ReferenceAgency).order_by(ReferenceAgency.id).execution_options(populate_existing=True)).all()
        plans = plan(parsed, [_stored(row) for row in existing], home=agency_settings.home_digits(db))
        by_id = {row.id: row for row in existing}
        changed = {}
        counts = Counter(row["status"] for row in plans)
        for item in plans:
            values = item["values"]
            if item["status"] == "create":
                row = ReferenceAgency(id=str(uuid4()), **{("agency_name" if key == "agencyName" else key): value for key, value in values.items()},
                                      staff_contacts=[], specialisations=[], preferred_countries=[])
                db.add(row)
                by_id[f"new:{item['rowNumber']}"] = row
                action = "Imported reference agency"
            elif item["status"] == "add_contact":
                row = by_id[item["agencyId"]]
                row.staff_contacts = [*(row.staff_contacts or []), {"name": values["name"], "phone": values.get("phone"),
                                                                  "email": values.get("email"), "designation": None}]
                action = "Imported reference agency contact"
            else:
                continue
            changed[row.id] = row
            db.add(AuditEntry(actor_id=user.id, action=action, target=row.agency_name, entity_type="reference_agency",
                              entity_id=row.id, meta={"file": parsed["fileName"], "rowNumber": item["rowNumber"],
                                                      "previewHash": parsed["previewHash"], "values": values}))
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(409, "The agency directory changed or is busy. Preview again and retry; this import was not saved.") from exc
    return {"created": counts["create"], "contactsAdded": counts["add_contact"],
            "skipped": counts["duplicate"] + counts["conflict"] + counts["invalid"],
            "conflicts": counts["conflict"], "invalid": counts["invalid"],
            "items": [ReferenceAgencyOut.model_validate(row).model_dump(by_alias=True, mode="json") for row in changed.values()]}
