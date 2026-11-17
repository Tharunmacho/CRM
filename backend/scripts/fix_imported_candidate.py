"""Give a bulk-imported candidate their real name and standard file names, in place (no re-import).

An import made before the naming fix keeps the FOLDER name as the candidate's name ("Gunavelan-alnoor") and the original file
names. This script takes the real name from the documents already read for the candidate (passport, then Aadhaar card, then
resume; or `--name` for one candidate), and renames their documents to "<Name>-<Document>.<ext>" (for example
"Gunavelan_R-Passport.pdf"): the candidate's own copies and the stage copies of the same file alike, and the small file
reference ({"id", "name", ...}) the stages and the saved draft keep, so the screens show the new names too.

    python scripts/fix_imported_candidate.py --candidate CI-156                       # dry run: shows what would change
    python scripts/fix_imported_candidate.py --candidate CI-156 --apply --allow-remote

* Only a candidate that came from the bulk import ("Bulk import" source) is touched; anyone else is refused (exit 1).
* The candidate's NAME is changed only when it is plausibly a stand-in made from the import FOLDER, or when `--name` is given.
  The name is a stand-in when the name has no field source yet (nobody typed it and no reader gave it) AND one of these holds:
    1. the candidate is marked as provisionally named (field_sources "_provisionalName", set by the current import), or
    2. the name looks like a folder name: it contains a hyphen, an underscore, a digit or the word "alnoor", in any case
       (the old import kept the folder name as it was, for example "Gunavelan-alnoor", "rashid khan-alnoor", "01 - Rahul").
  Any other name is KEPT and the dry run says so loudly ("name kept: it looks like a name the office or the reader gave; use
  --name to change it"): a name typed by a person (field source "manual"), a name already read from a document (field source
  "extracted": a resume spelling is not replaced by the passport spelling), and a plain name that came with the import sheet
  (no field source, nothing folder-like about it, for example "Rahul Kumar"). The files then take the name that was kept.
  A folder name without any of the markers above cannot be told from a sheet name; give `--name` for that candidate.
* A candidate whose name is a stand-in but whose documents give no readable name (and no `--name`) is left completely alone:
  neither the name nor the files change.
* A file already named "<Name>-<Document>.<ext>" for the name in use is left as it is, so a second run changes nothing.
  Every other imported document (also an original name such as "scan 12.pdf") is renamed: that is deliberate, and the dry run
  lists every old -> new name. Names are at most 150 characters with the extension kept (a long name is shortened, not the
  extension). Files the import did not make are never renamed: the original upload kept as received (slot "source_bundle",
  its original file name is the evidence), a stage file added by hand, unknown slots.
* With --apply the candidate's applications and stage records are locked (SELECT ... FOR UPDATE, after the candidate row) before
  their saved file names are rewritten, so a recruiter saving a stage at that moment waits and nothing is lost.
* One audit entry per candidate records the old name and the old file names, so the change can be reversed by hand.
* Nothing is deleted, merged or re-uploaded; the stored bytes are never touched.
* The database is whatever CRM_ENV_FILE (or backend/.env) points at; the shared one needs `--allow-remote`.

Exit codes: 0 done (or dry run), 1 candidate not found / refused, 2 refused by the guard or a bad option, 3 a candidate failed
(each candidate is its own transaction; the others continue).
"""
from __future__ import annotations

import argparse
import itertools
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.orm import undefer  # noqa: E402

from app.api.routers.files import _safe_name  # noqa: E402
from app.db.models import Application, ApplicationStage, Attachment, AuditEntry, Candidate  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services import bulk_candidate_import as bulk  # noqa: E402
from app.services import form_extraction as fx  # noqa: E402
from import_travelled_candidates import shared_database_reason  # noqa: E402

