"""PERMANENTLY delete exactly one candidate (CND-01006), even though the candidate is on an order.

One-off, tightly scoped. It is hard-wired to ONE reference (ALLOWED_CANDIDATE_REF below); any other value is refused, so
the script cannot delete anybody else even if the arguments are edited.

    python scripts/delete_candidate_permanently.py --candidate CND-01006              # DRY RUN: only shows what would go
    python scripts/delete_candidate_permanently.py --candidate CND-01006 --execute    # asks you to type a confirmation

It does not carry its own delete SQL. It calls the CRM's own admin delete (`DELETE /api/candidates/{id}?withdrawFromOrders=true`,
`app.api.routers.candidates.delete_candidate`) in-process with a real database session, so every cascade, lock, audit entry
and change-feed notice is the one the CRM itself produces: applications, stages, tasks, remarks, daily-work items, documents
and attachments, reading (OCR) jobs, notifications and links in to-dos, all in ONE transaction. The stored files are removed
only after that transaction has committed. The job order is then synchronised exactly as the app does it (`_sync_order`).

The CRM's safety blocks stay: a deployed / travelled candidate and any invoice or payment history are refused (also for an
imported candidate, which the plain admin delete would allow), and nothing is changed.

The database is whatever the app settings say (backend/.env, or CRM_ENV_FILE) unless --database-url is given. The host, port and
database name are printed first, never the password. Exit code 0 = done (or dry run), 1 = refused, 2 = bad arguments or refused by
the guard, 3 = failed.

The shared-database guard (the same one the other scripts use, `import_travelled_candidates.shared_database_reason`) applies to the
dry run as well, because even a dry run connects and reads: backend/.env is the SSH tunnel to the SHARED (production) database, so
unless CRM_ENV_FILE names a different env file, or the database is on this computer and not on the tunnel's port, the script
stops before it connects, unless `--allow-remote` is given on purpose. A --database-url is checked the same way (a remote host, or
the tunnel's port, needs --allow-remote).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The ONLY candidate this script can ever delete. Do not parameterise.
ALLOWED_CANDIDATE_REF = "CND-01006"
CONFIRMATION_PHRASE = f"DELETE {ALLOWED_CANDIDATE_REF} PERMANENTLY"

from fastapi import BackgroundTasks, HTTPException  # noqa: E402
from sqlalchemy import and_, create_engine, exists, func, or_, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.routers import candidates as candidate_routes  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import (  # noqa: E402
    Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry, Candidate, CandidateInvoice, Client, Company,
    DailyWorkItem, Designation, DocumentExtractionJob, InvoicePayment, Notification, Order, OrderStatus, Remark, Role, Task,
    TodoItem, User,
)
from import_travelled_candidates import shared_database_reason  # noqa: E402

TUNNEL_FILE = Path(__file__).resolve().parents[1] / ".db-tunnel.json"     # where backend/.env's SSH tunnel records its local port


def url_reason(url) -> str | None:
    """Why a database URL given on the command line must be treated as the shared one (None for a plain local database)."""
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


def out(text: str = "") -> None:
    print(text, flush=True)


# ------------------------------------------------------------------------------------------------ read-only helpers

def find_candidate(db: Session, ref: str):
    """Rows for the CRM reference (exact match). The caller stops unless there is exactly one."""
    return db.execute(
        select(Candidate.id, Candidate.name, Candidate.ref, Candidate.care_id, Candidate.traveller_id, Candidate.owner_id,
               Candidate.intake_owner_id).where(Candidate.ref == ref)).all()


def user_name(db: Session, user_id: str | None) -> str:
    if not user_id:
        return "(nobody)"
    return db.scalar(select(User.name).where(User.id == user_id)) or "(unknown user)"


def blockers(db: Session, candidate_id: str, traveller_id: str | None) -> list[str]:
    """Why this candidate must be kept on record. The CRM's own blocks, plus 'deployed' also for an imported candidate."""
    reasons: list[str] = []
    deployed = db.execute(
        select(Order.ref).join(Application, Application.order_id == Order.id)
        .where(Application.candidate_id == candidate_id, Application.status == ApplicationStatus.deployed)).scalars().all()
    if deployed:
        reasons.append(f"The candidate was deployed through {', '.join(sorted(set(deployed)))}; a deployed placement is kept on record.")
    if traveller_id:
        reasons.append("The candidate has a Traveller ID (they have travelled); that is kept on record.")
    app_ids = select(Application.id).where(Application.candidate_id == candidate_id)
    invoice = db.scalar(select(exists().where(
        CandidateInvoice.candidate_id == candidate_id,
        or_(CandidateInvoice.number.is_not(None), exists().where(InvoicePayment.invoice_id == CandidateInvoice.id)))))
    payment = db.scalar(select(exists().where(InvoicePayment.source_application_id.in_(app_ids))))
    if invoice or payment:
        reasons.append("The candidate has a reserved or issued invoice, or recorded payments; financial history is kept.")
    return reasons


