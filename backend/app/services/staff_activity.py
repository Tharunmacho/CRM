"""Explicit attendance and bounded, transparent browser-presence estimates."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.services.parallel_reads import parallel_reads
from app.db.models import (AuditEntry, Role, Session as LoginSession,
                           StaffAttendanceRequest as AttendanceRequest,
                           StaffAttendanceSession as Attendance,
                           StaffPresenceClient as PresenceClient,
                           StaffPresenceInterval as Presence, User)

HEARTBEAT_SECONDS = 30
OFFLINE_AFTER_SECONDS = 90
IDLE_AFTER_SECONDS = 300


def utcnow():
    return datetime.now(timezone.utc)


def lock_staff(db, user_id, *, clock=False):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    allowed = (Role.staff, Role.manager) if clock else (Role.staff,)
    if user is None or not user.active or user.role not in allowed:
        raise HTTPException(403, "Only active staff accounts can clock in or out")
    return user


def lock_staff_login(db, user, session_id, *, at=None, clock=False):
    """Mutations, logout and revocation share User → Login → presence locks."""
    lock_staff(db, user.id, clock=clock)
    now = at or utcnow()
    login = db.scalar(select(LoginSession).where(
        LoginSession.id == session_id, LoginSession.user_id == user.id,
    ).with_for_update().execution_options(populate_existing=True)) if session_id else None
    if login is None or login.expires_at <= now:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    return now


def bounds(day, zone):
    return (datetime.combine(day, time.min, ZoneInfo(zone)).astimezone(timezone.utc),
            datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(zone)).astimezone(timezone.utc))


def merged(intervals):
    result = []
    for start, end in sorted((a, b) for a, b in intervals if a < b):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def seconds(intervals):
    return max(0, int(sum((end - start).total_seconds() for start, end in merged(intervals))))


def intersections(left, right):
    return merged((max(a, c), min(b, d)) for a, b in left for c, d in right if max(a, c) < min(b, d))


def activity_timeline(shifts, active, idle):
    edges = sorted({point for ranges in (shifts, active, idle) for pair in ranges for point in pair})
    result = []
    for start, end in zip(edges, edges[1:]):
        if start >= end or not any(a <= start and end <= b for a, b in shifts):
            continue
        state = "online" if any(a <= start and end <= b for a, b in active) else "idle" if any(a <= start and end <= b for a, b in idle) else "offline"
        if result and result[-1]["status"] == state and result[-1]["endedAt"] == start:
            result[-1]["endedAt"] = end
        else:
            result.append({"startedAt": start, "endedAt": end, "status": state})
    return result


def presence_end(row, at):
    return row.ended_at or min(at, row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS))


def staff_summary(user, attendance, presence, start, end, at):
    tracked = user.role == Role.staff
    cutoff = min(end, at)
    shifts = merged((max(start, row.checked_in_at), min(cutoff, row.checked_out_at or at)) for row in attendance)
    active = merged((max(start, row.started_at), min(cutoff, presence_end(row, at))) for row in presence if row.state == "online")
    idle = merged((max(start, row.started_at), min(cutoff, presence_end(row, at))) for row in presence if row.state == "idle")
    active_seconds = seconds(intersections(shifts, active))
    seen_seconds = seconds(intersections(shifts, merged([*active, *idle])))
    total = seconds(shifts)
    current = [row for row in presence if row.ended_at is None and row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS) > at]
    live = next((row for row in attendance if row.checked_out_at is None), None)
    state = "online" if any(row.state == "online" for row in current) else "idle" if current else "offline"
    timeline = activity_timeline(shifts, active, idle) if tracked else []
    for interval in timeline:
        interval["durationSeconds"] = seconds([(interval["startedAt"], interval["endedAt"])])
    return {"userId": user.id, "name": user.name, "role": user.role.value, "active": user.active,
        "attendance": {"status": "checked_in" if live else "checked_out", "sessionId": live.id if live else None,
            "checkedInAt": live.checked_in_at if live else None,
            "lastCheckedOutAt": max((row.checked_out_at for row in attendance if row.checked_out_at), default=None),
            "totalSeconds": total,
            "sessions": [{"id": row.id, "checkedInAt": row.checked_in_at, "checkedOutAt": row.checked_out_at,
                "durationSeconds": seconds([(max(start, row.checked_in_at), min(cutoff, row.checked_out_at or at))]),
                "correctionReason": row.correction_reason, "correctedBy": row.corrected_by}
                for row in sorted(attendance, key=lambda item: item.checked_in_at) if row.checked_in_at < end and (row.checked_out_at or at) >= start]},
        "presence": {"status": state, "tracked": tracked,
            "lastSeenAt": max((row.last_seen_at for row in presence), default=None),
            "activeSeconds": active_seconds if tracked else 0,
            "idleSeconds": max(0, seen_seconds - active_seconds) if tracked else 0,
            # No browser signal is not evidence of absence for an untracked role.
            "offlineSeconds": max(0, total - seen_seconds) if tracked else 0,
            "sessions": timeline}}


def attendance_report(db, user, day, agency_timezone, staff_id=None, *, at=None):
    at = at or utcnow()
    start, end = bounds(day, agency_timezone)
    team = user.role in (Role.admin, Role.manager)
    if not team and staff_id and staff_id != user.id:
        raise HTTPException(403, "You can view only your own attendance")
    people = select(User).where(or_(User.role.in_([Role.staff, Role.manager]), User.id.in_(select(Attendance.user_id))))
    if user.role == Role.manager:
        people = people.where(or_(User.role == Role.staff, User.id == user.id))
        if staff_id and staff_id != user.id:
            target = db.get(User, staff_id)
            if target is None or target.role != Role.staff:
                raise HTTPException(403, "Managers can view Staff and their own attendance")
    if not team: people = people.where(User.id == user.id)
    elif staff_id: people = people.where(User.id == staff_id)
    # The shift and presence reads filter on the same people query, so all three can run at once.
    ids = people.order_by(None).with_only_columns(User.id).scalar_subquery()
    users, attendance, presence = parallel_reads(
        db,
        lambda session: list(session.scalars(people.order_by(User.name, User.id))),
        lambda session: list(session.scalars(select(Attendance).where(Attendance.user_id.in_(ids),
            or_(Attendance.checked_out_at.is_(None), Attendance.checked_out_at >= start),
            or_(Attendance.checked_in_at < end, Attendance.checked_out_at.is_(None))))),
        lambda session: list(session.scalars(select(Presence).where(Presence.user_id.in_(ids),
            or_(Presence.ended_at.is_(None), Presence.ended_at >= start),
            or_(Presence.started_at < end, Presence.ended_at.is_(None))))),
    )
    attendance_by_user, presence_by_user = defaultdict(list), defaultdict(list)
    for row in attendance:
        attendance_by_user[row.user_id].append(row)
    for row in presence:
        presence_by_user[row.user_id].append(row)
    return {"date": day.isoformat(), "timezone": agency_timezone, "canViewTeam": team, "generatedAt": at,
        "policy": {"heartbeatSeconds": HEARTBEAT_SECONDS, "offlineAfterSeconds": OFFLINE_AFTER_SECONDS,
                   "idleAfterSeconds": IDLE_AFTER_SECONDS, "durationScope": "checked_in_time"},
        "staff": [staff_summary(person, attendance_by_user[person.id],
                   presence_by_user[person.id], start, end, at) for person in users]}


def _request_session(db, user, kind, request_key, attendance_id=None):
    request = db.get(AttendanceRequest, (kind, request_key))
    if request is None:
        return None
    if request.user_id != user.id or (attendance_id is not None and request.attendance_id != attendance_id):
        raise HTTPException(409, "Something went wrong. Refresh the page and try again.")
    return db.get(Attendance, request.attendance_id)


def _remember_request(db, user, kind, request_key, row, at):
    # The user lock serializes a staff member's requests. ON CONFLICT also makes
    # collisions from another account return a controlled conflict, not a 500.
    db.execute(insert(AttendanceRequest).values(
        kind=kind, request_key=request_key, user_id=user.id, attendance_id=row.id, created_at=at,
    ).on_conflict_do_nothing(index_elements=["kind", "request_key"]))
    return _request_session(db, user, kind, request_key, row.id)


def check_in(db, user, request_key, *, auth_session_id, at=None):
    at = lock_staff_login(db, user, auth_session_id, at=at, clock=True)
    replay = _request_session(db, user, "check_in", request_key)
    if replay is not None: return replay
    existing = db.scalar(select(Attendance).where(Attendance.user_id == user.id, Attendance.checked_out_at.is_(None)))
    if existing is not None:
        return _remember_request(db, user, "check_in", request_key, existing, at)
    row = Attendance(user_id=user.id, checked_in_at=at, checkin_request_key=request_key)
    db.add(row); db.flush()
    db.add(AuditEntry(actor_id=user.id, action="Staff checked in", entity_type="attendance", entity_id=row.id,
                      meta={"checkedInAt": at.isoformat()}))
    return _remember_request(db, user, "check_in", request_key, row, at)


def check_out(db, user, session_id, request_key, *, auth_session_id, at=None):
    at = lock_staff_login(db, user, session_id=auth_session_id, at=at, clock=True)
    replay = _request_session(db, user, "check_out", request_key, session_id)
    if replay is not None: return replay
    row = db.scalar(select(Attendance).where(Attendance.id == session_id, Attendance.user_id == user.id).with_for_update())
    if row is None: raise HTTPException(404, "Attendance session not found")
    if row.checked_out_at:
        return _remember_request(db, user, "check_out", request_key, row, at)
    row.checked_out_at, row.checkout_request_key = max(at, row.checked_in_at), request_key
    db.add(AuditEntry(actor_id=user.id, action="Staff checked out", entity_type="attendance", entity_id=row.id,
                      meta={"checkedOutAt": row.checked_out_at.isoformat()}))
    return _remember_request(db, user, "check_out", request_key, row, at)


def heartbeat(db, user, client_id, auth_session_id, visible, interaction_age_seconds, *, sequence, at=None):
    at = lock_staff_login(db, user, auth_session_id, at=at)
    client = db.get(PresenceClient, (user.id, client_id))
    if client is not None:
        if client.auth_session_id != auth_session_id:
            raise HTTPException(409, "You signed in again since this page opened. Reload the page.")
        if sequence <= client.last_sequence:
            return False
        client.last_sequence, client.updated_at = sequence, at
    else:
        # Even an initial hidden/leave request creates a durable checkpoint. An
        # older first heartbeat cannot resurrect the page when it arrives later.
        db.add(PresenceClient(user_id=user.id, client_id=client_id, auth_session_id=auth_session_id,
                              last_sequence=sequence, updated_at=at))
    row = db.scalar(select(Presence).where(Presence.user_id == user.id, Presence.client_id == client_id,
                                          Presence.ended_at.is_(None)).with_for_update())
    wanted = "online" if interaction_age_seconds is not None and interaction_age_seconds < IDLE_AFTER_SECONDS else "idle"
    if row and (not visible or row.auth_session_id != auth_session_id or row.state != wanted or
                row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS) <= at):
        row.ended_at = max(row.started_at, min(at, row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS)))
        db.flush(); row = None
    if visible:
        if row is None:
            row = Presence(user_id=user.id, client_id=client_id, auth_session_id=auth_session_id,
                           state=wanted, started_at=at, last_seen_at=at)
            db.add(row)
        else:
            row.last_seen_at = max(at, row.last_seen_at)
    db.flush()
    return True


def close_stale_presence(db, *, at=None):
    at = at or utcnow()
    rows = list(db.scalars(select(Presence).where(Presence.ended_at.is_(None),
        Presence.last_seen_at <= at - timedelta(seconds=OFFLINE_AFTER_SECONDS)).order_by(Presence.id).with_for_update(skip_locked=True)))
    for row in rows:
        row.ended_at = max(row.started_at, row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS))
    return len(rows)


def close_login_presence(db, session_id, *, at=None):
    owner_id = db.scalar(select(LoginSession.user_id).where(LoginSession.id == session_id))
    if owner_id is None: return None
    db.scalar(select(User).where(User.id == owner_id).with_for_update())
    login = db.scalar(select(LoginSession).where(LoginSession.id == session_id).with_for_update().execution_options(populate_existing=True))
    if login is None: return None
    at = at or utcnow()
    for row in db.scalars(select(Presence).where(Presence.auth_session_id == session_id, Presence.ended_at.is_(None)).order_by(Presence.id).with_for_update()):
        row.ended_at = max(row.started_at, min(at, row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS)))
    return login


def logout_session(db, session_id, *, at=None):
    login = close_login_presence(db, session_id, at=at)
    if login is not None:
        db.delete(login)


def close_user_presence(db, user_id, *, at=None):
    db.scalar(select(User).where(User.id == user_id).with_for_update())
    list(db.scalars(select(LoginSession).where(LoginSession.user_id == user_id).order_by(LoginSession.id).with_for_update()))
    at = at or utcnow()
    for row in db.scalars(select(Presence).where(Presence.user_id == user_id, Presence.ended_at.is_(None)).order_by(Presence.id).with_for_update()):
        row.ended_at = max(row.started_at, min(at, row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS)))


def correct_session(db, actor, session_id, checked_in_at, checked_out_at, reason, *, at=None):
    at = at or utcnow()
    if actor.role != Role.admin: raise HTTPException(403, "Only Admin can correct attendance")
    row = db.get(Attendance, session_id)
    if row is None: raise HTTPException(404, "Attendance session not found")
    # Same owner lock ordering as check-in/check-out; historical inactive users may be corrected.
    db.scalar(select(User).where(User.id == row.user_id).with_for_update())
    row = db.scalar(select(Attendance).where(Attendance.id == session_id).with_for_update().execution_options(populate_existing=True))
    if checked_in_at > at or (checked_out_at and checked_out_at > at):
        raise HTTPException(422, "Attendance times cannot be in the future")
    if checked_out_at and checked_out_at < checked_in_at:
        raise HTTPException(422, "Check-out must be after check-in")
    other = db.scalar(select(Attendance.id).where(Attendance.user_id == row.user_id, Attendance.id != row.id,
        or_(Attendance.checked_out_at.is_(None), Attendance.checked_out_at > checked_in_at),
        Attendance.checked_in_at < (checked_out_at or datetime.max.replace(tzinfo=timezone.utc))).limit(1))
    if other: raise HTTPException(409, "Attendance times cannot overlap")
    before = {"checkedInAt": row.checked_in_at.isoformat(), "checkedOutAt": row.checked_out_at.isoformat() if row.checked_out_at else None}
    row.checked_in_at, row.checked_out_at, row.corrected_by, row.correction_reason = checked_in_at, checked_out_at, actor.id, reason
    db.add(AuditEntry(actor_id=actor.id, action="Corrected staff attendance", entity_type="attendance", entity_id=row.id,
        meta={"reason": reason, "before": before, "after": {"checkedInAt": checked_in_at.isoformat(), "checkedOutAt": checked_out_at.isoformat() if checked_out_at else None}}))
    return row
