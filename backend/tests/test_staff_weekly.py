"""Weekly staff report: hours, absence, daily tasks, stage moves, funnel, week boundaries, timezone, permissions, export.

Runs on the explicitly guarded disposable PostgreSQL DB (see test_database_naming._guard_test_url). The agency zone is
Asia/Kolkata (UTC+5:30, no daylight saving). Time is frozen at Thursday 2026-11-05 14:00 IST, in the week
Monday 2026-11-02 to Sunday 2026-11-08.
"""
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AttendanceCalendarDay, Candidate, Client, Company, DailyGoal,
    DailyWorkItem, Designation, Lead, LeadActivity, Order, Role, Source, StaffAttendanceSession, StaffPresenceInterval, Task, TaskKind, User,
)
from app.services import staff_weekly, staff_weekly_export
from test_database_naming import _migrate, schema_database

# Importing the router builds the database engine, which reads settings; use stand-in settings for that import, then
# put the real function back in every module that captured the stand-in (other test files import the same modules).
_settings_patch = patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused",
))
_stand_in = _settings_patch.start()
try:
    from app.api.routers import reports as reports_router
finally:
    _settings_patch.stop()
    import sys
    from app.core.config import get_settings as _real_settings
    for _name, _module in list(sys.modules.items()):
        if _name.startswith("app.") and getattr(_module, "get_settings", None) is _stand_in:
            _module.get_settings = _real_settings

TZ = "Asia/Kolkata"
IST = ZoneInfo(TZ)
MONDAY, TUESDAY, WEDNESDAY, THURSDAY = (date(2026, 11, 2) + timedelta(days=i) for i in range(4))
NOW = datetime(2026, 11, 5, 14, 0, tzinfo=IST).astimezone(timezone.utc)


def ist(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), IST).astimezone(timezone.utc)


def frozen():
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)
    return FrozenDatetime


def day_of(staff, iso):
    return next(d for d in staff["days"] if d["date"] == iso)


def person(report, user_id):
    return next(p for p in report["staff"] if p["id"] == user_id)


