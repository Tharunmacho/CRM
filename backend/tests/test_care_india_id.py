"""Care India public candidate IDs: one continuous CI-001, CI-002 ... series, safe alongside legacy CND refs."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Candidate
from app.services.care_india_id import allocate_care_id, format_care_id
from app.services.traveller_id import assign_traveller_id
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


def test_allocator_takes_the_number_after_the_highest_in_the_data_bank_with_no_financial_year():
    class DataBankSession:
        """Each allocation is two statements: the series lock, then ONE statement that reads the highest number and writes it back to
        the counter row (the read must start after the lock is held; folding the write-back into it saves a round trip)."""

        def __init__(self):
            self.highest = iter((0, 1, 2, 151, 152))      # what the Data Bank holds at each call (the highest CI number)
            self.calls = []
            self.counter = None

        def execute(self, statement, params=None):
            query = str(statement)
            self.calls.append((query, params))
            if "pg_advisory_xact_lock" in query:
                return SimpleNamespace()
            assert "FROM crm_candidates" in query and "crm_candidate_id_counters" in query
            serial = next(self.highest) + 1
            self.counter = serial                          # what the statement writes to the counter row and returns
            return SimpleNamespace(scalar_one=lambda: serial)

    db = DataBankSession()
    given = []
    for _ in range(5):
        given.append(allocate_care_id(db))
        assert db.counter == int(given[-1].split("-")[1])          # the counter row always equals the number just given
    assert given == ["CI-001", "CI-002", "CI-003", "CI-152", "CI-153"]
    assert len(db.calls) == 10 and all("fiscal" not in query for query, _ in db.calls)
    assert [("pg_advisory_xact_lock" in q, "FROM crm_candidates" in q, "crm_candidate_id_counters" in q) for q, _ in db.calls[:2]] == [
        (True, False, False), (False, True, True)]
    assert all(params == {"series": "GLOBAL"} for q, params in db.calls if "crm_candidate_id_counters" in q)


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


# --- The number is the Data Bank maximum + 1 (real PostgreSQL, isolated test database) ---------------------------------------

def _add(db, key, care_id=None):
    """Create one candidate the way the intakes do: allocate the Care India ID, then insert."""
    person = Candidate(id=key, ref=f"CND-{key}", name=key, phone=f"9300{abs(hash(key)) % 10**7:07d}", source="walk_in",
                       care_id=care_id or allocate_care_id(db))
    db.add(person)
    db.flush()
    return person.care_id


def _migrated(schema_database):
    _migrate(schema_database, "head")
    return sessionmaker(bind=schema_database)


def _counter(db):
    return db.scalar(text("SELECT last_serial FROM crm_candidate_id_counters WHERE series = 'GLOBAL'"))


def test_the_first_candidate_in_an_empty_data_bank_is_ci_001(schema_database):
    with _migrated(schema_database)() as db:
        assert allocate_care_id(db) == "CI-001"
        assert _counter(db) == 1
        db.commit()
        # Nothing was inserted, so the Data Bank is still empty: the same number is offered again (it was never consumed).
        assert allocate_care_id(db) == "CI-001"


def test_deleting_the_last_candidates_gives_their_numbers_to_the_next_ones(schema_database):
    with _migrated(schema_database)() as db:
        assert [_add(db, f"c{n}") for n in range(1, 6)] == [f"CI-00{n}" for n in range(1, 6)]
        db.commit()
        db.delete(db.get(Candidate, "c4"))
        db.delete(db.get(Candidate, "c5"))
        db.commit()
        assert _add(db, "again") == "CI-004"            # not CI-006: the tail of the series is given again
        assert _counter(db) == 4                         # the counter row went back with the Data Bank
        assert _add(db, "after") == "CI-005"
        db.commit()
        assert sorted(care for (care,) in db.execute(text("SELECT care_id FROM crm_candidates"))) == [f"CI-00{n}" for n in range(1, 6)]


def test_deleting_a_middle_candidate_leaves_the_hole_and_never_duplicates(schema_database):
    with _migrated(schema_database)() as db:
        for n in range(1, 6):
            _add(db, f"c{n}")
        db.commit()
        db.delete(db.get(Candidate, "c2"))
        db.commit()
        assert _add(db, "next") == "CI-006"              # max + 1: CI-002 stays empty, a number held by a candidate is never given
        db.commit()
        found = sorted(care for (care,) in db.execute(text("SELECT care_id FROM crm_candidates")))
        assert found == ["CI-001", "CI-003", "CI-004", "CI-005", "CI-006"] and len(set(found)) == 5
        assert _counter(db) == 6


def test_two_transactions_at_the_same_moment_take_turns_and_a_rollback_gives_the_number_back(schema_database):
    sessions = _migrated(schema_database)
    with sessions() as db:
        _add(db, "base")
        db.commit()
    first_has_number, release_first, second_done = threading.Event(), threading.Event(), threading.Event()
    given = {}

    def first():
        with sessions() as db:
            given["first"] = allocate_care_id(db)
            first_has_number.set()
            release_first.wait(10)
            db.rollback()                                # the first intake fails: nothing was consumed

    def second():
        first_has_number.wait(10)
        with sessions() as db:
            given["second"] = allocate_care_id(db)       # blocks on the series lock until the first transaction ends
            second_done.set()
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first), pool.submit(second)]
        assert first_has_number.wait(10)
        assert not second_done.wait(1.0)                 # really waiting for the first transaction, not racing it
        release_first.set()
        for future in futures:
            future.result(timeout=20)
    assert given == {"first": "CI-002", "second": "CI-002"}        # the rolled-back number is taken by the next intake
    with sessions() as db:
        assert _counter(db) == 2


def test_two_transactions_that_both_commit_get_different_numbers(schema_database):
    sessions = _migrated(schema_database)
    first_has_number, release_first, second_done = threading.Event(), threading.Event(), threading.Event()
    given = {}

    def first():
        with sessions() as db:
            given["first"] = _add(db, "one")
            first_has_number.set()
            release_first.wait(10)
            db.commit()

    def second():
        first_has_number.wait(10)
        with sessions() as db:
            given["second"] = _add(db, "two")            # waits for the first commit, then sees its number and takes the next
            second_done.set()
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first), pool.submit(second)]
        assert first_has_number.wait(10)
        assert not second_done.wait(1.0)
        release_first.set()
        for future in futures:
            future.result(timeout=20)
    assert given == {"first": "CI-001", "second": "CI-002"}
    with sessions() as db:
        assert _counter(db) == 2


def test_many_simultaneous_intakes_all_get_distinct_consecutive_numbers(schema_database):
    sessions = _migrated(schema_database)

    def create(index):
        with sessions() as db:
            care = _add(db, f"p{index}")
            db.commit()
            return care

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(24)))
    assert sorted(results) == [f"CI-{n:03d}" for n in range(1, 25)]


def test_the_counter_row_equals_the_last_number_given_and_numbers_past_999_gain_a_digit(schema_database):
    with _migrated(schema_database)() as db:
        _add(db, "top", care_id="CI-998")
        assert (allocate_care_id(db), _counter(db)) == ("CI-999", 999)
        _add(db, "n999", care_id="CI-999")
        assert (allocate_care_id(db), _counter(db)) == ("CI-1000", 1000)
        _add(db, "n1000", care_id="CI-1000")
        assert allocate_care_id(db) == "CI-1001"
        # Numerically, not as text: CI-1000 is higher than CI-999 although "CI-999" sorts after "CI-1000".
        assert db.scalar(text("SELECT MAX(substring(care_id from '^CI-([0-9]+)$')::bigint) FROM crm_candidates")) == 1000
        db.commit()


def test_traveller_numbers_are_a_separate_series_and_never_count_as_care_ids(schema_database):
    with _migrated(schema_database)() as db:
        assert [_add(db, key) for key in ("a", "b")] == ["CI-001", "CI-002"]
        # Travelling gives CI 01 to "a"; a traveller number is not a Care India number and moves nothing in that series.
        assert assign_traveller_id(db, db.get(Candidate, "a")) == "CI 01"
        assert allocate_care_id(db) == "CI-003"
        db.execute(text("UPDATE crm_traveller_id_counters SET last_serial = 900"))
        db.execute(text("UPDATE crm_candidates SET traveller_id = 'CI 900' WHERE id = 'b'"))
        assert allocate_care_id(db) == "CI-003"          # CI 900 is not read as 900
        # A traveller-style value can never be stored as a Care India ID, so it cannot reach the series either.
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(text("INSERT INTO crm_candidates (id, ref, care_id, name, phone, source) VALUES ('t', 'CND-T', 'CI-T-999', 't', '93999999', 'walk_in')"))
        assert allocate_care_id(db) == "CI-003"
        # The Care India allocation never touches the traveller counter.
        assert db.scalar(text("SELECT last_serial FROM crm_traveller_id_counters WHERE series = 'GLOBAL'")) == 900
        db.commit()
