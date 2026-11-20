"""Migration 0053: deleted_at / deleted_by / cleared_at on workflow_task_remarks.

Real PostgreSQL (isolated test database only). Additive and idempotent, nothing existing changes, an empty database migrates both ways,
and the downgrade honours the deletes whose text is kept in the audit log (and only those) before it drops the columns.
"""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from test_database_naming import _migrate, schema_database  # noqa: F401

PREVIOUS = "0052_speed_indexes_tasks"
THIS = "0053_remark_soft_delete"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0053_remark_soft_delete.py"
NEW = {"deleted_at", "deleted_by", "cleared_at"}


def columns(engine):
    return {c["name"]: c for c in inspect(engine).get_columns("workflow_task_remarks")}


def run(engine, sql, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)


def query(engine, sql, **params):
    with engine.begin() as c:
        return [tuple(row) for row in c.execute(text(sql), params).all()]


def test_the_revision_chain_and_the_columns(schema_database):
    spec = importlib.util.spec_from_file_location("m0053", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == THIS and module.down_revision == PREVIOUS and len(THIS) <= 32
    engine = schema_database
    _migrate(engine, PREVIOUS)
    assert NEW.isdisjoint(columns(engine))
    _migrate(engine, THIS)
    found = columns(engine)
    assert NEW <= set(found) and all(found[name]["nullable"] and found[name]["comment"] for name in NEW)
    keys = inspect(engine).get_foreign_keys("workflow_task_remarks")
    assert any(k["constrained_columns"] == ["deleted_by"] and k["referred_table"] == "auth_users" for k in keys)


def test_a_second_run_of_the_migration_body_is_harmless(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    run(engine, "INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Recruiter', 'u1@example.test', 'staff')")
    run(engine, "INSERT INTO crm_candidates (id, ref, name, phone, source, owner_id) VALUES ('c1', 'CND-1', 'Cand', '9000000001', 'walk_in', 'u1')")
    run(engine, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, owner_id) VALUES ('t1', 'initial_call', 'Call', 'c1', 'u1')")
    run(engine, "INSERT INTO workflow_task_remarks (id, task_id, body, author_id, deleted_at, deleted_by) VALUES ('r', 't1', 'x', 'u1', now(), 'u1')")
    spec = importlib.util.spec_from_file_location("m0053b", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            module.upgrade()
    assert NEW <= set(columns(engine))
    assert query(engine, "SELECT deleted_at IS NOT NULL, deleted_by FROM workflow_task_remarks") == [(True, "u1")]


def test_the_downgrade_removes_only_the_deletes_the_audit_log_remembers_and_an_empty_database_migrates_both_ways(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    run(engine, "INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Recruiter', 'u1@example.test', 'staff')")
    run(engine, "INSERT INTO crm_candidates (id, ref, name, phone, source, owner_id) VALUES ('c1', 'CND-1', 'Cand', '9000000001', 'walk_in', 'u1')")
    run(engine, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, owner_id) VALUES ('t1', 'initial_call', 'Call', 'c1', 'u1')")
    for key, body in (("keep", "Kept"), ("cleared", "Cleared only"), ("gone", "Deleted with an audit entry"), ("odd", "Deleted, no audit entry")):
        run(engine, "INSERT INTO workflow_task_remarks (id, task_id, body, author_id) VALUES (:k, 't1', :b, 'u1')", k=key, b=body)
    run(engine, "UPDATE workflow_task_remarks SET cleared_at = now() WHERE id = 'cleared'")
    run(engine, "UPDATE workflow_task_remarks SET deleted_at = now(), deleted_by = 'u1' WHERE id IN ('gone', 'odd')")
    run(engine, "INSERT INTO system_audit_log (id, actor_id, action, entity_type, entity_id, meta) "
                "VALUES ('a1', 'u1', 'Deleted a note', 'task', 't1', CAST(:m AS jsonb))", m='{"remarkId": "gone", "text": "Deleted with an audit entry"}')

    _migrate(engine, PREVIOUS, downgrade=True)
    assert NEW.isdisjoint(columns(engine))
    assert query(engine, "SELECT id FROM workflow_task_remarks ORDER BY id") == [("cleared",), ("keep",), ("odd",)]
    _migrate(engine, THIS)
    assert query(engine, "SELECT id, deleted_at IS NULL, cleared_at IS NULL FROM workflow_task_remarks ORDER BY id") == [
        ("cleared", True, True), ("keep", True, True), ("odd", True, True)]


def test_an_empty_database_goes_up_and_down(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    _migrate(engine, PREVIOUS, downgrade=True)
    assert NEW.isdisjoint(columns(engine))
    _migrate(engine, THIS)
    assert NEW <= set(columns(engine))
