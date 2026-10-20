"""
Stage editing.

Two operations, deliberately separate:

* **save** keeps work in progress on `applications.draft`. Pressing Save is the only
  thing that writes it — there is no autosave — so the endpoint has to be dependable:
  it refuses a stale write rather than letting one person's edits quietly replace
  another's.
* **complete** validates the stage against the server's own rules, writes the permanent
  record, closes the stage's task, opens the next one, and advances the order — all in
  one transaction, so the pipeline can never be left half-moved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.schemas import Wire
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.deps import current_user, ensure_can_work
from app.db.models import (
    Application,
    ApplicationStage,
    ApplicationStatus,
    AuditEntry,
    Candidate,
    CandidateStatus,
    ClosureReason,
    Order,
    OrderStatus,
    Task,
    TaskKind,
    User,
)
from app.domain.currencies import DEFAULT_ADVANCE_CURRENCY
from app.domain.stages import TOTAL_STAGES, stage_by_no

router = APIRouter(prefix="/api/applications", tags=["applications"])


class MapIn(BaseModel):
    candidate_id: str
    order_id: str


class MapOut(Wire):
    id: str
    candidate_id: str
    order_id: str
    current_stage: int
    task_id: str
    closed_initial_call: bool


@router.post("", response_model=MapOut, status_code=status.HTTP_201_CREATED)
def map_candidate(
    body: MapIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> MapOut:
    """
    Match a candidate to an order. The partial unique index on the table is what really
    guarantees one active application at a time; this check only turns the resulting
    integrity error into a readable message.
    """
    candidate = db.get(Candidate, body.candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    if db.get(Order, body.order_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    existing = db.scalar(
        select(Application).where(
            Application.candidate_id == candidate.id,
            Application.status == ApplicationStatus.active,
        )
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This candidate is already on another order. Unmap them first.",
        )

    now = datetime.now(timezone.utc)
    app = Application(candidate_id=candidate.id, order_id=body.order_id, current_stage=1)
    db.add(app)
    db.flush()

    # Mapping supersedes the initial call, so that task is withdrawn rather than left
    # running a second 48-hour clock alongside the stage 1 task. Cancelled, not
    # completed: the call never happened, and counting it as done would flatter the
    # staff performance report.
    closed_initial_call = False
    for call_task in db.scalars(
        select(Task).where(
            Task.candidate_id == candidate.id,
            Task.kind == TaskKind.initial_call,
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        )
    ):
        call_task.cancelled_at = now
        closed_initial_call = True

    first = stage_by_no(1)
    task = Task(
        kind=TaskKind.stage,
        title=first.task_title,
        candidate_id=candidate.id,
        application_id=app.id,
        stage=1,
        owner_id=candidate.owner_id or user.id,
        created_at=now,
    )
    db.add(task)

    if candidate.status is CandidateStatus.new:
        candidate.status = CandidateStatus.in_process

    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Mapped candidate to order",
            target=candidate.name,
            entity_type="application",
            entity_id=app.id,
        )
    )
    db.flush()
    db.commit()

    return MapOut(
        id=app.id,
        candidate_id=candidate.id,
        order_id=body.order_id,
        current_stage=1,
        task_id=task.id,
        closed_initial_call=closed_initial_call,
    )


@router.post("/{application_id}/unmap", status_code=status.HTTP_200_OK)
def unmap(
    application_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Close the application with reason Unmapped and withdraw its open tasks."""
    app = _load(db, application_id)
    if app.status is not ApplicationStatus.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This application is already closed.")

    now = datetime.now(timezone.utc)
    app.status = ApplicationStatus.closed
    app.closure_reason = ClosureReason.unmapped
    app.lock_version += 1

    for open_task in db.scalars(
        select(Task).where(
            Task.application_id == app.id,
            Task.completed_at.is_(None),
            Task.cancelled_at.is_(None),
        )
    ):
        open_task.cancelled_at = now

    db.add(
        AuditEntry(
            actor_id=user.id,
            action="Unmapped candidate",
            target=app.candidate_id,
            entity_type="application",
            entity_id=app.id,
        )
    )
    db.commit()
    return {"id": app.id, "status": app.status.value}


# ------------------------------------------------------------------ schemas

class DraftIn(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    # The version the client loaded. A mismatch means someone else saved in between.
    lock_version: int


class DraftOut(Wire):
    id: str
    draft: dict[str, Any]
    lock_version: int
    draft_saved_at: datetime | None
    draft_saved_by: str | None


class CompleteIn(BaseModel):
    lock_version: int


class CompleteOut(Wire):
    id: str
    status: str
    current_stage: int
    closure_reason: str | None
    lock_version: int
    next_task_id: str | None


# ------------------------------------------------------------------ helpers

def _load(db: Session, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return app


def _check_version(app: Application, sent: int) -> None:
    if app.lock_version != sent:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "This stage was updated by someone else while you were editing.",
                "lock_version": app.lock_version,
                "draft": app.draft,
            },
        )


def _open_task_for(db: Session, app: Application, stage: int | None = None) -> Task | None:
    stmt = select(Task).where(
        Task.application_id == app.id,
        Task.completed_at.is_(None),
        Task.cancelled_at.is_(None),
    )
    if stage is not None:
        stmt = stmt.where(Task.stage == stage)
    return db.scalars(stmt).first()


# ------------------------------------------------------------------- routes

