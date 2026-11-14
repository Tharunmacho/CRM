"""Care India public candidate IDs: one continuous CI-001, CI-002 ... series, safe alongside legacy CND refs."""
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services.care_india_id import allocate_care_id, format_care_id
from test_database_naming import _migrate, schema_database


def test_format_is_ci_dash_serial_padded_to_three_digits_and_growing_past_999():
    assert format_care_id(1) == "CI-001"
    assert format_care_id(152) == "CI-152"
    assert format_care_id(153) == "CI-153"
    assert format_care_id(999) == "CI-999"
    assert format_care_id(1000) == "CI-1000"
    assert format_care_id(12345) == "CI-12345"
    with pytest.raises(ValueError):
        format_care_id(0)


def test_allocator_uses_one_continuous_counter_with_no_financial_year():
    class CounterSession:
        def __init__(self):
            self.serials = iter((1, 2, 3, 152, 153))
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return SimpleNamespace(scalar_one=lambda: next(self.serials))

    db = CounterSession()
    assert [allocate_care_id(db) for _ in range(5)] == ["CI-001", "CI-002", "CI-003", "CI-152", "CI-153"]
    assert all("crm_candidate_id_counters" in query and "fiscal" not in query for query, _ in db.calls)
    assert all(params == {"series": "GLOBAL"} for _, params in db.calls)


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
        # 0040 numbered them by financial year (march = CI-25-26-001, april = CI-26-27-001); 0044 renumbers them in that
        # order into the one continuous series.
        assert rows == [
            ("april", "CND-LEGACY-APRIL", "CI-002"),
            ("march", "CND-LEGACY-MARCH", "CI-001"),
        ]
        direct = connection.execute(text("""
            INSERT INTO crm_candidates (id, ref, name, phone, source)
            VALUES ('direct', 'CND-DIRECT', 'Direct insert', '9000000003', 'walk_in')
            RETURNING care_id
        """)).scalar_one()
        assert direct == "CI-003"                  # the trigger backstop continues the series
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("""
                    INSERT INTO crm_candidates (id, ref, care_id, name, phone, source)
                    VALUES ('bad-id', 'CND-BAD-ID', 'CI-26-27-001', 'Bad ID', '9000000004', 'walk_in')
                """))
        # A savepoint keeps the successful backfill/direct-insert assertions in
        # this test transaction after PostgreSQL rejects the immutable update.
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE crm_candidates SET care_id='CI-999' WHERE id='direct'"))
