"""Care India TRAVELLER numbers (CI 01, CI 02 ... CI 99, CI 100 ...): one continuous series separate from the Data Bank Care India ID,
assigned dynamically when the placement is deployed (stage 13), never before, never reused, immutable afterwards."""
import json
import re
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Candidate, Role
from app.services.care_india_id import allocate_care_id
from app.services.traveller_id import allocate_traveller_id, assign_traveller_id, format_traveller_id
from test_application_workflow import complete, concurrent, mapped, payload, reach, save, subject, version, workflow  # noqa: F401
from test_database_naming import _migrate, schema_database  # noqa: F401


class CounterSession:
    """Stands in for the database.

    Traveller IDs: one serial counter row, bumped by one upsert per call.  Care India IDs: the number is read from the Data Bank
    (highest existing number + 1, here the last number given, as nothing is ever deleted in this fake) and written back to the
    counter row, so the fake keeps ``care_last`` and answers the "highest + 1" read from it.
    """

    def __init__(self):
        self.serials: dict[str, int] = {}
        self.care_last = 0
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "crm_traveller_id_counters" in sql:
            self.serials["traveller"] = self.serials.get("traveller", 0) + 1
            return SimpleNamespace(scalar_one=lambda value=self.serials["traveller"]: value)
        if "pg_advisory_xact_lock" in sql:
            return SimpleNamespace()
        if "FROM crm_candidates" in sql:                           # ONE statement: highest existing number + 1, written back to the counter row
            self.care_last += 1
            return SimpleNamespace(scalar_one=lambda value=self.care_last: value)
        raise AssertionError(sql)


def test_format_is_ci_space_two_digits_and_grows_wider_after_99():
    for serial, expected in ((1, "CI 01"), (2, "CI 02"), (9, "CI 09"), (10, "CI 10"), (42, "CI 42"), (99, "CI 99"), (100, "CI 100"),
                             (101, "CI 101"), (999, "CI 999"), (1234, "CI 1234")):
        assert format_traveller_id(serial) == expected
    with pytest.raises(ValueError):
        format_traveller_id(0)


def test_serial_is_one_continuous_series_independent_of_the_data_bank_care_id():
    db = CounterSession()
    assert [allocate_traveller_id(db) for _ in range(3)] == ["CI 01", "CI 02", "CI 03"]   # no restart, whatever the date
    # The Data Bank counter is a different table with its own serials: using one never moves the other.
    assert allocate_care_id(db) == "CI-001"
    assert allocate_traveller_id(db) == "CI 04"
    assert allocate_care_id(db) == "CI-002"
    tables = [("crm_traveller_id_counters" in sql, "crm_candidate_id_counters" in sql or "FROM crm_candidates" in sql) for sql, _ in db.calls]
    # Each traveller call is one statement on its own table; each Care India call is the lock + one read-and-write statement, never the traveller table.
    assert tables == [(True, False)] * 3 + [(False, False), (False, True)] + [(True, False)] + [(False, False), (False, True)]
    assert {params["series"] for sql, params in db.calls if "crm_traveller_id_counters" in sql or "INSERT INTO crm_candidate_id_counters" in sql} == {"GLOBAL"}


def test_assignment_is_idempotent_and_never_renumbers():
    db = CounterSession()
    person = SimpleNamespace(traveller_id=None)
    first = assign_traveller_id(db, person)
    assert first == person.traveller_id == "CI 01"
    # Reversed and completed again: the number stays and none is consumed.
    assert assign_traveller_id(db, person) == first
    assert assign_traveller_id(db, person) == first
    assert len(db.calls) == 1
    assert assign_traveller_id(db, SimpleNamespace(traveller_id=None)) == "CI 02"


# --------------------------------------------------------------- migration backfill

CANDIDATES = {   # id: (created_at, deployed application? ...) built below
    "a": "2025-06-01", "b": "2026-01-01", "c": "2025-12-01", "d": "2026-02-01", "e": "2026-02-02", "f": "2026-02-03",
    "g": "2026-02-04", "h": "2026-02-05", "never": "2026-02-06",
}


