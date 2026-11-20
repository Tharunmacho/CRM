"""Migration 0050: the Lead sheet status templates (crm_leads.status_label and the crm_lead_status_templates table).

Real PostgreSQL (isolated test database only). Leads seeded at 0049 keep every value; the three built-in templates are seeded once with
their tones; a second run of the migration body changes nothing and keeps what an administrator did; the downgrade keeps each lead's
built-in status, leaves a history line naming the label it loses, and drops the table and the column; an empty database migrates both ways.
"""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from test_database_naming import _migrate, schema_database  # noqa: F401

PREVIOUS = "0049_purge_user_tombstones"
THIS = "0050_lead_status_templates"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0050_lead_status_templates.py"


def query(engine, sql, **params):
    with engine.begin() as c:
        return [tuple(row) for row in c.execute(text(sql), params).all()]


def run(engine, sql, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)


def seed_leads(engine):
    run(engine, "INSERT INTO auth_users (id, name, email, role) VALUES ('u1', 'Recruiter', 'u1@example.test', 'staff')")
    for number, status in enumerate(("new", "contacted", "interested", "not_interested", "wrong_number"), start=1):
        run(engine, "INSERT INTO crm_leads (id, name, phone, status, owner_id) VALUES (:id, :n, :p, :s, 'u1')",
            id=f"l{number}", n=f"Lead {number}", p=f"+9190000000{number}", s=status)


def test_upgrade_adds_the_column_and_the_seeded_templates_and_changes_no_lead(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed_leads(engine)
    before = query(engine, "SELECT id, name, phone, status, owner_id FROM crm_leads ORDER BY id")
    assert "status_label" not in {c["name"] for c in inspect(engine).get_columns("crm_leads")}
    _migrate(engine, THIS)

    assert query(engine, "SELECT id, name, phone, status, owner_id FROM crm_leads ORDER BY id") == before
    assert query(engine, "SELECT count(*) FROM crm_leads WHERE status_label IS NOT NULL") == [(0,)]
    assert query(engine, "SELECT name, tone, active, builtin, sort_order FROM crm_lead_status_templates ORDER BY sort_order") == [
        ("Ringing", "warning", True, True, 10), ("No incoming calls", "warning", True, True, 20), ("Not eligible", "negative", True, True, 30)]
    columns = {c["name"]: c for c in inspect(engine).get_columns("crm_leads")}
    assert columns["status_label"]["nullable"] is True and columns["status_label"]["comment"]
    assert inspect(engine).get_table_comment("crm_lead_status_templates")["text"]


def test_the_name_is_unique_ignoring_case_short_and_free_of_commas_and_the_tone_is_one_of_four(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    for sql in ("INSERT INTO crm_lead_status_templates (id, name) VALUES ('x1', 'ringing')",                       # same name, other case
                "INSERT INTO crm_lead_status_templates (id, name) VALUES ('x2', '   ')",
                "INSERT INTO crm_lead_status_templates (id, name) VALUES ('x3', '" + "n" * 41 + "')",
                "INSERT INTO crm_lead_status_templates (id, name) VALUES ('x4', 'Busy, call later')",
                "INSERT INTO crm_lead_status_templates (id, name, tone) VALUES ('x5', 'Odd tone', 'loud')"):
        with pytest.raises((IntegrityError, DataError)):   # too long: the column itself refuses
            run(engine, sql)
    run(engine, "INSERT INTO crm_lead_status_templates (id, name) VALUES ('ok', 'Busy later')")
    assert query(engine, "SELECT tone, active, builtin FROM crm_lead_status_templates WHERE id = 'ok'") == [("neutral", True, False)]
    # the lead's own status keeps its CHECK: only the nine built-in codes
    run(engine, "INSERT INTO crm_leads (id, name, phone) VALUES ('l', 'Lead', '+919000000099')")
    with pytest.raises(IntegrityError):
        run(engine, "UPDATE crm_leads SET status = 'ringing' WHERE id = 'l'")
    run(engine, "UPDATE crm_leads SET status = 'contacted', status_label = 'Ringing' WHERE id = 'l'")


def _upgrade_body():
    spec = importlib.util.spec_from_file_location("migration_0050", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.upgrade


def test_running_the_migration_body_again_keeps_what_was_changed_since(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    run(engine, "UPDATE crm_lead_status_templates SET active = false, sort_order = 99 WHERE name = 'Ringing'")
    run(engine, "INSERT INTO crm_lead_status_templates (id, name, tone) VALUES ('mine', 'Visa ready', 'positive')")
    before = query(engine, "SELECT id, name, tone, active, builtin, sort_order FROM crm_lead_status_templates ORDER BY name")
    upgrade = _upgrade_body()
    with engine.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            upgrade()
    assert query(engine, "SELECT id, name, tone, active, builtin, sort_order FROM crm_lead_status_templates ORDER BY name") == before


def test_downgrade_keeps_every_lead_at_its_built_in_status_and_says_what_it_lost(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed_leads(engine)
    _migrate(engine, THIS)
    run(engine, "INSERT INTO crm_lead_status_templates (id, name, tone) VALUES ('mine', 'Visa ready', 'positive')")
    run(engine, "UPDATE crm_leads SET status = 'contacted', status_label = 'Ringing' WHERE id = 'l1'")
    run(engine, "UPDATE crm_leads SET status = 'not_interested', status_label = 'Not eligible' WHERE id = 'l4'")
    run(engine, "UPDATE crm_leads SET status = 'interested', status_label = 'Visa ready' WHERE id = 'l5'")

    _migrate(engine, PREVIOUS, downgrade=True)
    assert "crm_lead_status_templates" not in inspect(engine).get_table_names()
    assert "status_label" not in {c["name"] for c in inspect(engine).get_columns("crm_leads")}
    assert dict(query(engine, "SELECT id, status FROM crm_leads")) == {"l1": "contacted", "l2": "contacted", "l3": "interested", "l4": "not_interested", "l5": "interested"}
    lines = dict(query(engine, "SELECT lead_id, summary FROM crm_lead_activities WHERE kind = 'status'"))
    assert set(lines) == {"l1", "l4", "l5"} and 'Status "Ringing" no longer exists' in lines["l1"] and "Not eligible" in lines["l4"]

    _migrate(engine, THIS)   # up again: the built-in templates are back, the leads carry no label
    assert query(engine, "SELECT count(*) FROM crm_lead_status_templates") == [(3,)]
    assert query(engine, "SELECT count(*) FROM crm_leads WHERE status_label IS NOT NULL") == [(0,)]


def test_an_empty_database_migrates_both_ways(schema_database):
    engine = schema_database
    _migrate(engine, THIS)
    assert query(engine, "SELECT count(*) FROM crm_leads") == [(0,)]
    assert query(engine, "SELECT version_num FROM alembic_version") == [(THIS,)]
    _migrate(engine, PREVIOUS, downgrade=True)
    assert query(engine, "SELECT version_num FROM alembic_version") == [(PREVIOUS,)]
    assert "crm_lead_status_templates" not in inspect(engine).get_table_names()
