"""scripts/park_declined_candidates.py on the guarded disposable PostgreSQL database (migrated to head).

The script is driven through its main() with the test database substituted for the configured one; the shared-database guard is
faked (the real guard looks at backend/.env and the SSH tunnel, which a test must never open).
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.sql import false

from app.db import models as m
from app.services import daily_work
from test_database_naming import schema_database  # noqa: F401  (fixture the desk builds on)
# The desk fixture (users admin / manager / asha / ravi, one client, company, designation and order "order") and its helpers.
from test_daily_work import (  # noqa: F401  (desk is a fixture)
    WEDNESDAY, advance, asha_count, bank_row, desk, follow, ids, listing, mapped_candidates, on_a_job, open_tasks, second_order, to_stage,
)

BACKEND = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("park_declined_candidates", BACKEND / "scripts" / "park_declined_candidates.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
NUMBERS = count(1)
CS, AS = m.CandidateStatus, m.ApplicationStatus


@pytest.fixture
def park(desk, monkeypatch):
    """The script pointed at the test database, with the shared-database guard faked as 'a plain local database'."""
    monkeypatch.setattr(script, "SessionLocal", desk.sessions)
    monkeypatch.setattr(script, "engine", desk.sessions.kw["bind"])
    monkeypatch.setattr(script, "shared_database_reason", lambda: None)
    return desk


def add(env, key, care, *, status=CS.new, owner="asha", apps=()):
    """A candidate plus applications [(status, closure reason or None, stage)], oldest first (each created a day after the last)."""
    with env.sessions() as db:
        db.add(m.Candidate(id=key, ref=f"CND-{next(NUMBERS):05d}", care_id=care, name=f"Person {key}", phone=f"93000{next(NUMBERS):05d}",
                           source=m.Source.walk_in, owner_id=owner, status=status))
        db.flush()
        for day, (app_status, reason, stage) in enumerate(apps):
            db.add(m.Application(id=f"{key}-a{day}", candidate_id=key, order_id="order", owner_id=owner, status=app_status, current_stage=stage,
                                 closure_reason=m.ClosureReason(reason) if reason else None, created_at=BASE + timedelta(days=day)))
        db.commit()


def closed(reason, stage=5):
    return (AS.closed, reason, stage)


def seed(env):
    """Who must be parked (P-*) and who must be left alone (the rest)."""
    add(env, "p-offer", "CI-062", apps=[closed("offer_declined", 5)])
    add(env, "p-stage", "CI-063", owner="ravi", apps=[closed("stage_declined", 3)])
    add(env, "p-int", "CI-064", apps=[closed("not_interested", 2)])
    add(env, "p-travel", "CI-065", apps=[closed("not_travelling", 11)])
    add(env, "p-twice", "CI-066", apps=[closed("unmapped", 1), closed("offer_declined", 5)])     # the latest one is the decline
    add(env, "x-review", "CI-070", status=CS.needs_review, apps=[closed("offer_declined", 5)])   # needs_review stays
    add(env, "x-already", "CI-071", status=CS.not_interested, apps=[closed("offer_declined", 5)])
    add(env, "x-active", "CI-072", apps=[closed("offer_declined", 5), (AS.active, None, 1)])      # mapped again since: newer active one
    add(env, "x-unmapped", "CI-073", apps=[closed("offer_declined", 5), closed("unmapped", 1)])   # the latest closure is bookkeeping
    add(env, "x-changed", "CI-074", apps=[closed("changed_job", 1)])
    add(env, "x-rejected", "CI-075", apps=[closed("interview_rejected", 4)])
    add(env, "x-none", "CI-076")                                                                   # never mapped
    add(env, "x-deployed", "CI-077", status=CS.deployed, apps=[(AS.deployed, None, 11)])
    add(env, "x-older-active", "CI-078", apps=[(AS.active, None, 4), closed("stage_declined", 4)])   # still has an active placement


PARKED = ["CI-062", "CI-063", "CI-064", "CI-065", "CI-066"]


def statuses(env):
    with env.sessions() as db:
        return {c.care_id: c.status.value for c in db.scalars(select(m.Candidate))}


def snapshot(env):
    """Everything the script must never change, plus the audit log (to prove a dry run or a refusal wrote nothing)."""
    with env.sessions() as db:
        applications = [tuple(r) for r in db.execute(text(
            "SELECT id, status, closure_reason, current_stage, lock_version, draft::text, updated_at FROM crm_applications ORDER BY id"))]
        candidates = [tuple(r) for r in db.execute(text("SELECT id, status, owner_id, updated_at FROM crm_candidates ORDER BY id"))]
        tasks = [tuple(r) for r in db.execute(text("SELECT id, completed_at, cancelled_at FROM workflow_tasks ORDER BY id"))]
        audit = [tuple(r) for r in db.execute(text("SELECT id, action FROM system_audit_log ORDER BY id"))]
    return applications, candidates, tasks, audit


def run(*argv):
    return script.main(list(argv))


def parked_audits(env):
    with env.sessions() as db:
        return list(db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == script.AUDIT_ACTION).order_by(m.AuditEntry.target)))


def test_dry_run_changes_nothing_and_lists_exactly_the_right_candidates(park, capsys):
    seed(park)
    before = snapshot(park)
    assert run() == 0
    out = capsys.readouterr().out
    assert before == snapshot(park)
    assert "dry run" in out.lower() and "Dry run: nothing was changed" in out and "5 candidate(s) to park" in out
    for care in PARKED:
        assert care in out
    for care in ("CI-070", "CI-071", "CI-072", "CI-073", "CI-074", "CI-075", "CI-076", "CI-077", "CI-078"):
        assert care not in out
    line = next(row for row in out.splitlines() if "CI-062" in row)
    assert "Person p-offer" in line and "owner: Asha" in line and "offer_declined at stage 5" in line and "status: new" in line
    assert "owner: Ravi" in next(row for row in out.splitlines() if "CI-063" in row)
    assert "stage_declined at stage 3" in out and "not_interested at stage 2" in out and "not_travelling at stage 11" in out


def test_apply_parks_only_the_right_candidates_and_touches_nothing_else(park, capsys):
    seed(park)
    before = statuses(park)
    applications, candidates, tasks, audit = snapshot(park)
    assert run("--apply") == 0
    out = capsys.readouterr().out
    assert "WRITING" in out and "Parked 5 of 5." in out
    after = statuses(park)
    assert {care for care in after if after[care] != before[care]} == set(PARKED)
    assert all(after[care] == "not_interested" for care in PARKED)
    # Everyone else keeps exactly the status they had.
    assert {care: s for care, s in after.items() if care not in PARKED} == {care: s for care, s in before.items() if care not in PARKED}
    # Applications, tasks and ownership are untouched; only the candidate status (and its updated_at) moved.
    new_applications, new_candidates, new_tasks, new_audit = snapshot(park)
    assert new_applications == applications and new_tasks == tasks
    assert [(c[0], c[2]) for c in new_candidates] == [(c[0], c[2]) for c in candidates]
    # One audit entry per parked candidate, with the old status, the closure reason and the application id.
    entries = parked_audits(park)
    assert len(entries) == 5 and len(new_audit) == len(audit) + 5
    by_candidate = {e.entity_id: e for e in entries}
    assert set(by_candidate) == {"p-offer", "p-stage", "p-int", "p-travel", "p-twice"}
    offer = by_candidate["p-offer"]
    assert (offer.action, offer.entity_type, offer.target, offer.actor_id) == (script.AUDIT_ACTION, "candidate", "Person p-offer", None)
    assert offer.meta == {"old_status": "new", "closure_reason": "offer_declined", "application_id": "p-offer-a0"}
    assert by_candidate["p-twice"].meta["application_id"] == "p-twice-a1" and by_candidate["p-twice"].meta["closure_reason"] == "offer_declined"
    assert script.AUDIT_ACTION == "Parked after decline: status set to Not interested"


@pytest.mark.parametrize("reason", ["stage_declined", "offer_declined", "not_interested", "not_travelling"])
def test_every_candidate_said_no_reason_parks(park, reason):
    add(park, "c", "CI-900", apps=[closed(reason, 6)])
    assert run("--apply") == 0
    assert statuses(park)["CI-900"] == "not_interested"


@pytest.mark.parametrize("reason", ["unmapped", "changed_job", "interview_rejected", "prescreen_failed", "resume_not_received", "medical_unfit",
                                    "offer_not_received", "stage_rejected", "interview_not_possible", "passport_not_available",
                                    "advance_not_paid", "visa_rejected", "visa_stamping_refused"])
def test_rejected_and_administrative_closures_are_never_parked(park, reason):
    add(park, "c", "CI-901", apps=[closed(reason, 4)])
    before = snapshot(park)
    assert run("--apply") == 0
    assert statuses(park)["CI-901"] == "new" and before == snapshot(park)


def test_a_candidate_with_a_newer_active_application_is_skipped(park, capsys):
    add(park, "c", "CI-902", apps=[closed("offer_declined", 5), (AS.active, None, 1)])   # declined, then mapped to a new order
    add(park, "d", "CI-903", apps=[closed("offer_declined", 5), (AS.deployed, None, 11)])
    before = snapshot(park)
    assert run("--apply") == 0
    assert "0 candidate(s) to park" in capsys.readouterr().out
    assert statuses(park)["CI-902"] == "new" and statuses(park)["CI-903"] == "new" and before == snapshot(park)


def test_an_older_decline_behind_a_newer_unmapped_or_rejected_application_is_skipped(park):
    add(park, "c", "CI-904", apps=[closed("offer_declined", 5), closed("unmapped", 1)])
    add(park, "d", "CI-905", apps=[closed("stage_declined", 3), closed("interview_rejected", 4)])
    assert run("--apply") == 0
    assert statuses(park)["CI-904"] == "new" and statuses(park)["CI-905"] == "new" and parked_audits(park) == []


def test_needs_review_and_already_not_interested_are_left_alone(park):
    add(park, "r", "CI-906", status=CS.needs_review, apps=[closed("offer_declined", 5)])
    add(park, "n", "CI-907", status=CS.not_interested, apps=[closed("offer_declined", 5)])
    add(park, "i", "CI-908", status=CS.in_process, apps=[closed("offer_declined", 5)])
    before = snapshot(park)
    assert run("--apply") == 0
    assert statuses(park) == {"CI-906": "needs_review", "CI-907": "not_interested", "CI-908": "in_process"} and before == snapshot(park)


CLOSED = BASE + timedelta(days=10)       # the decline was closed (the placement's updated_at) on this day


def call(env, key, interested, at, *, called=True, closed_at=CLOSED):
    """Record a call on the candidate (as record_call does) and pin when their only application was closed."""
    with env.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_called = :called, call_interested = :i, call_recorded_at = :at WHERE id = :c"),
                   {"called": called, "i": interested, "at": at, "c": key})
        db.execute(text("UPDATE crm_applications SET updated_at = :closed WHERE candidate_id = :c"), {"closed": closed_at, "c": key})
        db.commit()


def test_a_candidate_who_said_yes_in_a_call_after_the_decline_is_not_parked_the_same_rule_as_the_tasks_list(park, capsys):
    add(park, "y-after", "CI-910", apps=[closed("offer_declined", 5)])      # yes after the closure: interested again
    add(park, "y-before", "CI-911", apps=[closed("offer_declined", 5)])     # yes, but BEFORE the closure
    add(park, "y-no", "CI-912", apps=[closed("offer_declined", 5)])         # a call after the closure, but the answer is no
    add(park, "y-same", "CI-913", apps=[closed("offer_declined", 5)])       # the same instant is not after
    add(park, "y-none", "CI-914", apps=[closed("offer_declined", 5)])       # never called
    call(park, "y-after", "yes", CLOSED + timedelta(hours=1))
    call(park, "y-before", "yes", CLOSED - timedelta(hours=1))
    call(park, "y-no", "no", CLOSED + timedelta(hours=1))
    call(park, "y-same", "yes", CLOSED)
    with park.sessions() as db:
        db.execute(text("UPDATE crm_applications SET updated_at = :closed WHERE candidate_id = 'y-none'"), {"closed": CLOSED})
        db.commit()
    assert run("--candidate", "CI-910", "CI-911", "CI-912", "CI-913", "CI-914") == 1          # one is reported as skipped: exit 1
    out = capsys.readouterr().out
    assert "4 candidate(s) to park" in out and "CI-910: skipped, said yes in a call" in out and "after the decline" in out
    # The Tasks list rule agrees with the script (checked while everybody is still "new"): only the candidate who said yes after the
    # decline is not parked there.
    with park.sessions() as db:
        listed = set(db.scalars(select(m.Candidate.id).where(m.Candidate.status.in_(daily_work.WORKABLE), ~daily_work._parked(),
                                                             m.Candidate.id.like("y-%"))))
    assert listed == {"y-after"}
    before = snapshot(park)
    assert run("--apply") == 0
    assert "Parked 4 of 4." in capsys.readouterr().out
    after = statuses(park)
    assert after["CI-910"] == "new" and all(after[care] == "not_interested" for care in ("CI-911", "CI-912", "CI-913", "CI-914"))
    assert {e.entity_id for e in parked_audits(park)} == {"y-before", "y-no", "y-same", "y-none"}
    assert before != snapshot(park)


@pytest.mark.parametrize("decline_has_the_greater_id", [True, False])
def test_two_applications_created_in_the_same_instant_are_decided_by_the_greater_id(park, decline_has_the_greater_id):
    with park.sessions() as db:
        db.add(m.Candidate(id="t", ref="CND-90001", care_id="CI-915", name="Tie", phone="9300090001", source=m.Source.walk_in, owner_id="asha", status=CS.new))
        db.flush()
        for app_id, reason in (("t-a", "unmapped" if decline_has_the_greater_id else "offer_declined"),
                               ("t-b", "offer_declined" if decline_has_the_greater_id else "unmapped")):
            db.add(m.Application(id=app_id, candidate_id="t", order_id="order", owner_id="asha", status=AS.closed, current_stage=5,
                                 closure_reason=m.ClosureReason(reason), created_at=BASE))
        db.commit()
    assert run("--apply") == 0
    assert statuses(park)["CI-915"] == ("not_interested" if decline_has_the_greater_id else "new")


def test_candidate_ids_are_trimmed_and_upper_cased(park, capsys):
    seed(park)
    assert run("--candidate", " ci-062 ", "ci-063", "ci-062", "   ", "--apply") == 0
    out = capsys.readouterr().out
    assert "Parked 2 of 2." in out and "not found" not in out
    assert {care for care, s in statuses(park).items() if s == "not_interested"} == {"CI-062", "CI-063", "CI-071"}
    assert run("--candidate", "   ", "--apply") == 2          # nothing left after trimming: refused, nothing written
    assert statuses(park)["CI-064"] == "new"


def test_a_second_run_changes_nothing(park, capsys):
    seed(park)
    assert run("--apply") == 0
    capsys.readouterr()
    after_first = snapshot(park)
    assert run("--apply") == 0
    out = capsys.readouterr().out
    assert "0 candidate(s) to park" in out and "Parked 0 of 0." in out
    assert after_first == snapshot(park) and len(parked_audits(park)) == 5


def test_candidate_option_limits_the_change_and_reports_the_others(park, capsys):
    seed(park)
    with park.sessions() as db:
        ref = db.scalar(select(m.Candidate.ref).where(m.Candidate.care_id == "CI-063"))
    assert run("--candidate", "CI-062", ref, "--apply") == 0          # a Care India ID and a CND reference both work
    capsys.readouterr()
    assert {care for care, s in statuses(park).items() if s == "not_interested"} == {"CI-062", "CI-063", "CI-071"}   # CI-071 already was
    # A candidate that is named but does not qualify, or does not exist, is reported and the exit code says so.
    assert run("--candidate", "CI-072", "CI-070", "CI-999", "CI-064") == 1
    out = capsys.readouterr().out
    assert "CI-064" in out and "1 candidate(s) to park" in out
    assert "CI-072: skipped, has an active or deployed application" in out
    assert "CI-070: skipped, status is 'needs_review', not 'new'" in out
    assert "CI-999: not found" in out
    assert statuses(park)["CI-064"] == "new"          # the dry run did not write


def test_the_guard_refuses_the_shared_database_unless_allow_remote_is_given(park, monkeypatch, capsys):
    seed(park)
    monkeypatch.setattr(script, "shared_database_reason", lambda: "the database is on port 55436, the SSH tunnel to the shared database.")
    before = snapshot(park)
    for argv in ([], ["--apply"], ["--candidate", "CI-062", "--apply"]):
        assert run(*argv) == 2
    captured = capsys.readouterr()
    assert "Refused: the database is on port 55436" in captured.err and "--allow-remote" in captured.err and captured.out == ""
    assert before == snapshot(park)
    assert run("--apply", "--allow-remote") == 0          # on purpose: now it runs
    assert {care for care, s in statuses(park).items() if s == "not_interested"} >= set(PARKED)


def test_the_real_guard_refuses_when_no_env_file_was_chosen(desk, monkeypatch, capsys):
    """backend/.env (the shared database) is what the app uses when CRM_ENV_FILE is not set: the real guard must refuse that."""
    park = desk
    seed(park)
    monkeypatch.setattr(script, "SessionLocal", park.sessions)   # the real guard is kept; should it ever pass, only the test database is reachable
    monkeypatch.setattr(script, "engine", park.sessions.kw["bind"])
    monkeypatch.delenv("CRM_ENV_FILE", raising=False)
    before = snapshot(park)
    assert script.shared_database_reason() is not None
    assert run("--apply") == 2 and "Refused:" in capsys.readouterr().err
    assert before == snapshot(park)


def test_one_candidate_failing_does_not_stop_the_rest(park, monkeypatch, capsys):
    seed(park)
    real = script.park

    def flaky(db, candidate_id):
        if candidate_id == "p-stage":
            raise RuntimeError("simulated failure")
        return real(db, candidate_id)

    monkeypatch.setattr(script, "park", flaky)
    assert run("--apply") == 3
    captured = capsys.readouterr()
    assert "CI-063: FAILED (simulated failure)" in captured.err and "Parked 4 of 5." in captured.out
    after = statuses(park)
    assert after["CI-063"] == "new" and all(after[c] == "not_interested" for c in ("CI-062", "CI-064", "CI-065", "CI-066"))


def test_a_candidate_who_changes_between_the_listing_and_the_write_is_not_parked(park, monkeypatch, capsys):
    add(park, "c", "CI-909", apps=[closed("offer_declined", 5)])
    real = script.find_eligible

    def listed_then_mapped(db, idents=None):
        found = real(db, idents)
        with park.sessions() as other:       # someone maps them to a new order after the list was made
            other.add(m.Application(id="late", candidate_id="c", order_id="order", owner_id="asha", status=AS.active, current_stage=1,
                                    created_at=BASE + timedelta(days=30)))
            other.commit()
        return found

    monkeypatch.setattr(script, "find_eligible", listed_then_mapped)
    assert run("--apply") == 0
    assert "CI-909: skipped, no longer qualifies" in capsys.readouterr().out
    assert statuses(park)["CI-909"] == "new" and parked_audits(park) == []


# ------------------------------------------------------------------ end to end with the Tasks list


def test_end_to_end_a_candidate_declined_at_stage_5_leaves_the_tasks_list_and_can_be_mapped_again(park, monkeypatch, capsys):
    """CI-062 style: declined the offer at stage 5, status 'new' (what the old code left). After the script they are off the list."""
    desk = park
    apps = mapped_candidates(desk, 2)                       # c0 declines at stage 5; c1 stays at prescreening
    order2 = second_order(desk)
    to_stage(desk, apps["c0"], 5)
    desk.clock["now"] = WEDNESDAY
    advance(desk, apps["c0"], {"result": "declined"})       # the real stage rules: closes as offer_declined
    with desk.sessions() as db:
        assert db.scalar(text("SELECT closure_reason FROM crm_applications WHERE id = :a"), {"a": apps["c0"]}) == "offer_declined"
        # Put back what the OLD code left behind, with its old Tasks-list rule (status 'new', no "parked" rule).
        db.execute(text("UPDATE crm_candidates SET status = 'new' WHERE id = 'c0'"))
        db.commit()
        care_id = db.scalar(select(m.Candidate.care_id).where(m.Candidate.id == "c0"))    # given at creation, immutable
    with monkeypatch.context() as old_rules:
        old_rules.setattr(daily_work, "_parked", lambda: false())
        assert sorted(ids(listing(desk, "asha"))) == ["c0", "c1"]            # the bug: c0 is on the list again as a candidate to call
        assert asha_count(desk) == {"total": 2, "toWork": 2, "doneToday": 0}
        assert bank_row(desk, "c0")["status"] == "new"

        assert run("--candidate", care_id, "--apply") == 0
        assert care_id in capsys.readouterr().out
        # Even with the new "parked" rule switched off, the status alone takes them off the list and the counts.
        assert ids(listing(desk, "asha")) == ["c1"] and listing(desk, "asha")["summary"] == {"total": 1, "toWork": 1, "doneToday": 0}
        assert asha_count(desk) == {"total": 1, "toWork": 1, "doneToday": 0}
    # With the current rules the decline (recorded today) is the recruiter's finished work: a ticked done-today row, never Follow-up.
    assert [(i["candidateId"], i["status"], i["declined"]) for i in listing(desk, "asha")["items"]] == [("c1", "open", False), ("c0", "done", True)]
    assert asha_count(desk) == {"total": 2, "toWork": 1, "doneToday": 1}
    desk.clock["now"] = WEDNESDAY + timedelta(days=1)
    # From tomorrow: not today's work, not Follow-up, not in the admin counts, for the recruiter or a supervisor.
    assert ids(listing(desk, "asha")) == ["c1"] and listing(desk, "asha")["summary"] == {"total": 1, "toWork": 1, "doneToday": 0}
    assert asha_count(desk) == {"total": 1, "toWork": 1, "doneToday": 0}
    assert listing(desk, "manager")["summary"] == {"total": 1, "toWork": 1, "doneToday": 0}
    assert ids(listing(desk, "admin", "/api/daily-work?staff=asha")) == ["c1"]
    assert follow(desk, "asha")["items"] == [] and follow(desk, "manager")["items"] == []
    # Still in the Data Bank with the new status, on no job, no open task, application and owner untouched.
    row = bank_row(desk, "c0")
    assert row is not None and row["status"] == "not_interested" and not on_a_job(desk, "c0") and open_tasks(desk, "c0") == 0
    with desk.sessions() as db:
        application = db.get(m.Application, apps["c0"])
        assert (application.status.value, application.closure_reason.value, application.current_stage) == ("closed", "offer_declined", 5)
        assert db.get(m.Candidate, "c0").owner_id == "asha"

    # Mapped to a new order through the real endpoint: back on today's list at stage 1.
    mapped = desk.client.post("/api/applications", json={"candidate_id": "c0", "order_id": order2})
    assert mapped.status_code == 201, mapped.text
    today = listing(desk, "asha")
    back = next(item for item in today["items"] if item["candidateId"] == "c0")
    assert (back["stage"], back["status"], back["applicationId"]) == (1, "open", mapped.json()["id"])
    assert today["summary"] == {"total": 2, "toWork": 2, "doneToday": 0} and asha_count(desk) == {"total": 2, "toWork": 2, "doneToday": 0}
    assert follow(desk, "asha")["items"] == []
