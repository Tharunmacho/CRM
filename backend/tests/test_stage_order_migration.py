"""Migration 0056: the new stage order (Advance 6, Passport 7, Medical 8, PCC 9) and the Google review as stage 11.

Real PostgreSQL (isolated test database only). Applications are seeded in the 0055 (twelve stage) numbering: one at stage 3, one on each
of the four reordered stages (old 6, 7, 8, 9), one at the visa, one at the stamping, a deployed one and one closed at the advance. Then
0055 -> 0056 is run and every stored stage number is asserted (records, current stage, tasks, daily work, notes, documents, audit trail,
escalation notices, the range checks), the in-flight rule (first unfinished stage of the new 6..9) and the Google review records;
then the downgrade is asserted to put everything back, and a second run of the migration body is a no-op.
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

PREVIOUS = "0055_remark_kind"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0056_stage_order_google_review.py"
OLD_TASKS = {3: "Schedule the interview", 6: "Complete the medical", 7: "Collect the police clearance (PCC)", 8: "Collect original passport",
             9: "Collect the advance", 10: "Follow up the visa", 11: "Get the visa stamped"}
APPS = {  # name: (old current stage, status, closure reason, records 1..N)
    "a3": (3, "active", None, 2), "b6": (6, "active", None, 5), "b7": (7, "active", None, 6), "b8": (8, "active", None, 7),
    "b9": (9, "active", None, 8), "b10": (10, "active", None, 9), "b11": (11, "active", None, 10), "d12": (12, "deployed", None, 12),
    "cl": (9, "closed", "advance_not_paid", 9),
}


def execute(connection, sql, **params):
    connection.execute(text(sql), params)


def seed(engine):
    with engine.begin() as c:
        execute(c, "INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Recruiter', 'u1@example.com', 'staff')")
        execute(c, "INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')")
        execute(c, "INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')")
        execute(c, "INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')")
        execute(c, "INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)")
        drafts = {"b6": {"applied": "yes"}, "b8": {"copy": {"id": "f1"}}, "b9": {"amount": 100}}
        for index, (name, (stage, status, closure, _upto)) in enumerate(APPS.items()):
            execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES (:id, :ref, :id, :phone, 'walk_in')",
                    id=f"c-{name}", ref=f"CND-{name}", phone=f"9000000050{index}")
            execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status, closure_reason, draft) "
                       "VALUES (:id, :cand, 'order', :stage, CAST(:status AS application_status), CAST(:closure AS closure_reason), CAST(:draft AS jsonb))",
                    id=name, cand=f"c-{name}", stage=stage, status=status, closure=closure, draft=json.dumps(drafts.get(name, {})))
        for name, (_stage, _status, _closure, upto) in APPS.items():
            for number in range(1, upto + 1):
                execute(c, "INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, outcome, completed_at, owner_id) "
                           "VALUES (:id, :app, :n, 1, CAST(:data AS jsonb), :outcome, CAST(:at AS timestamptz), 'u1')",
                        id=f"s-{name}-{number}", app=name, n=number, data=json.dumps({"feedback": f"{name} stage {number}"}),
                        outcome=f"outcome-{number}", at=f"2026-01-{number:02d} 10:00:00+00")
        # The visa of b11 carried the Google review tick; the stamping of d12 carries its file; the advance has an amount.
        execute(c, "UPDATE crm_application_stages SET data = CAST(:d AS jsonb) WHERE id = 's-b11-10'", d=json.dumps({"arrived": "yes", "googleReview": True}))
        execute(c, "UPDATE crm_application_stages SET amount = 5000, amount_currency = 'INR' WHERE id = 's-b9-9'")
        execute(c, "UPDATE crm_application_stages SET outcome = 'Not paid' WHERE id = 's-cl-9'")
        for name, stage, done in (("a3", 3, False), ("b6", 6, False), ("b7", 7, False), ("b8", 8, False), ("b9", 9, False), ("b10", 10, False),
                                  ("b11", 11, False), ("b9", 8, True)):
            execute(c, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, application_id, stage, owner_id, completed_at) "
                       "VALUES (:id, 'stage', :title, :cand, :app, :stage, 'u1', CASE WHEN :done THEN now() END)",
                    id=f"t-{name}-{stage}", title=OLD_TASKS.get(stage, "x"), cand=f"c-{name}", app=name, stage=stage, done=done)
        execute(c, "INSERT INTO workflow_daily_work_items (id, owner_id, work_date, task_id, application_id, candidate_id, stage, slot_no, session, due_at, kind) "
                   "VALUES ('d1', 'u1', '2026-11-01', 't-b8-8', 'b8', 'c-b8', 8, 1, 'morning', now(), 'stage')")
        execute(c, "INSERT INTO crm_application_feedback (id, candidate_id, application_id, author_id, kind, body, stage) VALUES "
                   "('n1', 'c-b8', 'b8', 'u1', 'update', 'passport is slow', 8), ('n2', 'c-a3', 'a3', 'u1', 'update', 'interview soon', 3)")
        for app, slot in (("b8", "stage_8_copy"), ("b9", "stage_9_receipt"), ("d12", "stage_7_file"), ("d12", "stage_10_file"),
                          ("d12", "stage_11_file"), ("d12", "stage_12_file"), ("a3", "stage_5_file")):
            execute(c, "INSERT INTO document_attachments (id, entity_type, entity_id, slot, version, file_name, object_key) "
                       "VALUES (:id, 'application', :app, :slot, 1, 'f.pdf', :key)", id=f"att-{app}-{slot}", app=app, slot=slot, key=f"k/{app}/{slot}")
        for action, meta in (
            ("Recorded stage 9: Collected", {"stage": 9, "attempt": 1}), ("Recorded stage 11: Stamped", {"stage": 11, "attempt": 1}),
            ("Recorded stage 3: Scheduled", {"stage": 3, "attempt": 1}),
            ("Imported candidate with documents and stage history", {"bulk_import": True, "completed_stages": 12}),
            ("Imported candidate with documents and stage history", {"bulk_import": True, "completed_stages": 5}),
        ):
            execute(c, "INSERT INTO system_audit_log (id, action, entity_type, meta) VALUES (gen_random_uuid()::text, :a, 'application', CAST(:m AS jsonb))",
                    a=action, m=json.dumps(meta))
        execute(c, "INSERT INTO system_notifications (id, user_id, kind, title, detail) VALUES "
                   "('nt1', 'u1', 'escalation', 'Overdue', 'Asha · Stage 11 · Get the visa stamped · Owner: Ravi · 50 hours overdue')")


def query(engine, sql, **params):
    with engine.begin() as c:
        return [tuple(row) for row in c.execute(text(sql), params).all()]


def apps(engine):
    return {row[0]: (row[1], row[2]) for row in query(engine, "SELECT id, current_stage, status::text FROM crm_applications")}


def numbers(engine, application):
    return [row[0] for row in query(engine, "SELECT stage FROM crm_application_stages WHERE application_id = :a ORDER BY stage, attempt", a=application)]


def record(engine, application, stage):
    return query(engine, "SELECT outcome, data, amount, completed_at FROM crm_application_stages WHERE application_id = :a AND stage = :s",
                 a=application, s=stage)[0]


def slots(engine, entity):
    return sorted(row[0] for row in query(engine, "SELECT slot FROM document_attachments WHERE entity_id = :e", e=entity))


def test_upgrade_renumbers_everything_and_puts_in_flight_candidates_on_their_first_unfinished_stage(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "head")

    # Current stage: renumbered, except the active ones on a reordered stage, who go to the first of 6..9 they have not completed (6).
    assert apps(engine) == {"a3": (3, "active"), "b6": (6, "active"), "b7": (6, "active"), "b8": (6, "active"), "b9": (6, "active"),
                            "b10": (10, "active"), "b11": (12, "active"), "d12": (13, "deployed"), "cl": (6, "closed")}
    # Stage records follow the map (old 6->8, 7->9, 8->7, 9->6, 11->12, 12->13); the new stage 11 exists for everyone past the visa.
    assert numbers(engine, "a3") == [1, 2]
    assert numbers(engine, "b6") == [1, 2, 3, 4, 5]
    assert numbers(engine, "b9") == [1, 2, 3, 4, 5, 7, 8, 9]                       # old 1..8: passport 7, medical 8, PCC 9; advance not done yet
    assert numbers(engine, "b10") == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert numbers(engine, "b11") == list(range(1, 12))                              # + the Google review record, current is Visa stamping
    assert numbers(engine, "d12") == list(range(1, 14))
    assert record(engine, "b9", 7)[1] == {"feedback": "b9 stage 8"} and record(engine, "b9", 8)[1] == {"feedback": "b9 stage 6"}
    assert record(engine, "b10", 6)[1] == {"feedback": "b10 stage 9"}               # the old advance is stage 6 now
    assert record(engine, "cl", 6)[0] == "Not paid" and record(engine, "b10", 6)[2] is None
    assert query(engine, "SELECT amount FROM crm_application_stages WHERE id = 's-b9-9'") == []                       # (old stage 9 of b9 never existed)
    # Google review: the tick is copied from the visa record; everybody else completed manually, on the visa day.
    assert record(engine, "b11", 11)[:2] == ("Obtained", {"googleReview": True})
    assert record(engine, "b11", 10)[1] == {"arrived": "yes", "googleReview": True}      # copied, not moved
    assert record(engine, "d12", 11)[:2] == ("Completed manually", {})
    assert record(engine, "d12", 11)[3] == record(engine, "d12", 10)[3]
    assert 11 not in numbers(engine, "b10")                                             # still at the visa: nothing to record yet

    # Drafts: kept only where the application did not change stage (b9: advance -> advance).
    drafts = dict(query(engine, "SELECT id, draft FROM crm_applications WHERE id IN ('b6', 'b8', 'b9')"))
    assert drafts == {"b6": {}, "b8": {}, "b9": {"amount": 100}}
    # Tasks: the open task of a moved application is the advance task of stage 6; the others are renumbered.
    tasks = {row[0]: (row[1], row[2]) for row in query(engine, "SELECT id, stage, title FROM workflow_tasks")}
    for name in ("b6", "b7", "b8", "b9"):
        stage = int(name[1])
        assert tasks[f"t-{name}-{stage}"] == (6, "Collect the advance"), name
    assert tasks["t-b9-8"] == (7, "Collect original passport")                         # the completed passport task of b9: old 8 -> 7
    assert tasks["t-a3-3"] == (3, "Schedule the interview") and tasks["t-b10-10"] == (10, "Follow up the visa")
    assert tasks["t-b11-11"][0] == 12
    assert query(engine, "SELECT stage FROM workflow_daily_work_items") == [(6,)]
    assert dict(query(engine, "SELECT id, stage FROM crm_application_feedback")) == {"n1": 7, "n2": 3}
    # Documents follow their stage.
    assert slots(engine, "b8") == ["stage_7_copy"] and slots(engine, "b9") == ["stage_6_receipt"] and slots(engine, "a3") == ["stage_5_file"]
    assert slots(engine, "d12") == sorted(["stage_9_file", "stage_10_file", "stage_12_file", "stage_13_file"])
    # Audit trail, bulk-import count, escalation notice.
    audit = {row[0] for row in query(engine, "SELECT action FROM system_audit_log")}
    assert {"Recorded stage 6: Collected", "Recorded stage 12: Stamped", "Recorded stage 3: Scheduled"} <= audit
    counts = sorted(row[0] for row in query(engine, "SELECT meta->>'completed_stages' FROM system_audit_log WHERE meta ? 'completed_stages'"))
    assert counts == ["13", "5"]
    assert query(engine, "SELECT detail FROM system_notifications") == [("Asha · Stage 12 · Get the visa stamped · Owner: Ravi · 50 hours overdue",)]


def test_range_checks_allow_thirteen_stages_and_no_more(schema_database):
    engine = schema_database
    _migrate(engine, "head")
    with engine.begin() as c:
        execute(c, "INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')")
        execute(c, "INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')")
        execute(c, "INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')")
        execute(c, "INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)")
        execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('c', 'CND-1', 'c', '90000000600', 'walk_in')")
        execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) VALUES ('a', 'c', 'order', 13, 'deployed')")
        execute(c, "INSERT INTO crm_application_stages (id, application_id, stage, attempt) VALUES ('s13', 'a', 13, 1)")
        for sql in ("UPDATE crm_applications SET current_stage = 14", "INSERT INTO crm_application_stages (id, application_id, stage, attempt) VALUES ('s14', 'a', 14, 1)",
                    "UPDATE crm_applications SET current_stage = 0"):
            with pytest.raises(IntegrityError):
                with c.begin_nested():
                    execute(c, sql)


def _body(name):
    spec = importlib.util.spec_from_file_location("migration_0056", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def test_running_the_migration_body_again_changes_nothing(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "head")
    snapshot = (apps(engine), {name: numbers(engine, name) for name in APPS}, slots(engine, "d12"))
    upgrade = _body("upgrade")
    with engine.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            upgrade()
    assert (apps(engine), {name: numbers(engine, name) for name in APPS}, slots(engine, "d12")) == snapshot


def test_downgrade_puts_the_old_order_back(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    original = {name: query(engine, "SELECT stage, attempt, outcome, data FROM crm_application_stages WHERE application_id = :a ORDER BY stage", a=name)
                for name in APPS}
    _migrate(engine, "head")
    _migrate(engine, PREVIOUS, downgrade=True)

    assert apps(engine) == {name: (values[0], values[1]) for name, values in APPS.items()}
    for name in APPS:                                                                  # records and their content, exactly as before
        assert query(engine, "SELECT stage, attempt, outcome, data FROM crm_application_stages WHERE application_id = :a ORDER BY stage", a=name) == original[name], name
    # The open tasks of the moved applications are back on their old stage with the old title.
    tasks = {row[0]: (row[1], row[2]) for row in query(engine, "SELECT id, stage, title FROM workflow_tasks")}
    for name in ("b6", "b7", "b8", "b9"):
        stage = int(name[1])
        assert tasks[f"t-{name}-{stage}"] == (stage, OLD_TASKS[stage]), name
    assert slots(engine, "b8") == ["stage_8_copy"] and slots(engine, "b9") == ["stage_9_receipt"]
    assert slots(engine, "d12") == sorted(["stage_7_file", "stage_10_file", "stage_11_file", "stage_12_file"])
    assert {"Recorded stage 9: Collected", "Recorded stage 11: Stamped"} <= {row[0] for row in query(engine, "SELECT action FROM system_audit_log")}
    # Upgrading again works.
    _migrate(engine, "head")
    assert apps(engine)["d12"] == (13, "deployed")