@router.get("/{application_id}/draft", response_model=DraftOut)
def read_draft(
    application_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DraftOut:
    app = _load(db, application_id)
    return DraftOut(
        id=app.id,
        draft=app.draft or {},
        lock_version=app.lock_version,
        draft_saved_at=app.draft_saved_at,
        draft_saved_by=app.draft_saved_by,
    )


@router.put("/{application_id}/draft", response_model=DraftOut)
def save_draft(
    application_id: str,
    body: DraftIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DraftOut:
    """Save work in progress. Explicit — nothing here runs on its own."""
    app = _load(db, application_id)
    ensure_can_work(db, app, user)

    if app.status is not ApplicationStatus.active:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This application is closed and cannot be edited."
        )

    _check_version(app, body.lock_version)

    app.draft = body.data
    app.draft_saved_at = datetime.now(timezone.utc)
    app.draft_saved_by = user.id
    app.lock_version += 1
    db.commit()

    return DraftOut(
        id=app.id,
        draft=app.draft,
        lock_version=app.lock_version,
        draft_saved_at=app.draft_saved_at,
        draft_saved_by=app.draft_saved_by,
    )


@router.post("/{application_id}/complete", response_model=CompleteOut)
def complete_stage(
    application_id: str,
    body: CompleteIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> CompleteOut:
    """
    Commit the current stage. Everything below happens in one transaction: either the
    candidate moves on completely, or nothing changes.
    """
    app = _load(db, application_id)
    ensure_can_work(db, app, user)

    if app.status is not ApplicationStatus.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This application is already closed.")

    _check_version(app, body.lock_version)

    definition = stage_by_no(app.current_stage)
    data = app.draft or {}
    result = definition.evaluate(data)

    # The browser greys out the button, but the decision is made here.
    if not result.can_complete:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "This stage is not complete.", "missing": result.missing},
        )

    now = datetime.now(timezone.utc)
    candidate = db.get(Candidate, app.candidate_id)

    attempt = (
        db.scalar(
            select(ApplicationStage.attempt)
            .where(
                ApplicationStage.application_id == app.id,
                ApplicationStage.stage == app.current_stage,
            )
            .order_by(ApplicationStage.attempt.desc())
            .limit(1)
        )
        or 0
    ) + 1

    amount = data.get("amount") if app.current_stage == 8 else None
    amount_currency = (
        (data.get("amountCurrency") or DEFAULT_ADVANCE_CURRENCY)
        if app.current_stage == 8 and amount
        else None
    )

    db.add(
        ApplicationStage(
            application_id=app.id,
            stage=app.current_stage,
            attempt=attempt,
            data=data,
            outcome=result.outcome,
            amount=amount,
            amount_currency=amount_currency,
            completed_by=user.id,
            completed_at=now,
        )
    )

    # Close the task this stage was raised for.
    task = _open_task_for(db, app, app.current_stage)
    if task is not None:
        task.completed_at = now

    closed = result.closes is not None
    deployed = not closed and app.current_stage == TOTAL_STAGES
    next_task_id: str | None = None

    if closed:
        app.status = ApplicationStatus.closed
        app.closure_reason = ClosureReason(result.closes)
        # The candidate is unmapped and returns to the Data Bank, so nothing of theirs
        # should still be sitting in someone's task list.
        for open_task in db.scalars(
            select(Task).where(
                Task.application_id == app.id,
                Task.completed_at.is_(None),
                Task.cancelled_at.is_(None),
            )
        ):
            # The session does not autoflush, so the task completed moments ago is
            # still "open" to this query. SQLAlchemy returns the same object from its
            # identity map, so check the attribute rather than the row — otherwise a
            # task ends up both completed and cancelled, and is counted twice.
            if open_task.completed_at is None:
                open_task.cancelled_at = now
        if result.closes == ClosureReason.not_interested.value and candidate is not None:
            candidate.status = CandidateStatus.not_interested

    elif deployed:
        app.status = ApplicationStatus.deployed
        if candidate is not None:
            candidate.status = CandidateStatus.deployed
        order = db.get(Order, app.order_id)
        if order is not None:
            deployed_count = db.scalar(
                select(func.count())
                .select_from(Application)
                .where(
                    Application.order_id == order.id,
                    Application.status == ApplicationStatus.deployed,
                )
            )
            # The row above is not committed yet, so count it in.
            if (deployed_count or 0) + 1 >= order.openings:
                order.status = OrderStatus.filled

    else:
        app.current_stage += 1
        nxt = stage_by_no(app.current_stage)
        new_task = Task(
            kind=TaskKind.stage,
            title=nxt.task_title,
            candidate_id=app.candidate_id,
            application_id=app.id,
            stage=app.current_stage,
            owner_id=candidate.owner_id if candidate and candidate.owner_id else user.id,
            created_at=now,
        )
        db.add(new_task)
        db.flush()
        next_task_id = new_task.id

    app.draft = {}
    app.draft_saved_at = None
    app.draft_saved_by = None
    app.lock_version += 1

    db.add(
        AuditEntry(
            actor_id=user.id,
            action=f"Completed stage {definition.no} — {definition.title}",
            target=f"{candidate.name if candidate else app.candidate_id}",
            entity_type="application",
            entity_id=app.id,
            meta={"outcome": result.outcome, "closes": result.closes},
            at=now,
        )
    )

    db.commit()

    return CompleteOut(
        id=app.id,
        status=app.status.value,
        current_stage=app.current_stage,
        closure_reason=app.closure_reason.value if app.closure_reason else None,
        lock_version=app.lock_version,
        next_task_id=next_task_id,
    )
