"""Daily work: one flat list of the recruiter's own workable candidates. No cap, no slots, no deadlines.

Runs on the explicitly guarded disposable PostgreSQL DB (see test_database_naming._guard_test_url).
Time is frozen: the agency zone is Asia/Kolkata, so 2026-11-03 03:00 UTC is Tuesday 08:30 local.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Candidate, CandidateStatus, Client, Company, DailyWorkItem, Designation, Notification, Order, Remark, Role, Source, Task, TaskKind, User,
)
from app.services import daily_work
from app.services.daily_work import scan_daily_work
from test_database_naming import _migrate, schema_database

with patch("app.core.config.get_settings", return_value=SimpleNamespace(
    database_url="postgresql+psycopg://unused@127.0.0.1:1/unused", storage_dir="unused", agency_timezone="Asia/Kolkata",
)):
    from app.api.routers import applications as applications_router
    from app.api.routers import daily_work as daily_router
    from app.api.routers import data as data_router
    from app.api.routers import workflow as workflow_router

TZ = "Asia/Kolkata"
TUESDAY = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)  # 08:30 IST
WEDNESDAY = TUESDAY + timedelta(days=1)


def frozen(clock):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"] if tz is None else clock["now"].astimezone(tz)
    return FrozenDatetime


@pytest.fixture
def desk(schema_database, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(daily_work, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    with sessions() as db:
        db.add_all([User(id=key, name=key.title(), email=f"{key}@example.test", role=role, active=True)
                    for key, role in (("admin", Role.admin), ("manager", Role.manager), ("asha", Role.staff), ("ravi", Role.staff))])
        db.add(Client(id="client", name="Mr Contact", country="UAE"))
        db.flush()
        db.add(Company(id="company", client_id="client", name="Gulf Builders", country="UAE"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add(Order(id="order", ref="ORD-1", company_id="company", designation_id="designation", openings=40, country="Qatar"))
        db.commit()
    clock = {"now": TUESDAY}
    monkeypatch.setattr(daily_router, "datetime", frozen(clock))
    monkeypatch.setattr(applications_router, "datetime", frozen(clock))
    monkeypatch.setattr(data_router, "datetime", frozen(clock))
    monkeypatch.setattr(applications_router, "get_settings", lambda: SimpleNamespace(storage_dir="unused"))
    monkeypatch.setattr(workflow_router, "get_settings", lambda: SimpleNamespace(agency_timezone=TZ))
    actor = SimpleNamespace(id="asha", role=Role.staff)
    app = FastAPI()
    app.include_router(applications_router.router)
    app.include_router(daily_router.router)
    app.include_router(workflow_router.router)
    app.include_router(data_router.router)

    def database():
        with sessions() as db:
            yield db

    # require_role(...) builds on current_user, so one override serves every endpoint's role check.
    app.dependency_overrides[daily_router.get_db] = database
    app.dependency_overrides[daily_router.current_user] = lambda: actor
    env = SimpleNamespace(sessions=sessions, clock=clock, actor=actor, app=app)
    with TestClient(app) as client:
        env.client = client
        yield env


def as_user(env, user_id):
    env.actor.id = user_id
    env.actor.role = Role.staff if user_id in {"asha", "ravi"} else Role.admin if user_id == "admin" else Role.manager


def mapped_candidates(env, count, *, owner="asha", start=0):
    """Candidates mapped to the order through the real endpoint; task ages are spread so 'waiting longest first' is testable."""
    ids = []
    with env.sessions() as db:
        for n in range(start, start + count):
            db.add(Candidate(id=f"c{n}", ref=f"CND-{n:03}", name=f"Candidate {n}", phone=f"90000{n:05}", source=Source.walk_in, owner_id=owner))
        db.commit()
    as_user(env, owner)
    for n in range(start, start + count):
        response = env.client.post("/api/applications", json={"candidate_id": f"c{n}", "order_id": "order"})
        assert response.status_code == 201, response.text
        ids.append((f"c{n}", response.json()["id"]))
    with env.sessions() as db:
        for n, (_candidate, app_id) in zip(range(start, start + count), ids):
            db.execute(text("UPDATE workflow_tasks SET created_at = :at WHERE application_id = :a"),
                       {"at": TUESDAY - timedelta(days=30 - n), "a": app_id})
        db.commit()
    return dict(ids)


def owned_candidates(env, count, *, owner="asha", start=100, **fields):
    """Candidates the recruiter owns that no order has been matched to; ages are spread so 'newest first' is testable."""
    ids = []
    with env.sessions() as db:
        for n in range(start, start + count):
            db.add(Candidate(id=f"u{n}", ref=f"CND-U{n}", name=f"Owned {n}", phone=f"91000{n:05}", source=Source.walk_in, owner_id=owner,
                             designations=["Mason"], created_at=TUESDAY - timedelta(days=200 - n), **fields))
            ids.append(f"u{n}")
        db.commit()
    return ids


def candidate_with_status(env, key, status, *, owner="asha"):
    with env.sessions() as db:
        db.add(Candidate(id=key, ref=f"CND-{key}", name=f"Candidate {key}", phone="9200000000", source=Source.walk_in, owner_id=owner,
                         status=status, needs_review=status == CandidateStatus.needs_review))
        db.commit()


def finish(env, app_id, stage=1):
    """Complete any of the first three stages through the real endpoints."""
    values = {1: {"result": "passed"}, 2: {"forwarded": "yes"},
              3: {"scheduled": "yes", "date": "2026-11-10", "time": "10:00", "mode": "online"}}[stage]
    values["feedback"] = f"Stage {stage} reviewed with candidate."
    version = env.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]
    saved = env.client.put(f"/api/applications/{app_id}/draft", json={"data": values, "lock_version": version})
    assert saved.status_code == 200, saved.text
    done = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert done.status_code == 200, done.text


def record_call(env, candidate_id, interested="yes", called=True):
    return env.client.post(f"/api/candidates/{candidate_id}/call", json={"called": called, "interested": interested})


def listing(env, user_id, path="/api/daily-work"):
    as_user(env, user_id)
    response = env.client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body):
    return [item["candidateId"] for item in body["items"]]


def follow(env, user_id, path="/api/follow-up"):
    return listing(env, user_id, path)


def set_application(env, app_id, *, stage=None, status=None, reason=None, candidate_status=None):
    """Move a placement the way the stage rules would (the rules themselves are tested elsewhere)."""
    with env.sessions() as db:
        if stage is not None:
            db.execute(text("UPDATE crm_applications SET current_stage = :s WHERE id = :a"), {"s": stage, "a": app_id})
        if status is not None:
            db.execute(text("UPDATE crm_applications SET status = :st, closure_reason = :r WHERE id = :a"), {"st": status, "r": reason, "a": app_id})
        if candidate_status is not None:
            db.execute(text("UPDATE crm_candidates SET status = :st WHERE id = (SELECT candidate_id FROM crm_applications WHERE id = :a)"),
                       {"st": candidate_status, "a": app_id})
        db.commit()


# ------------------------------------------------------------------ one flat list: no cap, no slots, no deadlines

def test_every_workable_candidate_is_listed_with_no_daily_cap_and_no_slots(desk):
    owned_candidates(desk, 12)
    body = listing(desk, "asha")
    assert len(body["items"]) == 12  # the old rule stopped at five a day
    assert body["summary"] == {"total": 12, "toWork": 12, "doneToday": 0}
    assert body["agencyDate"] == "2026-11-03" and body["truncated"] is False and "staff" not in body
    # Nothing of the quota/slot/deadline model is left in the answer.
    for key in ("dailyQuota", "morningQuota", "eveningQuota", "goals", "teamGoals", "checkpoints", "shortfall", "offToday", "goalsMode"):
        assert key not in body, key
    for key in ("session", "slotNo", "dueAt", "workDate", "postponed", "daysLate", "overdueAt", "allocatedAt", "startedAt"):
        assert all(key not in item for item in body["items"]), key
    assert {item["status"] for item in body["items"]} == {"open"}


def test_the_list_is_the_same_every_day_and_every_read_it_never_allocates_or_writes(desk):
    owned_candidates(desk, 4)
    first = listing(desk, "asha")
    desk.clock["now"] = WEDNESDAY
    second = listing(desk, "asha")
    assert ids(first) == ids(second) and second["agencyDate"] == "2026-11-04"  # no "carried over", no new day's quota
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DailyWorkItem)) == 0


def test_waiting_candidates_first_longest_waiting_first_then_the_rest_newest_first(desk):
    mapped_candidates(desk, 2)  # stage tasks waiting: c0 for 30 days, c1 for 29
    owned_candidates(desk, 3)
    body = listing(desk, "asha")
    assert ids(body) == ["c0", "c1", "u102", "u101", "u100"]
    assert [(i["kind"], i["waiting"]) for i in body["items"]] == [("stage", True), ("stage", True), ("call", False), ("call", False), ("call", False)]
    first = body["items"][0]
    assert (first["client"], first["country"], first["candidate"], first["position"], first["stage"]) == ("Gulf Builders", "Qatar", "Candidate 0", "Welder", 1)
    assert first["referral"]["label"] == "Walk-in" and first["status"] == "open" and first["remark"] is None
    assert first["candidateRef"] == "CND-000" and "candidateCareId" in first
    assert ids(listing(desk, "asha")) == ids(body)  # stable


def test_only_my_own_workable_candidates_are_listed_needs_review_included(desk):
    candidate_with_status(desk, "n", CandidateStatus.new)
    candidate_with_status(desk, "p", CandidateStatus.in_process)
    candidate_with_status(desk, "r", CandidateStatus.needs_review)  # flagged by intake: still the recruiter's to work
    candidate_with_status(desk, "d", CandidateStatus.deployed)
    candidate_with_status(desk, "x", CandidateStatus.not_interested)
    candidate_with_status(desk, "theirs", CandidateStatus.new, owner="ravi")
    candidate_with_status(desk, "nobody", CandidateStatus.new, owner=None)
    mine = listing(desk, "asha")
    assert sorted(ids(mine)) == ["n", "p", "r"]
    assert mine["summary"]["toWork"] == 3
    assert ids(listing(desk, "ravi")) == ["theirs"]


# ------------------------------------------------------------------ done today

def test_finishing_a_stage_through_the_real_workflow_moves_the_candidate_to_done_today(desk):
    apps = mapped_candidates(desk, 3)
    owned_candidates(desk, 1)
    as_user(desk, "asha")
    finish(desk, apps["c1"], 1)
    body = listing(desk, "asha")
    assert ids(body) == ["c0", "c2", "u100", "c1"]  # the finished one sits at the bottom
    assert [i["status"] for i in body["items"]] == ["open", "open", "open", "done"]
    assert body["items"][-1]["completedAt"] is not None and body["items"][-1]["stage"] == 2  # now waiting on stage 2
    assert body["summary"] == {"total": 4, "toWork": 3, "doneToday": 1}
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DailyWorkItem)) == 0  # no item rows are written any more

    finish(desk, apps["c0"], 1)
    body = listing(desk, "asha")
    assert ids(body)[:2] == ["c2", "u100"] and set(ids(body)[2:]) == {"c0", "c1"}  # both finished candidates are at the bottom
    assert body["summary"] == {"total": 4, "toWork": 2, "doneToday": 2}
    assert [i["completedAt"] for i in body["items"][2:]] == sorted((i["completedAt"] for i in body["items"][2:]), reverse=True)  # latest first

    # Tomorrow nothing is "done" any more and the finished candidates, past prescreening, are only in Follow-up: today's list is
    # just what still has prescreening to do, with no carry-over wording.
    desk.clock["now"] = WEDNESDAY
    tomorrow = listing(desk, "asha")
    assert sorted(ids(tomorrow)) == ["c2", "u100"] and tomorrow["summary"] == {"total": 2, "toWork": 2, "doneToday": 0}
    assert sorted(ids(follow(desk, "asha"))) == ["c0", "c1"]


def test_a_stage_finished_by_a_manager_on_the_recruiters_behalf_counts_for_the_owner(desk):
    apps = mapped_candidates(desk, 1)
    as_user(desk, "manager")
    finish(desk, apps["c0"], 1)
    body = listing(desk, "asha")
    assert [i["status"] for i in body["items"]] == ["done"] and body["summary"]["doneToday"] == 1


def test_a_reassigned_candidate_is_open_for_the_new_recruiter_and_gone_for_the_old_one(desk):
    apps = mapped_candidates(desk, 1)
    as_user(desk, "asha")
    finish(desk, apps["c0"], 1)
    assert listing(desk, "asha")["summary"]["doneToday"] == 1
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET owner_id = 'ravi' WHERE id = 'c0'"))
        db.commit()
    assert listing(desk, "asha")["items"] == [] and follow(desk, "asha")["items"] == []
    # Asha's prescreening is not Ravi's work, and the candidate is already past prescreening: it is Ravi's follow-up.
    assert listing(desk, "ravi")["items"] == [] and ids(follow(desk, "ravi")) == ["c0"]


def test_a_call_with_an_answered_outcome_today_is_done_an_unanswered_one_is_not(desk):
    owned_candidates(desk, 3)
    as_user(desk, "asha")
    desk.clock["now"] = TUESDAY + timedelta(hours=2)
    assert record_call(desk, "u100", interested="unreachable", called=False).status_code == 200
    body = listing(desk, "asha")
    assert {i["status"] for i in body["items"]} == {"open"}  # an unreachable candidate stays to be worked
    assert record_call(desk, "u101", interested="yes").status_code == 200
    body = listing(desk, "asha")
    assert ids(body) == ["u102", "u100", "u101"] and [i["status"] for i in body["items"]] == ["open", "open", "done"]
    assert body["summary"] == {"total": 3, "toWork": 2, "doneToday": 1}
    # Called earlier than today: not today's work done.
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_called = true, call_interested = 'yes', call_recorded_at = :at WHERE id = 'u102'"),
                   {"at": TUESDAY - timedelta(days=2)})
        db.commit()
    assert listing(desk, "asha")["summary"] == {"total": 3, "toWork": 2, "doneToday": 1}
    desk.clock["now"] = WEDNESDAY  # and tomorrow the call made today no longer counts
    assert listing(desk, "asha")["summary"] == {"total": 3, "toWork": 3, "doneToday": 0}


def test_a_candidate_matched_to_an_order_after_the_call_opens_the_stage_and_keeps_its_place(desk):
    owned_candidates(desk, 1)
    as_user(desk, "asha")
    assert listing(desk, "asha")["items"][0]["applicationId"] is None  # not matched: Open goes to the application form
    app_id = desk.client.post("/api/applications", json={"candidate_id": "u100", "order_id": "order"}).json()["id"]
    row = listing(desk, "asha")["items"][0]
    assert (row["applicationId"], row["stage"], row["client"], row["kind"], row["waiting"]) == (app_id, 1, "Gulf Builders", "stage", True)


# ------------------------------------------------------------------ the supervisors' view

def test_staff_see_only_their_own_list_and_supervisors_see_every_recruiter_with_counts(desk):
    mapped_candidates(desk, 7)
    mapped_candidates(desk, 3, owner="ravi", start=20)
    as_user(desk, "asha")
    assert desk.client.get("/api/daily-work?staff=ravi").status_code == 403
    assert desk.client.get("/api/daily-work?staff=asha").status_code == 200
    assert desk.client.get("/api/daily-work?staff=all").status_code == 200

    as_user(desk, "manager")
    team = desk.client.get("/api/daily-work").json()
    assert {s["userId"]: s["summary"] for s in team["staff"]} == {
        "asha": {"total": 7, "toWork": 7, "doneToday": 0}, "ravi": {"total": 3, "toWork": 3, "doneToday": 0}}
    assert len(team["items"]) == 10 and team["summary"] == {"total": 10, "toWork": 10, "doneToday": 0} and team["staffCount"] == 2
    assert {item["ownerId"] for item in team["items"]} == {"asha", "ravi"}
    only = listing(desk, "admin", "/api/daily-work?staff=ravi")
    assert {i["ownerId"] for i in only["items"]} == {"ravi"} and only["summary"]["total"] == 3 and only["staffCount"] == 1
    assert {s["userId"] for s in only["staff"]} == {"asha", "ravi"}  # the board always shows everyone
    assert listing(desk, "admin", "/api/daily-work?staff=all")["summary"]["total"] == 10
    assert desk.client.get("/api/daily-work?staff=nobody").status_code == 404
    assert desk.client.get("/api/daily-work?staff=manager").status_code == 404  # only recruiters have a list


def test_the_per_recruiter_summary_counts_to_work_and_done_today_separately(desk):
    apps = mapped_candidates(desk, 4)
    mapped_candidates(desk, 2, owner="ravi", start=40)
    as_user(desk, "asha")
    finish(desk, apps["c0"], 1)
    finish(desk, apps["c1"], 1)
    team = listing(desk, "manager")
    by = {s["userId"]: s["summary"] for s in team["staff"]}
    assert by["asha"] == {"total": 4, "toWork": 2, "doneToday": 2} and by["ravi"] == {"total": 2, "toWork": 2, "doneToday": 0}
    assert team["summary"] == {"total": 6, "toWork": 4, "doneToday": 2}
    # Within the supervisors' list the open work of everybody comes first, then everybody's finished candidates.
    assert [i["status"] for i in team["items"]] == ["open"] * 4 + ["done"] * 2


def test_a_recruiter_with_nothing_to_work_shows_zero_and_an_inactive_one_only_while_they_still_own_candidates(desk):
    with desk.sessions() as db:
        db.add_all([User(id="gone", name="Gone", email="gone@example.test", role=Role.staff, active=False),
                    User(id="gone2", name="Gone Two", email="gone2@example.test", role=Role.staff, active=False)])
        db.commit()
    candidate_with_status(desk, "left", CandidateStatus.new, owner="gone")
    team = listing(desk, "manager")
    by = {s["userId"]: s for s in team["staff"]}
    assert by["asha"]["summary"] == {"total": 0, "toWork": 0, "doneToday": 0} and by["asha"]["active"] is True
    assert by["gone"]["active"] is False and by["gone"]["summary"]["toWork"] == 1 and "gone2" not in by
    assert ids(team) == ["left"]


def test_a_remark_added_through_the_stage_task_or_the_initial_call_task_shows_in_the_row(desk):
    mapped_candidates(desk, 1)
    owned_candidates(desk, 1)
    with desk.sessions() as db:
        db.add(Task(id="call-task", kind=TaskKind.initial_call, title="Call", candidate_id="u100", owner_id="asha"))
        db.commit()
    body = listing(desk, "asha")
    stage_row, call_row = body["items"]
    assert stage_row["taskId"] and call_row["taskId"] == "call-task"
    with desk.sessions() as db:
        db.add(Remark(task_id=stage_row["taskId"], body="Called; passport tomorrow", author_id="asha"))
        db.add(Remark(task_id="call-task", body="Asked to ring back", author_id="asha"))
        db.commit()
    stage_row, call_row = listing(desk, "asha")["items"]
    assert stage_row["remark"]["body"] == "Called; passport tomorrow" and stage_row["remark"]["count"] == 1 and stage_row["remark"]["by"] == "Asha"
    assert call_row["remark"]["body"] == "Asked to ring back"


def test_an_unmatched_candidates_row_falls_back_on_the_form_preferences_and_says_so(desk):
    with desk.sessions() as db:
        base = dict(source=Source.walk_in, owner_id="asha")
        db.add_all([
            Candidate(id="p1", ref="CND-P1", name="Prefers", phone="9300000001", designations=["Welder", "Fitter"], skills=["Rigging"],
                      country_preference=" Qatar ", current_city="Kochi", created_at=TUESDAY - timedelta(days=50), **base),
            Candidate(id="p2", ref="CND-P2", name="Skills only", phone="9300000002", designations=[], skills=["Rigging", "Welding"],
                      current_city="Dubai, UAE", created_at=TUESDAY - timedelta(days=49), **base),
            Candidate(id="p3", ref="CND-P3", name="Blank form", phone="9300000003", designations=[], skills=[], country_preference="  ",
                      created_at=TUESDAY - timedelta(days=48), **base),
        ])
        db.commit()
    by_id = {r["candidateId"]: r for r in listing(desk, "asha")["items"]}
    a, b, c = by_id["p1"], by_id["p2"], by_id["p3"]
    assert (a["country"], a["countrySource"], a["countryIsPreferred"], a["locationHint"]) == ("Qatar", "preference", True, None)
    assert (a["position"], a["positionSource"], a["positionIsPreferred"]) == ("Welder", "preference", True)
    assert (a["client"], a["clientContact"], a["orderRef"]) == (None, None, None)  # a client exists only once matched
    assert (b["country"], b["countrySource"], b["countryIsPreferred"]) == (None, None, False)
    assert b["locationHint"] == "Dubai, UAE"  # the screen may still recognise a country in the current location
    assert (b["position"], b["positionSource"], b["positionIsPreferred"]) == ("Rigging", "skill", True)
    assert (c["country"], c["countrySource"], c["countryIsPreferred"], c["locationHint"]) == (None, None, False, None)
    assert (c["position"], c["positionSource"], c["positionIsPreferred"]) == (None, None, False)


def test_a_matched_candidate_keeps_the_orders_values_whatever_the_form_prefers(desk):
    mapped_candidates(desk, 1)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET country_preference = 'Oman', designations = '[\"Mason\"]'::jsonb, care_id = 'CI-26-27-001' WHERE id = 'c0'"))
        db.commit()
    row = listing(desk, "asha")["items"][0]
    assert (row["kind"], row["client"], row["country"], row["position"]) == ("stage", "Gulf Builders", "Qatar", "Welder")
    assert (row["countrySource"], row["countryIsPreferred"], row["positionSource"], row["positionIsPreferred"], row["locationHint"]) == ("order", False, "order", False, None)
    assert (row["candidateCareId"], row["candidateRef"]) == ("CI-26-27-001", "CND-000")  # Care India ID primary, CRM reference second


def test_a_row_cap_only_trims_the_rows_the_counts_stay_exact(desk, monkeypatch):
    owned_candidates(desk, 6)
    monkeypatch.setattr(daily_work, "MAX_ROWS", 4)
    body = listing(desk, "asha")
    assert len(body["items"]) == 4 and body["truncated"] is True
    assert body["summary"] == {"total": 6, "toWork": 6, "doneToday": 0}


# ------------------------------------------------------------------ goals (kept as data for the performance report, unused by the screen)

def test_goal_settings_still_work_but_no_longer_shape_the_daily_list(desk):
    owned_candidates(desk, 8)
    as_user(desk, "manager")
    table = desk.client.get("/api/daily-goals").json()
    assert table["default"] == {"candidates": 5, "morning": 3, "evening": 2, "forwardedToClient": 2, "stageMoves": 5}  # seeded rows
    assert desk.client.put("/api/daily-goals/default", json={"candidates": 4, "morning": 1}).status_code == 403  # admin only
    as_user(desk, "admin")
    assert desk.client.put("/api/daily-goals/default", json={"candidates": 2, "morning": 3}).status_code == 422
    assert desk.client.put("/api/daily-goals/default", json={}).status_code == 422
    saved = desk.client.put("/api/daily-goals/default", json={"candidates": 2, "morning": 1, "stageMoves": 9})
    assert saved.status_code == 200 and saved.json() == {"candidates": 2, "morning": 1, "evening": 1, "forwardedToClient": 2, "stageMoves": 9}
    own = desk.client.put("/api/daily-goals/staff/ravi", json={"stageMoves": 3}).json()
    assert own["overrides"] == ["stage_moves"]
    assert desk.client.put("/api/daily-goals/staff/admin", json={"stageMoves": 1}).status_code == 404
    assert desk.client.delete("/api/daily-goals/staff/ravi").json()["overrides"] == []
    assert len(listing(desk, "asha")["items"]) == 8  # a goal of 2 a day does not cap anything


# ------------------------------------------------------------------ the old items

def test_the_worker_pass_retires_open_items_of_the_old_quota_keeps_history_and_sends_no_alerts(desk):
    owned_candidates(desk, 2)
    with desk.sessions() as db:
        yesterday = TUESDAY - timedelta(days=1)
        for item_id, status, candidate in (("open-late", "open", "u100"), ("open-now", "open", "u101"), ("finished", "completed", "u100")):
            db.add(DailyWorkItem(id=item_id, owner_id="asha", work_date=yesterday.date(), kind="call", candidate_id=candidate, slot_no=1 if item_id == "finished" else 2 if item_id == "open-now" else 3,
                                 session="morning", due_at=yesterday, status=status, completed_at=yesterday if status == "completed" else None))
        db.commit()
        counts = scan_daily_work(db, ["admin"], now=TUESDAY, tz=TZ)
        db.commit()
        assert counts == {"dailyRetired": 2}
        rows = {r.id: (r.status, r.release_reason) for r in db.scalars(select(DailyWorkItem))}
        assert rows == {"open-late": ("released", "quota_removed"), "open-now": ("released", "quota_removed"), "finished": ("completed", None)}
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.kind == "daily_work")) == 0
        assert scan_daily_work(db, ["admin"], now=TUESDAY, tz=TZ) == {"dailyRetired": 0}  # idempotent
    assert len(listing(desk, "asha")["items"]) == 2  # the list never depended on those rows


def test_the_item_table_is_announced_to_live_screens():
    from app.services.change_feed import TOPIC_TABLES
    from app.services.change_feed import IGNORED_TABLES
    assert TOPIC_TABLES["workflow_daily_work_items"] == ("tasks",) and TOPIC_TABLES["workflow_daily_goals"] == ("tasks",)
    assert "workflow_daily_goal_progress" in IGNORED_TABLES


# ------------------------------------------------------------------ cost

def statements_of(env, user_id, path="/api/daily-work"):
    as_user(env, user_id)
    seen = []
    record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))  # noqa: E731
    engine = env.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = env.client.get(path)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    return seen, response.json()


def test_the_screen_is_a_read_only_handful_of_statements_whatever_the_number_of_recruiters(desk):
    with desk.sessions() as db:
        staff = [f"s{n:02}" for n in range(20)]
        db.add_all([User(id=uid, name=f"Staff {uid}", email=f"{uid}@example.test", role=Role.staff, active=True) for uid in staff])
        db.flush()
        db.add_all([Candidate(id=f"{uid}-c{n:02}", ref=f"CND-{uid}-{n:02}", name=f"Cand {uid} {n}", phone=f"93{i:03}{n:05}", source=Source.walk_in,
                              owner_id=uid, designations=["Welder"], created_at=TUESDAY - timedelta(days=100 - n))
                    for i, uid in enumerate(staff) for n in range(30)])
        db.commit()
    mapped_candidates(desk, 3)
    seen, mine = statements_of(desk, "asha")
    assert len(mine["items"]) == 3 and len(seen) <= 6, [sql[:70] for sql in seen]
    seen, team = statements_of(desk, "manager")
    assert len(team["items"]) == 603 and len(team["staff"]) == 22 and team["summary"]["toWork"] == 603 and team["truncated"] is False
    assert len(seen) <= 7, [sql[:70] for sql in seen]  # counts, roster, rows, placements, open tasks, remarks: not one per recruiter
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) or "pg_advisory" in sql for sql in seen)


# ------------------------------------------------------------------ the tables are untouched (no migration)

def test_migration_adds_three_tables_seeds_the_agency_goals_and_downgrades_cleanly(schema_database):
    engine = schema_database
    _migrate(engine, "0019_job_categories")
    before = set(inspect(engine).get_table_names())
    assert "workflow_daily_work_items" not in before
    _migrate(engine, "head")
    added = {"workflow_daily_work_items", "workflow_daily_goals", "workflow_daily_goal_progress"}
    assert added <= set(inspect(engine).get_table_names()) - before  # later migrations may add their own tables too
    with engine.connect() as connection:
        seeded = connection.execute(text("SELECT metric, target, morning_target FROM workflow_daily_goals WHERE user_id IS NULL ORDER BY metric")).all()
    assert seeded == [("candidates", 5, 3), ("forwarded_to_client", 2, None), ("stage_moves", 5, None)]
    with engine.begin() as connection:
        connection.execute(text("UPDATE workflow_daily_goals SET target = 7, morning_target = 4 WHERE metric = 'candidates'"))
        # Pretend the revision was lost after the tables were made: running 0020 again must keep the edit.
        connection.execute(text("UPDATE alembic_version SET version_num = '0019_job_categories'"))
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*), max(target) FROM workflow_daily_goals WHERE metric = 'candidates'")).one() == (1, 7)
    with engine.begin() as connection:  # the database itself refuses nonsense
        for bad in ("INSERT INTO workflow_daily_goals (id, metric, target) VALUES ('x', 'bogus', 1)",
                    "INSERT INTO workflow_daily_goals (id, metric, target, morning_target) VALUES ('y', 'candidates', 3, 4)",
                    "INSERT INTO workflow_daily_goals (id, metric, target) VALUES ('z', 'candidates', 3)"):
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(text(bad))
    _migrate(engine, "0019_job_categories", downgrade=True)
    assert not added & set(inspect(engine).get_table_names())  # the three tables are gone, nothing else of ours is left
    _migrate(engine, "head")
    assert added <= set(inspect(engine).get_table_names())


def test_upgrading_a_database_that_already_has_the_first_daily_work_schema_keeps_its_rows(schema_database):
    """The committed 0020 schema (no kind column, task_id required) upgrades in place to the current head."""
    engine = schema_database
    _migrate(engine, "0020_daily_work_quota")
    cols = {c["name"]: c for c in inspect(engine).get_columns("workflow_daily_work_items")}
    assert "kind" not in cols and cols["task_id"]["nullable"] is False
    with engine.begin() as c:
        c.execute(text("INSERT INTO auth_users (id,name,email,role,active,receives_auto_assign,on_leave) VALUES ('u','U','u@example.test','staff',true,false,false)"))
        c.execute(text("INSERT INTO crm_candidates (id,ref,name,phone,source,status,needs_review) VALUES ('c','CND-1','Old','9000000001','walk_in','new',false)"))
        c.execute(text("INSERT INTO workflow_tasks (id,kind,title,candidate_id,owner_id,escalated) VALUES ('t','stage','Stage','c','u',false)"))
        c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,task_id,candidate_id,slot_no,session,due_at,status) "
                       "VALUES ('i','u','2026-11-02','t','c',1,'morning','2026-11-02 05:30+00','open')"))
    _migrate(engine, "head")
    cols = {c["name"]: c for c in inspect(engine).get_columns("workflow_daily_work_items")}
    assert cols["kind"]["nullable"] is False and cols["task_id"]["nullable"] is True
    with engine.connect() as c:
        assert c.execute(text("SELECT kind, task_id, status FROM workflow_daily_work_items WHERE id = 'i'")).one() == ("stage", "t", "open")
        assert c.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_daily_work_one_open_per_candidate'")) == 0
        assert c.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_daily_work_candidate_open'")) == 1
    with engine.begin() as c:  # the database now allows an owned-candidate item, and still refuses a stage item with no task
        c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,kind,candidate_id,slot_no,session,due_at) "
                       "VALUES ('j','u','2026-11-02','call','c',2,'morning','2026-11-02 06:30+00')"))
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,kind,candidate_id,slot_no,session,due_at) "
                           "VALUES ('k','u','2026-11-02','stage','c',3,'morning','2026-11-02 06:30+00')"))
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(text("INSERT INTO workflow_daily_work_items (id,owner_id,work_date,kind,candidate_id,slot_no,session,due_at) "
                           "VALUES ('m','u','2026-11-02','bogus','c',4,'morning','2026-11-02 06:30+00')"))

    # A repeat is harmless; the downgrade refuses while owned-candidate items exist, then restores the old shape.
    with engine.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = '0022_form_options'"))
    _migrate(engine, "head")
    with pytest.raises(Exception, match="Cannot downgrade 0023"):
        _migrate(engine, "0022_form_options", downgrade=True)
    with engine.begin() as c:
        c.execute(text("DELETE FROM workflow_daily_work_items WHERE kind = 'call'"))
    _migrate(engine, "0022_form_options", downgrade=True)
    cols = {c["name"]: c for c in inspect(engine).get_columns("workflow_daily_work_items")}
    assert "kind" not in cols and cols["task_id"]["nullable"] is False
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM workflow_daily_work_items WHERE id = 'i'")) == 1  # the original row survived both ways
    _migrate(engine, "head")  # up again
    with engine.connect() as c:
        assert c.execute(text("SELECT kind FROM workflow_daily_work_items WHERE id = 'i'")).scalar() == "stage"


# ------------------------------------------------------------------ today's candidates -> follow-up -> travelled

def test_a_candidate_moves_from_todays_candidates_to_follow_up_when_prescreening_is_finished(desk):
    apps = mapped_candidates(desk, 2)
    as_user(desk, "asha")
    assert ids(listing(desk, "asha")) == ["c0", "c1"] and follow(desk, "asha")["items"] == []  # at stage 1: prescreening is today's task
    finish(desk, apps["c0"], 1)
    today, later = listing(desk, "asha"), follow(desk, "asha")
    assert [(i["candidateId"], i["status"]) for i in today["items"]] == [("c1", "open"), ("c0", "done")]  # ticked, not in the to-work part
    assert today["summary"] == {"total": 2, "toWork": 1, "doneToday": 1}
    row = later["items"][0]
    assert ids(later) == ["c0"] and row["stage"] == 2 and row["applicationId"] == apps["c0"] and later["summary"] == {"total": 1}
    assert (row["client"], row["country"], row["position"], row["candidateRef"]) == ("Gulf Builders", "Qatar", "Welder", "CND-000")
    # The next day only Follow-up has it.
    desk.clock["now"] = WEDNESDAY
    assert ids(listing(desk, "asha")) == ["c1"] and ids(follow(desk, "asha")) == ["c0"]


def test_follow_up_is_active_placements_at_stages_two_to_eleven_only(desk):
    apps = mapped_candidates(desk, 5)
    for candidate, stage in (("c1", 2), ("c2", 6), ("c3", 10)):
        set_application(desk, apps[candidate], stage=stage)
    set_application(desk, apps["c4"], stage=11)  # an active placement at 11 (ticket booking pending) is not yet travelled: still Follow-up
    assert sorted(ids(follow(desk, "asha"))) == ["c1", "c2", "c3", "c4"]
    assert ids(listing(desk, "asha")) == ["c0"]  # only the one still at prescreening


@pytest.mark.parametrize("reason", ["interview_rejected", "offer_declined", "offer_not_received", "medical_unfit", "not_interested", "resume_not_received", "unmapped"])
def test_a_negative_outcome_takes_the_candidate_out_of_follow_up(desk, reason):
    apps = mapped_candidates(desk, 2)
    for candidate in ("c0", "c1"):
        set_application(desk, apps[candidate], stage=4)
    assert sorted(ids(follow(desk, "asha"))) == ["c0", "c1"]
    # The application closes; the candidate goes back to being plain work (status new) or is closed as not interested.
    set_application(desk, apps["c0"], status="closed", reason=reason, candidate_status="not_interested" if reason == "not_interested" else "new")
    assert ids(follow(desk, "asha")) == ["c1"]
    assert follow(desk, "asha")["summary"] == {"total": 1}
    assert follow(desk, "manager")["summary"] == {"total": 1} and follow(desk, "manager")["staff"][0]["summary"] == {"total": 1}


def test_deployment_moves_the_candidate_from_follow_up_to_travelled(desk):
    from app.api.routers import travelled as travelled_router
    apps = mapped_candidates(desk, 1)
    set_application(desk, apps["c0"], stage=11)
    assert ids(follow(desk, "asha")) == ["c0"]  # ticket booking pending: still Follow-up, not yet Travelled
    with desk.sessions() as db:
        assert travelled_router.read_rows(db, db.get(User, "asha")) == []
    set_application(desk, apps["c0"], status="deployed", candidate_status="deployed")  # stage 11 completed
    assert follow(desk, "asha")["items"] == [] and listing(desk, "asha")["items"] == []
    with desk.sessions() as db:
        assert [r["candidateId"] for r in travelled_router.read_rows(db, db.get(User, "asha"))] == ["c0"]
        assert travelled_router.read_rows(db, db.get(User, "ravi")) == []  # a recruiter sees their own travelled


def test_follow_up_lists_the_stalest_first_with_the_last_remark(desk):
    apps = mapped_candidates(desk, 3)
    with desk.sessions() as db:
        for candidate, days in (("c0", 10), ("c1", 20), ("c2", 15)):
            db.execute(text("UPDATE crm_applications SET current_stage = 3, created_at = :at WHERE id = :a"), {"at": TUESDAY - timedelta(days=days), "a": apps[candidate]})
        db.commit()
    body = follow(desk, "asha")
    assert ids(body) == ["c1", "c2", "c0"]  # oldest last update first
    assert body["items"][0]["lastUpdateAt"] is not None and body["items"][0]["remark"] is None
    task_id = body["items"][0]["taskId"]
    assert task_id
    with desk.sessions() as db:
        db.add(Remark(task_id=task_id, body="Interview booked for Thursday", author_id="asha", created_at=TUESDAY))
        db.commit()
    body = follow(desk, "asha")
    assert ids(body) == ["c2", "c0", "c1"]  # a remark is an update: c1 is no longer stale
    assert body["items"][-1]["remark"]["body"] == "Interview booked for Thursday" and body["items"][-1]["remark"]["by"] == "Asha"


def test_follow_up_is_scoped_like_the_daily_list(desk):
    mine = mapped_candidates(desk, 2)
    theirs = mapped_candidates(desk, 1, owner="ravi", start=20)
    for app in (*mine.values(), *theirs.values()):
        set_application(desk, app, stage=5)
    as_user(desk, "asha")
    assert desk.client.get("/api/follow-up?staff=ravi").status_code == 403
    assert desk.client.get("/api/follow-up?staff=asha").status_code == 200
    assert sorted(ids(follow(desk, "asha"))) == ["c0", "c1"] and ids(follow(desk, "ravi")) == ["c20"]
    team = follow(desk, "manager")
    assert team["summary"] == {"total": 3} and team["staffCount"] == 2 and {i["ownerId"] for i in team["items"]} == {"asha", "ravi"}
    assert {s["userId"]: s["summary"]["total"] for s in team["staff"]} == {"asha": 2, "ravi": 1}
    only = follow(desk, "admin", "/api/follow-up?staff=ravi")
    assert ids(only) == ["c20"] and only["summary"] == {"total": 1} and {s["userId"] for s in only["staff"]} == {"asha", "ravi"}
    assert desk.client.get("/api/follow-up?staff=nobody").status_code == 404
    assert follow(desk, "admin", "/api/follow-up?staff=all")["summary"]["total"] == 3


def test_both_lists_are_read_only_and_announced_by_the_live_feed_tables_they_depend_on(desk):
    from app.services.change_feed import TOPIC_TABLES
    mapped_candidates(desk, 1)
    seen, _ = statements_of(desk, "asha", "/api/follow-up")
    assert len(seen) <= 5 and not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in seen)
    # A stage finished or an application closed writes these tables: the topics the screens subscribe to ('tasks', 'candidates', 'applications').
    assert TOPIC_TABLES["crm_applications"] == ("applications",) and TOPIC_TABLES["crm_application_stages"] == ("applications",)
    assert TOPIC_TABLES["crm_candidates"] == ("candidates",) and TOPIC_TABLES["workflow_tasks"] == ("tasks",)
