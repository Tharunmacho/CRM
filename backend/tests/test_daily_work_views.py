"""Tasks page views: `GET /api/daily-work?view=current|declined|rejected|travelled|all` and the `outcome` of every item.

Runs on the explicitly guarded disposable PostgreSQL DB (see test_database_naming._guard_test_url), with the same desk fixture as
test_daily_work. The first candidates are driven through the real stage endpoints, so the closures are the ones production writes.
"""
import io
from datetime import timedelta
import re
import zipfile

import pytest
from sqlalchemy import event, text

from test_daily_work import (  # noqa: F401  (desk is a fixture)
    TUESDAY, advance, as_user, desk, ids, listing, mapped_candidates, owned_candidates, record_stage, set_application, to_stage,
)
from test_database_naming import schema_database  # noqa: F401  (fixture used by desk)

DECLINED_AT_OFFER = {"result": "declined"}       # stage 5 -> offer_declined
REJECTED_AT_INTERVIEW = {"result": "rejected"}   # stage 4 -> interview_rejected


def ended_desk(desk):
    """asha: c0 declined the offer (stage 5), c1 rejected at the interview (stage 4), c2 travelled, c3 only unmapped, c4 still at prescreening.
    ravi: c10 declined (so scopes can be told apart)."""
    apps = mapped_candidates(desk, 5)
    as_user(desk, "asha")
    to_stage(desk, apps["c0"], 5)
    advance(desk, apps["c0"], DECLINED_AT_OFFER)
    to_stage(desk, apps["c1"], 4)
    advance(desk, apps["c1"], REJECTED_AT_INTERVIEW)
    set_application(desk, apps["c2"], stage=13, status="deployed", candidate_status="deployed")
    set_application(desk, apps["c3"], status="closed", reason="unmapped")
    other = mapped_candidates(desk, 1, owner="ravi", start=10)
    as_user(desk, "ravi")
    to_stage(desk, other["c10"], 5)
    advance(desk, other["c10"], DECLINED_AT_OFFER)
    return apps, other


def view(desk, user, name, extra=""):
    return listing(desk, user, f"/api/daily-work?view={name}{extra}")


def test_every_item_of_the_default_view_carries_a_current_outcome_and_the_default_rows_are_unchanged(desk):
    apps, _other = ended_desk(desk)
    plain = listing(desk, "asha")
    explicit = view(desk, "asha", "current")
    assert [i["candidateId"] for i in plain["items"]] == [i["candidateId"] for i in explicit["items"]]
    assert {i["outcome"]["kind"] for i in plain["items"]} == {"current"}
    assert all(i["outcome"] == {"kind": "current", "label": None, "reason": None, "stage": None, "at": None, "by": None} for i in plain["items"])
    assert plain["view"] == "current"
    # The rejected and the declined candidates are parked (Rejected / Declined tabs); the unmapped one is only off a job, so it stays on today's list.
    assert set(ids(plain)) == {"c3", "c4"}
    for item in listing(desk, "asha", "/api/follow-up")["items"]:
        assert item["outcome"]["kind"] == "current"


def test_declined_view_lists_the_candidate_with_who_why_and_where(desk):
    apps, _other = ended_desk(desk)
    body = view(desk, "asha", "declined")
    assert ids(body) == ["c0"]
    item = body["items"][0]
    assert item["taskId"] is None and item["applicationId"] == apps["c0"] and item["status"] == "closed"
    assert item["stage"] == 5 and item["client"] == "Gulf Builders" and item["country"] == "Qatar" and item["position"] == "Welder"
    outcome = item["outcome"]
    assert outcome["kind"] == "declined" and outcome["label"] == "Declined" and outcome["stage"] == 5
    assert outcome["reason"] == "Offer declined by the candidate" and outcome["by"] == "Asha" and outcome["at"]


def test_rejected_view_lists_every_other_negative_closure_and_never_unmapped(desk):
    ended_desk(desk)
    body = view(desk, "asha", "rejected")
    assert ids(body) == ["c1"]
    outcome = body["items"][0]["outcome"]
    assert (outcome["kind"], outcome["label"], outcome["stage"]) == ("rejected", "Rejected", 4)
    assert outcome["reason"] == "Rejected at the interview" and outcome["by"] == "Asha"


def test_travelled_view_lists_deployed_placements(desk):
    apps, _other = ended_desk(desk)
    body = view(desk, "asha", "travelled")
    assert ids(body) == ["c2"]
    outcome = body["items"][0]["outcome"]
    assert (outcome["kind"], outcome["label"], outcome["reason"], outcome["stage"]) == ("travelled", "Travelled", None, 13)
    # The Travelled screen reads the same deployed placements.
    desk.actor.id = "asha"
    assert view(desk, "admin", "travelled")["viewCounts"]["travelled"] == 1


