"""Private reminder mutations and safe completion retries through the real API."""
from datetime import timedelta

from sqlalchemy import select

from app.core.security import SESSION_COOKIE
from app.db.models import AuditEntry, Notification, TodoItem
from app.services.workflow_scheduler import deliver_due_todos
from test_database_naming import schema_database
from test_workflow_drilldown import history_client
from test_workflow_scheduler import NOW, workspace


def test_completion_replay_preserves_done_state_and_private_ownership(workspace, history_client):
    response = history_client.post('/api/todos', json={
        'title': 'Follow up on interview', 'dueAt': (NOW - timedelta(minutes=1)).isoformat(),
        'priority': 'high', 'linkTo': '/candidates/candidate', 'linkLabel': 'Synthetic candidate',
    })
    assert response.status_code == 201, response.text
    todo_id = response.json()['id']
    history_client.cookies.set(SESSION_COOKIE, 'history-manager')
    assert history_client.patch(f'/api/todos/{todo_id}', json={'done': True}).status_code == 404
    history_client.cookies.set(SESSION_COOKIE, 'history-owner')
    assert history_client.patch(f'/api/todos/{todo_id}', json={'done': True}).status_code == 403
    history_client.cookies.set(SESSION_COOKIE, 'history-admin')
    for _ in range(2):
        result = history_client.patch(f'/api/todos/{todo_id}', json={'done': True})
        assert result.status_code == 200 and result.json()['done'] is True
    with workspace() as db:
        assert deliver_due_todos(db, now=NOW) == 0
        events = list(db.scalars(select(AuditEntry).where(AuditEntry.entity_id == todo_id,
            AuditEntry.action == 'Completed personal to-do')))
        assert len(events) == 1
    for _ in range(2):
        assert history_client.patch(f'/api/todos/{todo_id}', json={'done': False}).json()['done'] is False
    with workspace() as db:
        assert deliver_due_todos(db, now=NOW) == 1
        db.commit()
        assert deliver_due_todos(db, now=NOW + timedelta(minutes=1)) == 0
        assert list(db.scalars(select(Notification.user_id).where(Notification.kind == 'todo'))) == ['admin']
        assert db.get(TodoItem, todo_id).done is False
    assert history_client.delete(f'/api/todos/{todo_id}').status_code == 204
    with workspace() as db:
        assert db.get(TodoItem, todo_id) is None


def test_edit_keeps_completion_and_reschedules_an_already_delivered_reminder(workspace, history_client):
    with workspace() as db:
        db.add(TodoItem(id='todo-edit', owner_id='admin', title='Before edit', due_at=NOW,
            done=True, reminded_at=NOW))
        db.commit()
    payload = {'title': 'After edit', 'dueAt': (NOW + timedelta(hours=1)).isoformat(), 'priority': 'normal'}
    result = history_client.patch('/api/todos/todo-edit', json=payload)
    assert result.status_code == 200 and result.json()['done'] is True
    with workspace() as db:
        row = db.get(TodoItem, 'todo-edit')
        assert row.title == 'After edit' and row.reminded_at is None
    assert history_client.patch('/api/todos/todo-edit', json={'done': False, 'ownerId': 'manager'}).status_code == 422
