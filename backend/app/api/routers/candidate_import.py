"""Bulk import of candidates from a folder of documents and one stage sheet (see services/bulk_candidate_import.py).

The screen drives four calls: the sheet's columns, reading an uploaded sheet, a dry-run check of every folder, and then one
upload per candidate. One request per candidate keeps each transaction small, lets the screen show progress and lets a
failed candidate be retried on its own without touching the others.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import current_user
from app.db.models import Role, User
from app.db.session import get_db
from app.services import bulk_candidate_import as bulk, duplicate_pages

router = APIRouter(prefix="/api/candidate-import", tags=["candidate-import"])
logger = logging.getLogger(__name__)

MAX_FOLDERS = 200
MAX_FILES_PER_CANDIDATE = 40
MAX_SHEET_BYTES = 5 * 1024 * 1024
MAX_IDENTIFY_FILES = 200          # one folder of mixed documents read by content: more than this is split into several folders
MAX_PAGES_PER_FILE = 120


class FileIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str = Field(min_length=1, max_length=400)
    kind: str | None = Field(default=None, max_length=40)
    # Only these pages (numbered in the file) belong to this candidate: a scan whose pages are different people's.
    pages: list[int] | None = Field(default=None, max_length=MAX_PAGES_PER_FILE)
    # The file's SHA-256 (hex), computed by the screen: lets /check say a folder's files are already in the CRM without reading them.
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("pages")
    @classmethod
    def _pages(cls, value):
        if value is not None and (not value or any(n < 1 for n in value)):
            raise ValueError("pages are numbered from 1")
        return sorted(set(value)) if value else value


class PersonIn(BaseModel):
    """One person found by reading the files of a folder (see /identify): their name and the files, or pages of files, they own."""
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=200)
    files: list[FileIn] = Field(default_factory=list, max_length=MAX_IDENTIFY_FILES)
    # Two pieces of evidence disagreed: the candidate is shown, but not ticked for import until a person looks.
    needsLook: bool = False
    # What the reading says to check on this person ("3 pages were placed by their file name: check."), as /identify gave it.
    warnings: list[dict] = Field(default_factory=list, max_length=20)


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
    # How a folder becomes candidates. "auto" separates a folder only when its file names start with different people's
    # names; "one" always makes the folder ONE candidate. `grouping` overrides `groupMode` for one picked folder.
    groupMode: Literal["auto", "one"] = "auto"
    grouping: dict[str, Literal["auto", "one"]] = Field(default_factory=dict, max_length=MAX_FOLDERS * 2)
    # A person's own choice for one file: {"<picked folder>/<file path>": "<candidate's folder key>" to move it there, or
    # "" to leave it out}. Beats every automatic decision.
    assign: dict[str, str] = Field(default_factory=dict, max_length=MAX_FOLDERS * MAX_FILES_PER_CANDIDATE)
    # The people found by reading a folder's files (the /identify call), {picked folder: people}. Such a folder becomes one
    # candidate per person instead of being separated by file names.
    people: dict[str, list[PersonIn]] = Field(default_factory=dict, max_length=MAX_FOLDERS)


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
    # The name was read from this person's own documents on the Import screen (see /identify). Without it `name` is IGNORED: a name
    # taken from a folder or file name is never stored (the documents are read for it at import).
    nameFromFiles: bool = False
    # The screen's idempotency key for this candidate (any unique text per import attempt of the folder): a second request with the
    # same key (double click, a retry, a second browser) answers "already_imported" instead of creating another candidate.
    importKey: str | None = Field(default=None, max_length=100)
    # What to do when the CRM already has this person (the answer's status was "duplicate"): "skip" (default: report only),
    # "add_documents" (file these documents into the existing candidate; nothing else of it changes) or "import_anyway" (accepted only
    # for a "possible" match, i.e. phone / email / name; a "certain" match, passport / same files, is never imported again).
    duplicateAction: Literal["skip", "add_documents", "import_anyway"] = "skip"


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
    # Files of the folder that fit no one (or two different people): shown so a person can place them.
    left_out: list[str] = field(default_factory=list)
    identified: bool = False       # made from what the files say (see /identify), not from their names
    needs_look: bool = False       # the evidence on the person's pages disagrees: not ticked for import by default


def assign_key(parent: str, path: str) -> str:
    return f"{parent}/{path}"


def _identified_units(folder: FolderIn, people: list[PersonIn]) -> list[Unit]:
    """One candidate per person found by reading the folder's files. Files the people do not claim wait for a person to place them."""
    known = {item.path: item for item in folder.files}
    units: list[Unit] = []
    taken: set[str] = set()
    for person in people:
        files = [FileIn(path=item.path, kind=known[item.path].kind, pages=item.pages) for item in person.files if item.path in known]
        if not files:
            continue                      # a person with nothing to import is not a candidate
        key = f"{folder.folder} / {person.name.strip()}"
        while key in taken:
            key += " (2)" if not key.endswith(")") else f" ({int(key.rsplit('(', 1)[1][:-1]) + 1})"
        taken.add(key)
        notes = [{"level": "info", "message": f"Found by reading the files: {person.name.strip()} ({len(files)} file{'s' if len(files) != 1 else ''})."}]
        if person.needsLook:
            notes.append({"level": "warning", "message": "The documents of this person disagree with each other (see the reading result). Check them before importing."})
        for warning in person.warnings:
            # pages placed by a file name or by a first name only are not certain: say so on the candidate
            message = str(warning.get("message") or "").strip()[:300] if isinstance(warning, dict) else ""
            if message:
                notes.append({"level": "warning", "message": message})
        units.append(Unit(folder=key, files=files, parent=folder.folder, name=person.name.strip(), notes=notes, identified=True, needs_look=person.needsLook))
    placed = {item.path for unit in units for item in unit.files}
    leftover = [item.path for item in folder.files if item.path not in placed]
    for unit in units:
        unit.left_out = list(leftover)
    return units


