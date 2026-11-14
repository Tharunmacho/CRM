"""Care India TRAVELLER IDs (CI-T-YY-YY-NNN): a series separate from the Data Bank Care India ID, numbered by the
financial year of the travel date, assigned when the placement is deployed (stage 11) and immutable afterwards."""
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Candidate, Role
from app.services.care_india_id import allocate_care_id, fiscal_key
from app.services.traveller_id import (
    allocate_traveller_id, assign_traveller_id, format_traveller_id, travel_moment,
)
from test_application_workflow import complete, concurrent, mapped, payload, reach, save, subject, version, workflow  # noqa: F401
from test_database_naming import _migrate, schema_database  # noqa: F401


class CounterSession:
    """Stands in for the database: one serial counter per (table, financial year), like the real upserts."""

    def __init__(self):
        self.serials: dict[tuple[str, str], int] = {}
        self.calls = []

    def execute(self, statement, params):
        sql = str(statement)
        table = "traveller" if "crm_traveller_fiscal_counters" in sql else "candidate"
        key = (table, params["fiscal_year"])
        self.serials[key] = self.serials.get(key, 0) + 1
        self.calls.append((sql, params))
        return SimpleNamespace(scalar_one=lambda value=self.serials[key]: value)


def test_format_is_ci_t_with_two_fiscal_years_and_a_serial_that_grows_past_three_digits():
    assert format_traveller_id("2026-2027", 1) == "CI-T-26-27-001"
    assert format_traveller_id("2026-2027", 42) == "CI-T-26-27-042"
    assert format_traveller_id("2026-2027", 1234) == "CI-T-26-27-1234"
    assert format_traveller_id("2099-2100", 7) == "CI-T-99-00-007"
    with pytest.raises(ValueError):
        format_traveller_id("2026-2027", 0)


def test_financial_year_comes_from_the_travel_date_on_the_april_boundary_in_india():
    # A plain travel date is already an agency-local day.
    assert fiscal_key(travel_moment(date(2027, 3, 31))) == "2026-2027"
    assert fiscal_key(travel_moment("2027-04-01")) == "2027-2028"
    # A timestamp (the fallback when no date is on file) is read in Asia/Kolkata: 18:29 UTC is still 31 March there.
    assert fiscal_key(travel_moment(None, datetime(2027, 3, 31, 18, 29, tzinfo=timezone.utc))) == "2026-2027"
    assert fiscal_key(travel_moment(None, datetime(2027, 3, 31, 18, 30, tzinfo=timezone.utc))) == "2027-2028"
    # A blank, malformed or impossible date counts as absent; the date wins over the fallback when it is real.
    fallback = datetime(2027, 6, 1, 6, 0, tzinfo=timezone.utc)
    for bad in ("", "  ", "15/11/2026", "2026-02-31", None):
        assert travel_moment(bad, fallback) is fallback
    assert travel_moment("2026-11-15", fallback) == date(2026, 11, 15)


def test_serial_restarts_each_april_and_is_independent_of_the_data_bank_care_id():
    db = CounterSession()
    assert allocate_traveller_id(db, date(2026, 4, 1)) == "CI-T-26-27-001"
    assert allocate_traveller_id(db, date(2027, 3, 31)) == "CI-T-26-27-002"
    assert allocate_traveller_id(db, date(2027, 4, 1)) == "CI-T-27-28-001"      # new financial year, new 001
    assert allocate_traveller_id(db, date(2026, 11, 15)) == "CI-T-26-27-003"
    # The Data Bank counter is a different table with its own serials: using one never moves the other.
    assert allocate_care_id(db, date(2026, 4, 1)) == "CI-26-27-001"
    assert allocate_traveller_id(db, date(2026, 4, 2)) == "CI-T-26-27-004"
    assert allocate_care_id(db, date(2026, 4, 2)) == "CI-26-27-002"
    tables = [("crm_traveller_fiscal_counters" in sql, "crm_candidate_fiscal_counters" in sql) for sql, _ in db.calls]
    assert tables == [(True, False)] * 4 + [(False, True), (True, False), (False, True)]


