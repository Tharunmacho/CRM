from datetime import date
from datetime import timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from app.db.models import AuditEntry, Candidate, Session as LoginSession, Task, TaskEvent, User
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
        reassign(ReassignIn(candidateIds=["candidate"], toUserId="other", reason="Cover the recruiter workload"), db, db.get(User, "admin"))
        assert task.owner_id == "other" and task.due_at == deadline
        event = db.scalar(select(TaskEvent).where(TaskEvent.kind == "assignment"))
        assert event.owner_id == "owner" and event.meta["toOwnerId"] == "other"
        assert event.meta["reason"] == "Cover the recruiter workload"
        audit = db.scalar(select(AuditEntry).where(AuditEntry.entity_id == "candidate", AuditEntry.action == "Candidate reassigned"))
        assert audit.meta == {"fromUserId": "owner", "toUserId": "other", "reason": "Cover the recruiter workload"}
        assert missed_updates(date(2026, 10, 20), "owner", db, db.get(User, "manager"))["taskIds"] == ["task"]
        assert missed_updates(date(2026, 10, 20), "other", db, db.get(User, "manager"))["taskIds"] == []
        assert missed_updates(date(2026, 10, 19), "owner", db, db.get(User, "manager"))["taskIds"] == []


@pytest.fixture
def history_client(workspace):
    from app.api.routers import candidates, data
    from app.db.session import get_db
    from app.core.security import SESSION_COOKIE
    from datetime import datetime, timezone
    with workspace() as db:
        db.add_all([LoginSession(id='history-' + uid, user_id=uid,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1)) for uid in ('admin', 'manager', 'owner', 'other')])
        db.commit()
    app = FastAPI()
    app.include_router(candidates.router)
    app.include_router(data.router)
    def database():
        with workspace() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, 'history-admin')
        yield client


def test_reassignment_requires_reason_and_preserves_closed_task_owner(workspace, history_client):
    from app.core.security import SESSION_COOKIE
    with workspace() as db:
        add_task(db, completed_at=NOW)
    payload = {'candidateIds': ['candidate'], 'toUserId': 'other'}
    assert history_client.post('/api/candidates/reassign', json=payload).status_code == 422
    assert history_client.post('/api/candidates/reassign', json={**payload, 'reason': '  '}).status_code == 422
    history_client.cookies.set(SESSION_COOKIE, 'history-owner')
    assert history_client.post('/api/candidates/reassign', json={**payload, 'reason': 'Staff cannot reassign'}).status_code == 403
    history_client.cookies.set(SESSION_COOKIE, 'history-manager')
    response = history_client.post('/api/candidates/reassign', json={**payload, 'reason': '  Rebalance recruitment work  '})
    assert response.status_code == 200, response.text
    with workspace() as db:
        assert db.get(Candidate, 'candidate').owner_id == 'other'
        assert db.get(Task, 'task').owner_id == 'owner'
        assert db.scalar(select(TaskEvent).where(TaskEvent.kind == 'assignment')) is None
    history_client.cookies.set(SESSION_COOKIE, 'history-owner')
    history = history_client.get('/api/candidates/candidate/assignment-history')
    assert history.status_code == 200
    assert history.headers['Cache-Control'] == 'private, no-store'
    row, = history.json()['items']
    assert (row['fromName'], row['toName'], row['actorName'], row['reason']) == ('Owner', 'Other', 'Manager', 'Rebalance recruitment work')
    assert 'meta' not in row
    history_client.cookies.clear()
    assert history_client.get('/api/candidates/candidate/assignment-history').status_code == 401


def test_history_cursor_keeps_tied_timestamps_and_excludes_other_evidence(workspace, history_client):
    with workspace() as db:
        for key in ('a', 'b', 'c'):
            db.add(AuditEntry(id=key, actor_id='manager', entity_type='candidate', entity_id='candidate',
                action='Candidate reassigned', target='Synthetic', at=NOW,
                meta={'fromUserId': 'owner', 'toUserId': 'other', 'reason': key, 'unrelatedSecret': 'never expose'}))
        db.add(AuditEntry(id='not-history', actor_id='admin', entity_type='candidate', entity_id='candidate', action='Edited profile', target='Synthetic', at=NOW, meta={'reason': 'not assignment'}))
        db.add(AuditEntry(id='other-history', actor_id='admin', entity_type='candidate', entity_id='different-candidate', action='Candidate reassigned', target='Other', at=NOW, meta={'fromUserId': 'owner', 'toUserId': 'other'}))
        db.commit()
    first = history_client.get('/api/candidates/candidate/assignment-history', params={'limit': 2}).json()
    assert [row['id'] for row in first['items']] == ['c', 'b']
    last = history_client.get('/api/candidates/candidate/assignment-history', params={'limit': 2, 'before': first['nextBefore']}).json()
    assert [row['id'] for row in last['items']] == ['a']
    assert last['nextBefore'] is None
    assert 'unrelatedSecret' not in str(first)
    for cursor in ('garbage', '2026-10-21T12:00:00|a', '2026-10-21T12:00:00+00:00|'):
        assert history_client.get('/api/candidates/candidate/assignment-history', params={'before': cursor}).status_code == 422
    assert history_client.get('/api/candidates/candidate/assignment-history', params={'limit': 0}).status_code == 422
    assert history_client.get('/api/candidates/missing/assignment-history').status_code == 404


def test_approved_leave_blocks_manual_and_account_change_transfers(workspace, history_client):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from fastapi import HTTPException
    from app.core.config import get_settings
    from app.db.models import AttendanceCalendarDay
    from app.services.user_admin import update_user
    day = datetime.now(timezone.utc).astimezone(ZoneInfo(get_settings().agency_timezone)).date()
    with workspace() as db:
        db.add(AttendanceCalendarDay(user_id='other', attendance_date=day, status='UL', reason='Approved absence', actor_id='admin'))
        db.commit()
    response = history_client.post('/api/candidates/reassign', json={
        'candidateIds': ['candidate'], 'toUserId': 'other', 'reason': 'This should fail'})
    assert response.status_code == 422
    with workspace() as db:
        with pytest.raises(HTTPException) as failure:
            update_user(db, db.get(User, 'admin'), 'owner', {'active': False, 'reassign_to_user_id': 'other'})
        assert failure.value.status_code == 422
        db.rollback()
        assert db.get(User, 'owner').active is True
        assert db.get(Candidate, 'candidate').owner_id == 'owner'
