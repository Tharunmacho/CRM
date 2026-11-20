"""Migration 0049: the tombstones the older "Delete user" left behind are purged.

Real PostgreSQL (isolated test database only). Two tombstone users (deleted-<id>@deleted.invalid) with history in several tables and
one normal user are seeded as 0048 left them; then 0048 -> 0049 is run and the tombstones are gone, their history belongs to the one
"Former user" system account, their personal rows are deleted, their live assignment pointers are blank, and the normal user is
untouched. A second run of the migration body, an upgrade with no tombstone and an empty database change nothing.
"""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.models import DELETED_EMAIL_DOMAIN, FORMER_USER_EMAIL, FORMER_USER_NAME, HIDDEN_EMAIL_DOMAINS
from test_database_naming import _migrate, schema_database  # noqa: F401

PREVIOUS = "0048_pcc_stage"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0049_purge_user_tombstones.py"


def execute(connection, sql, **params):
    connection.execute(text(sql), params)


def query(engine, sql, **params):
    with engine.begin() as c:
        return [tuple(row) for row in c.execute(text(sql), params).all()]


def seed(engine):
    with engine.begin() as c:
        execute(c, "INSERT INTO auth_users (id, name, email, role, active) VALUES "
                   "('t1', 'Asha (former user)', 'deleted-t1@deleted.invalid', 'staff', false), "
                   "('t2', 'Ravi (former user)', 'deleted-t2@deleted.invalid', 'staff', false), "
                   "('n1', 'Normal', 'normal@example.com', 'staff', true), "
                   "('adm', 'Admin', 'admin@example.com', 'admin', true)")
        for index, owner in enumerate(("t1", "t2", "n1")):
            execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source, owner_id) VALUES (:id, :ref, :id, :phone, 'walk_in', :owner)",
                    id=f"c-{owner}", ref=f"CND-{owner}", phone=f"9000000050{index}", owner=owner)
            execute(c, "INSERT INTO workflow_tasks (id, kind, title, candidate_id, owner_id, completed_at) "
                       "VALUES (:id, 'stage', 'A task', :cand, :owner, now())", id=f"task-{owner}", cand=f"c-{owner}", owner=owner)
            execute(c, "INSERT INTO workflow_task_remarks (id, task_id, body, author_id) VALUES (:id, :task, :body, :owner)",
                    id=f"remark-{owner}", task=f"task-{owner}", body=f"update by {owner}", owner=owner)
            execute(c, "INSERT INTO system_audit_log (id, actor_id, action) VALUES (:id, :owner, 'Did something')", id=f"audit-{owner}", owner=owner)
            execute(c, "INSERT INTO auth_sessions (id, user_id, expires_at) VALUES (:id, :owner, now() + interval '1 day')", id=f"sess-{owner}", owner=owner)
            execute(c, "INSERT INTO personal_todos (id, owner_id, title, due_at) VALUES (:id, :owner, 'Buy tea', now())", id=f"todo-{owner}", owner=owner)
            execute(c, "INSERT INTO staff_attendance_sessions (id, user_id, checked_in_at, checkin_request_key) "
                       "VALUES (:id, :owner, now() - interval '3 hours', :key)", id=f"att-{owner}", owner=owner, key=f"key-{owner}")


def counts(engine):
    return {table: query(engine, f"SELECT count(*) FROM {table}")[0][0] for table in (
        "auth_users", "auth_sessions", "personal_todos", "workflow_task_remarks", "system_audit_log", "staff_attendance_sessions",
        "workflow_tasks", "crm_candidates")}


def former_ids(engine):
    return [row[0] for row in query(engine, "SELECT id FROM auth_users WHERE email = :e", e=FORMER_USER_EMAIL)]


def test_the_models_still_treat_the_tombstone_domain_as_hidden():
    assert DELETED_EMAIL_DOMAIN == "deleted.invalid" and DELETED_EMAIL_DOMAIN in HIDDEN_EMAIL_DOMAINS
    assert FORMER_USER_EMAIL == "former-user@system.invalid" and FORMER_USER_NAME == "Former user"


