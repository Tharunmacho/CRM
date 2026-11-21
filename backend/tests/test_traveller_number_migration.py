"""Migration 0057: traveller numbers become ``CI 01`` ... and past travellers are numbered by DATE OF TRAVEL, oldest first.

Real PostgreSQL (isolated test database only). Travellers are seeded as 0056 left them (``CI-T-001`` ..., numbered in the order they were
deployed in the CRM, which is deliberately NOT the order they flew), 0056 -> 0057 is run, and the new order, the format, the counter, the
constraint, the immutability trigger, the downgrade and a second run are asserted.
"""
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Candidate
from app.services.traveller_id import assign_traveller_id
from test_database_naming import _migrate, schema_database  # noqa: F401

PREVIOUS = "0056_stage_order_google_review"
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0057_traveller_number_format.py"

# key: (candidate ref, stage-13 data or None for no ticket record, ticket completed_at, application status, old CI-T number as 0056 holds it)
TRAVELLERS = {
    "newest":   ("CND-A1", {"travelDate": "2026-11-20"}, "2026-11-10 09:00:00+00", "deployed", "CI-T-001"),   # deployed first, flew last
    "oldest":   ("CND-A2", {"travelDate": "2025-01-10"}, "2026-06-01 09:00:00+00", "deployed", "CI-T-002"),   # imported late, flew first
    "mid":      ("CND-A3", {"travelDate": "2025-06-15"}, "2025-06-10 09:00:00+00", "deployed", "CI-T-003"),
    "undated":  ("CND-A4", {"airline": "No date on file"}, "2025-12-25 08:00:00+00", "deployed", "CI-T-004"),  # completion day 25 Dec 2025
    "badday":   ("CND-A5", {"travelDate": "2026-02-31"}, "2025-09-01 08:00:00+00", "deployed", "CI-T-005"),   # impossible day: completion day 1 Sep 2025
    "tie-late": ("CND-A6", {"travelDate": "2026-02-02"}, "2026-01-30 12:00:00+00", "deployed", "CI-T-006"),   # same flight day as two others
    "tie-b":    ("CND-B7", {"travelDate": "2026-02-02"}, "2026-01-30 09:00:00+00", "deployed", "CI-T-007"),   # same completion time as tie-a: by reference
    "tie-a":    ("CND-A7", {"travelDate": "2026-02-02"}, "2026-01-30 09:00:00+00", "deployed", "CI-T-008"),
    "early":    ("CND-A8", {"travelDate": "2026-02-02"}, "2026-01-28 07:00:00+00", "deployed", "CI-T-009"),   # same flight day, ticket done first
    "noticket": ("CND-A9", None, None, "deployed", "CI-T-010"),                                               # no ticket record at all: last
}
# Oldest travel date first; ties by ticket completion time, then candidate reference. "unnumbered" (below) flew on 2025-03-01.
EXPECTED = {
    "oldest": "CI 01", "unnumbered": "CI 02", "mid": "CI 03", "badday": "CI 04", "undated": "CI 05", "early": "CI 06", "tie-a": "CI 07",
    "tie-b": "CI 08", "tie-late": "CI 09", "newest": "CI 10", "noticket": "CI 11", "active": None,
}


def execute(connection, sql, **params):
    connection.execute(text(sql), params)


def seed_base(connection):
    execute(connection, "INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')")
    execute(connection, "INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')")
    execute(connection, "INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')")
    execute(connection, "INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 500)")


def add_person(connection, key, ref, number, status, ticket, completed, index):
    execute(connection, "INSERT INTO crm_candidates (id, ref, name, phone, source, traveller_id) VALUES (:id, :ref, :id, :phone, 'walk_in', :number)",
            id=key, ref=ref, phone=f"90000{index:05d}", number=number)
    execute(connection, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) "
                        "VALUES (:id, :id, 'order', 13, CAST(:status AS application_status))", id=key, status=status)
    if ticket is not None:
        execute(connection, "INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, completed_at) "
                            "VALUES (:id, :id, 13, 1, CAST(:data AS jsonb), CAST(:at AS timestamptz))", id=key, data=json.dumps(ticket), at=completed)