def _units(folders: list[FolderIn], group_mode: str = "auto", grouping: dict[str, str] | None = None,
           assign: dict[str, str] | None = None, people: dict[str, list[PersonIn]] | None = None) -> list[Unit]:
    units: list[Unit] = []
    grouping, assign, people = grouping or {}, assign or {}, people or {}
    for folder in folders:
        mode = grouping.get(folder.folder) or group_mode
        if people.get(folder.folder) and mode != "one":
            first = len(units)
            units += _identified_units(folder, people[folder.folder])
            if len(units) > first:
                _apply_assignments(units, first, folder, assign)
                continue
        split = bulk.split_people([item.path for item in folder.files]) if mode != "one" else None
        first = len(units)
        if split is None:
            units.append(Unit(folder=folder.folder, files=folder.files, parent=folder.folder))
            _apply_assignments(units, first, folder, assign)
            continue
        notes = [{"level": "info", "message": f"Separated from the folder '{folder.folder}': its file names start with {len(split['people'])} different people's names."}]
        if split["shared"]:
            notes.append({"level": "info", "message": f"{len(split['shared'])} shared file{'s' if len(split['shared']) != 1 else ''} (a group ticket) added to everyone: " + ", ".join(p.rsplit("/", 1)[-1] for p in split["shared"]) + "."})
        if split["left_out"]:
            notes.append({"level": "warning", "message": "Left out because the name does not say whose it is: " + ", ".join(p.rsplit("/", 1)[-1] for p in split["left_out"]) + ". Add them from the candidate's profile."})
        taken: set[str] = set()
        for person in split["people"]:
            wanted = set(person["paths"])
            key = f"{folder.folder} / {person['name']}"
            while key in taken:   # two different people with one name must stay two candidates
                key += " (2)" if not key.endswith(")") else f" ({int(key.rsplit('(', 1)[1][:-1]) + 1})"
            taken.add(key)
            units.append(Unit(folder=key, files=[item for item in folder.files if item.path in wanted],
                              parent=folder.folder, name=person["name"], notes=notes, left_out=list(split["left_out"])))
        _apply_assignments(units, first, folder, assign)
    return units


