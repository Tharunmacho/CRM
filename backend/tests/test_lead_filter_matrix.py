"""Lead sheet filters, end to end: every filter alone and in combination through the real endpoints (list, Excel/CSV export, bulk
"select all matching"), compared with a brute-force Python filter over the same ~40 synthetic leads.

The agency time zone is Asia/Kolkata: a date filter is a calendar day there, from/to both included. Needs TEST_DATABASE_URL.
"""
from datetime import datetime, timedelta, timezone
from itertools import product
from random import Random
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import csv
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.api.routers import candidates, files, lead_bulk, leads
from app.core.deps import current_user
from app.db import models as m
from app.db.session import get_db
from app.services import lead_bulk as service
from app.services.daily_work import agency_today
from test_database_naming import _migrate, schema_database

IST = ZoneInfo("Asia/Kolkata")
STATUSES = ["new", "contacted", "interested", "resume_requested", "resume_received", "converted", "not_interested", "invalid", "wrong_number"]
OWNERS = ["yuv", "asha", "ravi", None]
CAMPAIGNS = ["Dubai Chef 18-11", "Qatar, Kitchen (Doha)", "Saudi Helper"]
POSITIONS = ["South Indian Chef (CDP)", "Kitchen Helper", "Dosa & Parotta Master"]
COUNTRIES = ["Dubai", "Qatar", "Saudi Arabia"]
PLATFORMS = ["fb", "ig", "other"]


def ist(day: int, hour: int = 12, minute: int = 0, second: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=IST).astimezone(timezone.utc)


