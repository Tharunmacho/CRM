"""Recruiter feedback on the application form ("why is this taking time") and the stage clock above it.

Contract: docs/workflows/application-form-v2-contract.md section 3. A note belongs to the candidate (and the application it was
written for). The stage clock reuses the 48-hour SLA of the open stage task (`workflow_tasks.sla_started_at` / `due_at`, which an
admin restart moves too); when the stage has no open task the clock starts from the last completed stage or the mapping.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Application, ApplicationFeedback, ApplicationStatus, AuditEntry, Candidate, Notification, Role, User,
)
from app.domain.stages import stage_by_no

KINDS = ("update", "delay", "issue", "client", "candidate")
REASONS = {
    "waiting_candidate": "Waiting for the candidate", "waiting_client": "Waiting for the client",
    "waiting_documents": "Waiting for documents", "waiting_visa": "Waiting for the visa",
    "waiting_payment": "Waiting for a payment", "other": "Other",
}
NOTIFY_KINDS = ("delay", "issue")  # these tell the admins and managers
EDIT_WINDOW = timedelta(minutes=15)
LIMIT_HOURS = 48
MAX_ITEMS = 50
BODY_LIMIT = 2000


# ------------------------------------------------------------------ reading

def read_items(session: Session, candidate_id: str) -> list:
    """The newest notes with their author's name (one statement)."""
    return list(session.execute(
        select(ApplicationFeedback.id, ApplicationFeedback.kind, ApplicationFeedback.reason, ApplicationFeedback.body,
               ApplicationFeedback.stage, ApplicationFeedback.author_id, ApplicationFeedback.application_id,
               ApplicationFeedback.created_at, User.name.label("author_name"))
        .outerjoin(User, User.id == ApplicationFeedback.author_id)
        .where(ApplicationFeedback.candidate_id == candidate_id)
        .order_by(ApplicationFeedback.created_at.desc(), ApplicationFeedback.id.desc()).limit(MAX_ITEMS)).all())


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def within_window(created_at: datetime, now: datetime | None = None) -> bool:
    return ((now or datetime.now(timezone.utc)) - created_at) <= EDIT_WINDOW


def can_change(user: User, author_id: str | None, created_at: datetime, now: datetime | None = None) -> bool:
    """Admin may delete any note; everyone else only their own, within 15 minutes of writing it."""
    return user.role == Role.admin or (author_id == user.id and within_window(created_at, now))


