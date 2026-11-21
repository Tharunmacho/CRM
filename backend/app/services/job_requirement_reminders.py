"""Deadline reminders for the Job requirements sheet, one small idempotent pass beside the day-end report.

* An ACTIVE requirement whose ``details.deadline`` is today or earlier notifies its owner once per requirement per (agency) day.
* The first day it is overdue (deadline before today) every Admin / MD is told once for that deadline (a new deadline can be told again).
* Every notice leaves a marker row in the audit log (entity ``job_requirement_reminder``), so a rerun, a restart or a second worker never
  repeats it. The caller commits.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.roles import ADMIN_LEVEL_ROLES
from app.db.models import AuditEntry, JobRequirement, Notification, User

KIND = "job_requirement_due"
LINK = "/job-requirements"
MARKER_ENTITY = "job_requirement_reminder"
OWNER_ACTION = "Job requirement due reminder"
ADMIN_ACTION = "Job requirement overdue notice"
LOCK_KEY = 724681930121   # next to the day-end report lock, never the same
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _label(row: JobRequirement) -> str:
    parts = [row.client_name or "A client", (row.details or {}).get("position") or ""]
    return " · ".join(part for part in (parts[0], parts[1], row.country or "") if part)


def deliver_job_requirement_reminders(db, *, now: datetime | None = None, agency_timezone: str | None = None) -> dict:
    """Returns {"owners": notices to owners, "admins": notices to admin-level users}; the caller commits."""
    now = now or datetime.now(timezone.utc)
    today: date = now.astimezone(ZoneInfo(agency_timezone or get_settings().agency_timezone)).date()
    deadline = JobRequirement.details["deadline"].astext
    result = {"owners": 0, "admins": 0}
    due = list(db.scalars(select(JobRequirement).where(
        JobRequirement.status == "active", deadline.op("~")(DATE_PATTERN), deadline <= today.isoformat()).order_by(JobRequirement.id)))
    if not due or not db.scalar(text(f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})")):
        return result
    ids = [row.id for row in due]
    markers: set[tuple] = set()
    for entity_id, action, target in db.execute(select(AuditEntry.entity_id, AuditEntry.action, AuditEntry.target).where(
            AuditEntry.entity_type == MARKER_ENTITY, AuditEntry.entity_id.in_(ids))):
        markers.add((entity_id, action, target))
    owner_ids = {row.owner_id for row in due if row.owner_id}
    owners = {user.id for user in db.scalars(select(User).where(User.id.in_(owner_ids), User.active.is_(True)))} if owner_ids else set()
    admins = list(db.scalars(select(User.id).where(User.active.is_(True), User.role.in_(ADMIN_LEVEL_ROLES)).order_by(User.id)))
    for row in due:
        when = row.details["deadline"]
        days = (today - date.fromisoformat(when)).days
        label = _label(row)
        if row.owner_id in owners and (row.id, OWNER_ACTION, today.isoformat()) not in markers:
            title = "Job requirement deadline is today" if days == 0 else f"Job requirement is {days} day{'s' if days != 1 else ''} overdue"
            db.add(Notification(user_id=row.owner_id, kind=KIND, title=title, detail=f"{label} · deadline {when}", link_to=LINK))
            db.add(AuditEntry(actor_id=None, action=OWNER_ACTION, target=today.isoformat(), entity_type=MARKER_ENTITY, entity_id=row.id,
                              meta={"owner": row.owner_id, "deadline": when}, at=now))
            result["owners"] += 1
        if days > 0 and admins and (row.id, ADMIN_ACTION, when) not in markers:
            for admin_id in admins:
                db.add(Notification(user_id=admin_id, kind=KIND, title="Job requirement is overdue", link_to=LINK,
                                    detail=f"{label} · deadline {when} · {days} day{'s' if days != 1 else ''} overdue"))
            db.add(AuditEntry(actor_id=None, action=ADMIN_ACTION, target=when, entity_type=MARKER_ENTITY, entity_id=row.id,
                              meta={"deadline": when}, at=now))
            result["admins"] += len(admins)
    db.flush()
    return result
