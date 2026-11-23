"""Nobody goes missing from the Tasks page: every candidate a recruiter owns is on exactly ONE of today's list, Follow-up, Declined,
Rejected or Travelled, and the All view (plus Follow-up, which the screen merges into it) lists every one of them.

Candidates are put in every state a recruiter can leave them in: through the real stage endpoints (rejected / declined at each stage), the
call dialog, the application form, unmap / change of job, and the odd statuses an import or an edit can leave behind.
"""
from sqlalchemy import text

from test_daily_work import (  # noqa: F401  (desk is a fixture)
    TUESDAY, advance, as_user, desk, ids, listing, mapped_candidates, owned_candidates, record_call, set_application, to_stage,
)
from test_database_naming import schema_database  # noqa: F401  (fixture used by desk)


def view(desk, name):
    return listing(desk, "asha", f"/api/daily-work?view={name}")


def seed(desk):
    """Returns {candidate id: expected tab} for every candidate seeded; expected tabs: current, follow, declined, rejected, travelled."""
    apps = mapped_candidates(desk, 14)
    as_user(desk, "asha")
    expected = {}
    to_stage(desk, apps["c0"], 5); advance(desk, apps["c0"], {"result": "declined"}); expected["c0"] = "declined"            # offer declined
    to_stage(desk, apps["c1"], 4); advance(desk, apps["c1"], {"result": "rejected"}); expected["c1"] = "rejected"            # rejected at the interview
    set_application(desk, apps["c2"], stage=13, status="deployed", candidate_status="deployed"); expected["c2"] = "travelled"
    set_application(desk, apps["c3"], status="closed", reason="unmapped"); expected["c3"] = "current"                         # only moved off a job
    expected["c4"] = "current"                                                                                              # prescreening
    to_stage(desk, apps["c5"], 3); expected["c5"] = "follow"                                                                  # interview scheduling
    advance(desk, apps["c6"], {"result": "failed"}); expected["c6"] = "rejected"                                              # failed prescreening
    advance(desk, apps["c7"], {"stop": "declined"}); expected["c7"] = "declined"                                              # declined at prescreening
    to_stage(desk, apps["c8"], 2); advance(desk, apps["c8"], {"stop": "rejected"}); expected["c8"] = "rejected"                # rejected at stage 2
    set_application(desk, apps["c9"], status="closed", reason="changed_job"); expected["c9"] = "current"                      # moved to another job
    to_stage(desk, apps["c10"], 3)
    with desk.sessions() as db:                                                                                              # a stale status next to an active placement
        db.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'c10'")); db.commit()
    expected["c10"] = "follow"
    to_stage(desk, apps["c11"], 2); advance(desk, apps["c11"], {"stop": "declined"}); expected["c11"] = "declined"            # declined at stage 2
    set_application(desk, apps["c12"], status="closed", reason=None, candidate_status="not_interested")                      # a legacy row closed with no reason at all,
    expected["c12"] = "declined"                                                                                            # the candidate marked not interested
    to_stage(desk, apps["c13"], 2); advance(desk, apps["c13"], {"stop": "declined"})                                          # declined at stage 2 ...
    assert record_call(desk, "c13", interested="yes").status_code == 200                                                      # ... said yes again ...
    with desk.sessions() as db:                                                                                              # ... and the status was edited back to not interested
        db.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'c13'")); db.commit()
    expected["c13"] = "declined"

    owned_candidates(desk, 7)                                                                                               # u100 .. u106, no placement
    assert record_call(desk, "u100", interested="no").status_code == 200; expected["u100"] = "declined"                       # said no at the call
    assert record_call(desk, "u101", interested="rejected").status_code == 200; expected["u101"] = "rejected"                 # rejected at the call
    with desk.sessions() as db:                                                                                              # marked not interested by an edit or an import
        db.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'u102'")); db.commit()
    expected["u102"] = "declined"
    expected["u103"] = "current"                                                                                            # nobody has spoken to them yet
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET status = 'needs_review' WHERE id = 'u104'")); db.commit()
    expected["u104"] = "current"
    assert record_call(desk, "u105", interested="unreachable", called=False).status_code == 200; expected["u105"] = "current"  # could not be reached
    assert record_call(desk, "u106", interested="no").status_code == 200                                                     # said no, then yes ...
    assert record_call(desk, "u106", interested="yes").status_code == 200
    with desk.sessions() as db:                                                                                              # ... then the status was edited back
        db.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'u106'")); db.commit()
    expected["u106"] = "declined"
    return expected