def _apply_assignments(units: list[Unit], first: int, folder: FolderIn, assign: dict[str, str]) -> None:
    """Apply a person's own file choices to the candidates made from this folder: move a file to another one, or leave it out."""
    mine = units[first:]
    chosen = {item.path: assign[assign_key(folder.folder, item.path)] for item in folder.files if assign_key(folder.folder, item.path) in assign}
    if not chosen:
        return
    by_key = {unit.folder: unit for unit in mine}
    for unit in mine:
        unit.files = [item for item in unit.files if item.path not in chosen]
    for path, target in chosen.items():
        item = next((entry for entry in folder.files if entry.path == path), None)
        if item is not None and target in by_key:
            by_key[target].files.append(item)
            for unit in mine:   # placed by hand: it no longer waits for a person
                unit.left_out = [left for left in unit.left_out if left != path]
    # A candidate left with no file at all is not a candidate (every file was moved or left out): a folder with nothing left plans
    # nothing, and the screen shows no row for it.
    units[first:] = [unit for unit in mine if unit.files]


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
    units = _units(body.folders, body.groupMode, body.grouping, body.assign, body.people)
    matched, unmatched = _match_rows(units, rows)
    ctx = bulk.load_context(db, default_order=body.defaultOrder, default_recruiter=body.defaultRecruiterId,
                            wanted_orders={row.get("order", "") for row in rows} | set(body.jobs.values()), only=_own(user), me=user)
    plans = []
    for unit in units:
        row = matched.get(unit.folder)
        values = _row_values(row) if row else {}
        # The name is the sheet's, or one read from the documents (a person /identify found); a folder or file NAME is only how files are
        # grouped, never the candidate's name.
        name_source = "sheet" if values.get("name") else "documents" if (unit.name and unit.identified) else "pending"
        if unit.name and unit.identified and not values.get("name"):
            values["name"] = unit.name
        if body.jobs.get(unit.folder):
            values["order"] = body.jobs[unit.folder].strip()[:40]
        plan = bulk.plan_candidate(db, ctx, folder=unit.folder, row=values or None, files=[item.model_dump() for item in unit.files],
                                   by_name=not unit.identified, folder_names=False, name_source=name_source)
        # Files this CRM already holds (the screen sends each file's SHA-256): the same folder imported again is flagged, not doubled.
        held = bulk.candidates_with_files(db, [{item.sha256.lower()} for item in unit.files if item.sha256])
        if held is not None and plan.get("alreadyInCrm") is None:
            candidate, count = held
            plan["alreadyInCrm"] = {"candidateId": candidate.id, "careId": candidate.care_id, "ref": candidate.ref, "name": candidate.name,
                                    "matchedBy": "same file", "files": count, "strength": "certain"}
            plan["duplicateOf"], plan["duplicateOfCareId"], plan["duplicateOfId"] = candidate.ref, candidate.care_id, candidate.id
            plan["issues"].append({"level": "error", "message": f"Already in the CRM as {candidate.care_id or candidate.ref} ({candidate.name}): "
                                                                 f"{count} of its files are the same files. Nothing is imported for this folder."})
            plan["action"] = "skip"
        # The notes below are about file NAMES: a person found by reading the files has none of them (see by_name).
        after = ("After the import the passports, Aadhaar cards and resumes are read: a document that names a different person is held back "
                 "for review, never mixed in.") if unit.identified else (
            "The files are sorted by their names for now. After the import the passports, Aadhaar cards and resumes are read: the name on "
            "them becomes the candidate's name, and a document that names a different person is held back for review, never mixed in.")
        plan["issues"] = unit.notes + plan["issues"] + [{"level": "info", "message": after}]
        # Passport, phone and email are read from the documents while importing, so that check cannot run here: say so on the card.
        plan["identityCheck"] = "while_importing"
        plan["issues"].append({"level": "info", "message": "Checked against the CRM while importing: the files are compared now, and the passport, phone "
                                                           "and email read from the documents are compared before the candidate is created."})
        plan["row"] = _row_values(row) if row else {}   # what the sheet said; the name taken from the files is not an entry of the sheet
        if body.jobs.get(unit.folder):                  # the job a person chose for this candidate in the list is theirs too
            plan["row"]["order"] = body.jobs[unit.folder].strip()[:40]
        plan["hasRow"] = row is not None
        plan["parent"] = unit.parent
        plan["leftOut"] = unit.left_out
        plan["identified"] = unit.identified
        plan["needsLook"] = unit.needs_look
        plans.append(plan)
    return {"plans": plans, "unmatchedRows": [{"rowNumber": row.get("rowNumber"), "name": row.get("name"), "folder": row.get("folder")} for row in unmatched]}


class IdentifyIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    folder: str = Field(min_length=1, max_length=200)
    files: list[FileIn] = Field(max_length=MAX_IDENTIFY_FILES)


# Reading a folder holds up to this much in memory and keeps the machine's OCR busy for minutes, so the cost is bounded: one
# folder's files together, and how many folders are read at the same moment (the others wait a little, then are told to retry).
MAX_IDENTIFY_TOTAL_BYTES = 300 * 1024 * 1024
IDENTIFY_CONCURRENCY = 2
IDENTIFY_WAIT_SECONDS = 10.0
_IDENTIFY_SLOTS = threading.BoundedSemaphore(IDENTIFY_CONCURRENCY)