def test_every_row_carries_the_date_of_travel_read_from_the_air_ticket_stage(desk):
    apps, _other = ended_desk(desk)
    record_stage(desk, apps["c2"], 13, attempt=1, data={"travelDate": "2001-01-01"})
    record_stage(desk, apps["c2"], 13, attempt=2, data={"travelDate": "2026-07-09"}, at=TUESDAY + timedelta(hours=1))   # the latest attempt counts
    travelled = view(desk, "asha", "travelled")["items"]
    assert [(item["candidateId"], item["travelDate"]) for item in travelled] == [("c2", "2026-07-09")]
    everything = {item["candidateId"]: item["travelDate"] for item in view(desk, "asha", "all")["items"]}
    assert everything == {"c4": None, "c3": None, "c1": None, "c0": None, "c2": "2026-07-09"}       # nobody else has travelled: no date, never a missing key
    # a ticket stage that was reopened (the candidate has not flown), or a date that is no calendar day, shows nothing
    record_stage(desk, apps["c2"], 13, attempt=3, data={"travelDate": "2026-07-10"}, reopened="Ticket changed", at=TUESDAY + timedelta(hours=2))
    assert view(desk, "asha", "travelled")["items"][0]["travelDate"] is None
    record_stage(desk, apps["c0"], 13, data={"travelDate": "2026-02-31"})
    assert {item["candidateId"]: item["travelDate"] for item in view(desk, "asha", "all")["items"]}["c0"] is None


def test_the_export_has_a_DOT_column_after_DOC(desk):
    apps, _other = ended_desk(desk)
    record_stage(desk, apps["c2"], 13, data={"travelDate": "2026-07-09"})
    as_user(desk, "asha")
    texts = sheet_texts(desk.client.get("/api/daily-work/export?view=travelled").content)
    assert texts.index("DOC") + 1 == texts.index("DOT")
    assert "09-Jul-2026" in texts


def test_all_view_is_today_plus_the_three_each_candidate_once(desk):
    ended_desk(desk)
    body = view(desk, "asha", "all")
    assert sorted(ids(body)) == ["c0", "c1", "c2", "c3", "c4"]
    kinds = {item["candidateId"]: item["outcome"]["kind"] for item in body["items"]}
    # c1 (rejected) is parked: on the Rejected view only; c3 was only unmapped, which is no outcome.
    assert kinds == {"c4": "current", "c3": "current", "c1": "rejected", "c0": "declined", "c2": "travelled"}
    assert len(ids(body)) == len(set(ids(body)))


def test_view_counts_agree_with_the_lists_in_every_scope(desk):
    ended_desk(desk)
    for user, staff in (("asha", ""), ("ravi", ""), ("admin", ""), ("manager", ""), ("admin", "&staff=asha"), ("admin", "&staff=ravi")):
        counts = view(desk, user, "current", staff)["viewCounts"]
        assert set(counts) == {"current", "declined", "rejected", "travelled"}
        for name in ("declined", "rejected", "travelled"):
            assert counts[name] == len(view(desk, user, name, staff)["items"]), (user, staff, name)
        assert counts["current"] == len(view(desk, user, "current", staff)["items"])
    assert view(desk, "asha", "current")["viewCounts"] == {"current": 2, "declined": 1, "rejected": 1, "travelled": 1}
    assert view(desk, "admin", "current")["viewCounts"] == {"current": 2, "declined": 2, "rejected": 1, "travelled": 1}


def test_scope_rules_are_those_of_the_default_view(desk):
    ended_desk(desk)
    assert ids(view(desk, "ravi", "declined")) == ["c10"]                       # a recruiter sees only their own
    assert sorted(ids(view(desk, "admin", "declined"))) == ["c0", "c10"]        # admin and MD see everyone ...
    assert sorted(ids(view(desk, "manager", "declined"))) == ["c0", "c10"]
    assert ids(view(desk, "admin", "declined", "&staff=ravi")) == ["c10"]       # ... or one recruiter
    as_user(desk, "ravi")
    assert desk.client.get("/api/daily-work?view=declined&staff=asha").status_code == 403
    assert desk.client.get("/api/daily-work?view=bogus").status_code == 422
    assert view(desk, "admin", "declined", "&staff=ravi")["staff"]             # the per-recruiter cards stay