def test_migration_0044_backfills_travelled_candidates_by_travel_date_then_renumbers_them_continuously(schema_database):
    engine = schema_database
    _migrate(engine, "0040_care_india_candidate_ids")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO crm_client_contacts (id, name) VALUES ('client', 'Client')"))
        connection.execute(text("INSERT INTO crm_employer_companies (id, client_id, name) VALUES ('company', 'client', 'Company')"))
        connection.execute(text("INSERT INTO crm_job_designations (id, title) VALUES ('job', 'Welder')"))
        connection.execute(text("INSERT INTO crm_job_orders (id, ref, company_id, designation_id, openings) VALUES ('order', 'ORD-1', 'company', 'job', 20)"))
        for key, created in CANDIDATES.items():
            connection.execute(text("INSERT INTO crm_candidates (id, ref, name, phone, source, created_at, updated_at) "
                                    "VALUES (:id, :ref, :id, :phone, 'walk_in', :created, :created)"),
                               {"id": key, "ref": f"CND-{key}", "phone": f"90000{len(key)}{ord(key[0])}", "created": created + " 08:00:00+00"})
        # id: (application status, stage-11 payload (the old numbering: the seed predates 0048) or None for no stage row, stage completed_at, application updated_at)
        placements = {
            "a": ("deployed", {"travelDate": "2026-03-31"}, "2026-03-31 10:00:00+00", "2026-03-31 10:00:00+00"),    # last day of FY 25-26
            "b": ("deployed", {"travelDate": "2026-04-01"}, "2026-03-20 10:00:00+00", "2026-03-20 10:00:00+00"),    # first day of FY 26-27
            "c": ("deployed", {"travelDate": "2026-04-01"}, "2026-03-21 10:00:00+00", "2026-03-21 10:00:00+00"),    # same day, entered earlier
            "d": ("deployed", {"travelDate": "2026-09-15"}, "2026-09-01 10:00:00+00", "2026-09-01 10:00:00+00"),
            "e": ("deployed", {"airline": "No date on file"}, "2027-03-31 20:00:00+00", "2027-03-31 20:00:00+00"),  # 1 April 01:30 in India
            "f": ("deployed", {"travelDate": "2026-02-31"}, "2026-05-01 10:00:00+00", "2026-05-01 10:00:00+00"),    # impossible date: completion day
            "g": ("deployed", None, None, "2026-12-10 10:00:00+00"),                                               # no stage row at all
            "h": ("active", {"travelDate": "2026-05-01"}, "2026-05-01 10:00:00+00", "2026-05-01 10:00:00+00"),      # not travelled
        }
        for key, (status, stage, completed, updated) in placements.items():
            connection.execute(text("INSERT INTO crm_applications (id, candidate_id, order_id, current_stage, status, created_at, updated_at) "
                                    "VALUES (:id, :id, 'order', 11, :status, :updated, :updated)"), {"id": key, "status": status, "updated": updated})
            if stage is not None:
                connection.execute(text("INSERT INTO crm_application_stages (id, application_id, stage, attempt, data, completed_at) "
                                        "VALUES (:id, :id, 11, 1, CAST(:data AS jsonb), :completed)"),
                                   {"id": key, "data": json.dumps(stage), "completed": completed})
    _migrate(engine, "0043_stage_stop_exits")
    with engine.begin() as connection:   # 0041 numbered them by the financial year of the travel date
        assert dict(connection.execute(text("SELECT id, traveller_id FROM crm_candidates WHERE traveller_id IS NOT NULL")).all()) == {
            "a": "CI-T-25-26-001", "c": "CI-T-26-27-001", "b": "CI-T-26-27-002", "f": "CI-T-26-27-003", "d": "CI-T-26-27-004",
            "g": "CI-T-26-27-005", "e": "CI-T-27-28-001"}
    _migrate(engine, "0044_simple_continuous_ids")

    # 0044 renumbers them contiguously in that same travel order, with no financial year in the number.
    expected = {
        "a": "CI-T-001", "c": "CI-T-002", "b": "CI-T-003", "f": "CI-T-004", "d": "CI-T-005", "g": "CI-T-006", "e": "CI-T-007",
        "h": None, "never": None,
    }
    with engine.begin() as connection:
        assert dict(connection.execute(text("SELECT id, traveller_id FROM crm_candidates")).all()) == expected
        # The Data Bank ID is a different series: CI-001.. for every candidate, no CI-T- ones.
        assert sorted(care for (care,) in connection.execute(text("SELECT care_id FROM crm_candidates")).all()) == [f"CI-{n:03d}" for n in range(1, 10)]
        assert dict(connection.execute(text("SELECT series, last_serial FROM crm_traveller_id_counters")).all()) == {"GLOBAL": 7}

        # Newly travelled people continue after the backfill.
        connection.execute(text("UPDATE crm_candidates SET traveller_id = 'CI-T-008' WHERE id = 'h'"))   # NULL -> value is allowed once
        for statement in (
            "UPDATE crm_candidates SET traveller_id = 'CI-T-009' WHERE id = 'h'",                              # changing it
            "UPDATE crm_candidates SET traveller_id = NULL WHERE id = 'a'",                                    # removing it
            "UPDATE crm_candidates SET traveller_id = 'CI-T-001' WHERE id = 'never'",                          # a duplicate
            "UPDATE crm_candidates SET traveller_id = 'CI-T-26-27-001' WHERE id = 'never'",                    # the old financial-year format
            "UPDATE crm_candidates SET traveller_id = 'CI-001' WHERE id = 'never'",                            # the Data Bank format
            "UPDATE crm_candidates SET traveller_id = 'CI-T-000' WHERE id = 'never'",                          # serial must be positive
            "UPDATE crm_candidates SET traveller_id = 'CI-T-1' WHERE id = 'never'",                            # at least three digits
        ):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(statement))
        assert connection.execute(text("SELECT traveller_id FROM crm_candidates WHERE id = 'h'")).scalar_one() == "CI-T-008"

    # The backfill is deterministic: removing it and applying it again gives every person the same number.
    _migrate(engine, "0040_care_india_candidate_ids", downgrade=True)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_name = 'crm_candidates' AND column_name = 'traveller_id'")).scalar_one() == 0
        assert connection.execute(text("SELECT to_regclass('crm_traveller_fiscal_counters')")).scalar_one() is None
        assert connection.execute(text("SELECT to_regclass('crm_traveller_id_counters')")).scalar_one() is None
    _migrate(engine, "0044_simple_continuous_ids")
    with engine.begin() as connection:
        assert dict(connection.execute(text("SELECT id, traveller_id FROM crm_candidates")).all()) == {**expected, "h": None}


