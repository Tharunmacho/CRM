"""User administration with disposable SQLite; never connects to configured DB."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import DefaultClause, JSON, MetaData, Text, create_engine, event, select, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_database_naming import schema_database

from app.core.security import SESSION_COOKIE, hash_password, verify_password
from app.db.models import AssignmentPointer, AttendanceHRRequest, AttendanceCalendarDay, AuditEntry, Base, Candidate, Role, Session as LoginSession, Source, Task, TaskEvent, TaskKind, User, StaffPresenceInterval

with patch('app.core.config.get_settings', return_value=SimpleNamespace(database_url='postgresql+psycopg://unused@127.0.0.1:1/user_admin_tests')):
    from app.api.routers import users
    from app.db.session import get_db


@pytest.fixture(params=['sqlite', 'postgres'])
def env(request):
    engine = request.getfixturevalue('schema_database') if request.param == 'postgres' else create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    models = [User, LoginSession, AssignmentPointer, Candidate, Task, TaskEvent, AuditEntry, StaffPresenceInterval, AttendanceHRRequest, AttendanceCalendarDay]
    for model in models:
        for column in metadata.tables[model.__tablename__].columns:
            if isinstance(column.type, JSONB): column.type = JSON()
            if isinstance(column.type, TSVECTOR): column.type = Text()
            if column.server_default is not None and '::jsonb' in str(column.server_default.arg):
                column.server_default = DefaultClause(text(str(column.server_default.arg).replace('::jsonb', '')))
    if request.param == 'postgres':
        Base.metadata.create_all(engine)
    else:
        metadata.create_all(engine, tables=[metadata.tables[model.__tablename__] for model in models])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    def restore_timezone(row, context, *args):
        for field in ('created_at', 'expires_at'):
            value = getattr(row, field)
            if value is not None and value.tzinfo is None:
                setattr(row, field, value.replace(tzinfo=timezone.utc))
    event.listen(LoginSession, 'load', restore_timezone)
    event.listen(LoginSession, 'refresh', restore_timezone)
    application = FastAPI()
    application.include_router(users.router)
    def database():
        with factory() as db: yield db
    application.dependency_overrides[get_db] = database
    with factory() as db:
        for identifier, role in [('admin', Role.admin), ('staff', Role.staff), ('receiver', Role.staff), ('manager', Role.manager)]:
            db.add(User(id=identifier, name=identifier.title(), email=identifier + '@example.test', role=role,
                        active=True, receives_auto_assign=role == Role.staff, on_leave=False, password_hash=hash_password('initial-test-password')))
        db.flush()
        for identifier in ['admin', 'staff', 'receiver', 'manager']:
            db.add(LoginSession(id='session-' + identifier, user_id=identifier, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()
    try:
        with TestClient(application, base_url='https://testserver') as client:
            client.cookies.set(SESSION_COOKIE, 'session-admin')
            yield SimpleNamespace(client=client, factory=factory)
    finally:
        event.remove(LoginSession, 'load', restore_timezone)
        event.remove(LoginSession, 'refresh', restore_timezone)
        engine.dispose()


def assigned_work(env):
    with env.factory() as db:
        db.add(Candidate(id='candidate', ref='C-TEST', name='Test candidate', phone='12345', source=Source.walk_in,
                         owner_id='staff', languages=[], skills=[], designations=[]))
        db.flush()
        for identifier, completed in [('open', None), ('completed', datetime(2026, 1, 5, tzinfo=timezone.utc))]:
            db.add(Task(id=identifier, title='Initial call', kind=TaskKind.initial_call, candidate_id='candidate',
                        owner_id='staff', created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), completed_at=completed))
        db.commit()


def test_all_account_routes_require_admin(env):
    for role in ['staff', 'manager']:
        env.client.cookies.set(SESSION_COOKIE, 'session-' + role)
        for method, path, payload in [('get', '/api/users', None), ('post', '/api/users', {}),
                ('patch', '/api/users/staff', {}), ('post', '/api/users/staff/password', {'password': 'new-test-password'}),
                ('post', '/api/users/staff/force-logout', None), ('get', '/api/users/staff/audit', None)]:
            response = getattr(env.client, method)(path, **({'json': payload} if payload is not None else {}))
            assert response.status_code == 403


def test_create_is_persistent_normalized_and_never_returns_or_audits_password(env):
    response = env.client.post('/api/users', json={'name': ' New Recruiter ', 'email': 'NEW@EXAMPLE.TEST',
        'role': 'staff', 'password': 'new-test-password', 'receivesAutoAssign': True})
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['name'] == 'New Recruiter' and record['email'] == 'new@example.test'
    assert record['receivesAutoAssign'] is True and 'password' not in response.text.lower()
    with env.factory() as db:
        saved = db.get(User, record['id'])
        assert verify_password('new-test-password', saved.password_hash)
        audit = db.scalar(select(AuditEntry).where(AuditEntry.entity_id == saved.id))
        assert audit.actor_id == 'admin' and 'password' not in str(audit.meta).lower()
    assert env.client.post('/api/users', json={'name': 'Duplicate', 'email': 'NEW@example.test', 'password': 'new-test-password'}).status_code == 409


@pytest.mark.parametrize('payload', [{'name': '  ', 'email': 'x@example.test', 'password': 'valid-test-password'},
    {'name': 'Name', 'email': 'invalid', 'password': 'valid-test-password'},
    {'name': 'Name', 'email': 'x@example.test', 'password': 'short'}])
def test_invalid_create_does_not_add_account(env, payload):
    assert env.client.post('/api/users', json=payload).status_code == 422
    assert len(env.client.get('/api/users').json()) == 4


def test_availability_and_activation_persist_and_nonstaff_cannot_join_rotation(env):
    assert env.client.patch('/api/users/staff', json={'onLeave': True, 'receivesAutoAssign': False}).status_code == 200
    assert env.client.patch('/api/users/manager', json={'receivesAutoAssign': True, 'onLeave': True}).json()['receivesAutoAssign'] is False
    with env.factory() as db:
        user = db.get(User, 'staff')
        assert user.on_leave and not user.receives_auto_assign
        assert db.get(User, 'manager').on_leave is False


def test_deactivation_requires_valid_target_and_is_atomic(env):
    assigned_work(env)
    for payload in [{'active': False}, {'active': False, 'reassignToUserId': 'manager'}, {'active': False, 'reassignToUserId': 'staff'}]:
        assert env.client.patch('/api/users/staff', json=payload).status_code == 422
    with env.factory() as db:
        assert db.get(User, 'staff').active
        assert db.get(Candidate, 'candidate').owner_id == 'staff'
        assert db.get(LoginSession, 'session-staff') is not None
    response = env.client.patch('/api/users/staff', json={'active': False, 'reassignToUserId': 'receiver'})
    assert response.status_code == 200, response.text
    with env.factory() as db:
        assert not db.get(User, 'staff').active
        assert not db.get(User, 'staff').receives_auto_assign
        assert db.get(Candidate, 'candidate').owner_id == 'receiver'
        assert db.get(Task, 'open').owner_id == 'receiver'
        assert db.get(Task, 'open').created_at.replace(tzinfo=None) == datetime(2026, 1, 1)
        assert db.get(Task, 'completed').owner_id == 'staff'
        event = db.scalar(select(TaskEvent).where(TaskEvent.task_id == 'open', TaskEvent.kind == 'assignment'))
        assert event.owner_id == 'receiver' and event.meta == {'fromOwnerId': 'staff', 'toOwnerId': 'receiver'}
        assert db.get(LoginSession, 'session-staff') is None
        assert db.scalar(select(AuditEntry).where(AuditEntry.entity_type == 'candidate')).meta['toUserId'] == 'receiver'


def test_failed_commit_rolls_back_reassignment_and_revocation(env, monkeypatch):
    assigned_work(env)
    def fail(db):
        db.flush()
        raise HTTPException(503, 'Synthetic storage failure')
    monkeypatch.setattr(users.user_admin, '_commit', fail)
    assert env.client.patch('/api/users/staff', json={'active': False, 'reassignToUserId': 'receiver'}).status_code == 503
    with env.factory() as db:
        assert db.get(User, 'staff').active
        assert db.get(Candidate, 'candidate').owner_id == 'staff'
        assert db.get(Task, 'open').owner_id == 'staff'
        assert db.get(LoginSession, 'session-staff') is not None


def test_role_change_transfers_work_and_revokes_sessions(env):
    assigned_work(env)
    assert env.client.patch('/api/users/staff', json={'role': 'manager'}).status_code == 422
    assert env.client.patch('/api/users/staff', json={'role': 'manager', 'reassignToUserId': 'receiver'}).status_code == 200
    with env.factory() as db:
        assert db.get(User, 'staff').role == Role.manager
        assert not db.get(User, 'staff').receives_auto_assign
        assert db.get(Task, 'open').owner_id == 'receiver'
        assert db.get(LoginSession, 'session-staff') is None


def test_admin_cannot_remove_last_admin_or_demote_self(env):
    assert env.client.patch('/api/users/admin', json={'active': False}).status_code == 409
    assert env.client.patch('/api/users/admin', json={'role': 'staff'}).status_code == 409
    assert env.client.patch('/api/users/manager', json={'role': 'admin'}).status_code == 200
    assert env.client.patch('/api/users/admin', json={'role': 'manager'}).status_code == 409


def test_password_reset_revokes_sessions_without_reactivation_and_force_logout_works(env):
    assert env.client.patch('/api/users/staff', json={'active': False}).status_code == 200
    response = env.client.post('/api/users/staff/password', json={'password': 'replacement-test-password'})
    assert response.status_code == 200
    with env.factory() as db:
        user = db.get(User, 'staff')
        assert not user.active and verify_password('replacement-test-password', user.password_hash)
        assert db.get(LoginSession, 'session-staff') is None
        assert 'replacement-test-password' not in str(list(db.scalars(select(AuditEntry.meta))))
    assert env.client.post('/api/users/receiver/force-logout').json()['revokedSessions'] == 1
    with env.factory() as db:
        assert db.get(User, 'receiver').active
        assert db.get(LoginSession, 'session-receiver') is None


def test_audit_paginates_equal_timestamps_and_excludes_unrelated_events(env):
    with env.factory() as db:
        for identifier in ['a', 'b', 'c']:
            db.add(AuditEntry(id=identifier, actor_id='admin', action='User updated', entity_type='user', entity_id='staff', at=datetime(2026, 1, 1)))
        db.add(AuditEntry(id='unrelated', actor_id='manager', action='Other activity', entity_type='user', entity_id='receiver'))
        db.commit()
    page = env.client.get('/api/users/staff/audit?limit=2').json()
    assert [row['id'] for row in page['items']] == ['c', 'b']
    next_page = env.client.get('/api/users/staff/audit', params={'before': page['nextBefore'], 'limit': 2}).json()
    assert [row['id'] for row in next_page['items']] == ['a']
    assert next_page['nextBefore'] is None