def order_figures(db: Session, order_id: str) -> dict:
    order = db.execute(select(Order.ref, Order.status, Order.openings).where(Order.id == order_id)).one()
    deployed = db.scalar(select(func.count()).select_from(Application).where(
        Application.order_id == order_id, Application.status == ApplicationStatus.deployed)) or 0
    active = db.scalar(select(func.count()).select_from(Application).where(
        Application.order_id == order_id, Application.status == ApplicationStatus.active)) or 0
    return {"ref": order.ref, "status": order.status, "openings": order.openings, "deployed": deployed, "active": active}


def status_after(figures: dict) -> OrderStatus:
    """What `applications._sync_order` leaves the order's status as once the placement is gone (deployed counts only)."""
    if figures["deployed"] >= figures["openings"]:
        return OrderStatus.filled
    return OrderStatus.open if figures["status"] == OrderStatus.filled else figures["status"]


def placements(db: Session, candidate_id: str):
    return db.execute(
        select(Application.id, Application.status, Application.current_stage, Order.id, Order.ref, Designation.title,
               Company.name, Client.name)
        .join(Order, Order.id == Application.order_id)
        .join(Designation, Designation.id == Order.designation_id)
        .join(Company, Company.id == Order.company_id)
        .join(Client, Client.id == Company.client_id)
        .where(Application.candidate_id == candidate_id).order_by(Order.ref, Application.created_at)).all()


def counts(db: Session, candidate_id: str) -> dict[str, int]:
    """What will go (or be unlinked). Only counts, never contents."""
    app_ids = select(Application.id).where(Application.candidate_id == candidate_id)
    task_ids = select(Task.id).where(Task.candidate_id == candidate_id)
    link = f"/candidates/{candidate_id}"

    def n(statement) -> int:
        return db.scalar(statement) or 0

    documents = select(func.count(Attachment.id)).where(or_(
        and_(Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id),
        and_(Attachment.entity_type == "application", Attachment.entity_id.in_(app_ids))))
    return {
        "applications (placements)": n(select(func.count()).select_from(Application).where(Application.candidate_id == candidate_id)),
        "application stages": n(select(func.count()).select_from(ApplicationStage).where(ApplicationStage.application_id.in_(app_ids))),
        "documents / attachments": n(documents),
        "stored files (documents with a file)": n(documents.where(Attachment.object_key.is_not(None))),
        "reading (OCR / extraction) jobs": n(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)),
        "tasks": n(select(func.count()).select_from(Task).where(Task.candidate_id == candidate_id)),
        "remarks": n(select(func.count()).select_from(Remark).where(Remark.task_id.in_(task_ids))),
        "daily-work items": n(select(func.count()).select_from(DailyWorkItem).where(DailyWorkItem.candidate_id == candidate_id)),
        "notifications": n(select(func.count()).select_from(Notification).where(or_(
            Notification.link_to == link, Notification.link_to.in_(select(func.concat("/tasks?task=", Task.id)).where(Task.candidate_id == candidate_id))))),
        "to-dos linked to the candidate (kept, link removed)": n(select(func.count()).select_from(TodoItem).where(TodoItem.link_to == link)),
    }


