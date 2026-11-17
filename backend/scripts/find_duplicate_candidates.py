"""Find candidates that exist more than once (the same folder imported several times while testing): READ-ONLY.

    python scripts/find_duplicate_candidates.py                  # list the groups, say which one to keep
    python scripts/find_duplicate_candidates.py --json           # the same, machine readable

Candidates are grouped when they share a passport number, a phone number, an email, or stored file contents (the same file hash on
two candidates; one shared file such as a group air ticket is not enough: two or more distinct files, or half of the smaller
candidate's files). For every group it prints the Care India IDs, names, when each was created, how many documents and how many
details read from the documents each has, which one to KEEP (the most details read, then the most documents, then the latest
Care India ID) and, for the others, the delete command in its dry-run flavour. It NEVER deletes or changes anything itself.

Note: scripts/delete_candidate_permanently.py is hard-wired to one reference and refuses every other; the printed commands show
the intended call and what that script says for it (use the CRM's own delete screen, or have the script's allowed reference changed
deliberately, for the others).

The database is whatever the app settings say (backend/.env, or CRM_ENV_FILE) unless --database-url is given. The same guard as
scripts/delete_candidate_permanently.py applies (a read still connects): backend/.env is the SSH tunnel to the SHARED database, so
unless CRM_ENV_FILE names another file, or the database is on this computer and not on the tunnel's port, the script stops before it
connects, unless `--allow-remote` is given on purpose. Exit code 0 = done, 2 = refused by the guard, 3 = failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session, undefer  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.models import Attachment, Candidate  # noqa: E402

TUNNEL_FILE = Path(__file__).resolve().parents[1] / ".db-tunnel.json"


def out(text: str = "") -> None:
    print(text, flush=True)


def shared_reason(url) -> str | None:
    host = url.host or ""
    if host not in {"127.0.0.1", "localhost", "::1", ""}:
        return f"the database is on '{host}', not on this computer."
    try:
        port = int(json.loads(TUNNEL_FILE.read_text(encoding="utf-8")).get("local_port") or 0) if TUNNEL_FILE.exists() else 0
    except (OSError, ValueError, TypeError, AttributeError):
        port = 0
    if port and url.port == port:
        return f"the database is on port {port}, the SSH tunnel to the shared database ({TUNNEL_FILE.name})."
    return None


def digits(value) -> str:
    return re.sub(r"\D", "", value or "")


def phone_key(value) -> str | None:
    number = digits(value)
    return number[-10:] if len(number) >= 10 else None


def care_number(candidate) -> int:
    match = re.fullmatch(r"CI-(\d+)", candidate.care_id or "")
    return int(match.group(1)) if match else 0


def details_of(candidate) -> int:
    from app.services import form_extraction as fx
    try:
        return len(fx.public_sources(candidate.field_sources))
    except Exception:
        return 0


def find_groups(candidates: list, attachments: list[tuple[str, str, str]]) -> list[dict]:
    """Groups of candidates that are one person. `attachments` are (candidate id, slot, content hash)."""
    parent = {c.id: c.id for c in candidates}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    reasons: dict[str, set[str]] = defaultdict(set)

    def join(first: str, second: str, why: str) -> None:
        a, b = find(first), find(second)
        if a != b:
            parent[b] = a
        reasons[first].add(why)
        reasons[second].add(why)

    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in candidates:
        for label, value in (("passport", (c.passport_no or "").replace(" ", "").upper() or None), ("phone", phone_key(c.phone)),
                             ("email", (c.email or "").strip().lower() or None)):
            if value:
                by_key[(label, value)].append(c.id)
    for (label, _), ids in by_key.items():
        for other in ids[1:]:
            join(ids[0], other, label)
    hashes: dict[str, set[str]] = defaultdict(set)
    holders: dict[str, set[str]] = defaultdict(set)
    for owner, _, digest in attachments:
        if digest:
            hashes[owner].add(digest)
            holders[digest].add(owner)
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for digest, owners in holders.items():
        ordered = sorted(owners)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                pairs[(first, second)] += 1
    for (first, second), shared in pairs.items():
        smaller = min(len(hashes[first]), len(hashes[second]))
        if shared >= 2 or (smaller and shared * 2 >= smaller):
            join(first, second, "same files")
    members: dict[str, list] = defaultdict(list)
    for c in candidates:
        members[find(c.id)].append(c)
    documents: dict[str, int] = defaultdict(int)
    for owner, _, _ in attachments:
        documents[owner] += 1
    groups = []
    for people in members.values():
        if len(people) < 2:
            continue
        ranked = sorted(people, key=lambda c: (details_of(c), documents[c.id], care_number(c)), reverse=True)
        groups.append({"keep": ranked[0].id, "reasons": sorted(set().union(*(reasons[c.id] for c in people))),
                       "members": [{"id": c.id, "careId": c.care_id, "ref": c.ref, "name": c.name, "created": c.created_at.isoformat() if c.created_at else None,
                                    "documents": documents[c.id], "detailsRead": details_of(c)} for c in sorted(people, key=care_number)]})
    return sorted(groups, key=lambda g: g["members"][0]["careId"] or "")


def run(engine, args) -> int:
    with Session(engine) as db:
        candidates = list(db.scalars(select(Candidate).options(undefer(Candidate.field_sources))))
        attachments = [(owner, slot, digest) for owner, slot, digest in db.execute(
            select(Attachment.entity_id, Attachment.slot, Attachment.content_hash).where(Attachment.entity_type == "candidate"))]
    groups = find_groups(candidates, attachments)
    if args.json:
        out(json.dumps({"groups": groups}, indent=2))
        return 0
    out(f"{len(candidates)} candidates read; {len(groups)} group(s) of the same person.")
    for number, group in enumerate(groups, 1):
        out("")
        out(f"Group {number}: matched by {', '.join(group['reasons'])}")
        for member in group["members"]:
            mark = "KEEP  " if member["id"] == group["keep"] else "delete"
            out(f"  {mark} {member['careId'] or '-':8} {member['ref']:10} {member['name'] or '-':32} created {member['created'] or '-'} "
                f"documents {member['documents']:3} details read {member['detailsRead']:3}")
        for member in group["members"]:
            if member["id"] != group["keep"]:
                out(f"    python scripts/delete_candidate_permanently.py --candidate {member['ref']}        # dry run: shows what would go")
    out("")
    out("Nothing was changed. (The commands above are dry runs; delete_candidate_permanently.py only accepts the one reference "
        "written in it, so any other candidate is refused there: use the CRM's own delete, or change its allowed reference on purpose.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the groups as JSON")
    parser.add_argument("--database-url", help="override the database from the app settings (backend/.env)")
    parser.add_argument("--allow-remote", action="store_true", help="allow the shared database (backend/.env is the tunnel to it); also for this read-only run")
    args = parser.parse_args(argv)
    url = make_url(args.database_url) if args.database_url else None
    if url is None and not os.environ.get("CRM_ENV_FILE"):
        why = "no CRM_ENV_FILE was set, so this would use backend/.env (the shared database)."
    else:
        why = shared_reason(url or make_url(get_settings().database_url))
    if why and not args.allow_remote:
        print(f"Refused: {why} Add --allow-remote on purpose to use it.", file=sys.stderr)
        return 2
    url = url or make_url(get_settings().database_url)
    if not args.json:
        out(f"Database: {url.host or 'local socket'}:{url.port or 5432}/{url.database} (read-only)")
    engine = create_engine(url, connect_args={"connect_timeout": 10}, future=True)
    try:
        return run(engine, args)
    except Exception as error:  # noqa: BLE001
        print(f"FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
