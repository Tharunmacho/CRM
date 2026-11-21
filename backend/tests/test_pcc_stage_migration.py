"""Migration 0048: PCC becomes stage 7 and stages 7..11 become 8..12.

Real PostgreSQL (isolated test database only). Applications are seeded at stages 3, 6, 7, 9 and 11 (plus a closed one) with their
stage records, tasks, documents, notes, daily-work items, audit entries and an escalation notice exactly as 0047 left them; then
0047 -> 0048 is run and every stored stage number, the carried PCC data and the range checks are asserted; then the downgrade is
asserted to merge PCC back and shift down, and upgrading again restores the PCC records. An empty database and a second run of the
migration body are no-ops.
"""
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from test_database_naming import _migrate, schema_database  # noqa: F401

PREVIOUS = "0047_agency_active_flag"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0048_pcc_stage.py"
OLD_TASKS = {1: "Prescreen the candidate", 3: "Schedule the interview", 6: "Complete the medical", 7: "Collect original passport",
             8: "Collect the advance", 9: "Follow up the visa"}


def execute(connection, sql, **params):
    connection.execute(text(sql), params)


def seed(engine):
    """Applications and everything that stores a stage number, in the 0047 (eleven stage) numbering."""
    with engine.begin() as c:
        execute(c, "INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Recruiter', 'u1@example.com', 'staff')")
        execute(c, "INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')")
        execute(c, "INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')")
        execute(c, "INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')")
        execute(c, "INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)")
        apps = {  # name: (old current stage, status, closure reason)
            "a3": (3, "active", None), "a6": (6, "active", None), "a7": (7, "active", None), "a9": (9, "active", None),
            "a11": (11, "deployed", None), "ac": (8, "closed", "advance_not_paid"), "ac6": (6, "closed", "medical_unfit"),
        }
        for index, (name, (stage, status, closure)) in enumerate(apps.items()):
            execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES (:id, :ref, :id, :phone, 'walk_in')",
                    id=f"c-{name}", ref=f"CND-{name}", phone=f"9000000020{index}")
            execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status, closure_reason, draft) "
                       "VALUES (:id, :cand, 'order', :stage, CAST(:status AS application_status), CAST(:closure AS closure_reason), CAST(:draft AS jsonb))",
                    id=name, cand=f"c-{name}", stage=stage, status=status, closure=closure,
                    draft=json.dumps({"copy": {"id": "f1"}} if name == "a7" else {"applied": "yes"} if name == "a6" else {}))

        def record(app, stage, attempt, outcome, data=None, reopened=None, day=1):
            execute(c, "INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, outcome, completed_at, reopened_reason, owner_id) "
                       "VALUES (:id, :app, :stage, :attempt, CAST(:data AS jsonb), :outcome, CAST(:at AS timestamptz), :reopened, 'u1')",
                    id=f"s-{app}-{stage}-{attempt}", app=app, stage=stage, attempt=attempt, outcome=outcome, data=json.dumps(data or {}),
                    at=f"2026-0{min(day, 9)}-01 10:00:00+00", reopened=reopened)

        for name, upto in (("a3", 2), ("a6", 5), ("a7", 6), ("a9", 8), ("a11", 11), ("ac", 7), ("ac6", 5)):
            for number in range(1, upto + 1):
                record(name, number, 1, f"outcome-{number}", {"feedback": f"{name} stage {number}"}, day=number)
        # a6 is at the medical and has recorded one retest.
        record("a6", 6, 1, "Unfit: reapplying", {"applied": "yes", "result": "unfit", "reapplying": "yes", "retestDate": "2026-12-01"}, day=6)
        record("ac6", 6, 1, "Unfit", {"result": "unfit"}, day=6)
        # a7's medical: Fit, no PCC field of any kind (what every real record looks like).
        for name in ("a7", "a9", "ac"):
            execute(c, "UPDATE crm_application_stages SET outcome = 'Fit', data = CAST(:d AS jsonb) WHERE id = :id",
                    d=json.dumps({"result": "fit", "feedback": "fit"}), id=f"s-{name}-6-1")
        # a11's medical hold PCC fields (only possible if written by hand or by a downgrade): they must be carried over.
        execute(c, "UPDATE crm_application_stages SET outcome = 'Fit', data = CAST(:d AS jsonb) WHERE id = 's-a11-6-1'",
                d=json.dumps({"result": "fit", "pccStatus": "received", "pccReceivedDate": "2026-03-01", "feedback": "fit"}))
        # a9's passport was reopened once: attempt 1 superseded, attempt 2 current.
        execute(c, "UPDATE crm_application_stages SET reopened_reason = 'wrong copy' WHERE id = 's-a9-7-1'")
        record("a9", 7, 2, "Collected", {"collected": "yes"}, day=7)
        record("ac", 8, 1, "Not paid", {"stop": "not_paid"}, day=8)
        execute(c, "UPDATE crm_application_stages SET data = CAST(:d AS jsonb) WHERE id = 's-a11-11-1'",
                d=json.dumps({"travelDate": "2026-09-12", "file": {"id": "f2"}}))
        execute(c, "UPDATE crm_application_stages SET amount = 5000, amount_currency = 'INR' WHERE id = 's-a9-8-1'")

        for name, stage, title, done in (("a3", 3, OLD_TASKS[3], False), ("a6", 6, OLD_TASKS[6], False), ("a7", 7, OLD_TASKS[7], False),
                                         ("a9", 9, OLD_TASKS[9], False), ("a9", 8, OLD_TASKS[8], True)):
            execute(c, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, application_id, stage, owner_id, completed_at) "
                       "VALUES (:id, 'stage', :title, :cand, :app, :stage, 'u1', CASE WHEN :done THEN now() END)",
                    id=f"t-{name}-{stage}", title=title, cand=f"c-{name}", app=name, stage=stage, done=done)
        execute(c, "INSERT INTO workflow_daily_work_items (id, owner_id, work_date, task_id, application_id, candidate_id, stage, slot_no, session, due_at, kind) "
                   "VALUES ('d1', 'u1', '2026-11-01', 't-a7-7', 'a7', 'c-a7', 7, 1, 'morning', now(), 'stage')")
        execute(c, "INSERT INTO crm_application_feedback (id, candidate_id, application_id, author_id, kind, body, stage) VALUES "
                   "('n1', 'c-a7', 'a7', 'u1', 'update', 'passport is slow', 7), ('n2', 'c-a3', 'a3', 'u1', 'update', 'interview soon', 3)")
        for app, slot in (("a7", "stage_7_copy"), ("a9", "stage_8_receipt"), ("a11", "stage_9_file"), ("a11", "stage_10_file"),
                          ("a11", "stage_11_file"), ("a11", "stage_8_receipt"), ("a3", "stage_5_file")):
            execute(c, "INSERT INTO document_attachments (id, entity_type, entity_id, slot, version, file_name, object_key) "
                       "VALUES (:id, 'application', :app, :slot, 1, 'f.pdf', :key)", id=f"att-{app}-{slot}", app=app, slot=slot, key=f"k/{app}/{slot}")
        execute(c, "INSERT INTO document_attachments (id, entity_type, entity_id, slot, version, file_name, object_key) "
                   "VALUES ('att-cand', 'candidate', 'c-a7', 'passport', 1, 'p.pdf', 'k/passport')")
        for action, meta, entity in (
            ("Saved stage 7 draft", {"stage": 7, "lock_version": 3}, "application"),
            ("Recorded stage 11: Booked", {"stage": 11, "attempt": 1, "outcome": "Booked"}, "application"),
            ("Recorded stage 3: Scheduled", {"stage": 3, "attempt": 1}, "application"),
            ("Reopened stage 7", {"stage": 7, "reason": "wrong copy", "superseded_attempts": [
                {"stage": 7, "attempt": 1}, {"stage": 8, "attempt": 1}, {"stage": 3, "attempt": 1}]}, "application"),
            ("Imported candidate with documents and stage history", {"bulk_import": True, "completed_stages": 11}, "application"),
            ("Imported candidate with documents and stage history", {"bulk_import": True, "completed_stages": 5}, "application"),
            ("Changed a setting", {"stage": 9}, "setting"),
        ):
            execute(c, "INSERT INTO system_audit_log (id, action, entity_type, meta) VALUES (gen_random_uuid()::text, :a, :e, CAST(:m AS jsonb))",
                    a=action, e=entity, m=json.dumps(meta))
        execute(c, "INSERT INTO system_notifications (id, user_id, kind, title, detail) VALUES "
                   "('nt1', 'u1', 'escalation', 'Overdue', 'Asha · Stage 9 · Follow up the visa · Owner: Ravi · 50 hours overdue'), "
                   "('nt2', 'u1', 'escalation', 'Overdue', 'Asha · Stage 3 · Schedule the interview · Owner: Ravi · 50 hours overdue')")


