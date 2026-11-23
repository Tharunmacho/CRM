"""Explicit attendance and bounded, transparent browser-presence estimates."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.services.parallel_reads import parallel_reads
from app.db.models import (AuditEntry, Role, Session as LoginSession, not_deleted_user,
                           StaffAttendanceRequest as AttendanceRequest,
                           StaffAttendanceSession as Attendance,
                           StaffPresenceClient as PresenceClient,
                           StaffPresenceInterval as Presence, User)
from app.core.roles import is_admin_level

HEARTBEAT_SECONDS = 30
OFFLINE_AFTER_SECONDS = 90
IDLE_AFTER_SECONDS = 300
# A recruiter whose browser has sent no heartbeat for this long has gone: the open session is closed at their last heartbeat.
ABANDONED_AFTER_SECONDS = 30 * 60
# After the sweep closed a session for silence (a tab hidden for 30 minutes, a laptop asleep), the page coming back starts a NEW session, so the
# rest of the day is counted. A deliberate check-out or sign-out is never undone by a heartbeat. False = the strict rule: a heartbeat
# starts a session only when the person has no session at all today.
RESTART_AFTER_INACTIVITY = True
# The roles that have attendance (the same two the Check in button accepts). Administrators have none.
TRACKED_ROLES = (Role.staff, Role.manager)
# Where a session came from / went to is kept in the request key (no schema change): a manual press carries a browser uuid.
LOGIN_KEY, HEARTBEAT_KEY, LOGOUT_KEY, SWEEP_KEY, MISSED_KEY = "login:", "auto:", "logout:", "sweep:", "missed:"
KEY_SOURCES = ((LOGIN_KEY, "login"), (HEARTBEAT_KEY, "heartbeat"), (LOGOUT_KEY, "logout"), (SWEEP_KEY, "inactivity"), (MISSED_KEY, "missed"))


def key_source(key):
    """'login', 'heartbeat', 'logout', 'inactivity' or 'missed' for a session the system opened or closed itself, 'manual' for a button press."""
    if not key:
        return None
    for prefix, name in KEY_SOURCES:
        if key.startswith(prefix):
            return name
    return "manual"


def utcnow():
    return datetime.now(timezone.utc)


def lock_staff(db, user_id, *, clock=False):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    allowed = (Role.staff, Role.manager) if clock else (Role.staff,)
    if user is None or not user.active or user.role not in allowed:
        raise HTTPException(403, "Only active recruiter accounts can clock in or out")
    return user


def lock_staff_login(db, user, session_id, *, at=None, clock=False):
    """Mutations, logout and revocation share User → Login → presence locks."""
    now = at or utcnow()
    if session_id:
        # Both rows in ONE locking statement, user first (`OF` fixes the order, the same User → Login order as everywhere). The common case
        # (an active staff member with a live session) is then one round trip instead of two; anything else takes the exact path below
        # so the refusal is the same as before.
        found = db.execute(select(User, LoginSession).join(LoginSession, LoginSession.user_id == User.id)
                           .where(User.id == user.id, LoginSession.id == session_id).with_for_update(of=[User, LoginSession])
                           .execution_options(populate_existing=True)).one_or_none()
        if found is not None:
            person, login = found
            allowed = (Role.staff, Role.manager) if clock else (Role.staff,)
            if person.active and person.role in allowed and login.expires_at > now:
                return now
    lock_staff(db, user.id, clock=clock)
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
    # A shift that never got its check-out counts for nothing once its day is over: half a day's punches prove nothing about how long the person
    # stayed, and the monthly policy (attendance_policy.calculate_day) gives that day no worked minutes either, so the board and the month agree.
    # While the day is still running (it contains `at`) an open shift counts up to now, which is what "still logged in" means.
    finished = end <= at

    def stop(row):
        return row.checked_out_at or (None if finished else at)

    shifts = merged((max(start, row.checked_in_at), min(cutoff, stop(row))) for row in attendance if stop(row) is not None)
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
                "durationSeconds": seconds([(max(start, row.checked_in_at), min(cutoff, stop(row)))]) if stop(row) is not None else 0,
                # True for a shift that was never checked out and whose day is over (a missed check-out): it has no hours.
                "unclosed": (row.checked_out_at is None and finished) or (row.checkout_request_key or "").startswith(MISSED_KEY),
                "checkInSource": key_source(row.checkin_request_key), "checkOutSource": key_source(row.checkout_request_key),
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
    team = is_admin_level(user)
    if not team and staff_id and staff_id != user.id:
        raise HTTPException(403, "You can view only your own attendance")
    people = select(User).where(or_(User.role.in_([Role.staff, Role.manager]), User.id.in_(select(Attendance.user_id))), not_deleted_user())
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
    db.add(AuditEntry(actor_id=user.id, action="Recruiter checked in", entity_type="attendance", entity_id=row.id,
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
    db.add(AuditEntry(actor_id=user.id, action="Recruiter checked out", entity_type="attendance", entity_id=row.id,
                      meta={"checkedOutAt": row.checked_out_at.isoformat()}))
    return _remember_request(db, user, "check_out", request_key, row, at)


# ----------------------------------------------------------------------------------------------- sign-in counts as attendance
# A sign-in (and, for people already signed in, the first page load of the day) starts the day's attendance, a sign-out ends it, and a
# browser that went silent for ABANDONED_AFTER_SECONDS is closed at its last heartbeat. The Check in / Check out buttons stay as a manual
# override. Everything here uses the same table, the same one-open-session index and the same audit as the buttons; the request key says
# who opened or closed the row (LOGIN_KEY / HEARTBEAT_KEY / LOGOUT_KEY / SWEEP_KEY / MISSED_KEY), so no schema change is needed.

def _agency_day_start(at):
    from app.core.config import get_settings
    zone = get_settings().agency_timezone
    return bounds(at.astimezone(ZoneInfo(zone)).date(), zone)[0]


def _last_activity(db, row):
    """The last moment this open session was seen: the newest heartbeat of the person since check-in, else the check-in itself."""
    seen = db.scalar(select(func.max(Presence.last_seen_at)).where(Presence.user_id == row.user_id, Presence.last_seen_at >= row.checked_in_at))
    return max(row.checked_in_at, seen) if seen is not None else row.checked_in_at


def _close_automatically(db, row, when, key, source, *, actor_id=None, action="Attendance closed automatically", **meta):
    row.checked_out_at, row.checkout_request_key = max(when, row.checked_in_at), key
    db.add(AuditEntry(actor_id=actor_id, action=action, entity_type="attendance", entity_id=row.id,
                      meta={"checkedOutAt": row.checked_out_at.isoformat(), "source": source, **meta}))
    db.flush()


def start_session(db, user, source, *, key=None, at=None):
    """Make sure an attendance session is open for this person; return it, or None when their role has no attendance.

    Idempotent: a second sign-in, a second tab or a second heartbeat finds the open session instead of starting another (the partial unique
    index allows only one open row per person). An open session that was abandoned is closed first, so the new one starts clean:
    a recruiter's at their last heartbeat; a manager has no heartbeat, so an open session from before today is closed at its own start
    (no hours, flagged as a missed check-out) instead of being given invented hours. `source='heartbeat'` (already signed in when this
    was deployed, or the cookie outlived the day, or the tab was hidden past the 30-minute limit) starts a session when the person has no
    session today, or the last one was closed for silence; it never restarts the day after a check-out or a sign-out."""
    at = at or utcnow()
    if not user.active or user.role not in TRACKED_ROLES:
        return None
    db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    day_start = _agency_day_start(at)
    row = db.scalar(select(Attendance).where(Attendance.user_id == user.id, Attendance.checked_out_at.is_(None)).with_for_update())
    if row is not None:
        if user.role == Role.staff:
            last = _last_activity(db, row)
            stale, close_at, close_key, why = last <= at - timedelta(seconds=ABANDONED_AFTER_SECONDS), last, SWEEP_KEY + row.id, "inactivity"
        else:
            stale, close_at, close_key, why = row.checked_in_at < day_start, row.checked_in_at, MISSED_KEY + row.id, "missed"
        if not stale:
            return row
        _close_automatically(db, row, close_at, close_key, why)
    if source == "heartbeat":
        latest = db.scalar(select(Attendance).where(
            Attendance.user_id == user.id,
            or_(Attendance.checked_in_at >= day_start, Attendance.checked_out_at >= day_start)).order_by(Attendance.checked_in_at.desc()).limit(1))
        if latest is not None:
            closed_for_silence = (latest.checkout_request_key or "").startswith((SWEEP_KEY, MISSED_KEY))
            if not (RESTART_AFTER_INACTIVITY and closed_for_silence):
                return None
    key = (key or ((LOGIN_KEY if source == "login" else HEARTBEAT_KEY) + uuid4().hex))[:64]
    created = Attendance(user_id=user.id, checked_in_at=at, checkin_request_key=key)
    try:
        with db.begin_nested():
            db.add(created)
            db.flush()
    except IntegrityError:
        return db.scalar(select(Attendance).where(Attendance.user_id == user.id, Attendance.checked_out_at.is_(None)))
    db.add(AuditEntry(actor_id=user.id, action="Recruiter checked in", entity_type="attendance", entity_id=created.id,
                      meta={"checkedInAt": at.isoformat(), "source": source}))
    _remember_request(db, user, "check_in", key, created, at)
    return created


def close_abandoned_sessions(db, *, at=None):
    """The presence worker's sweep: close every recruiter's open session whose browser has sent nothing for ABANDONED_AFTER_SECONDS, at the
    last heartbeat (their hours stay honest and 'Missed check-outs' keeps only what really could not be closed). Managers have no heartbeat,
    so they are never swept here; theirs is closed at the next sign-in (see start_session) or by the person."""
    at = at or utcnow()
    seen = (select(func.max(Presence.last_seen_at)).where(Presence.user_id == Attendance.user_id, Presence.last_seen_at >= Attendance.checked_in_at)
            .correlate(Attendance).scalar_subquery())
    last = func.greatest(Attendance.checked_in_at, func.coalesce(seen, Attendance.checked_in_at))
    rows = db.execute(select(Attendance, last).join(User, User.id == Attendance.user_id)
                      .where(Attendance.checked_out_at.is_(None), User.role == Role.staff,
                             last <= at - timedelta(seconds=ABANDONED_AFTER_SECONDS))
                      .order_by(Attendance.id).with_for_update(of=Attendance, skip_locked=True)).all()
    for row, stopped in rows:
        _close_automatically(db, row, stopped, SWEEP_KEY + row.id, "inactivity")
    return len(rows)


def _close_on_logout(db, login, at):
    user = db.get(User, login.user_id)
    if user is None or user.role not in TRACKED_ROLES:
        return
    # Still working in another browser (a page that sent a heartbeat in the last 90 s under a different login): the day goes on.
    elsewhere = db.scalar(select(Presence.id).where(Presence.user_id == login.user_id, Presence.ended_at.is_(None), Presence.auth_session_id != login.id,
                                                    Presence.last_seen_at > at - timedelta(seconds=OFFLINE_AFTER_SECONDS)).limit(1))
    if elsewhere is not None:
        return
    row = db.scalar(select(Attendance).where(Attendance.user_id == login.user_id, Attendance.checked_out_at.is_(None)).with_for_update())
    if row is not None:
        _close_automatically(db, row, at, LOGOUT_KEY + login.id, "logout", actor_id=login.user_id, action="Recruiter checked out")


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
            # A page appearing (or coming back after 90 s of silence) is the moment to make sure the day's attendance exists: this covers
            # people who were already signed in. It must run BEFORE this page's presence row exists, or the new row would look like activity.
            start_session(db, user, "heartbeat", at=at)
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


def logout_session(db, session_id, *, at=None, close_attendance=False):
    """Remove a login and end its presence. Only an explicit sign-out (`close_attendance=True`, routers/auth.py) also ends the day's attendance:
    cleaning up an EXPIRED login found later (core/deps.py) must never check out someone who is working in another browser right now."""
    login = close_login_presence(db, session_id, at=at)
    if login is not None:
        if close_attendance:
            _close_on_logout(db, login, at or utcnow())
        db.delete(login)


def close_user_presence(db, user_id, *, at=None):
    db.scalar(select(User).where(User.id == user_id).with_for_update())
    list(db.scalars(select(LoginSession).where(LoginSession.user_id == user_id).order_by(LoginSession.id).with_for_update()))
    at = at or utcnow()
    for row in db.scalars(select(Presence).where(Presence.user_id == user_id, Presence.ended_at.is_(None)).order_by(Presence.id).with_for_update()):
        row.ended_at = max(row.started_at, min(at, row.last_seen_at + timedelta(seconds=OFFLINE_AFTER_SECONDS)))


def correct_session(db, actor, session_id, checked_in_at, checked_out_at, reason, *, at=None):
    at = at or utcnow()
    if not is_admin_level(actor): raise HTTPException(403, "Only an Admin or MD can correct attendance")
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
    db.add(AuditEntry(actor_id=actor.id, action="Corrected recruiter attendance", entity_type="attendance", entity_id=row.id,
        meta={"reason": reason, "before": before, "after": {"checkedInAt": checked_in_at.isoformat(), "checkedOutAt": checked_out_at.isoformat() if checked_out_at else None}}))
    return row