def stage_clock(application, tasks: list, stage_rows: list, items: list, now: datetime) -> dict | None:
    """How long the active application has been on its current stage, against the 48-hour limit.

    `tasks` are the candidate's open tasks (application_id, stage, created_at, sla_started_at, due_at); `stage_rows` the
    application's stage records; `items` the newest notes. None when there is no active application.
    """
    if application is None or application.status != ApplicationStatus.active or not 1 <= application.current_stage <= 11:
        return None
    task = next((t for t in sorted(tasks, key=lambda t: t.created_at, reverse=True)
                 if t.application_id == application.id and t.stage == application.current_stage), None)
    if task is not None:
        since = task.sla_started_at or task.created_at
        due = task.due_at or since + timedelta(hours=LIMIT_HOURS)
    else:
        done = [r.completed_at for r in stage_rows if r.reopened_reason is None and r.completed_at is not None]
        since = max(done) if done else application.created_at
        due = since + timedelta(hours=LIMIT_HOURS)
    overdue = now > due
    explained = any(i.kind in ("delay", "update") and i.created_at >= since and i.application_id in (None, application.id) for i in items)
    return {"stage": application.current_stage, "stageLabel": stage_by_no(application.current_stage).title, "since": _iso(since),
            "hours": max(0, int((now - since).total_seconds() // 3600)), "limitHours": max(1, round((due - since).total_seconds() / 3600)),
            "overdue": overdue, "needsReason": overdue and not explained}


def build_block(user: User, candidate, application, items: list, tasks: list, stage_rows: list, can_write: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "items": [{"id": i.id, "kind": i.kind, "reason": i.reason, "body": i.body, "stage": i.stage, "authorId": i.author_id,
                   "authorName": i.author_name or "Former team member",
                   "createdAt": _iso(i.created_at), "canEdit": i.author_id == user.id and within_window(i.created_at, now),
                   "canDelete": can_change(user, i.author_id, i.created_at, now)} for i in items],
        "canWrite": can_write,
        "stageClock": stage_clock(application, tasks, stage_rows, items, now),
    }


# ------------------------------------------------------------------ writing

def _clean(kind: str | None, reason: str | None, body: str | None, *, partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if kind is not None or not partial:
        if kind not in KINDS:
            raise HTTPException(422, f"Choose a kind of note: {', '.join(KINDS)}.")
        out["kind"] = kind
    if reason is not None:
        reason = reason.strip()
        if reason and reason not in REASONS:
            raise HTTPException(422, "Choose one of the listed reasons.")
        out["reason"] = reason or None
    if body is not None or not partial:
        text = (body or "").strip()
        if not text:
            raise HTTPException(422, "Write the note.")
        if len(text) > BODY_LIMIT:
            raise HTTPException(422, f"A note can be at most {BODY_LIMIT} characters.")
        out["body"] = text
    return out


def _application(db: Session, candidate_id: str, application_id: str | None) -> Application | None:
    if application_id:
        application = db.get(Application, application_id)
        if application is None or application.candidate_id != candidate_id:
            raise HTTPException(404, "This candidate has no such application.")
        return application
    return db.scalar(select(Application).where(Application.candidate_id == candidate_id, Application.status == ApplicationStatus.active))


def _notify(db: Session, user: User, candidate, row: ApplicationFeedback) -> None:
    """Delay and issue notes tell every active admin and manager (not the writer)."""
    label = "Delay" if row.kind == "delay" else "Issue"
    reason = REASONS.get(row.reason or "")
    detail = f"{user.name}: " + (f"{reason}. " if reason else "") + row.body[:200]
    for person in db.scalars(select(User).where(User.active.is_(True), User.role.in_((Role.admin, Role.manager)), User.id != user.id)):
        db.add(Notification(user_id=person.id, kind="feedback", title=f"{label} noted for {candidate.name}", detail=detail,
                            link_to=f"/candidates/{candidate.id}/application"))


def create(db: Session, user: User, candidate_id: str, *, kind: str, reason: str | None, body: str, application_id: str | None) -> ApplicationFeedback:
    """Add a note in the caller's transaction (the caller commits). Owner, admin and manager only."""
    from app.core.deps import ensure_can_work_candidate
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    ensure_can_work_candidate(candidate, user, db)
    values = _clean(kind, reason, body)
    application = _application(db, candidate_id, application_id)
    row = ApplicationFeedback(candidate_id=candidate_id, application_id=application.id if application else None, author_id=user.id,
                              stage=application.current_stage if application else None, **values)
    db.add(row)
    db.flush()
    db.add(AuditEntry(actor_id=user.id, action="Application form: feedback added", target=candidate.name, entity_type="candidate",
                      entity_id=candidate_id, meta={"feedback_id": row.id, "kind": row.kind, "reason": row.reason, "stage": row.stage}))
    if row.kind in NOTIFY_KINDS:
        _notify(db, user, candidate, row)
    return row


def _own(db: Session, candidate_id: str, feedback_id: str) -> ApplicationFeedback:
    row = db.scalar(select(ApplicationFeedback).where(ApplicationFeedback.id == feedback_id, ApplicationFeedback.candidate_id == candidate_id)
                    .with_for_update())
    if row is None:
        raise HTTPException(404, "That note no longer exists.")
    return row


def edit(db: Session, user: User, candidate_id: str, feedback_id: str, *, kind: str | None, reason: str | None, body: str | None) -> ApplicationFeedback:
    row = _own(db, candidate_id, feedback_id)
    if row.author_id != user.id:
        raise HTTPException(403, "Only the person who wrote a note can change it.")
    if not within_window(row.created_at):
        raise HTTPException(403, "A note can be changed for 15 minutes after it is written.")
    values = _clean(kind, reason, body, partial=True)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(timezone.utc)
    candidate = db.get(Candidate, candidate_id)
    db.add(AuditEntry(actor_id=user.id, action="Application form: feedback edited", target=candidate.name if candidate else None, entity_type="candidate",
                      entity_id=candidate_id, meta={"feedback_id": row.id, "kind": row.kind}))
    return row


def delete(db: Session, user: User, candidate_id: str, feedback_id: str) -> None:
    row = _own(db, candidate_id, feedback_id)
    if not can_change(user, row.author_id, row.created_at):
        if user.role != Role.admin and row.author_id == user.id:
            raise HTTPException(403, "A note can be deleted for 15 minutes after it is written. Ask an admin to remove it.")
        raise HTTPException(403, "Only the person who wrote a note, or an admin, can delete it.")
    candidate = db.get(Candidate, candidate_id)
    db.add(AuditEntry(actor_id=user.id, action="Application form: feedback deleted", target=candidate.name if candidate else None,
                      entity_type="candidate", entity_id=candidate_id,
                      meta={"feedback_id": row.id, "kind": row.kind, "author_id": row.author_id}))
    db.delete(row)
