"""Migration 0044: the financial-year IDs become two simple continuous series (CI-001 and CI-T-001).

Real PostgreSQL (isolated test database only): renumbering from 0043 data in two financial years with gaps, contiguity from
001, uniqueness and format guards, counters set to the new maximum, issuance after the migration (including concurrently
and after a deletion), idempotence of the migration body and the downgrade / upgrade round trip.
"""
import importlib.util
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Candidate
from app.services.care_india_id import allocate_care_id
from app.services.traveller_id import allocate_traveller_id, assign_traveller_id
from test_database_naming import _migrate, schema_database  # noqa: F401

PREVIOUS = "0043_stage_stop_exits"
TARGET = "0044_simple_continuous_ids"      # the revision under test; later revisions (0057 renumbers the travellers again) have their own tests
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0044_simple_continuous_ids.py"

# key: (created_at, care_id as 0040 issued it). Financial year 2025-26 has a gap (003 was deleted), 2026-27 starts again at 001.
OLD_CARE = {
    "a": ("2025-05-01 08:00:00+00", "CI-25-26-001"),
    "b": ("2025-08-01 08:00:00+00", "CI-25-26-002"),
    "c": ("2026-01-15 08:00:00+00", "CI-25-26-004"),
    "d": ("2026-04-05 08:00:00+00", "CI-26-27-001"),
    "e": ("2026-06-01 08:00:00+00", "CI-26-27-002"),
    "f": ("2026-09-01 08:00:00+00", "CI-26-27-004"),
}
NEW_CARE = {"a": "CI-001", "b": "CI-002", "c": "CI-003", "d": "CI-004", "e": "CI-005", "f": "CI-006"}
# Travellers as 0041 numbered them (a gap in 2026-27 too): the new numbers keep this order.
OLD_TRAVELLER = {"b": "CI-T-25-26-001", "d": "CI-T-26-27-001", "e": "CI-T-26-27-002", "f": "CI-T-26-27-004"}
NEW_TRAVELLER = {"b": "CI-T-001", "d": "CI-T-002", "e": "CI-T-003", "f": "CI-T-004"}


def seed(engine):
    """Candidates and travellers exactly as 0043 left them, then a few more with no ID so the trigger numbers them."""
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')"))
        connection.execute(text("INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')"))
        connection.execute(text("INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')"))
        connection.execute(text("INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)"))
        for index, (key, (created, care)) in enumerate(OLD_CARE.items()):
            connection.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source, created_at, updated_at) "
                                    "VALUES (:id, :ref, :care, :id, :phone, 'walk_in', :created, :created)"),
                               {"id": key, "ref": f"CND-{key}", "care": care, "phone": f"9000000010{index}", "created": created})
        for key, number in OLD_TRAVELLER.items():
            connection.execute(text("UPDATE crm_candidates SET traveller_id = :number WHERE id = :id"), {"id": key, "number": number})
        # `f` travelled on a real date in 2026-27; its application is what a downgrade reads the travel date from.
        connection.execute(text("INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) VALUES ('app-f', 'f', 'order', 11, 'deployed')"))
        connection.execute(text("INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, completed_at) "
                                "VALUES ('stage-f', 'app-f', 11, 1, CAST(:data AS jsonb), '2026-09-10 10:00:00+00')"),
                           {"data": json.dumps({"travelDate": "2026-09-12"})})


def rows(engine, column):
    with engine.begin() as connection:
        return dict(connection.execute(text(f"SELECT id, {column} FROM crm_candidates")).all())


def counters(connection, table):
    return dict(connection.execute(text(f"SELECT series, last_serial FROM {table}")).all())