AUDIT_ACTION = "Renamed imported candidate and files"
NAME_ORDER = ("passport", "aadhaar", "resume")   # who is believed about a name, most trusted first
NAME_KEY = fx.SPECS["name"].key                  # "personal.candidateName"
# Documents the import stores for a candidate; the stage copies are found by the stage slot they sit in. "source_bundle" (the
# original upload kept as received) is deliberately NOT here: it is not produced by the import and its file name is the evidence.
CANDIDATE_KINDS = frozenset(bulk.CANDIDATE_SLOTS) | {"aadhaar"}
MAX_NAME = 150                                   # the longest stored file name (app.api.routers.files._safe_name)
NUMBER_ROOM = 4                                  # kept free in a name for a "-2" ... "-99" counter
# What a folder-made stand-in name carries (see the module docstring): a hyphen, an underscore, a digit or the client's name
# (the marker word below is the name of the client the old folders were named after; replace it for another client).
FOLDER_NAME_MARKERS = re.compile(r"[-_0-9]|alnoor", re.IGNORECASE)
STAGE_SLOT_KIND = {f"stage_{stage}_{field}": kind for kind, (stage, field) in bulk.STAGE_FILES.items()}
KIND_ORDER = {kind: index for index, kind in enumerate(bulk.KIND_LABELS)}


class Totals:
    """What was (or would be) changed: candidates, file names, stage file references."""

    def __init__(self, candidates: int = 0, files: int = 0, references: int = 0):
        self.candidates, self.files, self.references = candidates, files, references


def clean_name(value) -> str | None:
    text = " ".join(str(value or "").split())[:200]
    return text if text and text != fx.UNNAMED else None


def name_from_documents(candidate: Candidate) -> tuple[str | None, str | None]:
    """(name, the kind of document that gave it) from what was already read for this candidate."""
    for kind in NAME_ORDER:
        for raw in fx.stored_documents(candidate.ocr_data, kind):
            name = clean_name((fx.values_for(kind, raw) or {}).get("name"))
            if name:
                return name, kind
    return None, None


def looks_like_folder_name(name: str) -> bool:
    """True when a name carries what a folder name carries: a hyphen, an underscore, a digit or "alnoor" (any case)."""
    return bool(FOLDER_NAME_MARKERS.search(name or ""))


def fit_name(raw: str) -> str:
    """A safe stored file name of at most MAX_NAME characters. A longer one loses the end of its STEM, never its extension."""
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
    extension = PurePosixPath(raw).suffix
    if len(extension) > 16:
        extension = ""
    stem = raw[: len(raw) - len(extension)] if extension else raw
    if len(stem) + len(extension) > MAX_NAME:        # cut the stem BEFORE _safe_name, which would cut the end (the extension) itself
        stem = stem[: max(1, MAX_NAME - len(extension))].rstrip("-_. ")
    return _safe_name(stem + extension)


def standard_name(person: str, kinds: list[str], original: str, taken: set[str]) -> str:
    """`bulk.import_file_name`, kept within MAX_NAME characters: the person's part is shortened first (room is left for a counter),
    then, if the document labels alone are too long, the stem; the extension is never cut and the result stays unique in `taken`."""
    shown = person
    while len(shown) > 1:
        probe = bulk.import_file_name(shown, kinds, original, set())
        if len(probe) <= MAX_NAME - NUMBER_ROOM:
            break
        shown = shown[: max(1, len(shown) - max(1, len(probe) - (MAX_NAME - NUMBER_ROOM)))]
    result = bulk.import_file_name(shown, kinds, original, taken)
    if len(result) <= MAX_NAME:
        return _safe_name(result)
    taken.discard(result.lower())                                  # the labels alone are too long: shorten the stem
    shortened = fit_name(result)
    extension = PurePosixPath(shortened).suffix
    stem, number = shortened[: len(shortened) - len(extension)], 1
    result = shortened
    while result.lower() in taken:
        number += 1
        result = f"{stem[: MAX_NAME - len(extension) - len(str(number)) - 1]}-{number}{extension}"
    taken.add(result.lower())
    return result


def _label(kind: str) -> str:
    return bulk.name_slug(bulk.KIND_LABELS.get(kind, kind))


def follows_pattern(file_name: str, slug: str, kinds: list[str]) -> bool:
    """True when the name is already "<slug>-<these documents>[-n].<ext>" (the documents in any order)."""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    prefix = f"{slug}-".lower()
    if not stem.lower().startswith(prefix):
        return False
    rest = re.sub(r"-\d+$", "", stem[len(prefix):]).lower()
    labels = [_label(kind).lower() for kind in kinds]
    orders = itertools.permutations(labels) if len(labels) <= 5 else [tuple(labels)]
    return any(rest == "_".join(order) for order in orders)