def preview(db: Session, row) -> list:
    """Print what would go and return the placements (id, status, stage, order id, ...)."""
    out("PREVIEW - this candidate would be deleted PERMANENTLY")
    out(f"  Candidate : {row.name}")
    out(f"  CRM ref   : {row.ref}")
    out(f"  Care India ID: {row.care_id or '(none)'}")
    out(f"  Owner (recruiter)  : {user_name(db, row.owner_id)}")
    out(f"  Intake recruiter   : {user_name(db, row.intake_owner_id)}")
    rows = placements(db, row.id)
    out(f"  Orders / jobs the candidate is on ({len(rows)}):")
    for _, state, stage, order_id, order_ref, title, company, client in rows:
        figures = order_figures(db, order_id)
        free_before = max(figures["openings"] - figures["deployed"], 0)
        active_after = figures["active"] - (1 if state is ApplicationStatus.active else 0)
        out(f"    - {order_ref}: {title} | client {client} | company {company}")
        out(f"      placement status {state.value}, stage {stage}")
        after_status = status_after(figures) if state is ApplicationStatus.active else figures['status']
        out(f"      order status {figures['status'].value} -> {after_status.value}")
        out(f"      openings filled (deployed): {figures['deployed']} of {figures['openings']} before, "
            f"{figures['deployed']} of {figures['openings']} after (free openings {free_before} before, {free_before} after)")
        out(f"      candidates currently working on this order: {figures['active']} before, {active_after} after")
    if rows:
        out("  Note: the app counts an opening as filled only when a placement is DEPLOYED. A candidate who is still in the")
        out("  stages holds no filled opening, so the filled count does not change; the order simply stops listing this candidate.")
    out("  Rows that will be removed:")
    for label, value in counts(db, row.id).items():
        out(f"    - {label}: {value}")
    out("  Also: the candidate's mail/WhatsApp records stay but are unlinked; an audit entry 'Deleted candidate' is written.")
    return rows


# ------------------------------------------------------------------------------------------------ main