def test_existing_ids_are_renumbered_contiguously_from_001_in_their_current_order(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, TARGET)

    assert rows(engine, "care_id") == NEW_CARE                   # the CI-25-26-003 gap closed: 001..006, same relative order
    assert rows(engine, "traveller_id") == {**NEW_TRAVELLER, "a": None, "c": None}
    with engine.begin() as connection:
        found = [value for (value,) in connection.execute(text("SELECT care_id FROM crm_candidates ORDER BY care_id")).all()]
        assert found == [f"CI-{n:03d}" for n in range(1, 7)] and len(set(found)) == 6
        # The next number is one past the highest: the counters are set to the new maximum, the fiscal tables are gone.
        assert counters(connection, "crm_candidate_id_counters") == {"GLOBAL": 6}
        assert counters(connection, "crm_traveller_id_counters") == {"GLOBAL": 4}
        assert connection.execute(text("SELECT to_regclass('crm_candidate_fiscal_counters')")).scalar_one() is None
        assert connection.execute(text("SELECT to_regclass('crm_traveller_fiscal_counters')")).scalar_one() is None
        # The CND reference is untouched.
        assert dict(connection.execute(text("SELECT id, ref FROM crm_candidates")).all()) == {key: f"CND-{key}" for key in OLD_CARE}


