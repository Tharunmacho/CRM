"""Experience totals on the application form (pure unit tests, no database).

The reader sometimes gives only a job's start/end text or a written period and no ``duration_months``; the totals must
still come out, overlapping jobs must not be counted twice and nothing may be invented from text that cannot be read.
"""
from datetime import date

import pytest

from app.integrations.ocr import experience as xp
from app.integrations.ocr.merge import merge_results
from app.services import form_extraction as fx

TODAY = date(2026, 11, 17)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(xp, "_today", lambda: TODAY)


def job(**fields):
    return {"company": "Co", "title": "Role", **fields}


def years(*entries, **totals):
    return fx._experience_years({"experience": list(entries), **totals})


def months(entry):
    return xp.entry_span(entry).months


def test_vrl_logistics_example_from_the_candidate_cg156():
    raw = {"experience": [{"company": "VRL Logistics Ltd", "title": "Data Entry & QC", "country": "India",
                           "start": "Jan 2018", "end": "Mar 2020"}]}
    assert months(raw["experience"][0]) == 26
    assert fx._experience_years(raw) == (2, 2, None)
    values = fx.resume_values(raw)
    assert (values["total_experience"], values["experience_india"]) == (2, 2) and "gulf_experience" not in values
    assert values["work_history"][0]["period"] == "Jan 2018 - Mar 2020"


@pytest.mark.parametrize("text, expected", [
    ("Jan 2018 to Mar 2020", 26),
    ("January 2018 - March 2020", 26),
    ("03/2018 - 06/2021", 39),
    ("2018-03 to 2018-09", 6),
    ("15-03-2018 to 20-06-2018", 3),
    ("2018-2020", 24),
    ("2018 - 2020", 24),
    ("Mar 2018 - 2020", 24),
    ("2018 - Mar 2020", 24),
    ("Jan'18 - Mar'20", 26),
    ("2019 to present", 84),
    ("Mar 2024 till date", 32),
    ("Jan 2025 - Current", 22),
])
def test_written_period_is_turned_into_months(text, expected):
    assert months({"period": text}) == expected
    assert months({"duration": text}) == expected


def test_start_and_end_fields_hold_dates_text_or_numbers():
    assert months({"start": "Jan 2018", "end": "Present"}) == 106
    assert months({"start": 2018, "end": 2020}) == 24
    assert months({"start": "Jan 2018 to Mar 2020"}) == 26  # the whole range put in one field
    assert months({"start": "2021-01", "end": "2021-07"}) == 6


@pytest.mark.parametrize("text, expected", [
    ("2 years 3 months", 27), ("2.5 years", 30), ("18 months", 18), ("1 yr 6 mos", 18), ("3 years", 36), ("1 year", 12),
])
def test_stated_duration_without_dates(text, expected):
    assert months({"duration_human": text}) == expected
    assert fx._experience_years({"experience": [job(duration_human=text)]})[0] == expected // 12 or expected < 12


def test_reader_months_win_over_dates_and_text():
    assert months({"start": "Jan 2018", "end": "Mar 2020", "duration_months": 30}) == 30
    assert months({"start": "Apr 2020", "end": "Present", "duration_months": 40}) == 40


@pytest.mark.parametrize("entry", [
    {}, {"company": "X"}, {"period": "a long time"}, {"period": "n/a"}, {"start": "Jan 2018"},       # no end: not assumed current
    {"start": "garbage", "end": "worse"}, {"start": "Mar 2020", "end": "Jan 2018"},                  # backwards
    {"start": "2030", "end": "2032"}, {"period": "13/2018 - 02/2019"}, {"period": "2019-20"},        # future, bad month
    {"period": "Jan 2015 - Dec 2016, Jan 2018 - Dec 2019"},                                           # ambiguous
    {"duration_human": "0 months", "duration_months": 0}, {"duration_months": -5}, {"duration_months": True},
    {"duration_months": "26"}, {"start": None, "end": None, "duration_human": "some years"},
    {"start": "Mar 2020", "end": "Mar 2020"},
])
def test_unreadable_text_adds_nothing_and_never_raises(entry):
    assert months(entry) == 0
    assert fx._experience_years({"experience": [entry]}) == (None, None, None)
    assert "total_experience" not in fx.resume_values({"experience": [entry]})


def test_overlapping_jobs_are_counted_once():
    a = job(company="A", start="Jan 2018", end="Dec 2019")      # 23 months
    b = job(company="B", start="Jun 2019", end="Jun 2021")      # 24 months, 6 overlap
    jobs = xp.summarize([a, b])
    assert jobs.naive_total == 47 and jobs.total == 41          # Jan 2018 -> Jun 2021
    assert years(a, b) == (3, 3, None)


