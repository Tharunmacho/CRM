"""Care India public candidate IDs: April--March, safe alongside legacy CND refs."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services.care_india_id import allocate_care_id, fiscal_key, fiscal_year, format_care_id
from test_database_naming import _migrate, schema_database


def test_financial_year_uses_asia_kolkata_april_boundary():
    # 31 March 18:29 UTC is still 31 March in India; one minute later is 1 April.
    assert fiscal_year(datetime(2026, 3, 31, 18, 29, tzinfo=timezone.utc)) == (2025, 2026)
    assert fiscal_key(datetime(2026, 3, 31, 18, 30, tzinfo=timezone.utc)) == "2026-2027"
    assert format_care_id("2026-2027", 1) == "CI-26-27-001"
    assert format_care_id("2026-2027", 1234) == "CI-26-27-1234"
    with pytest.raises(ValueError):
        format_care_id("2026-2027", 0)


def test_allocator_uses_one_counter_per_financial_year():
    class CounterSession:
        def __init__(self):
            self.serials = iter((1, 2, 1))
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return SimpleNamespace(scalar_one=lambda: next(self.serials))

    db = CounterSession()
    april = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    march = datetime(2027, 3, 31, 17, 0, tzinfo=timezone.utc)
    next_april = datetime(2027, 4, 1, 0, 0, tzinfo=timezone.utc)
    assert allocate_care_id(db, april) == "CI-26-27-001"
    assert allocate_care_id(db, march) == "CI-26-27-002"
    assert allocate_care_id(db, next_april) == "CI-27-28-001"
    assert all("crm_candidate_fiscal_counters" in query for query, _ in db.calls)
    assert [params["fiscal_year"] for _, params in db.calls] == ["2026-2027", "2026-2027", "2027-2028"]


def test_migration_backfills_legacy_refs_and_database_guard_keeps_care_id_immutable(schema_database):
    engine = schema_database
    _migrate(engine, "0039_lead_experience_edits")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO crm_candidates (id, ref, name, phone, source, created_at, updated_at)
            VALUES
              ('march', 'CND-LEGACY-MARCH', 'March legacy', '9000000001', 'walk_in', '2026-03-31 18:29:00+00', '2026-03-31 18:29:00+00'),
              ('april', 'CND-LEGACY-APRIL', 'April legacy', '9000000002', 'walk_in', '2026-03-31 18:30:00+00', '2026-03-31 18:30:00+00')
        """))
    _migrate(engine, "head")
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT id, ref, care_id FROM crm_candidates ORDER BY id")).all()
        assert rows == [
            ("april", "CND-LEGACY-APRIL", "CI-26-27-001"),
            ("march", "CND-LEGACY-MARCH", "CI-25-26-001"),
        ]
        direct = connection.execute(text("""
            INSERT INTO crm_candidates (id, ref, name, phone, source)
            VALUES ('direct', 'CND-DIRECT', 'Direct insert', '9000000003', 'walk_in')
            RETURNING care_id
        """)).scalar_one()
        assert direct.startswith("CI-")
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("""
                    INSERT INTO crm_candidates (id, ref, care_id, name, phone, source)
                    VALUES ('bad-id', 'CND-BAD-ID', 'CI-26-28-001', 'Bad ID', '9000000004', 'walk_in')
                """))
        # A savepoint keeps the successful backfill/direct-insert assertions in
        # this test transaction after PostgreSQL rejects the immutable update.
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE crm_candidates SET care_id='CI-99-00-999' WHERE id='direct'"))