def test_a_candidate_deleted_before_the_migration_leaves_no_gap_and_ties_follow_entry_time(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM crm_candidates WHERE id = 'b'"))   # b is a traveller too: CI-T-25-26-001 goes with it
        # Two candidates with no ID at all (never numbered): they go last, by entry time then id.
        connection.execute(text("ALTER TABLE crm_candidates DISABLE TRIGGER crm_candidates_assign_care_id"))
        for key, created in (("z2", "2026-10-02 08:00:00+00"), ("z1", "2026-10-01 08:00:00+00")):
            connection.execute(text("INSERT INTO crm_candidates (id, ref, name, phone, source, created_at, updated_at) "
                                    "VALUES (:id, :ref, :id, :phone, 'walk_in', :created, :created)"),
                               {"id": key, "ref": f"CND-{key}", "phone": f"91000000{key[1]}", "created": created})
        connection.execute(text("ALTER TABLE crm_candidates ENABLE TRIGGER crm_candidates_assign_care_id"))
    _migrate(engine, TARGET)
    assert rows(engine, "care_id") == {"a": "CI-001", "c": "CI-002", "d": "CI-003", "e": "CI-004", "f": "CI-005", "z1": "CI-006", "z2": "CI-007"}
    assert rows(engine, "traveller_id") == {"a": None, "c": None, "d": "CI-T-001", "e": "CI-T-002", "f": "CI-T-003", "z1": None, "z2": None}
    with engine.begin() as connection:
        assert counters(connection, "crm_candidate_id_counters") == {"GLOBAL": 7}
        assert counters(connection, "crm_traveller_id_counters") == {"GLOBAL": 3}


def test_an_empty_database_gets_zero_counters_so_the_first_numbers_are_001(schema_database):
    engine = schema_database
    _migrate(engine, "head")
    with engine.begin() as connection:
        assert counters(connection, "crm_candidate_id_counters") == {"GLOBAL": 0}
        assert counters(connection, "crm_traveller_id_counters") == {"GLOBAL": 0}
    with sessionmaker(bind=engine)() as db:
        assert allocate_care_id(db) == "CI-001" and allocate_traveller_id(db) == "CI 01"
        db.commit()


def test_new_ids_continue_after_the_maximum_and_the_deleted_tail_numbers_are_given_again(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "head")
    with sessionmaker(bind=engine)() as db:
        new = Candidate(id="g", ref="CND-g", name="g", phone="90000000200", source="walk_in", care_id=allocate_care_id(db))
        db.add(new)
        db.flush()
        assert new.care_id == "CI-007"                                   # max (CI-006) + 1
        assert assign_traveller_id(db, new) == "CI 05"                   # the next deployed candidate: CI 04 + 1
        assert assign_traveller_id(db, new) == "CI 05"                   # exactly once, idempotent on retry
        db.commit()
        # A direct insert that forgets the ID is numbered by the database trigger from the same counter.
        assert db.execute(text("INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('h', 'CND-h', 'h', '90000000300', 'walk_in') RETURNING care_id")).scalar_one() == "CI-008"
        db.commit()
        # Deleting the highest candidates frees their Data Bank numbers: the next one is the Data Bank maximum (CI-006) + 1.
        # Traveller numbers are a separate series and are still never reused: CI 05 went to g and stays used.
        db.delete(db.get(Candidate, "g"))
        db.delete(db.get(Candidate, "h"))
        db.commit()
        assert allocate_care_id(db) == "CI-007"
        assert allocate_traveller_id(db) == "CI 06"
        db.commit()
        assert counters(db.connection(), "crm_candidate_id_counters") == {"GLOBAL": 7}   # the counter row equals the number just given


def test_ids_grow_naturally_past_999(schema_database):
    engine = schema_database
    _migrate(engine, "head")
    with sessionmaker(bind=engine)() as db:
        # The Data Bank holds CI-998 as its highest number; the Care India number is read from there (the counter row is ignored).
        db.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('top', 'CND-TOP', 'CI-998', 'top', '90000000399', 'walk_in')"))
        db.execute(text("INSERT INTO crm_traveller_id_counters (series, last_serial) VALUES ('GLOBAL', 98) ON CONFLICT (series) DO UPDATE SET last_serial = 98"))
        given = []
        for index in range(3):
            given.append(allocate_care_id(db))
            db.add(Candidate(id=f"n{index}", ref=f"CND-N{index}", name="n", phone=f"9000000040{index}", source="walk_in", care_id=given[-1]))
            db.flush()
        assert given == ["CI-999", "CI-1000", "CI-1001"]
        assert [allocate_traveller_id(db) for _ in range(3)] == ["CI 99", "CI 100", "CI 101"]      # two digits at least, then it simply grows wider
        # The database-side backstop agrees (it must not truncate to three digits).
        db.execute(text("UPDATE crm_candidate_id_counters SET last_serial = 1001"))
        assert db.execute(text("INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('big', 'CND-BIG', 'big', '90000000410', 'walk_in') RETURNING care_id")).scalar_one() == "CI-1002"
        db.commit()


def test_format_constraints_and_immutability_survive_the_renumbering(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "head")
    with engine.begin() as connection:
        for care in ("CI-26-27-001", "CI-1", "CI-01", "CI-000", "CI-0001", "CI-T-001", "ci-007", "CI-007 "):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('x', 'CND-X', :care, 'x', '90000000500', 'walk_in')"), {"care": care})
        for traveller in ("CI-T-26-27-001", "CI-T-001", "CI 1", "CI 00", "CI 001", "ci 01", "CI  01", "CI 01 ", "CI-001", "CI 0"):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("UPDATE crm_candidates SET traveller_id = :traveller WHERE id = 'a'"), {"traveller": traveller})
        with pytest.raises(IntegrityError):    # unique
            with connection.begin_nested():
                connection.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('x', 'CND-X', 'CI-001', 'x', '90000000500', 'walk_in')"))
        with pytest.raises(IntegrityError):    # the immutability triggers were re-enabled after the renumbering
            with connection.begin_nested():
                connection.execute(text("UPDATE crm_candidates SET care_id = 'CI-777' WHERE id = 'a'"))
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE crm_candidates SET traveller_id = 'CI 77' WHERE id = 'b'"))
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE crm_candidates SET traveller_id = NULL WHERE id = 'b'"))
        connection.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('ok', 'CND-OK', 'CI-1000', 'ok', '90000000501', 'walk_in')"))
        connection.execute(text("UPDATE crm_candidates SET traveller_id = 'CI 999' WHERE id = 'a'"))     # NULL -> value is allowed once


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_0044", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_body_is_safe_to_run_again_on_converted_data(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, TARGET)
    before = (rows(engine, "care_id"), rows(engine, "traveller_id"))
    module = _migration_module()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()                 # the old counter tables no longer exist: DROP IF EXISTS makes that harmless
        assert counters(connection, "crm_candidate_id_counters") == {"GLOBAL": 6}
        assert counters(connection, "crm_traveller_id_counters") == {"GLOBAL": 4}
    assert (rows(engine, "care_id"), rows(engine, "traveller_id")) == before


