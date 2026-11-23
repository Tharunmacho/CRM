"""Reports, end to end: a recruiter acts through the real endpoints and the Task / Lead sheet report shows it AT ONCE, for the right period.

Owner's rules, each asserted here:
  R1  every action (stage completed, placement rejected, 'not interested' / 'rejected' at the initial call, a lead call) is in the report in the very
      next request: no cache stands between the write and the figure (the report endpoint is uncached; its live topics are tested in test_change_feed);
  R2  'Today' (any period) counts the work DONE in it, by the time of the event itself, in the agency timezone (00:20 IST is today, 23:50 IST the day before);
      an old closure that a bulk edit touches today is NOT today's work;
  R4  figures of 'now' (waiting at a stage) do not move with the period, figures of work done do.

Runs on the explicitly guarded disposable PostgreSQL DB with the same desk fixture as test_daily_work (time frozen at Tuesday 2026-11-03 08:30 IST).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

from app.api.routers import daily_work_report as report_router
from app.api.routers import leads as leads_router
from app.db.models import (
    Application, ApplicationStage, ApplicationStatus, AuditEntry, Candidate, Client, ClosureReason, Company, Designation, Lead, Order, Source,
)
from test_daily_work import (  # noqa: F401  (desk is a fixture)
    TUESDAY, advance, as_user, desk, frozen, mapped_candidates, owned_candidates, set_application, to_stage,
)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture used by desk)

IST = ZoneInfo("Asia/Kolkata")
TODAY, YESTERDAY = "2026-11-03", "2026-11-02"
REPORT = "/api/reports/daily-work"


@pytest.fixture
def reports(desk, monkeypatch):
    """The desk plus the report router, on the same frozen clock."""
    monkeypatch.setattr(report_router, "datetime", frozen(desk.clock))
    monkeypatch.setattr(report_router, "get_settings", lambda: type("S", (), {"agency_timezone": "Asia/Kolkata"})())
    desk.app.include_router(report_router.router)
    return desk


def figures(env, day=TODAY, to=None, staff=None):
    """The numbers of the Task report as an admin reads them: (completed, stopped, matrix total, matrix lost, waiting per stage, exit reasons)."""
    previous = env.actor.id
    as_user(env, "admin")
    try:
        query = f"?from={day}&to={to or day}" + (f"&staff={staff}" if staff else "")
        response = env.client.get(REPORT + query)
        assert response.status_code == 200, response.text
        body = response.json()
    finally:
        as_user(env, previous)
    insight = body["taskInsight"]
    done = insight["workDone"]["totals"]
    return {
        "completed": insight["completed"]["value"], "stopped": insight["stopped"]["value"], "total": done["total"], "lost": done["lost"],
        "cells": done["completed"], "lostCells": done["stopped"], "waiting": {row["no"]: row["count"] for row in body["stages"]["totals"]},
        "reasons": {row["label"]: row["count"] for row in insight["exitReasons"]}, "body": body,
    }


def drill(env, metric, day=TODAY):
    previous = env.actor.id
    as_user(env, "admin")
    try:
        response = env.client.get(f"{REPORT}/drilldown?metric={metric}&from={day}&to={day}&page_size=100")
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        as_user(env, previous)


def call(env, candidate, interested, user="asha"):
    as_user(env, user)
    response = env.client.post(f"/api/candidates/{candidate}/call", json={"called": True, "interested": interested})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_action_is_in_todays_report_in_the_next_request(reports):
    apps = mapped_candidates(reports, 3)
    owned = owned_candidates(reports, 4)
    start = figures(reports)
    assert (start["completed"], start["stopped"], start["total"], start["lost"]) == (0, 0, 0, 0)
    assert start["waiting"][1] == 3   # a snapshot of now: three candidates wait at prescreening

    # a stage completed
    as_user(reports, "asha")
    advance(reports, apps["c0"], {"result": "passed"})
    after = figures(reports)
    assert (after["completed"], after["stopped"], after["total"], after["lost"]) == (1, 0, 1, 0)
    assert after["cells"]["1"] == 1
    assert (after["waiting"][1], after["waiting"][2]) == (2, 1)
    assert drill(reports, "done:1")["total"] == 1

    # a placement rejected in the stage window
    advance(reports, apps["c1"], {"result": "failed"})
    after = figures(reports)
    assert (after["completed"], after["stopped"], after["total"], after["lost"]) == (2, 1, 2, 1)
    assert drill(reports, "lost:1")["total"] == 1

    # not interested at the initial call, no job needed
    call(reports, owned[0], "no")
    after = figures(reports)
    assert (after["completed"], after["stopped"], after["total"], after["lost"]) == (3, 2, 3, 2)
    assert after["reasons"]["Not interested at the initial call"] == 1
    assert after["lostCells"]["1"] == 2

    # rejected at the initial call, no job needed
    call(reports, owned[1], "rejected")
    after = figures(reports)
    assert (after["completed"], after["stopped"], after["total"], after["lost"]) == (4, 3, 4, 3)
    assert after["reasons"]["Rejected at the initial call"] == 1
    listed = drill(reports, "lost:1")
    assert listed["total"] == 3 == len(listed["rows"])
    assert {"Candidate " + n for n in ("1",)} <= {row["name"] for row in listed["rows"]}
    assert any("Rejected at the initial call" in row["detail"] for row in listed["rows"])
    assert any("Not interested at the initial call" in row["detail"] for row in listed["rows"])

    # a 'yes' (interested), an 'unreachable' and the same 'no' again change nothing
    call(reports, owned[2], "yes")
    call(reports, owned[3], "unreachable")
    call(reports, owned[0], "no")
    assert figures(reports)["total"] == 4
    # the card numbers are the matrix: one source
    final = figures(reports)
    assert final["completed"] == final["total"] and final["stopped"] == final["lost"]
    assert drill(reports, "done:all")["total"] == final["total"]
    assert drill(reports, "lost:all")["total"] == final["lost"]


def test_a_call_decline_is_credited_to_the_person_who_recorded_it(reports):
    owned = owned_candidates(reports, 2)
    call(reports, owned[0], "no", user="asha")
    ravi = owned_candidates(reports, 1, owner="ravi", start=300)
    call(reports, ravi[0], "rejected", user="ravi")
    assert figures(reports, staff="asha")["lost"] == 1
    assert figures(reports, staff="ravi")["lost"] == 1
    assert figures(reports)["lost"] == 2
    assert drill(reports, "lost:1")["total"] == 2


def test_today_is_what_was_done_today_in_the_agency_zone_and_yesterday_is_not_today(reports):
    apps = mapped_candidates(reports, 4)
    owned = owned_candidates(reports, 3)
    # events by their own timestamps: 23:50 IST on the 2nd (yesterday), 00:20 IST on the 3rd (today), and a week ago
    late_yesterday = datetime(2026, 11, 2, 23, 50, tzinfo=IST).astimezone(timezone.utc)
    early_today = datetime(2026, 11, 3, 0, 20, tzinfo=IST).astimezone(timezone.utc)
    last_week = datetime(2026, 10, 27, 12, 0, tzinfo=IST).astimezone(timezone.utc)
    with reports.sessions() as db:
        for key, at in (("c0", late_yesterday), ("c1", early_today), ("c2", last_week)):
            db.add(ApplicationStage(application_id=apps[key], stage=1, attempt=1, outcome="Passed", completed_by="asha", owner_id="asha",
                                    completed_at=at, data={}))
        for candidate, at in ((owned[0], late_yesterday), (owned[1], early_today), (owned[2], last_week)):
            db.add(AuditEntry(actor_id="asha", action="Recorded initial call", entity_type="candidate", entity_id=candidate, at=at,
                              meta={"called": True, "interested": "no", "on_order": False}))
        db.commit()
    today, yesterday = figures(reports, TODAY), figures(reports, YESTERDAY)
    assert (today["total"], today["lost"]) == (2, 1)       # the stage at 00:20 IST + the decline at 00:20 IST
    assert (yesterday["total"], yesterday["lost"]) == (2, 1)   # the ones at 23:50 IST
    week = figures(reports, "2026-10-26", "2026-11-03")
    assert (week["total"], week["lost"]) == (6, 3)
    assert drill(reports, "done:1", TODAY)["total"] == 2 and drill(reports, "done:1", YESTERDAY)["total"] == 2
    # what is waiting NOW does not depend on the period chosen
    assert today["waiting"] == yesterday["waiting"] == week["waiting"]


def test_a_bulk_edit_does_not_make_an_old_closure_todays_work(reports):
    apps = mapped_candidates(reports, 2)
    as_user(reports, "asha")
    to_stage(reports, apps["c0"], 4)
    advance(reports, apps["c0"], {"result": "rejected"})   # rejected at the interview, today
    assert figures(reports)["lost"] == 1
    # the closure is moved a week back (as an old one), then an import touches updated_at of every row today
    week_ago = TUESDAY - timedelta(days=7)
    with reports.sessions() as db:
        db.execute(text("UPDATE crm_applications SET closed_at = :c, updated_at = :u WHERE id = :a"), {"c": week_ago, "u": TUESDAY, "a": apps["c0"]})
        db.execute(text("UPDATE crm_application_stages SET completed_at = :c WHERE application_id = :a"), {"c": week_ago, "a": apps["c0"]})
        db.commit()
    assert figures(reports)["lost"] == 0                   # not today's work
    as_user(reports, "asha")
    rows = reports.client.get("/api/daily-work?view=rejected").json()["items"]
    assert [row["candidateId"] for row in rows] == ["c0"]
    assert rows[0]["outcome"]["at"].startswith("2026-10-27")   # dated by the closure, not by the bulk edit of today


def test_a_reopened_stage_is_not_counted_twice(reports):
    apps = mapped_candidates(reports, 1)
    as_user(reports, "asha")
    to_stage(reports, apps["c0"], 3)
    assert figures(reports)["cells"]["1"] == 1 and figures(reports)["cells"]["2"] == 1
    version = reports.client.get(f"/api/applications/{apps['c0']}/draft").json()["lockVersion"]
    as_user(reports, "admin")   # reopening a completed stage is an admin action
    reopened = reports.client.post(f"/api/applications/{apps['c0']}/reopen", json={"stage": 2, "reason": "Corrected", "lock_version": version})
    assert reopened.status_code == 200, reopened.text
    as_user(reports, "asha")
    after = figures(reports)
    assert after["cells"]["2"] == 0 and after["cells"]["1"] == 1   # the reopened attempt is set aside, not counted
    advance(reports, apps["c0"], {"forwarded": "yes"})
    assert figures(reports)["cells"]["2"] == 1                    # completed again: counted once


def test_a_lead_call_shows_in_the_lead_report_at_once_and_only_for_today(reports, monkeypatch):
    reports.app.include_router(leads_router.router)
    # this part runs on the real clock: lead activity timestamps are written by the database
    monkeypatch.setattr(report_router, "datetime", datetime)
    now = datetime.now(timezone.utc)
    today = now.astimezone(IST).date().isoformat()
    with reports.sessions() as db:
        db.add_all([Lead(id=f"lead{n}", name=f"Lead {n}", phone=f"90000{n:05}", owner_id="asha") for n in range(3)])
        db.commit()
    def contacted():
        as_user(reports, "admin")
        body = reports.client.get(f"{REPORT}?from={today}&to={today}").json()
        return next(m["value"] for m in body["metrics"] if m["key"] == "leadsContacted"), body["leadFeedback"]
    assert contacted()[0] == 0
    as_user(reports, "asha")
    assert reports.client.post("/api/leads/lead0/call", json={"outcome": "interested"}).status_code == 200
    value, feedback = contacted()
    assert value == 1 and feedback["interested"] == 1
    as_user(reports, "asha")
    assert reports.client.post("/api/leads/lead1/call", json={"outcome": "no_answer"}).status_code == 200
    assert contacted()[0] == 2
    # a call made last week is not today's work
    with reports.sessions() as db:
        db.execute(text("UPDATE crm_lead_activities SET created_at = :c WHERE lead_id = 'lead0'"), {"c": now - timedelta(days=7)})
        db.commit()
    assert contacted()[0] == 1


# ------------------------------------------------------------------ migration 0060: closed_at

def test_migration_0060_adds_closed_at_and_backfills_the_closure_time(schema_database):  # noqa: F811
    engine = schema_database
    _migrate(engine, "head")
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    stage_time = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    touched = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    with sessions() as db:
        db.add(Client(id="cl", name="Mr Contact", country="UAE"))
        db.flush()
        db.add(Company(id="co", client_id="cl", name="Gulf Builders", country="UAE"))
        db.add(Designation(id="de", title="Welder"))
        db.flush()
        db.add(Order(id="or", ref="ORD-1", company_id="co", designation_id="de", openings=5, country="Qatar"))
        db.flush()
        for key in ("a", "b", "c"):
            db.add(Candidate(id=f"c{key}", ref=f"CND-{key}", name=f"Cand {key}", phone=f"9000000{ord(key)}", source=Source.walk_in))
        db.flush()
        db.add_all([Application(id="closed-with-stage", candidate_id="ca", order_id="or", current_stage=4, status=ApplicationStatus.closed,
                                closure_reason=ClosureReason.interview_rejected),
                    Application(id="closed-no-stage", candidate_id="cb", order_id="or", current_stage=1, status=ApplicationStatus.closed,
                                closure_reason=ClosureReason.not_interested),
                    Application(id="still-active", candidate_id="cc", order_id="or", current_stage=1, status=ApplicationStatus.active)])
        db.flush()
        db.add(ApplicationStage(application_id="closed-with-stage", stage=4, attempt=1, outcome="Rejected", completed_at=stage_time, data={}))
        db.commit()
    with engine.begin() as connection:   # an import touched every row's updated_at long after the closures
        connection.execute(text("UPDATE crm_applications SET updated_at = :t"), {"t": touched})
    _migrate(engine, "0059_whatsapp_ignored_numbers", downgrade=True)
    assert "closed_at" not in {c["name"] for c in inspect(engine).get_columns("crm_applications")}
    _migrate(engine, "0060_application_closed_at")
    assert "closed_at" in {c["name"] for c in inspect(engine).get_columns("crm_applications")}
    with engine.begin() as connection:
        rows = dict(connection.execute(text("SELECT id, closed_at FROM crm_applications")).all())
    assert rows["closed-with-stage"] == stage_time          # the closing stage record's time, not the import's
    assert rows["closed-no-stage"] == touched               # the best the old data allows: the last update
    assert rows["still-active"] is None                     # never closed
    _migrate(engine, "head")                                # idempotent on a database that already has the column
    assert "closed_at" in {c["name"] for c in inspect(engine).get_columns("crm_applications")}