async def _take_identify_slot() -> None:
    """Wait a short while for a free reading slot (polling, so a caller that goes away never leaves a slot taken)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + IDENTIFY_WAIT_SECONDS
    while not _IDENTIFY_SLOTS.acquire(blocking=False):
        if loop.time() >= deadline:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Another folder is being read right now. Try again in a minute.",
                                headers={"Retry-After": "60"})
        await asyncio.sleep(0.2)


def _people_in_crm(db: Session, people: list[dict]) -> dict[int, dict]:
    """{index of a person: {candidateId, careId, name, matchedBy: 'passport'|'phone'|'email', files: 0}} for the people the identify step
    read whose passport number, phone or email is already on a candidate of the CRM. The numbers themselves are never returned. Never
    raises: a failed look-up just flags nobody. (A candidate keeps no Aadhaar number of its own until the reading, so it is not matched.)"""
    from app.services.candidate_identity import identity_matches
    found: dict[int, dict] = {}
    try:
        for index, person in enumerate(people):
            match = person.get("_match") or {}
            for field_name, key, label in (("passports", "passport_no", "passport"), ("phones", "phone", "phone"), ("emails", "email", "email")):
                for value in match.get(field_name, [])[:3]:
                    hits = identity_matches(db, {key: value}, lock=False)
                    if hits:
                        candidate = hits[0][0]
                        found[index] = {"candidateId": candidate.id, "careId": candidate.care_id, "ref": candidate.ref, "name": candidate.name,
                                        "matchedBy": label, "files": 0, "strength": "certain" if label == "passport" else "possible"}
                        break
                if index in found:
                    break
    except Exception:
        logger.warning("Could not look up the identified people in the CRM.")
        db.rollback()
    return found


@router.post("/identify")
async def identify(request: Request, spec: str = Form(...), files: list[UploadFile] = File(default=[]),
                   db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Read the files of ONE picked folder (local OCR only, no online reader) and say which person each file, and each page of a
    file, belongs to. Explicit and on demand: reading many scans takes minutes. Nothing is written. The result lists every page
    exactly once (placed, unplaced with a reason, or unreadable with a reason) so nothing is lost silently; see
    services/people_by_content.py for the guarantees and their limits. Closing the request stops the reading.

    Bounded: at most MAX_IDENTIFY_FILES files and MAX_IDENTIFY_TOTAL_BYTES together (413 beyond that), IDENTIFY_CONCURRENCY folders
    at once (429 for the others), and no database connection is held while the files are read."""
    from app.api.routers.files import MAX_BYTES
    from app.services import people_by_content as pbc
    try:
        parsed = IdentifyIn.model_validate(json.loads(spec))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The request was not understood.") from exc
    if len(parsed.files) > MAX_IDENTIFY_FILES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"A folder can have at most {MAX_IDENTIFY_FILES} files to read. Split it into smaller folders.")
    if len(files) != len(parsed.files):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The files sent do not match the list.")
    too_big = (f"The files of this folder are over {MAX_IDENTIFY_TOTAL_BYTES // (1024 * 1024)} MB together. "
               "Split the folder into smaller folders and read them one by one.")
    if sum(min(upload.size or 0, MAX_BYTES + 1) for upload in files) > MAX_IDENTIFY_TOTAL_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, too_big)
    # Signed in and the request understood: the reading below takes minutes and needs no database, so give the connection (and the
    # transaction the sign-in check opened) back now instead of holding it idle for the whole read.
    await run_in_threadpool(db.close)
    await _take_identify_slot()
    try:
        readable: list[tuple[str, bytes]] = []
        rejected: list[tuple[str, str]] = []
        received = 0
        for item, upload in zip(parsed.files, files):
            data = await upload.read(MAX_BYTES + 1)
            received += len(data)
            if received > MAX_IDENTIFY_TOTAL_BYTES:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, too_big)
            if not item.path.lower().endswith(pbc.SUPPORTED):
                rejected.append((item.path, "Only PDF, Word and image files can be read."))
            elif not data:
                rejected.append((item.path, "The file is empty."))
            elif len(data) > MAX_BYTES:
                rejected.append((item.path, f"The file is over {MAX_BYTES // (1024 * 1024)} MB and was not read."))
            else:
                readable.append((item.path, data))
        stop = threading.Event()

        async def watch() -> None:
            while not stop.is_set():
                if await request.is_disconnected():
                    stop.set()
                    return
                await asyncio.sleep(1.5)

        watcher = asyncio.create_task(watch())
        started = time.perf_counter()
        try:
            result = await run_in_threadpool(lambda: pbc.identify_people(readable, rejected=rejected, should_stop=stop.is_set))
        finally:
            watcher.cancel()
            stop.set()
    finally:
        _IDENTIFY_SLOTS.release()
    logger.info("Identify %s: %d files, %d pages, %d people, %d unplaced rows, %.1fs", parsed.folder, result.totals["files"], result.totals["pages"],
                len(result.people), len(result.unplaced), time.perf_counter() - started)
    answer = result.to_dict()                           # (without the private "_match" identifiers)
    known = await run_in_threadpool(_people_in_crm, db, result.people)
    for key, person in enumerate(answer["people"]):
        if known.get(key):
            person["alreadyInCrm"] = known[key]        # the screen defaults this card to "Skip: already in the CRM as CI-167"
    return {"folder": parsed.folder, **answer}


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
        cut = None
        if item.pages and content_type == "application/pdf":
            # A scan whose pages are different people's: this person gets their own pages only (then split into documents below).
            try:
                data, cut = bulk.cut_pdf_pages(data, item.pages), list(item.pages)
            except ValueError:
                dropped.append({"level": "warning", "message": f"{short} was not imported: its pages for this candidate could not be cut out. Add them from the candidate's profile."})
                continue
        candidates.append({"name": name, "short": short, "data": data, "content_type": content_type, "kinds": kinds, "cut": cut})

    # A PDF whose name says nothing, or names several documents, is read page by page and cut into the documents it holds
    # (each read is slow and they do not depend on each other, so side by side; the same bytes twice are read once). Any other
    # file whose name says nothing is read for what it is.
    def read_scan(entry: dict) -> None:
        if bulk.wants_split(entry["kinds"], entry["content_type"]):
            entry["split"] = bulk.split_by_content(entry["data"], entry["content_type"], entry["kinds"], entry["name"])
            if not entry["split"].split:
                entry["kinds"] = entry["split"].pieces[0].kinds
        elif not entry["kinds"]:
            entry["kinds"] = bulk.classify_content(entry["data"]) or ["other"]

    receiving_done = time.perf_counter()
    reading, first_of = [], {}
    for entry in candidates:
        if bulk.wants_split(entry["kinds"], entry["content_type"]) or not entry["kinds"]:
            digest = hashlib.sha256(entry["data"]).hexdigest()
            if digest not in first_of:
                first_of[digest] = entry
                reading.append(entry)
    if reading:
        with ThreadPoolExecutor(max_workers=min(4, len(reading))) as pool:
            list(pool.map(read_scan, reading))
        for entry in candidates:   # a second copy of a scan that was read takes the same verdict
            original = first_of.get(hashlib.sha256(entry["data"]).hexdigest())
            if original is not None and original is not entry and "split" not in entry and (original.get("split") or not entry["kinds"]):
                entry["kinds"], entry["split"] = original["kinds"], original.get("split")

    sorted_at = time.perf_counter()
    uploads, seen = [], {}
    for entry in candidates:
        kinds, short = entry["kinds"], entry["short"]
        if "photo" in kinds and not entry["content_type"].startswith("image/"):
            dropped.append({"level": "warning", "message": f"{short} was not imported: the photo must be an image."})
            continue
        result = entry.get("split")
        if result is not None and result.note:
            dropped.append({"level": "info", "message": result.note})
        if result is not None and result.split:
            stem = entry["name"].rsplit(".", 1)[0] if "." in entry["name"].rsplit("/", 1)[-1] else entry["name"]
            # pages are numbered in the piece that was read; when the piece was itself cut from a larger scan, in that scan
            cut = entry.get("cut")
            pieces = [(piece.kinds, piece.data, [cut[n - 1] for n in piece.pages] if cut else piece.pages, result.reads_for(piece.pages))
                      for piece in result.pieces]
            path = stem + ".pdf"
        elif entry.get("cut"):
            pieces, path = [(kinds, entry["data"], entry["cut"], None)], entry["name"]
        else:
            whole = result.reads_for(list(range(1, len(result.page_reads) + 1))) if result is not None else None
            pieces, path = [(kinds, entry["data"], None, whole)], entry["name"]
        for piece_kinds, piece_data, piece_pages, piece_facts in pieces:
            digest = hashlib.sha256(piece_data).hexdigest()
            if digest in seen:   # the same file twice ("Medical.pdf" and "Medical_1.pdf"): kept once
                first = seen[digest]
                first.kinds = list(dict.fromkeys(first.kinds + piece_kinds))
                dropped.append({"level": "info", "message": f"{short} is a copy of {(first.original_name or first.path).rsplit('/', 1)[-1]} and was kept once."})
                continue
            uploads.append(bulk.Upload(path=path, kinds=piece_kinds, data=piece_data, content_type=entry["content_type"],
                                       pages=piece_pages, original_name=entry["name"] if piece_pages else None,
                                       facts=piece_facts, source=entry["data"]))
            seen[digest] = uploads[-1]

    # The same physical page filed twice in one kind of document (the passport scan on its own and the same pages inside the picker
    # scan, the signed offer scan ...): keep one copy of each page, note every page left out, keep the scans they came from.
    uploads, repeated = duplicate_pages.dedupe_uploads(uploads, settings=get_settings())
    dropped += [{"level": "info", "message": message} for message in repeated.messages()]

    row = _row_values(parsed.row)
    # The candidate's name comes from the sheet, or from the DOCUMENTS: the person the identify step read (parsed.name with nameFromFiles),
    # else the passport / Aadhaar card / resume read here (the same text the splitting read). Never from a folder or file name: with no
    # name read the candidate is "Name not read yet" and the first reading of a document replaces it.
    identity = bulk.read_identity(uploads, settings=get_settings())
    if row.get("name"):
        name_source = "sheet"
    elif parsed.name and parsed.nameFromFiles:
        row["name"], name_source = parsed.name.strip(), "documents"
    elif identity.name:
        row["name"], name_source = identity.name, "documents"
    else:
        name_source = "pending"
    ctx = bulk.load_context(db, default_order=parsed.defaultOrder, default_recruiter=parsed.defaultRecruiterId,
                            wanted_orders={row.get("order", "")}, only=_own(user), me=user)
    written: list[str] = []
    settings = get_settings()
    saving_at = time.perf_counter()
    try:
        result = bulk.import_candidate(db, user, ctx, folder=parsed.folder, row=row, uploads=uploads, written=written, duplicates=repeated.skipped,
                                     name_source=name_source, identity=identity, import_key=(parsed.importKey or "").strip() or None,
                                     on_duplicate=parsed.duplicateAction)
        saved_at = time.perf_counter()
        if result.get("status") in ("already_imported", "duplicate"):     # nothing was written or numbered: end the transaction (and release the lock)
            db.rollback()
            bulk.discard_written(settings, written)
            result["issues"] = result["issues"] + dropped
            return result
        if result.get("status") == "documents_added":       # only documents were filed, into the existing candidate
            db.commit()
            result["issues"] = result["issues"] + dropped
            return result
        db.commit()
        committed_at = time.perf_counter()
        _start_reading(db, user, result)
        logger.info("Import %s: %d files, receiving and sorting %.1fs (of which reading unnamed files %.1fs), saving %.1fs, commit %.1fs",
                    parsed.folder, len(uploads), saving_at - started, sorted_at - started, saved_at - saving_at, committed_at - saved_at)
        # Numbers only (no names): what the screen could show when an import is slow. ``checkSeconds`` is everything before the
        # candidate is written (receiving, reading unnamed files, sorting, de-duplicating, who they are); ``storeSeconds`` the
        # uploads to the document store; ``saveSeconds`` the rows (statements to the database) and ``commitSeconds`` the commit.
        stored_in = (result.get("timings") or {}).get("storeSeconds", 0.0)
        result["timings"] = {**(result.get("timings") or {}), "checkSeconds": round(saving_at - started, 2),
                             "receiveSeconds": round(receiving_done - started, 2), "readUnnamedSeconds": round(sorted_at - receiving_done, 2),
                             "saveSeconds": round(max(0.0, saved_at - saving_at - stored_in), 2),
                             "commitSeconds": round(committed_at - saved_at, 2), "totalSeconds": round(committed_at - started, 2)}
    except bulk.ImportRefused as refused:
        db.rollback()
        bulk.discard_written(settings, written)
        answer = {"action": "skip", "folder": parsed.folder, "name": refused.plan["name"], "issues": refused.plan["issues"] + dropped}
        if refused.plan.get("alreadyInCrm"):    # the CRM already has this person: the screen shows "Skip: already in the CRM as CI-167"
            known = refused.plan["alreadyInCrm"]
            answer.update(status="duplicate", careId=known["careId"], candidateId=known["candidateId"], matchedBy=known["matchedBy"],
                          strength=known.get("strength", "possible"), alreadyInCrm=known)
        return answer
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


