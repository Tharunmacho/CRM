"""Bulk import of candidates from a folder of documents and one stage sheet (see services/bulk_candidate_import.py).

The screen drives four calls: the sheet's columns, reading an uploaded sheet, a dry-run check of every folder, and then one
upload per candidate. One request per candidate keeps each transaction small, lets the screen show progress and lets a
failed candidate be retried on its own without touching the others.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import Role, User
from app.db.session import get_db
from app.services import bulk_candidate_import as bulk

router = APIRouter(prefix="/api/candidate-import", tags=["candidate-import"])
logger = logging.getLogger(__name__)

MAX_FOLDERS = 200
MAX_FILES_PER_CANDIDATE = 40
MAX_SHEET_BYTES = 5 * 1024 * 1024


class FileIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(min_length=1, max_length=400)
    kind: str | None = Field(default=None, max_length=40)


class FolderIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folder: str = Field(min_length=1, max_length=200)
    files: list[FileIn] = Field(default_factory=list, max_length=MAX_FILES_PER_CANDIDATE)


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folders: list[FolderIn] = Field(max_length=MAX_FOLDERS)
    rows: list[dict] = Field(default_factory=list, max_length=MAX_FOLDERS * 2)
    defaultOrder: str | None = Field(default=None, max_length=40)
    defaultRecruiterId: str | None = Field(default=None, max_length=36)
    # A job chosen for one candidate in the list: {candidate key: order reference}. Beats the sheet and the default.
    jobs: dict[str, str] = Field(default_factory=dict, max_length=MAX_FOLDERS * 2)


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folders: list[str] = Field(default_factory=list, max_length=MAX_FOLDERS)


class ReadAgainIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidateIds: list[str] = Field(max_length=MAX_FOLDERS)


class SpecIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folder: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    row: dict = Field(default_factory=dict)
    files: list[FileIn] = Field(default_factory=list, max_length=MAX_FILES_PER_CANDIDATE)
    defaultOrder: str | None = Field(default=None, max_length=40)
    defaultRecruiterId: str | None = Field(default=None, max_length=36)


def _own(user: User) -> User | None:
    """A staff member or manager imports for themselves, like adding a candidate by hand; only the admin may choose the recruiter."""
    return user if user.role != Role.admin else None


def _row_values(row: dict) -> dict:
    """Only the sheet's own keys, as short text: the screen sends rows back exactly as this API gave them."""
    return {key: str(row[key]).strip()[:500] for key in bulk.COLUMN_KEYS if row.get(key) not in (None, "")}


@dataclass
class Unit:
    """One candidate to import: an ordinary folder, or one person's files out of a folder that held several people."""
    folder: str
    files: list[FileIn]
    parent: str
    name: str | None = None
    notes: list[dict] = field(default_factory=list)


def _units(folders: list[FolderIn]) -> list[Unit]:
    units: list[Unit] = []
    for folder in folders:
        split = bulk.split_people([item.path for item in folder.files])
        if split is None:
            units.append(Unit(folder=folder.folder, files=folder.files, parent=folder.folder))
            continue
        notes = [{"level": "info", "message": f"Separated from the folder '{folder.folder}': its file names start with {len(split['people'])} different people's names."}]
        if split["shared"]:
            notes.append({"level": "info", "message": f"{len(split['shared'])} shared file{'s' if len(split['shared']) != 1 else ''} (a group ticket) added to everyone: " + ", ".join(p.rsplit("/", 1)[-1] for p in split["shared"]) + "."})
        if split["left_out"]:
            notes.append({"level": "warning", "message": "Left out because the name does not say whose it is: " + ", ".join(p.rsplit("/", 1)[-1] for p in split["left_out"]) + ". Add them from the candidate's profile."})
        for person in split["people"]:
            wanted = set(person["paths"])
            units.append(Unit(folder=f"{folder.folder} / {person['name']}", files=[item for item in folder.files if item.path in wanted],
                              parent=folder.folder, name=person["name"], notes=notes))
    return units


