"""Deleting a note (`DELETE /api/remarks/{id}`) and clearing the Remarks cell (`POST /api/tasks/{id}/remarks/clear`, "Select remark").

Both are soft: the row stays (deleted_at / deleted_by, cleared_at), an audit entry says who and when. A deleted note is invisible to every
reader (the Tasks rows and their Excel, Follow-up, the profile, the exports, the update checks); a cleared one only stops being the shown
remark. Runs on the explicitly guarded disposable PostgreSQL DB, with the desk fixture of test_daily_work.
"""
from datetime import timedelta

from sqlalchemy import event, select, text

from app.core.roles import ADMIN_LEVEL_VALUES  # noqa: F401  (documents which roles are supervisors)
from app.db.models import AuditEntry, Remark, User
from app.services import change_feed, client_tracker, desk_snapshot, staff_directory, tasks_export
from test_daily_work import (  # noqa: F401  (desk is a fixture)
    TUESDAY, TZ, as_user, desk, follow, ids, listing, mapped_candidates,
)
from test_database_naming import schema_database  # noqa: F401  (fixture used by desk)
from test_daily_work_views import sheet_texts

TODAY = "2026-11-03"


def only_row(desk, user="asha"):
    return listing(desk, user)["items"][0]


def row_remark(row):
    return row["remark"]["body"], row["remark"]["count"]


def put(desk, task_id, body, *, author="asha", minutes=0, key=None, kind="remark"):
    """A remark (the Remarks select's) or, with kind="note", a note of the NOTES column, written on the agency day (08:30 on Tuesday + minutes),
    straight into the table; its id is returned."""
    key = key or f"note-{body.lower().replace(' ', '-')}"
    with desk.sessions() as db:
        db.add(Remark(id=key, task_id=task_id, body=body, author_id=author, created_at=TUESDAY + timedelta(minutes=minutes), kind=kind))
        db.commit()
    return key


def delete(desk, user, remark_id):
    as_user(desk, user)
    return desk.client.delete(f"/api/remarks/{remark_id}")


def clear(desk, user, task_id):
    as_user(desk, user)
    return desk.client.post(f"/api/tasks/{task_id}/remarks/clear")


def stored(desk, remark_id):
    with desk.sessions() as db:
        return db.execute(text("SELECT deleted_at, deleted_by, cleared_at FROM workflow_task_remarks WHERE id = :i"), {"i": remark_id}).one_or_none()


def audits(desk, action):
    with desk.sessions() as db:
        return list(db.scalars(select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.at, AuditEntry.id)))


# ------------------------------------------------------------------------------------------------ who may delete a note