# ------------------------------------------------------- assignment when stage 13 completes

def travel(env, candidate, order, day):
    app_id = mapped(env, candidate, order)
    reach(env, app_id, 13)
    data = payload(env, app_id, 13)
    data["travelDate"] = day
    complete(env, app_id, data)
    return app_id


def ids(env):
    with env.sessions() as db:
        return {c.id: (c.care_id, c.traveller_id) for c in db.scalars(select(Candidate))}


def test_completing_stage_13_gives_the_next_traveller_number_in_the_order_people_travel(workflow):
    env = workflow
    assert all(traveller is None for _care, traveller in ids(env).values())     # nobody has one before travelling
    travel(env, "candidate", "order", "2027-03-31")
    travel(env, "second-candidate", "order", "2027-04-01")
    travel(env, "third-candidate", "other-order", "2026-11-15")
    found = ids(env)
    # One continuous series: numbered as each placement is deployed, whatever the travel date, with no restart on 1 April.
    assert found["candidate"][1] == "CI 01"
    assert found["second-candidate"][1] == "CI 02"
    assert found["third-candidate"][1] == "CI 03"
    # The Data Bank ID is untouched and a different number in a different series.
    assert all(care and re.fullmatch(r"CI-\d{3}", care) for care, _ in found.values())
    assert len({care for care, _ in found.values()}) == 3


