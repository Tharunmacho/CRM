"""Staff clocks and presence on disposable PostgreSQL, with controlled server time."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from threading import Event

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import StaffAttendanceRequest as AttendanceRequest, StaffPresenceClient as PresenceClient, AuditEntry, Role, Session as LoginSession, StaffAttendanceSession as Attendance, StaffPresenceInterval as Presence, User
from app.services import staff_activity as activity
from test_workflow_scheduler import workspace as base_workspace
from test_database_naming import _migrate, schema_database

START = datetime(2026, 10, 21, 3, 30, tzinfo=timezone.utc)


@pytest.fixture
def workspace(base_workspace):
    with base_workspace() as db:
        db.add_all([LoginSession(id='login-' + key, user_id=key, expires_at=START + timedelta(days=365))
                    for key in ('owner', 'other', 'admin', 'manager')])
        db.commit()
    return base_workspace


def test_checkin_checkout_replay_and_multiple_shifts_are_durable(workspace):
    with workspace() as db:
        user=db.get(User,'owner')
        first=activity.check_in(db,user,'first',auth_session_id='login-owner',at=START);db.commit()
        assert activity.check_in(db,user,'first',auth_session_id='login-owner',at=START+timedelta(seconds=5)).id==first.id
        assert activity.check_in(db,user,'different',auth_session_id='login-owner',at=START+timedelta(seconds=10)).id==first.id
        activity.check_out(db,user,first.id,'out1',auth_session_id='login-owner',at=START+timedelta(hours=2));db.commit()
        activity.check_out(db,user,first.id,'out2',auth_session_id='login-owner',at=START+timedelta(hours=3));db.commit()
        assert first.checked_out_at==START+timedelta(hours=2)
        assert activity.check_in(db,user,'first',auth_session_id='login-owner',at=START+timedelta(hours=3)).id==first.id
        second=activity.check_in(db,user,'second',auth_session_id='login-owner',at=START+timedelta(hours=3));db.commit()
        result=activity.attendance_report(db,user,date(2026,10,21),'Asia/Kolkata',at=START+timedelta(hours=4))['staff'][0]
        assert result['attendance']['totalSeconds']==3*3600
        assert result['attendance']['sessionId']==second.id
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_type=='attendance'))==3


def test_concurrent_checkin_creates_one_open_session(workspace):
    def start(key):
        with workspace() as db:
            row=activity.check_in(db,db.get(User,'owner'),key,auth_session_id='login-owner',at=START);db.commit();return row.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(start,['concurrent-a','concurrent-b']))
    assert len(set(ids))==1
    with workspace() as db:
        assert db.scalar(select(func.count()).select_from(Attendance))==1


def test_presence_multiple_tabs_idle_offline_and_work_time_intersection(workspace):
    with workspace() as db:
        user=db.get(User,'owner')
        activity.check_in(db,user,'presence-clock',auth_session_id='login-owner',at=START)
        # Simultaneous tabs must not double count; activity beats idle overlap.
        activity.heartbeat(db,user,'tab1','login-owner',True,0,sequence=1,at=START)
        activity.heartbeat(db,user,'tab2','login-owner',True,600,sequence=1,at=START)
        db.commit()
        activity.heartbeat(db,user,'tab1','login-owner',True,60,sequence=2,at=START+timedelta(seconds=60))
        activity.heartbeat(db,user,'tab2','login-owner',True,660,sequence=2,at=START+timedelta(seconds=60))
        db.commit()
        activity.heartbeat(db,user,'tab1','login-owner',False,None,sequence=3,at=START+timedelta(seconds=70));db.commit()
        result=activity.attendance_report(db,user,date(2026,10,21),'Asia/Kolkata',at=START+timedelta(seconds=180))['staff'][0]
        assert result['presence']['status']=='offline'
        assert result['presence']['activeSeconds']==70
        assert result['presence']['idleSeconds']==80
        assert result['presence']['offlineSeconds']==30
        assert [row['status'] for row in result['presence']['sessions']]==['online','idle','offline']
        assert sum(int((r['endedAt']-r['startedAt']).total_seconds()) for r in result['presence']['sessions'])==180
        assert activity.close_stale_presence(db,at=START+timedelta(seconds=180))==1
        db.commit()
        assert activity.close_stale_presence(db,at=START+timedelta(seconds=200))==0
        assert db.scalar(select(Attendance)).checked_out_at is None


def test_presence_timeout_creates_new_interval_without_counting_disconnection(workspace):
    with workspace() as db:
        user=db.get(User,'owner')
        activity.check_in(db,user,'longgap',auth_session_id='login-owner',at=START)
        activity.heartbeat(db,user,'tab','login-owner',True,0,sequence=1,at=START);db.commit()
        activity.heartbeat(db,user,'tab','login-owner',True,0,sequence=2,at=START+timedelta(hours=1));db.commit()
        result=activity.attendance_report(db,user,date(2026,10,21),'Asia/Kolkata',at=START+timedelta(hours=1,seconds=10))['staff'][0]
        assert result['presence']['activeSeconds']==100
        assert result['presence']['offlineSeconds']==3510
        assert db.scalar(select(func.count()).select_from(Presence))==2


def test_untracked_manager_attendance_is_not_reported_as_offline(workspace):
    with workspace() as db:
        manager = db.get(User, 'manager')
        activity.check_in(db, manager, 'manager-clock', auth_session_id='login-manager', at=START)
        db.commit()
        report = activity.attendance_report(db, manager, START.date(), 'UTC', 'manager', at=START + timedelta(hours=2))
        row = report['staff'][0]
        assert row['role'] == 'manager'
        assert report['generatedAt'] == START + timedelta(hours=2)
        assert row['attendance']['totalSeconds'] == 7200
        assert row['presence']['tracked'] is False
        assert row['presence']['activeSeconds'] == row['presence']['idleSeconds'] == row['presence']['offlineSeconds'] == 0
        assert row['presence']['sessions'] == []
        with pytest.raises(HTTPException) as forbidden:
            activity.heartbeat(db, manager, 'manager-tab', 'login-manager', True, 0, sequence=1, at=START)
        assert forbidden.value.status_code == 403


def test_activity_timeline_durations_exclude_gaps_between_attendance_sessions(workspace):
    with workspace() as db:
        user = db.get(User, 'owner')
        first = activity.check_in(db, user, 'morning', auth_session_id='login-owner', at=START)
        activity.heartbeat(db, user, 'tab', 'login-owner', True, 0, sequence=1, at=START)
        activity.heartbeat(db, user, 'tab', 'login-owner', True, 600, sequence=2, at=START + timedelta(seconds=30))
        activity.check_out(db, user, first.id, 'break', auth_session_id='login-owner', at=START + timedelta(seconds=60))
        db.commit()  # Each clock API action commits before another request begins.
        activity.check_in(db, user, 'afternoon', auth_session_id='login-owner', at=START + timedelta(seconds=600))
        db.commit()
        row = activity.attendance_report(db, user, START.date(), 'UTC', at=START + timedelta(seconds=660))['staff'][0]
        assert row['presence']['tracked'] is True
        assert row['attendance']['totalSeconds'] == 120
        assert [(item['status'], item['durationSeconds']) for item in row['presence']['sessions']] == [('online', 30), ('idle', 30), ('offline', 60)]
        assert sum(item['durationSeconds'] for item in row['presence']['sessions']) == row['attendance']['totalSeconds']
        assert row['presence']['offlineSeconds'] == 60


def test_agency_midnight_clips_attendance_without_losing_overnight_shift(workspace):
    with workspace() as db:
        user=db.get(User,'owner')
        begin=datetime(2026,10,20,18,tzinfo=timezone.utc)
        row=activity.check_in(db,user,'overnight',auth_session_id='login-owner',at=begin)
        activity.check_out(db,user,row.id,'overnight-out',auth_session_id='login-owner',at=begin+timedelta(hours=2));db.commit()
        for day, expected in [(date(2026,10,20),1800),(date(2026,10,21),5400)]:
            report=activity.attendance_report(db,user,day,'Asia/Kolkata',at=START)['staff'][0]
            assert report['attendance']['totalSeconds']==expected


def test_staff_scope_and_admin_reasoned_correction(workspace):
    with workspace() as db:
        user=db.get(User,'owner');admin=db.get(User,'admin');manager=db.get(User,'manager')
        first=activity.check_in(db,user,'correct1',auth_session_id='login-owner',at=START)
        activity.check_out(db,user,first.id,'correct-out',auth_session_id='login-owner',at=START+timedelta(hours=1));db.commit()
        second=activity.check_in(db,user,'correct2',auth_session_id='login-owner',at=START+timedelta(hours=2));db.commit()
        assert len(activity.attendance_report(db,user,START.date(),'UTC',at=START+timedelta(hours=3))['staff'])==1
        assert {row['userId'] for row in activity.attendance_report(db,manager,START.date(),'UTC',at=START+timedelta(hours=3))['staff']} == {'owner', 'other', 'manager'}
        with pytest.raises(HTTPException) as forbidden:
            activity.attendance_report(db,user,START.date(),'UTC','other',at=START)
        assert forbidden.value.status_code==403
        with pytest.raises(HTTPException):
            activity.correct_session(db,manager,first.id,START,START+timedelta(minutes=50),'Wrong approver',at=START+timedelta(hours=3))
        with pytest.raises(HTTPException) as overlap:
            activity.correct_session(db,admin,first.id,START,START+timedelta(hours=2,minutes=1),'Overlap',at=START+timedelta(hours=3))
        assert overlap.value.status_code==409
        activity.correct_session(db,admin,first.id,START,START+timedelta(minutes=50),'Corrected missed checkout',at=START+timedelta(hours=3));db.commit()
        audit=db.scalar(select(AuditEntry).where(AuditEntry.action=='Corrected staff attendance'))
        assert audit.meta['before']['checkedOutAt']!=audit.meta['after']['checkedOutAt']
        assert audit.meta['reason']=='Corrected missed checkout'
        assert second.checked_out_at is None


def test_logout_presence_closes_only_current_login_and_keeps_attendance(workspace):
    with workspace() as db:
        user=db.get(User,'owner')
        db.add_all([LoginSession(id='login-a',user_id=user.id,expires_at=START+timedelta(days=1)),LoginSession(id='login-b',user_id=user.id,expires_at=START+timedelta(days=1))]);db.commit()
        activity.check_in(db,user,'logout-clock',auth_session_id='login-owner',at=START)
        activity.heartbeat(db,user,'tab-a','login-a',True,0,sequence=1,at=START)
        activity.heartbeat(db,user,'tab-b','login-b',True,0,sequence=1,at=START);db.commit()
        activity.close_login_presence(db,'login-a',at=START+timedelta(seconds=20));db.commit()
        assert db.scalar(select(func.count()).select_from(Presence).where(Presence.ended_at.is_(None)))==1
        activity.close_user_presence(db,user.id,at=START+timedelta(seconds=30));db.commit()
        assert db.scalar(select(func.count()).select_from(Presence).where(Presence.ended_at.is_(None)))==0
        assert db.scalar(select(Attendance)).checked_out_at is None


def test_api_clock_validation_permissions_and_database_roundtrip(workspace,monkeypatch):
    from app.api.routers import attendance
    from app.core.deps import current_user
    from app.db.session import get_db
    monkeypatch.setattr(attendance,'get_settings',lambda:SimpleNamespace(agency_timezone='Asia/Kolkata'))
    app=FastAPI();app.include_router(attendance.router)
    role={'id':'owner'}
    def database():
        with workspace() as db:yield db
    def current():
        with workspace() as db:return db.get(User,role['id'])
    app.dependency_overrides[get_db]=database;app.dependency_overrides[current_user]=current
    with TestClient(app) as client:
        from app.core.security import SESSION_COOKIE
        client.cookies.set(SESSION_COOKIE, 'login-owner')
        initial = client.get('/api/attendance?staffId=owner')
        assert initial.headers['cache-control'] == 'no-store'
        owner_context = initial.json()['clockContext']
        assert len(owner_context) == 64 and 'login-owner' not in owner_context
        assert client.get('/api/attendance?staffId=owner').json()['clockContext'] == owner_context
        first=client.post('/api/attendance/check-in',json={'requestKey':str(uuid4())})
        assert first.status_code==200,first.text
        identifier=first.json()['attendance']['sessionId']
        assert identifier
        assert client.get('/api/attendance?staffId=other').status_code==403
        assert client.post('/api/presence/heartbeat',json={'clientId':str(uuid4()),'visible':True,'lastInteractionAt':'2026-10-21T10:00:00'}).status_code==422
        pulse = {'clientId': str(uuid4()), 'sequence': 1, 'visible': True, 'interactionAgeSeconds': 0}
        assert client.post('/api/presence/heartbeat', json=pulse).json()['status'] == 'online'
        assert client.post('/api/presence/heartbeat', json={**pulse, 'sequence': 0}).status_code == 422
        assert client.post('/api/presence/heartbeat', json={**pulse, 'interactionAgeSeconds': -1}).status_code == 422
        assert client.post('/api/presence/heartbeat', json={**pulse, 'interactionAgeSeconds': 86401}).status_code == 422
        assert client.post('/api/attendance/check-out',json={'sessionId':identifier,'requestKey':str(uuid4())}).json()['attendance']['status']=='checked_out'
        role['id']='manager'
        # The stale Staff login must not authorize the newly selected Manager.
        assert client.post('/api/attendance/check-in',json={'requestKey':str(uuid4())}).status_code==401
        client.cookies.set(SESSION_COOKIE, 'login-manager')
        assert client.get('/api/attendance').json()['clockContext'] != owner_context
        assert client.post('/api/attendance/check-in', json={'requestKey': str(uuid4()), 'clockContext': owner_context}).status_code == 409
        assert client.post('/api/attendance/check-in',json={'requestKey':str(uuid4())}).status_code==200
        assert {row['userId'] for row in client.get('/api/attendance').json()['staff']} == {'owner', 'other', 'manager'}


def test_every_accepted_checkin_key_replays_original_shift_after_checkout(workspace):
    with workspace() as db:
        user = db.get(User, 'owner')
        first = activity.check_in(db, user, 'tab-a', auth_session_id='login-owner', at=START)
        activity.check_in(db, user, 'tab-b-lost-response', auth_session_id='login-owner', at=START)
        db.commit()
        activity.check_out(db, user, first.id, 'out', auth_session_id='login-owner', at=START + timedelta(hours=1))
        db.commit()
        replay = activity.check_in(db, user, 'tab-b-lost-response', auth_session_id='login-owner', at=START + timedelta(hours=2))
        db.commit()
        assert replay.id == first.id and replay.checked_out_at is not None
        assert db.scalar(select(func.count()).select_from(Attendance)) == 1
        assert db.scalar(select(func.count()).select_from(AttendanceRequest)) == 3
        next_shift = activity.check_in(db, user, 'intentional-new-shift', auth_session_id='login-owner', at=START + timedelta(hours=3))
        assert next_shift.id != first.id


def test_checkout_retry_cannot_reverse_a_later_admin_correction(workspace):
    with workspace() as db:
        user = db.get(User, 'owner')
        row = activity.check_in(db, user, 'in', auth_session_id='login-owner', at=START)
        activity.check_out(db, user, row.id, 'out-a', auth_session_id='login-owner', at=START + timedelta(hours=1))
        activity.check_out(db, user, row.id, 'out-b', auth_session_id='login-owner', at=START + timedelta(hours=1))
        db.commit()
        activity.correct_session(db, db.get(User, 'admin'), row.id, START, None, 'Checkout was accidental', at=START + timedelta(hours=2))
        db.commit()
        activity.check_out(db, user, row.id, 'out-b', auth_session_id='login-owner', at=START + timedelta(hours=3))
        db.commit()
        assert db.get(Attendance, row.id).checked_out_at is None


def test_presence_sequence_rejects_leave_before_first_heartbeat_and_late_leave(workspace):
    with workspace() as db:
        user = db.get(User, 'owner')
        assert activity.heartbeat(db, user, 'tab', 'login-owner', False, None, sequence=2, at=START)
        db.commit()
        assert not activity.heartbeat(db, user, 'tab', 'login-owner', True, 0, sequence=1, at=START + timedelta(seconds=1))
        db.commit()
        assert db.scalar(select(func.count()).select_from(Presence)) == 0
        assert db.get(PresenceClient, ('owner', 'tab')).last_sequence == 2
        activity.heartbeat(db, user, 'tab', 'login-owner', True, 0, sequence=4, at=START + timedelta(seconds=2))
        db.commit()
        assert not activity.heartbeat(db, user, 'tab', 'login-owner', False, None, sequence=3, at=START + timedelta(seconds=3))
        assert not activity.heartbeat(db, user, 'tab', 'login-owner', True, 0, sequence=4, at=START + timedelta(seconds=80))
        db.commit()
        row = db.scalar(select(Presence))
        assert row.ended_at is None and row.last_seen_at == START + timedelta(seconds=2)
        result = activity.attendance_report(db, user, START.date(), 'UTC', at=START + timedelta(seconds=93))['staff'][0]
        assert result['presence']['status'] == 'offline'


def test_presence_state_uses_age_not_device_timestamp(workspace):
    with workspace() as db:
        user = db.get(User, 'owner')
        for sequence, age, expected in [(1, None, 'idle'), (2, 0, 'online'), (3, 299.99, 'online'), (4, 300, 'idle')]:
            moment = START + timedelta(seconds=sequence)
            activity.heartbeat(db, user, 'tab', 'login-owner', True, age, sequence=sequence, at=moment)
            db.commit()
            live = db.scalar(select(Presence).where(Presence.ended_at.is_(None)))
            assert live.state == expected and live.last_seen_at == moment


@pytest.mark.parametrize('state', ['revoked', 'expired', 'other-user'])
def test_heartbeat_revalidates_login_after_user_was_previously_authenticated(workspace, state):
    with workspace() as db:
        user = db.get(User, 'owner')
        if state == 'revoked': db.delete(db.get(LoginSession, 'login-owner'))
        elif state == 'expired': db.get(LoginSession, 'login-owner').expires_at = START
        db.commit()
        with pytest.raises(HTTPException) as failure:
            activity.heartbeat(db, user, 'tab', 'login-other' if state == 'other-user' else 'login-owner', True, 0, sequence=1, at=START)
        assert failure.value.status_code == 401
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Presence)) == 0
        assert db.scalar(select(func.count()).select_from(PresenceClient)) == 0


def test_waiting_heartbeat_after_logout_is_rejected_without_orphan_interval(workspace):
    ready = Event()
    def waiting_heartbeat():
        with workspace() as db:
            user = db.get(User, 'owner')  # Simulates current_user before the lock.
            ready.set()
            with pytest.raises(HTTPException) as failure:
                activity.heartbeat(db, user, 'late-tab', 'login-owner', True, 0, sequence=1, at=START)
            db.rollback()
            return failure.value.status_code
    with workspace() as db, ThreadPoolExecutor(max_workers=1) as pool:
        db.scalar(select(User).where(User.id == 'owner').with_for_update())
        future = pool.submit(waiting_heartbeat)
        assert ready.wait(5)
        activity.logout_session(db, 'login-owner', at=START)
        db.commit()
        assert future.result(timeout=5) == 401
        assert db.scalar(select(func.count()).select_from(Presence)) == 0


def test_logout_waits_for_inflight_heartbeat_then_closes_it(workspace):
    ready = Event()
    def logout():
        with workspace() as db:
            ready.set()
            activity.logout_session(db, 'login-owner', at=START + timedelta(seconds=1))
            db.commit()
    with workspace() as db, ThreadPoolExecutor(max_workers=1) as pool:
        user = db.get(User, 'owner')
        activity.check_in(db, user, 'clock', auth_session_id='login-owner', at=START)
        activity.heartbeat(db, user, 'tab', 'login-owner', True, 0, sequence=1, at=START)
        future = pool.submit(logout)
        assert ready.wait(5)
        db.commit()
        future.result(timeout=5)
        db.expire_all()
        assert db.get(LoginSession, 'login-owner') is None
        assert db.scalar(select(Presence)).ended_at == START + timedelta(seconds=1)
        assert db.scalar(select(Attendance)).checked_out_at is None
        assert db.scalar(select(PresenceClient)).auth_session_id is None


def test_cli_password_reset_closes_presence_without_checking_out(workspace, monkeypatch):
    from app import cli
    with workspace() as db:
        user = db.get(User, 'owner')
        activity.check_in(db, user, 'cli-clock', auth_session_id='login-owner', at=START)
        activity.heartbeat(db, user, 'cli-tab', 'login-owner', True, 0, sequence=1, at=START)
        db.commit()
    monkeypatch.setattr(cli, 'SessionLocal', workspace)
    assert cli.set_password('owner@example.test', 'replacement-test-password') == 0
    with workspace() as db:
        assert db.scalar(select(Presence)).ended_at is not None
        assert db.get(LoginSession, 'login-owner') is None
        assert db.scalar(select(Attendance)).checked_out_at is None


def test_migration_backfills_original_request_keys_and_preserves_history(schema_database):
    _migrate(schema_database, '0013_staff_attendance')
    with Session(schema_database) as db:
        user = User(id='legacy', name='Legacy', email='legacy@example.test', role=Role.staff)
        db.add(user); db.flush()
        db.add(Attendance(id='legacy-shift', user_id=user.id, checked_in_at=START, checked_out_at=START + timedelta(hours=1), checkin_request_key='legacy-in', checkout_request_key='legacy-out'))
        db.add(Presence(id='legacy-presence', user_id=user.id, client_id='legacy-tab', state='online', started_at=START, last_seen_at=START))
        db.commit()
    _migrate(schema_database, 'head')
    with Session(schema_database) as db:
        assert db.get(AttendanceRequest, ('check_in', 'legacy-in')).attendance_id == 'legacy-shift'
        assert db.get(AttendanceRequest, ('check_out', 'legacy-out')).attendance_id == 'legacy-shift'
        assert db.get(Attendance, 'legacy-shift').checked_out_at == START + timedelta(hours=1)
        assert db.get(Presence, 'legacy-presence').ended_at == START + timedelta(seconds=90)
