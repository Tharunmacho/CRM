"""The Daily Work Report refetches when something it counts changes: every such action publishes exactly one live event.

An event is one NOTIFY per committed transaction: {"t": [topics], "ids": {...}} with opaque ids only (see change_feed.py). The report
screen maps these topics to a refetch:

  leads         lead imported / called / status changed / assigned / unassigned / bulk-assigned (crm_leads, crm_lead_activities)
  candidates    candidate created / owner changed / deployed, and the candidate ids behind application and task changes
  applications  stage saved, completed, stopped, reopened, placement mapped / deployed
  tasks         task created / completed / reassigned, SLA events
  reports       added by the feed for every candidate / application / task / user change (not for leads)
  daily_report  a day-close submission (NEW: the audit table announces nothing by itself)

Runs on the guarded disposable PostgreSQL DB: NOTIFY only exists there.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.db import models as m
from app.services import change_feed
from app.services.traveller_id import assign_traveller_id
from lead_sheet_fixtures import meta_rows
from test_change_feed import Listener
from test_daily_work import as_user, desk, finish, mapped_candidates, owned_candidates  # noqa: F401  (fixtures and helpers)
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_lead_sheet import act_as, env as lead_env, imported  # noqa: F401  (fixture and helpers)


@pytest.fixture
def listening():
    change_feed.install()
    made = []

    def attach(engine):
        listener = Listener(engine)
        listener.messages(0.4)   # what the set-up announced
        made.append(listener)
        return listener

    yield attach
    for listener in made:
        listener.close()


def one_event(listener, *, includes, excludes=(), ids=None):
    """Exactly one event since the last call, naming at least `includes` and none of `excludes`; returns it."""
    messages = listener.messages(0.8)
    assert len(messages) == 1, messages
    event = messages[0]
    assert set(includes) <= set(event["t"]) and not set(excludes) & set(event["t"]), event
    for topic, expected in (ids or {}).items():
        assert set(expected) <= set(event.get("ids", {}).get(topic, [])), event
    blob = str(event)
    assert "@example.test" not in blob and "Test Lead" not in blob and "Candidate" not in blob   # opaque ids and topic names only
    return event


# ------------------------------------------------------------------ leads

def test_every_lead_action_publishes_one_leads_event(lead_env, listening):
    env = lead_env
    listener = listening(env.engine)
    imported(env, meta_rows(0, 6))
    event = one_event(listener, includes=["leads"], excludes=["daily_report"])   # a lead import
    ids = [row[0] for row in env.db.execute(text("SELECT id FROM crm_leads ORDER BY import_row"))]
    listener.messages(0.3)
    assert env.client.post("/api/leads/assign", json={"ids": ids[:2], "ownerId": "a"}).status_code == 200
    one_event(listener, includes=["leads"])                                               # assigned to one recruiter (a set-based write: topic only)
    assert env.client.post("/api/leads/assign", json={"ids": ids[2:4], "assigneeIds": ["a", "b"]}).status_code == 200
    one_event(listener, includes=["leads"])                                               # shared between recruiters (bulk assign)
    assert env.client.post("/api/leads/assign", json={"filter": {"owner": "unassigned"}, "ownerId": "auto"}).status_code == 200
    one_event(listener, includes=["leads"])                                               # spread automatically
    assert env.client.post("/api/leads/assign", json={"ids": ids[:1], "ownerId": "none"}).status_code == 200
    one_event(listener, includes=["leads"])                                               # taken back: unassigned
    call = env.client.post(f"/api/leads/{ids[1]}/call", json={"outcome": "interested"})
    assert call.status_code == 200, call.text
    one_event(listener, includes=["leads"], ids={"leads": [ids[1]]})                      # a call logged (lead_sheet.log_call)
    patched = env.client.patch(f"/api/leads/{ids[1]}", json={"status": "not_interested"})
    assert patched.status_code == 200, patched.text
    one_event(listener, includes=["leads"], ids={"leads": [ids[1]]})                      # a status / outcome change (patch_lead)
    assert env.client.patch(f"/api/leads/{ids[1]}", json={"status": "not_interested"}).status_code == 200
    assert listener.messages(0.5) == []                                                   # nothing changed: nothing announced
    assert event["t"].count("leads") == 1


def test_lead_events_do_not_carry_lead_ids_to_a_manager_or_recruiter():
    payload = {"t": ["leads"], "ids": {"leads": ["lead-1"]}, "at": 1}
    assert change_feed.visible_to(payload, "admin", "u")["ids"] == {"leads": ["lead-1"]}
    assert change_feed.visible_to(payload, "manager", "u") == {"topics": ["leads"], "at": 1}
    assert change_feed.visible_to(payload, "staff", "u") == {"topics": ["leads"], "at": 1}


# ------------------------------------------------------------------ candidates, stages, tasks, travellers

def test_candidate_stage_and_task_actions_each_publish_one_event(desk, listening):
    listener = listening(desk.sessions.kw["bind"])
    with desk.sessions() as db:   # a candidate created
        db.add(m.Candidate(id="new-1", ref="CND-NEW1", name="Candidate New", phone="9300000001", source=m.Source.walk_in, owner_id="asha"))
        db.commit()
    one_event(listener, includes=["candidates", "reports"], excludes=["leads"], ids={"candidates": ["new-1"]})
    with desk.sessions() as db:   # assigned to another recruiter (the owner changes)
        db.get(m.Candidate, "new-1").owner_id = "ravi"
        db.commit()
    one_event(listener, includes=["candidates", "reports"], ids={"candidates": ["new-1"]})

    apps = mapped_candidates(desk, 2)   # mapping creates the placement and its first task in one transaction
    listener.messages(0.5)
    (cand_a, app_a), (cand_b, app_b) = apps.items()

    # a stage saved as a draft, then completed: the completion moves the placement, ticks its task and creates the next one
    as_user(desk, "asha")
    version = desk.client.get(f"/api/applications/{app_a}/draft").json()["lockVersion"]
    saved = desk.client.put(f"/api/applications/{app_a}/draft",
                            json={"data": {"result": "passed", "feedback": "Prescreened by phone."}, "lock_version": version})
    assert saved.status_code == 200, saved.text
    one_event(listener, includes=["applications", "reports"])                           # the draft save
    done = desk.client.post(f"/api/applications/{app_a}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert done.status_code == 200, done.text
    event = one_event(listener, includes=["applications", "tasks", "reports"],
                      ids={"applications": [app_a], "candidates": [cand_a]})         # completed: stage row + task done + next task
    assert "daily_report" not in event["t"]

    version = desk.client.get(f"/api/applications/{app_b}/draft").json()["lockVersion"]   # a stage STOPPED (closes the placement)
    saved = desk.client.put(f"/api/applications/{app_b}/draft",
                            json={"data": {"stop": "rejected", "feedback": "Candidate was rejected at screening."}, "lock_version": version})
    assert saved.status_code == 200, saved.text
    listener.messages(0.5)
    stopped = desk.client.post(f"/api/applications/{app_b}/complete", json={"lock_version": saved.json()["lockVersion"]})
    assert stopped.status_code == 200, stopped.text
    one_event(listener, includes=["applications", "tasks", "reports"], ids={"applications": [app_b], "candidates": [cand_b]})

    as_user(desk, "admin")   # reopened (admin only): the stage record is superseded and the placement is active again
    version = desk.client.get(f"/api/applications/{app_b}/draft").json()["lockVersion"]
    reopened = desk.client.post(f"/api/applications/{app_b}/reopen", json={"lock_version": version, "stage": 1, "reason": "Entered by mistake."})
    assert reopened.status_code == 200, reopened.text
    one_event(listener, includes=["applications", "tasks", "reports"], ids={"applications": [app_b], "candidates": [cand_b]})

    with desk.sessions() as db:   # a task changing hands / its SLA window moving, and the SLA history: still the tasks topic
        task = db.scalars(select(m.Task).where(m.Task.application_id == app_a, m.Task.completed_at.is_(None))).first()
        task.owner_id = "ravi"
        task.due_at = datetime(2026, 12, 1, tzinfo=timezone.utc)
        db.commit()
        task_id = task.id
    one_event(listener, includes=["tasks", "reports"], ids={"tasks": [task_id]})
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET escalated = true, escalated_at = now() WHERE id = :t"), {"t": task_id})
        db.commit()
    one_event(listener, includes=["tasks", "reports"])                                  # an escalation (a task flagged overdue)


def test_a_traveller_being_deployed_publishes_one_event_with_the_candidate(desk, listening):
    apps = mapped_candidates(desk, 1)
    (candidate_id, app_id), = apps.items()
    listener = listening(desk.sessions.kw["bind"])
    with desk.sessions() as db:   # the same writes as completing stage 11: the stage row, the placement deployed, the Traveller ID
        application = db.get(m.Application, app_id)
        candidate = db.get(m.Candidate, candidate_id)
        db.add(m.ApplicationStage(application_id=app_id, stage=12, attempt=1, outcome="Booked", completed_by="asha", owner_id="asha",
                                  completed_at=datetime.now(timezone.utc), data={"travelDate": "2026-12-01"}))
        application.current_stage, application.status = 11, m.ApplicationStatus.deployed
        candidate.status = m.CandidateStatus.deployed
        assign_traveller_id(db, candidate)
        db.commit()
        assert candidate.traveller_id
    event = one_event(listener, includes=["applications", "candidates", "reports"], ids={"applications": [app_id], "candidates": [candidate_id]})
    assert "CI-T" not in str(event)   # the Traveller ID itself is never in an event


# ------------------------------------------------------------------ the day close

def test_a_day_close_publishes_one_daily_report_event_and_nothing_personal(desk, listening, monkeypatch):
    from types import SimpleNamespace

    from app.services import daily_work_report as service

    listener = listening(desk.sessions.kw["bind"])
    now = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)
    asha = SimpleNamespace(id="asha", name="Asha", role=m.Role.staff)

    def submit(note):
        with desk.sessions() as db:
            service.submit_day(db, asha, now.date(), note, "Asia/Kolkata", now)

    submit("All done today.")
    first = one_event(listener, includes=["daily_report"], excludes=["leads", "candidates", "tasks", "reports"], ids={"daily_report": ["asha"]})
    assert first["t"] == ["daily_report"]
    submit("Updated note")   # the repeat updates the entry: it is news again (the note shows on the screen)
    one_event(listener, includes=["daily_report"], ids={"daily_report": ["asha"]})
    assert "All done" not in str(first)
    payload = {"t": ["daily_report"], "ids": {"daily_report": ["asha"]}, "at": 1}
    assert "daily_report" in change_feed.CLIENT_TOPICS
    assert change_feed.visible_to(payload, "staff", "ravi") == {"topics": ["daily_report"], "at": 1}   # every desk is told, ids to supervisors only
    assert change_feed.visible_to(payload, "manager", "m")["ids"] == {"daily_report": ["asha"]}
    # a submit that fails (a date out of range) rolls back and announces nothing
    with desk.sessions() as db:
        with pytest.raises(Exception):
            service.submit_day(db, asha, datetime(2026, 10, 1).date(), None, "Asia/Kolkata", now)
    assert listener.messages(0.5) == []