def main(argv: list[str] | None = None, *, input_fn=input, is_interactive=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", required=True, help=f"the CRM reference; must be exactly {ALLOWED_CANDIDATE_REF}")
    parser.add_argument("--execute", action="store_true", help="really delete (without it: dry run, nothing is changed)")
    parser.add_argument("--database-url", help="override the database from the app settings (backend/.env)")
    parser.add_argument("--actor-email", help="the admin the deletion is recorded under (default: first active admin by email)")
    parser.add_argument("--allow-remote", action="store_true", help="allow the shared database (backend/.env is the tunnel to it); also for the dry run")
    args = parser.parse_args(argv)

    if args.candidate != ALLOWED_CANDIDATE_REF:
        print(f"Refused: this script only deletes {ALLOWED_CANDIDATE_REF}. It will not touch '{args.candidate}'.", file=sys.stderr)
        return 2
    # Before anything connects: even the dry run reads the database, so the guard applies to both.
    url = make_url(args.database_url) if args.database_url else None
    why = shared_database_reason() or (url_reason(url) if url is not None else None)
    if why and not args.allow_remote:
        print(f"Refused: {why} Add --allow-remote on purpose to use it.", file=sys.stderr)
        return 2
    interactive = sys.stdin.isatty() if is_interactive is None else is_interactive
    if args.execute and not interactive:
        print("Refused: --execute needs an interactive terminal to type the confirmation; no input is possible here.", file=sys.stderr)
        return 1

    url = url or make_url(get_settings().database_url)
    out(f"Database: {url.host or 'local socket'}:{url.port or 5432}/{url.database}")
    out("Mode    : EXECUTE (permanent)" if args.execute else "Mode    : dry run (nothing will be changed)")
    engine = create_engine(url, connect_args={"connect_timeout": 10}, future=True)
    try:
        return run(engine, args, input_fn)
    except Exception as error:  # noqa: BLE001
        print(f"FAILED: {type(error).__name__}: {error}\nThe delete is all-or-nothing: unless a 'Committed' line was printed above, nothing was deleted.", file=sys.stderr)
        return 3
    finally:
        engine.dispose()


def run(engine, args, input_fn) -> int:
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        found = find_candidate(db, ALLOWED_CANDIDATE_REF)
        if len(found) != 1:
            print(f"Stopped: {len(found)} candidates have the reference {ALLOWED_CANDIDATE_REF} (exactly one is required).", file=sys.stderr)
            return 1
        row = found[0]
        if row.ref != ALLOWED_CANDIDATE_REF:
            print("Stopped: the reference found does not match.", file=sys.stderr)
            return 1
        candidate_id = row.id

        actor = None
        if args.execute:
            query = select(User).where(User.role == Role.admin, User.active.is_(True))
            if args.actor_email:
                actor = db.scalar(query.where(func.lower(User.email) == args.actor_email.strip().lower()))
            else:
                actor = db.scalar(query.order_by(func.lower(User.email), User.id).limit(1))
            if actor is None:
                print("Stopped: no active admin user found to record the deletion under (give --actor-email).", file=sys.stderr)
                return 1
            out(f"Recorded under admin: {actor.name}")

        rows = preview(db, row)
        why = blockers(db, candidate_id, row.traveller_id)
        if why:
            out("")
            print("REFUSED - the CRM keeps this candidate on record:\n  " + "\n  ".join(why) + "\nNothing was changed.", file=sys.stderr)
            return 1
        order_ids = sorted({placement[3] for placement in rows})
        db.rollback()  # end the read-only transaction before the delete starts a new one

        if not args.execute:
            out("")
            out("DRY RUN: nothing was changed. To delete for real, run again with --execute.")
            return 0

        out("")
        out(f"This cannot be undone. To confirm, type exactly:  {CONFIRMATION_PHRASE}")
        try:
            typed = input_fn("> ")
        except EOFError:
            typed = None
        if typed != CONFIRMATION_PHRASE:
            print("Refused: the confirmation was not typed exactly. Nothing was changed.", file=sys.stderr)
            return 1

        # The re-check and the delete run in one session; the app's delete locks the candidate and re-reads everything again.
        why = blockers(db, candidate_id, row.traveller_id)
        if why:
            print("REFUSED - the CRM keeps this candidate on record:\n  " + "\n  ".join(why), file=sys.stderr)
            return 1
        db.rollback()
        background = BackgroundTasks()
        try:
            result = candidate_routes.delete_candidate(
                candidate_id=candidate_id, background=background, withdraw_from_orders=True, db=db, user=actor)
        except HTTPException as refusal:
            db.rollback()
            print(f"REFUSED by the CRM: {refusal.detail}\nNothing was changed.", file=sys.stderr)
            return 1
        except Exception:
            db.rollback()  # all-or-nothing: the delete is one transaction, so nothing is half-deleted
            raise
        out(f"Committed. CRM result: {result['deleted']}")
        # The same follow-up the API runs after its response: stored files go only now that the commit has succeeded.
        for task in background.tasks:
            try:
                task.func(*task.args, **task.kwargs)
            except Exception as error:  # noqa: BLE001
                print(f"Warning: stored file removal failed ({type(error).__name__}); the database delete is complete. "
                      "The leftover keys are recorded on the audit entry.", file=sys.stderr)

    return verify(engine, candidate_id, order_ids)


def verify(engine, candidate_id: str, order_ids: list[str]) -> int:
    out("")
    out("VERIFICATION")
    ok = True
    with Session(engine) as db:
        gone = db.scalar(select(func.count()).select_from(Candidate).where(or_(Candidate.id == candidate_id, Candidate.ref == ALLOWED_CANDIDATE_REF))) == 0
        leftovers = {
            "applications": db.scalar(select(func.count()).select_from(Application).where(Application.candidate_id == candidate_id)),
            "tasks": db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate_id)),
            "documents": db.scalar(select(func.count()).select_from(Attachment).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == candidate_id)),
            "reading jobs": db.scalar(select(func.count()).select_from(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)),
        }
        out(f"  candidate {ALLOWED_CANDIDATE_REF} gone: {'yes' if gone else 'NO'}")
        for label, value in leftovers.items():
            out(f"  {label} left: {value}")
        ok = gone and not any(leftovers.values())
        for order_id in order_ids:
            figures = order_figures(db, order_id)
            free = max(figures["openings"] - figures["deployed"], 0)
            out(f"  {figures['ref']}: status {figures['status'].value}, {figures['deployed']} of {figures['openings']} openings filled, "
                f"{free} free, {figures['active']} candidate(s) still working on it")
            if figures["status"] == OrderStatus.filled and free > 0:
                ok = False
        audit = db.scalar(select(func.count()).select_from(AuditEntry).where(
            AuditEntry.action == "Deleted candidate", AuditEntry.entity_id == candidate_id))
        out(f"  audit entry 'Deleted candidate': {'written' if audit else 'MISSING'}")
        ok = ok and bool(audit)
    out("RESULT: " + ("candidate permanently deleted, job order updated." if ok else "VERIFICATION FAILED - check the output above."))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
