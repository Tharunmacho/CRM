"""Experience range and Date added range filters: built offline, the SQL is only compiled, never run."""
import json
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.services.candidate_search import Search, agency_midnight, search_statement


def compiled(filters: dict):
    statement = search_statement(Search(column_filters=json.dumps(filters)))
    return statement.compile(dialect=postgresql.dialect())


def test_experience_range_uses_inclusive_bounds():
    query = compiled({"experience_min": "2", "experience_max": "6"})
    sql = str(query)
    assert "candidates.total_experience >=" in sql and "candidates.total_experience <=" in sql
    assert sorted(value for key, value in query.params.items() if key.startswith("total_experience")) == [2, 6]


def test_exact_experience_filter_still_works():
    assert "candidates.total_experience =" in str(compiled({"experience": "4"}))


def test_date_range_is_bounded_in_the_agency_timezone():
    query = compiled({"added_from": "2026-10-01", "added_to": "2026-10-03"})
    bounds = sorted(value for value in query.params.values() if hasattr(value, "utcoffset"))
    assert len(bounds) == 2
    # Midnight at the start of 1 Oct and of 4 Oct (the end day is inclusive), at the agency offset rather than UTC.
    assert bounds[1] - bounds[0] == timedelta(days=3)
    assert bounds[0].utcoffset() == agency_midnight("2026-10-01", "x").utcoffset()
    assert bounds[0].utcoffset() != timedelta(0)


def test_old_single_day_added_filter_becomes_a_day_range_and_free_text_still_matches_text():
    day = compiled({"added": "2026-10-01"})
    assert sum(1 for value in day.params.values() if hasattr(value, "utcoffset")) == 2
    text = compiled({"added": "2026-10"})
    assert not any(hasattr(value, "utcoffset") for value in text.params.values())


@pytest.mark.parametrize("filters", [{"added_from": "01/10/2026"}, {"added_to": "tomorrow"}, {"experience_min": "two"}, {"experience_max": "99"}, {"experience_min": "-1"}])
def test_bad_range_values_are_rejected_with_a_message(filters):
    with pytest.raises(HTTPException) as caught:
        compiled(filters)
    assert caught.value.status_code == 422


def test_unknown_column_filter_keys_are_still_rejected():
    with pytest.raises(HTTPException):
        compiled({"added_before": "2026-10-01"})
