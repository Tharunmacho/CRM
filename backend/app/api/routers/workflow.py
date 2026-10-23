"""Task automation controls and recipient-owned notification actions."""
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.schemas import NotificationOut, TaskOut
from app.core.config import get_settings
from app.core.deps import current_user, require_role
from app.db.models import AuditEntry, Notification, Role, Task, TaskEvent, User, WorkflowSettings
from app.db.session import get_db
from app.services.workflow_scheduler import add_event, settings_row

router = APIRouter(prefix="/api", tags=["workflow"])


@router.get("/workflow/settings")
def workflow_settings(db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin, Role.manager))):
    # Reading the row is one statement; it is only created (an idempotent insert) the first time.
    config = db.scalar(select(WorkflowSettings).where(WorkflowSettings.id == 1)) or settings_row(db)
    result = {"escalationRepeatHours": config.escalation_repeat_hours,
              "lastRunAt": config.last_run_at, "lastDailyCheckDate": config.last_daily_check_date,
              "agencyTimezone": get_settings().agency_timezone}
    return result


class SettingsIn(BaseModel):
    escalationRepeatHours: Literal[0, 24]


@router.patch("/workflow/settings")
def update_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    config = settings_row(db)
    config.escalation_repeat_hours, config.updated_by = body.escalationRepeatHours, user.id
    db.add(AuditEntry(actor_id=user.id, action="Changed escalation reminders", entity_type="workflow_settings", entity_id="1", meta=body.model_dump()))
    db.commit()
    return {"escalationRepeatHours": config.escalation_repeat_hours}


class ResetIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/tasks/{task_id}/reset-sla")
def reset_sla(task_id: str, body: ResetIn, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.completed_at or task.cancelled_at:
        raise HTTPException(409, "A closed task cannot have its deadline reset")
    now = datetime.now(timezone.utc)
    task.sla_started_at, task.due_at = now, now + timedelta(hours=48)
    task.escalated, task.escalated_at = False, None
    add_event(db, task, "sla_reset", f"reset:{task.id}:{now.isoformat()}", now, {"reason": body.reason, "actorId": user.id})
    db.add(AuditEntry(actor_id=user.id, action="Reset task SLA", entity_type="task", entity_id=task.id, target=task.title, meta={"reason": body.reason}))
    db.commit()
    return TaskOut.model_validate(task)


@router.get("/tasks/missed-updates")
def missed_updates(day: date, staff: str | None = None, db: Session = Depends(get_db),
                   user: User = Depends(require_role(Role.admin, Role.manager))):
    query = select(TaskEvent.task_id).where(
        TaskEvent.kind == "missed_update", TaskEvent.meta["agencyDate"].astext == day.isoformat())
    if staff and staff != "all":
        query = query.where(TaskEvent.owner_id == staff)
    return {"day": day.isoformat(), "taskIds": list(db.scalars(query.order_by(TaskEvent.task_id)))}


@router.get("/notifications")
def notifications(limit: int = 100, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc(), Notification.id).limit(max(1, min(limit, 200))))
    return [NotificationOut.model_validate(row) for row in rows]


@router.post("/notifications/read-all")
def read_all(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read.is_(False)).values(read=True))
    db.commit()
    return {"ok": True}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id).with_for_update())
    if row is None:
        raise HTTPException(404, "Notification not found")
    row.read = True
    db.commit()
    return NotificationOut.model_validate(row)