def test_a_yes_call_after_a_decline_takes_the_candidate_back_to_the_current_list_not_the_declined_view(desk):
    apps, _other = ended_desk(desk)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_recorded_at = now() + interval '1 hour', call_called = true, call_interested = 'yes', status = 'new' "
                        "WHERE id = 'c0'"))
        db.commit()
    assert "c0" not in ids(view(desk, "asha", "declined")) and view(desk, "asha", "current")["viewCounts"]["declined"] == 0
    assert "c0" in ids(view(desk, "asha", "current"))


def test_a_candidate_mapped_again_leaves_the_ended_views(desk):
    apps, _other = ended_desk(desk)
    as_user(desk, "asha")
    assert desk.client.post("/api/applications", json={"candidate_id": "c0", "order_id": "order"}).status_code == 201
    assert ids(view(desk, "asha", "declined")) == []
    assert "c0" in ids(view(desk, "asha", "current"))


def test_the_ended_lists_are_capped_and_say_so(desk, monkeypatch):
    from app.services import daily_work
    ended_desk(desk)
    monkeypatch.setattr(daily_work, "MAX_OUTCOME_ROWS", 1)
    body = view(desk, "admin", "declined")
    assert len(body["items"]) == 1 and body["truncated"] is True and body["viewCounts"]["declined"] == 2
    assert view(desk, "asha", "declined")["truncated"] is False


def sheet_texts(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        xml = book.read("xl/worksheets/sheet1.xml").decode()
    return re.findall(r"<t[^>]*>([^<]*)</t>", xml)


def test_the_export_honours_the_view_and_writes_the_outcome_in_the_stage_column(desk):
    ended_desk(desk)
    as_user(desk, "asha")
    response = desk.client.get("/api/daily-work/export?view=declined")
    assert response.status_code == 200
    texts = sheet_texts(response.content)
    assert "Candidate 0" in texts and "Candidate 4" not in texts
    stage = next(t for t in texts if t.startswith("Declined at 5"))
    assert stage == "Declined at 5 · Offer letter - Offer declined by the candidate - by Asha"
    plain = sheet_texts(desk.client.get("/api/daily-work/export").content)
    assert "Candidate 4" in plain and "Candidate 0" not in plain and "Candidate 2" not in plain          # the default export is the unchanged current table
    everything = sheet_texts(desk.client.get("/api/daily-work/export?view=all").content)
    assert {"Candidate 0", "Candidate 2", "Candidate 4"} <= set(everything)


class Counter(list):
    pass


def statements(desk, url, user):
    from sqlalchemy.orm import sessionmaker  # noqa: F401
    engine = desk.sessions.kw["bind"]
    seen = Counter()

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split())[:120])

    event.listen(engine, "before_cursor_execute", record)
    try:
        listing(desk, user, url)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


def test_the_view_counts_cost_one_statement_and_the_ended_views_a_handful(desk):
    ended_desk(desk)
    current = statements(desk, "/api/daily-work", "asha")
    assert 1 <= sum("count(*) FILTER" in s and "crm_applications.status" in s for s in current) <= 1
    assert len(current) <= 6, current     # rows, placements, tasks, facts, remarks and the view counts, all in one round
    declined = statements(desk, "/api/daily-work?view=declined", "asha")
    assert len(declined) <= 6, declined   # counts, view counts, the rows, then remarks, facts and who closed them side by side
    follow_up = statements(desk, "/api/follow-up", "asha")
    assert len(follow_up) <= 4, follow_up


def test_saving_a_remark_is_one_statement_and_keeps_its_refusals(desk):
    apps = mapped_candidates(desk, 2)
    with desk.sessions() as db:
        task_id = db.scalar(text("SELECT id FROM workflow_tasks WHERE application_id = :a"), {"a": apps["c0"]})
    as_user(desk, "asha")
    engine = desk.sessions.kw["bind"]
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = desk.client.post(f"/api/tasks/{task_id}/remarks", json={"body": "  Called, will send documents  "})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 201, response.text
    assert len(seen) == 1, seen
    remark = response.json()
    assert remark["taskId"] == task_id and remark["body"] == "Called, will send documents" and remark["authorId"] == "asha" and remark["createdAt"]
    assert set(remark) == {"id", "taskId", "body", "authorId", "createdAt", "kind"} and remark["kind"] == "remark"
    with desk.sessions() as db:
        assert db.scalar(text("SELECT count(*) FROM system_audit_log WHERE action = 'Added task remark' AND entity_id = :t"), {"t": task_id}) == 1
    assert listing(desk, "asha")["items"][0]["remark"]["body"] == "Called, will send documents"
    as_user(desk, "ravi")                                                    # somebody else's task
    assert desk.client.post(f"/api/tasks/{task_id}/remarks", json={"body": "x"}).status_code == 403
    as_user(desk, "admin")                                                   # a supervisor may
    assert desk.client.post(f"/api/tasks/{task_id}/remarks", json={"body": "Checked"}).status_code == 201
    assert desk.client.post("/api/tasks/nope/remarks", json={"body": "x"}).status_code == 404
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET completed_at = now() WHERE id = :t"), {"t": task_id})
        db.commit()
    assert desk.client.post(f"/api/tasks/{task_id}/remarks", json={"body": "late"}).status_code == 409
    with desk.sessions() as db:   # nothing was written by the refused calls
        assert db.scalar(text("SELECT count(*) FROM workflow_task_remarks WHERE task_id = :t"), {"t": task_id}) == 2