def test_every_candidate_is_on_exactly_one_tab_and_all_lists_everyone(desk):
    expected = seed(desk)
    tabs = {
        "current": set(ids(view(desk, "current"))),
        "follow": {item["candidateId"] for item in listing(desk, "asha", "/api/follow-up")["items"]},
        "declined": set(ids(view(desk, "declined"))),
        "rejected": set(ids(view(desk, "rejected"))),
        "travelled": set(ids(view(desk, "travelled"))),
    }
    # today's list never carries a Follow-up candidate, so "current" and "follow" are told apart by the Follow-up list
    found = {}
    for tab, members in tabs.items():
        for candidate in members:
            found.setdefault(candidate, []).append(tab)
    missing = sorted(set(expected) - set(found))
    # the one allowed overlap: a candidate who finished prescreening TODAY stays ticked on today's list while already in Follow-up (gone tomorrow)
    twice = {c: t for c, t in found.items() if len(t) > 1 and set(t) != {"current", "follow"}}
    wrong = {c: (expected[c], found[c]) for c in expected if c in found and expected[c] not in found[c]}
    assert not missing, f"on no tab at all: {missing}"
    assert not twice, f"on more than one tab: {twice}"
    assert not wrong, f"on the wrong tab (expected, found): {wrong}"
    # the All view (the screen merges Follow-up into it) lists every candidate once
    everyone = set(ids(view(desk, "all"))) | tabs["follow"]
    assert everyone == set(expected), f"All misses {sorted(set(expected) - everyone)}"
    counts = view(desk, "current")["viewCounts"]
    assert counts["declined"] == len(tabs["declined"]) and counts["rejected"] == len(tabs["rejected"]) and counts["travelled"] == len(tabs["travelled"])


def test_rejected_at_the_call_needs_no_client_or_job_and_shows_on_the_rejected_tab_with_its_reason(desk):
    owned_candidates(desk, 2)
    as_user(desk, "asha")
    saved = record_call(desk, "u100", interested="rejected")
    assert saved.status_code == 200 and "Rejected tab" in saved.json()["notice"] and "Data Bank" not in saved.json()["notice"]
    body = view(desk, "rejected")
    assert ids(body) == ["u100"] and ids(view(desk, "declined")) == [] and "u100" not in ids(view(desk, "current"))
    outcome = body["items"][0]["outcome"]
    assert (outcome["kind"], outcome["label"], outcome["reason"], outcome["stage"]) == ("rejected", "Rejected", "Rejected at the initial call", None)
    assert body["viewCounts"]["rejected"] == 1 and "u100" in ids(view(desk, "all"))
    # a later unreachable call changes nothing; an interested answer brings them back to today's list
    assert record_call(desk, "u100", interested="unreachable", called=False).status_code == 200
    assert ids(view(desk, "rejected")) == ["u100"]
    assert record_call(desk, "u100", interested="yes").status_code == 200
    assert ids(view(desk, "rejected")) == [] and "u100" in ids(view(desk, "current"))


def test_rejected_at_the_call_closes_a_prescreening_placement_as_failed_prescreening(desk):
    apps = mapped_candidates(desk, 1)
    as_user(desk, "asha")
    saved = record_call(desk, "c0", interested="rejected")
    assert saved.status_code == 200 and saved.json()["leftOrder"] is True
    with desk.sessions() as db:
        assert tuple(db.execute(text("SELECT status, closure_reason FROM crm_applications WHERE id = :a"), {"a": apps["c0"]}).one()) == ("closed", "prescreen_failed")
        assert db.scalar(text("SELECT count(*) FROM workflow_tasks WHERE application_id = :a AND completed_at IS NULL AND cancelled_at IS NULL"), {"a": apps["c0"]}) == 0
    body = view(desk, "rejected")
    assert ids(body) == ["c0"] and body["items"][0]["outcome"]["reason"] == "Failed prescreening" and body["items"][0]["outcome"]["stage"] == 1


def test_a_candidate_marked_not_interested_with_no_call_is_declined_with_a_plain_reason(desk):
    owned_candidates(desk, 1)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET status = 'not_interested' WHERE id = 'u100'")); db.commit()
    as_user(desk, "asha")
    body = view(desk, "declined")
    assert ids(body) == ["u100"] and body["items"][0]["outcome"]["reason"] == "Marked as not interested"