def test_a_naive_reader_total_does_not_undo_the_merge_but_a_larger_one_stands():
    a = job(company="A", start="Jan 2018", end="Jan 2020")      # 24
    b = job(company="B", start="Jan 2019", end="Jan 2021")      # 24, overlap 12: union 36, naive 48
    assert years(a, b, total_experience_months=48)[0] == 3      # equal to the naive sum: replaced by 36
    assert years(a, b, total_experience_months=60)[0] == 5      # the reader saw more than these jobs: kept


def test_undated_jobs_are_summed_dated_jobs_merged():
    dated = [job(company="A", start="Jan 2018", end="Jan 2019"), job(company="B", start="Jun 2018", end="Jan 2019")]
    undated = job(company="C", duration_months=14)
    assert xp.summarize([*dated, undated]).total == 12 + 14


def test_same_job_listed_twice_is_one_job():
    a = job(start="Jan 2018", end="Jan 2019", duration_months=12)
    assert xp.summarize([a, dict(a)]).total == 12
    b = job(duration_months=14)
    assert xp.summarize([b, dict(b)]).total == 14


def test_reader_totals_are_used_when_they_are_the_only_source():
    assert fx._experience_years({"total_experience_months": 66, "indian_experience_months": 26,
                                 "overseas_experience_months": 40}) == (5, 2, 3)
    assert fx._experience_years({"total_experience_months": 30}) == (2, 2, None)  # no job says abroad: all India


def test_larger_reader_total_beats_smaller_computed_one():
    assert years(job(start="Jan 2018", end="Jan 2019"), total_experience_months=48) == (4, 4, None)


def test_present_end_uses_today(monkeypatch):
    entry = job(country="UAE", start="Dec 2024", end="Present")
    assert years(entry) == (1, None, 1)
    monkeypatch.setattr(xp, "_today", lambda: date(2027, 11, 20))
    assert years(entry) == (2, None, 2)


def test_india_versus_abroad_per_entry():
    india = job(company="A", country="India", start="Jan 2010", end="Jan 2013")                # 36
    gulf = job(company="B", country="Saudi Arabia", start="Jan 2013", end="Jan 2018")          # 60
    flagged = job(company="C", is_overseas=True, start="Jan 2018", end="Jan 2020")             # 24, no country
    by_location = job(company="D", location="Kochi, Kerala, India", start="Jan 2020", end="Jan 2022")  # 24
    jobs = xp.summarize([india, gulf, flagged, by_location])
    assert (jobs.total, jobs.india, jobs.abroad) == (36 + 60 + 24 + 24, 36 + 24, 60 + 24)
    assert years(india, gulf, flagged, by_location) == (12, 5, 7)


def test_country_names_for_india_and_overseas_flag_on_default_model():
    # The Veris model sends is_overseas=False for every job, so a foreign country must still count as abroad.
    uae = job(country="UAE", is_overseas=False, start="Jan 2019", end="Jan 2021")
    assert xp.is_abroad(uae) and not xp.is_india(uae)
    for name in ("India", "INDIA", "Republic of India", "IND"):
        assert xp.is_india({"country": name}) and not xp.is_abroad({"country": name})
    assert not xp.is_india({"country": "Indonesia"}) and xp.is_abroad({"country": "Indonesia"})


def test_unknown_country_counts_toward_the_total_and_falls_back_to_india():
    unknown = job(company="X", duration_months=14)
    gulf = job(company="Y", is_overseas=True, duration_months=24)
    assert xp.summarize([unknown, gulf]).india == 0   # known-India months only
    assert fx._experience_years({"experience": [unknown, gulf]}) == (3, 1, 2)  # the previous behaviour is kept


def test_explicit_abroad_months_and_flag_is_overseas_still_work():
    raw = {"experience": [job(duration_months=26, country="India"), job(duration_months=40, is_overseas=True)],
           "total_experience_months": 66, "indian_experience_months": 26, "overseas_experience_months": 40}
    assert fx._experience_years(raw) == (5, 2, 3)


def test_years_are_completed_years_so_a_short_total_stays_blank_but_the_row_keeps_the_period():
    entry = job(country="India", start="Jan 2020", end="Sep 2020")
    assert months(entry) == 8
    assert fx._experience_years({"experience": [entry]}) == (0, 0, None)
    assert "total_experience" not in fx.resume_values({"experience": [entry]})  # 0 is never written over a blank
    assert fx.history_rows({"experience": [entry]})[0]["period"] == "Jan 2020 - Sep 2020"
    assert years(job(start="Jan 2018", end="Dec 2019")) == (1, 1, None)  # 23 months: completed years only


def test_history_row_shows_a_written_period_and_stated_duration_with_dates():
    assert fx.history_rows({"experience": [job(period="Jan 2018 to Mar 2020")]})[0]["period"] == "Jan 2018 to Mar 2020"
    row = fx.history_rows({"experience": [job(start="Jan 2018", end="Mar 2020", duration_human="2 years 2 months")]})[0]
    assert row["period"] == "Jan 2018 - Mar 2020 (2 years 2 months)"