def seed(engine):
    with engine.begin() as c:
        seed_base(c)
        for index, (key, (ref, ticket, completed, status, number)) in enumerate(TRAVELLERS.items()):
            add_person(c, key, ref, number, status, ticket, completed, index)
        # Not travelled: an active application with a ticket-stage draft, no number.
        execute(c, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('active', 'CND-Z1', 'active', '9000099991', 'walk_in')")
        execute(c, "INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status) VALUES ('active', 'active', 'order', 5, 'active')")
        # Deployed with a ticket but never numbered (should not exist): numbered by its date too, so nobody is left blank.
        add_person(c, "unnumbered", "CND-Z2", None, "deployed", {"travelDate": "2025-03-01"}, "2025-02-20 09:00:00+00", 50)
        execute(c, "INSERT INTO crm_traveller_id_counters (series, last_serial) VALUES ('GLOBAL', 12) ON CONFLICT (series) DO UPDATE SET last_serial = 12")


def numbers(engine):
    with engine.begin() as connection:
        return dict(connection.execute(text("SELECT id, traveller_id FROM crm_candidates")).all())


def counter(engine):
    with engine.begin() as connection:
        return connection.execute(text("SELECT last_serial FROM crm_traveller_id_counters WHERE series = 'GLOBAL'")).scalar_one()


def module():
    spec = importlib.util.spec_from_file_location("migration_0057", MIGRATION)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_past_travellers_are_numbered_by_date_of_travel_oldest_first(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    assert numbers(engine)["newest"] == "CI-T-001"                 # as 0056 numbered them: by deployment, not by flight date
    _migrate(engine, "0057_traveller_number_format")

    found = numbers(engine)
    assert found == EXPECTED
    assert counter(engine) == 11                                    # the highest number issued

    with engine.begin() as connection:
        execute(connection, "INSERT INTO crm_candidates (id, ref, name, phone, source) VALUES ('probe', 'CND-PROBE', 'probe', '9000088888', 'walk_in')")
        for bad in ("CI-T-001", "CI-T-26-27-001", "CI 1", "CI 0", "CI 00", "CI 001", "CI 0100", "ci 13", "CI  13", "CI 13 ", "CI-13", "CI13", "CI 1x"):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    execute(connection, "UPDATE crm_candidates SET traveller_id = :bad WHERE id = 'probe'", bad=bad)
        with pytest.raises(IntegrityError):                          # unique
            with connection.begin_nested():
                execute(connection, "UPDATE crm_candidates SET traveller_id = 'CI 01' WHERE id = 'probe'")
        for statement in ("UPDATE crm_candidates SET traveller_id = 'CI 77' WHERE id = 'oldest'",       # the immutability trigger is back
                          "UPDATE crm_candidates SET traveller_id = NULL WHERE id = 'oldest'"):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(statement))
        execute(connection, "UPDATE crm_candidates SET traveller_id = 'CI 12' WHERE id = 'probe'")      # NULL -> a valid new number is accepted once
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                execute(connection, "UPDATE crm_candidates SET traveller_id = 'CI 100' WHERE id = 'probe'")
        execute(connection, "DELETE FROM crm_candidates WHERE id = 'probe'")


def test_new_travellers_continue_the_same_series_after_the_highest_number(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "0057_traveller_number_format")
    with sessionmaker(bind=engine)() as db:
        newcomer = Candidate(id="newcomer", ref="CND-NEW", name="newcomer", phone="9000077777", source="walk_in")
        db.add(newcomer)
        db.flush()
        assert assign_traveller_id(db, newcomer) == "CI 12"           # highest ever issued (CI 11) + 1
        assert assign_traveller_id(db, newcomer) == "CI 12"           # exactly once
        db.commit()
        db.delete(newcomer)                                           # a number is never reused, even after the candidate is gone
        db.commit()
        other = Candidate(id="other", ref="CND-OTHER", name="other", phone="9000077778", source="walk_in")
        db.add(other)
        db.flush()
        assert assign_traveller_id(db, other) == "CI 13"
        db.commit()
        assert db.scalar(select(Candidate.traveller_id).where(Candidate.id == "oldest")) == "CI 01"   # nobody is renumbered by this


def test_running_the_migration_body_again_changes_nothing(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "0057_traveller_number_format")
    with sessionmaker(bind=engine)() as db:                           # a traveller who joined AFTER the conversion
        newcomer = Candidate(id="newcomer", ref="CND-AAA", name="newcomer", phone="9000077777", source="walk_in")
        db.add(newcomer)
        db.flush()
        assign_traveller_id(db, newcomer)
        db.commit()
    before = numbers(engine)
    assert before["newcomer"] == "CI 12"
    migration = module()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    assert numbers(engine) == before                                  # the new traveller was not shuffled in by date or reference
    assert counter(engine) == 12


def test_downgrade_gives_back_the_old_format_in_the_same_order_and_upgrading_again_agrees(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    seed(engine)
    _migrate(engine, "0057_traveller_number_format")
    converted = numbers(engine)
    assert converted == EXPECTED
    _migrate(engine, PREVIOUS, downgrade=True)
    old = numbers(engine)
    assert old == {key: (None if value is None else f"CI-T-{int(value[3:]):03d}") for key, value in converted.items()}
    assert old["oldest"] == "CI-T-001" and old["noticket"] == "CI-T-011"
    assert counter(engine) == 11
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):                          # the old constraint is back: the new format is refused, the old one accepted
            with connection.begin_nested():
                execute(connection, "UPDATE crm_candidates SET traveller_id = 'CI 12' WHERE id = 'active'")
        execute(connection, "UPDATE crm_candidates SET traveller_id = 'CI-T-012' WHERE id = 'active'")
        with pytest.raises(IntegrityError):                          # and so is the trigger
            with connection.begin_nested():
                execute(connection, "UPDATE crm_candidates SET traveller_id = 'CI-T-099' WHERE id = 'active'")
    # Upgrading again converts the same people the same way.
    with engine.begin() as connection:
        execute(connection, "ALTER TABLE crm_candidates DISABLE TRIGGER crm_candidates_keep_traveller_id_immutable")
        execute(connection, "UPDATE crm_candidates SET traveller_id = NULL WHERE id = 'active'")
        execute(connection, "ALTER TABLE crm_candidates ENABLE TRIGGER crm_candidates_keep_traveller_id_immutable")
    _migrate(engine, "0057_traveller_number_format")
    assert numbers(engine) == converted


def test_numbers_grow_wider_after_99_and_the_order_holds_across_a_hundred_travellers(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    count = 105
    with engine.begin() as c:
        seed_base(c)
        # Deployment order is the REVERSE of travel order: person 1 flew last.
        for index in range(1, count + 1):
            day = f"2025-{(count - index) // 28 + 1:02d}-{(count - index) % 28 + 1:02d}"
            add_person(c, f"p{index:03d}", f"CND-{index:04d}", f"CI-T-{index:03d}", "deployed", {"travelDate": day}, f"{day} 08:00:00+00", index)
    _migrate(engine, "0057_traveller_number_format")
    found = numbers(engine)
    assert found["p105"] == "CI 01" and found["p104"] == "CI 02" and found["p007"] == "CI 99" and found["p006"] == "CI 100" and found["p001"] == "CI 105"
    assert sorted(found.values(), key=lambda value: int(value[3:])) == [f"CI {n:02d}" if n < 100 else f"CI {n}" for n in range(1, count + 1)]
    assert counter(engine) == count
    _migrate(engine, PREVIOUS, downgrade=True)
    assert numbers(engine)["p001"] == "CI-T-105" and numbers(engine)["p105"] == "CI-T-001"


def test_an_empty_database_is_untouched_apart_from_the_new_rule(schema_database):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _migrate(engine, "0057_traveller_number_format")
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM crm_candidates")).scalar_one() == 0
        assert dict(connection.execute(text("SELECT series, last_serial FROM crm_traveller_id_counters")).all()) == {"GLOBAL": 0}
        execute(connection, "INSERT INTO crm_candidates (id, ref, name, phone, source, traveller_id) VALUES ('x', 'CND-X', 'x', '9000000001', 'walk_in', 'CI 01')")
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                execute(connection, "INSERT INTO crm_candidates (id, ref, name, phone, source, traveller_id) VALUES ('y', 'CND-Y', 'y', '9000000002', 'walk_in', 'CI-T-001')")
    _migrate(engine, PREVIOUS, downgrade=True)
    _migrate(engine, "0057_traveller_number_format")