def plan_files(db, candidate: Candidate, name: str) -> tuple[list[tuple[Attachment, str]], list[Attachment]]:
    """([(attachment, new file name)], [attachments left alone]) for the candidate.

    One stored file that counts as several documents ("signed offer, passport, aadhaar") gets one name carrying all of them, shared
    by every copy of it. The names are those the import itself would give, so a second file of one kind gets "-2"."""
    application_ids = list(db.scalars(select(Application.id).where(Application.candidate_id == candidate.id)))
    conditions = [(Attachment.entity_type == "candidate") & (Attachment.entity_id == candidate.id)]
    if application_ids:
        conditions.append((Attachment.entity_type == "application") & (Attachment.entity_id.in_(application_ids)))
    rows = list(db.scalars(select(Attachment).where(or_(*conditions)).order_by(Attachment.uploaded_at, Attachment.version, Attachment.id)))
    by_file: dict[str, list[Attachment]] = {}
    ignored: list[Attachment] = []
    for row in rows:
        known = (row.slot in CANDIDATE_KINDS) if row.entity_type == "candidate" else (row.slot in STAGE_SLOT_KIND)
        if known:
            by_file.setdefault(row.object_key, []).append(row)
        else:
            ignored.append(row)
    slug = bulk.name_slug(name)
    groups: list[tuple[list[str], list[Attachment]]] = []
    for group in by_file.values():
        kinds = [row.slot for row in group if row.entity_type == "candidate"] or [STAGE_SLOT_KIND[row.slot] for row in group]
        kinds = sorted(dict.fromkeys(kinds), key=lambda kind: (KIND_ORDER.get(kind, 999), kind))
        groups.append((kinds, group))
    # The import's own order: a scan of several documents first, then the files in the order they were stored.
    groups.sort(key=lambda item: (len(item[0]) == 1, min(row.version for row in item[1]), min(row.uploaded_at for row in item[1]),
                                  min(row.id for row in item[1])))
    old_slug = bulk.name_slug(candidate.name)
    taken: set[str] = set()
    chosen: dict[int, str] = {}
    for index, (kinds, group) in enumerate(groups):          # already "<new name>-<documents>": stays as it is
        current = group[0].file_name
        if len({row.file_name for row in group}) == 1 and follows_pattern(current, slug, kinds) and current.lower() not in taken:
            taken.add(current.lower())
            chosen[index] = current
    for index, (kinds, group) in enumerate(groups):          # "<old name>-<documents>": only the name part changes
        current = group[0].file_name
        if index in chosen or len({row.file_name for row in group}) != 1 or not follows_pattern(current, old_slug, kinds):
            continue
        carried = f"{slug}-{current[len(old_slug) + 1:]}"
        if len(carried) > MAX_NAME:                           # too long to carry over: the standard name below shortens the person's part
            continue
        carried = _safe_name(carried)
        if carried.lower() not in taken:
            taken.add(carried.lower())
            chosen[index] = carried
    plan: list[tuple[Attachment, str]] = []
    for index, (kinds, group) in enumerate(groups):          # anything else (an original name): the standard name
        new_name = chosen[index] if index in chosen else standard_name(name, kinds, group[0].file_name, taken)
        plan.extend((row, new_name) for row in group)
    return plan, ignored


def follow(value, names: dict[str, str], hits: list[int]):
    """A copy of a stage's JSON in which every file reference {"id", "name", ...} of a renamed attachment carries its new name
    (the same idea as `follow` in app.services.application_form.rename_imported_files)."""
    if isinstance(value, dict):
        changed = {key: follow(item, names, hits) for key, item in value.items()}
        file_id = value.get("id")
        if isinstance(file_id, str) and file_id in names and "name" in value and value["name"] != names[file_id]:
            changed["name"] = names[file_id]
            hits.append(1)
        return changed
    if isinstance(value, list):
        return [follow(item, names, hits) for item in value]
    return value


