"""The polled and heavy read endpoints: round-trip budgets, shared memo, and no answer ever crosses accounts.

Statement counts include the sign-in check (the memo of who is signed in is emptied by `recorded_statements`).
The response memo is switched on with CRM_RESPONSE_CACHE=force; it is off under pytest otherwise.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.security import SESSION_COOKIE
from app.core.config import Settings
from app.db.models import (
    Candidate, EmailIngestionJob, FormOption, MailAttachment, Role, Session as SessionRow, Source, User,
)
from app.services import response_cache as cache
from test_bootstrap_performance import assert_select_budget, recorded_statements
from test_database_naming import schema_database  # noqa: F401
from test_email_intake import intake  # noqa: F401
from test_inbox_attention import NOW, add_manual, add_whatsapp, failed_email


@pytest.fixture
def api(intake, monkeypatch):
    """A small app with the polled routers, signed-in as any of three roles by name."""
    with patch("app.core.config.get_settings", return_value=SimpleNamespace(
        agency_timezone="Asia/Kolkata", database_url=intake.engine.url.render_as_string(hide_password=False),
    )):
        from app.api.routers import candidates, form_options, inbox, reports, whatsapp
        from app.db.session import get_db

    # Other test modules import these routers inside `patch("app.core.config.get_settings", return_value=SimpleNamespace(...))`.
    # Whichever imports a router first leaves that patch's mock bound to the router's own `get_settings` name for the rest of
    # the process (a stand-in without `email_ingestion_enabled` / `whatsapp_enabled`). So this fixture sets complete real
    # settings on every module the tests below read them from, instead of trusting what earlier tests left behind.
    from app.services import candidate_search
    settings = Settings(_env_file=None, database_url=intake.engine.url.render_as_string(hide_password=False),
                        agency_timezone="Asia/Kolkata", redis_url=None)
    for module in (inbox, whatsapp, reports, candidates, candidate_search):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    with intake.sessions() as db:
        db.add_all([
            User(id="poll-manager", name="Manager", email="pm@example.test", role=Role.manager),
            User(id="poll-staff", name="Staff", email="ps@example.test", role=Role.staff),
        ])
        db.flush()
        for user_id in ("admin", "poll-manager", "poll-staff"):
            db.add(SessionRow(id=f"{user_id}-session", user_id=user_id, expires_at=NOW + timedelta(days=3650)))
        db.commit()

    application = FastAPI()
    for router in (inbox.router, whatsapp.router, candidates.router, form_options.router, reports.router, reports.dashboard_router):
        application.include_router(router)

    def isolated_db():
        with intake.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    cache.clear()
    with TestClient(application, base_url="https://testserver") as client:
        client.as_user = lambda user_id: client.cookies.set(SESSION_COOKIE, f"{user_id}-session")
        client.as_user("admin")
        yield client
    cache.clear()


def statements_for(intake, client, path, **kwargs):
    with recorded_statements(intake.engine) as statements:
        response = client.get(path, **kwargs)
    return response, statements


# ---------------------------------------------------------------- inbox attention

def test_attention_is_four_statements_cold_and_none_beyond_the_signin_check_when_kept(intake, api, monkeypatch):
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "0")
    job_id, _ = failed_email(intake, 11)
    with intake.sessions() as db:
        message_id = db.get(EmailIngestionJob, job_id).message_id
        db.add(MailAttachment(message_id=message_id, file_name="aaa-first.pdf", size_kb=1, kind="document", object_key="k/aaa"))
        db.commit()
    add_whatsapp(intake, "scan")
    add_manual(intake, "passport-a")

    response, statements = statements_for(intake, api, "/api/inbox/attention")
    assert response.status_code == 200
    assert_select_budget(statements, 5)  # sign-in check + email list (names ride along) + whatsapp + manual + the three totals together
    body = response.json()
    email = next(item for item in body["items"] if item["source"] == "email")
    assert email["files"] == ["aaa-first.pdf", "cv-11.pdf"]  # attachment names, in order, from the same statement
    assert body["totals"] == {"email": 1, "whatsapp": 1, "manual": 1, "all": 3}

    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    cache.clear()
    assert api.get("/api/inbox/attention").json() == body
    again, statements = statements_for(intake, api, "/api/inbox/attention")
    assert again.json() == body
    assert_select_budget(statements, 1)  # only the sign-in check


def test_attention_memo_is_per_account_and_a_new_failure_replaces_it(intake, api):
    failed_email(intake, 11)
    admin_first = api.get("/api/inbox/attention").json()
    assert admin_first["canRetry"] is True and admin_first["items"][0]["retryable"] is True

    api.as_user("poll-manager")  # same data, different rights: must not receive the admin's entry
    manager = api.get("/api/inbox/attention").json()
    assert manager["canRetry"] is False and manager["items"][0]["retryable"] is False

    api.as_user("poll-staff")  # no rights at all, even though an answer is kept for others
    assert api.get("/api/inbox/attention").status_code == 403

    api.as_user("admin")
    assert api.get("/api/inbox/attention").json() == admin_first
    failed_email(intake, 12, subject="Second failure")
    refreshed = api.get("/api/inbox/attention").json()
    assert refreshed["totals"]["email"] == 2 and len(refreshed["items"]) == 2


# ---------------------------------------------------------------- inbox health, pulse, unread badge, whatsapp health

def test_inbox_health_is_admin_only_even_when_an_answer_is_kept(intake, api):
    first = api.get("/api/inbox/health")
    assert first.status_code == 200
    _, statements = statements_for(intake, api, "/api/inbox/health")
    assert_select_budget(statements, 1)
    api.as_user("poll-manager")
    assert api.get("/api/inbox/health").status_code == 403
    api.as_user("poll-staff")
    assert api.get("/api/inbox/health").status_code == 403


def test_inbox_health_counts_follow_a_status_change_at_once(intake, api):
    job_id, _ = failed_email(intake, 11, "pending")
    assert api.get("/api/inbox/health").json()["counts"]["pending"] == 1
    failed_email(intake, 12, "failed")
    counts = api.get("/api/inbox/health").json()["counts"]
    assert counts["pending"] == 1 and counts["failed"] == 1


def test_pulse_is_one_shared_answer_for_every_role_and_follows_new_candidates(intake, api):
    api.as_user("poll-staff")
    cold, statements = statements_for(intake, api, "/api/inbox/pulse")
    assert_select_budget(statements, 3)  # sign-in check + revision + unread badge, side by side
    api.as_user("admin")
    _, statements = statements_for(intake, api, "/api/inbox/pulse")
    assert_select_budget(statements, 1)
    assert api.get("/api/inbox/pulse").json() == cold.json()

    with intake.sessions() as db:
        db.add(Candidate(id="late", ref="CND-LATE", name="Late arrival", phone="9000000077", source=Source.walk_in))
        db.commit()
    assert api.get("/api/inbox/pulse").json()["revision"] != cold.json()["revision"]


def test_whatsapp_health_is_admin_only_kept_and_current(intake, api):
    add_whatsapp(intake, "one", "failed")
    first = api.get("/api/whatsapp/health")
    assert first.status_code == 200 and first.json()["counts"]["failed"] == 1
    _, statements = statements_for(intake, api, "/api/whatsapp/health")
    assert_select_budget(statements, 1)
    api.as_user("poll-manager")
    assert api.get("/api/whatsapp/health").status_code == 403
    api.as_user("admin")
    add_whatsapp(intake, "two", "failed")
    assert api.get("/api/whatsapp/health").json()["counts"]["failed"] == 2


# ---------------------------------------------------------------- Data Bank summaries, form options

def test_data_bank_summaries_are_shared_kept_and_follow_candidate_changes(intake, api):
    with intake.sessions() as db:
        db.add_all([
            Candidate(id="one", ref="CND-1", name="One", phone="9000000001", source=Source.walk_in, skills=["TIG welding"]),
            Candidate(id="two", ref="CND-2", name="Two", phone="9000000002", source=Source.walk_in, skills=["Forklift"]),
        ])
        db.commit()
    counts = api.get("/api/candidates/pool-counts").json()
    facets = api.get("/api/candidates/skill-facets").json()
    assert counts["all"] == 2 and facets["total"] == 2 and {s["key"] for s in facets["skills"]} == {"tig welding", "forklift"}

    groups = api.get("/api/candidates/position-groups").json()
    for path in ("/api/candidates/pool-counts", "/api/candidates/skill-facets", "/api/candidates/position-groups"):
        _, statements = statements_for(intake, api, path)
        assert_select_budget(statements, 1)  # kept: only the sign-in check

    api.as_user("poll-staff")  # the bank is shared by every signed-in role, so the same answer
    assert api.get("/api/candidates/position-groups").json() == groups
    assert api.get("/api/candidates/pool-counts").json() == counts
    assert api.get("/api/candidates/skill-facets").json() == facets

    with intake.sessions() as db:
        db.add(Candidate(id="three", ref="CND-3", name="Three", phone="9000000003", source=Source.walk_in, skills=["PLC"]))
        db.commit()
    assert api.get("/api/candidates/pool-counts").json()["all"] == 3
    assert "plc" in {s["key"] for s in api.get("/api/candidates/skill-facets").json()["skills"]}
    # A different scope is a different entry.
    assert api.get("/api/candidates/pool-counts?staff=nobody").json()["all"] == 0


def test_pool_counts_and_facets_stay_within_their_statement_budgets(intake, api, monkeypatch):
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "0")
    _, statements = statements_for(intake, api, "/api/candidates/pool-counts")
    assert_select_budget(statements, 2)  # sign-in check + one statement for all five pools
    _, statements = statements_for(intake, api, "/api/candidates/skill-facets")
    assert_select_budget(statements, 3)  # sign-in check + the facet list and the totals side by side


def test_form_options_are_built_once_per_change_and_still_support_304(intake, api):
    first = api.get("/api/form-options")
    assert first.status_code == 200
    tag = first.headers["etag"]
    kept, statements = statements_for(intake, api, "/api/form-options", headers={"if-none-match": tag})
    assert kept.status_code == 304
    assert_select_budget(statements, 1)

    with intake.sessions() as db:
        db.add(FormOption(id="opt-new", category="service_charges_status", value="custom", label="Custom", sort_order=99))
        db.commit()
    changed = api.get("/api/form-options", headers={"if-none-match": tag})
    assert changed.status_code == 200 and changed.headers["etag"] != tag
    assert any(option["value"] == "custom" for category in changed.json()["categories"] for option in category["options"])


# ---------------------------------------------------------------- dashboard and reports

def test_dashboard_is_kept_per_person_and_follows_changes(intake, api):
    first = api.get("/api/dashboard")
    assert first.status_code == 200
    kept, statements = statements_for(intake, api, "/api/dashboard")
    assert kept.json() == first.json()
    assert_select_budget(statements, 1)

    api.as_user("poll-staff")
    staff = api.get("/api/dashboard")
    assert staff.status_code == 200 and staff.json() != first.json()  # a recruiter's dashboard is their own
    assert api.get("/api/dashboard?staff_id=admin").status_code == 403  # the scope check is never skipped

    api.as_user("admin")
    with intake.sessions() as db:
        db.add(Candidate(id="dash", ref="CND-D", name="Dash", phone="9000000055", source=Source.walk_in, owner_id="poll-staff"))
        db.commit()
    after = api.get("/api/dashboard")
    assert after.status_code == 200 and after.json() != first.json()


def test_reports_are_kept_per_person_and_scope(intake, api):
    first = api.get("/api/reports")
    assert first.status_code == 200
    _, statements = statements_for(intake, api, "/api/reports")
    assert_select_budget(statements, 1)
    api.as_user("poll-staff")
    own = api.get("/api/reports")
    assert own.status_code == 200
    assert api.get("/api/reports?staff_id=admin").status_code == 403  # a recruiter cannot ask for another person's numbers
    api.as_user("admin")
    assert api.get("/api/reports").json() == first.json()


def test_data_bank_list_is_one_statement_with_its_total_and_is_kept_briefly(intake, api, monkeypatch):
    with intake.sessions() as db:
        db.add_all([Candidate(id=f"c{n}", ref=f"CND-{n}", name=f"Person {n}", phone=f"90000000{n:02d}", source=Source.walk_in) for n in range(25)])
        db.commit()
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "0")
    response, statements = statements_for(intake, api, "/api/candidates?page=0&page_size=10")
    assert_select_budget(statements, 2)  # sign-in check + the page, with its total in the same statement
    body = response.json()
    assert body["total"] == 25 and len(body["items"]) == 10 and body["page"] == 0
    past = api.get("/api/candidates?page=9&page_size=10").json()  # beyond the end: the last page, with the true total
    assert past["total"] == 25 and past["page"] == 2 and len(past["items"]) == 5
    assert api.get("/api/candidates?q=nobody-matches-this").json() == {"items": [], "total": 0, "page": 0, "pageSize": 20}

    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    cache.clear()
    first = api.get("/api/candidates?page=0&page_size=10").json()
    kept, statements = statements_for(intake, api, "/api/candidates?page=0&page_size=10")
    assert kept.json() == first == body
    assert_select_budget(statements, 1)
    with intake.sessions() as db:
        db.add(Candidate(id="c-new", ref="CND-N", name="New", phone="9000099999", source=Source.walk_in))
        db.commit()
    assert api.get("/api/candidates?page=0&page_size=10").json()["total"] == 26
