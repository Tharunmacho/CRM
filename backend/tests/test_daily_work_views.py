"""Tasks page views: `GET /api/daily-work?view=current|declined|rejected|travelled|all` and the `outcome` of every item.

Runs on the explicitly guarded disposable PostgreSQL DB (see test_database_naming._guard_test_url), with the same desk fixture as
test_daily_work. The first candidates are driven through the real stage endpoints, so the closures are the ones production writes.
"""
import io
import re
import zipfile

import pytest
from sqlalchemy import event, text

from test_daily_work import (  # noqa: F401  (desk is a fixture)
    TUESDAY, advance, as_user, desk, ids, listing, mapped_candidates, owned_candidates, set_application, to_stage,
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
    # The rejected and the unmapped candidates are back in the pool, so (as before) they stay on today's list; the decline does not.
    assert set(ids(plain)) == {"c1", "c3", "c4"}
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


def test_all_view_is_today_plus_the_three_and_lists_a_rejected_candidate_on_todays_list_once(desk):
    ended_desk(desk)
    body = view(desk, "asha", "all")
    assert sorted(ids(body)) == ["c0", "c1", "c2", "c3", "c4"]
    kinds = {item["candidateId"]: item["outcome"]["kind"] for item in body["items"]}
    # c1 (rejected, back in the pool) is on today's list: once, as current; c3 was only unmapped, which is no outcome.
    assert kinds == {"c4": "current", "c3": "current", "c1": "current", "c0": "declined", "c2": "travelled"}
    assert len(ids(body)) == len(set(ids(body)))


def test_view_counts_agree_with_the_lists_in_every_scope(desk):
    ended_desk(desk)
    for user, staff in (("asha", ""), ("ravi", ""), ("admin", ""), ("manager", ""), ("admin", "&staff=asha"), ("admin", "&staff=ravi")):
        counts = view(desk, user, "current", staff)["viewCounts"]
        assert set(counts) == {"current", "declined", "rejected", "travelled"}
        for name in ("declined", "rejected", "travelled"):
            assert counts[name] == len(view(desk, user, name, staff)["items"]), (user, staff, name)
        assert counts["current"] == len(view(desk, user, "current", staff)["items"])
    assert view(desk, "asha", "current")["viewCounts"] == {"current": 3, "declined": 1, "rejected": 1, "travelled": 1}
    assert view(desk, "admin", "current")["viewCounts"] == {"current": 3, "declined": 2, "rejected": 1, "travelled": 1}


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