def test_completing_a_stage_locks_in_one_statement_and_writes_each_table_once(desk):
    apps = mapped_candidates(desk, 1)
    as_user(desk, "asha")
    app_id = apps["c0"]
    version = desk.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"]
    saved = desk.client.put(f"/api/applications/{app_id}/draft", json={"data": {"result": "passed", "feedback": "Reviewed."}, "lock_version": version})
    assert saved.status_code == 200, saved.text
    engine = desk.sessions.kw["bind"]
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split())[:60])

    event.listen(engine, "before_cursor_execute", record)
    try:
        done = desk.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": saved.json()["lockVersion"]})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert done.status_code == 200, done.text
    assert done.json()["currentStage"] == 2 and done.json()["nextTaskId"]
    # candidate + order + application locked together (the stand-in actor of this fixture costs two user reads; a real session has it
    # in memory), the attempt number, the open tasks, whether the next stage already has a live completed record (it is then skipped),
    # then one write per table
    assert len(seen) <= 11, seen
    assert sum(s.startswith("SELECT crm_candidates") for s in seen) == 1 and not any(s.startswith("SELECT crm_job_orders") for s in seen), seen
    assert sum(s.startswith("UPDATE crm_applications") for s in seen) == 1, seen
    assert done.status_code == 200 and desk.client.get(f"/api/applications/{app_id}/draft").json()["lockVersion"] == saved.json()["lockVersion"] + 1


# ------------------------------------------------------------------ a rejected candidate is off today's list; every detail shows on the ended tabs

def current_ids(desk, user="asha", extra=""):
    return sorted(ids(view(desk, user, "current", extra)))


def test_a_rejected_candidate_is_on_the_rejected_tab_only_and_the_counts_agree_everywhere(desk):
    ended_desk(desk)
    assert current_ids(desk) == ["c3", "c4"]                       # c1 (rejected at the interview) is no longer a bare "Initial call" row
    assert ids(view(desk, "asha", "rejected")) == ["c1"]
    everything = view(desk, "asha", "all")
    assert sorted(ids(everything)) == ["c0", "c1", "c2", "c3", "c4"] and len(ids(everything)) == len(set(ids(everything)))
    assert {i["candidateId"]: i["outcome"]["kind"] for i in everything["items"]} == {
        "c0": "declined", "c1": "rejected", "c2": "travelled", "c3": "current", "c4": "current"}
    body = view(desk, "asha", "current")
    assert body["summary"] == {"total": 2, "toWork": 2, "doneToday": 0} and body["viewCounts"] == {"current": 2, "declined": 1, "rejected": 1, "travelled": 1}
    # the per-recruiter card, the supervisor's total and the sidebar figure (the same summary) say the same
    card = next(s["summary"] for s in view(desk, "manager", "current")["staff"] if s["userId"] == "asha")
    assert card == {"total": 2, "toWork": 2, "doneToday": 0}
    assert view(desk, "manager", "current")["summary"]["total"] == 2
    with desk.sessions() as db:
        from app.services import daily_work
        assert daily_work.owner_counts(db, ["asha"])["asha"] == (2, 0)
    # the Data Bank keeps them: still a candidate, not removed
    with desk.sessions() as db:
        assert db.scalar(text("SELECT status FROM crm_candidates WHERE id = 'c1'")) == "new"


