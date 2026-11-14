"""Lead sheet dates: "New today" = uploaded in the last 24 hours (rolling), and the Dates filter on the upload date or on
the lead's own created date, by Asia/Kolkata calendar day. Synthetic people only; needs TEST_DATABASE_URL like the other DB tests.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from app.db import models as m
from app.services import lead_bulk, lead_metrics
from lead_sheet_fixtures import lead_row
from test_database_naming import schema_database  # noqa: F401  (fixture used by `env`)
from test_lead_sheet import act_as, env, imported  # noqa: F401  (env is a fixture)

IST = ZoneInfo("Asia/Kolkata")


def lead_ids(env):
    env.db.rollback()
    return {name: lead_id for lead_id, name in env.db.execute(select(m.Lead.id, m.Lead.name)).all()}


def set_times(env, number, *, uploaded=None, created=None):
    env.db.execute(text("UPDATE crm_leads SET created_at = COALESCE(:u, created_at), created_time = COALESCE(:c, created_time) WHERE id = :i"),
                   {"u": uploaded, "c": created, "i": lead_ids(env)[f"Test Lead {number:05d}"]})
    env.db.commit()


def listing(env, **params):
    response = env.client.get("/api/leads", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    return {item["name"] for item in body["items"]}, body["total"]


def names(*numbers):
    return {f"Test Lead {n:05d}" for n in numbers}


def seed_ages(env):
    """Six leads uploaded 1 h, 23 h, 25 h, 3 days and 10 days ago, plus one just now whose platform date is 40 days old."""
    imported(env, [lead_row(n) for n in range(6)])
    now = datetime.now(timezone.utc)
    for n, hours in enumerate((1, 23, 25, 72, 240)):
        set_times(env, n, uploaded=now - timedelta(hours=hours))
    set_times(env, 5, created=now - timedelta(days=40))


def test_new_today_counts_leads_uploaded_in_the_last_24_hours_whatever_their_own_date(env):
    seed_ages(env)
    summary = env.client.get("/api/leads/summary").json()
    # leads 0, 1 and 5 were uploaded within 24 h; lead 5's platform date is 40 days old and still counts, the 25 h one does not
    assert summary["total"] == 6 and summary["newToday"] == 3
    new, total = listing(env, uploaded_within_hours=24)
    assert new == names(0, 1, 5) and total == summary["newToday"], "the card filter returns exactly the counted leads"
    assert listing(env, uploaded_within_hours=48)[1] == 4 and listing(env, uploaded_within_hours=168)[1] == 5
    assert listing(env, uploaded_within_hours=24, status="contacted")[1] == 0, "it combines with the other filters"


def test_a_skipped_duplicate_reupload_does_not_count_as_new_and_does_not_move_the_original(env):
    imported(env, [lead_row(n) for n in range(4)])
    for n in range(4):
        set_times(env, n, uploaded=datetime.now(timezone.utc) - timedelta(days=5))
    again = imported(env, [lead_row(n) for n in range(6)])  # 0-3 are duplicates, 4-5 are new
    assert again["rowsNew"] == 2 and again["rowsDuplicate"] == 4
    assert env.client.get("/api/leads/summary").json()["newToday"] == 2
    assert listing(env, uploaded_within_hours=24)[0] == names(4, 5)


def test_staff_new_today_counts_only_their_own_leads(env):
    imported(env, [lead_row(n) for n in range(4)], assign_to="a")
    imported(env, [lead_row(n) for n in range(4, 7)])
    assert env.client.get("/api/leads/summary").json()["newToday"] == 7
    act_as(env, "a")
    mine = env.client.get("/api/leads/summary").json()
    assert (mine["scope"], mine["newToday"]) == ("mine", 4)
    assert listing(env, uploaded_within_hours=24)[1] == 4


def test_the_rolling_window_is_checked_at_the_edge(env):
    imported(env, [lead_row(n) for n in range(2)])
    now = datetime.now(timezone.utc)
    set_times(env, 0, uploaded=now - timedelta(hours=23, minutes=55))
    set_times(env, 1, uploaded=now - timedelta(hours=24, minutes=5))
    assert listing(env, uploaded_within_hours=24)[0] == names(0)
    assert env.client.get("/api/leads/summary").json()["newToday"] == 1


@pytest.mark.parametrize("value", ["0", "-1", "169", "abc"])
def test_uploaded_within_hours_is_validated(env, value):
    assert env.client.get("/api/leads", params={"uploaded_within_hours": value}).status_code == 422


def test_dates_filter_uses_the_agency_calendar_day_not_utc(env):
    imported(env, [lead_row(n) for n in range(4)])
    # 00:30 IST on 10 Nov is 19:00 UTC on 9 Nov; 23:30 IST on 10 Nov is 18:00 UTC the same day; 00:10 IST on 11 Nov is 18:40 UTC on 10 Nov
    stamps = {0: datetime(2026, 11, 10, 0, 30, tzinfo=IST), 1: datetime(2026, 11, 10, 23, 30, tzinfo=IST),
              2: datetime(2026, 11, 11, 0, 10, tzinfo=IST), 3: datetime(2026, 11, 9, 23, 59, tzinfo=IST)}
    for n, moment in stamps.items():
        set_times(env, n, uploaded=moment)
    assert listing(env, created_from="2026-11-10", created_to="2026-11-10")[0] == names(0, 1)
    assert listing(env, created_from="2026-11-11", created_to="2026-11-11")[0] == names(2)
    assert listing(env, created_from="2026-11-09", created_to="2026-11-09")[0] == names(3)
    assert listing(env, created_from="2026-11-10")[0] == names(0, 1, 2)
    assert listing(env, created_to="2026-11-10")[0] == names(0, 1, 3)
    assert listing(env, created_from="2026-11-10", created_to="2026-11-10", date_field="uploaded")[0] == names(0, 1), "uploaded is the default"


def test_dates_filter_can_use_the_leads_own_created_date(env):
    imported(env, [lead_row(n) for n in range(3)])
    for n, day in enumerate((4, 5, 6)):
        set_times(env, n, created=datetime(2026, 11, day, 12, 0, tzinfo=IST), uploaded=datetime(2026, 11, 14, 9, 0, tzinfo=IST))
    assert listing(env, created_from="2026-11-05", created_to="2026-11-05", date_field="created")[0] == names(1)
    assert listing(env, created_from="2026-11-04", created_to="2026-11-05", date_field="created")[0] == names(0, 1)
    assert listing(env, created_from="2026-11-14", created_to="2026-11-14", date_field="uploaded")[1] == 3
    assert listing(env, created_from="2026-11-05", created_to="2026-11-05", date_field="uploaded")[1] == 0
    assert env.client.get("/api/leads", params={"date_field": "bogus"}).status_code == 422


def test_a_lead_without_its_own_date_falls_back_to_the_upload_time_for_created(env):
    imported(env, [lead_row(0, created_time="")])
    env.db.rollback()
    lead = env.db.scalar(select(m.Lead))
    assert lead.created_time is None
    day = lead.created_at.astimezone(IST).date().isoformat()
    assert listing(env, created_from=day, created_to=day, date_field="created")[1] == 1


def test_presets_over_the_api_today_yesterday_last_seven_days(env):
    imported(env, [lead_row(n) for n in range(5)])
    today = datetime.now(IST).date()
    for n, back in enumerate((0, 0, 1, 5, 9)):
        day = today - timedelta(days=back)
        set_times(env, n, uploaded=datetime(day.year, day.month, day.day, 12, 0, tzinfo=IST))
    iso = lambda back: (today - timedelta(days=back)).isoformat()
    assert listing(env, created_from=iso(0), created_to=iso(0))[0] == names(0, 1)  # Today
    assert listing(env, created_from=iso(1), created_to=iso(1))[0] == names(2)  # Yesterday
    assert listing(env, created_from=iso(6), created_to=iso(0))[0] == names(0, 1, 2, 3)  # Last 7 days


def test_bulk_filter_selection_understands_the_new_date_parameters(env):
    seed_ages(env)
    admin = env.db.get(m.User, "admin")
    chosen, _ = lead_bulk.resolve(env.db, admin, None, lead_bulk.clean_filter({"uploaded_within_hours": "24", "date_field": "uploaded"}), lock=False)
    by_id = {lead_id: name for name, lead_id in lead_ids(env).items()}
    assert {by_id[row[0]] for row in chosen} == names(0, 1, 5), "select all N matching acts on exactly the counted leads"
    assert lead_bulk.clean_filter({"date_field": "created"})["date_field"] == "created"


def test_sort_by_upload_time_and_both_dates_are_returned(env):
    seed_ages(env)
    items = env.client.get("/api/leads", params={"sort": "uploaded", "direction": "desc", "page_size": 6}).json()["items"]
    assert items[0]["name"] in names(5, 0) and items[-1]["name"] == "Test Lead 00004", "newest upload first, ten-day-old upload last"
    assert [i["createdAt"] for i in items] == sorted((i["createdAt"] for i in items), reverse=True)
    assert all(i["createdAt"] and "createdTime" in i for i in items)


def test_dashboard_card_counts_the_same_uploaded_window(env):
    seed_ages(env)
    now = datetime.now(timezone.utc)
    admin = env.db.get(m.User, "admin")
    assert lead_metrics.dashboard_leads(env.db, admin, None, "Asia/Kolkata", now)["newToday"] == 3
    items = lead_metrics.dashboard_lead_items(env.db, admin, None, "newToday", "month", "Asia/Kolkata", now)
    assert items["total"] == 3 and items["at"] == "uploaded"
    assert {row["name"] for row in items["items"]} == names(0, 1, 5)
