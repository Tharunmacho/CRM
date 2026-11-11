"""Import candidates who have already travelled: a folder per candidate plus one `_placements.json`.

    python scripts/import_travelled_candidates.py --folder M:/crm/candidates_organized --user-email you@example.com          # dry run
    python scripts/import_travelled_candidates.py --folder ... --user-email ... --apply                                   # write

`_placements.json` is a list of {folder, client, country, job, travel_date|null, airline, pnr, agency?} (one per candidate folder).
It creates (or reuses) the client, its employer company, the job title and the order, then imports each candidate through
exactly the code the "Import candidates" screen uses (`app.services.bulk_candidate_import`) in "already travelled" mode, so the
stages, documents, reading queue and audit trail are the ones the CRM itself would have written.

Safe to run twice: a candidate whose name (or phone / email / passport) is already in the CRM is skipped, never doubled.
The database is whatever `CRM_ENV_FILE` (or backend/.env) points at. A database that is not on this computer is refused unless
`--allow-remote` is given, so a local rehearsal can never touch the shared one by accident.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.db.models import Application, ApplicationStatus, Candidate, Client, Company, Designation, Order, OrderStatus, Role, User  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services import bulk_candidate_import as bulk  # noqa: E402

CURRENCY = {"UAE": "AED", "Kuwait": "KWD", "Malaysia": "MYR"}
LABEL_KIND = {
    "resume": ["resume"], "interview packet with resume": ["resume"], "passport": ["passport"], "photo": ["photo"],
    "education certificate": ["education_certificate"], "experience certificate": ["experience_certificate"],
    "signed offer": ["signed_offer"], "medical": ["medical"], "pcc": ["pcc"], "visa": ["visa"], "visa stamping": ["visa_stamping"],
    "emigration": ["emigration"], "air ticket": ["air_ticket"], "air ticket (group booking)": ["air_ticket"],
    "google review": ["google_review"], "aadhaar": ["aadhaar"],
    "self declaration": ["other"], "payslip": ["other"], "insurance": ["other"], "other": ["other"],
}


def shared_database_reason() -> str | None:
    """Why the configured database must be treated as the shared one (None when it is a plain local database).

    127.0.0.1 alone proves nothing: backend/.env reaches the shared database through an SSH tunnel on a local port. So the
    database also counts as shared when it is on the tunnel's port, or when no separate CRM_ENV_FILE was chosen (backend/.env).
    """
    host = engine.url.host or ""
    if host not in {"127.0.0.1", "localhost", "::1", ""}:
        return f"the database is on '{host}', not on this computer."
    tunnel = Path(__file__).resolve().parents[1] / ".db-tunnel.json"
    try:
        port = json.loads(tunnel.read_text(encoding="utf-8")).get("local_port") if tunnel.exists() else None
    except (OSError, ValueError):
        port = None
    if port and engine.url.port == int(port):
        return f"the database is on port {port}, the SSH tunnel to the shared database (backend/.db-tunnel.json)."
    if not os.environ.get("CRM_ENV_FILE"):
        return "no CRM_ENV_FILE was set, so this would use backend/.env (the shared database)."
    return None


def kinds_from_name(person: str, filename: str) -> list[str]:
    """The organized folder names every file `<Person>-<Document>[ n].<ext>`; "A + B" means one scan holding both."""
    stem = Path(filename).stem
    label = stem[len(person) + 1:] if stem.startswith(person + "-") else stem
    label = re.sub(r"\s+\d+$", "", label).strip().lower()
    kinds: list[str] = []
    for part in label.split(" + "):
        kinds += LABEL_KIND.get(part.strip(), [])
    return list(dict.fromkeys(kinds)) or bulk.classify_path_kinds(filename) or ["other"]


def master_rows(db, user: User, placements: list[dict], apply: bool) -> dict[tuple[str, str], Order]:
    """Client -> company -> job title -> order, reused when they exist. Returns {(client, job): order}."""
    present = {n.lower() for n in db.scalars(select(Candidate.name))}
    needed = Counter((p["client"], p["job"]) for p in placements if p["folder"].lower() not in present)   # only people not yet in the CRM
    for p in placements:
        needed.setdefault((p["client"], p["job"]), 0)
    orders: dict[tuple[str, str], Order] = {}
    for (client_name, job), count in sorted(needed.items()):
        sample = next(p for p in placements if p["client"] == client_name)
        client = db.scalar(select(Client).where(func.lower(Client.name) == client_name.lower()))
        if client is None:
            client = Client(name=client_name, country=sample["country"], status="active",
                            notes="Created when the travelled candidates were imported. Contact person not recorded yet.")
            db.add(client)
            db.flush()
        company = db.scalar(select(Company).where(Company.client_id == client.id, func.lower(Company.name) == client_name.lower()))
        if company is None:
            company = Company(client_id=client.id, name=client_name, country=sample["country"], status="active")
            db.add(company)
            db.flush()
        designation = db.scalar(select(Designation).where(func.lower(Designation.title) == job.lower()))
        if designation is None:
            designation = Designation(title=job, required_skills=[], min_experience=0)
            db.add(designation)
            db.flush()
        order = db.scalar(select(Order).where(Order.company_id == company.id, Order.designation_id == designation.id)
                          .order_by(Order.status != OrderStatus.open, Order.created_at).limit(1))
        if order is None:
            number = db.scalar(select(func.nextval("crm_order_reference_seq")))
            order = Order(ref=f"ORD-{datetime.now(timezone.utc).year}-{number:05d}", company_id=company.id, designation_id=designation.id,
                          openings=count, country=sample["country"], currency=CURRENCY.get(sample["country"], "AED"),
                          benefits=[], status=OrderStatus.open)
            db.add(order)
            db.flush()
        else:
            deployed = db.scalar(select(func.count()).select_from(Application).where(
                Application.order_id == order.id, Application.status == ApplicationStatus.deployed)) or 0
            order.openings = max(order.openings, deployed + count)   # room for everyone already deployed plus the new people
        orders[(client_name, job)] = order
    if apply:
        db.commit()          # a dry run keeps these inside its own transaction and discards them at the end
    return orders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", required=True, help="the organized folder: one sub-folder per candidate and _placements.json")
    parser.add_argument("--user-email", help="the CRM user the import is recorded under (becomes the recruiter); default: the only active admin")
    parser.add_argument("--apply", action="store_true", help="write to the database (without it, only show what would happen)")
    parser.add_argument("--allow-remote", action="store_true", help="allow a database that is not on this computer")
    args = parser.parse_args()

    why = shared_database_reason()
    if why and not args.allow_remote:
        print(f"Refused: {why} This script only writes to a throw-away local database unless --allow-remote is given on purpose.", file=sys.stderr)
        return 2
    root = Path(args.folder)
    placements = json.loads((root / "_placements.json").read_text(encoding="utf-8"))
    print(f"Database: {engine.url.render_as_string(hide_password=True)}  |  {'WRITING' if args.apply else 'dry run (nothing is written)'}")

    with SessionLocal() as db:
        if args.user_email:
            user = db.scalar(select(User).where(func.lower(User.email) == args.user_email.strip().lower()))
        else:
            admins = list(db.scalars(select(User).where(User.role == Role.admin, User.active.is_(True))))
            user = admins[0] if len(admins) == 1 else None
            if user is None:
                print(f"{len(admins)} active admins found: give --user-email to choose who the import is recorded under.", file=sys.stderr)
                return 1
        if user is None:
            print("No such user.", file=sys.stderr)
            return 1
        print(f"Recorded under: {user.name} ({user.role.value})")
        orders = master_rows(db, user, placements, args.apply)
        results = []
        for placement in placements:
            folder = root / placement["folder"]
            files = sorted(p for p in folder.iterdir() if p.is_file())
            uploads = [bulk.Upload(path=f"{placement['folder']}/{f.name}", kinds=kinds_from_name(placement["folder"], f.name),
                                   data=f.read_bytes(), content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                       for f in files]
            order = orders[(placement["client"], placement["job"])]
            row = {"name": placement["folder"], "order": order.ref, "travel_date": placement.get("travel_date") or "",
                   "airline": placement.get("airline") or "", "pnr": placement.get("pnr") or "", "agency": placement.get("agency") or ""}
            row = {key: value for key, value in row.items() if value}
            ctx = bulk.load_context(db, default_order=None, default_recruiter=user.id, wanted_orders={order.ref}, me=user)
            ctx.historical = True
            ctx.travel_confirmed = bool(placement.get("travelled")) and not placement.get("travel_date")
            if not args.apply:
                results.append((placement["folder"], bulk.plan_candidate(
                    db, ctx, folder=placement["folder"], row=row, files=[{"path": u.path, "kinds": u.kinds} for u in uploads])))
                continue
            written: list[str] = []
            try:
                result = bulk.import_candidate(db, user, ctx, folder=placement["folder"], row=row, uploads=uploads, written=written)
                db.commit()
            except bulk.ImportRefused as refused:
                db.rollback()
                bulk.discard_written(bulk.get_settings(), written)
                result = {"action": "skip", "issues": refused.plan["issues"]}
            except Exception as exc:   # report it, keep going: re-running later only picks up what is missing
                db.rollback()
                bulk.discard_written(bulk.get_settings(), written)
                result = {"action": "error", "issues": [{"level": "error", "message": f"{type(exc).__name__}: {str(exc)[:200]}"}]}
            results.append((placement["folder"], result))
        if not args.apply:
            db.rollback()

    print()
    for name, result in results:
        issues = [i["message"] for i in result.get("issues", []) if i["level"] in {"error", "warning"}]
        through = result.get("completedThrough")
        print(f"{name:34s} {result['action']:14s}" + (f" stages 1-{through} done" if through else "") + (f"  ! {'; '.join(issues)}" if issues else ""))
    print("\n" + ", ".join(f"{count} x {action}" for action, count in Counter(r["action"] for _, r in results).items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