def test_resume_values_fill_totals_from_a_text_period():
    values = fx.resume_values({"experience": [
        {"company": "Gulf Co", "title": "Fitter", "country": "Qatar", "period": "2016 - 2019"},
        {"company": "Home Co", "title": "Fitter", "country": "India", "period": "2012-2016"},
    ]})
    assert (values["total_experience"], values["experience_india"], values["gulf_experience"]) == (7, 4, 3)


def test_ocr_mapping_fills_total_and_abroad_from_job_dates():
    from veriis.models import ResumeResponse
    from app.integrations.ocr.mapping import resume_fields

    response = ResumeResponse.model_validate({"request_id": "x", "processing_time_ms": 0, "experience": [
        {"title": "Fitter", "company": "Gulf Co", "country": "Qatar", "start": "Jan 2016", "end": "Jan 2019"},
        {"title": "Fitter", "company": "Home Co", "country": "India", "start": "Jan 2012", "end": "Jan 2016"},
    ]})
    fields, _ = resume_fields(response, designations=[])
    assert (fields.total_experience, fields.experience_india, fields.gulf_experience) == (7, 4, 3)


def test_merge_adds_up_dated_jobs_without_double_counting_overlaps():
    a = {"experience": [{"title": "A", "company": "X", "start": "Jan 2018", "end": "Jan 2020", "is_overseas": True}], "pages": []}
    b = {"experience": [{"title": "B", "company": "Y", "start": "Jan 2019", "end": "Jan 2021"}], "pages": []}
    merged, _, notes = merge_results("resume", [([1], a), ([2], b)])
    assert merged["total_experience_months"] == 36 and merged["overseas_experience_months"] == 24
    assert merged["indian_experience_months"] == 24 and notes


# --- abroad means a known foreign country, never "any text that is not India" --------------------------------------------

@pytest.mark.parametrize("place", ["Tamil Nadu", "Kerala", "Chennai", "TN", "Mumbai, Maharashtra", "Kochi", "Karnataka", "Site office",
                                   "xyzzy", "Unknown"])
def test_an_indian_state_a_city_or_unknown_text_is_never_abroad(place):
    assert not xp.is_abroad({"country": place})
    assert not xp.is_abroad({"country": place, "is_overseas": False})


@pytest.mark.parametrize("place", ["Kuwait", "KSA", "Saudi Arabia", "UAE", "U.A.E", "U.A.E.", "Dubai", "Abu Dhabi", "Sharjah", "Qatar", "Doha",
                                   "Oman", "Muscat", "Bahrain", "Singapore", "Malaysia", "Maldives", "Sri Lanka", "Nepal", "Bangladesh",
                                   "Jordan", "Iraq", "Egypt", "Turkey", "UK", "United Kingdom", "USA", "U.S.A.", "Canada", "Australia",
                                   "Germany", "Dubai, UAE", "Riyadh, Saudi Arabia", "Indonesia", "KUWAIT CITY", "kuwait"])
def test_a_known_foreign_country_or_city_is_abroad(place):
    assert xp.is_abroad({"country": place}) and not xp.is_india({"country": place})


def test_the_overseas_flag_decides_when_the_country_does_not_name_a_foreign_one():
    assert xp.is_abroad({"is_overseas": True}) and xp.is_abroad({"country": "Tamil Nadu", "is_overseas": True})
    assert not xp.is_abroad({"is_overseas": False}) and not xp.is_abroad({"country": "Tamil Nadu", "is_overseas": False})
    # The default reader model sends is_overseas=False for every job: a named foreign country still wins over that default.
    assert xp.is_abroad({"country": "Kuwait", "is_overseas": False})


def test_a_tamil_nadu_job_marked_not_overseas_is_not_counted_as_abroad_beside_a_kuwait_job():
    home = job(company="Home", country="Tamil Nadu", is_overseas=False, duration_months=36)
    gulf = job(company="Gulf", country="Kuwait", duration_months=24)
    totals = xp.summarize([home, gulf])
    assert (totals.total, totals.abroad, totals.india) == (60, 24, 0)       # Tamil Nadu: total only (unknown), not abroad
    assert years(home, gulf) == (5, 3, 2)                                  # years: unknown months fall back to India
    unknown = job(company="U", country="xyzzy", duration_months=12)
    assert xp.summarize([unknown]).abroad == 0 and xp.summarize([unknown]).total == 12


def test_the_merge_step_keeps_an_indian_state_job_out_of_the_overseas_months():
    entries = [{"title": "A", "company": "Home", "country": "Kerala", "is_overseas": False, "duration_months": 30},
               {"title": "B", "company": "Gulf", "country": "Dubai", "duration_months": 18}]
    merged, _, _ = merge_results("resume", [([1], {"experience": entries[:1], "pages": []}),
                                            ([2], {"experience": entries[1:], "pages": []})])
    assert merged["overseas_experience_months"] == 18 and merged["total_experience_months"] == 48
    assert merged["indian_experience_months"] == 30