def stage_references(db, candidate: Candidate, names: dict[str, str], *, lock: bool = False) -> list[tuple[object, str, object, int]]:
    """[(row, attribute, updated JSON, hits)] for every stage payload / saved draft of the candidate that holds an out-of-date file name.

    With `lock` (--apply) the candidate's applications and then their stage records are read with SELECT ... FOR UPDATE (the
    candidate row is already locked; this is the app's own order: candidate, application, stages) and refreshed from the database,
    so a stage or draft a recruiter saved a moment ago is the one rewritten, and a save that arrives now waits for our commit."""
    application_ids = list(db.scalars(select(Application.id).where(Application.candidate_id == candidate.id)))
    if not names or not application_ids:
        return []

    def locked(statement, model):
        statement = statement.order_by(model.id)
        return db.scalars(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    out: list[tuple[object, str, object, int]] = []
    applications = list(locked(select(Application).where(Application.id.in_(application_ids)), Application))
    for stage in locked(select(ApplicationStage).where(ApplicationStage.application_id.in_(application_ids)), ApplicationStage):
        hits: list[int] = []
        updated = follow(stage.data, names, hits)
        if hits:
            out.append((stage, "data", updated, len(hits)))
    for application in applications:
        hits = []
        updated = follow(application.draft, names, hits)
        if hits:
            out.append((application, "draft", updated, len(hits)))
    return out


def decide_name(candidate: Candidate, forced: str | None) -> tuple[str | None, str, str | None]:
    """(the name to use or None, where it comes from, "manual" | "extracted" | None for the field source).

    A field source of None with a name means the name is KEPT as it is (the files take it). The name is replaced by the one in the
    documents only when it is a folder stand-in (see the module docstring): provisional, or folder-like with no field source."""
    if forced:
        return forced, "--name", "manual"
    current = clean_name(candidate.name)
    source = fx.sources_of(candidate).get(NAME_KEY)
    if current and source == "manual":
        return current, "typed by a person (kept)", None
    if current and source == "extracted":
        return current, "already read from a document (kept)", None
    if current and not (fx.provisional_name(candidate) or looks_like_folder_name(current)):
        return current, "a name the office or the reader gave (kept)", None
    name, kind = name_from_documents(candidate)
    return (name, f"the {kind} document", "extracted") if name else (None, "", None)


def set_name(candidate: Candidate, name: str, source: str | None, from_kind: str | None) -> None:
    candidate.name = name
    if source == "manual":
        fx.mark_manual(candidate, ["name"])
    sources = fx.sources_of(candidate)
    if source == "extracted":
        sources[NAME_KEY] = "extracted"
        for key, kind in (("_fromPassport", "passport"), ("_fromAadhaar", "aadhaar")):   # as a read would remember who gave it
            holders = set(sources.get(key) or [])
            holders.discard(NAME_KEY)
            if from_kind == kind:
                holders.add(NAME_KEY)
            if holders:
                sources[key] = sorted(holders)
            else:
                sources.pop(key, None)
    sources.pop(fx.PROVISIONAL_NAME, None)
    candidate.field_sources = sources


def fix_one(db, ident: str, args) -> tuple[int, Totals]:
    """Plan (and with --apply, write) one candidate. Returns (exit code, what changed)."""
    totals = Totals()
    statement = (select(Candidate).where(or_(Candidate.care_id == ident, Candidate.ref == ident))
                 .options(undefer(Candidate.ocr_data), undefer(Candidate.field_sources)))
    candidate = db.scalar(statement.with_for_update() if args.apply else statement)
    print(f"\n== {ident}")
    if candidate is None:
        print("   not found")
        return 1, totals
    if (candidate.source_detail or "") != "Bulk import":
        print(f"   refused: '{candidate.name}' is not a bulk-imported candidate (source: {candidate.source_detail or candidate.source}).")
        return 1, totals
    name, why, source = decide_name(candidate, args.name)
    if not name:
        print(f"   no name could be read from the documents, so nothing changes (name stays '{candidate.name}'). Use --name to set one.")
        return 0, totals
    from_kind = why.split()[1] if source == "extracted" else None
    kept = source is None                      # the name stays exactly as stored; the files take it
    plan, ignored = plan_files(db, candidate, name)
    renames = [(attachment, new) for attachment, new in plan if attachment.file_name != new]
    references = stage_references(db, candidate, {attachment.id: new for attachment, new in renames}, lock=args.apply)
    name_changes = not kept and name != candidate.name
    print(f"   name: '{candidate.name}'  ->  '{name}'   (from {why})" if name_changes else f"   name: '{candidate.name}' (unchanged; {why})")
    if kept:
        read_name, read_kind = name_from_documents(candidate)
        differs = f" (the {read_kind} document says '{read_name}')" if read_name and read_name != name else ""
        print(f"   name kept: it looks like a name the office or the reader gave; use --name to change it{differs}")
    for attachment, new in plan:
        where = f"{attachment.entity_type}/{attachment.slot or '-'}"
        print(f"   {where:<34} {attachment.file_name}  ->  {new}" if attachment.file_name != new else f"   {where:<34} {attachment.file_name}  (unchanged)")
    for attachment in ignored:
        print(f"   {attachment.entity_type + '/' + (attachment.slot or '-'):<34} {attachment.file_name}  (left as it is: not an imported document)")
    refs = sum(item[3] for item in references)
    print(f"   {'to change' if not args.apply else 'changing'}: {'name, ' if name_changes else ''}{len(renames)} file name(s), {refs} stage file reference(s)")
    if not (name_changes or renames or references):
        print("   nothing to do")
        return 0, totals
    totals.candidates, totals.files, totals.references = 1, len(renames), refs
    if not args.apply:
        return 0, totals
    old_name = candidate.name
    old_files = {attachment.id: attachment.file_name for attachment, _ in renames}
    if name_changes:
        set_name(candidate, name, source, from_kind)
    for attachment, new in renames:
        attachment.file_name = new
    for row, attribute, updated, _ in references:
        setattr(row, attribute, updated)
    db.add(AuditEntry(actor_id=None, action=AUDIT_ACTION, target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"old_name": old_name, "new_name": candidate.name, "name_from": why,
                            "old_file_names": old_files, "new_file_names": {attachment.id: new for attachment, new in renames},
                            "stage_references_updated": refs}))
    db.commit()
    print("   done")
    return 0, totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", nargs="+", required=True, help="CI-156 style IDs (or CND-00123 references)")
    parser.add_argument("--name", help="use this name instead of reading it from the documents (one candidate only)")
    parser.add_argument("--apply", action="store_true", help="write the change (without it, only show it)")
    parser.add_argument("--allow-remote", action="store_true", help="allow the shared database")
    args = parser.parse_args(argv)
    idents = list(dict.fromkeys(item.strip().upper() for item in args.candidate if item.strip()))
    if not idents:
        print("Give at least one candidate.", file=sys.stderr)
        return 2
    if args.name is not None:
        args.name = clean_name(args.name)
        if not args.name:
            print("--name is empty.", file=sys.stderr)
            return 2
        if len(idents) != 1:
            print("--name can only be used with one candidate.", file=sys.stderr)
            return 2
    why = shared_database_reason()
    if why and not args.allow_remote:
        print(f"Refused: {why} Add --allow-remote on purpose to use it.", file=sys.stderr)
        return 2
    print(f"Database: {engine.url.render_as_string(hide_password=True)}  |  {'WRITING' if args.apply else 'dry run (nothing is written)'}")

    code, total, failed = 0, Totals(), []
    with SessionLocal() as db:
        for ident in idents:
            try:
                result, changed = fix_one(db, ident, args)
            except Exception as error:   # one candidate failing must not stop the others
                db.rollback()
                print(f"   FAILED: {error}", file=sys.stderr)
                failed.append(ident)
                code = max(code, 3)
                continue
            if not args.apply:
                db.rollback()            # a dry run holds nothing open
            code = max(code, result)
            total.candidates += changed.candidates
            total.files += changed.files
            total.references += changed.references
    verb = "Changed" if args.apply else "Would change"
    print(f"\n{verb} {total.candidates} candidate(s): {total.files} file name(s), {total.references} stage file reference(s)."
          + ("" if args.apply else " Dry run: nothing was written. Add --apply to write."))
    if failed:
        print(f"FAILED (nothing written for them): {', '.join(failed)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
