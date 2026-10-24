"""Task automation and ownership contracts on an explicitly disposable PostgreSQL DB."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Candidate, Notification, Remark, Role, Source, Task, TaskEvent, TaskKind, TodoItem, User, WorkflowSettings
from app.services.workflow_scheduler import deliver_due_todos, scan_workflow
from test_database_naming import _migrate, schema_database

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


@pytest.fixture
def workspace(schema_database):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id="admin", name="Admin", email="admin@example.test", role=Role.admin),
                    User(id="manager", name="Manager", email="manager@example.test", role=Role.manager),
                    User(id="owner", name="Owner", email="owner@example.test", role=Role.staff),
                    User(id="other", name="Other", email="other@example.test", role=Role.staff)])
        db.flush()
        db.add(Candidate(id="candidate", ref="CND-TASK", name="Synthetic candidate", phone="9000000001", source=Source.walk_in, owner_id="owner"))
        db.commit()
    return sessions


def add_task(db, task_id="task", **kwargs):
    task = Task(id=task_id, kind=TaskKind.initial_call, title="Initial call", candidate_id="candidate", owner_id="owner", created_at=NOW - timedelta(hours=49), **kwargs)
    db.add(task)
    db.commit()
    return task


def test_exact_48h_boundary_and_repeat_safe_escalation(workspace):
    with workspace() as db:
        task = add_task(db, due_at=NOW)
        assert scan_workflow(db, now=NOW)["escalations"] == 0
        db.commit()
        assert scan_workflow(db, now=NOW + timedelta(seconds=1))["escalations"] == 1
        db.commit()
        assert scan_workflow(db, now=NOW + timedelta(seconds=2))["escalations"] == 0
        db.commit()
        assert task.escalated
        recipients = set(db.scalars(select(Notification.user_id).where(Notification.kind == "escalation")))
        assert recipients == {"admin", "manager"}  # PRD role table: Staff do not receive escalations
        assert db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.kind == "escalation")) == 1


def test_overdue_notice_names_candidate_stage_owner_and_hours(workspace):
    with workspace() as db:
        add_task(db, due_at=NOW, stage=3)
        db.get(User, "owner").name = "Asha Owner"
        db.commit()
        scan_workflow(db, now=NOW + timedelta(hours=5, minutes=10))
        db.commit()
        details = set(db.scalars(select(Notification.detail).where(Notification.kind == "escalation")))
        assert details == {"Synthetic candidate · Stage 3 · Initial call · Owner: Asha Owner · 5 hours overdue"}


def test_repeat_setting_delivers_once_per_24_hour_window(workspace):
    with workspace() as db:
        add_task(db)
        scan_workflow(db, now=NOW)
        db.commit()
        assert scan_workflow(db, now=NOW + timedelta(days=1))["reminders"] == 0
        db.get(WorkflowSettings, 1).escalation_repeat_hours = 24
        db.commit()
        assert scan_workflow(db, now=NOW + timedelta(days=1))["reminders"] == 1
        db.commit()
        assert set(db.scalars(select(Notification.user_id).where(Notification.title == "Overdue task reminder"))) == {"admin", "manager"}
        assert scan_workflow(db, now=NOW + timedelta(days=1, minutes=1))["reminders"] == 0


def test_daily_updates_use_agency_day_owner_and_completion_exemption(workspace):
    with workspace() as db:
        # Yesterday in India is 19 Oct 18:30 UTC to 20 Oct 18:30 UTC.
        add_task(db, "missed")
        add_task(db, "updated")
        add_task(db, "completed", completed_at=NOW - timedelta(days=1))
        add_task(db, "cancelled", cancelled_at=NOW - timedelta(days=1))
        db.add_all([
            Remark(task_id="missed", author_id="manager", body="Supervisor note does not replace an owner's update", created_at=datetime(2026, 10, 20, 18, tzinfo=timezone.utc)),
            Remark(task_id="updated", author_id="owner", body="Owner update before India midnight", created_at=datetime(2026, 10, 20, 18, 29, tzinfo=timezone.utc)),
        ])
        db.commit()
        result = scan_workflow(db, now=NOW)
        db.commit()
        assert result["missedUpdates"] == 1
        events = list(db.scalars(select(TaskEvent).where(TaskEvent.kind == "missed_update")))
        assert [event.task_id for event in events] == ["missed"]
        assert events[0].meta["agencyDate"] == "2026-10-20"
        assert set(db.scalars(select(Notification.user_id).where(Notification.kind == "missed_update"))) == {"admin", "manager"}
        assert scan_workflow(db, now=NOW)["missedUpdates"] == 0


def test_due_todo_reminds_only_owner_once(workspace):
    with workspace() as db:
        db.add_all([TodoItem(id="due", owner_id="admin", title="Call employer", due_at=NOW),
                    TodoItem(owner_id="manager", title="Future item", due_at=NOW + timedelta(hours=1)),
                    TodoItem(owner_id="admin", title="Finished", due_at=NOW, done=True)])
        db.commit()
        assert scan_workflow(db, now=NOW)["todos"] == 1
        db.commit()
        assert scan_workflow(db, now=NOW)["todos"] == 0
        assert list(db.scalars(select(Notification.user_id).where(Notification.kind == "todo"))) == ["admin"]


def test_personal_reminder_runs_between_task_scans_and_recovers_rollback(workspace):
    with workspace() as db:
        db.add(TodoItem(id="independent-due", owner_id="admin", title="Call at the selected time", due_at=NOW))
        db.commit()
        assert deliver_due_todos(db, now=NOW - timedelta(seconds=1)) == 0
        db.commit()
        assert deliver_due_todos(db, now=NOW) == 1
        # A second worker skips the in-flight row; rollback must not lose the reminder.
        with workspace() as other:
            assert deliver_due_todos(other, now=NOW) == 0
            other.commit()
        db.rollback()
        with workspace() as other:
            assert deliver_due_todos(other, now=NOW + timedelta(seconds=30)) == 1
            other.commit()
            assert deliver_due_todos(other, now=NOW + timedelta(minutes=1)) == 0
            assert other.scalar(select(func.count()).select_from(Notification).where(Notification.kind == "todo")) == 1
            assert other.scalar(select(Notification.user_id).where(Notification.kind == "todo")) == "admin"


def test_worker_keeps_sla_cadence_while_delivering_timely_reminders(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app import workflow_worker as worker
    moments, calls, commits, waits = [0, 30, 60, 900], [], [], []
    clock = {"value": 0}
    class Stop:
        def is_set(self): return not moments
        def set(self): moments.clear()
        def wait(self, seconds):
            waits.append(seconds)
            moments.pop(0)
            if moments: clock["value"] = moments[0]
    @contextmanager
    def session():
        yield SimpleNamespace(commit=lambda: commits.append(clock["value"]))
    monkeypatch.setattr(worker.threading, "Event", Stop)
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(worker, "SessionLocal", session)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(agency_timezone="Asia/Kolkata"))
    monkeypatch.setattr(worker, "scan_workflow", lambda *_, **kw: calls.append(("tasks", clock["value"])) or {"todos": 0})
    monkeypatch.setattr(worker, "deliver_due_todos", lambda *_: calls.append(("todos", clock["value"])) or 0)
    worker.main()
    assert calls == [("tasks", 0), ("todos", 30), ("todos", 60), ("tasks", 900)]
    assert commits == [0, 30, 60, 900] and waits == [30] * 4


@pytest.mark.parametrize("assignment_at,expected_owner", [
    (datetime(2026, 10, 20, 18, 29, tzinfo=timezone.utc), "other"),
    (datetime(2026, 10, 20, 18, 30, tzinfo=timezone.utc), "owner"),
    (NOW, "owner"),
])
@pytest.mark.parametrize("has_owner_update", [False, True])
def test_daily_updates_use_owner_at_agency_day_end(workspace, assignment_at, expected_owner, has_owner_update):
    with workspace() as db:
        task = add_task(db, due_at=NOW + timedelta(days=1))
        db.add(TaskEvent(task_id=task.id, candidate_id=task.candidate_id,
                         owner_id="other", kind="assignment", event_key="transfer",
                         occurred_at=assignment_at,
                         meta={"fromOwnerId": "owner", "toOwnerId": "other"}))
        task.owner_id = "other"
        # An update by the other user must not satisfy the responsible owner's day.
        author = expected_owner if has_owner_update else ("other" if expected_owner == "owner" else "owner")
        db.add(Remark(task_id=task.id, author_id=author, body="Daily update",
                      created_at=datetime(2026, 10, 20, 18, 29, 30, tzinfo=timezone.utc)))
        db.commit()
        result = scan_workflow(db, now=NOW)
        db.commit()
        assert result["missedUpdates"] == (0 if has_owner_update else 1)
        event = db.scalar(select(TaskEvent).where(TaskEvent.kind == "missed_update"))
        if has_owner_update:
            assert event is None
        else:
            assert event.owner_id == expected_owner
            assert event.occurred_at == datetime(2026, 10, 20, 18, 29, 59, 999999, tzinfo=timezone.utc)
            links = list(db.scalars(select(Notification.link_to).where(Notification.kind == "missed_update")))
            assert links == [f"/tasks?staff={expected_owner}&date=2026-10-20"] * 2
        assert scan_workflow(db, now=NOW)["missedUpdates"] == 0


def test_admin_sla_reset_retains_prior_event_and_changes_deadline(workspace):
    from app.api.routers.workflow import ResetIn, reset_sla
    with workspace() as db:
        add_task(db)
        scan_workflow(db, now=NOW)
        db.commit()
        result = reset_sla("task", ResetIn(reason="Approved extension"), db, db.get(User, "admin"))
        assert not result.escalated
        assert result.due_at - result.sla_started_at == timedelta(hours=48)
        assert set(db.scalars(select(TaskEvent.kind).where(TaskEvent.task_id == "task"))) >= {"escalation", "sla_reset"}


def test_remark_ownership_closed_task_and_nonblank_validation(workspace):
    from app.api.routers.data import RemarkIn, add_remark
    with pytest.raises(ValidationError):
        RemarkIn(body="   ")
    with workspace() as db:
        task = add_task(db)
        with pytest.raises(HTTPException) as denied:
            add_remark("task", RemarkIn(body="Not my candidate"), db, db.get(User, "other"))
        assert denied.value.status_code == 403
        add_remark("task", RemarkIn(body="Called today"), db, db.get(User, "owner"))
        task.completed_at = NOW
        db.commit()
        with pytest.raises(HTTPException) as closed:
            add_remark("task", RemarkIn(body="After closure"), db, db.get(User, "owner"))
        assert closed.value.status_code == 409


def test_call_permissions_called_no_keeps_task_open(workspace):
    from app.api.routers.data import CallIn, record_call
    with workspace() as db:
        task = add_task(db)
        with pytest.raises(HTTPException) as denied:
            record_call("candidate", CallIn(interested="yes"), db, db.get(User, "other"))
        assert denied.value.status_code == 403
        record_call("candidate", CallIn(called=False, interested="unreachable"), db, db.get(User, "owner"))
        assert task.completed_at is None
        record_call("candidate", CallIn(called=True, interested="no"), db, db.get(User, "owner"))
        assert task.completed_at is not None
        assert db.get(Candidate, "candidate").status.value == "not_interested"


def test_notifications_are_recipient_owned(workspace):
    from app.api.routers.workflow import read_all, read_notification
    with workspace() as db:
        db.add_all([Notification(id="mine", user_id="owner", kind="assignment", title="Assigned"),
                    Notification(id="theirs", user_id="other", kind="assignment", title="Assigned")])
        db.commit()
        with pytest.raises(HTTPException) as denied:
            read_notification("theirs", db, db.get(User, "owner"))
        assert denied.value.status_code == 404
        read_all(db, db.get(User, "owner"))
        assert db.get(Notification, "mine").read
        assert not db.get(Notification, "theirs").read