def _match_rows(units: list[Unit], rows: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Pair every candidate with its sheet row (by the Folder column, then by Name); also return the rows nothing matched."""
    by_folder: dict[str, dict] = {}
    left = list(rows)
    for pass_ in ("folder", "name"):
        for unit in units:
            if unit.folder in by_folder:
                continue
            wanted = {bulk.row_key(unit.folder), bulk.row_key(bulk._clean_name(unit.folder)), bulk.row_key(unit.name or "")} - {""}
            hit = next((row for row in left if row.get(pass_) and bulk.row_key(str(row[pass_])) in wanted), None)
            if hit is not None:
                by_folder[unit.folder] = hit
                left.remove(hit)
    return by_folder, left


@router.get("/columns")
def columns(user: User = Depends(current_user)) -> dict:
    return {"columns": [{"key": c.key, "header": c.header, "hint": c.hint, "required": c.required} for c in bulk.COLUMNS],
            "kinds": [{"key": key, "label": label} for key, label in bulk.KIND_LABELS.items()]}


@router.post("/manifest")
def read_sheet(file: UploadFile = File(...), user: User = Depends(current_user)) -> dict:
    data = file.file.read(MAX_SHEET_BYTES + 1)
    if len(data) > MAX_SHEET_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The sheet must be under 5 MB.")
    try:
        parsed = bulk.read_manifest(data, file.filename or "")
    except bulk.ManifestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    rows = [{**_row_values(row), "rowNumber": row["rowNumber"]} for row in parsed["rows"]]
    return {"rows": rows, "recognised": parsed["recognised"], "ignored": parsed["ignored"]}


@router.post("/template")
def template(body: TemplateIn, user: User = Depends(current_user)) -> Response:
    """The sheet to fill in: one row per folder, the right headings, a notes sheet. Cells are text so phone numbers survive Excel."""
    from app.services import lead_xlsx
    header = [column.header for column in bulk.COLUMNS]
    rows = [header] + [[folder if column.key == "folder" else bulk._clean_name(folder) if column.key == "name" else ""
                        for column in bulk.COLUMNS] for folder in body.folders]
    notes = [["Column", "Needed?", "What to write"]] + [[c.header, "Yes" if c.required else "", c.hint] for c in bulk.COLUMNS]
    notes += [[], ["Dates", "", "Write dates like 14/03/2026 (day first)."],
              ["Missing details", "", "Leave a cell empty when you do not have it. The candidate is imported up to the last stage the sheet and the "
                                       "documents prove, and you continue from there on the stage screen."]]
    data = lead_xlsx.build_workbook([
        {"name": "Candidates", "rows": rows, "widths": lead_xlsx.fit_widths(rows), "header": True, "freeze": True, "filter": False},
        {"name": "Notes", "rows": notes, "widths": [28, 10, 90], "header": True, "freeze": False, "filter": False},
    ])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="candidate-import-sheet.xlsx"', "Cache-Control": "no-store"})


@router.post("/check")
def check(body: CheckIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Dry run: what each folder would become. Nothing is written."""
    rows = [_row_values(row) | {"rowNumber": row.get("rowNumber")} for row in body.rows if isinstance(row, dict)]
    units = _units(body.folders)
    matched, unmatched = _match_rows(units, rows)
    ctx = bulk.load_context(db, default_order=body.defaultOrder, default_recruiter=body.defaultRecruiterId,
                            wanted_orders={row.get("order", "") for row in rows} | set(body.jobs.values()), only=_own(user), me=user)
    plans = []
    for unit in units:
        row = matched.get(unit.folder)
        values = _row_values(row) if row else {}
        if unit.name and not values.get("name"):
            values["name"] = unit.name
        if body.jobs.get(unit.folder):
            values["order"] = body.jobs[unit.folder].strip()[:40]
        plan = bulk.plan_candidate(db, ctx, folder=unit.folder, row=values or None, files=[item.model_dump() for item in unit.files])
        plan["issues"] = unit.notes + plan["issues"]
        plan["row"] = values
        plan["hasRow"] = row is not None
        plan["parent"] = unit.parent
        plans.append(plan)
    return {"plans": plans, "unmatchedRows": [{"rowNumber": row.get("rowNumber"), "name": row.get("name"), "folder": row.get("folder")} for row in unmatched]}


@router.post("/candidate")
def import_one(spec: str = Form(...), files: list[UploadFile] = File(default=[]),
               db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Import one candidate: the folder's files in the order `spec.files` lists them, plus the sheet row."""
    from app.api.routers.files import ALLOWED, MAX_BYTES, _content_type
    started = time.perf_counter()
    try:
        parsed = SpecIn.model_validate(json.loads(spec))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The import request was not understood.") from exc
    if len(files) != len(parsed.files):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The files sent do not match the list.")

    candidates, dropped = [], []
    for item, upload in zip(parsed.files, files):
        name = item.path
        short = name.rsplit("/", 1)[-1]
        problem = bulk.file_problem(name)
        data = upload.file.read(MAX_BYTES + 1)
        content_type = _content_type(upload.content_type, name)
        if problem or not data or len(data) > MAX_BYTES or content_type not in ALLOWED:
            dropped.append({"level": "warning", "message": f"{short} was not imported: "
                            + (problem or ("it is empty." if not data else f"it is over {MAX_BYTES // (1024 * 1024)} MB." if len(data) > MAX_BYTES
                                           else "only PDF, Word and image files can be imported."))})
            continue
        kinds = [item.kind] if item.kind in bulk.KIND_LABELS else bulk.classify_path_kinds(name)
        candidates.append({"name": name, "short": short, "data": data, "content_type": content_type, "kinds": kinds})

    # Files whose names say nothing are read for what they are, side by side (each read is slow, they do not depend on each other).
    unnamed = [entry for entry in candidates if not entry["kinds"]]
    if unnamed:
        with ThreadPoolExecutor(max_workers=min(4, len(unnamed))) as pool:
            for entry, found in zip(unnamed, pool.map(lambda entry: bulk.classify_content(entry["data"]), unnamed)):
                entry["kinds"] = found or ["other"]

    sorted_at = time.perf_counter()
    uploads, seen = [], {}
    for entry in candidates:
        kinds, short = entry["kinds"], entry["short"]
        if "photo" in kinds and not entry["content_type"].startswith("image/"):
            dropped.append({"level": "warning", "message": f"{short} was not imported: the photo must be an image."})
            continue
        digest = hashlib.sha256(entry["data"]).hexdigest()
        if digest in seen:   # the same file twice ("Medical.pdf" and "Medical_1.pdf"): kept once
            first = seen[digest]
            first.kinds = list(dict.fromkeys(first.kinds + kinds))
            dropped.append({"level": "info", "message": f"{short} is a copy of {first.path.rsplit('/', 1)[-1]} and was kept once."})
            continue
        uploads.append(bulk.Upload(path=entry["name"], kinds=kinds, data=entry["data"], content_type=entry["content_type"]))
        seen[digest] = uploads[-1]

    row = _row_values(parsed.row)
    if parsed.name and not row.get("name"):
        row["name"] = parsed.name
    ctx = bulk.load_context(db, default_order=parsed.defaultOrder, default_recruiter=parsed.defaultRecruiterId,
                            wanted_orders={row.get("order", "")}, only=_own(user), me=user)
    written: list[str] = []
    settings = get_settings()
    saving_at = time.perf_counter()
    try:
        result = bulk.import_candidate(db, user, ctx, folder=parsed.folder, row=row, uploads=uploads, written=written)
        saved_at = time.perf_counter()
        db.commit()
        logger.info("Import %s: %d files, receiving and sorting %.1fs (of which reading unnamed files %.1fs), saving %.1fs, commit %.1fs",
                    parsed.folder, len(uploads), saving_at - started, sorted_at - started, saved_at - saving_at, time.perf_counter() - saved_at)
    except bulk.ImportRefused as refused:
        db.rollback()
        bulk.discard_written(settings, written)
        return {"action": "skip", "folder": parsed.folder, "name": refused.plan["name"], "issues": refused.plan["issues"] + dropped}
    except Exception as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.warning("Could not confirm the import rollback.")
        if not (isinstance(exc, DBAPIError) and exc.connection_invalidated):  # a lost COMMIT may have succeeded: keep the bytes
            bulk.discard_written(settings, written)
        if isinstance(exc, IntegrityError):
            raise HTTPException(status.HTTP_409_CONFLICT, "Another change was saved at the same moment. Retry this candidate.") from exc
        if isinstance(exc, (OSError, SQLAlchemyError)):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "This candidate could not be saved. Retry it.") from exc
        raise
    result["issues"] = result["issues"] + dropped
    result["folder"] = parsed.folder
    return result


@router.post("/read-again")
def read_again(body: ReadAgainIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Put the readings that failed (the reading service was down, a timeout) back in the queue for these candidates."""
    from sqlalchemy import select
    from app.core.deps import ensure_can_work_candidate
    from app.db.models import Attachment, Candidate
    from app.services.application_form import requeue_failed
    queued = 0
    for candidate in db.scalars(select(Candidate).where(Candidate.id.in_(body.candidateIds[:MAX_FOLDERS]))):
        try:
            ensure_can_work_candidate(candidate, user, db)
        except HTTPException:
            continue
        for slot in bulk.READ_SLOTS:
            latest = db.scalar(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                                                        Attachment.slot == slot).order_by(Attachment.version.desc()).limit(1))
            if latest is not None and requeue_failed(db, candidate.id, latest.id):
                queued += 1
    db.commit()
    return {"queued": queued}