def apps(engine):
    with engine.begin() as c:
        return {row[0]: (row[1], row[2], row[3]) for row in c.execute(text("SELECT id, current_stage, status::text, closure_reason::text FROM crm_applications"))}


def records(engine, application):
    with engine.begin() as c:
        rows = c.execute(text("SELECT stage, attempt, outcome, data, reopened_reason, amount, completed_at FROM crm_application_stages "
                              "WHERE application_id = :a ORDER BY stage, attempt"), {"a": application}).all()
    return {(r[0], r[1]): {"outcome": r[2], "data": r[3], "reopened": r[4], "amount": r[5], "at": r[6]} for r in rows}


def numbers(engine, application):
    return sorted(records(engine, application))


def slots(engine, entity_id):
    with engine.begin() as c:
        return sorted(row[0] for row in c.execute(text("SELECT slot FROM document_attachments WHERE entity_id = :e"), {"e": entity_id}))


def column(engine, sql, **params):
    with engine.begin() as c:
        return c.execute(text(sql), params).scalars().all()


def query(engine, sql, **params):
    with engine.begin() as c:
        return [tuple(row) for row in c.execute(text(sql), params).all()]


def test_upgrade_renumbers_every_stored_stage_number_and_carries_the_pcc_data(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    before = {name: records(engine, name) for name in ("a3", "a6", "a7", "a9", "a11", "ac", "ac6")}
    _migrate(engine, "0048_pcc_stage")

    # Applications: those at stage >= 7 move up one; the ones at 3 and 6 (and the one closed at 6) stay.
    assert apps(engine) == {"a3": (3, "active", None), "a6": (6, "active", None), "a7": (8, "active", None), "a9": (10, "active", None),
                            "a11": (12, "deployed", None), "ac": (9, "closed", "advance_not_paid"), "ac6": (6, "closed", "medical_unfit")}

    # Stage records: 1..6 untouched, old 7..11 are now 8..12, and everything past stage 6 has a stage-7 PCC record.
    assert numbers(engine, "a3") == [(1, 1), (2, 1)]
    assert numbers(engine, "a6") == [(n, 1) for n in range(1, 7)]
    assert numbers(engine, "ac6") == [(n, 1) for n in range(1, 7)]
    assert numbers(engine, "a7") == [(n, 1) for n in range(1, 8)]
    assert numbers(engine, "a9") == [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (7, 1), (8, 1), (8, 2), (9, 1)]
    assert numbers(engine, "a11") == [(n, 1) for n in range(1, 13)]
    assert numbers(engine, "ac") == [(n, 1) for n in range(1, 10)]
    after_a9, after_a11 = records(engine, "a9"), records(engine, "a11")
    # The passport attempts (old 7) keep their content, their reopening and their attempt numbers under stage 8.
    assert after_a9[(8, 1)]["reopened"] == "wrong copy" and after_a9[(8, 2)]["outcome"] == "Collected"
    assert after_a9[(9, 1)]["amount"] == 5000 and after_a9[(9, 1)]["data"] == before["a9"][(8, 1)]["data"]
    assert after_a11[(12, 1)]["data"] == {"travelDate": "2026-09-12", "file": {"id": "f2"}} and after_a11[(8, 1)]["data"] == before["a11"][(7, 1)]["data"]
    for name in ("a3", "a6", "ac6"):                         # nothing at or before stage 6 moved
        assert records(engine, name) == before[name]

    # PCC: an application past the medical gets a completed record with no data when the medical had no PCC field ...
    for name in ("a7", "a9", "ac"):
        pcc = records(engine, name)[(7, 1)]
        assert pcc["data"] == {} and pcc["outcome"] == "Completed manually" and pcc["reopened"] is None
        assert pcc["at"] == records(engine, name)[(6, 1)]["at"]                 # the timeline stays in order
        assert records(engine, name)[(6, 1)]["data"] == before[name][(6, 1)]["data"]
    # ... and the PCC fields of the old stage-6 record when it had them, moved off the medical record.
    carried = after_a11[(7, 1)]
    assert carried["data"] == {"pccStatus": "received", "pccReceivedDate": "2026-03-01"} and carried["outcome"] == "Received"
    assert after_a11[(6, 1)]["data"] == {"result": "fit", "feedback": "fit"}
    # The medical of an application still at stage 6 keeps what it has and gets no PCC record.
    assert records(engine, "a6")[(6, 1)]["data"]["retestDate"] == "2026-12-01"

    # Tasks, daily work, notes.
    tasks = dict(query(engine, "SELECT id, stage FROM workflow_tasks"))
    assert tasks == {"t-a3-3": 3, "t-a6-6": 6, "t-a7-7": 8, "t-a9-9": 10, "t-a9-8": 9}
    assert column(engine, "SELECT stage FROM workflow_daily_work_items") == [8]
    assert dict(query(engine, "SELECT id, stage FROM crm_application_feedback")) == {"n1": 8, "n2": 3}
    # Stage documents follow their stage; candidate documents and untouched stages do not move.
    assert slots(engine, "a7") == ["stage_8_copy"] and slots(engine, "a9") == ["stage_9_receipt"] and slots(engine, "a3") == ["stage_5_file"]
    assert slots(engine, "a11") == sorted(["stage_9_receipt", "stage_10_file", "stage_11_file", "stage_12_file"])
    assert slots(engine, "c-a7") == ["passport"]
    # Audit trail and the stored escalation notice.
    audit = {(row[0], json.dumps(row[1], sort_keys=True)) for row in query(engine, "SELECT action, meta FROM system_audit_log")}
    assert ("Saved stage 8 draft", json.dumps({"stage": 8, "lock_version": 3}, sort_keys=True)) in audit
    assert ("Recorded stage 12: Booked", json.dumps({"stage": 12, "attempt": 1, "outcome": "Booked"}, sort_keys=True)) in audit
    assert ("Recorded stage 3: Scheduled", json.dumps({"stage": 3, "attempt": 1}, sort_keys=True)) in audit
    assert ("Reopened stage 8", json.dumps({"stage": 8, "reason": "wrong copy", "superseded_attempts": [
        {"stage": 8, "attempt": 1}, {"stage": 9, "attempt": 1}, {"stage": 3, "attempt": 1}]}, sort_keys=True)) in audit
    assert ("Imported candidate with documents and stage history", json.dumps({"bulk_import": True, "completed_stages": 12}, sort_keys=True)) in audit
    assert ("Imported candidate with documents and stage history", json.dumps({"bulk_import": True, "completed_stages": 5}, sort_keys=True)) in audit
    assert ("Changed a setting", json.dumps({"stage": 9}, sort_keys=True)) in audit                  # not a stage entry: left alone
    assert dict(query(engine, "SELECT id, detail FROM system_notifications")) == {
        "nt1": "Asha · Stage 10 · Follow up the visa · Owner: Ravi · 50 hours overdue",
        "nt2": "Asha · Stage 3 · Schedule the interview · Owner: Ravi · 50 hours overdue"}


def test_range_checks_allow_twelve_stages_and_no_more(schema_database):
    engine = schema_database
    _migrate(engine, "0048_pcc_stage")
    with engine.begin() as c:
        execute(c, "INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')")
        execute(c, "INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')")
        execute(c, "INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')")
        execute(c, "INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)")
        execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('c', 'CND-1', 'c', '90000000300', 'walk_in')")
        execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) VALUES ('a', 'c', 'order', 12, 'deployed')")
        execute(c, "INSERT INTO crm_application_stages (id, application_id, stage, attempt) VALUES ('s12', 'a', 12, 1)")
        for sql in ("UPDATE crm_applications SET current_stage = 13", "INSERT INTO crm_application_stages (id, application_id, stage, attempt) VALUES ('s13', 'a', 13, 1)",
                    "UPDATE crm_applications SET current_stage = 0"):
            with pytest.raises(IntegrityError):
                with c.begin_nested():
                    execute(c, sql)
        assert "pcc_not_cleared" in c.execute(text("SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                                                   "WHERE t.typname = 'closure_reason'")).scalars().all()                    # the PCC exit exists


def test_an_empty_database_migrates_both_ways(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _migrate(engine, "0048_pcc_stage")
    assert column(engine, "SELECT count(*) FROM crm_application_stages") == [0]
    _migrate(engine, PREVIOUS, downgrade=True)
    with engine.begin() as c:
        execute(c, "INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')")
        execute(c, "INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')")
        execute(c, "INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')")
        execute(c, "INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)")
        execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('c', 'CND-1', 'c', '90000000301', 'walk_in')")
        with pytest.raises(IntegrityError):
            with c.begin_nested():
                execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) VALUES ('a', 'c', 'order', 12, 'active')")


def _body(name):
    spec = importlib.util.spec_from_file_location("migration_0048", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def test_running_the_migration_body_again_on_converted_data_changes_nothing(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "0048_pcc_stage")
    snapshot = ({name: records(engine, name) for name in ("a7", "a9", "a11")}, apps(engine), slots(engine, "a11"))
    upgrade = _body("upgrade")
    with engine.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            upgrade()
    assert ({name: records(engine, name) for name in ("a7", "a9", "a11")}, apps(engine), slots(engine, "a11")) == snapshot


def test_downgrade_merges_pcc_back_and_shifts_down_and_upgrading_again_restores_it(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    original = {name: records(engine, name) for name in ("a3", "a6", "a7", "a9", "a11", "ac", "ac6")}
    _migrate(engine, "0048_pcc_stage")

    # Work done on the new stage 7 after the upgrade: an application at PCC (open task, draft, document, note, audit), another
    # one closed at PCC, and a real PCC answer on a9.
    with engine.begin() as c:
        for name, index in (("p1", 1), ("p2", 2)):
            execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES (:id, :ref, :id, :phone, 'walk_in')",
                    id=f"c-{name}", ref=f"CND-{name}", phone=f"9000000040{index}")
        execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status, draft, draft_saved_at) "
                   "VALUES ('p1', 'c-p1', 'order', 7, 'active', CAST(:d AS jsonb), now())", d=json.dumps({"pccStatus": "pending"}))
        execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status, closure_reason) "
                   "VALUES ('p2', 'c-p2', 'order', 7, 'closed', 'pcc_not_cleared')")
        execute(c, "INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, outcome) VALUES "
                   "('s-p1-6', 'p1', 6, 1, '{\"result\": \"fit\"}', 'Fit'), ('s-p2-6', 'p2', 6, 1, '{\"result\": \"fit\"}', 'Fit'), "
                   "('s-p2-7', 'p2', 7, 1, '{\"stop\": \"not_cleared\"}', 'Not cleared')")
        execute(c, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, application_id, stage, owner_id) VALUES "
                   "('t-p1', 'stage', 'Collect the police clearance (PCC)', 'c-p1', 'p1', 7, 'u1')")
        execute(c, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, application_id, stage, owner_id, completed_at) VALUES "
                   "('t-p2', 'stage', 'Collect the police clearance (PCC)', 'c-p2', 'p2', 7, 'u1', now())")
        execute(c, "INSERT INTO workflow_daily_work_items (id, owner_id, work_date, task_id, application_id, candidate_id, stage, slot_no, session, due_at, kind) "
                   "VALUES ('d-p1', 'u1', '2026-11-01', 't-p1', 'p1', 'c-p1', 7, 1, 'morning', now(), 'stage')")
        execute(c, "INSERT INTO crm_application_feedback (id, candidate_id, application_id, author_id, kind, body, stage) VALUES "
                   "('n-p1', 'c-p1', 'p1', 'u1', 'update', 'PCC applied', 7)")
        execute(c, "INSERT INTO document_attachments (id, entity_type, entity_id, slot, version, file_name, object_key) VALUES "
                   "('att-p1', 'application', 'p1', 'stage_7_file', 1, 'pcc.pdf', 'k/pcc')")
        execute(c, "UPDATE crm_application_stages SET outcome = 'PCC pending', data = CAST(:d AS jsonb) WHERE application_id = 'a9' AND stage = 7",
                d=json.dumps({"pccStatus": "pending", "pccAppliedDate": "2026-04-02", "feedback": "applied"}))
        execute(c, "INSERT INTO system_audit_log (id, action, entity_type, meta) VALUES ('au-pcc', 'Recorded stage 7: PCC pending', 'application', "
                   "CAST(:m AS jsonb))", m=json.dumps({"stage": 7, "attempt": 1}))
    # The stage-7 record the migration made for a9 now holds a real answer.
    pcc_a9 = records(engine, "a9")[(7, 1)]
    assert pcc_a9["data"]["pccStatus"] == "pending"

    _migrate(engine, PREVIOUS, downgrade=True)

    assert apps(engine) == {"a3": (3, "active", None), "a6": (6, "active", None), "a7": (7, "active", None), "a9": (9, "active", None),
                            "a11": (11, "deployed", None), "ac": (8, "closed", "advance_not_paid"), "ac6": (6, "closed", "medical_unfit"),
                            "p1": (7, "active", None), "p2": (6, "closed", "unmapped")}
    for name in ("a3", "a6", "ac6"):
        assert records(engine, name) == original[name]
    for name in ("a7", "a9", "a11", "ac"):                               # same stage numbers and attempts as before the upgrade
        assert sorted(records(engine, name)) == sorted(original[name]), name
        for key in original[name]:
            if key != (6, 1):
                assert records(engine, name)[key]["data"] == original[name][key]["data"], (name, key)
    assert numbers(engine, "p1") == [(6, 1)] and numbers(engine, "p2") == [(6, 1)]
    # The PCC answers were nested in the effective stage-6 record (nothing for the empty ones); the other fields did not change.
    six = {name: records(engine, name)[(6, 1)]["data"] for name in ("a7", "a9", "a11", "ac")}
    assert six["a7"] == original["a7"][(6, 1)]["data"] and six["ac"] == original["ac"][(6, 1)]["data"]
    assert six["a11"]["pcc"] == {"pccStatus": "received", "pccReceivedDate": "2026-03-01", "outcome": "Received"}
    assert six["a9"]["pcc"] == {"pccStatus": "pending", "pccAppliedDate": "2026-04-02", "feedback": "applied", "outcome": "PCC pending"}
    # Tasks: the open PCC task is the Passport task of an application now at stage 7; the old numbers are back.
    with engine.begin() as c:
        tasks = {row[0]: (row[1], row[2]) for row in c.execute(text("SELECT id, stage, title FROM workflow_tasks"))}
        assert tasks["t-p1"] == (7, "Collect original passport") and tasks["t-p2"] == (6, "Collect the police clearance (PCC)")
        assert tasks["t-a7-7"][0] == 7 and tasks["t-a9-9"][0] == 9 and tasks["t-a9-8"][0] == 8
        assert dict(c.execute(text("SELECT id, stage FROM workflow_daily_work_items")).all()) == {"d1": 7, "d-p1": 7}
        assert dict(c.execute(text("SELECT id, stage FROM crm_application_feedback")).all()) == {"n1": 7, "n2": 3, "n-p1": 6}
        draft = c.execute(text("SELECT draft, draft_saved_at FROM crm_applications WHERE id = 'p1'")).one()
        assert draft[0] == {} and draft[1] is None                                        # the PCC draft cannot be shown by the old form
        assert c.execute(text("SELECT draft FROM crm_applications WHERE id = 'a7'")).scalar_one() == {"copy": {"id": "f1"}}
    assert slots(engine, "a7") == ["stage_7_copy"] and slots(engine, "a9") == ["stage_8_receipt"] and slots(engine, "p1") == ["stage_6_pcc"]
    assert sorted(slots(engine, "a11")) == sorted(["stage_8_receipt", "stage_9_file", "stage_10_file", "stage_11_file"])
    audit = {row[0] for row in query(engine, "SELECT action FROM system_audit_log")}
    assert {"Saved stage 7 draft", "Recorded stage 11: Booked", "Reopened stage 7", "Recorded stage 6: PCC pending"} <= audit
    with engine.begin() as c:
        with pytest.raises(IntegrityError):                                                # the eleven-stage checks are back
            with c.begin_nested():
                execute(c, "UPDATE crm_applications SET current_stage = 12 WHERE id = 'a3'")
        meta = {row[0]: row[1] for row in c.execute(text("SELECT action, meta FROM system_audit_log WHERE action IN ('Reopened stage 7', 'Imported candidate with documents and stage history')"))}
        assert meta["Reopened stage 7"]["superseded_attempts"] == [{"stage": 7, "attempt": 1}, {"stage": 8, "attempt": 1}, {"stage": 3, "attempt": 1}]
        assert dict(c.execute(text("SELECT id, detail FROM system_notifications")).all())["nt1"].startswith("Asha · Stage 9 · ")

    # Upgrading again restores the PCC records from the nested answers.
    _migrate(engine, "0048_pcc_stage")
    restored = records(engine, "a9")[(7, 1)]
    assert restored["data"] == {"pccStatus": "pending", "pccAppliedDate": "2026-04-02", "feedback": "applied"} and restored["outcome"] == "PCC pending"
    assert records(engine, "a11")[(7, 1)]["data"] == {"pccStatus": "received", "pccReceivedDate": "2026-03-01"}
    assert records(engine, "a11")[(6, 1)]["data"] == {"result": "fit", "feedback": "fit"}
    assert records(engine, "a7")[(7, 1)]["data"] == {} and records(engine, "a7")[(7, 1)]["outcome"] == "Completed manually"
    assert apps(engine)["a7"] == (8, "active", None) and apps(engine)["a11"] == (12, "deployed", None)
    assert slots(engine, "p1") == ["stage_7_file"] and slots(engine, "a7") == ["stage_8_copy"]