def test_downgrade_restores_a_valid_old_format_and_upgrading_again_gives_the_same_numbers(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "head")
    new_care, new_traveller = rows(engine, "care_id"), rows(engine, "traveller_id")

    _migrate(engine, PREVIOUS, downgrade=True)
    old_care, old_traveller = rows(engine, "care_id"), rows(engine, "traveller_id")
    # Financial year of created_at (India time), serial counted inside it in the current order: a deterministic, gap-free
    # restore (the 2025-26 gap is not recovered: that number is gone).
    assert old_care == {"a": "CI-25-26-001", "b": "CI-25-26-002", "c": "CI-25-26-003", "d": "CI-26-27-001", "e": "CI-26-27-002", "f": "CI-26-27-003"}
    # Financial year of the travel date: f has a real date (12 Sept 2026); the others fall back to when the candidate was last touched.
    assert old_traveller == {"a": None, "c": None, "b": "CI-T-25-26-001", "d": "CI-T-26-27-001", "e": "CI-T-26-27-002", "f": "CI-T-26-27-003"}
    with engine.begin() as connection:
        assert dict(connection.execute(text("SELECT fiscal_year, last_serial FROM crm_candidate_fiscal_counters")).all()) == {"2025-2026": 3, "2026-2027": 3}
        assert dict(connection.execute(text("SELECT fiscal_year, last_serial FROM crm_traveller_fiscal_counters")).all()) == {"2025-2026": 1, "2026-2027": 3}
        assert connection.execute(text("SELECT to_regclass('crm_candidate_id_counters')")).scalar_one() is None
        assert connection.execute(text("SELECT to_regclass('crm_traveller_id_counters')")).scalar_one() is None
        with pytest.raises(IntegrityError):      # the old constraint is back: the new format is refused again, the old one is accepted
            with connection.begin_nested():
                connection.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('x', 'CND-X', 'CI-001', 'x', '90000000600', 'walk_in')"))
        connection.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('x', 'CND-X', 'CI-26-27-009', 'x', '90000000600', 'walk_in')"))
        connection.execute(text("DELETE FROM crm_candidates WHERE id = 'x'"))
        # The restored DB-side trigger numbers a new insert by financial year again, continuing that year's counter.
        issued = connection.execute(text("INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('y', 'CND-Y', 'y', '90000000601', 'walk_in') RETURNING care_id")).scalar_one()
        assert re.fullmatch(r"CI-\d\d-\d\d-\d{3}", issued)
        connection.execute(text("DELETE FROM crm_candidates WHERE id = 'y'"))

    _migrate(engine, "head")
    assert (rows(engine, "care_id"), rows(engine, "traveller_id")) == (new_care, new_traveller)
    with engine.begin() as connection:
        assert counters(connection, "crm_candidate_id_counters") == {"GLOBAL": 6}


def test_concurrent_creations_never_share_a_care_id_or_traveller_number(schema_database):
    engine = schema_database
    _migrate(engine, "head")
    sessions = sessionmaker(bind=engine)

    def create(index):
        with sessions() as db:
            person = Candidate(id=f"p{index}", ref=f"CND-P{index}", name=f"P{index}", phone=f"9100000{index:04d}", source="walk_in", care_id=allocate_care_id(db))
            db.add(person)
            db.flush()
            assign_traveller_id(db, person)
            db.commit()
            return person.care_id, person.traveller_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(16)))
    assert sorted(care for care, _ in results) == [f"CI-{n:03d}" for n in range(1, 17)]
    assert sorted(traveller for _, traveller in results) == [f"CI {n:02d}" for n in range(1, 17)]
    with sessions() as db:
        assert {row.care_id for row in db.scalars(select(Candidate))} == {f"CI-{n:03d}" for n in range(1, 17)}


def test_a_deployed_candidate_with_no_traveller_id_is_numbered_after_the_existing_travellers(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    with engine.begin() as connection:     # deployed, but 0041 / the app never numbered them: should not happen, must not stay blank
        connection.execute(text("INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) VALUES ('app-a', 'a', 'order', 11, 'deployed')"))
    _migrate(engine, TARGET)
    assert rows(engine, "traveller_id") == {**NEW_TRAVELLER, "a": "CI-T-005", "c": None}
    with engine.begin() as connection:
        assert counters(connection, "crm_traveller_id_counters") == {"GLOBAL": 5}