def test_a_yes_call_after_a_rejection_puts_the_candidate_back_on_todays_list_not_the_rejected_tab(desk):
    ended_desk(desk)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_recorded_at = now() + interval '1 hour', call_called = true, call_interested = 'yes' WHERE id = 'c1'"))
        db.commit()
    assert ids(view(desk, "asha", "rejected")) == [] and view(desk, "asha", "current")["viewCounts"]["rejected"] == 0
    assert "c1" in current_ids(desk) and view(desk, "asha", "current")["summary"]["total"] == 3
    assert sorted(ids(view(desk, "asha", "all"))).count("c1") == 1
    # an older yes (before the closure) or a no does not bring them back
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_recorded_at = now() - interval '30 days', call_interested = 'yes' WHERE id = 'c1'"))
        db.commit()
    assert ids(view(desk, "asha", "rejected")) == ["c1"] and "c1" not in current_ids(desk)


def test_a_rejected_candidate_mapped_to_a_new_job_is_back_on_the_list_and_off_the_rejected_tab(desk):
    ended_desk(desk)
    as_user(desk, "asha")
    assert desk.client.post("/api/applications", json={"candidate_id": "c1", "order_id": "order"}).status_code == 201
    assert ids(view(desk, "asha", "rejected")) == [] and "c1" in current_ids(desk)
    counts = view(desk, "asha", "current")["viewCounts"]
    assert counts["rejected"] == 0 and counts["current"] == 3


def with_details(desk):
    """c1 (rejected at the interview): an agency, a remark on its stage task, a remark on its initial-call task, two notes and a date of commencement."""
    apps, _other = ended_desk(desk)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET source = 'referral', source_detail = 'Gulf Agency' WHERE id = 'c1'"))
        db.execute(text("UPDATE crm_applications SET commencement_date = '2026-12-05' WHERE id = :a"), {"a": apps["c1"]})
        stage_task = db.scalar(text("SELECT id FROM workflow_tasks WHERE application_id = :a ORDER BY created_at DESC LIMIT 1"), {"a": apps["c1"]})
        db.execute(text("INSERT INTO workflow_tasks (id, kind, title, candidate_id, owner_id, completed_at) VALUES ('call-c1', 'initial_call', 'Call', 'c1', 'asha', now())"))
        for rid, task, body, kind, minutes in (("r1", stage_task, "Client wants a younger profile", "remark", 3), ("r2", "call-c1", "Called, interested", "remark", 30),
                                               ("n1", stage_task, "Spoke to the client on Monday", "note", 2), ("n2", "call-c1", "Prefers Qatar", "note", 20)):
            db.execute(text("INSERT INTO workflow_task_remarks (id, task_id, body, author_id, kind, created_at) "
                            "VALUES (:i, :t, :b, 'asha', :k, now() - make_interval(mins => :m))"), {"i": rid, "t": task, "b": body, "k": kind, "m": minutes})
        db.commit()
    return apps


def test_the_rejected_row_carries_the_same_detail_as_a_current_row(desk):
    apps = with_details(desk)
    item = view(desk, "asha", "rejected")["items"][0]
    assert item["candidateId"] == "c1" and item["taskId"] is None
    assert (item["client"], item["position"], item["country"], item["agency"], item["commencementDate"]) == ("Gulf Builders", "Welder", "Qatar", "Gulf Agency", "2026-12-05")
    assert item["remark"]["body"] == "Client wants a younger profile" and item["remark"]["by"] == "Asha"      # the placement's newest remark wins
    assert [n["body"] for n in item["notes"]] == ["Spoke to the client on Monday", "Prefers Qatar"]            # notes of the placement and of the initial call, newest first
    assert [n["body"] for n in item["todayNotes"]] == []       # written at real "now", the frozen agency day is another one
    assert item["notes"][0]["by"] == "Asha" and item["notes"][0]["byId"] == "asha"
    # without a remark on the placement the initial call's remark shows, and a cleared remark is no remark
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_task_remarks SET deleted_at = now() WHERE id = 'r1'"))
        db.commit()
    assert view(desk, "asha", "rejected")["items"][0]["remark"]["body"] == "Called, interested"
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_task_remarks SET cleared_at = now() WHERE id = 'r2'"))
        db.commit()
    assert view(desk, "asha", "rejected")["items"][0]["remark"] is None
    # every tab that lists the row carries the same keys as a current row
    current_keys = set(view(desk, "asha", "current")["items"][0])
    current_keys -= {"stageMovedAt", "calledAt"}      # what was done today: nothing is done on an ended placement
    for name in ("declined", "rejected", "travelled", "all"):
        for row in view(desk, "asha", name)["items"]:
            assert current_keys <= set(row), (name, current_keys - set(row))


def test_the_ended_views_still_cost_a_handful_of_statements_with_the_notes(desk):
    with_details(desk)
    rejected = statements(desk, "/api/daily-work?view=rejected", "asha")
    assert len(rejected) <= 6, rejected
    everything = statements(desk, "/api/daily-work?view=all", "asha")
    assert len(everything) <= 10, everything   # today's five reads, view counts, the ended rows, then remarks / facts / who closed them side by side