def test_the_author_the_candidates_recruiter_and_supervisors_may_delete_but_nobody_else(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    mine = put(desk, task, "Called asha", author="asha")
    from_manager = put(desk, task, "Check the passport", author="manager", minutes=5)   # asha is the candidate's recruiter, not the author
    from_ravi = put(desk, task, "Ravi looked in", author="ravi", minutes=10)           # ravi wrote it on someone else's candidate
    by_admin = put(desk, task, "Admin note", author="admin", minutes=15)

    stranger = delete(desk, "ravi", mine)
    assert stranger.status_code == 403 and "can delete" in stranger.json()["detail"]
    assert stored(desk, mine).deleted_at is None and audits(desk, "Deleted a note") == []

    assert delete(desk, "asha", mine).status_code == 200                      # the author (and the recruiter)
    assert delete(desk, "asha", from_manager).status_code == 200              # the candidate's recruiter, not the author
    assert delete(desk, "ravi", from_ravi).status_code == 200                 # the author, though not the recruiter
    assert delete(desk, "manager", by_admin).status_code == 200               # an MD
    assert delete(desk, "admin", "no-such-note").status_code == 404
    assert delete(desk, "admin", mine).status_code == 404                     # already gone
    assert len(audits(desk, "Deleted a note")) == 4


def test_an_admin_may_delete_any_note(desk):
    mapped_candidates(desk, 1, owner="ravi")
    task = only_row(desk, "ravi")["taskId"]
    note = put(desk, task, "Ravi note", author="ravi")
    assert delete(desk, "admin", note).json() == {"deleted": note}


# ------------------------------------------------------------------------------------------------ the soft delete

def test_a_deleted_note_stays_in_the_table_with_who_and_when_and_the_audit_keeps_its_text(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    note = put(desk, task, "Wrong person, ignore this", author="asha")
    assert delete(desk, "manager", note).status_code == 200
    row = stored(desk, note)
    assert row.deleted_at is not None and row.deleted_by == "manager" and row.cleared_at is None
    (entry,) = audits(desk, "Deleted a note")
    assert entry.actor_id == "manager" and entry.entity_type == "task" and entry.entity_id == task and entry.target
    assert entry.meta["remarkId"] == note and entry.meta["text"] == "Wrong person, ignore this" and entry.meta["writtenBy"] == "asha"


def test_a_deleted_note_leaves_the_row_the_count_the_export_and_follow_up(desk):
    apps = mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    first = put(desk, task, "First call done")
    second = put(desk, task, "Second call: interested", minutes=30)
    row = only_row(desk)
    n_first = put(desk, task, "Note one", minutes=1, kind="note")
    n_second = put(desk, task, "Note two", minutes=31, kind="note")
    assert row_remark(only_row(desk)) == ("Second call: interested", 2)
    row = only_row(desk)
    assert [n["body"] for n in row["notes"]] == ["Note two", "Note one"] and [n["id"] for n in row["todayNotes"]] == [n_second, n_first]
    assert {n["byId"] for n in row["notes"]} == {"asha"}          # the screen decides from this who gets a "Delete note" button

    assert delete(desk, "asha", second).status_code == 200
    row = only_row(desk)
    assert row["remark"]["body"] == "First call done" and row["remark"]["count"] == 1
    assert delete(desk, "asha", n_second).status_code == 200
    assert [n["id"] for n in only_row(desk)["notes"]] == [n_first]
    xlsx = sheet_texts(desk.client.get("/api/daily-work/export").content)
    assert "First call done" in " ".join(xlsx) and "Second call" not in " ".join(xlsx) and "Note two" not in " ".join(xlsx)

    assert delete(desk, "asha", first).status_code == 200
    assert delete(desk, "asha", n_first).status_code == 200
    row = only_row(desk)
    assert row["remark"] is None and row["todayNotes"] == [] and row["notes"] == []
    with desk.sessions() as db:
        sheet = tasks_export.table(db, listing(desk, "asha")["items"], [], TODAY)
    assert sheet[1][13] == "" and sheet[1][14] == ""        # Remark and Notes columns of the Excel

    # Follow-up (stage 3 placement): the last update falls back when the only note is deleted, the remark is gone.
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_applications SET current_stage = 3 WHERE id = :a"), {"a": apps["c0"]})
        db.commit()
    followed = follow(desk, "asha")["items"][0]
    noted = put(desk, followed["taskId"], "Interview booked", key="note-follow")
    assert follow(desk, "asha")["items"][0]["remark"]["body"] == "Interview booked"
    assert delete(desk, "asha", noted).status_code == 200
    gone = follow(desk, "asha")["items"][0]
    assert gone["remark"] is None and gone["todayNotes"] == [] and gone["notes"] == []
    assert gone["lastUpdateAt"] == followed["lastUpdateAt"]


def test_a_deleted_note_is_gone_from_the_profile_the_record_export_the_client_tracker_and_the_update_checks(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    keep = put(desk, task, "Keep me")
    drop = put(desk, task, "Delete me", minutes=1)
    with desk.sessions() as db:
        assert {r["id"] for r in desk_snapshot.read_snapshot(db, "asha")["remarks"]} == {keep, drop}
    assert delete(desk, "asha", drop).status_code == 200
    with desk.sessions() as db:
        assert {r["id"] for r in desk_snapshot.read_snapshot(db, "asha")["remarks"]} == {keep}
        assert {r["id"] for r in desk_snapshot.read_snapshot(db, "manager", compact=True)["remarks"]} == {keep}
        from app.services import candidate_exports
        assert [r.id for r in candidate_exports.load_inspection(db, "c0", db.get(User, "asha")).remarks] == [keep]
        assert [r["remarks"] for r in client_tracker._read_rows(db, db.get(User, "manager"))] == ["Keep me"]
    assert delete(desk, "asha", keep).status_code == 200
    with desk.sessions() as db:
        assert desk_snapshot.read_snapshot(db, "asha")["remarks"] == []
        assert [r["remarks"] for r in client_tracker._read_rows(db, db.get(User, "manager"))] == [""]
        # The row is still there for whoever audits the table.
        assert db.scalar(text("SELECT count(*) FROM workflow_task_remarks WHERE deleted_at IS NOT NULL")) == 2


def test_a_deleted_note_is_not_a_daily_update(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    note = put(desk, task, "Updated today", author="asha")
    with desk.sessions() as db:
        before = {r["id"]: r for r in staff_directory.staff_directory(db, TZ, now=TUESDAY + timedelta(hours=1))["staff"]}["asha"]
    assert before["missingUpdates"] == 0
    assert delete(desk, "asha", note).status_code == 200
    with desk.sessions() as db:
        after = {r["id"]: r for r in staff_directory.staff_directory(db, TZ, now=TUESDAY + timedelta(hours=1))["staff"]}["asha"]
    assert after["missingUpdates"] == 1


def test_deleting_and_clearing_are_one_statement_and_announce_a_tasks_change(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    note = put(desk, task, "One statement")
    seen = []
    record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))  # noqa: E731
    engine = desk.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert clear(desk, "asha", task).json() == {"cleared": 1}
        cleared_sql = list(seen)
        seen.clear()
        assert delete(desk, "asha", note).status_code == 200
        deleted_sql = list(seen)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(cleared_sql) == 1 and len(deleted_sql) == 1, (cleared_sql, deleted_sql)
    for sql in (cleared_sql[0], deleted_sql[0]):
        assert ("UPDATE", "workflow_task_remarks") in change_feed.classify_statements(sql)
    assert change_feed.TOPIC_TABLES["workflow_task_remarks"] == ("tasks",)


# ------------------------------------------------------------------------------------------------ "Select remark": clear the cell

def test_clearing_empties_the_cell_and_the_excel_remark_but_keeps_every_note_in_the_history(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    older = put(desk, task, "Ringing")
    newer = put(desk, task, "Not interested now", minutes=10)
    typed = put(desk, task, "Typed in the NOTES column", minutes=11, kind="note")
    assert only_row(desk)["remark"]["body"] == "Not interested now"

    response = clear(desk, "asha", task)
    assert response.status_code == 200 and response.json() == {"cleared": 2}
    row = only_row(desk)
    assert row["remark"] is None                                              # the cell reads "Select remark" again
    assert [n["id"] for n in row["notes"]] == [typed]                         # a typed note is untouched by clearing the remark
    with desk.sessions() as db:
        sheet = tasks_export.table(db, listing(desk, "asha")["items"], [], TODAY)
        assert sheet[1][13] == ""                                              # the Excel's Remark column is empty
        assert "Typed in the NOTES column" in sheet[1][14]                     # ...and Notes still lists the note
        assert {r["id"] for r in desk_snapshot.read_snapshot(db, "asha")["remarks"]} == {older, newer, typed}   # the profile history is intact
        assert [r["remarks"] for r in client_tracker._read_rows(db, db.get(User, "manager"))] == [""]
    assert stored(desk, older).cleared_at is not None and stored(desk, older).deleted_at is None and stored(desk, typed).cleared_at is None
    (entry,) = audits(desk, "Cleared task remark")
    assert entry.actor_id == "asha" and entry.entity_id == task and entry.meta == {"remarks": 2}

    # Nothing left to clear: nothing is written, nothing is audited.
    assert clear(desk, "asha", task).json() == {"cleared": 0}
    assert len(audits(desk, "Cleared task remark")) == 1

    # A remark written afterwards works as before and is the shown one.
    as_user(desk, "asha")
    added = desk.client.post(f"/api/tasks/{task}/remarks", json={"body": "Back on the job"})
    assert added.status_code == 201, added.text
    assert only_row(desk)["remark"]["body"] == "Back on the job" and only_row(desk)["remark"]["count"] == 1
    assert clear(desk, "manager", task).json() == {"cleared": 1}            # an MD clears it too
    assert only_row(desk)["remark"] is None


def test_clearing_follows_the_same_permission_as_adding_a_remark(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    put(desk, task, "Ringing")
    refused = clear(desk, "ravi", task)
    assert refused.status_code == 403
    assert only_row(desk)["remark"]["body"] == "Ringing"
    assert clear(desk, "admin", "no-such-task").status_code == 404
    assert clear(desk, "admin", task).status_code == 200


def test_clearing_hides_the_placements_remark_in_follow_up_too(desk):
    apps = mapped_candidates(desk, 1)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_applications SET current_stage = 3 WHERE id = :a"), {"a": apps["c0"]})
        db.commit()
    item = follow(desk, "asha")["items"][0]
    put(desk, item["taskId"], "Documents received")
    assert follow(desk, "asha")["items"][0]["remark"]["body"] == "Documents received"
    assert clear(desk, "asha", item["taskId"]).json() == {"cleared": 1}
    assert follow(desk, "asha")["items"][0]["remark"] is None
    assert ids(follow(desk, "asha")) == ["c0"]