def test_upgrade_purges_tombstones_and_moves_their_history_to_the_former_user(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    assert former_ids(engine) == []
    _migrate(engine, "head")

    assert query(engine, "SELECT id FROM auth_users WHERE email LIKE '%@deleted.invalid'") == []
    ids = former_ids(engine)
    assert len(ids) == 1
    former = ids[0]
    assert query(engine, "SELECT name, role::text, active, receives_auto_assign, on_leave, password_hash FROM auth_users WHERE id = :i", i=former) == [
        (FORMER_USER_NAME, "staff", False, False, False, None)]
    assert sorted(row[0] for row in query(engine, "SELECT id FROM auth_users")) == sorted(["adm", "n1", former])

    # History is kept and now belongs to the one Former user account; the normal user's own rows are untouched.
    assert dict(query(engine, "SELECT id, author_id FROM workflow_task_remarks")) == {"remark-t1": former, "remark-t2": former, "remark-n1": "n1"}
    assert dict(query(engine, "SELECT id, actor_id FROM system_audit_log")) == {"audit-t1": former, "audit-t2": former, "audit-n1": "n1"}
    assert dict(query(engine, "SELECT id, owner_id FROM workflow_tasks")) == {"task-t1": former, "task-t2": former, "task-n1": "n1"}
    # The open attendance sessions were closed first (not left running for the Former user), the normal user's stays open.
    sessions = {row[0]: (row[1], row[2]) for row in query(engine, "SELECT id, user_id, checked_out_at IS NOT NULL FROM staff_attendance_sessions")}
    assert sessions == {"att-t1": (former, True), "att-t2": (former, True), "att-n1": ("n1", False)}
    # Personal rows vanish with the account; live assignment pointers become nobody.
    assert dict(query(engine, "SELECT id, user_id FROM auth_sessions")) == {"sess-n1": "n1"}
    assert dict(query(engine, "SELECT id, owner_id FROM personal_todos")) == {"todo-n1": "n1"}
    assert dict(query(engine, "SELECT id, owner_id FROM crm_candidates")) == {"c-t1": None, "c-t2": None, "c-n1": "n1"}


def test_a_second_run_and_an_upgrade_with_no_tombstone_change_nothing(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "head")
    snapshot = (counts(engine), query(engine, "SELECT id, name, email FROM auth_users ORDER BY id"),
                query(engine, "SELECT id, author_id FROM workflow_task_remarks ORDER BY id"))
    spec = importlib.util.spec_from_file_location("migration_0049", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(2):
        with engine.begin() as c:
            with Operations.context(MigrationContext.configure(c)):
                module.upgrade()
    assert (counts(engine), query(engine, "SELECT id, name, email FROM auth_users ORDER BY id"),
            query(engine, "SELECT id, author_id FROM workflow_task_remarks ORDER BY id")) == snapshot
    assert len(former_ids(engine)) == 1
    module.downgrade()  # irreversible by design: nothing to undo, and nothing raised


def test_an_existing_former_user_is_reused_and_no_tombstone_means_no_new_account(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    with engine.begin() as c:   # a normal database: the Former user exists already, a tombstone is still waiting
        execute(c, "INSERT INTO auth_users (id, name, email, role, active) VALUES "
                   "('f0', 'Former user', 'former-user@system.invalid', 'staff', false), "
                   "('t1', 'Asha (former user)', 'deleted-t1@deleted.invalid', 'staff', false)")
        execute(c, "INSERT INTO system_audit_log (id, actor_id, action) VALUES ('a1', 't1', 'Did something'), ('a0', 'f0', 'Did more')")
    _migrate(engine, "head")
    assert [row[0] for row in query(engine, "SELECT id FROM auth_users")] == ["f0"]
    assert dict(query(engine, "SELECT id, actor_id FROM system_audit_log")) == {"a1": "f0", "a0": "f0"}


def test_an_empty_database_migrates_up_to_the_purge_and_creates_nothing(schema_database):
    engine = schema_database
    _migrate(engine, "0049_purge_user_tombstones")   # the whole chain 0001 -> 0049 on an empty database
    assert query(engine, "SELECT version_num FROM alembic_version") == [("0049_purge_user_tombstones",)]
    assert query(engine, "SELECT count(*) FROM auth_users") == [(0,)]       # no Former user is invented when nothing was purged
    _migrate(engine, PREVIOUS, downgrade=True)  # the downgrade is a no-op
    assert query(engine, "SELECT version_num FROM alembic_version") == [(PREVIOUS,)]