def build_leads() -> list[dict]:
    """40 leads: 36 spread over 15-19 Nov (uploaded and the platform's own `created` time differ) plus 4 on the midnight edges, and
    3 uploaded today (real clock) so the Uploaded today card has something to find."""
    rng = Random(7)
    rows: list[dict] = []
    for n in range(33):
        uploaded = ist(15 + n % 5, 9 + n % 10, (n * 7) % 60)
        rows.append({"uploaded": uploaded, "created": uploaded - timedelta(days=n % 3, hours=n % 5)})
    for uploaded in (ist(18, 23, 30), ist(19, 0, 30), ist(18, 23, 59, 59), ist(19, 0, 0, 0)):
        rows.append({"uploaded": uploaded, "created": uploaded - timedelta(days=1)})
    now = datetime.now(timezone.utc)
    for n in range(3):
        rows.append({"uploaded": now - timedelta(minutes=n), "created": now - timedelta(days=2)})
    for n, row in enumerate(rows):
        row.update(status=STATUSES[(n * 5 + n // 4) % 9], owner=OWNERS[rng.randrange(4)], campaign=CAMPAIGNS[n % 3], position=POSITIONS[(n // 2) % 3],
                   country=COUNTRIES[(n // 3) % 3], platform=PLATFORMS[(n // 4) % 3], name=f"Lead {n:02d}", id=f"lead-{n:02d}")
    return rows


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    monkeypatch.setattr(files, "STORAGE", tmp_path)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin),
                    m.User(id="md", name="MD", email="md@example.test", role=m.Role.manager),
                    m.User(id="yuv", name="Yuvaraj", email="yuv@example.test", role=m.Role.staff),
                    m.User(id="asha", name="Asha", email="asha@example.test", role=m.Role.staff),
                    m.User(id="ravi", name="Ravi", email="ravi@example.test", role=m.Role.staff)])
        db.commit()
        data = build_leads()
        db.add_all([m.Lead(id=row["id"], name=row["name"], phone=f"+91900000{n:04d}", phone_key=f"900000{n:04d}", status=row["status"], owner_id=row["owner"],
                           campaign_name=row["campaign"], position_applied=row["position"], country_interest=row["country"], platform=row["platform"],
                           created_at=row["uploaded"], created_time=row["created"]) for n, row in enumerate(data)])
        db.commit()
        app = FastAPI()
        for router in (leads.router, lead_bulk.router, files.router, candidates.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        who = {"id": "admin"}
        app.dependency_overrides[current_user] = lambda: db.get(m.User, who["id"])
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, client=client, who=who, data=data)


def act_as(env, user_id):
    env.who["id"] = user_id
    env.db.expire_all()


def agency_day(moment: datetime):
    return moment.astimezone(IST).date()


def expected_ids(env, f: dict, viewer: str) -> set[str]:
    """The brute-force filter: what the Lead sheet should show for `f` (the query parameters) when `viewer` is signed in."""
    sees_all = viewer in ("admin", "md")
    field = "created" if f.get("date_field") == "created" else "uploaded"
    out = set()
    for row in env.data:
        if not sees_all and row["owner"] != viewer:
            continue
        statuses = [s for s in (f.get("status") or "").split(",") if s]
        if statuses and row["status"] not in statuses:
            continue
        if f.get("campaign") and row["campaign"] != f["campaign"]:
            continue
        if f.get("position") and row["position"] != f["position"]:
            continue
        if f.get("country") and row["country"] != f["country"]:
            continue
        if f.get("platform") and row["platform"] != f["platform"]:
            continue
        who = f.get("owner")
        if who in ("none", "unassigned") and row["owner"] is not None:
            continue
        if who and who not in ("none", "unassigned") and row["owner"] != (viewer if who == "me" else who):
            continue
        day = agency_day(row[field])
        if f.get("created_from") and day < datetime.fromisoformat(f["created_from"]).date():
            continue
        if f.get("created_to") and day > datetime.fromisoformat(f["created_to"]).date():
            continue
        if f.get("uploaded_today") and agency_day(row["uploaded"]) != agency_today(datetime.now(timezone.utc)):
            continue
        if f.get("q") and f["q"].lower() not in row["name"].lower():
            continue
        out.add(row["id"])
    return out


def listed(env, f: dict, **extra) -> tuple[set[str], int]:
    params = {k: v for k, v in f.items() if v not in (None, "")}
    response = env.client.get("/api/leads", params={"page_size": 100, **params, **extra})
    assert response.status_code == 200, (f, response.text)
    body = response.json()
    return {item["id"] for item in body["items"]}, body["total"]


def check(env, f: dict, viewer: str):
    want = expected_ids(env, f, viewer)
    got, total = listed(env, f)
    assert (got, total) == (want, len(want)), f"{viewer} {f}: expected {sorted(want)} got {sorted(got)} (total {total})"
    return want


DAY = {"from": "2026-11-18", "to": "2026-11-19"}
D19 = {"created_from": "2026-11-19", "created_to": "2026-11-19"}


# ------------------------------------------------------------------ the owner's own cases, one by one

def test_recruiter_and_date_together_show_that_recruiters_leads_of_that_day(env):
    """The reported bug: choose Yuvaraj and a date and nothing showed."""
    for viewer in ("admin", "md"):
        act_as(env, viewer)
        for day_from, day_to in (("2026-11-19", "2026-11-19"), ("2026-11-18", "2026-11-19"), ("2026-11-16", "2026-11-19"), ("2026-11-18", None), (None, "2026-11-17")):
            for field in ("uploaded", "created"):
                f = {"owner": "yuv", "created_from": day_from, "created_to": day_to, "date_field": field}
                check(env, f, viewer)
    act_as(env, "admin")
    assert expected_ids(env, {"owner": "yuv", "created_from": "2026-11-16", "created_to": "2026-11-19"}, "admin"), "the fixture must give Yuvaraj leads in the range"


def test_status_alone_filters_and_never_errors(env):
    act_as(env, "admin")
    for status in STATUSES + ["interested,resume_received", "new,contacted"]:
        want = check(env, {"status": status}, "admin")
        assert want, f"the fixture has no {status} lead"


def test_midnight_edges_in_the_agency_time_zone(env):
    act_as(env, "admin")
    on = lambda day, **kw: check(env, {"created_from": day, "created_to": day, **kw}, "admin")
    assert {"lead-33", "lead-35"} <= on("2026-11-18")  # 23:30 and 23:59:59 IST belong to the 18th
    assert {"lead-34", "lead-36"} <= on("2026-11-19")  # 00:30 and 00:00:00 IST belong to the 19th
    assert not ({"lead-33", "lead-35"} & on("2026-11-19")) and not ({"lead-34", "lead-36"} & on("2026-11-18"))
    for day in ("2026-11-17", "2026-11-18", "2026-11-19"):  # a single-day range, on the platform's own date too
        on(day, date_field="created")
        on(day, date_field="uploaded")


def test_from_only_to_only_and_reversed_ranges(env):
    act_as(env, "admin")
    check(env, {"created_from": "2026-11-17"}, "admin")
    check(env, {"created_to": "2026-11-17"}, "admin")
    check(env, {"created_from": "2026-11-19", "created_to": "2026-11-17"}, "admin")  # reversed: nothing, no error
    check(env, {"created_from": "2030-01-01"}, "admin")


def test_uploaded_today_is_the_calendar_day_and_combines_with_everything(env):
    act_as(env, "admin")
    today = {"uploaded_today": "true"}
    assert check(env, today, "admin")
    for extra in ({"owner": "yuv"}, {"status": "new"}, {"status": "new,contacted"}, {"campaign": CAMPAIGNS[1]}, {"platform": "fb"}):
        check(env, {**today, **extra}, "admin")


def test_every_filter_value_the_selects_offer_is_accepted_and_exact(env):
    act_as(env, "admin")
    for key, values in (("campaign", CAMPAIGNS), ("position", POSITIONS), ("country", COUNTRIES), ("platform", PLATFORMS)):
        for value in values:
            assert check(env, {key: value}, "admin"), (key, value)


def test_a_campaign_whose_name_has_a_comma_is_one_value(env):
    """Facets list `Qatar, Kitchen (Doha)` as one campaign; picking it must not be split into two names that match nothing."""
    act_as(env, "admin")
    assert len(check(env, {"campaign": "Qatar, Kitchen (Doha)"}, "admin")) >= 5


def test_search_with_filters_and_case(env):
    act_as(env, "admin")
    check(env, {"q": "lead 1", "owner": "yuv"}, "admin")
    check(env, {"q": "LEAD 2", "status": "new,contacted", "created_from": "2026-11-15", "created_to": "2026-11-19"}, "admin")


def test_empty_unknown_and_any_values(env):
    act_as(env, "admin")
    everything = {row["id"] for row in env.data}
    for f in ({}, {"status": ""}, {"owner": ""}, {"campaign": ""}, {"created_from": ""}, {"created_to": ""}, {"date_field": "uploaded"}, {"date_field": "created"}):
        got, total = listed(env, f)
        assert (got, total) == (everything, len(everything)), f
    assert listed(env, {"owner": "nobody-at-all"}) == (set(), 0)
    assert listed(env, {"campaign": "no such campaign"}) == (set(), 0)
    assert env.client.get("/api/leads", params={"status": "all"}).status_code == 422
    assert env.client.get("/api/leads", params={"status": "Contacted"}).status_code == 422
    assert env.client.get("/api/leads", params={"created_from": "19-11-2026"}).status_code == 422
    assert env.client.get("/api/leads", params={"created_from": "2026-11-19T00:00:00"}).status_code == 422


def test_pagination_with_filters(env):
    act_as(env, "admin")
    f = {"created_from": "2026-11-15", "created_to": "2026-11-19", "status": "new,contacted,interested"}
    want = expected_ids(env, f, "admin")
    seen: list[str] = []
    for page in range(0, 10):
        response = env.client.get("/api/leads", params={**f, "page": page, "page_size": 4})
        body = response.json()
        assert body["total"] == len(want)
        seen += [i["id"] for i in body["items"]]
        if (page + 1) * 4 >= len(want):
            break
    assert sorted(seen) == sorted(want) and len(seen) == len(set(seen))
    far = env.client.get("/api/leads", params={**f, "page": 50, "page_size": 4}).json()  # a page past the end is clamped, never empty
    assert far["items"] and far["total"] == len(want)


def test_scope_for_admin_md_and_a_recruiter(env):
    for viewer in ("admin", "md"):
        act_as(env, viewer)
        for owner in ("me", "none", "yuv", "asha", "unassigned", ""):
            check(env, {"owner": owner}, viewer)
    act_as(env, "yuv")
    for owner in ("", "me", "yuv", "asha", "none"):  # a recruiter never reaches beyond their own leads
        for f in ({"owner": owner}, {"owner": owner, **D19}, {"owner": owner, "status": "new,contacted"}):
            check(env, f, "yuv")
    assert listed(env, {"owner": "asha"}) == (set(), 0)


def test_the_summary_follows_the_viewer_not_the_filters(env):
    act_as(env, "admin")
    everyone = env.client.get("/api/leads/summary", params={"scope": "all"}).json()
    mine = env.client.get("/api/leads/summary", params={"scope": "mine"}).json()
    assert everyone["total"] == len(env.data) and mine["total"] == 0
    assert everyone["newToday"] == len(expected_ids(env, {"uploaded_today": "true"}, "admin"))
    act_as(env, "yuv")
    own = env.client.get("/api/leads/summary").json()
    assert own["total"] == len(expected_ids(env, {}, "yuv"))
    assert own["newToday"] == len(expected_ids(env, {"uploaded_today": "true"}, "yuv"))


# ------------------------------------------------------------------ the whole space, sampled

def test_random_combinations_agree_with_the_brute_force_filter(env):
    rng = Random(11)
    space = {
        "status": [None, "new", "contacted", "interested,resume_received", "converted", "not_interested"],
        "owner": [None, "me", "yuv", "asha", "ravi", "none"],
        "campaign": [None, *CAMPAIGNS],
        "position": [None, *POSITIONS],
        "country": [None, *COUNTRIES],
        "platform": [None, *PLATFORMS],
        "range": [(None, None), ("2026-11-19", "2026-11-19"), ("2026-11-18", "2026-11-19"), ("2026-11-15", "2026-11-17"), ("2026-11-17", None), (None, "2026-11-16")],
        "date_field": [None, "uploaded", "created"],
        "q": [None, "lead 0", "LEAD 1"],
        "today": [None, "true"],
    }
    combos = list(product(*space.values()))
    for viewer in ("admin", "md", "yuv"):
        act_as(env, viewer)
        for combo in rng.sample(combos, 220):
            f = dict(zip(space, combo))
            f["created_from"], f["created_to"] = f.pop("range")
            f["uploaded_today"] = f.pop("today")
            check(env, {k: v for k, v in f.items() if v is not None}, viewer)


# ------------------------------------------------------------------ export and bulk share the same filter

def test_export_and_select_all_matching_use_the_same_filter_as_the_list(env):
    filters = [{"owner": "yuv", **D19}, {"owner": "yuv", "created_from": "2026-11-16", "created_to": "2026-11-19"}, {"status": "new"}, {"status": "interested,resume_received"},
               {"owner": "asha", "status": "new,contacted", "date_field": "created", "created_from": "2026-11-15", "created_to": "2026-11-17"},
               {"uploaded_today": "true", "platform": "fb"}, {"campaign": "Qatar, Kitchen (Doha)"}]
    for viewer in ("admin", "md"):
        act_as(env, viewer)
        for f in filters:
            want = expected_ids(env, f, viewer)
            csv_response = env.client.get("/api/leads/export.csv", params=f)
            assert csv_response.status_code == 200, csv_response.text
            names = {row["Name"] for row in csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig")))} if want else set()
            assert names == {r["name"] for r in env.data if r["id"] in want}, (viewer, f)
            xlsx = env.client.post("/api/leads/bulk", json={"action": "export", "filter": f})
            assert xlsx.status_code == 200, xlsx.text
            assert int(xlsx.headers["X-Lead-Count"]) == len(want), (viewer, f)
            rows, from_filter = service.resolve(env.db, env.db.get(m.User, viewer), None, f, lock=False)
            assert from_filter and {r[0] for r in rows} == want, (viewer, f)
    act_as(env, "yuv")
    for f in ({}, {"owner": "asha"}, {"status": "new"}, D19):  # a recruiter's selection is always their own leads
        rows, _ = service.resolve(env.db, env.db.get(m.User, "yuv"), None, f, lock=False)
        assert {r[0] for r in rows} == expected_ids(env, {**f, "owner": "me"}, "yuv"), f


def test_bulk_filter_flags_sent_as_text_mean_what_they_say(env):
    """The select-all-matching body carries the list's query string values, so a flag arrives as the text "true" or "false"."""
    act_as(env, "admin")
    user = env.db.get(m.User, "admin")
    everyone = {row["id"] for row in env.data}
    off, _ = service.resolve(env.db, user, None, {"uploaded_today": "false"}, lock=False)
    assert {r[0] for r in off} == everyone
    on, _ = service.resolve(env.db, user, None, {"uploaded_today": "true"}, lock=False)
    assert {r[0] for r in on} == expected_ids(env, {"uploaded_today": "true"}, "admin")
