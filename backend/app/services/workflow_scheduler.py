"""Repeat-safe task monitoring. All times are stored in UTC; days use agency time."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Candidate, Notification, Remark, Role, Task, TaskEvent, TodoItem, User, WorkflowSettings


def task_deadline(task):
    return task.due_at or (task.sla_started_at or task.created_at) + timedelta(hours=48)


def overdue_detail(candidate, task, owner, hours):
    """Candidate, stage, owner and hours overdue (PRD p11), for the escalation and its reminders."""
    stage = f"Stage {task.stage} · " if task.stage is not None else ""
    return f"{candidate} · {stage}{task.title} · Owner: {owner} · {hours} hours overdue"


def settings_row(db):
    db.execute(insert(WorkflowSettings).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return db.scalar(select(WorkflowSettings).where(WorkflowSettings.id == 1).with_for_update())


def add_event(db, task, kind, key, at, meta=None, *, owner_id=None):
    event_id = db.scalar(insert(TaskEvent).values(
        task_id=task.id, candidate_id=task.candidate_id, owner_id=owner_id or task.owner_id,
        kind=kind, event_key=key, occurred_at=at, meta=meta,
    ).on_conflict_do_nothing(index_elements=["event_key"]).returning(TaskEvent.id))
    return bool(event_id)


def restart_sla(db, task, now, actor_id, reason):
    """Admin-chosen restart of the 48-hour clock (shared by reset-sla and reassignment)."""
    task.sla_started_at, task.due_at = now, now + timedelta(hours=48)
    task.escalated, task.escalated_at = False, None
    add_event(db, task, "sla_reset", f"reset:{task.id}:{now.isoformat()}", now, {"reason": reason, "actorId": actor_id})


def notify(db, recipients, kind, title, detail, link):
    for recipient in sorted(set(recipients)):
        db.add(Notification(user_id=recipient, kind=kind, title=title, detail=detail, link_to=link))


def deliver_due_todos(db, *, now=None):
    """Small independent scan; reminder and delivery marker commit together."""
    now = now or datetime.now(timezone.utc)
    count = 0
    for todo in db.scalars(select(TodoItem).where(
        TodoItem.done.is_(False), TodoItem.reminded_at.is_(None), TodoItem.due_at <= now,
    ).with_for_update(skip_locked=True)):
        notify(db, [todo.owner_id], "todo", "To-do due", todo.title, todo.link_to or "/todo")
        todo.reminded_at = now
        count += 1
    return count


def scan_workflow(db, *, now=None, agency_timezone="Asia/Kolkata"):
    """Caller commits once; failures leave both notifications and checkpoint unapplied."""
    now = now or datetime.now(timezone.utc)
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(724681930119)")):
        return {"busy": True}
    config = settings_row(db)
    supervisors = list(db.scalars(select(User.id).where(User.active.is_(True), User.role.in_([Role.admin, Role.manager]))))
    names = dict(db.execute(select(Candidate.id, Candidate.name)).all())
    owner_names = dict(db.execute(select(User.id, User.name)).all())
    counts = {"escalations": 0, "reminders": 0, "missedUpdates": 0, "todos": 0}
    tasks = list(db.scalars(select(Task).where(Task.completed_at.is_(None), Task.cancelled_at.is_(None)).order_by(Task.id).with_for_update(skip_locked=True)))
    for task in tasks:
        due = task_deadline(task)
        if now <= due:
            continue
        kind, key = "escalation", f"escalation:{task.id}:{due.isoformat()}"
        if task.escalated:
            if not config.escalation_repeat_hours or not task.escalated_at:
                continue
            period = int((now - task.escalated_at).total_seconds() // (config.escalation_repeat_hours * 3600))
            if period < 1:
                continue
            kind, key = "reminder", f"reminder:{task.id}:{due.isoformat()}:{period}"
        if not add_event(db, task, kind, key, now, {"dueAt": due.isoformat()}):
            continue
        if kind == "escalation":
            task.escalated, task.escalated_at = True, now
        counts["escalations" if kind == "escalation" else "reminders"] += 1
        overdue = max(0, int((now - due).total_seconds() // 3600))
        # PRD role table: only Admin and Manager receive 48-hour escalations; Staff do not
        # (the owner still sees the task overdue in Tasks / Dashboard).
        notify(db, supervisors, "escalation",
               "Task overdue" if kind == "escalation" else "Overdue task reminder",
               overdue_detail(names.get(task.candidate_id, "Candidate"), task, owner_names.get(task.owner_id, "Unassigned"), overdue),
               f"/tasks?task={task.id}")

    zone = ZoneInfo(agency_timezone)
    yesterday = now.astimezone(zone).date() - timedelta(days=1)
    day = config.last_daily_check_date + timedelta(days=1) if config.last_daily_check_date else yesterday
    # Check every unprocessed day after downtime. A first deployment starts with yesterday.
    while day <= yesterday:
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        active = list(db.scalars(select(Task).where(
            Task.created_at < end,
            or_(Task.completed_at.is_(None), Task.completed_at >= end),
            or_(Task.cancelled_at.is_(None), Task.cancelled_at >= end),
        )))
        updated = set(db.execute(select(Remark.task_id, Remark.author_id).where(Remark.created_at >= start, Remark.created_at < end)).all())
        # A catch-up scan can run after a reassignment. The first assignment at
        # or after the day boundary records who owned the task just before it.
        # Earlier legacy transfers without events cannot be reconstructed.
        historical_owners = {}
        if active:
            assignments = db.scalars(select(TaskEvent).where(
                TaskEvent.kind == "assignment", TaskEvent.occurred_at >= end,
                TaskEvent.task_id.in_([task.id for task in active]),
            ).order_by(TaskEvent.occurred_at, TaskEvent.id))
            for event in assignments:
                previous_owner = (event.meta or {}).get("fromOwnerId")
                if previous_owner:
                    historical_owners.setdefault(event.task_id, previous_owner)
        grouped = defaultdict(list)
        for task in active:
            owner_id = historical_owners.get(task.id, task.owner_id)
            if (task.id, owner_id) in updated:
                continue
            if add_event(db, task, "missed_update", f"missed:{task.id}:{day.isoformat()}", end - timedelta(microseconds=1), {"agencyDate": day.isoformat()}, owner_id=owner_id):
                grouped[owner_id].append(task.id)
                counts["missedUpdates"] += 1
        for owner, task_ids in grouped.items():
            name = db.scalar(select(User.name).where(User.id == owner)) or "Staff"
            notify(db, supervisors, "missed_update", f"{name}: daily updates missing",
                   f"{len(task_ids)} open tasks had no owner update on {day.isoformat()}.", f"/tasks?staff={owner}&date={day.isoformat()}")
        config.last_daily_check_date = day
        day += timedelta(days=1)

    counts["todos"] = deliver_due_todos(db, now=now)
    config.last_run_at = now
    return counts