def _start_reading(db: Session, user: User, result: dict) -> None:
    """A candidate that was just imported has its resume, passport and Aadhaar (and education) pages queued for the reader at once,
    so the name and the details fill in without anyone pressing "Read documents". The documents are already saved: a failure here
    never undoes the import, and the "Read documents" button still queues whatever is left."""
    candidate_id = result.get("candidateId")
    if result.get("status") != "imported" or not candidate_id:
        return
    try:
        queued = read_again(ReadAgainIn(candidateIds=[candidate_id]), db, user)
        result["readingQueued"] = int(queued.get("queued", 0))
    except Exception:       # the reader being down or a busy database must not make a saved candidate look unsaved
        logger.warning("Reading was not started for an imported candidate; it can be started with Read documents.")
        try:
            db.rollback()
        except SQLAlchemyError:
            pass


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidateIds: list[str] = Field(max_length=MAX_FOLDERS)


READING_OPEN = ("uploaded", "pending", "processing", "retry")
READING_DONE = ("completed", "attached")


def _may_work(candidate, user: User) -> bool:
    """In-memory twin of `app.core.deps.ensure_can_work_candidate` (same rule, no query): admin and manager may work any candidate,
    anyone else only the candidate they own. Kept equal by a matrix test; a refusal is a `continue` here, never an error."""
    if user.role in (Role.admin, Role.manager):
        return True
    return candidate.owner_id is not None and candidate.owner_id == user.id