def test_a_failed_callback_never_takes_a_revived_candidate_off_today_s_list_nor_a_decided_one_off_its_tab(desk):
    owned_candidates(desk, 3)
    as_user(desk, "asha")
    assert record_call(desk, "u100", interested="no").status_code == 200
    assert ids(view(desk, "declined")) == ["u100"]
    assert record_call(desk, "u100", interested="yes").status_code == 200                 # interested again: back on today's list
    assert "u100" in ids(view(desk, "current")) and ids(view(desk, "declined")) == []
    assert record_call(desk, "u100", interested="unreachable", called=False).status_code == 200   # a later failed callback
    assert "u100" in ids(view(desk, "current")), "a failed callback must not park a candidate who said yes"
    assert ids(view(desk, "declined")) == [] and ids(view(desk, "rejected")) == []
    with desk.sessions() as db:
        row = db.execute(text("SELECT call_interested, call_called FROM crm_candidates WHERE id = 'u100'")).one()
    assert tuple(row) == ("yes", True), "the stored yes is kept; only the time and remarks of the failed call are recorded"
    # a stored "rejected" survives a failed callback as well
    assert record_call(desk, "u101", interested="rejected").status_code == 200
    assert record_call(desk, "u101", interested="unreachable", called=False).status_code == 200
    assert ids(view(desk, "rejected")) == ["u101"]


def test_the_notice_names_the_tab_where_the_candidate_really_shows(desk):
    apps = mapped_candidates(desk, 2)
    as_user(desk, "asha")
    to_stage(desk, apps["c0"], 3); advance(desk, apps["c0"], {"stop": "declined"})        # declined by the candidate at a stage: Declined
    to_stage(desk, apps["c1"], 3); advance(desk, apps["c1"], {"stop": "rejected"})        # rejected by the recruiter at a stage: Rejected
    later = record_call(desk, "c0", interested="rejected").json()["notice"]                  # a later rejection at the call: the placement still decides
    assert "Declined" in later and "Rejected" not in later, later
    assert "c0" in ids(view(desk, "declined"))
    other = record_call(desk, "c1", interested="no").json()["notice"]                        # a later no at the call: still Rejected
    assert "Rejected" in other and "Declined" not in other, other
    assert "c1" in ids(view(desk, "rejected"))


def test_a_follow_up_candidate_with_a_rejected_call_shows_when_the_call_was_made(desk):
    apps = mapped_candidates(desk, 1)
    as_user(desk, "asha")
    to_stage(desk, apps["c0"], 3)
    assert record_call(desk, "c0", interested="rejected").status_code == 200
    item = next(i for i in listing(desk, "asha", "/api/follow-up")["items"] if i["candidateId"] == "c0")
    assert item["calledAt"], "a rejected call is a call that was made"


def test_a_candidate_rejected_at_the_call_comes_back_when_the_form_says_interested(desk):
    from app.api.routers import application_form
    desk.app.include_router(application_form.router)   # the Tasks desk does not mount the form's router
    owned_candidates(desk, 2)
    as_user(desk, "asha")
    assert record_call(desk, "u100", interested="rejected").status_code == 200
    assert ids(view(desk, "rejected")) == ["u100"]
    saved = desk.client.put("/api/candidates/u100/application-form", json={"screening": {"interestLevel": "interested"}})
    assert saved.status_code == 200, saved.text
    assert "u100" in ids(view(desk, "current")) and ids(view(desk, "rejected")) == []


def test_the_form_warns_when_the_last_call_was_a_rejection_but_the_interest_level_says_interested():
    from datetime import date
    from app.services.application_form import warnings_for
    form = {"personal": {"nationality": "Indian", "mobileNumber": None}, "passport": {"passportNumber": None, "expiry": {"status": "ok"}},
            "screening": {"callOutcome": {"interested": "rejected"}, "interestLevel": "interested", "familyMemberNumber": None, "nextFollowUpDate": None}}
    messages = warnings_for(form, date(2026, 11, 3))
    assert any("rejected" in message and "interest level says interested" in message for message in messages), messages
    form["screening"]["callOutcome"] = {"interested": "no"}
    assert any("not interested" in message for message in warnings_for(form, date(2026, 11, 3)))
