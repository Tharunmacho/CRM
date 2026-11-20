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
from app.domain.stages import TOTAL_STAGES, stage_by_no
from app.core.roles import ADMIN_LEVEL_ROLES, is_admin_level

KINDS = ("update", "delay", "issue", "client", "candidate")
REASONS = {
    "waiting_candidate": "Waiting for the candidate", "waiting_client": "Waiting for the client",
    "waiting_documents": "Waiting for documents", "waiting_visa": "Waiting for the visa",
    "waiting_payment": "Waiting for a payment", "other": "Other",
}
NOTIFY_KINDS = ("delay", "issue")  # these tell the admins and MDs
LIMIT_HOURS = 48
MAX_ITEMS = 50
BODY_LIMIT = 2000


# ------------------------------------------------------------------ reading

def read_items(session: Session, candidate_id: str) -> list:
    """The newest notes (a removed note is hidden) with their author's name (one statement)."""
    return list(session.execute(
        select(ApplicationFeedback.id, ApplicationFeedback.kind, ApplicationFeedback.reason, ApplicationFeedback.body,
               ApplicationFeedback.stage, ApplicationFeedback.author_id, ApplicationFeedback.application_id,
               ApplicationFeedback.created_at, ApplicationFeedback.edited_at, User.name.label("author_name"))
        .outerjoin(User, User.id == ApplicationFeedback.author_id)
        .where(ApplicationFeedback.candidate_id == candidate_id, ApplicationFeedback.deleted_at.is_(None))
        .order_by(ApplicationFeedback.created_at.desc(), ApplicationFeedback.id.desc()).limit(MAX_ITEMS)).all())


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def can_change(user: User, author_id: str | None, owner_id: str | None) -> bool:
    """Edit or delete a note, at any time: the person who wrote it, the candidate's recruiter, an admin or an MD."""
    return is_admin_level(user) or author_id == user.id or (owner_id is not None and owner_id == user.id)


def stage_clock(application, tasks: list, stage_rows: list, items: list, now: datetime) -> dict | None:
    """How long the active application has been on its current stage, against the 48-hour limit.

    `tasks` are the candidate's open tasks (application_id, stage, created_at, sla_started_at, due_at); `stage_rows` the
    application's stage records; `items` the newest notes. None when there is no active application.
    """
    if application is None or application.status != ApplicationStatus.active or not 1 <= application.current_stage <= TOTAL_STAGES:
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
    owner_id = getattr(candidate, "owner_id", None)
    return {
        "items": [{"id": i.id, "kind": i.kind, "reason": i.reason, "body": i.body, "stage": i.stage, "authorId": i.author_id,
                   "authorName": i.author_name or "Former team member",
                   "createdAt": _iso(i.created_at), "editedAt": _iso(i.edited_at),
                   "canEdit": can_change(user, i.author_id, owner_id), "canDelete": can_change(user, i.author_id, owner_id)} for i in items],
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
    """Delay and issue notes tell every active admin and MD (not the writer)."""
    label = "Delay" if row.kind == "delay" else "Issue"
    reason = REASONS.get(row.reason or "")
    detail = f"{user.name}: " + (f"{reason}. " if reason else "") + row.body[:200]
    for person in db.scalars(select(User).where(User.active.is_(True), User.role.in_(ADMIN_LEVEL_ROLES), User.id != user.id)):
        db.add(Notification(user_id=person.id, kind="feedback", title=f"{label} noted for {candidate.name}", detail=detail,
                            link_to=f"/candidates/{candidate.id}/application"))


def create(db: Session, user: User, candidate_id: str, *, kind: str, reason: str | None, body: str, application_id: str | None) -> ApplicationFeedback:
    """Add a note in the caller's transaction (the caller commits). Owner, admin and MD only."""
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


def _own(db: Session, user: User, candidate_id: str, feedback_id: str) -> tuple[ApplicationFeedback, Candidate]:
    """The live note (a removed one is 404) and its candidate, locked; 403 unless the caller may change it (author, owner, admin, MD)."""
    found = db.execute(select(ApplicationFeedback, Candidate).join(Candidate, Candidate.id == ApplicationFeedback.candidate_id)
                       .where(ApplicationFeedback.id == feedback_id, ApplicationFeedback.candidate_id == candidate_id,
                              ApplicationFeedback.deleted_at.is_(None)).with_for_update(of=ApplicationFeedback)).one_or_none()
    if found is None:
        raise HTTPException(404, "That note no longer exists.")
    row, candidate = found
    if not can_change(user, row.author_id, candidate.owner_id):
        raise HTTPException(403, "Only the person who wrote a note, the candidate's recruiter, an admin or an MD can change it.")
    return row, candidate


def edit(db: Session, user: User, candidate_id: str, feedback_id: str, *, kind: str | None, reason: str | None, body: str | None) -> ApplicationFeedback:
    row, candidate = _own(db, user, candidate_id, feedback_id)
    before = row.body
    values = _clean(kind, reason, body, partial=True)
    for key, value in values.items():
        setattr(row, key, value)
    now = datetime.now(timezone.utc)
    row.updated_at = now
    row.edited_at = now
    db.add(AuditEntry(actor_id=user.id, action="Application form: feedback edited", target=candidate.name, entity_type="candidate",
                      entity_id=candidate_id, meta={"feedback_id": row.id, "kind": row.kind, "author_id": row.author_id,
                                                    "before": before, "after": row.body}))
    return row


def delete(db: Session, user: User, candidate_id: str, feedback_id: str) -> None:
    """Soft delete: the note is hidden everywhere, the row stays, and the audit entry holds its text, who removed it and when."""
    row, candidate = _own(db, user, candidate_id, feedback_id)
    now = datetime.now(timezone.utc)
    row.deleted_at = now
    row.deleted_by = user.id
    db.add(AuditEntry(actor_id=user.id, action="Application form: feedback deleted", target=candidate.name,
                      entity_type="candidate", entity_id=candidate_id, at=now,
                      meta={"feedback_id": row.id, "kind": row.kind, "reason": row.reason, "stage": row.stage, "author_id": row.author_id,
                            "text": row.body, "deleted_at": now.isoformat()}))