def ended_app(desk):
    apps = with_details(desk)
    return apps


def test_remarks_notes_and_doc_are_written_on_an_ended_placement_and_read_back_on_every_tab(desk):
    apps = ended_app(desk)
    app_id = apps["c1"]
    as_user(desk, "asha")
    with desk.sessions() as db:
        before = db.execute(text("SELECT status, closure_reason, updated_at, current_stage FROM crm_applications WHERE id = :a"), {"a": app_id}).one()
        tasks_before = db.scalar(text("SELECT count(*) FROM workflow_tasks"))
        open_before = db.scalar(text("SELECT count(*) FROM workflow_tasks WHERE completed_at IS NULL AND cancelled_at IS NULL"))
    remark = desk.client.post(f"/api/applications/{app_id}/ended-remarks", json={"body": "  Waiting for the next opening  "})
    assert remark.status_code == 201, remark.text
    assert remark.json()["body"] == "Waiting for the next opening" and remark.json()["kind"] == "remark" and remark.json()["authorId"] == "asha"
    note = desk.client.post(f"/api/applications/{app_id}/ended-remarks", json={"body": "Called the agency", "kind": "note"})
    assert note.status_code == 201 and note.json()["kind"] == "note"
    for name in ("rejected", "all"):
        row = next(i for i in view(desk, "asha", name)["items"] if i["candidateId"] == "c1")
        assert row["remark"]["body"] == "Waiting for the next opening"
        assert row["notes"][0]["body"] == "Called the agency" and row["notes"][0]["id"] == note.json()["id"]
    # the placement is untouched: still closed for the same reason at the same time, nothing reopened, no open task made, off today's list
    with desk.sessions() as db:
        after = db.execute(text("SELECT status, closure_reason, updated_at, current_stage FROM crm_applications WHERE id = :a"), {"a": app_id}).one()
        assert tuple(after) == tuple(before)
        assert db.scalar(text("SELECT count(*) FROM workflow_tasks WHERE completed_at IS NULL AND cancelled_at IS NULL")) == open_before
        assert db.scalar(text("SELECT count(*) FROM workflow_tasks")) == tasks_before            # the placement's own task held them
        assert db.scalar(text("SELECT count(*) FROM system_audit_log WHERE action IN ('Added task remark', 'Added task note') AND actor_id = 'asha'")) >= 2
    assert "c1" not in current_ids(desk) and ids(view(desk, "asha", "rejected")) == ["c1"]
    # the notes can be deleted like any note, and "Select remark" clears the remark cell (the notes stay)
    assert desk.client.delete(f"/api/remarks/{note.json()['id']}").status_code == 200
    assert "Called the agency" not in [n["body"] for n in view(desk, "asha", "rejected")["items"][0]["notes"]]
    cleared = desk.client.post(f"/api/applications/{app_id}/ended-remarks/clear")
    assert cleared.status_code == 200 and cleared.json()["cleared"] >= 2
    row = view(desk, "asha", "rejected")["items"][0]
    assert row["remark"] is None and [n["body"] for n in row["notes"]] == ["Spoke to the client on Monday", "Prefers Qatar"]


def test_the_date_of_commencement_is_saved_on_an_ended_placement_without_moving_its_closure_time(desk):
    from app.api.routers import application_form as form_router
    apps = ended_app(desk)
    desk.app.include_router(form_router.router)
    as_user(desk, "asha")
    with desk.sessions() as db:
        closed_at = db.scalar(text("SELECT updated_at FROM crm_applications WHERE id = :a"), {"a": apps["c1"]})
    saved = desk.client.put(f"/api/candidates/c1/application-form?applicationId={apps['c1']}", json={"application": {"commencementDate": "2026-12-20"}})
    assert saved.status_code == 200, saved.text
    row = view(desk, "asha", "rejected")["items"][0]
    assert row["commencementDate"] == "2026-12-20"
    with desk.sessions() as db:
        assert db.scalar(text("SELECT updated_at FROM crm_applications WHERE id = :a"), {"a": apps["c1"]}) == closed_at
        assert db.scalar(text("SELECT status FROM crm_applications WHERE id = :a"), {"a": apps["c1"]}) == "closed"
    # a candidate whose yes call is older than the closure is therefore still parked (the DOC edit did not make the closure newer)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_candidates SET call_recorded_at = :at, call_called = true, call_interested = 'yes' WHERE id = 'c1'"), {"at": closed_at + timedelta(seconds=-5)})
        db.commit()
    assert "c1" not in current_ids(desk)