def _workable(db: Session, user: User, ids: list[str], *, sources: bool = False) -> list:
    """The candidates among `ids` that `user` may work, in the order asked, with ONE query (a hosted database costs ~300 ms each)."""
    from sqlalchemy import select
    from sqlalchemy.orm import undefer
    from app.db.models import Candidate
    statement = select(Candidate).where(Candidate.id.in_(ids))
    if sources:
        statement = statement.options(undefer(Candidate.field_sources))
    position = {key: index for index, key in enumerate(ids)}
    return sorted((c for c in db.scalars(statement) if _may_work(c, user)), key=lambda c: position[c.id])


@router.post("/status")
def status_of(body: StatusIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """What an imported candidate looks like now: the name (read from the documents once they are read), how each document was
    filed, how far the reading of the documents is, and the details found. The Import screen polls it after an import.
    Three queries however many candidates (the candidates, their reading jobs counted, their documents)."""
    from sqlalchemy import func, select
    from app.db.models import Attachment, DocumentExtractionJob
    from app.services import form_extraction as fx
    from app.services.parallel_reads import parallel_reads
    asked = list(dict.fromkeys(body.candidateIds))
    if not asked:
        return {"items": []}
    # The candidates, their reading jobs counted and their documents are three independent reads (the screen polls this): one round trip
    # together. Jobs and documents are read for every id asked and only the workable candidates' are used.
    candidates, job_counts, stored = parallel_reads(
        db,
        lambda session: _workable(session, user, asked, sources=True),
        lambda session: session.execute(
            select(DocumentExtractionJob.candidate_id, DocumentExtractionJob.status, func.count())
            .where(DocumentExtractionJob.candidate_id.in_(asked)).group_by(DocumentExtractionJob.candidate_id, DocumentExtractionJob.status)).all(),
        lambda session: list(session.scalars(select(Attachment).where(Attachment.entity_type == "candidate", Attachment.entity_id.in_(asked))
                                             .order_by(Attachment.version))),
    )
    if not candidates:
        return {"items": []}
    wanted = {candidate.id for candidate in candidates}
    counts: dict[str, dict[str, int]] = {}
    for candidate_id, job_status, count in job_counts:
        if candidate_id in wanted:
            counts.setdefault(candidate_id, {})[job_status] = count
    latest: dict[str, dict[str, Attachment]] = {}
    for attachment in stored:
        if attachment.entity_id in wanted:
            latest.setdefault(attachment.entity_id, {})[attachment.slot] = attachment      # the highest version of each kind is the current one
    items = []
    for candidate in candidates:
        jobs = counts.get(candidate.id, {})
        sources = fx.sources_of(candidate)
        name_source = ("documents" if sources.get("personal.candidateName") == "extracted"
                       else "typed" if sources.get("personal.candidateName") == "manual"
                       else ({"files": "files", "documents": "documents", "pending": "pending"}.get(sources.get(fx.PROVISIONAL_NAME), "folder")
                             if fx.provisional_name(candidate) else "sheet"))
        items.append({
            "candidateId": candidate.id, "name": candidate.name, "careId": candidate.care_id, "ref": candidate.ref,
            "nameSource": name_source, "needsReview": bool(candidate.needs_review), "phone": candidate.phone or None,
            "documents": [{"slot": slot, "label": bulk.KIND_LABELS.get(slot, slot), "fileName": row.file_name, "versions": row.version}
                          for slot, row in latest.get(candidate.id, {}).items()],
            "reading": {"total": sum(jobs.values()), "open": sum(jobs.get(key, 0) for key in READING_OPEN),
                        "done": sum(jobs.get(key, 0) for key in READING_DONE), "review": jobs.get("needs_review", 0),
                        "failed": jobs.get("failed", 0)},
            "detailsFound": len(fx.public_sources(sources)),
            "experience": {"total": candidate.total_experience, "india": candidate.experience_india, "abroad": candidate.gulf_experience},
        })
    return {"items": items}


@router.post("/read-again")
def read_again(body: ReadAgainIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Read the imported documents: the passport, Aadhaar card, resume and education certificates of each of these candidates that nobody has read yet are
    put in the queue, and a reading that failed (the reading service was down, a timeout) is queued again. Importing only stores.
    Three queries find what to do, however many candidates (the candidates, their documents, their reading jobs)."""
    from sqlalchemy import select
    from app.db.models import Attachment, DocumentExtractionJob
    from app.services.application_form import requeue_failed
    candidates = _workable(db, user, list(dict.fromkeys(body.candidateIds)))
    queued = 0
    readable = 0        # stored files that can be read (resume, passport, Aadhaar, education certificate), read or not: tells "nothing to read" from "all read"
    if candidates:
        wanted = [candidate.id for candidate in candidates]
        by_slot: dict[tuple[str, str], list[Attachment]] = {}       # newest version first
        for attachment in db.scalars(select(Attachment).where(
                Attachment.entity_type == "candidate", Attachment.entity_id.in_(wanted), Attachment.slot.in_(bulk.SENT_TO_READER))
                .order_by(Attachment.version.desc())):
            by_slot.setdefault((attachment.entity_id, attachment.slot), []).append(attachment)
        keys = {attachment.object_key for rows in by_slot.values() for attachment in rows}
        newest_job: dict[tuple[str, str], tuple[str, str | None]] = {}      # (candidate, stored file) -> (status, attachment) of its latest job
        if keys:
            for candidate_id, object_key, job_status, attachment_id in db.execute(
                    select(DocumentExtractionJob.candidate_id, DocumentExtractionJob.object_key, DocumentExtractionJob.status,
                           DocumentExtractionJob.attachment_id)
                    .where(DocumentExtractionJob.candidate_id.in_(wanted), DocumentExtractionJob.object_key.in_(keys))
                    .order_by(DocumentExtractionJob.created_at.desc())):
                newest_job.setdefault((candidate_id, object_key), (job_status, attachment_id))
        for candidate in candidates:
            unread: dict[str, list[Attachment]] = {}
            seen: set[str] = set()
            for slot in bulk.SENT_TO_READER:
                versions = by_slot.get((candidate.id, slot))
                if not versions:
                    continue
                latest = versions[0]
                # Every file stored together with the latest one (an import stores a passport's data page and back page as two versions of
                # the slot in one go) is read; a version replaced by a later, separate upload is not.
                batch = [latest] + [row for row in versions[1:] if row.uploaded_at == latest.uploaded_at]
                for attachment in batch:
                    if attachment.object_key in seen:
                        continue        # one stored file may sit in several slots (a scan of passport + Aadhaar + resume): it is read once
                    seen.add(attachment.object_key)
                    readable += 1
                    job = newest_job.get((candidate.id, attachment.object_key))
                    if job is None:
                        unread.setdefault(slot, []).append(attachment)
                    elif job[0] == "failed" and requeue_failed(db, candidate.id, job[1] or attachment.id):
                        queued += 1
            if unread:
                bulk.queue_reads(db, user, candidate, unread)
                queued += sum(len(items) for items in unread.values())
    db.commit()
    return {"queued": queued, "readable": readable}