def test_assignment_is_idempotent_and_never_renumbers():
    db = CounterSession()
    person = SimpleNamespace(traveller_id=None)
    first = assign_traveller_id(db, person, "2026-11-15", datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert first == person.traveller_id == "CI-T-26-27-001"
    # Reversed and completed again, with a travel date in another financial year: the number stays and none is consumed.
    assert assign_traveller_id(db, person, "2027-05-01") == first
    assert assign_traveller_id(db, person, None, datetime(2030, 1, 1, tzinfo=timezone.utc)) == first
    assert len(db.calls) == 1
    # No usable travel date: the moment the stage was completed decides the year.
    other = SimpleNamespace(traveller_id=None)
    assert assign_traveller_id(db, other, "not a date", datetime(2027, 3, 31, 19, 0, tzinfo=timezone.utc)) == "CI-T-27-28-001"


# --------------------------------------------------------------- migration backfill

CANDIDATES = {   # id: (created_at, deployed application? ...) built below
    "a": "2025-06-01", "b": "2026-01-01", "c": "2025-12-01", "d": "2026-02-01", "e": "2026-02-02", "f": "2026-02-03",
    "g": "2026-02-04", "h": "2026-02-05", "never": "2026-02-06",
}


def test_migration_backfills_travelled_candidates_by_travel_date_and_guards_the_number(schema_database):
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
        # id: (application status, stage-11 payload or None for no stage row, stage completed_at, application updated_at)
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
    _migrate(engine, "head")

    expected = {
        "a": "CI-T-25-26-001",
        "c": "CI-T-26-27-001", "b": "CI-T-26-27-002", "f": "CI-T-26-27-003", "d": "CI-T-26-27-004", "g": "CI-T-26-27-005",
        "e": "CI-T-27-28-001",
        "h": None, "never": None,
    }
    with engine.begin() as connection:
        assert dict(connection.execute(text("SELECT id, traveller_id FROM crm_candidates")).all()) == expected
        # The Data Bank ID is a different series and was not touched by this migration.
        assert all(care.startswith("CI-") and not care.startswith("CI-T-") for (care,) in connection.execute(text("SELECT care_id FROM crm_candidates")).all())
        assert dict(connection.execute(text("SELECT fiscal_year, last_serial FROM crm_traveller_fiscal_counters")).all()) == {
            "2025-2026": 1, "2026-2027": 5, "2027-2028": 1}

        # Newly travelled people continue after the backfill.
        connection.execute(text("UPDATE crm_candidates SET traveller_id = 'CI-T-26-27-006' WHERE id = 'h'"))   # NULL -> value is allowed once
        for statement in (
            "UPDATE crm_candidates SET traveller_id = 'CI-T-26-27-007' WHERE id = 'h'",                         # changing it
            "UPDATE crm_candidates SET traveller_id = NULL WHERE id = 'a'",                                    # removing it
            "UPDATE crm_candidates SET traveller_id = 'CI-T-26-27-001' WHERE id = 'never'",                    # a duplicate
            "UPDATE crm_candidates SET traveller_id = 'CI-T-26-28-001' WHERE id = 'never'",                    # years that do not follow
            "UPDATE crm_candidates SET traveller_id = 'CI-26-27-001' WHERE id = 'never'",                      # the Data Bank format
            "UPDATE crm_candidates SET traveller_id = 'CI-T-26-27-000' WHERE id = 'never'",                    # serial must be positive
        ):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(statement))
        assert connection.execute(text("SELECT traveller_id FROM crm_candidates WHERE id = 'h'")).scalar_one() == "CI-T-26-27-006"

    # The backfill is deterministic: removing it and applying it again gives every person the same number.
    _migrate(engine, "0040_care_india_candidate_ids", downgrade=True)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_name = 'crm_candidates' AND column_name = 'traveller_id'")).scalar_one() == 0
        assert connection.execute(text("SELECT to_regclass('crm_traveller_fiscal_counters')")).scalar_one() is None
    _migrate(engine, "head")
    with engine.begin() as connection:
        assert dict(connection.execute(text("SELECT id, traveller_id FROM crm_candidates")).all()) == {**expected, "h": None}