@pytest.fixture
def desk(schema_database):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=active, created_at=long_ago,
                         updated_at=long_ago)
                    for key, role, active in (("admin", Role.admin, True), ("manager", Role.manager, True),
                                              ("asha", Role.staff, True), ("ravi", Role.staff, True),
                                              ("meena", Role.staff, True), ("gone", Role.staff, False))])
        db.add(Client(id="client", name="Mr Contact", country="UAE"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Gulf Builders", country="UAE"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add(Order(id="order", ref="ORD-1", company_id="company", designation_id="designation", openings=40, country="Qatar"))
        db.commit()
    return SimpleNamespace(sessions=sessions, engine=schema_database)


def report(desk, **kwargs):
    with desk.sessions() as db:
        return staff_weekly.build_report(db, kwargs.pop("from_date", None), kwargs.pop("to_date", None), TZ,
                                         now=kwargs.pop("now", NOW), **kwargs)


def seed_attendance(desk):
    with desk.sessions() as db:
        add = lambda user, a, b: db.add(StaffAttendanceSession(
            user_id=user, checked_in_at=a, checked_out_at=b, checkin_request_key=f"in-{user}-{a.isoformat()}",
            checkout_request_key=None if b is None else f"out-{user}-{a.isoformat()}"))
        # Overnight Sunday 22:00 -> Monday 02:00 IST: only the two hours after midnight belong to Monday.
        add("asha", ist(MONDAY - timedelta(days=1), 22), ist(MONDAY, 2))
        add("asha", ist(MONDAY, 10), ist(MONDAY, 18, 30))
        add("asha", ist(TUESDAY, 10), ist(TUESDAY, 14))
        add("asha", ist(TUESDAY, 15), ist(TUESDAY, 19))
        add("asha", ist(THURSDAY, 10, 5), None)  # open session today
        add("ravi", ist(MONDAY, 9), ist(MONDAY, 17))
        add("ravi", ist(TUESDAY, 10), None)  # never closed on a past day
        add("ravi", ist(WEDNESDAY, 10), ist(WEDNESDAY, 19))
        # Presence: online for 3 of asha's Monday hours.
        db.add(StaffPresenceInterval(user_id="asha", client_id="tab", state="online", started_at=ist(MONDAY, 10),
                                     ended_at=ist(MONDAY, 13), last_seen_at=ist(MONDAY, 13)))
        db.add(AttendanceCalendarDay(user_id="meena", attendance_date=TUESDAY, status="PL", reason="approved", actor_id="admin"))
        db.commit()


def add_candidate(db, key, owner, *, stage, status=ApplicationStatus.active, created=None):
    db.add(Candidate(id=key, ref=f"CND-{key}", name=f"Candidate {key}", phone=f"900000{abs(hash(key)) % 10**5:05}",
                     source=Source.walk_in, owner_id=owner))
    db.flush()
    db.add(Application(id=f"app-{key}", candidate_id=key, order_id="order", owner_id=owner, current_stage=stage, status=status,
                       created_at=created or ist(MONDAY, 9), updated_at=created or ist(MONDAY, 9)))
    db.flush()


def move(db, key, stage, outcome, at, owner, *, attempt=1, reopened=None):
    db.add(ApplicationStage(application_id=f"app-{key}", stage=stage, attempt=attempt, outcome=outcome, owner_id=owner,
                            completed_by=owner, completed_at=at, reopened_reason=reopened, data={}))


def seed_pipeline(desk):
    with desk.sessions() as db:
        add_candidate(db, "c1", "asha", stage=3)
        add_candidate(db, "c2", "asha", stage=11, status=ApplicationStatus.deployed)
        add_candidate(db, "c3", "asha", stage=1, created=NOW - timedelta(days=20))  # stuck: nothing completed for 20 days
        add_candidate(db, "c4", "ravi", stage=2)
        add_candidate(db, "c5", "ravi", stage=1, status=ApplicationStatus.closed)
        move(db, "c1", 1, "Passed", ist(MONDAY, 11), "asha")
        move(db, "c1", 2, "Forwarded", ist(TUESDAY, 11), "asha")
        move(db, "c1", 2, "Forwarded", ist(WEDNESDAY, 11), "asha", attempt=2, reopened="redo")  # reopened: not counted
        move(db, "c2", 9, "Arrived", ist(TUESDAY, 12), "asha")
        move(db, "c2", 10, "Stamped", ist(WEDNESDAY, 12), "asha")
        move(db, "c2", 11, "Booked", ist(THURSDAY, 10), "asha")
        # Week boundaries in agency time: 23:59 on the Sunday before is last week, 00:00 Monday is this week.
        move(db, "c4", 1, "Passed", ist(MONDAY - timedelta(days=1), 23, 59), "ravi")
        move(db, "c4", 2, "Forwarded", ist(MONDAY, 0), "ravi")
        move(db, "c5", 1, "Failed", ist(WEDNESDAY, 9), "ravi")
        db.commit()


def seed_tasks(desk):
    with desk.sessions() as db:
        task_no = [0]

        def item(owner, day, slot, *, status="open", done_at=None, due=None, cancelled=False, candidate="c1"):
            task_no[0] += 1
            tid = f"t{task_no[0]}"
            db.add(Task(id=tid, kind=TaskKind.stage, title="Stage", candidate_id=candidate, application_id=f"app-{candidate}", stage=1,
                        owner_id=owner, created_at=ist(day, 8), cancelled_at=ist(day, 9) if cancelled else None))
            db.flush()
            due = due or ist(day, 10 + slot)
            db.add(DailyWorkItem(owner_id=owner, work_date=day, task_id=tid, application_id=f"app-{candidate}", candidate_id=candidate,
                                 stage=1, slot_no=slot, session="morning", due_at=due, status=status, completed_at=done_at,
                                 postponed_at=None, allocated_at=ist(day, 8)))
        # asha Monday: 3 on time, 1 done the next day (late and postponed), 1 never done.
        for slot in (1, 2, 3):
            item("asha", MONDAY, slot, status="completed", done_at=ist(MONDAY, 10 + slot, 0) - timedelta(minutes=5))
        item("asha", MONDAY, 4, status="completed", done_at=ist(TUESDAY, 10))
        item("asha", MONDAY, 5)
        # asha Tuesday: 2 on time, 3 still open and past their deadline; plus one released item that must be ignored.
        for slot in (1, 2):
            item("asha", TUESDAY, slot, status="completed", done_at=ist(TUESDAY, 10 + slot) - timedelta(minutes=1))
        for slot in (3, 4, 5):
            item("asha", TUESDAY, slot)
        item("asha", TUESDAY, 6, status="released")
        # ravi: three done on Monday; two open today with a deadline still ahead; one old open item from last week.
        for slot in (1, 2, 3):
            item("ravi", MONDAY, slot, status="completed", done_at=ist(MONDAY, 10 + slot) - timedelta(minutes=1), candidate="c4")
        for slot in (1, 2):
            item("ravi", THURSDAY, slot, due=ist(THURSDAY, 16 + slot), candidate="c4")
        item("ravi", date(2026, 10, 30), 1, candidate="c4")
        db.commit()


def seed_leads(desk):
    """Synthetic leads for the lead-sheet part of the report. asha: 4 assigned this week (3 called), ravi: 3 assigned, none called."""
    with desk.sessions() as db:
        def lead(key, owner, status, assigned, *, follow=None, calls=0):
            db.add(Lead(id=key, name=f"Lead {key}", phone=f"+9190000{abs(hash(key)) % 10**5:05}", owner_id=owner, status=status,
                        assigned_at=assigned, next_follow_up_at=follow, call_count=calls, created_time=assigned))

        lead("a1", "asha", "interested", ist(MONDAY, 9), calls=2)
        lead("a2", "asha", "resume_received", ist(MONDAY, 9), calls=1)
        lead("a3", "asha", "converted", ist(TUESDAY, 9), calls=1)
        lead("a4", "asha", "new", ist(WEDNESDAY, 9), follow=ist(TUESDAY, 12))          # never called, follow-up already past
        lead("a5", "asha", "contacted", ist(MONDAY - timedelta(days=3), 9), calls=1)    # assigned last week, called this week
        for n in (1, 2, 3):
            lead(f"r{n}", "ravi", "new", ist(TUESDAY, 9))
        lead("old", "ravi", "new", ist(MONDAY - timedelta(days=9), 9))                  # assigned long ago: not this period
        db.flush()

        def act(lead_id, user, kind, at, meta=None):
            db.add(LeadActivity(lead_id=lead_id, user_id=user, kind=kind, summary=kind, meta=meta, created_at=at))
        act("a1", "asha", "call", ist(MONDAY, 11), {"outcome": "interested"})
        act("a1", "asha", "call", ist(TUESDAY, 11), {"outcome": "answered"})
        act("a2", "asha", "call", ist(MONDAY, 12), {"outcome": "answered"})
        act("a2", "asha", "status", ist(MONDAY, 13), {"from": "contacted", "to": "interested"})
        act("a2", "asha", "resume", ist(TUESDAY, 12))
        act("a3", "asha", "call", ist(TUESDAY, 13), {"outcome": "answered"})
        act("a3", "asha", "convert", ist(WEDNESDAY, 12))
        act("a5", "asha", "call", ist(TUESDAY, 14), {"outcome": "no_answer"})
        act("a5", "asha", "call", ist(MONDAY - timedelta(days=2), 10), {"outcome": "answered"})   # last week: outside the period
        db.commit()


# ------------------------------------------------------------------ range

def test_default_range_is_the_current_monday_to_sunday_week_in_agency_time(desk):
    data = report(desk)
    assert data["range"]["from"] == "2026-11-02" and data["range"]["to"] == "2026-11-08"
    assert data["range"]["isWeek"] and data["range"]["isCurrent"] and len(data["range"]["days"]) == 7
    # 2026-11-08 20:00 UTC is already Monday 01:30 IST: the agency day, not the UTC day, decides the week.
    late_sunday = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)
    assert report(desk, now=late_sunday)["range"]["from"] == "2026-11-09"


def test_range_validation(desk):
    with desk.sessions() as db:
        with pytest.raises(HTTPException) as reversed_range:
            staff_weekly.build_report(db, date(2026, 11, 8), date(2026, 11, 2), TZ, now=NOW)
        with pytest.raises(HTTPException) as too_long:
            staff_weekly.build_report(db, date(2026, 1, 1), date(2026, 6, 1), TZ, now=NOW)
    assert reversed_range.value.status_code == 422 and too_long.value.status_code == 422
    assert report(desk, from_date=date(2026, 11, 4))["range"]["to"] == "2026-11-08"  # one date means that date's week


# ------------------------------------------------------------------ attendance

def test_hours_are_split_by_agency_day_and_absence_ignores_leave_and_days_off(desk):
    seed_attendance(desk)
    data = report(desk)
    asha = person(data, "asha")
    assert day_of(asha, "2026-11-02")["workedSeconds"] == (2 + 8.5) * 3600  # 2 h after midnight + 10:00-18:30
    assert day_of(asha, "2026-11-03")["workedSeconds"] == 8 * 3600  # two sessions, lunch excluded
    assert day_of(asha, "2026-11-04")["state"] == "absent"
    assert day_of(asha, "2026-11-05")["state"] == "present" and day_of(asha, "2026-11-05")["workedSeconds"] == int(3.9166 * 3600) \
        or abs(day_of(asha, "2026-11-05")["workedSeconds"] - (3 * 3600 + 55 * 60)) < 2
    assert asha["attendance"]["hoursWorked"] == pytest.approx(10.5 + 8 + 3 + 55 / 60, abs=0.01)
    assert asha["attendance"]["absentDays"] == 1 and asha["attendance"]["absentDates"] == ["2026-11-04"]
    assert day_of(asha, "2026-11-06")["state"] == "future"
    assert day_of(asha, "2026-11-08")["state"] == "future"
    assert asha["status"] == "worked"
    assert asha["attendance"]["activeHours"] == pytest.approx(3.0, abs=0.01)  # online presence inside the clocked time
    assert day_of(asha, "2026-11-02")["activeSeconds"] == 3 * 3600

    meena = person(data, "meena")  # never clocked in
    assert meena["status"] == "no_attendance" and meena["attendance"]["hoursWorked"] == 0
    assert day_of(meena, "2026-11-03")["state"] == "leave"  # approved leave is not an absence
    assert meena["attendance"]["absentDates"] == ["2026-11-02", "2026-11-04"]
    assert day_of(meena, "2026-11-05")["state"] == "pending"  # today, not in yet: not judged absent

    ravi = person(data, "ravi")
    assert day_of(ravi, "2026-11-03")["state"] == "present" and day_of(ravi, "2026-11-03")["missingCheckout"]
    assert day_of(ravi, "2026-11-03")["workedSeconds"] == 0  # open session on an earlier day has no trustworthy length
    assert ravi["attendance"]["missingCheckouts"] == 1 and ravi["attendance"]["hoursWorked"] == 17

    assert "gone" not in {p["id"] for p in data["staff"]}  # inactive and no activity: not reported
    assert data["summary"]["staffWorked"] == 2 and data["summary"]["staffAbsent"] == 1
    kinds = {(h["kind"], h["staffId"]) for h in data["highlights"]}
    assert ("no_attendance", "meena") in kinds and ("absent_days", "asha") in kinds and ("missing_checkout", "ravi") in kinds


def test_sunday_is_the_default_weekly_off_not_an_absence(desk):
    seed_attendance(desk)
    sunday_noon = datetime(2026, 11, 8, 12, 0, tzinfo=IST).astimezone(timezone.utc)
    data = report(desk, now=sunday_noon)
    assert day_of(person(data, "asha"), "2026-11-08")["state"] == "off"
    assert day_of(person(data, "asha"), "2026-11-06")["state"] == "absent"  # an ordinary working day with no check-in


# ------------------------------------------------------------------ stages

def test_stage_moves_outcomes_snapshot_and_week_boundaries(desk):
    seed_pipeline(desk)
    data = report(desk)
    asha, ravi = person(data, "asha"), person(data, "ravi")
    assert asha["stages"]["moves"] == 5  # the reopened attempt is excluded
    assert asha["stages"]["movesByStage"][1] == 1 and asha["stages"]["movesByStage"][2] == 1
    assert asha["outcomes"]["forwarded"] == 1 and asha["outcomes"]["visaReceived"] == 1
    assert asha["outcomes"]["visaStamped"] == 1 and asha["outcomes"]["deployed"] == 1
    assert asha["stages"]["inStageNow"][3] == 1 and asha["stages"]["inStageNow"][1] == 1  # c1 waits at 3, c3 at 1
    assert asha["stages"]["deployedTotal"] == 1 and asha["stages"]["activeCandidates"] == 2
    # Monday 00:00 IST is inside the week, Sunday 23:59 IST is not.
    assert ravi["stages"]["moves"] == 2 and ravi["outcomes"]["forwarded"] == 1
    assert day_of(ravi, "2026-11-02")["stageMoves"] == 1
    assert ravi["stages"]["closed"] == 1
    previous = report(desk, from_date=date(2026, 10, 26), to_date=date(2026, 11, 1))
    assert person(previous, "ravi")["stages"]["moves"] == 1 and person(previous, "asha")["stages"]["moves"] == 0
    assert previous["range"]["isCurrent"] is False


def test_team_funnel_counts_reached_stages_and_conversion(desk):
    seed_pipeline(desk)
    funnel = {row["stage"]: row for row in report(desk)["funnel"]}
    assert [funnel[n]["reached"] for n in (1, 2, 3, 4, 11)] == [5, 3, 2, 1, 1]  # c1 at 3 and c2 deployed pass stage 3
    assert funnel[2]["conversionPercent"] == 60.0 and funnel[2]["lostFromPrevious"] == 2
    assert funnel[1]["conversionPercent"] is None
    assert funnel[1]["inStageNow"] == 1 and funnel[1]["closedHere"] == 1 and funnel[3]["inStageNow"] == 1
    assert sum(row["movesInPeriod"] for row in funnel.values()) == 7
    assert [row["label"] for row in funnel.values()][0] == "Prescreening" and len(funnel) == 11


def test_stuck_candidates_and_the_idle_highlight(desk):
    seed_pipeline(desk)
    data = report(desk)
    stuck = person(data, "asha")["stuck"]
    assert stuck["count"] == 1 and stuck["oldestDays"] == 20 and stuck["examples"][0]["candidate"] == "Candidate c3"
    assert person(data, "ravi")["stuck"]["count"] == 0
    # c1 and c4 moved this week, so they are not stuck.
    assert [h["kind"] for h in data["highlights"] if h["staffId"] == "asha"].count("stuck_candidates") == 1


def test_detail_lists_each_candidate_end_to_end(desk):
    seed_pipeline(desk)
    with desk.sessions() as db:
        data = staff_weekly.build_report(db, None, None, TZ, now=NOW, staff_id="asha", with_candidates=True)
    assert [p["id"] for p in data["staff"]] == ["asha"]
    items = {c["candidateId"]: c for c in data["candidates"]["items"]}
    assert data["candidates"]["total"] == 3
    assert items["c2"]["status"] == "deployed" and items["c2"]["reached"] == 11 and [h["stage"] for h in items["c2"]["history"]] == [9, 10, 11]
    assert items["c1"]["stageLabel"] == "Interview scheduling" and items["c1"]["movedInRange"] == 2
    assert items["c3"]["daysInStage"] == 20 and items["c3"]["order"] == "ORD-1" and items["c3"]["client"] == "Mr Contact"
    assert len(data["moves"]) == 5 and data["moves"][0]["stage"] == 11  # newest first


# ------------------------------------------------------------------ daily tasks

def test_daily_task_numbers_follow_the_stored_item_states(desk):
    seed_pipeline(desk)
    seed_tasks(desk)
    data = report(desk)
    assert data["sources"]["dailyTasks"] == {"available": True, "hasData": True, "goals": {
        "candidates": 5, "forwarded_to_client": 2, "stage_moves": 5}}
    tasks = person(data, "asha")["tasks"]
    assert (tasks["assigned"], tasks["completed"]) == (10, 6)  # the released item is ignored
    assert tasks["onTime"] == 5 and tasks["completionPercent"] == 60.0
    assert tasks["postponed"] == 5  # Monday's late and open items plus Tuesday's three open ones
    assert tasks["overdue"] == 4  # open and past their hourly deadline
    assert tasks["backlog"] == 4 and tasks["oldestBacklogDays"] == 3
    assert day_of(person(data, "asha"), "2026-11-02")["tasks"] == {"assigned": 5, "done": 4, "missed": 1}
    ravi = person(data, "ravi")["tasks"]
    assert (ravi["assigned"], ravi["completed"], ravi["overdue"], ravi["postponed"]) == (5, 3, 0, 0)
    assert ravi["backlog"] == 1 and ravi["oldestBacklogDays"] == 6  # last week's open item, still carried
    assert data["summary"]["tasksAssigned"] == 15 and data["summary"]["tasksCompleted"] == 9
    assert data["summary"]["taskCompletionPercent"] == 60.0
    assert any(h["kind"] == "backlog" and h["staffId"] == "asha" for h in data["highlights"])

    previous = person(report(desk, from_date=date(2026, 10, 26), to_date=date(2026, 11, 1)), "ravi")["tasks"]
    # Last week as it stood at its end: the Friday item was open and past its deadline, and Monday's work does not exist yet.
    assert (previous["assigned"], previous["completed"], previous["overdue"], previous["backlog"]) == (1, 0, 1, 1)


def test_per_person_goal_overrides_the_agency_goal(desk):
    seed_pipeline(desk)
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_daily_goals SET target = 4 WHERE user_id IS NULL AND metric = 'stage_moves'"))
        db.add(DailyGoal(user_id="ravi", metric="stage_moves", target=10))
        db.commit()
    data = report(desk)
    assert person(data, "asha")["goals"]["stageMovesPerDay"] == 4 and person(data, "ravi")["goals"]["stageMovesPerDay"] == 10


def test_task_section_degrades_when_the_quota_tables_are_missing(desk):
    seed_pipeline(desk)
    with desk.engine.begin() as connection:
        connection.execute(text("DROP TABLE workflow_daily_goal_progress, workflow_daily_goals, workflow_daily_work_items CASCADE"))
    data = report(desk)
    assert data["sources"]["dailyTasks"]["available"] is False
    asha = person(data, "asha")
    assert asha["tasks"]["available"] is False and asha["tasks"]["completionPercent"] is None
    assert asha["stages"]["moves"] == 5  # everything else still works
    assert data["summary"]["tasksAvailable"] is False and any("not in this database" in n for n in data["notes"])


# ------------------------------------------------------------------ score

def test_score_is_the_weighted_average_of_measurable_parts(desk):
    seed_attendance(desk)
    seed_pipeline(desk)
    seed_tasks(desk)
    data = report(desk)
    asha = person(data, "asha")
    parts = {p["key"]: p for p in asha["scoreParts"]}
    assert parts["tasks"]["ratio"] == 0.6
    # 3 worked days (Mon, Tue, Thu) x goal 5 = 15 moves wanted, 5 made.
    assert parts["stages"]["ratio"] == pytest.approx(5 / 15, abs=1e-3)
    expected_hours = 8 + 8 + 8 + 8 * (4 / 9)  # Mon, Tue, absent Wed, and Thursday 10:00-14:00 of a 9 h shift
    assert asha["attendance"]["expectedHours"] == pytest.approx(expected_hours, abs=0.01)
    assert parts["hours"]["ratio"] == pytest.approx(asha["attendance"]["hoursWorked"] / expected_hours, abs=1e-3)
    weights = {p["key"]: p["weight"] for p in asha["scoreParts"]}
    expected = sum(parts[k]["ratio"] * weights[k] for k in parts) / sum(weights.values()) * 100
    assert asha["score"] == round(expected)
    meena = person(data, "meena")
    assert meena["score"] == 0  # expected hours exist and none were worked; no tasks or worked days to measure
    assert [p["key"] for p in meena["scoreParts"] if p["ratio"] is not None] == ["hours"]


def test_score_is_empty_when_nothing_can_be_measured(desk):
    # Everyone is new and the report covers next week: nothing is expected yet.
    data = report(desk, from_date=date(2026, 11, 9), to_date=date(2026, 11, 15))
    assert all(p["score"] is None for p in data["staff"])
    assert data["summary"]["averageScore"] is None and data["summary"]["staffWorked"] == 0


# ------------------------------------------------------------------ lead sheet numbers and the optional score part

def test_lead_numbers_per_person_for_the_period(desk):
    seed_leads(desk)
    data = report(desk)
    asha, ravi, meena = person(data, "asha"), person(data, "ravi"), person(data, "meena")
    lead = asha["leads"]
    assert lead["available"] is True
    assert (lead["assigned"], lead["contacted"], lead["notContacted"]) == (4, 3, 1)
    assert lead["calls"] == 5  # six calls logged by asha, one of them last week
    assert lead["worked"] == 4  # a1, a2, a3, a5
    assert lead["interested"] == 2  # a1 (call outcome) and a2 (status change)
    assert (lead["resumesReceived"], lead["converted"], lead["conversionPercent"]) == (1, 1, 25.0)
    assert lead["followUpsOverdue"] == 1 and lead["open"] == 4  # a1, a2, a4, a5 (a3 is converted)
    assert (ravi["leads"]["assigned"], ravi["leads"]["contacted"], ravi["leads"]["notContacted"], ravi["leads"]["calls"]) == (3, 0, 3, 0)
    assert ravi["leads"]["conversionPercent"] is None
    assert meena["leads"]["assigned"] == 0 and meena["leads"]["available"] is True
    summary = data["summary"]
    assert (summary["leadsAssigned"], summary["leadsContacted"], summary["leadsCalls"], summary["leadsWorked"]) == (7, 3, 5, 4)
    assert (summary["leadsConverted"], summary["leadsConversionPercent"], summary["leadsFollowUpsOverdue"]) == (1, 25.0, 1)
    assert summary["leadsAvailable"] is True and data["sources"]["leadSheet"]["available"] is True
    detail = report(desk, staff_id="asha")
    assert [p["id"] for p in detail["staff"]] == ["asha"] and detail["staff"][0]["leads"] == lead


def test_lead_period_follows_the_chosen_range(desk):
    seed_leads(desk)
    last_week = report(desk, from_date=date(2026, 10, 26), to_date=date(2026, 11, 1))
    assert person(last_week, "asha")["leads"]["assigned"] == 1  # only a5
    assert person(last_week, "asha")["leads"]["calls"] == 1
    next_week = report(desk, from_date=date(2026, 11, 9), to_date=date(2026, 11, 15))
    assert person(next_week, "asha")["leads"]["assigned"] == 0


def test_lead_follow_up_joins_the_score_only_when_leads_were_assigned(desk):
    seed_attendance(desk)
    seed_pipeline(desk)
    seed_tasks(desk)
    plain = report(desk)
    assert [p["key"] for p in person(plain, "asha")["scoreParts"]] == ["tasks", "hours", "stages"]
    assert {p["key"]: p["weight"] for p in person(plain, "asha")["scoreParts"]} == staff_weekly.WEIGHTS
    plain_scores = {p["id"]: p["score"] for p in plain["staff"]}

    seed_leads(desk)
    data = report(desk)
    asha, ravi, meena = person(data, "asha"), person(data, "ravi"), person(data, "meena")
    parts = {p["key"]: p for p in asha["scoreParts"]}
    assert list(parts) == ["tasks", "hours", "stages", "leads"]
    assert {k: p["weight"] for k, p in parts.items()} == {"tasks": 35, "hours": 30, "stages": 25, "leads": 10}
    assert parts["leads"]["ratio"] == 0.75 and "optional" in parts["leads"]["label"].lower()
    assert parts["leads"]["detail"] == "3 of 4 assigned leads called"
    assert asha["score"] == round(sum(p["ratio"] * p["weight"] for p in parts.values()))
    # ravi had leads and called none: the part is measured as zero, not dropped.
    assert ravi["scoreParts"][-1]["key"] == "leads" and ravi["scoreParts"][-1]["ratio"] == 0
    assert ravi["score"] < plain_scores["ravi"]
    # meena had no leads: exactly the old three parts and the old score.
    assert [p["key"] for p in meena["scoreParts"]] == ["tasks", "hours", "stages"] and meena["score"] == plain_scores["meena"]
    assert data["formula"]["weightsWithLeads"] == {"tasks": 35, "hours": 30, "stages": 25, "leads": 10}
    assert any("Lead follow-up (optional)" in line and "10%" in line for line in data["formula"]["lines"])


def test_lead_questions_to_ask(desk):
    seed_attendance(desk)
    seed_leads(desk)
    kinds = {(h["kind"], h["staffId"]) for h in report(desk)["highlights"]}
    assert ("leads_untouched", "ravi") in kinds           # 3 assigned, none called
    assert ("leads_untouched", "asha") not in kinds       # asha called 3 of 4
    assert ("leads_overdue", "asha") not in kinds         # only one overdue (threshold is 5)


def test_report_survives_missing_lead_tables(desk):
    seed_pipeline(desk)
    with desk.engine.begin() as connection:
        connection.execute(text("DROP TABLE crm_lead_activities, crm_leads, crm_lead_batches CASCADE"))
    data = report(desk)
    assert data["sources"]["leadSheet"]["available"] is False and data["summary"]["leadsAvailable"] is False
    assert all(p["leads"]["available"] is False for p in data["staff"])
    assert [p["key"] for p in person(data, "asha")["scoreParts"]] == ["tasks", "hours", "stages"]
    assert any("Lead sheet tables are not in this database" in n for n in data["notes"])
    assert "Leads by recruiter" in [name for name, _rows in staff_weekly_export.sheets(data)]


# ------------------------------------------------------------------ queries, permissions, export

def count_statements(engine, action):
    seen = []
    listener = lambda conn, cursor, statement, parameters, context, many: seen.append(statement)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return len(seen)


def test_query_count_does_not_grow_with_the_number_of_staff(desk):
    seed_attendance(desk)
    seed_pipeline(desk)
    seed_tasks(desk)
    small = count_statements(desk.engine, lambda: report(desk))
    with desk.sessions() as db:
        for n in range(12):
            db.add(User(id=f"extra{n}", name=f"Extra {n}", email=f"x{n}@example.test", role=Role.staff, active=True,
                        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    large = count_statements(desk.engine, lambda: report(desk))
    print("statements", small, large)
    assert small == large and large <= 25, (small, large)  # was 24 before the one lead-sheet aggregate statement


@pytest.fixture
def api(desk, monkeypatch):
    monkeypatch.setattr(staff_weekly, "datetime", frozen())
    monkeypatch.setattr(reports_router, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    actor = SimpleNamespace(id="admin", role=Role.admin)
    app = FastAPI()
    app.include_router(reports_router.router)

    def database():
        with desk.sessions() as db:
            yield db
    app.dependency_overrides[reports_router.get_db] = database
    app.dependency_overrides[reports_router.current_user] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, actor=actor, desk=desk)


def test_only_the_admin_can_open_the_report_or_export(api):
    seed_attendance(api.desk)
    for role, user in ((Role.manager, "manager"), (Role.staff, "asha")):
        api.actor.id, api.actor.role = user, role
        for url in ("/api/reports/staff-weekly", "/api/reports/staff-weekly/asha", "/api/reports/staff-weekly/export?format=csv"):
            assert api.client.get(url).status_code == 403, (role, url)
    api.actor.id, api.actor.role = "admin", Role.admin
    response = api.client.get("/api/reports/staff-weekly")
    assert response.status_code == 200 and response.json()["range"]["from"] == "2026-11-02"
    assert api.client.get("/api/reports/staff-weekly/nobody").status_code == 404
    assert api.client.get("/api/reports/staff-weekly?from_date=2026-11-08&to_date=2026-11-02").status_code == 422
    detail = api.client.get("/api/reports/staff-weekly/asha?from_date=2026-11-02&to_date=2026-11-08").json()
    assert [p["id"] for p in detail["staff"]] == ["asha"] and "candidates" in detail


def test_exports_are_real_files_with_the_report_content(api):
    seed_attendance(api.desk)
    seed_pipeline(api.desk)
    seed_tasks(api.desk)
    seed_leads(api.desk)
    book = api.client.get("/api/reports/staff-weekly/export?format=xlsx&from_date=2026-11-02&to_date=2026-11-08")
    assert book.status_code == 200 and "spreadsheetml" in book.headers["content-type"]
    assert 'filename="staff-weekly-2026-11-02-2026-11-08.xlsx"' in book.headers["content-disposition"]
    with ZipFile(BytesIO(book.content)) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        for name in ("Recruiter summary", "Day by day", "Stages by recruiter", "Leads by recruiter", "Team funnel", "Questions to ask"):
            assert name in workbook
        summary = archive.read("xl/worksheets/sheet2.xml").decode()
        assert "Asha" in summary and "No attendance" in summary and "Lead conversion %" in summary
        sheet_names = [part.split('"')[0] for part in workbook.split('<sheet name="')[1:]]
        leads_sheet = archive.read(f"xl/worksheets/sheet{sheet_names.index('Leads by recruiter') + 1}.xml").decode()
        assert "Assigned leads called" in leads_sheet and "Asha" in leads_sheet and "Ravi" in leads_sheet
    table = api.client.get("/api/reports/staff-weekly/export?format=csv")
    assert table.status_code == 200 and table.headers["content-type"].startswith("text/csv")
    body = table.content.decode("utf-8-sig").splitlines()
    assert body[0].startswith("Recruiter,Attendance,Days worked") and len(body) == 4  # asha, meena, ravi
    assert any(line.startswith("Asha,Worked,3,1,") for line in body)
    assert body[0].endswith("Leads assigned,Assigned leads called,Lead calls logged,Leads worked,Leads interested,Resumes received,"
                            "Leads converted,Lead conversion %,Lead follow-ups overdue")
    assert next(line for line in body if line.startswith("Asha,")).endswith(",4,3,5,4,2,1,1,25.0,1")


def test_csv_neutralises_formula_text():
    data = {"staff": [{"name": "=HYPERLINK(1)", "status": "worked", "score": None, "stuck": {"count": 0},
                       "attendance": {"workedDays": 0, "absentDays": 0, "hoursWorked": 0, "expectedHours": 0, "activeHours": 0},
                       "tasks": {"available": False, "completionPercent": None}, "stages": {"moves": 0, "activeCandidates": 0},
                       "outcomes": {k: 0 for k in ("forwarded", "interviewsScheduled", "selected", "visaReceived", "visaStamped", "deployed")}}]}
    assert "'=HYPERLINK(1)" in staff_weekly_export.csv_bytes(data).decode("utf-8-sig")


def test_report_uses_the_real_clock_when_none_is_given(desk):
    with desk.sessions() as db:
        data = staff_weekly.build_report(db, None, None, TZ)
    assert data["range"]["isCurrent"] and data["staff"]
