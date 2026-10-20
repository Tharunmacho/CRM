"""Verify table renaming with metadata checks and an opt-in disposable PostgreSQL DB.

Integration tests reset the public schema. They only accept TEST_DATABASE_URL for a
loopback PostgreSQL database named crm_schema_test*. No backend/.env is read, and a
normal test run skips these cases when the explicit test URL is absent.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import SESSION_COOKIE, hash_password, verify_password
from app.db.models import Base, Role, Session as SessionRow, User


PREVIOUS = "0005_candidate_ocr_data"
RENAMED = "0006_domain_table_names"
TABLE_NAMES = {
    "users": "auth_users",
    "sessions": "auth_sessions",
    "clients": "crm_client_contacts",
    "companies": "crm_employer_companies",
    "designations": "crm_job_designations",
    "orders": "crm_job_orders",
    "candidates": "crm_candidates",
    "applications": "crm_applications",
    "application_stages": "crm_application_stages",
    "tasks": "workflow_tasks",
    "remarks": "workflow_task_remarks",
    "assignment_pointer": "workflow_assignment_cursor",
    "todo_items": "personal_todos",
    "mail_accounts": "email_accounts",
    "mail_messages": "email_messages",
    "mail_attachments": "email_attachments",
    "ingestion_failures": "ingestion_failures",
    "attachments": "document_attachments",
    "notifications": "system_notifications",
    "audit_log": "system_audit_log",
}
TEST_PASSWORD = "schema-migration-test-password"
TEST_EMAIL = "schema-migration@example.test"
BACKEND = Path(__file__).resolve().parents[1]


def test_domain_names_cover_existing_models_without_extra_business_tables():
    assert set(Base.metadata.tables) == set(TABLE_NAMES.values())
    assert len(Base.metadata.tables) == 20
    for table in Base.metadata.tables.values():
        assert table.comment, f"{table.name} needs a browser-visible description"
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.name in TABLE_NAMES.values()


def _guard_test_url(raw):
    url = make_url(raw)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "::1", "localhost"}
        or not re.fullmatch(r"crm_schema_test[a-zA-Z0-9_]*", url.database or "")
        or url.query
    ):
        raise ValueError(
            "TEST_DATABASE_URL must use postgresql+psycopg, a loopback host, "
            "a crm_schema_test* database and no connection query overrides."
        )
    return url


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql+psycopg://test@database.example.test/crm_schema_test",
        "postgresql+psycopg://test@127.0.0.1/care_india",
        "postgresql+psycopg://test@127.0.0.1/crm_schema_test?host=database.example.test",
        "postgresql+psycopg://test@127.0.0.1/crm_schema_test?service=production",
        "sqlite:///crm_schema_test",
    ],
)
def test_disposable_database_guard_rejects_unsafe_targets(raw):
    with pytest.raises(ValueError):
        _guard_test_url(raw)


@pytest.fixture
def schema_database():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB")
    url = _guard_test_url(raw)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        database, server = connection.execute(
            text("SELECT current_database(), host(inet_server_addr())")
        ).one()
        assert database == url.database
        assert server in {"127.0.0.1", "::1"}

    def reset_public():
        # The URL and connected server were verified before any destructive action.
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))

    reset_public()
    try:
        yield engine
    finally:
        reset_public()
        engine.dispose()


def _migrate(engine, revision, *, downgrade=False, connection=None):
    config = Config(str(BACKEND / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    settings = SimpleNamespace(database_url=engine.url.render_as_string(hide_password=False))
    # env.py consults get_settings even for an externally supplied connection.
    with patch("app.core.config.get_settings", return_value=settings):
        operation = command.downgrade if downgrade else command.upgrade
        operation(config, revision)


def _seed_previous_schema(engine):
    now = datetime.now(timezone.utc)
    data = {
        "users": [
            {
                "id": "schema-user",
                "name": "Persisted manager",
                "email": TEST_EMAIL,
                "password_hash": hash_password(TEST_PASSWORD),
                "role": "manager",
                "active": True,
                "receives_auto_assign": False,
                "on_leave": True,
            },
            {
                "id": "schema-staff",
                "name": "Disabled staff",
                "email": "disabled-schema@example.test",
                "password_hash": hash_password("different-schema-test-password"),
                "role": "staff",
                "active": False,
                "receives_auto_assign": True,
                "on_leave": False,
            },
        ],
        "sessions": [{
            "id": "schema-existing-session",
            "user_id": "schema-user",
            "expires_at": now + timedelta(days=1),
        }],
        "clients": [{"id": "schema-client", "name": "Test contact", "email": "contact@example.test"}],
        "companies": [{
            "id": "schema-company", "client_id": "schema-client", "name": "Test employer",
        }],
        "designations": [{
            "id": "schema-designation", "title": "Welder",
            "required_skills": ["MIG", "TIG"], "min_experience": 3,
        }],
        "orders": [{
            "id": "schema-order", "ref": "ORD-SCHEMA-001",
            "company_id": "schema-company", "designation_id": "schema-designation",
            "openings": 2, "salary": 1200, "currency": "AED",
        }],
        "candidates": [{
            "id": "schema-candidate", "ref": "CND-SCHEMA-001",
            "name": "Synthetic candidate", "phone": "+910000000001",
            "email": "candidate@example.test", "source": "email",
            "owner_id": "schema-staff", "skills": ["MIG"],
            "resume_text": "Synthetic welding experience",
            "ocr_data": {"resume": {"name": "Synthetic candidate", "skills": ["MIG"]}},
        }],
        "applications": [{
            "id": "schema-application", "candidate_id": "schema-candidate",
            "order_id": "schema-order", "current_stage": 2,
            "draft": {"forwarded": "yes"}, "draft_saved_by": "schema-user",
            "lock_version": 7,
        }],
        "application_stages": [{
            "id": "schema-stage", "application_id": "schema-application", "stage": 1,
            "attempt": 1, "data": {"result": "passed", "remarks": "Synthetic check"},
            "outcome": "passed", "completed_by": "schema-user",
        }],
        "tasks": [{
            "id": "schema-task", "kind": "stage", "title": "Forward CV to client",
            "candidate_id": "schema-candidate", "application_id": "schema-application",
            "stage": 2, "owner_id": "schema-staff", "escalated": True,
            "escalated_at": now,
        }],
        "remarks": [{
            "id": "schema-remark", "task_id": "schema-task",
            "body": "Preserve this task history.", "author_id": "schema-staff",
        }],
        "assignment_pointer": [{"id": 1, "last_user_id": "schema-staff"}],
        "todo_items": [{
            "id": "schema-todo", "owner_id": "schema-user",
            "title": "Synthetic reminder", "due_at": now + timedelta(hours=2),
            "priority": "high", "done": False,
        }],
        "mail_accounts": [{
            "id": "schema-mail-account", "address": "inbox@example.test",
            "provider": "gmail", "auto_ingest": False,
            "secrets": {"test_fixture_marker": "synthetic-only"},
        }],
        "mail_messages": [{
            "id": "schema-mail", "account_id": "schema-mail-account",
            "provider_message_id": "provider-schema-message",
            "received_at": now, "subject": "Synthetic CV",
            "body": "Migration fixture", "candidate_id": "schema-candidate",
        }],
        "mail_attachments": [{
            "id": "schema-mail-file", "message_id": "schema-mail",
            "file_name": "synthetic-cv.pdf", "object_key": "test/mail/cv.pdf",
            "content_hash": "a" * 64,
        }],
        "ingestion_failures": [{
            "id": "schema-ingestion-failure", "file_name": "synthetic-failed.pdf",
            "source": "schema-mail-account", "reason": "Synthetic OCR timeout", "attempts": 3,
        }],
        "attachments": [
            {
                "id": "schema-file-v1", "entity_type": "candidate",
                "entity_id": "schema-candidate", "slot": "resume", "version": 1,
                "file_name": "synthetic-cv-v1.pdf", "object_key": "test/resume/v1.pdf",
                "content_hash": "b" * 64, "uploaded_by": "schema-user",
            },
            {
                "id": "schema-file-v2", "entity_type": "candidate",
                "entity_id": "schema-candidate", "slot": "resume", "version": 2,
                "file_name": "synthetic-cv-v2.pdf", "object_key": "test/resume/v2.pdf",
                "content_hash": "c" * 64, "uploaded_by": "schema-user",
            },
        ],
        "notifications": [{
            "id": "schema-notification", "user_id": "schema-user",
            "kind": "assignment", "title": "Synthetic assignment", "read": False,
        }],
        "audit_log": [{
            "id": "schema-audit", "actor_id": "schema-user", "action": "Synthetic create",
            "entity_type": "candidate", "entity_id": "schema-candidate",
            "meta": {"preserve": True, "document_id": "schema-file-v1"},
        }],
    }
    assert set(data) == set(TABLE_NAMES)
    metadata = MetaData()
    metadata.reflect(bind=engine, only=list(TABLE_NAMES))
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name in data:
                connection.execute(table.insert(), data[table.name])


def _data_snapshot(connection, *, renamed):
    snapshot = {}
    for old, new in TABLE_NAMES.items():
        name = new if renamed else old
        snapshot[old] = list(connection.execute(
            text(f'SELECT to_jsonb(record) FROM public."{name}" AS record ORDER BY id')
        ).scalars())
    return snapshot


def _catalog_snapshot(connection):
    tables = list(connection.execute(text("""
        SELECT c.oid::bigint, c.relowner::bigint, c.relacl::text,
               c.reloptions, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
        ORDER BY c.oid
    """)))
    # OIDs and key numbers are stable across a rename; constraint definitions below
    # are normalized separately because PostgreSQL renders the new table names.
    constraints = list(connection.execute(text("""
        SELECT k.oid::bigint, k.conname, k.contype, k.conrelid::bigint,
               k.confrelid::bigint, k.conkey, k.confkey,
               k.confupdtype, k.confdeltype, k.confmatchtype,
               k.condeferrable, k.condeferred, k.convalidated,
               pg_get_constraintdef(k.oid)
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname <> 'alembic_version'
        ORDER BY k.oid
    """)))
    normalized_constraints = []
    for row in constraints:
        definition = row[-1]
        for old, new in TABLE_NAMES.items():
            definition = re.sub(rf"\b{re.escape(new)}\b", old, definition)
        normalized_constraints.append((*row[:-1], definition))
    indexes = list(connection.execute(text("""
        SELECT i.indexrelid::bigint, i.indrelid::bigint, i.indkey::text,
               i.indclass::text, i.indcollation::text, i.indoption::text,
               i.indisunique, i.indisprimary, i.indisvalid,
               pg_get_expr(i.indexprs, i.indrelid),
               pg_get_expr(i.indpred, i.indrelid)
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname <> 'alembic_version'
        ORDER BY i.indexrelid
    """)))
    return tables, normalized_constraints, indexes


def _assert_renamed_schema(engine):
    assert set(inspect(engine).get_table_names(schema="public")) == {
        *TABLE_NAMES.values(), "alembic_version"
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == RENAMED
        for table in Base.metadata.tables.values():
            assert inspect(connection).get_table_comment(table.name, schema="public")["text"]
            actual_indexes = {
                index["name"] for index in inspect(connection).get_indexes(table.name, schema="public")
            }
            assert {index.name for index in table.indexes} <= actual_indexes
        for table, column in [
            ("auth_users", "email"), ("auth_users", "role"), ("auth_users", "password_hash"),
            ("auth_sessions", "user_id"), ("auth_sessions", "expires_at"),
        ]:
            comment = connection.scalar(text("""
                SELECT col_description(c.oid, a.attnum)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'public' AND c.relname = :table AND a.attname = :column
            """), {"table": table, "column": column})
            assert comment, f"{table}.{column} needs an explanation"


def test_rename_preserves_all_rows_relations_indexes_and_downgrade(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _seed_previous_schema(engine)
    with engine.connect() as connection:
        before = _data_snapshot(connection, renamed=False)
        catalog = _catalog_snapshot(connection)
    assert all(before.values()), "Every current business table must be populated for this test"

    _migrate(engine, RENAMED)
    _assert_renamed_schema(engine)
    with engine.connect() as connection:
        assert _data_snapshot(connection, renamed=True) == before
        assert _catalog_snapshot(connection) == catalog
    with Session(engine) as db:
        for mapper in Base.registry.mappers:
            assert db.scalar(select(func.count()).select_from(mapper.class_)) > 0

    _migrate(engine, PREVIOUS, downgrade=True)
    assert set(inspect(engine).get_table_names(schema="public")) == {
        *TABLE_NAMES, "alembic_version"
    }
    with engine.connect() as connection:
        assert _data_snapshot(connection, renamed=False) == before
        assert _catalog_snapshot(connection) == catalog
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS

    _migrate(engine, RENAMED)
    with engine.connect() as connection:
        assert _data_snapshot(connection, renamed=True) == before
        assert _catalog_snapshot(connection) == catalog


def test_new_names_keep_foreign_keys_and_unique_constraints_enforced(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _seed_previous_schema(engine)
    _migrate(engine, RENAMED)
    attempts = [
        (
            """INSERT INTO auth_sessions (id, user_id, expires_at)
               VALUES ('orphan-session', 'missing-user', now() + interval '1 day')""",
            "23503",
        ),
        (
            """INSERT INTO crm_applications (id, candidate_id, order_id)
               VALUES ('duplicate-application', 'schema-candidate', 'schema-order')""",
            "23505",
        ),
        (
            "INSERT INTO workflow_assignment_cursor (id) VALUES (2)",
            "23514",
        ),
        (
            """INSERT INTO document_attachments
               (id, entity_type, entity_id, slot, version, file_name, object_key)
               VALUES ('duplicate-version', 'candidate', 'schema-candidate',
                       'resume', 1, 'duplicate.pdf', 'test/duplicate.pdf')""",
            "23505",
        ),
    ]
    with engine.begin() as connection:
        for sql, expected_code in attempts:
            with pytest.raises(IntegrityError) as failure:
                with connection.begin_nested():
                    connection.execute(text(sql))
            assert failure.value.orig.sqlstate == expected_code


def test_existing_login_session_bootstrap_and_health_work_after_rename(
    schema_database, monkeypatch
):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _seed_previous_schema(engine)
    _migrate(engine, RENAMED)
    settings = SimpleNamespace(
        database_url=engine.url.render_as_string(hide_password=False),
        cors_origin_list=["https://testserver"],
        static_dir=str(Path(__file__) / "no-static-files"),
        storage_dir=str(Path(__file__) / "no-upload-files"),
        veris_ocr_base_url=None,
        veris_ocr_api_key=None,
    )
    with patch("app.core.config.get_settings", return_value=settings):
        from app import main
        from app.db.session import get_db
        from app.services import bootstrap

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(bootstrap, "_done", False)
    monkeypatch.setattr(bootstrap, "_last_error", None)

    def test_db():
        with factory() as db:
            yield db

    monkeypatch.setitem(main.app.dependency_overrides, get_db, test_db)
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.set(SESSION_COOKIE, "schema-existing-session")
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == TEST_EMAIL
        assert me.json()["role"] == "manager"
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["accounts"] == 2
        state = client.get("/api/bootstrap")
        assert state.status_code == 200
        assert state.json()["candidates"][0]["id"] == "schema-candidate"
        assert state.json()["orders"][0]["id"] == "schema-order"
        assert state.json()["tasks"][0]["id"] == "schema-task"

        client.cookies.clear()
        login = client.post(
            "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        assert login.status_code == 200
        assert login.json()["role"] == "manager"
        assert "password_hash" not in login.json()
        with factory() as db:
            assert verify_password(TEST_PASSWORD, db.get(User, "schema-user").password_hash)
            assert db.get(User, "schema-staff").active is False
            assert db.get(User, "schema-user").role is Role.manager
            assert db.get(SessionRow, login.cookies[SESSION_COOKIE]).user_id == "schema-user"


def test_fresh_database_upgrades_to_exactly_the_documented_tables(schema_database):
    engine = schema_database
    _migrate(engine, RENAMED)
    _assert_renamed_schema(engine)
    with Session(engine) as db:
        for mapper in Base.registry.mappers:
            assert db.scalar(select(func.count()).select_from(mapper.class_)) == 0


def test_target_name_collision_rolls_back_the_entire_rename(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _seed_previous_schema(engine)
    with engine.begin() as connection:
        before = _data_snapshot(connection, renamed=False)
        connection.execute(text("CREATE TABLE crm_job_orders (marker text NOT NULL)"))
        connection.execute(text("INSERT INTO crm_job_orders VALUES ('keep-this-table')"))
        catalog = _catalog_snapshot(connection)

    with pytest.raises(ProgrammingError) as failure:
        _migrate(engine, RENAMED)
    assert failure.value.orig.sqlstate == "42P07"
    assert set(inspect(engine).get_table_names(schema="public")) == {
        *TABLE_NAMES, "alembic_version", "crm_job_orders"
    }
    with engine.connect() as connection:
        assert _data_snapshot(connection, renamed=False) == before
        assert _catalog_snapshot(connection) == catalog
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS
        assert connection.scalar(text("SELECT marker FROM crm_job_orders")) == "keep-this-table"


def test_supplied_transaction_can_validate_then_rollback_without_committing(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _seed_previous_schema(engine)
    with engine.connect() as connection:
        before = _data_snapshot(connection, renamed=False)
        catalog = _catalog_snapshot(connection)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _migrate(engine, RENAMED, connection=connection)
            assert transaction.is_active, "Alembic must not commit the caller's transaction"
            assert _data_snapshot(connection, renamed=True) == before
            assert _catalog_snapshot(connection) == catalog
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == RENAMED
        finally:
            transaction.rollback()

    assert set(inspect(engine).get_table_names(schema="public")) == {
        *TABLE_NAMES, "alembic_version"
    }
    with engine.connect() as connection:
        assert _data_snapshot(connection, renamed=False) == before
        assert _catalog_snapshot(connection) == catalog
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS
