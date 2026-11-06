"""
Operational commands.

Run inside the container:

    docker exec -it <container> python -m app.cli create-user EMAIL --name NAME --role ROLE
    docker exec -it <container> python -m app.cli set-password EMAIL
    docker exec -it <container> python -m app.cli list-users

Accounts are created explicitly. Password prompts require an interactive terminal,
do not echo input, and ask for confirmation.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import warnings

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db.models import Role, Session as SessionRow, User
from app.db.session import SessionLocal


def _prompt_password() -> str | None:
    try:
        # getpass otherwise falls back to echoed input when no terminal is present.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New password: ")
            confirmation = getpass.getpass("Confirm password: ")
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("Password entry cancelled. Use an interactive terminal.", file=sys.stderr)
        return None

    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return None
    if password != confirmation:
        print("Passwords do not match. No changes were made.", file=sys.stderr)
        return None
    return password


def create_user(email: str, name: str, role: Role) -> int:
    email = email.strip().lower()
    name = name.strip()
    role = Role(role)
    if not email or len(email) > 255 or not name or len(name) > 160:
        print("Provide an email (up to 255 characters) and name (up to 160 characters).", file=sys.stderr)
        return 1

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)) is not None:
            print(f"An account with email {email!r} already exists. No changes were made.", file=sys.stderr)
            return 1

        password = _prompt_password()
        if password is None:
            return 1

        db.add(
            User(
                name=name,
                email=email,
                role=role,
                password_hash=hash_password(password),
                active=True,
                receives_auto_assign=(role is Role.staff),
                on_leave=False,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            print("Account creation failed because a database constraint was violated.", file=sys.stderr)
            return 1

    print(f"Created {role.value} account for {email}.")
    return 0


def set_password(email: str, password: str | None = None) -> int:
    email = email.strip().lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"No account with email {email!r}.", file=sys.stderr)
            print("Run 'python -m app.cli list-users' to see what exists.", file=sys.stderr)
            return 1

        if password is None:
            password = _prompt_password()
            if password is None:
                return 1
        if not password:
            print("Password must not be empty.", file=sys.stderr)
            return 1

        password_hash = hash_password(password)
        from app.services.staff_activity import close_user_presence
        # Use the same User → Login → Presence lock order as HTTP revocation.
        close_user_presence(db, user.id)
        user.password_hash = password_hash
        db.execute(delete(SessionRow).where(SessionRow.user_id == user.id))
        db.commit()

    print(f"Password updated for {email}. Existing sessions were revoked.")
    return 0


def list_users() -> int:
    with SessionLocal() as db:
        users = list(db.scalars(select(User).order_by(User.role, User.email)))

    if not users:
        print("No accounts exist. Create one with:")
        print("  python -m app.cli create-user EMAIL --name NAME --role ROLE")
        return 0

    width = max(len(u.email) for u in users)
    print(f"{'EMAIL'.ljust(width)}  ROLE      ACTIVE  PASSWORD SET")
    for u in users:
        print(
            f"{u.email.ljust(width)}  {u.role.value:<8}  "
            f"{'yes' if u.active else 'no':<6}  {'yes' if u.password_hash else 'NO'}"
        )
    return 0


def backfill_application_forms(candidate_id: str | None, dry_run: bool) -> int:
    """Fill the application-form columns of existing candidates from the OCR output already stored on them.

    Only empty fields (or ones still marked extracted) are filled; nothing a person entered is touched. Safe to repeat."""
    from app.services.application_form import backfill
    with SessionLocal() as db:
        summary = backfill(db, candidate_id=candidate_id, dry_run=dry_run)
    print(f"{summary['candidates']} candidates with stored documents checked, {summary['updated']} updated, "
          f"{summary['fieldsFilled']} fields filled{' (dry run, nothing saved)' if dry_run else ''}.")
    return 0


def backfill_lead_matches(dry_run: bool) -> int:
    """Flag every stored lead whose phone / e-mail is already a Data Bank candidate, and give leads with a follow-up date their to-do.

    One set-based statement each; nothing is overwritten (a lead that is already flagged or linked is left alone). Safe to repeat."""
    from app.services import lead_followups, lead_matching
    with SessionLocal() as db:
        flagged = lead_matching.backfill(db)
        todos = lead_followups.sync_all(db)
        if dry_run:
            db.rollback()
        else:
            db.commit()
    print(f"{flagged} leads flagged as already in the Data Bank; follow-up to-dos: {todos['created']} created, {todos['moved']} updated, "
          f"{todos['removed']} removed{' (dry run, nothing saved)' if dry_run else ''}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="Create an account with a prompted password")
    create.add_argument("email")
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[role.value for role in Role])

    sp = sub.add_parser("set-password", help="Set a user's password")
    sp.add_argument("email")

    sub.add_parser("list-users", help="Show accounts and whether a password is set")

    backfill = sub.add_parser("backfill-application-forms", help="Fill application-form fields of existing candidates from stored documents")
    backfill.add_argument("--candidate", help="Only this candidate id")
    backfill.add_argument("--dry-run", action="store_true", help="Report what would be filled; save nothing")

    leads = sub.add_parser("backfill-lead-matches", help="Flag existing leads that are already candidates and create the follow-up to-dos of existing leads")
    leads.add_argument("--dry-run", action="store_true", help="Report the counts; save nothing")

    args = parser.parse_args()
    if args.command == "backfill-lead-matches":
        return backfill_lead_matches(args.dry_run)
    if args.command == "create-user":
        return create_user(args.email, args.name, Role(args.role))
    if args.command == "set-password":
        return set_password(args.email)
    if args.command == "backfill-application-forms":
        return backfill_application_forms(args.candidate, args.dry_run)
    return list_users()


if __name__ == "__main__":
    raise SystemExit(main())
