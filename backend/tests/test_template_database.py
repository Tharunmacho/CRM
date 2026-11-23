"""The template-database shortcut for `_migrate(engine, "head")` (tests/template_db.py) must be exactly as good as the real migrations.

These tests build a head database the real way, clone it from the template, and compare catalog and seeded data; they also
prove the clone is only used for `upgrade head` on an empty database and that other pooled connections survive a replacement.
"""

import hashlib
import json
import re

import pytest
from sqlalchemy import create_engine, text

import template_db
from test_database_naming import PREVIOUS, _guard_test_url, _migrate, schema_database  # noqa: F401


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def row_digest(rows, stable):
    """Hash of a table's rows. `stable` drops what every migration run generates afresh (timestamps and random ids); the seeded
    content (names, sort orders, texts...) is still compared. The row order inside a table never matters."""
    items = []
    for row in rows:
        values = {key: value for key, value in row.items() if not (stable and (key.endswith("_at") or (isinstance(value, str) and UUID.match(value))))}
        items.append(json.dumps(values, sort_keys=True, default=str))
    return len(items), hashlib.md5("|".join(sorted(items)).encode()).hexdigest()


def snapshot(engine, stable=False):
    """Everything a test could notice: columns, indexes, constraints, sequences, enums, functions, triggers, owners and every row."""
    queries = {
        "columns": "SELECT table_name, column_name, ordinal_position, data_type, udt_name, is_nullable, column_default "
                   "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 3",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2",
        "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
                       "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2",
        "sequences": "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1",
        "enums": "SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)::text FROM pg_type t "
                 "JOIN pg_enum e ON e.enumtypid = t.oid WHERE t.typnamespace = 'public'::regnamespace GROUP BY 1 ORDER BY 1",
        "functions": "SELECT proname, pg_get_functiondef(oid) FROM pg_proc "
                     "WHERE pronamespace = 'public'::regnamespace AND prokind = 'f' ORDER BY 1, 2",
        "triggers": "SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1, 2",
        "extensions": "SELECT extname FROM pg_extension ORDER BY 1",
        "owners": "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public' ORDER BY 1",
        "schema": "SELECT nspname, nspowner::regrole::text, nspacl::text FROM pg_namespace WHERE nspname = 'public'",
        "revision": "SELECT version_num FROM alembic_version",
    }
    with engine.connect() as connection:
        result = {name: [tuple(row) for row in connection.execute(text(sql))] for name, sql in queries.items()}
        rows = {}
        for (table,) in connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")).all():
            data = [dict(row._mapping) for row in connection.execute(text(f'SELECT * FROM "{table}"'))]
            rows[table] = row_digest(data, stable)
        result["rows"] = rows
    return result


def reset(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def template_on():
    """These DB tests exercise the opt-in shortcut, so they run only when it is opted in (CRM_TEST_TEMPLATE=1)."""
    if not template_db._flag("CRM_TEST_TEMPLATE") or template_db._flag("CRM_TEST_NO_TEMPLATE"):
        pytest.skip("opt-in: set CRM_TEST_TEMPLATE=1 to prove the template-database shortcut")


def test_template_names_obey_the_disposable_database_rules():
    for bad in ("care_india", "crm_schema_test; DROP DATABASE x", "crm_schema_test name", "", "postgres"):
        with pytest.raises(ValueError):
            template_db._quoted(bad)
    assert template_db._quoted("crm_schema_test_y_tpl") == '"crm_schema_test_y_tpl"'
    url = _guard_test_url("postgresql+psycopg://postgres@127.0.0.1:54329/crm_schema_test_y")
    assert template_db.NAME.fullmatch(template_db._template_name(url))
    assert template_db._template_name(url) != url.database


def test_the_clone_is_identical_to_the_real_migrations(schema_database, template_on):
    engine = schema_database
    _migrate(engine, "head", fresh=True)
    real = snapshot(engine, stable=True)
    reset(engine)
    assert template_db.clone_head(engine, _migrate, _guard_test_url) is True, template_db._state["reason"]
    cloned = snapshot(engine, stable=True)
    assert set(cloned) == set(real)
    for key in real:
        assert cloned[key] == real[key], key
    assert real["revision"] and real["rows"]
    # and, with nothing set aside, the clone is exactly a copy of the template it came from (same ids, same timestamps)
    template = create_engine(engine.url.set(database=template_db._template_name(engine.url)), connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    try:
        assert snapshot(engine) == snapshot(template)
    finally:
        template.dispose()


def test_the_shortcut_is_only_for_upgrade_head_on_an_empty_database(schema_database, template_on):
    engine = schema_database
    _migrate(engine, PREVIOUS)                       # an older revision always runs the real migrations
    assert [row[0] for row in snapshot(engine)["revision"]] == [PREVIOUS]
    assert template_db.clone_head(engine, _migrate, _guard_test_url) is False   # not empty: nothing is dropped
    assert [row[0] for row in snapshot(engine)["revision"]] == [PREVIOUS]
    _migrate(engine, "head")                          # upgrade from older: real path, ends at head
    assert snapshot(engine)["revision"] != [(PREVIOUS,)]
    reset(engine)
    _migrate(engine, "head")                          # empty: clone (or the real path when the template is off)
    head = snapshot(engine)["revision"]
    _migrate(engine, "head")                          # already at head: nothing to do, still fine
    assert snapshot(engine)["revision"] == head
    _migrate(engine, "0007_email_intake", downgrade=True)   # downgrades always use the real path (same step the existing round-trip test uses)
    assert [row[0] for row in snapshot(engine)["revision"]] == ["0007_email_intake"]


def test_other_pooled_connections_to_the_replaced_database_keep_working(schema_database, template_on):
    engine = schema_database
    other = create_engine(engine.url, connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    try:
        _migrate(engine, "head")
        with other.connect() as connection:           # an idle pooled connection now exists on the OLD database
            assert connection.execute(text("SELECT 1")).scalar() == 1
        reset(engine)
        assert template_db.clone_head(engine, _migrate, _guard_test_url) is True, template_db._state["reason"]   # the database is replaced
        with other.connect() as connection:           # the stale pooled connection must be discarded, not raise
            assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1
    finally:
        other.dispose()


def test_the_shortcut_is_off_unless_opted_in_and_the_off_switch_always_wins(schema_database, monkeypatch):
    monkeypatch.delenv("CRM_TEST_TEMPLATE", raising=False)
    monkeypatch.delenv("CRM_TEST_NO_TEMPLATE", raising=False)
    assert template_db.enabled() is False             # default: the real migrations
    monkeypatch.setenv("CRM_TEST_TEMPLATE", "1")
    assert template_db.enabled() is True or template_db._state["disabled"]    # opted in (unless it broke earlier in the session)
    monkeypatch.setenv("CRM_TEST_NO_TEMPLATE", "1")
    assert template_db.enabled() is False
    assert template_db.clone_head(schema_database, _migrate, _guard_test_url) is False
    _migrate(schema_database, "head")                 # still works, through alembic
    assert snapshot(schema_database)["revision"]


def test_a_different_database_is_never_replaced(schema_database, template_on):
    engine = schema_database
    foreign = create_engine(engine.url.set(database="postgres"), connect_args={"connect_timeout": 5})
    try:
        assert template_db.clone_head(foreign, _migrate, _guard_test_url) is False   # not the guarded test database
    finally:
        foreign.dispose()
