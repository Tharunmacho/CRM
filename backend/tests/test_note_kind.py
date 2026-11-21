"""Notes are not remarks: `workflow_task_remarks.kind` ('remark' = the Remarks select's value, 'note' = typed in the NOTES column).

Real PostgreSQL (the disposable test database only), with the desk fixture of test_daily_work. A note never becomes the candidate's current
remark, a remark never appears in NOTES, clearing the Remarks cell leaves the notes, deleting works for both kinds, and the NOTES column
carries ALL of a candidate's notes (all days) newest first.
"""
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Remark
from app.services import daily_work, tasks_export
from test_daily_work import (  # noqa: F401  (desk is a fixture)
    TUESDAY, as_user, desk, follow, listing, mapped_candidates,
)
from test_database_naming import _migrate, schema_database  # noqa: F401
from test_remark_notes import TODAY, audits, delete, only_row, put, stored

PREVIOUS = "0054_soft_delete_files_fb"
THIS = "0055_remark_kind"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0055_remark_kind.py"


def add(desk, user, task, body, **extra):
    as_user(desk, user)
    return desk.client.post(f"/api/tasks/{task}/remarks", json={"body": body, **extra})


def bodies(notes):
    return [note["body"] for note in notes]


# ------------------------------------------------------------------------------------------------ writing

def test_the_default_is_a_remark_and_a_note_is_stored_as_a_note(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    remark = add(desk, "asha", task, "Visa in progress")
    assert remark.status_code == 201 and remark.json()["kind"] == "remark"
    note = add(desk, "asha", task, "Spoke to his brother, will send the passport", kind="note")
    assert note.status_code == 201 and note.json()["kind"] == "note"
    assert stored_kind(desk, remark.json()["id"]) == "remark" and stored_kind(desk, note.json()["id"]) == "note"
    explicit = add(desk, "asha", task, "Again", kind="remark")
    assert explicit.status_code == 201 and explicit.json()["kind"] == "remark"
    assert add(desk, "asha", task, "x", kind="comment").status_code == 422
    with desk.sessions() as db:   # the audit says which of the two was written
        assert db.scalar(text("SELECT count(*) FROM system_audit_log WHERE action = 'Added task note'")) == 1
        assert db.scalar(text("SELECT count(*) FROM system_audit_log WHERE action = 'Added task remark'")) == 2


def stored_kind(desk, remark_id):
    with desk.sessions() as db:
        return db.scalar(text("SELECT kind FROM workflow_task_remarks WHERE id = :i"), {"i": remark_id})


def test_a_note_follows_the_same_permission_and_the_same_closed_task_rule_as_a_remark(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    assert add(desk, "ravi", task, "Not my candidate", kind="note").status_code == 403
    assert add(desk, "admin", task, "An admin may", kind="note").status_code == 201
    assert add(desk, "manager", task, "An MD may", kind="note").status_code == 201
    assert add(desk, "asha", "no-such-task", "x", kind="note").status_code == 404
    with desk.sessions() as db:
        db.execute(text("UPDATE workflow_tasks SET completed_at = now() WHERE id = :t"), {"t": task})
        db.commit()
    assert add(desk, "asha", task, "Too late", kind="note").status_code == 409


# ------------------------------------------------------------------------------------------------ reading

def test_a_note_is_never_the_current_remark_and_a_remark_is_never_a_note(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    assert add(desk, "asha", task, "Documents received").status_code == 201
    only_note = add(desk, "asha", task, "Typed note", kind="note").json()["id"]
    row = only_row(desk)
    assert row["remark"]["body"] == "Documents received" and row["remark"]["count"] == 1     # the note did not become the remark
    assert bodies(row["notes"]) == ["Typed note"]   # the remark is not a note
    assert row["notes"][0]["id"] == only_note and row["notes"][0]["byId"] == "asha"

    # a candidate with a note and no remark has an empty Remarks cell
    mapped_candidates(desk, 1, owner="asha", start=5)
    others = [item for item in listing(desk, "asha")["items"] if item["candidateId"] == "c5"][0]
    add(desk, "asha", others["taskId"], "Only a note", kind="note")
    again = [item for item in listing(desk, "asha")["items"] if item["candidateId"] == "c5"][0]
    assert again["remark"] is None and bodies(again["notes"]) == ["Only a note"]
    with desk.sessions() as db:   # the Excel: Remark empty, Notes filled
        sheet = tasks_export.table(db, listing(desk, "asha")["items"], [], TODAY)
    line = next(r for r in sheet[1:] if r[7] == "Candidate 5")
    assert line[13] == "" and "Only a note" in line[14]
    other = next(r for r in sheet[1:] if r[7] == "Candidate 0")
    assert other[13] == "Documents received" and "Documents received" not in other[14] and "Typed note" in other[14]


def test_the_notes_are_all_of_them_newest_first_every_day_and_every_task_of_the_candidate(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    older = put(desk, task, "Last week", kind="note", minutes=-7 * 24 * 60)
    middle = put(desk, task, "Yesterday", kind="note", minutes=-24 * 60)
    newest = put(desk, task, "Just now", kind="note", minutes=5)
    for note_id in (older, middle, newest):
        assert stored_kind(desk, note_id) == "note"
    row = only_row(desk)
    assert [n["id"] for n in row["notes"]] == [newest, middle, older]
    assert [n["id"] for n in row["todayNotes"]] == [newest]              # the TODAY chip and the Excel's Today cell read today's only
    with desk.sessions() as db:
        cell = tasks_export.table(db, listing(desk, "asha")["items"], [], TODAY)[1][14]
    assert cell.splitlines() == ["08:35 Asha - Just now", "02 Nov 08:30 Asha - Yesterday", "27 Oct 08:30 Asha - Last week"]


def test_follow_up_rows_carry_the_notes_too_and_clearing_keeps_them(desk):
    apps = mapped_candidates(desk, 1)
    with desk.sessions() as db:
        db.execute(text("UPDATE crm_applications SET current_stage = 3 WHERE id = :a"), {"a": apps["c0"]})
        db.commit()
    item = follow(desk, "asha")["items"][0]
    put(desk, item["taskId"], "Documents received")
    keep = put(desk, item["taskId"], "A typed note", kind="note", minutes=1)
    as_user(desk, "asha")
    assert desk.client.post(f"/api/tasks/{item['taskId']}/remarks/clear").json() == {"cleared": 1}   # the remark only
    row = follow(desk, "asha")["items"][0]
    assert row["remark"] is None and [n["id"] for n in row["notes"]] == [keep]
    assert stored(desk, keep).cleared_at is None


def test_notes_are_capped_per_candidate(desk, monkeypatch):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    monkeypatch.setattr(daily_work, "NOTE_CAP", 3)
    for n in range(5):
        put(desk, task, f"Note {n}", kind="note", minutes=n)
    assert bodies(only_row(desk)["notes"]) == ["Note 4", "Note 3", "Note 2"]


# ------------------------------------------------------------------------------------------------ deleting and the statement count

def test_a_note_is_deleted_softly_by_the_same_rule_as_any_remark(desk):
    mapped_candidates(desk, 1)
    task = only_row(desk)["taskId"]
    mine = put(desk, task, "Mine", kind="note")
    theirs = put(desk, task, "Admin's", kind="note", author="admin", minutes=1)
    assert delete(desk, "ravi", mine).status_code == 403
    assert delete(desk, "asha", theirs).status_code == 200            # the candidate's recruiter
    assert delete(desk, "admin", mine).status_code == 200
    assert only_row(desk)["notes"] == []
    assert stored(desk, mine).deleted_at is not None and stored(desk, mine).deleted_by == "admin"
    assert {e.meta["text"] for e in audits(desk, "Deleted a note")} == {"Mine", "Admin's"}


def test_the_rows_read_the_log_in_the_same_statements_whatever_the_notes(desk):
    mapped_candidates(desk, 3)
    rows = listing(desk, "asha")["items"]
    for row in rows:
        for n in range(4):
            put(desk, row["taskId"], f"{row['candidate']} note {n}", kind="note", minutes=n, key=f"{row['candidateId']}-{n}")
    seen = []
    record = lambda conn, cur, statement, *rest: seen.append(" ".join(statement.split()))  # noqa: E731
    engine = desk.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        listing(desk, "asha")
        with_notes = len([s for s in seen if "workflow_task_remarks" in s])
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert with_notes == 1      # one read of the log for the whole list, not one per row


# ------------------------------------------------------------------------------------------------ the migration

def columns(engine):
    return {c["name"]: c for c in inspect(engine).get_columns("workflow_task_remarks")}


def run(engine, sql, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)


def seed(engine):
    run(engine, "INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Recruiter', 'u1@example.test', 'staff')")
    run(engine, "INSERT INTO crm_candidates (id, ref, name, phone, source, owner_id) VALUES ('c1', 'CND-1', 'Cand', '9000000001', 'walk_in', 'u1')")
    run(engine, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, owner_id) VALUES ('t1', 'initial_call', 'Call', 'c1', 'u1')")


def test_the_migration_keeps_every_existing_row_a_remark_and_is_idempotent(schema_database):
    spec = importlib.util.spec_from_file_location("m0055", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == THIS and module.down_revision == PREVIOUS and len(THIS) <= 32
    engine = schema_database
    _migrate(engine, PREVIOUS)
    assert "kind" not in columns(engine)
    seed(engine)
    run(engine, "INSERT INTO workflow_task_remarks (id, task_id, body, author_id) VALUES ('old', 't1', 'Written before', 'u1')")
    _migrate(engine, THIS)
    found = columns(engine)["kind"]
    assert not found["nullable"] and "remark" in str(found["default"]) and found["comment"]
    with engine.begin() as c:
        assert c.execute(text("SELECT kind FROM workflow_task_remarks WHERE id = 'old'")).scalar() == "remark"   # nothing was rewritten
        c.execute(text("INSERT INTO workflow_task_remarks (id, task_id, body, author_id) VALUES ('new', 't1', 'Old client', 'u1')"))
        assert c.execute(text("SELECT kind FROM workflow_task_remarks WHERE id = 'new'")).scalar() == "remark"   # an older writer stays a remark
        c.execute(text("INSERT INTO workflow_task_remarks (id, task_id, body, author_id, kind) VALUES ('n', 't1', 'A note', 'u1', 'note')"))
    with pytest.raises(IntegrityError):
        run(engine, "INSERT INTO workflow_task_remarks (id, task_id, body, author_id, kind) VALUES ('bad', 't1', 'x', 'u1', 'comment')")
    with engine.begin() as c:   # the body of the migration again: harmless, the notes stay notes
        with Operations.context(MigrationContext.configure(c)):
            module.upgrade()
        assert dict(c.execute(text("SELECT id, kind FROM workflow_task_remarks")).all()) == {"old": "remark", "new": "remark", "n": "note"}


def test_the_downgrade_drops_the_column_and_an_empty_database_goes_up_and_down(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    _migrate(engine, PREVIOUS, downgrade=True)
    assert "kind" not in columns(engine)
    _migrate(engine, THIS)
    assert "kind" in columns(engine)
    seed(engine)
    run(engine, "INSERT INTO workflow_task_remarks (id, task_id, body, author_id, kind) VALUES ('n', 't1', 'A note', 'u1', 'note')")
    _migrate(engine, PREVIOUS, downgrade=True)
    with engine.begin() as c:
        assert c.execute(text("SELECT body FROM workflow_task_remarks")).scalars().all() == ["A note"]   # a note reads as a remark again