def test_who_may_write_on_an_ended_placement(desk):
    apps = ended_app(desk)
    url = f"/api/applications/{apps['c1']}/ended-remarks"
    as_user(desk, "ravi")                                                    # another recruiter's candidate
    assert desk.client.post(url, json={"body": "x"}).status_code == 403
    assert desk.client.post(url + "/clear").status_code == 403
    for supervisor in ("admin", "manager"):                                  # admin and MD may
        as_user(desk, supervisor)
        assert desk.client.post(url, json={"body": f"by {supervisor}", "kind": "note"}).status_code == 201
    as_user(desk, "asha")
    assert desk.client.post(url, json={"body": ""}).status_code == 422
    assert desk.client.post("/api/applications/nope/ended-remarks", json={"body": "x"}).status_code == 404
    # an ACTIVE placement is written through its task, not here
    active = apps["c4"]
    assert desk.client.post(f"/api/applications/{active}/ended-remarks", json={"body": "x"}).status_code == 409
    notes = [n["body"] for n in view(desk, "asha", "rejected")["items"][0]["notes"]]
    assert "by admin" in notes and "by manager" in notes and "x" not in notes


def test_a_travelled_or_declined_placement_without_any_task_gets_a_cancelled_holder_task(desk):
    apps, _other = ended_desk(desk)
    with desk.sessions() as db:
        db.execute(text("DELETE FROM workflow_tasks WHERE application_id = :a"), {"a": apps["c2"]})
        db.commit()
    as_user(desk, "asha")
    response = desk.client.post(f"/api/applications/{apps['c2']}/ended-remarks", json={"body": "Flew on 12 Nov"})
    assert response.status_code == 201, response.text
    with desk.sessions() as db:
        holder = db.execute(text("SELECT cancelled_at, completed_at, application_id FROM workflow_tasks WHERE id = :t"), {"t": response.json()["taskId"]}).one()
        assert holder.cancelled_at is not None and holder.completed_at is None and holder.application_id == apps["c2"]
    row = view(desk, "asha", "travelled")["items"][0]
    assert row["candidateId"] == "c2" and row["remark"]["body"] == "Flew on 12 Nov"


# ------------------------------------------------------------------------------------------------ a candidate who says no before any placement


def test_a_candidate_who_says_no_at_the_initial_call_with_no_placement_goes_to_the_declined_tab_at_once(desk):
    from test_daily_work import record_call
    owned_candidates(desk, 2)   # u100, u101: never matched to an order
    as_user(desk, "asha")
    assert desk.client.get("/api/daily-work").json()["viewCounts"] == {"current": 2, "declined": 0, "rejected": 0, "travelled": 0}
    assert record_call(desk, "u100", interested="no").status_code == 200
    body = view(desk, "asha", "current")
    assert ids(body) == ["u101"] and body["viewCounts"] == {"current": 1, "declined": 1, "rejected": 0, "travelled": 0}
    declined = view(desk, "asha", "declined")
    assert ids(declined) == ["u100"] and view(desk, "asha", "rejected")["items"] == []
    row = declined["items"][0]
    assert row["applicationId"] is None and row["taskId"] is None and row["stage"] is None and row["status"] == "closed"
    assert row["client"] is None and row["orderRef"] is None and row["position"] == "Mason" and row["positionIsPreferred"] is True
    assert row["outcome"]["kind"] == "declined" and row["outcome"]["label"] == "Declined" and row["outcome"]["stage"] is None
    assert row["outcome"]["reason"] == "Candidate not interested at the initial call" and row["outcome"]["at"] is not None
    everything = view(desk, "asha", "all")
    assert sorted(ids(everything)) == ["u100", "u101"] and len(ids(everything)) == len(set(ids(everything)))   # once each, never also on today's list
    # the per-recruiter figure the supervisor sees and the sidebar number agree
    assert view(desk, "manager", "current")["viewCounts"]["declined"] == 1
    with desk.sessions() as db:
        from app.services import daily_work
        assert daily_work.owner_counts(db, ["asha"])["asha"] == (1, 0)


def test_a_yes_call_after_a_no_without_a_placement_takes_the_candidate_off_the_declined_tab_and_back_to_the_list(desk):
    from test_daily_work import record_call
    owned_candidates(desk, 1)
    as_user(desk, "asha")
    record_call(desk, "u100", interested="no")
    assert ids(view(desk, "asha", "declined")) == ["u100"] and ids(view(desk, "asha", "current")) == []
    record_call(desk, "u100", interested="yes")
    assert ids(view(desk, "asha", "declined")) == [] and ids(view(desk, "asha", "current")) == ["u100"]
    assert view(desk, "asha", "current")["viewCounts"] == {"current": 1, "declined": 0, "rejected": 0, "travelled": 0}


