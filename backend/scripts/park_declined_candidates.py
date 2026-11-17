"""Mark candidates who said no at a stage as "Not interested", so they stop coming back on the Tasks list as an "Initial call".

Before the fix, closing an application because the candidate declined (declined at a stage, declined the offer, not interested,
not travelling) put the candidate back to status "new". The Tasks list then showed them again as someone to call for the first
time. New declines now set "not_interested" themselves; this script does the same for the ones declined earlier.

    python scripts/park_declined_candidates.py                              # dry run: lists who would change
    python scripts/park_declined_candidates.py --candidate CI-062 CI-070    # dry run for these candidates only
    python scripts/park_declined_candidates.py --apply --allow-remote       # write

A candidate is changed only when ALL of these hold:
* their status is "new" (needs_review and not_interested are left alone);
* their MOST RECENT application (the greatest created_at, the id breaks a tie) is closed with the reason stage_declined,
  offer_declined, not_interested or not_travelling (an application closed as unmapped, changed_job, rejected, prescreen failed,
  medical unfit ... is never a reason to park);
* they have no active or deployed application;
* they have NOT said "yes" in a call recorded after that application was closed (the Tasks list rule in
  app.services.daily_work._parked): a call with the answer yes later than the application's updated_at (the time it was closed)
  means they are interested again and are to be worked, so they are skipped ("said yes ... after the decline").

The only change is candidate.status = "not_interested", plus one audit entry per candidate (old status, closure reason and the
application id). Applications, tasks, documents and ownership are never touched, and each candidate is its own transaction.
The candidate stays in the Data Bank and can be mapped to a new order, which brings them back at stage 1.
Safe to run twice: a parked candidate no longer has status "new". The database is whatever CRM_ENV_FILE (or backend/.env) points
at; the shared one needs --allow-remote.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import exists, or_, select  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from app.db.models import Application, ApplicationStatus, AuditEntry, Candidate, CandidateStatus, ClosureReason, User  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from import_travelled_candidates import shared_database_reason  # noqa: E402

# The candidate's own "no" (the same set the stage rules use in app/api/routers/applications.py).
CANDIDATE_SAID_NO = (ClosureReason.stage_declined, ClosureReason.offer_declined, ClosureReason.not_interested, ClosureReason.not_travelling)
AUDIT_ACTION = "Parked after decline: status set to Not interested"


def latest_application(db, candidate_id: str) -> Application | None:
    return db.scalar(select(Application).where(Application.candidate_id == candidate_id)
                     .order_by(Application.created_at.desc(), Application.id.desc()).limit(1))


def has_open_application(db, candidate_id: str) -> bool:
    return bool(db.scalar(select(exists().where(Application.candidate_id == candidate_id,
                                                Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed])))))


def skip_reason(db, candidate: Candidate) -> str | None:
    """Why this candidate must not be parked (None when they qualify)."""
    if candidate.status != CandidateStatus.new:
        return f"status is '{candidate.status.value}', not 'new'"
    if has_open_application(db, candidate.id):
        return "has an active or deployed application"
    latest = latest_application(db, candidate.id)
    if latest is None:
        return "has no application"
    if latest.status != ApplicationStatus.closed or latest.closure_reason not in CANDIDATE_SAID_NO:
        reason = latest.closure_reason.value if latest.closure_reason else "-"
        return f"latest application is {latest.status.value} ({reason}), not a decline"
    if said_yes_after(candidate, latest):
        return f"said yes in a call ({candidate.call_recorded_at:%Y-%m-%d %H:%M}) after the decline, so they are interested again"
    return None


def said_yes_after(candidate: Candidate, application: Application) -> bool:
    """The candidate answered "yes" in a call recorded later than the application was closed (the same rule as daily_work._parked)."""
    return bool(candidate.call_interested == "yes" and candidate.call_recorded_at is not None
                and candidate.call_recorded_at > application.updated_at)


def find_eligible(db, idents: list[str] | None = None) -> list[tuple[Candidate, Application, str | None]]:
    """[(candidate, their latest application, owner name)] for everyone who qualifies, in Care India ID order. One query."""
    newest, other = aliased(Application), aliased(Application)    # aliases: the same table is also the joined (latest) application
    latest = (select(newest.id).where(newest.candidate_id == Candidate.id).order_by(newest.created_at.desc(), newest.id.desc())
              .limit(1).correlate(Candidate).scalar_subquery())
    open_application = exists().where(other.candidate_id == Candidate.id,
                                      other.status.in_([ApplicationStatus.active, ApplicationStatus.deployed])).correlate(Candidate)
    query = (select(Candidate, Application, User.name).select_from(Candidate)
             .join(Application, Application.id == latest)
             .outerjoin(User, User.id == Candidate.owner_id)
             .where(Candidate.status == CandidateStatus.new, Application.status == ApplicationStatus.closed,
                    Application.closure_reason.in_(CANDIDATE_SAID_NO), ~open_application,
                    # not interested again: a yes in a call after the closure (NULL-safe, so a candidate never called qualifies)
                    or_(Candidate.call_interested.is_distinct_from("yes"), Candidate.call_recorded_at.is_(None),
                        Candidate.call_recorded_at <= Application.updated_at))
             .order_by(Candidate.care_id, Candidate.ref))
    if idents:
        query = query.where(or_(Candidate.care_id.in_(idents), Candidate.ref.in_(idents)))
    return [(candidate, application, owner) for candidate, application, owner in db.execute(query)]


def park(db, candidate_id: str) -> tuple[str, str] | None:
    """Re-check one candidate under a row lock and park them. Returns (old status, closure reason), or None if they no longer qualify."""
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None or skip_reason(db, candidate) is not None:
        db.rollback()
        return None
    application = latest_application(db, candidate.id)
    old, reason = candidate.status.value, application.closure_reason.value
    candidate.status = CandidateStatus.not_interested
    db.add(AuditEntry(actor_id=None, action=AUDIT_ACTION, target=candidate.name, entity_type="candidate", entity_id=candidate.id,
                      meta={"old_status": old, "closure_reason": reason, "application_id": application.id}))
    db.commit()
    return old, reason


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", nargs="+", help="only these candidates (CI-062 style IDs, or CND-00123 references)")
    parser.add_argument("--apply", action="store_true", help="write the change (without it, only show it)")
    parser.add_argument("--allow-remote", action="store_true", help="allow the shared database")
    args = parser.parse_args(argv)
    if args.candidate is not None:       # " ci-062 " works like "CI-062"; blanks and repeats are dropped
        args.candidate = list(dict.fromkeys(item.strip().upper() for item in args.candidate if item.strip()))
        if not args.candidate:
            print("Give at least one candidate with --candidate, or leave the option out for everyone.", file=sys.stderr)
            return 2
    why = shared_database_reason()
    if why and not args.allow_remote:
        print(f"Refused: {why} Add --allow-remote on purpose to use it.", file=sys.stderr)
        return 2
    print(f"Database: {engine.url.render_as_string(hide_password=True)}  |  {'WRITING' if args.apply else 'dry run (nothing is written)'}")

    code = 0
    with SessionLocal() as db:
        found = find_eligible(db, args.candidate)
        print(f"\n{len(found)} candidate(s) to park:")
        for candidate, application, owner in found:
            print(f"  {candidate.care_id or candidate.ref}  {candidate.name}  | owner: {owner or '-'}  | closed: {application.closure_reason.value} "
                  f"at stage {application.current_stage}  | status: {candidate.status.value}")
        if args.candidate:
            listed = {ident for candidate, _, _ in found for ident in (candidate.care_id, candidate.ref)}
            for ident in args.candidate:
                if ident in listed:
                    continue
                other = db.scalar(select(Candidate).where(or_(Candidate.care_id == ident, Candidate.ref == ident)))
                print(f"  {ident}: not found" if other is None else f"  {ident}: skipped, {skip_reason(db, other) or 'does not qualify'}")
                code = 1
        if not args.apply:
            print("\nDry run: nothing was changed. Add --apply to write.")
            return code
        done = 0
        for candidate, _, _ in found:
            label = candidate.care_id or candidate.ref
            try:
                changed = park(db, candidate.id)
            except Exception as error:   # one candidate failing must not stop the rest
                db.rollback()
                print(f"  {label}: FAILED ({error})", file=sys.stderr)
                code = 3
                continue
            if changed is None:
                print(f"  {label}: skipped, no longer qualifies")
                continue
            done += 1
            print(f"  {label}: {changed[0]} -> not_interested")
        print(f"\nParked {done} of {len(found)}.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
