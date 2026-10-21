from datetime import date
from sqlalchemy import select
from app.db.models import Task, TaskEvent, User
from app.api.routers.data import ReassignIn, reassign
from app.api.routers.workflow import missed_updates
from test_workflow_scheduler import NOW, workspace, add_task
from test_database_naming import schema_database


def test_missed_update_drilldown_keeps_historical_owner_after_transfer(workspace):
    with workspace() as db:
        task = add_task(db)
        db.add(TaskEvent(task_id=task.id, candidate_id=task.candidate_id, owner_id="owner", kind="missed_update",
                         event_key="missed:test", occurred_at=NOW, meta={"agencyDate": "2026-10-20"}))
        db.commit()
        deadline = task.due_at
        reassign(ReassignIn(candidateIds=["candidate"], toUserId="other"), db, db.get(User, "admin"))
        assert task.owner_id == "other" and task.due_at == deadline
        event = db.scalar(select(TaskEvent).where(TaskEvent.kind == "assignment"))
        assert event.owner_id == "owner" and event.meta["toOwnerId"] == "other"
        assert missed_updates(date(2026, 10, 20), "owner", db, db.get(User, "manager"))["taskIds"] == ["task"]
        assert missed_updates(date(2026, 10, 20), "other", db, db.get(User, "manager"))["taskIds"] == []
        assert missed_updates(date(2026, 10, 19), "owner", db, db.get(User, "manager"))["taskIds"] == []
