"""Move ONE converted candidate back to the Lead sheet, using the same checked code as the "Move back to Lead" button.

    python scripts/move_lead_back.py --candidate-ref CND-01082 --allow-remote                                          # check only
    python scripts/move_lead_back.py --candidate-ref CND-01082 --user-email ... --reason "Converted without a resume" --apply

It finds the lead that became that candidate, runs the reversal checks (it refuses if the candidate already has
recruitment history, invoices, completed work, etc.), and with --apply removes the candidate and restores the lead's
original status with an audit entry. The database is whatever backend/.env (or CRM_ENV_FILE) points at; a database that
is not on this computer, or the SSH tunnel to the shared one, needs --allow-remote on purpose.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import BackgroundTasks, HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.models import Candidate, Lead, Role, User  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services import lead_reversal  # noqa: E402
from scripts.import_travelled_candidates import shared_database_reason  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate-ref", required=True, help="the candidate's ref, e.g. CND-01082")
    parser.add_argument("--user-email", help="an active admin; the move is recorded under them (default: the only active admin)")
    parser.add_argument("--reason", default="Converted without a resume", help="recorded in the history (3+ characters)")
    parser.add_argument("--apply", action="store_true", help="really move it back (without this only the checks run)")
    parser.add_argument("--allow-remote", action="store_true", help="allow the shared / remote database")
    args = parser.parse_args()
    why = shared_database_reason()
    if why and not args.allow_remote:
        print(f"Refused: {why} Add --allow-remote to confirm this is meant for the shared database.", file=sys.stderr)
        return 2
    print(f"Database: {engine.url.render_as_string(hide_password=True)}  |  {'WRITING' if args.apply else 'check only (nothing is written)'}")
    with SessionLocal() as db:
        if args.user_email:
            admin = db.scalar(select(User).where(User.email == args.user_email.strip().lower()))
        else:
            admins = list(db.scalars(select(User).where(User.role == Role.admin, User.active.is_(True))))
            admin = admins[0] if len(admins) == 1 else None
            if admin is None:
                print(f"{len(admins)} active admins found: pass --user-email.", file=sys.stderr)
                return 2
        if admin is None or admin.role != Role.admin:
            print("That e-mail is not an admin.", file=sys.stderr)
            return 2
        candidate = db.scalar(select(Candidate).where(Candidate.ref == args.candidate_ref))
        if candidate is None:
            print(f"No candidate {args.candidate_ref}.", file=sys.stderr)
            return 2
        lead = db.scalar(select(Lead).where(Lead.candidate_id == candidate.id))
        if lead is None:
            print(f"{candidate.name} ({candidate.ref}) did not come from a lead, or is already moved back.", file=sys.stderr)
            return 2
        outcome = lead_reversal.check(db, admin, lead.id)
        print(f"Lead: {lead.name} | candidate {candidate.name} ({candidate.ref}) | allowed: {outcome['allowed']}"
              f"{'' if outcome['allowed'] else ' | ' + str(outcome['reason'])} | lead status after: {outcome['restoredStatus']}")
        if not outcome["allowed"]:
            return 1
        if not args.apply:
            print("Check passed. Re-run with --apply to move it back.")
            return 0
        later = BackgroundTasks()
        try:
            result = lead_reversal.reverse(db, admin, lead.id, candidate.id, args.reason, later)
        except HTTPException as exc:
            print(f"Not moved: {exc.detail}", file=sys.stderr)
            return 1
        asyncio.run(later())  # removes the candidate's stored files after the commit
        print(f"Done: {candidate.ref} removed from the Data Bank; lead '{lead.name}' is back on the Lead sheet "
              f"as '{result['restoredStatus']}'.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