# ------------------------------------------------------- assignment when stage 11 completes

def travel(env, candidate, order, day):
    app_id = mapped(env, candidate, order)
    reach(env, app_id, 11)
    data = payload(env, app_id, 11)
    data["travelDate"] = day
    complete(env, app_id, data)
    return app_id


def ids(env):
    with env.sessions() as db:
        return {c.id: (c.care_id, c.traveller_id) for c in db.scalars(select(Candidate))}


def test_completing_stage_11_numbers_the_traveller_by_the_financial_year_of_the_travel_date(workflow):
    env = workflow
    assert all(traveller is None for _care, traveller in ids(env).values())     # nobody has one before travelling
    travel(env, "candidate", "order", "2027-03-31")
    travel(env, "second-candidate", "order", "2027-04-01")
    travel(env, "third-candidate", "other-order", "2026-11-15")
    found = ids(env)
    assert found["candidate"][1] == "CI-T-26-27-001"
    assert found["second-candidate"][1] == "CI-T-27-28-001"      # 1 April: the serial restarts
    assert found["third-candidate"][1] == "CI-T-26-27-002"       # numbered by travel date, not by who finished first
    # The Data Bank ID is untouched and a different number in a different series.
    assert all(care and care.startswith("CI-") and not care.startswith("CI-T-") for care, _ in found.values())
    assert len({care for care, _ in found.values()}) == 3


def test_a_reversed_placement_keeps_its_traveller_id_when_completed_again(workflow):
    env = workflow
    app_id = travel(env, "candidate", "order", "2026-11-15")
    assert ids(env)["candidate"][1] == "CI-T-26-27-001"
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    response = env.client.post(f"/api/applications/{app_id}/reopen", json={
        "stage": 11, "reason": "Ticket corrected", "lock_version": version(env, app_id)})
    assert response.status_code == 200, response.text
    assert ids(env)["candidate"][1] == "CI-T-26-27-001"          # reversed from travelled: the number stays
    env.actor = SimpleNamespace(id="staff", role=Role.staff)
    draft = env.client.get(f"/api/applications/{app_id}/draft").json()["draft"]
    complete(env, app_id, {**draft, "travelDate": "2027-05-01"})  # now a later financial year
    assert ids(env)["candidate"][1] == "CI-T-26-27-001"          # never renumbered
    with env.sessions() as db:
        counters = dict(db.execute(text("SELECT fiscal_year, last_serial FROM crm_traveller_fiscal_counters")).all())
    assert counters == {"2026-2027": 1}                          # and the second completion consumed no number


def test_concurrent_completions_get_distinct_traveller_numbers(workflow):
    env = workflow
    app_ids = [mapped(env, candidate=key) for key in ("candidate", "second-candidate")]
    for app_id in app_ids:
        reach(env, app_id, 11)
        data = payload(env, app_id, 11)
        data["travelDate"] = "2026-11-15"
        assert save(env, app_id, data).status_code == 200
    locks = [version(env, app_id) for app_id in app_ids]

    def finish(index):
        with env.sessions() as db:
            subject.complete_stage(app_ids[index], subject.CompleteIn(lock_version=locks[index]), db, env.actor)

    assert concurrent(lambda: finish(0), lambda: finish(1)) == [200, 200]
    assert sorted(traveller for _care, traveller in ids(env).values() if traveller) == ["CI-T-26-27-001", "CI-T-26-27-002"]


def test_a_rolled_back_transaction_does_not_consume_a_traveller_number(schema_database):
    _migrate(schema_database, "head")
    with sessionmaker(bind=schema_database)() as db:
        assert allocate_traveller_id(db, date(2026, 11, 15)) == "CI-T-26-27-001"
        db.rollback()
        assert db.scalar(text("SELECT count(*) FROM crm_traveller_fiscal_counters")) == 0
        assert allocate_traveller_id(db, date(2026, 11, 16)) == "CI-T-26-27-001"
        db.commit()
