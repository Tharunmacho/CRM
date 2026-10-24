"""Transactional application lifecycle, typed stage drafts and immutable attempt history."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.api.schemas import Wire
from app.core.config import get_settings
from app.core.deps import current_user, ensure_can_work, require_role
from app.db.session import get_db
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, Attachment, AuditEntry,
    Candidate, CandidateStatus, ClosureReason, Order, OrderStatus, Role, Task, TaskKind, User,
)
from app.domain.orders import reopen_blocker
from app.domain.stages import FILE_FIELDS, TOTAL_STAGES, stage_by_no, validate_payload
from app.services import document_storage

router = APIRouter(prefix="/api/applications", tags=["applications"])


class MapIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    candidate_id: str = Field(min_length=1, max_length=36)
    order_id: str = Field(min_length=1, max_length=36)


class MapOut(Wire):
    id: str
    candidate_id: str
    order_id: str
    current_stage: int
    task_id: str
    closed_initial_call: bool


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any] = Field(default_factory=dict)
    lock_version: int = Field(ge=0)


class DraftOut(Wire):
    id: str
    draft: dict[str, Any]
    lock_version: int
    draft_saved_at: datetime | None
    draft_saved_by: str | None


class CompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_version: int = Field(ge=0)
    data: dict[str, Any] | None = None


class ReopenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_version: int = Field(ge=0)
    stage: int = Field(ge=1, le=TOTAL_STAGES)
    reason: str = Field(min_length=1, max_length=2000)


class UnmapIn(BaseModel):
    lock_version: int | None = Field(default=None, ge=0)


class CompleteOut(Wire):
    id: str
    status: str
    current_stage: int
    closure_reason: str | None
    lock_version: int
    next_task_id: str | None


@contextmanager
def _mutation(db: Session):
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Someone else changed this record while you were working. Refresh the page and try again.") from exc
    except OperationalError as exc:
        db.rollback()
        code = getattr(exc.orig, "sqlstate", None)
        if code in {"40001", "40P01", "55P03"}:
            raise HTTPException(409, "Another change is being saved right now. Refresh the page and try again.") from exc
        raise HTTPException(503, "The change could not be confirmed. Refresh the record before retrying.") from exc
    except Exception:
        db.rollback()
        raise


def _lock(db: Session, model, identifier: str, label: str):
    row = db.scalar(select(model).where(model.id == identifier).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, f"{label} not found")
    return row


def _load(db: Session, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(404, "Application not found")
    return app


def _locked_application(db: Session, application_id: str):
    identity = db.execute(select(Application.candidate_id, Application.order_id).where(Application.id == application_id)).one_or_none()
    if identity is None:
        raise HTTPException(404, "Application not found")
    # Shared with mapping/reassignment: never lock Application before Candidate.
    candidate = _lock(db, Candidate, identity.candidate_id, "Candidate")
    order = _lock(db, Order, identity.order_id, "Order")
    app = _lock(db, Application, application_id, "Application")
    return app, candidate, order


def _check_version(app: Application, sent: int) -> None:
    if app.lock_version != sent:
        raise HTTPException(409, {
            "message": "This application changed while you were editing. Your unsaved values are still on screen.",
            "lock_version": app.lock_version, "draft": app.draft,
            "current_stage": app.current_stage, "status": app.status.value,
        })


def _active(app: Application):
    if app.status != ApplicationStatus.active:
        raise HTTPException(409, "This candidate is no longer active on this order. Refresh the page to see the latest.")


def _owner(db: Session, candidate: Candidate) -> User:
    owner = db.get(User, candidate.owner_id) if candidate.owner_id else None
    if owner is None or not owner.active:
        raise HTTPException(409, "Assign this candidate to an active recruiter before starting or continuing stage work.")
    return owner


def _open_tasks(db: Session, app: Application):
    return list(db.scalars(select(Task).where(
        Task.application_id == app.id, Task.completed_at.is_(None), Task.cancelled_at.is_(None),
    ).with_for_update()))


def _task(db: Session, app: Application, candidate: Candidate, at: datetime, *, remedical_date: str | None = None):
    _owner(db, candidate)
    definition = stage_by_no(app.current_stage)
    task = Task(
        kind=TaskKind.remedical if remedical_date else TaskKind.stage,
        title=f"Follow up re-medical on {remedical_date}" if remedical_date else definition.task_title,
        candidate_id=candidate.id, application_id=app.id, stage=app.current_stage,
        owner_id=candidate.owner_id, created_at=at, sla_started_at=at, due_at=at + timedelta(hours=48),
    )
    db.add(task)
    db.flush()
    return task


def _audit(db, user, action, app, candidate, at, meta=None):
    db.add(AuditEntry(actor_id=user.id, action=action, target=candidate.name,
                      entity_type="application", entity_id=app.id, at=at, meta=meta))


def _out(app: Application, next_task: Task | None = None) -> CompleteOut:
    return CompleteOut(
        id=app.id, status=app.status.value, current_stage=app.current_stage,
        closure_reason=app.closure_reason.value if app.closure_reason else None,
        lock_version=app.lock_version, next_task_id=next_task.id if next_task else None,
    )


def _draft_out(app):
    return DraftOut(id=app.id, draft=app.draft or {}, lock_version=app.lock_version,
                    draft_saved_at=app.draft_saved_at, draft_saved_by=app.draft_saved_by)


def _sync_order(db: Session, order: Order):
    # The order lock serializes deployment and reopening across different candidates.
    db.flush()
    count = db.scalar(select(func.count()).select_from(Application).where(
        Application.order_id == order.id, Application.status == ApplicationStatus.deployed,
    )) or 0
    if count >= order.openings:
        order.status = OrderStatus.filled
    elif order.status == OrderStatus.filled:
        order.status = OrderStatus.open


def _normalized_data(db: Session, app: Application, payload: dict, *, require_bytes=False):
    data = dict(payload)
    for field in FILE_FIELDS.get(app.current_stage, ()):
        raw = data.get(field)
        if raw in (None, ""):
            continue
        # Old saved drafts can contain a storage key or URL. Resolve it to a real row.
        identifier = raw.get("id") if isinstance(raw, dict) else None
        if isinstance(raw, str):
            identifier = raw.removeprefix("/api/files/") if raw.startswith("/api/files/") else None
        attachment = db.get(Attachment, identifier) if identifier else (
            db.scalar(select(Attachment).where(Attachment.object_key == raw)) if isinstance(raw, str) else None
        )
        expected = f"stage_{app.current_stage}_{field}"
        owned = attachment is not None and (
            (attachment.entity_type == "application" and attachment.entity_id == app.id
             and attachment.slot in {expected, field})
            or (app.current_stage == 7 and field == "copy" and attachment.entity_type == "candidate"
                and attachment.entity_id == app.candidate_id and attachment.slot == "passport")
        )
        if not owned:
            raise HTTPException(422, {"message": f"Choose an uploaded {field} belonging to this application and stage.", "missing": [field]})
        # A legacy generic slot is accepted only when this stage already referenced it.
        if attachment.entity_type == "application" and attachment.slot == field:
            old_values = [app.draft or {}]
            old_values += [row.data for row in db.scalars(select(ApplicationStage).where(
                ApplicationStage.application_id == app.id, ApplicationStage.stage == app.current_stage,
            ))]
            def refers(value):
                value = value.get(field)
                return ((isinstance(value, dict) and value.get("id") == attachment.id)
                        or value in (attachment.object_key, f"/api/files/{attachment.id}"))
            if not any(refers(value) for value in old_values):
                raise HTTPException(422, "This is an old file. Upload it again in this stage before you use it.")
        if require_bytes:
            try:
                available = document_storage.exists(get_settings(), attachment.object_key)
            except (ValueError, FileNotFoundError):
                available = False
            except Exception:
                raise HTTPException(503, "We cannot reach file storage right now. Try again in a few minutes.") from None
            if not available:
                raise HTTPException(422, {"message": f"The stored {field} is unavailable. Upload a replacement before completing.", "missing": [field]})
        data[field] = {"id": attachment.id, "name": attachment.file_name,
                       "url": f"/api/files/{attachment.id}", "sizeKb": attachment.size_kb,
                       "version": attachment.version}
    if app.current_stage == 7 and data.get("heldBy"):
        raw = data["heldBy"]
        identifier = raw.get("id") if isinstance(raw, dict) else None
        keeper = db.get(User, identifier) if identifier else None
        if keeper is None and isinstance(raw, str):
            # Resolve legacy names only when they identify exactly one current user.
            matches = list(db.scalars(select(User).where(User.name == raw.strip())))
            keeper = matches[0] if len(matches) == 1 else None
        if keeper is None or not keeper.active or keeper.role != Role.staff:
            raise HTTPException(422, "Choose an active staff member who is holding the passport.")
        data["heldBy"] = {"id": keeper.id, "name": keeper.name}
    try:
        return validate_payload(app.current_stage, data)
    except ValidationError as exc:
        errors = [{"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
                  for item in exc.errors()]
        raise HTTPException(422, {"message": "Check the stage values before saving.", "errors": errors,
                                  "missing": [f'{item["field"]}: {item["message"]}' for item in errors]}) from exc


@router.post("", response_model=MapOut, status_code=status.HTTP_201_CREATED)
def map_candidate(body: MapIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    with _mutation(db):
        candidate = _lock(db, Candidate, body.candidate_id, "Candidate")
        order = _lock(db, Order, body.order_id, "Order")
        owner = _owner(db, candidate)
        if order.status != OrderStatus.open:
            raise HTTPException(409, "Open this order before matching candidates.")
        if candidate.status == CandidateStatus.deployed:
            raise HTTPException(409, "This candidate already has a deployed placement.")
        if db.scalar(select(Application.id).where(
            Application.candidate_id == candidate.id, Application.status == ApplicationStatus.active,
        )):
            raise HTTPException(409, "This candidate is already on an order. Unmap them first.")
        at = datetime.now(timezone.utc)
        app = Application(candidate_id=candidate.id, order_id=order.id, owner_id=owner.id,
                          current_stage=1, status=ApplicationStatus.active, lock_version=0, draft={})
        db.add(app)
        db.flush()
        closed_initial_call = False
        for task in db.scalars(select(Task).where(
            Task.candidate_id == candidate.id, Task.kind == TaskKind.initial_call,
            Task.completed_at.is_(None), Task.cancelled_at.is_(None),
        ).with_for_update()):
            task.cancelled_at = at
            closed_initial_call = True
        task = _task(db, app, candidate, at)
        candidate.status = CandidateStatus.in_process
        _audit(db, user, "Mapped candidate to order", app, candidate, at, {"order_id": order.id, "owner_id": owner.id})
    return MapOut(id=app.id, candidate_id=candidate.id, order_id=order.id, current_stage=1,
                  task_id=task.id, closed_initial_call=closed_initial_call)


@router.post("/{application_id}/unmap")
def unmap(application_id: str, body: UnmapIn | None = None,
          db: Session = Depends(get_db), user: User = Depends(current_user)):
    with _mutation(db):
        app, candidate, order = _locked_application(db, application_id)
        _active(app)
        if body and body.lock_version is not None:
            _check_version(app, body.lock_version)
        at = datetime.now(timezone.utc)
        app.status, app.closure_reason = ApplicationStatus.closed, ClosureReason.unmapped
        app.lock_version += 1
        for task in _open_tasks(db, app):
            task.cancelled_at = at
        candidate.status = CandidateStatus.needs_review if candidate.needs_review else CandidateStatus.new
        _audit(db, user, "Unmapped candidate", app, candidate, at)
    return {"id": app.id, "status": app.status.value, "lockVersion": app.lock_version}


@router.get("/{application_id}/draft", response_model=DraftOut)
def read_draft(application_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    app = _load(db, application_id)
    ensure_can_work(db, app, user)
    return _draft_out(app)


@router.put("/{application_id}/draft", response_model=DraftOut)
def save_draft(application_id: str, body: DraftIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    with _mutation(db):
        app, candidate, _ = _locked_application(db, application_id)
        ensure_can_work(db, app, user)
        _active(app)
        _check_version(app, body.lock_version)
        app.draft = _normalized_data(db, app, body.data)
        app.draft_saved_at, app.draft_saved_by = datetime.now(timezone.utc), user.id
        app.lock_version += 1
        _audit(db, user, f"Saved stage {app.current_stage} draft", app, candidate,
               app.draft_saved_at, {"stage": app.current_stage, "lock_version": app.lock_version})
    return _draft_out(app)


@router.post("/{application_id}/complete", response_model=CompleteOut)
def complete_stage(application_id: str, body: CompleteIn,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    with _mutation(db):
        app, candidate, order = _locked_application(db, application_id)
        ensure_can_work(db, app, user)
        _active(app)
        _check_version(app, body.lock_version)
        _owner(db, candidate)
        data = _normalized_data(db, app, body.data if body.data is not None else app.draft or {}, require_bytes=True)
        definition = stage_by_no(app.current_stage)
        result = definition.evaluate(data)
        if not result.can_complete:
            raise HTTPException(422, {"message": "This stage is not complete.", "missing": result.missing})
        if app.current_stage == TOTAL_STAGES:
            deployed = db.scalar(select(func.count()).select_from(Application).where(
                Application.order_id == order.id, Application.status == ApplicationStatus.deployed,
            )) or 0
            if deployed >= order.openings:
                raise HTTPException(409, "All openings on this order are already filled. Add more openings, or sort out an existing placement, before you finish this stage.")
        at = datetime.now(timezone.utc)
        attempt = (db.scalar(select(func.max(ApplicationStage.attempt)).where(
            ApplicationStage.application_id == app.id, ApplicationStage.stage == app.current_stage,
        )) or 0) + 1
        db.add(ApplicationStage(
            application_id=app.id, stage=app.current_stage, attempt=attempt,
            data=data, outcome=result.outcome, completed_by=user.id, completed_at=at,
            owner_id=candidate.owner_id or app.owner_id,
            amount=data.get("amount") if app.current_stage == 8 else None,
            amount_currency=data.get("amountCurrency", "INR") if app.current_stage == 8 else None,
        ))
        for task in _open_tasks(db, app):
            if task.stage == app.current_stage:
                task.completed_at = at
            else:
                task.cancelled_at = at
        next_task = None
        app.draft = {}
        if result.closes:
            app.status, app.closure_reason = ApplicationStatus.closed, ClosureReason(result.closes)
            candidate.status = (CandidateStatus.not_interested if app.current_stage == 2
                                else CandidateStatus.needs_review if candidate.needs_review else CandidateStatus.new)
        elif result.repeats:
            app.draft = {"applied": "yes", "appliedDate": data["retestDate"]}
            next_task = _task(db, app, candidate, at, remedical_date=data["retestDate"])
        elif app.current_stage == TOTAL_STAGES:
            app.status = ApplicationStatus.deployed
            candidate.status = CandidateStatus.deployed
            _sync_order(db, order)
        else:
            app.current_stage += 1
            if app.current_stage == 7:
                passport = db.scalar(select(Attachment).where(
                    Attachment.entity_type == "candidate", Attachment.entity_id == candidate.id,
                    Attachment.slot == "passport",
                ).order_by(Attachment.version.desc()).limit(1))
                if passport:
                    app.draft = {"copy": {"id": passport.id, "name": passport.file_name,
                                         "url": f"/api/files/{passport.id}", "version": passport.version}}
            next_task = _task(db, app, candidate, at)
        app.draft_saved_at, app.draft_saved_by = (at, user.id) if app.draft else (None, None)
        app.lock_version += 1
        _audit(db, user, f"Recorded stage {definition.no}: {result.outcome}", app, candidate, at,
               {"stage": definition.no, "attempt": attempt, "outcome": result.outcome,
                "closes": result.closes, "repeats": result.repeats})
    return _out(app, next_task)


@router.post("/{application_id}/reopen", response_model=CompleteOut)
def reopen_stage(application_id: str, body: ReopenIn, db: Session = Depends(get_db),
                 user: User = Depends(require_role(Role.admin))):
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "Please give a reason for reopening a completed stage.")
    with _mutation(db):
        app, candidate, order = _locked_application(db, application_id)
        _check_version(app, body.lock_version)
        _owner(db, candidate)
        blocker = reopen_blocker(order.status)
        if blocker:
            raise HTTPException(409, blocker)
        if app.status == ApplicationStatus.active and body.stage >= app.current_stage:
            raise HTTPException(409, "Choose a stage that was completed before the current stage.")
        if db.scalar(select(Application.id).where(
            Application.candidate_id == candidate.id, Application.id != app.id,
            Application.status.in_([ApplicationStatus.active, ApplicationStatus.deployed]),
        )):
            raise HTTPException(409, "This candidate has another active or deployed placement. Resolve that placement first.")
        records = list(db.scalars(select(ApplicationStage).where(
            ApplicationStage.application_id == app.id, ApplicationStage.stage >= body.stage,
            ApplicationStage.reopened_reason.is_(None),
        ).order_by(ApplicationStage.stage, ApplicationStage.attempt).with_for_update()))
        targets = [row for row in records if row.stage == body.stage and row.outcome != "Unfit: reapplying"]
        if not targets:
            raise HTTPException(409, "That stage has not been completed, so there is nothing to reopen.")
        at = datetime.now(timezone.utc)
        previous_status = app.status.value
        app.draft = dict(targets[-1].data)
        for record in records:
            record.reopened_reason = reason
        for task in _open_tasks(db, app):
            task.cancelled_at = at
        app.status, app.closure_reason, app.current_stage = ApplicationStatus.active, None, body.stage
        app.draft_saved_at, app.draft_saved_by = at, user.id
        app.lock_version += 1
        candidate.status = CandidateStatus.in_process
        next_task = _task(db, app, candidate, at)
        _sync_order(db, order)
        _audit(db, user, f"Reopened stage {body.stage}", app, candidate, at, {
            "stage": body.stage, "reason": reason, "previous_status": previous_status,
            "superseded_attempts": [{"stage": row.stage, "attempt": row.attempt} for row in records],
        })
    return _out(app, next_task)