def test_a_reversed_placement_keeps_its_traveller_id_when_completed_again(workflow):
    env = workflow
    app_id = travel(env, "candidate", "order", "2026-11-15")
    assert ids(env)["candidate"][1] == "CI 01"
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    response = env.client.post(f"/api/applications/{app_id}/reopen", json={
        "stage": 13, "reason": "Ticket corrected", "lock_version": version(env, app_id)})
    assert response.status_code == 200, response.text
    assert ids(env)["candidate"][1] == "CI 01"                # reversed from travelled: the number stays
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    draft = env.client.get(f"/api/applications/{app_id}/draft").json()["draft"]
    complete(env, app_id, {**draft, "travelDate": "2027-05-01"})  # a later date
    assert ids(env)["candidate"][1] == "CI 01"                # never renumbered
    with env.sessions() as db:
        counters = dict(db.execute(text("SELECT series, last_serial FROM crm_traveller_id_counters")).all())
    assert counters == {"GLOBAL": 1}                             # and the second completion consumed no number
    travel(env, "second-candidate", "order", "2027-06-01")
    assert ids(env)["second-candidate"][1] == "CI 02"         # the next traveller continues the series


def test_concurrent_completions_get_distinct_traveller_numbers(workflow):
    env = workflow
    app_ids = [mapped(env, candidate=key) for key in ("candidate", "second-candidate")]
    for app_id in app_ids:
        reach(env, app_id, 13)
        data = payload(env, app_id, 13)
        data["travelDate"] = "2026-11-15"
        assert save(env, app_id, data).status_code == 200
    locks = [version(env, app_id) for app_id in app_ids]

    def finish(index):
        with env.sessions() as db:
            subject.complete_stage(app_ids[index], subject.CompleteIn(lock_version=locks[index]), db, env.actor)

    assert concurrent(lambda: finish(0), lambda: finish(1)) == [200, 200]
    assert sorted(traveller for _care, traveller in ids(env).values() if traveller) == ["CI 01", "CI 02"]


def test_a_rolled_back_transaction_does_not_consume_a_traveller_number(schema_database):
    _migrate(schema_database, "head")
    with sessionmaker(bind=schema_database)() as db:
        assert allocate_traveller_id(db) == "CI 01"
        db.rollback()
        assert db.scalar(text("SELECT last_serial FROM crm_traveller_id_counters WHERE series = 'GLOBAL'")) == 0
        assert allocate_traveller_id(db) == "CI 01"
        db.commit()


def test_deleting_a_traveller_never_frees_their_number_for_the_next_one(schema_database):
    _migrate(schema_database, "head")
    with sessionmaker(bind=schema_database)() as db:
        for key in ("one", "two", "three"):
            person = Candidate(id=key, ref=f"CND-{key}", name=key, phone=f"9000000{len(key)}{ord(key[0])}", source="walk_in", care_id=allocate_care_id(db))
            db.add(person)
            db.flush()
            assign_traveller_id(db, person)
        db.commit()
        assert [c.traveller_id for c in db.scalars(select(Candidate).order_by(Candidate.id))] == ["CI 01", "CI 03", "CI 02"]  # ids sort one, three, two
        db.delete(db.get(Candidate, "three"))                 # the highest number leaves ...
        db.commit()
        fresh = Candidate(id="four", ref="CND-four", name="four", phone="90000009999", source="walk_in", care_id=allocate_care_id(db))
        db.add(fresh)
        db.flush()
        assert assign_traveller_id(db, fresh) == "CI 04"    # ... and CI 03 is not issued again (a traveller number is never reused)
        assert fresh.care_id == "CI-003"                        # but the Data Bank ID IS: "three" held CI-003, the highest, and it is given again