def test_a_no_after_a_placement_that_was_only_unmapped_is_declined_but_a_placement_outcome_is_never_counted_twice(desk):
    from test_daily_work import record_call
    apps = mapped_candidates(desk, 2)
    as_user(desk, "asha")
    set_application(desk, apps["c0"], status="closed", reason="unmapped")
    record_call(desk, "c0", interested="no")
    # c1 was rejected at the interview earlier AND says no on a later call: still one row, on the Rejected tab only
    to_stage(desk, apps["c1"], 4)
    advance(desk, apps["c1"], REJECTED_AT_INTERVIEW)
    record_call(desk, "c1", interested="no")
    counts = view(desk, "asha", "current")["viewCounts"]
    assert ids(view(desk, "asha", "declined")) == ["c0"] and ids(view(desk, "asha", "rejected")) == ["c1"]
    assert counts["declined"] == 1 and counts["rejected"] == 1
    assert sorted(ids(view(desk, "asha", "all"))) == ["c0", "c1"]
    # the unmapped placement is not an outcome: c0 reads as a plain 'said no at the call' row (Declined, no stage, no order), not as 'Rejected / Unmapped'
    declined = view(desk, "asha", "declined")["items"][0]["outcome"]
    assert declined["kind"] == "declined" and declined["label"] == "Declined" and declined["stage"] is None
    assert declined["reason"] == "Candidate not interested at the initial call"


def test_a_later_unreachable_call_does_not_take_a_declined_candidate_off_every_tab(desk):
    from test_daily_work import record_call
    owned_candidates(desk, 2)
    as_user(desk, "asha")
    assert record_call(desk, "u100", interested="no").status_code == 200
    assert ids(view(desk, "asha", "declined")) == ["u100"]
    assert record_call(desk, "u100", interested="unreachable", called=False).status_code == 200
    # still Declined (and counted once), never on Current again and never on no tab at all
    assert ids(view(desk, "asha", "declined")) == ["u100"] and "u100" not in ids(view(desk, "asha", "current"))
    assert view(desk, "asha", "current")["viewCounts"]["declined"] == 1
    # an interested answer still brings them back
    assert record_call(desk, "u100", interested="yes").status_code == 200
    assert ids(view(desk, "asha", "declined")) == [] and "u100" in ids(view(desk, "asha", "current"))


def test_choosing_not_interested_on_the_application_form_declines_the_candidate_like_the_call_dialog(desk):
    from app.api.routers import application_form
    desk.app.include_router(application_form.router)   # the Tasks desk does not mount the form's router
    owned_candidates(desk, 2)
    as_user(desk, "asha")
    url = "/api/candidates/u100/application-form"
    saved = desk.client.put(url, json={"screening": {"interestLevel": "not_interested"}})
    assert saved.status_code == 200, saved.text
    assert ids(view(desk, "asha", "current")) == ["u101"] and ids(view(desk, "asha", "declined")) == ["u100"]
    with desk.sessions() as db:
        row = db.execute(text("SELECT status, call_called, call_interested, call_recorded_at IS NOT NULL FROM crm_candidates WHERE id = 'u100'")).one()
        assert tuple(row) == ("not_interested", True, "no", True)
        assert db.scalar(text("SELECT count(*) FROM system_audit_log WHERE entity_id = 'u100' AND action = 'Recorded initial call' AND meta->>'source' = 'application_form'")) == 1
    # saving the same level again changes nothing and records no second call
    desk.client.put(url, json={"screening": {"interestLevel": "not_interested"}})
    with desk.sessions() as db:
        assert db.scalar(text("SELECT count(*) FROM system_audit_log WHERE entity_id = 'u100' AND action = 'Recorded initial call'")) == 1
    # an interested level brings them back to today's list and off the Declined tab
    assert desk.client.put(url, json={"screening": {"interestLevel": "interested"}}).status_code == 200
    assert sorted(ids(view(desk, "asha", "current"))) == ["u100", "u101"] and ids(view(desk, "asha", "declined")) == []
    # a level other than Not interested never touches a candidate who was not declined
    assert desk.client.put("/api/candidates/u101/application-form", json={"screening": {"interestLevel": "more_interested"}}).status_code == 200
    assert "u101" in ids(view(desk, "asha", "current"))
